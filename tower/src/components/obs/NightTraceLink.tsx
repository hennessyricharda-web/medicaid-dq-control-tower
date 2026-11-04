"use client";
/** Links a simulated night to its NIGHT trace (GET /runs?kind=NIGHT&night=<date>). Engine-wide roots are visible to
 *  the operations or audit duty only, so other viewers are told that rather than shown a dead link. */
import Link from "next/link";
import { useEffect, useState } from "react";
import { useObs } from "@/lib/api/obs";
import type { RunRow } from "@/lib/types/obs";

export function NightTraceLink({ nightDate }: { nightDate: string }) {
  const obs = useObs();
  const [runs, setRuns] = useState<RunRow[] | null>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    let live = true;
    setRuns(null);
    setFailed(false);
    obs.runs({ kind: "NIGHT", night: nightDate, limit: 10 }).then(
      (r) => live && setRuns(r.runs),
      () => live && setFailed(true),
    );
    return () => {
      live = false;
    };
  }, [obs, nightDate]);
  if (failed) return <span className="hint">trace unavailable</span>;
  if (!runs) return null;
  if (!runs.length) return <span className="hint" title="NIGHT traces are engine-wide: the operations or audit duty reads them">no trace in your scope</span>;
  const latest = runs[0]!;
  return (
    <Link className="mono" href={`/runs/${encodeURIComponent(latest.trace_id)}`} style={{ fontSize: "var(--t-sm)", fontWeight: 400 }}>
      Trace {latest.trace_id}
    </Link>
  );
}
