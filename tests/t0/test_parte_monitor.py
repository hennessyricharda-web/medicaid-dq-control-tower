"""Part E §6: MONITOR seat maths on known series (limits computed by hand), run rules, PSI binning, frozen
baselines, and the planted w1 signals (with clean relations as the negative control)."""
import json
import math
from pathlib import Path

import duckdb
import pytest

from dqagent.agent import monitor as m

ROOT = Path(__file__).resolve().parents[2]
W1 = ROOT / "synthetic" / "out" / "w1"


def _rows(rel, metric, values, ns=None, key=""):
    ns = ns or [v if metric == "rows" else 100 for v in values]
    return [{"period": f"P{i:02d}", "relation": rel, "metric": metric, "key": key, "value": v, "n": n}
            for i, (v, n) in enumerate(zip(values, ns))]


# ------------------------------------------------------------------------------------------------ XmR
def test_xmr_limits_match_hand_calculation():
    xs = [10, 12, 11, 13, 12, 11, 10, 12]
    # mean = 91 / 8 = 11.375 ; moving ranges 2,1,2,1,1,1,2 -> MR-bar = 10 / 7 = 1.4285714
    lim = m.xmr_limits(xs)
    assert lim["cl"] == pytest.approx(11.375)
    assert lim["mr_bar"] == pytest.approx(10 / 7)
    assert lim["ucl"] == pytest.approx(11.375 + 2.66 * 10 / 7)      # 15.175
    assert lim["lcl"] == pytest.approx(11.375 - 2.66 * 10 / 7)      # 7.575
    assert lim["sigma"] == pytest.approx((10 / 7) / 1.128)
    b = m.build_baseline(_rows("r", "rows", xs), "r", "rows")
    assert b["status"] == "FROZEN" and b["ucl"] == pytest.approx(15.175) and b["lcl"] == pytest.approx(7.575)
    assert m.evaluate(b, {"value": 15, "n": 15})["status"] == "OK"
    hi = m.evaluate(b, {"value": 16, "n": 16})
    lo = m.evaluate(b, {"value": 7, "n": 7})
    assert hi["status"] == "SIGNAL" and hi["signals"][0] == {"rule": "R1", "direction": "HIGH",
                                                             "detail": "point beyond the 3-sigma limits"}
    assert lo["signals"][0]["direction"] == "LOW"


def test_r2_is_off_by_default_and_fires_when_enabled():
    xs = [10, 12, 11, 13, 12, 11, 10, 12]
    b = m.build_baseline(_rows("r", "rows", xs), "r", "rows")
    recent = [12, 12.5, 13, 12, 12.2, 12.4, 13]              # 7 prior points above CL 11.375
    assert m.evaluate(b, {"value": 12, "n": 12}, recent=recent)["status"] == "OK"
    r2 = m.evaluate(b, {"value": 12, "n": 12}, recent=recent, cfg={"rules": {"R2": True}})
    assert [s["rule"] for s in r2["signals"]] == ["R2"]
    assert m.evaluate(b, {"value": 11, "n": 11}, recent=recent, cfg={"rules": {"R2": True}})["status"] == "OK"
    assert m.config({"rules": {"R1": False}})["rules"]["R1"] is True           # R1 cannot be switched off


# ------------------------------------------------------------------------------------------------ p-chart
def test_p_chart_uses_per_n_limits():
    nulls = [5, 6, 4, 5, 5, 6, 4, 5]                          # n = 100 each -> p-bar = 40 / 800 = 0.05
    rows = _rows("r", "null_rate:X.Y", [k / 100 for k in nulls], ns=[100] * 8)
    b = m.build_baseline(rows, "r", "null_rate:X.Y")
    assert b["p_bar"] == pytest.approx(0.05)
    r100 = m.evaluate(b, {"value": 0.10, "n": 100})
    assert r100["ucl"] == pytest.approx(0.05 + 3 * math.sqrt(0.05 * 0.95 / 100), abs=1e-6)   # 0.115383
    assert r100["lcl"] == 0.0 and r100["status"] == "OK"
    r400 = m.evaluate(b, {"value": 0.10, "n": 400})          # same rate, bigger delivery -> tighter limits
    assert r400["ucl"] == pytest.approx(0.05 + 3 * math.sqrt(0.05 * 0.95 / 400), abs=1e-6)   # 0.082692
    assert r400["lcl"] == pytest.approx(0.05 - 3 * math.sqrt(0.05 * 0.95 / 400), abs=1e-6)   # 0.017308
    assert r400["status"] == "SIGNAL" and r400["signals"][0]["direction"] == "HIGH"


