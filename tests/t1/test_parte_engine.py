"""Part E engine and runtime (seat 2a): gating ABC, tier / entitlement ceilings, the veto window, empty and missing
deliveries, the monitor seat, the parallel run, explanation binding, governed genesis and the schema-apply race fix.

Every test drives the real runtime against a real control plane (T1 Postgres) and data plane (DuckDB).
"""
import datetime as dt
import json
import os
import threading
import time
import uuid
from pathlib import Path

import psycopg
import pytest

from dqagent.cp import schema
from dqagent.hitl.decisions import Denied, Stale
from dqagent.hitl.modes import mode
from dqagent.model.outcome import Disposition as D
from dqagent.runtime import Runtime

from .conftest import PG
from .fixtures_w1 import acks, ie_file, person, x837
from .w1_harness import Slice

ROOT = Path(__file__).resolve().parents[2]
AGENT = "dq-reconciliation"
MED = ["AUTH_MEDICAID"]
REF = {"facts_hash": "a" * 64, "text_hash": "b" * 64}


def _ie(s, tmp, batch, persons, trailer=None, extra_empty=False):
    p = tmp / f"{batch}.txt"
    p.write_bytes(ie_file([person(a, b) for a, b in persons], trailer))
    files = [("IE", p)]
    if extra_empty:
        e = tmp / f"{batch}-empty.txt"
        e.write_bytes(ie_file([]))
        files.append(("IE", e))
    return s.run_pipeline("p4_eligibility_ie", batch, files, ["bronze.ie_segment"])


def _tier_item(s, tiers: dict):
    it = s.effective_item("AUTONOMY", f"autonomy:{AGENT}:RELEASE")
    it["content"].update(tiers)
    return it


def _held_event(s, uk):
    with s.cp.as_role("dq_eval_svc") as c:
        return c.execute("SELECT via, payload FROM dq_ctl.unit_events WHERE unit_key=%s AND to_state='HELD' "
                         "ORDER BY state_version DESC LIMIT 1", (uk,)).fetchone()


def _approve(s, uk, user="maker", explanation_ref=None):
    u = s.units(unit_key=uk)[0]
    tok = s.step_up(user, [uk], "RELEASE")
    return s.decisions.decide(MED, tok, [uk], "RELEASE", "Reviewed the evidence on the card for this test.",
                              {uk: u["state_version"]}, {uk: u["fingerprint"]},
                              session_person=s.idp.users[user]["emp_id"], explanation_ref=explanation_ref)


# -- 9. race fix: schema apply is serialised across the cluster ------------------------------------------------------
def _new_db(admin, prefix="dqrace_"):
    db = prefix + uuid.uuid4().hex[:8]
    admin.execute(f"CREATE DATABASE {db}")
    return db


def test_schema_apply_on_two_fresh_databases_concurrently_never_collides():
    """Role DDL is cluster-wide; without the cluster lock two concurrent applies fail with 'tuple concurrently
    updated'. Three rounds of two simultaneous applies must all succeed."""
    try:
        admin = psycopg.connect(dbname="postgres", autocommit=True, **PG)
    except psycopg.OperationalError as e:  # pragma: no cover
        pytest.skip(f"T1 Postgres not reachable: {e}")
    dbs = []
    try:
        for _ in range(3):
            pair = [_new_db(admin), _new_db(admin)]
            dbs += pair
            errors, start = [], threading.Barrier(2)

            def go(db):
                try:
                    with psycopg.connect(dbname=db, **PG) as c:
                        start.wait()
                        schema.apply(c)
                except Exception as e:  # noqa: BLE001 - collected and asserted below
                    errors.append(repr(e))
            ts = [threading.Thread(target=go, args=(db,)) for db in pair]
            for t in ts:
                t.start()
            for t in ts:
                t.join(120)
            assert not errors, errors
            for db in pair:
                with psycopg.connect(dbname=db, **PG) as c:
                    assert c.execute("SELECT count(*) FROM dq_ctl.allowed_transitions").fetchone()[0] > 0
    finally:
        for db in dbs:
            admin.execute(f"DROP DATABASE IF EXISTS {db} WITH (FORCE)")
        admin.close()


