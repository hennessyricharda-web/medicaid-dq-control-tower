/**
 * Disposition ranks from DESIGN.md §6. Colour is always paired with the text label and a
 * glyph so meaning never depends on colour alone.
 */
export type Tone = "ok" | "info" | "warn" | "bad" | "crit" | "neutral";

const RANK: Record<string, number> = {
  PASS: 0,
  TA_WARN: 1,
  KNOWN_ISSUE: 2,
  APPROXIMATE: 3,
  PENDING_BENCHMARK: 4,
  NOT_EVALUATED: 5,
  SYSTEM_NOT_LOADED: 5,
  NOT_IMPLEMENTED: 5,
  UNBALANCEABLE: 6,
  FAIL: 7,
  ERROR: 8,
  // Excluded from scoring (§6): shown, but never the worst.
  NOT_APPLICABLE_FOR_FORMAT: -1,
  ADVISORY: -1,
};

/** Unknown values fail closed: treated as worse than ERROR and labelled as unknown. */
export function dispositionRank(d: string): number {
  return d in RANK ? (RANK[d] as number) : 9;
}

export function isKnownDisposition(d: string): boolean {
  return d in RANK;
}

export function dispositionTone(d: string): Tone {
  const r = dispositionRank(d);
  if (r < 0) return "neutral";
  if (r === 0) return "ok";
  if (r <= 3) return "info";
  if (r <= 6) return "warn";
  if (r === 7) return "bad";
  return "crit";
}

export const TONE_GLYPH: Record<Tone, string> = {
  ok: "✓", // check
  info: "i",
  warn: "!",
  bad: "✕", // x
  crit: "⚠", // warning sign
  neutral: "–",
};

export function worstOf(ds: string[]): string {
  const scored = ds.filter((d) => dispositionRank(d) >= 0);
  if (scored.length === 0) return "NOT_EVALUATED"; // §6: empty worst-of = NOT_EVALUATED
  return scored.reduce((a, b) => (dispositionRank(b) > dispositionRank(a) ? b : a));
}

/** Lifecycle states, DESIGN §3.5. */
export function stateTone(s: string): Tone {
  switch (s) {
    case "RELEASED":
      return "ok";
    case "PENDING_RELEASE":
    case "EVALUATING":
    case "SEALED":
    case "LANDED":
      return "info";
    case "HELD":
    case "PENDING_DEPENDENCY":
    case "RE_EVALUATE_PENDING":
      return "warn";
    case "REJECTED":
    case "SUPERSEDED":
      return "neutral";
    case "ERROR":
      return "crit";
    default:
      return "warn";
  }
}

export const HOP_ORDER = ["TRANSLATOR", "FILE", "RAW", "BRONZE", "SILVER", "GOLD", "OUTBOUND_FILE"];
export function hopRank(h: string): number {
  const i = HOP_ORDER.indexOf(h.toUpperCase());
  return i < 0 ? HOP_ORDER.length : i;
}
