"""Parallel run, legacy vs new (Part E §3.3). Pure: load the generic legacy-totals file and compare.

legacy_totals JSON: {system, period, as_of, rows: [{relation, measure, value}]}
    measure: records | amount:<column> | rule_failed:<rule_id>
compare() matches per (period, relation, measure) against new-side values for the SAME period and as_of, and
returns rows shaped for dq_ctl.parallel_run_results:
    {run_id, period, as_of, relation, measure, legacy, new, delta, tolerance, status, explain_code}
status: MATCH (delta 0) | WITHIN (|delta| within tolerance) | BREAK. explain_code is always None here: a named
person sets it through the API. Tolerance per measure (PROFILE parallel_run.tolerance), e.g.
{records: 0, amount: 0.005, rule_failed: 0}: records / rule_failed are absolute, amount is RELATIVE to legacy.
A measure-specific key (e.g. "amount:total_charge") overrides its family. Advisory: never holds units.
"""
from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

DEFAULT_TOLERANCE = {"records": 0, "amount": 0.005, "rule_failed": 0}
STATUSES = ("MATCH", "WITHIN", "BREAK")
EXPLAIN_CODES = ("TIMING", "LEGACY_DEFECT", "NEW_DEFECT", "DEFINITION", "FIXED")


def load(path) -> dict:
    doc = json.loads(Path(path).read_text())
    for k in ("system", "period", "as_of", "rows"):
        if k not in doc:
            raise ValueError(f"{path}: legacy_totals lacks {k}")
    for r in doc["rows"]:
        if set(r) - {"relation", "measure", "value"} or not {"relation", "measure", "value"} <= set(r):
            raise ValueError(f"{path}: legacy_totals row must be exactly {{relation, measure, value}}: {r}")
        fam = r["measure"].split(":")[0]
        if fam not in ("records", "amount", "rule_failed") or (fam != "records" and ":" not in r["measure"]):
            raise ValueError(f"{path}: unknown measure {r['measure']!r}")
    return doc


def _tol(measure: str, tolerance: dict) -> tuple[float, bool]:
    fam = measure.split(":")[0]
    t = tolerance.get(measure, tolerance.get(fam, DEFAULT_TOLERANCE.get(fam, 0)))
    return float(t), fam == "amount"


def new_side_from_metrics(metric_rows: list[dict], rule_failed: dict[str, int] | None = None) -> dict:
    """New-side values {(relation, measure): value} from monitor metric rows (rows -> records, amount:<col>)
    plus optional {"<relation>|<rule_id>": failed} counts."""
    out = {}
    for r in metric_rows:
        if r["metric"] == "rows":
            out[(r["relation"], "records")] = r["value"]
        elif r["metric"].startswith("amount:"):
            out[(r["relation"], r["metric"])] = r["value"]
    for k, v in (rule_failed or {}).items():
        rel, rid = k.split("|", 1)
        out[(rel, f"rule_failed:{rid}")] = v
    return out


def _out(d: Decimal | None, integral: bool):
    if d is None:
        return None
    return int(d) if integral and d == d.to_integral_value() else float(d)


def compare(legacy: dict, new: dict, *, period: str, as_of: str, tolerance: dict | None = None,
            run_id: str = "") -> list[dict]:
    """legacy: a loaded legacy_totals doc; new: {(relation, measure): value} for the same period / as_of."""
    if legacy["period"] != period or str(legacy["as_of"]) != str(as_of):
        raise ValueError(f"legacy totals are for {legacy['period']} as of {legacy['as_of']}, "
                         f"not {period} as of {as_of}")
    tolerance = {**DEFAULT_TOLERANCE, **(tolerance or {})}
    out = []
    for r in legacy["rows"]:
        k = (r["relation"], r["measure"])
        lv = Decimal(str(r["value"]))
        nv = new.get(k)
        t, relative = _tol(r["measure"], tolerance)
        if nv is None:
            delta, status = None, "BREAK"
        else:
            nd = Decimal(str(nv))
            delta = nd - lv
            if delta == 0:
                status = "MATCH"
            else:
                allowed = Decimal(str(t)) * (abs(lv) if relative else 1)
                status = "WITHIN" if abs(delta) <= allowed else "BREAK"
        integral = not relative
        out.append({"run_id": run_id, "period": period, "as_of": str(as_of), "relation": r["relation"],
                    "measure": r["measure"], "legacy": _out(lv, integral),
                    "new": None if nv is None else _out(Decimal(str(nv)), integral),
                    "delta": _out(delta, integral), "tolerance": int(t) if integral and t == int(t) else t,
                    "status": status, "explain_code": None})
    return sorted(out, key=lambda x: (x["relation"], x["measure"]))
