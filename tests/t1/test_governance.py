"""Part D: two-person governance of configuration and rules (docs/rules/PARTD_DESIGN.md + pre-build red team)."""
import os

import psycopg
import pytest

from dqagent.hitl.decisions import Denied
from dqagent.runtime import Runtime

from .fixtures_w1 import ie_file, person
from .w1_harness import Slice


def _elig(s, tmp, batch="B-ELG-1", trailer=None, rows=None):
    p = tmp / f"{batch}.txt"
    p.write_bytes(ie_file(rows or [person("P1", "M0001"), person("P2", "M0002")], trailer))
    return s.run_pipeline("p4_eligibility_ie", batch, [("IE", p)], ["bronze.ie_segment"])


def test_effective_config_comes_from_the_cp_not_the_files(fresh_cp_dsn, monkeypatch):
    from dqagent.rules import model
    s = Slice(fresh_cp_dsn)
    monkeypatch.setattr(model, "load_state", lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("files read")))
    s._load_effective()                       # recompiles from approved CP artifacts only
    assert s.ratified and s.rules and s.ruleset_version


def test_overlay_change_needs_a_second_person_and_reopens_held_units(fresh_cp_dsn, tmp_path):
    s = Slice(fresh_cp_dsn)
    _elig(s, tmp_path, trailer=9)                                    # trailer mismatch -> HELD (H_IN_2)
    held = s.units(state="HELD")
    assert held
    old_hash = next(r.effective_hash for r in s.rules if r.id == "R-ELG-001")
    old_rs = s.ruleset_version
    item = {"artifact_id": "ZZ:overlay:R-ELG-001", "kind": "OVERLAY", "op": "PUT",
            "content": {"verb": "raise_priority", "priority": "Critical", "reason": "state priority",
                        "approved_ref": "SCR-101"}}
    cr = s.registry.propose("ZZ", "E100", "Raise DOB rule priority per state change ticket", [item])
    assert not cr["loosening"] and cr["approvals_required"] == 1
    assert any(ch["rule"] == "ZZ:R-ELG-001@ELIG_SEGMENT" for ch in cr["impact"]["rules"])
    tok = s.idp.token("maker", step_up=True, challenge=s.registry.challenge(cr["cr_id"], "APPROVED"))
    with pytest.raises(Denied, match="author"):                     # the author cannot approve their own change
        s.registry.decide(cr["cr_id"], "APPROVED", tok, "E100", ["governance:AUTH_RULES"])
    assert s.apply_change(cr["cr_id"]) == "AWAITING_APPROVAL"
    tok = s.idp.token("checker", step_up=True, challenge=s.registry.challenge(cr["cr_id"], "REJECTED"))
    with pytest.raises(Denied, match="challenge mismatch"):         # a step-up for another verdict does not count
        s.registry.decide(cr["cr_id"], "APPROVED", tok, "E200", ["governance:AUTH_RULES"])
    tok = s.idp.token("checker", step_up=True, challenge=s.registry.challenge(cr["cr_id"], "APPROVED"))
    s.registry.decide(cr["cr_id"], "APPROVED", tok, "E200", ["governance:AUTH_RULES"])
    assert s.apply_change(cr["cr_id"]) == "APPLIED"
    new = next(r for r in s.rules if r.id == "R-ELG-001")
    assert new.effective_hash != old_hash and new.header["priority"] == "Critical" and s.ruleset_version != old_rs
    with s.cp.as_role("dq_registry_svc") as c:
        reg = c.execute("SELECT semantic_hash, decision_ref FROM dq_ctl.registry_events WHERE artifact_id="
                        "'ZZ:R-ELG-001@ELIG_SEGMENT' ORDER BY id DESC LIMIT 1").fetchone()
    assert reg["semantic_hash"] == new.effective_hash and reg["decision_ref"] == cr["cr_id"]
    with s.cp.as_role("dq_eval_svc") as c:                          # the held units were re-opened and re-evaluated
        for u in held:
            ev = [r["to_state"] for r in c.execute("SELECT to_state FROM dq_ctl.unit_events WHERE unit_key=%s "
                                                    "ORDER BY state_version", (u["unit_key"],)).fetchall()]
            assert "RE_EVALUATE_PENDING" in ev and ev[-1] == "HELD"
            rsv = {r["ruleset_version"] for r in c.execute(
                "SELECT ruleset_version FROM dq_ctl.results WHERE unit_key=%s AND eval_version="
                "(SELECT max(eval_version) FROM dq_ctl.results WHERE unit_key=%s)", (u["unit_key"],) * 2).fetchall()}
            assert rsv == {s.ruleset_version}


