"""Part F P1-1 A: the trace schema applies on a fresh and an already-applied control plane (C3: helpers before the tables
that use them), and its CHECK helpers admit counts and short identifiers only."""
import json
import uuid

import psycopg
import pytest

from dqagent.cp import schema
from dqagent.cp.trace_schema import CORRELATED, TRACE_TABLES

from .conftest import PG


def test_trace_ddl_applies_on_fresh_and_reapplied_schema():
    admin = psycopg.connect(dbname="postgres", autocommit=True, **PG)
    db = "dqtr_" + uuid.uuid4().hex[:8]
    admin.execute(f"CREATE DATABASE {db}")
    try:
        for _ in range(2):                                        # fresh, then re-applied over itself
            with psycopg.connect(dbname=db, **PG) as c:
                schema.apply(c)
        with psycopg.connect(dbname=db, **PG) as c:
            have = {r[0] for r in c.execute("SELECT table_name FROM information_schema.tables "
                                            "WHERE table_schema='dq_ctl'").fetchall()}
            assert set(TRACE_TABLES) <= have
            for t in CORRELATED:
                cols = {r[0] for r in c.execute("SELECT column_name FROM information_schema.columns WHERE "
                                                "table_schema='dq_ctl' AND table_name=%s", (t,)).fetchall()}
                assert {"trace_id", "span_id", "code_sha256"} <= cols, t
            forced = dict(c.execute("SELECT relname, relforcerowsecurity FROM pg_class WHERE relname = ANY(%s)",
                                    (list(TRACE_TABLES),)).fetchall())
            assert all(forced[t] for t in TRACE_TABLES)
            # the view over veto_windows was re-created with the new columns and still answers
            c.execute("SELECT trace_id, deadline, vetoed FROM dq_ctl.veto_window_status LIMIT 0")
    finally:
        admin.execute(f"DROP DATABASE {db} WITH (FORCE)")
        admin.close()


@pytest.mark.parametrize("attrs,ok", [
    ({"units": 3, "ok": True, "ingest_state": "SEALED", "summary": {"held": 2}}, True),
    ({"note": "free text with spaces"}, False),
    ({"rule": "R-ELG-001"}, False),                                # an identifier: not in any vocabulary (I5)
    ({"ingest_state": "a.person@example.org"}, False),            # e-mail shaped
    ({"verdict": "M0001"}, False),                                 # a member-id shaped value for a known key
    ({"members": ["M0001"]}, False),
    ({"Upper": 1}, False),
    ({"nested": {"held": "two"}}, False),
    ({"name": "John Smith"}, False),
])
def test_span_attrs_ok_rejects_free_text_and_arrays(cp, attrs, ok):
    assert cp.execute("SELECT dq_ctl.span_attrs_ok(%s::jsonb)", (json.dumps(attrs),)).fetchone()[0] is ok
    cp.rollback()


@pytest.mark.parametrize("detail,ok", [
    ({"held": 2, "released": 0}, True),
    ({"held": -1}, False),
    ({"held": "2"}, False),
    ({"held": 1.5}, False),
    ([1, 2], False),
])
def test_counts_only_rejects_negative_and_strings(cp, detail, ok):
    assert cp.execute("SELECT dq_ctl.counts_only(%s::jsonb)", (json.dumps(detail),)).fetchone()[0] is ok
    cp.rollback()


def test_llm_calls_accepts_fixed_transport_and_board_refs_accept_element(cp):
    """The CHECK knows every transport of the deploy allow-list (fixed is gated there: test deployment only); the
    blackboard's refs_ok accepts element: citations (CDE)."""
    from dqagent.deploy import TRANSPORTS
    from dqagent.cp.trace_schema import LLM_TRANSPORTS
    assert set().union(*TRANSPORTS.values()) == set(LLM_TRANSPORTS)
    cp.execute("INSERT INTO dq_ctl.llm_calls(trace_id,span_id,subject,authorities,kind,provider,transport,source_mode,"
               "prompt_version,check_version,template_version,facts_hash,verdict) VALUES "
               "('EXP-x','0000000000000000','unit:u','{AUTH_MEDICAID}','unit_card','anthropic','fixed','RECORDED',"
               "'p','c','t','f','PASS')")
    with pytest.raises(psycopg.errors.CheckViolation):
        cp.execute("INSERT INTO dq_ctl.llm_calls(trace_id,span_id,subject,authorities,kind,provider,transport,"
                   "source_mode,prompt_version,check_version,template_version,facts_hash,verdict) VALUES "
                   "('EXP-x','0000000000000000','unit:u','{AUTH_MEDICAID}','unit_card','anthropic','smtp','LIVE',"
                   "'p','c','t','f','PASS')")
    cp.rollback()
    assert cp.execute("SELECT dq_ctl.refs_ok(ARRAY['element:ELG.DOB','trace:NIGHT-ZZ-20260101-a1'])").fetchone()[0]
    assert not cp.execute("SELECT dq_ctl.refs_ok(ARRAY['element:has space'])").fetchone()[0]
    cp.rollback()


def test_release_check_reasons_compile_in_postgres_at_the_maximum_length(cp):
    """The reasons CHECK is evaluated by Postgres (whose regex bounds stop at 255): a reason of exactly REASON_MAX
    characters is stored, one longer or without a clause prefix is refused."""
    from dqagent.cp.trace_schema import REASON_MAX
    head = "4: "
    ok = head + "x" * (REASON_MAX - len(head))
    cp.execute("INSERT INTO dq_ctl.release_checks(span_id,unit_key,ok,reasons,authorities) "
               "VALUES ('s','u',false,%s,'{}')", ([ok, "stale: skipped this cycle", "12: batch ABC incomplete"],))
    for bad in (ok + "x", "free text without a clause", "4: line\nbreak"):
        cp.execute("SAVEPOINT b")
        with pytest.raises(psycopg.errors.CheckViolation):
            cp.execute("INSERT INTO dq_ctl.release_checks(span_id,unit_key,ok,reasons,authorities) "
                       "VALUES ('s','u',false,%s,'{}')", ([bad],))
        cp.execute("ROLLBACK TO SAVEPOINT b")
    cp.rollback()
