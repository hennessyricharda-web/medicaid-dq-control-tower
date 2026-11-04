"""Part F P1-3 (K2c): the four new `/api/v1/catalog/*` routes (pipelines, nodes, discover, wire). Over the real
app and the real control plane, the house pattern of tests/t1/test_profile.py and test_governance_api.py.

The core demo-moment test (`test_wire_refuses_single_approver_then_accepts_two_tier_unmoved`) proves the two-person
wiring flow genuinely goes through the SAME `gov_propose`/`gov_decide`/`gov_apply` mechanism every other governed
change uses (not a new, one-off mechanism), and that the effective tier on the already-gated w1 relations does not
move -- the package's own highest-leverage demo claim (K2_DESIGN.md §5)."""
import os
from pathlib import Path

from fastapi.testclient import TestClient

from dqagent.api.app import create_app
from dqagent.backtest import run_backtest

from .w1_harness import Slice

ROOT = Path(__file__).resolve().parents[2]
W1 = ROOT / "synthetic" / "out" / "w1"
PG = dict(host=os.environ.get("DQ_PG_HOST", "/tmp"), port=int(os.environ.get("DQ_PG_PORT", "55432")),
          user=os.environ.get("DQ_PG_USER", "dq_gov"))


def H(rt, user, **kw):
    return {"Authorization": f"Bearer {rt.idp.token(user, **kw)}"}


TMSIS_MANIFEST = "pipelines/tmsis_outbound/target/manifest.json"


def _wire_body(mode="advisory", ceiling="L1"):
    return {"pipeline": {"id": "tmsis_outbound", "adapter": "dbt_files",
                        "scope": {"manifest_path": TMSIS_MANIFEST}, "platform": "duckdb",
                        "domains": ["CLAIMS"], "run_on": "manual"},
            "task_gates": [{"task_id": "ie_segment", "datasets": ["bronze.ie_segment"], "mode": mode,
                            "release_ceiling": ceiling},
                           {"task_id": "encounter_header", "datasets": ["bronze.encounter_header"], "mode": mode,
                            "release_ceiling": ceiling}],
            "rationale": "Wire tmsis_outbound as a consumer of w1 relations"}


def test_catalog_pipelines_lists_harness_pipelines_scoped(fresh_cp_dsn):
    rt = Slice(fresh_cp_dsn)
    api = TestClient(create_app(rt))
    body = api.get("/api/v1/catalog/pipelines", headers=H(rt, "maker")).json()
    ids = {pl["id"] for g in body["groups"] for pl in g["pipelines"]}
    assert {"p4_eligibility_ie", "p6_claims"} <= ids
    assert body["order"] == ["p4_eligibility_ie", "p6_claims"]
    # a SNAP-only caller sees only the pipeline whose authorities it holds (precision, not recall: omitted, not
    # redacted -- the same D7 scope rule the existing profile route already enforces)
    snap_body = api.get("/api/v1/catalog/pipelines", headers=H(rt, "snap")).json()
    snap_ids = {pl["id"] for g in snap_body["groups"] for pl in g["pipelines"]}
    assert "p4_eligibility_ie" in snap_ids and "p6_claims" not in snap_ids


def test_catalog_pipelines_outsider_no_scope_403(fresh_cp_dsn):
    rt = Slice(fresh_cp_dsn)
    api = TestClient(create_app(rt))
    r = api.get("/api/v1/catalog/pipelines", headers=H(rt, "outsider"))
    assert r.status_code == 403


def test_catalog_nodes_unknown_pipeline_404(fresh_cp_dsn):
    rt = Slice(fresh_cp_dsn)
    api = TestClient(create_app(rt))
    r = api.get("/api/v1/catalog/pipelines/does-not-exist/nodes", headers=H(rt, "maker"))
    assert r.status_code == 404


def test_catalog_nodes_scoped_403(fresh_cp_dsn):
    """D7 extended to `nodes`: a SNAP-only caller over a Medicaid-only pipeline gets 403, not a redacted 200."""
    rt = Slice(fresh_cp_dsn)
    api = TestClient(create_app(rt))
    r = api.get("/api/v1/catalog/pipelines/p6_claims/nodes", headers=H(rt, "snap"))
    assert r.status_code == 403
    ok = api.get("/api/v1/catalog/pipelines/p6_claims/nodes", headers=H(rt, "maker"))
    assert ok.status_code == 200, ok.text


