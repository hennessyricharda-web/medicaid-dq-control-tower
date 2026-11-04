"""Part E §1.5 (C1): earning autonomy on synthetic run w2_earn (manifest synthetic/out/w2_earn/earn_manifest.json).

  1 APPROVE   the dq-reconciliation agent starts at L2 for RELEASE on AUTH_MEDICAID: every clean Medicaid unit is
              held at the L2 ceiling and released by a person; the human-approved releases accrue.
  2 PROMOTE   the maker proposes L2 -> L3 with the track-record evidence; the DATABASE requires two approvers, one
              approval applies nothing, and N clean releases never promote on their own (negative control).
  3 VETO      the next batch opens veto windows (database clock): one unit releases exactly at its deadline (not one
              second before), the other is vetoed, which demotes the authority to L2 automatically.
  4 LOOKBACK  after a second two-person promotion, a look-back WITHDRAWN on the unit the agent released at L3 demotes
              again; a WITHDRAWN on a human-released unit does not (negative control).
"""
import datetime as dt
import json
from pathlib import Path

import pytest

from dqagent.hitl.decisions import Denied

from .evidence import grant_evidence
from .w1_harness import Slice

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "synthetic" / "out" / "w2_earn"
MAN = json.loads((DATA / "earn_manifest.json").read_text())
AGENT, AUTH, MED = MAN["agent"], MAN["authority"], [MAN["authority"]]


def _load(s, batch_id):
    f = next(b["file"] for b in MAN["ie_batches"] if b["batch_id"] == batch_id)
    s.run_pipeline("p4_eligibility_ie", batch_id, [("IE", DATA / f)], ["bronze.ie_segment"])
    s.verify_and_publish()


def _medicaid(s, batch_id, state=None):
    us = [u for u in s.units(batch_id=batch_id) if u["authorities"] == MED]
    return [u for u in us if state is None or u["state"] == state]


def _approve_all(s, batch_id, user="maker"):
    held = sorted(_medicaid(s, batch_id, "HELD"), key=lambda u: u["unit_key"])
    keys = [u["unit_key"] for u in held]
    tok = s.step_up(user, keys, "RELEASE")
    r = s.decisions.decide(MED, tok, keys, "RELEASE", "Reviewed the evidence; recommended release is correct.",
                           {u["unit_key"]: u["state_version"] for u in held},
                           {u["unit_key"]: u["fingerprint"] for u in held}, session_person=s.idp.users[user]["emp_id"])
    assert {x["status"] for x in r["results"]} == {"APPLIED"}
    return keys


def _promote(s, approvers=("checker", "checker2")):
    item = s.effective_item("AUTONOMY", f"autonomy:{AGENT}:{MAN['action_class']}")
    item["content"][AUTH] = MAN["promote_to"]
    cr = s.registry.propose("ZZ", "E100", "Promote release autonomy with the attached track record", [item])
    status = []
    for a in approvers:
        tok = s.idp.token(a, step_up=True, challenge=s.registry.challenge(cr["cr_id"], "APPROVED"))
        s.registry.decide(cr["cr_id"], "APPROVED", tok, s.idp.users[a]["emp_id"], ["governance:AUTH_RULES"])
        status.append(s.apply_change(cr["cr_id"]))
    return cr, status


def _tier(s):
    return s.registry.tier("ZZ", AGENT, "RELEASE", AUTH)


def _withdraw(s, item_id, person="E200"):
    with s.cp.as_role("dq_api", MED) as c:
        c.execute("SELECT dq_ctl.lookback_decide(%s,%s,%s,'WITHDRAWN',%s,NULL)",
                  (item_id, person, f"jti-{item_id}", "Released under the revoked rule version; withdraw it."))


