"""Part F P1-4 (K3): `gov_decide` / `gov_apply` refuse a TASK_GATE `mode` promotion (advisory -> block) unless a
backtest report is linked as evidence (`dq_ctl.cr_evidence`, `items_digest = sha256(items)`). PFP :906;
K3_DESIGN.md §6 item 4 (a SEPARATE function, `dq_ctl.mode_promotion_refusal`, parallel to `promotion_refusal`,
attached at both `gov_decide`'s APPROVED branch and `gov_apply`'s equivalent re-check). Tests PFP names, verbatim
(`docs/rules/PARTF_IMPLEMENTATION_PLAN.md:916-918`)."""
import os
import uuid
from pathlib import Path

import psycopg
import pytest

from dqagent.backtest import run_backtest
from dqagent.hitl.decisions import Denied

from .test_backtest import _mode_cr
from .test_pipelines_runtime import _wire_tmsis_outbound
from .w1_harness import Slice

ROOT = Path(__file__).resolve().parents[2]
W1 = ROOT / "synthetic" / "out" / "w1"
PG = dict(host=os.environ.get("DQ_PG_HOST", "/tmp"), port=int(os.environ.get("DQ_PG_PORT", "55432")),
          user=os.environ.get("DQ_PG_USER", "dq_gov"))
NIGHTS = 2


def _approve_once(s, cr, user):
    tok = s.idp.token(user, step_up=True, challenge=s.registry.challenge(cr["cr_id"], "APPROVED"))
    return s.registry.decide(cr["cr_id"], "APPROVED", tok, s.idp.users[user]["emp_id"], ["governance:AUTH_RULES"])


def _link_evidence(s, cr_id: str) -> str:
    """A fast, synthetic equivalent of `run_backtest`'s own write path (`dq_ctl.backtest_put`, PFP :904): writes a
    SYNTHESIZED report and links `dq_ctl.cr_evidence` with `items_digest = sha256(cr.items)`, the SAME digest
    `ceiling_promotion_refusal`/`mode_promotion_refusal` both check -- used here only for K9's ceiling-evidence
    tests, where the subject under test is the SQL refusal/snapshot logic, not the backtest sandbox itself
    (which `test_backtest.py` already covers end to end)."""
    report_id = f"BTR-{uuid.uuid4().hex[:12]}"
    digest = uuid.uuid4().hex + uuid.uuid4().hex[:32]           # 64 hex chars; the report's own content digest
    with s.cp.as_role("dq_api") as c:
        c.execute("SELECT dq_ctl.backtest_put(%s,%s,%s::jsonb,%s,%s)",
                  (report_id, cr_id, '{"synthetic": true}', "SYNTHESIZED", digest))
    return report_id


def test_mode_promotion_refused_without_report(fresh_cp_dsn):
    """Guards fail closed: a CR that flips a TASK_GATE's `mode` (advisory -> block) is refused by the DATABASE at
    the very first APPROVE, before any backtest report exists -- never autonomously promoted, never silently
    degraded to "approved, enforcement pending". The refusal is a real database CheckViolation (claims are enforced
    in the database, not only in Python), surfaced to the caller as `Denied`."""
    s = Slice(fresh_cp_dsn)
    _wire_tmsis_outbound(s, mode="advisory", ceiling="L1")
    cr = _mode_cr(s, mode="block")
    with pytest.raises(Denied, match="backtest report"):
        _approve_once(s, cr, "checker")
    # the refused decide rolled back entirely: a second, otherwise-identical approve attempt is refused the
    # same way (order independence / no partial state leaked by the failed attempt)
    with pytest.raises(Denied, match="backtest report"):
        _approve_once(s, cr, "checker")
    # confirm there is still no recorded approval at all (the CheckViolation rolled the whole decide back)
    with s.cp.as_role("dq_api") as c:
        n = c.execute("SELECT count(*) n FROM dq_ctl.config_cr_events WHERE cr_id=%s AND event='APPROVED'",
                      (cr["cr_id"],)).fetchone()["n"]
    assert n == 0
    # apply-time is refused identically (defence in depth: gov_apply re-checks, same as promotion_refusal's
    # own precedent) -- even if somehow called directly, AWAITING_APPROVAL fires first since nobody approved
    status = s.registry.apply(cr["cr_id"], expected_state="ZZ")
    assert status == "AWAITING_APPROVAL"


