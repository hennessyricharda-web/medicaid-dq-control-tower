/**
 * In-browser mock of the control-plane API and the T1 IdP. Used only when NEXT_PUBLIC_DQ_MOCK=1.
 * It returns HTTP-shaped {status, json} so the UI exercises the same error paths it would against the
 * real API. It mirrors dqagent/api/app.py (SEC-W1-4/10): every RELEASE / QUARANTINE_AND_RELEASE / REJECT,
 * maker or checker, needs a challenge-bound step-up token; HOLD is a no-op; /audit needs the audit duty;
 * scoped endpoints answer 403 without an effective release scope. State resets on page reload.
 */
import type { Approver, CrDetail, CrListRow, DecisionCard, DecideResponse, Me, MemoryRow, QueueCluster, Seat, Summary } from "../types";
import { RATIONALE_MIN } from "../config";
import { worstOf } from "../dispositions";
import { FEED_ACK_CODES, FEED_ACK_CODES_BY_KIND } from "../partE";
import {
  ABC_ROWS,
  abcRow,
  cnt,
  CLUSTERS,
  INTAKE_FAILED_BATCHES,
  initialCards,
  PIPELINES,
  ROSTER,
  STALE_ON_FIRST_DECIDE,
  chainPost,
  fx,
  initialAgentState,
  initialArtifacts,
  initialBlackboard,
  initialChangeRequests,
  initialCoverage,
  initialMeasures,
  initialLookback,
  initialMemory,
  PEOPLE_ORG,
  revocationFixture,
  explain,
  initialAgents,
  initialAutonomy,
  initialCde,
  initialClassification,
  initialEvalRuns,
  initialFeedItems,
  initialMonitorSeries,
  initialParallelRun,
  monitorRows,
  RULE_CLASSES,
  SIM_NIGHTS,
  SIM_PRELOAD,
  simSummary,
  U4,
  U1,
  TENANT,
  initialRules,
  MOCK_STATE,
  MOCK_CODE_SHA256,
  type MockUser,
} from "./fixtures";
import { REASON_CODES } from "../governance";
import { mockObs } from "./routes/obs";
import { mockProfile } from "./routes/profile";
import { mockCatalog, harnessGates, type GateRec, type WiredPipelineRec } from "./routes/catalog";
import { mockPromoteGate, mockProfileDraft, mockWire } from "./routes/wire";
import { mockBacktest, modePromotionRefusal, seedBacktestFixtures } from "./routes/backtest";
import type { CrEvidenceRow } from "../types/backtest";

interface R {
  status: number;
  json: unknown;
}
const ok = (json: unknown): R => ({ status: 200, json });
const err = (status: number, detail: string): R => ({ status, json: { detail } });
/** app.py decision errors: HTTPException(status, {"status": CODE, "reason": ...}) → {detail:{status, reason}}. */
const coded = (status: number, code: string, reason: string): R => ({ status, json: { detail: { status: code, reason } } });

const STEP_UP_MAX_AGE_MS = 5 * 60_000;
const INTAKE = "AUTH_INTAKE";
const STEPPED = ["RELEASE", "QUARANTINE_AND_RELEASE", "REJECT"] as const;
type Stepped = (typeof STEPPED)[number];
const isStepped = (v: unknown): v is Stepped => typeof v === "string" && (STEPPED as readonly string[]).includes(v);

const cards: Record<string, DecisionCard> = initialCards();
const staleOnce = new Set(STALE_ON_FIRST_DECIDE);
/** Step-up tokens issued by the mock IdP: subject, nonce (= challenge) and auth_time. */
const stepUps = new Map<string, { username: string; challenge: string; issued: number }>();
/** Step-up tokens already spent on a decision (dq_ctl.decision_tokens uniqueness). */
const usedTokens = new Set<string>();
/** Authorities covered so far in the current round, per unit (decisions.authority). */
const covered = new Map<string, Set<string>>();
let decisionSeq = 400;
let auditSeq = 0;
const audit: Record<string, unknown>[] = [];
/** K11 (PARTF_K7_K12_MOCK_CONTRACT.md): the invented decision-card feedback control's in-memory log. Recorded
 *  only -- never read back as evidence, never promoted into any other state. */
const explanationFeedback: { subject: string; verdict: string; comment: string | null; by: string; at: string }[] = [];

function log(actor: string, action: string, unit: string | null, detail: string) {
  auditSeq += 1;
  audit.unshift({
    x_seq: auditSeq,
    x_at: new Date().toISOString(),
    x_actor: actor,
    x_action: action,
    x_unit_key: unit,
    x_detail: detail,
  });
}
log("system", "GENESIS_RATIFIED", null, "test genesis (T1)");
log("engine", "BATCH_SEALED", null, "B-ZZ-20261019-ENC-01");
log("engine", "TRANSITION", "ZZ:MEDICAID:a3f19c:chain=ENC0001907", "EVALUATING -> HELD");
log("P-ZZ-1001", "DECIDE", "ZZ:MEDICAID:a3f19c:chain=ENC0001907", "QUARANTINE_AND_RELEASE -> AWAITING_CHECKER (D-ZZ-000311)");

function hashHex(s: string): string {
  // FNV-1a ×4 lanes. MOCK ONLY — not the DESIGN §2.4 challenge construction.
  let out = "";
  for (let lane = 0; lane < 4; lane++) {
    let h = 2166136261 ^ lane;
    for (let i = 0; i < s.length; i++) h = Math.imul(h ^ s.charCodeAt(i), 16777619) >>> 0;
    out += h.toString(16).padStart(8, "0");
  }
  return out;
}

function userFromToken(token: string | null) {
  if (!token || !token.startsWith("mock.session.")) return null;
  const username = token.slice("mock.session.".length);
  return ROSTER.find((u) => u.username === username) ?? null;
}

const hasRelease = (u: MockUser, auth: string) => u.me.duties.includes(`release:${auth}`);
/** As DecisionService: the unit's authorities, plus AUTH_INTAKE when the batch's intake verdict failed. */
function requiredAuthorities(card: DecisionCard): string[] {
  const s = new Set(card.authorities);
  if (INTAKE_FAILED_BATCHES.has(card.batch_id)) s.add(INTAKE);
  return [...s].sort();
}
// Seed coverage for fixture units that already carry a pending maker decision.
for (const c of Object.values(cards)) {
  const maker = ROSTER.find((u) => u.me.person_id === c.x_pending_decision?.maker_person_id);
  if (maker) covered.set(c.unit_key, new Set(requiredAuthorities(c).filter((a) => hasRelease(maker, a))));
}

/** Visible when the caller's release scope (never AUTH_INTAKE, as app.py) covers the unit's other authorities. */
const visible = (scope: string[], card: DecisionCard) => card.authorities.filter((a) => a !== INTAKE).every((a) => scope.includes(a));

/** Mirrors challenge_for(): binds unit, round, fingerprint, epoch, quarantine set, disposition vector, verb, seat, maker id. */
function challengeFor(card: DecisionCard, verb: string, seat: Seat, makerDecisionId: string | null, qrows: number[]): string {
  const dv = card.dispositions.map((d) => `${d.rule_id}:${d.rule_hash}:${d.disposition}`).sort().join(";");
  const q = [...qrows].sort((a, b) => a - b).join(",");
  return hashHex(
    `${verb}|${seat}|${makerDecisionId ?? ""}|${card.unit_key}|${card.state_version}|${card.fingerprint}|${card.fp_epoch}|${q}|${dv}`,
  );
}

function settle(card: DecisionCard, decision: string) {
  card.state = decision === "REJECT" ? "REJECTED" : "RELEASED";
  card.state_version += 1;
  card.allowed_decisions = [];
  card.x_pending_decision = null;
  card.approvals = { ...card.approvals, have: 0, makers: [] };
  covered.delete(card.unit_key);
}

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

export async function mockIdp(body: { username: string; step_up?: boolean; challenge?: string }): Promise<R> {
  await sleep(120);
  const user = ROSTER.find((u) => u.username === body.username);
  if (!user) return err(401, `unknown user '${body.username}' (mock roster: ${ROSTER.map((u) => u.username).join(", ")})`);
  if (body.step_up) {
    if (!body.challenge) return err(422, "step_up requires challenge");
    const t = `mock.stepup.${hashHex(`${body.username}|${body.challenge}|${Date.now()}|${Math.random()}`)}`;
    stepUps.set(t, { username: body.username, challenge: body.challenge, issued: Date.now() });
    return ok({ access_token: t, token_type: "Bearer" });
  }
  return ok({ access_token: `mock.session.${user.username}`, token_type: "Bearer" });
}

type Verified = { actor: MockUser } | { fail: R };

/** verify(step_up_token, max_age=5 min) and acr check: invalid or expired → 401 STEP_UP_INVALID. */
function verifyStepUp(tok: string): Verified {
  const su = stepUps.get(tok);
  if (!su) return { fail: coded(401, "STEP_UP_INVALID", "step-up token is not a valid step-up authentication") };
  if (Date.now() - su.issued > STEP_UP_MAX_AGE_MS) return { fail: coded(401, "STEP_UP_INVALID", "step-up auth_time is older than 5 minutes") };
  const actor = ROSTER.find((u) => u.username === su.username);
  if (!actor) return { fail: coded(401, "STEP_UP_INVALID", "step-up subject unknown") };
  return { actor };
}

/**
 * Mirrors DecisionService._decide for one unit: CAS (STALE) → duties (DENIED) → challenge binding (DENIED)
 * → token single-use (REPLAY) → separation of duties (DENIED) → record and compute status / missing duties.
 */
