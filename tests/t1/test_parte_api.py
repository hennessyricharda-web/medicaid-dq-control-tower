"""Part E over HTTP (PARTE_DESIGN §1, §2, §3, §7, §8, §9 A1/A6, §10): veto, two-person promotion, entitlement expiry,
audit-chain verification, export allow-lists, the summary genesis enum, feed items and the parallel-run step-up."""
import datetime as dt
import json

import psycopg
import pytest
from fastapi.testclient import TestClient

from dqagent.api.app import GENESIS_LABELS, create_app

from .evidence import allow_promotions
from .fixtures_w1 import ie_file, person
from .w1_harness import Slice

AGENT = "dq-reconciliation"
DATA_VALUES = ("M0001", "M0002", "M0003", "M0004", "Jane", "Doe", "19800101", "H P1", "HP1", "HP3")


def H(rt, user, **kw):
    return {"Authorization": f"Bearer {rt.idp.token(user, **kw)}"}


def ref(e):
    return {"facts_hash": e["facts_hash"], "text_hash": e["text_hash"]}


def _run(s, tmp, batch, people, trailer=None):
    p = tmp / f"{batch}.txt"
    p.write_bytes(ie_file(people, trailer=trailer))
    return s.run_pipeline("p4_eligibility_ie", batch, [("IE", p)], ["bronze.ie_segment"])


# -- §1.4 veto ----------------------------------------------------------------------------------------------------
def test_veto_inside_the_window_holds_and_demotes_and_is_409_after_release(fresh_cp_dsn, tmp_path):
    s = Slice(fresh_cp_dsn)
    api = TestClient(create_app(s))
    _run(s, tmp_path, "B-REL", [person("P1", "M0001"), person("P2", "M0002")])
    s.verify_and_publish()
    rel = s.units(state="RELEASED")[0]["unit_key"]
    card = api.get(f"/api/v1/units/{rel}", headers=H(s, "maker")).json()
    r = api.post(f"/api/v1/units/{rel}/veto", headers=H(s, "maker"),
                 json={"reason_code": "DATA_SUSPECT", "explanation_ref": ref(card["explanation"])})
    assert r.status_code == 409, r.text                                  # past veto: the unit has released

    _run(s, tmp_path, "B-HELD", [person("P3", "M0003"), person("P4", "M0004")], trailer=7)
    u = s.units(state="HELD")[0]
    with s.cp.as_role("dq_api", ["AUTH_MEDICAID"]) as c:                 # put it behind an open veto window (as L3)
        v = s.cp.transition(c, u["unit_key"], u["state_version"], "EVALUATING", "RE_PIN", "E100", {})
    with s.cp.as_role("dq_eval_svc") as c:
        c.execute("INSERT INTO dq_ctl.veto_windows(unit_key,eval_version,window_s,policy_hash) VALUES (%s,%s,600,'t')",
                  (u["unit_key"], v))
    q = api.get("/api/v1/queue", headers=H(s, "maker")).json()
    vcl = next(cl for cl in q if u["unit_key"] in cl["units"])
    assert vcl["required_mode"] == "H_ON" and vcl["veto_deadline"]
    card = api.get(f"/api/v1/units/{u['unit_key']}", headers=H(s, "maker")).json()
    assert card["state"] == "EVALUATING" and card["veto_window"]["remaining_s"] > 0
    assert "veto" in card["explanation"]["text"].lower()
    url = f"/api/v1/units/{u['unit_key']}/veto"
    assert api.post(url, headers=H(s, "maker"), json={"reason_code": "DATA_SUSPECT"}).status_code == 422  # no ref
    bad = {"facts_hash": "0" * 64, "text_hash": card["explanation"]["text_hash"]}
    assert api.post(url, headers=H(s, "maker"),
                    json={"reason_code": "DATA_SUSPECT", "explanation_ref": bad}).status_code == 422  # mismatched
    assert api.post(url, headers=H(s, "maker"), json={"reason_code": "BECAUSE"}).status_code == 422
    ok = api.post(url, headers=H(s, "maker"),
                  json={"reason_code": "DATA_SUSPECT", "explanation_ref": ref(card["explanation"])})
    assert ok.status_code == 200 and ok.json()["state"] == "HELD", ok.text
    assert ok.json()["explanation"]["text"]
    assert s.registry.tier("ZZ", AGENT, "RELEASE", "AUTH_MEDICAID") == "L2"          # the veto demoted the authority
    au = {(r["action_class"], r["authority"]): r for r in api.get("/api/v1/autonomy", headers=H(s, "maker")).json()}
    med = au[("RELEASE", "AUTH_MEDICAID")]
    assert med["tier"] == "L2" and med["demotions"][-1]["reason_code"] == "VETO"
    assert med["demotions"][-1]["explanation"]["text"] and med["demotions"][-1]["from_tier"] == "L4"
    assert med["track_record"]["since"] >= med["demotions"][-1]["at"]       # §1.3: the record restarts at the change
    again = api.post(url, headers=H(s, "maker"),
                     json={"reason_code": "DATA_SUSPECT", "explanation_ref": ref(card["explanation"])})
    assert again.status_code == 409                                         # HELD now: no longer vetoable


