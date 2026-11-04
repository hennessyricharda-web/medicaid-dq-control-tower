"use client";
/**
 * Citations under the card's plain-language explanation (Part F P1-1 §I): one chip per claim, linking the fact to
 * its evidence ref, plus the recorded source mode and the fact-check status. Template text is labelled "generated
 * from facts" (it is not fact-checked); a FALLBACK is never cached, so it has no recorded claims.
 */
import { useEffect, useState } from "react";
import { ApiError } from "@/lib/api";
import { useObs } from "@/lib/api/obs";
import type { Explanation } from "@/lib/types";
import type { Citations } from "@/lib/types/obs";
import { RefLink, SourceModeChip } from "./common";

function factCheckText(e: Explanation, mode: string | null): string {
  if (e.fact_check === "TEMPLATE") return "not fact-checked: the template writes only from the facts";
  if (e.fact_check === "FALLBACK") return "the provider's wording failed the fact check; the template wording is shown";
  if (e.fact_check === "PASS") return mode === "SCRIPTED" ? "scripted text (no model called) passed the fact check" : "passed the fact check";
  return `fact check: ${e.fact_check}`;
}

export function CitationSlot({ explanation, subject }: { explanation: Explanation | null | undefined; subject: string }) {
  const obs = useObs();
  const [data, setData] = useState<Citations | null>(null);
  const [err, setErr] = useState<ApiError | null>(null);
  const fh = explanation?.facts_hash;
  const th = explanation?.text_hash;
  useEffect(() => {
    let live = true;
    setData(null);
    setErr(null);
    if (!fh || !th) return;
    obs.citations(subject, { facts_hash: fh, text_hash: th }).then(
      (d) => live && setData(d),
      (e: unknown) => live && setErr(e instanceof ApiError ? e : new ApiError(0, { detail: String(e) })),
    );
    return () => {
      live = false;
    };
  }, [obs, subject, fh, th]);
  if (!explanation) return null;
  return (
    <div className="citations" aria-label="Citations for this explanation" style={{ marginTop: 4 }}>
      <div className="row" style={{ gap: 6, fontSize: "var(--t-sm)" }}>
        <strong>Claims</strong>
        {data ? <SourceModeChip mode={data.source_mode} /> : null}
        {data ? <span className="chip t-neutral">{data.label === "generated_from_facts" ? "generated from facts" : "checked claims"}</span> : null}
        <span className="hint" data-fact-check={explanation.fact_check}>
          {factCheckText(explanation, data?.source_mode ?? null)}
        </span>
      </div>
      {data ? (
        data.citations.length ? (
          <div className="cite-list" style={{ marginTop: 4 }}>
            {data.citations.map((c) => (
              <span key={c.fact_key} className={`cite${c.resolvable ? "" : " unres"}`} title={c.span?.shown ? `Cites: “${c.span.shown}”` : `Fact ${c.fact_key}`}>
                {c.fact_key}
                {c.evidence_ref ? (
                  <>
                    {" →"} <RefLink refId={c.evidence_ref} resolvable={c.resolvable} />
                  </>
                ) : (
                  " (no evidence ref)"
                )}
              </span>
            ))}
          </div>
        ) : (
          <p className="hint" style={{ margin: "4px 0 0" }}>
            No claims were recorded for this text.
          </p>
        )
      ) : err ? (
        <p className="hint" style={{ margin: "4px 0 0" }}>
          {err.status === 404
            ? explanation.fact_check === "FALLBACK"
              ? "No citations: a FALLBACK text is never cached, so no claims are recorded for it."
              : "No citations are recorded for this explanation."
            : `Citations could not be loaded: ${err.message}`}
        </p>
      ) : (
        <p className="hint" style={{ margin: "4px 0 0" }}>
          Loading citations…
        </p>
      )}
    </div>
  );
}
