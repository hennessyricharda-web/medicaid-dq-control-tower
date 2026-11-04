"""Part F P1-1 (F-05 backend): trace roots and spans, over the real control plane and synthetic w1.

I-8: tracing only observes. Running the same nights with tracing on and off gives identical outcomes (units, results,
the agent's blackboard including its hash chain, and the latest event of every unit), and the trace itself is
deterministic: two fresh control planes give the same trace shape.
"""
import contextlib
import hashlib
import re
import json
import os
import uuid
from pathlib import Path

import psycopg
import pytest

from dqagent.cp import schema
from dqagent.cp.schema import SERVICE_ROLES
from dqagent.cp.trace_schema import TRACE_TABLES
from dqagent.obs import tracer
from dqagent.runtime import Runtime, seed_routing, synthetic_nights

PG = dict(host=os.environ.get("DQ_PG_HOST", "/tmp"), port=int(os.environ.get("DQ_PG_PORT", "55432")),
          user=os.environ.get("DQ_PG_USER", "dq_gov"))
W1 = Path(__file__).resolve().parents[2] / "synthetic" / "out" / "w1"
EXPECTED = {"RELEASED": 420, "HELD": 291, "PENDING_DEPENDENCY": 47}


@contextlib.contextmanager
def _cp(tmp):
    try:
        admin = psycopg.connect(dbname="postgres", autocommit=True, **PG)
    except Exception as e:  # pragma: no cover
        pytest.skip(f"T1 Postgres not reachable: {e}")
    tmp.mkdir(parents=True, exist_ok=True)
    db = "dqcp_tr_" + uuid.uuid4().hex[:8]
    admin.execute(f"CREATE DATABASE {db}")
    dsn = dict(dbname=db, **PG)
    with psycopg.connect(**dsn) as c:
        schema.apply(c)
    try:
        yield dsn, Runtime(dsn, dp_path=str(tmp / f"{db}.duckdb"), test_genesis=True)
    finally:
        admin.execute(f"DROP DATABASE {db} WITH (FORCE)")
        admin.close()


def _nights(rt, k=None):
    nights = synthetic_nights(W1, seed_routing("ZZ"))
    for i, (night, files) in enumerate(nights[:k] if k else nights):
        rt.run_night(W1, night, files, last=(k is None and i == len(nights) - 1))


def _q(dsn, sql, *a):
    with psycopg.connect(**dsn) as c:
        return [tuple(r) for r in c.execute(sql, a).fetchall()]


def _shape(dsn) -> str:
    """The trace without timestamps, digests of the code / config, or surrogate row ids."""
    doc = {
        "runs": _q(dsn, "SELECT trace_id, kind, parent_trace_id, state, attempt, night_date::text, authorities "
                        "FROM dq_ctl.runs ORDER BY trace_id"),
        "events": _q(dsn, "SELECT trace_id, event, error_class FROM dq_ctl.run_events ORDER BY trace_id, id"),
        "spans": _q(dsn, "SELECT trace_id, span_id, parent_span_id, ordinal, name, seat, subject, authorities, status, "
                         "attrs::text, refs FROM dq_ctl.spans ORDER BY trace_id, span_id"),
        "checks": _q(dsn, "SELECT span_id, unit_key, ok, reasons FROM dq_ctl.release_checks ORDER BY id"),
        "guardrails": _q(dsn, "SELECT trace_id, span_id, code, subject, detail::text FROM dq_ctl.guardrail_events "
                              "ORDER BY id"),
    }
    return hashlib.sha256(json.dumps(doc, sort_keys=True, default=str).encode()).hexdigest()


def _outcome(dsn) -> dict:
    return {
        "units": _q(dsn, "SELECT unit_key, node, state FROM dq_ctl.units ORDER BY unit_key"),
        "results": _q(dsn, "SELECT DISTINCT ON (unit_key, rule_id) unit_key, rule_id, disposition, evaluated, failed "
                           "FROM dq_ctl.results ORDER BY unit_key, rule_id, eval_version DESC, id DESC"),
        "latest": _q(dsn, "SELECT DISTINCT ON (unit_key) unit_key, to_state, payload::text FROM dq_ctl.unit_events "
                          "ORDER BY unit_key, state_version DESC"),
        "board": _q(dsn, "SELECT batch_id, seat, kind, subject_type, subject_id, template, body::text, entry_hash "
                         "FROM dq_ctl.agent_blackboard ORDER BY seq"),
        # file ids inside a measure name are random surrogates (intake uuid): normalised
        "abc": _q(dsn, "SELECT batch_id, hop, regexp_replace(measure, 'F[0-9a-f]{12}', 'F*'), disposition, residual "
                       "FROM dq_ctl.abc ORDER BY id"),
    }