def test_schema_apply_waits_for_the_cluster_lock():
    """The lock is the mechanism: while another session holds the fixed key in the lock database, apply blocks."""
    admin = psycopg.connect(dbname="postgres", autocommit=True, **PG)
    db = _new_db(admin)
    holder = psycopg.connect(dbname=schema.CLUSTER_LOCK_DB, autocommit=True, **PG)
    try:
        holder.execute("SELECT pg_advisory_lock(%s)", (schema.CLUSTER_LOCK_KEY,))
        done = threading.Event()

        def go():
            with psycopg.connect(dbname=db, **PG) as c:
                schema.apply(c)
            done.set()
        t = threading.Thread(target=go)
        t.start()
        assert not done.wait(1.5), "apply ran while another session held the cluster lock"
        holder.execute("SELECT pg_advisory_unlock(%s)", (schema.CLUSTER_LOCK_KEY,))
        assert done.wait(120)
        t.join()
    finally:
        holder.close()
        admin.execute(f"DROP DATABASE IF EXISTS {db} WITH (FORCE)")
        admin.close()


# -- 1. one gating-ABC definition / 4. empty deliveries ------------------------------------------------------------
def test_an_empty_expected_delivery_opens_a_feed_item_and_never_raises_the_floor(fresh_cp_dsn, tmp_path):
    s = Slice(fresh_cp_dsn)
    r = _ie(s, tmp_path, "B-EMPTY-1", [("P1", "M0001")], extra_empty=True)
    assert r["feed_items"] and not r["abc_failed"]
    hop = [h for h in r["abc"] if h["measure"] == "expect_nonzero"]
    assert len(hop) == 1 and hop[0]["disposition"] == "FAIL" and hop[0]["gating"] is False
    assert s.verify_and_publish()["released"]                          # mode A: the empty file held nothing
    with s.cp.as_role("dq_seal_svc") as c:
        it = c.execute("SELECT * FROM dq_ctl.feed_items_status WHERE item_id=%s", (r["feed_items"][0],)).fetchone()
        assert it["kind"] == "EMPTY_DELIVERY" and it["feed"] == "IE_EXTRACT" and it["status"] == "OPEN"
        assert c.execute("SELECT count(*) n FROM dq_ctl.abc_gating_failures WHERE batch_id='B-EMPTY-1'").fetchone()["n"] == 0
    with s.cp.as_role("dq_eval_svc") as c:
        assert c.execute("SELECT 1 FROM dq_ctl.agent_blackboard WHERE template='EMPTY_DELIVERY'").fetchone()
    # the same batch with a GATING failure (trailer mismatch) is held at the ABC floor: the view still works
    r2 = _ie(s, tmp_path, "B-BAD-1", [("P2", "M0002")], trailer=9)
    assert r2["abc_failed"] and _held_event(s, s.units(batch_id="B-BAD-1")[0]["unit_key"])["payload"]["mode"] == "H_IN_2"


def test_empty_ok_feed_opens_no_item_and_missing_delivery_opens_once(fresh_cp_dsn, tmp_path):
    from dqagent.engine.intake import feed_policy
    s = Slice(fresh_cp_dsn)
    assert feed_policy(s.feeds, "X12_834") == {"expected": True, "empty_ok": True, "every": "P1D"}
    assert feed_policy({"feeds": {}}, "X12_834")["empty_ok"] and feed_policy({"feeds": {}}, "IE_EXTRACT")["expected"]
    it = __import__("dqagent.engine.intake", fromlist=["Intake"]).Intake(s.cp, s.dp, "p4", "B-X", feeds=s.feeds,
                                                                            state="ZZ")
    it._expect_nonzero("X12_834", "F1", 0, "ZZ-BROKER")              # an empty 834 delta: nothing
    assert it.abc == [] and it.feed_items == []
    _ie(s, tmp_path, "B-ELG-1", [("P1", "M0001")])
    assert s.check_missing_deliveries() == []                          # inside every cadence
    later = dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=8)
    first = s.check_missing_deliveries(now=later)
    again = s.check_missing_deliveries(now=later)
    with s.cp.as_role("dq_seal_svc") as c:
        rows = c.execute("SELECT feed, kind FROM dq_ctl.feed_items WHERE kind='MISSING_DELIVERY' ORDER BY feed").fetchall()
    # IE (P1D), 834 (P1D) and 837P (P1W, never delivered: measured from the first file) are all overdue; once each
    assert sorted(r["feed"] for r in rows) == ["IE_EXTRACT", "X12_834", "X12_837P"] and sorted(first) == sorted(again)
    assert not s.units(state="HELD")                                   # never a batch floor


