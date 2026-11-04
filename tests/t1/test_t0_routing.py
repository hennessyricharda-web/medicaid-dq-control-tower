"""Part F T0, F-02: inbound file routing driven by the governed FEEDS configuration.

Before the fix the file plan was hard-coded on name prefixes and every unknown file fell through to the claims
pipeline. Now the routes come from FEEDS (`routing`), a file no route matches opens one UNROUTED_FILE feed item and is
never ingested, and changing a route is a two-person change request.
"""
import json
import shutil
from pathlib import Path

import psycopg
import pytest

from dqagent.cli.main import load_synthetic
from dqagent.ingest.routing import Routing
from dqagent.rules.ir import IRError
from dqagent.runtime import Runtime, arrival_order, seed_routing, synthetic_nights

from .test_parte_redteam_fixes import _sim
from .w1_harness import Slice

ROOT = Path(__file__).resolve().parents[2]
SYN = ROOT / "synthetic" / "out"
DIRS = ("w1", "w1_holdout7", "w1_partc", "w1_realistic", "w2_earn")
EXPECTED = {"RELEASED": 420, "HELD": 291, "PENDING_DEPENDENCY": 47}


# -- a frozen copy of the pre-F-02 code (runtime.py arrival_order / _file_plan), the reference the routing must match
_OLD_PARTNERS = {"ZZMCOA": "ZZ-MCO-A", "ZZMCOB": "ZZ-MCO-B", "ZZBROKER": "ZZ-BROKER"}


def _old_arrival_order(data: Path):
    def key(p: Path):
        parts = p.stem.split("_")
        date = next((x for x in parts if len(x) == 8 and x.isdigit()), "0")
        kind = 0 if p.name.startswith("ie_") else 1 if p.name.startswith("834_") else 2
        seq = int(parts[-1]) if parts[-1].isdigit() and len(parts[-1]) < 4 else 0
        return (date, kind, seq, p.name)
    return sorted([*data.glob("ie_extract_*.txt"), *data.glob("834_*.x12"), *data.glob("837P_*.x12")], key=key)


def _old_file_plan(data, p: Path):
    data = Path(data)
    batch = "B-" + p.stem
    ack = data / ("ack_" + "_".join(p.stem.split("_")[1:]) + ".csv")
    ack = ack if ack.exists() else None
    if p.name.startswith("ie_"):
        return "p4_eligibility_ie", batch, [("IE", p)], ["bronze.ie_segment"]
    partner = _OLD_PARTNERS.get(p.stem.split("_")[1], p.stem.split("_")[1])
    if p.name.startswith("834_"):
        return "p4_eligibility_ie", batch, [("X12_834", p, partner, ack)], ["bronze.enrollment_834"]
    return "p6_claims", batch, [("X12_837P", p, partner, ack)], ["bronze.encounter_header", "bronze.encounter_line"]


def test_routing_reproduces_w1_plan_and_order():
    routing = Routing(seed_routing("ZZ"))
    for d in DIRS:
        data = SYN / d
        old = _old_arrival_order(data)
        assert arrival_order(data, routing) == old, d                     # every file, same order
        for p in old:
            plan = routing.plan(data, p)
            assert tuple(plan[:4]) == _old_file_plan(data, p), (d, p.name)
        inbound = [p for p in data.iterdir() if p.is_file() and not routing.ignored(p.name)]
        assert sorted(inbound) == sorted(old), d                         # nothing else is inbound (acks etc. ignored)


def test_arrival_order_needs_a_routing():
    with pytest.raises(TypeError):
        arrival_order(SYN / "w1")                                         # no file-name fallback
    with pytest.raises(IRError, match="routing"):
        Routing.of({"feeds": {}})


def _w1_with(tmp, *extra):
    data = tmp / "w1"
    shutil.copytree(SYN / "w1", data)
    for name in extra:
        (data / name).write_bytes(b"not a format any route knows\n")
    return data


def _unrouted(dsn):
    with psycopg.connect(**dsn) as c:
        return c.execute("SELECT batch_id, feed, authority FROM dq_ctl.feed_items WHERE kind='UNROUTED_FILE' "
                         "ORDER BY item_id").fetchall()


def test_unknown_dated_file_reaches_a_night_and_opens_one_feed_item(fresh_cp_dsn, tmp_path):
    data = _w1_with(tmp_path, "zz_unknown_20260115.dat")
    nights = dict(synthetic_nights(data, seed_routing("ZZ")))
    assert "zz_unknown_20260115.dat" in [p.name for p in nights["2026-01-15"]]   # it joins its delivery night
    rt = Runtime(fresh_cp_dsn, dp_path=str(tmp_path / "dp.duckdb"), test_genesis=True)
    out = load_synthetic(rt, data)
    assert out["zz_unknown_20260115.dat"]["routed"] is False
    assert _unrouted(fresh_cp_dsn) == [("B-zz_unknown_20260115.dat", "UNROUTED", "AUTH_INTAKE")]
    with psycopg.connect(**fresh_cp_dsn) as c:
        assert not c.execute("SELECT 1 FROM dq_ctl.batches WHERE batch_id LIKE 'B-zz_unknown_20260115%%'").fetchone()
        assert not c.execute("SELECT 1 FROM dq_ctl.files WHERE batch_id LIKE 'B-zz_unknown_20260115%%'").fetchone()
        got = dict(c.execute("SELECT state, count(*) FROM dq_ctl.units GROUP BY 1").fetchall())
    assert got == EXPECTED                                                # nothing reached claims


