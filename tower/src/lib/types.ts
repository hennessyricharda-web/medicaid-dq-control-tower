/**
 * Types for the control-plane API, transcribed from docs/W1_SPINE.md §3 (FROZEN for W1).
 *
 * Rule: every field named here without an `x_` prefix appears verbatim in the spine.
 * Anything the spine does not define is either typed `unknown` (and rendered generically)
 * or carries an `x_` prefix and is listed in the Tower's contract-ambiguity notes.
 */

// GET /me
export interface Me {
  person_id: string;
  name: string;
  duties: string[];
  authorities: string[];
  /** Tenant profile: the person's organisation code (e.g. STATE | VENDOR) and its label; null when unknown. */
  org?: string | null;
  org_label?: string | null;
}

// GET /tenant (public, no auth). Values come from the tenant profile at runtime, never from the repository.
export interface Tenant {
  display_name: string | null;
  agency: string | null;
  division: string | null;
  organisations: Record<string, string> | null;
  /** Organisation code whose people must approve configuration changes; null = no organisation rule. */
  approver_org: string | null;
  source?: string | null;
}

// ---------------------------------------------------------------------------------------------------
// Part E (PARTE_DESIGN.md §7): every explanation is plain language over control-plane facts only.
// ---------------------------------------------------------------------------------------------------
export interface Explanation {
  text: string;
  /** "template" (built-in, offline, deterministic) or a provider key. */
  source: string;
  /** TEMPLATE = the deterministic template, generated only from facts (not fact-checked); PASS = a provider's text
   *  passed the structured check; FALLBACK = the provider's text failed it, so the template text is shown instead. */
  fact_check: "TEMPLATE" | "PASS" | "FALLBACK" | string;
  facts_hash: string;
  text_hash: string;
}
/** Sent with every decision's challenge so the decision records the explanation that was shown. */
export interface ExplanationRef {
  facts_hash: string;
  text_hash: string;
}
export type Tier = "L1" | "L2" | "L3" | "L4";
export interface VetoWindow {
  opened_at: string;
  /** Computed by the database clock. */
  deadline: string;
  remaining_s: number;
}

// GET /summary
export interface Summary {
  /** Part E: NOT_RATIFIED | TEST_GENESIS | TEST_GENESIS_PLUS | RATIFIED; the Tower alerts unless RATIFIED. */
  genesis_state?: string;
  genesis_label?: string;
  /** Age of the oldest HELD unit, minutes. */
  oldest_held_age_min?: number | null;
  /** SLA remaining for the oldest-due HELD unit, minutes; negative = overdue. */
  oldest_sla_remaining_min?: number | null;
  /** F-16 (app.py explainer_view): label, source_mode and transport are additive; source_mode null = unknown. */
  explainer?: { provider: string; decisions: string; label?: string; source_mode?: string | null; transport?: string | null; x_transport_refused?: string | null };
  /** §10b nightly-run simulator (demo mode only); absent or enabled=false outside demo mode. */
  sim?: { enabled: boolean; current_date: string | null; queued: number };
  /** app.py deployment_view(): the control plane's DERIVED binding (dqagent/deploy.py `bound`/`label`), never the
   *  runtime's own claim. Non-sensitive (R4-2). null on a server with no `deployment_info` (e.g. an older build). */
  deployment?: {
    deployment: string;
    idp_kind: string | null;
    genesis_kind: string | null;
    bound: boolean;
    label: string;
  } | null;
  /** Pre-Part E label (kept for older servers). */
  genesis_status: string;
  held_units: number;
  decisions_due: number;
  oldest_sla_minutes: number | null;
  mine: number;
  /** Part E fix: out_of_balance counts GATING failures only; advisory (non-gating) failures are counted separately. */
  abc: { balanced: number; out_of_balance: number; advisory?: number };
  deny_propagation_lag_s: number;
  pipeline_order_ok: boolean;
  llm_provider: string;
  /** Part D: digest of the compiled effective ruleset (null until genesis is ratified). */
  ruleset_version?: string | null;
  /** Part D: set when the catalog / IR code on disk differs from the approved CATALOG digest. */
  drift?: string | null;
  awaiting_checker?: number;
}