# -- 2. tier ceiling and entitlement ------------------------------------------------------------------------------
def test_tier_ceiling_holds_with_the_why_and_a_human_still_releases(fresh_cp_dsn, tmp_path):
    s = Slice(fresh_cp_dsn)
    assert s.registry.demote(AGENT, "RELEASE", "AUTH_MEDICAID", "L2", "TEST_SETUP") == 1
    _ie(s, tmp_path, "B-ELG-1", [("P1", "M0001")])
    uk = s.units(batch_id="B-ELG-1")[0]["unit_key"]
    ev = _held_event(s, uk)
    assert ev["payload"]["mode"] == "H_IN" and ev["payload"]["ceiling"] == "L2 ceiling"
    assert "tier: L2 ceiling for AUTH_MEDICAID -> H_IN" in ev["payload"]["mode_trace"]
    why = s.unit_mode(uk)
    assert why["mode"] == "H_IN" and why["ceiling"] == "L2 ceiling" and why["autonomy"]["AUTH_MEDICAID"]["tier"] == "L2"
    r = _approve(s, uk, explanation_ref=REF)
    assert r["results"][0]["status"] == "APPLIED"
    assert uk in s.verify_and_publish()["released"]
    with s.cp.as_role("dq_eval_svc") as c:                                 # 7: the explanation shown is bound
        d = c.execute("SELECT explanation_ref FROM dq_ctl.decisions WHERE unit_key=%s", (uk,)).fetchone()
    assert d["explanation_ref"] == REF


def test_expired_release_entitlement_lowers_the_ceiling_but_never_blocks_a_human_release(fresh_cp_dsn, tmp_path):
    s = Slice(fresh_cp_dsn)
    ent = s.effective_item("ENTITLEMENT", f"entitlement:{AGENT}:RELEASE")
    nb = dt.datetime.fromisoformat(ent["content"]["not_before"])
    ent["content"]["not_after"] = (nb + dt.timedelta(seconds=1)).isoformat()    # lower-only: one approver
    cr, st = s.govern([ent])
    assert st == "APPLIED" and cr["approvals_required"] == 1
    _ie(s, tmp_path, "B-ELG-1", [("P1", "M0001")])
    uk = s.units(batch_id="B-ELG-1")[0]["unit_key"]
    ev = _held_event(s, uk)
    assert ev["payload"]["mode"] == "H_IN" and ev["payload"]["ceiling"] == "no RELEASE entitlement"
    _approve(s, uk)
    assert uk in s.verify_and_publish()["released"]


def test_explanation_ref_must_be_well_formed(fresh_cp_dsn, tmp_path):
    s = Slice(fresh_cp_dsn)
    _ie(s, tmp_path, "B-ELG-1", [("P1", "M0001")], trailer=9)
    uk = s.units(batch_id="B-ELG-1")[0]["unit_key"]
    with pytest.raises(Denied, match="explanation_ref"):
        _approve(s, uk, explanation_ref={"facts_hash": "x"})


def test_explain_for_uses_the_effective_classification_and_is_deterministic(fresh_cp_dsn):
    s = Slice(fresh_cp_dsn)
    obj = {"agent_id": AGENT, "action_class": "RELEASE", "authority": "AUTH_MEDICAID", "from_tier": "L3",
           "to_tier": "L2", "reason_code": "VETO", "cr_id": "DEM-1"}
    a, b = s.explain_for("demotion", obj), s.explain_for("demotion", obj)
    assert a == b and a["text"] and a["source"] == "template" and a["fact_check"] == "TEMPLATE"
    assert len(a["facts_hash"]) == 64 and len(a["text_hash"]) == 64
    assert s.classification()                                             # the effective artifact, not a file


