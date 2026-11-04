"""Catalog endpoints (PFP P1-2 / P1-3, K1 + K2c):

  GET  /api/v1/catalog/pipelines/{id}/profile?as_of=  -> the pipeline's governance configuration snapshot (K1)
  GET  /api/v1/catalog/pipelines                      -> every pipeline (HARNESS or WIRED), grouped by provenance
  GET  /api/v1/catalog/pipelines/{id}/nodes            -> the pipeline's nodes drawer (Effective tier column)
  GET  /api/v1/catalog/pipelines/{id}/graph?overlay=   -> the pipeline's node/edge graph for React Flow (K4, PFP P2-4)
  POST /api/v1/catalog/discover                        -> preview a dbt manifest (no governance mutation)
  POST /api/v1/catalog/wire                             -> propose a new consumer PIPELINE + its TASK_GATE items
  POST /api/v1/catalog/pipelines/{id}/profile/draft      -> K8: propose an edit to a pipeline's PIPELINE content,
                                                             merged onto its current stored row (K7_K8_K9_DESIGN.md §2)

K2c-1 (RESOLVED, K2_DESIGN.md §9): these are new, distinct routes from the existing `GET /api/v1/pipelines`
(`app.py:725-727`, `runtime.py:647-666`) -- that endpoint, and the Tower's existing flat "Pipeline map" page, are
left untouched; this file adds a second, richer view, it does not replace the first.

D7 (CRITICAL, red-team-named scope enforcement, carried forward from K1): the pipeline's authority set is the
UNION of its gated relations' `authorities_by_program`. For `pipelines`/`profile` this means filtering the caller's
view (list routes omit what they may not see; the single-pipeline `profile`/`nodes` routes still 403, never a
redacted 200). A pipeline's "gated relations" are its TASK_GATE items' `datasets` (K2a/K2b), which --
confirmed by direct trace of `pipeline_task_gate_seed`, registry.py:147-189 -- are EXACTLY a HARNESS pipeline's
own `gated_nodes` for the 4 genesis block gates, so one definition (TASK_GATE datasets) now covers both HARNESS
and WIRED pipelines uniformly; no provenance-specific authority logic is needed.

The two-person wiring flow (`POST /catalog/wire`): builds well-formed PIPELINE + TASK_GATE items and calls
`services.registry.propose(...)` -- the SAME `gov_propose` every other governed change in this codebase uses.
This route does NOT decide or apply anything; the caller still drives the proposal through the EXISTING
`/api/v1/governance/change-requests/{cr_id}/challenge` and `/decide` routes (`app.py:1097-1128`), the same
two-person mechanism `cr_decide` already exercises. Nothing here is a new approval mechanism: PIPELINE and
TASK_GATE are never lower-only (K2a, `gov_item_lower_only`), so `gov_propose`'s own, unchanged
`approvals_required` computation (`governance_schema.py:149`) already stores 2 for every `/wire` proposal, and a
second approval from a DIFFERENT person is enforced the same way `test_change_request_flow_over_http` already
proves for every other kind (`gov_decide`'s author-cannot-approve-own-change check, `governance_schema.py:174-176`;
`gov_apply`'s approval-count check, `:223-230`).

Registered by the app (BC) as catalog_routes(app, services, principal, scope), the same pattern as
obs_routes(app, services, principal) / ops_routes(app, services, principal) (app.py:1342-1345).
"""
from __future__ import annotations

from fastapi import Body, Depends, FastAPI, HTTPException, Query

from dqagent.governance.profile import (
    _authorities_of,
    compose_profile,
    dataset_governance,
    pipeline_authorities,
)


def _gov_duty(p) -> None:
    """Same check as app.py's own `gov_duty` (governance:AUTH_RULES, token claim AND roster); duplicated here
    (not imported) because app.py's `gov_duty` is a route-local closure, not a module-level function -- the same
    boundary K1's `catalog_routes.py` already respects by not importing anything from `app.py` itself."""
    if "governance:AUTH_RULES" not in p["_duties"]:
        raise HTTPException(403, "governance duty required (token claim AND roster)")


