/**
 * Mock of dqagent/api/catalog_routes.py's `POST /catalog/discover` and `POST /catalog/wire` (PFP :836-839,
 * EXECUTION_PLAN.md wave K2c). `discover` is a read-only preview over the two REAL, committed dbt manifests this
 * demo ships (`MANIFESTS` in routes/catalog.ts) -- the mock cannot read a file, so any OTHER manifest_path is
 * honestly refused with the SAME 422 {status:"REFUSED", reason} shape the real path-confinement check uses.
 *
 * `wire` builds the SAME two items the real route builds (one PIPELINE, one TASK_GATE per `task_gates[]` entry),
 * then PROPOSES them through the mock's OWN existing two-person change-request mechanism (`server.ts`'s
 * `proposeCr`, the same function `POST /governance/change-requests` already uses) -- not a second, parallel CR
 * mechanism. PIPELINE and TASK_GATE are never lower-only (K2a, mirrored in `proposeCr`), so `approvals_required`
 * is always 2 here, the SAME fact the real database computes. This route itself never decides or applies
 * anything; the returned `cr_id` goes through the existing challenge/decide flow, exactly like every other CR.
 */
import type { AutonomyRow, Me, Pipelines } from "../../types";
import type { WireImpact } from "../../types/catalog";
import { MOCK_STATE } from "../fixtures";
import { pipelineAuthorities } from "./profile";
import { allGates, buildIntroducedBy, datasetGov, MANIFESTS, type GateRec } from "./catalog";

type R = { status: number; json: unknown };
const ok = (json: unknown): R => ({ status: 200, json });
const coded = (status: number, code: string, reason: string): R => ({ status, json: { detail: { status: code, reason } } });

/** Duplicated from server.ts's own `govDuty`, not imported: the SAME boundary catalog_routes.py itself keeps
 *  from app.py (a route-local closure, not a shared function) -- this mock mirrors that file-boundary choice. */
const hasGovDuty = (me: Me) => me.duties.includes("governance:AUTH_RULES");

export interface WireCtx {
  me: Me;
  pipelines: Pipelines;
  autonomy: AutonomyRow[];
  wiredGates: GateRec[];
  /** The mock's own existing CR-proposal mechanism (server.ts's `proposeCr`), reused -- not duplicated. Returns
   *  the SAME {status, json:{cr_id, impact, loosening, approvals_required}} shape /governance/change-requests
   *  already returns, or an HTTP-shaped refusal. */
  propose: (items: { artifact_id: string; kind: string; op: string; content: unknown }[], rationale: string) => R;
}

const b0 = (body: unknown) => (body ?? {}) as Record<string, unknown>;

