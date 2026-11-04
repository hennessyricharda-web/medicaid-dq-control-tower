"""Part E seeds and pure rules (no database): agent registry seed, lower-only mirror, classification merge, lifecycle
edges, policy and feeds manifest additions."""
import datetime as dt

import pytest
import yaml

from dqagent.cp.agent_schema import CONTROL_SEATS, DQ_AGENT, SEATS
from dqagent.cp.governance_schema import CLASS_RANK as SQL_CLASS_RANK, TIER_CEILING
from dqagent.governance.registry import (CLASS_RANK, _expected_kind, agent_seed, classification_seed,
                                         iso_duration_seconds, lower_only, seed_from_files)
from dqagent.model.lifecycle import VIA_ROLE, State, Via, allowed, wrapper_for

AGENTS = yaml.safe_load(open("config/agents.yaml"))


def test_every_agent_seat_login_is_bound_to_a_registered_seat():
    for role, (agent, seat) in SEATS.items():
        assert agent in AGENTS["agents"], role
        if seat not in CONTROL_SEATS:
            assert seat in AGENTS["agents"][agent]["seats"], (role, seat)
    assert "dq_monitor_svc" in SEATS and SEATS["dq_monitor_svc"] == (DQ_AGENT, "MONITOR")


def test_the_seed_keeps_w1_invariant_and_registers_the_next_agent():
    now = dt.datetime(2026, 10, 23, 15, 0, tzinfo=dt.timezone.utc)
    items = {i["artifact_id"]: i for i in agent_seed("ZZ", now)}
    assert items[f"ZZ:autonomy:{DQ_AGENT}:RELEASE"]["content"] == {"AUTH_MEDICAID": "L4", "AUTH_SNAP": "L2"}
    ent = items[f"ZZ:entitlement:{DQ_AGENT}:RELEASE"]["content"]
    assert dt.datetime.fromisoformat(ent["not_after"]) - dt.datetime.fromisoformat(ent["not_before"]) == dt.timedelta(days=365)
    assert dt.datetime.fromisoformat(ent["not_before"]) <= now
    assert items[f"ZZ:agent:{DQ_AGENT}"]["content"]["status"] == "ACTIVE"
    gov = items["ZZ:agent:data-governance-classification"]["content"]
    assert gov["status"] == "REGISTERED"
    assert not [a for a in items if a.startswith(("ZZ:entitlement:data-governance", "ZZ:autonomy:data-governance"))]
    for aid, it in items.items():
        assert _expected_kind("ZZ", aid) == it["kind"], aid
    kinds = {i["kind"] for i in seed_from_files("ZZ")}
    assert {"AGENT", "ENTITLEMENT", "AUTONOMY", "CDE", "CLASSIFICATION", "MONITOR"} <= kinds


def test_lower_only_mirror():
    old = {"AUTH_MEDICAID": "L4", "AUTH_SNAP": "L2"}
    assert not lower_only("AUTONOMY", None, {"AUTH_MEDICAID": "L1"}, "PUT")        # brand new = a grant
    assert lower_only("AUTONOMY", old, {"AUTH_MEDICAID": "L3", "AUTH_SNAP": "L2"}, "PUT")
    assert lower_only("AUTONOMY", old, {"AUTH_MEDICAID": "L3"}, "PUT")              # a dropped entry falls to L1
    assert not lower_only("AUTONOMY", old, {"AUTH_MEDICAID": "L4", "AUTH_SNAP": "L3"}, "PUT")
    assert not lower_only("AUTONOMY", old, {"AUTH_TANF": "L2"}, "PUT")              # missing meant L1
    assert lower_only("AUTONOMY", old, None, "DELETE")
    e = {"authorities": ["AUTH_A", "AUTH_B"], "not_before": "2026-01-01T00:00:00+00:00",
         "not_after": "2027-01-01T00:00:00+00:00"}
    assert lower_only("ENTITLEMENT", e, {**e, "authorities": ["AUTH_A"]}, "PUT")
    assert lower_only("ENTITLEMENT", e, {**e, "not_after": "2026-06-01T00:00:00+00:00"}, "PUT")
    assert not lower_only("ENTITLEMENT", e, {**e, "not_after": "2027-06-01T00:00:00+00:00"}, "PUT")
    assert not lower_only("ENTITLEMENT", e, {**e, "authorities": ["AUTH_A", "AUTH_C"]}, "PUT")
    assert not lower_only("ENTITLEMENT", None, e, "PUT")
    a = {"name": "n", "version": "1", "status": "ACTIVE", "seats": ["X", "Y"]}
    assert lower_only("AGENT", a, {**a, "status": "SUSPENDED"}, "PUT")
    assert lower_only("AGENT", a, {**a, "seats": ["X"]}, "PUT")
    assert not lower_only("AGENT", {**a, "status": "SUSPENDED"}, a, "PUT")        # re-activation is a grant
    assert not lower_only("AGENT", a, {**a, "version": "2"}, "PUT")
    c = {"E1": "payment", "E2": "identity"}
    assert lower_only("CLASSIFICATION", c, {"E1": "clinical", "E2": "identity", "E3": "none"}, "PUT")
    assert not lower_only("CLASSIFICATION", c, {"E1": "none", "E2": "identity"}, "PUT")
    assert not lower_only("CLASSIFICATION", c, {"E2": "identity"}, "PUT")


