/** Client for the observability routes (dqagent/api/obs_routes.py). Shapes: lib/types/obs.ts. */
import { useMemo } from "react";
import { call } from "../api";
import { useAuth } from "../auth";
import type { Citations, ExplanationFeedbackBody, ExplanationFeedbackResponse, GuardrailList, OtlpExport, RunDetail, RunDiff, RunList, UnitTrace, Usage } from "../types/obs";

export interface RunQuery {
  kind?: string;
  pipeline?: string;
  night?: string;
  limit?: number;
  cursor?: string | null;
}
export interface GuardrailQuery {
  code?: string;
  subject?: string;
  trace_id?: string;
  limit?: number;
  cursor?: string | null;
}

function qs(q: object): string {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(q)) if (v !== undefined && v !== null && v !== "") p.set(k, String(v));
  const s = p.toString();
  return s ? `?${s}` : "";
}

export function obsClient(token: string | null) {
  return {
    runs: (q: RunQuery = {}) => call<RunList>(token, "GET", `/runs${qs(q)}`),
    run: (traceId: string, expandRows = true) => call<RunDetail>(token, "GET", `/runs/${encodeURIComponent(traceId)}${expandRows ? "?expand=rows" : ""}`),
    otlp: (traceId: string) => call<OtlpExport>(token, "GET", `/runs/${encodeURIComponent(traceId)}/otlp`),
    unitTrace: (unitKey: string) => call<UnitTrace>(token, "GET", `/units/${encodeURIComponent(unitKey)}/trace`),
    citations: (subject: string, ref: { facts_hash: string; text_hash: string }) =>
      call<Citations>(token, "GET", `/explanations/${encodeURIComponent(subject)}/citations${qs({ explanation_ref: `${ref.facts_hash}:${ref.text_hash}` })}`),
    guardrails: (q: GuardrailQuery = {}) => call<GuardrailList>(token, "GET", `/guardrail-events${qs(q)}`),
    usage: (days?: number) => call<Usage>(token, "GET", `/llm/usage${qs({ days })}`),
    diff: (a: string, b: string) => call<RunDiff>(token, "GET", `/runs/diff${qs({ a, b })}`),
    /** K11 (PARTF_K7_K12_MOCK_CONTRACT.md). PRE-BACKEND: an INVENTED route; no real counterpart exists yet. Only
     *  records the comment and says so -- never creates an evaluation candidate, never touches promotion state. */
    explanationFeedback: (b: ExplanationFeedbackBody) => call<ExplanationFeedbackResponse>(token, "POST", "/explanation-feedback", b),
  };
}
export type ObsClient = ReturnType<typeof obsClient>;

export function useObs(): ObsClient {
  const { session } = useAuth();
  return useMemo(() => obsClient(session?.token ?? null), [session?.token]);
}
