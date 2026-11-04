"""K0 step 4 (Backend-API-Gov): `GET /api/v1/evals` (B9) and `/api/v1/autonomy` exposing `evidence` / `minimums`
(B7). E-13 (`GET /eval-attestations?status=PROPOSED`) is explicitly OUT of scope for K0 (user decision, 2026-10-24,
deferred to K11) and is not touched or tested here.

Test lenses (per this seat's rules): guards fail closed; degenerate inputs; order independence; no silent
degradation; claims enforced in the database, not only in Python."""
import psycopg
import pytest
from fastapi.testclient import TestClient

from dqagent.api.app import create_app
from dqagent.llm import PROMPT_VERSION
from dqagent.obs import tracer

from .evidence import grant_evidence, record_run
from .w1_harness import Slice


def H(rt, user, **kw):
    return {"Authorization": f"Bearer {rt.idp.token(user, **kw)}"}


# -- GET /api/v1/evals (B9) ------------------------------------------------------------------------------------------

def test_get_evals_lists_recorded_runs_newest_first(fresh_cp_dsn):
    s = Slice(fresh_cp_dsn)
    api = TestClient(create_app(s))
    det1 = record_run(s)
    llm = record_run(s, suite="explainer", source_mode="LIVE", prompt_version="p-x")
    r = api.get("/api/v1/evals", headers=H(s, "maker"))
    assert r.status_code == 200, r.text
    rows = r.json()
    ids = [row["run_id"] for row in rows]
    assert det1 in ids and llm in ids
    assert ids.index(llm) < ids.index(det1)                     # newest first (llm recorded after det1)
    row = next(row for row in rows if row["run_id"] == det1)
    assert row == {"run_id": det1, "suite": "agent_eval", "source_mode": "DETERMINISTIC", "cases": 12,
                   "passed": 12, "code_sha256": tracer.code_sha256(), "prompt_version": None,
                   "junit_sha256": row["junit_sha256"], "at": row["at"]}
    assert row["junit_sha256"] and len(row["junit_sha256"]) == 64


def test_get_evals_suite_filter_and_degenerate_inputs(fresh_cp_dsn):
    s = Slice(fresh_cp_dsn)
    api = TestClient(create_app(s))
    det = record_run(s)
    llm = record_run(s, suite="explainer", source_mode="LIVE", prompt_version="p-x")
    only_agent = api.get("/api/v1/evals", headers=H(s, "maker"), params={"suite": "agent_eval"}).json()
    assert {row["run_id"] for row in only_agent} == {det}
    only_explainer = api.get("/api/v1/evals", headers=H(s, "maker"), params={"suite": "explainer"}).json()
    assert {row["run_id"] for row in only_explainer} == {llm}
    bad = api.get("/api/v1/evals", headers=H(s, "maker"), params={"suite": "not_a_suite"})
    assert bad.status_code == 422, bad.text                     # guard fails closed, not a 500 or silent empty list
    one = api.get("/api/v1/evals", headers=H(s, "maker"), params={"limit": 1})
    assert one.status_code == 200 and len(one.json()) == 1


def test_get_evals_requires_governance_duty_not_merely_authentication(fresh_cp_dsn):
    s = Slice(fresh_cp_dsn)
    api = TestClient(create_app(s))
    record_run(s)
    assert api.get("/api/v1/evals", headers=H(s, "snap")).status_code == 403     # authenticated, no governance duty
    r = api.get("/api/v1/evals", headers=H(s, "maker"))
    assert r.status_code == 200 and r.json()


def test_get_evals_empty_is_an_empty_list_not_an_error(fresh_cp_dsn):
    s = Slice(fresh_cp_dsn)
    api = TestClient(create_app(s))
    r = api.get("/api/v1/evals", headers=H(s, "maker"))
    assert r.status_code == 200 and r.json() == []


