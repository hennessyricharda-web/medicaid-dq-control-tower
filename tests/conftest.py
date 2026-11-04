"""Suite-wide fixtures (Part F T0, F-15 / DEC-022).

The suite runs as the `test` deployment, set EXPLICITLY here rather than by a silent default in the code (an unset
DQ_DEPLOYMENT means `production`, which fails closed). Production paths are not hidden by this: they are exercised on
purpose through the `production_env` fixture, in tests/t1/test_production_paths.py.
"""
import pytest

from dqagent.deploy import ENV_VAR


@pytest.fixture(scope="session", autouse=True)
def test_deployment():
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv(ENV_VAR, "test")
        yield "test"


@pytest.fixture
def production_env(monkeypatch):
    """No stated deployment (so `production`), and no keys, OIDC issuer or test-genesis override in the environment.
    A test adds back exactly what it means to provide."""
    for v in (ENV_VAR, "DQ_K_EV", "DQ_K_FP", "DQ_K_ABC", "DQ_OIDC_ISSUER", "DQ_ALLOW_TEST_GENESIS"):
        monkeypatch.delenv(v, raising=False)
    return monkeypatch
