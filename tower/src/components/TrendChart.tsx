"use client";
/**
 * Dependency-free SVG trend chart for one measure series (PARTD_DESIGN §6.5):
 * [min, max] threshold band, dashed baseline median, points shaped AND coloured by disposition,
 * suppressed small cells (n<11) drawn as gaps. A data table is the text alternative.
 */
import { useId, useState } from "react";
import { DispositionChip, ToneChip } from "./Chip";
import { dispositionTone, type Tone } from "@/lib/dispositions";
import { fmtNum } from "@/lib/format";
import type { MeasureSeries } from "@/lib/types";

const W = 760;
const H = 280;
const P = { l: 64, r: 16, t: 16, b: 58 };

function Marker({ tone, x, y }: { tone: Tone; x: number; y: number }) {
  const c = `m-${tone}`;
  switch (tone) {
    case "ok":
      return <circle className={c} cx={x} cy={y} r={5} />;
    case "warn":
      return <polygon className={c} points={`${x},${y - 6} ${x + 6},${y + 5} ${x - 6},${y + 5}`} />;
    case "bad":
    case "crit":
      return <rect className={c} x={x - 5} y={y - 5} width={10} height={10} />;
    default:
      return <polygon className={c} points={`${x},${y - 6} ${x + 6},${y} ${x},${y + 6} ${x - 6},${y}`} />;
  }
}

const SHAPE_NAME: Record<Tone, string> = { ok: "circle", info: "diamond", neutral: "diamond", warn: "triangle", bad: "square", crit: "square" };

function LegendMarker({ tone }: { tone: Tone }) {
  return (
    <svg className="chart" width={16} height={16} viewBox="-8 -8 16 16" aria-hidden="true" style={{ width: 16, display: "inline" }}>
      <Marker tone={tone} x={0} y={0} />
    </svg>
  );
}

const num = (v: unknown): number | null => (typeof v === "number" && Number.isFinite(v) ? v : null);

/**
 * Part E control-chart overlay (PARTE_DESIGN §6). XmR: constant CL / UCL / LCL. p-chart: per-point UCL / LCL
 * (limits vary with n). Signal points get a ring and an "R1"-style label; a point's colour then encodes
 * signal vs in-control instead of the DQ disposition.
 */
export interface ControlOverlay {
  chart: string;
  cl: number | null;
  ucl: number | null;
  lcl: number | null;
  sigma2: [number, number] | null;
  perPoint: { ucl: number | null; lcl: number | null }[] | null;
  /** point index → run rule(s) that fired */
  signals: Map<number, string>;
}

