"""Part F P1-3 (K2b): the `modes.autonomy_for`/`evaluate.py` call-site changes, the PIPELINE_RUN span, the scoped
reopen (`_affected_relations`), and the genesis-seeded harness composition. Tests PFP names, verbatim
(`docs/rules/PARTF_IMPLEMENTATION_PLAN.md:862-869`)."""
import copy

from dqagent.harness.plan import order

from .fixtures_w1 import ie_file, person
from .w1_harness import Slice


def _approve(s, cr, *users):
    for u in users:
        tok = s.idp.token(u, step_up=True, challenge=s.registry.challenge(cr["cr_id"], "APPROVED"))
        s.registry.decide(cr["cr_id"], "APPROVED", tok, s.idp.users[u]["emp_id"], ["governance:AUTH_RULES"])
    return s.apply_change(cr["cr_id"])


def _wire_tmsis_outbound(s, mode="advisory", ceiling="L1"):
    """Wire a new consumer PIPELINE + TASK_GATE pair on the two w1 relations the demo moment names
    (`bronze.ie_segment`, `bronze.encounter_header`), per PFP's own golden path (K2_DESIGN.md §5)."""
    pipe = {"artifact_id": "ZZ:pipeline:tmsis_outbound", "kind": "PIPELINE", "op": "PUT", "content": {
        "adapter": "dbt_files", "scope": {"manifest_path": "pipelines/tmsis_outbound/target/manifest.json"},
        "platform": "duckdb", "domains": ["CLAIMS"], "layer_map": {}, "run_on": "manual"}}
    gate_ie = {"artifact_id": "ZZ:taskgate:tmsis_outbound:ie_segment", "kind": "TASK_GATE", "op": "PUT", "content": {
        "task_id": "ie_segment", "datasets": ["bronze.ie_segment"], "mode": mode, "correlate": "LATEST_SEALED",
        "max_wait": "PT2H", "on_timeout": "PASS_ADVISORY" if mode == "advisory" else "HOLD",
        "release_ceiling": ceiling, "reintroduce": []}}
    gate_enc = {"artifact_id": "ZZ:taskgate:tmsis_outbound:encounter_header", "kind": "TASK_GATE", "op": "PUT",
               "content": {"task_id": "encounter_header", "datasets": ["bronze.encounter_header"], "mode": mode,
                           "correlate": "LATEST_SEALED", "max_wait": "PT2H",
                           "on_timeout": "PASS_ADVISORY" if mode == "advisory" else "HOLD",
                           "release_ceiling": ceiling, "reintroduce": []}}
    cr = s.registry.propose("ZZ", "E100", "Wire tmsis_outbound as a consumer of w1 relations", [pipe, gate_ie, gate_enc])
    assert cr["approvals_required"] == 2            # PIPELINE is never lower-only (PFP :753)
    status = _approve(s, cr, "checker", "checker2")
    assert status == "APPLIED"
    return cr


def _snapshot_tiers(s, relations):
    """{(relation, authority): (task_gate_ceiling, autonomy_tier_for)} for AUTH_MEDICAID, the authority both w1
    relations carry (I-10's own comparison set)."""
    agent, auth = "dq-reconciliation", "AUTH_MEDICAID"
    out = {}
    with s.cp.as_role("dq_registry_svc") as c:
        for rel in relations:
            ceiling = c.execute("SELECT dq_ctl.task_gate_ceiling('ZZ', %s) t", (rel,)).fetchone()["t"]
            tier = c.execute("SELECT dq_ctl.autonomy_tier_for('ZZ', %s, 'RELEASE', %s, %s) t",
                             (agent, auth, rel)).fetchone()["t"]
            out[rel] = (ceiling, tier)
    return out


W1_RELATIONS = ("bronze.ie_segment", "bronze.enrollment_834", "bronze.encounter_header", "bronze.encounter_line")


def test_consumer_gate_of_gated_datasets_changes_no_autonomy(fresh_cp_dsn):
    """I-10, ORCH-1 (PFP :863-866). (a) Under the test-genesis seed, wiring tmsis_outbound as an advisory L1
    consumer of two ALREADY-gated w1 relations changes neither `task_gate_ceiling` nor `autonomy_tier_for` for
    those relations: they are already introduced by the genesis block gates, so the new gate's PUT hits ORCH-1's
    "already introduced" case, never "FIRST" (K2_DESIGN.md §5, the package's own highest-leverage dependency).
    Negative control: a gate that INTRODUCES a brand-new dataset (never gated before) at L1 DOES move that
    dataset's ceiling -- the mechanism is live, not merely absent-by-accident."""
    s = Slice(fresh_cp_dsn)
    before = _snapshot_tiers(s, W1_RELATIONS)
    _wire_tmsis_outbound(s, mode="advisory", ceiling="L1")
    after = _snapshot_tiers(s, W1_RELATIONS)
    assert before == after                     # (a): the effective tier on every w1 relation is unchanged

    # negative control: a gate introducing a dataset NEVER gated before (not one of the 4 w1 relations) at L1
    # DOES move that dataset's own ceiling -- confirming the no-change result above is not a dead code path.
    new_ds = "bronze.never_gated_before"
    fresh_gate = {"artifact_id": "ZZ:taskgate:tmsis_outbound:never_gated", "kind": "TASK_GATE", "op": "PUT",
                 "content": {"task_id": "never_gated", "datasets": [new_ds], "mode": "block",
                             "correlate": "LATEST_SEALED", "max_wait": "PT2H", "on_timeout": "HOLD",
                             "release_ceiling": "L1", "reintroduce": []}}
    cr = s.registry.propose("ZZ", "E100", "Introduce a brand-new dataset at L1", [fresh_gate])
    assert _approve(s, cr, "checker", "checker2") == "APPLIED"
    with s.cp.as_role("dq_registry_svc") as c:
        moved = c.execute("SELECT dq_ctl.task_gate_ceiling('ZZ', %s) t", (new_ds,)).fetchone()["t"]
    assert moved == "L1"                       # the negative control: THIS dataset's ceiling DID move