def test_trace_shape_digest_stable_across_two_fresh_cps(tmp_path):
    failures0 = tracer.STATS["flush_failures"]
    digests = []
    for i in range(2):
        with _cp(tmp_path / str(i)) as (dsn, rt):
            _nights(rt, 4)
            assert _q(dsn, "SELECT count(*) FROM dq_ctl.spans")[0][0] > 0
            assert _q(dsn, "SELECT count(*) FROM dq_ctl.release_checks")[0][0] > 0     # flushes were not lost
            digests.append(_shape(dsn))
    assert digests[0] == digests[1]
    assert tracer.STATS["flush_failures"] == failures0, tracer.STATS


def test_tracing_off_and_on_give_identical_outcomes(tmp_path, monkeypatch):
    """I-8 over the FULL w1 load: the outcome (420 / 291 / 47) and every observed row are identical with tracing off."""
    out = {}
    for mode in ("1", "0"):
        monkeypatch.setenv("DQ_TRACE", mode)
        with _cp(tmp_path / mode) as (dsn, rt):
            _nights(rt)
            out[mode] = _outcome(dsn)
            states = dict(_q(dsn, "SELECT state, count(*) FROM dq_ctl.units GROUP BY 1"))
            runs = _q(dsn, "SELECT count(*) FROM dq_ctl.runs")[0][0]
        assert states == EXPECTED, (mode, states)
        assert (runs > 0) is (mode == "1")
    assert out["1"] == out["0"]


def test_trace_rows_cannot_be_forged_by_other_roles(fresh_cp_dsn):
    """I8: no role but dq_trace_svc inserts into ANY trace table (all of them), and the writer cannot change a row."""
    others = [r for r in SERVICE_ROLES if r != "dq_trace_svc"]
    for role in others:
        for t in TRACE_TABLES:
            with psycopg.connect(**{**fresh_cp_dsn, "user": role}) as c:
                with pytest.raises(psycopg.errors.InsufficientPrivilege):
                    c.execute(f"INSERT INTO dq_ctl.{t} DEFAULT VALUES")
        with psycopg.connect(**{**fresh_cp_dsn, "user": role}) as c:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                c.execute("SELECT nextval('dq_ctl.trace_id_seq')")              # nor mint a trace id
    with psycopg.connect(**{**fresh_cp_dsn, "user": "dq_trace_svc"}) as c:
        c.execute("INSERT INTO dq_ctl.runs(trace_id,kind,state,config_digest,code_digest,deployment) "
                  "VALUES ('NIGHT-ZZ-20260101-a1','NIGHT','ZZ','c','c','test')")
        c.commit()
        for stmt in ("UPDATE dq_ctl.runs SET state='YY'", "DELETE FROM dq_ctl.runs"):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                c.execute(stmt)
            c.rollback()
    for t in TRACE_TABLES:                                       # append-only even for the admin
        with psycopg.connect(**fresh_cp_dsn) as c:
            with pytest.raises(psycopg.Error, match="append-only"):
                c.execute(f"TRUNCATE dq_ctl.{t} CASCADE")


def test_trace_columns_outside_bb_and_audit_hash(tmp_path):
    with _cp(tmp_path) as (dsn, rt):
        _nights(rt, 2)
        with psycopg.connect(**dsn) as c:
            assert c.execute("SELECT dq_ctl.bb_verify()").fetchone()[0] is None          # the board chain holds
            rows = c.execute("SELECT seq, prev_hash, row_hash FROM dq_ctl.audit_log ORDER BY seq").fetchall()
            assert all(b[1] == a[2] for a, b in zip(rows, rows[1:]))                     # the audit chain holds
            assert c.execute("SELECT count(*) FROM dq_ctl.agent_blackboard WHERE trace_id IS NOT NULL"
                             ).fetchone()[0] > 0
            assert c.execute("SELECT count(*) FROM dq_ctl.unit_events WHERE payload ?| "
                             "ARRAY['trace_id','span_id','code_sha256']").fetchone()[0] == 0
            code = c.execute("SELECT DISTINCT code_sha256 FROM dq_ctl.results").fetchall()
            assert code == [(tracer.code_sha256(),)]                                    # stamped, one version
            # every correlated trace id that names a recorded root is a real root (others: an unrecorded idle cycle)
            assert c.execute("SELECT count(*) FROM dq_ctl.unit_events e WHERE e.trace_id LIKE 'NIGHT-%' AND NOT EXISTS "
                             "(SELECT 1 FROM dq_ctl.runs r WHERE r.trace_id = e.trace_id)").fetchone()[0] == 0


