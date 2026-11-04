"""Part E post-build red team fixes (E4, E6, E7, E8, the nits) and the live-smoke defects (L1, L2, L4), over the
real control plane. E1/E2 are in tests/t0/test_explainer_adversarial.py; E3 in test_parte_api / test_parte_cp; E5 in
test_sim_nightly (it reuses the simulator fixtures)."""
import contextlib
import json
import os
import uuid
from pathlib import Path

import psycopg
import pytest
from fastapi.testclient import TestClient

from dqagent.api.app import create_app
from dqagent.cp import schema
from dqagent.cp.agent_schema import DQ_AGENT
from dqagent.obs import tracer
from dqagent.runtime import DEFAULT_USERS, SimRecoveryNeeded, seed_routing, synthetic_nights

from .evidence import grant_evidence, record_run
from .fixtures_w1 import ie_file, person
from .w1_harness import Slice

PG = dict(host=os.environ.get("DQ_PG_HOST", "/tmp"), port=int(os.environ.get("DQ_PG_PORT", "55432")),
          user=os.environ.get("DQ_PG_USER", "dq_gov"))
W1 = Path(__file__).resolve().parents[2] / "synthetic" / "out" / "w1"
AGENT = DQ_AGENT


def H(rt, user, **kw):
    return {"Authorization": f"Bearer {rt.idp.token(user, **kw)}"}


@contextlib.contextmanager
def _sim(tmp):
    """A fresh CP database and a nightly-mode runtime over w1 (nothing run yet)."""
    try:
        admin = psycopg.connect(dbname="postgres", autocommit=True, **PG)
    except Exception as e:  # pragma: no cover
        pytest.skip(f"T1 Postgres not reachable: {e}")
    db = "dqcp_fix_" + uuid.uuid4().hex[:8]
    admin.execute(f"CREATE DATABASE {db}")
    dsn = dict(dbname=db, **PG)
    with psycopg.connect(**dsn) as c:
        schema.apply(c)
    try:
        rt = Slice(dsn, dp_path=str(tmp / f"{db}.duckdb"), users=DEFAULT_USERS)
        rt.sim_enable(W1, parte=True)
        yield rt, dsn
    finally:
        admin.execute(f"DROP DATABASE {db} WITH (FORCE)")
        admin.close()


def _elig(s, tmp, batch="B-ELG-1"):
    p = tmp / f"{batch}.txt"
    p.write_bytes(ie_file([person("P1", "M0001"), person("P2", "M0002")]))
    return s.run_pipeline("p4_eligibility_ie", batch, [("IE", p)], ["bronze.ie_segment"])


# -- E4 + R4-5: an evidence bundle is two-person (API); it is evidence for a promotion and never resets the record --
def test_eval_attestation_needs_a_second_person_and_never_resets_the_record(fresh_cp_dsn, tmp_path):
    s = Slice(fresh_cp_dsn)
    api = TestClient(create_app(s))
    for i in range(2):
        _elig(s, tmp_path, batch=f"B-ELG-{i}")
        s.verify_and_publish()

    def med():
        return next(a for a in api.get("/api/v1/autonomy", headers=H(s, "maker")).json()
                    if a["action_class"] == "RELEASE" and a["authority"] == "AUTH_MEDICAID")["track_record"]
    before = med()
    assert before["autonomous"] > 0 and before["eval_attestation"] is None
    assert before["versions"] and before["current_version"]["autonomous"] == before["autonomous"]
    det = record_run(s)
    old = {"label": "suite-9", "code_sha256": "c" * 64, "tests": 500, "failures": 0, "junit_sha256": "d" * 64}
    assert api.post("/api/v1/eval-attestations", headers=H(s, "maker"), json=old).status_code == 422   # old contract
    body = {"label": "suite-9", "code_sha256": tracer.code_sha256(), "det_run_id": det}
    att = api.post("/api/v1/eval-attestations", headers=H(s, "maker"), json=body)
    assert att.status_code == 200, att.text
    assert att.json() == {"id": att.json()["id"], "status": "PROPOSED", "halves": {"deterministic": True, "llm": False}}
    url = f"/api/v1/eval-attestations/{att.json()['id']}/confirm"
    assert api.post(url, headers=H(s, "maker")).status_code in (403, 409, 422)    # the proposer cannot confirm
    assert api.post(url, headers=H(s, "snap")).status_code == 403                 # no governance duty
    ok = api.post(url, headers=H(s, "checker"))
    assert ok.status_code == 200 and ok.json()["status"] == "CONFIRMED" and ok.json()["confirmed_by"] == "E200"
    assert ok.json()["halves"] == {"deterministic": True, "llm": False} and ok.json()["det_run_id"] == det
    after = med()
    assert after["autonomous"] == before["autonomous"]                 # R4-5: evidence, never a reset
    assert after["eval_attestation"]["confirmed_by"] == "E200" and after["eval_attestation"]["halves"]["llm"] is False
    scripted = record_run(s, suite="explainer", source_mode="SCRIPTED", prompt_version="p-x")
    bad = api.post("/api/v1/eval-attestations", headers=H(s, "maker"), json={**body, "llm_run_id": scripted})
    assert bad.status_code in (409, 422), bad.text                     # a scripted run is not evidence


