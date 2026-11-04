/**
 * PARTE_DESIGN §7: a plain-language explanation over control-plane facts only. Plain text first; the source
 * and the fact check are shown small, and a FALLBACK (the provider's text failed the check, so the template
 * text is shown) is stated plainly. The LLM is never in the decision path.
 */
import { ToneChip } from "./Chip";
import { shortHash } from "@/lib/format";
import type { Explanation as E } from "@/lib/types";

/**
 * `sourceMode` (from the citations or the trace, when the caller has it): "fact-checked" is claimed only for a LIVE
 * or RECORDED text that passed; SCRIPTED reads "scripted, checked"; without it a pass reads "checked".
 */
export function Explanation({ e, label = "Explanation", compact, sourceMode }: { e: E | null | undefined; label?: string; compact?: boolean; sourceMode?: string | null }) {
  if (!e || !e.text) {
    return (
      <p className="hint err" role="note">
        {label}: none was supplied by the server. Every decision object must carry one; treat this as a contract defect.
      </p>
    );
  }
  const fallback = e.fact_check === "FALLBACK";
  const template = e.fact_check === "TEMPLATE";
  return (
    <div className={`explanation${compact ? " compact" : ""}`} role="note" aria-label={label}>
      <p className="explanation-text">{e.text}</p>
      <div className="explanation-meta">
        {template ? null : <ToneChip tone="neutral" label={e.source} title={`Explanation source: ${e.source}`} />}
        {fallback ? (
          <ToneChip tone="warn" label="FALLBACK" title="The configured provider's text failed the fact check; the template text is shown instead" />
        ) : template ? (
          <ToneChip tone="neutral" label="deterministic template" title="Generated only from the facts by the built-in template; not fact-checked" />
        ) : (
          <ToneChip
            tone="ok"
            label={sourceMode === "LIVE" || sourceMode === "RECORDED" ? "fact-checked" : sourceMode === "SCRIPTED" ? "scripted, checked" : "checked"}
            title={
              sourceMode === "SCRIPTED"
                ? "Scripted text (no model is called) passed the check against the facts"
                : "Every number and identifier was checked against the facts"
            }
          />
        )}
        {fallback ? <span className="hint">The provider&rsquo;s wording failed the fact check, so the template wording is shown.</span> : null}
        <span className="hint">
          facts <code title={e.facts_hash}>{shortHash(e.facts_hash, 8)}</code> · text <code title={e.text_hash}>{shortHash(e.text_hash, 8)}</code>
        </span>
      </div>
    </div>
  );
}