# ------------------------------------------------------------------------------------------------ PSI
def test_psi_matches_hand_calculation():
    # expected 50/50, actual 78/22 of 100: 0.28*ln(1.56) + 0.28*ln(0.5/0.22) = 0.124512 + 0.229875
    r = m.psi({"A": 0.5, "B": 0.5}, {"A": 78, "B": 22})
    assert r["computed"] and r["psi"] == pytest.approx(0.28 * math.log(0.78 / 0.5) + 0.28 * math.log(0.5 / 0.22))
    assert r["psi"] == pytest.approx(0.354387, abs=1e-6)


def test_psi_folds_rare_bins_into_other_and_keeps_other():
    exp = {"A": 0.80, "B": 0.10, "C": 0.06, "D": 0.03, "E": 0.01}
    r = m.psi(exp, {"A": 80, "B": 10, "C": 6, "D": 3, "E": 1})          # n = 100: D (3) and E (1) are < 5
    # D + E fold into OTHER (expected 4); OTHER is still < 5, so the next-smallest (C, 6) joins it: OTHER = 10
    assert set(r["bins"]) == {"A", "B", "OTHER"} and r["bins"]["OTHER"]["expected_count"] == pytest.approx(10)
    assert r["bins"]["OTHER"]["actual_count"] == 10
    assert r["computed"] and r["psi"] == pytest.approx(0.0, abs=1e-9)
    new_cat = m.psi({"A": 0.5, "B": 0.5}, {"A": 50, "B": 45, "Z": 5})   # unseen category lands in OTHER
    # OTHER (expected 0) is kept, not dropped: it absorbs the smallest expected bin until it reaches 5 expected
    assert set(new_cat["bins"]) == {"B", "OTHER"} and new_cat["bins"]["OTHER"]["actual_count"] == 55
    assert new_cat["computed"] and all(b["expected_count"] >= 5 for b in new_cat["bins"].values())
    tiny = m.psi({"A": 0.97, "B": 0.03}, {"A": 97, "B": 3})
    assert not tiny["computed"] and "fewer than 2 bins" in tiny["reason"]


def test_psi_status_bands():
    hist = []
    for i in range(8):
        hist += [{"period": f"P{i}", "relation": "r", "metric": "mix:E.L", "key": "A", "value": 0.5, "n": 200},
                 {"period": f"P{i}", "relation": "r", "metric": "mix:E.L", "key": "B", "value": 0.5, "n": 200}]
    b = m.build_baseline(hist, "r", "mix:E.L")
    cur = lambda a: [{"key": "A", "value": a, "n": 200}, {"key": "B", "value": 1 - a, "n": 200}]
    assert m.evaluate(b, cur(0.55))["status"] == "OK"         # PSI 0.0201
    assert m.evaluate(b, cur(0.66))["status"] == "WATCH"      # PSI 0.1047
    assert m.evaluate(b, cur(0.78))["status"] == "SHIFT"      # PSI 0.3544


# ------------------------------------------------------------------------------------------------ baselines
def test_baseline_needs_8_periods_excludes_signalled_and_is_frozen():
    xs = [100, 102, 98, 101, 99, 100, 103, 97, 101]
    rows = _rows("r", "rows", xs)
    building = m.build_baseline(rows[:7], "r", "rows")
    assert building["status"] == "BASELINE_BUILDING"
    res = m.evaluate(building, {"value": 10, "n": 10})
    assert res["status"] == "BASELINE_BUILDING" and res["signals"] == []
    assert m.board_post(res)[0] == "BASELINE_BUILDING"
    spiked = [dict(r) for r in rows]
    spiked[4]["value"] = 160
    poisoned = m.build_baseline(spiked, "r", "rows")
    clean = m.build_baseline(spiked, "r", "rows", exclude_periods=["P04"])
    assert clean["excluded_periods"] == ["P04"] and "P04" not in clean["periods"] and len(clean["periods"]) == 8
    assert clean["ucl"] < poisoned["ucl"]
    assert m.evaluate(clean, {"value": 112, "n": 112})["status"] == "SIGNAL"
    assert m.evaluate(poisoned, {"value": 112, "n": 112})["status"] == "OK"     # what poisoning would hide
    assert m.verify_baseline(clean)
    tampered = dict(clean, ucl=999.0)
    assert not m.verify_baseline(tampered)
    with pytest.raises(ValueError):
        m.evaluate(tampered, {"value": 1, "n": 1})
    later = rows + [{"period": "P99", "relation": "r", "metric": "rows", "key": "", "value": 5000, "n": 5000}]
    pinned = m.run(later, [{"period": "Q", "relation": "r", "metric": "rows", "key": "", "value": 100, "n": 100}],
                   period="Q", baselines={("r", "rows", ""): clean})
    assert pinned[0]["baseline_hash"] == clean["baseline_hash"]                  # a pinned baseline is reused