// GET /queue
export interface QueueCluster {
  cluster_id: string;
  authority: string;
  rule_ids: string[];
  node: string;
  failure_signature: string;
  unit_count: number;
  worst_disposition: string;
  required_mode: string;
  sla_due: string;
  units: string[];
  /** Part E: the tier ceiling of the acting agent for this cluster's authority. */
  tier?: Tier;
  /** Part E: earliest veto deadline among the cluster's units (database clock). */
  veto_deadline?: string | null;
}

export interface Disposition {
  rule_id: string;
  rule_hash: string;
  check_class: string;
  priority: string;
  disposition: string;
  evaluated: number;
  failed: number;
  metric: number | string | null;
  /** F-29/F-30: from the compiled rule; may be a number, a string, an object such as {max: 0.01}, or null. */
  threshold: number | string | Record<string, unknown> | null;
  sample_tokens: string[];
  explanation?: Explanation;
}

export interface Dependency {
  domain: string;
  policy: string;
  state: string;
  /** app.py returns the dependency's unit_key. */
  unit?: string;
}

export interface ProposedAction {
  verb: string;
  /** Spine leaves `params` untyped. The Tower only reads `x_row_seqs` from it (see ambiguity A4). */
  params: Record<string, unknown> & { x_row_seqs?: number[] };
  /** F-07/F-30: null when there is no quarantine proposal (x_dry_run_reason says why); chains may be null ("not computed"). */
  dry_run: { rows_quarantined: number; chains: number | null; x_chains_reason?: string; x_proposals?: { decision_id: string; qset_hash: string }[] } | null;
  x_dry_run_reason?: string | null;
}

export interface LlmHypothesis {
  provider: string;
  category: string;
  cited_rules: string[];
  confidence_band: string;
}

/**
 * x_ extension (ambiguity A1): /confirm needs `decision_id`, but the card carries no
 * pending decision, so a checker in a separate session cannot learn it from the spine.
 */
export interface XPendingDecision {
  decision_id: string;
  decision: DecisionVerb;
  rationale: string;
  maker_person_id: string;
  quarantine_row_seqs: number[];
}

// GET /units/{unit_key}
export interface DecisionCard {
  unit_key: string;
  batch_id: string;
  pipeline: string;
  node: string;
  program: string;
  authorities: string[];
  state: string;
  state_version: number;
  fingerprint: string;
  fp_epoch: number | string;
  required_mode: string;
  /** app.py GET /units/{k}: the HELD event's mode trace, rendered as "Why this is held" (SCP-W1-10). */
  mode_trace: string[];
  /** app.py: one entry per decision recorded this round (maker and checker seats). */
  approvals: { required: number; have: number; makers: Approver[] };
  dispositions: Disposition[];
  /** Spine: `abc_before_after:[...]` — element shape not defined (ambiguity A3). Rendered generically. */
  abc_before_after: Record<string, unknown>[];
  dependencies: Dependency[];
  proposed_actions: ProposedAction[];
  llm_hypothesis: LlmHypothesis | null;
  allowed_decisions: string[];
  /** F-30: null when the unit was never held or the policy has no SLA for its mode (x_sla_reason says which). */
  sla_due: string | null;
  x_sla_reason?: string | null;
  x_pending_decision?: XPendingDecision | null;
  /** Part E */
  explanation?: Explanation;
  tier?: Tier;
  veto_window?: VetoWindow | null;
}

export interface Approver {
  person_id: string;
  seat: Seat;
}

export type Seat = "MAKER" | "CHECKER";

export const DECISION_VERBS = ["RELEASE", "REJECT", "HOLD", "QUARANTINE_AND_RELEASE"] as const;
export type DecisionVerb = (typeof DECISION_VERBS)[number];

