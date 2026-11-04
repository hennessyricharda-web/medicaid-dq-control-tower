"""Part F R4-5 (DEC-023, DEC-024): evidence-gated promotion with a continuous track record.

A promotion (an AUTONOMY tier rank that rises) is refused by the DATABASE, at approval and again at apply, unless
(a) a confirmed evidence bundle is complete for the running code version (test / demo: the deterministic half;
pilot / production: deterministic AND a LIVE explainer run on the configured prompt), and (b) the current code
version's record meets the target tier's governed minimums (POLICY promotion_minimums). Demotion needs neither. The
track record never resets: it is segmented by code version."""
import json
from pathlib import Path

import psycopg
import pytest

from dqagent.engine.cp import CP
from dqagent.hitl.decisions import Denied
from dqagent.llm import PROMPT_VERSION
from dqagent.obs import tracer

from .evidence import attest, confirm, grant_evidence, record_run, set_minimums
from .w1_harness import Slice

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "synthetic" / "out" / "w2_earn"
MAN = json.loads((DATA / "earn_manifest.json").read_text())
AGENT, AUTH, MED = MAN["agent"], MAN["authority"], [MAN["authority"]]


def _load(s, batch_id):
    f = next(b["file"] for b in MAN["ie_batches"] if b["batch_id"] == batch_id)
    s.run_pipeline("p4_eligibility_ie", batch_id, [("IE", DATA / f)], ["bronze.ie_segment"])
    s.verify_and_publish()


def _approve_held(s, batch_id, n=None):
    held = sorted((u for u in s.units(batch_id=batch_id) if u["authorities"] == MED and u["state"] == "HELD"),
                  key=lambda u: u["unit_key"])[:n]
    keys = [u["unit_key"] for u in held]
    tok = s.step_up("maker", keys, "RELEASE")
    s.decisions.decide(MED, tok, keys, "RELEASE", "Reviewed the evidence; recommended release is correct.",
                       {u["unit_key"]: u["state_version"] for u in held},
                       {u["unit_key"]: u["fingerprint"] for u in held}, session_person="E100")
    s.verify_and_publish()
    return keys


def _promotion(s, tier="L3", auth=AUTH):
    item = s.effective_item("AUTONOMY", f"autonomy:{AGENT}:RELEASE")
    item["content"][auth] = tier
    return s.registry.propose("ZZ", "E100", "Promote release autonomy with the attached record", [item])


def _approve(s, cr, user):
    tok = s.idp.token(user, step_up=True, challenge=s.registry.challenge(cr["cr_id"], "APPROVED"))
    s.registry.decide(cr["cr_id"], "APPROVED", tok, s.idp.users[user]["emp_id"], ["governance:AUTH_RULES"])
    return s.apply_change(cr["cr_id"])


def _at_l2(s):
    assert s.registry.demote(AGENT, "RELEASE", AUTH, "L2", "TEST_SETUP") == 1
    assert s.registry.tier("ZZ", AGENT, "RELEASE", AUTH) == "L2"


def test_promotion_refused_without_evidence_bundle(fresh_cp_dsn, tmp_path):
    s = Slice(fresh_cp_dsn, dp_path=str(tmp_path / "dp.duckdb"))
    _at_l2(s)
    _load(s, "B-ie_extract_20260401")
    _approve_held(s, "B-ie_extract_20260401")                       # a record far above the demo minimum
    cr = _promotion(s)
    assert cr["impact"]["track_record"]["evidence"]["complete"] is False
    with pytest.raises(Denied, match=r"promotion requires complete evidence for code [0-9a-f]{12}: deterministic ✗"):
        _approve(s, cr, "checker")
    assert s.registry.tier("ZZ", AGENT, "RELEASE", AUTH) == "L2"
    att = attest(s, record_run(s))                                   # proposed, not confirmed: still no evidence
    with pytest.raises(Denied, match="deterministic ✗"):
        _approve(s, cr, "checker")
    confirm(s, att)
    assert _approve(s, cr, "checker") == "AWAITING_APPROVAL"
    assert _approve(s, cr, "checker2") == "APPLIED" and s.registry.tier("ZZ", AGENT, "RELEASE", AUTH) == "L3"