# -- E6: an empty period never crashes; a failed night is recorded and resumed without double ingest -------------
def test_a_failed_night_is_recorded_and_the_retry_never_ingests_twice(tmp_path):
    with _sim(tmp_path) as (rt, dsn):
        night, files = synthetic_nights(W1, seed_routing("ZZ"))[0]
        real = rt.check_missing_deliveries
        calls = {"n": 0}

        def boom(*a, **k):                                 # fails AFTER the night's files were ingested and sealed
            if "now" not in k:                             # the worker cycle's own check (between files) is fine
                return real(*a, **k)
            calls["n"] += 1
            raise RuntimeError("simulated crash mid-night")
        rt.check_missing_deliveries = boom
        with pytest.raises(RuntimeError):
            rt.sim_run_next("E100")
        n0 = rt.sim_nights()["nights"][0]
        assert n0["status"] == "FAILED" and n0["error_class"] == "RuntimeError" and n0["run_id"] is None
        with psycopg.connect(**dsn) as c:
            ev = c.execute("SELECT event FROM dq_ctl.sim_night_events WHERE night_date=%s ORDER BY id",
                           (night,)).fetchall()
            files_before = c.execute("SELECT count(*) FROM dq_ctl.files").fetchone()[0]
            units_before = c.execute("SELECT count(*) FROM dq_ctl.units").fetchone()[0]
        assert [e[0] for e in ev] == ["STARTED", "FAILED"] and files_before == len(files) and units_before > 0
        rt.check_missing_deliveries = real
        out = rt.sim_run_next("E100")                       # resumes: sealed files are not ingested again
        assert out["night_date"] == night
        with psycopg.connect(**dsn) as c:
            assert c.execute("SELECT count(*) FROM dq_ctl.files").fetchone()[0] == files_before
            assert c.execute("SELECT count(*) FROM dq_ctl.units").fetchone()[0] == units_before
            assert c.execute("SELECT count(*) FROM (SELECT sha256 FROM dq_ctl.files GROUP BY 1 HAVING count(*) > 1) d"
                             ).fetchone()[0] == 0
            ev = [e[0] for e in c.execute("SELECT event FROM dq_ctl.sim_night_events WHERE night_date=%s ORDER BY id",
                                          (night,)).fetchall()]
        assert ev == ["STARTED", "FAILED", "RESUMED"]
        assert out["counts"]["units_created"]["n"] == units_before          # counted from the FIRST start's marks
        assert rt.sim_nights()["nights"][0]["status"] == "RUN"

        # a file that was only partly ingested (registered, never sealed) is reported, never loaded twice
        night2, files2 = synthetic_nights(W1, seed_routing("ZZ"))[1]
        real_seal = rt.sealer.seal

        def no_seal(*a, **k):
            raise RuntimeError("crash before seal")
        rt.sealer.seal = no_seal
        with pytest.raises(RuntimeError):
            rt.sim_run_next("E100")
        rt.sealer.seal = real_seal
        with psycopg.connect(**dsn) as c:
            reg = c.execute("SELECT count(*) FROM dq_ctl.files").fetchone()[0]
        with pytest.raises(SimRecoveryNeeded, match="partly ingested"):
            rt.sim_run_next("E100")
        with psycopg.connect(**dsn) as c:
            assert c.execute("SELECT count(*) FROM dq_ctl.files").fetchone()[0] == reg
        api = TestClient(create_app(rt))
        r = api.post("/api/v1/sim/run-next", headers=H(rt, "maker"))
        assert r.status_code == 409 and "partly ingested" in r.text
        assert rt.sim_nights()["nights"][1]["status"] == "FAILED"