export function mockWire(ctx: WireCtx, method: string, url: URL, body: unknown): R | null {
  const p = url.pathname;
  if (method !== "POST" || (p !== "/catalog/discover" && p !== "/catalog/wire")) return null;
  if (!hasGovDuty(ctx.me)) return { status: 403, json: { detail: "governance duty required (token claim AND roster)" } };
  const b = b0(body);

  if (p === "/catalog/discover") {
    const manifestPath = b.manifest_path;
    if (!manifestPath || typeof manifestPath !== "string") return coded(422, "REFUSED", "manifest_path (string) is required");
    if (!manifestPath.startsWith("pipelines/")) return coded(422, "REFUSED", `manifest_path must be confined to pipelines/: ${JSON.stringify(manifestPath)}`);
    const disc = MANIFESTS[manifestPath];
    if (!disc) return coded(422, "REFUSED", `no manifest at ${JSON.stringify(manifestPath)}`);
    const layerMap = (b.layer_map ?? {}) as Record<string, string>;
    return ok({
      manifest_digest: disc.manifest_digest,
      nodes: disc.nodes.map((n) => (layerMap[n.node] ? { ...n, layer: layerMap[n.node], layer_source: "layer_map" } : n)),
    });
  }

  // p === "/catalog/wire"
  const rationale = typeof b.rationale === "string" ? b.rationale.trim() : "";
  if (rationale.length < 10) return coded(422, "REFUSED", "rationale of at least 10 characters required");
  const pipeline = (b.pipeline ?? {}) as Record<string, unknown>;
  const pid = typeof pipeline.id === "string" ? pipeline.id : "";
  const gates = Array.isArray(b.task_gates) ? (b.task_gates as Record<string, unknown>[]) : [];
  if (!pid || !gates.length) return coded(422, "REFUSED", "pipeline.id and a non-empty task_gates[] are required");
  const scope = (pipeline.scope ?? {}) as Record<string, unknown>;
  const manifestPath = typeof scope.manifest_path === "string" ? scope.manifest_path : "";
  if (!manifestPath) return coded(422, "REFUSED", "pipeline.scope.manifest_path is required");

  const pipelineItem = {
    artifact_id: `${MOCK_STATE}:pipeline:${pid}`,
    kind: "PIPELINE",
    op: "PUT",
    content: {
      adapter: pipeline.adapter ?? "dbt_files",
      scope: { manifest_path: manifestPath },
      platform: pipeline.platform ?? "duckdb",
      domains: pipeline.domains ?? [],
      layer_map: pipeline.layer_map ?? {},
      run_on: pipeline.run_on ?? "manual",
    },
  };
  const items: { artifact_id: string; kind: string; op: string; content: unknown }[] = [pipelineItem];
  const requested = new Set<string>();
  for (const g of gates) {
    const taskId = typeof g.task_id === "string" ? g.task_id : "";
    const datasets = Array.isArray(g.datasets) ? (g.datasets as string[]) : [];
    if (!taskId || !datasets.length) return coded(422, "REFUSED", "each task_gates[] entry needs task_id and a non-empty datasets[]");
    const mode = typeof g.mode === "string" ? g.mode : "advisory";
    datasets.forEach((d) => requested.add(d));
    items.push({
      artifact_id: `${MOCK_STATE}:taskgate:${pid}:${taskId}`,
      kind: "TASK_GATE",
      op: "PUT",
      content: {
        task_id: taskId,
        datasets,
        mode,
        correlate: "LATEST_SEALED",
        max_wait: g.max_wait ?? "PT2H",
        on_timeout: g.on_timeout ?? (mode === "advisory" ? "PASS_ADVISORY" : "HOLD"),
        release_ceiling: g.release_ceiling ?? "L1",
        reintroduce: g.reintroduce ?? [],
      },
    });
  }

  // Impact preview (read-only, BEFORE propose): which requested datasets are already gated by a still-effective
  // introducer (ORCH-1's "reopens nothing") vs. ones this wiring would introduce. Uses the CURRENT gate set
  // (this proposal's own items are not yet effective), the SAME order the real route computes it in.
  const introducedBy = buildIntroducedBy({ pipelines: ctx.pipelines, wiredGates: ctx.wiredGates });
  const alreadyGated: { dataset: string; introduced_by: string }[] = [];
  const introduced: string[] = [];
  const tiersBefore: Record<string, string | null> = {};
  const tiersAfter: Record<string, string | null> = {};
  const rank: Record<string, number> = { L1: 1, L2: 2, L3: 3, L4: 4 };
  for (const ds of [...requested].sort()) {
    const before = datasetGov(ds, pipelineAuthorities([ds]), introducedBy, { pipelines: ctx.pipelines, wiredGates: ctx.wiredGates, autonomy: ctx.autonomy });
    tiersBefore[ds] = before.effective_tier;
    const introducer = introducedBy.get(ds);
    if (introducer) {
      alreadyGated.push({ dataset: ds, introduced_by: introducer });
      tiersAfter[ds] = before.effective_tier; // unchanged: an already-gated consumer reopens nothing
    } else {
      introduced.push(ds);
      const ceiling = (gates.find((g) => (Array.isArray(g.datasets) ? (g.datasets as string[]) : []).includes(ds))?.release_ceiling as string) || "L1";
      tiersAfter[ds] = before.agent_tier === null ? null : [before.agent_tier, ceiling].reduce((w, t) => ((rank[t] ?? 1) < (rank[w] ?? 1) ? t : w));
    }
  }

  const r = ctx.propose(items, rationale);
  if (r.status >= 400) return r;
  const { cr_id, approvals_required } = r.json as { cr_id: string; approvals_required: number; impact?: Record<string, unknown> };
  const baseImpact = (r.json as { impact?: Record<string, unknown> }).impact ?? {};
  const ceilings = [...new Set(gates.filter((g) => (g.datasets as string[] | undefined)?.some((d) => introduced.includes(d))).map((g) => (g.release_ceiling as string) || "L1"))].sort();
  const impact: WireImpact = {
    ...baseImpact,
    gates_added: items.filter((i) => i.kind === "TASK_GATE").map((i) => (i.content as { task_id: string }).task_id),
    datasets_introduced: introduced,
    datasets_already_gated: alreadyGated,
    ceiling: { value: ceilings.length === 1 ? ceilings[0]! : ceilings.length ? ceilings : null, applies_to: introduced },
    mode: [...new Set(gates.map((g) => (typeof g.mode === "string" ? g.mode : "advisory")))].sort(),
    validation_errors: [],
    effective_tier_before: tiersBefore,
    effective_tier_after: tiersAfter,
  };
  return ok({ cr_id, approvals_required, impact });
}

/**
 * F-11b fix: mock of `catalog_routes.py`'s new `catalog_promote_gate` route
 * (`POST /catalog/pipelines/{pipeline_id}/task-gates/{task_id}/promote`). Same reasoning as the real route:
 * a TASK_GATE PUT item is a full-content replace, so this reads the gate's CURRENT full content from the
 * mock's own `allGates` (mode/correlate/max_wait/on_timeout/release_ceiling/reintroduce included -- the real
 * fixture the Tower's Catalog row never exposes) and changes only `mode`, then proposes through the SAME
 * `ctx.propose` / mock CR mechanism `mockWire` above already uses. The caller sends nothing but a rationale.
 */
