"""T1: control-plane database invariants against a real Postgres 16.

Every hostile case runs AS the service role (its own login), never as the bootstrap admin,
so the database, not Python, is what refuses it (SEC-W1-1/2/7).
"""
import uuid

import psycopg
import pytest

from dqagent.model.lifecycle import TRANSITIONS, VIA_ROLE, Via, wrapper_for
from dqagent.cp.schema import SERVICE_ROLES

ROLE_FN = {"dq_api": "transition_api", "dq_eval_svc": "transition_eval",
           "dq_seal_svc": "transition_seal", "dq_publish_svc": "transition_publish"}


def as_role(dsn, role):
    return psycopg.connect(**{**dsn, "user": role})


def T(c, u, v, to, via, actor="x"):
    return c.execute(f"SELECT dq_ctl.{wrapper_for(via)}(%s,%s,%s,%s,%s,'{{}}')", (u, v, to, via, actor)).fetchone()[0]


def _mk_unit(cp, dsn, auths=("AUTH_MEDICAID",), sealed=True):
    b = "b_" + uuid.uuid4().hex[:6]
    u = "u_" + uuid.uuid4().hex[:6]
    with as_role(dsn, "dq_seal_svc") as s:
        s.execute("INSERT INTO dq_ctl.batches(batch_id,pipeline_id,node) VALUES (%s,'p6','brz')", (b,))
        s.execute("INSERT INTO dq_ctl.units(unit_key,batch_id,pipeline_id,node,program_code,authorities,partition_key,state)"
                  " VALUES (%s,%s,'p6','brz','MEDICAID',%s,%s,'LANDED')", (u, b, list(auths), "chain-" + u))
        if sealed:
            T(s, u, 0, "SEALED", "SEAL")
            s.execute("UPDATE dq_ctl.units SET fingerprint='fp1', fp_epoch=1, max_row_seq=1 WHERE unit_key=%s", (u,))
    return u


def _to_held(dsn, u):
    with as_role(dsn, "dq_eval_svc") as e:
        T(e, u, 1, "EVALUATING", "ENGINE")
        T(e, u, 2, "HELD", "ENGINE")


def test_transition_happy_path_and_cas(cp, cp_dsn):
    u = _mk_unit(cp, cp_dsn)
    with as_role(cp_dsn, "dq_eval_svc") as e:
        assert T(e, u, 1, "EVALUATING", "ENGINE") == 2
        with pytest.raises(psycopg.errors.SerializationFailure):
            T(e, u, 1, "HELD", "ENGINE")


def test_illegal_transition_rejected(cp, cp_dsn):
    u = _mk_unit(cp, cp_dsn)
    with as_role(cp_dsn, "dq_publish_svc") as p, pytest.raises(psycopg.errors.CheckViolation):
        T(p, u, 1, "RELEASED", "PUBLISH")                          # SEALED -> RELEASED is not an edge


def test_no_human_edge_reaches_released():
    assert not [t for t in TRANSITIONS if t[1].value == "RELEASED" and t[2] is not Via.PUBLISH]
    assert not [t for t in TRANSITIONS if t[1].value == "PENDING_RELEASE" and t[2] is Via.ENGINE]


@pytest.mark.parametrize("role", list(ROLE_FN))
def test_every_role_refused_every_foreign_wrapper_and_core(cp, cp_dsn, role):
    """Hostile role sweep: a service can call only its own wrapper, never the core function."""
    u = _mk_unit(cp, cp_dsn)
    for other, fn in ROLE_FN.items():
        if other == role:
            continue
        with as_role(cp_dsn, role) as c, pytest.raises(psycopg.errors.InsufficientPrivilege):
            c.execute(f"SELECT dq_ctl.{fn}(%s,1,'EVALUATING','ENGINE','x','{{}}')", (u,))
    with as_role(cp_dsn, role) as c, pytest.raises(psycopg.errors.InsufficientPrivilege):
        c.execute("SELECT dq_ctl._transition(%s,1,'EVALUATING','ENGINE','x','{}')", (u,))