# -- E7: a live-signalled period never feeds a later baseline; a pinned baseline stays frozen ------------------------
def test_a_live_signalled_period_is_excluded_from_the_next_baseline(fresh_cp_dsn):
    s = Slice(fresh_cp_dsn)
    rel, metric = "bronze.encounter_header", "rows"
    auth = s._relation_authority(rel)
    sid = s._series_id(rel, metric, "")
    with s.cp.as_role("dq_monitor_svc") as c:
        for i in range(1, 9):                                          # 8 clean history periods
            s._insert_metrics(c, f"HISTORY:P0{i}", [{"period": f"P0{i}", "relation": rel, "metric": metric,
                                                     "key": "", "value": 100 + (i % 3), "n": 100 + (i % 3)}])
        s._insert_metrics(c, "PERIOD:P09", [{"period": "P09", "relation": rel, "metric": metric, "key": "",
                                             "value": 900, "n": 900}])            # the live period that signalled
        s.seats.board.post(c, "PERIOD:P09", auth, "FINDING", "METRIC", sid, "SPC_SIGNAL",
                           {"relation": rel, "metric": metric, "period": "P09", "key": "", "value": 900, "lcl": 95,
                            "ucl": 106, "cl": 101, "rule": "R1", "baseline_ref": "a" * 64})
        s._insert_metrics(c, "PERIOD:P10", [{"period": "P10", "relation": rel, "metric": metric, "key": "",
                                             "value": 101, "n": 101}])
    r = next(x for x in s.monitor_status("P10") if x["relation"] == rel and x["metric"] == metric)
    assert "P09" in r["baseline"]["excluded_periods"] and "P09" not in r["baseline"]["periods"]
    assert r["baseline"]["status"] == "FROZEN" and r["status"] == "OK"     # P09's 900 never widened the limits
    assert s.monitor_exclusions()[(rel, metric, "")] == ["P09"]
    # a PINNED baseline is used as-is (frozen), whatever later periods do
    pinned = {**r["baseline"]}
    with s.cp.as_role("dq_monitor_svc") as c:
        c.execute("SELECT dq_ctl.mem_pin_baseline('METRIC', %s, %s, %s::jsonb, 'SPC_BASELINE')",
                  (sid, auth, json.dumps({"relation": rel, "metric": metric, "key": "", "n_periods": 8,
                                          "min_periods": 8, "baseline_ref": pinned["baseline_hash"],
                                          "template_args": pinned}, sort_keys=True, default=str)))
        s._insert_metrics(c, "PERIOD:P11", [{"period": "P11", "relation": rel, "metric": metric, "key": "",
                                             "value": 100, "n": 100}])
    r11 = next(x for x in s.monitor_status("P11") if x["relation"] == rel and x["metric"] == metric)
    assert r11["baseline_hash"] == pinned["baseline_hash"]


# -- E8: a veto demotes the unit's state only ------------------------------------------------------------------------
def test_a_veto_in_one_state_leaves_the_other_states_tier(fresh_cp_dsn, tmp_path):
    s = Slice(fresh_cp_dsn)
    with psycopg.connect(**fresh_cp_dsn) as adm:                      # a second state's tier (same agent)
        content = json.dumps({"AUTH_MEDICAID": "L4"})
        adm.execute("INSERT INTO dq_ctl.config_artifacts(artifact_id,version,state,kind,op,content,content_hash,cr_id) "
                    "VALUES (%s,1,'YY','AUTONOMY','PUT',%s::jsonb,'h','TEST-YY')",
                    (f"YY:autonomy:{AGENT}:RELEASE", content))
    assert s.registry.tier("YY", AGENT, "RELEASE", "AUTH_MEDICAID") == "L4"
    assert s.registry.tier("ZZ", AGENT, "RELEASE", "AUTH_MEDICAID") == "L4"
    _elig(s, tmp_path)
    u = next(x for x in s.units(state="EVALUATING") if "AUTH_MEDICAID" in x["authorities"])
    with s.cp.as_role("dq_eval_svc") as c:
        c.execute("INSERT INTO dq_ctl.veto_windows(unit_key,eval_version,window_s,policy_hash,state) "
                  "VALUES (%s,1,600,'p','ZZ')", (u["unit_key"],))
    with s.cp.as_role("dq_api", list(u["authorities"])) as c:
        c.execute("SELECT dq_ctl.veto_unit(%s,%s,'E100','DATA_CONCERN',NULL,NULL)", (u["unit_key"], u["state_version"]))
    assert s.registry.tier("ZZ", AGENT, "RELEASE", "AUTH_MEDICAID") == "L2"
    assert s.registry.tier("YY", AGENT, "RELEASE", "AUTH_MEDICAID") == "L4"       # the other state keeps its tier


