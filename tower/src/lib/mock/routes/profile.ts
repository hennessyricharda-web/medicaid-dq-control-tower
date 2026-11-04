/**
 * Mock of dqagent/api/catalog_routes.py + dqagent/governance/profile.py (PFP P1-2, K1): the per-pipeline governance
 * configuration snapshot. Derived LIVE from the mock's own state at request time (the house pattern of
 * routes/obs.ts), never a static recorded transcript (K1_DESIGN.md D1: the recorded-fixture mechanism is deferred
 * to K7). source_mode never appears here; this route carries no generated text.
 *
 * Mock limitation, stated once: the mock keeps only the CURRENT version of each governed artifact (no history), so
 * `contributors` lists one version per artifact, and `as_of` does not change the composed content -- an as_of that
 * names a trace this tab actually recorded still resolves (200, same snapshot); an as_of naming no such trace is
 * 404, matching the real route's fail-closed behaviour. The real backend keeps every artifact version and a real
 * as_of cutoff genuinely changes what is returned (D9).
 */
import type { AutonomyRow, GovArtifact, Me, Pipelines, RuleRow, SimRunSummary } from "../../types";
import type { PipelineProfile, ProfileContract, ProfileContributorVersion, ProfileRule, ProfileRouting } from "../../types/catalog";
import { MOCK_STATE } from "../fixtures";

type R = { status: number; json: unknown };
const ok = (json: unknown): R => ({ status: 200, json });
const err = (status: number, detail: unknown): R => ({ status, json: { detail } });

export interface ProfileCtx {
  me: Me;
  pipelines: Pipelines;
  rules: RuleRow[];
  artifacts: GovArtifact[];
  autonomy: AutonomyRow[];
  nights: SimRunSummary[];
}

/** config/state/ZZ/contracts_w1.yaml, mirrored exactly for the two relations it actually governs in W1
 * (bronze.ie_segment, bronze.enrollment_834, bronze.encounter_header, bronze.encounter_line); the mock's extra
 * silver/gold/raw nodes (not yet gated in the real W1 state) are given a reasonable authority set by domain so
 * every gated_node in PIPELINES resolves to SOMETHING, never silently dropped. */
/** Exported (additive, K2c): the Catalog mock (routes/catalog.ts) reuses this SAME relation -> authority table
 * rather than keeping a second copy -- one definition of "which authorities gate which relation" in this tab. */
export const CONTRACTS: Record<string, { domain: string; authorities_by_program: Record<string, string[]>; status: string }> = {
  "raw.x12_segment": { domain: "INTAKE", authorities_by_program: { MEDICAID: ["AUTH_MEDICAID"] }, status: "ACTIVE" },
  "raw.flat_record": { domain: "INTAKE", authorities_by_program: { MEDICAID: ["AUTH_MEDICAID"] }, status: "ACTIVE" },
  "bronze.ie_segment": {
    domain: "ELIGIBILITY",
    authorities_by_program: { MEDICAID: ["AUTH_MEDICAID"], MCHIP: ["AUTH_MEDICAID"], SCHIP: ["AUTH_CHIP"], SNAP: ["AUTH_SNAP"], TANF: ["AUTH_TANF"] },
    status: "DRAFT",
  },
  "silver.eligibility_span": {
    domain: "ELIGIBILITY",
    authorities_by_program: { MEDICAID: ["AUTH_MEDICAID"], MCHIP: ["AUTH_MEDICAID"], SCHIP: ["AUTH_CHIP"], SNAP: ["AUTH_SNAP"], TANF: ["AUTH_TANF"] },
    status: "ACTIVE",
  },
  "bronze.enrollment_834": { domain: "ENROLLMENT", authorities_by_program: { MEDICAID: ["AUTH_MEDICAID"] }, status: "DRAFT" },
  "silver.provider": { domain: "PROVIDER", authorities_by_program: { MEDICAID: ["AUTH_MEDICAID"] }, status: "ACTIVE" },
  "bronze.encounter_header": { domain: "CLAIMS", authorities_by_program: { MEDICAID: ["AUTH_MEDICAID"] }, status: "DRAFT" },
  "silver.encounter_chain": { domain: "CLAIMS", authorities_by_program: { MEDICAID: ["AUTH_MEDICAID"] }, status: "ACTIVE" },
  "gold.tmsis_cot_subset": { domain: "CLAIMS", authorities_by_program: { MEDICAID: ["AUTH_MEDICAID"] }, status: "ACTIVE" },
};

/** config/state/ZZ/feeds.yaml's routing.routes, mirrored for the mock's own pipeline ids. */
const ROUTING: ProfileRouting[] = [
  { match: "^ie_extract_(?P<date>\\d{8})\\.txt$", format: "IE", pipeline: "P_ELIGIBILITY", relations: ["bronze.ie_segment"] },
  {
    match: "^834_(?P<partner>[A-Z0-9]+)_(?P<date>\\d{8})_(?P<seq>\\d+)\\.x12$",
    format: "X12_834",
    pipeline: "P_ENROLLMENT",
    relations: ["bronze.enrollment_834"],
    ack: "ack_{partner}_{date}_{seq}.csv",
  },
  {
    match: "^837P_(?P<partner>[A-Z0-9]+)_(?P<date>\\d{8})_(?P<seq>\\d+)\\.x12$",
    format: "X12_837P",
    pipeline: "P_ENCOUNTER",
    relations: ["bronze.encounter_header", "bronze.encounter_line"],
    ack: "ack_{partner}_{date}_{seq}.csv",
  },
];

