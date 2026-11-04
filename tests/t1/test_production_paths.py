"""Part F T0 (F-15, F-19 guard, E-06, I-9, DEC-022): the production-like paths, exercised on purpose.

The suite runs as the `test` deployment (tests/conftest.py). These tests leave that on purpose, through the
`production_env` fixture or an explicit `deployment=`, so the fail-closed paths are tested rather than hidden:
pilot / production refuse missing keys, test keys, test genesis and an in-memory data plane; production refuses
without an OIDC IdP; a runtime's deployment must match the control plane's derived binding; --allow-sim permits the
simulator on a governed pilot control plane and nothing else.
"""
from pathlib import Path

import psycopg
import pytest
from fastapi.testclient import TestClient

from dqagent import deploy
from dqagent.api.app import create_app
from dqagent.cli.main import load_synthetic, main
from dqagent.deploy import NotConfiguredError
from dqagent.runtime import Runtime, bootstrap_roster
from dqagent.secrets import TEST_KEYS, EnvSecrets, is_test_keys

from .w1_harness import Slice

ROOT = Path(__file__).resolve().parents[2]
W1 = ROOT / "synthetic" / "out" / "w1"
EXPECTED = {"RELEASED": 420, "HELD": 291, "PENDING_DEPENDENCY": 47}
ENV_KEYS = {"DQ_K_EV": "pilot-ev-key", "DQ_K_FP": "pilot-fp-key", "DQ_K_ABC": "pilot-abc-key"}


def _keys(mp):
    for k, v in ENV_KEYS.items():
        mp.setenv(k, v)


def _nothing_written(dsn):
    with psycopg.connect(**dsn) as c:
        assert c.execute("SELECT count(*) FROM dq_ctl.config_artifacts").fetchone()[0] == 0
        assert c.execute("SELECT count(*) FROM dq_ctl.roster").fetchone()[0] == 0
        assert c.execute("SELECT count(*) FROM dq_ctl.deployment_binding").fetchone()[0] == 0


def _pilot(dsn, tmp, **kw):
    """A governed pilot control plane: roster bootstrap, then a GENESIS change request ratified by two people."""
    bootstrap_roster(dsn, {}, "ROSTER-BOOTSTRAP")
    rt = Runtime(dsn, dp_path=str(tmp / "pilot.duckdb"), deployment="pilot", **kw)
    assert rt.ratify_genesis()["status"] == "APPLIED"
    return rt


def test_default_deployment_is_production(production_env):
    assert deploy.resolve(None) == "production"
    production_env.setenv(deploy.ENV_VAR, "staging")
    with pytest.raises(NotConfiguredError, match="unknown deployment"):
        deploy.resolve(None)


def test_production_without_env_keys_raises(fresh_cp_dsn, tmp_path, production_env):
    with pytest.raises(NotConfiguredError, match="DQ_K_EV"):
        Runtime(fresh_cp_dsn, dp_path=str(tmp_path / "dp.duckdb"))
    production_env.setenv("DQ_K_EV", TEST_KEYS["K_EV"].decode())          # a labelled test key through the env
    production_env.setenv("DQ_K_FP", "x")
    production_env.setenv("DQ_K_ABC", "y")
    with pytest.raises(NotConfiguredError, match="TEST key"):
        EnvSecrets().keys()
    with pytest.raises(NotConfiguredError, match="TEST keys"):
        Runtime(fresh_cp_dsn, dp_path=str(tmp_path / "dp.duckdb"), deployment="pilot", keys=TEST_KEYS)
    as_str = {k: v.decode() for k, v in TEST_KEYS.items()}                 # Nit 8: the same keys as str
    assert is_test_keys(as_str) and is_test_keys({"K_EV": "t1-ev-key"})
    with pytest.raises(NotConfiguredError, match="TEST keys"):
        Runtime(fresh_cp_dsn, dp_path=str(tmp_path / "dp.duckdb"), deployment="pilot", keys=as_str)
    _nothing_written(fresh_cp_dsn)


def test_production_without_oidc_raises(fresh_cp_dsn, tmp_path, production_env):
    _keys(production_env)
    with pytest.raises(NotConfiguredError, match="OIDC"):
        Runtime(fresh_cp_dsn, dp_path=str(tmp_path / "dp.duckdb"))
    production_env.setenv("DQ_OIDC_ISSUER", "https://idp.example.invalid")
    with pytest.raises(NotConfiguredError, match="not implemented"):     # L-16: no OIDC IdP exists yet
        Runtime(fresh_cp_dsn, dp_path=str(tmp_path / "dp.duckdb"))
    _nothing_written(fresh_cp_dsn)