/** Verbs that change state. Each needs a challenge-bound step-up (SEC-W1-4/10); HOLD does not. */
export type SteppedVerb = Exclude<DecisionVerb, "HOLD">;

// POST /units/{unit_key}/decide
export type DecideBody =
  | {
      decision: SteppedVerb;
      quarantine_row_seqs: number[];
      rationale: string;
      expected_state_version: number;
      bound_fingerprint: string;
      step_up_token: string;
    }
  | {
      /** HOLD is a no-op decide with no step-up. */
      decision: "HOLD";
      quarantine_row_seqs: number[];
      rationale: string;
      expected_state_version: number;
      bound_fingerprint: string;
    };

/**
 * Returned by /decide and /confirm. STALE / DENIED / REPLAY / STEP_UP_* arrive as HTTP errors
 * ({detail:{status,reason}}), never in a 200 body.
 */
export interface DecideResponse {
  /** null for HOLD (no decision is recorded). */
  decision_id: string | null;
  status: "AWAITING_CHECKER" | "AWAITING_COVERAGE" | "APPLIED";
  reason: string;
  /** Authorities whose duty no approver has covered yet (e.g. AUTH_INTAKE). Absent for HOLD. */
  x_missing_duties?: string[];
}

// POST /units/{unit_key}/challenge
/** Maker body binds the verb and quarantine set; checker body is {} (the server binds the pending maker decision). */
export type ChallengeBody =
  | { decision: SteppedVerb; quarantine_row_seqs: number[]; explanation_ref?: ExplanationRef }
  | { explanation_ref?: ExplanationRef }
  | Record<string, never>;

export interface ChallengeResponse {
  challenge: string;
  step_up_url: string;
  /** Which seat the server bound the challenge to: CHECKER whenever a maker decision is pending. */
  x_seat: Seat;
}

// POST /units/{unit_key}/confirm
export interface ConfirmBody {
  /** Must equal the pending maker decision id, else 409 STALE. */
  decision_id: string;
  step_up_token: string;
  /** Optional on the server (it substitutes a default); the Tower sends it only when the checker typed one. */
  rationale?: string;
  expected_state_version?: number;
}

// POST /units/{unit_key}/reveal
export interface RevealResponse {
  reveal_ref: string;
  expires_at: string;
}

// GET /pipelines
export interface Pipelines {
  pipelines: {
    id: string;
    platform: string;
    orchestrator: string;
    domains: string[];
    gated_nodes: { node: string; state: string }[];
  }[];
  order: string[];
  dependencies: { domain: string; depends_on: string; policy: string; temporal_binding: string }[];
}

// GET /abc?batch_id=
export interface AbcRow {
  hop: string;
  measure: string;
  program: string;
  submitter_id: string;
  src_in: number;
  tgt_out: number;
  rejected: number;
  quarantined: number;
  deduplicated: number;
  filtered: number;
  held: number;
  residual: number;
  disposition: string;
}

// GET /audit?limit= — spine says only "read-only list" (ambiguity A6).
export type AuditRow = Record<string, unknown>;

// IdP (T1)
export interface TokenResponse {
  access_token: string;
}

// ---------------------------------------------------------------------------------------------------
// Part D (PARTD_DESIGN.md §5), shapes transcribed from dqagent/api/app.py.
// ---------------------------------------------------------------------------------------------------

/** POST …/challenge for change requests and known issues: no seat (the verdict is bound instead). */
export interface VerdictChallenge {
  challenge: string;
  step_up_url: string;
}
export type Verdict = "APPROVED" | "REJECTED";

// GET /governance/state
export interface GovArtifact {
  artifact_id: string;
  kind: string;
  version: number;
  op: string;
  content_hash: string;
  cr_id: string;
  at: string;
  approved_by: string | null;
}
export interface GovState {
  state: string;
  ratified: boolean;
  ruleset_version: string | null;
  drift: string | null;
  artifacts: GovArtifact[];
}

