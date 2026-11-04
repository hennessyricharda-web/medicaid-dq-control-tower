"""A SQLite-backed test shim for CP-adjacent logic, for use on a machine with no Postgres.

NOT auto-loaded. This file is deliberately NOT named `conftest.py`, so pytest's default
collection never imports it and no test gets this fixture by accident. A test opts in by
importing a fixture from this module explicitly, e.g.:

    from tests.t1.conftest_sqlite_shim import sqlite_cp_dsn

    def test_something(sqlite_cp_dsn):
        ...

Why this exists, and how little it can prove (docs/blackboard.md, 2026-10-24 ORCHESTRATOR entry
"user-directed scope -- K0 through K6 straight through, plus a SQLite CP-test shim"): the CP's
Postgres-specific mechanisms (SECURITY DEFINER wrappers + role-bound GRANT/REVOKE, real row-lock
CAS raising psycopg.errors.SerializationFailure, RLS, append-only triggers) ARE the business
logic under test in nearly every CP-touching test, not a thin layer over it. There is today no
meaningful RUNNABLE set beyond tests with zero DB dependency, which already run without any
fixture at all. This module is accepted, narrow infrastructure: it proves the harness works, and
gives a (currently tiny) placeholder for any future test whose logic turns out to be separable
from the CP's enforcement mechanisms. It is PERMANENTLY out of scope for
`tests/t1/test_w1_outcomes.py` (the pinned 420/291/47 invariant) and
`tests/t1/test_trace_fault_injection.py` (tracing-never-changes-an-outcome): neither may ever be
made to import this fixture.

The `cp_dsn` / `fresh_cp_dsn` / `cp` fixtures in `tests/t1/conftest.py` are untouched by this file
and keep skipping honestly when Postgres is down -- this module never becomes a silent fallback
for them.
"""
from __future__ import annotations

import pathlib
import sqlite3
import sys

import pytest

from tests.t1.conftest import PG, cp_dsn  # noqa: F401 -- cp_dsn re-exported so tests can request it

# Deliberately minimal. NOT a DDL translation of dqagent/cp/schema.py -- the real schema's
# GRANT/REVOKE, RLS, triggers and SECURITY DEFINER wrappers are exactly the thing a hand-mirrored
# copy would either silently drop (testing nothing) or silently re-implement a second time (risking
# drift from the real logic, which is the single most dangerous failure shape here). Every
# (table, column) named below is checked, whenever Postgres is reachable, to be a real subset of
# dq_ctl's actual Postgres columns (test_sqlite_shim_columns_are_a_subset_of_postgres below). This
# table exists only to prove the fixture -> schema -> connection harness works end to end; it
# carries none of dq_ctl.batches' real behavior.
SQLITE_SHIM_SCHEMA: dict[str, tuple[str, ...]] = {
    "batches": ("batch_id", "pipeline_id", "node"),
}

_SHIM_RAN: list[str] = []


def _apply_sqlite_shim_schema(conn: sqlite3.Connection) -> None:
    for table, cols in SQLITE_SHIM_SCHEMA.items():
        col_sql = ", ".join(f"{c} TEXT NOT NULL" for c in cols)
        conn.execute(f"CREATE TABLE IF NOT EXISTS {table} ({col_sql})")
    conn.commit()


def _register_banner_once(config) -> None:
    pm = config.pluginmanager
    if not pm.has_plugin("dq_sqlite_cp_shim_banner"):
        pm.register(sys.modules[__name__], "dq_sqlite_cp_shim_banner")


@pytest.fixture
def sqlite_cp_dsn(request, tmp_path) -> pathlib.Path:
    """A SQLite file standing in for a CP-ish database, for a test that opts in explicitly.

    Returns a pathlib.Path, NOT a dict, on purpose: dqagent.engine.cp.CP.connect_as expects a
    dict-shaped psycopg DSN (host/port/user/dbname/...). A Path can never satisfy that call
    shape, so an accidental attempt to feed this fixture into Slice/Runtime/CP fails immediately
    and loudly (TypeError / AttributeError at the call site) instead of silently connecting to
    garbage or a stray local Postgres.
    """
    request.node.add_marker(pytest.mark.sqlite_shim)
    _register_banner_once(request.config)
    _SHIM_RAN.append(request.node.nodeid)
    db_path = tmp_path / "cp_shim.sqlite3"
    conn = sqlite3.connect(db_path)
    try:
        _apply_sqlite_shim_schema(conn)
    finally:
        conn.close()
    return db_path


