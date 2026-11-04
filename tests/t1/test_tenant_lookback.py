"""Tenant approver organisation and the look-back over releases made under a revoked rule version."""
import psycopg
import pytest
from fastapi.testclient import TestClient

from dqagent.api.app import create_app
from dqagent.hitl.decisions import Denied

from .fixtures_w1 import ie_file, person
from .w1_harness import Slice

TENANT = {"display_name": "State A", "agency": "State Medicaid agency", "division": "Program division",
          "organisations": {"STATE": "State agency", "VENDOR": "Implementation partner"},
          "people": {"E100": "VENDOR", "E200": "STATE", "E210": "VENDOR", "E900": "STATE"},
          "governance": {"approver_org": "STATE"}}


def H(rt, user, **kw):
    return {"Authorization": f"Bearer {rt.idp.token(user, **kw)}"}


def test_configuration_needs_an_approver_from_the_tenant_approver_org(fresh_cp_dsn):
    s = Slice(fresh_cp_dsn, tenant=TENANT)
    api = TestClient(create_app(s))
    assert api.get("/api/v1/tenant").json()["approver_org"] == "STATE"
    assert api.get("/api/v1/me", headers=H(s, "maker")).json()["org"] == "VENDOR"
    item = {"artifact_id": "ZZ:overlay:R-ENC-001", "kind": "OVERLAY", "op": "PUT",
            "content": {"verb": "raise_priority", "priority": "Critical", "reason": "r", "approved_ref": "SCR-1"}}
    cr = s.registry.propose("ZZ", "E100", "vendor analyst proposes a priority change", [item])
    tok = s.idp.token("checker2", step_up=True, challenge=s.registry.challenge(cr["cr_id"], "APPROVED"))
    with pytest.raises(Denied, match="organisation"):               # a VENDOR person cannot approve
        s.registry.decide(cr["cr_id"], "APPROVED", tok, "E210", ["governance:AUTH_RULES"])
    tok = s.idp.token("checker", step_up=True, challenge=s.registry.challenge(cr["cr_id"], "APPROVED"))
    s.registry.decide(cr["cr_id"], "APPROVED", tok, "E200", ["governance:AUTH_RULES"])
    assert s.apply_change(cr["cr_id"]) == "APPLIED"


def test_revocation_opens_a_lookback_for_human_rereview_and_withdraw_denies(fresh_cp_dsn, tmp_path):
    s = Slice(fresh_cp_dsn, tenant=TENANT)                       # tenant ON: STATE approves, VENDOR proposes
    p = tmp_path / "ie.txt"
    p.write_bytes(ie_file([person("P1", "M0001"), person("P2", "M0002")]))
    s.run_pipeline("p4_eligibility_ie", "B-ELG-1", [("IE", p)], ["bronze.ie_segment"])
    out = s.verify_and_publish()
    assert len(out["released"]) == 2
    rule = next(r for r in s.rules if r.id == "R-ELG-001")
    rev = {"artifact_id": f"ZZ:revocation:R-ELG-001@ELIG_SEGMENT:{rule.effective_hash[:12]}", "kind": "REVOCATION",
           "op": "PUT", "content": {"rule": "ZZ:R-ELG-001@ELIG_SEGMENT", "hash": rule.effective_hash,
                                    "reason": "rule logic found defective after release"}}
    fix = {"artifact_id": "ZZ:overlay:R-ELG-001", "kind": "OVERLAY", "op": "PUT",
           "content": {"verb": "raise_priority", "priority": "Critical", "reason": "corrected version",
                       "approved_ref": "SCR-2"}}
    with pytest.raises(Exception, match="never approved"):
        s.registry.propose("ZZ", "E100", "revoke an invented version",
                           [{**rev, "content": {**rev["content"], "hash": "f" * 16}}, fix])
    with pytest.raises(Exception, match="replacement"):             # a revocation must bring a new version
        s.registry.propose("ZZ", "E100", "revoke without replacement", [rev])
    cr, status = s.govern([rev, fix])                                # approver: checker (STATE)
    assert status == "APPLIED"
    api = TestClient(create_app(s))
    items = api.get("/api/v1/lookback", headers=H(s, "maker")).json()
    assert len(items) == 2 and all(i["status"] == "OPEN" and i["in_live_manifest"] for i in items)
    board = api.get("/api/v1/agent/blackboard", headers=H(s, "maker")).json()
    assert any(b["template"] == "LOOKBACK_FLAGGED" and b["seat"] == "REGISTRY" for b in board)
    re = api.post(f"/api/v1/lookback/{items[0]['item_id']}/reassess", headers=H(s, "maker")).json()
    assert re["replacement_version"] and re["replacement_version"] != rule.effective_hash[:12]
    # the revoked version can never be approved again, by any later request (red-team C1)
    later = {"artifact_id": "ZZ:overlay:R-ELG-001", "kind": "OVERLAY", "op": "DELETE"}
    with pytest.raises(Exception, match="REVOKED"):
        s.registry.propose("ZZ", "E100", "drop the overlay: would bring the revoked version back", [later])
    it = items[0]["item_id"]
    for who, code in (("maker", 409), ("checker", 200)):         # VENDOR cannot resolve; STATE can
        ch = api.post(f"/api/v1/lookback/{it}/challenge", headers=H(s, who), json={"verdict": "WITHDRAWN"}).json()
        body = {"verdict": "WITHDRAWN", "rationale": "Released under the revoked DOB rule; withdraw pending re-review.",
                "step_up_token": s.idp.token(who, step_up=True, challenge=ch["challenge"])}
        if who == "checker":
            assert api.post(f"/api/v1/lookback/{it}/decide", headers=H(s, who), json={**body, "verdict": "ACCEPTED"}
                            ).status_code == 403                     # token bound to another verdict
        r = api.post(f"/api/v1/lookback/{it}/decide", headers=H(s, who), json=body)
        assert r.status_code == code, (who, r.text)
    vis = {r[0] for r in s.dp.fetchall("SELECT _dq_unit_key FROM dq_rel.ie_segment_released")}
    assert items[0]["unit_key"] not in vis and items[1]["unit_key"] in vis
    after = {i["item_id"]: i["status"] for i in api.get("/api/v1/lookback", headers=H(s, "maker")).json()}
    assert after[it] == "WITHDRAWN" and after[items[1]["item_id"]] == "OPEN"
    with psycopg.connect(**{**fresh_cp_dsn, "user": "dq_api"}) as c:   # no back door around lookback_decide
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            c.execute("INSERT INTO dq_ctl.lookback_events(item_id,event) VALUES (%s,'WITHDRAWN')", (items[1]["item_id"],))


