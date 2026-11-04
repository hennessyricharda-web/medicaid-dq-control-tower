"""T1: the spine §3 HTTP contract, end to end, through FastAPI's TestClient (the same calls the Tower makes)."""
import pytest
from fastapi.testclient import TestClient

from dqagent.api.app import create_app

from .fixtures_w1 import ie_file, person
from .w1_harness import Slice


@pytest.fixture
def rt(fresh_cp_dsn, tmp_path):
    r = Slice(fresh_cp_dsn)
    p = tmp_path / "ie.txt"
    p.write_bytes(ie_file([person("P1", "M0001"), person("P2", "M0002")], trailer=7))   # ABC fail ⇒ H_IN_2
    r.run_pipeline("p4_eligibility_ie", "B1", [("IE", p)], ["bronze.ie_segment"])
    return r


def H(rt, user, **kw):
    return {"Authorization": f"Bearer {rt.idp.token(user, **kw)}"}


def test_two_person_release_over_http(rt):
    api = TestClient(create_app(rt))
    assert api.get("/api/v1/queue").status_code == 401
    me = api.get("/api/v1/me", headers=H(rt, "maker")).json()
    assert me["authorities"] == ["AUTH_MEDICAID"]
    q = api.get("/api/v1/queue", headers=H(rt, "maker")).json()
    assert q and q[0]["required_mode"] == "H_IN_2"
    uk = q[0]["units"][0]
    card = api.get(f"/api/v1/units/{uk}", headers=H(rt, "maker")).json()
    assert card["state"] == "HELD" and "RELEASE" in card["allowed_decisions"]
    assert all(len(t) == 64 for d in card["dispositions"] for t in d["sample_tokens"])   # opaque tokens only
    body = {"decision": "RELEASE", "rationale": "Trailer count error confirmed by the IE vendor ticket.",
            "expected_state_version": card["state_version"], "bound_fingerprint": card["fingerprint"],
            "quarantine_row_seqs": []}
    assert api.post(f"/api/v1/units/{uk}/decide", json=body, headers=H(rt, "maker")).status_code == 428  # no step-up
    mchal = api.post(f"/api/v1/units/{uk}/challenge", json={"decision": "RELEASE", "quarantine_row_seqs": []},
                     headers=H(rt, "maker")).json()
    assert mchal["x_seat"] == "MAKER"
    body["step_up_token"] = rt.idp.token("maker", step_up=True, challenge=mchal["challenge"])
    r = api.post(f"/api/v1/units/{uk}/decide", json=body, headers=H(rt, "maker"))
    assert r.status_code == 200 and r.json()["status"] == "AWAITING_CHECKER", r.text
    assert api.post(f"/api/v1/units/{uk}/decide", json=body, headers=H(rt, "maker")).status_code == 409   # replay
    card2 = api.get(f"/api/v1/units/{uk}", headers=H(rt, "checker")).json()
    pend = card2["x_pending_decision"]
    assert pend["maker_person_id"] == "E100"
    chal = api.post(f"/api/v1/units/{uk}/challenge", json={}, headers=H(rt, "checker")).json()["challenge"]
    # replaying the maker's own identity as checker is refused
    assert api.post(f"/api/v1/units/{uk}/confirm", headers=H(rt, "checker"),          # wrong decision id
                    json={"decision_id": "D-other", "step_up_token": rt.idp.token("checker", step_up=True, challenge=chal)}
                    ).status_code == 409
    bad = api.post(f"/api/v1/units/{uk}/confirm", headers=H(rt, "maker"),
                   json={"decision_id": pend["decision_id"],
                         "step_up_token": rt.idp.token("maker", step_up=True, challenge=chal)})
    assert bad.status_code == 403
    ok = api.post(f"/api/v1/units/{uk}/confirm", headers=H(rt, "checker"),
                  json={"decision_id": pend["decision_id"], "rationale": "Independently verified the vendor ticket.",
                        "step_up_token": rt.idp.token("checker", step_up=True, challenge=chal)})
    assert ok.status_code == 200 and ok.json()["status"] == "APPLIED", ok.text
    assert api.get(f"/api/v1/units/{uk}", headers=H(rt, "maker")).json()["state"] == "RELEASED"


