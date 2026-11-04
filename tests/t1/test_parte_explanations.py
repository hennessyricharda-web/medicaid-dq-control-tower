"""Part E §7 CONTRACT test: every GET endpoint that returns an explainable object carries a non-empty plain-language
`explanation` {text, source, fact_check, facts_hash, text_hash}, and the default (template) text is byte-stable.

Explainable objects (PARTE_DESIGN §7): card dispositions, the unit decision card, change requests and their impact,
look-back items, monitor signals, parallel-run rows (breaks), feed items and demotions (plus autonomy rows and CDE
rows, which the Command Tower also explains)."""
import json
import re

import pytest
from fastapi.testclient import TestClient

from dqagent.api.app import create_app

from .fixtures_w1 import ie_file, person
from .w1_harness import Slice

AGENT = "dq-reconciliation"
HEX = re.compile(r"^[0-9a-f]{64}$")
MONITOR_HISTORY = "synthetic/out/w1/monitor_history.json"


def H(rt, user, **kw):
    return {"Authorization": f"Bearer {rt.idp.token(user, **kw)}"}


def assert_explained(obj, where):
    e = obj.get("explanation") if isinstance(obj, dict) else None
    assert isinstance(e, dict), f"{where}: no explanation"
    assert isinstance(e.get("text"), str) and e["text"].strip(), f"{where}: empty explanation text"
    assert e.get("source") and e.get("fact_check") in ("TEMPLATE", "PASS", "FALLBACK"), f"{where}: {e}"
    assert HEX.match(e.get("facts_hash", "")) and HEX.match(e.get("text_hash", "")), f"{where}: hashes {e}"


def _seed_monitor(s):
    from pathlib import Path

    from dqagent.rules.model import ROOT
    hist = json.loads(Path(ROOT / MONITOR_HISTORY).read_text())
    last = max(hist["periods"])
    cur = [dict(r, period="2026-Q1") for r in hist["metrics"] if r["period"] == last]
    for r in cur:                                   # planted: a volume drop on the header relation
        if r["relation"] == "bronze.encounter_header" and r["metric"] == "rows":
            r["value"] = round(r["value"] * 0.3)
    # stored as the seat stores them: history periods (HISTORY:<p>) and the CLOSED current period (PERIOD:<p>);
    # a period that has not closed is PROVISIONAL and never shows a signal (live smoke L4)
    with s.cp.as_role("dq_monitor_svc") as c:
        for r in hist["metrics"] + cur:
            pb = f"PERIOD:{r['period']}" if r["period"] == "2026-Q1" else f"HISTORY:{r['period']}"
            c.execute("INSERT INTO dq_ctl.monitor_metrics(batch_id,period,relation,metric,key,value,n,authority) "
                      "VALUES (%s,%s,%s,%s,%s,%s,%s,'AUTH_MEDICAID')",
                      (pb, r["period"], r["relation"], r["metric"], r["key"], r["value"], r["n"]))


def _seed_parallel_and_feeds(s):
    with s.cp.as_role("dq_seal_svc") as c:
        for rel, m, leg, new, delta, tol, status in (
                ("bronze.encounter_header", "records", 164, 164, 0, 0, "MATCH"),
                ("bronze.encounter_header", "amount:mco_paid", 36200.0, 36241.64, 41.64, 0.005, "WITHIN"),
                ("bronze.enrollment_834", "records", 140, 174, 34, 0, "BREAK")):
            c.execute("INSERT INTO dq_ctl.parallel_run_results(run_id,system,period,as_of,relation,measure,legacy,new,"
                      "delta,tolerance,status,authority) VALUES ('PR-1','LEGACY','2026-Q1','2026-04-01',%s,%s,%s,%s,%s,"
                      "%s,%s,'AUTH_MEDICAID')", (rel, m, leg, new, delta, tol, status))
        c.execute("SELECT dq_ctl.feed_item_open('ZZ','X12_837P','EMPTY_DELIVERY','B-EMPTY','F-EMPTY','AUTH_MEDICAID',NULL)")


@pytest.fixture(scope="module")
def world(cp_dsn_module, tmp_path_factory):
    s = Slice(cp_dsn_module)
    tmp = tmp_path_factory.mktemp("w")
    clean = tmp / "clean.txt"
    clean.write_bytes(ie_file([person("P1", "M0001"), person("P2", "M0002")]))
    s.run_pipeline("p4_eligibility_ie", "B-ELG-1", [("IE", clean)], ["bronze.ie_segment"])
    assert len(s.verify_and_publish()["released"]) == 2
    rule = next(r for r in s.rules if r.id == "R-ELG-001")           # revoke -> look-back items over the releases
    rev = {"artifact_id": f"ZZ:revocation:R-ELG-001@ELIG_SEGMENT:{rule.effective_hash[:12]}", "kind": "REVOCATION",
           "op": "PUT", "content": {"rule": "ZZ:R-ELG-001@ELIG_SEGMENT", "hash": rule.effective_hash,
                                    "reason": "rule logic found defective after release"}}
    fix = {"artifact_id": "ZZ:overlay:R-ELG-001", "kind": "OVERLAY", "op": "PUT",
           "content": {"verb": "raise_priority", "priority": "Critical", "reason": "corrected version",
                       "approved_ref": "SCR-2"}}
    assert s.govern([rev, fix])[1] == "APPLIED"
    held = tmp / "held.txt"
    held.write_bytes(ie_file([person("P3", "M0003"), person("P4", "M0004")], trailer=7))   # ABC fail -> HELD
    s.run_pipeline("p4_eligibility_ie", "B-HELD", [("IE", held)], ["bronze.ie_segment"])
    api = TestClient(create_app(s))
    up = s.effective_item("AUTONOMY", f"autonomy:{AGENT}:RELEASE")
    up["content"]["AUTH_SNAP"] = "L3"
    r = api.post("/api/v1/governance/change-requests", headers=H(s, "maker"),
                 json={"rationale": "Promote SNAP release to supervised", "items": [up]})
    assert r.status_code == 200, r.text
    assert s.registry.demote(AGENT, "RELEASE", "AUTH_MEDICAID", "L3", "VETO", "unit:ZZ-EXAMPLE") == 1
    _seed_monitor(s)
    _seed_parallel_and_feeds(s)
    return s, api, r.json()["cr_id"]