@pytest.mark.parametrize("via", [v.value for v in Via])
def test_own_wrapper_refuses_foreign_verbs(cp, cp_dsn, via):
    u = _mk_unit(cp, cp_dsn)
    for role, fn in ROLE_FN.items():
        if VIA_ROLE[Via(via)] == role:
            continue
        with as_role(cp_dsn, role) as c, pytest.raises(psycopg.errors.InsufficientPrivilege):
            c.execute(f"SELECT dq_ctl.{fn}(%s,1,'EVALUATING',%s,'x','{{}}')", (u, via))


def test_api_cannot_move_held_to_pending_release_without_its_decision(cp, cp_dsn):
    u = _mk_unit(cp, cp_dsn)
    _to_held(cp_dsn, u)
    with as_role(cp_dsn, "dq_api") as a:
        a.execute("SELECT dq_ctl.set_scope(ARRAY['AUTH_MEDICAID'])")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            T(a, u, 3, "PENDING_RELEASE", "DECIDE", "E100")


def test_units_born_landed_and_fingerprint_write_once(cp, cp_dsn):
    with as_role(cp_dsn, "dq_seal_svc") as s:
        s.execute("INSERT INTO dq_ctl.batches(batch_id,pipeline_id,node) VALUES ('bx','p','n')")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            s.execute("INSERT INTO dq_ctl.units(unit_key,batch_id,pipeline_id,node,program_code,authorities,"
                      "partition_key,state) VALUES ('ux','bx','p','n','MEDICAID','{AUTH_MEDICAID}','k','RELEASED')")
    u = _mk_unit(cp, cp_dsn)
    with as_role(cp_dsn, "dq_seal_svc") as s, pytest.raises(psycopg.errors.InsufficientPrivilege):
        s.execute("UPDATE dq_ctl.units SET fingerprint='forged' WHERE unit_key=%s", (u,))


def test_terminal_is_terminal(cp, cp_dsn):
    u = _mk_unit(cp, cp_dsn)
    _to_held(cp_dsn, u)
    with as_role(cp_dsn, "dq_api") as a:
        T(a, u, 3, "REJECTED", "REJECT", "p")
    with as_role(cp_dsn, "dq_eval_svc") as e, pytest.raises(psycopg.errors.CheckViolation):
        T(e, u, 4, "EVALUATING", "ENGINE")


def test_head_cannot_change_without_event(cp, cp_dsn):
    u = _mk_unit(cp, cp_dsn)
    with pytest.raises(psycopg.errors.RaiseException):
        cp.execute("UPDATE dq_ctl.units SET state='RELEASED', state_version=9 WHERE unit_key=%s", (u,))
    cp.rollback()


@pytest.mark.parametrize("stmt", [
    "UPDATE dq_ctl.audit_log SET event='x'",
    "DELETE FROM dq_ctl.unit_events",
    "TRUNCATE dq_ctl.decisions",
    "TRUNCATE dq_ctl.decisions CASCADE",
    "TRUNCATE dq_ctl.roster",
    "TRUNCATE dq_ctl.manifest_reservations",
])
def test_append_only_even_for_admin(cp, cp_dsn, stmt):
    _mk_unit(cp, cp_dsn)                                  # seed rows so row-level guards actually fire
    with pytest.raises((psycopg.errors.InsufficientPrivilege, psycopg.errors.FeatureNotSupported)):
        cp.execute(stmt)
    cp.rollback()


def test_bootstrap_login_is_not_a_member_of_owner(cp):
    r = cp.execute("SELECT pg_has_role(current_user, 'dq_owner', 'MEMBER') AND NOT "
                   "(SELECT rolsuper FROM pg_roles WHERE rolname = current_user)").fetchone()[0]
    assert r is False


