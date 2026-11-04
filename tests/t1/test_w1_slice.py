"""T1 vertical slice: intake → seal → evaluate → mode → decide/confirm → verifier → publish → released views.

Each test asserts a W1 exit criterion (DESIGN §15) against real Postgres plus DuckDB.
"""
import pytest

from dqagent.hitl.decisions import Denied, Replay, Stale

from .fixtures_w1 import acks, ie_file, person, x837
from .w1_harness import Slice

SCOPE = ["AUTH_MEDICAID"]


@pytest.fixture
def s(fresh_cp_dsn, tmp_path):
    sl = Slice(fresh_cp_dsn)
    sl.tmp = tmp_path
    return sl


def _write(tmp, name, data):
    p = tmp / name
    p.write_bytes(data)
    return p


def _eligibility(s, batch="B-ELG-1", rows=None, trailer=None):
    rows = rows or [person("P1", "M0001"), person("P2", "M0002"), person("P3", "M0003")]
    p = _write(s.tmp, f"{batch}.txt", ie_file(rows, trailer))
    return s.run_pipeline("p4_eligibility_ie", batch, [("IE", p)], ["bronze.ie_segment"])


def _encounters(s, batch, claims, isa13="000000101", statuses=None):
    p = _write(s.tmp, f"{batch}.x12", x837("ZZMCOA", isa13, claims))
    a = _write(s.tmp, f"{batch}.ack.csv", acks(isa13, statuses or ["A"] * len(claims)))
    return s.run_pipeline("p6_claims", batch, [("X12_837P", p, "ZZ-MCO-A", a)],
                          ["bronze.encounter_header", "bronze.encounter_line"])


def decide(s, user, u, verb, rationale, q=None, maker=None, token=None):
    """Tower flow: challenge → IdP step-up bound to it → decide (maker) / confirm (checker)."""
    uk = u["unit_key"]
    tok = token or s.step_up(user, [uk], verb, None if maker else {uk: q or []}, maker)
    return s.decisions.decide(SCOPE, tok, [uk], verb, rationale, {uk: u["state_version"]}, {uk: u["fingerprint"]},
                              {uk: q or []}, maker_decision_id=maker)


def _released(s, view):
    return s.dp.fetchall(f"SELECT * FROM dq_rel.{view}")


def test_clean_eligibility_is_released_autonomously_and_visible(s):
    r = _eligibility(s)
    assert all(a["disposition"] == "PASS" for a in r["abc"]), r["abc"]
    assert r["summary"]["bronze.ie_segment"]["autonomous"] == 3
    out = s.verify_and_publish()
    assert len(out["released"]) == 3, out["blocked"]
    assert len(_released(s, "ie_segment_released")) == 3


def test_trailer_mismatch_holds_with_two_person_floor(s):
    r = _eligibility(s, trailer=5)
    assert any(a["hop"] == "FILE->RAW" and a["disposition"] == "FAIL" for a in r["abc"])
    assert r["summary"]["bronze.ie_segment"]["held"] == 3
    held = s.units(state="HELD")
    ev = s.publisher.run()
    assert not ev["released"] and len(_released(s, "ie_segment_released")) == 0
    # The H_IN_2 path: a step-up maker, then a step-up checker, each bound to its own challenge.
    u = held[0]
    with pytest.raises(Denied):                                         # a session bearer never decides
        s.decisions.decide(SCOPE, s.idp.token("maker"), [u["unit_key"]], "RELEASE", "session token is not enough",
                           {u["unit_key"]: u["state_version"]}, {u["unit_key"]: u["fingerprint"]})
    r1 = decide(s, "maker", u, "RELEASE", "Trailer miscount confirmed with IE vendor; detail rows complete.")
    assert r1["results"][0]["status"] == "AWAITING_CHECKER"
    mk = r1["results"][0]["decision_id"]
    with pytest.raises(Denied):                                         # step-up bound to other evidence
        decide(s, "checker", u, "RELEASE", "checker reviewed evidence fully", maker=mk,
               token=s.idp.token("checker", step_up=True, challenge="not-this-one"))
    with pytest.raises(Denied):                                         # checker may not flip the maker's verb
        decide(s, "checker", u, "REJECT", "checker trying to reject instead", maker=mk)
    with pytest.raises(Denied):                                         # maker cannot be checker
        decide(s, "maker", u, "RELEASE", "same person trying again here", maker=mk)
    r2 = decide(s, "checker", u, "RELEASE", "Independent check of trailer evidence done.", maker=mk)
    assert r2["results"][0]["status"] == "APPLIED"
    out = s.verify_and_publish()
    assert u["unit_key"] in out["released"]
    assert len(_released(s, "ie_segment_released")) == 1