# -- §1.2 promotion is two-person ---------------------------------------------------------------------------------
def test_single_approver_leaves_a_promotion_awaiting_and_it_never_applies(fresh_cp_dsn):
    s = Slice(fresh_cp_dsn)
    allow_promotions(s)                  # R4-5: evidence + zero minimums; the subject here is the two-person rule
    api = TestClient(create_app(s))
    up = s.effective_item("AUTONOMY", f"autonomy:{AGENT}:RELEASE")
    up["content"]["AUTH_SNAP"] = "L3"
    r = api.post("/api/v1/governance/change-requests", headers=H(s, "maker"),
                 json={"rationale": "Promote SNAP release to supervised", "items": [up]})
    assert r.status_code == 200 and r.json()["approvals_required"] == 2, r.text
    tr = r.json()["impact"]["track_record"]
    assert tr["hash"] and tr["rows"] and "vetoes" in tr                     # server snapshot (+ display fields)
    cr = r.json()["cr_id"]
    d = api.get(f"/api/v1/governance/change-requests/{cr}", headers=H(s, "checker")).json()
    assert "L2 to L3" in d["explanation"]["text"] and d["impact"]["explanation"]["text"]
    forged = {"facts_hash": "a" * 64, "text_hash": "b" * 64}
    assert api.post(f"/api/v1/governance/change-requests/{cr}/challenge", headers=H(s, "checker"),
                    json={"verdict": "APPROVED", "explanation_ref": forged}).status_code == 422
    ch = api.post(f"/api/v1/governance/change-requests/{cr}/challenge", headers=H(s, "checker"),
                  json={"verdict": "APPROVED", "explanation_ref": ref(d["explanation"])}).json()
    assert ch["explanation_ref"] == ref(d["explanation"])

    def decide(user, **extra):
        return api.post(f"/api/v1/governance/change-requests/{cr}/decide", headers=H(s, user),
                        json={"verdict": "APPROVED", **extra,
                              "step_up_token": s.idp.token(user, step_up=True, challenge=ch["challenge"])})
    assert decide("maker").status_code == 403                               # the author is never an approver
    assert decide("checker", explanation_ref=forged).status_code == 422     # differs from the bound explanation
    one = decide("checker")
    assert one.status_code == 200 and one.json()["status"] == "AWAITING_APPROVAL", one.text
    assert decide("checker").status_code == 409                             # the same person cannot be the second
    assert s.registry.tier("ZZ", AGENT, "RELEASE", "AUTH_SNAP") == "L2"     # never applied on one approval
    d2 = api.get(f"/api/v1/governance/change-requests/{cr}", headers=H(s, "checker")).json()
    assert d2["status"] == "AWAITING_APPROVAL" and not any(e["event"] == "APPLIED" for e in d2["events"])
    # a step-up not obtained through this server's challenge carries no bound explanation: 422 on decide
    raw = s.idp.token("checker2", step_up=True, challenge=s.registry.challenge(cr, "APPROVED"))
    assert api.post(f"/api/v1/governance/change-requests/{cr}/decide", headers=H(s, "checker2"),
                    json={"verdict": "APPROVED", "step_up_token": raw}).status_code == 422
    with s.cp.as_role("dq_api") as c:                                       # the ref is stored with the approval
        ev = c.execute("SELECT explanation_ref FROM dq_ctl.config_cr_events WHERE cr_id=%s AND event='APPROVED'",
                       (cr,)).fetchone()
    assert ev["explanation_ref"] == ref(d["explanation"])


