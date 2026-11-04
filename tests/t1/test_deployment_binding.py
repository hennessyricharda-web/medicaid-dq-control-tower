"""Part F R4-2 (N2): the deployment binding is DERIVED in the database, never asserted by a caller.

dq_ctl.bind_deployment(p_deployment) works out the genesis kind itself (TEST-GENESIS artifacts -> TEST; an APPLIED
GENESIS change request -> GOVERNED; neither -> refused), allows TEST with test | demo and GOVERNED with pilot |
production, and only the registry service may call it. A GOVERNED binding is written inside gov_apply, in the same
transaction as the genesis. No binding row reads as production.
"""
import psycopg
import pytest
from psycopg.rows import dict_row

from dqagent import deploy
from dqagent.runtime import Runtime, bootstrap_roster

from .w1_harness import Slice

ENV_KEYS = {"DQ_K_EV": "pilot-ev-key", "DQ_K_FP": "pilot-fp-key", "DQ_K_ABC": "pilot-abc-key"}


def _as(dsn, role):
    return psycopg.connect(**{**dsn, "user": role})


def test_registry_cannot_prebind_governed_on_fresh_cp(fresh_cp_dsn):
    """The N2 attack: the registry service binds pilot / production BEFORE any genesis, so a later TEST genesis would
    run under it (or a governed one would be pre-labelled). Refused: there is no genesis to derive from."""
    for d in ("pilot", "production", "test", "demo"):
        with _as(fresh_cp_dsn, "dq_registry_svc") as c:
            with pytest.raises(psycopg.errors.CheckViolation, match="no genesis to bind"):
                c.execute("SELECT dq_ctl.bind_deployment(%s,'LOCAL')", (d,))
    for role in ("dq_api", "dq_seal_svc", "dq_eval_svc", "dq_publish_svc"):
        with _as(fresh_cp_dsn, role) as c:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                c.execute("SELECT dq_ctl.bind_deployment('pilot','LOCAL')")
    for role in ("dq_registry_svc", "dq_api"):
        with _as(fresh_cp_dsn, role) as c:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                c.execute("INSERT INTO dq_ctl.deployment_binding(genesis_kind,deployment) VALUES ('GOVERNED','pilot')")


def test_governed_genesis_binds_inside_gov_apply(fresh_cp_dsn, tmp_path, monkeypatch):
    for k, v in ENV_KEYS.items():
        monkeypatch.setenv(k, v)
    bootstrap_roster(fresh_cp_dsn, {}, "ROSTER-BOOTSTRAP")
    rt = Runtime(fresh_cp_dsn, dp_path=str(tmp_path / "dp.duckdb"), deployment="pilot")
    cr = rt.registry.propose_genesis("ZZ", "E100")
    for user, emp in (("checker", "E200"), ("checker2", "E210")):
        tok = rt.idp.token(user, step_up=True, challenge=rt.registry.challenge(cr["cr_id"], "APPROVED"))
        rt.registry.decide(cr["cr_id"], "APPROVED", tok, emp, ["governance:AUTH_RULES"])
    with pytest.raises(psycopg.errors.CheckViolation, match="unknown deployment"):
        rt.registry.apply(cr["cr_id"], expected_state="ZZ", deployment=None)    # no stated deployment: refused ...
    with psycopg.connect(**fresh_cp_dsn) as c:                                  # ... and the whole apply rolled back
        assert c.execute("SELECT count(*) FROM dq_ctl.config_artifacts").fetchone()[0] == 0
        assert c.execute("SELECT count(*) FROM dq_ctl.deployment_binding").fetchone()[0] == 0
    assert rt.apply_change(cr["cr_id"]) == "APPLIED" and rt.ratified
    with psycopg.connect(**fresh_cp_dsn) as c:
        row = c.execute("SELECT genesis_kind, deployment, idp_kind FROM dq_ctl.deployment_binding").fetchone()
        assert row == ("GOVERNED", "pilot", "LOCAL")
        applied = c.execute("SELECT xmin::text FROM dq_ctl.config_cr_events WHERE cr_id=%s AND event='APPLIED'",
                            (cr["cr_id"],)).fetchone()[0]
        bound = c.execute("SELECT xmin::text FROM dq_ctl.deployment_binding").fetchone()[0]
        assert applied == bound                                                 # the same transaction
        with pytest.raises(psycopg.Error, match="append-only|not allowed|mutat"):  # append-only, even for the admin
            c.execute("UPDATE dq_ctl.deployment_binding SET deployment='production'")
    with _as(fresh_cp_dsn, "dq_registry_svc") as c:
        with pytest.raises(psycopg.errors.CheckViolation, match="bound to pilot"):
            c.execute("SELECT dq_ctl.bind_deployment('production','OIDC')")             # no re-binding
    with _as(fresh_cp_dsn, "dq_registry_svc") as c:
        assert c.execute("SELECT dq_ctl.bind_deployment('pilot','LOCAL')").fetchone()[0] == "GOVERNED"   # same: a no-op


