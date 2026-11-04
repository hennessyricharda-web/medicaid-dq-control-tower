"use client";
/**
 * The interactive pipelines graph (PFP P2-4, EXECUTION_PLAN K4). The identified pipeline comes from `?id=`
 * (this page itself has no `[id]` route segment -- the brief's own file path, `app/pipelines/graph/page.tsx`,
 * is deliberate, matching PFP's literal naming), defaulting to the first pipeline in the catalog's own order
 * when none is given, so the page is never blank when reached from the nav rail directly.
 *
 * `GraphCanvas` (React Flow) is loaded only through `next/dynamic` with `ssr:false`: it touches `window` at
 * import time and must never run during server rendering or static generation (PFP P2-4, DEC-020's own
 * React-Flow-only-in-this-package boundary).
 *
 * Accessible default (PFP P2-4 / DEC-020): the Table/Outline view is shown, not React Flow, when
 * `prefers-reduced-motion: reduce` or `forced-colors: active` matches, or when the viewer has saved
 * "Graph view: Table" (`PREF.graphView`) -- React Flow itself ships none of this (the tech-selection record's
 * own accessibility finding), so this page is where that gap is actually covered, not assumed closed by the
 * dependency. Short of those, the page opens in Table view and switches to Graph only once a POINTER
 * interaction (not a key press) is the first thing the viewer does on the page -- so a keyboard-first visitor
 * never gets the graph by default either. The first focusable element on the page is always the "Table view
 * (accessible)" button, in both views, regardless of which one is showing.
 */
import dynamic from "next/dynamic";
import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { useAuth } from "@/lib/auth";
import { useCatalog } from "@/lib/api/catalog";
import { useLoad } from "@/lib/useLoad";
import { SignIn } from "@/components/SignIn";
import { ErrorNote } from "@/components/ErrorNote";
import { StateChip } from "@/components/Chip";
import { PageBody, PageHeader } from "@/components/layout/PageHeader";
import { EmptyState } from "@/components/layout/EmptyState";
import { PREF, usePref } from "@/lib/prefs";
import type { GraphEdge, GraphNode } from "@/lib/types/catalog";

// `ssr:false`: React Flow reads `window`/`ResizeObserver` at import time (PFP P2-4).
const GraphCanvas = dynamic(() => import("@/components/graph/GraphCanvas").then((m) => m.GraphCanvas), {
  ssr: false,
  loading: () => <p className="hint">Loading the graph renderer&hellip;</p>,
});

type View = "table" | "graph";

/** The three STATIC accessible-default triggers (PFP P2-4): reduced-motion, forced-colors, and the saved
 *  preference. Re-read live (not cached at module scope), since the OS setting can change while the tab is
 *  open and `forced-colors` has no React hook of its own in this codebase. */
function useForcedTable(savedView: string): boolean {
  const [forced, setForced] = useState(savedView === "table");
  useEffect(() => {
    if (savedView === "table") {
      setForced(true);
      return;
    }
    let mq: MediaQueryList[] = [];
    try {
      const reduce = window.matchMedia("(prefers-reduced-motion: reduce)");
      const forcedColors = window.matchMedia("(forced-colors: active)");
      mq = [reduce, forcedColors];
      const update = () => setForced(reduce.matches || forcedColors.matches);
      update();
      mq.forEach((m) => m.addEventListener("change", update));
      return () => mq.forEach((m) => m.removeEventListener("change", update));
    } catch {
      // matchMedia unavailable (ADJ-8: cannot detect reliably); stay with the safe default (table).
      setForced(true);
    }
  }, [savedView]);
  return forced;
}

/** The fourth trigger: table stays the default until the viewer's FIRST interaction with the page is a
 *  pointer action, not a key press. `isTypingTarget`-style scoping is not needed here -- any key at all
 *  (including Tab) counts as "keyboard first", any mouse/touch/pen action switches to graph. */