export function mockPromoteGate(ctx: WireCtx, method: string, url: URL, body: unknown): R | null {
  const m = /^\/catalog\/pipelines\/([^/]+)\/task-gates\/([^/]+)\/promote$/.exec(url.pathname);
  if (method !== "POST" || !m) return null;
  if (!hasGovDuty(ctx.me)) return { status: 403, json: { detail: "governance duty required (token claim AND roster)" } };
  const pipelineId = decodeURIComponent(m[1]!);
  const taskId = decodeURIComponent(m[2]!);
  const b = b0(body);
  const rationale = typeof b.rationale === "string" ? b.rationale.trim() : "";
  if (rationale.length < 10) return coded(422, "REFUSED", "rationale of at least 10 characters required");
  const gate = allGates({ pipelines: ctx.pipelines, wiredGates: ctx.wiredGates }).find(
    (g) => g.pipelineId === pipelineId && g.taskId === taskId,
  );
  if (!gate) return coded(404, "REFUSED", `no TASK_GATE ${JSON.stringify(taskId)} on pipeline ${JSON.stringify(pipelineId)}`);
  if (gate.mode !== "advisory") {
    return coded(422, "REFUSED", `gate is ${JSON.stringify(gate.mode)}, not advisory; only an advisory gate can be promoted to block here`);
  }
  const item = {
    artifact_id: gate.id,
    kind: "TASK_GATE",
    op: "PUT",
    content: {
      task_id: gate.taskId,
      datasets: gate.datasets,
      mode: "block",
      correlate: "LATEST_SEALED",
      max_wait: gate.maxWait,
      on_timeout: gate.onTimeout,
      release_ceiling: gate.releaseCeiling,
      reintroduce: gate.reintroduce,
    },
  };
  const r = ctx.propose([item], rationale);
  if (r.status >= 400) return r;
  const { cr_id, approvals_required } = r.json as { cr_id: string; approvals_required: number };
  return ok({ cr_id, approvals_required });
}

/**
 * K8 (PARTF_K7_K12_MOCK_CONTRACT.md): mock of the INVENTED `POST /catalog/pipelines/{id}/profile/draft` route.
 * PRE-BACKEND -- no real route exists for this yet (the architect's own deferral: PIPELINE/TASK_GATE edits beyond
 * `/catalog/wire` were never built server-side). This mock builds the SAME PIPELINE-kind PUT item `mockWire`
 * above builds, then proposes it through the SAME `ctx.propose` mechanism -- not a second, parallel CR shape.
 * Never touches E-09's baseline-key rename (BACKLOG:26, gated by ADJ-6): this body has no baseline field at all.
 */
export function mockProfileDraft(ctx: WireCtx, method: string, url: URL, body: unknown): R | null {
  const m = /^\/catalog\/pipelines\/([^/]+)\/profile\/draft$/.exec(url.pathname);
  if (method !== "POST" || !m) return null;
  if (!hasGovDuty(ctx.me)) return { status: 403, json: { detail: "governance duty required (token claim AND roster)" } };
  const pipelineId = decodeURIComponent(m[1]!);
  const b = b0(body);
  const rationale = typeof b.rationale === "string" ? b.rationale.trim() : "";
  if (rationale.length < 10) return coded(422, "REFUSED", "rationale of at least 10 characters required");
  const pl = ctx.pipelines.pipelines.find((p) => p.id === pipelineId);
  if (!pl) return coded(404, "REFUSED", `unknown pipeline ${JSON.stringify(pipelineId)}`);
  // Same convention as mockWire's own pipelineItem above: this is a PIPELINE-kind PUT (a full-content replace),
  // not a patch, so every field is carried, defaulted from the CURRENT harness/profile values where the body
  // omits one. No baseline-key field is read, carried or renamed here (BACKLOG:26, gated by ADJ-6).
  const item = {
    artifact_id: `${MOCK_STATE}:pipeline:${pipelineId}`,
    kind: "PIPELINE",
    op: "PUT",
    content: {
      adapter: "dbt_files",
      scope: { manifest_path: `pipelines/${pipelineId.toLowerCase()}/target/manifest.json` },
      platform: typeof b.platform === "string" && b.platform.trim() ? b.platform.trim() : pl.platform,
      domains: Array.isArray(b.domains) ? b.domains.filter((d): d is string => typeof d === "string") : pl.domains,
      layer_map: {},
      run_on: typeof b.run_on === "string" && b.run_on.trim() ? b.run_on.trim() : "manual",
    },
  };
  const r = ctx.propose([item], rationale);
  if (r.status >= 400) return r;
  const { cr_id, approvals_required } = r.json as { cr_id: string; approvals_required: number };
  return ok({ cr_id, approvals_required });
}