def test_composed_harness_equals_yaml(fresh_cp_dsn):
    """PFP :810: `cfg_from_artifacts` recomposes `harness` from the genesis-seeded HARNESS artifact unchanged --
    the input to `_load_effective`/`plan.py`/`pipelines_view` is identical to `config/harness_w1.yaml`'s own
    content, whatever PIPELINE/TASK_GATE items genesis also seeds alongside it."""
    import yaml

    from dqagent.runtime import ROOT
    s = Slice(fresh_cp_dsn)
    file_harness = yaml.safe_load(open(ROOT / "config" / "harness_w1.yaml"))
    assert s.harness == file_harness
    assert order(s.harness) == order(file_harness)
    # the OLD endpoint's own pinned assertion (B7, test_api_contract.py) stays true: K2 adds governance items but
    # does not change harness_w1.yaml's 2 pipelines
    assert [p["id"] for p in s.harness["pipelines"]] == ["p4_eligibility_ie", "p6_claims"]


def test_wiring_consumer_reopens_nothing(fresh_cp_dsn, tmp_path):
    """PFP :815, `_affected_relations` (K2b). A consumer of datasets that are already gated reopens nothing: wiring
    tmsis_outbound moves no unit out of its current state (no RE_EVALUATE_PENDING transition is recorded for any
    unit of bronze.ie_segment / bronze.encounter_header)."""
    s = Slice(fresh_cp_dsn, dp_path=str(tmp_path / "dp.duckdb"))
    p = tmp_path / "b1.txt"
    p.write_bytes(ie_file([person("P1", "M0001"), person("P2", "M0002")]))
    s.run_pipeline("p4_eligibility_ie", "B1", [("IE", p)], ["bronze.ie_segment"])
    s.verify_and_publish()
    with s.cp.as_role("dq_eval_svc") as c:
        before = {r["unit_key"]: r["state_version"] for r in c.execute(
            "SELECT unit_key, state_version FROM dq_ctl.units WHERE node='bronze.ie_segment'").fetchall()}
    assert before                                 # the batch actually created units to watch
    _wire_tmsis_outbound(s, mode="advisory", ceiling="L1")
    with s.cp.as_role("dq_eval_svc") as c:
        after = {r["unit_key"]: r["state_version"] for r in c.execute(
            "SELECT unit_key, state_version FROM dq_ctl.units WHERE node='bronze.ie_segment'").fetchall()}
    assert before == after                        # no unit's state_version moved: nothing was reopened


def test_genesis_still_needs_two(fresh_cp_dsn, tmp_path, monkeypatch):
    """PFP :862 (via EXECUTION_PLAN.md's K2b scope: the GOVERNED genesis path, now carrying PIPELINE/TASK_GATE
    items, still needs two approvers -- genesis already needed 2 for its brand-new AUTONOMY grants
    (WIRING_ARCHITECTURE.md §5's own stated invariant), and the PIPELINE/TASK_GATE items added by K2a's split do
    not lower that requirement."""
    for k in ("DQ_K_EV", "DQ_K_FP", "DQ_K_ABC"):
        monkeypatch.setenv(k, f"pilot-{k.lower()}-key")
    from dqagent.runtime import Runtime
    rt = Runtime(fresh_cp_dsn, dp_path=str(tmp_path / "dp.duckdb"), deployment="pilot")
    import psycopg
    with psycopg.connect(**fresh_cp_dsn) as c:
        for emp, duty, auth in (("E100", "governance", "AUTH_RULES"), ("E200", "governance", "AUTH_RULES"),
                                ("E210", "governance", "AUTH_RULES")):
            c.execute("INSERT INTO dq_ctl.roster(person_id,duty,authority,action,decision_ref) VALUES "
                      "(%s,%s,%s,'GRANT','ROSTER-FIXTURE')", (emp, duty, auth))
        c.commit()
    cr = rt.registry.propose_genesis("ZZ", "E100")
    assert cr["approvals_required"] == 2
    tok = rt.idp.token("checker", step_up=True, challenge=rt.registry.challenge(cr["cr_id"], "APPROVED"))
    rt.registry.decide(cr["cr_id"], "APPROVED", tok, "E200", ["governance:AUTH_RULES"])
    assert rt.apply_change(cr["cr_id"]) == "AWAITING_APPROVAL" and not rt.ratified
    tok = rt.idp.token("checker2", step_up=True, challenge=rt.registry.challenge(cr["cr_id"], "APPROVED"))
    rt.registry.decide(cr["cr_id"], "APPROVED", tok, "E210", ["governance:AUTH_RULES"])
    assert rt.apply_change(cr["cr_id"]) == "APPLIED" and rt.ratified
    # the PIPELINE/TASK_GATE items genesis seeded (K2a) are present and did not need a different approval count
    with rt.cp.as_role("dq_registry_svc") as c:
        kinds = {r["kind"] for r in c.execute(
            "SELECT DISTINCT kind FROM dq_ctl.config_effective WHERE state='ZZ' AND op='PUT'").fetchall()}
    assert {"PIPELINE", "TASK_GATE"} <= kinds