# -- L1: intake feed items are visible to the intake duty, and only to it -----------------------------------------
def test_intake_feed_items_are_seen_and_acknowledged_by_the_intake_duty_only(fresh_cp_dsn):
    s = Slice(fresh_cp_dsn)
    api = TestClient(create_app(s))
    with s.cp.as_role("dq_seal_svc") as c:
        i = c.execute("SELECT dq_ctl.feed_item_open('ZZ','IE_EXTRACT','EMPTY_DELIVERY','B-I','F-I','AUTH_INTAKE',NULL) i"
                      ).fetchone()["i"]
    assert "release:AUTH_INTAKE" in api.get("/api/v1/me", headers=H(s, "checker")).json()["duties"]
    seen = api.get("/api/v1/feeds/items", headers=H(s, "checker")).json()
    assert [x["item_id"] for x in seen] == [i]
    for user in ("maker", "snap"):                                     # no intake duty: none, and no ack
        assert api.get("/api/v1/feeds/items", headers=H(s, user)).json() == []
        assert api.post(f"/api/v1/feeds/items/{i}/ack", headers=H(s, user),
                        json={"reason_code": "HOLIDAY"}).status_code == 404
    assert api.get("/api/v1/feeds/items", headers=H(s, "auditor")).status_code == 403
    ok = api.post(f"/api/v1/feeds/items/{i}/ack", headers=H(s, "checker"), json={"reason_code": "HOLIDAY"})
    assert ok.status_code == 200 and ok.json()["status"] == "ACKED", ok.text


# -- L4: before the period closes the status is PROVISIONAL; SIGNAL only after close -------------------------------
def test_monitoring_is_provisional_until_the_period_closes(tmp_path):
    with _sim(tmp_path) as (rt, dsn):
        rt.sim_preload(15)
        api = TestClient(create_app(rt))
        rows = api.get("/api/v1/monitor", headers=H(rt, "maker")).json()
        live = [r for r in rows if r["period"] == "2026-Q1"]
        assert live and {r["status"] for r in live} <= {"PROVISIONAL", "BASELINE_BUILDING"}
        assert not any(r["status"] in ("SIGNAL", "SHIFT", "WATCH") or r["last_signal"] for r in live)
        p = next(r for r in live if r["status"] == "PROVISIONAL")
        assert 0 < p["received_pct"] < 100 and "of 2026-Q1 received" in p["note"]
        assert "still open" in p["explanation"]["text"]
        while True:                                                     # the last night closes the period
            try:
                rt.sim_run_next("E100")
            except Exception:  # noqa: BLE001 - SimConflict: no night queued
                break
        closed = [r for r in api.get("/api/v1/monitor", headers=H(rt, "maker")).json() if r["period"] == "2026-Q1"]
        assert "PROVISIONAL" not in {r["status"] for r in closed}
        assert any(r["status"] in ("SIGNAL", "SHIFT") for r in closed)