def test_catalog_nodes_effective_tier_column(fresh_cp_dsn):
    """The nodes drawer's own Effective tier column: each genesis-gated w1 relation carries a non-null
    effective_tier via dataset_governance/autonomy_tier_for (K2a/K2b), and a real introducer artifact id."""
    rt = Slice(fresh_cp_dsn)
    api = TestClient(create_app(rt))
    body = api.get("/api/v1/catalog/pipelines/p4_eligibility_ie/nodes", headers=H(rt, "maker")).json()
    gated_rows = [n for n in body["nodes"] if n["gate"] is not None]
    assert gated_rows, body
    for n in gated_rows:
        ds = n["datasets"][0]
        assert ds["dataset"] in ("bronze.ie_segment", "bronze.enrollment_834")
        assert ds["effective_tier"] in ("L1", "L2", "L3", "L4")
        assert ds["introduced_by"] and ds["introduced_by"].startswith("ZZ:taskgate:")


def test_catalog_discover_confined_to_pipelines(fresh_cp_dsn):
    """PFP :836: `discover`'s own path confinement (enforced inside dqagent.adapters.dag.dbt_files.discover) is
    reachable, and refused, through the route -- a 422, not a 500 or a silent read outside pipelines/."""
    rt = Slice(fresh_cp_dsn)
    api = TestClient(create_app(rt))
    r = api.post("/api/v1/catalog/discover", headers=H(rt, "maker"),
                json={"manifest_path": "config/agents.yaml"})
    assert r.status_code == 422, r.text
    assert "pipelines/" in r.json()["detail"]["reason"]


def test_catalog_discover_requires_governance_duty(fresh_cp_dsn):
    rt = Slice(fresh_cp_dsn)
    api = TestClient(create_app(rt))
    r = api.post("/api/v1/catalog/discover", headers=H(rt, "snap"),
                json={"manifest_path": TMSIS_MANIFEST})
    assert r.status_code == 403


def test_catalog_discover_previews_the_committed_manifest(fresh_cp_dsn):
    rt = Slice(fresh_cp_dsn)
    api = TestClient(create_app(rt))
    r = api.post("/api/v1/catalog/discover", headers=H(rt, "maker"), json={"manifest_path": TMSIS_MANIFEST})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["manifest_digest"]
    names = {n["node"] for n in body["nodes"]}
    assert "stg_eligibility" in names


def test_wire_requires_governance_duty(fresh_cp_dsn):
    rt = Slice(fresh_cp_dsn)
    api = TestClient(create_app(rt))
    r = api.post("/api/v1/catalog/wire", headers=H(rt, "snap"), json=_wire_body())
    assert r.status_code == 403


def test_wire_degenerate_inputs_refused(fresh_cp_dsn):
    rt = Slice(fresh_cp_dsn)
    api = TestClient(create_app(rt))
    bad = {"pipeline": {"id": "x"}, "task_gates": [], "rationale": "short but a pipeline with no manifest and no gates"}
    r = api.post("/api/v1/catalog/wire", headers=H(rt, "maker"), json=bad)
    assert r.status_code == 422
    too_short = _wire_body() | {"rationale": "short"}
    r2 = api.post("/api/v1/catalog/wire", headers=H(rt, "maker"), json=too_short)
    assert r2.status_code == 422


