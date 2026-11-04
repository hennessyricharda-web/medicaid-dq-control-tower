"use client";
/** The card rail's trace link (Part F P1-1 §I): every traced step that names this unit, in the trace explorer. */
import Link from "next/link";
import type { DecisionCard } from "@/lib/types";

export function TraceSlot({ card }: { card: DecisionCard }) {
  return (
    <section className="panel" aria-labelledby="trace-h">
      <h2 id="trace-h">Trace</h2>
      <p className="hint" style={{ margin: "0 0 6px" }}>
        The traced steps that name this unit: release checks, evaluations and explanations.
      </p>
      <Link className="btn" href={`/runs?unit=${encodeURIComponent(card.unit_key)}`} style={{ textDecoration: "none" }}>
        Open this unit&rsquo;s trace
      </Link>
    </section>
  );
}
