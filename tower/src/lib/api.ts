import { API_BASE, IDP_BASE, MOCK } from "./config";
import { mockApi, mockIdp } from "./mock/server";
import type {
  AbcRow,
  AgentRow,
  AgentState,
  AuditVerify,
  AutonomyRow,
  CdeRow,
  ClassificationRow,
  ExplainerInfo,
  ExplanationRef,
  FeedItem,
  MonitorRow,
  MonitorSeries,
  ParallelRunRow,
  SimNights,
  AnnotationQueueResponse,
  EvalAttestation,
  EvalRun,
  EvalRunDiff,
  SimRunSummary,
  AuditRow,
  BoardEntry,
  CoverageResponse,
  CrDecideResponse,
  CrDetail,
  CrListRow,
  CrProposeBody,
  CrProposeResponse,
  GovState,
  KnownIssueBody,
  LookbackItem,
  LookbackReassessment,
  LookbackVerdict,
  Tenant,
  MeasureRow,
  MeasureSeries,
  MemoryRow,
  RulesResponse,
  Verdict,
  VerdictChallenge,
  ChallengeBody,
  ChallengeResponse,
  ConfirmBody,
  DecideBody,
  DecideResponse,
  DecisionCard,
  Me,
  Pipelines,
  QueueCluster,
  RevealResponse,
  Summary,
  TokenResponse,
} from "./types";

/**
 * Decision error codes carried in FastAPI's envelope as {detail:{status, reason}} (app.py):
 * 428 STEP_UP_REQUIRED · 401 STEP_UP_INVALID · 409 STALE · 409 REPLAY · 403 DENIED.
 */
export type ApiErrorCode = "STEP_UP_REQUIRED" | "STEP_UP_INVALID" | "STALE" | "REPLAY" | "DENIED";

/** Error carrying the HTTP status (and decision code, when present) so callers can branch on it. */
export class ApiError extends Error {
  constructor(
    public status: number,
    public body: unknown,
  ) {
    super(ApiError.describe(status, body));
  }
  /** {detail:{...}} when the server sent a structured detail, else the body itself. */
  private static inner(body: unknown): Record<string, unknown> | null {
    if (!body || typeof body !== "object") return null;
    const b = body as Record<string, unknown>;
    if (b.detail && typeof b.detail === "object") return b.detail as Record<string, unknown>;
    return b;
  }
  static describe(status: number, body: unknown): string {
    const d = ApiError.inner(body);
    if (d) {
      if (typeof d.reason === "string") return `HTTP ${status}${typeof d.status === "string" ? ` ${d.status}` : ""}: ${d.reason}`;
      if (typeof d.detail === "string") return `HTTP ${status}: ${d.detail}`;
      if (d.detail !== undefined) return `HTTP ${status}: ${JSON.stringify(d.detail)}`;
      if (typeof d.status === "string") return `HTTP ${status}: ${d.status}`;
    }
    return `HTTP ${status}`;
  }
  /** The decision code (detail.status) when the server sent one. */
  get code(): ApiErrorCode | null {
    const s = ApiError.inner(this.body)?.status;
    return typeof s === "string" ? (s as ApiErrorCode) : null;
  }
  /** 409 that is not a replay: the unit or pending decision moved; reload the card. */
  get isStale() {
    return this.status === 409 && this.code !== "REPLAY";
  }
  /** 409 REPLAY: the step-up token was already used. Obtain a new challenge and step-up. */
  get isReplay() {
    return this.status === 409 && this.code === "REPLAY";
  }
  /** 428 STEP_UP_REQUIRED or 401 STEP_UP_INVALID: the step-up is missing, invalid or expired. */
  get isStepUpProblem() {
    return this.status === 428 || (this.status === 401 && this.code === "STEP_UP_INVALID");
  }
  /** 401 on the session bearer itself (not a step-up failure): the reviewer must sign in again. */
  get isSessionExpired() {
    return this.status === 401 && this.code !== "STEP_UP_INVALID";
  }
  /** 403: missing duty / scope / audit duty, or a DENIED decision (SoD, coverage, binding). */
  get isForbidden() {
    return this.status === 403;
  }
}

type Method = "GET" | "POST";

/**
 * This tab's own state-changing calls on a unit (decide / confirm / veto / challenge). The card's stale poll must not
 * mistake our own change for someone else's: it ignores a version change while one is in flight or if one finished
 * after the poll's fetch started. Changes made elsewhere still raise the stale banner.
 */
