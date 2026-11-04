"""Facts for explanations (Part E §7). Built from control-plane rows and aggregates ONLY.

The facts are the only thing a provider ever sees, and the only thing a template may say. This module:
  * drops every key that names an element or column of a masked class (identity, clinical, part2), drops
    value-like keys (value, bins, ...) of any fact ABOUT such an element (element/column names it; the element's
    NAME is metadata and is kept), and never carries row samples / tokens;
  * withholds everything about a Part 2 unit (facts carrying part2=true): only the kind and a withheld flag remain;
  * suppresses small cells: a count key with a value from 1 to 10 becomes the string "<11";
  * normalises numbers (floats rounded; rates gain a *_pct display fact) so a template prints only fact values;
  * is deterministic: the same input gives the same facts and the same facts_hash.
"""
from __future__ import annotations

import hashlib
import json
import re

MASKED_CLASSES = {"identity", "clinical", "part2"}
SUPPRESSED = "<11"
SMALL_CELL_MAX = 10
# keys that hold counts of records / units / people (small-cell suppressed); approval counts etc. are not counts
COUNT_KEYS = {"count", "failed", "evaluated", "rows", "records", "n", "units", "unit_count", "rows_affected",
              "held_units", "released_units", "failed_units", "nulls", "detail_rows"}
COUNT_SUFFIXES = ("_count", "_rows", "_units", "_records", "_failed", "_evaluated")
RATE_SUFFIXES = ("rate", "share", "_pct_raw")
RATE_KEYS = {"rate", "share", "completeness", "validity", "completeness_min", "validity_min", "p_bar",
             "relative_delta"}
# row-level material never enters facts; and value-like keys of a dict ABOUT a masked element are dropped
ALWAYS_DROP = {"sample_tokens", "samples", "sample", "examples", "example", "raw", "row", "rows_sample", "values_seen"}
VALUE_KEYS = {"value", "values", "observed", "expected_value", "top_values", "categories", "bins"}
# always-safe structural keys (never treated as element names)
_STRUCTURAL = {"kind", "relation", "metric", "key", "chart", "status", "rule", "rule_id", "disposition"}


def facts_hash(facts: dict) -> str:
    return hashlib.sha256(canonical(facts).encode()).hexdigest()


def canonical(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str, ensure_ascii=True)


def _is_count(key: str) -> bool:
    k = key.lower()
    return k in COUNT_KEYS or k.endswith(COUNT_SUFFIXES)


def _is_rate(key: str) -> bool:
    k = key.lower()
    return k in RATE_KEYS or k.endswith(RATE_SUFFIXES)


def _masked_names(classification: dict | None) -> set[str]:
    """Element ids and column names whose effective class is masked (lower-cased)."""
    out = set()
    classification = classification or {}
    if isinstance(classification.get("classes"), dict):      # rules/classification_defaults.yaml shape
        classification = classification["classes"]
    for name, cls in classification.items():
        classes = cls if isinstance(cls, (list, tuple, set)) else [cls]
        if any(str(c).lower() in MASKED_CLASSES for c in classes):
            out.add(str(name).lower())
    return out


def _num(v):
    if isinstance(v, bool) or v is None:
        return v
    if isinstance(v, int):
        return v
    if isinstance(v, float):
        if v == int(v) and abs(v) < 1e15:
            return int(v)
        return round(v, 4) if abs(v) < 1 else round(v, 2)
    try:                                   # Decimal and friends
        f = float(v)
    except (TypeError, ValueError):
        return v
    return _num(f)