export type CrOrigin = "HUMAN" | "PROPOSER" | "GENESIS";
/** app.py _cr_row: terminal event (APPLIED / REJECTED / STALE / WITHDRAWN) or AWAITING_APPROVAL / APPROVED. */
export interface CrRow {
  cr_id: string;
  state: string;
  author: string;
  origin: CrOrigin | string;
  loosening: boolean;
  approvals: number;
  approvals_required: number;
  status: string;
  at: string;
  rationale: string;
}
export interface CrListRow extends CrRow {
  items: { artifact_id: string; kind: string; op: string }[];
  impact_summary: { changed?: number; rules_total?: number; loosening_reasons: string[] };
}
export interface RuleFacts {
  hash: string;
  status: string;
  gating: string | null;
  relation: string | null;
  kind: string;
}
export interface CrImpact {
  /** Part E: AUTONOMY changes carry the track-record snapshot approvers sign. K9 (ORCH-1 step b): the SAME
   *  snapshot also carries `task_gate` -- {artifact_id: has_evidence} for every TASK_GATE item in this proposal,
   *  a NEW additive key alongside `rows`/`minimums`/`evidence`, never folded into those. */
  track_record?: TrackRecord & { hash?: string; task_gate?: Record<string, boolean> };
  explanation?: Explanation;
  rules: { rule: string; before: RuleFacts | null; after: RuleFacts | null }[];
  loosening_reasons: string[];
  ruleset_before?: string | null;
  ruleset_after?: string | null;
  summary?: { changed?: number; rules_total?: number };
}
export interface CrItem {
  artifact_id: string;
  kind: string;
  op: "PUT" | "DELETE" | string;
  content: unknown;
  content_hash?: string;
  /** The effective content of the artifact now (null when the artifact is new). */
  current: unknown;
}
export interface CrEvent {
  event: string;
  person_id: string | null;
  detail: unknown;
  at: string;
}
export interface CrDetail extends CrRow {
  explanation?: Explanation;
  items: CrItem[];
  impact: CrImpact;
  base: unknown;
  events: CrEvent[];
  x_you_are_author: boolean;
}
export interface CrProposeBody {
  state?: string;
  rationale: string;
  items: { artifact_id: string; kind: string; op: string; content?: unknown }[];
}
export interface CrProposeResponse {
  cr_id: string;
  impact: CrImpact;
  loosening: boolean;
  approvals_required: number;
}
export interface CrDecideResponse {
  cr_id: string;
  status: string;
}

// GET /rules (rules.model.describe + approved_by_cr)
export interface RuleOverlay {
  verb: string;
  status: "ACTIVE" | "EXPIRED" | string | null;
  expires: string | null;
  approved_ref: string | null;
}
export interface RuleRow {
  id: string;
  pack: string;
  entity: string;
  kind: string;
  status: string;
  reason: string;
  effective_hash: string;
  relation: string | null;
  title: string | null;
  priority: string | null;
  stage: string | null;
  dimension: string | null;
  req_ids: string[] | null;
  analog_of: string | null;
  gating: string | null;
  overlay: RuleOverlay | null;
  /** Part E: sensitivity classes of the elements this rule binds (identity, clinical, payment, program, coverage, part2, none). */
  classification?: string[];
  origin: string | null;
  approved_by_cr: string | null;
}
export interface RulesResponse {
  state: string;
  ruleset_version: string | null;
  drift: string | null;
  ratified: boolean;
  rules: RuleRow[];
}

// GET /coverage
export interface BasicCell {
  /** EXERCISED | DECLARED_NOT_EXERCISED | NOT_ACTIVE | EMPTY (rules/basic.py basic_coverage). */
  status: string;
  active?: number;
  not_applicable?: number;
  other?: number;
  /** "RULE_ID@entity:STATUS" */
  rules?: string[];
}
export interface CoverageResponse {
  requirements: {
    req_id: string;
    domain: string;
    stage: string;
    dimension: string;
    priority: string;
    source: string;
    requirement_status: string | null;
    coverage: string;
    rules: string[];
  }[];
  /** "domain|stage" → {coverage state → count} */
  summary: Record<string, Record<string, number>>;
  basic: {
    matrix: Record<string, Record<string, BasicCell>>;
    summary: Record<string, number>;
  };
}

