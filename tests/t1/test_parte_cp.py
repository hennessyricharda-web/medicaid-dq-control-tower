"""Part E control plane (docs/rules/PARTE_DESIGN.md §1, §2, §3, §6, §7, §8): the database enforces two-person
promotion, lower-only demotion, entitlement / registry status on the agent's seats, one definition of a gating ABC
failure, feed items, append-only Part E tables and the agent namespace in the blackboard hash chain."""
import copy
import datetime as dt
import json

import psycopg
import pytest
from fastapi.testclient import TestClient

from dqagent.api.app import create_app

from .evidence import allow_promotions, attest, confirm, record_run
from .fixtures_w1 import ie_file, person
from .w1_harness import Slice

AGENT = "dq-reconciliation"
BODY = json.dumps({"rule_id": "R-ENC-004", "unit_count": 1})
REF = json.dumps({"facts_hash": "a" * 64, "text_hash": "b" * 64})
POST = "SELECT dq_ctl.bb_post('B','AUTH_MEDICAID','FINDING','RULE','R','RULE_NON_PASS',%s::jsonb,NULL,NULL) AS s"


def H(rt, user, **kw):
    return {"Authorization": f"Bearer {rt.idp.token(user, **kw)}"}


def _item(s, kind, suffix):
    return s.effective_item(kind, suffix)


def _approve(s, cr, *users):
    for u in users:
        tok = s.idp.token(u, step_up=True, challenge=s.registry.challenge(cr["cr_id"], "APPROVED"))
        s.registry.decide(cr["cr_id"], "APPROVED", tok, s.idp.users[u]["emp_id"], ["governance:AUTH_RULES"])
    return s.apply_change(cr["cr_id"])


def _elig(s, tmp, batch="B-ELG-1"):
    p = tmp / f"{batch}.txt"
    p.write_bytes(ie_file([person("P1", "M0001"), person("P2", "M0002")]))
    return s.run_pipeline("p4_eligibility_ie", batch, [("IE", p)], ["bronze.ie_segment"])


# -- §1.2 promotion is two-person, enforced by the database -------------------------------------------------------
def test_a_promotion_with_a_single_approver_never_applies(fresh_cp_dsn):
    s = Slice(fresh_cp_dsn)
    allow_promotions(s)                  # R4-5: evidence + zero minimums; the subject here is the two-person rule
    api = TestClient(create_app(s))
    assert s.registry.tier("ZZ", AGENT, "RELEASE", "AUTH_SNAP") == "L2"
    up = _item(s, "AUTONOMY", f"autonomy:{AGENT}:RELEASE")
    up["content"]["AUTH_SNAP"] = "L3"                                 # L2 -> L3: a promotion
    r = api.post("/api/v1/governance/change-requests", headers=H(s, "maker"),
                 json={"rationale": "Promote SNAP release to supervised", "items": [up]})
    assert r.status_code == 200 and r.json()["approvals_required"] == 2, r.text
    assert "track_record" in r.json()["impact"] and r.json()["impact"]["track_record"]["hash"]
    cr = r.json()["cr_id"]
    ch = api.post(f"/api/v1/governance/change-requests/{cr}/challenge", headers=H(s, "checker"),
                  json={"verdict": "APPROVED"}).json()["challenge"]
    own = api.post(f"/api/v1/governance/change-requests/{cr}/decide", headers=H(s, "maker"),
                   json={"verdict": "APPROVED", "step_up_token": s.idp.token("maker", step_up=True, challenge=ch)})
    assert own.status_code == 403                                      # the author can never be one of the two
    one = api.post(f"/api/v1/governance/change-requests/{cr}/decide", headers=H(s, "checker"),
                   json={"verdict": "APPROVED", "step_up_token": s.idp.token("checker", step_up=True, challenge=ch)})
    assert one.status_code == 200 and one.json()["status"] == "AWAITING_APPROVAL", one.text
    again = api.post(f"/api/v1/governance/change-requests/{cr}/decide", headers=H(s, "checker"),
                     json={"verdict": "APPROVED", "step_up_token": s.idp.token("checker", step_up=True, challenge=ch)})
    assert again.status_code == 409                                    # one person cannot supply the second approval
    assert s.registry.tier("ZZ", AGENT, "RELEASE", "AUTH_SNAP") == "L2"  # single approver: refused, tier unchanged
    two = api.post(f"/api/v1/governance/change-requests/{cr}/decide", headers=H(s, "checker2"),
                   json={"verdict": "APPROVED", "step_up_token": s.idp.token("checker2", step_up=True, challenge=ch)})
    assert two.json()["status"] == "APPLIED", two.text
    assert s.registry.tier("ZZ", AGENT, "RELEASE", "AUTH_SNAP") == "L3"