function useKeyboardFirstGate(forcedTable: boolean): boolean {
  const [pointerSeen, setPointerSeen] = useState(false);
  useEffect(() => {
    if (forcedTable) return;
    let decided = false;
    const onPointer = () => {
      if (decided) return;
      decided = true;
      setPointerSeen(true);
      cleanup();
    };
    const onKey = () => {
      if (decided) return;
      decided = true;
      cleanup();
    };
    function cleanup() {
      window.removeEventListener("pointerdown", onPointer, true);
      window.removeEventListener("mousedown", onPointer, true);
      window.removeEventListener("keydown", onKey, true);
    }
    window.addEventListener("pointerdown", onPointer, true);
    window.addEventListener("mousedown", onPointer, true);
    window.addEventListener("keydown", onKey, true);
    return cleanup;
  }, [forcedTable]);
  return pointerSeen;
}

function GraphOutline({
  nodes,
  edges,
  focusId,
  onFocus,
  loading,
}: {
  nodes: GraphNode[];
  edges: GraphEdge[];
  focusId: string | null;
  onFocus: (id: string) => void;
  /** The fetch hasn't resolved yet: show a loading row inside the table shell itself, rather than a totally
   *  separate "Loading graph…" paragraph with no table in it at all -- the accessible default (DEC-020) means
   *  the table/caption should exist on screen from first paint, not only once data lands. */
  loading?: boolean;
}) {
  const incoming = useMemo(() => {
    const m = new Map<string, GraphEdge[]>();
    for (const e of edges) m.set(e.to, [...(m.get(e.to) ?? []), e]);
    return m;
  }, [edges]);
  return (
    <div className="tablewrap">
      <table>
        <caption className="skip">The same node set and edges as the graph, as a table (accessible default)</caption>
        <thead>
          <tr>
            <th scope="col">Node</th>
            <th scope="col">State</th>
            <th scope="col">Gate</th>
            <th scope="col">Depends on</th>
          </tr>
        </thead>
        <tbody>
          {nodes.map((n) => {
            const deps = incoming.get(n.id) ?? [];
            return (
              <tr key={n.id} data-selected={n.id === focusId || undefined}>
                <td>
                  <button type="button" className="linklike mono" onClick={() => onFocus(n.id)} aria-current={n.id === focusId ? "true" : undefined}>
                    {n.id}
                  </button>
                </td>
                <td>{n.state ? <StateChip value={n.state} /> : <span className="hint">discovered, not evaluated</span>}</td>
                <td className="mono">{n.gate ? n.gate.mode ?? "—" : <span className="dash">–</span>}</td>
                <td className="hint">
                  {deps.length
                    ? deps.map((d, i) => (
                        <span key={`${d.from}:${i}`} className="mono">
                          {i > 0 ? ", " : ""}
                          {d.from}
                          {d.orderOk ? "" : " (✕ order)"}
                        </span>
                      ))
                    : "—"}
                </td>
              </tr>
            );
          })}
          {!nodes.length ? (
            <tr>
              <td colSpan={4}>
                {loading ? (
                  <span className="hint" aria-live="polite">
                    Loading graph&hellip;
                  </span>
                ) : (
                  <>
                    <span className="dash">–</span> No node resolved for this pipeline.
                  </>
                )}
              </td>
            </tr>
          ) : null}
        </tbody>
      </table>
    </div>
  );
}

