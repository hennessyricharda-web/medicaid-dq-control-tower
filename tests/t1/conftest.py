"""T1 fixtures: a fresh Postgres database per test session on the local cluster.

The cluster is started by tools/t1_postgres.sh (default port 55432, socket in /tmp).
If it is not reachable, CP tests are SKIPPED with an explicit reason. They are never
reported as passed.
"""
import os
import uuid

import psycopg
import pytest

from dqagent.cp import schema

PG = dict(host=os.environ.get("DQ_PG_HOST", "/tmp"), port=int(os.environ.get("DQ_PG_PORT", "55432")),
          user=os.environ.get("DQ_PG_USER", "dq_gov"))


@pytest.fixture(scope="session")
def cp_dsn():
    try:
        admin = psycopg.connect(dbname="postgres", autocommit=True, **PG)
    except Exception as e:  # pragma: no cover
        pytest.skip(f"T1 Postgres not reachable: {e}")
    db = "dqcp_" + uuid.uuid4().hex[:8]
    admin.execute(f"CREATE DATABASE {db}")
    dsn = dict(dbname=db, **PG)
    with psycopg.connect(**dsn) as c:
        schema.apply(c)
    yield dsn
    admin.execute(f"DROP DATABASE {db} WITH (FORCE)")
    admin.close()


@pytest.fixture
def cp(cp_dsn):
    with psycopg.connect(**cp_dsn) as c:
        yield c


@pytest.fixture
def fresh_cp_dsn():
    """A brand-new CP database per test. Lifecycle ordering is global per partition, so
    slice tests must not share state."""
    try:
        admin = psycopg.connect(dbname="postgres", autocommit=True, **PG)
    except Exception as e:  # pragma: no cover
        pytest.skip(f"T1 Postgres not reachable: {e}")
    db = "dqcp_" + uuid.uuid4().hex[:8]
    admin.execute(f"CREATE DATABASE {db}")
    dsn = dict(dbname=db, **PG)
    with psycopg.connect(**dsn) as c:
        schema.apply(c)
    yield dsn
    admin.execute(f"DROP DATABASE {db} WITH (FORCE)")
    admin.close()
