"""MONITOR seat computation (Part E §6): distribution and statistical-process-control monitoring. ADVISORY ONLY.

This module is PURE computation plus one data-plane aggregate query helper. It never writes to the control plane;
the engine seat (dq_monitor_svc) stores the metric rows in dq_ctl.monitor_metrics, pins baselines as SPC_BASELINE
memory, and posts SPC_SIGNAL / MIX_SHIFT / BASELINE_BUILDING. Nothing here changes a disposition or a mode.

Metrics (aggregates only; one row per (period, relation, metric, key), shaped like dq_ctl.monitor_metrics):
    rows                       key ""          value = row count            n = row count
    amount:<payment column>    key ""          value = sum                  n = row count
    null_rate:<CDE element>    key ""          value = null share           n = row count
    mix:<CDE element>          key <category>  value = category share       n = row count (period total)

Charts:
    XmR      rows, amount       CL = mean, MR-bar = mean |x_i - x_(i-1)|, sigma = MR-bar / 1.128,
                                limits = CL +/- 2.66 * MR-bar (= 3 sigma)
    p-chart  null_rate          p-bar = sum(nulls) / sum(n) over the baseline; per-period limits
                                p-bar +/- 3 * sqrt(p-bar * (1 - p-bar) / n), clipped to [0, 1]
    PSI      mix                PSI = sum((a - e) * ln(a / e)) over bins of shares; computed ONLY when every bin has
                                an expected count (e * n) of at least 5. Rare categories fold into OTHER, which is
                                kept (never dropped). 0.10 <= PSI < 0.25 is WATCH, PSI >= 0.25 is SHIFT.
Run rules:  R1 (a point beyond the limits) is always on. R2 (8 consecutive points on one side of CL) is
            available and OFF by default (config rules.R2).
Baselines:  at least 8 eligible prior periods (default up to 20, most recent), periods with an open signal are
            excluded (no poisoning), and the result is frozen: it carries its own hash and is evaluated as-is.
            With fewer eligible periods the status is BASELINE_BUILDING and nothing is signalled.
"""
from __future__ import annotations

import copy
import hashlib
import itertools
import json
import math
from pathlib import Path

D2 = 1.128                     # bias constant for moving ranges of span 2
XMR_FACTOR = 2.66              # 3 / d2
OTHER = "OTHER"

DEFAULT_CONFIG = {
    "rules": {"R1": True, "R2": False},
    "r2_run_length": 8,
    "min_baseline_periods": 8,
    "max_baseline_periods": 20,
    "psi": {"watch": 0.10, "shift": 0.25, "min_expected": 5, "epsilon": 1e-4},
}

STATUS = ("OK", "SIGNAL", "WATCH", "SHIFT", "BASELINE_BUILDING", "NOT_COMPUTED", "PROVISIONAL")
# PROVISIONAL (live smoke L4): the period has not closed; a reading on part of a period is never a SIGNAL


def _canon(o) -> str:
    return json.dumps(o, sort_keys=True, separators=(",", ":"), default=str)


def _h(o) -> str:
    return hashlib.sha256(_canon(o).encode()).hexdigest()


def config(overrides: dict | None = None) -> dict:
    """DEFAULT_CONFIG deep-merged with a MONITOR config (artifact content). R1 cannot be switched off."""
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    for k, v in (overrides or {}).items():
        if isinstance(v, dict) and isinstance(cfg.get(k), dict):
            cfg[k].update(v)
        else:
            cfg[k] = v
    cfg["rules"]["R1"] = True
    return cfg


def chart_for(metric: str) -> str:
    if metric == "rows" or metric.startswith("amount:"):
        return "xmr"
    if metric.startswith("null_rate:"):
        return "p"
    if metric.startswith("mix:"):
        return "psi"
    raise ValueError(f"unknown monitor metric {metric!r}")


def _r(x: float, nd: int = 6) -> float:
    return float(round(x, nd))


