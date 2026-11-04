"""Critical data elements and minimum viable DQ (Part E §4). ADVISORY ONLY: never changes a disposition or mode.

The effective CDE artifact `<ST>:cde` has content {element: {mvdq: {completeness_min, validity_min}, steward_domain}}.
For each CDE, the latest pass rates are computed from the rules BOUND to that element:
    completeness  B-CMP-* rules
    validity      B-VAL-* rules and code-set rules (check_class codeset_*)
Status (one of):
    MEETS          every measured rate is at or above its minimum, and no bound code set is missing
    BELOW          a measured rate is under its minimum
    NOT_EXERCISED  the element's domain is not delivered / no bound rule is active / nothing was evaluated
    NOT_LOADED     a bound code-set rule cannot run because the code set is absent (never a silent MEETS)
Inputs are plain Python (compiled rules or dicts, result rows as dicts), so this is testable without Postgres.
"""
from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
STATUSES = ("MEETS", "BELOW", "NOT_EXERCISED", "NOT_LOADED")


def _get(r, k, default=None):
    if isinstance(r, dict):
        return r.get(k, (r.get("header") or {}).get(k, default))
    v = getattr(r, k, None)
    return v if v is not None else (getattr(r, "header", {}) or {}).get(k, default)


def rule_bindings(state: str, cfg: dict | None = None) -> dict[str, list[str]]:
    """{rule_id: sorted canonical elements the rule binds} for a state's enabled catalog: the rule's `requires`
    plus every element its assert / applies_when / numerator / denominator reads (as compile_rules computes)."""
    from dqagent.rules.model import Resolver, _load_yaml, load_catalog, load_state
    st = cfg if cfg is not None else load_state(state)
    elements = _load_yaml(ROOT / "rules" / "elements.yaml")
    res = Resolver(elements, st["elements"])
    out: dict[str, set] = {}
    for rule in load_catalog(st["profile"]["packs"], elements):
        needed = set(rule.get("requires", []))
        if rule.get("distinct"):
            needed.add(rule["distinct"])
        for part in ("assert", "applies_when", "numerator", "denominator"):
            if rule.get(part) is not None:
                needed |= res.elements_used(rule[part])
        out.setdefault(rule["id"], set()).update(needed)
    return {k: sorted(v) for k, v in sorted(out.items())}


def load_cde_defaults(path: str | Path | None = None) -> dict:
    """The seed CDE content (rules/cde_defaults.yaml) if present; {} otherwise."""
    p = Path(path) if path else ROOT / "rules" / "cde_defaults.yaml"
    if not p.exists():
        return {}
    doc = yaml.safe_load(p.read_text()) or {}
    return doc.get("cdes", doc.get("cde", doc))


def _role(rule) -> str | None:
    rid = str(_get(rule, "id"))
    cc = str(_get(rule, "check_class") or "")
    if rid.startswith("B-CMP-"):
        return "completeness"
    if rid.startswith("B-VAL-") or cc.startswith("codeset"):
        return "validity"
    return None


def latest_results(results: list[dict]) -> list[dict]:
    """Latest eval_version per (unit_key, rule_id)."""
    best: dict = {}
    for r in results:
        k = (r.get("unit_key"), r["rule_id"])
        if k not in best or (r.get("eval_version") or 0) >= (best[k].get("eval_version") or 0):
            best[k] = r
    return [best[k] for k in sorted(best, key=lambda x: (str(x[0]), x[1]))]


def _rate(rows: list[dict]) -> tuple[float | None, int, int]:
    ev = sum(int(r.get("evaluated") or 0) for r in rows)
    fl = sum(int(r.get("failed") or 0) for r in rows)
    return (None if ev == 0 else round(1 - fl / ev, 6)), ev, fl


def cde_status(cde: dict, rules: list, results: list[dict], bindings: dict[str, list[str]]) -> list[dict]:
    """Per-CDE MVDQ status. Sorted by element.

    cde       effective CDE content {element: {mvdq: {completeness_min, validity_min}, steward_domain}}
    rules     compiled rules (CompiledRule or dicts with id, status, check_class, entity)
    results   dq_ctl.results rows as dicts {unit_key, eval_version, rule_id, disposition, evaluated, failed}
    bindings  {rule_id: [elements]} (rule_bindings(state))
    """
    latest = latest_results(results)
    by_rule: dict[str, list[dict]] = {}
    for r in latest:
        if r.get("disposition") in ("NOT_APPLICABLE", "NOT_LOADED"):
            continue
        by_rule.setdefault(r["rule_id"], []).append(r)
    out = []
    for el in sorted(cde):
        spec = cde[el] or {}
        mv = spec.get("mvdq") or {}
        bound = [r for r in rules if el in bindings.get(str(_get(r, "id")), []) and _role(r)]
        comp = [r for r in bound if _role(r) == "completeness"]
        val = [r for r in bound if _role(r) == "validity"]
        active = {str(_get(r, "id")) for r in bound if _get(r, "status") == "ACTIVE"}
        not_loaded = sorted({str(_get(r, "id")) for r in val if _get(r, "status") == "NOT_LOADED"})
        c_rate, c_ev, c_fl = _rate([x for r in comp if str(_get(r, "id")) in active
                                    for x in by_rule.get(str(_get(r, "id")), [])])
        v_rate, v_ev, v_fl = _rate([x for r in val if str(_get(r, "id")) in active
                                    for x in by_rule.get(str(_get(r, "id")), [])])
        row = {"element": el, "steward_domain": spec.get("steward_domain"),
               "thresholds": {"completeness_min": mv.get("completeness_min"), "validity_min": mv.get("validity_min")},
               "completeness": c_rate, "validity": v_rate, "evaluated": max(c_ev, v_ev),
               "failed": {"completeness": c_fl, "validity": v_fl},
               "rules": sorted({f"{_get(r, 'id')}@{_get(r, 'entity')}:{_get(r, 'status')}" for r in bound}),
               "not_loaded_rules": not_loaded}
        below = [d for d, rate, mn in (("completeness", c_rate, mv.get("completeness_min")),
                                        ("validity", v_rate, mv.get("validity_min")))
                 if rate is not None and mn is not None and rate < float(mn)]
        if not bound:
            row["status"], row["reason"] = "NOT_EXERCISED", "no completeness / validity rule binds this element"
        elif below:
            row["status"], row["reason"] = "BELOW", "under the minimum for " + ", ".join(below)
        elif c_rate is None and v_rate is None:
            if not_loaded and not active:
                row["status"], row["reason"] = "NOT_LOADED", "code set not loaded: " + ", ".join(not_loaded)
            else:
                why = sorted({str(_get(r, "status_reason") or _get(r, "status")) for r in bound
                              if _get(r, "status") != "ACTIVE"})
                row["status"] = "NOT_EXERCISED"
                row["reason"] = "; ".join(why) if why and not active else "no records evaluated"
        elif not_loaded:
            row["status"], row["reason"] = "NOT_LOADED", "code set not loaded: " + ", ".join(not_loaded)
        else:
            row["status"], row["reason"] = "MEETS", ""
        out.append(row)
    return out