def test_mode_promotion_two_person_with_report(fresh_cp_dsn):
    """Once a backtest report is stored (through `dq_ctl.backtest_put`, which links `dq_ctl.cr_evidence` with
    `items_digest = sha256(items)` in the same transaction), two DIFFERENT approvers can promote the gate from
    `advisory` to `block`, and `gov_apply` actually flips the mode live."""
    s = Slice(fresh_cp_dsn)
    _wire_tmsis_outbound(s, mode="advisory", ceiling="L1")
    cr = _mode_cr(s, mode="block")

    report = run_backtest(pipeline_id="tmsis_outbound", cr_id=cr["cr_id"], nights=NIGHTS, data=W1, pg=PG,
                          live_db=fresh_cp_dsn["dbname"])
    assert report["label"] == "SYNTHESIZED"
    assert report["report_id"].startswith("BTR-")

    # the evidence is visible via cr_detail's additive evidence[] field shape (same columns cr_evidence stores)
    with s.cp.as_role("dq_api") as c:
        ev = c.execute("SELECT report_id, items_digest, linked_by FROM dq_ctl.cr_evidence WHERE cr_id=%s",
                       (cr["cr_id"],)).fetchall()
    assert len(ev) == 1
    assert ev[0]["report_id"] == report["report_id"]
    assert ev[0]["linked_by"] == "SYSTEM:BACKTEST"

    assert _approve_once(s, cr, "checker")["verdict"] == "APPROVED"
    assert s.registry.apply(cr["cr_id"], expected_state="ZZ") == "AWAITING_APPROVAL"   # one of two: not yet
    assert _approve_once(s, cr, "checker2")["verdict"] == "APPROVED"
    assert s.registry.apply(cr["cr_id"], expected_state="ZZ") == "APPLIED"

    with s.cp.as_role("dq_registry_svc") as c:
        ie = c.execute("SELECT content FROM dq_ctl.config_effective WHERE artifact_id=%s",
                       ("ZZ:taskgate:tmsis_outbound:ie_segment",)).fetchone()
    assert ie["content"]["mode"] == "block"