def test_the_callers_loosening_flag_cannot_lower_the_database_requirement(fresh_cp_dsn):
    """C4: a forged gov_propose that claims `not loosening` for a promotion still needs two approvers."""
    s = Slice(fresh_cp_dsn)
    up = _item(s, "AUTONOMY", f"autonomy:{AGENT}:RELEASE")
    up["content"]["AUTH_SNAP"] = "L4"
    with s.cp.as_role("dq_api") as c:
        c.execute("SELECT dq_ctl.gov_propose('CR-FORGED','ZZ','E100','HUMAN','forged promotion',%s::jsonb,"
                  "'{}'::jsonb,'{}'::jsonb,false,'h','{}'::jsonb,NULL)", (json.dumps([up]),))
        r = c.execute("SELECT loosening, approvals_required FROM dq_ctl.config_change_requests WHERE cr_id='CR-FORGED'"
                      ).fetchone()
    assert r["loosening"] and r["approvals_required"] == 2
    with s.cp.as_role("dq_api") as c:                                   # a DEMOTION origin cannot be proposed
        with pytest.raises(psycopg.errors.CheckViolation):
            c.execute("SELECT dq_ctl.gov_propose('CR-F2','ZZ','E100','DEMOTION','forged demotion',%s::jsonb,"
                      "'{}'::jsonb,'{}'::jsonb,false,'h','{}'::jsonb,NULL)", (json.dumps([up]),))
    bad = copy.deepcopy(up)
    bad["content"]["AUTH_SNAP"] = "L9"
    with s.cp.as_role("dq_api") as c:                                   # malformed tiers are refused by the database
        with pytest.raises(psycopg.errors.CheckViolation):
            c.execute("SELECT dq_ctl.gov_propose('CR-F3','ZZ','E100','HUMAN','malformed tier',%s::jsonb,"
                      "'{}'::jsonb,'{}'::jsonb,false,'h','{}'::jsonb,NULL)", (json.dumps([bad]),))


def test_a_brand_new_entitlement_or_tier_needs_two(fresh_cp_dsn):
    s = Slice(fresh_cp_dsn)
    now = dt.datetime.now(dt.timezone.utc)
    ent = {"artifact_id": "ZZ:entitlement:data-governance-classification:POST_BOARD", "kind": "ENTITLEMENT", "op": "PUT",
           "content": {"authorities": ["AUTH_MEDICAID"], "not_before": now.isoformat(),
                       "not_after": (now + dt.timedelta(days=1)).isoformat()}}
    cr = s.registry.propose("ZZ", "E100", "Entitle the classification agent to post", [ent])
    assert cr["loosening"] and cr["approvals_required"] == 2
    assert _approve(s, cr, "checker") == "AWAITING_APPROVAL"
    assert _approve(s, cr, "checker2") == "APPLIED"
    tier = {"artifact_id": "ZZ:autonomy:data-governance-classification:POST_BOARD", "kind": "AUTONOMY", "op": "PUT",
            "content": {"AUTH_MEDICAID": "L1"}}                          # even an L1 entry is a new grant
    assert s.registry.propose("ZZ", "E100", "Tier the classification agent", [tier])["approvals_required"] == 2


def test_a_lower_only_change_passes_with_one_approver(fresh_cp_dsn):
    s = Slice(fresh_cp_dsn)
    ent = _item(s, "ENTITLEMENT", f"entitlement:{AGENT}:PIN_MEMORY")
    nb = dt.datetime.fromisoformat(ent["content"]["not_after"]) - dt.timedelta(days=30)
    ent["content"]["not_after"] = nb.isoformat()                       # shorter
    ent["content"]["authorities"] = [a for a in ent["content"]["authorities"] if a != "AUTH_TANF"]   # fewer
    tier = _item(s, "AUTONOMY", f"autonomy:{AGENT}:RELEASE")
    tier["content"]["AUTH_MEDICAID"] = "L3"                            # lower
    cls = _item(s, "CLASSIFICATION", "classification")
    cls["content"]["CLM.TOTAL_CHARGE"] = "identity"                     # payment -> identity: MORE sensitive
    cr = s.registry.propose("ZZ", "E100", "Lower-only: shorten, narrow, demote, tighten", [ent, tier, cls])
    assert not cr["loosening"] and cr["approvals_required"] == 1, cr
    assert _approve(s, cr, "checker") == "APPLIED"
    assert s.registry.tier("ZZ", AGENT, "RELEASE", "AUTH_MEDICAID") == "L3"
    down = _item(s, "CLASSIFICATION", "classification")
    down["content"]["CLM.DX1"] = "none"                                 # a classification DOWNGRADE needs two
    assert s.registry.propose("ZZ", "E100", "Downgrade a clinical element", [down])["approvals_required"] == 2
    ext = _item(s, "ENTITLEMENT", f"entitlement:{AGENT}:PIN_MEMORY")
    ext["content"]["not_after"] = (nb + dt.timedelta(days=60)).isoformat()   # extending is a grant
    assert s.registry.propose("ZZ", "E100", "Extend the pin entitlement", [ext])["approvals_required"] == 2


