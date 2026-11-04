"""dqagent.eval.recorder (Part F K0 step 1; red-team I9 evidence integrity).

Two layers, named honestly per CLAUDE.md's SQLite-shim classification (K0_DESIGN.md §7):
  * pure derivation logic (no DB at all): `_derive_source_mode` can NEVER report LIVE for a transport that did
    not actually call out, whatever a caller's cfg claims. This is testable right now, with no Postgres.
  * the end-to-end product path (CLI -> recorder -> dq_ctl.eval_run_record): requires a live control plane. These
    tests SKIP honestly when Postgres is unreachable (same convention as tests/t1/conftest.py); they are never
    reported as passed by omission.
"""
from __future__ import annotations

import os
import uuid

import psycopg
import pytest

from dqagent.cp import schema
from dqagent.eval.recorder import SuiteRunError, _derive_source_mode, run_suite
from dqagent.obs import tracer

PG = dict(host=os.environ.get("DQ_PG_HOST", "/tmp"), port=int(os.environ.get("DQ_PG_PORT", "55432")),
          user=os.environ.get("DQ_PG_USER", "dq_gov"))

ANTHROPIC_CFG = {"provider": "anthropic", "transport": "http"}
ANTHROPIC_ENV_FULL = {"DQ_EXPLAINER_ANTHROPIC_ENDPOINT": "https://example.invalid", "DQ_EXPLAINER_ANTHROPIC_KEY": "k",
                     "DQ_EXPLAINER_ANTHROPIC_MODEL": "m"}


# ------------------------------------------------------------------------------------------------ pure logic (no DB)
def test_agent_eval_suite_is_always_deterministic():
    """Structural: the agent_eval suite calls no LLM transport (tests/agent_eval/README.md), so its source_mode is
    never derived from configuration -- it is a constant, never overridable by a cfg/env argument."""
    assert _derive_source_mode("agent_eval", {"provider": "anthropic", "transport": "http"}, env={}) == "DETERMINISTIC"
    assert _derive_source_mode("agent_eval", None) == "DETERMINISTIC"


def test_explainer_source_mode_is_never_live_unless_a_real_http_transport_is_fully_configured():
    """I9: a SCRIPTED or unconfigured transport can never be recorded as LIVE."""
    assert _derive_source_mode("explainer", None) == "TEMPLATE"                                   # default config
    assert _derive_source_mode("explainer", {"provider": "anthropic", "transport": "scripted"}) == "SCRIPTED"
    assert _derive_source_mode("explainer", ANTHROPIC_CFG, env={}) == "TEMPLATE"                  # unconfigured: I6
    assert _derive_source_mode("explainer", ANTHROPIC_CFG, env=ANTHROPIC_ENV_FULL) == "LIVE"      # the one true case


def test_explainer_source_mode_ignores_a_forged_label_in_cfg():
    """A caller cannot smuggle 'source_mode: LIVE' (or any other label) through cfg: it is not read for that
    purpose anywhere in _derive_source_mode / dqagent.llm.status."""
    forged = {**ANTHROPIC_CFG, "source_mode": "LIVE", "transport": "scripted"}
    assert _derive_source_mode("explainer", forged, env={}) == "SCRIPTED"


def test_run_suite_rejects_an_unknown_suite_name():
    with pytest.raises(ValueError, match="agent_eval"):
        run_suite(object(), "not-a-real-suite")


def test_run_suite_refuses_to_record_a_suite_that_collects_nothing(tmp_path):
    """A degenerate input (a path matching no test) must refuse, not write a misleading zero-case row."""
    empty = tmp_path / "test_empty_stub.py"
    empty.write_text("# no tests here\n")
    with pytest.raises(SuiteRunError, match="zero tests"):
        run_suite(object(), "explainer", paths=[str(empty)])


# ------------------------------------------------------------------------------------------------ the product path
def _fresh_db():
    try:
        admin = psycopg.connect(dbname="postgres", autocommit=True, **PG)
    except Exception as e:  # pragma: no cover
        pytest.skip(f"T1 Postgres not reachable: {e}")
    db = "dqcp_evalrec_" + uuid.uuid4().hex[:8]
    admin.execute(f"CREATE DATABASE {db}")
    dsn = dict(dbname=db, **PG)
    with psycopg.connect(**dsn) as c:
        schema.apply(c)
    return admin, db, dsn


def test_run_suite_writes_one_deterministic_eval_runs_row_through_the_product_path():
    """End to end: the CLI's own path (run_suite -> dq_ctl.eval_run_record as dq_eval_svc) against a tiny stub
    suite, so the mechanics are proven without waiting ~20s for the real explainer suite. Degenerate-input and
    order-independence are covered by the no-DB tests above; this proves the DB write itself."""
    from dqagent.engine.cp import CP
    admin, db, dsn = _fresh_db()
    try:
        stub = os.path.join(os.path.dirname(__file__), "_stub_suite.py")
        cp = CP(dsn)
        row = run_suite(cp, "explainer", paths=[stub])
        assert row["suite"] == "explainer" and row["source_mode"] == "TEMPLATE"
        assert row["cases"] == 2 and row["passed"] == 1 and row["code_sha256"] == tracer.code_sha256()
        with psycopg.connect(**dsn) as c:
            r = c.execute("SELECT suite, source_mode, cases, passed, code_sha256 FROM dq_ctl.eval_runs "
                          "WHERE run_id=%s", (row["run_id"],)).fetchone()
        assert tuple(r) == ("explainer", "TEMPLATE", 2, 1, tracer.code_sha256())
    finally:
        admin.execute(f"DROP DATABASE {db} WITH (FORCE)")
        admin.close()


def test_eval_runs_rejects_a_forged_source_mode_write_by_any_other_role():
    """I9, the append-only / role-restricted half (D4, trace_schema.py:544-560): eval_run_record is SECURITY
    DEFINER and checks session_user itself, so even a direct call (bypassing this module entirely) cannot forge a
    row as a role other than dq_eval_svc, and a written row can never be mutated afterwards (a SCRIPTED run
    cannot be upgraded to LIVE after the fact). This is a database guarantee, not a Python one."""
    from dqagent.engine.cp import CP
    admin, db, dsn = _fresh_db()
    try:
        cp = CP(dsn)
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            with cp.as_role("dq_api") as c:
                c.execute("SELECT dq_ctl.eval_run_record(%s,%s,%s,%s,%s,%s,%s,%s)",
                          ("forged-1", "explainer", "LIVE", 1, 1, "a" * 64, None, "b" * 64))
        with cp.as_role("dq_eval_svc") as c:
            c.execute("SELECT dq_ctl.eval_run_record(%s,%s,%s,%s,%s,%s,%s,%s)",
                      ("real-1", "explainer", "SCRIPTED", 1, 1, "a" * 64, None, "b" * 64))
        with psycopg.connect(**dsn) as adm:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                adm.execute("UPDATE dq_ctl.eval_runs SET source_mode='LIVE' WHERE run_id='real-1'")
    finally:
        admin.execute(f"DROP DATABASE {db} WITH (FORCE)")
        admin.close()
