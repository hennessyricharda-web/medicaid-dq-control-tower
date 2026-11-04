"use client";
import { useState } from "react";
import { useAuth } from "@/lib/auth";
import { useLoad } from "@/lib/useLoad";
import { Gate } from "@/components/SignIn";
import { ErrorNote } from "@/components/ErrorNote";
import { ToneChip } from "@/components/Chip";
import { Explanation } from "@/components/Explanation";
import { TrendChart, type ControlOverlay } from "@/components/TrendChart";
import { statusTone } from "@/lib/partE";
import { fmtNum, shortHash } from "@/lib/format";
import type { MeasureSeries, MonitorRow, MonitorSeries } from "@/lib/types";
import { PageBody, PageHeader } from "@/components/layout/PageHeader";

const MIN_PERIODS = 8;

function toChart(s: MonitorSeries): { series: MeasureSeries; control: ControlOverlay } {
  const idx = new Map(s.points.map((p, i) => [p.batch_id, i]));
  const signals = new Map<number, string>();
  for (const g of s.signals) {
    const i = idx.get(g.batch_id);
    if (i !== undefined) signals.set(i, signals.has(i) ? `${signals.get(i)},${g.rule}` : g.rule);
  }
  return {
    series: {
      rule_id: `${s.relation} ${s.metric}`,
      thresholds: {},
      baseline_median: null,
      points: s.points.map((p) => ({
        batch_id: p.period ? `${p.period}` : p.batch_id,
        node: s.relation,
        disposition: "ADVISORY",
        long_disposition: null,
        suppressed: p.value === null,
        value: p.value,
        denominator: p.n,
      })),
    },
    control: {
      chart: s.chart,
      cl: s.cl,
      ucl: s.ucl,
      lcl: s.lcl,
      sigma2: s.sigma2,
      perPoint: s.chart === "p" ? s.points.map((p) => ({ ucl: p.ucl ?? null, lcl: p.lcl ?? null })) : null,
      signals,
    },
  };
}