# -- §1.2 demotion is automatic and lower-only ----------------------------------------------------------------------
def test_demotion_is_lower_only_and_only_the_registry_demotes(fresh_cp_dsn):
    s = Slice(fresh_cp_dsn)
    pending = _item(s, "AUTONOMY", f"autonomy:{AGENT}:POST_BOARD")
    pending["content"]["AUTH_TANF"] = "L3"
    cr = s.registry.propose("ZZ", "E100", "Lower TANF posting while the demotion happens", [pending])
    assert s.registry.demote(AGENT, "RELEASE", "AUTH_MEDICAID", "L2", "VETO", "unit:U-1") == 1
    assert s.registry.tier("ZZ", AGENT, "RELEASE", "AUTH_MEDICAID") == "L2"
    with s.cp.as_role("dq_registry_svc") as c:
        with pytest.raises(psycopg.errors.CheckViolation, match="lower-only"):       # raising via demote fails
            c.execute("SELECT dq_ctl.autonomy_demote(%s,'RELEASE','AUTH_MEDICAID','L3','VETO',NULL)", (AGENT,))
    with s.cp.as_role("dq_registry_svc") as c:
        with pytest.raises(psycopg.errors.CheckViolation, match="lower-only"):       # equal is not a demotion either
            c.execute("SELECT dq_ctl.autonomy_demote(%s,'RELEASE','AUTH_SNAP','L2','VETO',NULL)", (AGENT,))
    for role in ("dq_api", "dq_eval_svc", "dq_publish_svc"):
        with s.cp.as_role(role) as c:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                c.execute("SELECT dq_ctl.autonomy_demote(%s,'RELEASE','AUTH_SNAP','L1','VETO',NULL)", (AGENT,))
    with s.cp.as_role("dq_registry_svc") as c:
        dem = c.execute("SELECT r.origin, r.author, s.terminal FROM dq_ctl.config_change_requests r "
                        "JOIN dq_ctl.config_cr_status s USING (cr_id) WHERE r.origin='DEMOTION'").fetchall()
    assert [(d["origin"], d["terminal"]) for d in dem] == [("DEMOTION", "APPLIED")]
    with s.cp.as_role("dq_auditor") as c:
        b = c.execute("SELECT agent, seat, template, body, refs FROM dq_ctl.agent_blackboard "
                      "WHERE template='AUTONOMY_DEMOTED'").fetchone()
    assert b["seat"] == "REGISTRY" and b["agent"] == AGENT and b["body"]["from_tier"] == "L4" \
        and b["body"]["to_tier"] == "L2" and "unit:U-1" in b["refs"]
    assert _approve(s, cr, "checker") == "STALE"          # the demotion moved the artifact under the pending request


def test_a_veto_inside_the_window_holds_the_unit_and_demotes(fresh_cp_dsn, tmp_path):
    s = Slice(fresh_cp_dsn)
    _elig(s, tmp_path)                                                   # clean -> mode A, waits in EVALUATING
    u = next(x for x in s.units(state="EVALUATING") if "AUTH_MEDICAID" in x["authorities"])
    with s.cp.as_role("dq_eval_svc") as c:                               # the evaluator opens the window
        c.execute("INSERT INTO dq_ctl.veto_windows(unit_key,eval_version,opened_at,window_s,policy_hash) "
                  "VALUES (%s,1,'2000-01-01',600,'p')", (u["unit_key"],))
        w = c.execute("SELECT opened_at, remaining_s, elapsed FROM dq_ctl.veto_window_status WHERE unit_key=%s",
                      (u["unit_key"],)).fetchone()
    assert w["opened_at"].year > 2000 and not w["elapsed"] and w["remaining_s"] > 590   # the DATABASE clock opens it
    with s.cp.as_role("dq_api", list(u["authorities"])) as c:            # the plain wrapper cannot take the edge
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            c.execute("SELECT dq_ctl.transition_api(%s,%s,'HELD','VETO','E100','{}'::jsonb)",
                      (u["unit_key"], u["state_version"]))
    with s.cp.as_role("dq_api", list(u["authorities"])) as c:
        c.execute("SELECT dq_ctl.veto_unit(%s,%s,'E100','DATA_CONCERN','J-VETO-1',%s::jsonb)",
                  (u["unit_key"], u["state_version"], json.dumps({"facts_hash": "a" * 64, "text_hash": "b" * 64})))
    assert s.units(unit_key=u["unit_key"])[0]["state"] == "HELD"
    assert s.registry.tier("ZZ", AGENT, "RELEASE", "AUTH_MEDICAID") == "L2"
    with s.cp.as_role("dq_auditor") as c:
        v = c.execute("SELECT person_id, reason_code, explanation_ref FROM dq_ctl.vetoes").fetchone()
    assert v["person_id"] == "E100" and v["explanation_ref"]["facts_hash"] == "a" * 64
    s.verify_and_publish()
    rel = s.units(state="RELEASED")
    if rel:                                                              # a released unit is past veto (API: 409)
        with s.cp.as_role("dq_api", list(rel[0]["authorities"])) as c:
            with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState):
                c.execute("SELECT dq_ctl.veto_unit(%s,%s,'E100','DATA_CONCERN',NULL,NULL)",
                          (rel[0]["unit_key"], rel[0]["state_version"]))