function decideOne(
  key: string,
  card: DecisionCard,
  tok: string,
  actor: MockUser,
  seat: Seat,
  decision: Stepped,
  rationale: string,
  expectedVersion: unknown,
  boundFp: unknown,
  qrows: number[],
  makerDecisionId: string | null,
): R {
  if (card.state !== "HELD") return coded(409, "STALE", `${key} is ${card.state}, not HELD`);
  if (staleOnce.has(key)) {
    staleOnce.delete(key);
    card.state_version += 1; // simulate the engine re-evaluating under the reviewer
    log("engine", "TRANSITION", key, `re-evaluated; state_version -> ${card.state_version}`);
    return coded(409, "STALE", `${key} changed (state_version ${card.state_version})`);
  }
  if (expectedVersion !== card.state_version) return coded(409, "STALE", `${key} changed (state_version ${card.state_version})`);
  if (boundFp !== card.fingerprint) return coded(409, "STALE", `${key} evidence changed (fingerprint)`);
  for (const a of card.authorities)
    if (!hasRelease(actor, a)) return coded(403, "DENIED", `missing effective duty release:${a} (token claim AND roster required)`);
  if (stepUps.get(tok)?.challenge !== challengeFor(card, decision, seat, makerDecisionId, qrows))
    return coded(403, "DENIED", "step-up token is not bound to this decision (challenge mismatch)");
  if (usedTokens.has(tok)) return coded(409, "REPLAY", "token or seat already used: decision_tokens_jti_key");
  const prior: Approver[] = card.approvals.makers;
  if (prior.some((m) => m.person_id === actor.me.person_id))
    return coded(403, "DENIED", "separation of duties: the same person cannot approve twice");
  if (seat === "MAKER" && prior.length) return coded(403, "DENIED", "a maker decision already exists; confirm it as checker");

  usedTokens.add(tok); // still verifiable afterwards, so a resend is caught as REPLAY, as in app.py
  const required = requiredAuthorities(card);
  const cov = covered.get(key) ?? new Set<string>();
  for (const a of required) if (hasRelease(actor, a)) cov.add(a);
  covered.set(key, cov);
  const makers = [...prior, { person_id: actor.me.person_id, seat }];
  const have = makers.length;
  const need = Math.max(1, card.approvals.required);
  const missing = required.filter((a) => !cov.has(a));
  decisionSeq += 1;
  const decision_id = `D-ZZ-${String(decisionSeq).padStart(6, "0")}`;
  card.approvals = { ...card.approvals, have, makers };
  if (seat === "MAKER")
    card.x_pending_decision = { decision_id, decision, rationale, maker_person_id: actor.me.person_id, quarantine_row_seqs: qrows };
  let status: DecideResponse["status"] = "AWAITING_CHECKER";
  if (have >= need && missing.length === 0) {
    settle(card, decision);
    status = "APPLIED";
  } else if (have >= need) status = "AWAITING_COVERAGE";
  const resp: DecideResponse = { decision_id, status, reason: `${have}/${need} approvals`, x_missing_duties: missing };
  log(actor.me.person_id, seat === "MAKER" ? "DECIDE" : "CONFIRM", key, `${decision} -> ${status} (${decision_id}); step-up verified`);
  return ok(resp);
}

/** F-16 / F-30: the explainer as app.py explainer_view reports the built-in template (never NullProvider). */
const MOCK_EXPLAINER = {
  provider: "template",
  label: "built-in template (offline)",
  configured: true,
  decisions: "deterministic",
  source_mode: "TEMPLATE",
  transport: "none",
  x_transport_refused: null,
};

/** After-build red team Finding 1 (2026-10-29): matches app.py `deployment_view()`'s real shape and the real
 * `/api/v1/summary` key (`deployment`, not an `x_` field on `/tenant` -- that field never existed server-side).
 * dqagent/deploy.py label("demo", "LOCAL") = "Demo (local IdP)"; matches the mock NIGHT runs' own deployment: "demo". */
const MOCK_DEPLOYMENT = { deployment: "demo", idp_kind: "LOCAL", genesis_kind: "TEST", bound: true, label: "Demo (local IdP)" };

export async function mockApi(token: string | null, method: string, path: string, body: unknown): Promise<R> {
  await sleep(150);
  const url0 = new URL(path, "http://mock");
  if (method === "GET" && url0.pathname === "/tenant") return ok(structuredClone(TENANT)); // public, as app.py
  const user = userFromToken(token);
  if (!user) return err(401, "missing or invalid bearer token");
  const org = PEOPLE_ORG[user.me.person_id] ?? null;
  const me: Me = { ...user.me, org, org_label: org ? (TENANT.organisations?.[org] ?? null) : null };
  const url = new URL(path, "http://mock");
  const p = url.pathname;

  if (method === "GET" && p === "/me") return ok(me);

  // Part F P1-1 observability routes (runs, unit traces, citations, guardrail events); scoped inside, as the API.
  const obs = mockObs(
    { me, cards: Object.values(cards), nights: simRuns, decorated: (c) => decorateCard(structuredClone(c)) },
    method,
    url,
  );
  if (obs) return obs;

  // K1 / Part F P1-2: the per-DAG agent profile, same live-derivation pattern as mockObs above.
  const profile = await mockProfile({ me, pipelines: PIPELINES, rules, artifacts, autonomy, nights: simRuns }, method, url);
  if (profile) return profile;

  // K11 (PARTF_K7_K12_MOCK_CONTRACT.md): the decision-card feedback control. PRE-BACKEND -- an INVENTED route;
  // no real counterpart exists yet (`test_feedback_creates_candidate_and_hides_comment` is real backend logic
  // that does not exist in this codebase). This mock does only ONE thing: records the comment in-memory and says
  // so. It never creates an evaluation candidate and never touches any promotion or track-record state.
  if (method === "POST" && p === "/explanation-feedback") {
    const fb = (body ?? {}) as Record<string, unknown>;
    const subject = typeof fb.subject === "string" ? fb.subject.trim() : "";
    const verdict = fb.verdict;
    if (!subject || (verdict !== "helpful" && verdict !== "unhelpful" && verdict !== "wrong")) {
      return err(422, "subject (string) and verdict (helpful|unhelpful|wrong) required");
    }
    explanationFeedback.push({ subject, verdict, comment: typeof fb.comment === "string" ? fb.comment.trim() : null, by: me.person_id, at: new Date().toISOString() });
    return ok({ recorded: true });
  }

  // K2c: the Catalog table + nodes drawer (live derivation), and the discover preview + wiring wizard (which
  // proposes through the SAME two-person change-request mechanism governance() below already exercises).
  const catalog = mockCatalog({ me, pipelines: PIPELINES, autonomy, wiredPipelines, wiredGates }, method, url);
  if (catalog) return catalog;
  const wire = mockWire(
    { me, pipelines: PIPELINES, autonomy, wiredGates, propose: (items, rationale) => proposeCr(me, items, rationale) },
    method,
    url,
    body,
  );
  if (wire) return wire;
  // F-11b fix: K3's promote-to-block flow, same ctx shape mockWire already uses.
  const promoteGate = mockPromoteGate(
    { me, pipelines: PIPELINES, autonomy, wiredGates, propose: (items, rationale) => proposeCr(me, items, rationale) },
    method,
    url,
    body,
  );
  if (promoteGate) return promoteGate;
  // K8 (PARTF_K7_K12_MOCK_CONTRACT.md): PRE-BACKEND -- no real route exists for this yet. Same ctx shape and
  // same propose mechanism mockWire/mockPromoteGate above already use.
  const profileDraft = mockProfileDraft(
    { me, pipelines: PIPELINES, autonomy, wiredGates, propose: (items, rationale) => proposeCr(me, items, rationale) },
    method,
    url,
    body,
  );
  if (profileDraft) return profileDraft;

  // Part F P1-4 (K3): GET /backtests/{report_id} (dqagent/api/app.py::backtest_detail, :1012-1023). Same
  // governance-duty boundary as /governance/* below (backtest_detail calls gov_duty(p) too).
  if (p.startsWith("/backtests/")) {
    if (!govDuty(me)) return err(403, "governance duty required (token claim AND roster)");
    const bt = mockBacktest({ reports: backtestReports }, method, url);
    if (bt) return bt;
  }

  if (p.startsWith("/governance/")) return governance(me, method, p, url, body);

  if (method === "GET" && p === "/audit") {
    if (!me.duties.includes("audit:AUTH_AUDIT")) return err(403, "audit duty required");
    const limit = Number(url.searchParams.get("limit") || 50);
    return ok(audit.slice(0, Math.max(1, Math.min(500, limit))).map((r) => ({ label: labelFor(String(r.x_action)), ...r })));
  }

  // Every other endpoint is scoped: no effective release duty in any authority → 403 (app.py scope()).
  if (me.authorities.length === 0) return err(403, "no effective release duty (token claim AND roster) in any authority");

  if (method === "GET" && p === "/summary") {
    const all = Object.values(cards).filter((c) => visible(me.authorities, c));
    const open = all.filter((c) => c.allowed_decisions.length > 0 || c.x_pending_decision);
    const mins = open.filter((c) => c.sla_due).map((c) => Math.round((Date.parse(c.sla_due!) - Date.now()) / 60000));
    const mine = open.filter((c) => !c.approvals.makers.some((m) => m.person_id === me.person_id)).length;
    const balanced = ABC_ROWS.filter((r) => r.residual === 0).length;
    const s: Summary = {
      genesis_status: "RATIFIED_TEST",
      held_units: all.filter((c) => c.state === "HELD").length,
      decisions_due: open.length,
      oldest_sla_minutes: mins.length ? Math.min(...mins) : null,
      mine,
      // Part E fix: out_of_balance = gating failures only; TRANSLATOR (acknowledgement) hops are advisory here.
      abc: {
        balanced,
        out_of_balance: ABC_ROWS.filter((r) => r.residual !== 0 && r.hop !== "TRANSLATOR").length,
        advisory: ABC_ROWS.filter((r) => r.residual !== 0 && r.hop === "TRANSLATOR").length,
      },
      deny_propagation_lag_s: 42,
      pipeline_order_ok: true,
      llm_provider: "template",
      ruleset_version: rulesetVersion,
      drift: MOCK_DRIFT,
      awaiting_checker: open.filter((c) => c.x_pending_decision).length,
      genesis_state: "TEST_GENESIS",
      genesis_label: "TEST_GENESIS (T1)",
      oldest_held_age_min: heldAgeMin(all),
      oldest_sla_remaining_min: mins.length ? Math.min(...mins) : null,
      explainer: MOCK_EXPLAINER,
      sim: { enabled: true, current_date: simRuns.length ? simRuns[simRuns.length - 1]!.night_date : null, queued: SIM_NIGHTS.length - simRuns.length },
      deployment: MOCK_DEPLOYMENT,
    };
    // Part E: decisions_due = HELD units still short of their required approvals.
    s.decisions_due = all.filter((c) => c.state === "HELD" && c.approvals.have < Math.max(1, c.approvals.required)).length;
    return ok(s);
  }

  if (method === "GET" && p === "/queue") {
    const q: QueueCluster[] = CLUSTERS.map((c) => {
      const units = c.units.filter((k) => {
        const card = cards[k];
        return card && visible(me.authorities, card) && (card.allowed_decisions.length > 0 || card.x_pending_decision || card.veto_window);
      });
      const worst = units.length
        ? worstOf(units.flatMap((k) => cards[k]!.dispositions.map((d) => d.disposition)))
        : c.worst_disposition;
      const deadlines = units.map((k) => cards[k]!.veto_window?.deadline).filter((d): d is string => !!d).sort();
      return { ...c, units, unit_count: units.length, worst_disposition: worst, tier: tierFor(c.authority), veto_deadline: deadlines[0] ?? null };
    }).filter((c) => c.unit_count > 0);
    return ok(q);
  }

  if (method === "GET" && p === "/pipelines") return ok(PIPELINES);

  if (method === "GET" && p === "/abc") {
    const b = url.searchParams.get("batch_id");
    return ok(b ? ABC_ROWS.filter((r) => r.x_batch_id === b) : ABC_ROWS);
  }

  const d = partD(me, method, p, url, body);
  if (d) return d;

  const m = p.match(/^\/units\/([^/]+)(?:\/(decide|challenge|confirm|reveal|veto))?$/);
  if (!m) return err(404, `no route ${method} ${p}`);
  const key = decodeURIComponent(m[1]!);
  const action = m[2];
  const card = cards[key];
  if (!card || !visible(me.authorities, card)) return err(404, "not found in your scope");

  if (method === "GET" && !action) return ok(decorateCard(structuredClone(card)));
  if (action === "veto" && method === "POST") {
    const w = card.veto_window;
    if (!w || card.state !== "EVALUATING" || Date.parse(w.deadline) <= Date.now())
      return coded(409, "REFUSED", "no open veto window: the unit has released or the window has passed");
    if (typeof b0(body).reason_code !== "string") return err(422, "reason_code required");
    card.state = "HELD";
    card.state_version += 1;
    card.veto_window = null;
    card.allowed_decisions = ["RELEASE", "QUARANTINE_AND_RELEASE", "REJECT", "HOLD"];
    demote(card.authorities[0] ?? "AUTH_MEDICAID", "VETO", `unit:${key}`);
    log(me.person_id, "VETO", key, String(b0(body).reason_code));
    return ok({ state: "HELD" });
  }
  if (method !== "POST") return err(405, "method not allowed");
  const b = (body ?? {}) as Record<string, unknown>;

  if (action === "challenge") {
    // Maker: {decision, quarantine_row_seqs}. Checker: {} — binds the pending maker decision's id, verb and quarantine set.
    const pend = card.x_pending_decision;
    let challenge: string;
    if (pend) {
      challenge = challengeFor(card, pend.decision, "CHECKER", pend.decision_id, pend.quarantine_row_seqs);
    } else {
      if (!isStepped(b.decision)) return err(422, "decision required (RELEASE | QUARANTINE_AND_RELEASE | REJECT)");
      const q = Array.isArray(b.quarantine_row_seqs) ? (b.quarantine_row_seqs as number[]) : [];
      challenge = challengeFor(card, b.decision, "MAKER", null, q);
    }
    const x_seat: Seat = pend ? "CHECKER" : "MAKER";
    log(me.person_id, "CHALLENGE_ISSUED", key, `${x_seat}${pend ? ` for ${pend.decision_id}` : ""}`);
    return ok({ challenge, step_up_url: "mock://idp/token", x_seat });
  }

  if (action === "decide") {
    if (b.decision === "HOLD") return ok({ decision_id: null, status: "APPLIED", reason: "unit remains HELD" } satisfies DecideResponse);
    if (!isStepped(b.decision)) return err(422, "unknown decision");
    if (!card.allowed_decisions.includes(b.decision)) return err(422, `${b.decision} is not in allowed_decisions`);
    const rationale = typeof b.rationale === "string" ? b.rationale.trim() : "";
    if (rationale.length < RATIONALE_MIN) return err(422, `rationale must be at least ${RATIONALE_MIN} characters`);
    const tok = typeof b.step_up_token === "string" ? b.step_up_token : "";
    if (!tok) return coded(428, "STEP_UP_REQUIRED", "obtain a step-up over POST /challenge first");
    const v = verifyStepUp(tok);
    if ("fail" in v) return v.fail;
    const q = Array.isArray(b.quarantine_row_seqs) ? (b.quarantine_row_seqs as number[]) : [];
    return decideOne(key, card, tok, v.actor, "MAKER", b.decision, rationale, b.expected_state_version, b.bound_fingerprint, q, null);
  }

  if (action === "confirm") {
    const pend = card.x_pending_decision;
    if (!pend) return err(404, "no pending maker decision on this unit in your scope");
    if (b.decision_id !== pend.decision_id)
      return coded(409, "STALE", "decision_id is not this unit's pending maker decision");
    const tok = typeof b.step_up_token === "string" ? b.step_up_token : "";
    // app.py /confirm has no 428 branch: DecisionService raises Denied for a missing token.
    if (!tok) return coded(403, "DENIED", "every decision requires a challenge-bound step-up token");
    const v = verifyStepUp(tok);
    if ("fail" in v) return v.fail;
    if (!isStepped(pend.decision)) return coded(403, "DENIED", "pending decision is not confirmable");
    const rationale =
      typeof b.rationale === "string" && b.rationale.trim() ? b.rationale.trim() : "Checker independently reviewed the evidence on the card.";
    const expected = b.expected_state_version ?? card.state_version;
    return decideOne(key, card, tok, v.actor, "CHECKER", pend.decision, rationale, expected, card.fingerprint, pend.quarantine_row_seqs, pend.decision_id);
  }

  if (action === "reveal") {
    const purpose = typeof b.purpose === "string" ? b.purpose.trim() : "";
    if (purpose.length < 10) return err(422, "purpose must be at least 10 characters");
    const ref = `dq_reveal.rv_${hashHex(`${key}|${me.person_id}|${Date.now()}`).slice(0, 12)}`;
    const expires_at = new Date(Date.now() + 15 * 60_000).toISOString();
    log(me.person_id, "REVEAL", key, `${ref}; purpose: ${purpose}`);
    return ok({ reveal_ref: ref, expires_at });
  }

  return err(404, "not found");
}