def test_intake_failure_needs_an_approver_with_intake_duty(s):
    _eligibility(s, trailer=5)                                          # FILE->RAW (AUTH_INTAKE) hop FAILs
    u = s.units(state="HELD")[0]
    mk = decide(s, "maker", u, "RELEASE", "Trailer miscount confirmed with IE vendor; rows complete.")
    r = decide(s, "checker2", u, "RELEASE", "Second Medicaid reviewer agrees with the maker.",
               maker=mk["results"][0]["decision_id"])["results"][0]
    assert r["status"] == "AWAITING_COVERAGE" and r["missing_duties"] == ["AUTH_INTAKE"]
    assert s.units(unit_key=u["unit_key"])[0]["state"] == "HELD"


def test_step_up_replay_is_refused(s):
    _eligibility(s, trailer=9)
    u = s.units(state="HELD")[0]
    tok = s.step_up("maker", [u["unit_key"]], "RELEASE", {u["unit_key"]: []})
    decide(s, "maker", u, "RELEASE", "first use of this step-up token here", token=tok)
    with pytest.raises((Replay, Denied)):
        decide(s, "maker", u, "RELEASE", "second use of the very same token", token=tok)


def test_forged_decision_rows_by_a_compromised_api_never_release(s):
    """SEC-W1-3: dq_api writes decision rows citing OLD genuine tokens and moves the unit itself."""
    _eligibility(s, batch="B1", trailer=9)
    _eligibility(s, batch="B2", rows=[person("P7", "M0007")], trailer=9)
    a = [u for u in s.units(state="HELD") if u["batch_id"] == "B1"][0]
    victim = [u for u in s.units(state="HELD") if u["batch_id"] == "B2"][0]
    m1 = decide(s, "maker", a, "RELEASE", "genuine maker decision on unit A")["results"][0]["decision_id"]
    decide(s, "checker", a, "RELEASE", "genuine checker decision on unit A", maker=m1)
    import json
    with s.cp.as_role("dq_api", SCOPE) as c:
        toks = c.execute("SELECT token_id, person_id FROM dq_ctl.decisions WHERE unit_key=%s", (a["unit_key"],)).fetchall()
        for i, t in enumerate(toks):
            c.execute("INSERT INTO dq_ctl.decisions(decision_id,unit_key,round,seat,person_id,authority,decision,rationale,"
                      "token_id,bound_fingerprint,fp_epoch,qset_hash,disposition_vector,maker_decision_id) VALUES "
                      "(%s,%s,%s,%s,%s,'AUTH_INTAKE,AUTH_MEDICAID','RELEASE','forged by a compromised api',%s,%s,1,%s,"
                      "%s::jsonb,%s)",
                      (f"F{i}", victim["unit_key"], victim["state_version"], ["MAKER", "CHECKER"][i], t["person_id"],
                       t["token_id"], victim["fingerprint"], __import__("dqagent.hitl.decisions", fromlist=["x"]).qset_hash([]),
                       json.dumps({}), None if i == 0 else "F0"))
        s.cp.transition(c, victim["unit_key"], victim["state_version"], "PENDING_RELEASE", "CONFIRM", toks[1]["person_id"])
    out = s.verify_and_publish()
    assert victim["unit_key"] not in out["released"]
    assert any("attestation" in w for w in out["blocked"][victim["unit_key"]])