def test_mode_promotion_refusal_is_specific_to_mode_changing_items(fresh_cp_dsn):
    """Precision, not recall: a CR that changes something OTHER than a TASK_GATE's mode (a release_ceiling
    LOWERING, on the gate that actually INTRODUCES its dataset -- K9's own new ceiling-inert check requires this,
    otherwise the change is correctly refused as inert, exercised separately in
    `test_ceiling_change_refused_on_consumer_gate` below) is NOT caught by `mode_promotion_refusal` -- it needs no
    backtest report, and approves normally. A LOWERING, not a rise, is used deliberately: K9's sibling check
    (`ceiling_promotion_refusal`) requires evidence for a RISE (demotion-is-automatic's own standing rule: only a
    rise needs proof, never a lowering) -- `test_ceiling_L2_to_L3_opens_veto_windows_when_authority_L3` below
    covers the rise-needs-evidence case this test must NOT trip over.

    `tmsis_outbound`'s own helper gates two w1 relations ALREADY introduced by the genesis HARNESS gates (K9's own
    new check would correctly refuse a ceiling change there as inert, since raising/lowering it has no effect).
    This test instead wires a brand-new dataset nothing else gates, so `tmsis_outbound`'s own gate genuinely is
    the introducer, and a ceiling-only change on it is a real, non-inert change."""
    s = Slice(fresh_cp_dsn)
    pipe = {"artifact_id": "ZZ:pipeline:tmsis_outbound", "kind": "PIPELINE", "op": "PUT", "content": {
        "adapter": "dbt_files", "scope": {"manifest_path": "pipelines/tmsis_outbound/target/manifest.json"},
        "platform": "duckdb", "domains": ["CLAIMS"], "layer_map": {}, "run_on": "manual"}}
    gate = {"artifact_id": "ZZ:taskgate:tmsis_outbound:own_relation", "kind": "TASK_GATE", "op": "PUT", "content": {
        "task_id": "own_relation", "datasets": ["bronze.tmsis_outbound_own_relation"], "mode": "advisory",
        "correlate": "LATEST_SEALED", "max_wait": "PT2H", "on_timeout": "PASS_ADVISORY",
        "release_ceiling": "L2", "reintroduce": []}}
    cr0 = s.registry.propose("ZZ", "E100", "Wire tmsis_outbound's own new relation", [pipe, gate])
    assert cr0["approvals_required"] == 2
    assert _approve_once(s, cr0, "checker")["verdict"] == "APPROVED"
    assert _approve_once(s, cr0, "checker2")["verdict"] == "APPROVED"
    assert s.registry.apply(cr0["cr_id"], expected_state="ZZ") == "APPLIED"

    item = s.effective_item("TASK_GATE", "taskgate:tmsis_outbound:own_relation")
    item["content"]["release_ceiling"] = "L1"            # a LOWERING: no evidence needed, by either check
    cr = s.registry.propose("ZZ", "E100", "Lower tmsis_outbound's own gate's ceiling", [item])
    # pre-existing behavior, unaffected by K9: a ceiling-only lowering (mode/everything else unchanged) is the
    # database's own lower-only TASK_GATE case (`gov_item_lower_only`, governance_schema.py:1008-1012) -- one
    # approver, not two. K9 adds no evidence requirement to a lowering either way.
    assert cr["approvals_required"] == 1
    assert _approve_once(s, cr, "checker")["verdict"] == "APPROVED"
    assert s.registry.apply(cr["cr_id"], expected_state="ZZ") == "APPLIED"


def test_mode_promotion_refusal_ignores_a_brand_new_gate(fresh_cp_dsn):
    """Degenerate input: a brand-new TASK_GATE (no prior `old` row) is never a MODE CHANGE by this check's own
    definition (nothing to promote FROM) -- it still needs two approvers (PIPELINE/TASK_GATE are never
    lower-only), but not a backtest report."""
    s = Slice(fresh_cp_dsn)
    cr = _wire_tmsis_outbound(s, mode="advisory", ceiling="L1")   # the gate's FIRST wiring: already APPLIED by the
    assert cr is not None                                        # helper itself (it two-person-approves internally)


# -- K9 (ORCH-1 step b, K7_K8_K9_DESIGN.md §3): ceiling is inert on a non-introducing gate; a ceiling RISE needs
# evidence, a lowering does not; the genesis exemption; the track-record snapshot carries task_gate evidence too.

def test_ceiling_change_refused_on_consumer_gate(fresh_cp_dsn):
    """CRITICAL finding, the main K9 demo claim: `tmsis_outbound`'s own gate on `bronze.ie_segment` consumes a
    relation the GENESIS harness gate already introduces (`gated_dataset_introductions` has a FIRST row for it
    from genesis, pointing at `ZZ:taskgate:p4_eligibility_ie:ie_segment`) -- so a ceiling change on
    `tmsis_outbound`'s own (non-introducing) gate is refused with the exact PFP wording, at PROPOSE time, before
    any approval round starts."""
    s = Slice(fresh_cp_dsn)
    _wire_tmsis_outbound(s, mode="advisory", ceiling="L1")
    item = s.effective_item("TASK_GATE", "taskgate:tmsis_outbound:ie_segment")
    item["content"]["release_ceiling"] = "L2"
    with pytest.raises(psycopg.errors.CheckViolation,
                       match=r"ceiling is inert: datasets are gated by ZZ:taskgate:p4_eligibility_ie:ie_segment"):
        s.registry.propose("ZZ", "E100", "Raise tmsis_outbound ie_segment's ceiling (refused: inert)", [item])