const ownInFlight = new Map<string, number>();
const ownLastEnd = new Map<string, number>();
function unitOf(path: string): string | null {
  const m = path.match(/^\/units\/([^/?]+)\/(decide|confirm|veto|challenge)$/);
  return m ? decodeURIComponent(m[1]!) : null;
}
export function ownUnitChangeSince(unitKey: string, since: number): boolean {
  return (ownInFlight.get(unitKey) ?? 0) > 0 || (ownLastEnd.get(unitKey) ?? 0) >= since;
}

export async function call<T>(token: string | null, method: Method, path: string, body?: unknown): Promise<T> {
  const own = method === "POST" ? unitOf(path) : null;
  if (!own) return callRaw<T>(token, method, path, body);
  ownInFlight.set(own, (ownInFlight.get(own) ?? 0) + 1);
  try {
    return await callRaw<T>(token, method, path, body);
  } finally {
    ownInFlight.set(own, (ownInFlight.get(own) ?? 1) - 1);
    ownLastEnd.set(own, Date.now());
  }
}

async function callRaw<T>(token: string | null, method: Method, path: string, body?: unknown): Promise<T> {
  if (MOCK) {
    const r = await mockApi(token, method, path, body);
    if (r.status >= 400) throw new ApiError(r.status, r.json);
    return r.json as T;
  }
  const headers: Record<string, string> = { Accept: "application/json" };
  if (token) headers.Authorization = `Bearer ${token}`;
  if (body !== undefined) headers["Content-Type"] = "application/json";
  let res: Response;
  try {
    res = await fetch(`${API_BASE}${path}`, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
      cache: "no-store",
      credentials: "omit",
    });
  } catch (e) {
    throw new ApiError(0, { detail: `Network error reaching ${API_BASE}: ${(e as Error).message}` });
  }
  let json: unknown = null;
  const text = await res.text();
  if (text) {
    try {
      json = JSON.parse(text);
    } catch {
      json = { detail: text.slice(0, 500) };
    }
  }
  if (!res.ok) throw new ApiError(res.status, json);
  return json as T;
}

const u = (k: string) => `/units/${encodeURIComponent(k)}`;
const cr = (id: string) => `/governance/change-requests/${encodeURIComponent(id)}`;