def test_track_record_counts_releases_and_never_promotes(fresh_cp_dsn, tmp_path):
    """Part F R4-5 semantics (DEC-024): the record counts releases per code version and NEVER resets; an attestation
    is two-person EVIDENCE for a promotion, not a reset. (The Part E reset behaviour is evidenced by the Part E
    receipt up to 2026-10-23.)"""
    s = Slice(fresh_cp_dsn)
    for i in range(3):
        _elig(s, tmp_path, batch=f"B-ELG-{i}")
        s.verify_and_publish()
    with s.cp.as_role("dq_registry_svc") as c:
        tr = {r["authority"]: r for r in c.execute("SELECT * FROM dq_ctl.track_record WHERE agent_id=%s "
                                                   "AND action_class='RELEASE'", (AGENT,)).fetchall()}
    released = [u for u in s.units(state="RELEASED") if "AUTH_MEDICAID" in u["authorities"]]
    assert released and tr["AUTH_MEDICAID"]["autonomous"] == len(released)
    assert tr["AUTH_MEDICAID"]["current_version"]["autonomous"] == len(released)
    # negative control: clean releases never change a tier (no automatic promotion, ever)
    assert s.registry.tier("ZZ", AGENT, "RELEASE", "AUTH_SNAP") == "L2"
    assert s.registry.tier("ZZ", AGENT, "RELEASE", "AUTH_MEDICAID") == "L4"
    q = ("SELECT autonomous, prior_autonomous, eval_label FROM dq_ctl.track_record WHERE agent_id=%s "
         "AND action_class='RELEASE' AND authority='AUTH_MEDICAID'")
    att = attest(s, record_run(s), label="suite-1")                     # E4: ONE person only PROPOSES
    with s.cp.as_role("dq_registry_svc") as c:
        one = c.execute(q, (AGENT,)).fetchone()
    assert one["autonomous"] == len(released) and one["eval_label"] is None
    with s.cp.as_role("dq_api") as c:
        with pytest.raises(psycopg.errors.CheckViolation, match="second person"):    # nor confirm their own
            c.execute("SELECT dq_ctl.eval_attest_confirm(%s,'E100')", (att,))
    with s.cp.as_role("dq_api") as c:
        with pytest.raises(psycopg.errors.InsufficientPrivilege, match="governance"):  # nor a non-governance person
            c.execute("SELECT dq_ctl.eval_attest_confirm(%s,'E400')", (att,))
    with psycopg.connect(**fresh_cp_dsn) as c:                           # the table refuses it as well (any writer)
        with pytest.raises(psycopg.errors.CheckViolation, match="second person"):
            c.execute("INSERT INTO dq_ctl.eval_attestation_confirmations(attestation_id,person_id) VALUES (%s,'E100')",
                      (att,))
    confirm(s, att)                                                      # two people: EVIDENCE (I7 + E4)
    with s.cp.as_role("dq_registry_svc") as c:
        after = c.execute(q, (AGENT,)).fetchone()
        ev = c.execute("SELECT dq_ctl.evidence_status() e").fetchone()["e"]
    assert after["autonomous"] == len(released) and after["eval_label"] == "suite-1"   # R4-5: nothing resets
    assert after["prior_autonomous"] == 0 and ev["deterministic"] and ev["complete"]
    with s.cp.as_role("dq_api") as c:
        with pytest.raises(psycopg.errors.UniqueViolation):
            c.execute("SELECT dq_ctl.eval_attest_confirm(%s,'E210')", (att,))
    with s.cp.as_role("dq_eval_svc") as c:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):        # only a person through the api attests
            c.execute("SELECT dq_ctl.eval_attest('x',%s,%s,NULL,'E100')", ("c" * 64, "run-x"))