// GET /agent/state, /agent/blackboard, /agent/memory
export interface AgentStateRow {
  batch_id: string;
  node: string;
  units: number;
  released: number;
  held: number;
  pending: number;
  in_flight: number;
  board_entries: number;
  hypotheses: number;
}
export interface AgentState {
  llm_provider: string;
  batches: AgentStateRow[];
}
export interface BoardEntry {
  seq: number;
  batch_id: string | null;
  authority: string;
  seat: string;
  person_id: string | null;
  kind: string;
  subject_type: string;
  subject_id: string;
  template: string;
  body: Record<string, unknown>;
  refs: number[] | null;
  prev_hash: string;
  entry_hash: string;
  at: string;
  text: string;
}
export interface MemoryRow {
  memory_id: number;
  kind: "BASELINE" | "KNOWN_ISSUE" | string;
  scope_type: string;
  scope_id: string;
  authority: string | null;
  content: Record<string, unknown>;
  content_hash: string;
  created_seat: string;
  proposed_by: string | null;
  approved_by: string | null;
  expires_at: string | null;
  status: string;
  at: string;
}
export interface KnownIssueBody {
  rule_id: string;
  authority: string;
  reason_code: string;
  /** ISO date (YYYY-MM-DD) */
  expires: string;
}

// GET /measures, /measures/series
export interface Thresholds {
  min?: number | null;
  max?: number | null;
  [k: string]: unknown;
}
export interface MeasureRow {
  batch_id: string;
  node: string;
  rule_id: string;
  numerator: number | null;
  denominator: number | null;
  value: number | null;
  thresholds: Thresholds | null;
  disposition: string;
  at: string;
  /** server-side small-cell suppression: counts and value are withheld */
  suppressed?: boolean;
}
export interface SeriesPoint {
  batch_id: string;
  node: string;
  disposition: string;
  long_disposition: string | null;
  suppressed: boolean;
  value: number | null;
  denominator: number | null;
}
export interface MeasureSeries {
  rule_id: string;
  thresholds: Thresholds | Record<string, never>;
  baseline_median: number | null;
  points: SeriesPoint[];
}

// GET /lookback — releases made under a rule version that was later revoked.
export interface LookbackItem {
  item_id: number;
  cr_id: string;
  unit_key: string;
  batch_id: string;
  node: string;
  authority: string;
  rule_artifact: string;
  rule_hash: string;
  released_at: string | null;
  /** Part E: computed live from the current manifest. */
  in_live_manifest: boolean;
  /** Part E: the snapshot taken when the item opened. */
  in_live_manifest_at_open?: boolean;
  explanation?: Explanation;
  at: string;
  resolution: string | null;
  reassessment: LookbackReassessment | null;
  /** OPEN | ACCEPTED | WITHDRAWN */
  status: string;
}
export interface LookbackReassessment {
  /** Reassessed only against approved current versions. `version` = the failing rule's version (12 hex). */
  failing_rules: { rule_id: string; failed: number; evaluated: number; version?: string; version_approved?: boolean }[];
  ruleset_version: string | null;
  /** The approved version that replaced the revoked one (12 hex); null when none compiles today. */
  replacement_version?: string | null;
}
export type LookbackVerdict = "ACCEPTED" | "WITHDRAWN";

