"use client";
/** Shared pieces of the trace explorer: source-mode and status chips, evidence-ref links, time helpers. */
import Link from "next/link";
import { ToneChip } from "@/components/Chip";
import type { Tone } from "@/lib/dispositions";

/**
 * The provenance of generated text, exactly as recorded. TEMPLATE: the built-in template, no model. SCRIPTED: a
 * scripted transport, no model is called. RECORDED: a recorded model response replayed. LIVE: a model was called.
 * A missing value is shown as such and never defaults to LIVE.
 */
const MODE: Record<string, { label: string; title: string }> = {
  TEMPLATE: { label: "TEMPLATE", title: "Built-in deterministic template; no model is called" },
  SCRIPTED: { label: "SCRIPTED", title: "Scripted transport; no model is called" },
  RECORDED: { label: "RECORDED", title: "A recorded model response, replayed; no model is called now" },
  LIVE: { label: "LIVE", title: "A model was called for this text" },
};
export function SourceModeChip({ mode }: { mode: string | null | undefined }) {
  if (!mode) return <span className="src NONE" title="No source mode recorded for this row">not recorded</span>;
  const m = MODE[mode];
  return (
    <span className={`src ${m ? mode : "NONE"}`} title={m?.title ?? `Unrecognised source mode ${mode}`}>
      {m?.label ?? `UNKNOWN: ${mode}`}
    </span>
  );
}

const STATUS_TONE: Record<string, Tone> = { COMPLETED: "ok", OK: "ok", STARTED: "info", RESUMED: "info", FAILED: "bad", ERROR: "bad", SKIPPED: "neutral" };
export function StatusChip({ status }: { status: string | null | undefined }) {
  if (!status) return <ToneChip tone="neutral" label="no events" />;
  return <ToneChip tone={STATUS_TONE[status] ?? "warn"} label={status} />;
}

/** An evidence ref (dqagent/cp/trace_schema.py REFS_PREFIXES) as a link where the Tower has a screen for it. */
export function refTarget(ref: string): string | null {
  const i = ref.indexOf(":");
  const kind = ref.slice(0, i);
  const id = ref.slice(i + 1);
  switch (kind) {
    case "unit":
      return `/units/${encodeURIComponent(id)}`;
    case "cr":
      return `/governance/${encodeURIComponent(id)}`;
    case "trace":
      return `/runs/${encodeURIComponent(id)}`;
    case "rule":
      return `/rules`;
    case "dec":
    case "decisions":
      return null;
    default:
      return null;
  }
}
export function RefLink({ refId, resolvable = true }: { refId: string; resolvable?: boolean }) {
  const href = resolvable ? refTarget(refId) : null;
  if (!resolvable)
    return (
      <span className="mono muted" title="Not resolvable in your scope">
        {refId}
      </span>
    );
  return href ? (
    <Link className="mono" href={href}>
      {refId}
    </Link>
  ) : (
    <span className="mono" title="No Tower screen for this kind of object yet">
      {refId}
    </span>
  );
}

export function durMs(a: string, b: string): number {
  const d = Date.parse(b) - Date.parse(a);
  return Number.isFinite(d) ? Math.max(0, d) : 0;
}
export function fmtDur(ms: number): string {
  if (ms < 1000) return `${ms} ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(ms < 10_000 ? 2 : 1)} s`;
  return `${Math.floor(ms / 60_000)} min ${Math.round((ms % 60_000) / 1000)} s`;
}
/** A span attr value for display: counts objects as k=v pairs. */
export function fmtAttr(v: unknown): string {
  if (v && typeof v === "object") return Object.entries(v as Record<string, unknown>).map(([k, x]) => `${k}=${String(x)}`).join(", ") || "{}";
  return String(v);
}