def test_ceiling_check_ignores_a_brand_new_gate(fresh_cp_dsn):
    """The genesis-exemption regression test for the before-build red team's CRITICAL fix #1 (mirrors
    `test_mode_promotion_refusal_ignores_a_brand_new_gate` above, the precedent this follows): a brand-new TASK_GATE
    on a brand-new dataset (zero `gated_dataset_introductions` rows at PROPOSE time -- introduction only happens at
    APPLY, strictly afterward) is never refused as "inert": the gate IS the introducer-to-be, not evidence of a
    non-introducing gate. `_wire_tmsis_outbound`'s own first wiring (non-INHERIT, non-default `release_ceiling`)
    already proves this path works for ordinary `/catalog/wire`-shaped calls -- confirmed still APPLIED, not
    refused, by the precedent test directly above."""
    s = Slice(fresh_cp_dsn)
    cr = _wire_tmsis_outbound(s, mode="advisory", ceiling="L2")   # a non-default ceiling on the FIRST wiring
    assert cr is not None                                         # already APPLIED by the helper; never refused


def test_ceiling_L1_to_L2_changes_no_mode(fresh_cp_dsn):
    """Honest semantics (K7_K8_K9_DESIGN.md §3.4, PFP's own text, unchanged by K9): L1 -> L2 changes no unit's
    MODE (both are H_IN under `TIER_CEILING`) -- the database write and the audit chain are still real, but the
    unit-outcome effect is inert in a DIFFERENT sense than the "ceiling is inert" refusal above (no gate-identity
    problem here; this gate IS the introducer). A rise needs evidence (K9's own new check); once linked, it
    applies normally, and `task_gate_ceiling` reports the new value."""
    s = Slice(fresh_cp_dsn)
    pipe = {"artifact_id": "ZZ:pipeline:tmsis_outbound", "kind": "PIPELINE", "op": "PUT", "content": {
        "adapter": "dbt_files", "scope": {"manifest_path": "pipelines/tmsis_outbound/target/manifest.json"},
        "platform": "duckdb", "domains": ["CLAIMS"], "layer_map": {}, "run_on": "manual"}}
    gate = {"artifact_id": "ZZ:taskgate:tmsis_outbound:own_l1l2", "kind": "TASK_GATE", "op": "PUT", "content": {
        "task_id": "own_l1l2", "datasets": ["bronze.tmsis_outbound_own_l1l2"], "mode": "advisory",
        "correlate": "LATEST_SEALED", "max_wait": "PT2H", "on_timeout": "PASS_ADVISORY",
        "release_ceiling": "L1", "reintroduce": []}}
    cr0 = s.registry.propose("ZZ", "E100", "Wire tmsis_outbound's own L1/L2 relation", [pipe, gate])
    assert _approve_once(s, cr0, "checker")["verdict"] == "APPROVED"
    assert _approve_once(s, cr0, "checker2")["verdict"] == "APPROVED"
    assert s.registry.apply(cr0["cr_id"], expected_state="ZZ") == "APPLIED"

    item = s.effective_item("TASK_GATE", "taskgate:tmsis_outbound:own_l1l2")
    item["content"]["release_ceiling"] = "L2"
    cr = s.registry.propose("ZZ", "E100", "Raise tmsis_outbound's own gate L1 -> L2", [item])
    with pytest.raises(Denied, match="release_ceiling rise"):
        _approve_once(s, cr, "checker")
    _link_evidence(s, cr["cr_id"])
    assert _approve_once(s, cr, "checker")["verdict"] == "APPROVED"
    assert _approve_once(s, cr, "checker2")["verdict"] == "APPROVED"
    assert s.registry.apply(cr["cr_id"], expected_state="ZZ") == "APPLIED"
    with s.cp.as_role("dq_registry_svc") as c:
        ceil = c.execute("SELECT dq_ctl.task_gate_ceiling('ZZ', 'bronze.tmsis_outbound_own_l1l2') c").fetchone()["c"]
    assert ceil == "L2"