def test_production_and_pilot_refuse_an_in_memory_data_plane(fresh_cp_dsn, production_env):
    _keys(production_env)
    for d in ("pilot", "production"):
        with pytest.raises(NotConfiguredError, match=":memory:"):
            Runtime(fresh_cp_dsn, deployment=d)


def test_test_genesis_under_production_raises(fresh_cp_dsn, tmp_path, production_env):
    """C5: refused BEFORE anything is written (no roster, no artifacts, no binding)."""
    _keys(production_env)
    with pytest.raises(NotConfiguredError, match="test genesis"):
        Runtime(fresh_cp_dsn, dp_path=str(tmp_path / "dp.duckdb"), test_genesis=True)
    _nothing_written(fresh_cp_dsn)


def test_test_genesis_under_pilot_raises(fresh_cp_dsn, tmp_path, monkeypatch):
    _keys(monkeypatch)
    with pytest.raises(NotConfiguredError, match="test genesis"):
        Runtime(fresh_cp_dsn, dp_path=str(tmp_path / "dp.duckdb"), test_genesis=True, deployment="pilot")
    _nothing_written(fresh_cp_dsn)


def test_governed_genesis_refused_in_demo(fresh_cp_dsn, tmp_path):
    bootstrap_roster(fresh_cp_dsn, {}, "ROSTER-BOOTSTRAP")
    rt = Runtime(fresh_cp_dsn, dp_path=str(tmp_path / "dp.duckdb"), deployment="demo")
    with pytest.raises(NotConfiguredError, match="not a demo deployment"):
        rt.ratify_genesis()
    with psycopg.connect(**fresh_cp_dsn) as c:                           # the apply rolled back with the bind
        assert c.execute("SELECT count(*) FROM dq_ctl.config_artifacts").fetchone()[0] == 0
        assert c.execute("SELECT count(*) FROM dq_ctl.config_cr_events WHERE event='APPLIED'").fetchone()[0] == 0
        assert c.execute("SELECT count(*) FROM dq_ctl.deployment_binding").fetchone()[0] == 0
    assert not rt.ratified


def test_demo_runtime_refused_on_pilot_bound_cp(fresh_cp_dsn, tmp_path, monkeypatch):
    _keys(monkeypatch)
    rt = _pilot(fresh_cp_dsn, tmp_path)
    rt.dp.con.close()
    with pytest.raises(NotConfiguredError, match="bound to the pilot"):
        Runtime(fresh_cp_dsn, dp_path=str(tmp_path / "pilot.duckdb"), deployment="demo")
    with pytest.raises(NotConfiguredError, match="test genesis"):
        Runtime(fresh_cp_dsn, dp_path=str(tmp_path / "pilot.duckdb"), deployment="pilot", test_genesis=True)


def test_pilot_runtime_refused_on_test_bound_cp(fresh_cp_dsn, tmp_path, monkeypatch):
    """E-06: DQ_ALLOW_TEST_GENESIS=1 no longer lets a non-test runtime accept a control plane that two people never
    seeded: the TEST binding (test | demo) must also match the runtime's deployment."""
    _keys(monkeypatch)
    Slice(fresh_cp_dsn)
    monkeypatch.delenv("DQ_ALLOW_TEST_GENESIS", raising=False)
    with pytest.raises(RuntimeError, match="TEST-GENESIS"):
        Runtime(fresh_cp_dsn, dp_path=str(tmp_path / "dp.duckdb"), deployment="pilot")
    monkeypatch.setenv("DQ_ALLOW_TEST_GENESIS", "1")
    with pytest.raises(NotConfiguredError, match="bound to the test"):
        Runtime(fresh_cp_dsn, dp_path=str(tmp_path / "dp.duckdb"), deployment="pilot")
    with pytest.raises(NotConfiguredError, match="bound to the test"):
        Runtime(fresh_cp_dsn, deployment="demo")
    assert Runtime(fresh_cp_dsn).deployment == "test"                   # the matching deployment still opens it