def test_loosening_threshold_is_refused_and_a_stale_request_never_applies(fresh_cp_dsn):
    s = Slice(fresh_cp_dsn)
    bad = {"artifact_id": "ZZ:overlay:M-RULE-7907", "kind": "OVERLAY", "op": "PUT",
           "content": {"verb": "tighten", "thresholds": {"max": 0.5}, "reason": "r", "approved_ref": "x"}}
    with pytest.raises(Exception, match="LOOSEN"):
        s.registry.propose("ZZ", "E100", "attempt to widen a CMS maximum", [bad])
    a = {"artifact_id": "ZZ:overlay:M-RULE-7907", "kind": "OVERLAY", "op": "PUT",
         "content": {"verb": "tighten", "thresholds": {"max": 0.005}, "reason": "r", "approved_ref": "x"}}
    b = {**a, "content": {**a["content"], "thresholds": {"max": 0.002}}}
    cr1 = s.registry.propose("ZZ", "E100", "tighten NPI measure to 0.5 pct", [a])
    cr2 = s.registry.propose("ZZ", "E100", "tighten NPI measure to 0.2 pct", [b])
    for cr in (cr1, cr2):
        tok = s.idp.token("checker", step_up=True, challenge=s.registry.challenge(cr["cr_id"], "APPROVED"))
        s.registry.decide(cr["cr_id"], "APPROVED", tok, "E200", ["governance:AUTH_RULES"])
    assert s.apply_change(cr1["cr_id"]) == "APPLIED"
    assert s.apply_change(cr2["cr_id"]) == "STALE"                  # its base moved: the approver saw another world


def test_release_fails_closed_on_drift_and_on_a_required_rule_without_result(fresh_cp_dsn, tmp_path, monkeypatch):
    s = Slice(fresh_cp_dsn)
    _elig(s, tmp_path)                                               # clean -> mode A (EVALUATING)
    real = s.governance_ctx

    def plus_fake():
        g = dict(real())
        fake = type(g["rules"][0])(**{**g["rules"][0].__dict__})
        fake.id, fake.relation, fake.status, fake.kind = "R-NEVER-RAN", "bronze.ie_segment", "ACTIVE", "row"
        fake.header = {**fake.header, "gating": "gate"}
        return {**g, "rules": list(g["rules"]) + [fake]}
    monkeypatch.setattr(s.publisher, "governance", plus_fake)
    out = s.verify_and_publish()
    assert not out["released"] and any("2b:" in r for rs in out["blocked"].values() for r in rs), out["blocked"]
    monkeypatch.setattr(s.publisher, "governance", real)
    from dqagent.governance import registry as reg          # REAL drift detection: the code on disk differs
    true_digest = reg.catalog_digest
    monkeypatch.setattr(reg, "catalog_digest", lambda: {**true_digest(), "digest": "0" * 64})
    s._load_effective()
    assert s.drift
    out = s.verify_and_publish()
    assert not out["released"] and any("2c:" in r for rs in out["blocked"].values() for r in rs)
    monkeypatch.setattr(reg, "catalog_digest", true_digest)
    s._load_effective()
    real_v = s.ruleset_version
    s.ruleset_version = "superseded"                        # 2d: results from another ruleset never release
    out = s.verify_and_publish()
    assert not out["released"] and any("2d:" in r for rs in out["blocked"].values() for r in rs)
    s.ruleset_version = real_v
    assert s.verify_and_publish()["released"]


def test_without_ratified_genesis_nothing_is_evaluated_until_a_second_person_ratifies(fresh_cp_dsn, tmp_path,
                                                                                         monkeypatch):
    # Part F R4-2: a GOVERNED genesis binds pilot | production only, so this runs the pilot path (keys from the env)
    for k in ("DQ_K_EV", "DQ_K_FP", "DQ_K_ABC"):
        monkeypatch.setenv(k, f"pilot-{k.lower()}-key")
    rt = Runtime(fresh_cp_dsn, dp_path=str(tmp_path / "dp.duckdb"), deployment="pilot")   # NOT test mode: no genesis
    assert not rt.ratified and rt.rules == []
    with psycopg.connect(**fresh_cp_dsn) as c:                       # roster only (duties), no configuration
        for emp, duty, auth in (("E100", "governance", "AUTH_RULES"), ("E200", "governance", "AUTH_RULES"),
                                ("E210", "governance", "AUTH_RULES"), ("E100", "release", "AUTH_MEDICAID")):
            c.execute("INSERT INTO dq_ctl.roster(person_id,duty,authority,action,decision_ref) VALUES "
                      "(%s,%s,%s,'GRANT','ROSTER-FIXTURE')", (emp, duty, auth))
        c.commit()
    r = _elig(rt, tmp_path)
    assert r["summary"]["bronze.ie_segment"].get("x_not_ratified")
    with rt.cp.as_role("dq_eval_svc") as c:
        assert {u["state"] for u in c.execute("SELECT state FROM dq_ctl.units").fetchall()} == {"SEALED"}
    cr = rt.registry.propose_genesis("ZZ", "E100")
    # Part E (C4): genesis grants the agent its entitlements and autonomy tiers (brand-new grants), so the database
    # requires TWO approvers other than the author
    assert cr["approvals_required"] == 2
    tok = rt.idp.token("checker", step_up=True, challenge=rt.registry.challenge(cr["cr_id"], "APPROVED"))
    rt.registry.decide(cr["cr_id"], "APPROVED", tok, "E200", ["governance:AUTH_RULES"])
    assert rt.apply_change(cr["cr_id"]) == "AWAITING_APPROVAL" and not rt.ratified
    tok = rt.idp.token("checker2", step_up=True, challenge=rt.registry.challenge(cr["cr_id"], "APPROVED"))
    rt.registry.decide(cr["cr_id"], "APPROVED", tok, "E210", ["governance:AUTH_RULES"])
    assert rt.apply_change(cr["cr_id"]) == "APPLIED" and rt.ratified
    with rt.cp.as_role("dq_eval_svc") as c:
        assert "SEALED" not in {u["state"] for u in c.execute("SELECT state FROM dq_ctl.units").fetchall()}