# -- 3. veto window ------------------------------------------------------------------------------------------------
def test_veto_window_releases_at_the_database_deadline_and_a_veto_demotes(fresh_cp_dsn, tmp_path):
    s = Slice(fresh_cp_dsn)
    cr, st = s.govern([_tier_item(s, {"AUTH_MEDICAID": "L3"})])           # L4 -> L3: lower-only, one approver
    assert st == "APPLIED" and cr["approvals_required"] == 1
    _ie(s, tmp_path, "B-ELG-1", [("P1", "M0001"), ("P2", "M0002"), ("P3", "M0003")])
    with s.cp.as_role("dq_publish_svc") as c:
        ws = c.execute("SELECT unit_key, opened_at, deadline, window_s FROM dq_ctl.veto_window_status "
                       "ORDER BY deadline").fetchall()
    assert len(ws) == 3 and {w["window_s"] for w in ws} == {600}
    assert {u["state"] for u in s.units(batch_id="B-ELG-1")} == {"EVALUATING"}
    first, rest = ws[0], ws[1:]
    assert all(w["deadline"] > first["deadline"] for w in rest)
    out = s.verify_and_publish()                                          # database clock: window still open
    assert not out["released"] and all(any(r.startswith("5: veto window open") for r in why)
                                       for why in out["blocked"].values())
    s.publisher.clock = lambda: first["deadline"] - dt.timedelta(seconds=1)
    assert not s.verify_and_publish()["released"]                         # not one second early
    s.publisher.clock = lambda: first["deadline"]
    assert s.verify_and_publish()["released"] == [first["unit_key"]]      # exactly at the deadline
    s.publisher.clock = None
    with pytest.raises(Stale):                                            # past veto once released (api: 409)
        s.veto(MED, first["unit_key"], s.units(unit_key=first["unit_key"])[0]["state_version"], "E100",
               "DATA_SUSPECT")
    vetoed = rest[0]["unit_key"]
    s.veto(MED, vetoed, s.units(unit_key=vetoed)[0]["state_version"], "E100", "DATA_SUSPECT",
           explanation_ref=REF)
    assert s.units(unit_key=vetoed)[0]["state"] == "HELD"
    assert s.registry.tier("ZZ", AGENT, "RELEASE", "AUTH_MEDICAID") == "L2"   # automatic, lower-only
    # C3: the third unit's recomputed mode now exceeds H_ON (L2 ceiling): the publisher HOLDS it via CEILING
    third = rest[1]["unit_key"]
    s.publisher.clock = lambda: rest[1]["deadline"] + dt.timedelta(seconds=1)
    out = s.verify_and_publish()                                          # (veto() already ran one publish cycle)
    assert third not in out["released"] and s.units(unit_key=third)[0]["state"] == "HELD"
    ev = _held_event(s, third)
    assert ev["via"] == "CEILING" and ev["payload"]["mode"] == "H_IN" and ev["payload"]["ceiling"] == "L2 ceiling"
    # the vetoed unit never releases on its own and needs a person (vetoed -> at least H_IN)
    with s.cp.as_role("dq_api", MED) as c:
        assert s.decisions._mode_of(c, vetoed) == "H_IN"
    with s.cp.as_role("dq_eval_svc") as c:
        tr = c.execute("SELECT vetoes, released_after_window FROM dq_ctl.track_record WHERE agent_id=%s AND "
                       "action_class='RELEASE' AND authority='AUTH_MEDICAID'", (AGENT,)).fetchone()
        dem = c.execute("SELECT count(*) n FROM dq_ctl.agent_blackboard WHERE template='AUTONOMY_DEMOTED'").fetchone()
    assert dem["n"] == 1 and tr is not None


def test_dependency_escalation_is_max_h_in_and_persisted_for_the_publisher(fresh_cp_dsn, tmp_path):
    # pure: the old line replaced H_ON with itself (C2); _max never lowers and lifts H_ON / A to H_IN
    pol = __import__("yaml").safe_load(open(ROOT / "config/state/ZZ/policy_w1.yaml"))
    l3 = {"AUTH_MEDICAID": {"tier": "L3", "entitled": True}}
    assert mode(pol, MED, [D.PASS], abc_failed=False, autonomy=l3)[0] == "H_ON"
    assert mode(pol, MED, [D.PASS], abc_failed=False, autonomy=l3, escalated="wait exceeded")[0] == "H_IN"
    assert mode(pol, MED, [D.PASS], abc_failed=True, escalated="wait exceeded")[0] == "H_IN_2"
    s = Slice(fresh_cp_dsn)
    _ie(s, tmp_path, "B-ELG-1", [("P1", "M0001")], trailer=9)           # eligibility held (ABC floor)
    p = tmp_path / "enc.x12"
    p.write_bytes(x837("ZZMCOA", "000000101", [{"id": "C1", "member": "M0001",
                                                "lines": [("Z9001", 100.0, "20260110")]}]))
    a = tmp_path / "enc.ack.csv"
    a.write_bytes(acks("000000101", ["A"]))
    s.run_pipeline("p6_claims", "B-ENC-1", [("X12_837P", p, "ZZ-MCO-A", a)],
                   ["bronze.encounter_header", "bronze.encounter_line"])
    enc = s.units(batch_id="B-ENC-1")[0]["unit_key"]
    assert s.units(unit_key=enc)[0]["state"] == "PENDING_DEPENDENCY"
    real = s.evaluator.now
    s.evaluator.now = lambda: real() + dt.timedelta(days=10)
    s.reevaluate_pending()
    ev = _held_event(s, enc)
    assert ev["payload"]["escalated"].startswith("dependency wait exceeded") and ev["payload"]["mode"] in ("H_IN", "H_IN_2")
    why = s.unit_mode(enc)
    assert any(t.startswith("dependency wait exceeded") for t in why["mode_trace"])


