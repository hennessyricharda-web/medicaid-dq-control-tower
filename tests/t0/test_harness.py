import copy

import yaml

from dqagent.harness.plan import order, validate

H = yaml.safe_load(open("config/harness_w1.yaml"))


def test_w1_harness_valid_and_ordered():
    assert validate(H) == []
    assert order(H) == ["p4_eligibility_ie", "p6_claims"]


def test_wait_cycle_rejected():
    h = copy.deepcopy(H)
    h["dependencies"].append({"domain": "ELIGIBILITY", "depends_on": "CLAIMS", "policy": "WAIT"})
    assert any("cycle" in e for e in validate(h))


def test_xref_key_map_forced_to_ealr():
    h = copy.deepcopy(H)
    h["dependencies"][0]["key_map"] = ["member_id=xref.msis_id"]
    assert any("xref-resolved" in e for e in validate(h))


def test_trigger_rule_fail_open_rejected():
    errs = validate(H, trigger_rules={"p6_claims": {"build_gold": "none_failed"}})
    assert any("trigger_rule none_failed" in e for e in errs)


def test_gated_node_must_exist_in_dag():
    errs = validate(H, dag_nodes={"p4_eligibility_ie": {"bronze.ie_segment"}, "p6_claims": set()})
    assert any("not in DAG source" in e for e in errs)
