"""E0 (Part E): pins the unit outcomes on synthetic run w1.

Before Part E, the 420 released / 291 held / 47 pending-dependency count was checked by hand when each receipt was
taken. This test makes it a gate. A change that moves the count must update the expected value here and explain why
in docs/blackboard.md.
"""
import os
import uuid
from pathlib import Path

import psycopg
import pytest

from dqagent.cp import schema

PG = dict(host=os.environ.get("DQ_PG_HOST", "/tmp"), port=int(os.environ.get("DQ_PG_PORT", "55432")),
          user=os.environ.get("DQ_PG_USER", "dq_gov"))
ROOT = Path(__file__).resolve().parents[2]
EXPECTED = {"RELEASED": 420, "HELD": 291, "PENDING_DEPENDENCY": 47}


@pytest.fixture
def fresh_dsn():
    try:
        admin = psycopg.connect(dbname="postgres", autocommit=True, **PG)
    except Exception as e:  # pragma: no cover
        pytest.skip(f"T1 Postgres not reachable: {e}")
    db = "dqcp_w1o_" + uuid.uuid4().hex[:8]
    admin.execute(f"CREATE DATABASE {db}")
    dsn = dict(dbname=db, **PG)
    with psycopg.connect(**dsn) as c:
        schema.apply(c)
    yield dsn
    admin.execute(f"DROP DATABASE {db} WITH (FORCE)")
    admin.close()


def test_w1_unit_outcomes_are_pinned(fresh_dsn, tmp_path, monkeypatch):
    monkeypatch.setenv("DQ_ALLOW_TEST_GENESIS", "1")
    from dqagent.cli.main import load_synthetic
    from dqagent.runtime import Runtime
    rt = Runtime(fresh_dsn, dp_path=str(tmp_path / "dp.duckdb"), test_genesis=True)
    load_synthetic(rt, ROOT / "synthetic" / "out" / "w1")
    with psycopg.connect(**fresh_dsn) as c:
        got = dict(c.execute("SELECT state, count(*) FROM dq_ctl.units GROUP BY 1").fetchall())
    assert got == EXPECTED