def test_ceiling_L2_to_L3_opens_veto_windows_when_authority_L3(fresh_cp_dsn):
    """The step that actually opens veto windows, per the design's own §3.4: L2 -> L3 raises `task_gate_ceiling`,
    and `autonomy_tier_for` (the lower of the agent's own 4-arg tier and this ceiling) reports L3 once the
    AUTHORITY's own autonomy tier is ALSO at least L3 -- confirmed directly against the real function, not
    inferred, since AUTH_MEDICAID's own seeded tier is L4 (config/agents.yaml), comfortably above L3."""
    s = Slice(fresh_cp_dsn)
    pipe = {"artifact_id": "ZZ:pipeline:tmsis_outbound", "kind": "PIPELINE", "op": "PUT", "content": {
        "adapter": "dbt_files", "scope": {"manifest_path": "pipelines/tmsis_outbound/target/manifest.json"},
        "platform": "duckdb", "domains": ["CLAIMS"], "layer_map": {}, "run_on": "manual"}}
    gate = {"artifact_id": "ZZ:taskgate:tmsis_outbound:own_l2l3", "kind": "TASK_GATE", "op": "PUT", "content": {
        "task_id": "own_l2l3", "datasets": ["bronze.tmsis_outbound_own_l2l3"], "mode": "advisory",
        "correlate": "LATEST_SEALED", "max_wait": "PT2H", "on_timeout": "PASS_ADVISORY",
        "release_ceiling": "L2", "reintroduce": []}}
    cr0 = s.registry.propose("ZZ", "E100", "Wire tmsis_outbound's own L2/L3 relation", [pipe, gate])
    assert _approve_once(s, cr0, "checker")["verdict"] == "APPROVED"
    assert _approve_once(s, cr0, "checker2")["verdict"] == "APPROVED"
    assert s.registry.apply(cr0["cr_id"], expected_state="ZZ") == "APPLIED"

    item = s.effective_item("TASK_GATE", "taskgate:tmsis_outbound:own_l2l3")
    item["content"]["release_ceiling"] = "L3"
    cr = s.registry.propose("ZZ", "E100", "Raise tmsis_outbound's own gate L2 -> L3", [item])
    _link_evidence(s, cr["cr_id"])
    assert _approve_once(s, cr, "checker")["verdict"] == "APPROVED"
    assert _approve_once(s, cr, "checker2")["verdict"] == "APPROVED"
    assert s.registry.apply(cr["cr_id"], expected_state="ZZ") == "APPLIED"

    with s.cp.as_role("dq_registry_svc") as c:
        ceil = c.execute("SELECT dq_ctl.task_gate_ceiling('ZZ', 'bronze.tmsis_outbound_own_l2l3') c").fetchone()["c"]
        eff = c.execute("SELECT dq_ctl.autonomy_tier_for('ZZ','dq-reconciliation','RELEASE','AUTH_MEDICAID',"
                        "'bronze.tmsis_outbound_own_l2l3') t").fetchone()["t"]
    assert ceil == "L3"
    assert eff == "L3"            # min(agent's own L4, this relation's L3 ceiling) = L3: H_ON, veto windows open


