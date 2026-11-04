from dqagent.model.lifecycle import TERMINAL, TRANSITIONS, State, Via, allowed, is_terminal
from dqagent.model.outcome import (
    EXCLUDED, RANK, Disposition as D, autonomous_allowed, rank, worsened, worst_of,
)


def test_every_disposition_is_ranked_or_excluded():
    for d in D:
        assert (d in RANK) ^ (d in EXCLUDED), d


def test_order_is_the_published_total_order():
    order = [D.PASS, D.TA_WARN, D.KNOWN_ISSUE, D.APPROXIMATE, D.PENDING_BENCHMARK,
             D.NOT_EVALUATED, D.UNBALANCEABLE, D.FAIL, D.ERROR]
    assert [rank(d) for d in order] == sorted(rank(d) for d in order)
    assert rank(D.SYSTEM_NOT_LOADED) == rank(D.NOT_IMPLEMENTED) == rank(D.NOT_EVALUATED)


def test_empty_worst_of_is_not_evaluated_never_pass():
    assert worst_of([]) is D.NOT_EVALUATED
    assert worst_of([D.ADVISORY, D.NOT_APPLICABLE_FOR_FORMAT]) is D.NOT_EVALUATED
    assert worst_of([D.PASS, D.FAIL, D.TA_WARN]) is D.FAIL


def test_worsened_covers_excluded_transitions():
    assert worsened(D.TA_WARN, D.FAIL)
    assert not worsened(D.FAIL, D.TA_WARN)        # improvement does not stale
    assert worsened(D.ADVISORY, D.FAIL)           # decision under ADVISORY never covers FAIL
    assert worsened(D.PASS, D.ADVISORY)
    assert not worsened(D.ADVISORY, D.ADVISORY)


def test_autonomy_floor():
    assert autonomous_allowed(D.APPROXIMATE)
    for d in (D.PENDING_BENCHMARK, D.NOT_EVALUATED, D.SYSTEM_NOT_LOADED, D.FAIL, D.ERROR,
              D.ADVISORY, D.NOT_APPLICABLE_FOR_FORMAT):
        assert not autonomous_allowed(d), d


def test_terminal_states_have_no_exits():
    for frm, _, _ in TRANSITIONS:
        assert not is_terminal(frm), frm
    assert TERMINAL == {State.RELEASED, State.REJECTED, State.SUPERSEDED}


def test_error_has_an_exit_and_rejected_is_reachable():
    assert allowed(State.ERROR, State.REJECTED, Via.REJECT)
    assert allowed(State.HELD, State.REJECTED, Via.REJECT)
    assert not allowed(State.RELEASED, State.HELD, Via.DECIDE)   # post-release => DENY, not state


def test_every_non_terminal_state_is_exitable():
    exits = {frm for frm, _, _ in TRANSITIONS}
    for s in State:
        assert is_terminal(s) or s in exits, s