// =====================================================================================================
// Part D mock (PARTD_DESIGN §5), mirroring app.py: governance needs the governance duty; the approver must
// differ from the author (403); the step-up is bound to (cr, verdict, signed content); jti single-use (409
// REPLAY); a non-open request is 409 STALE; a missing token is 428. Known issues follow the same shape.
// =====================================================================================================

/** Set to a string to see the drift banner in mock mode. */
const MOCK_DRIFT: string | null = null;
const rules = initialRules();
let rulesetVersion = fx("ruleset:v1");
const artifacts = initialArtifacts();
// Part F P1-4 (K3): two promotion CRs seeded with their backtest evidence already linked (one mode-only, one
// not), so the Backtest panel has something real to render without a live CLI trigger (routes/backtest.ts's own
// header comment explains why). A CR proposed fresh through /pipelines/[id]/promote in THIS session carries no
// evidence, exactly like the real database immediately after gov_propose.
const k3Fixtures = seedBacktestFixtures();
const backtestReports = k3Fixtures.reports;
const crEvidence = new Map<string, CrEvidenceRow[]>(k3Fixtures.evidence);
const crs: CrDetail[] = [...initialChangeRequests(rules), revocationFixture(rules), ...k3Fixtures.crs];
const coverage = initialCoverage(rules);
const measureRows = initialMeasures();
const agentState = initialAgentState();
const board = initialBlackboard();
const memory: MemoryRow[] = initialMemory();
const lookback = initialLookback();
/** "<state>:<rule>@<entity>|<hash>" of every revoked version: never approved again. */
const revoked = new Set<string>(lookback.map((i) => `${i.rule_artifact}|${i.rule_hash}`));
/** challenge → what it binds, so a token minted for one verdict / request cannot be spent on another. */
const verdictChallenges = new Map<string, string>();

// K2c: PIPELINE / TASK_GATE artifacts a `/catalog/wire` proposal introduces, materialized ONLY once their change
// request is APPLIED (both approvals recorded) -- never at propose time, the SAME "nothing changes until two
// people approve" rule the rest of this mock already keeps. Read by routes/catalog.ts (never written there).
// Part F P1-4 (K3): seeded with tmsis_outbound ALREADY wired and its gate already applied (as if K2c's own wire
// smoke suite had already run in an earlier session) -- the promote page (K3's own smoke target) needs an
// existing, advisory-mode TASK_GATE to pick from in a FRESH browser context, without re-running the whole wire
// flow first. Mirrors _wire_tmsis_outbound's real shape (tests/t1/test_pipelines_runtime.py:19-38).
const wiredPipelines: WiredPipelineRec[] = [
  { id: "tmsis_outbound", adapter: "dbt_files", manifestPath: "pipelines/tmsis_outbound/target/manifest.json", platform: "duckdb", domains: ["CLAIMS", "ELIGIBILITY"], layerMap: {}, runOn: "manual" },
];
const wiredGates: GateRec[] = [
  { id: `${MOCK_STATE}:taskgate:tmsis_outbound:outbound_eligibility_extract`, pipelineId: "tmsis_outbound", taskId: "outbound_eligibility_extract", datasets: ["silver.eligibility_span"], mode: "advisory", releaseCeiling: "L1", maxWait: "PT2H", onTimeout: "PASS_ADVISORY", reintroduce: [] },
];

const govDuty = (me: Me) => me.duties.includes("governance:AUTH_RULES");
const crSigned = (c: CrDetail) => fx(JSON.stringify({ id: c.cr_id, items: c.items.map((i) => i.content_hash), imp: c.impact.ruleset_after }));

function listRow(c: CrDetail): CrListRow {
  return {
    cr_id: c.cr_id,
    state: c.state,
    author: c.author,
    origin: c.origin,
    loosening: c.loosening,
    approvals: c.approvals,
    approvals_required: c.approvals_required,
    status: c.status,
    at: c.at,
    rationale: c.rationale,
    items: c.items.map((i) => ({ artifact_id: i.artifact_id, kind: i.kind, op: i.op })),
    impact_summary: { ...(c.impact.summary ?? {}), loosening_reasons: c.impact.loosening_reasons },
  };
}