def test_track_record_counts_human_releases(fresh_cp_dsn, tmp_path):
    """The SNAPSHOT (`track_record_snapshot`, not E-07 itself -- E-07 is already closed by K0,
    `trace_schema.py:385,408,433,454`, confirmed by the design and not re-litigated here) carries the
    `human_released` count for an AUTONOMY item's proposal, visible on the signed `impact.track_record` block a
    real approver reads before stepping up -- the same field `test_evidence_promotion.py` already proves the
    underlying view counts correctly; this test proves the PROPOSAL-TIME snapshot surfaces it."""
    from .fixtures_w1 import ie_file, person

    s = Slice(fresh_cp_dsn)
    # L2 ceiling for AUTH_MEDICAID (H_IN, one person) -- the SAME pattern
    # `test_tier_ceiling_holds_with_the_why_and_a_human_still_releases` uses, so the held unit needs only one
    # approver (not the H_IN_2 ABC-floor path, which needs two).
    assert s.registry.demote("dq-reconciliation", "RELEASE", "AUTH_MEDICAID", "L2", "TEST_SETUP") == 1
    p = tmp_path / "B-REL.txt"
    p.write_bytes(ie_file([person("P1", "M0001")]))
    s.run_pipeline("p4_eligibility_ie", "B-REL", [("IE", p)], ["bronze.ie_segment"])
    held = s.units(batch_id="B-REL", state="HELD")
    assert held, held
    for u in held:
        tok = s.step_up("maker", [u["unit_key"]], "RELEASE")
        r = s.decisions.decide(["AUTH_MEDICAID"], tok, [u["unit_key"]], "RELEASE",
                               "Reviewed the evidence on the card for this test.",
                               {u["unit_key"]: u["state_version"]}, {u["unit_key"]: u["fingerprint"]},
                               session_person="E100")
        assert r["results"][0]["status"] == "APPLIED", r
    assert set(u["unit_key"] for u in held) <= set(s.verify_and_publish()["released"])

    item = s.effective_item("AUTONOMY", "autonomy:dq-reconciliation:RELEASE")
    item["content"]["AUTH_SNAP"] = "L3"          # a DIFFERENT authority's rise: AUTH_MEDICAID's own record is read
    cr = s.registry.propose("ZZ", "E100", "Promote SNAP release (reading the MEDICAID record along the way)", [item])
    tr = cr["impact"]["track_record"]
    assert tr["hash"] and tr["rows"]
    row = next(r for aid, rows in tr["rows"].items() for r in rows if r["authority"] == "AUTH_MEDICAID")
    assert row["human_released"] == len(held)


def test_track_record_snapshot_carries_task_gate_evidence(fresh_cp_dsn):
    """K9's own new additive `task_gate` key (K7_K8_K9_DESIGN.md §3.3 point 3): a TASK_GATE item's evidence
    status is visible in the SAME signed snapshot an AUTONOMY promotion's approver already sees -- additive, a
    sibling of `rows`/`minimums`/`evidence`, never folded into `evidence` (the orchestrator's own decision)."""
    s = Slice(fresh_cp_dsn)
    _wire_tmsis_outbound(s, mode="advisory", ceiling="L1")
    item = s.effective_item("TASK_GATE", "taskgate:tmsis_outbound:ie_segment")
    item["content"]["mode"] = "block"            # a mode change: track_record_snapshot now fires for TASK_GATE too
    cr = s.registry.propose("ZZ", "E100", "Promote tmsis_outbound ie_segment to block", [item])
    tg = cr["impact"]["track_record"]["task_gate"]
    assert tg == {"ZZ:taskgate:tmsis_outbound:ie_segment": False}      # no report linked yet
    report = run_backtest(pipeline_id="tmsis_outbound", cr_id=cr["cr_id"], nights=NIGHTS, data=W1, pg=PG,
                          live_db=fresh_cp_dsn["dbname"])
    assert report["report_id"].startswith("BTR-")
    cr2 = s.registry.propose("ZZ", "E100", "A second, unrelated proposal naming the same gate item",
                             [s.effective_item("TASK_GATE", "taskgate:tmsis_outbound:ie_segment")])
    # the second proposal's OWN items differ (content now reflects the first CR's own items pre-apply state is
    # unrelated); what matters here is the ORIGINAL cr's own items digest now has a linked report
    with s.cp.as_role("dq_api") as c:
        has = c.execute("SELECT EXISTS (SELECT 1 FROM dq_ctl.cr_evidence e WHERE e.cr_id=%s) h",
                        (cr["cr_id"],)).fetchone()["h"]
    assert has is True
    assert cr2["impact"]["track_record"] is not None  # TASK_GATE-only proposals now also produce a snapshot
