"use client";
/**
 * The interactive React Flow surface for one pipeline's graph (PFP P2-4). Loaded only via `next/dynamic` with
 * `ssr:false` from `app/pipelines/graph/page.tsx` -- this module touches `window`/`ResizeObserver` at import
 * time (React Flow's own requirement) and must never run during server rendering or static generation.
 *
 * Positions come from `lib/graph/layout.ts` (deterministic, no auto-layout plugin): this component only turns
 * those fixed positions into React Flow's `Node[]`/`Edge[]`, it never recomputes or animates a layout itself.
 * Arrow-key traversal along edges is React Flow's own built-in accessibility behaviour (Tab/Enter/Space/Escape,
 * arrow-key movement between connected nodes); `disableKeyboardA11y` is left at its default (enabled) and
 * `nodesFocusable`/`edgesFocusable` are left at their defaults -- nothing here turns that off.
 */
import { useCallback, useMemo, useRef } from "react";
import {
  Background,
  Controls,
  Handle,
  Position,
  ReactFlow,
  type Edge,
  type Node,
  type NodeProps,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { stateTone, TONE_GLYPH } from "@/lib/dispositions";
import { layoutGraph } from "@/lib/graph/layout";
import type { GraphEdge, GraphNode } from "@/lib/types/catalog";

type NodeData = { label: string; state: string | null; gateMode: string | null };

function PipelineNode({ data, selected }: NodeProps<Node<NodeData>>) {
  const tone = data.state ? stateTone(data.state) : "neutral";
  return (
    <div
      className={`gnode t-${tone}${selected ? " selected" : ""}`}
      style={{
        padding: "8px 10px",
        borderRadius: 6,
        border: `1px solid var(--${tone}-bd)`,
        background: `var(--${tone}-bg)`,
        color: `var(--${tone}-fg)`,
        minWidth: 140,
        fontSize: 12,
      }}
    >
      {/* The longest-path layout (lib/graph/layout.ts) places columns left-to-right in dependency order, so
          edges run left-to-right too: target on the left (incoming, from an earlier column), source on the
          right (outgoing, to a later column). React Flow needs BOTH handles present on every custom node type
          to resolve an edge's endpoints at all -- with neither, it silently drops every edge (warns "source
          handle id: null", error #008) rather than falling back to the node's own bounding box. */}
      <Handle type="target" position={Position.Left} />
      <div className="mono" style={{ fontWeight: 700 }}>
        {data.state ? TONE_GLYPH[tone] : "·"} {data.label}
      </div>
      <div style={{ opacity: 0.85 }}>{data.state ?? "discovered, not evaluated"}</div>
      {data.gateMode ? <div style={{ opacity: 0.7 }}>gate: {data.gateMode}</div> : null}
      <Handle type="source" position={Position.Right} />
    </div>
  );
}

const nodeTypes = { pipeline: PipelineNode };

function PolicyEdgeLabel({ label, orderOk }: { label?: string | null; orderOk: boolean }) {
  if (!label && orderOk) return null;
  return (
    <span className="hint" style={{ fontSize: 10 }}>
      {label ?? ""}
      {orderOk ? "" : " ✕ ORDER"}
    </span>
  );
}
// Exported for the Table/Outline view, which renders the same fact without React Flow's edge rendering at all.
export { PolicyEdgeLabel };

export interface GraphCanvasProps {
  nodes: GraphNode[];
  edges: GraphEdge[];
  /** Node id selected in the outline/table, kept in sync both ways; undefined selects nothing. */
  focusId?: string | null;
  onFocus?: (id: string) => void;
}

export function GraphCanvas({ nodes, edges, focusId, onFocus }: GraphCanvasProps) {
  const layout = useMemo(() => layoutGraph(nodes, edges), [nodes, edges]);
  const rfNodes: Node<NodeData>[] = useMemo(
    () =>
      nodes.map((n) => {
        const p = layout.nodes.find((l) => l.id === n.id) ?? { x: 0, y: 0 };
        return {
          id: n.id,
          type: "pipeline",
          position: { x: p.x, y: p.y },
          selected: n.id === focusId,
          data: { label: n.id, state: n.state, gateMode: n.gate?.mode ?? null },
        };
      }),
    [nodes, layout, focusId],
  );
  const rfEdges: Edge[] = useMemo(
    () =>
      edges.map((e, i) => ({
        id: `${e.from}->${e.to}#${i}`,
        source: e.from,
        target: e.to,
        animated: false,
        label: e.policy ?? (e.orderOk ? undefined : "✕ ORDER"),
        style: e.orderOk ? undefined : { stroke: "var(--bad-bd)" },
      })),
    [edges],
  );

  // React Flow's own pane is a plain, unfocusable div: clicking it moves DOM focus nowhere, so a Tab press right
  // after falls back to the surrounding page's own tab order (the PageHeader's "Refresh" button, etc.) instead of
  // reaching the graph at all -- the keyboard-only visitor never gets in. `onPaneClick` below puts focus directly
  // on the first node instead, so the next Tab moves forward within `.react-flow__nodes` (node to node), never
  // back out to the page chrome. Edges stay in the Tab sequence too (`edgesFocusable` default, untouched, same as
  // nodesFocusable) -- React Flow's own fixed internal DOM order renders `.react-flow__edges` BEFORE
  // `.react-flow__nodes` inside the viewport, so a click that lands on the wrapper itself (rather than a node)
  // would put the very next Tab on an edge, not a node; starting on a node sidesteps that ordering entirely.
  const wrapRef = useRef<HTMLDivElement>(null);
  const focusFirstNode = useCallback(() => {
    wrapRef.current?.querySelector<HTMLElement>(".react-flow__node")?.focus();
  }, []);

  return (
    <div
      ref={wrapRef}
      tabIndex={-1}
      style={{ width: "100%", height: 560, border: "1px solid var(--border)", borderRadius: 8, outline: "none" }}
    >
      <ReactFlow
        nodes={rfNodes}
        edges={rfEdges}
        nodeTypes={nodeTypes}
        fitView
        onNodeClick={(_, n) => onFocus?.(n.id)}
        onPaneClick={focusFirstNode}
        aria-label="Pipeline graph. Arrow keys move along edges; Escape returns focus to the surrounding page."
        proOptions={{ hideAttribution: true }}
      >
        <Background />
        <Controls />
      </ReactFlow>
    </div>
  );
}
