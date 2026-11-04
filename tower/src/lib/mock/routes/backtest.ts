/**
 * Mock of K3's one new route, `GET /api/v1/backtests/{report_id}` (`dqagent/api/app.py::backtest_detail`,
 * `:1012-1023`), plus the in-memory state that backs the additive `evidence[]` field on the existing CR-detail
 * mock response (`server.ts`'s `governance()`). Shapes: `lib/types/backtest.ts`.
 *
 * Scope boundary (K3_DESIGN.md §6 Decision 1, mandatory): a mode-only CR's own release/hold/pending delta is
 * LEGITIMATELY ZERO in this build -- `block` mode is not yet behaviorally enforced anywhere. `MODE_ONLY_DISCLOSURE`
 * below is copied VERBATIM from `dqagent/backtest.py`'s own `MODE_ONLY_DISCLOSURE` constant (lines ~65-71); it is
 * never re-authored or softened here. `isModeOnlyCr` mirrors `backtest.py::_is_mode_only_cr`'s own predicate (every
 * item is a TASK_GATE PUT that changes `content.mode` and NOTHING else) so the two seeded fixtures below derive
 * their `delta_note` from the SAME logic the real backend runs, rather than each hard-coding one outcome.
 *
 * Mock limitation, stated once, not hidden: there is no CLI in a browser. The real demo moment ("backtest the
 * consumer gate over the synthetic nights") runs `dqagent backtest ...` live, outside Tower, and its evidence
 * lands in `dq_ctl.cr_evidence` before a second approval is possible. The mock cannot run that CLI, so it SEEDS
 * two already-decided-but-not-yet-approved promotion CRs with their evidence already linked (one mode-only, one
 * not), so the Backtest panel has something real to render without a live trigger. A FRESH mode-change CR
 * proposed through `/pipelines/[id]/promote` in this same tab carries NO evidence (exactly like the real
 * database immediately after `gov_propose`), so attempting to approve it is refused honestly, through the SAME
 * `modePromotionRefusal` predicate `server.ts`'s decide handler calls -- the mock never fabricates evidence for a
 * CR a person actually proposed in this session.
 */
import type { CrDetail } from "../../types";
import type { BacktestDetailResponse, BacktestReport, CrEvidenceRow } from "../../types/backtest";
import { fx, MOCK_STATE } from "../fixtures";

type R = { status: number; json: unknown };
const ok = (json: unknown): R => ({ status: 200, json });
const err = (status: number, detail: unknown): R => ({ status, json: { detail } });

/** Verbatim copy of `dqagent/backtest.py`'s `MODE_ONLY_DISCLOSURE` constant -- never re-authored, never softened,
 *  rendered exactly as-is wherever `delta_note` is non-null (K3_DESIGN.md §6 Decision 1, mandatory). */
export const MODE_ONLY_DISCLOSURE =
  "This change does not alter release/hold/pending outcomes in this backtest; `block` mode is not yet " +
  "behaviorally enforced (the gate's `mode` field is read only by the database's own validity and lower-only " +
  "checks, never by the evaluation or publish path -- confirmed by direct grep of this tree). The mechanics " +
  "below (sandbox comparison, the determinism double-run, the evidence linkage) are real; the release/hold/" +
  "pending delta is legitimately zero for a mode-only change, not a measurement failure.";

function canon(v: unknown): string {
  if (v === null || typeof v !== "object") return JSON.stringify(v);
  if (Array.isArray(v)) return `[${v.map(canon).join(",")}]`;
  const o = v as Record<string, unknown>;
  return `{${Object.keys(o)
    .sort()
    .map((k) => `${JSON.stringify(k)}:${canon(o[k])}`)
    .join(",")}}`;
}

/** Mirrors `backtest.py::_is_mode_only_cr`: true exactly when every (old, new) pair is a TASK_GATE PUT whose
 *  `content.mode` differs and every OTHER key is unchanged. */
export function isModeOnlyCr(pairs: { old: Record<string, unknown>; next: Record<string, unknown> }[]): boolean {
  if (!pairs.length) return false;
  for (const { old, next } of pairs) {
    if (old.mode === next.mode) return false;
    const oldRest: Record<string, unknown> = { ...old };
    const nextRest: Record<string, unknown> = { ...next };
    delete oldRest.mode;
    delete nextRest.mode;
    if (canon(oldRest) !== canon(nextRest)) return false;
  }
  return true;
}