# -- §2 entitlement expiry ----------------------------------------------------------------------------------------
def test_an_expired_entitlement_shows_live_false(fresh_cp_dsn):
    s = Slice(fresh_cp_dsn)
    api = TestClient(create_app(s))
    ag = {a["agent_id"]: a for a in api.get("/api/v1/agents", headers=H(s, "maker")).json()}
    assert ag[AGENT]["status"] == "ACTIVE" and all(e["live"] for e in ag[AGENT]["entitlements"])
    assert ag["data-governance-classification"]["status"] == "REGISTERED"
    ent = s.effective_item("ENTITLEMENT", f"entitlement:{AGENT}:PIN_MEMORY")
    nb = dt.datetime.fromisoformat(ent["content"]["not_before"])
    ent["content"]["not_after"] = (nb + dt.timedelta(seconds=1)).isoformat()   # lower-only: one approver
    if dt.datetime.now(dt.timezone.utc) < nb + dt.timedelta(seconds=2):
        pytest.skip("within the first seconds of a UTC day")
    assert s.govern([ent])[1] == "APPLIED"
    ag = {a["agent_id"]: a for a in api.get("/api/v1/agents", headers=H(s, "maker")).json()}
    live = {e["action_class"]: e["live"] for e in ag[AGENT]["entitlements"]}
    assert live == {"PIN_MEMORY": False, "POST_BOARD": True, "RELEASE": True}


# -- A6 audit verification ----------------------------------------------------------------------------------------
def test_audit_verify_catches_a_tampered_row_and_audit_rows_have_labels(fresh_cp_dsn, tmp_path):
    s = Slice(fresh_cp_dsn)
    api = TestClient(create_app(s))
    _run(s, tmp_path, "B1", [person("P1", "M0001")], trailer=7)
    rows = api.get("/api/v1/audit", headers=H(s, "auditor")).json()
    assert rows and all(r["label"] and r["label"] != r["event"] for r in rows)
    v = api.get("/api/v1/audit/verify", headers=H(s, "auditor")).json()
    assert v["audit"]["ok"] and v["audit"]["first_break"] is None and v["audit"]["through_seq"] == len(rows)
    assert v["blackboard"]["ok"] and v["blackboard"]["through_seq"] >= 1
    with psycopg.connect(**fresh_cp_dsn) as c:                       # the owner bypasses the append-only trigger
        c.execute("ALTER TABLE dq_ctl.audit_log DISABLE TRIGGER USER")
        c.execute("UPDATE dq_ctl.audit_log SET event='TAMPERED' WHERE seq=2")
        c.execute("ALTER TABLE dq_ctl.audit_log ENABLE TRIGGER USER")
    v = api.get("/api/v1/audit/verify", headers=H(s, "auditor")).json()
    assert not v["audit"]["ok"] and v["audit"]["first_break"] == 2 and v["audit"]["through_seq"] == 1
    assert api.get("/api/v1/audit/verify", headers=H(s, "outsider")).status_code == 403


