"""Part F P1-1 open items owned by Backend-B: N-T0-2 (feed-item wording) and F-25 (board small cells)."""
import pytest

from dqagent import llm
from dqagent.agent import seats
from dqagent.llm import check as chk
from dqagent.llm import facts as F

TEMPLATE = {"provider": "template"}


@pytest.mark.parametrize("kind,must,must_not", [
    ("UNROUTED_FILE", ["matched no route", "not loaded", "a route was proposed through a change request",
                       "the file was rejected", "a resubmission was requested"],
     ["No delivery", "arrived with no records", "no activity, a holiday"]),
    ("RECOVERY_DATA_LOST", ["Recovery of batch B-1 was refused", "no longer in the data plane", "stays held",
                            "a redelivery was requested", "a resubmission was requested"],
     ["No delivery", "arrived with no records", "no activity, a holiday"]),
])
def test_feed_item_wording_fits_unrouted_and_recovery_items(kind, must, must_not):
    raw = {"item_id": 9, "feed": "RECOVERY", "kind": kind, "batch_id": "B-1", "file": "ab" * 32, "status": "OPEN"}
    t = llm.explain("feed_item", raw, cfg=TEMPLATE)["text"]
    for m in must:
        assert m in t, (kind, m, t)
    for m in must_not:
        assert m not in t, (kind, m, t)
    assert "ab" * 32 not in t                              # the unrouted file is stored as a hash: never printed
    assert chk.check(t, [], F.build("feed_item", raw), require_claims=False) == (True, [])
    acked = llm.explain("feed_item", {**raw, "reason_code": "RESUBMIT_REQUESTED"}, cfg=TEMPLATE)["text"]
    assert "acknowledged with the reason: a resubmission was requested" in acked and "can acknowledge" not in acked


def test_template_ack_phrases_cover_every_kind_code():
    from dqagent.cp.governance_schema import FEED_ACK_CODES_BY_KIND
    from dqagent.llm.template import FEED_REASONS
    assert {c for v in FEED_ACK_CODES_BY_KIND.values() for c in v} <= set(FEED_REASONS)


def test_other_feed_items_keep_their_wording():
    t = llm.explain("feed_item", {"item_id": 7, "feed": "IE_EXTRACT", "kind": "MISSING_DELIVERY"}, cfg=TEMPLATE)
    assert t["text"].startswith("No delivery from feed IE_EXTRACT")


@pytest.mark.parametrize("n,shown", [(0, "0"), (1, "fewer than 11"), (10, "fewer than 11"), (11, "11"), (420, "420")])
def test_board_counts_are_small_cell_suppressed(n, shown):
    t = seats.render("RULE_NON_PASS", {"rule_id": "R-1", "disposition": "FAIL", "unit_count": n, "node": "bronze.x"})
    assert t == f"Rule R-1 is FAIL on {shown} unit(s) of bronze.x."
    r = seats.render("RELEASE_SUMMARY", {"batch_id": "B-1", "node": "bronze.x", "released": n, "held": 3,
                                         "pending": 0})
    assert f"{shown} released" in r and "fewer than 11 held" in r and "0 pending" in r


def test_board_suppression_is_display_only():
    body = {"rule_id": "R-1", "disposition": "FAIL", "unit_count": 1, "node": "bronze.x"}
    seats.render("RULE_NON_PASS", body)
    assert body["unit_count"] == 1                          # the stored body (and so bb_hash) is untouched
    assert "{" not in seats.render("MEASURE_BREACH", {"measure_id": "M-1", "value": 3, "threshold": 5})
