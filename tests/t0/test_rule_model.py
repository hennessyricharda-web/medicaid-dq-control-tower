"""T0: Part B rule model — canonical elements, state binding, feed applicability, overlays, effective hash."""
import copy

import pytest

from dqagent.rules import model
from dqagent.rules.ir import IRError


def _by(rules, rid, ent=None):
    return next(r for r in rules if r.id == rid and (ent is None or r.entity == ent))


def test_zz_compiles_every_rule_and_both_dialects_render():
    rs = model.compile_rules("ZZ")
    core = [r for r in rs if not r.pack.startswith("basic/")]
    assert all(r.status in ("ACTIVE", "NOT_LOADED") for r in core)
    # basic DQ: delivered domains ACTIVE; declared (undelivered) domains NOT_APPLICABLE because their feed is absent
    for r in rs:
        if r.pack.startswith("basic/"):
            assert r.status == "ACTIVE" or (r.status == "NOT_APPLICABLE" and "not delivered" in r.status_reason), r
    for r in rs:
        if r.kind == "row" and r.status == "ACTIVE":
            for d in ("duckdb", "snowflake"):
                assert r.sql(d)


def test_same_catalog_binds_to_a_differently_named_state_with_zero_rule_edits():
    zz, xx = model.compile_rules("ZZ"), model.compile_rules("XX")
    a, b = _by(zz, "R-ENC-001"), _by(xx, "R-ENC-001")
    assert a.semantic_hash == b.semantic_hash                      # the RULE is identical
    assert a.relation == "bronze.encounter_header" and b.relation == "stg.enc_hdr"
    assert "x_total_charge" in b.sql("duckdb") and "x_total_charge" not in a.sql("duckdb")
    assert a.effective_hash != b.effective_hash                    # but the bound behaviour is re-approved per state


def test_feed_or_element_not_delivered_makes_rule_not_applicable_never_pass():
    xx = model.compile_rules("XX")
    assert _by(xx, "R-834-002").status == "NOT_APPLICABLE"          # XX receives no 834
    assert "X12_834" in _by(xx, "R-834-002").status_reason
    assert _by(xx, "R-ENC-010").status == "NOT_APPLICABLE"          # PRV.BILLING_ALT_ID not delivered ...
    assert _by(xx, "R-ENC-003").status == "ACTIVE"                  # ... but the NPI check-digit rule still runs
    assert _by(xx, "R-ENC-001").status == "ACTIVE"
    assert _by(xx, "ENV-ISA-WIDTH", "ENROLLMENT_834").status == "NOT_APPLICABLE"   # the compiled entity's feed decides
    assert _by(xx, "ENV-ISA-WIDTH", "ENCOUNTER_HEADER").status == "ACTIVE"


def test_nc_paid_amount_rule_waits_for_the_unpublished_guide():
    nc = model.compile_rules("STATE_A")
    r = _by(nc, "SA-ENC-PLAN-PAID-PRESENT")
    assert r.status == "NOT_APPLICABLE" and "CLM.MCO_PAID" in r.status_reason
    assert _by(nc, "SA-ENC-MEMBER-ID-SHAPE").status == "ACTIVE"


def test_overlays_waive_and_tighten_but_never_loosen():
    xx = model.compile_rules("XX")
    assert _by(xx, "R-ENC-002").status == "STATE_WAIVED"
    assert _by(xx, "M-RULE-7907").thresholds["max"] == 0.005
    with pytest.raises(IRError, match="LOOSEN"):
        model._apply_overlay({"id": "M", "thresholds": {"max": 0.01}},
                             {"verb": "tighten", "thresholds": {"max": 0.5}, "reason": "x", "approved_ref": "y"})
    with pytest.raises(IRError, match="lacks reason"):
        model._apply_overlay({"id": "M"}, {"verb": "waive", "approved_ref": "y"})


def test_effective_hash_moves_with_params_bindings_and_overlays(monkeypatch):
    base = model.compile_rules("STATE_A")
    h0 = _by(base, "SA-ENC-MEMBER-ID-SHAPE").effective_hash
    real = model.load_state

    def patched(state):
        st = copy.deepcopy(real(state))
        st["profile"]["params"]["member_id_pattern"] = "[A-Z0-9]{10}"
        return st
    monkeypatch.setattr(model, "load_state", patched)
    assert _by(model.compile_rules("STATE_A"), "SA-ENC-MEMBER-ID-SHAPE").effective_hash != h0


def test_any_logic_edit_changes_the_hash_presentation_edits_do_not():
    from dqagent.rules.ir import semantic_hash
    r = {"id": "M", "kind": "measure", "numerator": {"lit": True}, "title": "a"}
    assert semantic_hash(r) != semantic_hash({**r, "numerator": {"lit": False}})
    assert semantic_hash(r) != semantic_hash({**r, "kind": "row"})
    assert semantic_hash(r) == semantic_hash({**r, "title": "b", "citation": {"x": 1}})


def test_coverage_counts_inventory_requirements_not_rules():
    from pathlib import Path
    root = Path(model.ROOT) / "rules" / "inventory"
    cov = model.coverage(model.compile_rules("ZZ"), [root / "federal.csv"])
    by = {r["req_id"]: r["coverage"] for r in cov["requirements"]}
    assert by["X12-837P-002"] == "ACTIVE"
    assert by["RULE-7907"] == "ANALOG_ACTIVE" and by["RULE-884"] == "PARTIAL"   # pre-load analogs never close a CMS measure
    assert by["RULE-2105"] == "GAP"
    assert by["RULE-2241"] == "GAP"                                   # a CMS measure with no rule yet stays a visible gap
    assert sum(1 for v in by.values() if v == "GAP") > 90


def test_every_overlay_verb_expires_and_unknown_rule_ids_are_refused():
    import copy as _c
    import pytest as _p
    from dqagent.rules.ir import IRError
    base = model.load_state("ZZ")
    cfg = _c.deepcopy(base)
    cfg["overlays"] = {"R-ENC-001": {"verb": "raise_priority", "priority": "Critical", "reason": "r",
                                     "approved_ref": "CR-1", "expires": "2020-01-01"}}
    r = _by(model.compile_rules("ZZ", cfg=cfg), "R-ENC-001")
    assert r.header["overlay"]["status"] == "EXPIRED" and r.header["priority"] != "Critical"
    cfg["overlays"]["R-ENC-001"]["expires"] = "2999-01-01"
    r = _by(model.compile_rules("ZZ", cfg=cfg), "R-ENC-001")
    assert r.header["overlay"]["status"] == "ACTIVE" and r.header["priority"] == "Critical"
    cfg["overlays"] = {"NO-SUCH-RULE": {"verb": "waive", "reason": "r", "approved_ref": "CR-2"}}
    with _p.raises(IRError, match="not in the enabled catalog"):
        model.compile_rules("ZZ", cfg=cfg)


def test_a_rule_cannot_compile_twice_onto_one_relation():
    import copy as _c
    import pytest as _p
    from dqagent.rules.ir import IRError
    cfg = _c.deepcopy(model.load_state("ZZ"))
    cfg["elements"]["entities"]["ENROLLMENT_834"] = cfg["elements"]["entities"]["ENCOUNTER_HEADER"]
    with _p.raises(IRError, match="compiles twice"):
        model.compile_rules("ZZ", cfg=cfg)