# -- §8 B3 exports --------------------------------------------------------------------------------------------------
def test_exports_carry_only_allow_listed_fields_and_no_values(fresh_cp_dsn, tmp_path):
    from dqagent.adapters.metadata.assertions import FIELDS
    from dqagent.adapters.metadata.lineage import EDGE_PROPS, EDGE_TYPES
    s = Slice(fresh_cp_dsn)
    api = TestClient(create_app(s))
    _run(s, tmp_path, "B-REL", [person("P1", "M0001"), person("P2", "M0002")])
    s.verify_and_publish()
    _run(s, tmp_path, "B-HELD", [person("P3", "M0003")], trailer=7)
    a = api.get("/api/v1/export/dq-assertions", headers=H(s, "maker"))
    assert a.status_code == 200 and a.json()
    for row in a.json():
        assert tuple(row) == FIELDS
        for k in ("evaluated", "failed"):                                 # small cells (1-10) suppressed
            assert row[k] is None or row[k] == 0 or row[k] >= 11, row
    lin = api.get("/api/v1/export/lineage", headers=H(s, "maker")).json()
    assert set(lin) == {"producer", "schemaURL", "state", "datasets", "jobs", "edges"}
    for e in lin["edges"]:
        assert e["type"] in EDGE_TYPES and set(e["props"]) <= set(EDGE_PROPS[e["type"]]) and set(e) == {
            "type", "src", "dst", "props"}
    assert {e["type"] for e in lin["edges"]} >= {"checks", "binds", "result_of", "released_in"}
    for d in lin["datasets"]:
        assert all(set(f) == {"name"} for f in d["facets"]["schema"]["fields"])
    blob = json.dumps(a.json()) + json.dumps(lin)
    for v in DATA_VALUES:
        assert v not in blob, v
    assert api.get("/api/v1/export/lineage", headers=H(s, "maker")).json() == lin       # deterministic
    assert api.get("/api/v1/export/dq-assertions", headers=H(s, "outsider")).status_code == 403


# -- A1 summary -----------------------------------------------------------------------------------------------------
def test_summary_genesis_enum_sla_and_decisions_due(fresh_cp_dsn, tmp_path):
    s = Slice(fresh_cp_dsn)
    api = TestClient(create_app(s))
    _run(s, tmp_path, "B1", [person("P1", "M0001"), person("P2", "M0002")], trailer=7)
    sm = api.get("/api/v1/summary", headers=H(s, "maker")).json()
    assert sm["genesis_state"] in GENESIS_LABELS and sm["genesis_state"] == "TEST_GENESIS"
    assert sm["genesis_state"] != "RATIFIED"                            # the Tower alerts on anything but RATIFIED
    assert sm["genesis_label"] == GENESIS_LABELS["TEST_GENESIS"]
    assert sm["held_units"] == 2 and sm["decisions_due"] == 2
    assert sm["oldest_held_age_min"] == 0
    assert 48 * 60 - 2 <= sm["oldest_sla_remaining_min"] <= 48 * 60    # H_IN_2: PT48H from the HELD time
    ex = sm["explainer"]                                                # F-16 adds keys; the original three are unchanged
    assert {k: ex[k] for k in ("provider", "decisions", "label")} == {
        "provider": "template", "decisions": "deterministic", "label": "built-in template (offline)"}
    assert ex["transport"] == "none" and ex["x_transport_refused"] is None and "source_mode" in ex
    item = {"artifact_id": "ZZ:overlay:R-ENC-001", "kind": "OVERLAY", "op": "PUT",
            "content": {"verb": "raise_priority", "priority": "Critical", "reason": "state priority",
                        "approved_ref": "SCR-7"}}
    s.govern([item])
    assert api.get("/api/v1/summary", headers=H(s, "maker")).json()["genesis_state"] == "TEST_GENESIS_PLUS"


