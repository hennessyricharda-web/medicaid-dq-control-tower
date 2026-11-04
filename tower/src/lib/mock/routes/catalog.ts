/**
 * Mock of dqagent/api/catalog_routes.py's K2c routes (PFP P1-3, EXECUTION_PLAN.md wave K2c): the Catalog table
 * (`GET /catalog/pipelines`) and the nodes drawer (`GET /catalog/pipelines/{id}/nodes`). Derived LIVE from the
 * mock's own state at request time (routes/obs.ts's house pattern), never a static recorded transcript (K2c-2,
 * the SAME deferral K1 made for routes/profile.ts: tools/record_fixtures.py does not exist yet).
 *
 * Shared with routes/wire.ts (one-directional: wire.ts imports from here, this file never imports wire.ts), since
 * both the Catalog table and the wiring wizard's impact preview need the SAME "which gate introduced this
 * dataset, and at what tier" reasoning -- one definition, not two.
 *
 * Mock design decision, reported here (not silently assumed): the real backend's harness_w1.yaml has 2 HARNESS
 * pipelines; this tab's PIPELINES fixture (K0/Part E) has 5, richer than real W1 by design (profile.ts's own
 * CONTRACTS comment already extends the same fixture past the real 2 relations). This mock extends the SAME
 * fixture one step further: every gated_node of every mock HARNESS pipeline is treated as governed by its own
 * synthesized `block` TASK_GATE (`taskgate:<pipeline_id>:<relation_short_name>`, the real seed's own naming
 * convention, `registry.py:165-180`), release_ceiling INHERIT (the real genesis block gates' own value). No mock
 * HARNESS pipeline carries a manifest_path (none of these 5 synthetic ids correspond to a real dbt project), so
 * their nodes drawer shows TASK_GATE rows only, never a discovered-only row -- honest, not a gap: K2b's two real
 * dbt projects (`pipelines/w1_dbt`, `pipelines/tmsis_outbound`) are reachable instead through the WIRE wizard's
 * discover preview and, once wired (two-person applied), through a WIRED pipeline's own nodes drawer.
 */
import type { AutonomyRow, Me, Pipelines } from "../../types";
import type { CatalogNode, CatalogPipeline, DiscoveredNode, GraphEdge, GraphNode } from "../../types/catalog";
import { MOCK_STATE, fx } from "../fixtures";
import { pipelineAuthorities } from "./profile";

type R = { status: number; json: unknown };
const ok = (json: unknown): R => ({ status: 200, json });
const err = (status: number, detail: unknown): R => ({ status, json: { detail } });

/** One TASK_GATE, in-HARNESS or wired, in the one shape both routes/catalog.ts and routes/wire.ts share. */
export interface GateRec {
  id: string;
  pipelineId: string;
  taskId: string;
  datasets: string[];
  mode: string;
  releaseCeiling: string;
  maxWait: string;
  onTimeout: string;
  reintroduce: string[];
}

/** One WIRED pipeline, recorded only once its proposing change request has BOTH approvals (never at propose
 *  time) -- matching the real route's own source, `dq_ctl.config_effective` (winning, APPLIED artifacts only). */
export interface WiredPipelineRec {
  id: string;
  adapter: string;
  manifestPath: string | null;
  platform: string;
  domains: string[];
  layerMap: Record<string, string>;
  runOn: string;
}

export interface CatalogCtx {
  me: Me;
  pipelines: Pipelines;
  autonomy: AutonomyRow[];
  wiredPipelines: WiredPipelineRec[];
  wiredGates: GateRec[];
}

/** The 4(+)-block-gate genesis seed, mirrored for this tab's own (richer) HARNESS fixture: one `block` gate per
 *  gated_node, named `taskgate:<pipeline_id>:<relation_short_name>` (registry.py's own convention). */
