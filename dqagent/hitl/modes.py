"""Deterministic mode() (DESIGN §8; Part E §1). The LLM never feeds this function.

mode = max(POLICY[authority][worst disposition], FLOOR entries that apply, TIER CEILING, ENTITLEMENT CEILING)

Part E §1.1: an autonomy tier is a CEILING on what the agent may do on its own, per (agent, RELEASE, authority):
L4 -> A, L3 -> H_ON (act after a veto window), L2 / L1 -> H_IN. A missing tier is L1 (never L4); no live RELEASE
entitlement lowers the ceiling to H_IN (I7: expiry never blocks a human-approved release). Floors still raise any
tier to H_IN_2. Every step that moved the mode is written into the trace, so a card can show WHY ("L2 ceiling").

effective_mode() recomputes the mode of a unit FROM STORED ROWS ONLY (latest results, the one gating-ABC definition,
the persisted dependency escalation, vetoes, the database's tier and entitlement). The publisher (clause 4), the
decision service (a vetoed / ceiling-held unit) and the runtime's card helper all call it, so they cannot disagree.
"""
from __future__ import annotations

from dqagent.model.outcome import Disposition as D, autonomous_allowed, worst_of

ORDER = ["A", "H_ON", "H_IN", "H_IN_2"]
TIER_CEILING = {"L4": "A", "L3": "H_ON", "L2": "H_IN", "L1": "H_IN"}   # = cp.governance_schema.TIER_CEILING
STRUCTURAL_PROGRAMS = ("SNAP", "TANF")


def _max(a: str, b: str) -> str:
    return a if ORDER.index(a) >= ORDER.index(b) else b


def mode(policy: dict, authorities: list[str], dispositions: list[D], *, abc_failed: bool,
         structural: bool = False, advisory_waived: bool = False, autonomy: dict | None = None,
         escalated: str | None = None, vetoed: bool = False) -> tuple[str, list[str]]:
    """Returns (mode, trace). The trace records why the mode was chosen and is shown in the Tower.

    autonomy   {authority: {"tier": "L1".."L4", "entitled": bool}} for the agent that releases (action class RELEASE).
               None = no ceiling (pure policy computation, used by policy tooling); the engine always passes it, and an
               authority missing from it is L1 and not entitled (fail closed).
    escalated  the persisted dependency-wait escalation (C2): at least H_IN, never replaces H_ON with itself.
    vetoed     a person vetoed this unit: at least H_IN (a person must decide it).
    """
    trace = []
    worst = worst_of(dispositions)
    m = "A"
    for auth in authorities:
        pa = policy["policy"].get(auth)
        if pa is None:
            trace.append(f"no policy for {auth} -> H_IN_2 (fail closed)")
            m = _max(m, "H_IN_2")
            continue
        pm = pa.get(worst.value, pa.get("default_non_pass", "H_IN") if worst is not D.PASS else "A")
        trace.append(f"policy[{auth}][{worst.value}] = {pm}")
        m = _max(m, pm)
    fl = policy["floor"]
    if not autonomous_allowed(worst) and worst is not D.PASS:
        m = _max(m, fl["rank_ge_4"])
        trace.append(f"floor: {worst.value} never autonomous -> {fl['rank_ge_4']}")
    if abc_failed:
        m = _max(m, fl["abc_failed_release"])
        trace.append(f"floor: ABC failed in batch -> {fl['abc_failed_release']}")
    if structural:
        m = _max(m, fl["structural_program"])
        trace.append("floor: STRUCTURAL program -> " + fl["structural_program"])
    if advisory_waived:
        m = _max(m, fl["advisory_floor_waived"])
        trace.append("floor: floor waived by ADVISORY -> " + fl["advisory_floor_waived"])
    if autonomy is not None:
        for auth in authorities:
            a = autonomy.get(auth) or {}
            tier = a.get("tier") if a.get("tier") in TIER_CEILING else "L1"
            ceil = TIER_CEILING[tier]
            trace.append(f"tier: {tier} ceiling for {auth} -> {ceil}")
            m = _max(m, ceil)
            if not a.get("entitled"):
                trace.append(f"entitlement: no live RELEASE entitlement for {auth} -> H_IN ceiling")
                m = _max(m, "H_IN")
    if escalated:
        m = _max(m, "H_IN")                          # C2: _max, never `H_IN if m == 'A' else m`
        trace.append(f"{escalated} -> at least H_IN")
    if vetoed:
        m = _max(m, "H_IN")
        trace.append("vetoed by a person -> at least H_IN")
    return m, trace


def ceiling_label(trace: list[str]) -> str | None:
    """The short WHY for a card, e.g. 'L2 ceiling' or 'no RELEASE entitlement', when a ceiling bound the mode."""
    for t in trace:
        if t.startswith("entitlement:"):
            return "no RELEASE entitlement"
    tiers = [t.split()[1] for t in trace if t.startswith("tier: ")]
    low = min(tiers, key=lambda x: int(x[1])) if tiers else None
    return f"{low} ceiling" if low and low != "L4" else None


