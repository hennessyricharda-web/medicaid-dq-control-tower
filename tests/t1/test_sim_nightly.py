"""Part E §10b (DEC-018): the nightly-run simulator, over the real control plane and synthetic run w1.

INVARIANT: preloading K nights and then running every remaining night ends exactly at w1's pinned outcome
(420 RELEASED / 291 HELD / 47 PENDING_DEPENDENCY), for K=15 (the demo default) and for K=0. The full load
(cli.load_synthetic, pinned by test_w1_outcomes) goes through the same night-runner.
Also: every summary count reconciles with control-plane queries; run-next is 409 once no night is queued and while
another night is running; every /sim endpoint is 404 outside nightly mode; run-next is 403 without the operations duty
(token group AND roster); the explanation is present and byte-stable from the template."""
import contextlib
import os
import uuid
from pathlib import Path

import psycopg
import pytest
from fastapi.testclient import TestClient

from dqagent import llm
from dqagent.api.app import create_app
from dqagent.cp import schema
from dqagent.runtime import (DEFAULT_USERS, SIM_LOCK_KEY, Runtime, SimConflict, file_date, seed_routing,
                             synthetic_nights)

from .w1_harness import Slice

PG = dict(host=os.environ.get("DQ_PG_HOST", "/tmp"), port=int(os.environ.get("DQ_PG_PORT", "55432")),
          user=os.environ.get("DQ_PG_USER", "dq_gov"))
ROOT = Path(__file__).resolve().parents[2]
W1 = ROOT / "synthetic" / "out" / "w1"
EXPECTED = {"RELEASED": 420, "HELD": 291, "PENDING_DEPENDENCY": 47}
USERS = {**DEFAULT_USERS,                       # holds the token group but has NO roster grant for operations
         "opsonly": {"sub": "u-ops", "emp_id": "E310", "name": "Group Only", "groups": ["dq-duty-operations"]}}


def H(rt, user):
    return {"Authorization": f"Bearer {rt.idp.token(user)}"}


@contextlib.contextmanager
def _database(tmp):
    try:
        admin = psycopg.connect(dbname="postgres", autocommit=True, **PG)
    except Exception as e:  # pragma: no cover
        pytest.skip(f"T1 Postgres not reachable: {e}")
    db = "dqcp_sim_" + uuid.uuid4().hex[:8]
    admin.execute(f"CREATE DATABASE {db}")
    dsn = dict(dbname=db, **PG)
    with psycopg.connect(**dsn) as c:
        schema.apply(c)
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("DQ_ALLOW_TEST_GENESIS", "1")
        rt = Runtime(dsn, dp_path=str(tmp / f"{db}.duckdb"), test_genesis=True, users=USERS)
        try:
            yield rt, dsn
        finally:
            admin.execute(f"DROP DATABASE {db} WITH (FORCE)")
            admin.close()


def _states(dsn):
    with psycopg.connect(**dsn) as c:
        return dict(c.execute("SELECT state, count(*) FROM dq_ctl.units GROUP BY 1").fetchall())


@pytest.fixture(scope="module")
def k15(tmp_path_factory):
    """Demo default: nights 1..15 preloaded at startup, the rest run by the operator through the API."""
    with _database(tmp_path_factory.mktemp("k15")) as (rt, dsn):
        rt.sim_enable(W1, parte=True)
        pre = rt.sim_preload(15)
        api = TestClient(create_app(rt))
        before = api.get("/api/v1/sim/nights", headers=H(rt, "checker")).json()
        posted = []
        while True:
            r = api.post("/api/v1/sim/run-next", headers=H(rt, "maker"))
            if r.status_code == 409:
                break
            assert r.status_code == 200, r.text
            posted.append(r.json())
        yield {"rt": rt, "dsn": dsn, "api": api, "pre": pre, "posted": posted, "before": before, "last409": r}