def test_pilot_with_env_keys_and_governed_genesis_loads_w1_to_420_291_47(fresh_cp_dsn, tmp_path, monkeypatch):
    """The production key path end to end: keys from the environment (never the test literals), a governed genesis
    ratified by two people, the pilot binding, and the full w1 load reaches the pinned outcome."""
    _keys(monkeypatch)
    rt = _pilot(fresh_cp_dsn, tmp_path)
    assert rt.keys_source == "ENV" and rt.keys["K_EV"] == b"pilot-ev-key" and rt.genesis_kind == "GOVERNED"
    assert rt.registry.deployment_binding()["deployment"] == "pilot"
    load_synthetic(rt, W1)
    with psycopg.connect(**fresh_cp_dsn) as c:
        got = dict(c.execute("SELECT state, count(*) FROM dq_ctl.units GROUP BY 1").fetchall())
    assert got == EXPECTED


def test_allow_sim_permits_only_sim_on_pilot(fresh_cp_dsn, tmp_path, monkeypatch):
    _keys(monkeypatch)
    rt = _pilot(fresh_cp_dsn, tmp_path)
    with pytest.raises(NotConfiguredError, match="--allow-sim"):
        rt.sim_enable(W1, parte=False)
    assert not rt.sim_enabled
    rt.sim_enable(W1, parte=False, allow_governed=True, person_id="E100")
    assert rt.sim_enabled and rt.sim_on_governed
    with psycopg.connect(**fresh_cp_dsn) as c:
        assert c.execute("SELECT event, person_id FROM dq_ctl.deployment_events").fetchall() == \
            [("SIM_ENABLED_ON_GOVERNED", "E100")]
    rt.earn_story_seed()                                                 # lower-only: permitted
    assert len(rt.sim_preload(1)) == 1                                   # preloading synthetic nights: permitted
    api = TestClient(create_app(rt))
    tok = {"Authorization": f"Bearer {rt.idp.token('maker')}"}
    assert api.get("/api/v1/sim/nights", headers=tok).status_code == 200
    # ... and nothing else: still no test keys, no test genesis, no scripted transport in pilot
    assert deploy.allowed_transports("pilot") == {"none", "http"}
    with pytest.raises(NotConfiguredError):
        deploy.require_transport("scripted", "pilot")
    rt.dp.con.close()
    with pytest.raises(NotConfiguredError):
        Runtime(fresh_cp_dsn, dp_path=str(tmp_path / "pilot.duckdb"), deployment="pilot", keys=TEST_KEYS)
    with pytest.raises(NotConfiguredError):
        Runtime(fresh_cp_dsn, dp_path=str(tmp_path / "pilot.duckdb"), deployment="pilot", test_genesis=True)
    with pytest.raises(SystemExit, match="--allow-sim applies only"):  # the CLI flag means nothing elsewhere
        main(["demo", "--data", str(W1), "--allow-sim", "--nightly"])
    with pytest.raises(SystemExit, match="--allow-sim applies only"):
        main(["demo", "--data", str(W1), "--allow-sim", "--genesis", "governed"])


@pytest.mark.parametrize("deployment", deploy.DEPLOYMENTS)
@pytest.mark.parametrize("transport", ["none", "http", "scripted", "recorded", "fixed", "future"])
def test_transport_allowlist_by_deployment_deploy_side(deployment, transport):
    """R4-3, the deploy-side helper (the llm side, which calls it, is Backend-B's)."""
    allowed = {"test": {"none", "scripted", "recorded", "http", "fixed"}, "demo": {"none", "scripted", "recorded", "http"},
               "pilot": {"none", "http"}, "production": {"none", "http"}}[deployment]
    assert deploy.allowed_transports(deployment) == allowed
    if transport in allowed:
        assert deploy.require_transport(transport, deployment) == transport
    else:
        with pytest.raises(NotConfiguredError):
            deploy.require_transport(transport, deployment)


def test_pilot_runtime_explainer_refuses_scripted_whatever_the_environment_says(fresh_cp_dsn, tmp_path, monkeypatch):
    """The explainer's transport check follows the RUNTIME's deployment (pilot), not $DQ_DEPLOYMENT (demo): a pilot
    runtime refuses the scripted transport even when the environment claims demo. The template still renders."""
    _keys(monkeypatch)
    rt = _pilot(fresh_cp_dsn, tmp_path)
    monkeypatch.setenv(deploy.ENV_VAR, "demo")
    obj = {"agent_id": "a", "action_class": "RELEASE", "authority": "AUTH_MEDICAID", "from_tier": "L3",
           "to_tier": "L2", "reason_code": "VETO", "cr_id": "DEM-1"}
    with pytest.raises(NotConfiguredError, match="scripted"):
        rt.explain_for("demotion", obj, cfg={"provider": "anthropic", "transport": "scripted"})
    assert rt.explain_for("demotion", obj, cfg={"provider": "template"})["source"] == "template"
