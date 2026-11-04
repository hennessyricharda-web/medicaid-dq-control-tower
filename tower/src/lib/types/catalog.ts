/**
 * Catalog response shapes: a field-for-field mirror of dqagent.governance.profile.compose_profile's dict, served
 * by GET /api/v1/catalog/pipelines/{id}/profile?as_of= (dqagent/api/catalog_routes.py). PFP P1-2 / K1_DESIGN.md §3.
 * Keep this in step with the backend; the mock route (lib/mock/routes/profile.ts) returns exactly this shape.
 */

/** harness.pipelines[] entry, until P1-3 replaces this with a PIPELINE item (K1_DESIGN.md §1 PFP :710). */
export interface ProfilePipeline {
  id: string;
  platform: string;
  orchestrator: string;
  dag_source?: Record<string, unknown>;
  contracts?: string;
  gated_nodes: string[];
  domains: string[];
}

/** config/state/<ST>/contracts_w1.yaml's per-relation entry, filtered to this pipeline's gated relations. */
export interface ProfileContract {
  domain: string;
  pipeline?: string;
  grain?: string[];
  partition?: string;
  partition_expr?: string;
  program_column?: string;
  program_constant?: string;
  authorities_by_program?: Record<string, string[]>;
  pre_split_authority?: string;
  ordering?: string;
  control_tags?: Record<string, string[]>;
  supersession?: Record<string, unknown>;
  chain?: Record<string, unknown>;
  unit_from_parent?: Record<string, unknown>;
  status: string;
}

/** The compiled rule's status / gating, and effective hash against the latest APPROVED hash (never defaulted). */
export interface ProfileRule {
  hash: string;
  status: string;
  gating: string | null;
  approved_hash: string | null;
  effective_equals_approved: boolean;
}

export interface ProfileDependency {
  domain: string;
  depends_on: string;
  key_map?: string[];
  /** The real route echoes the harness's own dependency dict (an object); the mock's lighter fixture carries only
   *  the binding's type as a string. Both are rendered the same way: shown as text, never assumed to be one shape. */
  temporal_binding?: Record<string, unknown> | string;
  policy: string;
  max_wait?: string;
  escalation?: string;
}

export interface ProfileRouting {
  match: string;
  format: string;
  pipeline: string;
  relations: string[];
  ack?: string;
}

/** L-10: the monitor spec / bindings are sourced from files, not governed. governed is always false here. */
export interface ProfileMonitors {
  content: Record<string, unknown>;
  provenance: "FILE";
  governed: false;
}

export interface ProfileContributorVersion {
  version: number;
  content_hash: string;
  cr_id: string;
  /** person_ids who recorded an APPROVED event on this artifact version's change request. */
  approvers: string[];
}

// ---------------------------------------------------------------------------------------------------------------
// K2c (PFP :830-839, EXECUTION_PLAN.md wave K2c, dqagent/api/catalog_routes.py): the Catalog table, its nodes
// drawer (the "Effective tier column"), the discover preview and the wiring wizard. A field-for-field mirror of
// the 4 new routes; keep this in step with the backend, and keep lib/mock/routes/catalog.ts and wire.ts same-shape.
// ---------------------------------------------------------------------------------------------------------------

/** Worst unit state for one gated node/dataset, reduced under the caller's own scope (never as admin). */
export interface CatalogNodeState {
  state: "RELEASED" | "HELD" | "PENDING" | "EMPTY" | string;
  x_counts: Record<string, number>;
}

/** GET /catalog/pipelines item: one TASK_GATE artifact naming this pipeline. */
export interface CatalogTaskGate {
  id: string;
  task_id: string | null;
  datasets: string[] | null;
  mode: string | null;
  release_ceiling: string | null;
}

/** GET /catalog/pipelines' per-pipeline row (`catalog_routes.py:_pipeline_row`). */
export interface CatalogPipeline {
  id: string;
  provenance: "HARNESS" | "WIRED" | string;
  platform: string | null;
  domains: string[];
  /** The PIPELINE artifact's own content (adapter, scope.manifest_path, platform, domains, layer_map, run_on),
   *  or null when this pipeline carries no PIPELINE artifact (never assumed to have one shape beyond that). */
  source: Record<string, unknown> | null;
  nodes: (CatalogNodeState & { node: string })[];
  task_gates: CatalogTaskGate[];
  governance: { authorities: string[] };
  last_night: { trace_id: string; night_date: string | null; started_at: string } | null;
}

export interface CatalogGroup {
  provenance: "HARNESS" | "WIRED" | string;
  pipelines: CatalogPipeline[];
}

/** GET /catalog/pipelines (PFP :834): grouped by provenance, with the dependency-order strip (`order`, the whole
 *  state's own global order -- context, not just one pipeline's position, same convention as PipelineProfile.order). */
