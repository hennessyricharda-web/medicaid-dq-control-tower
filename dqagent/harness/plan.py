"""Harness: validate a set of DAG pipelines and compute the global processing order (DESIGN §4.3)."""
from __future__ import annotations

ALLOWED_TRIGGER_RULES = {"all_success"}
POLICIES = {"WAIT", "EVALUATE_AGAINST_LAST_RELEASED"}
BINDINGS = {"LATEST_RELEASED", "AS_OF_CUTOFF", "PERIOD"}


def _domain_owner(h):
    return {d: p["id"] for p in h["pipelines"] for d in p["domains"]}


def validate(h: dict, dag_nodes: dict[str, set[str]] | None = None,
             trigger_rules: dict[str, dict[str, str]] | None = None) -> list[str]:
    """Return a list of problems; an empty list means valid.

    Checks: unique ids; dependencies name known domains; legal policy and binding;
    xref-mapped keys forced to EALR; the WAIT graph is acyclic after declared breaks; gated
    nodes exist in the DAG source (when given); downstream trigger rules are all_success."""
    errs = []
    ids = [p["id"] for p in h["pipelines"]]
    if len(ids) != len(set(ids)):
        errs.append("duplicate pipeline ids")
    owner = _domain_owner(h)
    for d in h.get("dependencies", []):
        for k in ("domain", "depends_on"):
            if d[k] not in owner:
                errs.append(f"dependency names unknown domain {d[k]}")
        if d["policy"] not in POLICIES:
            errs.append(f"illegal policy {d['policy']}")
        tb = d.get("temporal_binding", {}).get("type", "LATEST_RELEASED")
        if tb not in BINDINGS:
            errs.append(f"illegal temporal binding {tb}")
        if any("xref" in str(k).lower() for k in d.get("key_map", [])) and d["policy"] == "WAIT":
            errs.append(f"{d['domain']}->{d['depends_on']}: xref-resolved key_map must be EVALUATE_AGAINST_LAST_RELEASED")
    if _cycle(h):
        errs.append("WAIT dependency graph has a cycle; break it with an EVALUATE_AGAINST_LAST_RELEASED edge")
    if dag_nodes is not None:
        for p in h["pipelines"]:
            missing = set(p["gated_nodes"]) - dag_nodes.get(p["id"], set())
            if missing:
                errs.append(f"{p['id']}: gated nodes not in DAG source: {sorted(missing)}")
    for pid, rules in (trigger_rules or {}).items():
        for task, rule in rules.items():
            if rule not in ALLOWED_TRIGGER_RULES:
                errs.append(f"{pid}.{task}: trigger_rule {rule} can run downstream of a HOLD (only all_success allowed)")
    return errs


def _wait_edges(h):
    return [(d["domain"], d["depends_on"]) for d in h.get("dependencies", []) if d["policy"] == "WAIT"]


def _cycle(h) -> bool:
    g = {}
    for a, b in _wait_edges(h):
        g.setdefault(a, set()).add(b)
    seen, stack = set(), set()

    def visit(n):
        if n in stack:
            return True
        if n in seen:
            return False
        seen.add(n)
        stack.add(n)
        if any(visit(m) for m in g.get(n, ())):
            return True
        stack.discard(n)
        return False
    return any(visit(n) for n in list(g))


def order(h: dict) -> list[str]:
    """Global pipeline order: a topological sort over all dependency edges (WAIT and EALR) at
    pipeline level. Ties break by declaration order. An EALR edge orders the pipelines but
    never blocks."""
    owner = _domain_owner(h)
    deps = {p["id"]: set() for p in h["pipelines"]}
    for d in h.get("dependencies", []):
        a, b = owner[d["domain"]], owner[d["depends_on"]]
        if a != b:
            deps[a].add(b)
    out, placed = [], set()
    decl = [p["id"] for p in h["pipelines"]]
    while len(out) < len(decl):
        ready = [p for p in decl if p not in placed and deps[p] <= placed]
        if not ready:                     # only an EALR-closed loop remains; fall back to declaration order
            ready = [p for p in decl if p not in placed][:1]
        out.append(ready[0])
        placed.add(ready[0])
    return out