def test_w2_earn_story(fresh_cp_dsn, tmp_path):
    s = Slice(fresh_cp_dsn, dp_path=str(tmp_path / "dp.duckdb"))
    assert s.registry.demote(AGENT, "RELEASE", AUTH, MAN["start_tier"], "TEST_SETUP") == 1     # the test state
    assert _tier(s) == "L2"
    phases = {p["phase"]: p for p in MAN["phases"]}

    # 1 APPROVE: every clean Medicaid unit is held at the L2 ceiling (the agent recommends) and a person releases it
    human_released = []
    for b in phases["APPROVE"]["batches"]:
        _load(s, b)
        want = next(x["medicaid_person_units"] for x in MAN["ie_batches"] if x["batch_id"] == b)
        held = _medicaid(s, b, "HELD")
        assert len(held) == want == len(_medicaid(s, b))
        with s.cp.as_role("dq_eval_svc") as c:
            pl = [c.execute("SELECT payload FROM dq_ctl.unit_events WHERE unit_key=%s AND to_state='HELD' "
                            "ORDER BY state_version DESC LIMIT 1", (u["unit_key"],)).fetchone()["payload"] for u in held]
        assert {(p["mode"], p.get("ceiling")) for p in pl} == {("H_IN", "L2 ceiling")}
        human_released += _approve_all(s, b)
        out = s.verify_and_publish()
        assert set(_k for _k in out["released"] if _k in human_released) >= {u["unit_key"] for u in held}
    assert _tier(s) == "L2"                                   # negative control: clean releases never promote

    # 2 PROMOTE: two-person, database-enforced, with the evidence the approvers sign. R4-5: the database also needs
    # a confirmed deterministic evidence bundle for this code version and the DEMO minimum record (L3: at least 5
    # human-approved releases on this version); phase 1 supplied the record. Negative control FIRST: the SAME
    # change request is refused BEFORE the evidence bundle exists -- the record alone (5 releases) is not enough --
    # and only succeeds once the test supplies the bundle (R4-5: evidence first, THEN approval, never the reverse).
    item = s.effective_item("AUTONOMY", f"autonomy:{AGENT}:{MAN['action_class']}")
    item["content"][AUTH] = MAN["promote_to"]
    cr = s.registry.propose("ZZ", "E100", "Promote release autonomy with the attached track record", [item])
    assert cr["impact"]["track_record"]["evidence"]["complete"] is False
    tok = s.idp.token("checker", step_up=True, challenge=s.registry.challenge(cr["cr_id"], "APPROVED"))
    with pytest.raises(Denied, match="deterministic"):
        s.registry.decide(cr["cr_id"], "APPROVED", tok, s.idp.users["checker"]["emp_id"], ["governance:AUTH_RULES"])
    assert _tier(s) == "L2"                                      # still refused: no evidence yet
    grant_evidence(s)                                            # now the bundle exists; the same CR succeeds
    # R4-5: cr["impact"]["track_record"] is a PROPOSE-TIME snapshot (registry.py::track_record_snapshot), taken
    # before grant_evidence ran above -- it is signed into the CR and never changes. Re-fetch a live snapshot now,
    # the same way propose() computed the stale one, to assert against the CURRENT evidence/record (K1 defect fix).
    tr = s.registry.track_record_snapshot("ZZ", [item])
    status = []
    for a in ("checker", "checker2"):
        tok = s.idp.token(a, step_up=True, challenge=s.registry.challenge(cr["cr_id"], "APPROVED"))
        s.registry.decide(cr["cr_id"], "APPROVED", tok, s.idp.users[a]["emp_id"], ["governance:AUTH_RULES"])
        status.append(s.apply_change(cr["cr_id"]))
    assert cr["approvals_required"] == 2 and cr["loosening"]
    assert tr["hash"] and f"ZZ:autonomy:{AGENT}:RELEASE" in tr["rows"]
    assert tr["evidence"]["complete"] and tr["minimums"]["label"] == "demo minimums"
    row = next(r for r in tr["rows"][f"ZZ:autonomy:{AGENT}:RELEASE"] if r["authority"] == AUTH)
    assert row["current_version"]["human_released"] >= tr["minimums"]["L3"]["human_releases"]
    assert status == ["AWAITING_APPROVAL", "APPLIED"] and _tier(s) == MAN["promote_to"]

    # 3 VETO: the next batch opens veto windows by the database clock
    vb = phases["VETO"]["batches"][0]
    _load(s, vb)
    units = sorted(_medicaid(s, vb), key=lambda u: u["unit_key"])
    assert len(units) == 2 and {u["state"] for u in units} == {"EVALUATING"}
    with s.cp.as_role("dq_publish_svc") as c:
        w = {r["unit_key"]: r for r in c.execute(
            "SELECT unit_key, deadline, remaining_s, window_s FROM dq_ctl.veto_window_status WHERE unit_key = ANY(%s)",
            ([u["unit_key"] for u in units],)).fetchall()}
    first, second = sorted(units, key=lambda u: w[u["unit_key"]]["deadline"])
    assert w[second["unit_key"]]["deadline"] > w[first["unit_key"]]["deadline"]
    assert w[first["unit_key"]]["window_s"] == 600 and w[first["unit_key"]]["remaining_s"] > 0
    s.publisher.clock = lambda: w[first["unit_key"]]["deadline"] - dt.timedelta(seconds=1)
    assert not [k for k in s.verify_and_publish()["released"] if k in w]          # not one second early
    s.publisher.clock = lambda: w[first["unit_key"]]["deadline"]
    assert [k for k in s.verify_and_publish()["released"] if k in w] == [first["unit_key"]]   # at the deadline
    s.publisher.clock = None
    with s.cp.as_role("dq_eval_svc") as c:
        rec = c.execute("SELECT released_after_window, autonomous FROM dq_ctl.track_record WHERE agent_id=%s "
                        "AND action_class='RELEASE' AND authority=%s", (AGENT, AUTH)).fetchone()
    assert rec["released_after_window"] == 1 and rec["autonomous"] == 0
    sv = s.units(unit_key=second["unit_key"])[0]["state_version"]
    s.veto(MED, second["unit_key"], sv, "E200", "DATA_SUSPECT")
    assert s.units(unit_key=second["unit_key"])[0]["state"] == "HELD" and _tier(s) == "L2"

    # 4 LOOKBACK: promote again (two people), revoke a rule version the L3 release ran under, withdraw it -> demotes
    _, status = _promote(s)
    assert status[-1] == "APPLIED" and _tier(s) == "L3"
    assert s.units(unit_key=second["unit_key"])[0]["state"] == "HELD"     # a vetoed unit stays with people
    rule = next(r for r in s.rules if r.id == "R-ELG-001")
    rev = {"artifact_id": f"ZZ:revocation:R-ELG-001@ELIG_SEGMENT:{rule.effective_hash[:12]}", "kind": "REVOCATION",
           "op": "PUT", "content": {"rule": "ZZ:R-ELG-001@ELIG_SEGMENT", "hash": rule.effective_hash,
                                    "reason": "rule logic found defective after release"}}
    fix = {"artifact_id": "ZZ:overlay:R-ELG-001", "kind": "OVERLAY", "op": "PUT",
           "content": {"verb": "raise_priority", "priority": "Critical", "reason": "corrected version",
                       "approved_ref": "SCR-EARN"}}
    _, st = s.govern([rev, fix])
    assert st == "APPLIED"
    with s.cp.as_role("dq_api", MED) as c:
        items = {r["unit_key"]: r["item_id"] for r in c.execute("SELECT item_id, unit_key FROM dq_ctl.lookback_items")}
    assert first["unit_key"] in items and human_released[0] in items
    _withdraw(s, items[human_released[0]])                                  # a person released it: no demotion
    assert _tier(s) == "L3"
    _withdraw(s, items[first["unit_key"]], person="E210")                   # the agent released it at L3: demotes
    assert _tier(s) == "L2"
    with s.cp.as_role("dq_eval_svc") as c:
        reasons = [r["body"]["reason_code"] for r in c.execute(
            "SELECT body FROM dq_ctl.agent_blackboard WHERE template='AUTONOMY_DEMOTED' ORDER BY seq").fetchall()]
    assert reasons == ["TEST_SETUP", "VETO", "LOOKBACK_WITHDRAWN"]