def test_python_and_sql_share_the_sensitivity_order_and_tier_ceilings():
    assert CLASS_RANK == SQL_CLASS_RANK
    assert TIER_CEILING == {"L4": "A", "L3": "H_ON", "L2": "H_IN", "L1": "H_IN"}


def test_classification_seed_merges_control_tags_and_never_downgrades():
    d = "config/state/ZZ/"
    cls = classification_seed("ZZ", yaml.safe_load(open(d + "elements.yaml")), yaml.safe_load(open(d + "contracts_w1.yaml")))
    assert cls["CLM.DX1"] == "clinical" and cls["ELG.MEMBER_ID"] == "identity" and cls["LINE.CHARGE"] == "payment"
    assert all(v in CLASS_RANK for v in cls.values())
    defaults = yaml.safe_load(open("rules/classification_defaults.yaml"))["classes"]
    assert all(CLASS_RANK[cls[e]] >= CLASS_RANK[c] for e, c in defaults.items())
    canon = yaml.safe_load(open("rules/elements.yaml"))["elements"]
    assert set(cls) <= set(canon)
    cdes = yaml.safe_load(open("rules/cde_defaults.yaml"))["cdes"]
    assert set(cdes) <= set(canon)


def test_lifecycle_edges_for_the_veto_window():
    assert allowed(State.EVALUATING, State.HELD, Via.VETO) and VIA_ROLE[Via.VETO] == "dq_api"
    assert allowed(State.EVALUATING, State.HELD, Via.CEILING) and VIA_ROLE[Via.CEILING] == "dq_publish_svc"
    assert wrapper_for("CEILING") == wrapper_for("PUBLISH")


def test_policy_and_feeds_manifest_carry_part_e_keys():
    pol = yaml.safe_load(open("config/state/ZZ/policy_w1.yaml"))
    assert pol["approvals"]["H_ON"]["persons"] == 0
    assert iso_duration_seconds(pol["veto_window"]) == 600
    assert {iso_duration_seconds(v) for v in pol["sla"].values()} == {3600, 86400, 172800}
    for st in ("ZZ", "STATE_A", "XX"):
        f = yaml.safe_load(open(f"config/state/{st}/feeds.yaml"))
        assert f["part2_programs"] == []
        for feed in ("IE_EXTRACT", "X12_837P"):
            assert f["feeds"][feed]["cadence"]["expected"] is True, (st, feed)
        if f["feeds"]["X12_834"].get("delivered"):
            assert f["feeds"]["X12_834"]["empty_ok"] is True, st


def test_iso_durations():
    assert iso_duration_seconds("PT10M") == 600 and iso_duration_seconds("P1W") == 604800
    assert iso_duration_seconds("P1DT2H") == 93600
    for bad in ("10M", "P", "PT", "", None, "P1Y"):
        with pytest.raises(ValueError):
            iso_duration_seconds(bad)