export function harnessGates(pipelines: Pipelines): GateRec[] {
  const out: GateRec[] = [];
  for (const p of pipelines.pipelines) {
    for (const g of p.gated_nodes) {
      const short = g.node.split(".").pop() ?? g.node;
      out.push({
        id: `${MOCK_STATE}:taskgate:${p.id}:${short}`,
        pipelineId: p.id,
        taskId: short,
        datasets: [g.node],
        mode: "block",
        releaseCeiling: "INHERIT",
        maxWait: "PT2H",
        onTimeout: "HOLD",
        reintroduce: [],
      });
    }
  }
  return out;
}

/** Exported for routes/wire.ts's `mockPromoteGate` (F-11b fix): the one place that needs a gate's full content
 *  (mode/maxWait/onTimeout/reintroduce included) by pipeline+task_id, not just by dataset. */
export function allGates(ctx: Pick<CatalogCtx, "pipelines" | "wiredGates">): GateRec[] {
  return [...harnessGates(ctx.pipelines), ...ctx.wiredGates];
}

/** A dataset's worst observed unit state, read off whichever mock pipeline fixture already tracks it (the SAME
 *  fixture both HARNESS and WIRED rows read, since a WIRED gate may well name an existing HARNESS-governed
 *  relation, PFP's own "reopens nothing" case). A genuinely new WIRED-only dataset name resolves to EMPTY, never
 *  fabricated. The mock carries no per-state unit breakdown for this lightweight fixture, so `x_counts` is always
 *  `{}` here -- a stated simplification, not a silently-dropped field. */
function nodeStateFor(dataset: string, ctx: Pick<CatalogCtx, "pipelines">): { state: string; x_counts: Record<string, number> } {
  for (const p of ctx.pipelines.pipelines) {
    const g = p.gated_nodes.find((x) => x.node === dataset);
    if (g) return { state: g.state, x_counts: {} };
  }
  return { state: "EMPTY", x_counts: {} };
}

/** dataset -> the id of the gate that introduced it FIRST (HARNESS genesis gates always win over anything wired
 *  later, mirroring `gated_dataset_introductions`'s own version ordering: the earliest PUT wins until removed). */
export function buildIntroducedBy(ctx: Pick<CatalogCtx, "pipelines" | "wiredGates">): Map<string, string> {
  const m = new Map<string, string>();
  for (const g of allGates(ctx)) for (const ds of g.datasets) if (!m.has(ds)) m.set(ds, g.id);
  return m;
}

const TIER_RANK: Record<string, number> = { L1: 1, L2: 2, L3: 3, L4: 4 };
const lowest = (ts: string[]): string => ts.reduce((w, t) => ((TIER_RANK[t] ?? 1) < (TIER_RANK[w] ?? 1) ? t : w));

/** `dataset_governance`'s own reduction (profile.py:69-97), mirrored: agent_tier is the plain RELEASE tier,
 *  lowest across authorities (a missing autonomy row means L1, this mock's own pre-existing convention);
 *  effective_tier additionally applies the introducing gate's release_ceiling, UNLESS that ceiling is INHERIT
 *  (task_gate_ceiling() SQL: INHERIT -> NULL -> no additional ceiling). Both null when the dataset resolves to no
 *  authority (never defaulted to a tier that was never computed). */
export function datasetGov(
  dataset: string,
  authorities: string[],
  introducedBy: Map<string, string>,
  ctx: Pick<CatalogCtx, "pipelines" | "wiredGates" | "autonomy">,
): { dataset: string; introduced_by: string | null; agent_tier: string | null; effective_tier: string | null } {
  const introduced_by = introducedBy.get(dataset) ?? null;
  if (!authorities.length) return { dataset, introduced_by, agent_tier: null, effective_tier: null };
  const agent_tier = lowest(authorities.map((a) => ctx.autonomy.find((r) => r.action_class === "RELEASE" && r.authority === a)?.tier ?? "L1"));
  const gate = introduced_by ? allGates(ctx).find((g) => g.id === introduced_by) : null;
  const ceiling = gate && gate.releaseCeiling !== "INHERIT" ? gate.releaseCeiling : null;
  const effective_tier = ceiling ? lowest([agent_tier, ceiling]) : agent_tier;
  return { dataset, introduced_by, agent_tier, effective_tier };
}

