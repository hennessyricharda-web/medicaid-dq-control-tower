import type { Tone } from "./dispositions";
import type { Explanation, ExplanationRef, EvidenceStatus, Tier, TrackRecordVersion } from "./types";

/** PARTE_DESIGN §3.2. Delivery-cadence items only (EMPTY_DELIVERY, MISSING_DELIVERY); kept for callers that have
 *  no kind in hand. A feed item's own ack control must use FEED_ACK_CODES_BY_KIND[item.kind] instead: the server
 *  (governance_schema.py FEED_ACK_CODES_BY_KIND, feed_item_ack's per-kind CHECK) refuses any other code 422/exception
 *  for UNROUTED_FILE and RECOVERY_DATA_LOST items, so offering this flat list to those kinds would show choices the
 *  server never accepts. */
export const FEED_ACK_CODES = ["NO_ACTIVITY", "HOLIDAY", "SUBMITTER_INACTIVE", "RESUBMIT_REQUESTED"] as const;
/** governance_schema.py FEED_ACK_CODES_BY_KIND: each feed-item kind accepts only its own acknowledgement codes. */
export const FEED_ACK_CODES_BY_KIND: Record<string, readonly string[]> = {
  EMPTY_DELIVERY: FEED_ACK_CODES,
  MISSING_DELIVERY: FEED_ACK_CODES,
  UNROUTED_FILE: ["ROUTE_PROPOSED", "FILE_REJECTED", "RESUBMIT_REQUESTED"],
  RECOVERY_DATA_LOST: ["REDELIVERY_REQUESTED", "RESUBMIT_REQUESTED"],
};
export function feedAckCodesFor(kind: string): readonly string[] {
  return FEED_ACK_CODES_BY_KIND[kind] ?? FEED_ACK_CODES;
}
/** governance_schema.py FEED_ITEM_KINDS: honest label + explanation of what each kind means, never a generic
 *  fallback. The two delivery kinds keep the section's own prose (cadence); the other two are routing/recovery
 *  events with no cadence meaning, so they get their own line. */
export const FEED_KIND_INFO: Record<string, { label: string; tone: Tone; what: string }> = {
  EMPTY_DELIVERY: { label: "EMPTY_DELIVERY", tone: "warn", what: "the feed arrived but carried 0 detail rows" },
  MISSING_DELIVERY: { label: "MISSING_DELIVERY", tone: "bad", what: "the feed did not arrive by its expected time" },
  UNROUTED_FILE: { label: "UNROUTED_FILE", tone: "bad", what: "an inbound file matched no feed route; it was never ingested" },
  RECOVERY_DATA_LOST: { label: "RECOVERY_DATA_LOST", tone: "bad", what: "a frozen load this batch's recovery needs is no longer in the data plane; the batch stays held" },
};
export function feedKindInfo(kind: string): { label: string; tone: Tone; what: string } {
  return FEED_KIND_INFO[kind] ?? { label: `UNKNOWN: ${kind}`, tone: "warn", what: "unrecognised feed-item kind" };
}
/** PARTE_DESIGN §3.3: a named person explains a break; LEGACY_DEFECT and DEFINITION need a step-up. */
export const EXPLAIN_CODES = ["TIMING", "LEGACY_DEFECT", "NEW_DEFECT", "DEFINITION", "FIXED"] as const;
export const STEP_UP_EXPLAIN = new Set(["LEGACY_DEFECT", "DEFINITION"]);
/** Tower assumption: the contract names `{reason_code}` for a veto but no list. */
export const VETO_REASON_CODES = ["EVIDENCE_INCOMPLETE", "DATA_SUSPECT", "POLICY_CONCERN", "DEPENDENCY_CONCERN"] as const;
/** PARTE_DESIGN §1.1: there is no PROPOSE_CR action class. */
export const ACTION_CLASSES = ["RELEASE", "POST_BOARD", "PIN_MEMORY"] as const;
export const TIERS: Tier[] = ["L1", "L2", "L3", "L4"];