def validate_policy(policy: dict) -> None:
    """Invariant (Part C post-build F6): an advisory basic-DQ warning (TA_WARN) never changes a mode. For every
    authority, TA_WARN must map to the same mode as PASS; a policy that says otherwise is refused at load."""
    for auth, pa in policy["policy"].items():
        pass_m = pa.get("PASS", "A")
        ta_m = pa.get("TA_WARN", pa.get("default_non_pass", "H_IN"))
        if ta_m != pass_m:
            raise ValueError(f"policy[{auth}]: TA_WARN maps to {ta_m} but PASS maps to {pass_m}; "
                             "advisory warnings must never change a mode")


# -- stored-row helpers (one definition for evaluator, publisher, decision service and runtime) ------------------
def release_agent(c) -> str | None:
    """The agent whose seat publishes (dq_ctl.seat_agent('dq_publish_svc'))."""
    return c.execute("SELECT dq_ctl.seat_agent('dq_publish_svc') AS a").fetchone()["a"]


def autonomy_for(c, state: str | None, authorities: list[str], action_class: str = "RELEASE",
                 relation: str | None = None) -> dict:
    """{authority: {tier, entitled}} as the DATABASE says now (a missing tier is L1).

    Part F P1-3 (K2b, PFP :793-804): `relation`, when given, additionally applies ORCH-1's per-dataset ceiling
    (`dq_ctl.autonomy_tier_for`, K2a) -- the lower of the 4-argument `autonomy_tier` and the relation's
    `task_gate_ceiling`. The 4-argument `autonomy_tier` call (relation=None, the default) is UNCHANGED: other
    callers (`track_record`, `tier_of`, the grants) rely on its own 4-argument shape (PFP :799)."""
    agent = release_agent(c)
    out = {}
    for auth in sorted(set(authorities)):
        if relation is None:
            r = c.execute("SELECT dq_ctl.autonomy_tier(%s,%s,%s,%s) AS t, dq_ctl.entitled(%s,%s,%s) AS e",
                          (state, agent, action_class, auth, agent, action_class, auth)).fetchone()
        else:
            r = c.execute("SELECT dq_ctl.autonomy_tier_for(%s,%s,%s,%s,%s) AS t, dq_ctl.entitled(%s,%s,%s) AS e",
                          (state, agent, action_class, auth, relation, agent, action_class, auth)).fetchone()
        out[auth] = {"tier": r["t"] or "L1", "entitled": bool(r["e"])}
    return out


def gating_abc_failed(c, batch_id: str) -> bool:
    """Part E §3.1 (I1): the ONE definition of a gating ABC failure."""
    return bool(c.execute("SELECT 1 FROM dq_ctl.abc_gating_failures WHERE batch_id=%s LIMIT 1", (batch_id,)).fetchone())


def abc_missing(c, batch_id: str) -> bool:
    """Part F F-01: the ONE definition of "ABC incomplete" (dq_ctl.batches_abc_missing): the batch is sealed but its
    LANDING->GATED ABC was never recorded (a crash between the seal and the ABC). Read as a service role."""
    return bool(c.execute("SELECT 1 FROM dq_ctl.batches_abc_missing WHERE batch_id=%s LIMIT 1", (batch_id,)).fetchone())


def latest_eval_version(c, unit_key: str) -> int | None:
    return c.execute("SELECT max(eval_version) v FROM dq_ctl.results WHERE unit_key=%s", (unit_key,)).fetchone()["v"]


def escalation_of(c, unit_key: str, eval_version: int | None) -> str | None:
    """The dependency escalation persisted on the evaluation event of `eval_version` (C2), if any."""
    if eval_version is None:
        return None
    r = c.execute("SELECT payload->>'escalated' AS e FROM dq_ctl.unit_events WHERE unit_key=%s AND via='ENGINE' "
                  "AND payload ? 'escalated' AND payload->>'eval_version' = %s ORDER BY state_version DESC LIMIT 1",
                  (unit_key, str(eval_version))).fetchone()
    return r["e"] if r else None


def was_vetoed(c, unit_key: str) -> bool:
    return bool(c.execute("SELECT 1 FROM dq_ctl.unit_events WHERE unit_key=%s AND via='VETO' LIMIT 1",
                          (unit_key,)).fetchone())


def effective_mode(c, policy: dict, unit: dict, state: str | None, results: list[dict] | None = None,
                   eval_version: int | None = None) -> tuple[str, list[str], dict]:
    """Recompute the mode of `unit` from stored rows. Returns (mode, trace, autonomy). `results`: the latest
    evaluation's rows {rule_id, disposition} (read when not given)."""
    uk = unit["unit_key"]
    if eval_version is None:
        eval_version = latest_eval_version(c, uk)
    if results is None:
        results = c.execute("SELECT DISTINCT ON (rule_id) rule_id, disposition FROM dq_ctl.results WHERE unit_key=%s "
                            "AND eval_version=%s ORDER BY rule_id, id DESC", (uk, eval_version)).fetchall()
    disps = [D(r["disposition"]) for r in results]
    auto = autonomy_for(c, state, unit["authorities"], relation=unit["node"])
    m, trace = mode(policy, unit["authorities"], disps, abc_failed=gating_abc_failed(c, unit["batch_id"]),
                    structural=unit["program_code"] in STRUCTURAL_PROGRAMS,
                    advisory_waived=any(d is D.ADVISORY for d in disps), autonomy=auto,
                    escalated=escalation_of(c, uk, eval_version), vetoed=was_vetoed(c, uk))
    return m, trace, auto
