"use client";
/**
 * Maker decision panel. Guards against the tired-reviewer failure mode:
 * - only `allowed_decisions` render as buttons; nothing is preselected
 * - rationale is typed by the human (no prefill, no AI text), min 20 chars after trim
 * - a two-step select → review → submit, with the consequences restated in the review step
 * - releasing over FAIL/ERROR/unknown dispositions requires an explicit acknowledgement
 * - the request binds expected_state_version + bound_fingerprint of the card actually on screen
 * - every state-changing verb is challenge-bound (SEC-W1-4/10): POST /challenge {decision, quarantine_row_seqs}
 *   → IdP step-up with nonce = challenge (5-minute window) → POST /decide with step_up_token. HOLD needs none.
 */
import { refOf } from "@/lib/partE";
import { useState } from "react";
import { ApiError } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { RATIONALE_MIN } from "@/lib/config";
import { dispositionRank } from "@/lib/dispositions";
import { shortHash } from "@/lib/format";
import { DECISION_VERBS, type ChallengeResponse, type DecisionCard, type DecisionVerb, type SteppedVerb } from "@/lib/types";
import { dispositionCounts } from "./Evidence";
import { coverageNote, StepUpForm, StepUpWindow, useStepUpCountdown, type StepUpGrant } from "./StepUp";

export type Outcome = { tone: "ok" | "info" | "bad"; title: string; body: string };

const VERB_HELP: Record<DecisionVerb, string> = {
  RELEASE: "Release every row in this unit as-is.",
  QUARANTINE_AND_RELEASE: "Quarantine the proposed rows, release the rest.",
  HOLD: "Keep the unit held. Nothing is published.",
  REJECT: "Reject the unit. It will not be released.",
};

export function quarantineSet(card: DecisionCard): number[] {
  const s = new Set<number>();
  for (const a of card.proposed_actions)
    if (a.verb === "QUARANTINE_ROWS" && Array.isArray(a.params?.x_row_seqs))
      for (const n of a.params.x_row_seqs) if (Number.isInteger(n)) s.add(n);
  return [...s].sort((a, b) => a - b);
}

export function Approvals({ card, myId }: { card: DecisionCard; myId: string }) {
  const { required, have, makers } = card.approvals;
  const remaining = Math.max(0, required - have);
  return (
    <div>
      <div className="approvals" aria-label={`${have} of ${required} approvals`}>
        {Array.from({ length: Math.max(required, have) }, (_, i) => (
          <span key={i} className={`slot${i < have ? " filled" : ""}`} aria-hidden="true">
            {i < have ? "✓" : i + 1}
          </span>
        ))}
        <strong>
          {have} of {required} approval(s)
        </strong>
      </div>
      <p style={{ marginTop: 6 }}>
        {remaining === 0 ? "No further approvals required." : <strong>{remaining} more approval(s) required{have > 0 ? " from a different person" : ""}.</strong>}
      </p>
      {makers.length ? (
        <p className="hint">
          Approved by:{" "}
          {makers.map((m) => (
            <span key={`${m.person_id}:${m.seat}`} className="mono">
              {m.person_id} ({m.seat.toLowerCase()}){m.person_id === myId ? " (you)" : ""}{" "}
            </span>
          ))}
        </p>
      ) : (
        <p className="hint">No one has approved yet.</p>
      )}
    </div>
  );
}

/** F-30: a dry run can be absent (no proposal) and its chain count can be "not computed"; say so, never sum a null. */
function dryRunText(card: DecisionCard): string {
  const runs = card.proposed_actions.map((a) => a.dry_run).filter((d): d is NonNullable<typeof d> => !!d);
  if (!runs.length) {
    const why = card.proposed_actions.map((a) => a.x_dry_run_reason).find(Boolean);
    return `no dry run${why ? ` (${why})` : ""}`;
  }
  const rows = runs.reduce((n, d) => n + d.rows_quarantined, 0);
  const chains = runs.every((d) => d.chains !== null) ? `${runs.reduce((n, d) => n + (d.chains ?? 0), 0)} chain(s)` : "chains not computed";
  return `${rows} row(s) / ${chains} proposed for quarantine`;
}