# ------------------------------------------------------------------------------------------------ chart maths
def xmr_limits(values: list[float]) -> dict:
    """Individuals and moving range over the values IN ORDER (moving ranges between adjacent eligible points)."""
    if len(values) < 2:
        raise ValueError("XmR needs at least 2 points")
    xs = [float(v) for v in values]
    cl = sum(xs) / len(xs)
    mrs = [abs(b - a) for a, b in itertools.pairwise(xs)]
    mr_bar = sum(mrs) / len(mrs)
    sigma = mr_bar / D2
    return {"cl": cl, "mr_bar": mr_bar, "sigma": sigma, "ucl": cl + XMR_FACTOR * mr_bar,
            "lcl": cl - XMR_FACTOR * mr_bar}


def p_limits(p_bar: float, n: int) -> tuple[float, float]:
    """Per-n p-chart limits, clipped to [0, 1]."""
    if n <= 0:
        raise ValueError("p-chart needs n > 0")
    s = math.sqrt(p_bar * (1 - p_bar) / n)
    return max(0.0, p_bar - 3 * s), min(1.0, p_bar + 3 * s)


def fold_bins(expected: dict[str, float], actual: dict[str, int], n: int, min_expected: float) -> tuple[dict, dict]:
    """Fold rare categories (expected count e*n < min_expected) and categories absent from the baseline into OTHER.
    If OTHER itself is non-empty and still under min_expected, the next-smallest category joins it, so OTHER is
    kept rather than dropped. Returns (expected shares, actual counts) per bin."""
    cats = sorted(set(expected) | set(actual))
    keep = {c for c in cats if c != OTHER and expected.get(c, 0.0) * n >= min_expected}
    while True:
        e_other = sum(expected.get(c, 0.0) for c in cats if c not in keep)
        a_other = sum(actual.get(c, 0) for c in cats if c not in keep)
        if (e_other == 0 and a_other == 0) or e_other * n >= min_expected or not keep:
            break
        smallest = min(keep, key=lambda c: (expected.get(c, 0.0), c))
        keep.discard(smallest)
    e_bins = {c: expected.get(c, 0.0) for c in sorted(keep)}
    a_bins = {c: actual.get(c, 0) for c in sorted(keep)}
    if e_other > 0 or a_other > 0:
        e_bins[OTHER] = e_other
        a_bins[OTHER] = a_other
    return e_bins, a_bins


def psi(expected: dict[str, float], actual: dict[str, int], *, min_expected: float = 5, epsilon: float = 1e-4) -> dict:
    """PSI of actual counts against expected shares. {psi|None, bins, computed, reason}."""
    n = sum(actual.values())
    if n <= 0:
        return {"psi": None, "computed": False, "reason": "no rows in the period", "bins": {}}
    e_bins, a_bins = fold_bins(expected, actual, n, min_expected)
    bins = {c: {"expected_share": _r(e_bins[c]), "expected_count": _r(e_bins[c] * n, 2),
                "actual_count": a_bins[c], "actual_share": _r(a_bins[c] / n)} for c in e_bins}
    if len(e_bins) < 2:
        return {"psi": None, "computed": False, "reason": "fewer than 2 bins after folding", "bins": bins}
    short = sorted(c for c in e_bins if e_bins[c] * n < min_expected)
    if short:
        return {"psi": None, "computed": False,
                "reason": f"bins under {min_expected:g} expected: {', '.join(short)}", "bins": bins}
    total = 0.0
    for c in e_bins:
        a = max(a_bins[c] / n, epsilon)
        e = max(e_bins[c], epsilon)
        total += (a - e) * math.log(a / e)
    return {"psi": total, "computed": True, "reason": "", "bins": bins}


# ------------------------------------------------------------------------------------------------ baselines
def _series(rows: list[dict], relation: str, metric: str, key: str | None) -> dict[str, list[dict]]:
    """period -> metric rows for one (relation, metric[, key])."""
    out: dict[str, list[dict]] = {}
    for r in rows:
        if r["relation"] != relation or r["metric"] != metric:
            continue
        if key is not None and (r.get("key") or "") != key:
            continue
        out.setdefault(str(r["period"]), []).append(r)
    return out