function applyCr(c: CrDetail) {
  for (const it of c.items) {
    const a = artifacts.find((x) => x.artifact_id === it.artifact_id);
    const version = (a?.version ?? 0) + 1;
    const row = { artifact_id: it.artifact_id, kind: it.kind, version, op: it.op, content_hash: fx(`${it.artifact_id}#${version}`), cr_id: c.cr_id, at: new Date().toISOString(), approved_by: c.events.filter((e) => e.event === "APPROVED").map((e) => e.person_id).join(",") };
    if (a) Object.assign(a, row);
    else artifacts.push(row);
    const m = it.artifact_id.match(/^[A-Z]+:overlay:(.+)$/);
    const content = it.content as Record<string, unknown> | null;
    if (m && content) {
      const r = rules.find((x) => x.id === m[1]);
      if (r) {
        r.overlay = { verb: String(content.verb), status: "ACTIVE", expires: (content.expires as string) ?? null, approved_ref: (content.approved_ref as string) ?? null };
        if (content.verb === "raise_priority") r.priority = String(content.priority);
        if (content.verb === "promote") r.gating = "gate";
      }
    }
  }
  for (const it of c.items.filter((i) => i.kind === "AUTONOMY")) {
    const [, , agent, ac] = it.artifact_id.split(":");
    for (const [auth, t] of Object.entries((it.content ?? {}) as Record<string, string>)) {
      const cur = autonomy.find((r) => r.agent_id === agent && r.action_class === ac && r.authority === auth);
      if (cur) Object.assign(cur, { tier: t, ceiling_mode: CEILING[t] ?? "H_IN", cr_id: c.cr_id });
    }
  }
  // K2c: a PIPELINE / TASK_GATE item becomes effective only now (APPLIED, both approvals recorded) -- the SAME
  // moment every other kind above takes effect, never at propose time.
  for (const it of c.items.filter((i) => i.kind === "PIPELINE")) {
    const pid = it.artifact_id.slice(it.artifact_id.indexOf(":pipeline:") + ":pipeline:".length);
    const content = (it.content ?? {}) as { adapter?: string; scope?: { manifest_path?: string }; platform?: string; domains?: string[]; layer_map?: Record<string, string>; run_on?: string };
    const rec: WiredPipelineRec = {
      id: pid,
      adapter: content.adapter ?? "dbt_files",
      manifestPath: content.scope?.manifest_path ?? null,
      platform: content.platform ?? "duckdb",
      domains: content.domains ?? [],
      layerMap: content.layer_map ?? {},
      runOn: content.run_on ?? "manual",
    };
    const existing = wiredPipelines.find((w) => w.id === pid);
    if (existing) Object.assign(existing, rec);
    else wiredPipelines.push(rec);
  }
  for (const it of c.items.filter((i) => i.kind === "TASK_GATE")) {
    const [, , pid, taskId] = it.artifact_id.split(":");
    const content = (it.content ?? {}) as { task_id?: string; datasets?: string[]; mode?: string; release_ceiling?: string; max_wait?: string; on_timeout?: string; reintroduce?: string[] };
    const rec: GateRec = {
      id: it.artifact_id,
      pipelineId: pid!,
      taskId: content.task_id ?? taskId!,
      datasets: content.datasets ?? [],
      mode: content.mode ?? "advisory",
      releaseCeiling: content.release_ceiling ?? "L1",
      maxWait: content.max_wait ?? "PT2H",
      onTimeout: content.on_timeout ?? "HOLD",
      reintroduce: content.reintroduce ?? [],
    };
    const existing = wiredGates.find((g) => g.id === rec.id);
    if (existing) Object.assign(existing, rec);
    else wiredGates.push(rec);
  }
  for (const it of c.items.filter((i) => i.kind === "REVOCATION")) {
    const rc = it.content as { rule: string; hash: string };
    const r = rules.find((x) => `${MOCK_STATE}:${x.id}@${x.entity}` === rc.rule);
    revoked.add(`${rc.rule}|${rc.hash}`);
    if (r) {
      r.approved_by_cr = null; // the version is no longer approved: new releases under it are blocked
      const n = Math.max(0, ...lookback.map((x) => x.item_id));
      [1, 2].forEach((k) =>
        lookback.push({ ...lookback[0]!, item_id: n + k, cr_id: c.cr_id, rule_artifact: rc.rule, rule_hash: rc.hash, node: r.relation ?? "?", unit_key: `ZZ:MEDICAID:a3f19c:rev=${n + k}`, in_live_manifest: true, status: "OPEN", resolution: null, reassessment: null, at: new Date().toISOString() }),
      );
    }
  }
  for (const ch of c.impact.rules) {
    if (ch.after?.status === "REVOKED") continue;
    const id = ch.rule.slice(ch.rule.indexOf(":") + 1, ch.rule.indexOf("@"));
    const r = rules.find((x) => x.id === id);
    if (r && ch.after) Object.assign(r, { effective_hash: ch.after.hash, status: ch.after.status, gating: ch.after.gating, approved_by_cr: c.cr_id });
  }
  rulesetVersion = c.impact.ruleset_after ?? fx(`ruleset:${c.cr_id}`);
  c.status = "APPLIED";
  c.events.push({ event: "APPLIED", person_id: null, detail: { ruleset_version: rulesetVersion }, at: new Date().toISOString() });
  for (const o of crs) if (o !== c && o.status === "AWAITING_APPROVAL" && o.items.some((i) => c.items.some((j) => j.artifact_id === i.artifact_id))) {
    o.status = "STALE";
    o.events.push({ event: "STALE", person_id: null, detail: `base version moved by ${c.cr_id}`, at: new Date().toISOString() });
  }
}

/** verify + binding + single use; returns the actor or an HTTP-shaped refusal. */
function spendVerdictToken(tok: string, binds: string): { actor: MockUser } | { fail: R } {
  const v = verifyStepUp(tok);
  if ("fail" in v) return v;
  const ch = stepUps.get(tok)!.challenge;
  if (verdictChallenges.get(ch) !== binds) return { fail: coded(403, "DENIED", "step-up token is not bound to this request and verdict") };
  if (usedTokens.has(tok)) return { fail: coded(409, "REPLAY", "token already used") };
  usedTokens.add(tok);
  return v;
}

/**
 * Part F P1-4 (K3): the CURRENT content of an artifact this CR's item names, for the item's own `current` field
 * (`CrItem.current`, `app.py::cr_detail`'s own `cur.get(i["artifact_id"])`). A TASK_GATE artifact's real current
 * content lives in `wiredGates`/`harnessGates` (K2c's own state), not the generic `artifacts` array (which only
 * ever carried a `{note: "current version (mock)"}` placeholder for every kind) -- this matters for K3 specifically
 * because the promote page's and `modePromotionRefusal`'s own predicate both compare `old.mode` against
 * `next.mode`, and a placeholder object has no `mode` key to compare.
 */
function currentContentFor(artifactId: string): Record<string, unknown> | null {
  const gate = [...harnessGates(PIPELINES), ...wiredGates].find((g) => g.id === artifactId);
  if (gate) {
    return { task_id: gate.taskId, datasets: gate.datasets, mode: gate.mode, correlate: "LATEST_SEALED",
             max_wait: gate.maxWait, on_timeout: gate.onTimeout, release_ceiling: gate.releaseCeiling,
             reintroduce: gate.reintroduce };
  }
  return artifacts.some((a) => a.artifact_id === artifactId) ? { note: "current version (mock)" } : null;
}

/**
 * The one CR-proposal mechanism (`POST /governance/change-requests`'s own body, extracted so K2c's `/catalog/wire`
 * reuses it rather than building a second, parallel one -- the SAME `gov_propose` every change request in the real
 * backend goes through). PIPELINE and TASK_GATE are never lower-only (K2a; `gov_item_lower_only`, `registry.py`'s
 * Python mirror, `governance_schema.py:896-902`), so a wiring proposal's items unconditionally force two approvers
 * here, the SAME fact the real database computes via `gov_items_lower_only`.
 */
function proposeCr(me: Me, items: { artifact_id: string; kind: string; op: string; content: unknown }[], rationale: string): R {
  if (rationale.length < 10) return err(422, "rationale of at least 10 characters required");
  if (!items.length) return coded(422, "REFUSED", "no items");
  const reasons: string[] = [];
  const impactRules: CrDetail["impact"]["rules"] = [];
  for (const it of items) {
    if (it.kind === "PIPELINE" || it.kind === "TASK_GATE") {
      reasons.push(`${it.artifact_id}: ${it.kind} is never lower-only (K2a)`);
      continue;
    }
    if (it.kind === "REVOCATION") {
      const rc = (it.content ?? {}) as { rule?: string; hash?: string; reason?: string };
      const rid = rc.rule?.slice(rc.rule.indexOf(":") + 1, rc.rule.indexOf("@"));
      const r = rules.find((x) => `${MOCK_STATE}:${x.id}@${x.entity}` === rc.rule);
      if (!r || !rc.hash || (rc.reason ?? "").length < 10)
        return coded(422, "REFUSED", "a REVOCATION names {rule: <state>:<rule>@<entity>, hash, reason (10+ chars)}");
      if (r.effective_hash !== rc.hash || !r.approved_by_cr) return coded(422, "REFUSED", `version ${rc.hash} of ${rid} was never approved`);
      if (!items.some((o) => o.kind === "OVERLAY" && o.artifact_id === `${MOCK_STATE}:overlay:${r.id}`))
        return coded(422, "REFUSED", `revoking ${rc.rule} needs a replacement in the same request (an overlay that changes it, or a waive): otherwise the revoked version would stay in force`);
      const before = { hash: r.effective_hash, status: r.status, gating: r.gating, relation: r.relation, kind: r.kind };
      impactRules.push({ rule: rc.rule!, before, after: { ...before, status: "REVOKED" } });
      continue;
    }
    const id = it.artifact_id.match(/^[A-Z]+:overlay:(.+)$/)?.[1];
    const content = (it.content ?? {}) as Record<string, unknown>;
    if (id) {
      const r = rules.find((x) => x.id === id);
      if (!r) return coded(422, "REFUSED", `overlays name rule ids not in the enabled catalog: ['${id}']`);
      if (content.verb === "tighten" && typeof (content.thresholds as Record<string, unknown>)?.max === "number" && ((content.thresholds as Record<string, number>).max ?? 0) > 0.01)
        return coded(422, "REFUSED", `overlay on ${id} would LOOSEN max; not allowed`);
      if (content.verb === "waive" || content.verb === "narrow") reasons.push(`${it.artifact_id}: verb ${String(content.verb)} reduces what is checked`);
      const before = { hash: r.effective_hash, status: r.status, gating: r.gating, relation: r.relation, kind: r.kind };
      const afterHash = fx(`${r.id}:${JSON.stringify(content)}`, 16);
      if (revoked.has(`${MOCK_STATE}:${r.id}@${r.entity}|${afterHash}`))
        return coded(422, "REFUSED", `${MOCK_STATE}:${r.id}@${r.entity} version ${afterHash.slice(0, 12)} was REVOKED; change the rule so it compiles to a new version instead`);
      impactRules.push({ rule: `${MOCK_STATE}:${r.id}@${r.entity}`, before, after: { ...before, hash: afterHash, status: content.verb === "waive" ? "STATE_WAIVED" : r.status } });
    }
  }
  let trackRecord: CrDetail["impact"]["track_record"] | undefined;
  for (const it of items.filter((i) => i.kind === "AUTONOMY")) {
    const [, , agent, ac] = it.artifact_id.split(":");
    const content = (it.content ?? {}) as Record<string, string>;
    for (const [auth, t] of Object.entries(content)) {
      const cur = autonomy.find((r) => r.agent_id === agent && r.action_class === ac && r.authority === auth);
      const from = cur?.tier ?? "L1"; // a missing entry means L1
      if (TIERS_ORDER.indexOf(t) > TIERS_ORDER.indexOf(from)) reasons.push(`${agent} ${ac} ${auth}: ${from} -> ${t} raises autonomy`);
      if (cur && t !== from) trackRecord = { ...cur.track_record, hash: cur.track_record_hash };
    }
  }
  const cr_id = `CR-${fx(`${Date.now()}|${Math.random()}`, 10)}`;
  const loosening = reasons.length > 0;
  const c: CrDetail = {
    cr_id,
    state: MOCK_STATE,
    author: me.person_id,
    origin: "HUMAN",
    loosening,
    approvals: 0,
    approvals_required: loosening ? 2 : 1,
    status: "AWAITING_APPROVAL",
    at: new Date().toISOString(),
    rationale,
    items: items.map((it, i) => ({ ...it, content_hash: fx(`${cr_id}:item${i}`), current: currentContentFor(it.artifact_id) })),
    impact: { ...(trackRecord ? { track_record: trackRecord } : {}), rules: impactRules, loosening_reasons: reasons, ruleset_before: rulesetVersion, ruleset_after: fx(`ruleset:${cr_id}`), summary: { changed: impactRules.length, rules_total: rules.length } },
    base: { ruleset: rulesetVersion },
    events: [{ event: "PROPOSED", person_id: me.person_id, detail: null, at: new Date().toISOString() }],
    x_you_are_author: false,
  };
  crs.push(c);
  return ok({ cr_id, impact: c.impact, loosening, approvals_required: c.approvals_required });
}

