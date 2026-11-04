"use client";
/**
 * A person's verdict with a challenge-bound step-up, shared by change requests, known issues and look-back items:
 *   choose a verdict → POST …/challenge {verdict} → step-up at the IdP with nonce = challenge
 *   → POST …/decide {verdict, [rationale], step_up_token}.
 * 403 = refused for this person (author, organisation, duty, or a token bound elsewhere) · 409 = STALE / resolved /
 * REPLAY · 428 = the step-up is missing · 401 = step-up invalid.
 * The step-up token lives only in this component's state and is dropped after one use.
 */
import { useState } from "react";
import { ApiError } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import type { Verdict, VerdictChallenge } from "@/lib/types";
import { StepUpForm, StepUpWindow, useStepUpCountdown, type StepUpGrant } from "./StepUp";

export interface VerdictOutcome {
  tone: "ok" | "info" | "warn" | "bad";
  title: string;
  body: string;
}

export interface VerdictOption<V extends string> {
  value: V;
  label: string;
  /** Rendered as a danger action, with `warning` shown while selected. */
  danger?: boolean;
  warning?: string;
}

const APPROVE_REJECT: VerdictOption<Verdict>[] = [
  { value: "APPROVED", label: "Approve" },
  { value: "REJECTED", label: "Reject", danger: true },
];