# -- §3.2 feed items and §3.3 parallel run ----------------------------------------------------------------------------
def test_feed_ack_and_parallel_run_step_up(fresh_cp_dsn):
    s = Slice(fresh_cp_dsn)
    api = TestClient(create_app(s))
    with s.cp.as_role("dq_seal_svc") as c:
        c.execute("SELECT dq_ctl.feed_item_open('ZZ','X12_837P','EMPTY_DELIVERY','B-E','F-E','AUTH_MEDICAID',NULL)")
        for m, status in (("records", "BREAK"), ("amount:total_charge", "BREAK")):
            c.execute("INSERT INTO dq_ctl.parallel_run_results(run_id,system,period,as_of,relation,measure,legacy,new,"
                      "delta,tolerance,status,authority) VALUES ('PR-1','LEGACY','2026-Q1','2026-04-01',"
                      "'bronze.encounter_header',%s,140,174,34,0,%s,'AUTH_MEDICAID')", (m, status))
    fi = api.get("/api/v1/feeds/items", headers=H(s, "maker")).json()
    assert fi[0]["status"] == "OPEN" and fi[0]["explanation"]["text"]
    url = f"/api/v1/feeds/items/{fi[0]['item_id']}/ack"
    assert api.post(url, headers=H(s, "maker"), json={"reason_code": "NOPE"}).status_code == 422
    ok = api.post(url, headers=H(s, "maker"), json={"reason_code": "HOLIDAY",
                                                    "explanation_ref": ref(fi[0]["explanation"])})
    assert ok.status_code == 200 and ok.json()["status"] == "ACKED" and ok.json()["reason_code"] == "HOLIDAY"
    assert api.post(url, headers=H(s, "maker"), json={"reason_code": "HOLIDAY"}).status_code == 409
    rows = api.get("/api/v1/parallel-run", headers=H(s, "maker"), params={"period": "2026-Q1"}).json()
    r1, r2 = rows[0]["row_id"], rows[1]["row_id"]
    assert api.post(f"/api/v1/parallel-run/{r1}/explain", headers=H(s, "maker"),
                    json={"explain_code": "TIMING"}).json()["explain_code"] == "TIMING"
    assert api.post(f"/api/v1/parallel-run/{r2}/explain", headers=H(s, "maker"),
                    json={"explain_code": "LEGACY_DEFECT"}).status_code == 428
    ch = api.post(f"/api/v1/parallel-run/{r2}/challenge", headers=H(s, "maker"),
                  json={"explain_code": "LEGACY_DEFECT"}).json()
    other = s.idp.token("maker", step_up=True, challenge=ch["challenge"])
    assert api.post(f"/api/v1/parallel-run/{r2}/explain", headers=H(s, "maker"),       # bound to another code
                    json={"explain_code": "DEFINITION", "step_up_token": other}).status_code == 403
    tok = s.idp.token("maker", step_up=True, challenge=ch["challenge"])
    done = api.post(f"/api/v1/parallel-run/{r2}/explain", headers=H(s, "maker"),
                    json={"explain_code": "LEGACY_DEFECT", "step_up_token": tok})
    assert done.status_code == 200 and done.json()["explain_code"] == "LEGACY_DEFECT", done.text
    assert done.json()["counts_for_go_no_go"] is True
    # E3: every feed acknowledgement and every explain code stores the explanation shown, never NULL (the server binds
    # the current explanation when the body sends none; a stale or foreign ref is refused)
    with s.cp.as_role("dq_auditor") as c:
        acks = c.execute("SELECT explanation_ref FROM dq_ctl.feed_item_events WHERE event='ACKNOWLEDGED'").fetchall()
        prx = c.execute("SELECT explain_code, explanation_ref FROM dq_ctl.parallel_run_explanations").fetchall()
    assert acks and all(a["explanation_ref"] for a in acks)
    assert {x["explain_code"] for x in prx} == {"TIMING", "LEGACY_DEFECT"} and all(x["explanation_ref"] for x in prx)
    assert prx[0]["explanation_ref"] == ref(rows[0]["explanation"])                  # TIMING sent no ref: bound
    with s.cp.as_role("dq_seal_svc") as c:
        c.execute("SELECT dq_ctl.feed_item_open('ZZ','X12_837P','EMPTY_DELIVERY','B-E2','F-E2','AUTH_MEDICAID',NULL)")
    fi2 = [x for x in api.get("/api/v1/feeds/items", headers=H(s, "maker")).json() if x["status"] == "OPEN"][0]
    bad = api.post(f"/api/v1/feeds/items/{fi2['item_id']}/ack", headers=H(s, "maker"),
                   json={"reason_code": "HOLIDAY", "explanation_ref": {"facts_hash": "0" * 64, "text_hash": "0" * 64}})
    assert bad.status_code == 422