def _row_counts(dsn):
    tables = (*TRACE_TABLES, "unit_events", "results", "manifest_versions", "agent_blackboard")
    return {t: _q(dsn, f"SELECT count(*) FROM dq_ctl.{t}")[0][0] for t in tables}


def test_idle_publish_cycle_writes_nothing(tmp_path):
    """I9: a worker cycle that changes nothing (the demo worker every 3 s) writes zero rows, also while units wait on
    a dependency (they are not re-evaluated while their dependency versions are unchanged)."""
    with _cp(tmp_path) as (dsn, rt):
        _nights(rt, 6)
        rt.verify_and_publish()
        assert _q(dsn, "SELECT count(*) FROM dq_ctl.units WHERE state='PENDING_DEPENDENCY'")[0][0] > 0
        before = _row_counts(dsn)
        for _ in range(3):
            rt.verify_and_publish()
        assert _row_counts(dsn) == before


def test_publish_cycle_root_parented_or_standalone(tmp_path):
    with _cp(tmp_path) as (dsn, rt):
        _nights(rt, 1)
        (night_id,) = _q(dsn, "SELECT trace_id FROM dq_ctl.runs WHERE kind='NIGHT'")[0]
        assert re.fullmatch(r"NIGHT-ZZ-20260101-a1-[0-9]+", night_id)
        inside = _q(dsn, "SELECT trace_id, parent_trace_id FROM dq_ctl.runs WHERE kind='PUBLISH_CYCLE'")
        assert inside and all(p == night_id for _, p in inside)                        # parented to the NIGHT
        assert all(t.startswith("PCY-ZZ-e") for t, _ in inside)
        with rt.cp.as_role("dq_eval_svc") as c:                                         # re-open one HELD unit, as
            u = c.execute("SELECT unit_key, state_version FROM dq_ctl.units WHERE state='HELD' "   # an applied change
                          "ORDER BY unit_key LIMIT 1").fetchone()                       # does, outside any root
            rt.cp.transition(c, u["unit_key"], u["state_version"], "RE_EVALUATE_PENDING", "RULESET", "dq_eval_svc",
                             {"reason": "test"})
        n0 = len(inside)
        rt.verify_and_publish()                                                         # the demo worker: standalone
        rows = _q(dsn, "SELECT trace_id, parent_trace_id FROM dq_ctl.runs WHERE kind='PUBLISH_CYCLE' ORDER BY started_at")
        assert len(rows) >= n0 + 1 and rows[-1][1] is None
        night = _q(dsn, "SELECT kind, parent_trace_id, attempt FROM dq_ctl.runs WHERE trace_id=%s", night_id)
        assert night == [("NIGHT", None, 1)]
        names = {r[0] for r in _q(dsn, "SELECT name FROM dq_ctl.spans WHERE trace_id=%s", night_id)}
        assert {"file", "intake", "seal", "abc", "monitor", "missing_deliveries", "summary"} <= names
        assert any(n.startswith("rule:") for n in names)
        subjects = {r[0] for r in _q(dsn, "SELECT subject FROM dq_ctl.spans WHERE trace_id=%s AND name='file'",
                                     night_id)}
        assert subjects == {"feed:IE_EXTRACT", "feed:X12_834"}                         # the feed, never a file name
        # F-34: the night's root carries the union of the authorities it touched; a scoped reviewer finds it
        scope = _q(dsn, "SELECT authorities FROM dq_ctl.run_scopes WHERE trace_id=%s", night_id)[0][0]
        assert "AUTH_MEDICAID" in scope
        with psycopg.connect(**{**dsn, "user": "dq_api"}) as c:
            c.execute("SELECT dq_ctl.set_scope(ARRAY['AUTH_SNAP'])")
            assert c.execute("SELECT count(*) FROM dq_ctl.runs WHERE trace_id=%s", (night_id,)).fetchone()[0] == \
                (1 if "AUTH_SNAP" in scope else 0)
            spans = c.execute("SELECT authorities FROM dq_ctl.spans WHERE trace_id=%s", (night_id,)).fetchall()
            assert all(set(a) <= {"AUTH_SNAP"} and a for (a,) in spans)                # only in-scope steps
            c.execute("SELECT dq_ctl.set_scope(ARRAY['AUTH_MEDICAID'])")
            med = c.execute("SELECT authorities FROM dq_ctl.spans WHERE trace_id=%s", (night_id,)).fetchall()
            assert med and all(set(a) <= {"AUTH_MEDICAID"} and a for (a,) in med)