@pytest.fixture(scope="module")
def cp_dsn_module():
    import uuid

    import psycopg

    from dqagent.cp import schema

    from .conftest import PG
    try:
        admin = psycopg.connect(dbname="postgres", autocommit=True, **PG)
    except Exception as e:  # pragma: no cover
        pytest.skip(f"T1 Postgres not reachable: {e}")
    db = "dqcp_" + uuid.uuid4().hex[:8]
    admin.execute(f"CREATE DATABASE {db}")
    dsn = dict(dbname=db, **PG)
    with psycopg.connect(**dsn) as c:
        schema.apply(c)
    yield dsn
    admin.execute(f"DROP DATABASE {db} WITH (FORCE)")
    admin.close()


def sweep(s, api, cr_id) -> dict:
    """Walk every GET endpoint that returns explainable objects; return {where: explanation}."""
    h = H(s, "maker")
    seen = {}

    def take(obj, where):
        assert_explained(obj, where)
        seen[where] = obj["explanation"]

    q = api.get("/api/v1/queue", headers=h).json()
    units = sorted({u for cl in q for u in cl["units"]})
    assert units, "the fixture must hold at least one unit"
    for uk in units:
        card = api.get(f"/api/v1/units/{uk}", headers=h).json()
        take(card, f"card {uk}")
        assert card["dispositions"]
        for d in card["dispositions"]:
            take(d, f"card {uk} disposition {d['rule_id']}")
    crs = api.get("/api/v1/governance/change-requests", headers=h).json()
    ids = {c["cr_id"] for c in crs}
    assert cr_id in ids and any(i.startswith("DEM-") for i in ids)
    for cid in sorted(ids):
        d = api.get(f"/api/v1/governance/change-requests/{cid}", headers=h).json()
        take(d, f"cr {cid}")
        take(d["impact"], f"cr {cid} impact")
    lb = api.get("/api/v1/lookback", headers=h).json()
    assert lb
    for it in lb:
        take(it, f"lookback {it['item_id']}")
    mon = api.get("/api/v1/monitor", headers=h).json()
    assert any(m["status"] in ("SIGNAL", "WATCH", "SHIFT") for m in mon)
    for m in mon:
        take(m, f"monitor {m['relation']} {m['metric']}")
        if m["last_signal"]:
            take(m["last_signal"], f"monitor signal {m['relation']} {m['metric']}")
        params = {"relation": m["relation"], "metric": m["metric"], **({"key": m["key"]} if m["key"] else {})}
        ser = api.get("/api/v1/monitor/series", headers=h, params=params).json()
        for i, sig in enumerate(ser["signals"]):
            take(sig, f"series {m['relation']} {m['metric']} signal {i}")
    pr = api.get("/api/v1/parallel-run", headers=h).json()
    assert {r["status"] for r in pr} == {"MATCH", "WITHIN", "BREAK"}
    for r in pr:
        take(r, f"parallel-run {r['row_id']}")
    fi = api.get("/api/v1/feeds/items", headers=h).json()
    assert fi
    for it in fi:
        take(it, f"feed item {it['item_id']}")
    au = api.get("/api/v1/autonomy", headers=h).json()
    assert any(r["demotions"] for r in au)
    for r in au:
        take(r, f"autonomy {r['action_class']} {r['authority']}")
        for i, d in enumerate(r["demotions"]):
            take(d, f"demotion {r['action_class']} {r['authority']} {i}")
    cov = api.get("/api/v1/coverage", headers=h, params={"cde": "true"}).json()
    assert cov["cde"]
    for c in cov["cde"]:
        take(c, f"cde {c['element']}")
    return seen


def test_every_explainable_object_carries_an_explanation(world):
    s, api, cr_id = world
    seen = sweep(s, api, cr_id)
    kinds = {k.split()[0] for k in seen}
    assert {"card", "cr", "lookback", "monitor", "series", "parallel-run", "feed", "autonomy", "demotion",
            "cde"} <= kinds, kinds


def test_template_explanations_are_byte_stable_across_calls(world):
    s, api, cr_id = world
    one, two = sweep(s, api, cr_id), sweep(s, api, cr_id)
    assert one.keys() == two.keys()
    for k in one:
        assert json.dumps(one[k], sort_keys=True).encode() == json.dumps(two[k], sort_keys=True).encode(), k
        assert one[k]["source"] == "template" and one[k]["fact_check"] == "TEMPLATE", k


def test_explanations_carry_no_record_values(world):
    """Facts come from control-plane rows and aggregates only: no member ids / names from the data plane."""
    s, api, cr_id = world
    blob = json.dumps(sweep(s, api, cr_id))
    for v in ("M0001", "M0002", "M0003", "M0004", "Jane", "Doe", "19800101"):
        assert v not in blob, v