def build_baseline(rows: list[dict], relation: str, metric: str, key: str = "", *, before: str | None = None,
                   exclude_periods=(), cfg: dict | None = None) -> dict:
    """A frozen SPC_BASELINE content from prior period metric rows (plain dicts shaped like monitor_metrics).

    rows             may contain any relations / metrics; this picks (relation, metric, key) (all keys for mix)
    before           only periods strictly before this label (sorted lexically: use sortable labels)
    exclude_periods  periods with an open signal on this series (excluded, never poison the baseline)
    """
    cfg = config(cfg)
    chart = chart_for(metric)
    by_p = _series(rows, relation, metric, None if chart == "psi" else key)
    excluded = sorted(p for p in by_p if p in set(map(str, exclude_periods)) and (before is None or p < before))
    eligible = sorted(p for p in by_p if p not in set(excluded) and (before is None or p < before)
                      and not (chart == "p" and not int(by_p[p][0].get("n") or 0)))   # E6: an empty period has no rate
    eligible = eligible[-int(cfg["max_baseline_periods"]):]
    head = {"kind": "SPC_BASELINE", "relation": relation, "metric": metric, "key": "" if chart == "psi" else key,
            "chart": chart, "periods": eligible, "excluded_periods": excluded,
            "min_periods": int(cfg["min_baseline_periods"])}
    if len(eligible) < int(cfg["min_baseline_periods"]):
        head["status"] = "BASELINE_BUILDING"
        head["baseline_hash"] = _h(head)
        return head
    if chart == "xmr":
        vals = [float(by_p[p][0]["value"]) for p in eligible]
        lim = xmr_limits(vals)
        body = {"values": vals, **{k: _r(v) for k, v in lim.items()}}
    elif chart == "p":
        nulls = sum(float(by_p[p][0]["value"]) * int(by_p[p][0]["n"]) for p in eligible)
        n_tot = sum(int(by_p[p][0]["n"]) for p in eligible)
        body = {"p_bar": _r(nulls / n_tot), "n_total": n_tot,
                "values": [float(by_p[p][0]["value"]) for p in eligible], "n": [int(by_p[p][0]["n"]) for p in eligible]}
    else:
        counts: dict[str, float] = {}
        n_tot = 0
        for p in eligible:
            n_p = int(by_p[p][0]["n"])
            n_tot += n_p
            for r in by_p[p]:
                counts[r["key"]] = counts.get(r["key"], 0) + round(float(r["value"]) * n_p)
        body = {"expected": {c: _r(v / n_tot) for c, v in sorted(counts.items())}, "n_total": n_tot}
    out = {**head, "status": "FROZEN", **body}
    out["baseline_hash"] = _h(out)
    return out


def verify_baseline(baseline: dict) -> bool:
    """A pinned baseline is frozen: its content must still hash to baseline_hash."""
    b = {k: v for k, v in baseline.items() if k != "baseline_hash"}
    return _h(b) == baseline.get("baseline_hash")