# -- 10. genesis needs two approvers; pre-genesis runs fail closed without raising -------------------------------
def test_governed_genesis_needs_two_approvers_and_pre_genesis_abc_failure_does_not_raise(fresh_cp_dsn, tmp_path,
                                                                                          monkeypatch):
    from dqagent.runtime import bootstrap_roster
    bootstrap_roster(fresh_cp_dsn, {}, "ROSTER-BOOTSTRAP")
    for k in ("DQ_K_EV", "DQ_K_FP", "DQ_K_ABC"):                      # R4-2: governed genesis is pilot | production
        monkeypatch.setenv(k, f"pilot-{k.lower()}-key")
    rt = Runtime(fresh_cp_dsn, dp_path=str(tmp_path / "dp.duckdb"), deployment="pilot")
    assert not rt.ratified
    r = _ie(rt, tmp_path, "B-PRE-1", [("P1", "M0001")], trailer=9)      # ABC failure before the agent exists
    assert r["abc_failed"] and r["summary"]["bronze.ie_segment"].get("x_not_ratified")
    with pytest.raises(Denied):
        rt.ratify_genesis(author="maker", approvers=("maker",))            # the author cannot approve
    g = rt.ratify_genesis(author="checker", approvers=("maker", "checker2"))
    assert g["approvals_required"] == 2 and g["status"] == "APPLIED" and rt.ratified