def test_audit_chain_links(cp, cp_dsn):
    _mk_unit(cp, cp_dsn)
    rows = cp.execute("SELECT seq, prev_hash, row_hash FROM dq_ctl.audit_log ORDER BY seq").fetchall()
    for a, b in zip(rows, rows[1:]):
        assert b[1] == a[2]
    head = cp.execute("SELECT seq, head_hash FROM dq_ctl.audit_head").fetchone()
    assert head == (rows[-1][0], rows[-1][2])


def test_rls_unscoped_api_sees_nothing_and_scope_filters(cp, cp_dsn):
    u_med = _mk_unit(cp, cp_dsn, auths=("AUTH_MEDICAID",))
    u_snap = _mk_unit(cp, cp_dsn, auths=("AUTH_SNAP",))
    u_mix = _mk_unit(cp, cp_dsn, auths=("AUTH_MEDICAID", "AUTH_FTI"))
    with as_role(cp_dsn, "dq_api") as a:
        assert a.execute("SELECT count(*) FROM dq_ctl.units").fetchone()[0] == 0
        assert a.execute("SELECT count(*) FROM dq_ctl.unit_events").fetchone()[0] == 0
        a.execute("SELECT dq_ctl.set_scope(ARRAY['AUTH_MEDICAID'])")
        seen = {r[0] for r in a.execute("SELECT unit_key FROM dq_ctl.units").fetchall()}
        ev = {r[0] for r in a.execute("SELECT unit_key FROM dq_ctl.unit_events").fetchall()}
    assert u_med in seen and u_snap not in seen and u_mix not in seen   # mixed needs ALL authorities
    assert u_snap not in ev and u_med in ev


def test_api_cannot_write_heads_create_or_read_tokens_or_audit(cp, cp_dsn):
    u = _mk_unit(cp, cp_dsn)
    for stmt in ("UPDATE dq_ctl.units SET fingerprint='x' WHERE unit_key='" + u + "'",
                 "CREATE TABLE dq_ctl.evil(x int)", "SELECT * FROM dq_ctl.decision_tokens",
                 "SELECT * FROM dq_ctl.audit_log"):
        with as_role(cp_dsn, "dq_api") as a, pytest.raises(psycopg.errors.InsufficientPrivilege):
            a.execute(stmt)


def test_service_logins_cannot_reset_into_admin(cp_dsn):
    for role in SERVICE_ROLES:
        with as_role(cp_dsn, role) as c:
            c.execute("RESET ROLE")
            assert c.execute("SELECT current_user").fetchone()[0] == role


TOK_INS = ("INSERT INTO dq_ctl.decision_tokens(token_id,person_id,jws,jti,auth_time,kid,challenge,step_up) "
           "VALUES (%s,%s,'jws',%s,now(),'k1','c',true)")
DEC_INS = ("INSERT INTO dq_ctl.decisions(decision_id,unit_key,round,seat,person_id,authority,decision,rationale,"
           "token_id,bound_fingerprint,fp_epoch,qset_hash,disposition_vector) VALUES "
           "(%s,%s,%s,%s,%s,'AUTH_MEDICAID','RELEASE',%s,%s,'f',1,'q','{}')")


def test_token_jti_single_use_person_unique_token_per_unit_and_rationale_min(cp, cp_dsn):
    u = _mk_unit(cp, cp_dsn)
    cp.execute(TOK_INS, ("t1", "E1", "J1"))
    cp.execute(DEC_INS, ("d1", u, 1, "MAKER", "E1", "ABC balanced; release ok", "t1"))
    cp.commit()
    with pytest.raises(psycopg.errors.UniqueViolation):          # replayed token (same jti)
        cp.execute(TOK_INS, ("t2", "E2", "J1"))
    cp.rollback()
    with pytest.raises(psycopg.errors.UniqueViolation):          # same token reused on the same unit
        cp.execute(DEC_INS, ("d1b", u, 2, "MAKER", "E1", "reusing the same token again", "t1"))
    cp.rollback()
    cp.execute(TOK_INS, ("t3", "E1", "J3"))
    with pytest.raises(psycopg.errors.UniqueViolation):          # same person twice in a round
        cp.execute(DEC_INS, ("d3", u, 1, "CHECKER", "E1", "same person as maker here", "t3"))
    cp.rollback()
    cp.execute(TOK_INS, ("t4", "E3", "J4"))
    with pytest.raises(psycopg.errors.CheckViolation):           # rationale too short
        cp.execute(DEC_INS, ("d4", u, 2, "MAKER", "E3", "ok", "t4"))
    cp.rollback()