// ---- Part E §10 ----------------------------------------------------------------------------------------
export interface Entitlement {
  action_class: string;
  authorities: string[];
  not_before: string | null;
  not_after: string | null;
  /** now() between not_before and not_after, by the database clock. */
  live: boolean;
}
export interface AgentRow {
  agent_id: string;
  name: string;
  version: string;
  /** REGISTERED | ACTIVE | SUSPENDED */
  status: string;
  seats: string[];
  entitlements: Entitlement[];
  cr_id: string | null;
}
/** The per-code-version slice of a track record (dq_ctl.track_record_by_version / the `current_version` block). */
export interface TrackRecordVersion {
  code_sha256: string | null;
  autonomous: number;
  released_after_window: number;
  human_released: number;
  human_decisions: number;
  vetoes: number;
  overrides: number;
  denies: number;
  lookback_withdrawn: number;
}
export interface TrackRecord {
  /** R4-5: the sum over EVERY code version ever (continuous; never reset). */
  autonomous: number;
  h_on_released: number;
  vetoes: number;
  overrides: number;
  denies: number;
  lookback_withdrawn: number;
  human_released?: number;
  human_decisions?: number;
  /** When the current TIER began (shown for context only; it is never used to reset a count, R4-5/DEC-024). */
  since?: string | null;
  /** The latest CONFIRMED (two-person) eval attestation; `at` is when it was confirmed. It is evidence for its
   *  code version; it never resets this track record (R4-5/DEC-024). */
  eval_attestation?: {
    label: string;
    code_sha256: string;
    tests?: number;
    failures: number;
    at?: string | null;
    proposed_by?: string | null;
    confirmed_by?: string | null;
    halves?: { deterministic: boolean; llm: boolean };
  } | null;
  /** R4-5: the RUNNING code version's own slice -- this is what a promotion minimum is checked against. */
  current_version?: TrackRecordVersion;
  /** R4-5: one entry per code version this record has ever seen, oldest first. */
  versions?: TrackRecordVersion[];
  /** API-compatibility name for "every earlier code version's total" (kept from before R4-5; nothing resets). */
  prior?: {
    autonomous: number;
    h_on_released: number;
    vetoes: number;
    overrides: number;
    denies: number;
    lookback_withdrawn: number;
    since: string | null;
    until: string | null;
  } | null;
}
export interface Demotion {
  at: string;
  from_tier: Tier;
  to_tier: Tier;
  reason_code: string;
  ref: string | null;
  explanation?: Explanation;
}
/**
 * K0 / B7: dq_ctl.evidence_status() for the RUNNING code version, state-wide (identical on every /autonomy row).
 * `complete` is deployment-conditional: test/demo need only `deterministic`; pilot/production need `llm` too.
 */