function GraphView() {
  const router = useRouter();
  const params = useSearchParams();
  const { session } = useAuth();
  const catalog = useCatalog();
  const { data: cat } = useLoad(session ? catalog.catalogPipelines : null, [session?.token], 30_000);

  const allIds = useMemo(() => cat?.groups.flatMap((g) => g.pipelines.map((p) => p.id)) ?? [], [cat]);
  const id = params.get("id") || allIds[0] || null;

  const { data, error, loading, reload } = useLoad(
    session && id ? () => catalog.catalogPipelineGraph(id) : null,
    [session?.token, id],
  );

  const [savedView, setSavedView] = usePref<string>(PREF.graphView, "");
  const forcedTable = useForcedTable(savedView);
  const pointerFirst = useKeyboardFirstGate(forcedTable);
  const [manualView, setManualView] = useState<View | null>(null);
  const view: View = manualView ?? (forcedTable ? "table" : pointerFirst ? "graph" : "table");

  const [focusId, setFocusId] = useState<string | null>(null);

  const onPickPipeline = useCallback(
    (next: string) => {
      const sp = new URLSearchParams(params.toString());
      sp.set("id", next);
      router.push(`/pipelines/graph?${sp.toString()}`);
    },
    [params, router],
  );

  if (!session)
    return (
      <>
        <PageHeader title="Pipelines graph" />
        <PageBody>
          <SignIn />
        </PageBody>
      </>
    );

  return (
    <>
      <PageHeader
        title="Pipelines graph"
        sub={id ? <span className="mono">{id}</span> : "no pipeline in scope"}
        actions={
          <>
            <button type="button" onClick={() => void reload()} disabled={loading}>
              {loading ? "Refreshing…" : "Refresh"}
            </button>
            <Link href="/pipelines/catalog">Back to Catalog</Link>
          </>
        }
      />
      <PageBody>
        <section className="panel" aria-labelledby="graph-view-h">
          <h2 id="graph-view-h" className="sr">
            View
          </h2>
          <div className="row" style={{ gap: 8, marginBottom: 10, flexWrap: "wrap" }}>
            {/* Always first in tab order, in BOTH views (PFP P2-4: "the first focusable element is Table view (accessible)"). */}
            <button type="button" aria-pressed={view === "table"} onClick={() => setManualView("table")}>
              Table view (accessible)
            </button>
            <button type="button" aria-pressed={view === "graph"} onClick={() => setManualView("graph")}>
              Graph view (React Flow)
            </button>
            <label className="hint" style={{ display: "inline-flex", alignItems: "center", gap: 4 }}>
              <input
                type="checkbox"
                checked={savedView === "table"}
                onChange={(e) => setSavedView(e.target.checked ? "table" : "")}
              />
              Always open in table view
            </label>
            {allIds.length > 1 ? (
              <label className="hint" style={{ display: "inline-flex", alignItems: "center", gap: 4 }}>
                Pipeline
                <select value={id ?? ""} onChange={(e) => onPickPipeline(e.target.value)}>
                  {allIds.map((pid) => (
                    <option key={pid} value={pid}>
                      {pid}
                    </option>
                  ))}
                </select>
              </label>
            ) : null}
          </div>
          {error ? <ErrorNote error={error} what={`the graph for ${id ?? "this pipeline"}`} /> : null}
          {/* Table view renders its own shell (caption included) immediately, with a loading row in place of
              data that hasn't arrived yet -- the accessible default (DEC-020) is a real table on screen from
              first paint, not a view that only exists once the fetch resolves. Graph view still waits for real
              data: React Flow has nothing useful to lay out before then, and nothing (e.g. the saved-preference
              reload check) depends on it appearing earlier. */}
          {!error && view === "table" ? (
            <GraphOutline nodes={data?.nodes ?? []} edges={data?.edges ?? []} focusId={focusId} onFocus={setFocusId} loading={!data} />
          ) : null}
          {!error && view === "graph" && !data ? <p className="hint" aria-live="polite">Loading graph&hellip;</p> : null}
          {!error && view === "graph" && data && data.nodes.length === 0 ? (
            <EmptyState title="No node resolved for this pipeline." line="Nothing to show in either view." />
          ) : null}
          {!error && view === "graph" && data && data.nodes.length > 0 ? (
            <GraphCanvas nodes={data.nodes} edges={data.edges} focusId={focusId} onFocus={setFocusId} />
          ) : null}
        </section>
      </PageBody>
    </>
  );
}

export default function Page() {
  return <GraphView />;
}
