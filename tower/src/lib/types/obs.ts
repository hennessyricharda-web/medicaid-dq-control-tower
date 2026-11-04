/**
 * Observability response shapes: a field-for-field mirror of dqagent/api/contracts/obs.py (every model there
 * forbids extra keys). Keep the two in step; the mock routes (lib/mock/routes/obs.ts) return exactly these shapes.
 */
export type RunStatus = "STARTED" | "RESUMED" | "COMPLETED" | "FAILED";
export type SourceMode = "TEMPLATE" | "SCRIPTED" | "RECORDED" | "LIVE";
export const RUN_KINDS = ["NIGHT", "PIPELINE_RUN", "REEVALUATE", "PUBLISH_CYCLE", "DECISION", "APPLY_CHANGE", "BACKTEST", "EXPLAIN", "RECOVER"] as const;
export const GUARDRAIL_CODES = [
  "MASKED", "SUPPRESSED", "PART2_WITHHELD", "CHECK_REJECTED", "ADVICE_BLOCKED", "CAUSAL_BLOCKED", "FALLBACK", "FLOOR_APPLIED",
  "CEILING_APPLIED", "ENTITLEMENT_REFUSED", "SCOPE_DENIED", "BODY_REJECTED", "EXPLANATION_MISMATCH", "STEP_UP_REQUIRED", "RECOVERY_REFUSED",
] as const;

export interface RunRow {
  trace_id: string;
  kind: string;
  state: string;
  pipeline_id: string | null;
  night_date: string | null;
  attempt: number;
  status: RunStatus | null;
  started_at: string;
  span_count: number;
  source_mode: string | null;
}
export interface RunList {
  runs: RunRow[];
  next: string | null;
}
export interface RunEvent {
  event: RunStatus;
  error_class: string | null;
  at: string;
}
export interface Run {
  trace_id: string;
  kind: string;
  parent_trace_id: string | null;
  state: string;
  pipeline_id: string | null;
  night_date: string | null;
  attempt: number;
  person_id: string | null;
  config_digest: string;
  code_digest: string;
  deployment: string;
  source_mode: string | null;
  started_at: string;
}
export interface LlmCall {
  call_id: number;
  kind: string;
  provider: string;
  model: string | null;
  transport: string;
  source_mode: SourceMode;
  prompt_version: string;
  check_version: string;
  template_version: string;
  facts_hash: string;
  verdict: "PASS" | "REJECT" | "NOT_CHECKED" | "NO_OUTPUT";
  reason_codes: string[];
  output_text: string | null;
  input_tokens: number | null;
  output_tokens: number | null;
  latency_ms: number | null;
  finish_reason: string | null;
}
export interface Guardrail {
  id: number;
  trace_id: string | null;
  span_id: string | null;
  code: string;
  subject: string;
  seat: string | null;
  /** Counts only (dq_ctl.counts_only): never names or values. */
  detail: Record<string, number>;
  fail_mode: "open" | "closed";
  at: string;
}
export interface ReleaseCheck {
  unit_key: string;
  ok: boolean;
  reasons: string[];
}
/** Span attrs are scalars (number, boolean, short code string) or a counts-only object (span_attrs_ok). */
export type SpanAttr = number | boolean | string | Record<string, number>;
export interface Span {
  span_id: string;
  parent_span_id: string | null;
  ordinal: number;
  name: string;
  seat: string | null;
  subject: string | null;
  status: "OK" | "ERROR" | "SKIPPED";
  started_at: string;
  ended_at: string;
  attrs: Record<string, SpanAttr>;
  refs: string[];
  /** Filled only with ?expand=rows (run detail) and always on unit traces: {release_checks: [...]}. */
  linked: Record<string, ReleaseCheck[]>;
  llm_call: LlmCall | null;
  guardrails: Guardrail[];
}
export interface RunDetail {
  run: Run;
  events: RunEvent[];
  spans: Span[];
  /** 1 when some steps are outside the caller's scope, else 0. Never a count. */
  hidden_out_of_scope: number;
  hidden_label: string | null;
}
export interface UnitTrace {
  unit_key: string;
  spans: Span[];
}
export interface Citation {
  /** {start, end, shown} in the explanation text, or null (template citations carry no text span). */
  span: { start: number; end: number; shown?: string | null } | null;
  fact_key: string;
  evidence_ref: string | null;
  resolvable: boolean;
}
export interface Citations {
  subject: string;
  explanation_ref: { facts_hash: string; text_hash: string };
  label: "generated_from_facts" | "slot";
  source_mode: string;
  citations: Citation[];
}
/**
 * K11 (PARTF_K7_K12_MOCK_CONTRACT.md): the decision-card feedback control. PRE-BACKEND -- an INVENTED route; no
 * real counterpart exists yet. The real mechanism this would eventually feed, `test_feedback_creates_candidate_
 * and_hides_comment`, does not exist in this codebase, so this mock does only ONE thing: records the comment and
 * says so. It never creates an evaluation candidate, never touches any promotion or track-record state, and the
 * response names only `recorded` -- nothing else is claimed.
 */
export interface ExplanationFeedbackBody {
  subject: string;
  /** helpful | unhelpful | wrong (a closed set; never free-text alone -- see the UI's own choices) */
  verdict: "helpful" | "unhelpful" | "wrong";
  comment?: string;
}
export interface ExplanationFeedbackResponse {
  recorded: true;
}
export interface GuardrailList {
  events: Guardrail[];
  next: string | null;
}
export interface OtlpExport {
  resourceSpans: Record<string, unknown>[];
}
/** summary.explainer (app.py explainer_view, F-16): the real explainer status. source_mode is null when unknown. */
export interface ExplainerView {
  provider: string;
  decisions: string;
  label?: string;
  source_mode?: SourceMode | null;
  transport?: string | null;
  x_transport_refused?: string | null;
}

// ------------------------------------------------------------------------------------------------------- F-35 / P2-1
/** GET /llm/usage row: dqagent/api/contracts/obs.py UsageRow, computed server-side (dq_ctl.llm_calls), never in
 *  the browser. tokens_label is set only for TEMPLATE/SCRIPTED/RECORDED ("no model is called..."); null for LIVE. */
export interface UsageRow {
  day: string;
  provider: string;
  transport: string;
  source_mode: string;
  calls: number;
  verdicts: Record<string, number>;
  input_tokens: number;
  output_tokens: number;
  latency_ms_p50: number | null;
  latency_ms_p95: number | null;
  tokens_label: string | null;
}
export interface Usage {
  days: number;
  rows: UsageRow[];
}
/** GET /runs/diff: dqagent/api/contracts/obs.py RunDiff. Outcome deltas only (status, llm verdict, fact-check,
 *  guardrail counts, release-check ok) — never a reason and never a causal field: what differs, not why. */
export interface SpanDelta {
  key: string;
  in_a: boolean;
  in_b: boolean;
  status: { a: string | null; b: string | null };
  verdict: { a: string | null; b: string | null };
  fact_check: { a: string | null; b: string | null };
}
export interface ReleaseCheckDelta {
  unit_key: string;
  a: boolean | null;
  b: boolean | null;
}
export interface RunDiff {
  a: string;
  b: string;
  status: { a: string | null; b: string | null };
  spans_only_in_a: number;
  spans_only_in_b: number;
  span_deltas: SpanDelta[];
  guardrail_deltas: Record<string, { a: number; b: number }>;
  release_check_deltas: ReleaseCheckDelta[];
  hidden_out_of_scope: number;
}