def test_decision_root_wraps_the_decision_and_its_publish_cycle(tmp_path):
    """A person's decision runs under a DECISION root (DEC-J<step-up jti>), readable in the unit's scope; the decision
    row is correlated with it and the publish cycle that follows (after_decision) is its child."""
    from fastapi.testclient import TestClient

    from dqagent.api.app import create_app

    from .fixtures_w1 import ie_file, person
    with _cp(tmp_path) as (dsn, rt):
        p = tmp_path / "ie.txt"
        p.write_bytes(ie_file([person("P1", "M0001")], trailer=7))                 # ABC fail -> held for people
        rt.run_pipeline("p4_eligibility_ie", "B1", [("IE", p)], ["bronze.ie_segment"])
        api = TestClient(create_app(rt))
        H = {"Authorization": f"Bearer {rt.idp.token('maker')}"}
        uk = _q(dsn, "SELECT unit_key FROM dq_ctl.units WHERE state='HELD'")[0][0]
        card = api.get(f"/api/v1/units/{uk}", headers=H).json()
        chal = api.post(f"/api/v1/units/{uk}/challenge", json={"decision": "RELEASE"}, headers=H).json()["challenge"]
        tok = rt.idp.token("maker", step_up=True, challenge=chal)
        from dqagent.hitl.idp import verify
        jti = verify(tok, rt.idp.jwks())["jti"]
        body = {"decision": "RELEASE", "rationale": "Reviewed the evidence on the card for this test.",
                "expected_state_version": card["state_version"], "bound_fingerprint": card["fingerprint"],
                "step_up_token": tok}
        r = api.post(f"/api/v1/units/{uk}/decide", json=body, headers=H)
        assert r.status_code == 200
        run = _q(dsn, "SELECT trace_id, kind, parent_trace_id, person_id, authorities FROM dq_ctl.runs "
                      "WHERE kind='DECISION'")
        (tid, *rest), = run
        assert re.fullmatch(rf"DEC-J{re.escape(jti)}-[0-9]+", tid) and rest == ["DECISION", None, "E100",
                                                                              ["AUTH_MEDICAID"]]
        assert r.json()["trace_id"] == tid                                              # F-34: returned by /decide
        assert _q(dsn, "SELECT trace_id FROM dq_ctl.decisions") == [(tid,)]
        assert _q(dsn, "SELECT event FROM dq_ctl.run_events WHERE trace_id=%s ORDER BY id", tid) == \
            [("STARTED",), ("COMPLETED",)]
        did = _q(dsn, "SELECT decision_id FROM dq_ctl.decisions")[0][0]               # F-34: unit / decision -> runs
        assert [x["trace_id"] for x in rt.runs_for(["AUTH_MEDICAID"], decision_id=did)] == [tid]
        assert tid in {x["trace_id"] for x in rt.runs_for(["AUTH_MEDICAID"], unit_key=uk)}
        assert rt.runs_for(["AUTH_SNAP"], decision_id=did) == []
        with psycopg.connect(**{**dsn, "user": "dq_api"}) as c:                       # readable in scope
            c.execute("SELECT dq_ctl.set_scope(ARRAY['AUTH_MEDICAID'])")
            assert c.execute("SELECT count(*) FROM dq_ctl.runs WHERE kind='DECISION'").fetchone()[0] == 1


def test_trace_switch_is_honoured_only_in_the_test_deployment(monkeypatch):
    """I7: DQ_TRACE=0 turns tracing off in the test deployment only; any other deployment stays traced."""
    monkeypatch.setenv("DQ_TRACE", "0")
    assert tracer.enabled("test") is False
    for d in ("demo", "pilot", "production"):
        assert tracer.enabled(d) is True
    monkeypatch.setenv("DQ_TRACE", "1")
    assert tracer.enabled("test") is True