# -- §2 entitlements and registry status gate the agent's seats ------------------------------------------------------
def test_an_expired_entitlement_makes_bb_post_refuse(fresh_cp_dsn):
    s = Slice(fresh_cp_dsn)
    with s.cp.as_role("dq_triage_svc") as c:
        assert c.execute(POST, (BODY,)).fetchone()["s"]
    ent = _item(s, "ENTITLEMENT", f"entitlement:{AGENT}:POST_BOARD")
    nb = dt.datetime.fromisoformat(ent["content"]["not_before"])
    ent["content"]["not_after"] = (nb + dt.timedelta(seconds=1)).isoformat()        # lapsed (lower-only: 1 approver)
    cr = s.registry.propose("ZZ", "E100", "Let the posting entitlement lapse", [ent])
    assert cr["approvals_required"] == 1 and _approve(s, cr, "checker") == "APPLIED"
    with s.cp.as_role("dq_triage_svc") as c:
        with pytest.raises(psycopg.errors.InsufficientPrivilege, match="no live POST_BOARD"):
            c.execute(POST, (BODY,))
    with s.cp.as_role("dq_api", ["AUTH_MEDICAID"]) as c:                 # a PERSON's note is not the agent acting
        c.execute("SELECT dq_ctl.bb_post('B','AUTH_MEDICAID','NOTE','RULE','R','STEWARD_NOTE',%s::jsonb,NULL,'E100')",
                  (json.dumps({"rule_id": "R", "reason_code": "X"}),))
    with s.cp.as_role("dq_triage_svc") as c:                             # PIN_MEMORY is a separate entitlement
        c.execute("SELECT dq_ctl.mem_pin_baseline('MEASURE','M@N','AUTH_MEDICAID',%s::jsonb)",
                  (json.dumps({"measure_id": "M", "median": 0.1, "n_periods": 2}),))


def test_a_seat_of_a_non_active_agent_is_refused(fresh_cp_dsn):
    s = Slice(fresh_cp_dsn)
    with s.cp.as_role("dq_triage_svc") as c:                             # a service cannot write another namespace
        with pytest.raises(psycopg.errors.InsufficientPrivilege, match="namespace"):
            c.execute("SELECT dq_ctl.bb_post('B','AUTH_MEDICAID','FINDING','RULE','R','RULE_NON_PASS',%s::jsonb,"
                      "NULL,NULL,'data-governance-classification')", (BODY,))
    ag = _item(s, "AGENT", f"agent:{AGENT}")
    ag["content"]["status"] = "SUSPENDED"                                  # ACTIVE -> SUSPENDED: lower-only
    cr = s.registry.propose("ZZ", "E100", "Suspend the agent", [ag])
    assert cr["approvals_required"] == 1 and _approve(s, cr, "checker") == "APPLIED"
    with s.cp.as_role("dq_triage_svc") as c:
        with pytest.raises(psycopg.errors.InsufficientPrivilege, match="not ACTIVE"):
            c.execute(POST, (BODY,))
    with s.cp.as_role("dq_triage_svc") as c:
        with pytest.raises(psycopg.errors.InsufficientPrivilege, match="not ACTIVE"):
            c.execute("SELECT dq_ctl.mem_pin_baseline('MEASURE','M@N','AUTH_MEDICAID',%s::jsonb)",
                      (json.dumps({"measure_id": "M", "median": 0.1, "n_periods": 2}),))
    back = _item(s, "AGENT", f"agent:{AGENT}")
    back["content"]["status"] = "ACTIVE"                                    # re-activation is a grant: two people
    assert s.registry.propose("ZZ", "E100", "Re-activate the agent", [back])["approvals_required"] == 2


# -- §3 reconciliation ---------------------------------------------------------------------------------------------
def test_abc_gating_failures_excludes_non_gating_rows(fresh_cp_dsn):
    s = Slice(fresh_cp_dsn)
    with s.cp.as_role("dq_seal_svc") as c:
        for hop, gating, disp in (("FILE->RAW", False, "FAIL"), ("RAW->LANDING", True, "FAIL"),
                                  ("LANDING->GATED", True, "PASS"), ("LEGACY->NEW", False, "FAIL")):
            c.execute("INSERT INTO dq_ctl.abc(batch_id,authority,hop,measure,disposition,gating) "
                      "VALUES ('B-G','AUTH_INTAKE',%s,'m',%s,%s)", (hop, disp, gating))
        c.execute("INSERT INTO dq_ctl.abc(batch_id,authority,hop,measure,disposition) VALUES ('B-G','AUTH_INTAKE','X','m','FAIL')")
        got = sorted(r["hop"] for r in c.execute("SELECT hop FROM dq_ctl.abc_gating_failures WHERE batch_id='B-G'"))
    assert got == ["RAW->LANDING", "X"]                                     # gating defaults to true