function governance(me: Me, method: string, p: string, url: URL, body: unknown): R {
  if (!govDuty(me)) return err(403, "governance duty required (token claim AND roster)");
  const b = (body ?? {}) as Record<string, unknown>;
  if (method === "GET" && p === "/governance/state")
    return ok({ state: MOCK_STATE, ratified: true, ruleset_version: rulesetVersion, drift: MOCK_DRIFT, artifacts: structuredClone(artifacts) });
  if (p === "/governance/change-requests") {
    if (method === "GET") {
      const st = url.searchParams.get("status");
      return ok(crs.map(listRow).filter((r) => !st || r.status === st).sort((x, y) => y.at.localeCompare(x.at)));
    }
    if (method === "POST") {
      const rationale = typeof b.rationale === "string" ? b.rationale.trim() : "";
      const items = Array.isArray(b.items) ? (b.items as CrDetail["items"]) : [];
      return proposeCr(me, items, rationale);
    }
  }
  const m = p.match(/^\/governance\/change-requests\/([^/]+)(?:\/(challenge|decide))?$/);
  if (!m) return err(404, `no route ${method} ${p}`);
  const c = crs.find((x) => x.cr_id === decodeURIComponent(m[1]!));
  if (!c) return err(404, "unknown change request");
  if (method === "GET" && !m[2]) {
    const d = structuredClone(c);
    const kinds = [...new Set(d.items.map((i) => i.kind))].join(", ");
    d.explanation =
      d.explanation ??
      explain(
        `${d.cr_id} proposes ${d.items.length} ${kinds} change(s) for state ${d.state}. It is ${d.status}, with ${d.approvals} of ${d.approvals_required} approvals${d.loosening ? "; it loosens checks, so two approvers are required" : ""}.`,
        { cr: d.cr_id, kinds, status: d.status, approvals: d.approvals, required: d.approvals_required, loosening: d.loosening },
      );
    d.impact.explanation =
      d.impact.explanation ??
      explain(
        d.impact.rules.length
          ? `${d.impact.rules.length} compiled rule version(s) change: ${d.impact.rules.map((r) => r.rule.split(":").slice(1).join(":")).join(", ")}.`
          : "No compiled rule changes.",
        { changed: d.impact.rules.map((r) => r.rule) },
      );
    // Part F P1-4 (K3-7): additive -- the linked backtest evidence for this CR, oldest first (mirrors
    // app.py::flat_cr_evidence / cr_detail's own "evidence": flat_cr_evidence(evidence)). Empty array means no
    // evidence linked yet, exactly like a fresh CR in the real database.
    return ok({ ...d, x_you_are_author: c.author === me.person_id, evidence: structuredClone(crEvidence.get(c.cr_id) ?? []) });
  }
  if (method !== "POST") return err(405, "method not allowed");
  if (b.verdict !== "APPROVED" && b.verdict !== "REJECTED") return err(422, "verdict APPROVED | REJECTED");
  const binds = `CR|${c.cr_id}|${b.verdict}|${crSigned(c)}`;
  if (m[2] === "challenge") {
    const challenge = fx(`${binds}|${Date.now()}|${Math.random()}`);
    verdictChallenges.set(challenge, binds);
    return ok({ challenge, step_up_url: "mock://idp/token" });
  }
  const tok = typeof b.step_up_token === "string" ? b.step_up_token : "";
  if (!tok) return coded(428, "STEP_UP_REQUIRED", "obtain a step-up over /challenge first");
  const v = spendVerdictToken(tok, binds);
  if ("fail" in v) return v.fail;
  if (v.actor.me.person_id === c.author)
    return coded(403, "DENIED", "two-person rule: the author cannot approve or reject their own change");
  if (TENANT.approver_org && PEOPLE_ORG[v.actor.me.person_id] !== TENANT.approver_org)
    return coded(403, "DENIED", `configuration is approved by the ${TENANT.approver_org} organisation; this person is not in it`);
  if (c.events.some((e) => e.event === "APPROVED" && e.person_id === v.actor.me.person_id))
    return coded(403, "DENIED", "the same person cannot approve twice");
  if (c.status !== "AWAITING_APPROVAL") return coded(409, "STALE", `request is ${c.status}`);
  // Part F P1-4 (K3-3, RESOLVED): mirrors gov_decide's APPROVED branch (governance_schema.py:194-200) -- an
  // APPROVE on a CR that changes an EXISTING TASK_GATE's mode is refused unless a dq_ctl.cr_evidence row already
  // links this cr_id. The refusal message is the SAME "promotion requires..." text the database returns, so the
  // Tower's refusal-surfacing code path (ApiError.isForbidden) is exercised identically in mock and live.
  if (b.verdict === "APPROVED") {
    const refusal = modePromotionRefusal(c, crEvidence);
    if (refusal) return coded(403, "DENIED", refusal);
  }
  const at = new Date().toISOString();
  c.events.push({ event: b.verdict, person_id: v.actor.me.person_id, detail: null, at });
  log(v.actor.me.person_id, `CR_${b.verdict}`, null, c.cr_id);
  if (b.verdict === "REJECTED") {
    c.status = "REJECTED";
    return ok({ cr_id: c.cr_id, status: "REJECTED" });
  }
  c.approvals += 1;
  const applied = c.approvals >= c.approvals_required;
  if (applied) applyCr(c);
  return ok({ cr_id: c.cr_id, status: applied ? "APPLIED" : "AWAITING_APPROVAL" });
}