def _clean(key: str, v, masked: set[str]):
    if isinstance(v, dict):
        about_masked = any(str(v.get(f, "")).lower() in masked for f in ("element", "column"))
        out = {}
        for k in sorted(v, key=str):
            ks = str(k)
            if ks.lower() in masked or ks.lower() in ALWAYS_DROP or (about_masked and ks.lower() in VALUE_KEYS):
                continue
            c = _clean(ks, v[k], masked)
            if c is not _DROP:
                out[ks] = c
        return out
    if isinstance(v, (list, tuple)):
        items = [_clean(key, x, masked) for x in v]
        return [x for x in items if x is not _DROP]
    if isinstance(v, str):
        return v
    v = _num(v)
    if _is_count(key) and isinstance(v, int) and not isinstance(v, bool) and 1 <= v <= SMALL_CELL_MAX:
        return SUPPRESSED
    return v


class _Drop:
    pass


_DROP = _Drop()


def _derive(f: dict) -> dict:
    """Display facts (percentages) so a template never computes a number that is not a fact."""
    out = dict(f)
    for k, v in f.items():
        if _is_rate(k) and isinstance(v, (int, float)) and not isinstance(v, bool) and 0 <= v <= 1:
            out[f"{k}_pct"] = round(v * 100, 1)
    return out


def build(kind: str, raw: dict, *, classification: dict | None = None) -> dict:
    """Facts for one explanation. `raw` is a plain dict of control-plane fields and aggregates."""
    raw = dict(raw or {})
    if raw.get("part2") or raw.get("part2_program"):
        return {"explanation_kind": kind, "part2_withheld": True}
    masked = _masked_names(classification)
    masked -= _STRUCTURAL
    f = _clean("", raw, masked)
    f = {} if f is _DROP else f
    for k in ("part2", "part2_program"):
        f.pop(k, None)
    f = _derive(f) if isinstance(f, dict) else {}
    for k, v in list(f.items()):
        if isinstance(v, dict):
            f[k] = _derive(v)
    f["explanation_kind"] = kind
    return f


def flatten(facts: dict, prefix: str = "") -> dict[str, object]:
    """Dotted-key view of the facts (claims address facts by these keys)."""
    out: dict[str, object] = {}
    for k, v in facts.items():
        key = f"{prefix}{k}"
        if isinstance(v, dict):
            out.update(flatten(v, key + "."))
        elif isinstance(v, list):
            for i, x in enumerate(v):
                if isinstance(x, dict):
                    out.update(flatten(x, f"{key}.{i}."))
                else:
                    out[f"{key}.{i}"] = x
        else:
            out[key] = v
    return out


# ------------------------------------------------------------------------------------------------ shape adapters
def _metric_parts(metric: str) -> tuple[str, str | None]:
    head, _, tail = metric.partition(":")
    return head, (tail or None)


def adapt(kind: str, raw: dict) -> dict:
    """Map known engine shapes onto the fact keys the templates use. Unknown shapes pass through unchanged.
    Idempotent: adapting adapted facts changes nothing."""
    raw = dict(raw or {})
    if kind == "monitor_signal" and "metric" in raw and ("signals" in raw or "baseline_hash" in raw):
        head, tail = _metric_parts(raw["metric"])
        f = {k: raw[k] for k in ("relation", "metric", "period", "status", "chart", "baseline_periods", "n",
                                 "part2", "part2_program", "received_pct", "received_through") if k in raw}
        f["measure"] = head
        if head == "amount":
            f["column"] = tail
        elif tail:
            f["element"] = tail
        sig = (raw.get("signals") or [{}])[0]
        if sig.get("rule"):
            f["rule"] = sig["rule"]
        if sig.get("direction"):
            f["direction"] = sig["direction"]
        if raw.get("chart") == "xmr":
            for k in ("value", "cl", "ucl", "lcl"):
                if raw.get(k) is not None:
                    f[k] = raw[k]
        elif raw.get("chart") == "p":
            for k in ("value", "cl", "ucl", "lcl"):
                if raw.get(k) is not None:
                    f[f"{k}_rate"] = raw[k]
        elif raw.get("chart") == "psi":
            if raw.get("value") is not None:
                f["psi"] = raw["value"]
            th = raw.get("psi_thresholds") or {}
            if th:
                f["psi_watch"], f["psi_shift"] = th.get("watch"), th.get("shift")
            f["bins"] = [{"category": c, "now_pct": round(b["actual_share"] * 100, 1),
                          "usual_pct": round(b["expected_share"] * 100, 1)}
                         for c, b in sorted((raw.get("bins") or {}).items())]
        if raw.get("reason"):
            f["not_computed_reason"] = raw["reason"]
        if raw.get("status") == "BASELINE_BUILDING":
            f["min_periods"] = (raw.get("baseline") or {}).get("min_periods", 8)
        return f
    if kind == "parallel_break" and "measure" in raw and "measure_kind" not in raw:
        f = dict(raw)
        m = str(raw["measure"])
        if m.startswith("amount:") and raw.get("tolerance") is not None:
            f["tolerance_rate"] = f.pop("tolerance")
        f["measure_kind"] = m.split(":")[0]
        return f
    return raw