# ------------------------------------------------------------------------------------------------ evaluation
def evaluate(baseline: dict, current: list[dict] | dict, *, period: str | None = None, recent: list[float] = (),
             cfg: dict | None = None) -> dict:
    """Evaluate the current period against a FROZEN baseline.

    current  for xmr / p: one metric row {value, n}; for psi: the period's rows {key, value (share), n}
    recent   prior values in order (oldest first) for R2 only (xmr and p charts)
    Returns {relation, metric, key, chart, period, status, value, n, signals[], limits..., baseline_hash}.
    """
    cfg = config(cfg)
    if not verify_baseline(baseline):
        raise ValueError("baseline content does not match its baseline_hash (not frozen)")
    chart = baseline["chart"]
    rows = current if isinstance(current, list) else [current]
    out = {"relation": baseline["relation"], "metric": baseline["metric"], "key": baseline["key"], "chart": chart,
           "period": period if period is not None else (rows[0].get("period") if rows else None),
           "baseline_hash": baseline["baseline_hash"], "baseline_periods": len(baseline["periods"]),
           "excluded_periods": list(baseline.get("excluded_periods", [])), "signals": []}
    if baseline["status"] == "BASELINE_BUILDING":
        out.update(status="BASELINE_BUILDING", value=rows[0]["value"] if rows and chart != "psi" else None,
                   n=rows[0].get("n") if rows else None)
        return out
    if chart in ("xmr", "p"):
        r = rows[0]
        x, n = float(r["value"]), int(r.get("n") or 0)
        if chart == "p" and n <= 0:
            # E6: an empty period (e.g. an empty delivery) has no null rate to compare: NOT_COMPUTED, never a crash
            out.update(status="NOT_COMPUTED", value=None, n=n, reason="no records in the period")
            return out
        if chart == "xmr":
            cl, ucl, lcl, sigma = baseline["cl"], baseline["ucl"], baseline["lcl"], baseline["sigma"]
        else:
            cl = baseline["p_bar"]
            lcl, ucl = p_limits(cl, n)
            sigma = math.sqrt(cl * (1 - cl) / n)
            lcl, ucl, sigma = _r(lcl), _r(ucl), _r(sigma)
        out.update(value=x, n=n, cl=cl, ucl=ucl, lcl=lcl, sigma=sigma,
                   sigma2=[_r(cl - 2 * sigma), _r(cl + 2 * sigma)])
        if x > ucl or x < lcl:
            out["signals"].append({"rule": "R1", "direction": "HIGH" if x > ucl else "LOW",
                                   "detail": "point beyond the 3-sigma limits"})
        if cfg["rules"].get("R2"):
            k = int(cfg["r2_run_length"])
            run = [float(v) for v in list(recent)[-(k - 1):]] + [x]
            if len(run) >= k and (all(v > cl for v in run) or all(v < cl for v in run)):
                out["signals"].append({"rule": "R2", "direction": "HIGH" if x > cl else "LOW",
                                       "detail": f"{k} consecutive points on one side of the centre line"})
        out["status"] = "SIGNAL" if out["signals"] else "OK"
        return out
    # psi
    n = int(rows[0]["n"]) if rows else 0
    actual = {r["key"]: round(float(r["value"]) * int(r["n"])) for r in rows}
    res = psi(baseline["expected"], actual, min_expected=cfg["psi"]["min_expected"], epsilon=cfg["psi"]["epsilon"])
    out.update(n=n, value=None if res["psi"] is None else _r(res["psi"]), bins=res["bins"])
    if not res["computed"]:
        out.update(status="NOT_COMPUTED", reason=res["reason"])
        return out
    v = res["psi"]
    if v >= cfg["psi"]["shift"]:
        out["status"] = "SHIFT"
    elif v >= cfg["psi"]["watch"]:
        out["status"] = "WATCH"
    else:
        out["status"] = "OK"
    if out["status"] != "OK":
        out["signals"].append({"rule": "PSI", "direction": out["status"],
                               "detail": f"population stability index {out['value']}"})
    out["psi_thresholds"] = {"watch": cfg["psi"]["watch"], "shift": cfg["psi"]["shift"]}
    return out


def combine_partials(rows: list[dict]) -> list[dict]:
    """One period's per-batch metric rows (each batch's own aggregates) combined into period-so-far rows, exactly:
    rows / amount are sums; null_rate is the n-weighted mean; mix shares are recombined from per-batch counts (each
    batch's total n counted once). Rows need batch_id. Deterministic and sorted."""
    groups: dict = {}
    for r in rows:
        groups.setdefault((r["period"], r["relation"], r["metric"]), []).append(r)
    out = []
    for (period, rel, metric), rs in sorted(groups.items()):
        chart = chart_for(metric)
        batch_n = {r["batch_id"]: int(r.get("n") or 0) for r in rs}
        n = sum(batch_n.values())
        if chart == "xmr":
            out.append({"period": period, "relation": rel, "metric": metric, "key": "",
                        "value": _r(sum(float(r["value"]) for r in rs), 2), "n": n})
        elif chart == "p":
            nulls = sum(float(r["value"]) * int(r.get("n") or 0) for r in rs)
            out.append({"period": period, "relation": rel, "metric": metric, "key": "",
                        "value": _r(nulls / n) if n else 0.0, "n": n})
        else:
            counts: dict = {}
            for r in rs:
                counts[r["key"]] = counts.get(r["key"], 0) + round(float(r["value"]) * int(r.get("n") or 0))
            for k, cnt in sorted(counts.items()):
                out.append({"period": period, "relation": rel, "metric": metric, "key": k,
                            "value": _r(cnt / n) if n else 0.0, "n": n})
    return out


