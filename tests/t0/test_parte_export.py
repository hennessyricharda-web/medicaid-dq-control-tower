"""Part E §8 B3 (export) and §3.3 (parallel-run comparison): deterministic, sorted, allow-listed, suppressed."""
import json
import random
from pathlib import Path

import pytest
import yaml

from dqagent.adapters.metadata import assertions, legacy_totals, lineage

ROOT = Path(__file__).resolve().parents[2]
RULES = [
    {"id": "B-CMP-CLM-POS", "entity": "ENCOUNTER_HEADER", "relation": "bronze.encounter_header", "status": "ACTIVE",
     "dimension": "completeness", "effective_hash": "aaaa"},
    {"id": "R-ENC-007", "entity": "ENCOUNTER_HEADER", "relation": "bronze.encounter_header", "status": "ACTIVE",
     "dimension": "validity", "effective_hash": "bbbb"},
    {"id": "B-CMP-RX-NDC", "entity": "PHARMACY_CLAIM", "relation": None, "status": "NOT_APPLICABLE",
     "dimension": "completeness", "effective_hash": "cccc"},
]
BINDINGS = {"B-CMP-CLM-POS": ["CLM.POS"], "R-ENC-007": ["LINE.PROC_CODE"], "B-CMP-RX-NDC": ["RX.NDC"]}
UNITS = [{"unit_key": "U1", "batch_id": "B1", "node": "bronze.encounter_header"},
         {"unit_key": "U2", "batch_id": "B1", "node": "bronze.encounter_header"},
         {"unit_key": "U3", "batch_id": "B2", "node": "bronze.encounter_header"},
         {"unit_key": "P2", "batch_id": "B2", "node": "bronze.encounter_header", "part2": True}]
RESULTS = [
    {"unit_key": "U1", "eval_version": 1, "rule_id": "B-CMP-CLM-POS", "disposition": "TA_WARN", "evaluated": 40,
     "failed": 30, "ruleset_version": "rs1", "rule_hash": "aaaa", "sample_tokens": ["tok"]},
    {"unit_key": "U1", "eval_version": 2, "rule_id": "B-CMP-CLM-POS", "disposition": "TA_WARN", "evaluated": 40,
     "failed": 3, "ruleset_version": "rs1", "rule_hash": "aaaa"},
    {"unit_key": "U2", "eval_version": 1, "rule_id": "B-CMP-CLM-POS", "disposition": "TA_WARN", "evaluated": 60,
     "failed": 4, "ruleset_version": "rs1", "rule_hash": "aaaa"},
    {"unit_key": "U3", "eval_version": 1, "rule_id": "R-ENC-007", "disposition": "FAIL", "evaluated": 50,
     "failed": 12, "ruleset_version": "rs1", "rule_hash": "bbbb"},
    {"unit_key": "U3", "eval_version": 1, "rule_id": "B-CMP-CLM-POS", "disposition": "PASS", "evaluated": 50,
     "failed": 0, "ruleset_version": "rs1", "rule_hash": "aaaa"},
    {"unit_key": "P2", "eval_version": 1, "rule_id": "R-ENC-007", "disposition": "FAIL", "evaluated": 9,
     "failed": 9, "ruleset_version": "rs1", "rule_hash": "bbbb"},
]


def test_assertions_shape_allow_list_suppression_and_part2():
    rows = assertions.build_assertions(RESULTS, RULES, UNITS, BINDINGS, cr_id="CR-3")
    assert all(tuple(r) == assertions.FIELDS for r in rows)
    by = {(r["rule"], r["batch"], r["disposition"]): r for r in rows}
    b1 = by[("B-CMP-CLM-POS@ENCOUNTER_HEADER", "B1", "TA_WARN")]
    assert b1["evaluated"] == 100 and b1["failed"] is None              # 3 + 4 = 7 failed -> suppressed (1-10)
    assert b1["element"] == "CLM.POS" and b1["asset"] == "bronze.encounter_header" and b1["cr_id"] == "CR-3"
    b2 = by[("R-ENC-007@ENCOUNTER_HEADER", "B2", "FAIL")]
    assert b2["evaluated"] == 50 and b2["failed"] == 12                  # the Part 2 unit is excluded entirely
    assert by[("B-CMP-CLM-POS@ENCOUNTER_HEADER", "B2", "PASS")]["failed"] == 0
    assert "tok" not in json.dumps(rows) and "P2" not in json.dumps(rows)


def test_assertions_are_deterministic_and_sorted():
    a = assertions.build_assertions(RESULTS, RULES, UNITS, BINDINGS)
    shuffled = list(RESULTS)
    random.Random(7).shuffle(shuffled)
    b = assertions.build_assertions(shuffled, list(reversed(RULES)), list(reversed(UNITS)), BINDINGS)
    assert json.dumps(a) == json.dumps(b)
    assert a == sorted(a, key=lambda d: (d["asset"], d["rule"], d["element"] or "", d["batch"], d["disposition"]))


