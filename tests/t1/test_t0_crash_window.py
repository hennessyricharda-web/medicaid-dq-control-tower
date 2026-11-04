"""Part F T0, F-01 and R4-1: a crash between the seal and the ABC never fails open, and recovery.

Before the fix a batch sealed without its LANDING->GATED ABC read as "ABC passed" (no rows = no failure) and its units
could release. Now: the one definition dq_ctl.batches_abc_missing makes the evaluator skip the batch, publisher clause
12 refuse it, and a resumed night stop with SimRecoveryNeeded until an operator recovers it (step-up, operations
duty). Recovery recomputes the ABC from the frozen load set; it refuses when the data plane has lost those loads.
"""
import psycopg
import pytest
from fastapi.testclient import TestClient

import dqagent.runtime as runtime_mod
from dqagent.api.app import create_app
from dqagent.runtime import (
    DEFAULT_USERS,
    RecoveryDataLost,
    RecoveryRefused,
    SimRecoveryNeeded,
    seed_routing,
    synthetic_nights,
)

from .fixtures_w1 import ie_file, person
from .test_parte_redteam_fixes import W1, _sim
from .w1_harness import Slice

BATCH = "B-ELG-1"


def H(rt, user, **kw):
    return {"Authorization": f"Bearer {rt.idp.token(user, **kw)}"}


def _elig(s, tmp, batch=BATCH, trailer=None):
    p = tmp / f"{batch}.txt"
    p.write_bytes(ie_file([person("P1", "M0001"), person("P2", "M0002")], trailer))
    return s.run_pipeline("p4_eligibility_ie", batch, [("IE", p)], ["bronze.ie_segment"])