def test_wire_refuses_single_approver_then_accepts_two_tier_unmoved(fresh_cp_dsn):
    """The package's own central demo moment (K2_DESIGN.md §5, EXECUTION_PLAN.md K2c): wiring tmsis_outbound in
    advisory mode through POST /catalog/wire needs two DIFFERENT approvers, enforced by the SAME gov_propose /
    gov_decide / gov_apply mechanism every other governed change uses -- not a new one-off mechanism. The database
    refuses: the author approving their own proposal (gov_decide's own check), and a single approver's apply
    (AWAITING_APPROVAL). After a second, different approver, the change applies, and the effective tier on the two
    ALREADY-gated w1 relations this pipeline reads does not move (ORCH-1, I-10's own assertion, reused here over
    HTTP instead of the Python registry)."""
    rt = Slice(fresh_cp_dsn)
    api = TestClient(create_app(rt))

    def tiers():
        with rt.cp.as_role("dq_registry_svc") as c:
            return {ds: c.execute("SELECT dq_ctl.autonomy_tier_for('ZZ','dq-reconciliation','RELEASE',"
                                  "'AUTH_MEDICAID',%s) t", (ds,)).fetchone()["t"]
                    for ds in ("bronze.ie_segment", "bronze.encounter_header")}

    before = tiers()
    r = api.post("/api/v1/catalog/wire", headers=H(rt, "maker"), json=_wire_body())
    assert r.status_code == 200, r.text
    body = r.json()
    cr_id = body["cr_id"]
    # PIPELINE is never lower-only (K2a): the database stores approvals_required = 2, not a route-side constant
    assert body["approvals_required"] == 2
    assert body["impact"]["datasets_already_gated"], body["impact"]
    assert not body["impact"]["datasets_introduced"]          # both relations are already gated by genesis
    assert body["impact"]["effective_tier_before"] == body["impact"]["effective_tier_after"]

    # same mechanism as every other change request: the EXISTING challenge/decide routes, never a new endpoint
    chal = api.post(f"/api/v1/governance/change-requests/{cr_id}/challenge", headers=H(rt, "checker"),
                    json={"verdict": "APPROVED"}).json()["challenge"]
    # the database refuses the AUTHOR approving their own proposal
    own_chal = api.post(f"/api/v1/governance/change-requests/{cr_id}/challenge", headers=H(rt, "maker"),
                       json={"verdict": "APPROVED"}).json()["challenge"]
    own = api.post(f"/api/v1/governance/change-requests/{cr_id}/decide", headers=H(rt, "maker"),
                   json={"verdict": "APPROVED", "step_up_token": rt.idp.token("maker", step_up=True, challenge=own_chal)})
    assert own.status_code == 403, own.text

    # ONE (different) approver: the database does not apply yet (AWAITING_APPROVAL), not APPLIED
    one = api.post(f"/api/v1/governance/change-requests/{cr_id}/decide", headers=H(rt, "checker"),
                  json={"verdict": "APPROVED", "step_up_token": rt.idp.token("checker", step_up=True, challenge=chal)})
    assert one.status_code == 200 and one.json()["status"] == "AWAITING_APPROVAL", one.text
    assert tiers() == before                                   # still unmoved after only one approver

    # a SECOND, different approver: now it applies
    chal2 = api.post(f"/api/v1/governance/change-requests/{cr_id}/challenge", headers=H(rt, "checker2"),
                     json={"verdict": "APPROVED"}).json()["challenge"]
    two = api.post(f"/api/v1/governance/change-requests/{cr_id}/decide", headers=H(rt, "checker2"),
                  json={"verdict": "APPROVED",
                        "step_up_token": rt.idp.token("checker2", step_up=True, challenge=chal2)})
    assert two.status_code == 200 and two.json()["status"] == "APPLIED", two.text
    assert tiers() == before                                   # the effective tier on the datasets it reads is unmoved


# -- K4 (PFP P2-4): GET /catalog/pipelines/{id}/graph?overlay=gates --------------------------------------------

def test_catalog_graph_rejects_unknown_overlay(fresh_cp_dsn):
    """A fixed allow-list (today only "gates"): anything else is a 400, never a silent passthrough."""
    rt = Slice(fresh_cp_dsn)
    api = TestClient(create_app(rt))
    r = api.get("/api/v1/catalog/pipelines/p4_eligibility_ie/graph?overlay=bogus", headers=H(rt, "maker"))
    assert r.status_code == 400, r.text


def test_catalog_graph_unknown_pipeline_404(fresh_cp_dsn):
    rt = Slice(fresh_cp_dsn)
    api = TestClient(create_app(rt))
    r = api.get("/api/v1/catalog/pipelines/does-not-exist/graph?overlay=gates", headers=H(rt, "maker"))
    assert r.status_code == 404


