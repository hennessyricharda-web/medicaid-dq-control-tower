"""Shared export helpers: allow-lists, small-cell suppression and stable sorting."""
from __future__ import annotations

import json

SMALL_CELL_MIN, SMALL_CELL_MAX = 1, 10


def suppress(n):
    """A count from 1 to 10 is suppressed (None). 0 and >= 11 are shown."""
    if n is None:
        return None
    n = int(n)
    return None if SMALL_CELL_MIN <= n <= SMALL_CELL_MAX else n


def allow(d: dict, fields: tuple[str, ...]) -> dict:
    return {k: d.get(k) for k in fields}


def stable_key(d: dict) -> str:
    return json.dumps(d, sort_keys=True, default=str)


def _get(r, k, default=None):
    if isinstance(r, dict):
        if k in r:
            return r[k]
        return (r.get("header") or {}).get(k, default)
    v = getattr(r, k, None)
    return v if v is not None else (getattr(r, "header", {}) or {}).get(k, default)
