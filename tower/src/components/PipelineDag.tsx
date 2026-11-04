/** Plain-SVG DAG of pipelines. No chart library. The same facts are also rendered as tables on the page. */
import { stateTone, TONE_GLYPH } from "@/lib/dispositions";
import type { Pipelines } from "@/lib/types";

export interface DagEdge {
  from: string;
  to: string;
  policy: string;
  temporal_binding: string;
  domain: string;
  depends_on: string;
  orderOk: boolean;
}

export function buildEdges(p: Pipelines): { edges: DagEdge[]; unresolved: string[] } {
  const owner = new Map<string, string>();
  for (const pl of p.pipelines) for (const d of pl.domains) owner.set(d, pl.id);
  const idx = (id: string) => p.order.indexOf(id);
  const edges: DagEdge[] = [];
  const unresolved: string[] = [];
  for (const d of p.dependencies) {
    const from = owner.get(d.depends_on);
    const to = owner.get(d.domain);
    if (!from || !to) {
      unresolved.push(`${d.domain} ← ${d.depends_on}`);
      continue;
    }
    const fi = idx(from);
    const ti = idx(to);
    edges.push({ ...d, from, to, orderOk: fi >= 0 && ti >= 0 && fi < ti });
  }
  return { edges, unresolved };
}

const W = 230;
const GAPX = 120;
const GAPY = 26;
const HEAD = 58;
const LINE = 19;

export function PipelineDag({ p }: { p: Pipelines }) {
  const { edges } = buildEdges(p);
  const ids = p.pipelines.map((x) => x.id);
  const orderIdx = (id: string) => {
    const i = p.order.indexOf(id);
    return i < 0 ? 1e6 : i;
  };
  // Longest-path rank, bounded to avoid looping on a cyclic (invalid) graph.
  const rank = new Map(ids.map((id) => [id, 0]));
  for (let iter = 0; iter < ids.length; iter++)
    for (const e of edges) if (e.from !== e.to) rank.set(e.to, Math.max(rank.get(e.to)!, rank.get(e.from)! + 1));
  const cols = new Map<number, string[]>();
  for (const id of [...ids].sort((a, b) => orderIdx(a) - orderIdx(b))) {
    const r = Math.min(rank.get(id)!, ids.length);
    cols.set(r, [...(cols.get(r) ?? []), id]);
  }
  const height = (id: string) => HEAD + (p.pipelines.find((x) => x.id === id)?.gated_nodes.length ?? 0) * LINE + 10;
  const pos = new Map<string, { x: number; y: number; h: number }>();
  let maxY = 0;
  for (const [r, list] of [...cols.entries()].sort(([a], [b]) => a - b)) {
    let y = 20;
    for (const id of list) {
      const h = height(id);
      pos.set(id, { x: 20 + r * (W + GAPX), y, h });
      y += h + GAPY;
    }
    maxY = Math.max(maxY, y);
  }
  const vbW = 20 + (Math.max(0, ...cols.keys()) + 1) * (W + GAPX) - GAPX + 20;
  const vbH = maxY + 10;

  return (
    <svg className="dag" viewBox={`0 0 ${vbW} ${vbH}`} role="img" aria-labelledby="dag-t dag-d">
      <title id="dag-t">Pipeline dependency graph</title>
      <desc id="dag-d">
        {edges.map((e) => `${e.to} depends on ${e.from} (${e.policy}, ${e.temporal_binding})${e.orderOk ? "" : " ORDER VIOLATION"}`).join("; ")}
      </desc>
      <defs>
        <marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
          <path d="M0,0 L10,5 L0,10 z" className="arrow" />
        </marker>
      </defs>
      {edges.map((e, i) => {
        const a = pos.get(e.from)!;
        const b = pos.get(e.to)!;
        const x1 = a.x + W;
        const y1 = a.y + Math.min(a.h / 2, 30);
        const x2 = b.x;
        const y2 = b.y + Math.min(b.h / 2, 30);
        const mx = (x1 + x2) / 2;
        const back = x2 <= x1;
        const d = back
          ? `M${a.x + W / 2},${a.y + a.h} C${a.x + W / 2},${a.y + a.h + 40} ${b.x + W / 2},${b.y - 40} ${b.x + W / 2},${b.y}`
          : `M${x1},${y1} C${mx},${y1} ${mx},${y2} ${x2},${y2}`;
        return (
          <g key={i}>
            <path d={d} className={`edge${e.orderOk ? "" : " bad"}`} markerEnd="url(#arrow)" />
            <text x={mx} y={(y1 + y2) / 2 - 6 + (i % 2) * 14} textAnchor="middle" className="edgelabel">
              {e.policy}
              {e.orderOk ? "" : " ✕ ORDER"}
            </text>
          </g>
        );
      })}
      {p.pipelines.map((pl) => {
        const q = pos.get(pl.id)!;
        return (
          <g key={pl.id} transform={`translate(${q.x},${q.y})`}>
            <rect width={W} height={q.h} rx={6} className="nodebox" />
            <text x={10} y={20} fontWeight={700} fontSize={14}>
              {pl.id}
              <tspan dx={6} fontWeight={400} fontSize={11}>
                #{orderIdx(pl.id) === 1e6 ? "?" : orderIdx(pl.id) + 1}
              </tspan>
            </text>
            <text x={10} y={37} fontSize={11}>
              {pl.platform} · {pl.orchestrator}
            </text>
            <text x={10} y={52} fontSize={11}>
              domains: {pl.domains.join(", ")}
            </text>
            {pl.gated_nodes.map((g, i) => {
              const tone = stateTone(g.state);
              return (
                <g key={g.node} transform={`translate(10, ${HEAD + 4 + i * LINE})`}>
                  <rect x={0} y={-1} width={W - 20} height={LINE - 3} rx={3} fill={`var(--${tone}-bg)`} stroke={`var(--${tone}-bd)`} />
                  <text x={5} y={12} fontSize={11} style={{ fill: `var(--${tone}-fg)` }}>
                    {TONE_GLYPH[tone]} {g.node.length > 22 ? `${g.node.slice(0, 21)}…` : g.node} · {g.state}
                  </text>
                </g>
              );
            })}
          </g>
        );
      })}
    </svg>
  );
}