def test_catalog_graph_outsider_403_not_redacted_200(fresh_cp_dsn):
    """The single most important correctness requirement from the before-build red team (K4 Design entry,
    finding 1): a caller with NO authority over this pipeline gets 403, never a 200 with blanked values.
    `_worst_node_state`'s RLS role would happily return an (empty-looking but structurally real) response if this
    route relied on it alone for scoping -- it suppresses VALUES, it does not gate node/gate/dataset identifiers.
    `snap` holds AUTH_SNAP only; p6_claims's gated relations are AUTH_MEDICAID (contracts_w1.yaml)."""
    rt = Slice(fresh_cp_dsn)
    api = TestClient(create_app(rt))
    r = api.get("/api/v1/catalog/pipelines/p6_claims/graph?overlay=gates", headers=H(rt, "snap"))
    assert r.status_code == 403, r.text
    assert "node" not in r.text and "gate" not in r.text        # not even a key name of the withheld shape leaks
    ok = api.get("/api/v1/catalog/pipelines/p6_claims/graph?overlay=gates", headers=H(rt, "maker"))
    assert ok.status_code == 200, ok.text


def test_catalog_graph_node_set_matches_nodes_route(fresh_cp_dsn):
    """Exit criterion (EXECUTION_PLAN.md K4): "the graph and the table show the same node set." Both routes
    share the same TASK_GATE/manifest composition; the graph reshapes `node` to `id` for a graph library, but
    the underlying set of governed-dataset ids must be identical."""
    rt = Slice(fresh_cp_dsn)
    api = TestClient(create_app(rt))
    table = api.get("/api/v1/catalog/pipelines/p4_eligibility_ie/nodes", headers=H(rt, "maker")).json()
    graph = api.get("/api/v1/catalog/pipelines/p4_eligibility_ie/graph?overlay=gates", headers=H(rt, "maker")).json()
    assert {n["node"] for n in table["nodes"]} == {n["id"] for n in graph["nodes"]}


def test_catalog_graph_gated_node_carries_state_and_gate(fresh_cp_dsn):
    rt = Slice(fresh_cp_dsn)
    api = TestClient(create_app(rt))
    body = api.get("/api/v1/catalog/pipelines/p4_eligibility_ie/graph?overlay=gates", headers=H(rt, "maker")).json()
    by_id = {n["id"]: n for n in body["nodes"]}
    for ds in ("bronze.ie_segment", "bronze.enrollment_834"):
        assert by_id[ds]["state"] in ("EMPTY", "HELD", "PENDING", "RELEASED"), by_id[ds]
        assert by_id[ds]["gate"]["id"].startswith("ZZ:taskgate:p4_eligibility_ie:")
        assert by_id[ds]["gate"]["mode"]


def test_catalog_graph_cross_pipeline_dependency_edge(fresh_cp_dsn):
    """`config/harness_w1.yaml`'s own dependency: CLAIMS (p6_claims) WAITs on ELIGIBILITY (p4_eligibility_ie).
    New backend logic (no prior server-side equivalent, K4 Design entry's golden-path edge note): the edge names
    the domains, carries the declared policy, and `orderOk` confirms the global pipeline order actually places
    the upstream pipeline (p4) at or before the downstream one (p6)."""
    rt = Slice(fresh_cp_dsn)
    api = TestClient(create_app(rt))
    body = api.get("/api/v1/catalog/pipelines/p6_claims/graph?overlay=gates", headers=H(rt, "maker")).json()
    assert {"from": "ELIGIBILITY", "to": "CLAIMS", "policy": "WAIT", "orderOk": True} in body["edges"]


def test_catalog_graph_same_pipeline_domain_edge_order_ok_trivially_true(fresh_cp_dsn):
    """p4_eligibility_ie owns BOTH domains in its own EVALUATE_AGAINST_LAST_RELEASED edge (ENROLLMENT depends on
    ELIGIBILITY) -- the edge still reports, and `orderOk` is trivially true (same owning pipeline, so no
    scheduling violation is possible), never defaulted to a value this route cannot support."""
    rt = Slice(fresh_cp_dsn)
    api = TestClient(create_app(rt))
    body = api.get("/api/v1/catalog/pipelines/p4_eligibility_ie/graph?overlay=gates", headers=H(rt, "maker")).json()
    assert {"from": "ELIGIBILITY", "to": "ENROLLMENT", "policy": "EVALUATE_AGAINST_LAST_RELEASED", "orderOk": True} \
        in body["edges"]