def pytest_terminal_summary(terminalreporter) -> None:
    """Fires only in a session where some test actually used sqlite_cp_dsn (self-registered by
    that fixture, never wired in ahead of time) -- a normal session that never imports this
    module never sees this banner."""
    n = len(_SHIM_RAN)
    if n == 0:
        return
    terminalreporter.write_line("")
    terminalreporter.write_sep("=", "SQLite CP-test shim")
    terminalreporter.write_line(f"{n} test(s) marked 'sqlite_shim' ran this session:")
    for nodeid in _SHIM_RAN:
        terminalreporter.write_line(f"  - {nodeid}")
    terminalreporter.write_line("These passes prove NOTHING about Postgres-specific guarantees:")
    terminalreporter.write_line("no roles, no GRANT/REVOKE, no RLS, no real row-lock CAS. See")
    terminalreporter.write_line("tests/t1/conftest_sqlite_shim.py and the 2026-10-24 blackboard")
    terminalreporter.write_line("entry for the scoping rationale.")
    terminalreporter.write_sep("=")


def _postgres_up() -> bool:
    try:
        import psycopg
    except Exception:
        return False
    try:
        c = psycopg.connect(dbname="postgres", autocommit=True, **PG)
    except Exception:
        return False
    c.close()
    return True


@pytest.mark.skipif(
    not _postgres_up(),
    reason="T1 Postgres not reachable: the SQLite-shim schema drift guard needs a real schema to "
           "compare against, and must never silently pass without one.",
)
def test_sqlite_shim_columns_are_a_subset_of_postgres(cp_dsn):
    """Safeguard against SQLite/Postgres schema drift (the named risk in the design decision):
    every (table, column) the hand-maintained SQLite shim schema declares must also appear in
    `information_schema.columns` of a freshly schema.apply()'d real Postgres database. Reuses the
    cp_dsn fixture from tests/t1/conftest.py (same PG config as the skipif probe above), which
    already pytest.skip()s when Postgres is unreachable -- a skip is reported as skipped, never
    as a pass, so this can never be read as a false green."""
    import psycopg

    with psycopg.connect(**cp_dsn) as c:
        rows = c.execute(
            "SELECT table_name, column_name FROM information_schema.columns WHERE table_schema = %s",
            ("dq_ctl",),
        ).fetchall()
    real_columns = {(r[0], r[1]) for r in rows}

    missing = [(table, col) for table, cols in SQLITE_SHIM_SCHEMA.items() for col in cols
               if (table, col) not in real_columns]
    assert not missing, (
        f"SQLite shim schema has drifted from the real Postgres dq_ctl schema: {missing} not "
        f"found in information_schema.columns. Fix SQLITE_SHIM_SCHEMA (or the Postgres column), "
        f"never let the two diverge silently."
    )


def test_sqlite_cp_dsn_fixture_returns_a_path_not_a_dict(sqlite_cp_dsn):
    """Smoke test for the fixture itself (collected normally: this function matches pytest's
    test_* naming even though the containing file does not). Proves the harness end to end:
    the fixture returns a real Path to a SQLite file with the minimal schema already applied,
    and ordinary insert/select against it works."""
    assert isinstance(sqlite_cp_dsn, pathlib.Path)
    assert not isinstance(sqlite_cp_dsn, dict)
    assert sqlite_cp_dsn.exists()

    conn = sqlite3.connect(sqlite_cp_dsn)
    try:
        conn.execute("INSERT INTO batches (batch_id, pipeline_id, node) VALUES ('b1', 'p1', 'brz')")
        conn.commit()
        row = conn.execute(
            "SELECT batch_id, pipeline_id, node FROM batches WHERE batch_id = 'b1'"
        ).fetchone()
    finally:
        conn.close()
    assert row == ("b1", "p1", "brz")