export function makeClient(token: string | null) {
  return {
    me: () => call<Me>(token, "GET", "/me"),
    summary: () => call<Summary>(token, "GET", "/summary"),
    queue: () => call<QueueCluster[]>(token, "GET", "/queue"),
    card: (k: string) => call<DecisionCard>(token, "GET", u(k)),
    decide: (k: string, b: DecideBody) => call<DecideResponse>(token, "POST", `${u(k)}/decide`, b),
    // Maker: {decision, quarantine_row_seqs}. Checker: {} (the server binds the pending maker decision).
    challenge: (k: string, b: ChallengeBody) => call<ChallengeResponse>(token, "POST", `${u(k)}/challenge`, b),
    // Session token as bearer (reads/auth only); the challenge-bound step-up token travels in the body.
    confirm: (k: string, b: ConfirmBody) => call<DecideResponse>(token, "POST", `${u(k)}/confirm`, b),
    reveal: (k: string, purpose: string) => call<RevealResponse>(token, "POST", `${u(k)}/reveal`, { purpose }),
    pipelines: () => call<Pipelines>(token, "GET", "/pipelines"),
    abc: (batchId: string) =>
      call<AbcRow[]>(token, "GET", `/abc${batchId ? `?batch_id=${encodeURIComponent(batchId)}` : ""}`),
    audit: (limit: number) => call<AuditRow[]>(token, "GET", `/audit?limit=${limit}`),

    // Part D governance. 403 = author / no governance duty · 409 = STALE or REPLAY · 428 = step-up missing.
    govState: () => call<GovState>(token, "GET", "/governance/state"),
    crList: (status?: string) =>
      call<CrListRow[]>(token, "GET", `/governance/change-requests${status ? `?status=${encodeURIComponent(status)}` : ""}`),
    crGet: (id: string) => call<CrDetail>(token, "GET", `${cr(id)}`),
    crPropose: (b: CrProposeBody) => call<CrProposeResponse>(token, "POST", "/governance/change-requests", b),
    crChallenge: (id: string, verdict: Verdict, explanation_ref?: ExplanationRef) =>
      call<VerdictChallenge>(token, "POST", `${cr(id)}/challenge`, { verdict, ...(explanation_ref ? { explanation_ref } : {}) }),
    crDecide: (id: string, verdict: Verdict, step_up_token: string) =>
      call<CrDecideResponse>(token, "POST", `${cr(id)}/decide`, { verdict, step_up_token }),

    rules: () => call<RulesResponse>(token, "GET", "/rules"),
    coverage: () => call<CoverageResponse>(token, "GET", "/coverage"),

    agentState: () => call<AgentState>(token, "GET", "/agent/state"),
    blackboard: (batchId: string) =>
      call<BoardEntry[]>(token, "GET", `/agent/blackboard${batchId ? `?batch_id=${encodeURIComponent(batchId)}` : ""}`),
    memory: () => call<MemoryRow[]>(token, "GET", "/agent/memory"),
    kiPropose: (b: KnownIssueBody) => call<{ memory_id: number; status: string }>(token, "POST", "/agent/memory/known-issues", b),
    kiChallenge: (id: number, verdict: Verdict) => call<VerdictChallenge>(token, "POST", `/agent/memory/${id}/challenge`, { verdict }),
    kiDecide: (id: number, verdict: Verdict, step_up_token: string) =>
      call<{ memory_id: number; status: string }>(token, "POST", `/agent/memory/${id}/decide`, { verdict, step_up_token }),

    /** Public: no bearer needed. */
    tenant: () => call<Tenant>(null, "GET", "/tenant"),

    lookback: () => call<LookbackItem[]>(token, "GET", "/lookback"),
    lbReassess: (id: number) => call<LookbackReassessment & { item_id: number }>(token, "POST", `/lookback/${id}/reassess`),
    lbChallenge: (id: number, verdict: LookbackVerdict, explanation_ref?: ExplanationRef) =>
      call<VerdictChallenge>(token, "POST", `/lookback/${id}/challenge`, { verdict, ...(explanation_ref ? { explanation_ref } : {}) }),
    // 403 = step-up bound to another item / verdict · 409 = already resolved (or rationale refused).
    lbDecide: (id: number, verdict: LookbackVerdict, rationale: string, step_up_token: string) =>
      call<{ item_id: number; status: string }>(token, "POST", `/lookback/${id}/decide`, { verdict, rationale, step_up_token }),

    // ---- Part E §10 ----
    agents: () => call<AgentRow[]>(token, "GET", "/agents"),
    autonomy: () => call<AutonomyRow[]>(token, "GET", "/autonomy"),
    /** Allowed while the unit is EVALUATING with an open window (database clock); 409 once released. */
    veto: (k: string, reason_code: string, explanation_ref?: ExplanationRef) =>
      call<{ state: string }>(token, "POST", `${u(k)}/veto`, { reason_code, ...(explanation_ref ? { explanation_ref } : {}) }),
    feedItems: () => call<FeedItem[]>(token, "GET", "/feeds/items"),
    feedAck: (id: number, reason_code: string) => call<FeedItem>(token, "POST", `/feeds/items/${id}/ack`, { reason_code }),
    parallelRun: (period?: string) =>
      call<ParallelRunRow[]>(token, "GET", `/parallel-run${period ? `?period=${encodeURIComponent(period)}` : ""}`),
    /** Tower assumption (contract gap): the step-up nonce for LEGACY_DEFECT / DEFINITION comes from this challenge. */
    prChallenge: (rowId: number, explain_code: string, explanation_ref?: ExplanationRef) =>
      call<VerdictChallenge>(token, "POST", `/parallel-run/${rowId}/challenge`, { explain_code, ...(explanation_ref ? { explanation_ref } : {}) }),
    prExplain: (rowId: number, explain_code: string, step_up_token?: string, explanation_ref?: ExplanationRef) =>
      call<ParallelRunRow>(token, "POST", `/parallel-run/${rowId}/explain`, {
        explain_code,
        ...(step_up_token ? { step_up_token } : {}),
        ...(explanation_ref ? { explanation_ref } : {}),
      }),
    cde: () => call<CoverageResponse & { cde: CdeRow[] }>(token, "GET", "/coverage?cde=true"),
    classification: () => call<ClassificationRow[]>(token, "GET", "/classification"),
    monitor: () => call<MonitorRow[]>(token, "GET", "/monitor"),
    monitorSeries: (relation: string, metric: string, key?: string | null) =>
      call<MonitorSeries>(
        token,
        "GET",
        `/monitor/series?relation=${encodeURIComponent(relation)}&metric=${encodeURIComponent(metric)}${key ? `&key=${encodeURIComponent(key)}` : ""}`,
      ),
    auditVerify: () => call<AuditVerify>(token, "GET", "/audit/verify"),
    explainer: () => call<ExplainerInfo>(token, "GET", "/explainer"),
    /**
     * K0 / B9: recorded `agent_eval` / `explainer` runs, newest first, for the attestation proposal picker below.
     * `suite` filters to one suite; `limit` caps the page (server default 50, max 200). NOT the E-13 pending-
     * attestations picker (that is `GET /eval-attestations?status=PROPOSED`, deferred to K11).
     */
    listEvals: (suite?: string, limit?: number) =>
      call<EvalRun[]>(
        token,
        "GET",
        `/evals${suite || limit ? "?" : ""}${[suite ? `suite=${encodeURIComponent(suite)}` : null, limit ? `limit=${limit}` : null]
          .filter(Boolean)
          .join("&")}`,
      ),
    /**
     * R4-5 5-arg contract: a governance person proposes {label, code_sha256, det_run_id, llm_run_id?}, naming
     * RECORDED eval runs -- the database copies their junit hashes, tests, failures and prompt version. A SECOND
     * governance person (approver org) confirms; only a confirmed attestation is promotion evidence (it never
     * resets the track record, DEC-024/R4-5).
     */
    evalAttest: (b: { label: string; code_sha256: string; det_run_id: string; llm_run_id?: string }) =>
      call<EvalAttestation>(token, "POST", "/eval-attestations", b),
    evalConfirm: (id: number) => call<EvalAttestation>(token, "POST", `/eval-attestations/${id}/confirm`),
    /** K11 (PARTF_K7_K12_MOCK_CONTRACT.md). PRE-BACKEND: an INVENTED route; no real counterpart exists yet. */
    evalRunDiff: (a: string, b: string) =>
      call<EvalRunDiff>(token, "GET", `/eval-runs/diff?a=${encodeURIComponent(a)}&b=${encodeURIComponent(b)}`),

    // §10b nightly-run simulator: 404 outside demo mode; run-next needs the operations duty, 409 when nothing is queued.
    simNights: () => call<SimNights>(token, "GET", "/sim/nights"),
    simRunNext: () => call<SimRunSummary>(token, "POST", "/sim/run-next"),
    simRuns: () => call<SimRunSummary[]>(token, "GET", "/sim/runs"),
    simRun: (id: string) => call<SimRunSummary>(token, "GET", `/sim/runs/${encodeURIComponent(id)}`),

    /** K11 (PARTF_K7_K12_MOCK_CONTRACT.md). PRE-BACKEND: an INVENTED route; no real counterpart exists yet. */
    annotationQueue: () => call<AnnotationQueueResponse>(token, "GET", "/annotation-queue"),

    measures: (batchId?: string) =>
      call<MeasureRow[]>(token, "GET", `/measures${batchId ? `?batch_id=${encodeURIComponent(batchId)}` : ""}`),
    series: (ruleId: string, node?: string) =>
      call<MeasureSeries>(
        token,
        "GET",
        `/measures/series?rule_id=${encodeURIComponent(ruleId)}${node ? `&node=${encodeURIComponent(node)}` : ""}`,
      ),
  };
}
export type Client = ReturnType<typeof makeClient>;

/** T1 local IdP. Tokens are returned to the caller and held in React memory only. */
export async function idpToken(
  username: string,
  stepUp?: { challenge: string; url?: string },
): Promise<string> {
  const payload = stepUp ? { username, step_up: true, challenge: stepUp.challenge } : { username };
  if (MOCK) {
    const r = await mockIdp(payload);
    if (r.status >= 400) throw new ApiError(r.status, r.json);
    return (r.json as TokenResponse).access_token;
  }
  const url = stepUp?.url || `${IDP_BASE}/token`;
  let res: Response;
  try {
    res = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify(payload),
      cache: "no-store",
      credentials: "omit",
    });
  } catch (e) {
    throw new ApiError(0, { detail: `Network error reaching IdP ${url}: ${(e as Error).message}` });
  }
  const json = (await res.json().catch(() => null)) as TokenResponse | null;
  if (!res.ok || !json || typeof json.access_token !== "string") throw new ApiError(res.status, json);
  return json.access_token;
}
