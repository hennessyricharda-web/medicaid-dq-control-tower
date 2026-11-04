"""Lineage export (Part E §8 B3): GET /api/v1/export/lineage. OpenLineage-style dataset / job facets plus a typed
edge list, the input a future knowledge graph would ingest. Vendor-neutral; no data values.

Document:
  {producer, schemaURL, state, datasets[{namespace, name, facets{schema{fields[{name}]}, dataQuality{rules}}}],
   jobs[{namespace, name, facets{pipeline{domains, gated_nodes, orchestrator}}}],
   edges[{type, src, dst, props}]}
Typed refs: dataset:<relation>, element:<EL>, rule:<id>@<entity>, unit:<key>, job:<pipeline>, domain:<D>,
            person:<id>, release:<id>, cr:<id>
Edge types (EDGE_TYPES):
  binds        rule -> element          the rule reads the element
  depends_on   domain -> domain         harness dependency (props: policy)
  checks       rule -> dataset          the rule runs on the relation
  result_of    unit -> rule             latest result (props: disposition)
  decided_by   unit -> person           a human decision (props: verb, decision_id)
  released_in  unit -> release          the unit was published in that release / manifest version
Part 2 units are excluded from unit-level edges. Sorted and deterministic.
"""
from __future__ import annotations

from dqagent.adapters.metadata._common import _get, stable_key

PRODUCER = "dqagent/export-lineage/1"
SCHEMA_URL = "https://openlineage.io/spec/2-0-2/OpenLineage.json"
EDGE_TYPES = ("binds", "depends_on", "checks", "result_of", "decided_by", "released_in")
EDGE_PROPS = {"depends_on": ("policy",), "result_of": ("disposition",), "decided_by": ("verb", "decision_id"),
              "binds": (), "checks": (), "released_in": ()}


def _edge(t, src, dst, **props):
    return {"type": t, "src": src, "dst": dst, "props": {k: props.get(k) for k in EDGE_PROPS[t] if k in props}}


def build_lineage(*, state: str, harness: dict, rules: list, bindings: dict[str, list[str]],
                  relation_columns: dict[str, list[str]] | None = None, units: list[dict] = (),
                  results: list[dict] = (), decisions: list[dict] = (), releases: list[dict] = ()) -> dict:
    """harness           config/harness_*.yaml content {pipelines[], dependencies[]}
       rules             compiled rules (ACTIVE ones are exported; others carry no edges)
       relation_columns  {relation: [column names]} for the schema facet (names only)
       units             {unit_key, node, part2?}; results {unit_key, rule_id, eval_version, disposition}
       decisions         {decision_id, unit_key, person_id, verb}; releases {unit_key, release_id}"""
    ns = f"dq:{state}"
    relation_columns = relation_columns or {}
    part2 = {u["unit_key"] for u in units if u.get("part2")}
    node_of = {u["unit_key"]: u.get("node") for u in units}
    active = [r for r in rules if _get(r, "status") == "ACTIVE" and _get(r, "relation")]
    edges = []
    rel_rules: dict[str, list[str]] = {}
    for r in active:
        rref = f"rule:{_get(r, 'id')}@{_get(r, 'entity')}"
        rel = _get(r, "relation")
        rel_rules.setdefault(rel, []).append(rref)
        edges.append(_edge("checks", rref, f"dataset:{rel}"))
        for el in bindings.get(str(_get(r, "id")), []):
            edges.append(_edge("binds", rref, f"element:{el}"))
    for d in harness.get("dependencies", []) or []:
        edges.append(_edge("depends_on", f"domain:{d['domain']}", f"domain:{d['depends_on']}", policy=d.get("policy")))
    rule_ent = {(str(_get(r, "id")), _get(r, "relation")): _get(r, "entity") for r in active}
    latest: dict = {}
    for r in results:
        k = (r["unit_key"], r["rule_id"])
        if k not in latest or (r.get("eval_version") or 0) >= (latest[k].get("eval_version") or 0):
            latest[k] = r
    for (uk, rid), r in latest.items():
        ent = rule_ent.get((rid, node_of.get(uk)))
        if uk in part2 or ent is None:
            continue
        edges.append(_edge("result_of", f"unit:{uk}", f"rule:{rid}@{ent}", disposition=r.get("disposition")))
    for d in decisions:
        if d.get("unit_key") in part2 or not d.get("unit_key"):
            continue
        edges.append(_edge("decided_by", f"unit:{d['unit_key']}", f"person:{d['person_id']}", verb=d.get("verb"),
                           decision_id=d.get("decision_id")))
    for rel in releases:
        if rel["unit_key"] in part2:
            continue
        edges.append(_edge("released_in", f"unit:{rel['unit_key']}", f"release:{rel['release_id']}"))
    datasets = []
    gated = sorted({n for p in harness.get("pipelines", []) or [] for n in p.get("gated_nodes", [])} |
                   set(rel_rules))
    for rel in gated:
        datasets.append({"namespace": ns, "name": rel,
                         "facets": {"schema": {"fields": [{"name": c} for c in sorted(relation_columns.get(rel, []))]},
                                    "dataQuality": {"rules": sorted(rel_rules.get(rel, []))}}})
    jobs = [{"namespace": ns, "name": p["id"],
             "facets": {"pipeline": {"domains": sorted(p.get("domains", [])),
                                     "gated_nodes": sorted(p.get("gated_nodes", [])),
                                     "orchestrator": p.get("orchestrator"), "platform": p.get("platform")}},
             "outputs": [{"namespace": ns, "name": n} for n in sorted(p.get("gated_nodes", []))]}
            for p in sorted(harness.get("pipelines", []) or [], key=lambda p: p["id"])]
    uniq = {stable_key(e): e for e in edges}
    return {"producer": PRODUCER, "schemaURL": SCHEMA_URL, "state": state, "datasets": datasets, "jobs": jobs,
            "edges": [uniq[k] for k in sorted(uniq, key=lambda k: (uniq[k]["type"], uniq[k]["src"],
                                                                     uniq[k]["dst"], k))]}