def test_unrostered_and_audit_scoping(rt):
    api = TestClient(create_app(rt))
    for path in ("/api/v1/queue", "/api/v1/pipelines", "/api/v1/abc", "/api/v1/summary"):
        assert api.get(path, headers=H(rt, "outsider")).status_code == 403, path
    assert api.get("/api/v1/audit", headers=H(rt, "maker")).status_code == 403          # no audit duty
    a = api.get("/api/v1/audit", headers=H(rt, "auditor"))
    assert a.status_code == 200 and a.json()


def test_pipelines_counts_respect_scope(rt):
    api = TestClient(create_app(rt))
    snap = api.get("/api/v1/pipelines", headers=H(rt, "snap")).json()
    assert all(not g["x_counts"] for p in snap["pipelines"] for g in p["gated_nodes"])   # no Medicaid counts leak


def test_snap_reviewer_cannot_see_medicaid_units(rt):
    api = TestClient(create_app(rt))
    assert api.get("/api/v1/queue", headers=H(rt, "snap")).json() == []
    uk = rt.units(state="HELD")[0]["unit_key"]
    assert api.get(f"/api/v1/units/{uk}", headers=H(rt, "snap")).status_code == 404


def test_stale_returns_409_and_short_rationale_422(rt):
    api = TestClient(create_app(rt))
    uk = rt.units(state="HELD")[0]["unit_key"]
    body = {"decision": "RELEASE", "rationale": "short", "expected_state_version": 0, "bound_fingerprint": "x"}
    assert api.post(f"/api/v1/units/{uk}/decide", json=body, headers=H(rt, "maker")).status_code == 422
    body["rationale"] = "A long enough rationale for the release decision."
    body["step_up_token"] = rt.idp.token("maker", step_up=True, challenge="x")
    assert api.post(f"/api/v1/units/{uk}/decide", json=body, headers=H(rt, "maker")).status_code == 409


def test_reveal_creates_dp_view_without_values_in_response(rt):
    api = TestClient(create_app(rt))
    uk = rt.units(state="HELD")[0]["unit_key"]
    r = api.post(f"/api/v1/units/{uk}/reveal", json={"purpose": "verify trailer evidence"}, headers=H(rt, "maker"))
    assert r.status_code == 200 and set(r.json()) == {"reveal_ref", "expires_at"}
    rows = rt.dp.fetchall(f"SELECT first_name, msis_id FROM {r.json()['reveal_ref']}")
    assert rows and all(x == ("***", "***") for x in rows)    # identity columns incl. MSIS ID are masked
    import datetime as dt
    assert rt.sweep_reveals(now=dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=1)) == [r.json()["reveal_ref"]]
    with pytest.raises(Exception):
        rt.dp.fetchall(f"SELECT 1 FROM {r.json()['reveal_ref']}")                     # dropped on expiry


def test_pipelines_and_abc_endpoints(rt):
    api = TestClient(create_app(rt))
    pl = api.get("/api/v1/pipelines", headers=H(rt, "maker")).json()
    assert pl["order"] == ["p4_eligibility_ie", "p6_claims"]
    abc = api.get("/api/v1/abc", headers=H(rt, "maker")).json()
    assert any(a["hop"] == "FILE->RAW" and a["disposition"] == "FAIL" for a in abc)