def test_resume_of_night_with_unrouted_file(tmp_path):
    data = _w1_with(tmp_path, "zz_unknown_20260101.dat")
    with _sim(tmp_path) as (rt, dsn):
        rt.sim_enable(data, parte=False)
        real = rt.check_missing_deliveries

        def boom(*a, **k):                                                # crash after the night's files
            if "now" not in k:
                return real(*a, **k)
            raise RuntimeError("simulated crash mid-night")
        rt.check_missing_deliveries = boom
        with pytest.raises(RuntimeError):
            rt.sim_run_next("E100")
        rt.check_missing_deliveries = real
        out = rt.sim_run_next("E100")                                     # resumes; the unrouted file is not ingested
        assert "zz_unknown_20260101.dat" in out["files"]
        assert len(_unrouted(dsn)) == 1
        assert rt.sim_nights()["nights"][0]["status"] == "RUN"


def test_undated_unknown_file_opens_item_once(tmp_path):
    data = _w1_with(tmp_path, "README.txt")
    nights = synthetic_nights(data, seed_routing("ZZ"))
    assert [p.name for p in nights[0][1]][-1] == "README.txt"            # undated: opened on the first night only
    assert sum(p.name == "README.txt" for _, fs in nights for p in fs) == 1
    with _sim(tmp_path) as (rt, dsn):
        rt.sim_enable(data, parte=False)
        rt.sim_run_next("E100")
        rt.sim_run_next("E100")
        first = rt.ingest_file(data, data / "README.txt")                 # a repeat opens nothing new
        assert first["routed"] is False
        assert _unrouted(dsn) == [("B-README.txt", "UNROUTED", "AUTH_INTAKE")]


def test_routing_change_is_a_change_request(fresh_cp_dsn, tmp_path):
    s = Slice(fresh_cp_dsn)
    name = "837P_ZZMCOA_20260125_1.x12"
    assert s.file_authorities(name) == ["AUTH_MEDICAID"]
    feeds = s.effective_item("FEEDS", "feeds")
    bad = s.effective_item("FEEDS", "feeds")
    bad["content"]["routing"]["routes"][0]["match"] = "^ie_extract_(?P<date>"           # not a regex
    with pytest.raises(IRError, match="regular expression"):
        s.registry.propose("ZZ", "E100", "broken routing change", [bad])
    bad = s.effective_item("FEEDS", "feeds")
    bad["content"]["routing"]["routes"][0]["pipeline"] = "p_nowhere"
    with pytest.raises(IRError, match="not in the harness"):
        s.registry.propose("ZZ", "E100", "routing to an unknown pipeline", [bad])
    feeds["content"]["routing"]["routes"] = [r for r in feeds["content"]["routing"]["routes"]
                                             if r["format"] != "X12_837P"]
    cr = s.registry.propose("ZZ", "E100", "stop routing professional encounters", [feeds])
    assert cr["loosening"] and cr["approvals_required"] == 2
    assert "inbound file routing changed" in cr["impact"]["loosening_reasons"]
    cr, status = s.govern([feeds], approvers=("checker", "checker2"))
    assert status == "APPLIED"
    assert s.file_authorities(name) is None and s._file_plan(SYN / "w1", SYN / "w1" / name) is None
    out = s.ingest_file(SYN / "w1", SYN / "w1" / name)                     # now unrouted: an item, no batch
    assert out["routed"] is False and _unrouted(fresh_cp_dsn) == [("B-837P_ZZMCOA_20260125_1.x12", "UNROUTED",
                                                                    "AUTH_INTAKE")]