def _crash_once(monkeypatch):
    """landing_to_gated_abc raises on its FIRST call only: the crash between the seal and the ABC record."""
    real = runtime_mod.landing_to_gated_abc
    calls = {"n": 0}

    def boom(*a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("simulated crash between seal and ABC")
        return real(*a, **k)
    monkeypatch.setattr(runtime_mod, "landing_to_gated_abc", boom)


def _crashed(dsn, tmp, monkeypatch, dp_path):
    s = Slice(dsn, dp_path=dp_path)
    _crash_once(monkeypatch)
    with pytest.raises(RuntimeError, match="between seal and ABC"):
        _elig(s, tmp)
    return s


def _restart(old, dsn, dp_path):
    """A new process: the old runtime's data-plane handle is closed, a new runtime opens the same CP (and DP path)."""
    old.dp.con.close()
    return Slice(dsn, dp_path=dp_path)


def _recover_api(api, s, reason, user="maker"):
    ch = api.post(f"/api/v1/recovery/batches/{BATCH}/challenge", headers=H(s, user), json={"reason": reason})
    assert ch.status_code == 200, ch.text
    tok = s.idp.token(user, step_up=True, challenge=ch.json()["challenge"])
    return api.post(f"/api/v1/recovery/batches/{BATCH}/abc", headers=H(s, user),
                    json={"reason": reason, "step_up_token": tok})


def _batch_states(dsn, batch=BATCH):
    with psycopg.connect(**dsn) as c:
        return dict(c.execute("SELECT state, count(*) FROM dq_ctl.units WHERE batch_id=%s GROUP BY 1",
                              (batch,)).fetchall())


def _hops(dsn, batch=BATCH):
    with psycopg.connect(**dsn) as c:
        return [r[0] for r in c.execute("SELECT hop FROM dq_ctl.abc WHERE batch_id=%s ORDER BY id", (batch,)).fetchall()]


def test_crash_after_seal_before_landing_abc_never_releases(fresh_cp_dsn, tmp_path, monkeypatch):
    dp = str(tmp_path / "dp.duckdb")
    s = _crashed(fresh_cp_dsn, tmp_path, monkeypatch, dp)
    assert _batch_states(fresh_cp_dsn) == {"SEALED": 2}
    assert "LANDING->GATED" not in _hops(fresh_cp_dsn) and _hops(fresh_cp_dsn)     # intake ABC was recorded first
    s = _restart(s, fresh_cp_dsn, dp)
    s.after_decision()
    s.after_decision()
    assert _batch_states(fresh_cp_dsn) == {"SEALED": 2}                          # never evaluated, never released
    out = s.reevaluate_pending()
    assert out and all(v.get("x_abc_missing") for k, v in out.items() if k.startswith(BATCH + ":"))
    with s.cp.as_role("dq_publish_svc") as c:                                     # clause 12, the publisher's own check
        u = c.execute("SELECT * FROM dq_ctl.units WHERE batch_id=%s LIMIT 1", (BATCH,)).fetchone()
        ok, why = s.publisher.can_release(c, dict(u))
    assert not ok and "12: batch ABC incomplete (recovery needed)" in why


def test_resume_raises_recovery_needed(tmp_path, monkeypatch):
    with _sim(tmp_path) as (rt, dsn):
        _crash_once(monkeypatch)
        with pytest.raises(RuntimeError, match="between seal and ABC"):
            rt.sim_run_next("E100")
        night, files = synthetic_nights(W1, seed_routing("ZZ"))[0]
        with pytest.raises(SimRecoveryNeeded, match="sealed but ABC was never recorded") as e:
            rt.sim_run_next("E100")
        assert f"/api/v1/recovery/batches/B-{files[0].stem}/abc" in str(e.value)
        r = TestClient(create_app(rt)).post("/api/v1/sim/run-next", headers=H(rt, "maker"))
        assert r.status_code == 409 and "ABC was never recorded" in r.text
        assert _batch_states(dsn, "B-" + files[0].stem).keys() == {"SEALED"}


def test_recovery_records_abc_then_evaluates_and_can_release(fresh_cp_dsn, tmp_path, monkeypatch):
    dp = str(tmp_path / "dp.duckdb")
    s = _restart(_crashed(fresh_cp_dsn, tmp_path, monkeypatch, dp), fresh_cp_dsn, dp)
    out = s.recover_batch_abc(BATCH, "E100", reason="operator recovery after a crash")
    assert out["batch_id"] == BATCH and out["abc_rows"] == 1 and out["evaluated_units"] == 2
    assert _hops(fresh_cp_dsn)[-1] == "LANDING->GATED"
    with s.cp.as_role("dq_seal_svc") as c:
        assert not c.execute("SELECT 1 FROM dq_ctl.batches_abc_missing WHERE batch_id=%s", (BATCH,)).fetchone()
    s.verify_and_publish()
    assert _batch_states(fresh_cp_dsn) == {"RELEASED": 2}                        # w1 seed: L4, clean -> mode A


def test_recovery_succeeds_when_landing_intact(fresh_cp_dsn, tmp_path, monkeypatch):
    """R4-1 negative control: the same runtime (landing intact), no restart."""
    s = _crashed(fresh_cp_dsn, tmp_path, monkeypatch, str(tmp_path / "dp.duckdb"))
    assert s._lost_loads(BATCH) == {}
    assert s.recover_batch_abc(BATCH, "E100")["abc_rows"] == 1


def test_recovery_refuses_on_fresh_dataplane(fresh_cp_dsn, tmp_path, monkeypatch):
    """R4-1: a crash leaves the batch sealed without ABC; the restart has a NEW in-memory data plane, so the frozen
    loads are gone. An empty data plane must never read as balanced: 409, no ABC row, nothing released."""
    s = _crashed(fresh_cp_dsn, tmp_path, monkeypatch, ":memory:")
    s = Slice(fresh_cp_dsn)                                                      # restart: fresh :memory: data plane
    with pytest.raises(RecoveryDataLost, match="no longer in the data plane"):
        s.recover_batch_abc(BATCH, "E100")
    api = TestClient(create_app(s))
    r = _recover_api(api, s, "operator recovery after a crash")
    assert r.status_code == 409 and r.json()["detail"]["status"] == "RECOVERY_DATA_LOST"
    assert "LANDING->GATED" not in _hops(fresh_cp_dsn)
    s.after_decision()
    assert "RELEASED" not in _batch_states(fresh_cp_dsn)
    with s.cp.as_role("dq_seal_svc") as c:
        items = c.execute("SELECT kind, feed, authority FROM dq_ctl.feed_items WHERE batch_id=%s", (BATCH,)).fetchall()
    assert [dict(i) for i in items] == [{"kind": "RECOVERY_DATA_LOST", "feed": "RECOVERY", "authority": "AUTH_INTAKE"}]


def test_recovery_refused_when_abc_complete(fresh_cp_dsn, tmp_path):
    s = Slice(fresh_cp_dsn, dp_path=str(tmp_path / "dp.duckdb"))
    _elig(s, tmp_path)
    with pytest.raises(RecoveryRefused, match="has its ABC"):
        s.recover_batch_abc(BATCH, "E100")
    with pytest.raises(RecoveryRefused, match="not a sealed batch"):
        s.recover_batch_abc("B-NOPE", "E100")
    api = TestClient(create_app(s))
    r = _recover_api(api, s, "operator recovery attempt")
    assert r.status_code == 409 and r.json()["detail"]["status"] == "RECOVERY_NOT_APPLICABLE"
    assert _hops(fresh_cp_dsn).count("LANDING->GATED") == 1


def test_recovery_needs_operations_duty_and_a_single_use_step_up(fresh_cp_dsn, tmp_path, monkeypatch):
    """Red team I5: the challenge is a random server-side row, bound to batch, person and reason, expiring, and
    consumed exactly once by the database. Recovery is API-only (no CLI path)."""
    import time

    import psycopg as pg
    dp = str(tmp_path / "dp.duckdb")
    s = _restart(_crashed(fresh_cp_dsn, tmp_path, monkeypatch, dp), fresh_cp_dsn, dp)
    api = TestClient(create_app(s))
    url, chal = f"/api/v1/recovery/batches/{BATCH}/abc", f"/api/v1/recovery/batches/{BATCH}/challenge"
    reason = "operator recovery after a crash"

    def call(tok, why=reason):
        return api.post(url, headers=H(s, "maker"), json={"reason": why, "step_up_token": tok})
    assert api.post(chal, headers=H(s, "checker"), json={"reason": reason}).status_code == 403   # no operations duty
    assert api.post(url, headers=H(s, "checker"), json={"reason": reason}).status_code == 403
    assert api.post(chal, headers=H(s, "maker"), json={"reason": "short"}).status_code == 422
    assert api.post(url, headers=H(s, "maker"), json={"reason": reason}).status_code == 428     # no step-up
    ch = api.post(chal, headers=H(s, "maker"), json={"reason": reason}).json()["challenge"]
    ch2 = api.post(chal, headers=H(s, "maker"), json={"reason": reason}).json()["challenge"]
    assert ch != ch2 and len(ch) == 64                                            # random, not derivable
    other = api.post("/api/v1/recovery/batches/B-OTHER/challenge", headers=H(s, "maker"),
                     json={"reason": reason}).json()["challenge"]
    assert call(s.idp.token("maker")).status_code == 403                          # a session token, not a step-up
    assert call(s.idp.token("maker", step_up=True, challenge=other)).status_code == 403          # another batch
    assert call(s.idp.token("checker", step_up=True, challenge=ch)).status_code == 403          # another person
    assert call(s.idp.token("maker", step_up=True, challenge="f" * 64)).status_code == 403      # never issued
    assert call(s.idp.token("maker", step_up=True, challenge=ch),
                "a different reason than the one issued").status_code == 403                    # another reason
    with s.cp.as_role("dq_api") as c:                                             # an expired challenge
        old = c.execute("SELECT dq_ctl.recovery_challenge_issue(%s,'E100',%s,1) AS ch", (BATCH, reason)).fetchone()["ch"]
    time.sleep(1.2)
    assert call(s.idp.token("maker", step_up=True, challenge=old)).status_code == 403
    assert _batch_states(fresh_cp_dsn) == {"SEALED": 2}                          # nothing ran on a refused call
    tok = s.idp.token("maker", step_up=True, challenge=ch)
    r = call(tok)
    assert r.status_code == 200 and r.json() == {"batch_id": BATCH, "abc_rows": 1, "evaluated_units": 2}
    again = call(tok)
    assert again.status_code == 409 and "already used" in again.text             # consumed exactly once
    with pg.connect(**{**fresh_cp_dsn, "user": "dq_api"}) as c:                   # no back door to the rows
        with pytest.raises(pg.errors.InsufficientPrivilege):
            c.execute("SELECT * FROM dq_ctl.recovery_challenges")
    from dqagent.cli.main import main
    with pytest.raises(SystemExit):                                               # no CLI recovery path
        main(["recover", "--batch", BATCH])


def test_recovery_refuses_a_half_written_load(fresh_cp_dsn, tmp_path, monkeypatch):
    """Red team C1: a load that is still present but lost rows (a half-written snapshot) differs from its frozen
    landing digest: RECOVERY_DATA_LOST, never an ABC that balances 0 against 0."""
    dp = str(tmp_path / "dp.duckdb")
    s = _crashed(fresh_cp_dsn, tmp_path, monkeypatch, dp)
    s.dp.execute("DELETE FROM landing.ie_segment WHERE _dq_batch_id = ? AND rowid = "
                 "(SELECT min(rowid) FROM landing.ie_segment WHERE _dq_batch_id = ?)", [BATCH, BATCH])
    s.dp.execute(f"DELETE FROM bronze.ie_segment WHERE _dq_batch_id = '{BATCH}' AND _dq_row_seq = "
                 f"(SELECT min(_dq_row_seq) FROM bronze.ie_segment WHERE _dq_batch_id = '{BATCH}')")
    s = _restart(s, fresh_cp_dsn, dp)
    assert s._lost_loads(BATCH)                                                  # present, but not intact
    with pytest.raises(RecoveryDataLost):
        s.recover_batch_abc(BATCH, "E100")
    assert "LANDING->GATED" not in _hops(fresh_cp_dsn)
    s.after_decision()
    assert _batch_states(fresh_cp_dsn) == {"SEALED": 2}


def test_recovery_refuses_a_load_retagged_to_another_batch(fresh_cp_dsn, tmp_path, monkeypatch):
    """Red team C1: the load's rows now carry another _dq_batch_id; the presence and digest checks are filtered on
    THIS batch, so the load reads as lost (the ABC count is batch-filtered too and would have balanced 0 = 0)."""
    dp = str(tmp_path / "dp.duckdb")
    s = _crashed(fresh_cp_dsn, tmp_path, monkeypatch, dp)
    s.dp.execute("UPDATE landing.ie_segment SET _dq_batch_id = 'B-OTHER' WHERE _dq_batch_id = ?", [BATCH])
    s.dp.execute("UPDATE bronze.ie_segment SET _dq_batch_id = 'B-OTHER' WHERE _dq_batch_id = ?", [BATCH])
    s = _restart(s, fresh_cp_dsn, dp)
    with pytest.raises(RecoveryDataLost):
        s.recover_batch_abc(BATCH, "E100")
    assert "LANDING->GATED" not in _hops(fresh_cp_dsn)


def test_landing_gated_abc_is_recorded_once_across_processes(fresh_cp_dsn, tmp_path, monkeypatch):
    """Red team I5: two processes recovering at once cannot both record the ABC (unique per batch and relation)."""
    from dqagent.engine.run import record_abc
    dp = str(tmp_path / "dp.duckdb")
    s = _restart(_crashed(fresh_cp_dsn, tmp_path, monkeypatch, dp), fresh_cp_dsn, dp)
    lg, _ = runtime_mod.landing_to_gated_abc(s.cp, s.dp, BATCH, ["bronze.ie_segment"])
    record_abc(s.cp, BATCH, lg)                                                  # the "other process" wins the race
    with pytest.raises(psycopg.errors.UniqueViolation):
        record_abc(s.cp, BATCH, lg)
    assert _hops(fresh_cp_dsn).count("LANDING->GATED") == 1


def test_seats_post_once_on_resume(tmp_path):
    """A crash after the INTAKE and EVALUATOR seats posted for a batch but before TRIAGE: the resumed night runs only
    the seat that has no row for the batch, so no seat posts twice."""
    with _sim(tmp_path) as (rt, dsn):
        night, files = synthetic_nights(W1, seed_routing("ZZ"))[0]
        real = rt.seats.triage
        calls = {"n": 0}

        def boom(batch_id):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("simulated crash before the TRIAGE seat")
            return real(batch_id)
        rt.seats.triage = boom
        with pytest.raises(RuntimeError, match="TRIAGE"):
            rt.sim_run_next("E100")
        batch = "B-" + files[0].stem
        with psycopg.connect(**dsn) as c:
            before = {r[0] for r in c.execute("SELECT DISTINCT seat FROM dq_ctl.agent_blackboard WHERE batch_id=%s",
                                              (batch,)).fetchall()}
        out = rt.run_night(W1, night, files, last=False, record=True, person_id="E100")
        resumed = out["file_results"][files[0].name]
        assert resumed["resumed"] and resumed["seats"] == [x for x in ("INTAKE", "EVALUATOR", "TRIAGE")
                                                            if x not in before]
        assert "TRIAGE" in resumed["seats"]
        with psycopg.connect(**dsn) as c:
            dup = c.execute("SELECT seat, template, subject_id, count(*) FROM dq_ctl.agent_blackboard WHERE batch_id=%s "
                            "GROUP BY 1, 2, 3 HAVING count(*) > 1", (batch,)).fetchall()
        assert dup == []
        rt.seats.triage = real


def test_intake_abc_row_order_unchanged(fresh_cp_dsn, tmp_path):
    """Intake ABC rows are recorded before the seal, LANDING->GATED after it: the stored order (row ids) is the same
    as before the change (intake rows first, then LANDING->GATED), and equals the order run_pipeline returns."""
    s = Slice(fresh_cp_dsn, users=DEFAULT_USERS)
    r = _elig(s, tmp_path, trailer=9)                                            # a failing intake hop too
    stored = _hops(fresh_cp_dsn)
    assert stored == [h["hop"] for h in r["abc"]]
    assert stored[-1] == "LANDING->GATED" and "LANDING->GATED" not in stored[:-1] and len(stored) > 1


def test_a_transient_dataplane_error_is_retryable_not_data_lost(fresh_cp_dsn, tmp_path, monkeypatch):
    """R5 L3: a data-plane read error that is not proof of loss raises RecoveryTransient (api 503 RECOVERY_RETRY),
    opens no feed item and records nothing; the batch stays held. Only a proven mismatch or absence is DATA_LOST."""
    import duckdb

    from dqagent.runtime import RecoveryTransient
    dp = str(tmp_path / "dp.duckdb")
    s = _restart(_crashed(fresh_cp_dsn, tmp_path, monkeypatch, dp), fresh_cp_dsn, dp)
    real = s.dp.fetchall

    def flaky(sql, *a, **k):
        if "landing." in sql:
            raise duckdb.IOException("simulated transient read error")
        return real(sql, *a, **k)
    s.dp.fetchall = flaky
    with pytest.raises(RecoveryTransient, match="retry"):
        s.recover_batch_abc(BATCH, "E100")
    api = TestClient(create_app(s))
    r = _recover_api(api, s, "operator recovery after a crash")
    assert r.status_code == 503 and r.json()["detail"]["status"] == "RECOVERY_RETRY"
    with s.cp.as_role("dq_seal_svc") as c:
        assert not c.execute("SELECT 1 FROM dq_ctl.feed_items WHERE batch_id=%s", (BATCH,)).fetchone()
    assert "LANDING->GATED" not in _hops(fresh_cp_dsn)
    s.dp.fetchall = real
    assert s.recover_batch_abc(BATCH, "E100")["abc_rows"] == 1                  # the retry succeeds


def test_recovery_challenge_refuses_null_arguments(fresh_cp_dsn):
    """R5 L2: a NULL batch, person or reason never passes the binding check (nor can it be issued)."""
    import psycopg as pg
    Slice(fresh_cp_dsn)
    reason = "operator recovery after a crash"
    with pg.connect(**{**fresh_cp_dsn, "user": "dq_api"}) as c:
        ch = c.execute("SELECT dq_ctl.recovery_challenge_issue(%s,'E100',%s,60)", (BATCH, reason)).fetchone()[0]
        c.commit()
    for args in ((ch, None, "E100", reason), (ch, BATCH, None, reason), (ch, BATCH, "E100", None),
                 (None, BATCH, "E100", reason)):
        with pg.connect(**{**fresh_cp_dsn, "user": "dq_api"}) as c:
            with pytest.raises(pg.errors.InsufficientPrivilege):
                c.execute("SELECT dq_ctl.recovery_challenge_consume(%s,%s,%s,%s)", args)
    for args in ((None, "E100", reason), (BATCH, None, reason), (BATCH, "E100", None), (BATCH, "E100", reason, None)):
        with pg.connect(**{**fresh_cp_dsn, "user": "dq_api"}) as c:
            with pytest.raises(pg.errors.CheckViolation):
                c.execute("SELECT dq_ctl.recovery_challenge_issue(%s,%s,%s,%s)", (*args, 60)[:4] if len(args) == 3
                          else args)


def test_seal_refuses_a_load_whose_rows_carry_another_batch(fresh_cp_dsn, tmp_path):
    """R5 L4: the seal and the recovery digest the same batch-filtered rows. A load cannot span two batches (intake
    registers a fresh load id per batch); a row of the load tagged with another batch is refused at the seal."""
    from dqagent.engine.seal import SealIntegrityError
    s = Slice(fresh_cp_dsn)
    real = s.sealer.seal

    def tamper_then_seal(batch_id, pipeline_id, relations):
        s.dp.execute("UPDATE landing.ie_segment SET _dq_batch_id = 'B-OTHER' WHERE _dq_batch_id = ? AND rowid = "
                     "(SELECT min(rowid) FROM landing.ie_segment WHERE _dq_batch_id = ?)", [batch_id, batch_id])
        return real(batch_id, pipeline_id, relations)
    s.sealer.seal = tamper_then_seal
    with pytest.raises(SealIntegrityError, match="another batch"):
        _elig(s, tmp_path)


def test_each_recovery_refusal_writes_exactly_one_guardrail_event(fresh_cp_dsn, tmp_path, monkeypatch):
    """N-T0-1: one writer (explain_service.record_guardrail), one RECOVERY_REFUSED event per refusal, correlated with
    the refusal's RECOVER root and engine-wide (no authorities: audit / operations only)."""
    import psycopg as pg
    _crashed(fresh_cp_dsn, tmp_path, monkeypatch, ":memory:")
    s = Slice(fresh_cp_dsn)                                                      # fresh data plane: loads are gone
    for _ in range(2):
        with pytest.raises(RecoveryDataLost):
            s.recover_batch_abc(BATCH, "E100")
    with pg.connect(**fresh_cp_dsn) as c:
        rows = c.execute("SELECT trace_id, subject, seat, authorities, detail FROM dq_ctl.guardrail_events "
                         "WHERE code='RECOVERY_REFUSED' ORDER BY id").fetchall()
    assert [r[1:] for r in rows] == [(f"batch:{BATCH}", "recovery", [], {"loads_lost": 1})] * 2
    import re
    assert all(re.fullmatch(rf"RCV-{BATCH}-[0-9]+", r[0]) for r in rows) and rows[0][0] != rows[1][0]