def test_promotion_refused_below_minimum_record(fresh_cp_dsn, tmp_path):
    s = Slice(fresh_cp_dsn, dp_path=str(tmp_path / "dp.duckdb"))
    _at_l2(s)
    grant_evidence(s)
    _load(s, "B-ie_extract_20260411")
    _approve_held(s, "B-ie_extract_20260411")                        # 3 human-approved releases; the demo L3 needs 5
    cr = _promotion(s)
    with pytest.raises(Denied, match="promotion requires a minimum record: 3 of 5 human-approved releases"):
        _approve(s, cr, "checker")
    _load(s, "B-ie_extract_20260415")
    _approve_held(s, "B-ie_extract_20260415")                        # 4
    with pytest.raises(Denied, match="4 of 5 human-approved releases"):
        _approve(s, cr, "checker")
    _load(s, "B-ie_extract_20260401")
    _approve_held(s, "B-ie_extract_20260401", n=1)                   # 5: meets the demo minimum
    assert _approve(s, cr, "checker") == "AWAITING_APPROVAL"
    assert _approve(s, cr, "checker2") == "APPLIED"


def test_demo_deterministic_bundle_suffices(fresh_cp_dsn, tmp_path):
    s = Slice(fresh_cp_dsn, dp_path=str(tmp_path / "dp.duckdb"))   # a test deployment: deterministic half only
    _at_l2(s)
    _load(s, "B-ie_extract_20260401")
    _approve_held(s, "B-ie_extract_20260401")
    grant_evidence(s, llm=False)
    cr = _promotion(s)
    ev = cr["impact"]["track_record"]["evidence"]
    assert ev["complete"] and ev["deterministic"] and not ev["llm"] and not ev["llm_required"]
    assert cr["impact"]["track_record"]["minimums"]["label"] == "demo minimums"
    assert _approve(s, cr, "checker") == "AWAITING_APPROVAL" and _approve(s, cr, "checker2") == "APPLIED"


def _raw_plane(dsn, deployment):
    """A control plane bound directly in SQL (no runtime: production needs an OIDC IdP, L-16), with the governance
    roster the attestation functions require."""
    with psycopg.connect(**dsn) as c:
        c.execute("INSERT INTO dq_ctl.deployment_binding(genesis_kind, deployment, idp_kind) VALUES "
                  "('GOVERNED', %s, 'OIDC')", (deployment,))
        for emp in ("E100", "E200"):
            c.execute("INSERT INTO dq_ctl.roster(person_id,duty,authority,action,decision_ref) "
                      "VALUES (%s,'governance','AUTH_RULES','GRANT','TEST')", (emp,))
    return CP(dsn)


def _status(cp):
    with cp.as_role("dq_registry_svc") as c:
        c.execute("SELECT set_config('dq.prompt_version', %s, true)", (PROMPT_VERSION,))
        return c.execute("SELECT dq_ctl.evidence_status() e").fetchone()["e"]


def test_production_needs_det_and_live_llm(fresh_cp_dsn):
    cp = _raw_plane(fresh_cp_dsn, "production")
    s = type("S", (), {"cp": cp})()
    att = attest(s, record_run(s))
    confirm(s, att)
    st = _status(cp)
    assert st["deployment"] == "production" and st["deterministic"] and not st["llm"] and not st["complete"]
    item = {"artifact_id": f"ZZ:autonomy:{AGENT}:RELEASE", "kind": "AUTONOMY", "op": "PUT", "content": {AUTH: "L3"}}
    with cp.as_role("dq_registry_svc") as c:
        c.execute("SELECT set_config('dq.prompt_version', %s, true)", (PROMPT_VERSION,))
        # origin 'HUMAN': an operational promotion, not GENESIS's one-time exemption (K1 defect fix)
        msg = c.execute("SELECT dq_ctl.promotion_refusal('ZZ', %s::jsonb, 'HUMAN') m",
                        (json.dumps([item]),)).fetchone()["m"]
    assert msg.endswith("deterministic ✓ / live LLM ✗"), msg
    llm = record_run(s, suite="explainer", source_mode="LIVE", prompt_version="p-other")
    confirm(s, attest(s, record_run(s), llm))
    assert not _status(cp)["complete"]                               # an LLM half on ANOTHER prompt is not evidence
    llm = record_run(s, suite="explainer", source_mode="LIVE", prompt_version=PROMPT_VERSION)
    confirm(s, attest(s, record_run(s), llm))
    st = _status(cp)
    assert st["deterministic"] and st["llm"] and st["complete"]
    with cp.as_role("dq_registry_svc") as c:                          # evidence passes; the record is now the gate
        c.execute("SELECT set_config('dq.prompt_version', %s, true)", (PROMPT_VERSION,))
        msg = c.execute("SELECT dq_ctl.promotion_refusal('ZZ', %s::jsonb, 'HUMAN') m",
                        (json.dumps([item]),)).fetchone()["m"]
    assert msg == "promotion requires a minimum record: 0 of 20 human-approved releases"   # production defaults


