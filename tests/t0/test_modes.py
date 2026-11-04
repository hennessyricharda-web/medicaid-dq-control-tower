import yaml

from dqagent.engine.evaluate import npi_valid
from dqagent.hitl.modes import mode
from dqagent.model.outcome import Disposition as D

POLICY = yaml.safe_load(open("config/state/ZZ/policy_w1.yaml"))


def test_all_pass_medicaid_is_autonomous():
    assert mode(POLICY, ["AUTH_MEDICAID"], [D.PASS, D.PASS], abc_failed=False)[0] == "A"


def test_fail_is_held_and_abc_failure_needs_two_people():
    assert mode(POLICY, ["AUTH_MEDICAID"], [D.PASS, D.FAIL], abc_failed=False)[0] == "H_IN"
    assert mode(POLICY, ["AUTH_MEDICAID"], [D.PASS], abc_failed=True)[0] == "H_IN_2"


def test_structural_and_advisory_never_autonomous():
    assert mode(POLICY, ["AUTH_SNAP"], [D.PASS], abc_failed=False, structural=True)[0] == "H_IN"
    assert mode(POLICY, ["AUTH_MEDICAID"], [D.PASS, D.ADVISORY], abc_failed=False, advisory_waived=True)[0] == "H_IN"


def test_unknown_authority_fails_closed():
    assert mode(POLICY, ["AUTH_FTI"], [D.PASS], abc_failed=False)[0] == "H_IN_2"


def test_npi_luhn():
    assert npi_valid("1234567893")          # CMS published example
    assert not npi_valid("1234567890")
    assert not npi_valid("12345")
    assert not npi_valid(None)