def test_concurrent_scoped_readers_never_see_other_authorities(s):
    """SEC-W1-2: two scoped readers and the publisher worker in parallel threads."""
    import threading
    _eligibility(s, rows=[person("P1", "M0001"), person("P2", "M0002", prog="SNAP")], trailer=9)
    from fastapi.testclient import TestClient
    from dqagent.api.app import create_app
    api = TestClient(create_app(s))
    hs = {"Authorization": "Bearer " + s.idp.token("snap", ttl=3600)}
    hm = {"Authorization": "Bearer " + s.idp.token("maker", ttl=3600)}
    bad = []

    def reader(h, auth):
        for _ in range(25):
            for cl in api.get("/api/v1/queue", headers=h).json():
                if cl["authority"] != auth:
                    bad.append(cl["authority"])

    def worker():
        for _ in range(10):
            s.verify_and_publish()
    ts = [threading.Thread(target=reader, args=(hs, "AUTH_SNAP")), threading.Thread(target=reader, args=(hm, "AUTH_MEDICAID")),
          threading.Thread(target=worker)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert bad == []


def test_duty_requires_token_claim_and_roster(s):
    _eligibility(s, trailer=9)
    u = s.units(state="HELD")[0]
    with pytest.raises(Denied):          # has the group claim but no roster grant
        decide(s, "outsider", u, "RELEASE", "I have the claim but I am not on the roster.")


def test_stale_on_changed_evidence(s):
    _eligibility(s, trailer=9)
    u = s.units(state="HELD")[0]
    tok = s.step_up("maker", [u["unit_key"]], "RELEASE", {u["unit_key"]: []})
    with pytest.raises(Stale):
        s.decisions.decide(SCOPE, tok, [u["unit_key"]], "RELEASE", "Bound to a fingerprint that is not current.",
                           {u["unit_key"]: u["state_version"]}, {u["unit_key"]: "deadbeef"})


def test_encounters_wait_for_eligibility_then_held_for_advisory_and_quarantine_hides_rows(s):
    _eligibility(s)                                # eligibility units EVALUATING (mode A) but not yet RELEASED
    claims = [{"id": "E1", "member": "M0001", "lines": [("Z9001", 100.0, "20260110")]},
              {"id": "E2", "member": "M0002", "lines": [("Z9002", 60.0, "20260110"), ("Z9003", 40.0, "20260111")],
               "total": 120.0}]                     # CLM02 mismatch
    r = _encounters(s, "B-ENC-1", claims)
    assert r["summary"]["bronze.encounter_header"]["pending_dependency"] == 2   # WAIT on non-terminal ELG
    s.verify_and_publish()                         # releases eligibility, then re-evaluates the waiting encounters
    assert len(s.units(state="HELD", node="bronze.encounter_header")) == 2   # ADVISORY ⇒ floor waived ⇒ ≥ H_IN
    s.verify_and_publish()
    assert _released(s, "encounter_header_released") == []
    held = {u["unit_key"]: u for u in s.units(state="HELD", node="bronze.encounter_header")}
    # The CLM02 unit: quarantine its header row and release the remainder (H_IN, 1 person).
    bad = [uk for uk in held if s.dp.fetchall(
        "SELECT 1 FROM bronze.encounter_header WHERE _dq_unit_key=? AND encounter_id='E2'", [uk])][0]
    good = [uk for uk in held if uk != bad][0]
    seq = s.dp.fetchall("SELECT _dq_row_seq FROM bronze.encounter_header WHERE _dq_unit_key=?", [bad])[0][0]
    for uk, q in ((good, []), (bad, [seq])):
        u = held[uk]
        decide(s, "maker", u, "QUARANTINE_AND_RELEASE" if q else "RELEASE",
               "Reviewed dispositions; codeset validity advisory accepted for demo.", q=q)
    out = s.verify_and_publish()
    assert {good, bad} <= set(out["released"])
    vis = {r[0] for r in s.dp.fetchall("SELECT encounter_id FROM dq_rel.encounter_header_released")}
    assert vis == {"E1"}                           # E2's header is quarantined, so hidden
    lines = {r[0] for r in s.dp.fetchall("SELECT encounter_id FROM dq_rel.encounter_line_released")}
    assert lines == {"E1"}                         # CHAIN quarantine: E2's lines go with its header


def test_tamper_after_release_is_denied_and_removed(s):
    _eligibility(s)
    s.verify_and_publish()
    assert len(_released(s, "ie_segment_released")) == 3
    s.dp.execute("UPDATE bronze.ie_segment SET eff_end = DATE '2025-06-30' WHERE msis_id='M0002'")  # tamper
    out = s.verifier.attest_fingerprints()
    assert out["denied"] == 1
    blocked = s.publisher.run()
    vis = {r[0] for r in s.dp.fetchall("SELECT msis_id FROM dq_rel.ie_segment_released")}
    assert vis == {"M0001", "M0003"}
    assert blocked is not None


def test_seal_race_late_load_is_not_visible_and_is_reported(s):
    r = _eligibility(s)
    assert r["late_loads"] == []
    # A pipeline writes a late row into landing for the already-sealed batch (new load, never frozen).
    s.dp.execute("INSERT INTO landing.ie_segment SELECT * FROM landing.ie_segment LIMIT 1")
    s.dp.execute("UPDATE landing.ie_segment SET _dq_load_id='LATE' WHERE rowid = (SELECT max(rowid) FROM landing.ie_segment)")
    from dqagent.engine.run import landing_to_gated_abc
    _, late = landing_to_gated_abc(s.cp, s.dp, "B-ELG-1", ["bronze.ie_segment"])
    assert late == ["LATE"]
    s.verify_and_publish()
    assert len(_released(s, "ie_segment_released")) == 3


def test_rows_appended_to_a_released_unit_are_fenced_and_detected(s):
    _eligibility(s)
    s.verify_and_publish()
    uk, mx = s.dp.fetchall("SELECT _dq_unit_key, _dq_row_seq FROM bronze.ie_segment WHERE msis_id='M0001'")[0]
    s.dp.execute("INSERT INTO bronze.ie_segment SELECT * REPLACE (999999 AS _dq_row_seq) FROM bronze.ie_segment "
                 "WHERE msis_id='M0001'")
    assert len(_released(s, "ie_segment_released")) == 3          # max_row_seq fence hides the injected row
    assert s.verifier.attest_fingerprints()["denied"] == 1        # and the verifier detects it


def test_per_partition_order_blocks_later_batch(s):
    _eligibility(s, batch="B-ELG-1", trailer=9)                    # held (ABC fail)
    _eligibility(s, batch="B-ELG-2", rows=[person("P1", "M0001", start="20260101")])
    later = [u for u in s.units(batch_id="B-ELG-2")]
    assert later and all(u["state"] == "PENDING_DEPENDENCY" for u in later)   # waits for the earlier version
    s.publisher.run()
    assert all(u["state"] == "PENDING_DEPENDENCY" for u in s.units(batch_id="B-ELG-2"))


def test_dependency_wait_escalates_to_a_human_after_max_wait(s):
    import datetime as dt
    _eligibility(s, batch="B-ELG-1", trailer=9)                    # earlier version held
    _eligibility(s, batch="B-ELG-2", rows=[person("P1", "M0001", start="20260101")])
    assert s.units(batch_id="B-ELG-2")[0]["state"] == "PENDING_DEPENDENCY"
    s.evaluator.now = lambda: dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=3)
    s.reevaluate_pending()
    u = s.units(batch_id="B-ELG-2")[0]
    assert u["state"] == "HELD"
    with s.cp.as_role("dq_eval_svc") as c:
        ev = c.execute("SELECT payload FROM dq_ctl.unit_events WHERE unit_key=%s AND to_state='HELD'",
                       (u["unit_key"],)).fetchone()["payload"]
    assert any("escalated" in t for t in ev["mode_trace"])