def test_a_test_genesis_cp_is_refused_outside_test_mode_and_only_the_registry_approves(fresh_cp_dsn, monkeypatch):
    Slice(fresh_cp_dsn)
    monkeypatch.delenv("DQ_ALLOW_TEST_GENESIS", raising=False)
    with pytest.raises(RuntimeError, match="TEST-GENESIS"):
        Runtime(fresh_cp_dsn)
    for role in ("dq_publish_svc", "dq_api", "dq_eval_svc"):
        with psycopg.connect(**{**fresh_cp_dsn, "user": role}) as c:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                c.execute("INSERT INTO dq_ctl.registry_events(artifact_kind,artifact_id,semantic_hash,state,decision_ref)"
                          " VALUES ('rule','ZZ:X@Y','h','APPROVED','forged')")
    monkeypatch.setenv("DQ_ALLOW_TEST_GENESIS", "0")                # only the literal "1" allows it
    with pytest.raises(RuntimeError, match="TEST-GENESIS"):
        Runtime(fresh_cp_dsn)


def test_classifier_and_shape_guards(fresh_cp_dsn):
    from dqagent.rules.ir import IRError
    s = Slice(fresh_cp_dsn)
    with pytest.raises(IRError, match="not of kind"):              # kind spoofing: policy content under OVERLAY
        s.registry.propose("ZZ", "E100", "spoofed kind attempt", [{"artifact_id": "ZZ:policy", "kind": "OVERLAY",
                                                                 "op": "PUT", "content": {"verb": "waive"}}])
    with pytest.raises(IRError, match="cannot be deleted"):        # F1: mandatory artifacts cannot vanish
        s.registry.propose("ZZ", "E100", "delete the policy", [{"artifact_id": "ZZ:policy", "kind": "POLICY", "op": "DELETE"}])
    pol = s.effective_item("POLICY", "policy")
    pol["content"]["floor"]["abc_failed_release"] = "H_IN"          # floor lowered
    assert s.registry.propose("ZZ", "E100", "lower the ABC floor", [pol])["loosening"]
    tight = {"artifact_id": "ZZ:overlay:M-RULE-7907", "kind": "OVERLAY", "op": "PUT",
             "content": {"verb": "tighten", "thresholds": {"max": 0.005}, "reason": "r", "approved_ref": "x"}}
    cr, st = s.govern([tight])
    assert not cr["loosening"] and st == "APPLIED"
    rm = s.registry.propose("ZZ", "E100", "remove the tightening overlay",
                            [{"artifact_id": "ZZ:overlay:M-RULE-7907", "kind": "OVERLAY", "op": "DELETE"}])
    assert rm["loosening"] and rm["approvals_required"] == 2
    harness = s.effective_item("HARNESS", "harness")
    assert s.registry.propose("ZZ", "E100", "edit harness", [harness])["loosening"]


def test_apply_does_not_race_the_publish_worker(fresh_cp_dsn, tmp_path):
    """Tower smoke found apply_change racing the demo worker (SerializationFailure). Operations are serialized."""
    import threading
    s = Slice(fresh_cp_dsn)
    _elig(s, tmp_path, trailer=9)
    errors, stop = [], threading.Event()

    def worker():
        while not stop.is_set():
            try:
                s.verify_and_publish()
            except Exception as e:  # noqa: BLE001
                errors.append(repr(e))
    t = threading.Thread(target=worker)
    t.start()
    try:
        item = {"artifact_id": "ZZ:overlay:R-ELG-001", "kind": "OVERLAY", "op": "PUT",
                "content": {"verb": "raise_priority", "priority": "Critical", "reason": "r", "approved_ref": "SCR-9"}}
        cr, status = s.govern([item])
    finally:
        stop.set()
        t.join()
    assert status == "APPLIED" and not errors, errors
    with s.cp.as_role("dq_registry_svc") as c:      # only the CHANGED rule carries this request as its approval
        refs = {r["artifact_id"] for r in c.execute("SELECT artifact_id FROM dq_ctl.registry_events WHERE decision_ref=%s",
                                                    (cr["cr_id"],)).fetchall()}
    assert refs == {"ZZ:R-ELG-001@ELIG_SEGMENT"}, refs