export interface CatalogPipelinesResponse {
  groups: CatalogGroup[];
  order: string[];
}

/** {introduced_by, agent_tier, effective_tier}: `dqagent.governance.profile.dataset_governance`'s own return,
 *  reused verbatim by the nodes route for every TASK_GATE-governed row. `effective_tier` is the ceiling-aware
 *  tier (`autonomy_tier_for`); `agent_tier` is the plain 4-argument tier with no task-gate ceiling applied. Both
 *  null when the node resolves to no authority (never defaulted to a tier that was not computed). */
export interface DatasetGovernance {
  dataset: string;
  introduced_by: string | null;
  agent_tier: string | null;
  effective_tier: string | null;
}

/** GET /catalog/pipelines/{id}/nodes row (PFP :835): two honestly-labelled kinds, never joined (the backend's own
 *  documented reason -- a governed dataset and a dbt manifest MODEL node are different objects with no derivable
 *  per-node correspondence). A TASK_GATE row carries `gate` and one `datasets[]` entry (the Effective tier column);
 *  a discovered dbt-manifest-only row carries `layer`/`layer_source` only, `gate` and `datasets` both empty. */
export interface CatalogNode {
  node: string;
  layer: string;
  layer_source: "TASK_GATE" | "layer_map" | "meta.dq_layer" | "UNLAYERED" | string;
  evaluation: CatalogNodeState | null;
  gate: { id: string; mode: string | null } | null;
  datasets: DatasetGovernance[];
}

export interface CatalogNodesResponse {
  nodes: CatalogNode[];
}

/** POST /catalog/discover body (PFP :836): a read-only preview, writes nothing. `adapter` is carried for clarity
 *  (the only adapter this package wires is `dbt_files`); the real route itself never reads or checks it. */
export interface DiscoverBody {
  adapter: "dbt_files";
  manifest_path: string;
  layer_map?: Record<string, string>;
}

/** discover()'s own return (`dqagent/adapters/dag/dbt_files.py`): one row per discovered dbt MODEL node. */
export interface DiscoveredNode {
  node: string;
  resource_type: string;
  schema: string | null;
  relation_name: string | null;
  depends_on: string[];
  layer: string;
  layer_source: "layer_map" | "meta.dq_layer" | "UNLAYERED" | string;
}
export interface DiscoverResponse {
  manifest_digest: string;
  nodes: DiscoveredNode[];
}
/** The route's own 422 shape on a bad or unconfined manifest path (`{status, reason}` inside `detail`). */
export interface RefusedError {
  status: "REFUSED";
  reason: string;
}

// ---------------------------------------------------------------------------------------------------------------
// K4 (PFP P2-4, EXECUTION_PLAN.md wave K4, dqagent/api/catalog_routes.py): the interactive pipelines graph
// (`GET /catalog/pipelines/{id}/graph?overlay=gates`). The golden-path example (blackboard.md, "K4 pre-flight +
// Design", before-build red team round 1): edges are NEW backend logic -- there is no server-side equivalent yet
// of PipelineDag.tsx's client-side buildEdges -- composed from the SAME _pipeline_task_gates/_authorities_of/
// _worst_node_state data the existing routes already use for nodes. The route repeats the explicit
// `_authorities_of`/`caller_scope & auths` -> 403 check (catalog_pipeline_nodes:195-198) verbatim before composing
// any response: a redacted-200 is never returned to a caller with zero authority over the pipeline (D-K4-1).
// -----------------------------------------------------------------------------------------------------------------

/** One node in the graph: a TASK_GATE-governed dataset (carries `gate`) or a discovered-only node (no gate). */
export interface GraphNode {
  id: string;
  state: CatalogNodeState["state"] | null;
  gate: { id: string; mode: string | null } | null;
}

/** One dependency edge, honestly labelled: `orderOk` reports the existing global-order cross-check
 *  (PipelineDag.tsx's own convention), never causal wording about why an edge exists. */
export interface GraphEdge {
  from: string;
  to: string;
  policy: string | null;
  orderOk: boolean;
}

/** `?overlay=gates` is the only value this route accepts today; any other value is a 400 (fixed allow-list,
 *  no unvalidated filter-bypass surface). */
export type GraphOverlay = "gates";

export interface PipelineGraphResponse {
  nodes: GraphNode[];
  edges: GraphEdge[];
}