# -- 5/6. monitor seat and parallel run on synthetic w1 (with the w1 outcome as the negative control) --------------
def test_w1_with_part_e_extras_keeps_the_outcomes_fires_planted_signals_and_runs_the_parallel_run(fresh_cp_dsn,
                                                                                                    tmp_path,
                                                                                                    monkeypatch):
    monkeypatch.setenv("DQ_ALLOW_TEST_GENESIS", "1")
    from dqagent.cli.main import load_synthetic
    data = ROOT / "synthetic" / "out" / "w1"
    man = json.loads((data / "parte_manifest.json").read_text())
    rt = Runtime(fresh_cp_dsn, dp_path=str(tmp_path / "dp.duckdb"), test_genesis=True)
    out = load_synthetic(rt, data, parte=True)
    with psycopg.connect(**fresh_cp_dsn) as c:
        got = dict(c.execute("SELECT state, count(*) FROM dq_ctl.units GROUP BY 1").fetchall())
        veto_windows = c.execute("SELECT count(*) FROM dq_ctl.veto_windows").fetchone()[0]
        ceiling = c.execute("SELECT count(*) FROM dq_ctl.unit_events WHERE via='CEILING'").fetchone()[0]
    # negative control (advisory monitor, non-gating parallel run): the pinned w1 outcome is unchanged
    assert got == {"RELEASED": 420, "HELD": 291, "PENDING_DEPENDENCY": 47}
    assert veto_windows == 0 and ceiling == 0
    res = {(r["relation"], r["metric"], r["key"]): r for r in out["_parte"]["monitor"]}
    for e in man["monitor"]["expected_signals"]:
        r = res[(e["relation"], e["metric"], e["key"])]
        if e["rule"] == "PSI":
            assert r["status"] == e["direction"], r
        else:
            assert r["status"] == "SIGNAL" and {(s["rule"], s["direction"]) for s in r["signals"]} == {(e["rule"], e["direction"])}, r
    planted = {(e["relation"], e["metric"], e["key"]) for e in man["monitor"]["expected_signals"]}
    clean = [r for k, r in res.items() if k not in planted]
    assert clean and all(r["status"] == "OK" for r in clean), [(k, r["status"]) for k, r in res.items() if k not in planted]
    assert {(c["relation"], c["metric"], c["key"]) for c in man["monitor"]["clean_series"]} <= set(res)
    with rt.cp.as_role("dq_monitor_svc") as c:
        posts = c.execute("SELECT template, subject_id, seat FROM dq_ctl.agent_blackboard WHERE seat='MONITOR' "
                          "ORDER BY seq").fetchall()
        pins = c.execute("SELECT count(*) n FROM dq_ctl.agent_memory WHERE kind='SPC_BASELINE'").fetchone()["n"]
    assert sum(p["template"] == "SPC_SIGNAL" for p in posts) == 4 and sum(p["template"] == "MIX_SHIFT" for p in posts) == 1
    assert pins == len(res)
    # the period's value equals the data plane over the whole w1 load (= the generator's recorded observation)
    obs = {(o["relation"], o["metric"], o["key"]): o["value"] for o in man["w1_observed"]}
    for k, r in res.items():
        if r["chart"] != "psi":
            assert abs(float(r["value"]) - float(obs[k])) < 1e-6, (k, r["value"], obs[k])
    rt.monitor_close_period()                                              # closing twice posts nothing new
    with rt.cp.as_role("dq_monitor_svc") as c:
        assert c.execute("SELECT count(*) n FROM dq_ctl.agent_blackboard WHERE seat='MONITOR'").fetchone()["n"] == len(posts)
    # parallel run: MATCH / WITHIN / BREAK exactly as planted; non-gating; idempotent
    pr = {(r["relation"], r["measure"]): r for r in out["_parte"]["parallel_run"]}
    for e in man["parallel_run"]["expected"]:
        r = pr[(e["relation"], e["measure"])]
        assert r["status"] == e["status"] and float(r["new"]) == pytest.approx(float(e["new"])), (e, r)
    assert {r["status"] for r in pr.values()} == {"MATCH", "WITHIN", "BREAK"}
    again = rt.load_legacy_totals(data / "legacy_totals.json")
    assert sorted(r["row_id"] for r in again) == sorted(r["row_id"] for r in pr.values())
    with rt.cp.as_role("dq_seal_svc") as c:
        st = c.execute("SELECT count(*) FILTER (WHERE NOT counts_for_go_no_go) open_breaks FROM dq_ctl.parallel_run_status"
                       ).fetchone()
    assert st["open_breaks"] == 2                                          # each BREAK awaits a person's explain code
    series = rt.monitor_series("bronze.encounter_header", "rows")
    assert [p["source"] for p in series["points"]][-1] == "PERIOD" and series["baseline"]["status"] == "FROZEN"


# -- Part F E-08: a NON-gating intake verdict never demands the intake duty ----------------------------------------
def test_nongating_empty_delivery_needs_no_intake_duty(fresh_cp_dsn, tmp_path):
    """An empty expected delivery records a non-gating FAIL on a pre-split hop (and a feed item). The decision path
    must read the same one definition as publisher clause 7 (gating only), so a Medicaid reviewer without the intake
    duty completes the decision alone. Negative control: a GATING intake failure still needs the intake duty."""
    s = Slice(fresh_cp_dsn)
    assert s.registry.demote(AGENT, "RELEASE", "AUTH_MEDICAID", "L2", "TEST_SETUP") == 1   # held for one person (H_IN)
    _ie(s, tmp_path, "B-EMPTY-1", [("P1", "M0001")], extra_empty=True)
    uk = s.units(batch_id="B-EMPTY-1")[0]["unit_key"]
    assert _held_event(s, uk)["payload"]["mode"] == "H_IN"
    with s.cp.as_role("dq_api", MED) as c:
        v = c.execute("SELECT disposition, gating FROM dq_ctl.intake_verdicts('B-EMPTY-1')").fetchall()
    assert ("FAIL", False) in {(r["disposition"], r["gating"]) for r in v}             # still shown on the card
    r = _approve(s, uk)                                                                 # maker: no intake duty
    assert r["results"][0]["status"] == "APPLIED"
    assert uk in s.verify_and_publish()["released"]
    # negative control: a gating intake failure (trailer mismatch) still requires the intake duty
    from dqagent.hitl.decisions import intake_failed
    _ie(s, tmp_path, "B-BAD-1", [("P2", "M0002")], trailer=9)
    with s.cp.as_role("dq_api", MED) as c:
        assert intake_failed(c, "B-BAD-1") and not intake_failed(c, "B-EMPTY-1")