/** dqagent/governance/registry.py MONITOR_DEFAULTS, mirrored exactly (a seeded config literal, not a secret). */
const MONITOR_DEFAULTS = { advisory: true, min_periods: 8, run_rules: { R1: true, R2: false }, sigma: 3, psi: { watch: 0.1, shift: 0.25, min_expected: 5 }, rebaseline: [] as unknown[] };

export function pipelineAuthorities(nodes: string[]): string[] {
  const out = new Set<string>();
  for (const n of nodes) for (const auths of Object.values(CONTRACTS[n]?.authorities_by_program ?? {})) for (const a of auths) out.add(a);
  return [...out].sort();
}

/** `NIGHT-<state>-<yyyymmdd>-a1`, the exact format routes/obs.ts's nightRun() mints for each simulated night. */
function nightTraceIds(nights: SimRunSummary[]): Set<string> {
  return new Set(nights.map((n) => `NIGHT-${MOCK_STATE}-${n.night_date.replace(/-/g, "")}-a1`));
}

async function digest(obj: unknown): Promise<string> {
  const bytes = new TextEncoder().encode(JSON.stringify(obj));
  const buf = await crypto.subtle.digest("SHA-256", bytes);
  return [...new Uint8Array(buf)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

export async function mockProfile(ctx: ProfileCtx, method: string, url: URL): Promise<R | null> {
  if (method !== "GET") return null;
  const m = url.pathname.match(/^\/catalog\/pipelines\/([^/]+)\/profile$/);
  if (!m) return null;
  const id = decodeURIComponent(m[1]!);
  const { me } = ctx;

  if (me.authorities.length === 0) return err(403, "no effective release duty (token claim AND roster) in any authority");

  const pipeline = ctx.pipelines.pipelines.find((p) => p.id === id);
  if (!pipeline) return err(404, "pipeline not found");
  const gatedNodes = pipeline.gated_nodes.map((g) => g.node);
  const pipelineAuths = pipelineAuthorities(gatedNodes);
  if (!me.authorities.some((a) => pipelineAuths.includes(a)))
    return err(403, "no effective release duty (token claim AND roster) over this pipeline's authorities");

  const asOf = url.searchParams.get("as_of");
  if (asOf && !nightTraceIds(ctx.nights).has(asOf)) return err(404, `${asOf} not found`);

  const contracts: Record<string, ProfileContract> = {};
  for (const n of gatedNodes) if (CONTRACTS[n]) contracts[n] = CONTRACTS[n];

  const rules: Record<string, ProfileRule> = {};
  for (const r of ctx.rules) {
    if (!r.relation || !gatedNodes.includes(r.relation)) continue;
    const approvedHash = r.approved_by_cr ? r.effective_hash : null;
    rules[`${MOCK_STATE}:${r.id}@${r.entity}`] = {
      hash: r.effective_hash,
      status: r.status,
      gating: r.gating,
      approved_hash: approvedHash,
      effective_equals_approved: approvedHash === r.effective_hash,
    };
  }

  const tiers: Record<string, string> = {};
  for (const a of pipelineAuths) tiers[a] = ctx.autonomy.find((x) => x.action_class === "RELEASE" && x.authority === a)?.tier ?? "L1";

  const dependencies = ctx.pipelines.dependencies.filter((d) => pipeline.domains.includes(d.domain) || pipeline.domains.includes(d.depends_on));
  const routing = ROUTING.filter((r) => r.pipeline === id);

  const ruleIds = new Set([...Object.keys(rules)].map((k) => k.split(":", 2)[1]!.split("@")[0]!));
  const wantedArtifacts = new Set([`${MOCK_STATE}:feeds`, `${MOCK_STATE}:policy`, `${MOCK_STATE}:profile`, `${MOCK_STATE}:elements`, `${MOCK_STATE}:catalog`, ...[...ruleIds].map((rid) => `${MOCK_STATE}:overlay:${rid}`)]);
  const contributors: Record<string, ProfileContributorVersion[]> = {};
  for (const a of ctx.artifacts) {
    if (!wantedArtifacts.has(a.artifact_id)) continue;
    contributors[a.artifact_id] = [
      { version: a.version, content_hash: a.content_hash, cr_id: a.cr_id, approvers: (a.approved_by ?? "").split(",").filter(Boolean) },
    ];
  }

  const out: Omit<PipelineProfile, "profile_digest"> = {
    pipeline: { id: pipeline.id, platform: pipeline.platform, orchestrator: pipeline.orchestrator, gated_nodes: gatedNodes, domains: pipeline.domains },
    contracts,
    rules,
    policy: { modes: ["A", "H_ON", "H_IN", "H_IN_2"] },
    tiers,
    monitors: { content: MONITOR_DEFAULTS, provenance: "FILE", governed: false },
    dependencies,
    order: ctx.pipelines.order,
    routing,
    contributors,
    as_of: asOf,
  };
  return ok({ ...out, profile_digest: await digest(out) });
}