/** The two REAL, committed dbt manifests this demo ships (`pipelines/w1_dbt`, `pipelines/tmsis_outbound`),
 *  mirrored exactly as `dqagent/adapters/dag/dbt_files.discover()` would read them -- the mock never reads a
 *  file (the browser cannot), so any OTHER manifest_path is honestly refused, same as a confinement or
 *  not-found failure on the real adapter. `manifest_digest` is a mock-only FNV stand-in, never sha256. */
export const MANIFESTS: Record<string, { manifest_digest: string; nodes: DiscoveredNode[] }> = {
  "pipelines/tmsis_outbound/target/manifest.json": {
    manifest_digest: fx("manifest:tmsis_outbound"),
    nodes: [
      {
        node: "outbound_eligibility_extract",
        resource_type: "model",
        schema: "main_outbound",
        relation_name: '"tmsis_outbound_parse"."main_outbound"."outbound_eligibility_extract"',
        depends_on: ["stg_eligibility"],
        layer: "OUTBOUND",
        layer_source: "meta.dq_layer",
      },
      {
        node: "outbound_eligibility_summary",
        resource_type: "model",
        schema: "main_outbound",
        relation_name: '"tmsis_outbound_parse"."main_outbound"."outbound_eligibility_summary"',
        depends_on: ["stg_eligibility"],
        layer: "OUTBOUND",
        layer_source: "meta.dq_layer",
      },
      {
        node: "outbound_encounter_extract",
        resource_type: "model",
        schema: "main_outbound",
        relation_name: '"tmsis_outbound_parse"."main_outbound"."outbound_encounter_extract"',
        depends_on: ["stg_encounters"],
        layer: "OUTBOUND",
        layer_source: "meta.dq_layer",
      },
      {
        node: "outbound_encounter_summary",
        resource_type: "model",
        schema: "main_outbound",
        relation_name: '"tmsis_outbound_parse"."main_outbound"."outbound_encounter_summary"',
        depends_on: ["stg_encounters"],
        layer: "OUTBOUND",
        layer_source: "meta.dq_layer",
      },
      {
        node: "stg_eligibility",
        resource_type: "model",
        schema: "main_staging",
        relation_name: '"tmsis_outbound_parse"."main_staging"."stg_eligibility"',
        depends_on: ["ie_segment_released"],
        layer: "OUTBOUND",
        layer_source: "meta.dq_layer",
      },
      {
        node: "stg_encounters",
        resource_type: "model",
        schema: "main_staging",
        relation_name: '"tmsis_outbound_parse"."main_staging"."stg_encounters"',
        depends_on: ["encounter_header_released"],
        layer: "OUTBOUND",
        layer_source: "meta.dq_layer",
      },
    ],
  },
  "pipelines/w1_dbt/target/manifest.json": {
    manifest_digest: fx("manifest:w1_dbt"),
    nodes: [
      {
        node: "gold_member_month",
        resource_type: "model",
        schema: "main_gold",
        relation_name: '"dq_w1"."main_gold"."gold_member_month"',
        depends_on: ["silver_eligibility_span"],
        layer: "UNLAYERED",
        layer_source: "UNLAYERED",
      },
      {
        node: "gold_tmsis_cot_subset",
        resource_type: "model",
        schema: "main_gold",
        relation_name: '"dq_w1"."main_gold"."gold_tmsis_cot_subset"',
        depends_on: ["silver_encounter_chain"],
        layer: "UNLAYERED",
        layer_source: "UNLAYERED",
      },
      {
        node: "gold_tmsis_elg_subset",
        resource_type: "model",
        schema: "main_gold",
        relation_name: '"dq_w1"."main_gold"."gold_tmsis_elg_subset"',
        depends_on: ["silver_eligibility_span"],
        layer: "UNLAYERED",
        layer_source: "UNLAYERED",
      },
      {
        node: "silver_eligibility_span",
        resource_type: "model",
        schema: "main_silver",
        relation_name: '"dq_w1"."main_silver"."silver_eligibility_span"',
        depends_on: ["ie_segment_current"],
        layer: "UNLAYERED",
        layer_source: "UNLAYERED",
      },
      {
        node: "silver_encounter_chain",
        resource_type: "model",
        schema: "main_silver",
        relation_name: '"dq_w1"."main_silver"."silver_encounter_chain"',
        depends_on: ["encounter_header_released"],
        layer: "UNLAYERED",
        layer_source: "UNLAYERED",
      },
    ],
  },
};