def test_rules_coverage_and_measures_endpoints(rt):
    api = TestClient(create_app(rt))
    rules = api.get("/api/v1/rules", headers=H(rt, "maker")).json()
    assert rules["state"] == "ZZ" and any(r["id"] == "R-ENC-001" and r["status"] == "ACTIVE" for r in rules["rules"])
    cov = api.get("/api/v1/coverage", headers=H(rt, "maker")).json()
    kinds = {r["coverage"] for r in cov["requirements"]}
    assert "ACTIVE" in kinds and "GAP" in kinds
    assert api.get("/api/v1/measures", headers=H(rt, "maker")).status_code == 200


def _decide_maker(api, rt, uk, verb, qrows):
    card = api.get(f"/api/v1/units/{uk}", headers=H(rt, "maker")).json()
    chal = api.post(f"/api/v1/units/{uk}/challenge", json={"decision": verb, "quarantine_row_seqs": qrows},
                    headers=H(rt, "maker")).json()["challenge"]
    body = {"decision": verb, "rationale": "Quarantine the trailer-mismatch rows pending the vendor fix.",
            "expected_state_version": card["state_version"], "bound_fingerprint": card["fingerprint"],
            "quarantine_row_seqs": qrows, "step_up_token": rt.idp.token("maker", step_up=True, challenge=chal)}
    r = api.post(f"/api/v1/units/{uk}/decide", json=body, headers=H(rt, "maker"))
    assert r.status_code == 200, r.text


def test_card_has_no_stub_values(rt):
    """F-07: metric/threshold from the results row, dependencies from the harness/contract, the dry run from a
    stored quarantine proposal only, and sla_due = HELD time + policy.sla[mode] (the /summary arithmetic)."""
    import datetime as dt

    from dqagent.governance.registry import iso_duration_seconds

    api = TestClient(create_app(rt))
    uk = rt.units(state="HELD")[0]["unit_key"]
    card = api.get(f"/api/v1/units/{uk}", headers=H(rt, "maker")).json()
    # SLA due: HELD event time + policy.sla[mode]; never created_at
    with rt.cp.as_role("dq_eval_svc") as c:
        hev = c.execute("SELECT at, coalesce(payload->>'mode','H_IN_2') mode FROM dq_ctl.unit_events WHERE unit_key=%s "
                        "AND to_state='HELD' ORDER BY state_version DESC LIMIT 1", (uk,)).fetchone()
        created = c.execute("SELECT created_at FROM dq_ctl.units WHERE unit_key=%s", (uk,)).fetchone()["created_at"]
    want = hev["at"] + dt.timedelta(seconds=iso_duration_seconds(rt.policy["sla"][hev["mode"]]))
    assert dt.datetime.fromisoformat(card["sla_due"]) == want and card["x_sla_reason"] is None
    assert dt.datetime.fromisoformat(card["sla_due"]) != created
    # dry run: no proposal yet -> null with a reason; never a fabricated chains: 1
    (qa,) = card["proposed_actions"]
    assert qa["dry_run"] is None and qa["x_dry_run_reason"] == "no proposal"
    assert qa["params"]["x_row_seqs"] == card["x_row_seqs"]           # the proposal the Tower submits is unchanged
    # metric/threshold (F-29): the engine stores the measured failing share and the rule's own thresholds ...
    fail = next(d for d in card["dispositions"] if d["disposition"] == "FAIL")
    rule = next(r for r in rt.rules if r.id == fail["rule_id"] and r.relation == card["node"])
    assert fail["evaluated"] and fail["metric"] == pytest.approx(fail["failed"] / fail["evaluated"])
    assert fail["metric"] > 0 and fail["threshold"] == (rule.thresholds or None)
    # ... negative control: a FAIL rule with a stored metric/threshold shows exactly what is stored
    with rt.cp.as_role("dq_eval_svc") as c:
        c.execute("INSERT INTO dq_ctl.results(unit_key,eval_version,authority,rule_id,rule_hash,check_class,priority,"
                  "disposition,evaluated,failed,metric,threshold,sample_tokens,ruleset_version) SELECT unit_key,"
                  "eval_version,authority,rule_id,rule_hash,check_class,priority,disposition,evaluated,failed,0.25,"
                  "'{\"max\": 0}'::jsonb,sample_tokens,ruleset_version FROM dq_ctl.results WHERE unit_key=%s AND "
                  "rule_id=%s ORDER BY id DESC LIMIT 1", (uk, fail["rule_id"]))
    card = api.get(f"/api/v1/units/{uk}", headers=H(rt, "maker")).json()
    got = next(d for d in card["dispositions"] if d["rule_id"] == fail["rule_id"])
    assert got["disposition"] == "FAIL" and got["metric"] == 0.25 and got["threshold"] == {"max": 0}
    # dry run from the stored MAKER proposal
    q = card["x_row_seqs"][:1]
    _decide_maker(api, rt, uk, "QUARANTINE_AND_RELEASE", q)
    (qa,) = api.get(f"/api/v1/units/{uk}", headers=H(rt, "checker")).json()["proposed_actions"]
    assert qa["x_dry_run_reason"] is None and qa["dry_run"]["rows_quarantined"] == len(q)
    assert qa["dry_run"]["chains"] is None and qa["dry_run"]["x_chains_reason"] == "not computed"