/** POST /catalog/wire body (PFP :837): one PIPELINE item plus one TASK_GATE item per `task_gates[]` entry. */
export interface WireTaskGate {
  task_id: string;
  datasets: string[];
  mode: "advisory" | "block" | "hold_until_terminal" | string;
  max_wait?: string;
  on_timeout?: string;
  release_ceiling?: string;
  reintroduce?: string[];
}
export interface WireBody {
  pipeline: {
    id: string;
    adapter: "dbt_files" | string;
    scope: { manifest_path: string };
    platform?: string;
    domains?: string[];
    layer_map?: Record<string, string>;
    run_on?: string;
  };
  task_gates: WireTaskGate[];
  /** Server-enforced minimum of 10 characters (`catalog_routes.py:catalog_wire`); mirrored client-side only for a
   *  good error message, never relied on alone. */
  rationale: string;
}

/** The impact preview `/wire` returns alongside the proposal (never decided or applied by this route itself --
 *  the SAME two-person `/governance/change-requests/{cr_id}/challenge` + `/decide` mechanism applies next). The
 *  demo's load-bearing fact: `effective_tier_after` for an already-gated dataset never moves (ORCH-1, "reopens
 *  nothing"); this is shown exactly as the database computed it, with no causal wording layered on top. */
export interface WireImpact {
  gates_added: string[];
  datasets_introduced: string[];
  datasets_already_gated: { dataset: string; introduced_by: string }[];
  ceiling: { value: string | string[] | null; applies_to: string[] };
  mode: string[];
  validation_errors: string[];
  effective_tier_before: Record<string, string | null>;
  effective_tier_after: Record<string, string | null>;
  /** The rest of `services.registry.propose`'s own impact shape (rules, loosening_reasons, ruleset_before/after,
   *  summary, track_record): same object every other change request already carries (CrImpact in lib/types.ts),
   *  spread in here by the real route (`r["impact"] | {...}`). Typed loosely: this package renders only the
   *  wiring-specific fields above; the governance screen already renders the rest when this CR is opened there. */
  rules?: unknown[];
  loosening_reasons?: string[];
  ruleset_before?: string | null;
  ruleset_after?: string | null;
  summary?: Record<string, unknown>;
}

export interface WireResponse {
  cr_id: string;
  approvals_required: number;
  impact: WireImpact;
}

/** F-11b fix: POST /catalog/pipelines/{id}/task-gates/{task_id}/promote body. No `content` -- the server reads
 *  the gate's own current full content and patches only `mode`, so the browser (whose `CatalogTaskGate` row
 *  carries only id/task_id/datasets/mode/release_ceiling) never has to assemble a complete TASK_GATE object. */
export interface PromoteGateBody {
  rationale: string;
}
export interface PromoteGateResponse {
  cr_id: string;
  approvals_required: number;
}

/**
 * K8 (PARTF_K7_K12_MOCK_CONTRACT.md): POST /catalog/pipelines/{id}/profile/draft body. PRE-BACKEND -- no real
 * route exists for this yet; the mock proposes the SAME PIPELINE-kind item `/catalog/wire` already builds (see
 * WireBody.pipeline above), through the SAME propose/CR mechanism, so the shape here is deliberately narrow: only
 * the fields a PIPELINE artifact actually carries. Never the baseline-key rename (BACKLOG:26, gated by ADJ-6) --
 * this body carries no baseline field at all.
 */
export interface ProfileDraftBody {
  platform?: string;
  domains?: string[];
  run_on?: string;
  rationale: string;
}
/** Same shape as PromoteGateResponse: a cr_id and how many approvals it needs, nothing decided or applied here. */
export interface ProfileDraftResponse {
  cr_id: string;
  approvals_required: number;
}

export interface PipelineProfile {
  pipeline: ProfilePipeline;
  /** relation id -> contract entry, filtered to this pipeline's gated_nodes. */
  contracts: Record<string, ProfileContract>;
  /** "<state>:<rule_id>@<entity>" -> rule entry, filtered to this pipeline's gated relations. */
  rules: Record<string, ProfileRule>;
  policy: Record<string, unknown>;
  /** authority -> the 4-argument autonomy_tier (RELEASE action class only, PFP :714). */
  tiers: Record<string, string>;
  monitors: ProfileMonitors;
  dependencies: ProfileDependency[];
  /** The GLOBAL pipeline order (harness.plan.order), for context -- not this pipeline's own order. */
  order: string[];
  /** FEEDS routing entries naming this pipeline. */
  routing: ProfileRouting[];
  /** artifact_id -> every version of it, each with its approvers (PFP :718, "who approved each part"). */
  contributors: Record<string, ProfileContributorVersion[]>;
  /** The as_of this was composed under: null ("now") or the trace_id the caller passed. Echoed back, not resolved. */
  as_of: string | null;
  /** sha256 over the whole dict (Registry._h's pattern); equal to the NIGHT root's profile_digests[pipeline_id)
   *  stamped by the SAME function, for the same effective configuration (K1_DESIGN.md §4 step 4). */
  profile_digest: string;
}