export function VerdictFlow<V extends string = Verdict>({
  idPrefix,
  what,
  reviewText,
  options,
  rationaleMin,
  forbiddenHint,
  challenge,
  decide,
  onOutcome,
  onStale,
  onFailed,
}: {
  idPrefix: string;
  /** e.g. "change request CR-1a2b" */
  what: string;
  reviewText: string;
  /** Defaults to Approve / Reject. */
  options?: VerdictOption<V>[];
  /** When set, a rationale of at least this many characters is required and sent with the decision. */
  rationaleMin?: number;
  forbiddenHint?: string;
  challenge: (v: V) => Promise<VerdictChallenge>;
  decide: (v: V, stepUpToken: string, rationale: string) => Promise<{ status: string }>;
  onOutcome: (o: VerdictOutcome) => void;
  onStale: (why: string) => void;
  /** Any other failure: the server may have acted anyway (e.g. a 5xx after commit), so the caller re-reads the truth. */
  onFailed?: () => void;
}) {
  const opts = (options ?? (APPROVE_REJECT as unknown as VerdictOption<V>[])) as VerdictOption<V>[];
  const { session } = useAuth();
  const [verdict, setVerdict] = useState<V>(opts[0]!.value);
  const [reviewed, setReviewed] = useState(false);
  const [rationale, setRationale] = useState("");
  const [ch, setCh] = useState<VerdictChallenge | null>(null);
  const [stepUp, setStepUp] = useState<StepUpGrant | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const leftMs = useStepUpCountdown(stepUp);
  if (!session) return null;
  const opt = opts.find((o) => o.value === verdict) ?? opts[0]!;
  const rationaleOk = rationaleMin === undefined || rationale.trim().length >= rationaleMin;

  function startOver() {
    setCh(null);
    setStepUp(null);
  }

  function fail(e: unknown, stage: string) {
    const ae = e instanceof ApiError ? e : new ApiError(0, { detail: String(e) });
    if (/REVOKED/.test(ae.message)) {
      startOver();
      setErr(`${stage} refused: ${ae.message}. A revoked version can never be approved again; the rule must change to a new version.`);
      onFailed?.();
      return;
    }
    if (ae.isStale) {
      startOver();
      onStale(ae.message);
      return;
    }
    if (ae.isReplay || ae.isStepUpProblem) {
      startOver();
      setErr(
        ae.isReplay
          ? `${stage} refused: this step-up was already used (REPLAY). Request a new challenge and step up again.`
          : `${stage} refused: a fresh step-up is required (${ae.message}). Request a new challenge.`,
      );
      return;
    }
    if (ae.isForbidden) {
      startOver();
      setErr(
        `${stage} refused (${ae.message}). ${forbiddenHint ?? "You may be the author, outside the approving organisation, already have decided, or lack the duty."}`,
      );
      return;
    }
    setErr(
      `${stage} failed: ${ae.message}.${ae.status >= 500 ? " The server may have recorded it anyway; the status above was re-read." : ""}`,
    );
    if (ae.status >= 500) startOver();
    onFailed?.();
  }

  async function requestChallenge() {
    setBusy(true);
    setErr(null);
    try {
      setCh(await challenge(verdict));
      setStepUp(null);
    } catch (e) {
      fail(e, "Challenge");
    } finally {
      setBusy(false);
    }
  }

  async function submit() {
    if (!stepUp) return;
    const token = stepUp.token;
    setStepUp(null); // single-use, whatever the outcome
    setBusy(true);
    setErr(null);
    try {
      const r = await decide(verdict, token, rationale.trim());
      setCh(null);
      onOutcome(
        r.status === "APPLIED"
          ? { tone: "ok", title: `${what}: approved and applied`, body: `Status ${r.status}.` }
          : { tone: "info", title: `${what}: ${opt.label.toLowerCase()} recorded`, body: `Status ${r.status}.` },
      );
    } catch (e) {
      fail(e, "Decision");
    } finally {
      setBusy(false);
    }
  }

  const stage = !ch ? 1 : !stepUp ? 2 : 3;
  const rid = `${idPrefix}-rationale`;

  return (
    <>
      <fieldset disabled={busy} style={{ border: "none", padding: 0, margin: 0 }}>
        <ol className="steps">
          <li className={stage > 1 ? "done" : "active"}>
            <fieldset style={{ border: "none", padding: 0, margin: "0 0 6px" }} disabled={stage > 1}>
              <legend className="skip">Verdict</legend>
              <div className="row" role="radiogroup" aria-label="Verdict">
                {opts.map((o) => (
                  <label key={o.value} style={{ fontWeight: 500, display: "inline-flex", gap: 6, alignItems: "center", margin: 0 }}>
                    <input type="radio" name={`${idPrefix}-verdict`} value={o.value} checked={verdict === o.value} onChange={() => setVerdict(o.value)} />
                    {o.label}
                  </label>
                ))}
              </div>
            </fieldset>
            {opt.warning ? (
              <p className="banner t-warn" role="note" style={{ margin: "4px 0 8px" }}>
                {opt.warning}
              </p>
            ) : null}
            {rationaleMin !== undefined ? (
              <>
                <label htmlFor={rid}>Rationale (at least {rationaleMin} characters; recorded with the decision)</label>
                <textarea
                  id={rid}
                  value={rationale}
                  onChange={(e) => setRationale(e.target.value)}
                  disabled={stage > 1}
                  aria-describedby={`${rid}-n`}
                  spellCheck
                />
                <p id={`${rid}-n`} className={rationaleOk ? "hint" : "hint err"}>
                  {rationale.trim().length}/{rationaleMin} characters
                </p>
              </>
            ) : null}
            <label style={{ fontWeight: 500, display: "flex", gap: 8, alignItems: "flex-start" }}>
              <input type="checkbox" checked={reviewed} onChange={(e) => setReviewed(e.target.checked)} style={{ marginTop: 4 }} disabled={stage > 1} />
              <span>{reviewText}</span>
            </label>
            <button type="button" onClick={() => void requestChallenge()} disabled={!reviewed || !rationaleOk || stage > 1} style={{ marginTop: 6 }}>
              Request challenge
            </button>
          </li>
          <li className={stage > 2 ? "done" : stage === 2 ? "active" : ""}>
            <strong>Step-up re-authentication</strong>
            {ch ? (
              <StepUpForm
                ch={ch}
                sessionUsername={session.username}
                inputId={`${idPrefix}-stepup-user`}
                disabled={busy || stage !== 2}
                onGranted={(g) => {
                  setErr(null);
                  setStepUp(g);
                }}
                onError={setErr}
              />
            ) : (
              <p className="hint">Available after the challenge is issued. The challenge binds the verdict and exactly the content shown.</p>
            )}
          </li>
          <li className={stage === 3 ? "active" : ""}>
            <strong>{opt.label}</strong>
            {stepUp ? (
              <>
                <StepUpWindow leftMs={leftMs} />
                <div className="row">
                  <button type="button" className={opt.danger ? "danger" : "primary"} onClick={() => void submit()} disabled={leftMs <= 0}>
                    {opt.label} {what}
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
      {err ? (
        <p role="alert" className="err">
          {err}
        </p>
      ) : null}
    </>
  );
}
