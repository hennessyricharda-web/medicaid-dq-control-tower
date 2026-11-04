"""tests/t1/test_k11_explorer.py: K11 Explorer integration tests (PFP P2-1 :940-965, F-05, F-22, E-13).

Covers:
- GET /eval-attestations?status=PROPOSED / CONFIRMED (E-13)
- GET /evals/{run_id} and GET /evals/diff
- POST /explanations/feedback and candidate generation
- GET /annotation-queue and /annotations
- EVAL_CASE kind rejection in gov_item_lower_only
"""
import pytest
from fastapi.testclient import TestClient

from dqagent.api.app import create_app
from dqagent.governance.registry import lower_only

from .fixtures_w1 import ie_file, person
from .test_parte_explanations import cp_dsn_module  # noqa: F401 - module-scoped fresh control plane
from .w1_harness import Slice


def H(rt, user, **kw):
    return {"Authorization": f"Bearer {rt.idp.token(user, **kw)}"}


@pytest.fixture(scope="module")
def world(cp_dsn_module, tmp_path_factory):  # noqa: F811
    s = Slice(cp_dsn_module)
    tmp = tmp_path_factory.mktemp("k11")
    held = tmp / "held.txt"
    held.write_bytes(ie_file([person("P3", "M0003"), person("P4", "M0004")], trailer=7))
    s.run_pipeline("p4_eligibility_ie", "B-K11", [("IE", held)], ["bronze.ie_segment"])
    api = TestClient(create_app(s))
    return s, api


def test_eval_case_kind_is_not_lower_only():
    assert lower_only("EVAL_CASE", {}, {}, "PUT") is False


def test_eval_attestations_listing_e13(world):
    s, api = world
    # Query pending and confirmed attestations
    res_proposed = api.get("/api/v1/eval-attestations?status=PROPOSED", headers=H(s, "checker"))
    assert res_proposed.status_code == 200
    assert isinstance(res_proposed.json(), list)

    res_all = api.get("/api/v1/eval-attestations", headers=H(s, "checker"))
    assert res_all.status_code == 200
    assert isinstance(res_all.json(), list)


def test_explanation_feedback_and_annotation_queue(world):
    s, api = world
    # Accurate feedback
    fb1 = api.post("/api/v1/explanations/feedback", headers=H(s, "maker"), json={
        "facts_hash": "a" * 64,
        "text_hash": "b" * 64,
        "verdict": "ACCURATE",
        "comment": "Looks good"
    })
    assert fb1.status_code == 200
    assert fb1.json()["status"] == "RECORDED"
    assert fb1.json()["candidate_id"] is None

    # Inaccurate feedback -> creates candidate
    fb2 = api.post("/api/v1/explanations/feedback", headers=H(s, "maker"), json={
        "facts_hash": "c" * 64,
        "text_hash": "d" * 64,
        "verdict": "INACCURATE",
        "reason_code": "HALLUCINATION",
        "comment": "Wrong claim"
    })
    assert fb2.status_code == 200
    assert fb2.json()["status"] == "RECORDED"
    cand_id = fb2.json()["candidate_id"]
    assert cand_id is not None

    # Check annotation queue
    queue = api.get("/api/v1/annotation-queue", headers=H(s, "checker"))
    assert queue.status_code == 200
    items = queue.json()
    assert any(it["candidate_id"] == cand_id for it in items)


def test_eval_run_detail_and_diff(world):
    s, api = world
    # Seed an eval run directly in the database
    with s.cp.as_role("dq_eval_svc") as c:
        c.execute(
            "INSERT INTO dq_ctl.eval_runs(run_id, suite, source_mode, cases, passed, code_sha256, prompt_version, junit_sha256) "
            "VALUES ('run-k11-test-1', 'agent_eval', 'DETERMINISTIC', 2, 2, %s, 'v1', %s) "
            "ON CONFLICT DO NOTHING",
            ("1" * 64, "2" * 64)
        )
        c.execute(
            "INSERT INTO dq_ctl.eval_runs(run_id, suite, source_mode, cases, passed, code_sha256, prompt_version, junit_sha256) "
            "VALUES ('run-k11-test-2', 'agent_eval', 'DETERMINISTIC', 2, 1, %s, 'v1', %s) "
            "ON CONFLICT DO NOTHING",
            ("1" * 64, "3" * 64)
        )
        c.execute(
            "INSERT INTO dq_ctl.eval_cases(run_id, case_id, kind, facts_hash, verdict, passed) "
            "VALUES ('run-k11-test-1', 'case-1', 'SCENARIO', 'f1', 'PASS', true), "
            "       ('run-k11-test-1', 'case-2', 'SCENARIO', 'f2', 'PASS', true), "
            "       ('run-k11-test-2', 'case-1', 'SCENARIO', 'f1', 'PASS', true), "
            "       ('run-k11-test-2', 'case-2', 'SCENARIO', 'f2', 'FAIL', false) "
            "ON CONFLICT DO NOTHING"
        )

    detail = api.get("/api/v1/evals/run-k11-test-1", headers=H(s, "checker"))
    assert detail.status_code == 200
    assert len(detail.json()["eval_cases"]) == 2

    diff = api.get("/api/v1/evals/diff?left=run-k11-test-1&right=run-k11-test-2", headers=H(s, "checker"))
    assert diff.status_code == 200
    assert diff.json()["total_diffs"] == 1
    assert diff.json()["diffs"][0]["case_id"] == "case-2"
    assert diff.json()["diffs"][0]["status"] == "CHANGED"