def test_catalog_graph_wired_pipeline_includes_discovered_nodes_second_kind(fresh_cp_dsn):
    """A WIRED pipeline's discovered dbt-manifest nodes are the SECOND row kind (`catalog_pipeline_nodes`'s own
    precedent): `state`/`gate` both `None` ("discovered, not evaluated"), `layer`/`layer_source` carried instead,
    never a fabricated link to a governed dataset."""
    rt = Slice(fresh_cp_dsn)
    api = TestClient(create_app(rt))
    rt.govern([
        {"artifact_id": "ZZ:pipeline:tmsis_outbound", "kind": "PIPELINE", "op": "PUT",
         "content": {"adapter": "dbt_files", "scope": {"manifest_path": TMSIS_MANIFEST}, "platform": "duckdb",
                     "domains": ["CLAIMS"], "layer_map": {}, "run_on": "manual"}},
        {"artifact_id": "ZZ:taskgate:tmsis_outbound:ie_segment", "kind": "TASK_GATE", "op": "PUT",
         "content": {"task_id": "ie_segment", "datasets": ["bronze.ie_segment"], "mode": "advisory",
                     "correlate": "LATEST_SEALED", "max_wait": "PT2H", "on_timeout": "PASS_ADVISORY",
                     "release_ceiling": "L1", "reintroduce": []}},
    ], approvers=("checker", "checker2"))  # this CR is a loosening change (approvals_required: 2) --
    # one approver leaves it AWAITING_APPROVAL and never applied; reproduced directly before this fix
    # (artifact never reached config_effective, route correctly 404'd on a pipeline that was never really created)
    body = api.get("/api/v1/catalog/pipelines/tmsis_outbound/graph?overlay=gates", headers=H(rt, "maker")).json()
    discovered = [n for n in body["nodes"] if n["gate"] is None]
    assert discovered, body
    assert {n["id"] for n in discovered} == {"outbound_eligibility_extract", "outbound_eligibility_summary",
                                              "outbound_encounter_extract", "outbound_encounter_summary",
                                              "stg_eligibility", "stg_encounters"}
    for n in discovered:
        assert n["state"] is None and n["layer"] == "OUTBOUND" and n["layer_source"] == "meta.dq_layer"


# -- BACKLOG F-11b (K3 P1-4 step (a)'s own demo moment): POST /catalog/pipelines/{id}/task-gates/{task_id}/promote

def _wire_advisory_gate(rt, mode="advisory"):
    """Wires tmsis_outbound with one TASK_GATE (advisory by default), through the registry directly (the SAME
    full-content shape `/catalog/wire` itself would build) -- this is the GATE's own creation, not the bug under
    test, and a BRAND-NEW gate (no prior `old` row) needs no linked backtest report regardless of `mode`
    (`dq_ctl.mode_promotion_refusal` only fires on an actual advisory->block CHANGE, test_promotion.py's own
    `test_mode_promotion_refusal_ignores_a_brand_new_gate`)."""
    rt.govern([
        {"artifact_id": "ZZ:pipeline:tmsis_outbound", "kind": "PIPELINE", "op": "PUT",
         "content": {"adapter": "dbt_files", "scope": {"manifest_path": TMSIS_MANIFEST}, "platform": "duckdb",
                     "domains": ["CLAIMS"], "layer_map": {}, "run_on": "manual"}},
        {"artifact_id": "ZZ:taskgate:tmsis_outbound:ie_segment", "kind": "TASK_GATE", "op": "PUT",
         "content": {"task_id": "ie_segment", "datasets": ["bronze.ie_segment"], "mode": mode,
                     "correlate": "LATEST_SEALED", "max_wait": "PT2H",
                     "on_timeout": "PASS_ADVISORY" if mode == "advisory" else "HOLD",
                     "release_ceiling": "L1", "reintroduce": []}},
    ], approvers=("checker", "checker2"))