def test_a_tenant_requiring_an_approver_org_refuses_an_unconfigured_cp(fresh_cp_dsn):
    from dqagent.runtime import Runtime
    with pytest.raises(RuntimeError, match="approver organisation"):
        Runtime(fresh_cp_dsn, tenant=TENANT)


def test_lookback_demotion_scoped_to_state(fresh_cp_dsn, tmp_path):
    """Part F E-05 (E8): withdrawing a look-back item for an AUTONOMOUS release demotes the RELEASE tier of the state
    whose revocation raised it, and leaves another state's tier for the same agent and authority alone."""
    import json
    from dqagent.cp.agent_schema import DQ_AGENT
    s = Slice(fresh_cp_dsn)
    with psycopg.connect(**fresh_cp_dsn) as adm:                       # a second state's tier, same agent
        adm.execute("INSERT INTO dq_ctl.config_artifacts(artifact_id,version,state,kind,op,content,content_hash,cr_id) "
                    "VALUES (%s,1,'YY','AUTONOMY','PUT',%s::jsonb,'h','TEST-YY')",
                    (f"YY:autonomy:{DQ_AGENT}:RELEASE", json.dumps({"AUTH_MEDICAID": "L4"})))
    p = tmp_path / "ie.txt"
    p.write_bytes(ie_file([person("P1", "M0001"), person("P2", "M0002")]))
    s.run_pipeline("p4_eligibility_ie", "B-ELG-1", [("IE", p)], ["bronze.ie_segment"])
    assert len(s.verify_and_publish()["released"]) == 2               # L4: released by the agent on its own
    rule = next(r for r in s.rules if r.id == "R-ELG-001")
    rev = {"artifact_id": f"ZZ:revocation:R-ELG-001@ELIG_SEGMENT:{rule.effective_hash[:12]}", "kind": "REVOCATION",
           "op": "PUT", "content": {"rule": "ZZ:R-ELG-001@ELIG_SEGMENT", "hash": rule.effective_hash,
                                    "reason": "rule logic found defective after release"}}
    fix = {"artifact_id": "ZZ:overlay:R-ELG-001", "kind": "OVERLAY", "op": "PUT",
           "content": {"verb": "raise_priority", "priority": "Critical", "reason": "corrected version",
                       "approved_ref": "SCR-2"}}
    _, status = s.govern([rev, fix])
    assert status == "APPLIED"
    with s.cp.as_role("dq_api", ["AUTH_MEDICAID"]) as c:
        item = c.execute("SELECT item_id FROM dq_ctl.lookback_items ORDER BY item_id LIMIT 1").fetchone()["item_id"]
        c.execute("SELECT dq_ctl.lookback_decide(%s,%s,%s,'WITHDRAWN',%s,NULL)",
                  (item, "E200", "jti-e05", "Released under the revoked rule version; withdraw it."))
    assert s.registry.tier("ZZ", DQ_AGENT, "RELEASE", "AUTH_MEDICAID") == "L2"    # the revoking state is demoted
    assert s.registry.tier("YY", DQ_AGENT, "RELEASE", "AUTH_MEDICAID") == "L4"    # the other state keeps its tier