@pytest.fixture(scope="module")
def k0(tmp_path_factory):
    with _database(tmp_path_factory.mktemp("k0")) as (rt, dsn):
        rt.sim_enable(W1, parte=True)
        assert rt.sim_preload(0) == []
        assert rt.sim_status() == {"enabled": True, "current_date": None, "queued": 22}
        runs = []
        with pytest.raises(SimConflict, match="no night is queued"):
            while True:
                runs.append(rt.sim_run_next("E100"))
        yield {"rt": rt, "dsn": dsn, "runs": runs}


# -- the invariant -----------------------------------------------------------------------------------------------
def test_k15_then_every_remaining_night_ends_at_the_w1_outcome(k15):
    assert _states(k15["dsn"]) == EXPECTED
    assert len(k15["pre"]) == 15 and len(k15["posted"]) == 7
    assert [n["status"] for n in k15["before"]["nights"]] == ["RUN"] * 15 + ["QUEUED"] * 7
    assert k15["before"]["current_date"] == "2026-03-01"
    assert all(r["person_id"] is None for r in k15["pre"]) and all(r["person_id"] == "E100" for r in k15["posted"])
    assert k15["rt"].sim_runs()[-1]["totals"] == EXPECTED and k15["posted"][-1]["night_date"] == "2026-04-01"
    # E5: what the operator was SHOWN is their scope only (Medicaid), never the all-authority totals
    assert sum(k15["posted"][-1]["totals"].values()) < sum(EXPECTED.values())


def test_k0_is_the_same_as_the_full_load(k0):
    assert _states(k0["dsn"]) == EXPECTED
    assert len(k0["runs"]) == 22 and k0["runs"][-1]["totals"] == EXPECTED


def test_nights_are_the_delivery_dates_of_w1_in_arrival_order():
    nights = synthetic_nights(W1, seed_routing("ZZ"))
    assert len(nights) == 22 and nights[0][0] == "2026-01-01" and nights[-1][0] == "2026-04-01"
    assert [p.name for p in nights[0][1]] == ["ie_extract_20260101.txt", "834_ZZBROKER_20260101_1.x12"]
    assert sum(len(fs) for _, fs in nights) == 23


# -- summaries reconcile with the control plane --------------------------------------------------------------------
def _ids(runs, key, prefix="unit:"):
    return {i.removeprefix(prefix) for r in runs for i in r["counts"][key]["ids"]}


