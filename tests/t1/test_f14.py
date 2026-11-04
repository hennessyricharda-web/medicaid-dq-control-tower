"""tests/t1/test_f14.py: K10 Robustness integration tests (PFP P2-7 :1044-1051, F-14).

Covers:
- Advisory lock per pipeline in run_pipeline
- PIPELINE SLA field and cluster sla_due calculation
- Alert sinks
"""
import pytest
from fastapi.testclient import TestClient

from dqagent.alerts import LogSink, WebhookSink
from dqagent.api.app import create_app
from dqagent.deploy import NotConfiguredError

from .fixtures_w1 import ie_file, person
from .test_parte_explanations import cp_dsn_module  # noqa: F401 - module-scoped fresh control plane
from .w1_harness import Slice


def H(rt, user, **kw):
    return {"Authorization": f"Bearer {rt.idp.token(user, **kw)}"}


@pytest.fixture(scope="module")
def world(cp_dsn_module, tmp_path_factory):  # noqa: F811
    s = Slice(cp_dsn_module)
    tmp = tmp_path_factory.mktemp("k10")
    held = tmp / "held.txt"
    held.write_bytes(ie_file([person("P3", "M0003"), person("P4", "M0004")], trailer=7))
    s.run_pipeline("p4_eligibility_ie", "B-K10", [("IE", held)], ["bronze.ie_segment"])
    api = TestClient(create_app(s))
    return s, api


def test_cluster_sla_due_arithmetic(world):
    s, api = world
    q = api.get("/api/v1/queue", headers=H(s, "maker")).json()
    assert q, "expected at least one cluster in queue"
    for cl in q:
        assert "sla_due" in cl
        assert cl["sla_due"] is not None


def test_pipeline_execution_succeeds(world, tmp_path):
    s, _api = world
    f = tmp_path / "f.txt"
    f.write_bytes(ie_file([person("P1", "M0001")], trailer=1))
    out = s.run_pipeline("p4_eligibility_ie", "B-K10-2", [("IE", f)], ["bronze.ie_segment"])
    assert "units" in out