export interface EvidenceStatus {
  code_sha256: string | null;
  prompt_version: string | null;
  deployment: string;
  deterministic: boolean;
  llm: boolean;
  llm_required: boolean;
  complete: boolean;
}
/** A single tier's governed minimums (dq_ctl.DEFAULT_PROMOTION_MINIMUMS shape, or the state's POLICY override). */
export type PromotionMinimumsTier = Record<string, number>;
/** K0 / B7: dq_ctl.promotion_minimums(state), state-wide (identical on every /autonomy row). */
export interface PromotionMinimums {
  label?: string;
  L2?: PromotionMinimumsTier;
  L3?: PromotionMinimumsTier;
  L4?: PromotionMinimumsTier;
}
export interface AutonomyRow {
  agent_id: string;
  /** RELEASE | POST_BOARD | PIN_MEMORY */
  action_class: string;
  authority: string;
  tier: Tier;
  ceiling_mode: string;
  cr_id: string | null;
  track_record: TrackRecord;
  track_record_hash: string;
  demotions?: Demotion[];
  explanation?: Explanation;
  /** K0 / B7: the SAME evidence/minimums a promotion change request's impact block would show, state-wide. */
  evidence?: EvidenceStatus;
  minimums?: PromotionMinimums;
}
export interface FeedItem {
  item_id: number;
  feed: string;
  submitter_id: string | null;
  /** EMPTY_DELIVERY | MISSING_DELIVERY | UNROUTED_FILE | RECOVERY_DATA_LOST (governance_schema.py FEED_ITEM_KINDS) */
  kind: string;
  period: string | null;
  expected_by: string | null;
  /** OPEN | ACKED */
  status: string;
  reason_code: string | null;
  acked_by?: string | null;
  at: string;
  explanation?: Explanation;
}
export interface ParallelRunRow {
  row_id: number;
  run_id: string;
  period: string;
  as_of: string;
  relation: string;
  measure: string;
  legacy: number | null;
  new: number | null;
  delta: number | null;
  tolerance: number | null;
  /** MATCH | WITHIN | BREAK */
  status: string;
  explain_code: string | null;
  explanation?: Explanation;
}
export interface CdeRow {
  element: string;
  domain: string;
  completeness: number | null;
  validity: number | null;
  thresholds: { completeness_min: number | null; validity_min: number | null };
  /** MEETS | BELOW | NOT_EXERCISED | NOT_LOADED */
  status: string;
  steward_domain?: string | null;
  explanation?: Explanation;
}
export interface ClassificationRow {
  element: string;
  class: string;
  source: string;
  cr_id: string | null;
}
export interface MonitorRow {
  relation: string;
  metric: string;
  key: string | null;
  /** OK | SIGNAL | WATCH | SHIFT | BASELINE_BUILDING | NOT_COMPUTED | PROVISIONAL (period still open: never SIGNAL) */
  status: string;
  /** PROVISIONAL: the share of the period received so far. */
  note?: string | null;
  received_pct?: number | null;
  n_periods?: number;
  chart?: string;
  period?: string | null;
  /** The metric's current plain-language reading (signal or not). */
  explanation?: Explanation;
  last_signal: { batch_id: string; period?: string; rule: string; direction?: string; explanation?: Explanation } | null;
}
export interface MonitorPoint {
  batch_id: string;
  period?: string | null;
  value: number | null;
  n: number | null;
  /** p-charts: per-period limits. */
  ucl?: number | null;
  lcl?: number | null;
}
export interface MonitorSeries {
  relation: string;
  metric: string;
  key: string | null;
  /** XmR | p | PSI */
  chart: string;
  status: string;
  points: MonitorPoint[];
  cl: number | null;
  ucl: number | null;
  lcl: number | null;
  sigma2: [number, number] | null;
  signals: { batch_id: string; rule: string; explanation?: Explanation }[];
  baseline_ref: string | null;
  mix?: {
    psi: number | null;
    status: string;
    bins?: { category: string; expected: number; actual: number }[];
    /** The element is masked (identity / clinical / part2): category shares are not returned. */
    bins_withheld?: boolean;
  } | null;
}
export interface ChainVerify {
  ok: boolean;
  through_seq: number;
  head_hash: string;
  first_break: number | null;
}
export interface AuditVerify {
  audit: ChainVerify;
  blackboard: ChainVerify;
}
export interface ExplainerInfo {
  provider: string;
  configured: boolean;
  fallback_count: number;
  /** app.py GET /explainer also sends decisions and label (F-16). */
  decisions?: string;
  label?: string;
}

// ---- §10b nightly-run simulator (demo mode; the Tower labels it SIMULATED) -------------------------------
export interface SimNight {
  night_date: string;
  files: string[];
  /** QUEUED | STARTED | RUN | FAILED */
  status: string;
  run_id: string | null;
  /** FAILED: the class of the error that stopped the night. */
  error_class?: string | null;
}
export interface SimNights {
  current_date: string | null;
  nights: SimNight[];
}
/** Tower assumption: every count is {n, ids[]} with typed refs (unit:, feed:, bb:, cr:, lookback:, pr:). */
export interface SimCount {
  n: number;
  ids: string[];
  /** Unit ids are filtered to the caller's scope, so n can exceed ids.length. */
  ids_scoped?: boolean;
}
export interface SimRunSummary {
  run_id: string;
  night_date: string;
  started_at: string;
  finished_at: string | null;
  person_id: string | null;
  files: string[];
  counts: {
    files: SimCount;
    abc_breaks: SimCount & { gating: number; advisory: number };
    units_created: SimCount;
    released_autonomously: SimCount;
    veto_windows_opened: SimCount;
    held: SimCount & { mode_reasons: Record<string, number> };
    pending_dependency: SimCount;
    feed_items: SimCount;
    monitor_signals: SimCount;
    demotions: SimCount;
  };
  explanation?: Explanation;
  /** Unit ids in the counts are filtered to the caller's scope (n can exceed ids). */
  ids_scoped?: boolean;
  simulated?: boolean;
}