# ------------------------------------------------------------------------------------------------ planted w1
def _w1_results(cfg=None):
    h = m.load_history(W1 / "monitor_history.json")
    man = json.loads((W1 / "parte_manifest.json").read_text())
    return m.run(h["metrics"], man["w1_observed"], period=man["period"], exclude=h["exclude"], cfg=cfg), man


def test_planted_signals_fire_on_w1():
    res, man = _w1_results()
    by = {(r["relation"], r["metric"], r["key"]): r for r in res}
    for s in man["monitor"]["expected_signals"]:
        r = by[(s["relation"], s["metric"], s["key"])]
        assert r["baseline_periods"] >= 8, s
        if s["rule"] == "PSI":
            assert r["status"] == s["direction"] == "SHIFT" and r["value"] >= 0.25, r
            assert m.board_post(r)[0] == "MIX_SHIFT"
        else:
            assert r["status"] == "SIGNAL" and [(x["rule"], x["direction"]) for x in r["signals"]] == \
                [(s["rule"], s["direction"])], r
            assert m.board_post(r)[0] == "SPC_SIGNAL"
    kinds = {s["planted_as"].split(" (")[0] for s in man["monitor"]["expected_signals"]}
    assert kinds == {"volume drop", "null-rate jump", "mix shift"}
    assert len({s["relation"] for s in man["monitor"]["expected_signals"]}) == 3


def test_clean_relations_stay_silent_negative_control():
    res, man = _w1_results()
    by = {(r["relation"], r["metric"], r["key"]): r for r in res}
    assert man["monitor"]["clean_relations"] == ["bronze.encounter_line"]
    for c in man["monitor"]["clean_series"]:
        r = by[(c["relation"], c["metric"], c["key"])]
        assert r["status"] == "OK" and r["signals"] == [], r
        assert m.board_post(r) is None
    assert len(res) == len(man["monitor"]["expected_signals"]) + len(man["monitor"]["clean_series"])
    # the old signalled spike is excluded; including it would widen the clean relation's limits
    line = by[("bronze.encounter_line", "rows", "")]
    assert line["excluded_periods"] == ["2024-Q3"] and line["baseline_periods"] == 11
    res_r2, _ = _w1_results({"rules": {"R2": True}})
    assert all(s["rule"] != "R2" for r in res_r2 for s in r["signals"])


def test_query_aggregates_on_a_duckdb_relation():
    con = duckdb.connect()
    con.execute("CREATE SCHEMA bronze")
    con.execute("CREATE TABLE bronze.t(_dq_batch_id VARCHAR, amt DECIMAL(18,2), code VARCHAR, opt DATE)")
    con.execute("INSERT INTO bronze.t VALUES ('B1', 10.50, 'A', NULL), ('B1', 2.25, 'B', DATE '2026-01-01'), "
                "('B2', 1.00, 'A', NULL), ('B2', 4.00, NULL, NULL)")
    spec = {"rows": True, "amount": ["amt"], "null_rate": ["T.OPT"], "mix": ["T.CODE"]}
    got = m.query_aggregates(con, "bronze.t", spec, {"T.OPT": "opt", "T.CODE": "code"}, period="P")
    assert got == [
        {"period": "P", "relation": "bronze.t", "metric": "amount:amt", "key": "", "value": 17.75, "n": 4},
        {"period": "P", "relation": "bronze.t", "metric": "mix:T.CODE", "key": "(null)", "value": 0.25, "n": 4},
        {"period": "P", "relation": "bronze.t", "metric": "mix:T.CODE", "key": "A", "value": 0.5, "n": 4},
        {"period": "P", "relation": "bronze.t", "metric": "mix:T.CODE", "key": "B", "value": 0.25, "n": 4},
        {"period": "P", "relation": "bronze.t", "metric": "null_rate:T.OPT", "key": "", "value": 0.75, "n": 4},
        {"period": "P", "relation": "bronze.t", "metric": "rows", "key": "", "value": 4, "n": 4}]
    b1 = m.query_aggregates(con, "bronze.t", {"rows": True}, {}, period="P", batch_ids=["B1"])
    assert b1[0]["value"] == 2
    with pytest.raises(ValueError):
        m.query_aggregates(con, "bronze.t; DROP TABLE x", {"rows": True}, {}, period="P")