def test_feed_item_open_and_acknowledge(fresh_cp_dsn):
    s = Slice(fresh_cp_dsn)
    with s.cp.as_role("dq_seal_svc") as c:
        i = c.execute("SELECT dq_ctl.feed_item_open('ZZ','IE_EXTRACT','EMPTY_DELIVERY','B-1','F-1','AUTH_MEDICAID',NULL) i"
                      ).fetchone()["i"]
        assert c.execute("SELECT dq_ctl.feed_item_open('ZZ','IE_EXTRACT','EMPTY_DELIVERY','B-1','F-1','AUTH_MEDICAID',"
                         "NULL) i").fetchone()["i"] == i                     # idempotent per (feed, kind, batch)
    with s.cp.as_role("dq_api", ["AUTH_MEDICAID"]) as c:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            c.execute("SELECT dq_ctl.feed_item_open('ZZ','IE_EXTRACT','EMPTY_DELIVERY','B-2',NULL,'AUTH_MEDICAID',NULL)")
    with s.cp.as_role("dq_api", ["AUTH_SNAP"]) as c:                       # out of scope
        with pytest.raises(psycopg.errors.NoDataFound):
            c.execute("SELECT dq_ctl.feed_item_ack(%s,'E100','HOLIDAY')", (i,))
    with s.cp.as_role("dq_api", ["AUTH_MEDICAID"]) as c:
        with pytest.raises(psycopg.errors.CheckViolation):
            c.execute("SELECT dq_ctl.feed_item_ack(%s,'E100','BECAUSE')", (i,))
    with s.cp.as_role("dq_api", ["AUTH_MEDICAID"]) as c:                   # E3: the explanation shown is mandatory
        with pytest.raises(psycopg.errors.CheckViolation, match="explanation"):
            c.execute("SELECT dq_ctl.feed_item_ack(%s,'E100','NO_ACTIVITY')", (i,))
    with s.cp.as_role("dq_api", ["AUTH_MEDICAID"]) as c:
        c.execute("SELECT dq_ctl.feed_item_ack(%s,'E100','NO_ACTIVITY',NULL,%s::jsonb)", (i, REF))
        st = c.execute("SELECT status, reason_code, acknowledged_by FROM dq_ctl.feed_items_status WHERE item_id=%s",
                       (i,)).fetchone()
    assert (st["status"], st["reason_code"], st["acknowledged_by"]) == ("ACKNOWLEDGED", "NO_ACTIVITY", "E100")
    with s.cp.as_role("dq_api", ["AUTH_MEDICAID"]) as c:
        with pytest.raises(psycopg.errors.UniqueViolation):
            c.execute("SELECT dq_ctl.feed_item_ack(%s,'E200','HOLIDAY',NULL,%s::jsonb)", (i, REF))


def test_parallel_run_explain_codes(fresh_cp_dsn):
    s = Slice(fresh_cp_dsn)
    with s.cp.as_role("dq_eval_svc") as c:
        ids = {st: c.execute("INSERT INTO dq_ctl.parallel_run_results(run_id,system,period,as_of,relation,measure,legacy,"
                             "new,delta,tolerance,status,authority) VALUES ('R1','LEGACY','2026-01','2026-01-31',"
                             "'bronze.encounter_header',%s,10,11,1,0,%s,'AUTH_MEDICAID') RETURNING row_id",
                             (m, st)).fetchone()["row_id"] for st, m in (("BREAK", "records"), ("MATCH", "amount:paid"))}
    with s.cp.as_role("dq_api", ["AUTH_MEDICAID"]) as c:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):          # LEGACY_DEFECT needs a step-up
            c.execute("SELECT dq_ctl.parallel_run_explain(%s,'E100','LEGACY_DEFECT',NULL,false)", (ids["BREAK"],))
    with s.cp.as_role("dq_api", ["AUTH_MEDICAID"]) as c:
        with pytest.raises(psycopg.errors.CheckViolation):                 # only a BREAK takes a code
            c.execute("SELECT dq_ctl.parallel_run_explain(%s,'E100','TIMING',NULL,false)", (ids["MATCH"],))
    with s.cp.as_role("dq_api", ["AUTH_MEDICAID"]) as c:                   # E3: the explanation shown is mandatory
        with pytest.raises(psycopg.errors.CheckViolation, match="explanation"):
            c.execute("SELECT dq_ctl.parallel_run_explain(%s,'E100','TIMING',NULL,false)", (ids["BREAK"],))
    with s.cp.as_role("dq_api", ["AUTH_MEDICAID"]) as c:
        c.execute("SELECT dq_ctl.parallel_run_explain(%s,'E100','TIMING',NULL,false,%s::jsonb)", (ids["BREAK"], REF))
        r = c.execute("SELECT explain_code, counts_for_go_no_go FROM dq_ctl.parallel_run_status WHERE row_id=%s",
                      (ids["BREAK"],)).fetchone()
    assert r["explain_code"] == "TIMING" and r["counts_for_go_no_go"]


