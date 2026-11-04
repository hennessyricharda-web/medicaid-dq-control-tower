"""DQ assertions export (Part E §8 B3): GET /api/v1/export/dq-assertions.

One row per (asset, element, rule, batch, disposition):
    {asset, element, rule, dimension, disposition, evaluated, failed, batch, ruleset_version, effective_hash, cr_id}
    asset      the rule's relation (the gated dataset)
    element    one canonical element the rule binds (one row per bound element; null when it binds none)
    rule       "<rule_id>@<entity>"
    evaluated  rows evaluated (summed over the batch's units, latest eval_version per unit)
    failed     rows failed, suppressed to null when 1-10
Part 2 units are excluded entirely. No data values: fields come from the allow-list FIELDS only.
"""
from __future__ import annotations

from dqagent.adapters.metadata._common import _get, allow, suppress

FIELDS = ("asset", "element", "rule", "dimension", "disposition", "evaluated", "failed", "batch",
          "ruleset_version", "effective_hash", "cr_id")


def build_assertions(results: list[dict], rules: list, units: list[dict], bindings: dict[str, list[str]], *,
                     cr_id: str | None = None) -> list[dict]:
    """results  dq_ctl.results rows {unit_key, eval_version, rule_id, rule_hash, disposition, evaluated, failed,
                ruleset_version}
       rules    compiled rules (CompiledRule or dicts {id, entity, relation, dimension, effective_hash})
       units    dq_ctl.units rows {unit_key, batch_id, node, part2?}
       bindings {rule_id: [elements]}
       cr_id    the change request that approved the effective ruleset (if known)"""
    unit = {u["unit_key"]: u for u in units}
    by_rel = {(str(_get(r, "id")), _get(r, "relation")): r for r in rules}
    latest: dict = {}
    for r in results:
        k = (r["unit_key"], r["rule_id"])
        if k not in latest or (r.get("eval_version") or 0) >= (latest[k].get("eval_version") or 0):
            latest[k] = r
    agg: dict = {}
    for (uk, rid), r in latest.items():
        u = unit.get(uk)
        if u is None or u.get("part2"):
            continue
        rule = by_rel.get((rid, u.get("node")))
        if rule is None:
            continue
        k = (u.get("node"), rid, str(_get(rule, "entity")), u.get("batch_id"), r["disposition"],
             r.get("ruleset_version"), _get(rule, "effective_hash") or r.get("rule_hash"),
             _get(rule, "dimension"))
        a = agg.setdefault(k, [0, 0])
        a[0] += int(r.get("evaluated") or 0)
        a[1] += int(r.get("failed") or 0)
    out = []
    for (asset, rid, ent, batch, disp, rsv, eh, dim), (ev, fl) in agg.items():
        for el in (bindings.get(rid) or [None]):
            out.append(allow({"asset": asset, "element": el, "rule": f"{rid}@{ent}", "dimension": dim,
                              "disposition": disp, "evaluated": ev, "failed": suppress(fl), "batch": batch,
                              "ruleset_version": rsv, "effective_hash": eh, "cr_id": cr_id}, FIELDS))
    return sorted(out, key=lambda d: tuple("" if d[f] is None else str(d[f]) for f in
                                            ("asset", "rule", "element", "batch", "disposition")))
