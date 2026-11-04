"use client";
import { useMemo, useState } from "react";
import { useAuth } from "@/lib/auth";
import { useLoad } from "@/lib/useLoad";
import { Gate } from "@/components/SignIn";
import { ErrorNote } from "@/components/ErrorNote";
import { DispositionChip } from "@/components/Chip";
import { TrendChart } from "@/components/TrendChart";
import { fmtNum, fmtTime } from "@/lib/format";
import type { MeasureRow, MeasureSeries, Thresholds } from "@/lib/types";
import { PageBody, PageHeader } from "@/components/layout/PageHeader";

const SMALL_CELL = 11;
const key = (b: string, n: string, r: string) => `${b}|${n}|${r}`;

function thText(t: Thresholds | null): string {
  if (!t) return "—";
  const parts = [t.min !== undefined && t.min !== null ? `≥ ${fmtNum(t.min)}` : null, t.max !== undefined && t.max !== null ? `≤ ${fmtNum(t.max)}` : null];
  const s = parts.filter(Boolean).join(" and ");
  return s || JSON.stringify(t);
}

function Measures() {
  const { session, client } = useAuth();
  const { data, error, loading, reload } = useLoad(session ? () => client.measures() : null, [session?.token]);
  const [sel, setSel] = useState<{ rule: string; node: string } | null>(null);

  // /measures is ordered newest first: the first row per (batch, node, rule) is the latest.
  const latest = useMemo(() => {
    const seen = new Map<string, MeasureRow>();
    for (const r of data ?? []) if (!seen.has(key(r.batch_id, r.node, r.rule_id))) seen.set(key(r.batch_id, r.node, r.rule_id), r);
    return [...seen.values()].sort((a, b) => a.rule_id.localeCompare(b.rule_id) || a.node.localeCompare(b.node) || a.batch_id.localeCompare(b.batch_id));
  }, [data]);
  const ruleIds = useMemo(() => [...new Set(latest.map((r) => r.rule_id))], [latest]);

  // The longitudinal disposition is carried by the series endpoint only: one read per measure.
  const { data: series } = useLoad(
    session && ruleIds.length ? () => Promise.all(ruleIds.map((r) => client.series(r))) : null,
    [session?.token, ruleIds.join(",")],
  );
  const longBy = useMemo(() => {
    const m = new Map<string, string | null>();
    for (const s of series ?? []) for (const p of s.points) m.set(key(p.batch_id, p.node, s.rule_id), p.long_disposition);
    return m;
  }, [series]);

  const { data: one, error: oneErr } = useLoad<MeasureSeries>(sel ? () => client.series(sel.rule, sel.node) : null, [sel?.rule, sel?.node]);

  return (
    <>
      <ErrorNote error={error} what="measures" />
      <section className="panel" aria-labelledby="m-h">
        <h2 id="m-h" className="row" style={{ justifyContent: "space-between" }}>
          <span>
            Latest measure per batch <span className="sub">{data ? `${latest.length} row(s) · ${ruleIds.length} measure(s)` : "loading…"}</span>
          </span>
          <button type="button" onClick={() => void reload()} disabled={loading}>
            Refresh
          </button>
        </h2>
        <p className="hint">Select a measure to see its trend. Counts are suppressed by the server when a denominator, numerator or complement is between 1 and {SMALL_CELL - 1} (small-cell rule).</p>
        <div className="tablewrap">
          <table>
            <caption className="skip">Latest value of each measure per batch and node; select a row to chart its trend</caption>
            <thead>
              <tr>
                <th scope="col">Measure</th>
                <th scope="col">Node</th>
                <th scope="col">Batch</th>
                <th scope="col" className="num">
                  Value
                </th>
                <th scope="col" className="num">
                  Num / den
                </th>
                <th scope="col">Threshold</th>
                <th scope="col">Disposition</th>
                <th scope="col">Longitudinal</th>
                <th scope="col">At</th>
              </tr>
            </thead>
            <tbody>
              {latest.map((r) => {
                const small = r.suppressed === true;   // decided by the server; counts never reach the browser
                const isSel = sel?.rule === r.rule_id && sel?.node === r.node;
                const ld = longBy.get(key(r.batch_id, r.node, r.rule_id));
                return (
                  <tr key={key(r.batch_id, r.node, r.rule_id)} className={isSel ? "selected" : undefined}>
                    <td>
                      <button
                        type="button"
                        className="linklike mono"
                        aria-pressed={isSel}
                        onClick={() => setSel({ rule: r.rule_id, node: r.node })}
                        aria-label={`Show trend for ${r.rule_id} on ${r.node}`}
                      >
                        {r.rule_id}
                      </button>
                    </td>
                    <td className="mono">{r.node}</td>
                    <td className="mono">{r.batch_id}</td>
                    <td className="num">{small ? "suppressed (n<11)" : fmtNum(r.value)}</td>
                    <td className="num">{small ? "<11" : `${fmtNum(r.numerator)} / ${fmtNum(r.denominator)}`}</td>
                    <td>{thText(r.thresholds)}</td>
                    <td>
                      <DispositionChip value={r.disposition} />
                    </td>
                    <td>{ld ? <DispositionChip value={ld} /> : <span className="muted" title={series ? "No longitudinal verdict (needs a prior period)" : "loading"}>—</span>}</td>
                    <td className="hint">{fmtTime(r.at)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
        {data && !latest.length ? <p className="muted">No measure results yet.</p> : null}
      </section>
      <section className="panel" aria-labelledby="trend-h" aria-live="polite">
        <h2 id="trend-h">Trend</h2>
        {!sel ? <p className="muted">Select a measure above.</p> : null}
        <ErrorNote error={oneErr} what="the measure series" />
        {sel && one && one.rule_id === sel.rule ? <TrendChart s={one} title={`${sel.rule} on ${sel.node}`} /> : sel && !oneErr ? <p className="muted">Loading…</p> : null}
      </section>
    </>
  );
}

export default function Page() {
  return (
    <>
      <PageHeader title="Measures" />
      <PageBody>
      <Gate>
        <Measures />
      </Gate>
      </PageBody>
    </>
  );
}