function pipelineRow(
  id: string,
  provenance: "HARNESS" | "WIRED",
  platform: string | null,
  domains: string[],
  source: Record<string, unknown> | null,
  gates: GateRec[],
  ctx: CatalogCtx,
): CatalogPipeline {
  const datasets = [...new Set(gates.flatMap((g) => g.datasets))];
  return {
    id,
    provenance,
    platform,
    domains,
    source,
    nodes: datasets.map((d) => ({ node: d, ...nodeStateFor(d, ctx) })),
    task_gates: gates.map((g) => ({ id: g.id, task_id: g.taskId, datasets: g.datasets, mode: g.mode, release_ceiling: g.releaseCeiling })),
    governance: { authorities: pipelineAuthorities(datasets) },
    // K2b's PIPELINE_RUN span is not yet modelled in the mock's run-trace builder (routes/obs.ts's `world()`
    // mints only NIGHT and EXPLAIN roots); every pipeline honestly reports no last night, rather than one
    // fabricated. Stated limitation, not a silent gap.
    last_night: null,
  };
}

export function mockCatalog(ctx: CatalogCtx, method: string, url: URL): R | null {
  if (method !== "GET") return null;
  const p = url.pathname;
  const scope = new Set(ctx.me.authorities);

  if (p === "/catalog/pipelines") {
    const harness: CatalogPipeline[] = [];
    for (const pl of ctx.pipelines.pipelines) {
      const gates = harnessGates(ctx.pipelines).filter((g) => g.pipelineId === pl.id);
      const auths = pipelineAuthorities(gates.flatMap((g) => g.datasets));
      if (!auths.some((a) => scope.has(a))) continue; // D7 extended to a list: omit, never redact
      harness.push(pipelineRow(pl.id, "HARNESS", pl.platform, pl.domains, null, gates, ctx));
    }
    const wired: CatalogPipeline[] = [];
    for (const w of ctx.wiredPipelines) {
      const gates = ctx.wiredGates.filter((g) => g.pipelineId === w.id);
      const auths = pipelineAuthorities([...new Set(gates.flatMap((g) => g.datasets))]);
      if (!auths.some((a) => scope.has(a))) continue;
      wired.push(
        pipelineRow(
          w.id,
          "WIRED",
          w.platform,
          w.domains,
          { adapter: w.adapter, scope: { manifest_path: w.manifestPath }, platform: w.platform, domains: w.domains, layer_map: w.layerMap, run_on: w.runOn },
          gates,
          ctx,
        ),
      );
    }
    const groups = [
      { provenance: "HARNESS", pipelines: harness },
      { provenance: "WIRED", pipelines: wired },
    ].filter((g) => g.pipelines.length);
    return ok({ groups, order: ctx.pipelines.order });
  }

  const nm = p.match(/^\/catalog\/pipelines\/([^/]+)\/nodes$/);
  if (nm) {
    const id = decodeURIComponent(nm[1]!);
    const wp = ctx.wiredPipelines.find((w) => w.id === id);
    const known = ctx.pipelines.pipelines.some((pl) => pl.id === id) || !!wp;
    if (!known) return err(404, "pipeline not found");
    const gates = allGates(ctx).filter((g) => g.pipelineId === id);
    const datasets = [...new Set(gates.flatMap((g) => g.datasets))];
    const auths = pipelineAuthorities(datasets);
    if (!auths.some((a) => scope.has(a))) return err(403, "no effective release duty (token claim AND roster) over this pipeline's authorities");
    const introducedBy = buildIntroducedBy(ctx);
    const rows: CatalogNode[] = [];
    for (const g of gates) {
      for (const ds of g.datasets) {
        const gov = datasetGov(ds, pipelineAuthorities([ds]), introducedBy, ctx);
        rows.push({
          node: ds,
          layer: "GATED",
          layer_source: "TASK_GATE",
          evaluation: nodeStateFor(ds, ctx),
          gate: { id: g.id, mode: g.mode },
          datasets: [gov],
        });
      }
    }
    if (wp?.manifestPath) {
      const disc = MANIFESTS[wp.manifestPath];
      if (disc) for (const n of disc.nodes) rows.push({ node: n.node, layer: n.layer, layer_source: n.layer_source, evaluation: null, gate: null, datasets: [] });
    }
    return ok({ nodes: rows });
  }

  // K4 (PFP P2-4): GET /catalog/pipelines/{id}/graph?overlay=gates. Same-shape mock, shipped alongside the live
  // route in this step (not deferred to K7). Repeats the EXPLICIT _authorities_of/caller_scope&auths -> 403 check
  // verbatim (D-K4-1) rather than relying on any implicit suppression -- the SAME pattern the /nodes route above
  // already uses. The node SET deliberately matches /nodes exactly (TASK_GATE-governed datasets + any discovered
  // dbt-manifest nodes), so "the graph and the table show the same node set" (EXECUTION_PLAN K4 exit criterion)
  // holds in mock mode too. Edges are new logic here (no PipelineDag.tsx reuse, per the golden-path note): a
  // discovered dbt node's own depends_on, restricted to other nodes already in this pipeline's node set -- honest
  // about what this mock can derive (a HARNESS pipeline with no manifest_path carries no inner dependency data in
  // this fixture, so it returns nodes with an empty edge list, never a fabricated edge).
  const gm = p.match(/^\/catalog\/pipelines\/([^/]+)\/graph$/);
  if (gm) {
    const id = decodeURIComponent(gm[1]!);
    const overlay = url.searchParams.get("overlay");
    if (overlay !== "gates") return err(400, `overlay must be one of: gates (got '${overlay}')`);
    const wp = ctx.wiredPipelines.find((w) => w.id === id);
    const known = ctx.pipelines.pipelines.some((pl) => pl.id === id) || !!wp;
    if (!known) return err(404, "pipeline not found");
    const gates = allGates(ctx).filter((g) => g.pipelineId === id);
    const datasets = [...new Set(gates.flatMap((g) => g.datasets))];
    const auths = pipelineAuthorities(datasets);
    if (!auths.some((a) => scope.has(a))) return err(403, "no effective release duty (token claim AND roster) over this pipeline's authorities");
    const nodes: GraphNode[] = [];
    for (const g of gates) for (const ds of g.datasets) nodes.push({ id: ds, state: nodeStateFor(ds, ctx).state, gate: { id: g.id, mode: g.mode } });
    const disc = wp?.manifestPath ? MANIFESTS[wp.manifestPath] : null;
    if (disc) for (const n of disc.nodes) nodes.push({ id: n.node, state: null, gate: null });
    const nodeIds = new Set(nodes.map((n) => n.id));
    const edges: GraphEdge[] = [];
    if (disc) for (const n of disc.nodes) for (const dep of n.depends_on) if (nodeIds.has(dep)) edges.push({ from: dep, to: n.node, policy: null, orderOk: true });
    return ok({ nodes, edges });
  }

  return null;
}