function MixPanel({ s }: { s: MonitorSeries }) {
  const mix = s.mix;
  if (!mix) return <p className="muted">No category mix recorded.</p>;
  const bins = mix.bins ?? [];
  const max = Math.max(0.0001, ...bins.flatMap((b) => [b.expected, b.actual]));
  return (
    <div>
      <p>
        Population stability index{" "}
        <strong className="mono">{mix.psi === null ? "not computed" : mix.psi.toFixed(3)}</strong>{" "}
        <ToneChip tone={statusTone(mix.status)} label={mix.status} />{" "}
        <span className="hint">0.10–0.25 WATCH · 0.25 or more SHIFT · computed only when every bin expects at least 5; rare categories fold into OTHER</span>
      </p>
      {mix.bins_withheld ? (
        <p className="banner t-info" role="note">
          Category shares withheld: this element is masked (identity, clinical or part2), so only the PSI and its status are shown.
        </p>
      ) : null}
      {bins.length ? (
      <div className="tablewrap">
        <table>
          <caption>Category mix: baseline expected share versus this period&rsquo;s share</caption>
          <thead>
            <tr>
              <th scope="col">Category</th>
              <th scope="col" className="num">
                Expected
              </th>
              <th scope="col" className="num">
                Actual
              </th>
              <th scope="col">Comparison</th>
            </tr>
          </thead>
          <tbody>
            {bins.map((b) => (
              <tr key={b.category}>
                <td className="mono">{b.category}</td>
                <td className="num">{(b.expected * 100).toFixed(1)}%</td>
                <td className="num">{(b.actual * 100).toFixed(1)}%</td>
                <td aria-hidden="true" style={{ minWidth: 180 }}>
                  <div>
                    <span className="bar exp" style={{ width: `${(b.expected / max) * 150}px` }} /> <span className="hint">expected</span>
                  </div>
                  <div>
                    <span className="bar" style={{ width: `${(b.actual / max) * 150}px` }} /> <span className="hint">actual</span>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      ) : null}
    </div>
  );
}

function SeriesView({ row }: { row: MonitorRow }) {
  const { session, client } = useAuth();
  const { data: s, error } = useLoad(session ? () => client.monitorSeries(row.relation, row.metric, row.key) : null, [
    session?.token,
    row.relation,
    row.metric,
    row.key,
  ]);
  const title = `${row.relation} · ${row.metric}${row.key ? ` · ${row.key}` : ""}`;
  if (error) return <ErrorNote error={error} what="the monitor series" />;
  if (!s || s.relation !== row.relation || s.metric !== row.metric) return <p className="muted">Loading…</p>;
  // Only the server's BASELINE_BUILDING means "not enough periods"; NOT_COMPUTED / PROVISIONAL have their own notes.
  const building = s.status === "BASELINE_BUILDING";
  const chart = toChart(s);
  return (
    <>
      <p className="hint">
        Chart <strong>{s.chart}</strong> · baseline {s.baseline_ref ? <code>{shortHash(s.baseline_ref, 12)}</code> : "not pinned"} · advisory
        only: a signal never changes a unit&rsquo;s mode.
      </p>
      {building ? (
        <div className="banner t-info" role="status">
          <strong>BASELINE_BUILDING.</strong> {s.points.length} of at least {MIN_PERIODS} eligible periods. No control limits are set and nothing posts
          until the baseline is pinned (frozen; periods with an open signal are excluded).
        </div>
      ) : null}
      {s.status === "NOT_COMPUTED" ? (
        <div className="banner t-neutral" role="status">
          <strong>NOT_COMPUTED.</strong> This period has no rows to compute the metric over (n = 0); it is left out of baselines.
        </div>
      ) : null}
      {s.status === "PROVISIONAL" ? (
        <div className="banner t-info" role="status">
          <strong>PROVISIONAL.</strong> The period is still open, so no signal is raised until it closes.
          {row.note ? ` ${row.note}` : ""}
        </div>
      ) : null}
      {s.chart === "PSI" ? (
        <MixPanel s={s} />
      ) : building ? (
        <TrendChart s={chart.series} title={`${title} (no limits yet)`} />
      ) : (
        <TrendChart s={chart.series} control={chart.control} title={title} />
      )}
      {s.signals.length ? (
        <>
          <h3>Signals</h3>
          <ul style={{ paddingLeft: 18 }}>
            {s.signals.map((g, i) => (
              <li key={i}>
                <span className="mono">{g.batch_id}</span> <ToneChip tone="bad" label={g.rule} />
                <Explanation e={g.explanation} label={`Signal ${g.rule} explanation`} compact />
              </li>
            ))}
          </ul>
        </>
      ) : null}
    </>
  );
}

function Monitoring() {
  const { session, client } = useAuth();
  const { data, error, loading, reload } = useLoad(session ? client.monitor : null, [session?.token], 60_000);
  const [sel, setSel] = useState<MonitorRow | null>(null);
  const k = (r: MonitorRow) => `${r.relation}|${r.metric}|${r.key ?? ""}`;
  const counts = (data ?? []).reduce<Record<string, number>>((m, r) => ({ ...m, [r.status]: (m[r.status] ?? 0) + 1 }), {});
  return (
    <>
      <p className="hint">
        The MONITOR seat charts aggregates only (row counts, payment sums, CDE null rates, category mix). XmR for counts and sums, p-charts with
        per-period limits for null rates, PSI for mix. Rule R1 (beyond 3σ) is on by default.
      </p>
      <ErrorNote error={error} what="monitoring" />
      <section className="panel" aria-labelledby="mon-h">
        <h2 id="mon-h" className="row" style={{ justifyContent: "space-between" }}>
          <span>
            Monitored metrics{" "}
            <span className="sub">
              {Object.entries(counts)
                .map(([s, n]) => `${n} ${s}`)
                .join(" · ") || "loading…"}
            </span>
          </span>
          <button type="button" onClick={() => void reload()} disabled={loading}>
            Refresh
          </button>
        </h2>
        <div className="tablewrap">
          <table>
            <caption className="skip">Monitored metrics with status and last signal; select one to chart it</caption>
            <thead>
              <tr>
                <th scope="col">Relation</th>
                <th scope="col">Metric</th>
                <th scope="col">Status</th>
                <th scope="col" className="num">
                  Periods
                </th>
                <th scope="col">Reading / last signal</th>
              </tr>
            </thead>
            <tbody>
              {(data ?? []).map((r) => (
                <tr key={k(r)} className={sel && k(sel) === k(r) ? "selected" : undefined}>
                  <td className="mono">{r.relation}</td>
                  <td>
                    <button type="button" className="linklike mono" aria-pressed={!!sel && k(sel) === k(r)} onClick={() => setSel(r)}>
                      {r.metric}
                      {r.key ? ` · ${r.key}` : ""}
                    </button>
                  </td>
                  <td>
                    <ToneChip tone={statusTone(r.status)} label={r.status} />
                    {r.note ? <div className="hint">{r.note}</div> : null}
                  </td>
                  <td className="num">
                    {fmtNum(r.n_periods ?? null)}
                    {typeof r.received_pct === "number" ? <div className="hint">{Math.round(r.received_pct)}% received</div> : null}
                  </td>
                  <td style={{ maxWidth: 420 }}>
                    {r.last_signal ? (
                      <>
                        <span className="mono">{r.last_signal.period ?? r.last_signal.batch_id}</span> <ToneChip tone="bad" label={r.last_signal.rule} />
                        {r.last_signal.direction ? <span className="hint"> {r.last_signal.direction}</span> : null}
                        <Explanation e={r.last_signal.explanation ?? r.explanation} label="Signal explanation" compact />
                      </>
                    ) : r.explanation ? (
                      <Explanation e={r.explanation} label="Metric reading" compact />
                    ) : (
                      <span className="muted">none</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
      <section className="panel" aria-labelledby="ch-h" aria-live="polite">
        <h2 id="ch-h">Chart</h2>
        {sel ? <SeriesView key={k(sel)} row={sel} /> : <p className="muted">Select a metric above.</p>}
      </section>
    </>
  );
}

export default function Page() {
  return (
    <>
      <PageHeader title="Monitors" sub="Advisory: never changes a unit's mode" />
      <PageBody>
      <Gate>
        <Monitoring />
      </Gate>
      </PageBody>
    </>
  );
}