def _pipeline_task_gates(registry, state: str, pipeline_id: str) -> list[dict]:
    """Every effective TASK_GATE artifact named `taskgate:<pipeline_id>:*` (the SAME query
    `runtime.py:_pipeline_run` already runs server-side, `runtime.py:1734-1737`, reused here read-only)."""
    with registry.cp.as_role("dq_registry_svc") as c:
        return c.execute(
            "SELECT artifact_id, version, content FROM dq_ctl.config_effective WHERE state=%s AND kind='TASK_GATE' "
            "AND op='PUT' AND artifact_id LIKE %s ORDER BY artifact_id",
            (state, f"{state}:taskgate:{pipeline_id}:%")).fetchall()


def _pipeline_datasets(gates: list[dict]) -> list[str]:
    out: set[str] = set()
    for g in gates:
        out.update(g["content"].get("datasets") or [])
    return sorted(out)


def _pipeline_artifact(registry, state: str, pipeline_id: str) -> dict | None:
    arts = registry.artifacts(state)
    a = arts.get(f"{state}:pipeline:{pipeline_id}")
    return a["content"] if a else None


def _harness_ids(harness: dict) -> list[str]:
    return [p["id"] for p in harness.get("pipelines") or []]


def _pipeline_domains(harness: dict, pipeline_id: str) -> list[str]:
    """This pipeline's own domains, HARNESS-declared only (`harness_w1.yaml`'s own field) -- a WIRED pipeline's
    `domains` (its stored PIPELINE content) names no entry in `harness["dependencies"]`, so it legitimately
    contributes no cross-pipeline edge; empty is the honest answer there, not a gap."""
    for p in harness.get("pipelines") or []:
        if p["id"] == pipeline_id:
            return list(p.get("domains") or [])
    return []


def _domain_owner(harness: dict) -> dict[str, str]:
    return {d: p["id"] for p in harness.get("pipelines") or [] for d in (p.get("domains") or [])}


def _pipeline_graph_edges(harness: dict, pipeline_id: str) -> list[dict]:
    """New backend logic (K4 Design entry's golden-path edge/dependency note): there is no existing server-side
    equivalent of `PipelineDag.tsx`'s client-side `buildEdges` (confirmed by direct read of that file), so this
    is new, not reused. Edges are the cross-DOMAIN dependency rows in `harness["dependencies"]` (DESIGN §4.3,
    `harness/plan.py`) that touch one of THIS pipeline's own domains -- `from`/`to` are domain names (the
    harness's own vocabulary, kept un-translated so a domain owned by this very pipeline, e.g. an
    EVALUATE_AGAINST_LAST_RELEASED edge between two of its own domains, still round-trips honestly), `policy` is
    the declared WAIT/EVALUATE_AGAINST_LAST_RELEASED policy, and `orderOk` is whether the GLOBAL pipeline order
    (`harness.plan.order`, the same function `catalog_pipelines` already calls) actually places the upstream
    pipeline at or before this one -- a schedule sanity check, not a re-statement of `plan.validate`'s own acyclic
    check. `orderOk` is `None` when the upstream domain names no pipeline this harness owns (an unresolvable
    edge is reported as unresolved, never defaulted to true)."""
    domains = set(_pipeline_domains(harness, pipeline_id))
    if not domains:
        return []
    owner = _domain_owner(harness)
    from dqagent.harness.plan import order as harness_order
    pos = {pid: i for i, pid in enumerate(harness_order(harness))}
    out = []
    for d in harness.get("dependencies") or []:
        if d["domain"] not in domains:
            continue
        upstream_owner = owner.get(d["depends_on"])
        this_owner = owner.get(d["domain"], pipeline_id)
        order_ok = (pos.get(upstream_owner, -1) <= pos.get(this_owner, -1)) if upstream_owner else None
        out.append({"from": d["depends_on"], "to": d["domain"], "policy": d["policy"], "orderOk": order_ok})
    return out


