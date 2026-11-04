"""Unit lifecycle (DESIGN §3.6). The transition table below is the ONLY definition.

Rules
  * A state is terminal only if this table marks it terminal.
  * `allowed(from, to, via)` is the single check used by the CP transition function
    and mirrored by the Postgres CHECK/trigger (generated from this table, never
    hand-written).
"""
from __future__ import annotations

from enum import Enum


class State(str, Enum):
    LANDED = "LANDED"
    SEALED = "SEALED"
    EVALUATING = "EVALUATING"
    PENDING_DEPENDENCY = "PENDING_DEPENDENCY"
    RE_EVALUATE_PENDING = "RE_EVALUATE_PENDING"
    HELD = "HELD"
    PENDING_RELEASE = "PENDING_RELEASE"
    RELEASED = "RELEASED"
    REJECTED = "REJECTED"
    SUPERSEDED = "SUPERSEDED"
    ERROR = "ERROR"


class Via(str, Enum):
    SEAL = "SEAL"
    ENGINE = "ENGINE"
    PUBLISH = "PUBLISH"
    DECIDE = "DECIDE"
    CONFIRM = "CONFIRM"
    RE_PIN = "RE_PIN"
    VETO = "VETO"
    REJECT = "REJECT"
    SUPERSEDE = "SUPERSEDE"
    RULESET = "RULESET"      # an APPROVED ruleset change re-opens non-terminal units (Part D, red-team C5)
    CEILING = "CEILING"      # Part E (C3): the publisher's recomputed mode exceeds the H_ON ceiling -> HELD, never silent


TERMINAL: frozenset[State] = frozenset({State.RELEASED, State.REJECTED, State.SUPERSEDED})

S, V = State, Via
TRANSITIONS: frozenset[tuple[State, State, Via]] = frozenset(
    {
        (S.LANDED, S.SEALED, V.SEAL),
        (S.SEALED, S.EVALUATING, V.ENGINE),
        *((S.EVALUATING, to, V.ENGINE) for to in (
            S.PENDING_DEPENDENCY, S.RE_EVALUATE_PENDING, S.HELD, S.ERROR)),
        # No EVALUATING -> PENDING_RELEASE via ENGINE: PENDING_RELEASE means "humans approved".
        (S.EVALUATING, S.RELEASED, V.PUBLISH),          # mode A only (enforced by can_release)
        (S.PENDING_DEPENDENCY, S.EVALUATING, V.ENGINE),
        (S.RE_EVALUATE_PENDING, S.EVALUATING, V.ENGINE),
        (S.HELD, S.PENDING_RELEASE, V.DECIDE),
        (S.HELD, S.PENDING_RELEASE, V.CONFIRM),
        # No HELD -> RELEASED edge: a human decision ends at PENDING_RELEASE, and ONLY the
        # publisher (via PUBLISH, after can_release) reaches RELEASED. (SEC-W1-1)
        (S.HELD, S.REJECTED, V.REJECT),
        (S.HELD, S.EVALUATING, V.RE_PIN),
        (S.PENDING_RELEASE, S.RELEASED, V.PUBLISH),
        (S.PENDING_RELEASE, S.HELD, V.VETO),
        # Part E (C2/C3): an H_ON unit waits in EVALUATING behind a veto window. A person may veto it while the
        # window is open (dq_ctl.veto_unit), and the publisher holds it when its recomputed mode now exceeds H_ON,
        # so a unit never sits silently in EVALUATING.
        (S.EVALUATING, S.HELD, V.VETO),
        (S.EVALUATING, S.HELD, V.CEILING),
        (S.ERROR, S.REJECTED, V.REJECT),
        (S.ERROR, S.SUPERSEDED, V.SUPERSEDE),
        (S.ERROR, S.EVALUATING, V.ENGINE),
        # Part D (C5): after an approved configuration / ruleset change is applied, every non-terminal unit of an
        # affected relation is re-opened for evaluation under the new ruleset. Its open decisions belong to the
        # previous round and can no longer be confirmed (state_version moves).
        *((frm, S.RE_EVALUATE_PENDING, V.RULESET) for frm in (
            S.EVALUATING, S.HELD, S.PENDING_DEPENDENCY, S.PENDING_RELEASE)),
    }
)


# Which service may drive each verb (SEC-W1-1). The CP generates one SECURITY DEFINER wrapper
# per role from this map, and grants each wrapper only to its role, so the DATABASE refuses
# e.g. the api calling PUBLISH or the evaluator calling DECIDE.
VIA_ROLE: dict[Via, str] = {
    V.SEAL: "dq_seal_svc",
    V.ENGINE: "dq_eval_svc", V.SUPERSEDE: "dq_eval_svc", V.RULESET: "dq_eval_svc",
    V.PUBLISH: "dq_publish_svc", V.CEILING: "dq_publish_svc",
    V.DECIDE: "dq_api", V.CONFIRM: "dq_api", V.REJECT: "dq_api", V.VETO: "dq_api", V.RE_PIN: "dq_api",
}
HUMAN_VIAS = frozenset({V.DECIDE, V.CONFIRM, V.REJECT, V.VETO, V.RE_PIN})


def wrapper_for(via: str) -> str:
    """Name of the role-bound transition function for `via`."""
    return "transition_" + VIA_ROLE[Via(via)].removeprefix("dq_").removesuffix("_svc")


def is_terminal(s: State) -> bool:
    return s in TERMINAL


def allowed(frm: State, to: State, via: Via) -> bool:
    return (frm, to, via) in TRANSITIONS


def sql_allowed_transitions_values() -> str:
    """VALUES list used to generate the Postgres transition guard (single source)."""
    rows = sorted((a.value, b.value, c.value) for a, b, c in TRANSITIONS)
    return ",\n".join(f"('{a}','{b}','{c}')" for a, b, c in rows)
