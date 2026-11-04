"""Part E synthetic data: w1 legacy totals, w1 monitor history, and the w2_earn run (§1.5, §3.3, §6).
Everything is deterministic: regenerating into a temp dir reproduces the committed bytes. w1's own files are
never regenerated (tests pin w1 at 420 / 291 / 47)."""
import json
import os
import re
from pathlib import Path

import pytest

from dqagent.adapters.metadata import legacy_totals as lt
from dqagent.agent import monitor as m
from synthetic import parte

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "synthetic" / "out"
W1_EXTRAS = ("legacy_totals.json", "monitor_history.json", "parte_manifest.json")


def test_w1_extras_are_deterministic(tmp_path):
    (tmp_path / "w1").mkdir()
    parte.write_w1_extras(tmp_path)
    for name in W1_EXTRAS:
        assert (tmp_path / "w1" / name).read_bytes() == (OUT / "w1" / name).read_bytes(), name


def test_w1_extras_never_generate_w1(tmp_path):
    with pytest.raises(SystemExit):
        parte.write_w1_extras(tmp_path)                 # no w1 dir: refuses rather than generating one


def test_legacy_totals_have_one_timing_and_one_dollar_break():
    doc = lt.load(OUT / "w1" / "legacy_totals.json")
    man = json.loads((OUT / "w1" / "parte_manifest.json").read_text())
    assert (doc["period"], doc["as_of"]) == (man["period"], man["as_of"])
    new = lt.new_side_from_metrics(man["w1_observed"])
    got = lt.compare(doc, new, period=doc["period"], as_of=doc["as_of"], tolerance=man["parallel_run"]["tolerance"])
    exp = {(e["relation"], e["measure"]): e for e in man["parallel_run"]["expected"]}
    assert {(g["relation"], g["measure"]) for g in got} == set(exp)
    for g in got:
        assert g["status"] == exp[(g["relation"], g["measure"])]["status"], g
    assert {g["status"] for g in got} == {"MATCH", "WITHIN", "BREAK"}
    breaks = [e for e in exp.values() if e["status"] == "BREAK"]
    assert sorted(e["planted_as"] for e in breaks) == ["AMOUNT", "TIMING"]
    timing = next(e for e in breaks if e["planted_as"] == "TIMING")
    assert timing["measure"] == "records" and timing["new"] - timing["legacy"] == parte.W1_834_LATE_FILE_ROWS
    dollar = next(e for e in breaks if e["planted_as"] == "AMOUNT")
    assert dollar["measure"].startswith("amount:") and round(dollar["legacy"] - dollar["new"], 2) == 1250.00


def test_monitor_history_is_aggregate_only_with_12_prior_periods():
    h = m.load_history(OUT / "w1" / "monitor_history.json")
    assert h["periods"] == parte.PRIOR_PERIODS and len(h["periods"]) == 12
    assert all(p < h["current_period"] for p in h["periods"])
    assert set(h["spec"]) == {"bronze.encounter_header", "bronze.encounter_line", "bronze.ie_segment",
                              "bronze.enrollment_834"}
    for r in h["metrics"]:
        assert set(r) == {"period", "relation", "metric", "key", "value", "n"}
        assert isinstance(r["value"], (int, float)) and isinstance(r["n"], int)
    per_series = {}
    for r in h["metrics"]:
        per_series.setdefault((r["relation"], r["metric"], r["key"]), set()).add(r["period"])
    assert all(len(v) == 12 for v in per_series.values())
    assert h["exclude"][("bronze.encounter_line", "rows", "")] == ["2024-Q3"]
    for r in h["metrics"]:
        if r["metric"].startswith("mix:"):
            assert r["key"] not in ("", None)


def test_w1_observed_matches_spec_and_bindings():
    man = json.loads((OUT / "w1" / "parte_manifest.json").read_text())
    rels = {r["relation"] for r in man["w1_observed"]}
    assert rels == set(parte.SPEC)
    rows = {r["relation"]: r["value"] for r in man["w1_observed"] if r["metric"] == "rows"}
    assert rows == {"bronze.encounter_header": 164, "bronze.encounter_line": 311, "bronze.ie_segment": 470,
                    "bronze.enrollment_834": 174}


@pytest.mark.skipif(not os.environ.get("DQ_W1_DP"), reason="set DQ_W1_DP to a w1 data-plane DuckDB to re-verify")
def test_w1_observed_equals_the_w1_data_plane():
    parte.verify_dp(os.environ["DQ_W1_DP"])


def test_w2_earn_is_deterministic_and_aggregate_safe(tmp_path):
    r = parte.write_w2_earn(tmp_path, commit_log=tmp_path / "log")
    committed = OUT / "w2_earn"
    names = sorted(p.name for p in committed.iterdir())
    assert names == sorted(p.name for p in r["out_dir"].iterdir())
    for n in names:
        assert (r["out_dir"] / n).read_bytes() == (committed / n).read_bytes(), n
    man = json.loads((committed / "earn_manifest.json").read_text())
    assert man["mode"] == "realistic" and man["start_tier"] == "L2" and man["promote_to"] == "L3"
    assert json.loads((committed / "ledger.json").read_text()) == []          # realistic: no seeded defects
    phases = {p["phase"]: p for p in man["phases"]}
    assert list(phases) == ["APPROVE", "PROMOTE", "VETO", "LOOKBACK"]
    units = {b["batch_id"]: b["medicaid_person_units"] for b in man["ie_batches"]}
    assert len(phases["APPROVE"]["batches"]) >= 3 and all(units[b] >= 1 for b in phases["APPROVE"]["batches"])
    assert units[phases["VETO"]["batches"][0]] >= 2 and phases["PROMOTE"]["approvers_required"] == 2
    order = [b["batch_id"] for b in man["ie_batches"]]
    assert order.index(phases["VETO"]["batches"][0]) > max(order.index(b) for b in phases["APPROVE"]["batches"])
    text = (committed / "earn_manifest.json").read_text()
    assert not re.search(r"ZZ[0-9]{8}|IE[0-9]{5,}|HH[0-9]{5,}", text)         # no member / person / household ids
