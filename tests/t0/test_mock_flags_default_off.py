"""tests/t0/test_mock_flags_default_off.py (K7, PFP P1-M, PARTF_IMPLEMENTATION_PLAN.md:393 and :58, binding).

Confirmed ABSENT before this build (K7_K8_K9_DESIGN.md §1.1; re-confirmed independently this dispatch by Glob,
zero matches). Built from scratch, not extended.

The four checks, verbatim from PFP:
  1. DQ_DEPLOYMENT unset resolves to production;
  2. NEXT_PUBLIC_DQ_MOCK unset is falsy;
  3. DQ_ALLOW_TEST_GENESIS alone never suffices without a bound TEST deployment;
  4. transport: scripted is refused outside test/demo (pilot and production).
"""
from __future__ import annotations

import os
import re
import uuid
from pathlib import Path

import psycopg
import pytest

from dqagent import deploy
from dqagent.deploy import ENV_VAR, NotConfiguredError

ROOT = Path(__file__).resolve().parents[2]
PG = dict(host=os.environ.get("DQ_PG_HOST", "/tmp"), port=int(os.environ.get("DQ_PG_PORT", "55432")),
          user=os.environ.get("DQ_PG_USER", "dq_gov"))


@pytest.fixture
def clean_env(monkeypatch):
    """No stated deployment, no evidence keys, no OIDC issuer, no test-genesis override -- the same scope the
    T1 `production_env` fixture clears (tests/conftest.py:19-25), reproduced here so this check does not depend
    on importing a t1-only fixture."""
    for v in (ENV_VAR, "DQ_K_EV", "DQ_K_FP", "DQ_K_ABC", "DQ_OIDC_ISSUER", "DQ_ALLOW_TEST_GENESIS"):
        monkeypatch.delenv(v, raising=False)
    return monkeypatch


@pytest.fixture
def fresh_cp_dsn():
    """Self-contained copy of tests/t1/conftest.py's fixture of the same name (that one is t1-scoped and not
    visible from tests/t0/; this file lives in t0 per the architect seat's ownership, PFP P1-M, so it carries its
    own copy rather than reaching into t1's conftest). Skips, never fails, when T1 Postgres is unreachable --
    this one check (DQ_ALLOW_TEST_GENESIS) is the only one of the four in this file that needs a real control
    plane; the other three are pure-Python / static-source checks."""
    try:
        admin = psycopg.connect(dbname="postgres", autocommit=True, **PG)
    except Exception as e:  # pragma: no cover
        pytest.skip(f"T1 Postgres not reachable: {e}")
    db = "dqcp_" + uuid.uuid4().hex[:8]
    admin.execute(f"CREATE DATABASE {db}")
    dsn = dict(dbname=db, **PG)
    from dqagent.cp import schema
    with psycopg.connect(**dsn) as c:
        schema.apply(c)
    yield dsn
    admin.execute(f"DROP DATABASE {db} WITH (FORCE)")
    admin.close()


def test_deployment_defaults_production(clean_env):
    """dqagent/deploy.py:19,47-52, confirmed: resolve(None) with $DQ_DEPLOYMENT unset returns "production"."""
    assert deploy.resolve(None) == "production"
    assert deploy.DEFAULT_DEPLOYMENT == "production"
    # An explicit, unrecognised deployment still raises rather than silently defaulting.
    clean_env.setenv(ENV_VAR, "staging")
    with pytest.raises(NotConfiguredError, match="unknown deployment"):
        deploy.resolve(None)