function partD(me: Me, method: string, p: string, url: URL, body: unknown): R | null {
  const b = (body ?? {}) as Record<string, unknown>;
  const e = partE(me, method, p, url, b);
  if (e) return e;
  if (method === "GET" && p === "/lookback")
    return ok(
      lookback.map((i) => ({
        ...structuredClone(i),
        in_live_manifest_at_open: atOpen.get(i.item_id) ?? i.in_live_manifest,
        explanation:
          i.explanation ??
          explain(
            `Unit ${i.unit_key} was released at ${i.released_at ?? "an unknown time"} under ${i.rule_artifact} version ${i.rule_hash.slice(0, 12)}, which ${i.cr_id} later revoked. A person must decide whether the release stands.`,
            { item: i.item_id, rule: i.rule_artifact, hash: i.rule_hash, cr: i.cr_id },
          ),
      })),
    );
  const lm = p.match(/^\/lookback\/(\d+)\/(reassess|challenge|decide)$/);
  if (lm && method === "POST") {
    const it = lookback.find((x) => x.item_id === Number(lm[1]));
    if (!it) return err(404, "unknown look-back item in your scope");
    if (lm[2] === "reassess") {
      const cur = rules.find((r) => `${MOCK_STATE}:${r.id}@${r.entity}` === it.rule_artifact);
      const replacement = cur && cur.approved_by_cr && !revoked.has(`${it.rule_artifact}|${cur.effective_hash}`) ? cur.effective_hash.slice(0, 12) : null;
      const r1 = rules.find((r) => r.id === "R-ELG-001")!;
      const failing = it.item_id % 2 === 0 ? [{ rule_id: "R-ELG-001", failed: 1, evaluated: 4, version: r1.effective_hash.slice(0, 12) }] : [];
      it.reassessment = { failing_rules: failing, ruleset_version: rulesetVersion, replacement_version: replacement };
      return ok({ item_id: it.item_id, ...it.reassessment });
    }
    if (b.verdict !== "ACCEPTED" && b.verdict !== "WITHDRAWN") return err(422, "verdict ACCEPTED | WITHDRAWN");
    const want = fx(`LOOKBACK|${it.item_id}|${b.verdict}`);
    if (lm[2] === "challenge") return ok({ challenge: want, step_up_url: "mock://idp/token" });
    const tok = typeof b.step_up_token === "string" ? b.step_up_token : "";
    if (!tok) return coded(428, "STEP_UP_REQUIRED", "obtain a step-up over /challenge first");
    const v = verifyStepUp(tok);
    if ("fail" in v) return v.fail;
    if (stepUps.get(tok)?.challenge !== want || v.actor.me.person_id !== me.person_id)
      return coded(403, "DENIED", "step-up not bound to this look-back decision");
    if (usedTokens.has(tok)) return coded(409, "REFUSED", "token already used");
    if (TENANT.approver_org && PEOPLE_ORG[me.person_id] !== TENANT.approver_org)
      return coded(409, "REFUSED", `look-back items are resolved by the ${TENANT.approver_org} organisation`);
    if (!me.authorities.includes(it.authority)) return coded(409, "REFUSED", "every authority of the unit must be in your scope");
    if (it.status !== "OPEN") return coded(409, "REFUSED", "already resolved");
    if (typeof b.rationale !== "string" || b.rationale.trim().length < 20)
      return coded(409, "REFUSED", "verdict ACCEPTED | WITHDRAWN with a rationale of at least 20 characters");
    usedTokens.add(tok);
    it.status = it.resolution = String(b.verdict);
    if (b.verdict === "WITHDRAWN") {
      it.in_live_manifest = false;
      demote(it.authority, "LOOKBACK_WITHDRAWN", `lookback:${it.item_id}`);
    }
    log(me.person_id, `LOOKBACK_${it.status}`, it.unit_key, String(b.rationale));
    return ok({ item_id: it.item_id, status: it.status });
  }
  function smallCell(n: number | null, d: number | null): boolean {
    return d !== null && (d < 11 || (n !== null && ((n > 0 && n < 11) || (d - n > 0 && d - n < 11))));
  }
  if (method === "GET" && p === "/rules")
    return ok({
      state: MOCK_STATE,
      ruleset_version: rulesetVersion,
      drift: MOCK_DRIFT,
      ratified: true,
      rules: structuredClone(rules).map((r) => ({ ...r, classification: RULE_CLASSES[r.id] ?? [] })),
    });
  if (method === "GET" && p === "/coverage") return ok(structuredClone(coverage));
  if (method === "GET" && p === "/measures") {
    const bid = url.searchParams.get("batch_id");
    return ok(measureRows.filter((r) => !bid || r.batch_id === bid).map((r) => {
      const o: Record<string, unknown> = { ...r };
      delete o.x_long;
      const small = smallCell(r.numerator, r.denominator);   // mirrors the server rule
      if (small) Object.assign(o, { numerator: null, denominator: null, value: null });
      o.suppressed = small;
      return o;
    }));
  }
  if (method === "GET" && p === "/measures/series") {
    const rule = url.searchParams.get("rule_id") ?? "";
    const node = url.searchParams.get("node");
    const pts = measureRows.filter((r) => r.rule_id === rule && (!node || r.node === node)).slice().reverse();
    const bl = [...memory].reverse().find((x) => x.kind === "BASELINE" && x.status === "PINNED" && x.scope_id.startsWith(`${rule}@`));
    return ok({
      rule_id: rule,
      thresholds: pts[pts.length - 1]?.thresholds ?? {},
      baseline_median: (bl?.content.median as number | undefined) ?? null,
      points: pts.map((r) => {
        const small = smallCell(r.numerator, r.denominator);
        return { batch_id: r.batch_id, node: r.node, disposition: r.disposition, long_disposition: r.x_long, suppressed: small, value: small ? null : r.value, denominator: small ? null : r.denominator };
      }),
    });
  }
  if (method === "GET" && p === "/agent/state") {
    const batches = agentState.map((r) => ({
      ...r,
      board_entries: board.filter((e) => e.batch_id === r.batch_id).length,
      hypotheses: board.filter((e) => e.batch_id === r.batch_id && e.kind === "HYPOTHESIS").length,
    }));
    return ok({ llm_provider: "template", explainer: MOCK_EXPLAINER, batches });
  }
  if (method === "GET" && p === "/agent/blackboard") {
    const bid = url.searchParams.get("batch_id");
    return ok(
      board
        .filter((e) => (!bid || e.batch_id === bid) && (e.kind !== "MEMORY_REF" || e.person_id === me.person_id))
        .slice()
        .reverse(),
    );
  }
  if (method === "GET" && p === "/agent/memory")
    return ok(memory.filter((r) => r.kind !== "KNOWN_ISSUE" || r.status === "PROPOSED" || [r.proposed_by, r.approved_by].includes(me.person_id)));
  if (method === "POST" && p === "/agent/memory/known-issues") {
    const rule_id = typeof b.rule_id === "string" ? b.rule_id.trim() : "";
    const authority = String(b.authority ?? "");
    const reason_code = String(b.reason_code ?? "");
    if (!(REASON_CODES as readonly string[]).includes(reason_code)) return coded(403, "DENIED", `reason_code must be one of ${JSON.stringify([...REASON_CODES])}`);
    if (!me.authorities.includes(authority)) return coded(422, "REFUSED", "authority is not in your scope");
    if (!rules.some((r) => r.id === rule_id)) return coded(422, "REFUSED", `unknown rule ${rule_id}`);
    const exp = String(b.expires ?? "");
    if (!/^\d{4}-\d{2}-\d{2}$/.test(exp) || Date.parse(exp) <= Date.now()) return coded(422, "REFUSED", "expires must be a future date");
    const memory_id = Math.max(...memory.map((x) => x.memory_id)) + 1;
    const at = new Date().toISOString();
    memory.push({
      memory_id,
      kind: "KNOWN_ISSUE",
      scope_type: "RULE",
      scope_id: rule_id,
      authority,
      content: { rule_id, reason_code, authority, expires: exp },
      content_hash: fx(`ki:${memory_id}:${rule_id}:${reason_code}:${exp}`),
      created_seat: "STEWARD",
      proposed_by: me.person_id,
      approved_by: null,
      expires_at: `${exp}T00:00:00Z`,
      status: "PROPOSED",
      at,
    });
    board.push(
      chainPost(board[board.length - 1], { batch_id: null, authority, seat: "STEWARD", person_id: me.person_id, kind: "MEMORY_REF", subject_type: "RULE", subject_id: rule_id, template: "KNOWN_ISSUE_PROPOSED", body: { memory_id, rule_id, expires: exp }, text: `Known issue ${memory_id} proposed on ${rule_id} (expires ${exp}).` }, at),
    );
    return ok({ memory_id, status: "PROPOSED" });
  }
  const km = p.match(/^\/agent\/memory\/(\d+)\/(challenge|decide)$/);
  if (km && method === "POST") {
    const row = memory.find((x) => x.memory_id === Number(km[1]) && x.kind === "KNOWN_ISSUE");
    if (!row) return coded(403, "DENIED", "unknown known issue");
    if (b.verdict !== "APPROVED" && b.verdict !== "REJECTED") return err(422, "verdict APPROVED | REJECTED");
    const binds = `KI|${row.memory_id}|${row.content_hash}|${b.verdict}`;
    if (km[2] === "challenge") {
      const challenge = fx(`${binds}|${Date.now()}|${Math.random()}`);
      verdictChallenges.set(challenge, binds);
      return ok({ challenge, step_up_url: "mock://idp/token" });
    }
    const tok = typeof b.step_up_token === "string" ? b.step_up_token : "";
    if (!tok) return coded(428, "STEP_UP_REQUIRED", "obtain a step-up over /challenge first");
    const v = spendVerdictToken(tok, binds);
    if ("fail" in v) return v.fail;
    if (v.actor.me.person_id === row.proposed_by) return coded(403, "DENIED", "a known issue needs a second person");
    if (row.status !== "PROPOSED") return coded(409, "STALE", `known issue is ${row.status}`);
    row.status = String(b.verdict);
    if (b.verdict === "APPROVED") row.approved_by = v.actor.me.person_id;
    return ok({ memory_id: row.memory_id, status: row.status });
  }
  return null;
}

// =====================================================================================================
// Part E mock (PARTE_DESIGN §10)
// =====================================================================================================
const TIERS_ORDER = ["L1", "L2", "L3", "L4"];
const CEILING: Record<string, string> = { L4: "A", L3: "H_ON", L2: "H_IN", L1: "H_IN" };
const agents = initialAgents();
const autonomy = initialAutonomy();
const feedItems = initialFeedItems();
const parallel = initialParallelRun();
const cde = initialCde();
const classification = initialClassification();
const monitorSeries = initialMonitorSeries();
const atOpen = new Map<number, boolean>(lookback.map((i) => [i.item_id, i.in_live_manifest]));
const prChallenges = new Map<string, string>();
// K0 / B9: the recorded eval runs a governance person picks det_run_id / llm_run_id from (GET /evals).
const evalRuns = initialEvalRuns();
interface MockAttestation {
  id: number;
  label: string;
  code_sha256: string;
  tests: number;
  failures: number;
  junit_sha256: string;
  person_id: string;
  status: string;
  confirmed_by: string | null;
  confirmed_at: string | null;
  det_run_id: string;
  llm_run_id: string | null;
  prompt_version: string | null;
  halves: { deterministic: boolean; llm: boolean };
}
const attestations: MockAttestation[] = [];
const b0 = (body: unknown) => (body ?? {}) as Record<string, unknown>;

function tierFor(authority: string): DecisionCard["tier"] {
  return (autonomy.find((r) => r.action_class === "RELEASE" && r.authority === authority)?.tier ?? "L1") as DecisionCard["tier"];
}

function heldAgeMin(all: DecisionCard[]): number | null {
  // Fixture cards carry no created_at; a fixed age keeps the mock deterministic.
  return all.some((c) => c.state === "HELD") ? 47 : null;
}

const LABELS: Record<string, string> = {
  GENESIS_RATIFIED: "Configuration genesis ratified",
  BATCH_SEALED: "Batch sealed at intake",
  TRANSITION: "Unit changed state",
  DECIDE: "Maker decision recorded",
  CONFIRM: "Checker confirmation recorded",
  CHALLENGE_ISSUED: "Step-up challenge issued",
  REVEAL: "Record values revealed for a stated purpose",
  VETO: "Release vetoed during its window",
  AUTONOMY_DEMOTED: "Agent tier lowered automatically",
  SIM_NIGHT_RUN: "Simulated night run (demo mode)",
};
function labelFor(a: string): string {
  if (LABELS[a]) return LABELS[a]!;
  if (a.startsWith("CR_")) return `Change request ${a.slice(3).toLowerCase()}`;
  if (a.startsWith("LOOKBACK_")) return `Look-back item ${a.slice(9).toLowerCase()}`;
  return a.replace(/_/g, " ").toLowerCase();
}