def _graph_nodes(registry, state: str, caller_scope: list, gates: list[dict], datasets: list[str],
                 manifest_nodes: list[dict]) -> list[dict]:
    """`catalog_pipeline_nodes`'s own two-row-kind precedent (`:184-219`), reshaped for a graph library's own
    node contract (`id`, not `node` -- React Flow's required key, PFP P2-4). Kind 1: one row per TASK_GATE-
    governed dataset, carrying its worst observed state (`_worst_node_state`, the SAME RLS-scoped reduction the
    nodes drawer already uses) and its gate. Kind 2: one row per discovered dbt-manifest node, "discovered, not
    evaluated" -- `state`/`gate` both `None`, never fabricated, carrying only the manifest's own `layer`/
    `layer_source` (never inferred from a folder name, `dbt_files.discover`'s own rule)."""
    states = _worst_node_state(registry, state, caller_scope, datasets)
    nodes = []
    for g in gates:
        for ds in (g["content"].get("datasets") or []):
            nodes.append({"id": ds, "state": states.get(ds, {"state": "EMPTY", "x_counts": {}})["state"],
                          "gate": {"id": g["artifact_id"], "mode": g["content"].get("mode")}})
    for n in manifest_nodes:
        nodes.append({"id": n["node"], "state": None, "gate": None,
                      "layer": n["layer"], "layer_source": n["layer_source"]})
    return nodes


GRAPH_OVERLAYS = {"gates"}     # fixed allow-list (K4 Design entry, before-build red team): any other value is a
                               # 400, never a silent passthrough -- no unvalidated filter-bypass surface.


def _worst_node_state(registry, state: str, scope, nodes: list[str]) -> dict[str, dict]:
    """Per-node worst unit state, scoped under the caller's own RLS (never as admin, SEC-W1-8) -- the same
    reduction `runtime.pipelines_view` already uses (`runtime.py:647-666`), reused here for WIRED pipelines too
    (which `pipelines_view` never covers, since it only iterates `self.harness["pipelines"]`)."""
    if not nodes:
        return {}
    with registry.cp.as_role("dq_api", scope) as c:
        rows = c.execute("SELECT node, state, count(*) n FROM dq_ctl.units WHERE node = ANY(%s) GROUP BY 1, 2",
                         (nodes,)).fetchall()
    by_node: dict = {}
    for r in rows:
        by_node.setdefault(r["node"], {})[r["state"]] = r["n"]
    out = {}
    for n in nodes:
        st = by_node.get(n, {})
        worst = ("HELD" if st.get("HELD") else "PENDING" if any(
            k in st for k in ("PENDING_DEPENDENCY", "PENDING_RELEASE", "EVALUATING", "SEALED")) else
            "RELEASED" if st else "EMPTY")
        out[n] = {"state": worst, "x_counts": st}
    return out


def _last_night(registry, state: str, pipeline_id: str) -> dict | None:
    with registry.cp.as_role("dq_auditor") as c:
        r = c.execute("SELECT trace_id, night_date, started_at FROM dq_ctl.runs WHERE state=%s AND kind='PIPELINE_RUN' "
                      "AND pipeline_id=%s ORDER BY started_at DESC LIMIT 1", (state, pipeline_id)).fetchone()
    return {"trace_id": r["trace_id"], "night_date": r["night_date"].isoformat() if r["night_date"] else None,
            "started_at": r["started_at"].isoformat()} if r else None