/**
 * GET /api/v1/evals (K0 / B9): a recorded `dq_ctl.eval_runs` row, newest first. agent_eval runs are always
 * DETERMINISTIC (the suite has no transport); explainer runs can be LIVE/SCRIPTED/RECORDED/TEMPLATE.
 * A governance person picks a DETERMINISTIC agent_eval run here as the `det_run_id` for POST /eval-attestations
 * (and, optionally, a LIVE explainer run as `llm_run_id`) -- E-13's separate pending-attestations picker is
 * deferred to K11 and is NOT this list.
 */
export interface EvalRun {
  run_id: string;
  /** agent_eval | explainer */
  suite: string;
  /** DETERMINISTIC | LIVE | SCRIPTED | RECORDED | TEMPLATE */
  source_mode: string;
  cases: number;
  passed: number;
  code_sha256: string;
  prompt_version: string | null;
  junit_sha256: string;
  at: string;
}

/**
 * K11 (PARTF_K7_K12_MOCK_CONTRACT.md): the annotation queue. PRE-BACKEND -- an INVENTED route, `GET
 * /annotation-queue`, no real counterpart. `subject` is always a REAL entity key already used elsewhere in this
 * codebase (a decision unit's `unit_key`, or a feed item's `feed:{item_id}`) -- never a fabricated id. `status`
 * is FEEDBACK_RECORDED only when the invented `/explanation-feedback` mechanism (K11's feedback control) has
 * actually recorded something for that subject in this tab; it is never defaulted to recorded.
 */
export interface AnnotationQueueItem {
  subject: string;
  kind: "UNIT" | "FEED_ITEM";
  label: string;
  status: "OPEN" | "FEEDBACK_RECORDED";
  at: string;
}
export interface AnnotationQueueResponse {
  items: AnnotationQueueItem[];
}

/**
 * K11 (PARTF_K7_K12_MOCK_CONTRACT.md): the eval-run diff view. PRE-BACKEND -- no real route exists for this yet;
 * unlike `GET /evals` above (real), this is an INVENTED mock-only mechanism. Same honesty pattern the existing,
 * real `/runs/diff` screen already demonstrates (its own file header, `app/runs/diff/page.tsx`): states what
 * differs, never why, and carries no reason the server itself does not return. `fields` lists only the keys whose
 * value differs between the two named runs; a key absent from `fields` is unchanged.
 */
export interface EvalRunDiff {
  a: EvalRun;
  b: EvalRun;
  fields: string[];
}

/**
 * POST /eval-attestations (PROPOSED, R4-5 5-arg contract: {label, code_sha256, det_run_id, llm_run_id?}) and
 * POST /eval-attestations/{id}/confirm (CONFIRMED). `tests`/`failures`/`junit_sha256` are copied by the database
 * from the named `det_run_id` eval run -- never sent by the caller (the old 6-arg {tests, failures, junit_sha256}
 * shape is refused with 422, "old contract").
 */
export interface EvalAttestation {
  id: number;
  label: string;
  code_sha256: string;
  tests: number;
  failures: number;
  junit_sha256: string;
  person_id: string;
  status: "PROPOSED" | "CONFIRMED" | string;
  confirmed_by: string | null;
  confirmed_at?: string | null;
  /** Which evidence halves this attestation carries: deterministic always true; llm only when llm_run_id was given. */
  halves?: { deterministic: boolean; llm: boolean };
  det_run_id?: string | null;
  llm_run_id?: string | null;
  prompt_version?: string | null;
}
