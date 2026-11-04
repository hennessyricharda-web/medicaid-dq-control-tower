/**
 * "Mode" and "Why" for the queue and the card (AUDIT §1.2, LAYOUT_SYSTEM §7b). Why is the first step of the
 * mode trace (dqagent/hitl/modes.py) that set the required mode. The tier is shown as a ceiling, never as the
 * outcome.
 */
import { dispositionRank } from "./dispositions";
import { TIER_INFO } from "./partE";
import type { QueueCluster, Tier } from "./types";

/** Mock data spells modes with hyphens (H-IN-2); the API uses underscores (H_IN_2). */
export function normMode(m: string | null | undefined): string {
  return (m ?? "").replace(/-/g, "_");
}

const MODE_RANK: Record<string, number> = { A: 0, H_ON: 1, H_IN: 2, H_IN_2: 3 };
export function modeRank(m: string): number {
  return MODE_RANK[normMode(m)] ?? 4;
}

/** modes.py: only PASS and TA_WARN may be autonomous; anything worse is floored to H_IN. */
export function autonomousAllowed(worst: string): boolean {
  const r = dispositionRank(worst);
  return r >= 0 && r <= 1;
}

/** A short label for one mode-trace step, e.g. "floor: FAIL never autonomous -> H_IN" -> "FAIL floor". */
export function stepLabel(step: string): string {
  const s = step.trim();
  let m = /^floor: (\w+) never autonomous/.exec(s);
  if (m) return `${m[1]} floor`;
  if (/^floor: ABC failed/i.test(s)) return "ABC failed";
  if (/^floor: STRUCTURAL/i.test(s)) return "Structural program";
  if (/^floor: floor waived/i.test(s)) return "Advisory waiver";
  m = /^tier:? (L[1-4]) ceiling/i.exec(s);
  if (m) return `${m[1]} ceiling`;
  if (/^entitlement/i.test(s)) return "No entitlement";
  if (/^vetoed/i.test(s)) return "Vetoed";
  if (/^no policy/i.test(s)) return "No policy";
  if (/^policy/i.test(s)) return "Policy";
  m = /^(\S+) -> at least/.exec(s);
  if (m) return `Escalated: ${m[1]}`;
  return s.length > 28 ? `${s.slice(0, 27)}…` : s;
}

function stepTarget(step: string): string | null {
  const m = /(?:->|=)\s*(H[_-]IN[_-]2|H[_-]IN|H[_-]ON|A)\b(?!.*(?:->|=))/.exec(step) ?? /\b(H[_-]IN[_-]2|H[_-]IN|H[_-]ON)\b(?!.*\bH[_-])/.exec(step);
  return m ? normMode(m[1]) : null;
}

/** The first trace step, other than the base policy, that reaches the required mode; else the policy step. */
export function whyFromTrace(trace: string[] | null | undefined, requiredMode: string): string | null {
  if (!trace?.length) return null;
  const want = normMode(requiredMode);
  const hit = trace.find((t) => !/^policy/i.test(t.trim()) && stepTarget(t) === want) ?? trace.find((t) => stepTarget(t) === want);
  return hit ? stepLabel(hit) : null;
}

/**
 * The queue list has no trace, so the row's Why is read from the facts it does carry (mode, worst disposition,
 * tier, veto deadline). The detail pane shows the trace itself.
 */
export function whyFromCluster(c: QueueCluster): string {
  const mode = normMode(c.required_mode);
  if (c.veto_deadline && mode === "H_ON") return `${c.tier ?? "L3"} ceiling`;
  if (mode === "H_IN_2") return "Two people: see trace";
  if (!autonomousAllowed(c.worst_disposition)) return `${c.worst_disposition} floor`;
  if (c.tier && modeRank(TIER_INFO[c.tier].ceiling) >= modeRank(mode)) return `${c.tier} ceiling`;
  return "Policy";
}

/** "Agent tier L3, not binding" when a floor or policy holds the unit stricter than the tier's ceiling. */
export function tierBinding(tier: Tier | null | undefined, requiredMode: string): boolean | null {
  if (!tier) return null;
  return modeRank(TIER_INFO[tier].ceiling) >= modeRank(requiredMode);
}

/** The medallion layer named by a node's prefix ("bronze.encounter_line" -> "bronze"). */
export function layerOf(node: string): "raw" | "bronze" | "silver" | "gold" | null {
  const p = node.split(".")[0]?.toLowerCase();
  return p === "raw" || p === "bronze" || p === "silver" || p === "gold" ? p : null;
}