def test_test_genesis_cannot_bind_production(fresh_cp_dsn):
    s = Slice(fresh_cp_dsn)
    assert s.registry.deployment_binding()["deployment"] == "test"
    for d in ("production", "pilot", "demo"):
        with _as(fresh_cp_dsn, "dq_registry_svc") as c:
            with pytest.raises(psycopg.errors.CheckViolation):
                c.execute("SELECT dq_ctl.bind_deployment(%s,'LOCAL')", (d,))
    with psycopg.connect(**fresh_cp_dsn) as c:
        assert c.execute("SELECT genesis_kind, deployment, idp_kind FROM dq_ctl.deployment_binding").fetchall() == \
            [("TEST", "test", "LOCAL")]


def test_no_binding_reads_as_production(fresh_cp_dsn):
    for role in ("dq_api", "dq_eval_svc", "dq_publish_svc", "dq_registry_svc", "dq_auditor"):
        with psycopg.connect(**{**fresh_cp_dsn, "user": role}, row_factory=dict_row) as c:
            b = deploy.bound(c)
        assert b["deployment"] == "production" and b["bound"] is False and b["genesis_kind"] is None
    rt = Runtime(fresh_cp_dsn)                                                  # pre-genesis test runtime: unbound
    assert rt.registry.deployment_binding()["deployment"] == "production"


def test_binding_carries_the_idp_kind_and_a_local_pilot_is_labelled_not_production(fresh_cp_dsn, tmp_path,
                                                                                    monkeypatch):
    """Red team frame finding: `demo --genesis governed` binds pilot on the LOCAL demo IdP; the binding records the IdP
    kind beside the deployment, the api shows "Pilot (local IdP - not production)", the database refuses production
    without OIDC and refuses re-binding with another IdP kind. Governed + demo stays refused (N2)."""
    from fastapi.testclient import TestClient

    from dqagent.api.app import create_app
    for k, v in ENV_KEYS.items():
        monkeypatch.setenv(k, v)
    bootstrap_roster(fresh_cp_dsn, {}, "ROSTER-BOOTSTRAP")
    rt = Runtime(fresh_cp_dsn, dp_path=str(tmp_path / "dp.duckdb"), deployment="pilot")
    assert rt.ratify_genesis()["status"] == "APPLIED"
    info = rt.deployment_info()
    assert (info["deployment"], info["idp_kind"], info["genesis_kind"]) == ("pilot", "LOCAL", "GOVERNED")
    assert info["label"] == "Pilot (local IdP \u2013 not production)"
    api = TestClient(create_app(rt))
    assert api.get("/api/v1/tenant").json()["deployment"]["label"] == info["label"]
    sm = api.get("/api/v1/summary", headers={"Authorization": f"Bearer {rt.idp.token('maker')}"}).json()
    assert sm["deployment"]["idp_kind"] == "LOCAL" and sm["deployment"]["deployment"] == "pilot"
    with _as(fresh_cp_dsn, "dq_registry_svc") as c:
        with pytest.raises(psycopg.errors.CheckViolation, match="bound to pilot"):
            c.execute("SELECT dq_ctl.bind_deployment('pilot','OIDC')")             # another IdP kind: refused
    with _as(fresh_cp_dsn, "dq_registry_svc") as c:
        with pytest.raises(psycopg.errors.CheckViolation, match="not a demo deployment"):
            c.execute("SELECT dq_ctl.bind_deployment('demo','LOCAL')")             # governed + demo: refused (N2)
    assert deploy.label("pilot", "OIDC") == "Pilot" and deploy.label("demo", "LOCAL") == "Demo (local IdP)"


def test_production_needs_oidc_in_the_database(fresh_cp_dsn, tmp_path, monkeypatch):
    for k, v in ENV_KEYS.items():
        monkeypatch.setenv(k, v)
    bootstrap_roster(fresh_cp_dsn, {}, "ROSTER-BOOTSTRAP")
    rt = Runtime(fresh_cp_dsn, dp_path=str(tmp_path / "dp.duckdb"), deployment="pilot")
    cr = rt.registry.propose_genesis("ZZ", "E100")
    for user, emp in (("checker", "E200"), ("checker2", "E210")):
        tok = rt.idp.token(user, step_up=True, challenge=rt.registry.challenge(cr["cr_id"], "APPROVED"))
        rt.registry.decide(cr["cr_id"], "APPROVED", tok, emp, ["governance:AUTH_RULES"])
    with pytest.raises(psycopg.errors.CheckViolation, match="production needs an OIDC IdP"):
        rt.registry.apply(cr["cr_id"], expected_state="ZZ", deployment="production", idp_kind="LOCAL")
    with psycopg.connect(**fresh_cp_dsn) as c:
        assert c.execute("SELECT count(*) FROM dq_ctl.deployment_binding").fetchone()[0] == 0