# -- append-only on every new table ---------------------------------------------------------------------------------
NEW_TABLES = {"eval_attestations": "id", "veto_windows": "window_id", "vetoes": "veto_id", "feed_items": "item_id",
              "feed_item_events": "id", "parallel_run_results": "row_id", "parallel_run_explanations": "id",
              "monitor_metrics": "id"}


def test_every_new_part_e_table_is_append_only(fresh_cp_dsn, tmp_path):
    s = Slice(fresh_cp_dsn)
    _elig(s, tmp_path)
    u = s.units(state="EVALUATING")[0]
    attest(s, record_run(s), label="suite")                             # R4-5: an attestation names recorded runs
    with s.cp.as_role("dq_eval_svc") as c:
        c.execute("INSERT INTO dq_ctl.veto_windows(unit_key,eval_version,window_s,policy_hash) VALUES (%s,1,600,'p')",
                  (u["unit_key"],))
        rid = c.execute("INSERT INTO dq_ctl.parallel_run_results(run_id,system,period,as_of,relation,measure,status,"
                        "authority) VALUES ('R','L','P','A','r','records','BREAK','AUTH_MEDICAID') RETURNING row_id"
                        ).fetchone()["row_id"]
    with s.cp.as_role("dq_api", list(u["authorities"])) as c:
        c.execute("SELECT dq_ctl.veto_unit(%s,%s,'E100','DATA_CONCERN',NULL,NULL)", (u["unit_key"], u["state_version"]))
    with s.cp.as_role("dq_api", ["AUTH_MEDICAID"]) as c:
        c.execute("SELECT dq_ctl.parallel_run_explain(%s,'E100','TIMING',NULL,false,%s::jsonb)", (rid, REF))
    with s.cp.as_role("dq_seal_svc") as c:
        c.execute("SELECT dq_ctl.feed_item_open('ZZ','IE_EXTRACT','EMPTY_DELIVERY','B','F','AUTH_MEDICAID',NULL)")
    with s.cp.as_role("dq_monitor_svc") as c:
        c.execute("INSERT INTO dq_ctl.monitor_metrics(batch_id,period,relation,metric,key,value,n,authority) "
                  "VALUES ('B','P1','bronze.ie_segment','rows','',2,2,'AUTH_MEDICAID')")
    writer = {"veto_windows": "dq_eval_svc", "parallel_run_results": "dq_eval_svc", "monitor_metrics": "dq_monitor_svc"}
    with psycopg.connect(**fresh_cp_dsn) as adm:
        for t, pk in NEW_TABLES.items():
            assert adm.execute(f"SELECT count(*) FROM dq_ctl.{t}").fetchone()[0] >= 1, t
            for stmt in (f"UPDATE dq_ctl.{t} SET {pk} = {pk}", f"DELETE FROM dq_ctl.{t}", f"TRUNCATE dq_ctl.{t} CASCADE"):
                with pytest.raises(psycopg.errors.InsufficientPrivilege, match="append-only"):
                    adm.execute(stmt)                          # even the database owner's session is refused
                adm.rollback()
    for t, role in writer.items():                             # and the writing service holds no UPDATE / DELETE
        with s.cp.as_role(role) as c:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                c.execute(f"DELETE FROM dq_ctl.{t}")