# -- Part F P1-1: the trace writer is not an engine role, and no engine role writes trace rows ---------------------
def test_trace_role_holds_no_transition_wrapper_and_no_engine_write(cp, cp_dsn):
    u = _mk_unit(cp, cp_dsn)
    for fn in (*ROLE_FN.values(), "_transition"):
        with as_role(cp_dsn, "dq_trace_svc") as c, pytest.raises(psycopg.errors.InsufficientPrivilege):
            c.execute(f"SELECT dq_ctl.{fn}(%s,1,'EVALUATING','ENGINE','x','{{}}')", (u,))
    for stmt in ("INSERT INTO dq_ctl.abc(batch_id,authority,hop,measure,disposition) VALUES ('b','A','h','m','PASS')",
                 "INSERT INTO dq_ctl.results(unit_key,eval_version,authority,rule_id,rule_hash,check_class,priority,"
                 "disposition) VALUES ('u',1,'A','r','h','c','p','PASS')",
                 "SELECT * FROM dq_ctl.decision_tokens", "SELECT * FROM dq_ctl.audit_log",
                 "SELECT dq_ctl.bb_post('b','AUTH_MEDICAID','NOTE','BATCH','b','RULE_NON_PASS','{}'::jsonb,NULL,NULL)"):
        with as_role(cp_dsn, "dq_trace_svc") as c, pytest.raises(psycopg.errors.InsufficientPrivilege):
            c.execute(stmt)


@pytest.mark.parametrize("role", [r for r in SERVICE_ROLES if r != "dq_trace_svc"])
def test_no_other_role_writes_any_trace_table(cp_dsn, role):
    from dqagent.cp.trace_schema import TRACE_TABLES
    for t in TRACE_TABLES:
        with as_role(cp_dsn, role) as c, pytest.raises(psycopg.errors.InsufficientPrivilege):
            c.execute(f"INSERT INTO dq_ctl.{t} DEFAULT VALUES")


def test_scoped_api_never_reads_engine_wide_trace_rows(cp, cp_dsn):
    """I1: an EMPTY authority set is not "inside every scope" for the trace tables (cardinality guard)."""
    with as_role(cp_dsn, "dq_trace_svc") as c:
        c.execute("INSERT INTO dq_ctl.runs(trace_id,kind,state,config_digest,code_digest,deployment,authorities) "
                  "VALUES ('NIGHT-ZZ-29990101-a1','NIGHT','ZZ','c','c','test','{}'), "
                  "('DEC-Dinv1','DECISION','ZZ','c','c','test','{AUTH_MEDICAID}')")
        c.commit()
    with as_role(cp_dsn, "dq_api") as a:
        a.execute("SELECT dq_ctl.set_scope(ARRAY['AUTH_MEDICAID'])")
        seen = {r[0] for r in a.execute("SELECT trace_id FROM dq_ctl.runs").fetchall()}
    assert "DEC-Dinv1" in seen and "NIGHT-ZZ-29990101-a1" not in seen
    with as_role(cp_dsn, "dq_auditor") as a:
        seen = {r[0] for r in a.execute("SELECT trace_id FROM dq_ctl.runs").fetchall()}
    assert {"DEC-Dinv1", "NIGHT-ZZ-29990101-a1"} <= seen