/** Mirrors `dq_ctl.mode_promotion_refusal`'s predicate (`governance_schema.py:442-458`), simplified for the mock:
 *  any evidence linked to this `cr_id` satisfies it (the real function additionally checks the evidence row's
 *  `items_digest` against a fresh hash of the CR's CURRENT items; the mock does not recompute that hash -- a
 *  stated simplification, not a silent one). NULL (no refusal) when no item changes an EXISTING gate's mode, or
 *  when evidence already exists for this CR. */
export function modePromotionRefusal(cr: CrDetail, evidenceByCr: Map<string, CrEvidenceRow[]>): string | null {
  for (const it of cr.items) {
    if (it.kind !== "TASK_GATE" || it.op !== "PUT") continue;
    const old = it.current as Record<string, unknown> | null;
    if (!old || typeof old !== "object") continue; // a brand-new gate: nothing to promote FROM
    const next = (it.content ?? {}) as Record<string, unknown>;
    if (old.mode === next.mode) continue; // not a mode change
    if (!(evidenceByCr.get(cr.cr_id)?.length)) {
      return `promotion requires a linked backtest report (dq_ctl.cr_evidence) for the TASK_GATE mode change on ${it.artifact_id}`;
    }
  }
  return null;
}

export interface BacktestCtx {
  reports: Map<string, BacktestDetailResponse>;
}

/** `GET /api/v1/backtests/{report_id}` (`app.py:1012-1023`); 404 on an unknown id, the same as the real route. */
export function mockBacktest(ctx: BacktestCtx, method: string, url: URL): R | null {
  if (method !== "GET") return null;
  const m = url.pathname.match(/^\/backtests\/([^/]+)$/);
  if (!m) return null;
  const id = decodeURIComponent(m[1]!);
  const r = ctx.reports.get(id);
  return r ? ok(structuredClone(r)) : err(404, "unknown backtest report");
}

const ago = (min: number) => new Date(Date.now() - min * 60_000).toISOString();

function side(released: number, held: number, pending: number, wouldHold: number, heldReasons: Record<string, number>): BacktestReport["base"] {
  return {
    relations: { "silver.eligibility_span": { RELEASED: released, HELD: held, PENDING_DEPENDENCY: pending } },
    rule_hit_rates: { "R-ELG-001": { evaluated: released + held + pending, failed: 1, hit_rate: Number((1 / Math.max(1, released + held + pending)).toFixed(4)) } },
    held_reasons: heldReasons,
    would_hold: wouldHold,
  };
}

function deltas(base: BacktestReport["base"], cand: BacktestReport["base"]): BacktestReport["deltas"] {
  const rel = "silver.eligibility_span";
  const b = base.relations[rel] ?? {};
  const c = cand.relations[rel] ?? {};
  const states = new Set([...Object.keys(b), ...Object.keys(c)]) as Set<"RELEASED" | "HELD" | "PENDING_DEPENDENCY">;
  const out: Record<string, number> = {};
  for (const st of states) out[st] = (c[st] ?? 0) - (b[st] ?? 0);
  return { relations: { [rel]: out }, would_hold: cand.would_hold - base.would_hold };
}

const gateContent = (mode: string, releaseCeiling: string, taskId: string, dataset: string): Record<string, unknown> => ({
  task_id: taskId,
  datasets: [dataset],
  mode,
  correlate: "LATEST_SEALED",
  max_wait: "PT2H",
  on_timeout: "HOLD",
  release_ceiling: releaseCeiling,
  reintroduce: [],
});