# ------------------------------------------------------------------------------------------------ P1-1 §D
def build_result(kind: str, raw: dict, *, classification: dict | None = None) -> tuple[dict, dict]:
    """build() plus the masking counts for the guardrail events: {masked, suppressed, part2_withheld}. The counts
    are of FIELDS (never of people or records), so they are not themselves small cells."""
    facts = build(kind, raw, classification=classification)
    if facts.get("part2_withheld"):
        return facts, {"masked": 0, "suppressed": 0, "part2_withheld": 1}
    masked_names = _masked_names(classification) - _STRUCTURAL
    flat_raw = flatten(dict(raw or {}))
    masked = sum(1 for k in flat_raw if any(p.lower() in masked_names for p in k.split(".")))
    suppressed = sum(1 for k, v in flatten(facts).items() if v == SUPPRESSED and flat_raw.get(k) != SUPPRESSED)
    return facts, {"masked": masked, "suppressed": suppressed, "part2_withheld": 0}


# the reference prefixes the trace tables accept (dq_ctl.span_refs_ok)
_REF_OF = {"unit_key": "unit", "relation": "rel", "rule_id": "rule", "cr_id": "cr", "decision_id": "dec",
           "batch_id": "batch", "file": "file", "pipeline_id": "pipeline", "run_id": "trace", "lookback_id": "unit"}
_SUBJECT_ORDER = ("unit_key", "decision_id", "cr_id", "rule_id", "batch_id", "lookback_id", "run_id", "file",
                  "relation")
_REF_OK = re.compile(r"^[A-Za-z0-9_.:@|/+=-]{1,200}$")


def _ref(prefix: str, v) -> str | None:
    s = str(v)
    return f"{prefix}:{s}" if _REF_OK.match(s) else None


def fact_sources(kind: str, raw: dict) -> dict[str, str]:
    """{fact_key: evidence_ref} for the facts of one explanation (citations: slot -> fact_key -> evidence_ref).
    An identifier fact cites its own object (rule_id -> rule:<id>); every other fact cites the explanation's
    subject object, the first of unit, decision, change request, rule, batch, look-back, run, file, relation.
    Never part of facts_hash: the sources are emitted BESIDE the facts."""
    f = adapt(kind, raw)
    flat = flatten(f)
    own = {k: _ref(_REF_OF[k], v) for k, v in flat.items() if k in _REF_OF and v not in (None, "")}
    subj = next((own[k] for k in _SUBJECT_ORDER if own.get(k)), None)
    if kind == "rule_disposition" and own.get("rule_id") and f.get("relation"):
        subj = _ref("results", f"{f['rule_id']}@{f['relation']}") or subj
    out = {}
    for k in sorted(set(flat) | set(flatten(build(kind, raw)))):          # derived display facts (_pct) too
        if k in ("explanation_kind", "part2_withheld"):
            continue
        ref = own.get(k) or subj
        if ref:
            out[k] = ref
    return out