def test_every_summary_count_reconciles_with_control_plane_queries(k15):
    rt, runs = k15["rt"], k15["rt"].sim_runs()
    assert len(runs) == 22 and [r["run_id"] for r in runs] == [f"SIM-{n.replace('-', '')}"
                                                                 for n, _ in synthetic_nights(W1, seed_routing("ZZ"))]
    for r in runs:
        for k, cnt in r["counts"].items():
            if k != "units_created":
                assert cnt["n"] == len(cnt["ids"]), (r["run_id"], k)
    with psycopg.connect(**k15["dsn"]) as c:
        units = {u: (b, s) for u, b, s in c.execute("SELECT unit_key, batch_id, state FROM dq_ctl.units").fetchall()}
        for r in runs:                                              # units created = the night's batches
            mine = {u for u, (b, _) in units.items() if file_date(b) == r["night_date"].replace("-", "")}
            assert r["counts"]["units_created"]["n"] == len(mine) and _ids([r], "units_created") == mine
            assert r["counts"]["files"]["ids"] == [f"file:{f}" for f in r["files"]]
        assert sum(r["counts"]["units_created"]["n"] for r in runs) == len(units) == sum(EXPECTED.values())
        held = {u for u, (_, s) in units.items() if s == "HELD"}
        assert _ids(runs, "held") == held                          # every HELD unit was held during some night
        auto = {u for (u,) in c.execute("SELECT DISTINCT unit_key FROM dq_ctl.unit_events WHERE to_state='RELEASED' "
                                        "AND from_state='EVALUATING'").fetchall()}
        assert _ids(runs, "released_autonomously") == auto
        assert sum(r["counts"]["released_autonomously"]["n"] for r in runs) == EXPECTED["RELEASED"]
        assert _ids(runs, "veto_windows_opened") == set() == {
            u for (u,) in c.execute("SELECT unit_key FROM dq_ctl.veto_windows").fetchall()}
        assert _ids(runs, "pending_dependency") == {u for u, (_, s) in units.items() if s == "PENDING_DEPENDENCY"}
        feed = {str(i) for (i,) in c.execute("SELECT item_id FROM dq_ctl.feed_items").fetchall()}
        assert _ids(runs, "feed_items", "feed:") == feed and feed       # simulated clock: missing deliveries open
        abc = {str(i) for (i,) in c.execute("SELECT id FROM dq_ctl.abc WHERE disposition <> 'PASS'").fetchall()}
        assert _ids(runs, "abc_breaks", "abc:") == abc
        for r in runs:
            a = r["counts"]["abc_breaks"]
            assert a["gating"] + a["advisory"] == a["n"]
        sig = {str(s) for (s,) in c.execute("SELECT seq FROM dq_ctl.agent_blackboard WHERE seat='MONITOR' AND template "
                                            "IN ('SPC_SIGNAL','MIX_SHIFT')").fetchall()}
        assert _ids(runs, "monitor_signals", "bb:") == sig and len(sig) == 5      # the five planted signals
        assert runs[-1]["counts"]["monitor_signals"]["n"] == 5          # the period closes on the last night
        dem = {x for (x,) in c.execute("SELECT cr_id FROM dq_ctl.config_change_requests WHERE origin='DEMOTION'")}
        assert _ids(runs, "demotions", "cr:") == dem
        reasons = sum(sum(r["counts"]["held"]["mode_reasons"].values()) for r in runs)
        assert reasons == sum(r["counts"]["held"]["n"] for r in runs)
        cur = c.execute("SELECT max(night_date)::text FROM dq_ctl.sim_runs").fetchone()[0]
    assert rt.sim_nights()["current_date"] == cur == "2026-04-01"


def test_sim_runs_are_append_only(k15):
    with psycopg.connect(**k15["dsn"]) as adm:
        for stmt in ("UPDATE dq_ctl.sim_runs SET person_id = 'X'", "DELETE FROM dq_ctl.sim_runs",
                     "TRUNCATE dq_ctl.sim_runs"):
            with pytest.raises(psycopg.errors.InsufficientPrivilege, match="append-only"):
                adm.execute(stmt)
            adm.rollback()
    with k15["rt"].cp.as_role("dq_seal_svc") as c:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            c.execute("DELETE FROM dq_ctl.sim_runs")
    with k15["rt"].cp.as_role("dq_api") as c:                          # the api reads, never writes
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            c.execute("INSERT INTO dq_ctl.sim_runs(run_id,night_date,started_at,summary) "
                      "VALUES ('SIM-20990101','2099-01-01',now(),'{}')")


# -- the API -------------------------------------------------------------------------------------------------------
def test_run_next_is_409_when_no_night_is_queued_and_while_a_night_runs(k15):
    rt, api = k15["rt"], k15["api"]
    assert k15["last409"].status_code == 409 and "no night is queued" in k15["last409"].text
    assert rt.sim_status() == {"enabled": True, "current_date": "2026-04-01", "queued": 0}
    lock = psycopg.connect(**{**k15["dsn"], "user": "dq_seal_svc"}, autocommit=True)
    try:                                                                # another session holds the night lock
        assert lock.execute("SELECT pg_try_advisory_lock(hashtext(%s))", (SIM_LOCK_KEY.format(state="ZZ"),)
                            ).fetchone()[0]
        r = api.post("/api/v1/sim/run-next", headers=H(rt, "maker"))
        assert r.status_code == 409 and "already running" in r.text
    finally:
        lock.close()