# -- L2: --earn-story: L2 at start, a two-person promotion to L3, the next night opens real veto windows -------------
def test_earn_story_promotion_opens_veto_windows_on_the_next_night(tmp_path):
    with _sim(tmp_path) as (rt, dsn):
        assert rt.earn_story_seed() == 1
        assert rt.registry.tier("ZZ", AGENT, "RELEASE", "AUTH_MEDICAID") == "L2"
        pre = rt.sim_preload(15)
        assert sum(r["counts"]["released_autonomously"]["n"] for r in pre) == 0        # L2: a person decides
        # R4-5: a promotion needs the demo minimum (5 human-approved releases on this code version) and a
        # confirmed deterministic evidence bundle: a person releases what the preloaded nights held
        released = 0
        by_batch: dict = {}
        for u in rt.units(state="HELD"):
            if u["authorities"] == ["AUTH_MEDICAID"]:
                by_batch.setdefault(u["batch_id"], []).append(u)
        for _b, held in sorted(by_batch.items()):
            if released >= 5:
                break
            keys = [u["unit_key"] for u in held]
            tok = rt.step_up("maker", keys, "RELEASE")
            r = rt.decisions.decide(["AUTH_MEDICAID"], tok, keys, "RELEASE",
                                    "Reviewed the evidence; the release is correct.",
                                    {u["unit_key"]: u["state_version"] for u in held},
                                    {u["unit_key"]: u["fingerprint"] for u in held}, session_person="E100")
            released += sum(1 for x in r["results"] if x["status"] == "APPLIED")
            rt.verify_and_publish()
        assert released >= 5, released
        grant_evidence(rt)
        up = rt.effective_item("AUTONOMY", f"autonomy:{AGENT}:RELEASE")
        up["content"]["AUTH_MEDICAID"] = "L3"
        cr, status = rt.govern([up], approvers=("checker", "checker2"),
                               rationale="Promote Medicaid release to supervised after a clean record")
        assert cr["approvals_required"] == 2 and status == "APPLIED"
        assert rt.registry.tier("ZZ", AGENT, "RELEASE", "AUTH_MEDICAID") == "L3"
        api = TestClient(create_app(rt))
        r = api.post("/api/v1/sim/run-next", headers=H(rt, "maker"))
        assert r.status_code == 200, r.text
        assert r.json()["counts"]["veto_windows_opened"]["n"] >= 1, r.json()["counts"]
        with psycopg.connect(**dsn) as c:
            w = c.execute("SELECT count(*) FROM dq_ctl.veto_window_status WHERE NOT elapsed AND NOT vetoed").fetchone()[0]
        assert w >= 1                                                    # live countdowns on clean units


# -- nits --------------------------------------------------------------------------------------------------------------
def test_nightly_is_refused_with_governed_genesis_unless_allowed():
    from dqagent.cli.main import main
    with pytest.raises(SystemExit, match="allow-sim"):
        main(["demo", "--data", str(W1), "--nightly", "--genesis", "governed"])
    with pytest.raises(SystemExit, match="--nightly"):
        main(["demo", "--data", str(W1), "--earn-story"])


def test_moving_an_existing_element_to_none_is_never_lower_only(fresh_cp_dsn):
    s = Slice(fresh_cp_dsn)
    cls = s.effective_item("CLASSIFICATION", "classification")
    assert cls["content"]["ELG.SEX"] == "identity"
    down = {**cls, "content": {**cls["content"], "ELG.SEX": "none"}}
    assert s.registry.propose("ZZ", "E100", "Downgrade an identity element to none", [down])["approvals_required"] == 2
    new = {**cls, "content": {**cls["content"], "ZZ.NEW_ELEMENT": "none"}}       # a new element defaulting to none
    assert s.registry.propose("ZZ", "E100", "Add a new element with the default class", [new])["approvals_required"] == 1


# -- Part F E-10: one stale unit never aborts the publish cycle -------------------------------------------------------
def test_one_stale_unit_does_not_abort_publish_cycle(fresh_cp_dsn, tmp_path):
    """The first candidate's transition fails its compare-and-set (another writer moved it): that unit is skipped
    for this cycle ("stale: skipped this cycle", its savepoint rolled back) and every other unit still releases."""
    s = Slice(fresh_cp_dsn)
    _elig(s, tmp_path)
    cands = sorted((u for u in s.units(state="EVALUATING")), key=lambda u: (u["created_at"], u["unit_key"]))
    assert len(cands) == 2
    stale = cands[0]["unit_key"]
    real = s.publisher.cp.transition

    class StaleOnce:
        def __getattr__(self, k):
            return getattr(real.__self__, k)

        def transition(self, c, unit_key, expected, *a, **k):
            if unit_key == stale:                         # a CAS the database refuses (serialization_failure)
                return real(c, unit_key, expected - 1, *a, **k)
            return real(c, unit_key, expected, *a, **k)
    s.publisher.cp = StaleOnce()
    out = s.verify_and_publish()                          # verifier attestations, then the publish cycle
    assert out["blocked"][stale] == ["stale: skipped this cycle"] and out["stale"] == [stale]
    assert out["released"] == [cands[1]["unit_key"]]
    s.publisher.cp = real.__self__
    assert stale in s.verify_and_publish()["released"]    # the next cycle picks it up
    assert {u["state"] for u in s.units(batch_id="B-ELG-1")} == {"RELEASED"}
