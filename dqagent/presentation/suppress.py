"""The ONLY suppression site (DESIGN §8). Applied server-side by the api's read endpoints.

A count about an authority the viewer does not hold is suppressed when it is below the
threshold. Complementary suppression: if exactly one cell in a row group is suppressed,
the next-smallest cell is suppressed too, so the hidden value can't be derived from the
total. The gate and the engine always compute unsuppressed values.
"""
from __future__ import annotations

SUPPRESSED = None


def suppress_cells(cells: list[dict], viewer_scope: set[str], threshold: int = 11,
                   value_key: str = "value", authority_key: str = "authority") -> list[dict]:
    out = [dict(c) for c in cells]
    hidden = []
    for c in out:
        v = c.get(value_key)
        if c.get(authority_key) not in viewer_scope and v is not None and 0 < v < threshold:
            c[value_key] = SUPPRESSED
            c["suppressed"] = True
            hidden.append(c)
    if len(hidden) == 1:
        rest = sorted((c for c in out if not c.get("suppressed") and c.get(value_key) is not None
                       and c.get(authority_key) not in viewer_scope), key=lambda c: c[value_key])
        if rest:
            rest[0][value_key] = SUPPRESSED
            rest[0]["suppressed"] = True
            rest[0]["complementary"] = True
    return out