def test_batch_ids_are_collision_proof_and_w1_ids_are_unchanged(fresh_cp_dsn, tmp_path):
    """Red team Nit 7. Routed files keep B-<stem> (unit keys embed the batch id, so w1's ids and the agent-eval digests
    must not move): proven against the pre-F-02 rule on all five synthetic runs. An unrouted file uses B-<full name>,
    so a stray `x.bak` never shares the batch of a routed `x.txt`; any remaining collision in a delivery is refused."""
    routing = Routing(seed_routing("ZZ"))
    for d in DIRS:
        for p in _old_arrival_order(SYN / d):
            assert routing.batch_id(p) == "B-" + p.stem == _old_file_plan(SYN / d, p)[1]
    data = _w1_with(tmp_path, "ie_extract_20260101.bak")                 # same stem as a routed IE file
    assert routing.batch_id(data / "ie_extract_20260101.bak") == "B-ie_extract_20260101.bak"
    assert routing.batch_id(data / "ie_extract_20260101.txt") == "B-ie_extract_20260101"
    synthetic_nights(data, routing)                                       # no collision: listed fine
    (data / "ie_extract_20260101").write_bytes(b"no extension\n")       # unrouted B-<name> == routed B-<stem>
    with pytest.raises(IRError, match="batch id collision"):
        synthetic_nights(data, routing)
    loose = seed_routing("ZZ")                                            # a loose route matching two extensions
    loose["routes"][0]["match"] = r"^ie_extract_(?P<date>\d{8})\.(txt|csv)$"
    data2 = _w1_with(tmp_path / "b", "ie_extract_20260101.csv")
    with pytest.raises(IRError, match="batch id collision"):
        synthetic_nights(data2, Routing(loose))


def test_routing_change_needs_two_approvers_in_the_database(fresh_cp_dsn):
    """Red team I6: the DATABASE refuses one approver for a FEEDS routing change even when the caller says the change
    is not loosening (gov_propose with p_loosening = false). A FEEDS change that leaves routing alone stays one."""
    import json
    s = Slice(fresh_cp_dsn)
    feeds = s.effective_item("FEEDS", "feeds")
    same = s.effective_item("FEEDS", "feeds")
    feeds["content"]["routing"]["ignore"].append("^notes\\.txt$")
    same["content"]["not_delivered_elements"] = []                         # unchanged routing
    with psycopg.connect(**{**fresh_cp_dsn, "user": "dq_api"}) as c:
        for cr, item in (("CR-ROUTE-1", feeds), ("CR-ROUTE-0", same)):
            c.execute("SELECT dq_ctl.gov_propose(%s,'ZZ','E100','HUMAN','a routing change, said to be harmless',"
                      "%s::jsonb,'{}'::jsonb,'{}'::jsonb,false,'h','{}'::jsonb,'{}'::jsonb)", (cr, json.dumps([item])))
        rows = dict(c.execute("SELECT cr_id, approvals_required FROM dq_ctl.config_change_requests").fetchall())
    assert rows == {"CR-ROUTE-1": 2, "CR-ROUTE-0": 1}
    with psycopg.connect(**{**fresh_cp_dsn, "user": "dq_registry_svc"}) as c:   # gov_apply re-derives it too
        assert c.execute("SELECT dq_ctl.gov_items_lower_only('ZZ', %s::jsonb)", (json.dumps([feeds]),)).fetchone()[0] \
            is False


def test_feed_ack_codes_are_validated_per_kind(fresh_cp_dsn, tmp_path):
    """Orchestrator decision: UNROUTED_FILE takes ROUTE_PROPOSED | FILE_REJECTED | RESUBMIT_REQUESTED; a delivery item
    keeps its own codes; neither accepts the other's (the database decides)."""
    from dqagent.cp.governance_schema import FEED_ACK_CODES, FEED_ACK_CODES_BY_KIND
    s = Slice(fresh_cp_dsn)
    unrouted = s.ingest_file(tmp_path, tmp_path / "stray_20260101.dat")["feed_items"][0]
    ref = json.dumps({"facts_hash": "a" * 64, "text_hash": "b" * 64})

    def ack(item, code):
        with psycopg.connect(**{**fresh_cp_dsn, "user": "dq_api"}) as c:
            c.execute("SELECT dq_ctl.set_scope(ARRAY['AUTH_INTAKE'])")
            c.execute("SELECT dq_ctl.feed_item_ack(%s,'E200',%s,NULL,%s::jsonb)", (item, code, ref))
    for code in ("NO_ACTIVITY", "HOLIDAY", "REDELIVERY_REQUESTED", "BOGUS"):
        with pytest.raises(psycopg.errors.CheckViolation, match="UNROUTED_FILE"):
            ack(unrouted, code)
    ack(unrouted, "ROUTE_PROPOSED")
    assert set(FEED_ACK_CODES) == {c for v in FEED_ACK_CODES_BY_KIND.values() for c in v}
    assert FEED_ACK_CODES_BY_KIND["RECOVERY_DATA_LOST"] == ("REDELIVERY_REQUESTED", "RESUBMIT_REQUESTED")
    with psycopg.connect(**{**fresh_cp_dsn, "user": "dq_seal_svc"}) as c:
        empty = c.execute("SELECT dq_ctl.feed_item_open('ZZ','IE_EXTRACT','EMPTY_DELIVERY','B-E1',NULL,"
                          "'AUTH_INTAKE',NULL)").fetchone()[0]
    for code in ("ROUTE_PROPOSED", "FILE_REJECTED"):
        with pytest.raises(psycopg.errors.CheckViolation, match="EMPTY_DELIVERY"):
            ack(empty, code)
    ack(empty, "NO_ACTIVITY")