def test_evals_role_enforced_in_the_database_not_only_python(fresh_cp_dsn):
    """I9-adjacent: dq_api already holds SELECT on eval_runs (trace_schema.py's R4-5 grants); confirm this in the
    database directly, independent of the route's own duty check, so the claim is not Python-only."""
    s = Slice(fresh_cp_dsn)
    record_run(s)
    with s.cp.as_role("dq_api") as c:
        rows = c.execute("SELECT run_id FROM dq_ctl.eval_runs").fetchall()
    assert rows
    with psycopg.connect(**fresh_cp_dsn) as c:                   # a role with no grant is refused by Postgres itself
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            with c.cursor() as cur:
                cur.execute("SET ROLE dq_publish_svc")            # not in the eval_runs SELECT grant list
                cur.execute("SELECT 1 FROM dq_ctl.eval_runs")


# -- /api/v1/autonomy evidence / minimums (B7) -----------------------------------------------------------------------

def test_autonomy_exposes_evidence_and_minimums_sourced_from_the_database(fresh_cp_dsn):
    s = Slice(fresh_cp_dsn)
    api = TestClient(create_app(s))
    rows = api.get("/api/v1/autonomy", headers=H(s, "maker")).json()
    assert rows, "fixture must carry at least one row in the maker's scope"
    for row in rows:
        assert "evidence" in row and "minimums" in row
    with s.cp.as_role("dq_registry_svc") as c:
        c.execute("SELECT set_config('dq.prompt_version', %s, true)", (PROMPT_VERSION,))
        want_ev = c.execute("SELECT dq_ctl.evidence_status() e").fetchone()["e"]
        want_min = c.execute("SELECT dq_ctl.promotion_minimums('ZZ') m").fetchone()["m"]
    assert rows[0]["evidence"]["complete"] is False and rows[0]["evidence"] == want_ev
    assert rows[0]["minimums"] == want_min and want_min["label"] == "demo minimums"
    # every row carries the SAME evidence/minimums (state-wide, not per-authority) -- order independence
    assert all(r["evidence"] == rows[0]["evidence"] for r in rows)
    assert all(r["minimums"] == rows[0]["minimums"] for r in rows)


def test_autonomy_evidence_flips_true_after_a_real_confirmed_attestation_not_hardcoded(fresh_cp_dsn):
    """Proves evidence is read from the database, not a fixed literal: before any attestation it is incomplete;
    after a real confirmed one for the running code, it is complete -- same route, same process, no mock."""
    s = Slice(fresh_cp_dsn)
    api = TestClient(create_app(s))
    before = api.get("/api/v1/autonomy", headers=H(s, "maker")).json()[0]["evidence"]
    assert before["complete"] is False and before["deterministic"] is False
    grant_evidence(s)
    after = api.get("/api/v1/autonomy", headers=H(s, "maker")).json()[0]["evidence"]
    assert after["complete"] is True and after["deterministic"] is True
    assert after["code_sha256"] == tracer.code_sha256()


def test_autonomy_minimums_reflect_a_governed_change_not_a_restart(fresh_cp_dsn):
    """Raising the minimums (one approver) must show up on the VERY NEXT /autonomy call -- proves `minimums` is
    read live from dq_ctl.promotion_minimums, not cached or computed once at startup."""
    s = Slice(fresh_cp_dsn)
    api = TestClient(create_app(s))
    before = api.get("/api/v1/autonomy", headers=H(s, "maker")).json()[0]["minimums"]
    assert before["L3"]["human_releases"] == 5
    item = s.effective_item("POLICY", "policy")
    item["content"]["promotion_minimums"]["L3"]["human_releases"] = 6
    cr, status = s.govern([item], approvers=("checker",))        # raising needs only one approver
    assert status == "APPLIED"
    after = api.get("/api/v1/autonomy", headers=H(s, "maker")).json()[0]["minimums"]
    assert after["L3"]["human_releases"] == 6


def test_autonomy_scoped_caller_still_gets_evidence_and_minimums(fresh_cp_dsn, tmp_path):
    """A reviewer scoped to one authority only (not the full catalog) still gets the state-wide evidence/minimums
    fields on their own rows -- no silent degradation for a narrower scope."""
    s = Slice(fresh_cp_dsn)
    api = TestClient(create_app(s))
    rows = api.get("/api/v1/autonomy", headers=H(s, "snap")).json()      # snap: AUTH_SNAP only
    assert rows and all(r["authority"] == "AUTH_SNAP" for r in rows)
    for row in rows:
        assert "evidence" in row and "minimums" in row and row["minimums"]["label"] == "demo minimums"