def test_card_dependencies_come_from_contract_not_a_literal(rt):
    """F-07: a later version of the same partition, escalated past max_wait, shows its ordering predecessor with
    the contract's ordering policy and its own domain, not the hard-coded ELIGIBILITY/WAIT pair."""
    import datetime as dt
    import pathlib
    import tempfile

    f = pathlib.Path(tempfile.mkdtemp()) / "ie2.txt"
    f.write_bytes(ie_file([person("P1", "M0001", start="20260101")]))
    rt.run_pipeline("p4_eligibility_ie", "B2", [("IE", f)], ["bronze.ie_segment"])
    (u2,) = rt.units(batch_id="B2")
    assert u2["state"] == "PENDING_DEPENDENCY"
    rt.evaluator.now = lambda: dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=3)
    rt.reevaluate_pending()
    assert rt.units(batch_id="B2")[0]["state"] == "HELD"
    card = TestClient(create_app(rt)).get(f"/api/v1/units/{u2['unit_key']}", headers=H(rt, "maker")).json()
    b1 = {u["unit_key"] for u in rt.units(batch_id="B1")}
    assert card["dependencies"], "the escalated unit's dependency vector is on the card"
    for d in card["dependencies"]:
        assert d["unit"] in b1 and d["domain"] == rt.contracts[card["node"]]["domain"]
        assert d["policy"] == rt.contracts[card["node"]]["ordering"] and d["x_basis"] == "contract.ordering"


def test_llm_provider_label_is_real(rt, monkeypatch):
    """F-16: llm_provider reports the configured explainer, plus its derived transport; never 'NullProvider'."""
    from dqagent import llm as _llm

    api = TestClient(create_app(rt))
    s = api.get("/api/v1/summary", headers=H(rt, "maker")).json()
    a = api.get("/api/v1/agent/state", headers=H(rt, "maker")).json()
    st = _llm.status(_llm.load_config())
    for body in (s, a):
        assert body["llm_provider"] == st["provider"] != "NullProvider"
        assert body["explainer"]["transport"] == _llm.transport_for(_llm.load_config(), rt.deployment)
        assert body["explainer"]["label"] == st["label"] and "source_mode" in body["explainer"]
    # negative control: a configured HTTP provider is reported as such (the label tracks the config, not a literal)
    monkeypatch.setattr(_llm, "load_config", lambda: {"provider": "anthropic", "providers": {}})
    api2 = TestClient(create_app(rt))
    for path in ("/api/v1/summary", "/api/v1/agent/state"):
        b = api2.get(path, headers=H(rt, "maker")).json()
        assert b["llm_provider"] == "anthropic" and b["explainer"]["provider"] == "anthropic"
        assert b["explainer"]["transport"] == "http" and b["explainer"]["x_transport_refused"] is None