@pytest.mark.parametrize("mode", ["SCRIPTED", "TEMPLATE", "RECORDED"])
def test_scripted_or_template_llm_run_rejected_by_eval_attest(fresh_cp_dsn, mode):
    s = Slice(fresh_cp_dsn)
    det = record_run(s)
    bad = record_run(s, suite="explainer", source_mode=mode, prompt_version=PROMPT_VERSION)
    with pytest.raises(psycopg.errors.CheckViolation, match="not evidence"):
        attest(s, det, bad)
    for kw in ({"source_mode": "SCRIPTED"}, {"passed": 11}, {"code": "e" * 64}, {"suite": "explainer"}):
        run = record_run(s, **kw)
        with pytest.raises(psycopg.errors.CheckViolation, match="deterministic half"):
            attest(s, run)
    live_no_prompt = record_run(s, suite="explainer", source_mode="LIVE")
    with pytest.raises(psycopg.errors.CheckViolation, match="recorded prompt version"):
        attest(s, det, live_no_prompt)
    with s.cp.as_role("dq_api") as c:                                 # the caller never supplies the hashes
        row = c.execute("SELECT * FROM dq_ctl.eval_attestations WHERE id=%s", (attest(s, det),)).fetchone()
    assert row["tests"] == 12 and row["failures"] == 0 and row["det_run_id"] == det and row["junit_sha256_llm"] is None


def test_track_record_never_resets_and_segments_by_version(fresh_cp_dsn, tmp_path, monkeypatch):
    s = Slice(fresh_cp_dsn, dp_path=str(tmp_path / "dp.duckdb"))
    _at_l2(s)
    _load(s, "B-ie_extract_20260411")
    first = _approve_held(s, "B-ie_extract_20260411")                 # 3 under the running code
    other = "f" * 64
    monkeypatch.setattr(tracer, "code_sha256", lambda: other)        # a NEW code version writes the next events
    _load(s, "B-ie_extract_20260415")
    second = _approve_held(s, "B-ie_extract_20260415")               # 1 under the new code
    with s.cp.as_role("dq_registry_svc") as c:
        vs = {r["code_sha256"]: r for r in c.execute(
            "SELECT * FROM dq_ctl.track_record_by_version WHERE agent_id=%s AND action_class='RELEASE' AND authority=%s",
            (AGENT, AUTH)).fetchall()}
        tr = c.execute("SELECT * FROM dq_ctl.track_record WHERE agent_id=%s AND action_class='RELEASE' AND authority=%s",
                       (AGENT, AUTH)).fetchone()
    assert vs[other]["human_released"] == len(second) and sum(v["human_released"] for v in vs.values()) == \
        len(first) + len(second)
    assert tr["human_released"] == len(first) + len(second)          # the total spans every version
    assert tr["current_version"]["code_sha256"] == other and tr["current_version"]["human_released"] == len(second)
    assert tr["prior_autonomous"] == 0
    grant_evidence(s)                                                # an attestation is evidence, never a reset
    with s.cp.as_role("dq_registry_svc") as c:
        after = c.execute("SELECT human_released, current_version FROM dq_ctl.track_record WHERE agent_id=%s "
                          "AND action_class='RELEASE' AND authority=%s", (AGENT, AUTH)).fetchone()
    assert after["human_released"] == tr["human_released"] and after["current_version"] == tr["current_version"]
    cr = _promotion(s)                                               # the new version has 1 of 5: refused
    with pytest.raises(Denied, match="1 of 5 human-approved releases"):
        _approve(s, cr, "checker")