def test_old_promote_page_payload_was_genuinely_malformed(fresh_cp_dsn):
    """Reproduces the confirmed red-team finding directly, before asserting the fix: the EXACT incomplete
    content object the promote page used to build (`task_id`/`datasets`/`mode`/`release_ceiling` only, no
    `correlate`/`max_wait`/`on_timeout`/`reintroduce`) sent through the generic `POST /governance/change-requests`
    route (`client.crPropose`, the call path the old page actually used) is deterministically rejected by
    `gov_item_valid` as a malformed TASK_GATE item -- the CR is never created. This is not hypothetical: it is
    the same call path, the same incomplete shape, over the real app and the real control plane."""
    rt = Slice(fresh_cp_dsn)
    api = TestClient(create_app(rt))
    _wire_advisory_gate(rt)
    old_incomplete_payload = {
        "rationale": "Promote tmsis_outbound ie_segment to block (reproducing the old page's payload)",
        "items": [{
            "artifact_id": "ZZ:taskgate:tmsis_outbound:ie_segment", "kind": "TASK_GATE", "op": "PUT",
            "content": {"task_id": "ie_segment", "datasets": ["bronze.ie_segment"], "mode": "block",
                        "release_ceiling": "L1"},
        }],
    }
    r = api.post("/api/v1/governance/change-requests", headers=H(rt, "maker"), json=old_incomplete_payload)
    assert r.status_code == 422, r.text
    assert "malformed TASK_GATE item" in r.json()["detail"]["reason"]


def test_promote_gate_route_accepts_the_genuinely_incomplete_client_payload(fresh_cp_dsn):
    """The fix (BACKLOG F-11b): the FIXED promote page sends nothing but a rationale -- no `content` object at
    all, the genuinely-incomplete shape a browser holding only `CatalogTaskGate` can ever produce. This exercises
    the actual new/changed server code path (`catalog_promote_gate`) over the real app and the real control
    plane, not a hand-completed object: the route itself must read the gate's current full content and complete
    the item server-side, satisfy `gov_item_valid` (`max_wait` included), and the resulting CR must genuinely
    APPLY -- flipping the TASK_GATE's mode live -- through the SAME two-person challenge/decide mechanism every
    other change request uses."""
    rt = Slice(fresh_cp_dsn)
    api = TestClient(create_app(rt))
    _wire_advisory_gate(rt)

    client_payload = {"rationale": "Promote tmsis_outbound ie_segment to block (K3 demo moment)"}  # no content
    r = api.post("/api/v1/catalog/pipelines/tmsis_outbound/task-gates/ie_segment/promote",
                headers=H(rt, "maker"), json=client_payload)
    assert r.status_code == 200, r.text
    body = r.json()
    cr_id = body["cr_id"]
    assert cr_id.startswith("CR-")
    assert body["approvals_required"] == 2            # a mode change is never lower-only

    # a real mode promotion is refused at approval without linked backtest evidence (test_promotion.py's own
    # mode_promotion_refusal) -- link it the same way the demo's own live step does, then approve for real
    report = run_backtest(pipeline_id="tmsis_outbound", cr_id=cr_id, nights=2, data=W1, pg=PG,
                          live_db=fresh_cp_dsn["dbname"])
    assert report["report_id"].startswith("BTR-")


# -- K8 (K7_K8_K9_DESIGN.md §2): POST /catalog/pipelines/{id}/profile/draft -------------------------------------

def test_profile_draft_proposes_a_pipeline_item(fresh_cp_dsn):
    """The route builds exactly one PIPELINE-kind PUT item for the pipeline's own artifact id and calls the SAME
    `registry.propose(...)` every other governed change here uses -- not a new, one-off mechanism."""
    rt = Slice(fresh_cp_dsn)
    api = TestClient(create_app(rt))
    r = api.post("/api/v1/catalog/pipelines/p6_claims/profile/draft", headers=H(rt, "maker"),
                json={"platform": "duckdb", "rationale": "Clarify the platform label for the provider audit."})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["cr_id"].startswith("CR-")
    with rt.cp.as_role("dq_api") as c:
        row = c.execute("SELECT items FROM dq_ctl.config_change_requests WHERE cr_id=%s",
                        (body["cr_id"],)).fetchone()
    items = row["items"]
    assert len(items) == 1
    assert items[0]["kind"] == "PIPELINE" and items[0]["op"] == "PUT"
    assert items[0]["artifact_id"] == "ZZ:pipeline:p6_claims"
    assert items[0]["content"]["platform"] == "duckdb"