def catalog_routes(app: FastAPI, services, principal, scope) -> None:

    @app.get("/api/v1/catalog/pipelines/{pipeline_id}/profile")
    def pipeline_profile(pipeline_id: str, as_of: str | None = Query(default=None), p=Depends(principal)):
        caller_scope = set(scope(p))      # house pattern (app.py:321,387,430): any effective release duty, or 403
        try:
            pipeline_auths = set(pipeline_authorities(services.harness, services.contracts, pipeline_id))
        except KeyError:
            raise HTTPException(404, "pipeline not found") from None
        if not caller_scope & pipeline_auths:
            raise HTTPException(403, "no effective release duty (token claim AND roster) over this pipeline's "
                                     "authorities")
        try:
            return compose_profile(services.registry, services.state, pipeline_id, as_of)
        except KeyError as e:
            raise HTTPException(404, f"{e.args[0] if e.args else pipeline_id} not found") from None

    # -- K2c: Catalog table, nodes drawer, discover preview, wire proposal --------------------------------------
    def _pipeline_row(pipeline_id: str, provenance: str, caller_scope: set, platform: str | None,
                      domains: list[str]) -> dict | None:
        gates = _pipeline_task_gates(services.registry, services.state, pipeline_id)
        nodes = _pipeline_datasets(gates)
        auths = set(_authorities_of(services.contracts, nodes))
        if not (caller_scope & auths):            # precision, not recall: omit what the caller may not see
            return None
        states = _worst_node_state(services.registry, services.state, list(caller_scope), nodes)
        return {"id": pipeline_id, "provenance": provenance, "platform": platform, "domains": domains,
                "source": _pipeline_artifact(services.registry, services.state, pipeline_id),
                "nodes": [{"node": n, **states.get(n, {"state": "EMPTY", "x_counts": {}})} for n in nodes],
                "task_gates": [{"id": g["artifact_id"], "task_id": g["content"].get("task_id"),
                                "datasets": g["content"].get("datasets"), "mode": g["content"].get("mode"),
                                "release_ceiling": g["content"].get("release_ceiling")} for g in gates],
                "governance": {"authorities": sorted(auths)},
                "last_night": _last_night(services.registry, services.state, pipeline_id)}

    @app.get("/api/v1/catalog/pipelines")
    def catalog_pipelines(p=Depends(principal)):
        """PFP :834: `{groups:[{provenance, pipelines[]}], order[]}`. Lists every pipeline the caller may see
        (filtered, never redacted -- D7 extended to a list: an item the caller has no authority over is omitted,
        not shown partially)."""
        caller_scope = set(scope(p))
        from dqagent.harness.plan import order as harness_order
        harness_ids = set(_harness_ids(services.harness))
        groups: dict[str, list] = {"HARNESS": [], "WIRED": []}
        for pl in services.harness.get("pipelines") or []:
            row = _pipeline_row(pl["id"], "HARNESS", caller_scope, pl.get("platform"), pl.get("domains") or [])
            if row is not None:
                groups["HARNESS"].append(row)
        with services.cp.as_role("dq_registry_svc") as c:
            wired = c.execute("SELECT artifact_id, content FROM dq_ctl.config_effective WHERE state=%s "
                              "AND kind='PIPELINE' AND op='PUT' ORDER BY artifact_id", (services.state,)).fetchall()
        for w in wired:
            pid = w["artifact_id"].split(":pipeline:", 1)[1]
            if pid in harness_ids:
                continue
            row = _pipeline_row(pid, "WIRED", caller_scope, w["content"].get("platform"),
                                w["content"].get("domains") or [])
            if row is not None:
                groups["WIRED"].append(row)
        return {"groups": [{"provenance": k, "pipelines": v} for k, v in groups.items() if v],
                "order": harness_order(services.harness)}

    @app.get("/api/v1/catalog/pipelines/{pipeline_id}/nodes")
    def catalog_pipeline_nodes(pipeline_id: str, p=Depends(principal)):
        """PFP :835: `{nodes:[{node,layer,layer_source,evaluation,gate:{id,mode},datasets:[...]}]}`.
        `effective_tier` on each dataset comes from `autonomy_tier_for` (via `dataset_governance`), K2a/K2b's
        ceiling-aware tier -- the "nodes drawer with the Effective tier column" (EXECUTION_PLAN.md K2c).

        Build decision, reported not silently assumed: this codebase's governed `datasets` (sealed bronze
        relations, ORCH-1's unit of ceiling) and a dbt manifest's MODEL nodes are two different objects with no
        derivable per-node correspondence in the data model -- confirmed directly, `dbt_runner.py`'s own comment
        that `block` gates "name no dbt model at all in the w1 project; bronze is sealed directly, never
        dbt-built." This route therefore returns two kinds of row, each honestly labelled, rather than inventing a
        join: one row per TASK_GATE-governed dataset (carries `gate` and `datasets`, the Effective tier column),
        and one row per discovered dbt manifest node when this pipeline has one (carries `layer`/`layer_source`
        only, `gate` and `datasets` both empty -- a manifest node's layer is real and worth showing; a fabricated
        link to a governed dataset would not be)."""
        caller_scope = set(scope(p))
        harness_ids = set(_harness_ids(services.harness))
        if pipeline_id not in harness_ids and _pipeline_artifact(services.registry, services.state, pipeline_id) is None:
            raise HTTPException(404, "pipeline not found")
        gates = _pipeline_task_gates(services.registry, services.state, pipeline_id)
        datasets = _pipeline_datasets(gates)
        auths = set(_authorities_of(services.contracts, datasets))
        if not (caller_scope & auths):
            raise HTTPException(403, "no effective release duty (token claim AND roster) over this pipeline's "
                                     "authorities")
        states = _worst_node_state(services.registry, services.state, list(caller_scope), datasets)
        rows = []
        for g in gates:
            for ds in (g["content"].get("datasets") or []):
                gov = dataset_governance(services.registry, services.state, ds,
                                         sorted(_authorities_of(services.contracts, [ds])))
                rows.append({"node": ds, "layer": "GATED", "layer_source": "TASK_GATE",
                            "evaluation": states.get(ds, {"state": "EMPTY", "x_counts": {}}),
                            "gate": {"id": g["artifact_id"], "mode": g["content"].get("mode")},
                            "datasets": [{"dataset": ds, **gov}]})
        pipe = _pipeline_artifact(services.registry, services.state, pipeline_id)
        manifest_path = (pipe or {}).get("scope", {}).get("manifest_path")
        if manifest_path:
            from dqagent.adapters.dag.dbt_files import DiscoveryError, discover
            try:
                disc = discover(manifest_path, layer_map=(pipe or {}).get("layer_map") or {})
                for n in disc["nodes"]:
                    rows.append({"node": n["node"], "layer": n["layer"], "layer_source": n["layer_source"],
                                "evaluation": None, "gate": None, "datasets": []})
            except DiscoveryError:
                pass    # the manifest is not yet built in this environment; the GATED rows above still stand
        return {"nodes": rows}

    @app.get("/api/v1/catalog/pipelines/{pipeline_id}/graph")
    def catalog_pipeline_graph(pipeline_id: str, overlay: str = Query(default="gates"), p=Depends(principal)):
        """K4, PFP P2-4 (:993-1008): `{nodes:[{id,state,gate:{id,mode}} | {id,layer,layer_source,state:null,
        gate:null}], edges:[{from,to,policy,orderOk}]}` -- the golden-path example on the blackboard (K4 Design
        entry, matched exactly). `overlay` is checked against the fixed allow-list FIRST, before anything else
        runs (today only "gates" is valid); any other value is a 400.

        D7 (CRITICAL, before-build red team round 1 finding 1, fixed per that finding): this route carries its
        OWN explicit authority check, copied verbatim from `catalog_pipeline_nodes:195-198` below -- confirmed by
        direct trace that `_worst_node_state`'s `as_role("dq_api", scope)` is layer-two suppression of state/count
        VALUES inside an ALREADY-authorized response (SEC-W1-8), never the 403/omit decision itself. Without this
        route's own check, a caller with zero authority over this pipeline would get a full 200 of real node,
        gate and dataset identifiers -- the single-resource "403, never a redacted 200" rule D7 states for
        `profile`/`nodes` applies here too, confirmed by `test_catalog_graph_outsider_403_not_redacted_200`
        below."""
        if overlay not in GRAPH_OVERLAYS:
            raise HTTPException(400, f"overlay must be one of {sorted(GRAPH_OVERLAYS)}; got {overlay!r}")
        caller_scope = set(scope(p))
        harness_ids = set(_harness_ids(services.harness))
        pipe = _pipeline_artifact(services.registry, services.state, pipeline_id)
        if pipeline_id not in harness_ids and pipe is None:
            raise HTTPException(404, "pipeline not found")
        gates = _pipeline_task_gates(services.registry, services.state, pipeline_id)
        datasets = _pipeline_datasets(gates)
        auths = set(_authorities_of(services.contracts, datasets))
        if not (caller_scope & auths):
            raise HTTPException(403, "no effective release duty (token claim AND roster) over this pipeline's "
                                     "authorities")
        manifest_nodes: list[dict] = []
        manifest_path = (pipe or {}).get("scope", {}).get("manifest_path")
        if manifest_path:
            from dqagent.adapters.dag.dbt_files import DiscoveryError, discover
            try:
                disc = discover(manifest_path, layer_map=(pipe or {}).get("layer_map") or {})
                manifest_nodes = disc["nodes"]
            except DiscoveryError:
                pass    # same stance as catalog_pipeline_nodes: the manifest is not yet built here; gated rows stand
        nodes = _graph_nodes(services.registry, services.state, list(caller_scope), gates, datasets, manifest_nodes)
        edges = _pipeline_graph_edges(services.harness, pipeline_id)
        return {"nodes": nodes, "edges": edges}

    @app.post("/api/v1/catalog/discover")
    def catalog_discover(body: dict = Body(...), p=Depends(principal)):
        """PFP :836: `{adapter:'dbt_files', manifest_path}` -> the preview; nothing is written. Path confinement to
        `pipelines/` is enforced INSIDE `discover()` itself (`dqagent/adapters/dag/dbt_files.py:42-47`, confirmed
        by direct read: `p.resolve().relative_to(PIPELINES_ROOT)` raises before the file is ever opened) -- this
        route adds no second confinement check, it only translates that failure into an HTTP response."""
        _gov_duty(p)
        manifest_path = body.get("manifest_path")
        if not manifest_path or not isinstance(manifest_path, str):
            raise HTTPException(422, {"status": "REFUSED", "reason": "manifest_path (string) is required"})
        from dqagent.adapters.dag.dbt_files import DiscoveryError, discover
        try:
            return discover(manifest_path, layer_map=body.get("layer_map") or {})
        except DiscoveryError as e:
            raise HTTPException(422, {"status": "REFUSED", "reason": str(e)}) from None

    @app.post("/api/v1/catalog/wire")
    def catalog_wire(body: dict = Body(...), p=Depends(principal)):
        """PFP :837: `{pipeline, task_gates[], rationale}` -> `{cr_id, approvals_required, impact}`. Builds a
        PIPELINE item and one TASK_GATE item per `task_gates[]` entry, then calls `services.registry.propose(...)`
        -- the SAME `gov_propose` every other change request in this codebase goes through. This route only
        PROPOSES: decide/apply happens through the EXISTING `/governance/change-requests/{cr_id}/challenge` and
        `/decide` routes (app.py:1097-1128), never a new mechanism. PIPELINE/TASK_GATE are never lower-only
        (K2a), so `approvals_required` is always 2 for a `/wire` proposal -- the database's own decision, not a
        constant this route hard-codes (reported here as what the database stores, in case a future kind change
        ever disagrees)."""
        _gov_duty(p)
        state = services.state
        rationale = (body.get("rationale") or "").strip()
        if len(rationale) < 10:
            raise HTTPException(422, {"status": "REFUSED", "reason": "rationale of at least 10 characters required"})
        pipe = body.get("pipeline") or {}
        pid = pipe.get("id")
        gates = body.get("task_gates") or []
        if not pid or not isinstance(gates, list) or not gates:
            raise HTTPException(422, {"status": "REFUSED",
                                      "reason": "pipeline.id and a non-empty task_gates[] are required"})
        manifest_path = (pipe.get("scope") or {}).get("manifest_path")
        if not manifest_path:
            raise HTTPException(422, {"status": "REFUSED", "reason": "pipeline.scope.manifest_path is required"})
        pipeline_item = {"artifact_id": f"{state}:pipeline:{pid}", "kind": "PIPELINE", "op": "PUT", "content": {
            "adapter": pipe.get("adapter") or "dbt_files", "scope": {"manifest_path": manifest_path},
            "platform": pipe.get("platform") or "duckdb", "domains": pipe.get("domains") or [],
            "layer_map": pipe.get("layer_map") or {}, "run_on": pipe.get("run_on") or "manual"}}
        items = [pipeline_item]
        requested_datasets: set[str] = set()
        for g in gates:
            task_id, datasets = g.get("task_id"), g.get("datasets") or []
            if not task_id or not datasets:
                raise HTTPException(422, {"status": "REFUSED",
                                          "reason": "each task_gates[] entry needs task_id and a non-empty datasets[]"})
            mode = g.get("mode") or "advisory"
            requested_datasets.update(datasets)
            items.append({"artifact_id": f"{state}:taskgate:{pid}:{task_id}", "kind": "TASK_GATE", "op": "PUT",
                         "content": {"task_id": task_id, "datasets": datasets, "mode": mode,
                                     "correlate": "LATEST_SEALED", "max_wait": g.get("max_wait") or "PT2H",
                                     "on_timeout": g.get("on_timeout") or
                                                   ("PASS_ADVISORY" if mode == "advisory" else "HOLD"),
                                     "release_ceiling": g.get("release_ceiling") or "L1",
                                     "reintroduce": g.get("reintroduce") or []}})
        # impact preview (read-only, before propose): which requested datasets are already gated by a STILL
        # EFFECTIVE introducer (ORCH-1's "reopens nothing" case, PFP :815) vs. ones this wiring would introduce.
        with services.cp.as_role("dq_registry_svc") as c:
            already_gated, introduced = [], []
            tiers_before, tiers_after = {}, {}
            for ds in sorted(requested_datasets):
                row = c.execute("SELECT introduced_by FROM dq_ctl.gated_dataset_introductions WHERE state=%s "
                                "AND dataset=%s ORDER BY version DESC LIMIT 1", (state, ds)).fetchone()
                effective = row and c.execute("SELECT 1 FROM dq_ctl.config_effective WHERE artifact_id=%s AND op='PUT'",
                                              (row["introduced_by"],)).fetchone()
                auths = sorted(_authorities_of(services.contracts, [ds]))
                before = dataset_governance(services.registry, state, ds, auths)
                tiers_before[ds] = before["effective_tier"]
                if effective:
                    already_gated.append({"dataset": ds, "introduced_by": row["introduced_by"]})
                    tiers_after[ds] = before["effective_tier"]     # unchanged: an already-gated consumer reopens nothing
                else:
                    introduced.append(ds)
                    ceiling = next((g.get("release_ceiling") or "L1" for g in gates if ds in (g.get("datasets") or [])),
                                   "L1")
                    from dqagent.governance.registry import TIERS as _TIERS
                    rank = {t: i + 1 for i, t in enumerate(_TIERS)}
                    tiers_after[ds] = min([before["agent_tier"], ceiling], key=lambda t: rank.get(t, 1))
        from dqagent.rules.ir import IRError
        import psycopg
        try:
            r = services.registry.propose(state, p["emp_id"], rationale, items)
        except IRError as e:
            raise HTTPException(422, {"status": "REFUSED", "reason": str(e)}) from e
        except (psycopg.errors.CheckViolation, psycopg.errors.InsufficientPrivilege) as e:   # Part E item checks (C4)
            raise HTTPException(422, {"status": "REFUSED", "reason": str(e).splitlines()[0]}) from e
        ceilings = sorted({g.get("release_ceiling") or "L1" for g in gates if any(
            ds in introduced for ds in (g.get("datasets") or []))})
        return {"cr_id": r["cr_id"], "approvals_required": r["approvals_required"], "impact": r["impact"] | {
            "gates_added": [g["content"]["task_id"] for g in items if g["kind"] == "TASK_GATE"],
            "datasets_introduced": introduced, "datasets_already_gated": already_gated,
            "ceiling": {"value": ceilings[0] if len(ceilings) == 1 else (ceilings or None), "applies_to": introduced},
            "mode": sorted({g.get("mode") or "advisory" for g in gates}),
            "validation_errors": [], "effective_tier_before": tiers_before, "effective_tier_after": tiers_after}}

    @app.post("/api/v1/catalog/pipelines/{pipeline_id}/profile/draft")
    def catalog_profile_draft(pipeline_id: str, body: dict = Body(...), p=Depends(principal)):
        """K8 (K7_K8_K9_DESIGN.md §2): `{platform?, domains?, run_on?, rationale}` -> `{cr_id, approvals_required}`.
        Same body/response shape as the mock (`wire.ts`'s `mockProfileDraft`), but NOT the same construction: the
        mock hardcodes `layer_map: {}` and derives `manifest_path` from a naming formula, which would silently
        corrupt a real pipeline's stored values. This route instead reads the pipeline's CURRENT
        `config_effective` row (`{state}:pipeline:{pipeline_id}`, op='PUT') and merges only the caller's supplied
        overrides onto it -- `adapter`, `scope.manifest_path`, and `layer_map` are always carried from the current
        row unchanged (before-build red team CRITICAL finding #2, fixed in the design, §2.1 point 1). A pipeline
        that has never been wired (no current PUT row) is a 404, never a fabricated default.

        Builds exactly one PIPELINE-kind PUT item and calls `services.registry.propose(...)`, the SAME
        `gov_propose` mechanism `/catalog/wire` and `/catalog/.../promote` already use. PIPELINE is never
        lower-only (`governance_schema.py:1002`), so `approvals_required` is always 2 -- the database's own
        decision, not a route-side constant."""
        _gov_duty(p)
        state = services.state
        rationale = (body.get("rationale") or "").strip()
        if len(rationale) < 10:
            raise HTTPException(422, {"status": "REFUSED", "reason": "rationale of at least 10 characters required"})
        current = _pipeline_artifact(services.registry, state, pipeline_id)
        if current is None:
            raise HTTPException(404, {"status": "REFUSED", "reason": f"unknown pipeline {pipeline_id!r}"})
        content = dict(current)
        if isinstance(body.get("platform"), str) and body["platform"].strip():
            content["platform"] = body["platform"].strip()
        if isinstance(body.get("domains"), list):
            content["domains"] = [d for d in body["domains"] if isinstance(d, str)]
        if isinstance(body.get("run_on"), str) and body["run_on"].strip():
            content["run_on"] = body["run_on"].strip()
        item = {"artifact_id": f"{state}:pipeline:{pipeline_id}", "kind": "PIPELINE", "op": "PUT", "content": content}
        from dqagent.rules.ir import IRError
        import psycopg
        try:
            r = services.registry.propose(state, p["emp_id"], rationale, [item])
        except IRError as e:
            raise HTTPException(422, {"status": "REFUSED", "reason": str(e)}) from e
        except (psycopg.errors.CheckViolation, psycopg.errors.InsufficientPrivilege) as e:   # Part E item checks (C4)
            raise HTTPException(422, {"status": "REFUSED", "reason": str(e).splitlines()[0]}) from e
        return {"cr_id": r["cr_id"], "approvals_required": r["approvals_required"]}

    @app.post("/api/v1/catalog/pipelines/{pipeline_id}/task-gates/{task_id}/promote")
    def catalog_promote_gate(pipeline_id: str, task_id: str, body: dict = Body(...), p=Depends(principal)):
        """BACKLOG F-11b fix (K3 PFP P1-4 step (a)'s own demo moment, Tower's promote page). `{rationale}` ->
        `{cr_id, approvals_required}`. This route exists because a TASK_GATE PUT item is a FULL-content replace
        (`registry._candidate`), not a patch -- `gov_item_valid` (`governance_schema.py:832-847`) requires every
        key (`correlate`/`max_wait`/`on_timeout`/`reintroduce` included), and the Catalog row the browser holds
        (`CatalogTaskGate`) only ever carried `id`/`task_id`/`datasets`/`mode`/`release_ceiling` -- a browser
        PUT built from that row alone is deterministically malformed. Fixed the SAME way `/catalog/wire` above
        already avoids making the caller supply a complete object: this route reads the gate's CURRENT full
        content server-side (`_pipeline_task_gates`, the same query `/catalog/pipelines` itself uses) and
        changes only `mode` -- the caller sends nothing but a rationale. This is more robust than making the
        client responsible for completeness (K3_DESIGN.md's own option (b)): a future field added to TASK_GATE
        needs no matching change here, since the full current content is carried through untouched.

        Never a new approval mechanism: builds exactly one TASK_GATE PUT item and calls
        `services.registry.propose(...)`, the SAME `gov_propose` every other governed change in this codebase
        goes through (identical to `/catalog/wire` above). Decide/apply still happens through the EXISTING
        `/governance/change-requests/{cr_id}/challenge` + `/decide` routes."""
        _gov_duty(p)
        state = services.state
        rationale = (body.get("rationale") or "").strip()
        if len(rationale) < 10:
            raise HTTPException(422, {"status": "REFUSED", "reason": "rationale of at least 10 characters required"})
        gates = {g["content"].get("task_id"): g for g in _pipeline_task_gates(services.registry, state, pipeline_id)}
        gate = gates.get(task_id)
        if gate is None:
            raise HTTPException(404, {"status": "REFUSED",
                                      "reason": f"no TASK_GATE {task_id!r} on pipeline {pipeline_id!r}"})
        content = dict(gate["content"])
        if content.get("mode") != "advisory":
            raise HTTPException(422, {"status": "REFUSED", "reason": f"gate is {content.get('mode')!r}, not "
                                      "advisory; only an advisory gate can be promoted to block here"})
        content["mode"] = "block"
        item = {"artifact_id": gate["artifact_id"], "kind": "TASK_GATE", "op": "PUT", "content": content}
        from dqagent.rules.ir import IRError
        import psycopg
        try:
            r = services.registry.propose(state, p["emp_id"], rationale, [item])
        except IRError as e:
            raise HTTPException(422, {"status": "REFUSED", "reason": str(e)}) from e
        except (psycopg.errors.CheckViolation, psycopg.errors.InsufficientPrivilege) as e:   # Part E item checks (C4)
            raise HTTPException(422, {"status": "REFUSED", "reason": str(e).splitlines()[0]}) from e
        return {"cr_id": r["cr_id"], "approvals_required": r["approvals_required"]}