def period_bounds(label: str):
    """(first day, first day after) of a period label: 'YYYY-Qn' or 'YYYY-MM'; None for any other label."""
    import datetime as _dt
    import re as _re
    m = _re.fullmatch(r"(\d{4})-Q([1-4])", str(label or ""))
    if m:
        y, q = int(m.group(1)), int(m.group(2))
        start = _dt.date(y, 3 * q - 2, 1)
        end = _dt.date(y + 1, 1, 1) if q == 4 else _dt.date(y, 3 * q + 1, 1)
        return start, end
    m = _re.fullmatch(r"(\d{4})-(\d{2})", str(label or ""))
    if m:
        y, mo = int(m.group(1)), int(m.group(2))
        return _dt.date(y, mo, 1), (_dt.date(y + 1, 1, 1) if mo == 12 else _dt.date(y, mo + 1, 1))
    return None


def received_share(label: str, through) -> float | None:
    """Share of a period's calendar received when deliveries run through `through` (a date), clipped to [0, 1]."""
    b = period_bounds(label)
    if b is None or through is None:
        return None
    start, end = b
    return max(0.0, min(1.0, ((through - start).days + 1) / (end - start).days))


def provisional(result: dict, *, share: float | None, through=None) -> dict:
    """A result on an OPEN period (live smoke L4): status PROVISIONAL, no signals (a signal needs a closed period),
    with the share of the period received. The would-be reading is not exposed as a status."""
    out = dict(result)
    out.update(status="PROVISIONAL", signals=[], received_pct=None if share is None else round(share * 100, 1),
               received_through=None if through is None else str(through))
    pct = "an unknown share" if share is None else f"about {round(share * 100, 1)}%"
    out["note"] = (f"Provisional: {pct} of {result.get('period')} received"
                   + (f" (deliveries through {through})" if through is not None else "")
                   + ". Signals are raised only when the period closes.")
    return out


def series_keys(rows: list[dict]) -> list[tuple[str, str, str]]:
    """Distinct (relation, metric, key) series; mix series are one series per (relation, metric) with key ""."""
    out = set()
    for r in rows:
        out.add((r["relation"], r["metric"], "" if chart_for(r["metric"]) == "psi" else (r.get("key") or "")))
    return sorted(out)


def run(history: list[dict], current: list[dict], *, period: str, exclude: dict | None = None,
        baselines: dict | None = None, cfg: dict | None = None) -> list[dict]:
    """Evaluate every series in `current` (rows of ONE period) against baselines.

    baselines  {(relation, metric, key): frozen baseline}; a pinned baseline is reused as-is (frozen). Missing
               ones are built from `history` (prior periods), excluding `exclude[(relation, metric, key)]` periods.
    Sorted, deterministic output; each result carries the baseline it used under "baseline".
    """
    baselines = dict(baselines or {})
    exclude = exclude or {}
    out = []
    for (rel, metric, key) in series_keys(current):
        sk = (rel, metric, key)
        b = baselines.get(sk) or build_baseline(history, rel, metric, key, before=period,
                                                exclude_periods=exclude.get(sk, ()), cfg=cfg)
        chart = chart_for(metric)
        cur = [r for r in current if r["relation"] == rel and r["metric"] == metric
               and (chart == "psi" or (r.get("key") or "") == key)]
        recent = []
        if chart != "psi" and b.get("status") == "FROZEN":
            by_p = _series(history, rel, metric, key)
            recent = [float(by_p[p][0]["value"]) for p in sorted(by_p) if p < period]
        res = evaluate(b, cur if chart == "psi" else cur[0], period=period, recent=recent, cfg=cfg)
        res["baseline"] = b
        out.append(res)
    return out


# ------------------------------------------------------------------------------------------------ board posts
def board_post(result: dict) -> tuple[str, dict] | None:
    """(template, body) for the blackboard, or None when there is nothing to post. Identifiers and aggregates only."""
    base = {"relation": result["relation"], "metric": result["metric"], "period": result.get("period"),
            "baseline_hash": result["baseline_hash"]}
    if result["status"] == "BASELINE_BUILDING":
        return "BASELINE_BUILDING", {**base, "eligible_periods": result["baseline_periods"]}
    if result["chart"] == "psi" and result["status"] in ("WATCH", "SHIFT"):
        return "MIX_SHIFT", {**base, "psi": result["value"], "status": result["status"]}
    if result["status"] == "SIGNAL":
        return "SPC_SIGNAL", {**base, "key": result["key"], "value": result["value"],
                              "lcl": result["lcl"], "ucl": result["ucl"],
                              "rules": [s["rule"] for s in result["signals"]]}
    return None


