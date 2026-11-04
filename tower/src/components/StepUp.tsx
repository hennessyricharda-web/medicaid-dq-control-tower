"use client";
/**
 * Challenge-bound step-up re-authentication, shared by the maker (DecisionPanel) and checker (CheckerFlow).
 * POST {step_up_url} {username, step_up: true, challenge} → access_token, valid for 5 minutes (auth_time).
 * The token is held only in the caller's component state and is dropped after one use.
 */
import { useEffect, useState } from "react";
import { ApiError, idpToken } from "@/lib/api";
import { shortHash } from "@/lib/format";
import type { ChallengeResponse, VerdictChallenge } from "@/lib/types";

export const STEP_UP_WINDOW_MS = 5 * 60_000; // auth_time ≤ 5 min (DESIGN §2.4)

export interface StepUpGrant {
  token: string;
  at: number;
}

/** Milliseconds left in the step-up window, ticking once a second while a grant is held. */
export function useStepUpCountdown(grant: StepUpGrant | null): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!grant) return;
    setNow(Date.now());
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, [grant]);
  return grant ? STEP_UP_WINDOW_MS - (now - grant.at) : 0;
}

export function StepUpWindow({ leftMs }: { leftMs: number }) {
  return (
    <p className={leftMs > 0 ? "hint" : "hint err"} aria-live="polite">
      {leftMs > 0 ? `Step-up valid for about ${Math.ceil(leftMs / 1000)}s.` : "Step-up window has passed. Request a new challenge."}
    </p>
  );
}

/** Username prompt for the T1 IdP, bound to one challenge. */
export function StepUpForm({
  ch,
  sessionUsername,
  inputId,
  disabled,
  onGranted,
  onError,
}: {
  /** Unit challenges carry the bound seat; change-request and known-issue challenges bind a verdict instead. */
  ch: ChallengeResponse | (VerdictChallenge & { x_seat?: undefined });
  sessionUsername: string;
  inputId: string;
  disabled: boolean;
  onGranted: (g: StepUpGrant) => void;
  onError: (msg: string) => void;
}) {
  const [username, setUsername] = useState("");
  const [busy, setBusy] = useState(false);

  async function stepUp() {
    if (username.trim() !== sessionUsername) {
      onError("Step-up must be performed by the signed-in reviewer. The username does not match this session.");
      return;
    }
    setBusy(true);
    try {
      const token = await idpToken(username.trim(), { challenge: ch.challenge, url: ch.step_up_url });
      onGranted({ token, at: Date.now() });
    } catch (e) {
      const ae = e instanceof ApiError ? e : new ApiError(0, { detail: String(e) });
      onError(`Step-up failed: ${ae.message}`);
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <p className="hint">
        Challenge <code title={ch.challenge}>{shortHash(ch.challenge, 16)}</code>
        {ch.x_seat ? (
          <>
            {" "}
            · seat <span className="mono">{ch.x_seat}</span>
          </>
        ) : null}
        {ch.step_up_url ? (
          <>
            {" "}
            · step_up_url <code>{ch.step_up_url}</code>
          </>
        ) : null}
      </p>
      <label htmlFor={inputId}>Re-enter your username</label>
      <div className="row">
        <input
          id={inputId}
          type="text"
          autoComplete="off"
          value={username}
          onChange={(e) => setUsername(e.target.value)}
          style={{ flex: 1 }}
          disabled={disabled || busy}
        />
        <button type="button" onClick={() => void stepUp()} disabled={disabled || busy || !username.trim()}>
          {busy ? "Stepping up…" : "Step up"}
        </button>
      </div>
    </>
  );
}

/** One-line description of the remaining-coverage state for an outcome banner. */
export function coverageNote(missing: string[] | undefined): string {
  if (!missing || missing.length === 0) return "";
  return ` Another approver holding the ${missing.join(", ")} duty must confirm before it can apply.`;
}