def test_manifest_crash_after_dp_commit_never_reuses_a_version_and_blocks_releases(s):
    """SEC-W1-6: the DP pointer moves but the CP record is lost; a later DENY must still disappear."""
    _eligibility(s)
    s.verify_and_publish()
    v1 = s.dp.fetchall("SELECT version FROM dq_rel.manifest_pointer")[0][0]
    _eligibility(s, batch="B-ELG-2", rows=[person("P9", "M0009")])
    s.verifier.attest_fingerprints()
    s.verifier.attest_publication(s.publisher.dp_name)
    import dqagent.intervention.publish as pub
    src = pub.Publisher.publish_manifest
    def broken(self):                                       # the 3rd CP call (the record) fails
        orig_as_role = self.cp.as_role
        calls = {"n": 0}
        def as_role(role, scope=None):
            calls["n"] += 1
            if calls["n"] == 3:
                raise RuntimeError("CP unavailable after DP commit")
            return orig_as_role(role, scope)
        self.cp.as_role = as_role
        try:
            return src(self)
        finally:
            self.cp.as_role = orig_as_role
    pub.Publisher.publish_manifest = broken
    try:
        with pytest.raises(RuntimeError):
            s.publisher.run()
    finally:
        pub.Publisher.publish_manifest = src
    v2 = s.dp.fetchall("SELECT version FROM dq_rel.manifest_pointer")[0][0]
    assert v2 == v1 + 1                                      # DP moved; CP has no record of v2
    # clause 10: until the verifier sees DP == CP, nothing new releases
    _eligibility(s, batch="B-ELG-3", rows=[person("P8", "M0008")])
    out = s.verify_and_publish()
    assert not [u for u in out["released"] if u.startswith("B-ELG-3")]
    assert any("10:" in w for ws in out["blocked"].values() for w in ws)
    out = s.verify_and_publish()                             # the publisher back-fills the CP record; healthy again
    assert [u for u in out["released"] if u.startswith("B-ELG-3")]
    # DENY a released unit: the republish takes a NEW version (reserved in the CP) and the unit vanishes
    s.dp.execute("UPDATE bronze.ie_segment SET eff_end = DATE '2025-06-30' WHERE msis_id='M0002'")
    s.verify_and_publish()
    v3 = s.dp.fetchall("SELECT version FROM dq_rel.manifest_pointer")[0][0]
    assert v3 > v2
    assert "M0002" not in {r[0] for r in s.dp.fetchall("SELECT msis_id FROM dq_rel.ie_segment_released")}