def test_profile_draft_needs_two_approvers(fresh_cp_dsn):
    """PIPELINE is never lower-only (`governance_schema.py:1002`): the database stores approvals_required = 2,
    and a single approver's apply leaves it AWAITING_APPROVAL -- the same two-person mechanism `/catalog/wire`
    already proves, exercised here over the new route."""
    rt = Slice(fresh_cp_dsn)
    api = TestClient(create_app(rt))
    r = api.post("/api/v1/catalog/pipelines/p6_claims/profile/draft", headers=H(rt, "maker"),
                json={"platform": "duckdb", "rationale": "Clarify the platform label for the provider audit."})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["approvals_required"] == 2
    cr_id = body["cr_id"]
    chal = api.post(f"/api/v1/governance/change-requests/{cr_id}/challenge", headers=H(rt, "checker"),
                    json={"verdict": "APPROVED"}).json()["challenge"]
    one = api.post(f"/api/v1/governance/change-requests/{cr_id}/decide", headers=H(rt, "checker"),
                   json={"verdict": "APPROVED", "step_up_token": rt.idp.token("checker", step_up=True, challenge=chal)})
    assert one.status_code == 200 and one.json()["status"] == "AWAITING_APPROVAL", one.text
    chal2 = api.post(f"/api/v1/governance/change-requests/{cr_id}/challenge", headers=H(rt, "checker2"),
                     json={"verdict": "APPROVED"}).json()["challenge"]
    two = api.post(f"/api/v1/governance/change-requests/{cr_id}/decide", headers=H(rt, "checker2"),
                  json={"verdict": "APPROVED",
                        "step_up_token": rt.idp.token("checker2", step_up=True, challenge=chal2)})
    assert two.status_code == 200 and two.json()["status"] == "APPLIED", two.text


def test_profile_draft_rejects_short_rationale(fresh_cp_dsn):
    rt = Slice(fresh_cp_dsn)
    api = TestClient(create_app(rt))
    r = api.post("/api/v1/catalog/pipelines/p6_claims/profile/draft", headers=H(rt, "maker"),
                json={"platform": "duckdb", "rationale": "short"})
    assert r.status_code == 422, r.text


def test_profile_draft_unknown_pipeline_404(fresh_cp_dsn):
    rt = Slice(fresh_cp_dsn)
    api = TestClient(create_app(rt))
    r = api.post("/api/v1/catalog/pipelines/does-not-exist/profile/draft", headers=H(rt, "maker"),
                json={"platform": "duckdb", "rationale": "Clarify the platform label for the provider audit."})
    assert r.status_code == 404, r.text


