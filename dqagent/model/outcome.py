"""Disposition model (DESIGN §6). This total order is a day-one invariant.

A disposition is the outcome of one rule against one unit. The total order lets
the engine:
  * roll up worst-of over a set of rules,
  * decide whether a later evaluation WORSENED relative to a bound decision (§7 clause 4),
  * apply the autonomy floor (rank >= 4 is never autonomous).

Only PASS counts as passing for scoring and coverage.
"""
from __future__ import annotations

from enum import Enum
from typing import Iterable


class Disposition(str, Enum):
    PASS = "PASS"
    TA_WARN = "TA_WARN"
    KNOWN_ISSUE = "KNOWN_ISSUE"
    APPROXIMATE = "APPROXIMATE"
    PENDING_BENCHMARK = "PENDING_BENCHMARK"
    NOT_EVALUATED = "NOT_EVALUATED"
    SYSTEM_NOT_LOADED = "SYSTEM_NOT_LOADED"
    NOT_IMPLEMENTED = "NOT_IMPLEMENTED"
    UNBALANCEABLE = "UNBALANCEABLE"
    FAIL = "FAIL"
    ERROR = "ERROR"
    # Excluded from ranking. Neither ever satisfies a floor.
    NOT_APPLICABLE_FOR_FORMAT = "NOT_APPLICABLE_FOR_FORMAT"
    ADVISORY = "ADVISORY"
    NOT_APPLICABLE = "NOT_APPLICABLE"      # a required feed / element is not delivered (state feed manifest)
    STATE_WAIVED = "STATE_WAIVED"          # a two-person-approved state overlay waives the rule
    NOT_IN_SCOPE = "NOT_IN_SCOPE"          # the rule's applies_when selected no row of this unit (nothing to judge)


RANK: dict[Disposition, int] = {
    Disposition.PASS: 0,
    Disposition.TA_WARN: 1,
    Disposition.KNOWN_ISSUE: 2,
    Disposition.APPROXIMATE: 3,
    Disposition.PENDING_BENCHMARK: 4,
    Disposition.NOT_EVALUATED: 5,
    Disposition.SYSTEM_NOT_LOADED: 5,
    Disposition.NOT_IMPLEMENTED: 5,
    Disposition.UNBALANCEABLE: 6,
    Disposition.FAIL: 7,
    Disposition.ERROR: 8,
}

EXCLUDED: frozenset[Disposition] = frozenset(
    {Disposition.NOT_APPLICABLE_FOR_FORMAT, Disposition.ADVISORY, Disposition.NOT_APPLICABLE,
     Disposition.STATE_WAIVED, Disposition.NOT_IN_SCOPE}
)

# Rank at or above which a disposition can never be released autonomously (mode A).
NEVER_AUTONOMOUS_RANK = 4


def rank(d: Disposition) -> int | None:
    """Rank of a disposition, or None if it is excluded from ranking."""
    if d in EXCLUDED:
        return None
    return RANK[d]


def is_pass(d: Disposition) -> bool:
    return d is Disposition.PASS


def worst_of(dispositions: Iterable[Disposition]) -> Disposition:
    """Worst-of rollup. An empty (or all-excluded) set is NOT_EVALUATED, never PASS."""
    ranked = [d for d in dispositions if d not in EXCLUDED]
    if not ranked:
        return Disposition.NOT_EVALUATED
    return max(ranked, key=lambda d: RANK[d])


def worsened(bound: Disposition, current: Disposition) -> bool:
    """True if `current` is strictly worse than `bound`.

    Moving into or out of an excluded disposition counts as a change of kind. It is
    treated as worsening unless both sides are the same excluded value, so a decision
    bound under ADVISORY never silently covers a later FAIL.
    """
    if bound == current:
        return False
    rb, rc = rank(bound), rank(current)
    if rb is None or rc is None:
        return True
    return rc > rb


def autonomous_allowed(d: Disposition) -> bool:
    r = rank(d)
    return r is not None and r < NEVER_AUTONOMOUS_RANK