# -- §8 B1 blackboard namespace, §5 classification ------------------------------------------------------------------
def test_blackboard_agent_filter_typed_refs_and_classification(fresh_cp_dsn, tmp_path):
    s = Slice(fresh_cp_dsn)
    api = TestClient(create_app(s))
    _run(s, tmp_path, "B1", [person("P1", "M0001")], trailer=7)
    bb = api.get("/api/v1/agent/blackboard", headers=H(s, "maker"), params={"agent": AGENT}).json()
    assert bb and all(e["agent"] == AGENT and all(isinstance(r, str) for r in (e["refs"] or [])) for e in bb)
    assert api.get("/api/v1/agent/blackboard", headers=H(s, "maker"),
                   params={"agent": "data-governance-classification"}).json() == []
    cls = {r["element"]: r for r in api.get("/api/v1/classification", headers=H(s, "maker")).json()}
    assert cls["ELG.MEMBER_ID"]["class"] == "identity" and cls["ELG.MEMBER_ID"]["masked"]
    rules = api.get("/api/v1/rules", headers=H(s, "maker")).json()["rules"]
    assert all(isinstance(r["classification"], list) for r in rules)
    assert any("identity" in r["classification"] for r in rules)


# -- §7 explanation binding on the unit decision ---------------------------------------------------------------------
def test_unit_decision_binds_the_explanation_shown_at_the_challenge(fresh_cp_dsn, tmp_path):
    s = Slice(fresh_cp_dsn)
    api = TestClient(create_app(s))
    _run(s, tmp_path, "B1", [person("P1", "M0001")], trailer=7)
    uk = s.units(state="HELD")[0]["unit_key"]
    card = api.get(f"/api/v1/units/{uk}", headers=H(s, "maker")).json()
    url = f"/api/v1/units/{uk}"
    forged = {"facts_hash": "c" * 64, "text_hash": card["explanation"]["text_hash"]}
    assert api.post(f"{url}/challenge", headers=H(s, "maker"), json={
        "decision": "RELEASE", "quarantine_row_seqs": [], "explanation_ref": forged}).status_code == 422
    ch = api.post(f"{url}/challenge", headers=H(s, "maker"), json={
        "decision": "RELEASE", "quarantine_row_seqs": [], "explanation_ref": ref(card["explanation"])}).json()
    assert ch["explanation_ref"] == ref(card["explanation"])
    body = {"decision": "RELEASE", "rationale": "Trailer count error confirmed by the IE vendor ticket.",
            "expected_state_version": card["state_version"], "bound_fingerprint": card["fingerprint"],
            "quarantine_row_seqs": [], "step_up_token": s.idp.token("maker", step_up=True, challenge=ch["challenge"])}
    assert api.post(f"{url}/decide", headers=H(s, "maker"),
                    json={**body, "explanation_ref": forged}).status_code == 422          # not the bound one
    raw = s.decisions.challenge(["AUTH_MEDICAID"], [uk], "RELEASE", {uk: []})            # never bound by the api
    assert api.post(f"{url}/decide", headers=H(s, "maker"), json={
        **body, "step_up_token": s.idp.token("maker", step_up=True, challenge=raw + "0")}).status_code == 422
    r = api.post(f"{url}/decide", headers=H(s, "maker"), json=body)
    assert r.status_code == 200 and r.json()["status"] == "AWAITING_CHECKER", r.text