function demote(authority: string, reason: string, ref: string) {
  const r = autonomy.find((x) => x.action_class === "RELEASE" && x.authority === authority);
  if (!r || TIERS_ORDER.indexOf(r.tier) <= TIERS_ORDER.indexOf("L2")) return;
  const from = r.tier;
  r.tier = "L2";
  r.ceiling_mode = "H_IN";
  r.track_record = { ...r.track_record, vetoes: r.track_record.vetoes + (reason === "VETO" ? 1 : 0), lookback_withdrawn: r.track_record.lookback_withdrawn + (reason === "LOOKBACK_WITHDRAWN" ? 1 : 0) };
  r.demotions = [
    ...(r.demotions ?? []),
    {
      at: new Date().toISOString(),
      from_tier: from,
      to_tier: "L2",
      reason_code: reason,
      ref,
      explanation: explain(
        `${reason === "VETO" ? "A reviewer vetoed a release" : "A released unit was withdrawn on look-back"} by ${r.agent_id} for ${authority}, so its RELEASE tier for ${authority} was lowered from ${from} to L2. A person now decides each release.`,
        { agent: r.agent_id, authority, from, to: "L2", reason, ref },
      ),
    },
  ];
  log("dq_registry_svc", "AUTONOMY_DEMOTED", null, `${r.agent_id} RELEASE ${authority} ${from} -> L2 (${reason})`);
}

function decorateCard(c: DecisionCard): DecisionCard {
  // Part E fix: unit-detail ABC rows carry gating_failure (a non-zero residual on a gating hop).
  c.abc_before_after = c.abc_before_after.map((r) => ("gating_failure" in r ? r : { ...r, gating_failure: r.residual !== 0 && r.hop !== "TRANSLATOR" }));
  const worst = worstOf(c.dispositions.map((d) => d.disposition));
  c.tier = c.tier ?? tierFor(c.authorities.find((a) => a !== INTAKE) ?? c.authorities[0] ?? "AUTH_MEDICAID");
  c.dispositions = c.dispositions.map((d) => ({
    ...d,
    explanation:
      d.explanation ??
      explain(
        d.disposition === "PASS"
          ? `Rule ${d.rule_id} passed: ${cnt(d.failed)} of ${cnt(d.evaluated)} rows failed.`
          : `Rule ${d.rule_id} is ${d.disposition}: ${cnt(d.failed)} of ${cnt(d.evaluated)} rows failed against a threshold of ${d.threshold ?? "none"}.`,
        { rule_id: d.rule_id, unit_key: c.unit_key, disposition: d.disposition, failed: d.failed, evaluated: d.evaluated, threshold: d.threshold },
      ),
  }));
  c.explanation =
    c.explanation ??
    explain(
      c.veto_window
        ? `This ${c.node} unit from batch ${c.batch_id} is ${c.state}. At tier ${c.tier} the agent releases it at ${c.veto_window.deadline} unless a person vetoes. The worst disposition is ${worst}.`
        : `This ${c.node} unit from batch ${c.batch_id} is ${c.state} under mode ${c.required_mode}. The worst disposition is ${worst}; ${c.approvals.have} of ${c.approvals.required} approvals are recorded.`,
      { unit_key: c.unit_key, batch_id: c.batch_id, state: c.state, mode: c.required_mode, worst, approvals_have: c.approvals.have, approvals_required: c.approvals.required, tier: c.tier },
    );
  return c;
}

// An L3 unit inside its veto window (the w2_earn story). Its own batch, with ABC balanced, and a worst disposition
// of TA_WARN: per modes.py and policy_w1.yaml, only PASS / TA_WARN may be autonomous, so only those units can sit in
// a veto window (FAIL floors to H_IN; ABC failed in the batch floors to H_IN_2).
const U6 = "ZZ:MEDICAID:a3f19c:chain=ENC0002377";
const BATCH_VETO = "B-ZZ-20261019-ENC-02";
cards[U6] = {
  ...structuredClone(cards[U1]!),
  unit_key: U6,
  batch_id: BATCH_VETO,
  node: "bronze.encounter_line",
  state: "EVALUATING",
  state_version: 3,
  fingerprint: "5b0e7d2a91c4f8e36a1d0c9b7e5f4a3d2c1b0a9f8e7d6c5b4a3f2e1d0c9b8a7f",
  required_mode: "H_ON",
  allowed_decisions: [],
  x_pending_decision: null,
  approvals: { required: 0, have: 0, makers: [] },
  mode_trace: ["policy[AUTH_MEDICAID][TA_WARN] = A", "tier: L3 ceiling for AUTH_MEDICAID -> H_ON"],
  dispositions: [
    { rule_id: "R-ENC-003", rule_hash: "sha256:3e91a07c5d22", check_class: "referential", priority: "Medium", disposition: "TA_WARN", evaluated: 1290, failed: 14, metric: 0.0109, threshold: 0.01, sample_tokens: [] },
    { rule_id: "RPRV.NPI_LUHN", rule_hash: "sha256:c03d8e112a9f", check_class: "validity", priority: "High", disposition: "PASS", evaluated: 1290, failed: 0, metric: 0, threshold: 0, sample_tokens: [] },
    { rule_id: "R837.CLM02_LINE_SUM", rule_hash: "sha256:4be1a9c0d7e2", check_class: "transaction_structure", priority: "High", disposition: "PASS", evaluated: 1290, failed: 0, metric: 0, threshold: 0, sample_tokens: [] },
  ],
  abc_before_after: [abcRow("before", "BRONZE", 1290, 1290), abcRow("after", "BRONZE", 1290, 1290)],
  // As app.py sends them for a unit with no quarantine proposal that was never held (F-07 / F-30).
  proposed_actions: [{ verb: "QUARANTINE_ROWS", params: { x_row_seqs: [] }, dry_run: null, x_dry_run_reason: "no proposal" }],
  sla_due: null,
  x_sla_reason: "unit never held",
  llm_hypothesis: null,
  explanation: undefined,
  tier: "L3",
  veto_window: { opened_at: new Date(Date.now() - 2 * 60_000).toISOString(), deadline: new Date(Date.now() + 8 * 60_000).toISOString(), remaining_s: 480 },
};
CLUSTERS.push({
  cluster_id: "CL-ZZ-VETO-1",
  authority: "AUTH_MEDICAID",
  rule_ids: ["R-ENC-003"],
  node: "bronze.encounter_line",
  failure_signature: "L3: releases at the deadline unless vetoed",
  worst_disposition: "TA_WARN",
  required_mode: "H_ON",
  sla_due: new Date(Date.now() + 8 * 60_000).toISOString(),
  units: [U6],
});

// §10b: nights 1..SIM_PRELOAD were run at startup by the demo preload (person_id null).
const simRuns = SIM_NIGHTS.slice(0, SIM_PRELOAD).map((_, i) => simSummary(i, [], null));

