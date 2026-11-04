"use client";
/**
 * K11 (PARTF_K7_K12_MOCK_CONTRACT.md): the decision-card feedback control. PRE-BACKEND -- an INVENTED mechanism,
 * `POST /explanation-feedback`, with no real counterpart. The real mechanism this would eventually feed,
 * `test_feedback_creates_candidate_and_hides_comment`, does not exist in this codebase, so this control claims
 * only ONE thing on success: "Feedback recorded." It never claims an evaluation candidate was created, and never
 * implies any promotion or track-record state changed -- that is real backend logic this pass does not simulate.
 *
 * Kept out of components/Evidence.tsx on purpose: that file's own header states "nothing here can change state"
 * and holds no hooks; this control needs local state (open/closed, the chosen verdict) and submits a write, so it
 * lives in its own client component instead of changing Evidence.tsx's contract.
 */
import { useState } from "react";
import { ApiError } from "@/lib/api";
import { useObs } from "@/lib/api/obs";
import type { ExplanationFeedbackBody } from "@/lib/types/obs";
import { PreBackendBanner } from "@/components/layout/PreBackendBanner";
import { PREVIEW_K7_K12 } from "@/lib/config";

const VERDICTS: { id: ExplanationFeedbackBody["verdict"]; label: string }[] = [
  { id: "helpful", label: "Helpful" },
  { id: "unhelpful", label: "Not helpful" },
  { id: "wrong", label: "Wrong" },
];

export function ExplanationFeedback({ subject }: { subject: string }) {
  const obs = useObs();
  const [open, setOpen] = useState(false);
  // K8-K12 preview gate: OFF by default; run_demo.ps1 never sets NEXT_PUBLIC_DQ_PREVIEW_K7_K12. Guarded here,
  // inside the component, so the unit page looks exactly as it did before this build -- fully absent, not
  // disabled-and-visible -- regardless of where this component is later used.
  const gated = !PREVIEW_K7_K12;
  const [verdict, setVerdict] = useState<ExplanationFeedbackBody["verdict"] | null>(null);
  const [comment, setComment] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<ApiError | null>(null);
  const [recorded, setRecorded] = useState(false);

  if (gated) return null;
  if (recorded) {
    return (
      <p className="hint" role="status" style={{ marginTop: 4 }}>
        Feedback recorded.
      </p>
    );
  }
  if (!open) {
    return (
      <button type="button" className="sm" onClick={() => setOpen(true)} style={{ marginTop: 4 }}>
        Feedback on this explanation
      </button>
    );
  }

  async function submit() {
    if (!verdict) return;
    setBusy(true);
    setErr(null);
    try {
      await obs.explanationFeedback({ subject, verdict, comment: comment.trim() || undefined });
      setRecorded(true);
    } catch (e) {
      setErr(e instanceof ApiError ? e : new ApiError(0, { detail: String(e) }));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="panel" style={{ marginTop: 6, padding: 10 }} aria-labelledby="ef-h">
      <h3 id="ef-h" style={{ fontSize: "var(--t-sm)", margin: "0 0 6px" }}>
        Feedback on this explanation
      </h3>
      <PreBackendBanner detail="Invented mock route (POST /explanation-feedback); no real backend route exists for this yet. Recording feedback here never creates an evaluation candidate or changes any promotion/track-record state." />
      <div className="row" role="radiogroup" aria-label="Verdict">
        {VERDICTS.map((v) => (
          <button
            key={v.id}
            type="button"
            className={verdict === v.id ? "sm primary" : "sm"}
            role="radio"
            aria-checked={verdict === v.id}
            onClick={() => setVerdict(v.id)}
          >
            {v.label}
          </button>
        ))}
      </div>
      <label htmlFor="ef-comment">Comment (optional)</label>
      <textarea id="ef-comment" value={comment} onChange={(e) => setComment(e.target.value)} rows={2} />
      {err ? (
        <div role="alert" className="banner t-bad" style={{ marginTop: 6 }}>
          {err.message}
        </div>
      ) : null}
      <div className="row" style={{ marginTop: 6 }}>
        <button type="button" className="primary" onClick={() => void submit()} disabled={!verdict || busy}>
          {busy ? "Submitting…" : "Submit feedback"}
        </button>
        <button type="button" onClick={() => setOpen(false)}>
          Cancel
        </button>
      </div>
    </div>
  );
}
