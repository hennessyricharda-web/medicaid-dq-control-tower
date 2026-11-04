/**
 * Deterministic in-house layout for the pipelines graph (PFP P2-4, EXECUTION_PLAN K4 step 2): no auto-layout
 * plugin (dagre, elkjs, ...), so node positions are reproducible across renders of the SAME graph -- a
 * requirement in its own right (a layout that reshuffles on every mount makes the keyboard-only traversal and
 * any visual diffing undependable), not an accessibility feature by itself.
 *
 * Algorithm: longest-path rank (same bounded, cycle-safe technique PipelineDag.tsx already uses for the pipeline
 * map), columns left to right by rank, rows top to bottom within a column ordered by node id (a stable,
 * content-derived order -- never by array/object insertion order, which is not guaranteed stable across two
 * independent fetches of the same graph).
 */
import type { GraphEdge, GraphNode } from "@/lib/types/catalog";

export interface LaidOutNode {
  id: string;
  x: number;
  y: number;
}

export interface GraphLayout {
  nodes: LaidOutNode[];
  width: number;
  height: number;
}

const COL_W = 260;
const COL_GAP = 80;
const ROW_H = 72;
const ROW_GAP = 16;
const PAD = 24;

/** Longest-path rank, bounded to the node count to avoid looping on a cyclic (invalid) graph -- the SAME bound
 *  PipelineDag.tsx's own `rank` loop uses, for the same reason. */
function rankOf(nodes: GraphNode[], edges: GraphEdge[]): Map<string, number> {
  const rank = new Map<string, number>(nodes.map((n) => [n.id, 0]));
  const known = new Set(rank.keys());
  const safeEdges = edges.filter((e) => known.has(e.from) && known.has(e.to) && e.from !== e.to);
  for (let iter = 0; iter < nodes.length; iter++) {
    let changed = false;
    for (const e of safeEdges) {
      const next = Math.max(rank.get(e.to)!, rank.get(e.from)! + 1);
      if (next !== rank.get(e.to)) {
        rank.set(e.to, next);
        changed = true;
      }
    }
    if (!changed) break;
  }
  return rank;
}

/** Positions every node once, deterministically, from its id and the edges alone -- the same input always
 *  produces the same output, regardless of the order the API happened to list nodes or edges in. */
export function layoutGraph(nodes: GraphNode[], edges: GraphEdge[]): GraphLayout {
  const rank = rankOf(nodes, edges);
  const cols = new Map<number, string[]>();
  for (const n of [...nodes].sort((a, b) => a.id.localeCompare(b.id))) {
    const r = Math.min(rank.get(n.id) ?? 0, nodes.length);
    cols.set(r, [...(cols.get(r) ?? []), n.id]);
  }
  const pos = new Map<string, LaidOutNode>();
  let maxRows = 0;
  for (const [r, ids] of [...cols.entries()].sort(([a], [b]) => a - b)) {
    ids.forEach((id, i) => {
      pos.set(id, { id, x: PAD + r * (COL_W + COL_GAP), y: PAD + i * (ROW_H + ROW_GAP) });
    });
    maxRows = Math.max(maxRows, ids.length);
  }
  const maxCol = Math.max(0, ...cols.keys());
  return {
    nodes: nodes.map((n) => pos.get(n.id) ?? { id: n.id, x: PAD, y: PAD }),
    width: PAD * 2 + (maxCol + 1) * COL_W + maxCol * COL_GAP,
    height: PAD * 2 + maxRows * ROW_H + Math.max(0, maxRows - 1) * ROW_GAP,
  };
}