export function TrendChart({ s, title, control }: { s: MeasureSeries; title: string; control?: ControlOverlay }) {
  const [asTable, setAsTable] = useState(false);
  const uid = useId();
  const tMin = num(s.thresholds?.min);
  const tMax = num(s.thresholds?.max);
  const med = num(s.baseline_median);
  const vals = s.points.map((p) => p.value).filter((v): v is number => v !== null);
  const lim = control
    ? [control.cl, control.ucl, control.lcl, ...(control.perPoint ?? []).flatMap((q) => [q.ucl, q.lcl])].filter((v): v is number => num(v) !== null)
    : [];
  const ys = [...vals, ...lim, ...(tMin !== null ? [tMin] : []), ...(tMax !== null ? [tMax] : []), ...(med !== null ? [med] : [])];
  let lo = ys.length ? Math.min(...ys) : 0;
  let hi = ys.length ? Math.max(...ys) : 1;
  if (lo === hi) {
    lo -= Math.abs(lo) * 0.1 || 0.5;
    hi += Math.abs(hi) * 0.1 || 0.5;
  }
  const pad = (hi - lo) * 0.1;
  lo = lo >= 0 && lo - pad < 0 ? 0 : lo - pad;
  hi += pad;
  const n = s.points.length;
  const x = (i: number) => P.l + (n <= 1 ? (W - P.l - P.r) / 2 : (i * (W - P.l - P.r)) / (n - 1));
  const y = (v: number) => P.t + ((hi - v) * (H - P.t - P.b)) / (hi - lo);
  const ticks = Array.from({ length: 5 }, (_, i) => lo + ((hi - lo) * i) / 4);

  // Line segments break at suppressed or missing values (gaps, never interpolated).
  const segs: string[] = [];
  let cur: string[] = [];
  s.points.forEach((p, i) => {
    if (p.value === null) {
      if (cur.length > 1) segs.push(cur.join(" "));
      cur = [];
    } else cur.push(`${x(i)},${y(p.value)}`);
  });
  if (cur.length > 1) segs.push(cur.join(" "));
  const suppressed = s.points.filter((p) => p.suppressed).length;
  const toneAt = (i: number, d: string): Tone => (control ? (control.signals.has(i) ? "bad" : "ok") : dispositionTone(d));
  const tones = [...new Set(s.points.map((p, i) => (p.value !== null ? toneAt(i, p.disposition) : null)).filter((t): t is Tone => t !== null))];
  const stepPath = (pick: (q: { ucl: number | null; lcl: number | null }) => number | null) => {
    if (!control?.perPoint) return "";
    const half = n <= 1 ? 20 : (W - P.l - P.r) / (n - 1) / 2;
    return control.perPoint
      .map((q, i) => {
        const v = pick(q);
        return v === null ? "" : `M${Math.max(P.l, x(i) - half)},${y(v)} L${Math.min(W - P.r, x(i) + half)},${y(v)}`;
      })
      .join(" ");
  };
  const bandLo = tMin ?? lo;
  const bandHi = tMax ?? hi;
  const thText = `${tMin !== null ? `min ${fmtNum(tMin)}` : "no min"}, ${tMax !== null ? `max ${fmtNum(tMax)}` : "no max"}`;

  return (
    <figure style={{ margin: 0 }}>
      <div className="row" style={{ justifyContent: "space-between" }}>
        <figcaption id={`${uid}-t`}>
          <strong>{title}</strong>
          <span className="hint">
            {" "}
            · {n} period(s) ·{" "}
            {control
              ? `${control.chart} chart · centre line ${control.cl !== null ? fmtNum(Number(control.cl.toPrecision(4))) : "—"}${control.signals.size ? ` · ${control.signals.size} signal(s)` : " · in control"}`
              : `threshold band ${thText} · baseline median ${med !== null ? fmtNum(med) : "not pinned"}`}
            {suppressed ? ` · ${suppressed} suppressed` : ""}
          </span>
        </figcaption>
        <button type="button" aria-pressed={asTable} onClick={() => setAsTable((v) => !v)}>
          {asTable ? "Show chart" : "Show as table"}
        </button>
      </div>
      {n === 0 ? (
        <p className="muted">No periods recorded for this measure.</p>
      ) : asTable ? (
        <div className="tablewrap">
          <table>
            <caption className="skip">{title}: values per period</caption>
            <thead>
              <tr>
                <th scope="col">Batch (period)</th>
                <th scope="col">Node</th>
                <th scope="col" className="num">
                  Value
                </th>
                <th scope="col" className="num">
                  Denominator
                </th>
                <th scope="col">Disposition</th>
                <th scope="col">Longitudinal</th>
              </tr>
            </thead>
            <tbody>
              {s.points.map((p, i) => (
                <tr key={`${p.batch_id}|${p.node}`}>
                  <td className="mono">{p.batch_id}</td>
                  <td className="mono">{p.node}</td>
                  <td className="num">{p.suppressed ? "suppressed (n<11)" : fmtNum(p.value)}</td>
                  <td className="num">{p.suppressed ? "<11" : fmtNum(p.denominator)}</td>
                  <td>
                    {control ? (
                      control.signals.has(i) ? (
                        <ToneChip tone="bad" label={`signal ${control.signals.get(i)}`} />
                      ) : (
                        <ToneChip tone="ok" label="in control" />
                      )
                    ) : (
                      <DispositionChip value={p.disposition} />
                    )}
                  </td>
                  <td>{p.long_disposition ? <DispositionChip value={p.long_disposition} /> : <span className="muted">—</span>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <>
          <svg className="chart" viewBox={`0 0 ${W} ${H}`} role="img" aria-labelledby={`${uid}-t ${uid}-d`}>
            <desc id={`${uid}-d`}>
              {`Trend of ${s.rule_id} across ${n} periods. ${s.points
                .map((p) => `${p.batch_id}: ${p.suppressed ? "suppressed" : fmtNum(p.value)} ${p.disposition}`)
                .join("; ")}.`}
            </desc>
            {ticks.map((t) => (
              <g key={t}>
                <line className="grid" x1={P.l} x2={W - P.r} y1={y(t)} y2={y(t)} />
                <text x={P.l - 6} y={y(t) + 4} textAnchor="end">
                  {fmtNum(Number(t.toPrecision(3)))}
                </text>
              </g>
            ))}
            {tMin !== null || tMax !== null ? (
              <rect className="thband" x={P.l} width={W - P.l - P.r} y={y(bandHi)} height={Math.max(0, y(bandLo) - y(bandHi))} />
            ) : null}
            {med !== null ? <line className="median" x1={P.l} x2={W - P.r} y1={y(med)} y2={y(med)} /> : null}
            {control?.sigma2 ? (
              <rect className="sigma2" x={P.l} width={W - P.l - P.r} y={y(control.sigma2[1])} height={Math.max(0, y(control.sigma2[0]) - y(control.sigma2[1]))} />
            ) : null}
            {control && control.cl !== null ? <line className="cl" x1={P.l} x2={W - P.r} y1={y(control.cl)} y2={y(control.cl)} /> : null}
            {control && !control.perPoint && control.ucl !== null ? <line className="limit" x1={P.l} x2={W - P.r} y1={y(control.ucl)} y2={y(control.ucl)} /> : null}
            {control && !control.perPoint && control.lcl !== null ? <line className="limit" x1={P.l} x2={W - P.r} y1={y(control.lcl)} y2={y(control.lcl)} /> : null}
            {control?.perPoint ? (
              <>
                <path className="limit" d={stepPath((q) => q.ucl)} />
                <path className="limit" d={stepPath((q) => q.lcl)} />
              </>
            ) : null}
            <line className="axis" x1={P.l} x2={P.l} y1={P.t} y2={H - P.b} />
            <line className="axis" x1={P.l} x2={W - P.r} y1={H - P.b} y2={H - P.b} />
            {s.points.map((p, i) =>
              p.suppressed ? (
                <g key={`g${i}`}>
                  <rect className="gap" x={x(i) - 9} y={P.t} width={18} height={H - P.t - P.b} />
                  <text x={x(i)} y={P.t + 12} textAnchor="middle">
                    n&lt;11
                  </text>
                </g>
              ) : null,
            )}
            {segs.map((pts, i) => (
              <polyline key={i} className="line" points={pts} />
            ))}
            {s.points.map((p, i) =>
              p.value !== null ? (
                <g key={`m${i}`}>
                  <title>
                    {control
                      ? `${p.batch_id}: ${fmtNum(p.value)}${control.signals.has(i) ? ` signal ${control.signals.get(i)}` : " in control"}`
                      : `${p.batch_id} · ${p.node}: ${fmtNum(p.value)} (${p.disposition}${p.long_disposition ? `, longitudinal ${p.long_disposition}` : ""})`}
                  </title>
                  <Marker tone={toneAt(i, p.disposition)} x={x(i)} y={y(p.value)} />
                  {control?.signals.has(i) ? (
                    <>
                      <circle className="signal" cx={x(i)} cy={y(p.value)} r={10} />
                      <text x={x(i)} y={y(p.value) - 14} textAnchor="middle">
                        {control.signals.get(i)}
                      </text>
                    </>
                  ) : null}
                </g>
              ) : null,
            )}
            {s.points.map((p, i) => (
              <text key={`x${i}`} x={x(i)} y={H - P.b + 14} textAnchor="end" transform={`rotate(-25 ${x(i)} ${H - P.b + 14})`}>
                {p.batch_id.length > 12 ? `…${p.batch_id.slice(-11)}` : p.batch_id}
              </text>
            ))}
          </svg>
          <div className="legend" aria-label="Chart legend">
            {control ? null : tMin !== null || tMax !== null ? (
              <span>
                <svg width={22} height={12} aria-hidden="true" className="chart" style={{ width: 22, display: "inline" }}>
                  <rect className="thband" x={1} y={1} width={20} height={10} />
                </svg>{" "}
                threshold band [{tMin !== null ? fmtNum(tMin) : "−∞"}, {tMax !== null ? fmtNum(tMax) : "+∞"}]
              </span>
            ) : (
              <span>no threshold</span>
            )}
            {med !== null ? (
              <span>
                <svg width={26} height={10} aria-hidden="true" className="chart" style={{ width: 26, display: "inline" }}>
                  <line className="median" x1={0} x2={26} y1={5} y2={5} />
                </svg>{" "}
                baseline median {fmtNum(med)}
              </span>
            ) : null}
            {control ? (
              <>
                <span>
                  <svg width={26} height={10} aria-hidden="true" className="chart" style={{ width: 26, display: "inline" }}>
                    <line className="cl" x1={0} x2={26} y1={5} y2={5} />
                  </svg>{" "}
                  centre line {control.cl !== null ? fmtNum(Number(control.cl.toPrecision(4))) : "—"}
                </span>
                <span>
                  <svg width={26} height={10} aria-hidden="true" className="chart" style={{ width: 26, display: "inline" }}>
                    <line className="limit" x1={0} x2={26} y1={5} y2={5} />
                  </svg>{" "}
                  {control.perPoint ? "UCL / LCL per period (vary with n)" : `UCL ${control.ucl !== null ? fmtNum(Number(control.ucl.toPrecision(4))) : "—"} / LCL ${control.lcl !== null ? fmtNum(Number(control.lcl.toPrecision(4))) : "—"}`}
                </span>
                {control.sigma2 ? (
                  <span>
                    <svg width={14} height={14} aria-hidden="true" className="chart" style={{ width: 14, display: "inline" }}>
                      <rect className="sigma2" x={1} y={1} width={12} height={12} />
                    </svg>{" "}
                    ±2σ zone
                  </span>
                ) : null}
                <span>
                  <LegendMarker tone="ok" /> circle: in control
                </span>
                <span>
                  <LegendMarker tone="bad" /> ringed square: signal (run rule shown)
                </span>
              </>
            ) : (
              tones.map((t) => (
                <span key={t}>
                  <LegendMarker tone={t} /> {SHAPE_NAME[t]}:{" "}
                  {[...new Set(s.points.filter((p) => p.value !== null && dispositionTone(p.disposition) === t).map((p) => p.disposition))].join(", ")}
                </span>
              ))
            )}
            <span>
              <svg width={14} height={14} aria-hidden="true" className="chart" style={{ width: 14, display: "inline" }}>
                <rect className="gap" x={1} y={1} width={12} height={12} />
              </svg>{" "}
              suppressed (n&lt;11): drawn as a gap, value withheld
            </span>
          </div>
        </>
      )}
    </figure>
  );
}