def test_profile_draft_merges_onto_current_content_not_a_formula(fresh_cp_dsn):
    """The regression test for the before-build red team's CRITICAL fix (K7_K8_K9_DESIGN.md §2.1 point 1): wire a
    pipeline with a non-default `layer_map` and a `manifest_path` that does NOT match the mock's own
    `pipelines/<id>/target/manifest.json` naming formula. Edit ONLY `platform` through the route; confirm
    `layer_map`, `scope.manifest_path` and `adapter` are carried UNCHANGED in the resulting CR's stored item --
    never reconstructed from a formula, never defaulted to `{}`."""
    rt = Slice(fresh_cp_dsn)
    api = TestClient(create_app(rt))
    non_formula_manifest = "pipelines/tmsis_outbound/custom/path/manifest.json"
    non_default_layer_map = {"stg_eligibility": "SOURCE"}
    rt.govern([
        {"artifact_id": "ZZ:pipeline:tmsis_outbound", "kind": "PIPELINE", "op": "PUT",
         "content": {"adapter": "dbt_files", "scope": {"manifest_path": non_formula_manifest},
                     "platform": "snowflake", "domains": ["CLAIMS"], "layer_map": non_default_layer_map,
                     "run_on": "manual"}},
    ], approvers=("checker", "checker2"))

    r = api.post("/api/v1/catalog/pipelines/tmsis_outbound/profile/draft", headers=H(rt, "maker"),
                json={"platform": "duckdb", "rationale": "Switch the platform label only; everything else stays."})
    assert r.status_code == 200, r.text
    cr_id = r.json()["cr_id"]
    with rt.cp.as_role("dq_api") as c:
        row = c.execute("SELECT items FROM dq_ctl.config_change_requests WHERE cr_id=%s", (cr_id,)).fetchone()
    content = row["items"][0]["content"]
    assert content["platform"] == "duckdb"                                  # the one field the caller changed
    assert content["scope"]["manifest_path"] == non_formula_manifest        # carried unchanged, never reformulated
    assert content["layer_map"] == non_default_layer_map                    # carried unchanged, never defaulted to {}
    assert content["adapter"] == "dbt_files"
    assert content["domains"] == ["CLAIMS"]

    # same two-person mechanism as every other change request: the EXISTING challenge/decide routes, and the
    # applied artifact itself carries the merged content through to config_effective (not just the CR's own items)
    chal1 = api.post(f"/api/v1/governance/change-requests/{cr_id}/challenge", headers=H(rt, "checker"),
                     json={"verdict": "APPROVED"}).json()["challenge"]
    one = api.post(f"/api/v1/governance/change-requests/{cr_id}/decide", headers=H(rt, "checker"),
                   json={"verdict": "APPROVED",
                         "step_up_token": rt.idp.token("checker", step_up=True, challenge=chal1)})
    assert one.status_code == 200 and one.json()["status"] == "AWAITING_APPROVAL", one.text

    chal2 = api.post(f"/api/v1/governance/change-requests/{cr_id}/challenge", headers=H(rt, "checker2"),
                     json={"verdict": "APPROVED"}).json()["challenge"]
    two = api.post(f"/api/v1/governance/change-requests/{cr_id}/decide", headers=H(rt, "checker2"),
                   json={"verdict": "APPROVED",
                         "step_up_token": rt.idp.token("checker2", step_up=True, challenge=chal2)})
    assert two.status_code == 200 and two.json()["status"] == "APPLIED", two.text

    with rt.cp.as_role("dq_registry_svc") as c:
        row = c.execute("SELECT content FROM dq_ctl.config_effective WHERE artifact_id=%s",
                        ("ZZ:pipeline:tmsis_outbound",)).fetchone()
    # the platform genuinely flipped live, and every field the client never sent survived the round trip unchanged
    assert row["content"]["platform"] == "duckdb"
    assert row["content"]["scope"]["manifest_path"] == non_formula_manifest
    assert row["content"]["layer_map"] == non_default_layer_map
    assert row["content"]["adapter"] == "dbt_files"


def test_promote_gate_route_requires_governance_duty(fresh_cp_dsn):
    rt = Slice(fresh_cp_dsn)
    api = TestClient(create_app(rt))
    _wire_advisory_gate(rt)
    r = api.post("/api/v1/catalog/pipelines/tmsis_outbound/task-gates/ie_segment/promote",
                headers=H(rt, "snap"), json={"rationale": "Promote tmsis_outbound ie_segment to block"})
    assert r.status_code == 403


def test_promote_gate_route_unknown_gate_404(fresh_cp_dsn):
    rt = Slice(fresh_cp_dsn)
    api = TestClient(create_app(rt))
    r = api.post("/api/v1/catalog/pipelines/tmsis_outbound/task-gates/does-not-exist/promote",
                headers=H(rt, "maker"), json={"rationale": "Promote a gate that was never wired"})
    assert r.status_code == 404


def test_promote_gate_route_refuses_a_non_advisory_gate(fresh_cp_dsn):
    """Precision, not recall: the GatePicker only ever offers an advisory gate; the server enforces the same
    rule itself (never trusts the browser alone), refusing a gate that is already `block` (or any other mode)."""
    rt = Slice(fresh_cp_dsn)
    api = TestClient(create_app(rt))
    _wire_advisory_gate(rt, mode="block")      # brand-new, already block -- no promotion CR, no evidence needed
    r = api.post("/api/v1/catalog/pipelines/tmsis_outbound/task-gates/ie_segment/promote",
                headers=H(rt, "maker"), json={"rationale": "Promote a gate that is already block"})
    assert r.status_code == 422, r.text
    assert "not advisory" in r.json()["detail"]["reason"]
