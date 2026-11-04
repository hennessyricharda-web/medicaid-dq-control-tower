"""Part D over HTTP: the calls the Tower makes for change requests, onboarding and measure series."""
from fastapi.testclient import TestClient

from dqagent.api.app import create_app

from .w1_harness import Slice


def H(rt, user, **kw):
    return {"Authorization": f"Bearer {rt.idp.token(user, **kw)}"}


def test_change_request_flow_over_http(fresh_cp_dsn):
    rt = Slice(fresh_cp_dsn)
    api = TestClient(create_app(rt))
    assert api.get("/api/v1/governance/state", headers=H(rt, "outsider")).status_code == 403
    st = api.get("/api/v1/governance/state", headers=H(rt, "maker")).json()
    assert st["ratified"] and st["drift"] is None and any(a["artifact_id"] == "ZZ:feeds" for a in st["artifacts"])
    item = {"artifact_id": "ZZ:overlay:R-ENC-001", "kind": "OVERLAY", "op": "PUT",
            "content": {"verb": "raise_priority", "priority": "Critical", "reason": "state priority",
                        "approved_ref": "SCR-7"}}
    r = api.post("/api/v1/governance/change-requests", headers=H(rt, "maker"),
                 json={"rationale": "Raise balancing rule priority", "items": [item]})
    assert r.status_code == 200, r.text
    cr = r.json()["cr_id"]
    lst = api.get("/api/v1/governance/change-requests", headers=H(rt, "checker")).json()
    assert lst[0]["cr_id"] == cr and lst[0]["status"] == "AWAITING_APPROVAL"
    d = api.get(f"/api/v1/governance/change-requests/{cr}", headers=H(rt, "maker")).json()
    assert d["x_you_are_author"] and d["impact"]["rules"] and d["items"][0]["current"] is None
    assert api.post(f"/api/v1/governance/change-requests/{cr}/decide", headers=H(rt, "checker"),
                    json={"verdict": "APPROVED"}).status_code == 428
    ch = api.post(f"/api/v1/governance/change-requests/{cr}/challenge", headers=H(rt, "maker"),
                  json={"verdict": "APPROVED"}).json()["challenge"]
    own = api.post(f"/api/v1/governance/change-requests/{cr}/decide", headers=H(rt, "maker"),
                   json={"verdict": "APPROVED", "step_up_token": rt.idp.token("maker", step_up=True, challenge=ch)})
    assert own.status_code == 403
    ok = api.post(f"/api/v1/governance/change-requests/{cr}/decide", headers=H(rt, "checker"),
                  json={"verdict": "APPROVED", "step_up_token": rt.idp.token("checker", step_up=True, challenge=ch)})
    assert ok.status_code == 200 and ok.json()["status"] == "APPLIED", ok.text
    rules = api.get("/api/v1/rules", headers=H(rt, "maker")).json()
    r1 = next(x for x in rules["rules"] if x["id"] == "R-ENC-001")
    assert r1["approved_by_cr"] == cr and r1["overlay"]["verb"] == "raise_priority" and r1["priority"] == "Critical"
    bad = api.post("/api/v1/governance/change-requests", headers=H(rt, "maker"), json={
        "rationale": "attempt to widen", "items": [{"artifact_id": "ZZ:overlay:M-RULE-7907", "kind": "OVERLAY", "op": "PUT",
                                                    "content": {"verb": "tighten", "thresholds": {"max": 0.9},
                                                                "reason": "r", "approved_ref": "x"}}]})
    assert bad.status_code == 422


def test_onboarding_profile_is_allowlisted_and_proposal_becomes_a_change_request(fresh_cp_dsn, tmp_path):
    from .fixtures_w1 import ie_file, person
    rt = Slice(fresh_cp_dsn)
    rows = [person(f"P{i}", f"M{i:04d}") for i in range(15)]
    p = tmp_path / "ie.txt"
    p.write_bytes(ie_file(rows))
    rt.run_pipeline("p4_eligibility_ie", "B-ELG-1", [("IE", p)], ["bronze.ie_segment"])
    api = TestClient(create_app(rt))
    assert api.post("/api/v1/onboarding/profile", headers=H(rt, "maker"),
                    json={"relations": ["dq_ctl.roster"]}).status_code == 403            # not allowlisted
    prof = api.post("/api/v1/onboarding/profile", headers=H(rt, "maker"),
                    json={"relations": ["bronze.ie_segment"]}).json()["profiles"]
    blob = str(prof)
    assert "M0001" not in blob and "Jane" not in blob                                     # aggregates only
    r = api.post("/api/v1/onboarding/propose-bindings", headers=H(rt, "maker"),
                 json={"state": "ZZ", "relations": ["bronze.ie_segment"], "rationale": "Onboard eligibility bindings"})
    assert r.status_code == 200, r.text                                                    # becomes a change request
    body = r.json()
    assert body["cr_id"].startswith("CR-") and body["profile_digest"]
    assert any(x["element"] == "ELG.MEMBER_ID" and x["column"] == "msis_id" and x["needs_attention"]
               for x in body["proposals"])
    d = api.get(f"/api/v1/governance/change-requests/{body['cr_id']}", headers=H(rt, "checker")).json()
    assert d["origin"] == "PROPOSER" and d["status"] == "AWAITING_APPROVAL"
    merged = d["items"][0]["content"]["columns"]                                           # a MERGE: nothing removed
    assert "CLM.MEMBER_ID" in merged and merged["ELG.MEMBER_ID"] == "msis_id"


def test_measure_series_suppresses_small_cells(fresh_cp_dsn, tmp_path):
    from .fixtures_w1 import acks, ie_file, person, x837
    rt = Slice(fresh_cp_dsn)
    p = tmp_path / "ie.txt"
    p.write_bytes(ie_file([person("P1", "M0001")]))
    rt.run_pipeline("p4_eligibility_ie", "B-ELG-1", [("IE", p)], ["bronze.ie_segment"])
    x = tmp_path / "e.x12"
    x.write_bytes(x837("ZZMCOA", "000000101", [{"id": "C1", "member": "M0001", "lines": [("Z9001", 10.0, "20260110")]}]))
    a = tmp_path / "e.csv"
    a.write_bytes(acks("000000101", ["A"]))
    rt.run_pipeline("p6_claims", "B-ENC-1", [("X12_837P", x, "ZZ-MCO-A", a)], ["bronze.encounter_header", "bronze.encounter_line"])
    api = TestClient(create_app(rt))
    s = api.get("/api/v1/measures/series", params={"rule_id": "M-RULE-7907"}, headers=H(rt, "maker")).json()
    assert s["points"] and all(pt["suppressed"] and pt["value"] is None for pt in s["points"])   # n=1 < 11
    assert s["thresholds"]["max"] == 0.01
    rows = api.get("/api/v1/measures", headers=H(rt, "maker")).json()          # the table endpoint too
    assert rows and all(r["suppressed"] and r["numerator"] is None and r["denominator"] is None for r in rows)
