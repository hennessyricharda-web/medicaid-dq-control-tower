"use client";
/**
 * Checker (second approver) flow, DESIGN §2.4 / SEC-W1-4/10:
 *   review the maker's decision → POST /challenge {} (binds the maker's decision id, verb and quarantine set)
 *   → step-up re-auth bound to the challenge → POST /confirm {decision_id, step_up_token, expected_state_version}.
 * The step-up token lives only in this component's state and is dropped after use.
 */
import { useState } from "react";
import { refOf } from "@/lib/partE";
import { ApiError } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import type { ChallengeResponse, DecisionCard } from "@/lib/types";
import type { Outcome } from "./DecisionPanel";
import { coverageNote, StepUpForm, StepUpWindow, useStepUpCountdown, type StepUpGrant } from "./StepUp";

export function CheckerFlow({
  card,
  locked,
  onStale,
  onOutcome,
}: {
  card: DecisionCard;
  locked: boolean;
  onStale: (why: string) => void;
  onOutcome: (o: Outcome) => void;
}) {
  const { client, session } = useAuth();
  const pend = card.x_pending_decision;
  const [reviewed, setReviewed] = useState(false);
  const [ch, setCh] = useState<ChallengeResponse | null>(null);
  const [stepUp, setStepUp] = useState<StepUpGrant | null>(null);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const leftMs = useStepUpCountdown(stepUp);

  if (!pend || !session) return null;
  // Separation of duties: anyone who already approved this round (maker or an earlier checker) cannot approve again.
  const alreadyApproved =
    card.approvals.makers.some((m) => m.person_id === session.me.person_id) || pend.maker_person_id === session.me.person_id;
  const isLast = card.approvals.have + 1 >= card.approvals.required;

  function startOver() {
    setCh(null);
    setStepUp(null);
  }

  function fail(e: unknown, stage: string) {
    const ae = e instanceof ApiError ? e : new ApiError(0, { detail: String(e) });
    if (ae.isStale) {
      onStale(ae.message);
      return;
    }
    if (ae.isReplay || ae.isStepUpProblem) {
      startOver();
      setErr(
        ae.isReplay
          ? `${stage} refused: this step-up was already used (REPLAY). Request a new challenge and step up again.`
          : `${stage} refused: the step-up is missing, invalid or expired (${ae.message}). Request a new challenge.`,
      );
      return;
    }
    if (ae.isForbidden) {
      setErr(`${stage} not permitted (duty, scope or separation of duties): ${ae.message}`);
      return;
    }
    setErr(`${stage} failed: ${ae.message}`);
  }

  async function requestChallenge() {
    setBusy(true);
    setErr(null);
    try {
      const c = await client.challenge(card.unit_key, card.explanation ? { explanation_ref: refOf(card.explanation) } : {});
      if (c.x_seat !== "CHECKER") {
        // The pending maker decision disappeared between the card read and the challenge.
        onStale("The pending maker decision is no longer on this unit.");
        return;
      }
      setCh(c);
      setStepUp(null);
    } catch (e) {
      fail(e, "Challenge");
    } finally {
      setBusy(false);
    }
  }

  async function confirm() {
    if (!stepUp) return;
    const token = stepUp.token;
    setStepUp(null); // a step-up token is single-use, whatever the outcome
    setBusy(true);
    setErr(null);
    try {
      const trimmed = note.trim();
      const r = await client.confirm(card.unit_key, {
        decision_id: pend!.decision_id,
        step_up_token: token,
        expected_state_version: card.state_version,
        ...(trimmed ? { rationale: trimmed } : {}),
      });
      setCh(null);
      const missing = r.x_missing_duties ?? [];
      onOutcome(
        r.status === "APPLIED"
          ? { tone: "ok", title: `${pend!.decision} confirmed and applied`, body: `Decision ${r.decision_id}. ${r.reason}` }
          : r.status === "AWAITING_COVERAGE"
            ? {
                tone: "info",
                title: "Confirmation recorded — awaiting coverage",
                body: `Decision ${r.decision_id}. ${r.reason}.${coverageNote(missing)}`,
              }
            : {
                tone: "info",
                title: "Confirmation recorded — awaiting another approver",
                body: `Decision ${r.decision_id}. ${r.reason}.${coverageNote(missing)}`,
              },
      );
    } catch (e) {
      fail(e, "Confirm");
    } finally {
      setBusy(false);
    }
  }

  const stage = !ch ? 1 : !stepUp ? 2 : 3;

  return (
    <section className="panel" aria-labelledby="checker-h" style={{ borderColor: "var(--focus)", borderWidth: 2 }}>
      <h2 id="checker-h">Checker confirmation</h2>
      <dl className="kv">
        <dt>Decision</dt>
        <dd className="mono">
          {pend.decision} ({pend.decision_id})
        </dd>
        <dt>Maker</dt>
        <dd className="mono">{pend.maker_person_id}</dd>
        <dt>Quarantine</dt>
        <dd>{pend.quarantine_row_seqs.length ? `${pend.quarantine_row_seqs.length} row(s): ${pend.quarantine_row_seqs.join(", ")}` : "none"}</dd>
      </dl>
      <p style={{ marginTop: 8 }}>
        <strong>Maker&rsquo;s rationale</strong>
      </p>
      <blockquote style={{ margin: "0 0 10px", padding: "6px 10px", borderLeft: "3px solid var(--border-strong)" }}>{pend.rationale}</blockquote>
      {isLast ? <p className="hint">Your confirmation may be the last approval. If every required duty is covered, it applies the decision atomically.</p> : null}

      {alreadyApproved ? (
        <p className="banner t-info">You have already approved this unit, so you cannot confirm it (separation of duties). Another approver must confirm.</p>
      ) : (
        <fieldset disabled={locked || busy} style={{ border: "none", padding: 0, margin: 0 }}>
          <ol className="steps">
            <li className={stage > 1 ? "done" : "active"}>
              <label style={{ fontWeight: 500, display: "flex", gap: 8, alignItems: "flex-start" }}>
                <input type="checkbox" checked={reviewed} onChange={(e) => setReviewed(e.target.checked)} style={{ marginTop: 4 }} />
                <span>I reviewed the dispositions, ABC and dry-run on this card myself, not only the maker&rsquo;s rationale.</span>
              </label>
              <button type="button" onClick={() => void requestChallenge()} disabled={!reviewed || stage > 1} style={{ marginTop: 6 }}>
                Request challenge
              </button>
            </li>
            <li className={stage > 2 ? "done" : stage === 2 ? "active" : ""}>
              <strong>Step-up re-authentication</strong>
              {ch ? (
                <StepUpForm
                  ch={ch}
                  sessionUsername={session.username}
                  inputId="checker-stepup-user"
                  disabled={locked || busy || stage !== 2}
                  onGranted={(g) => {
                    setErr(null);
                    setStepUp(g);
                  }}
                  onError={setErr}
                />
              ) : (
                <p className="hint">Available after the challenge is issued.</p>
              )}
            </li>
            <li className={stage === 3 ? "active" : ""}>
              <strong>Confirm</strong>
              {stepUp ? (
                <>
                  <StepUpWindow leftMs={leftMs} />
                  <label htmlFor="checker-note">Checker rationale (optional; recorded with your confirmation)</label>
                  <textarea id="checker-note" value={note} onChange={(e) => setNote(e.target.value)} autoComplete="off" spellCheck />
                  <div className="row">
                    <button type="button" className="primary" onClick={() => void confirm()} disabled={leftMs <= 0}>
                      Confirm {pend.decision.replace(/_/g, " ")}
                    </button>
                    <button type="button" onClick={startOver}>
                      Start over
                    </button>
                  </div>
                </>
              ) : (
                <p className="hint">Available after step-up.</p>
              )}
            </li>
          </ol>
        </fieldset>
      )}
      {err ? (
        <p role="alert" className="err">
          {err}
        </p>
      ) : null}
    </section>
  );
}
