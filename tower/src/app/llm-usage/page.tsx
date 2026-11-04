"use client";
/**
 * LLM usage (wireframe 23). The configured explainer as the API reports it, then the recorded text-generation
 * calls, grouped by day, source mode, provider and transport exactly as GET /api/v1/llm/usage (obs_routes.py
 * F-35 / P2-1) returns them — computed server-side over dq_ctl.llm_calls, never in the browser. source_mode is
 * shown exactly as recorded (TEMPLATE, SCRIPTED, RECORDED, LIVE); nothing defaults to LIVE.
 */
import { useAuth } from "@/lib/auth";
import { useObs } from "@/lib/api/obs";
import { useLoad } from "@/lib/useLoad";
import type { Usage as UsageResponse } from "@/lib/types/obs";
import { SignIn } from "@/components/SignIn";
import { ErrorNote } from "@/components/ErrorNote";
import { ToneChip } from "@/components/Chip";
import { PageBody, PageHeader } from "@/components/layout/PageHeader";
import { EmptyState } from "@/components/layout/EmptyState";
import { useShellData } from "@/components/layout/ShellData";
import { SourceModeChip } from "@/components/obs/common";

const DAYS = 30;

function Usage() {
  const obs = useObs();
  const { summary } = useShellData();
  const { data: usage, error, loading } = useLoad<UsageResponse>(() => obs.usage(DAYS), [obs]);

  const ex = summary?.explainer;
  const rows = usage?.rows ?? [];
  return (
    <>
      <PageHeader title="LLM usage" sub="the explainer's recorded text-generation calls, by day" />
      <PageBody>
        <section className="panel" aria-labelledby="lu-cfg">
          <h2 id="lu-cfg">Configured explainer</h2>
          {ex ? (
            <dl className="kv">
              <dt>Source mode</dt>
              <dd>
                <SourceModeChip mode={ex.source_mode ?? null} />{" "}
                {ex.source_mode === "LIVE" ? null : <span className="hint">no model is called in this mode</span>}
              </dd>
              <dt>Provider</dt>
              <dd className="mono">{ex.provider}</dd>
              <dt>Transport</dt>
              <dd className="mono">{ex.transport ?? "not reported"}</dd>
              <dt>Label</dt>
              <dd>{ex.label ?? "not reported"}</dd>
              <dt>Decisions</dt>
              <dd>{ex.decisions}: the explainer is never in the decision path</dd>
              {ex.x_transport_refused ? (
                <>
                  <dt>Refused</dt>
                  <dd>
                    <ToneChip tone="bad" label="transport refused" /> {ex.x_transport_refused}
                  </dd>
                </>
              ) : null}
            </dl>
          ) : (
            <p className="hint">Loading the explainer status&hellip;</p>
          )}
        </section>
        <ErrorNote error={error} what="llm usage" />
        <p className="hint">Computed by the server over the last {usage?.days ?? DAYS} day(s) of recorded calls in your scope (GET /api/v1/llm/usage).</p>
        <section className="panel" aria-labelledby="lu-use">
          <h2 id="lu-use">Calls by day and source</h2>
          {loading && !usage ? (
            <p className="hint">Loading&hellip;</p>
          ) : rows.length === 0 ? (
            <EmptyState inline title="No text-generation calls recorded in your scope." />
          ) : (
            <table className="dt">
              <thead>
                <tr>
                  <th scope="col">Day</th>
                  <th scope="col">Source</th>
                  <th scope="col">Provider</th>
                  <th scope="col">Transport</th>
                  <th scope="col" className="num">
                    Calls
                  </th>
                  <th scope="col">Check verdicts</th>
                  <th scope="col" className="num">
                    Tokens in / out
                  </th>
                  <th scope="col" className="num">
                    Latency p50 / p95
                  </th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r, i) => (
                  <tr key={`${r.day}|${r.provider}|${r.transport}|${r.source_mode}|${i}`}>
                    <td className="mono">{r.day}</td>
                    <td>
                      <SourceModeChip mode={r.source_mode} />
                    </td>
                    <td className="mono">{r.provider}</td>
                    <td className="mono">{r.transport}</td>
                    <td className="num">{r.calls}</td>
                    <td>{Object.entries(r.verdicts).map(([v, n]) => `${v} ${n}`).join(", ")}</td>
                    <td className="num">
                      {r.input_tokens} / {r.output_tokens}
                      {r.tokens_label ? <div className="hint">{r.tokens_label}</div> : null}
                    </td>
                    <td className="num">
                      {r.latency_ms_p50 ?? "–"} / {r.latency_ms_p95 ?? "–"} ms
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </section>
      </PageBody>
    </>
  );
}

export default function Page() {
  const { session } = useAuth();
  if (!session)
    return (
      <>
        <PageHeader title="LLM usage" />
        <PageBody>
          <SignIn />
        </PageBody>
      </>
    );
  return <Usage />;
}
