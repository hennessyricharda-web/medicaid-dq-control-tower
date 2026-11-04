"""Part E §4: critical data elements and minimum viable DQ (advisory). Plain-Python inputs, no Postgres."""
import pytest

from dqagent.rules import cde
from dqagent.rules.model import compile_rules

CDE = {"CLM.POS": {"mvdq": {"completeness_min": 0.98, "validity_min": 0.95}, "steward_domain": "CLAIMS"},
       "CLM.DX1": {"mvdq": {"completeness_min": 0.98, "validity_min": 0.95}, "steward_domain": "CLAIMS"},
       "RX.NDC": {"mvdq": {"completeness_min": 0.98, "validity_min": 0.95}, "steward_domain": "PHARMACY"},
       "ELG.DOB": {"mvdq": {"completeness_min": 0.99}, "steward_domain": "ELIGIBILITY"}}


@pytest.fixture(scope="module")
def zz():
    return compile_rules("ZZ"), cde.rule_bindings("ZZ")


def _res(rule_id, evaluated, failed, unit="U1", v=1, disp=None):
    return {"unit_key": unit, "eval_version": v, "rule_id": rule_id, "evaluated": evaluated, "failed": failed,
            "disposition": disp or ("PASS" if failed == 0 else "TA_WARN")}


def _by(rows):
    return {r["element"]: r for r in rows}


def test_bindings_come_from_the_catalog(zz):
    _, b = zz
    assert "CLM.POS" in b["B-CMP-CLM-POS"] and "CLM.POS" in b["B-VAL-CLM-POS"]
    assert {"CLM.DX1", "LINE.PROC_CODE"} <= set(b["R-ENC-008"])          # code-set rule binds both elements


def test_meets_and_seeded_nulls_push_below(zz):
    rules, b = zz
    good = [_res("B-CMP-CLM-POS", 164, 0), _res("B-VAL-CLM-POS", 164, 2)]
    r = _by(cde.cde_status(CDE, rules, good, b))["CLM.POS"]
    assert r["status"] == "MEETS" and r["completeness"] == 1.0 and r["validity"] == pytest.approx(162 / 164, abs=1e-6)
    seeded = [_res("B-CMP-CLM-POS", 164, 10), _res("B-VAL-CLM-POS", 164, 2)]     # 10 missing POS values
    r = _by(cde.cde_status(CDE, rules, seeded, b))["CLM.POS"]
    assert r["status"] == "BELOW" and r["completeness"] < 0.98 and "completeness" in r["reason"]


def test_latest_eval_version_wins(zz):
    rules, b = zz
    rows = [_res("B-CMP-CLM-POS", 164, 50, v=1), _res("B-CMP-CLM-POS", 164, 0, v=2), _res("B-VAL-CLM-POS", 164, 0)]
    assert _by(cde.cde_status(CDE, rules, rows, b))["CLM.POS"]["status"] == "MEETS"


def test_undelivered_domain_is_not_exercised_never_meets(zz):
    rules, b = zz
    r = _by(cde.cde_status(CDE, rules, [_res("B-CMP-RX-NDC", 0, 0, disp="NOT_APPLICABLE")], b))["RX.NDC"]
    assert r["status"] == "NOT_EXERCISED" and "not delivered" in r["reason"]


def test_absent_code_set_is_not_loaded_never_meets(zz):
    rules, b = zz
    rows = [_res("B-CMP-CLM-DX1", 164, 0), _res("B-VAL-CLM-DX1", 164, 0)]
    r = _by(cde.cde_status(CDE, rules, rows, b))["CLM.DX1"]
    assert r["status"] == "NOT_LOADED" and r["not_loaded_rules"] == ["R-ENC-008"]
    rows = [_res("B-CMP-CLM-DX1", 164, 40), _res("B-VAL-CLM-DX1", 164, 0)]       # BELOW outranks NOT_LOADED
    assert _by(cde.cde_status(CDE, rules, rows, b))["CLM.DX1"]["status"] == "BELOW"


def test_no_results_and_no_bound_rules_are_not_exercised(zz):
    rules, b = zz
    out = _by(cde.cde_status({**CDE, "ZZZ.NOTHING": {"mvdq": {}}}, rules, [], b))
    assert out["ELG.DOB"]["status"] == "NOT_EXERCISED" and out["ELG.DOB"]["reason"] == "no records evaluated"
    assert out["ZZZ.NOTHING"]["status"] == "NOT_EXERCISED"
    assert {r["status"] for r in out.values()} <= set(cde.STATUSES)
    assert list(out) == sorted(out)


def test_accepts_plain_dict_rules():
    rules = [{"id": "B-CMP-A-B", "entity": "E", "status": "ACTIVE", "check_class": "completeness"}]
    r = cde.cde_status({"A.B": {"mvdq": {"completeness_min": 0.9}}}, rules, [_res("B-CMP-A-B", 100, 5)],
                       {"B-CMP-A-B": ["A.B"]})
    assert r[0]["status"] == "MEETS" and r[0]["completeness"] == 0.95


def test_seed_defaults_file_computes_for_every_cde(zz):
    rules, b = zz
    defaults = cde.load_cde_defaults()
    if not defaults:
        pytest.skip("rules/cde_defaults.yaml not present yet")
    out = cde.cde_status(defaults, rules, [], b)
    assert [r["element"] for r in out] == sorted(defaults)
    assert all(r["status"] in cde.STATUSES and r["status"] != "MEETS" for r in out)   # no results: never MEETS
    assert _by(out)["RX.NDC"]["status"] == "NOT_EXERCISED"