export function DecisionPanel({
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
  const [choice, setChoice] = useState<DecisionVerb | null>(null);
  const [rationale, setRationale] = useState("");
  const [ackFailures, setAckFailures] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [ch, setCh] = useState<ChallengeResponse | null>(null);
  const [stepUp, setStepUp] = useState<StepUpGrant | null>(null);
  const leftMs = useStepUpCountdown(stepUp);

  // With a maker decision pending, /challenge binds the CHECKER seat and a second maker decide is DENIED,
  // so only HOLD is offered here; confirming happens in the checker panel.
  const pending = !!card.x_pending_decision;
  const allowed = DECISION_VERBS.filter((v) => card.allowed_decisions.includes(v) && (!pending || v === "HOLD"));
  const unsupported = card.allowed_decisions.filter((v) => !(DECISION_VERBS as readonly string[]).includes(v));
  const qset = quarantineSet(card);
  const severe = card.dispositions.filter((d) => dispositionRank(d.disposition) >= 7);
  const releasing = choice === "RELEASE" || choice === "QUARANTINE_AND_RELEASE";
  const needAck = releasing && severe.length > 0;
  const trimmed = rationale.trim();
  const rationaleOk = trimmed.length >= RATIONALE_MIN;
  const qOk = choice !== "QUARANTINE_AND_RELEASE" || qset.length > 0;
  const iAmMaker = !!session && card.approvals.makers.some((m) => m.person_id === session.me.person_id);
  const stepped = !!choice && choice !== "HOLD";
  const ready = !!choice && rationaleOk && qOk && (!needAck || ackFailures) && !locked && !busy;
  const canChallenge = ready && stepped && !ch;
  const canSubmit = ready && (!stepped || (!!stepUp && leftMs > 0));
  const qrows = choice === "QUARANTINE_AND_RELEASE" ? qset : [];

  /** The challenge binds the verb and quarantine set: any change to the choice discards it. */
  function dropChallenge() {
    setCh(null);
    setStepUp(null);
  }

  function choose(v: DecisionVerb | null) {
    setChoice(v);
    setAckFailures(false);
    setErr(null);
    dropChallenge();
  }

  function reset() {
    choose(null);
    setRationale("");
  }

  function fail(e: unknown, stage: string) {
    const ae = e instanceof ApiError ? e : new ApiError(0, { detail: String(e) });
    if (ae.isStale) return onStale(ae.message);
    if (ae.isReplay) {
      dropChallenge();
      return setErr(`${stage} refused: this step-up was already used (REPLAY). Request a new challenge and step up again.`);
    }
    if (ae.isStepUpProblem) {
      dropChallenge();
      return setErr(`${stage} refused: the step-up is missing, invalid or expired (${ae.message}). Request a new challenge.`);
    }
    if (ae.isForbidden) return setErr(`${stage} not permitted (duty, scope or separation of duties): ${ae.message}`);
    if (ae.status === 422) return setErr(`${stage} rejected as invalid: ${ae.message}`);
    setErr(`${stage} failed: ${ae.message}`);
  }

  async function requestChallenge() {
    if (!choice || choice === "HOLD" || !canChallenge) return;
    const verb: SteppedVerb = choice;
    setBusy(true);
    setErr(null);
    try {
      const c = await client.challenge(card.unit_key, { decision: verb, quarantine_row_seqs: qrows, explanation_ref: refOf(card.explanation) });
      if (c.x_seat !== "MAKER") {
        // Someone recorded a maker decision since this card was read; the server bound the checker seat.
        onStale("Another approver recorded a maker decision on this unit while you were reviewing it.");
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

  async function submit() {
    if (!choice || !canSubmit) return;
    const binding = { rationale: trimmed, expected_state_version: card.state_version, bound_fingerprint: card.fingerprint };
    const token = stepUp?.token;
    setStepUp(null); // a step-up token is single-use, whatever the outcome
    setBusy(true);
    setSubmitting(true);
    setErr(null);
    try {
      const r =
        choice === "HOLD"
          ? await client.decide(card.unit_key, { decision: "HOLD", quarantine_row_seqs: [], ...binding })
          : await client.decide(card.unit_key, { decision: choice, quarantine_row_seqs: qrows, step_up_token: token ?? "", ...binding });
      const done = choice;
      reset();
      const missing = r.x_missing_duties ?? [];
      onOutcome(
        r.status === "APPLIED"
          ? { tone: "ok", title: `${done} applied`, body: `${r.decision_id ? `Decision ${r.decision_id}. ` : ""}${r.reason}` }
          : r.status === "AWAITING_COVERAGE"
            ? {
                tone: "info",
                title: `${done} recorded — awaiting coverage`,
                body: `Decision ${r.decision_id}. ${r.reason}.${coverageNote(missing)}`,
              }
            : {
                tone: "info",
                title: `${done} recorded — awaiting checker`,
                body: `Decision ${r.decision_id}. ${r.reason}. A different approver must confirm with step-up re-authentication.${coverageNote(missing)}`,
              },
      );
    } catch (e) {
      fail(e, "Decision");
    } finally {
      setBusy(false);
      setSubmitting(false);
    }
  }

  return (
    <section className="panel" aria-labelledby="decide-h">
      <h2 id="decide-h">Decide</h2>
      <dl className="kv" style={{ marginBottom: 10 }}>
        <dt>Required mode</dt>
        <dd className="mono">{card.required_mode}</dd>
        <dt>Evidence</dt>
        <dd>
          {dispositionCounts(card.dispositions)
            .map(([d, n]) => `${n} ${d}`)
            .join(" · ") || "none"}
        </dd>
        <dt>Dry-run</dt>
        <dd>{dryRunText(card)}</dd>
        <dt>Binding</dt>
        <dd>
          <span className="mono">v{card.state_version}</span> · <code title={card.fingerprint}>{shortHash(card.fingerprint, 10)}</code>
        </dd>
      </dl>
      <Approvals card={card} myId={session?.me.person_id ?? ""} />

      {iAmMaker ? (
        <p className="banner t-info" style={{ marginTop: 8 }}>
          You have already approved this unit. A different person must confirm it.
        </p>
      ) : pending ? (
        <p className="hint" style={{ marginTop: 8 }}>
          A maker decision is pending on this unit. Confirm it in the checker panel; only HOLD is available here.
        </p>
      ) : null}

      {allowed.length === 0 ? (
        <p className="muted">No decisions are available to you on this unit in its current state.</p>
      ) : (
        <fieldset style={{ border: "none", padding: 0, margin: "10px 0 0" }} disabled={locked || busy}>
          <legend style={{ fontWeight: 650, marginBottom: 6 }}>1. Choose a decision</legend>
          <div className="row" role="group">
            {allowed.map((v) => (
              <button
                key={v}
                type="button"
                aria-pressed={choice === v}
                className={v === "REJECT" ? "danger" : undefined}
                onClick={() => choose(v)}
                title={VERB_HELP[v]}
              >
                {v.replace(/_/g, " ")}
              </button>
            ))}
          </div>
          {unsupported.length ? <p className="hint">Also allowed by the API but not actionable here: {unsupported.join(", ")}</p> : null}

          <div style={{ marginTop: 12 }}>
            <label htmlFor="rationale">2. Rationale (typed by you, at least {RATIONALE_MIN} characters)</label>
            <textarea
              id="rationale"
              value={rationale}
              onChange={(e) => setRationale(e.target.value)}
              aria-describedby="rationale-count"
              autoComplete="off"
              spellCheck
            />
            <div id="rationale-count" className={rationaleOk ? "hint" : "hint err"} aria-live="polite">
              {trimmed.length} / {RATIONALE_MIN} minimum{rationaleOk ? " ✓" : ""}
            </div>
          </div>

          {choice ? (
            <div className={`banner ${releasing ? "t-warn" : choice === "REJECT" ? "t-bad" : "t-info"}`} style={{ marginTop: 12 }}>
              <h3>3. Review: {choice.replace(/_/g, " ")}</h3>
              <p>{VERB_HELP[choice]}</p>
              <ul style={{ margin: "4px 0 8px", paddingLeft: 18 }}>
                <li>
                  Unit <span className="mono">{card.unit_key}</span> at state_version <span className="mono">{card.state_version}</span>
                </li>
                {choice === "QUARANTINE_AND_RELEASE" ? (
                  <li>
                    {qset.length ? (
                      <>
                        Quarantines {qset.length} row(s): <span className="mono">{qset.join(", ")}</span>
                      </>
                    ) : (
                      <strong>No quarantine row set is proposed, so this decision cannot be submitted.</strong>
                    )}
                  </li>
                ) : null}
                {choice === "RELEASE" && qset.length ? <li>Ignores the proposed quarantine of {qset.length} row(s). Every row is released.</li> : null}
                {card.approvals.required - card.approvals.have > 1 ? <li>Records your approval. A different approver must still confirm.</li> : null}
              </ul>
              {needAck ? (
                <label style={{ fontWeight: 500, display: "flex", gap: 8, alignItems: "flex-start" }}>
                  <input
                    type="checkbox"
                    checked={ackFailures}
                    onChange={(e) => {
                      setAckFailures(e.target.checked);
                      if (!e.target.checked) dropChallenge();
                    }}
                    style={{ marginTop: 4 }}
                  />
                  <span>
                    I have read the {severe.length} failing disposition(s) ({severe.map((d) => d.rule_id).join(", ")}) and still choose to release.
                  </span>
                </label>
              ) : null}
              {stepped ? (
                <ol className="steps" style={{ marginTop: 10 }}>
                  <li className={ch ? "done" : "active"}>
                    <strong>Request a challenge bound to this decision</strong>
                    <div className="row" style={{ marginTop: 4 }}>
                      <button type="button" onClick={() => void requestChallenge()} disabled={!canChallenge}>
                        {busy && !ch ? "Requesting…" : "Request challenge"}
                      </button>
                    </div>
                  </li>
                  <li className={stepUp ? "done" : ch ? "active" : ""}>
                    <strong>Step-up re-authentication</strong>
                    {ch ? (
                      <StepUpForm
                        ch={ch}
                        sessionUsername={session?.username ?? ""}
                        inputId="maker-stepup-user"
                        disabled={locked || busy || !!stepUp}
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
                  <li className={stepUp ? "active" : ""}>
                    <strong>Submit</strong>
                    {stepUp ? <StepUpWindow leftMs={leftMs} /> : <p className="hint">Available after step-up.</p>}
                  </li>
                </ol>
              ) : null}
              <div className="row" style={{ marginTop: 10 }}>
                <button type="button" className="primary" onClick={() => void submit()} disabled={!canSubmit}>
                  {submitting ? "Submitting…" : `Submit ${choice.replace(/_/g, " ")}`}
                </button>
                {stepped && ch ? (
                  <button type="button" onClick={dropChallenge} disabled={busy}>
                    Start over
                  </button>
                ) : null}
                <button type="button" onClick={() => choose(null)} disabled={busy}>
                  Cancel
                </button>
              </div>
              {!rationaleOk ? <p className="hint">Submit is disabled until the rationale has at least {RATIONALE_MIN} characters.</p> : null}
              {stepped && rationaleOk && !stepUp ? <p className="hint">Submit is enabled after a challenge-bound step-up (valid 5 minutes).</p> : null}
            </div>
          ) : null}
        </fieldset>
      )}
      {locked ? <p className="hint err">Decisions are locked until you acknowledge the reload banner above.</p> : null}
      {err ? (
        <p role="alert" className="err">
          {err}
        </p>
      ) : null}
    </section>
  );
}