# ------------------------------------------------------------------------------------------------ data plane
def _qi(name: str) -> str:
    """Quote a dotted identifier for DuckDB (relation / column names from config, never user input)."""
    parts = name.split(".")
    for p in parts:
        if not p.replace("_", "").isalnum():
            raise ValueError(f"unsafe identifier {name!r}")
    return ".".join(f'"{p}"' for p in parts)


def query_aggregates(con, relation: str, spec: dict, bindings: dict[str, str], *, period: str,
                     batch_ids: list[str] | None = None) -> list[dict]:
    """Compute one period's metric rows for one relation IN THE DATA PLANE (aggregates only, no row values leave).

    con        a DuckDB connection / cursor (anything with .execute(sql, params).fetchall())
    spec       {"rows": true, "amount": [<payment column>], "null_rate": [<element>], "mix": [<element>]}
    bindings   element -> column for this state (config/state/<ST>/elements.yaml "columns")
    batch_ids  restrict to these _dq_batch_id values (None = the whole relation)
    Returns rows {period, relation, metric, key, value, n} sorted by (metric, key).
    """
    rel = _qi(relation)
    where, params = "", []
    if batch_ids is not None:
        if not batch_ids:
            return []
        where = " WHERE _dq_batch_id IN (" + ", ".join("?" for _ in batch_ids) + ")"
        params = list(batch_ids)
    n = int(con.execute(f"SELECT count(*) FROM {rel}{where}", params).fetchall()[0][0])
    out = []
    if spec.get("rows", True):
        out.append({"period": period, "relation": relation, "metric": "rows", "key": "", "value": n, "n": n})
    for col in spec.get("amount", []) or []:
        s = con.execute(f"SELECT coalesce(sum({_qi(col)}), 0) FROM {rel}{where}", params).fetchall()[0][0]
        out.append({"period": period, "relation": relation, "metric": f"amount:{col}", "key": "",
                    "value": float(round(float(s), 2)), "n": n})
    for el in spec.get("null_rate", []) or []:
        col = _qi(bindings[el])
        k = int(con.execute(f"SELECT count(*) FILTER (WHERE {col} IS NULL) FROM {rel}{where}", params).fetchall()[0][0])
        out.append({"period": period, "relation": relation, "metric": f"null_rate:{el}", "key": "",
                    "value": _r(k / n) if n else 0.0, "n": n})
    for el in spec.get("mix", []) or []:
        col = _qi(bindings[el])
        for cat, k in con.execute(f"SELECT coalesce(CAST({col} AS VARCHAR), '(null)') AS c, count(*) FROM {rel}{where} "
                                  "GROUP BY 1 ORDER BY 1", params).fetchall():
            out.append({"period": period, "relation": relation, "metric": f"mix:{el}", "key": str(cat),
                        "value": _r(int(k) / n) if n else 0.0, "n": n})
    return sorted(out, key=lambda r: (r["metric"], r["key"]))


# ------------------------------------------------------------------------------------------------ history files
def load_history(path) -> dict:
    """Load a monitor history file (synthetic/out/<run>/monitor_history.json).

    {schema: "monitor_history/1", state, period_grain, current_period, spec{relation: spec}, bindings{el: col},
     periods[], metrics[{period, relation, metric, key, value, n}], signalled[{period, relation, metric, key, rule}]}
    Returns the document with `exclude` added: {(relation, metric, key): [periods]} from `signalled`.
    """
    doc = json.loads(Path(path).read_text())
    if doc.get("schema") != "monitor_history/1":
        raise ValueError(f"{path}: not a monitor_history/1 document")
    for r in doc["metrics"]:
        for k in ("period", "relation", "metric", "key", "value", "n"):
            if k not in r:
                raise ValueError(f"{path}: metric row lacks {k}")
        chart_for(r["metric"])
    ex: dict = {}
    for s in doc.get("signalled", []):
        sk = (s["relation"], s["metric"], "" if chart_for(s["metric"]) == "psi" else (s.get("key") or ""))
        ex.setdefault(sk, []).append(s["period"])
    doc["exclude"] = {k: sorted(v) for k, v in ex.items()}
    return doc
