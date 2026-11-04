import type { ApiError } from "@/lib/api";

/**
 * 403 is an access answer, not a failure: /audit needs the audit duty, and scoped endpoints need an
 * effective release duty (token group claim AND roster grant) in at least one authority.
 */
export function ErrorNote({ error, what }: { error: ApiError | null; what: string }) {
  if (!error) return null;
  if (error.status === 403) {
    return (
      <div role="alert" className="banner t-warn">
        <h2>You do not have access to {what}.</h2>
        <p>
          The control plane refused this request (HTTP 403). Audit requires the audit duty; decision queues, cards, pipelines
          and ABC require an effective release duty in at least one authority (your token&rsquo;s group claim and the duty
          roster must both grant it). Ask your duty administrator if this is unexpected.
        </p>
        <p className="hint">{error.message}</p>
      </div>
    );
  }
  return (
    <div role="alert" className="banner t-bad">
      <strong>Could not load {what}.</strong> {error.message}
    </div>
  );
}
