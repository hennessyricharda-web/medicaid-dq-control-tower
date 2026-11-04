import type { Tone } from "./dispositions";

/** dqagent/agent/memory.py REASON_CODES — the server refuses any other value (422). */
export const REASON_CODES = [
  "EXPECTED_POLICY_SHIFT",
  "REFERENCE_DATA_LAG",
  "SPEC_AMBIGUITY",
  "UPSTREAM_DEFECT_TICKETED",
  "VENDOR_FIX_SCHEDULED",
] as const;

/** Change-request status (app.py _cr_row). Unknown values fail closed to "warn" and keep their label. */
export function crStatusTone(s: string): Tone {
  switch (s) {
    case "APPLIED":
      return "ok";
    case "APPROVED":
    case "AWAITING_APPROVAL":
    case "PROPOSED":
      return "info";
    case "REJECTED":
    case "WITHDRAWN":
      return "neutral";
    case "STALE":
      return "bad";
    default:
      return "warn";
  }
}

/** Rule status (rules/model.py): only ACTIVE checks anything. */
export function ruleStatusTone(s: string): Tone {
  switch (s) {
    case "ACTIVE":
      return "ok";
    case "NOT_APPLICABLE":
    case "STATE_WAIVED":
      return "neutral";
    case "NOT_BOUND":
    case "NOT_LOADED":
      return "warn";
    default:
      return "warn";
  }
}

/** Basic-DQ matrix cell (rules/basic.py basic_coverage). */
export function basicCellTone(s: string): Tone {
  switch (s) {
    case "EXERCISED":
      return "ok";
    case "DECLARED_NOT_EXERCISED":
      return "warn";
    case "NOT_ACTIVE":
      return "bad";
    case "EMPTY":
      return "neutral";
    default:
      return "warn";
  }
}

/** Requirement coverage (rules/model.py coverage): never "covered" without an ACTIVE full rule. */
export function reqCoverageTone(s: string): Tone {
  switch (s) {
    case "ACTIVE":
      return "ok";
    case "PARTIAL":
    case "ANALOG_ACTIVE":
      return "info";
    case "GAP":
      return "bad";
    case "NOT_APPLICABLE":
    case "STATE_WAIVED":
      return "neutral";
    default:
      return "warn";
  }
}

export function memoryStatusTone(s: string): Tone {
  switch (s) {
    case "APPROVED":
    case "PINNED":
      return "ok";
    case "PROPOSED":
      return "info";
    case "REJECTED":
    case "SUPERSEDED":
      return "neutral";
    default:
      return "warn";
  }
}

export const DIMENSIONS = ["completeness", "validity", "uniqueness", "referential", "consistency", "timeliness"] as const;

/** Flatten JSON to dotted key paths so two versions can be compared key by key. */
export function flatten(v: unknown, prefix = "", out: Map<string, string> = new Map()): Map<string, string> {
  if (v !== null && typeof v === "object") {
    const entries = Array.isArray(v) ? v.map((x, i) => [String(i), x] as const) : Object.entries(v as Record<string, unknown>);
    if (entries.length === 0) out.set(prefix || "(root)", Array.isArray(v) ? "[]" : "{}");
    for (const [k, x] of entries) flatten(x, prefix ? `${prefix}${Array.isArray(v) ? `[${k}]` : `.${k}`}` : k, out);
    return out;
  }
  out.set(prefix || "(root)", v === undefined ? "undefined" : JSON.stringify(v));
  return out;
}

export type DiffKind = "added" | "removed" | "changed" | "same";
export function keyDiff(before: unknown, after: unknown): { key: string; kind: DiffKind; before?: string; after?: string }[] {
  const a = before === null || before === undefined ? new Map<string, string>() : flatten(before);
  const b = after === null || after === undefined ? new Map<string, string>() : flatten(after);
  const keys = [...new Set([...a.keys(), ...b.keys()])].sort();
  return keys.map((key) => {
    const x = a.get(key);
    const y = b.get(key);
    const kind: DiffKind = x === undefined ? "added" : y === undefined ? "removed" : x === y ? "same" : "changed";
    return { key, kind, before: x, after: y };
  });
}