# -- §8 B1: the agent namespace is inside the hash chain ------------------------------------------------------------
def test_bb_verify_detects_a_tamper_of_the_agent_field(fresh_cp_dsn):
    s = Slice(fresh_cp_dsn)
    with s.cp.as_role("dq_triage_svc") as c:
        seq = c.execute("SELECT dq_ctl.bb_post('B','AUTH_MEDICAID','FINDING','RULE','R','RULE_NON_PASS',%s::jsonb,"
                        "ARRAY['bb:1','mem:2','rule:R-1@ELIG_SEGMENT','cr:CR-1'],NULL) s", (BODY,)).fetchone()["s"]
        row = c.execute("SELECT agent, seat, refs FROM dq_ctl.agent_blackboard WHERE seq=%s", (seq,)).fetchone()
    assert (row["agent"], row["seat"]) == (AGENT, "TRIAGE") and row["refs"][0] == "bb:1"
    with s.cp.as_role("dq_triage_svc") as c:                             # untyped refs are refused
        with pytest.raises(psycopg.errors.CheckViolation):
            c.execute("SELECT dq_ctl.bb_post('B','AUTH_MEDICAID','FINDING','RULE','R','RULE_NON_PASS',%s::jsonb,"
                      "ARRAY['12'],NULL)", (BODY,))
    with s.cp.as_role("dq_auditor") as c:
        assert c.execute("SELECT dq_ctl.bb_verify() AS b").fetchone()["b"] is None
    with psycopg.connect(**fresh_cp_dsn) as adm:                          # break-glass tamper of the AGENT only
        adm.execute("ALTER TABLE dq_ctl.agent_blackboard DISABLE TRIGGER agent_blackboard_no_mut")
        adm.execute("UPDATE dq_ctl.agent_blackboard SET agent='data-governance-classification' WHERE seq=%s", (seq,))
        adm.execute("ALTER TABLE dq_ctl.agent_blackboard ENABLE TRIGGER agent_blackboard_no_mut")
    with s.cp.as_role("dq_auditor") as c:
        assert c.execute("SELECT dq_ctl.bb_verify() AS b").fetchone()["b"] == seq


# -- §6 control-chart baselines are frozen once pinned; §7 explanations are bound to decisions ---------------------
def test_spc_baseline_is_frozen_until_a_monitor_change_request(fresh_cp_dsn):
    s = Slice(fresh_cp_dsn)
    pin = "SELECT dq_ctl.mem_pin_baseline('METRIC','bronze.ie_segment|rows|','AUTH_MEDICAID',%s::jsonb,'SPC_BASELINE') m"
    b1 = json.dumps({"relation": "bronze.ie_segment", "metric": "rows", "cl": 100, "ucl": 130, "lcl": 70, "n_periods": 8})
    b2 = json.dumps({"relation": "bronze.ie_segment", "metric": "rows", "cl": 90, "ucl": 120, "lcl": 60, "n_periods": 9})
    with s.cp.as_role("dq_triage_svc") as c:                              # only the MONITOR seat pins SPC baselines
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            c.execute(pin, (b1,))
    with s.cp.as_role("dq_monitor_svc") as c:
        m1 = c.execute(pin, (b1,)).fetchone()["m"]
        assert c.execute(pin, (b1,)).fetchone()["m"] == m1
    with s.cp.as_role("dq_monitor_svc") as c:
        with pytest.raises(psycopg.errors.CheckViolation, match="frozen"):
            c.execute(pin, (b2,))
    mon = _item(s, "MONITOR", "monitor")
    mon["content"]["rebaseline"] = ["bronze.ie_segment|rows|"]
    cr = s.registry.propose("ZZ", "E100", "Re-baseline eligibility row counts", [mon])
    assert _approve(s, cr, "checker") == "APPLIED"
    with s.cp.as_role("dq_monitor_svc") as c:
        assert c.execute(pin, (b2,)).fetchone()["m"] != m1


def test_a_change_request_decision_binds_the_explanation_shown(fresh_cp_dsn):
    from dqagent.hitl.decisions import Denied
    s = Slice(fresh_cp_dsn)
    tier = _item(s, "AUTONOMY", f"autonomy:{AGENT}:PIN_MEMORY")
    tier["content"]["AUTH_TANF"] = "L2"
    cr = s.registry.propose("ZZ", "E100", "Lower TANF memory pinning", [tier])
    ex = {"facts_hash": "1" * 64, "text_hash": "2" * 64}
    tok = s.idp.token("checker", step_up=True, challenge=s.registry.challenge(cr["cr_id"], "APPROVED"))
    with pytest.raises(Denied, match="challenge mismatch"):              # a step-up without the explanation does not count
        s.registry.decide(cr["cr_id"], "APPROVED", tok, "E200", ["governance:AUTH_RULES"], explanation_ref=ex)
    tok = s.idp.token("checker", step_up=True, challenge=s.registry.challenge(cr["cr_id"], "APPROVED", ex))
    s.registry.decide(cr["cr_id"], "APPROVED", tok, "E200", ["governance:AUTH_RULES"], explanation_ref=ex)
    with s.cp.as_role("dq_registry_svc") as c:
        e = c.execute("SELECT explanation_ref FROM dq_ctl.config_cr_events WHERE cr_id=%s AND event='APPROVED'",
                      (cr["cr_id"],)).fetchone()
    assert e["explanation_ref"] == ex
    with s.cp.as_role("dq_api") as c:                                     # malformed refs are refused by the database
        with pytest.raises(psycopg.errors.CheckViolation):
            c.execute("SELECT dq_ctl.gov_decide(%s,'E210','J-X','APPROVED','{\"facts_hash\":\"x\"}'::jsonb)", (cr["cr_id"],))