def test_run_next_needs_the_operations_duty(k15):
    rt, api = k15["rt"], k15["api"]
    for user in ("checker", "opsonly", "snap"):                        # no group / group without roster / neither
        assert api.post("/api/v1/sim/run-next", headers=H(rt, user)).status_code == 403, user
    assert "operations:AUTH_OPERATIONS" in api.get("/api/v1/me", headers=H(rt, "maker")).json()["duties"]
    assert not any(d.startswith("operations:") for d in api.get("/api/v1/me", headers=H(rt, "opsonly")).json()["duties"])


def test_endpoints_shapes_summary_block_and_scoped_ids(k15):
    rt, api = k15["rt"], k15["api"]
    n = api.get("/api/v1/sim/nights", headers=H(rt, "checker")).json()
    assert n["current_date"] == "2026-04-01" and len(n["nights"]) == 22
    assert set(n["nights"][0]) == {"night_date", "files", "status", "run_id", "error_class"}
    assert n["nights"][0]["run_id"] == "SIM-20260101"
    runs = api.get("/api/v1/sim/runs", headers=H(rt, "maker")).json()
    one = api.get("/api/v1/sim/runs/SIM-20260101", headers=H(rt, "maker")).json()
    assert len(runs) == 22 and one == runs[0]
    assert {"run_id", "night_date", "started_at", "finished_at", "person_id", "files", "counts",
            "explanation"} <= set(one)
    assert set(one["counts"]) == {"files", "abc_breaks", "units_created", "released_autonomously",
                                  "veto_windows_opened", "held", "pending_dependency", "feed_items",
                                  "monitor_signals", "demotions"}
    assert api.get("/api/v1/sim/runs/SIM-20990101", headers=H(rt, "maker")).status_code == 404
    sm = api.get("/api/v1/summary", headers=H(rt, "maker")).json()
    assert sm["sim"] == {"enabled": True, "current_date": "2026-04-01", "queued": 0}
    assert not {"scope_index", "held_reason_of", "totals_by_scope"} & set(one)      # the index never leaves


# -- E5: every summary is rebuilt per caller from in-scope rows only ---------------------------------------------------
def _visible_units(rt, auths):
    with rt.cp.as_role("dq_api", auths) as c:
        return {r["unit_key"]: r["state"] for r in c.execute("SELECT unit_key, state FROM dq_ctl.units").fetchall()}


def test_sim_summaries_are_rebuilt_per_caller_scope(k15):
    rt, api = k15["rt"], k15["api"]
    full = {r["run_id"]: r for r in rt.sim_runs()}
    snap_units, med_units = _visible_units(rt, ["AUTH_SNAP"]), _visible_units(rt, ["AUTH_MEDICAID"])
    assert snap_units and med_units and not set(snap_units) & set(med_units)
    for user, mine, other, auth in (("snap", snap_units, med_units, "AUTH_SNAP"),
                                    ("maker", med_units, snap_units, "AUTH_MEDICAID")):
        runs = api.get("/api/v1/sim/runs", headers=H(rt, user)).json()
        assert len(runs) == 22
        for r in runs:
            idx = full[r["run_id"]]["scope_index"]
            for k, cnt in r["counts"].items():
                assert cnt["n"] == len(cnt["ids"]), (user, r["run_id"], k)          # counts ARE the visible rows
                for i in cnt["ids"]:
                    if i.startswith("unit:"):
                        assert i[5:] in mine and i[5:] not in other, (user, i)
                    elif not i.startswith("file:"):
                        assert idx[i]["a"] in ([auth], ["AUTH_INTAKE"]), (user, i, idx[i])
            a = r["counts"]["abc_breaks"]
            assert a["gating"] + a["advisory"] == a["n"]
            assert sum(r["counts"]["held"]["mode_reasons"].values()) == r["counts"]["held"]["n"]
            # the explanation carries only this caller's numbers
            assert r["explanation"] == llm.explain("nightly_run", Runtime.nightly_facts(r), cfg={"provider": "template"})
        last = runs[-1]["totals"]
        want: dict = {}
        for st in mine.values():
            want[st] = want.get(st, 0) + 1
        assert last == want, (user, last, want)                    # no unit of another authority in the totals
    snap_last = api.get("/api/v1/sim/runs/SIM-20260401", headers=H(rt, "snap")).json()
    assert sum(snap_last["totals"].values()) < sum(EXPECTED.values())