def test_lineage_document_and_typed_edges():
    harness = yaml.safe_load((ROOT / "config" / "harness_w1.yaml").read_text())
    doc = lineage.build_lineage(
        state="ZZ", harness=harness, rules=RULES, bindings=BINDINGS,
        relation_columns={"bronze.encounter_header": ["pos", "encounter_id"]}, units=UNITS, results=RESULTS,
        decisions=[{"decision_id": 9, "unit_key": "U3", "person_id": "E100", "verb": "DENY"},
                   {"decision_id": 10, "unit_key": "P2", "person_id": "E100", "verb": "RELEASE"}],
        releases=[{"unit_key": "U1", "release_id": "M-5"}, {"unit_key": "P2", "release_id": "M-5"}])
    assert doc["producer"] and doc["schemaURL"].startswith("https://openlineage.io/")
    types = {e["type"] for e in doc["edges"]}
    assert types == set(lineage.EDGE_TYPES)
    assert {"type": "binds", "src": "rule:B-CMP-CLM-POS@ENCOUNTER_HEADER", "dst": "element:CLM.POS",
            "props": {}} in doc["edges"]
    assert {"type": "checks", "src": "rule:R-ENC-007@ENCOUNTER_HEADER", "dst": "dataset:bronze.encounter_header",
            "props": {}} in doc["edges"]
    assert {"type": "depends_on", "src": "domain:CLAIMS", "dst": "domain:ELIGIBILITY",
            "props": {"policy": "WAIT"}} in doc["edges"]
    assert {"type": "result_of", "src": "unit:U1", "dst": "rule:B-CMP-CLM-POS@ENCOUNTER_HEADER",
            "props": {"disposition": "TA_WARN"}} in doc["edges"]
    assert not any("RX-NDC" in e["src"] for e in doc["edges"])          # inactive rules carry no edges
    assert "unit:P2" not in json.dumps(doc)                               # Part 2 units excluded
    ds = {d["name"]: d for d in doc["datasets"]}
    assert [f["name"] for f in ds["bronze.encounter_header"]["facets"]["schema"]["fields"]] == ["encounter_id", "pos"]
    assert {j["name"] for j in doc["jobs"]} == {"p4_eligibility_ie", "p6_claims"}
    assert doc["edges"] == sorted(doc["edges"], key=lambda e: (e["type"], e["src"], e["dst"],
                                                               json.dumps(e, sort_keys=True)))
    again = lineage.build_lineage(state="ZZ", harness=harness, rules=list(reversed(RULES)), bindings=BINDINGS,
                                  relation_columns={"bronze.encounter_header": ["encounter_id", "pos"]},
                                  units=UNITS, results=list(reversed(RESULTS)),
                                  decisions=[{"decision_id": 9, "unit_key": "U3", "person_id": "E100", "verb": "DENY"}],
                                  releases=[{"unit_key": "U1", "release_id": "M-5"}])
    assert json.dumps(again, sort_keys=True) == json.dumps(doc, sort_keys=True)


def test_parallel_run_compare_tolerances():
    doc = {"system": "LEGACY_MMIS", "period": "P1", "as_of": "2026-04-01",
           "rows": [{"relation": "r", "measure": "records", "value": 100},
                    {"relation": "r", "measure": "amount:paid", "value": 1000.00},
                    {"relation": "r", "measure": "amount:charge", "value": 1000.00},
                    {"relation": "r", "measure": "rule_failed:R-1", "value": 4},
                    {"relation": "s", "measure": "records", "value": 7}]}
    new = {("r", "records"): 100, ("r", "amount:paid"): 1004.99, ("r", "amount:charge"): 1005.01,
           ("r", "rule_failed:R-1"): 5}
    out = {(x["relation"], x["measure"]): x for x in legacy_totals.compare(doc, new, period="P1", as_of="2026-04-01")}
    assert out[("r", "records")]["status"] == "MATCH" and out[("r", "records")]["delta"] == 0
    assert out[("r", "amount:paid")]["status"] == "WITHIN"            # 4.99 <= 0.5% of 1000
    assert out[("r", "amount:charge")]["status"] == "BREAK"           # 5.01 > 5.00
    assert out[("r", "rule_failed:R-1")]["status"] == "BREAK"
    assert out[("s", "records")]["status"] == "BREAK" and out[("s", "records")]["new"] is None
    assert all(x["explain_code"] is None for x in out.values())
    with pytest.raises(ValueError):
        legacy_totals.compare(doc, new, period="P2", as_of="2026-04-01")