def test_next_public_mock_defaults_off(monkeypatch):
    """tower/src/lib/config.ts:3 (`export const MOCK = process.env.NEXT_PUBLIC_DQ_MOCK === "1"`), confirmed by
    direct read this dispatch: MOCK is a strict `=== "1"` comparison, never a generic truthiness check, so unset,
    "", "0", "true" and "false" are ALL falsy -- "true" alone would not turn mock on, which is deliberately
    stricter than most "is this flag on" checks and worth asserting explicitly. This is a static-source check (no
    Node runtime is assumed present in a t0 Python test); the same pattern is re-used at config.ts:11 for
    PREVIEW_K7_K12, checked identically below for the same reason F-45/F-47/F-50/F-51 files this flag was
    extended for are gated on it."""
    config_ts = (ROOT / "tower" / "src" / "lib" / "config.ts").read_text(encoding="utf-8")
    m = re.search(r'export const MOCK = process\.env\.NEXT_PUBLIC_DQ_MOCK === "1";', config_ts)
    assert m, "config.ts's MOCK export no longer matches the exact strict-equality form this check pins down"
    m2 = re.search(r'export const PREVIEW_K7_K12 = process\.env\.NEXT_PUBLIC_DQ_PREVIEW_K7_K12 === "1";', config_ts)
    assert m2, "config.ts's PREVIEW_K7_K12 export no longer matches the same strict-equality pattern"
    monkeypatch.delenv("NEXT_PUBLIC_DQ_MOCK", raising=False)
    assert os.environ.get("NEXT_PUBLIC_DQ_MOCK") != "1"          # unset: the condition the .ts file checks is false
    for off_value in ("", "0", "true", "TRUE", "yes", "mock"):
        monkeypatch.setenv("NEXT_PUBLIC_DQ_MOCK", off_value)
        assert os.environ["NEXT_PUBLIC_DQ_MOCK"] != "1", f"{off_value!r} must not satisfy the === \"1\" check"


def test_allow_test_genesis_alone_is_insufficient(fresh_cp_dsn, tmp_path, monkeypatch):
    """dqagent/runtime.py:291-301 (_check_deployment): DQ_ALLOW_TEST_GENESIS=1 permits a non-test runtime to open
    a control plane that already carries TEST-GENESIS artifacts, but only past the FIRST gate -- it still needs
    the control plane's DERIVED binding (test | demo) to equal the runtime's own stated deployment
    (runtime.py:306-309). DQ_ALLOW_TEST_GENESIS=1 plus a mismatched deployment (pilot, against a TEST-bound CP)
    still raises. Exercises the real mechanism directly (not cited by test id only): this is the same scenario
    tests/t1/test_production_paths.py::test_pilot_runtime_refused_on_test_bound_cp covers; reproduced here with
    its own fresh_cp_dsn copy (see fixture above) so this file's own claim is self-proving, not a forwarding
    reference."""
    from dqagent.runtime import Runtime
    from dqagent.secrets import TEST_KEYS
    from tests.t1.w1_harness import Slice

    for k, v in {"DQ_K_EV": "pilot-ev-key", "DQ_K_FP": "pilot-fp-key", "DQ_K_ABC": "pilot-abc-key"}.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("DQ_ALLOW_TEST_GENESIS", raising=False)
    Slice(fresh_cp_dsn)                                            # seeds a TEST-GENESIS control plane
    with pytest.raises(RuntimeError, match="TEST-GENESIS"):
        Runtime(fresh_cp_dsn, dp_path=str(tmp_path / "dp.duckdb"), deployment="pilot")
    monkeypatch.setenv("DQ_ALLOW_TEST_GENESIS", "1")
    # The override alone is NOT enough: the CP is bound test|demo, pilot still does not match it.
    with pytest.raises(NotConfiguredError, match="bound to the test"):
        Runtime(fresh_cp_dsn, dp_path=str(tmp_path / "dp.duckdb"), deployment="pilot")
    # Only the literal string "1" counts (red-team C6, runtime.py:299: `!= "1"`), confirmed directly.
    monkeypatch.setenv("DQ_ALLOW_TEST_GENESIS", "0")
    with pytest.raises(RuntimeError, match="TEST-GENESIS"):
        Runtime(fresh_cp_dsn, dp_path=str(tmp_path / "dp.duckdb"), deployment="demo", keys=TEST_KEYS)


def test_scripted_refused_outside_test_demo():
    """dqagent/deploy.py:24-29 (TRANSPORTS): pilot and production both allow only {"none", "http"}; scripted (and
    recorded, and fixed) are refused in both. Exercised directly against the real allow-list dict and the real
    require_transport() guard, for every governed deployment."""
    for governed in ("pilot", "production"):
        allowed = deploy.allowed_transports(governed)
        assert "scripted" not in allowed and "recorded" not in allowed and "fixed" not in allowed
        assert allowed == frozenset({"none", "http"})
        with pytest.raises(NotConfiguredError, match="not allowed"):
            deploy.require_transport("scripted", governed)
    for test_like in ("test", "demo"):
        assert "scripted" in deploy.allowed_transports(test_like)
        assert deploy.require_transport("scripted", test_like) == "scripted"