export const TIER_INFO: Record<Tier, { name: string; ceiling: string; card: string }> = {
  L4: { name: "Autonomous", ceiling: "A", card: "acts without a person" },
  L3: { name: "Supervised", ceiling: "H_ON", card: "acts after a veto window" },
  L2: { name: "Recommend", ceiling: "H_IN", card: "a person decides; the agent recommends" },
  L1: { name: "Assist", ceiling: "H_IN", card: "a person decides; evidence only" },
};
export const tierRank = (t: Tier) => TIERS.indexOf(t);
export function tierTone(t: Tier): Tone {
  return t === "L4" ? "ok" : t === "L3" ? "info" : t === "L2" ? "neutral" : "neutral";
}

export function refOf(e: Explanation | null | undefined): ExplanationRef | undefined {
  return e ? { facts_hash: e.facts_hash, text_hash: e.text_hash } : undefined;
}

export function statusTone(s: string): Tone {
  switch (s) {
    case "MATCH":
    case "MEETS":
    case "OK":
    case "ACKED":
    case "ACTIVE":
      return "ok";
    case "WITHIN":
    case "WATCH":
    case "BASELINE_BUILDING":
    case "PROVISIONAL":
    case "STARTED":
    case "REGISTERED":
      return "info";
    case "FAILED":
      return "crit";
    case "BREAK":
    case "BELOW":
    case "SIGNAL":
    case "SHIFT":
      return "bad";
    case "NOT_EXERCISED":
    case "NOT_COMPUTED":
    case "NOT_LOADED":
    case "SUSPENDED":
      return "neutral";
    default:
      return "warn";
  }
}

/** Tower assumption: /me lists the operations duty as "operations:<authority>"; the server still enforces it. */
export function isOperator(duties: string[] | undefined): boolean {
  return !!duties?.some((d) => d === "operations" || d.startsWith("operations:"));
}

/** Typed refs (PARTE_DESIGN §8 B1) to the screen that shows them. */
export function refHref(ref: string): { href: string; label: string } {
  const i = ref.indexOf(":");
  const kind = i < 0 ? "" : ref.slice(0, i);
  const id = i < 0 ? ref : ref.slice(i + 1);
  switch (kind) {
    case "unit":
      return { href: `/units/${encodeURIComponent(id)}`, label: id };
    case "cr":
      return { href: `/governance/${encodeURIComponent(id)}`, label: id };
    case "feed":
      return { href: `/reconciliation#feed-${encodeURIComponent(id)}`, label: `feed item ${id}` };
    case "pr":
      return { href: `/reconciliation#pr-${encodeURIComponent(id)}`, label: `parallel-run ${id}` };
    case "lookback":
      return { href: "/lookback", label: `look-back ${id}` };
    case "bb":
      return { href: "/agent", label: `blackboard #${id}` };
    case "mem":
      return { href: "/agent", label: `memory ${id}` };
    case "rule":
      return { href: "/rules", label: id };
    default:
      return { href: "#", label: ref };
  }
}

/** Fired on window by the top band after a simulated night runs, so open screens re-read. */
export const SIM_RAN_EVENT = "dq:sim-ran";

// ---------------------------------------------------------------------------------------------------
// K0 / B7: the demo moment's two display strings, built ONLY from /autonomy's own `evidence`/`minimums`
// fields (dq_ctl.evidence_status() / dq_ctl.promotion_minimums(state)) -- never a hardcoded literal, and
// never defaulting an absent field to a "looks fine" state (an absent evidence/minimums reads as unknown,
// not as passing).
// ---------------------------------------------------------------------------------------------------

/** "evidence ✓ (deterministic, demo)" -- the glyph, the halves actually required for this deployment, and the
 *  deployment name, read live each time; a production/pilot deployment needing the LLM half shows both halves. */