def test_demotion_needs_no_evidence(fresh_cp_dsn):
    s = Slice(fresh_cp_dsn)
    with s.cp.as_role("dq_registry_svc") as c:
        assert c.execute("SELECT dq_ctl.evidence_status() e").fetchone()["e"]["complete"] is False
    assert s.registry.tier("ZZ", AGENT, "RELEASE", AUTH) == "L4"
    down = s.effective_item("AUTONOMY", f"autonomy:{AGENT}:RELEASE")
    down["content"][AUTH] = "L3"                                     # a governed lowering: one approver, no evidence
    cr = s.registry.propose("ZZ", "E100", "Lower Medicaid release to supervised", [down])
    assert cr["approvals_required"] == 1 and _approve(s, cr, "checker") == "APPLIED"
    assert s.registry.demote(AGENT, "RELEASE", AUTH, "L2", "VETO") == 1          # the automatic path
    assert s.registry.tier("ZZ", AGENT, "RELEASE", AUTH) == "L2"


def test_raising_minimums_one_person_lowering_two(fresh_cp_dsn):
    s = Slice(fresh_cp_dsn)
    item = s.effective_item("POLICY", "policy")
    mins = item["content"]["promotion_minimums"]
    assert mins["label"] == "demo minimums" and mins["L3"]["human_releases"] == 5

    def with_l3(v, key="human_releases"):
        it = s.effective_item("POLICY", "policy")
        it["content"]["promotion_minimums"]["L3"][key] = v
        return it
    up = s.registry.propose("ZZ", "E100", "Raise the L3 minimum to six releases", [with_l3(6)])
    assert not up["loosening"] and up["approvals_required"] == 1
    assert _approve(s, up, "checker") == "APPLIED"
    for it, why in ((with_l3(4), "lowers the promotion minimum"), (with_l3(1, "overrides"), "lowers the promotion")):
        cr = s.registry.propose("ZZ", "E100", "Lower an L3 promotion minimum", [it])
        assert cr["loosening"] and cr["approvals_required"] == 2 and any(why in w for w in cr["impact"]["loosening_reasons"])
    gone = s.effective_item("POLICY", "policy")
    del gone["content"]["promotion_minimums"]["L4"]
    assert s.registry.propose("ZZ", "E100", "Drop the L4 minimums", [gone])["approvals_required"] == 2
    with s.cp.as_role("dq_api") as c:                                 # a forged "not loosening" flag changes nothing
        c.execute("SELECT dq_ctl.gov_propose('CR-FORGED-MIN','ZZ','E100','HUMAN','forged lowering',%s::jsonb,"
                  "'{}'::jsonb,'{}'::jsonb,false,'h','{}'::jsonb,NULL)", (json.dumps([with_l3(2)]),))
        r = c.execute("SELECT approvals_required FROM dq_ctl.config_change_requests WHERE cr_id='CR-FORGED-MIN'"
                      ).fetchone()
    assert r["approvals_required"] == 2
    set_minimums(s, {**mins, "L3": {**mins["L3"], "human_releases": 5}})   # back down: two approvers, applied


def test_old_6arg_eval_attest_signature_gone(fresh_cp_dsn):
    s = Slice(fresh_cp_dsn)
    with s.cp.as_role("dq_api") as c:
        sigs = [r["a"] for r in c.execute(
            "SELECT pg_get_function_identity_arguments(p.oid) a FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace "
            "WHERE n.nspname='dq_ctl' AND p.proname='eval_attest'").fetchall()]
    assert sigs == ["p_label text, p_code_sha256 text, p_det_run_id text, p_llm_run_id text, p_person text"]
    with s.cp.as_role("dq_api") as c:
        with pytest.raises(psycopg.errors.UndefinedFunction):
            c.execute("SELECT dq_ctl.eval_attest('x',%s,1,0,%s,'E100')", ("c" * 64, "d" * 64))