function partE(me: Me, method: string, p: string, url: URL, b: Record<string, unknown>): R | null {
  if (method === "GET" && p === "/sim/nights") {
    const ran = new Map(simRuns.map((r) => [r.night_date, r.run_id]));
    return ok({
      current_date: simRuns.length ? simRuns[simRuns.length - 1]!.night_date : null,
      nights: SIM_NIGHTS.map((n) => ({ ...n, status: ran.has(n.night_date) ? "RUN" : "QUEUED", run_id: ran.get(n.night_date) ?? null, error_class: null })),
    });
  }
  if (method === "POST" && p === "/sim/run-next") {
    if (!me.duties.some((d) => d.startsWith("operations:"))) return err(403, "operations duty required (token claim AND roster)");
    if (me.authorities.length === 0) return err(403, "no effective release duty (token claim AND roster) in any authority");
    const i = simRuns.length;
    if (i >= SIM_NIGHTS.length) return coded(409, "REFUSED", "no night is queued");
    // The first queued night holds two units that exist as mock cards, so its links open real cards.
    const r = simSummary(i, i === SIM_PRELOAD ? [U1, U4] : [], me.person_id);
    simRuns.push(r);
    log(me.person_id, "SIM_NIGHT_RUN", null, `${r.night_date} ${r.run_id}`);
    return ok(structuredClone(r));
  }
  if (method === "GET" && p === "/sim/runs") return ok(structuredClone(simRuns));
  const sm = p.match(/^\/sim\/runs\/([^/]+)$/);
  if (method === "GET" && sm) {
    const r = simRuns.find((x) => x.run_id === decodeURIComponent(sm[1]!));
    return r ? ok(structuredClone(r)) : err(404, "unknown run");
  }
  if (method === "GET" && p === "/agents") return ok(structuredClone(agents));
  if (method === "GET" && p === "/evals") {
    // K0 / B9: a plain listing, newest first -- not the E-13 pending-attestations picker (deferred to K11).
    if (!me.duties.includes("governance:AUTH_RULES")) return err(403, "governance duty required");
    const suite = url.searchParams.get("suite");
    if (suite !== null && suite !== "agent_eval" && suite !== "explainer") return err(422, "suite one of agent_eval, explainer");
    const limitRaw = url.searchParams.get("limit");
    const limit = limitRaw ? Number(limitRaw) : 50;
    const rows = evalRuns
      .filter((r) => suite === null || r.suite === suite)
      .slice()
      .sort((a, c) => (a.at < c.at ? 1 : a.at > c.at ? -1 : 0))
      .slice(0, Math.max(1, Math.min(200, limit)));
    return ok(structuredClone(rows));
  }
  if (method === "GET" && p === "/eval-runs/diff") {
    // K11 (PARTF_K7_K12_MOCK_CONTRACT.md): PRE-BACKEND -- an INVENTED route, no real counterpart exists yet.
    // States what differs between the two named runs, never why (same honesty pattern as the real /runs/diff).
    if (!me.duties.includes("governance:AUTH_RULES")) return err(403, "governance duty required");
    const aId = url.searchParams.get("a");
    const bId = url.searchParams.get("b");
    const a = evalRuns.find((r) => r.run_id === aId);
    const b = evalRuns.find((r) => r.run_id === bId);
    if (!a || !b) return err(404, "unknown eval run on one or both sides");
    const keys: (keyof typeof a)[] = ["suite", "source_mode", "cases", "passed", "code_sha256", "prompt_version", "junit_sha256"];
    const fields = keys.filter((k) => a[k] !== b[k]);
    return ok({ a: structuredClone(a), b: structuredClone(b), fields });
  }
  if (method === "POST" && p === "/eval-attestations") {
    // R4-5 5-arg contract: {label, code_sha256, det_run_id, llm_run_id?}. The OLD 6-arg {tests, failures,
    // junit_sha256} shape is refused with 422 ("old contract"), matching the real dq_ctl.eval_attest -- mock/
    // production parity (E15).
    if (!me.duties.includes("governance:AUTH_RULES")) return err(403, "governance duty required");
    const extra = Object.keys(b).filter((k) => !["label", "code_sha256", "det_run_id", "llm_run_id"].includes(k));
    if (extra.length) return err(422, `old contract: the eval runs supply tests, failures and junit hashes; send only label, code_sha256, det_run_id, llm_run_id (unexpected: ${extra.join(", ")})`);
    if (b.label === undefined || b.label === "" || b.code_sha256 === undefined || b.code_sha256 === "" || b.det_run_id === undefined || b.det_run_id === "")
      return err(422, "label, code_sha256, det_run_id (and optionally llm_run_id) required");
    const det = evalRuns.find((r) => r.run_id === String(b.det_run_id));
    if (!det || det.source_mode !== "DETERMINISTIC" || det.suite !== "agent_eval" || det.cases === 0 || det.passed !== det.cases || det.code_sha256 !== String(b.code_sha256))
      return coded(422, "REFUSED", `the deterministic half must be a fully passing DETERMINISTIC agent_eval run of code ${String(b.code_sha256).slice(0, 12)}`);
    let llmRun: (typeof evalRuns)[number] | null = null;
    if (b.llm_run_id !== undefined && b.llm_run_id !== null && b.llm_run_id !== "") {
      llmRun = evalRuns.find((r) => r.run_id === String(b.llm_run_id)) ?? null;
      if (!llmRun || llmRun.source_mode !== "LIVE" || llmRun.suite !== "explainer" || llmRun.cases === 0 || llmRun.passed !== llmRun.cases || llmRun.code_sha256 !== String(b.code_sha256) || !llmRun.prompt_version)
        return coded(422, "REFUSED", `the LLM half must be a fully passing LIVE explainer run of code ${String(b.code_sha256).slice(0, 12)} with a recorded prompt version (a SCRIPTED, RECORDED or TEMPLATE run is not evidence)`);
    }
    const a: MockAttestation = {
      id: attestations.length + 1,
      label: String(b.label),
      code_sha256: String(b.code_sha256),
      tests: det.cases,
      failures: det.cases - det.passed,
      junit_sha256: det.junit_sha256,
      person_id: me.person_id,
      status: "PROPOSED",
      confirmed_by: null,
      confirmed_at: null,
      det_run_id: det.run_id,
      llm_run_id: llmRun?.run_id ?? null,
      prompt_version: llmRun?.prompt_version ?? null,
      halves: { deterministic: true, llm: llmRun !== null },
    };
    attestations.push(a);
    log(me.person_id, "EVAL_ATTESTATION_PROPOSED", null, `${a.id} ${a.label}`);
    return ok({ id: a.id, status: a.status, halves: a.halves });
  }
  const am = p.match(/^\/eval-attestations\/(\d+)\/confirm$/);
  if (am && method === "POST") {
    if (!me.duties.includes("governance:AUTH_RULES")) return err(403, "governance duty required");
    const a = attestations.find((x) => x.id === Number(am[1]));
    if (!a) return err(404, "unknown attestation");
    if (a.person_id === me.person_id) return coded(409, "REFUSED", "the proposer cannot confirm their own attestation");
    if (TENANT.approver_org && PEOPLE_ORG[me.person_id] !== TENANT.approver_org)
      return coded(409, "REFUSED", `eval attestations are confirmed by the ${TENANT.approver_org} organisation`);
    if (a.status === "CONFIRMED") return coded(409, "REFUSED", "already confirmed");
    Object.assign(a, { status: "CONFIRMED", confirmed_by: me.person_id, confirmed_at: new Date().toISOString() });
    // R4-5 / DEC-024: nothing resets. A confirmed attestation is evidence for its code version only; the track
    // record itself is continuous. We only update the LATEST-confirmed-attestation display field, never the counts.
    for (const r of autonomy) {
      r.track_record = {
        ...r.track_record,
        eval_attestation: { label: a.label, code_sha256: a.code_sha256, tests: a.tests, failures: a.failures, at: a.confirmed_at, proposed_by: a.person_id, confirmed_by: a.confirmed_by, halves: a.halves },
      };
      // The code version this attestation names now has complete evidence (mock mirrors dq_ctl.evidence_status()).
      if (a.code_sha256 === MOCK_CODE_SHA256 && r.evidence) {
        r.evidence = { ...r.evidence, code_sha256: a.code_sha256, deterministic: true, llm: a.halves.llm || r.evidence.llm, complete: true };
      }
    }
    log(me.person_id, "EVAL_ATTESTATION_CONFIRMED", null, `${a.id}`);
    return ok({ id: a.id, label: a.label, code_sha256: a.code_sha256, tests: a.tests, failures: a.failures, person_id: a.person_id, status: a.status, confirmed_by: a.confirmed_by, confirmed_at: a.confirmed_at, halves: a.halves, det_run_id: a.det_run_id, llm_run_id: a.llm_run_id, prompt_version: a.prompt_version });
  }
  if (method === "GET" && p === "/autonomy") return ok(structuredClone(autonomy));
  if (method === "GET" && p === "/explainer")
    return ok({ provider: "template", configured: true, fallback_count: 1, decisions: "deterministic", label: MOCK_EXPLAINER.label });
  if (method === "GET" && p === "/feeds/items") return ok(structuredClone(feedItems));
  const fm = p.match(/^\/feeds\/items\/(\d+)\/ack$/);
  if (fm && method === "POST") {
    const it = feedItems.find((x) => x.item_id === Number(fm[1]));
    if (!it) return err(404, "unknown feed item");
    // governance_schema.py FEED_ACK_CODES_BY_KIND / feed_item_ack: each kind accepts only its own codes.
    const allowed = FEED_ACK_CODES_BY_KIND[it.kind] ?? FEED_ACK_CODES;
    if (!allowed.includes(String(b.reason_code))) return err(422, `reason_code for a ${it.kind} item is one of ${JSON.stringify(allowed)}`);
    if (it.status !== "OPEN") return coded(409, "REFUSED", "already acknowledged");
    Object.assign(it, { status: "ACKED", reason_code: String(b.reason_code), acked_by: me.person_id });
    log(me.person_id, "FEED_ITEM_ACKED", null, `${it.item_id} ${it.reason_code}`);
    return ok(structuredClone(it));
  }
  if (method === "GET" && p === "/annotation-queue") {
    // K11 (PARTF_K7_K12_MOCK_CONTRACT.md): PRE-BACKEND -- an INVENTED route, no real counterpart exists yet.
    // Every subject is a REAL entity key this codebase already uses (a decision unit's unit_key, or a feed
    // item's feed:{item_id}) -- never a fabricated id. status is FEEDBACK_RECORDED only when the invented
    // /explanation-feedback mechanism actually recorded something for that subject in this tab.
    const recordedSubjects = new Set(explanationFeedback.map((f) => f.subject));
    const unitItems = Object.values(cards)
      .filter((c) => c.state === "HELD" || c.state === "AWAITING_CHECKER")
      .map((c) => ({
        subject: c.unit_key,
        kind: "UNIT" as const,
        label: `${c.unit_key} (${c.pipeline})`,
        status: (recordedSubjects.has(c.unit_key) ? "FEEDBACK_RECORDED" : "OPEN") as "OPEN" | "FEEDBACK_RECORDED",
        at: c.sla_due,
      }));
    const feedQueueItems = feedItems
      .filter((f) => f.status === "OPEN")
      .map((f) => ({
        subject: `feed:${f.item_id}`,
        kind: "FEED_ITEM" as const,
        label: `${f.feed} ${f.kind}${f.submitter_id ? ` from ${f.submitter_id}` : ""}`,
        status: (recordedSubjects.has(`feed:${f.item_id}`) ? "FEEDBACK_RECORDED" : "OPEN") as "OPEN" | "FEEDBACK_RECORDED",
        at: f.at,
      }));
    return ok({ items: [...unitItems, ...feedQueueItems] });
  }
  if (method === "GET" && p === "/parallel-run") {
    const per = url.searchParams.get("period");
    return ok(structuredClone(parallel.filter((r) => !per || r.period === per)));
  }
  const pm = p.match(/^\/parallel-run\/(\d+)\/(challenge|explain)$/);
  if (pm && method === "POST") {
    const row = parallel.find((x) => x.row_id === Number(pm[1]));
    if (!row) return err(404, "unknown parallel-run row");
    const code = String(b.explain_code ?? "");
    if (!["TIMING", "LEGACY_DEFECT", "NEW_DEFECT", "DEFINITION", "FIXED"].includes(code)) return err(422, "explain_code");
    const binds = `PR|${row.row_id}|${code}`;
    if (pm[2] === "challenge") {
      const challenge = fx(`${binds}|${Date.now()}`);
      prChallenges.set(challenge, binds);
      return ok({ challenge, step_up_url: "mock://idp/token" });
    }
    if (code === "LEGACY_DEFECT" || code === "DEFINITION") {
      const tok = typeof b.step_up_token === "string" ? b.step_up_token : "";
      if (!tok) return coded(428, "STEP_UP_REQUIRED", `${code} needs a step-up`);
      const v = verifyStepUp(tok);
      if ("fail" in v) return v.fail;
      if (prChallenges.get(stepUps.get(tok)!.challenge) !== binds || v.actor.me.person_id !== me.person_id)
        return coded(403, "DENIED", "step-up not bound to this explain code");
      if (usedTokens.has(tok)) return coded(409, "REPLAY", "token already used");
      usedTokens.add(tok);
    }
    row.explain_code = code;
    log(me.person_id, "PARALLEL_RUN_EXPLAINED", null, `${row.row_id} ${code}`);
    return ok(structuredClone(row));
  }
  if (method === "GET" && p === "/coverage" && url.searchParams.get("cde") === "true") return ok({ ...structuredClone(coverage), cde: structuredClone(cde) });
  if (method === "GET" && p === "/classification") return ok(structuredClone(classification));
  if (method === "GET" && p === "/monitor") return ok(monitorRows(monitorSeries));
  if (method === "GET" && p === "/monitor/series") {
    const rel = url.searchParams.get("relation");
    const met = url.searchParams.get("metric");
    const key = url.searchParams.get("key");
    const s = monitorSeries.find((x) => x.relation === rel && x.metric === met && (x.key || null) === (key || null));
    return s ? ok(structuredClone(s)) : err(404, "no such monitored metric");
  }
  if (method === "GET" && p === "/audit/verify") {
    const head = board[board.length - 1];
    return ok({
      audit: { ok: true, through_seq: auditSeq, head_hash: fx(`audit:${auditSeq}`), first_break: null },
      blackboard: { ok: true, through_seq: head?.seq ?? 0, head_hash: head?.entry_hash ?? "0".repeat(64), first_break: null },
    });
  }
  return null;
}