export function evidenceLabel(ev: EvidenceStatus | null | undefined): { ok: boolean | null; text: string } {
  if (!ev) return { ok: null, text: "evidence: unknown (the server sent no evidence status)" };
  const glyph = ev.complete ? "✓" : "✗";
  const halves = ev.llm_required ? `deterministic ${ev.deterministic ? "✓" : "✗"} / live LLM ${ev.llm ? "✓" : "✗"}` : "deterministic";
  return { ok: ev.complete, text: `evidence ${glyph} (${halves}, ${ev.deployment})` };
}

const MIN_FIELD: Record<string, keyof TrackRecordVersion> = {
  human_decisions: "human_decisions",
  human_releases: "human_released",
  released_after_window: "released_after_window",
  overrides: "overrides",
  denies: "denies",
  lookback_withdrawn: "lookback_withdrawn",
  vetoes: "vetoes",
};
const AT_LEAST = new Set(["human_decisions", "human_releases", "released_after_window"]);
const MIN_LABEL: Record<string, string> = {
  human_decisions: "human decisions",
  human_releases: "human-approved releases",
  released_after_window: "releases after a veto window",
  overrides: "overrides",
  denies: "denies after release",
  lookback_withdrawn: "look-back withdrawals",
  vetoes: "vetoes",
  override_rate_max: "override rate",
};

export interface RecordCheck {
  key: string;
  label: string;
  have: number;
  need: number;
  atLeast: boolean;
  ok: boolean;
  pct?: boolean;
}

/** Every governed minimum for one tier, checked against the RUNNING code version's own count only -- the exact
 *  counting dq_ctl.promotion_refusal uses, so a new code version honestly starts over, not inherited. */
export function recordChecks(cv: TrackRecordVersion | null | undefined, tierMin: Record<string, number> | undefined): RecordCheck[] {
  if (!tierMin) return [];
  const have = (k: string): number => {
    if (k === "override_rate_max") return cv && cv.human_decisions > 0 ? cv.overrides / cv.human_decisions : 0;
    const f = MIN_FIELD[k];
    return f && cv ? (cv[f] as number) : 0;
  };
  return Object.entries(tierMin)
    .filter(([k]) => k !== "label")
    .map(([k, need]) => {
      const atLeast = AT_LEAST.has(k);
      const h = have(k);
      const n = Number(need);
      return { key: k, label: MIN_LABEL[k] ?? k, have: h, need: n, atLeast, ok: atLeast ? h >= n : h <= n, pct: k === "override_rate_max" };
    });
}

export function fmtCheckValue(c: RecordCheck, v: number): string {
  return c.pct ? `${(v * 100).toFixed(1)}%` : String(v);
}

/** "record 5/5 ✓" for the tier's at-least count (the demo-moment headline, e.g. minimums.L3.human_releases vs the
 *  row's own current-code-version track record); every other governed check (at-most counts) listed alongside it,
 *  never folded in silently -- a promotion can still be refused on one of those even when the headline passes. */
export function recordSummary(checks: RecordCheck[]): { ok: boolean; headline: string; rest: RecordCheck[] } {
  if (checks.length === 0) return { ok: true, headline: "no governed minimum for this tier", rest: [] };
  const atLeast = checks.filter((c) => c.atLeast);
  const rest = checks.filter((c) => !atLeast.includes(c));
  const ok = checks.every((c) => c.ok);
  if (atLeast.length === 1) {
    const c = atLeast[0]!;
    return { ok, headline: `record ${fmtCheckValue(c, c.have)}/${fmtCheckValue(c, c.need)} ${c.ok ? "✓" : "✗"}`, rest };
  }
  // No single at-least count (or more than one): list everything, nothing hidden.
  return { ok, headline: checks.map((c) => `${c.label} ${fmtCheckValue(c, c.have)}/${fmtCheckValue(c, c.need)} ${c.ok ? "✓" : "✗"}`).join(" · "), rest: [] };
}