function buildFixture(
  seed: string,
  cr: Omit<CrDetail, "cr_id" | "items" | "events" | "base" | "x_you_are_author" | "impact">,
  artifactId: string,
  oldContent: Record<string, unknown>,
  newContent: Record<string, unknown>,
  base: BacktestReport["base"],
  cand: BacktestReport["base"],
): { cr: CrDetail; report: BacktestDetailResponse; evidence: CrEvidenceRow } {
  const cr_id = `CR-${fx(`${seed}:cr`, 10)}`;
  const report_id = `BTR-${fx(`${seed}:report`, 12)}`;
  const modeOnly = isModeOnlyCr([{ old: oldContent, next: newContent }]);
  const report: BacktestReport = {
    cr_id,
    pipeline_id: "tmsis_outbound",
    state: MOCK_STATE,
    nights: 22,
    base,
    cand,
    deltas: deltas(base, cand),
    trace_shape_digest_base: fx(`${seed}:base`, 64),
    trace_shape_digest_cand: fx(`${seed}:cand`, 64),
    inputs_digest: fx(`${seed}:inputs`, 64),
    delta_note: modeOnly ? MODE_ONLY_DISCLOSURE : null,
    digest: fx(`${seed}:digest`, 64),
    label: "SYNTHESIZED",
  };
  const detail: BacktestDetailResponse = { report_id, cr_id, label: "SYNTHESIZED", digest: report.digest, at: ago(20), report };
  const evidence: CrEvidenceRow = { report_id, items_digest: fx(`${seed}:items`, 64), linked_by: "SYSTEM:BACKTEST", at: ago(19) };
  const full: CrDetail = {
    ...cr,
    cr_id,
    items: [
      {
        artifact_id: artifactId,
        kind: "TASK_GATE",
        op: "PUT",
        content: newContent,
        content_hash: fx(`${cr_id}:item0`),
        current: oldContent,
      },
    ],
    impact: { rules: [], loosening_reasons: [] },
    base: {},
    events: [{ event: "PROPOSED", person_id: cr.author, detail: null, at: ago(25) }],
    x_you_are_author: false,
  };
  return { cr: full, report: detail, evidence };
}

/**
 * Two seeded promotion CRs, demonstrating BOTH `delta_note` outcomes without a live CLI trigger (see the file
 * header). Both are `AWAITING_APPROVAL`, 0/2 approvals, loosening=false (tightening a gate is never a loosening) --
 * TASK_GATE items are never lower-only (K2a), so a mode change always needs two approvers by construction.
 */
export function seedBacktestFixtures(): { crs: CrDetail[]; reports: Map<string, BacktestDetailResponse>; evidence: Map<string, CrEvidenceRow[]> } {
  const common = {
    state: MOCK_STATE,
    author: "P-ZZ-1001",
    origin: "HUMAN" as const,
    loosening: false,
    approvals: 0,
    approvals_required: 2,
    status: "AWAITING_APPROVAL",
    at: ago(24),
  };

  // Fixture A: mode-only (advisory -> block, nothing else changes) -> delta_note present, base === cand.
  const aOld = gateContent("advisory", "L1", "outbound_eligibility_extract", "silver.eligibility_span");
  const aNew = gateContent("block", "L1", "outbound_eligibility_extract", "silver.eligibility_span");
  const aSide = side(40, 3, 0, 2, { "floor H_IN": 3 });
  const a = buildFixture(
    "k3fixtureA",
    { ...common, rationale: "Backtest ran clean over 22 synthetic nights; promoting outbound_eligibility_extract to block." },
    `${MOCK_STATE}:taskgate:tmsis_outbound:outbound_eligibility_extract`,
    aOld,
    aNew,
    aSide,
    aSide, // identical base/cand: mode has no behavioral effect yet
  );

  // Fixture B: mode AND release_ceiling both change -> NOT mode-only -> delta_note null, a genuine, non-mode delta
  // (the ceiling, not the still-unenforced block mode, is what moves these counts).
  const bOld = gateContent("advisory", "L1", "outbound_summary", "gold.tmsis_outbound_subset");
  const bNew = gateContent("block", "L2", "outbound_summary", "gold.tmsis_outbound_subset");
  const bBase = side(40, 3, 0, 2, { "floor H_IN": 3 });
  const bCand = side(33, 10, 0, 2, { "floor H_IN": 3, "ceiling L2": 7 });
  const b = buildFixture(
    "k3fixtureB",
    { ...common, rationale: "Backtest shows the tightened release ceiling (not block mode, which is unenforced) holds more units; promoting with the ceiling change." },
    `${MOCK_STATE}:taskgate:tmsis_outbound:outbound_summary`,
    bOld,
    bNew,
    bBase,
    bCand,
  );

  const reports = new Map<string, BacktestDetailResponse>([
    [a.report.report_id, a.report],
    [b.report.report_id, b.report],
  ]);
  const evidence = new Map<string, CrEvidenceRow[]>([
    [a.cr.cr_id, [a.evidence]],
    [b.cr.cr_id, [b.evidence]],
  ]);
  return { crs: [a.cr, b.cr], reports, evidence };
}
