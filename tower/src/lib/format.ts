export function shortHash(s: string, n = 12): string {
  if (!s) return "";
  return s.length <= n ? s : `${s.slice(0, n)}…`;
}

export function slaMinutesFromNow(iso: string | null | undefined, now: number = Date.now()): number | null {
  if (!iso) return null;
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return null;
  return Math.round((t - now) / 60000);
}

export function describeMinutes(m: number | null): string {
  if (m === null) return "unknown";
  const abs = Math.abs(m);
  const txt = abs >= 120 ? `${Math.floor(abs / 60)}h ${abs % 60}m` : `${abs} min`;
  return m < 0 ? `overdue ${txt}` : `in ${txt}`;
}

export function fmtTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return iso;
  return new Date(t).toISOString().replace("T", " ").replace(/\.\d+Z$/, "Z");
}

export function fmtNum(v: unknown): string {
  if (v === null || v === undefined) return "—";
  if (typeof v === "number") return Number.isInteger(v) ? v.toLocaleString("en-US") : v.toLocaleString("en-US", { maximumFractionDigits: 4 });
  if (typeof v === "object") return JSON.stringify(v);
  return String(v);
}

export function encUnit(k: string): string {
  return encodeURIComponent(k);
}

/** A rule threshold as the API sends it (F-30): a number, a string, an object such as {min, max}, or null. */
export function fmtThreshold(v: unknown): string {
  if (v === null || v === undefined) return "none";
  if (typeof v === "object" && !Array.isArray(v)) {
    const parts = Object.entries(v as Record<string, unknown>).map(([k, x]) => `${k} ${fmtNum(x)}`);
    return parts.length ? parts.join(", ") : "none";
  }
  return fmtNum(v);
}