def test_sim_endpoints_are_403_without_a_release_scope(k15):
    rt, api = k15["rt"], k15["api"]
    for url in ("/api/v1/sim/nights", "/api/v1/sim/runs", "/api/v1/sim/runs/SIM-20260101"):
        for user in ("auditor", "opsonly"):
            assert api.get(url, headers=H(rt, user)).status_code == 403, (url, user)


def test_every_sim_endpoint_is_404_outside_nightly_mode(fresh_cp_dsn):
    s = Slice(fresh_cp_dsn)
    api = TestClient(create_app(s))
    for m, url in (("get", "/api/v1/sim/nights"), ("post", "/api/v1/sim/run-next"), ("get", "/api/v1/sim/runs"),
                   ("get", "/api/v1/sim/runs/SIM-20260101")):
        assert getattr(api, m)(url, headers=H(s, "maker")).status_code == 404, url
    assert api.get("/api/v1/summary", headers=H(s, "maker")).json()["sim"]["enabled"] is False


# -- the mandatory explanation ---------------------------------------------------------------------------------------
def test_every_run_carries_a_byte_stable_template_explanation(k15, k0):
    for runs in (k15["rt"].sim_runs(), k0["runs"]):
        for r in runs:
            e = r["explanation"]
            again = llm.explain("nightly_run", Runtime.nightly_facts(r), cfg={"provider": "template"})
            assert e == again and e["source"] == "template" and e["fact_check"] == "TEMPLATE"
            assert e["text"].startswith(f"Simulated night {r['night_date']} (run {r['run_id']})")
    a = {r["run_id"]: r["explanation"]["text_hash"] for r in k15["rt"].sim_runs()}
    b = {r["run_id"]: r["explanation"]["text_hash"] for r in k0["runs"]}
    assert a == b                                   # same nights, same facts, same bytes whatever K was


# -- Part F E-04: a queued night lists only the files the caller may see ----------------------------------------------
def test_queued_nights_list_only_in_scope_files(tmp_path):
    """A QUEUED night's file is listed only when its route's relations' authorities meet the caller's scope; the
    intake duty sees every inbound file; an unrouted file only the intake or the operations duty."""
    import shutil
    data = tmp_path / "w1"
    shutil.copytree(W1, data)
    (data / "zz_unknown_20260101.dat").write_bytes(b"no route matches this\n")
    with _database(tmp_path) as (rt, dsn):
        rt.sim_enable(data, parte=False)
        api = TestClient(create_app(rt))

        def files(user):
            n = api.get("/api/v1/sim/nights", headers=H(rt, user)).json()["nights"]
            assert all(x["status"] == "QUEUED" for x in n)
            return {f for x in n for f in x["files"]}
        everything = {p.name for _, fs in synthetic_nights(data, seed_routing("ZZ")) for p in fs}
        ie = {f for f in everything if f.startswith("ie_extract_")}
        routed = everything - {"zz_unknown_20260101.dat"}
        assert files("snap") == ie                                    # SNAP: eligibility only (no 834 / 837P)
        assert files("checker2") == routed                            # Medicaid: every routed file, not the unknown
        assert files("maker") == everything                           # Medicaid + operations: also the unrouted file
        assert files("checker") == everything                         # the intake duty: every inbound file
