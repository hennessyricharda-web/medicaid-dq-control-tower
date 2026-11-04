"use client";
/**
 * Traces (wireframe 16): the runs the agent recorded, filtered by kind, DAG (pipeline) and night, or every traced
 * step that names one unit. Table first (DEC-020). Pick two runs to compare their outcomes.
 */
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useState } from "react";
import { ApiError } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useObs } from "@/lib/api/obs";
import { fmtTime } from "@/lib/format";
import { RUN_KINDS, type RunRow, type Span } from "@/lib/types/obs";
import { SignIn } from "@/components/SignIn";
import { ErrorNote } from "@/components/ErrorNote";
import { ToneChip } from "@/components/Chip";
import { PageBody, PageHeader } from "@/components/layout/PageHeader";
import { SelectChip, Toolbar } from "@/components/layout/Toolbar";
import { DataTable, type Column } from "@/components/layout/DataTable";
import { EmptyState } from "@/components/layout/EmptyState";
import { useUrlParam } from "@/components/layout/SplitPane";
import { RefLink, SourceModeChip, StatusChip, durMs, fmtDur } from "@/components/obs/common";

function setParams(router: ReturnType<typeof useRouter>, next: Record<string, string>) {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(next)) if (v) p.set(k, v);
  router.replace(`/runs${p.toString() ? `?${p}` : ""}`);
}

function UnitTraceView({ unitKey }: { unitKey: string }) {
  const obs = useObs();
  const [spans, setSpans] = useState<Span[] | null>(null);
  const [err, setErr] = useState<ApiError | null>(null);
  useEffect(() => {
    let live = true;
    setSpans(null);
    setErr(null);
    obs.unitTrace(unitKey).then(
      (t) => live && setSpans(t.spans),
      (e: unknown) => live && setErr(e instanceof ApiError ? e : new ApiError(0, { detail: String(e) })),
    );
    return () => {
      live = false;
    };
  }, [obs, unitKey]);
  const cols: Column<Span>[] = [
    { id: "at", header: "Started", cell: (s) => fmtTime(s.started_at), width: 190 },
    { id: "name", header: "Step", cell: (s) => s.name, mono: true },
    { id: "seat", header: "Seat", cell: (s) => s.seat, width: 110 },
    { id: "status", header: "Status", cell: (s) => <StatusChip status={s.status} />, width: 90 },
    { id: "dur", header: "Duration", cell: (s) => fmtDur(durMs(s.started_at, s.ended_at)), num: true, width: 90 },
    {
      id: "check",
      header: "Release check",
      cell: (s) => {
        const c = s.linked.release_checks?.[0];
        if (!c) return null;
        return c.ok ? <ToneChip tone="ok" label="may release" /> : <span title={c.reasons.join("; ")}><ToneChip tone="warn" label={`held: ${c.reasons[0] ?? ""}`} /></span>;
      },
    },
    { id: "src", header: "Text source", cell: (s) => (s.llm_call ? <SourceModeChip mode={s.llm_call.source_mode} /> : null), width: 110 },
  ];
  if (err) return <ErrorNote error={err} what={`the trace of unit ${unitKey}`} />;
  if (!spans) return <p className="hint">Loading the unit&rsquo;s trace&hellip;</p>;
  return (
    <>
      <p className="hint" style={{ margin: "0 0 8px" }}>
        {spans.length} traced step(s) name <RefLink refId={`unit:${unitKey}`} />. A unit trace lists steps across runs; the API does not carry each
        step&rsquo;s run id, so the steps are not linked to their runs here.
      </p>
      {spans.length ? (
        <div style={{ display: "flex", minHeight: 0, height: "calc(100vh - 250px)", border: "1px solid var(--border)", borderRadius: 5 }}>
          <DataTable columns={cols} rows={spans} rowKey={(s) => s.span_id} caption={`Traced steps for unit ${unitKey}`} primary />
        </div>
      ) : (
        <EmptyState inline title="No traced steps name this unit in your scope." />
      )}
    </>
  );
}

function Traces() {
  const { session } = useAuth();
  const obs = useObs();
  const router = useRouter();
  const kind = useUrlParam("kind") ?? "";
  const pipeline = useUrlParam("pipeline") ?? "";
  const night = useUrlParam("night") ?? "";
  const unit = useUrlParam("unit") ?? "";
  const [rows, setRows] = useState<RunRow[] | null>(null);
  const [next, setNext] = useState<string | null>(null);
  const [err, setErr] = useState<ApiError | null>(null);
  const [busy, setBusy] = useState(false);
  const [checked, setChecked] = useState<Set<string>>(new Set());
  const [unitDraft, setUnitDraft] = useState(unit);
  const [dagDraft, setDagDraft] = useState(pipeline);
  useEffect(() => setUnitDraft(unit), [unit]);
  useEffect(() => setDagDraft(pipeline), [pipeline]);

  const load = useCallback(
    async (cursor: string | null) => {
      setBusy(true);
      setErr(null);
      try {
        const r = await obs.runs({ kind: kind || undefined, pipeline: pipeline || undefined, night: night || undefined, limit: 50, cursor });
        setRows((prev) => (cursor && prev ? [...prev, ...r.runs] : r.runs));
        setNext(r.next);
      } catch (e) {
        setErr(e instanceof ApiError ? e : new ApiError(0, { detail: String(e) }));
      } finally {
        setBusy(false);
      }
    },
    [obs, kind, pipeline, night],
  );
  useEffect(() => {
    setChecked(new Set()); // a new filter is a new list: nothing stays ticked
    if (!unit) void load(null);
  }, [load, unit]);

  const params = { kind, pipeline, night, unit };
  const pick = [...checked];
  const cols: Column<RunRow>[] = useMemo(
    () => [
      {
        id: "trace",
        header: "Trace",
        cell: (r) => (
          <Link className="mono" href={`/runs/${encodeURIComponent(r.trace_id)}`} onClick={(e) => e.stopPropagation()}>
            {r.trace_id}
          </Link>
        ),
        title: (r) => r.trace_id,
      },
      { id: "kind", header: "Kind", cell: (r) => r.kind, width: 120 },
      { id: "status", header: "Status", cell: (r) => <StatusChip status={r.status} />, width: 110 },
      { id: "night", header: "Night", cell: (r) => r.night_date, width: 110 },
      { id: "dag", header: "DAG", cell: (r) => r.pipeline_id, mono: true, width: 140 },
      { id: "attempt", header: "Attempt", cell: (r) => r.attempt, num: true, width: 76, p: 2 },
      { id: "steps", header: "Steps", cell: (r) => r.span_count, num: true, width: 70 },
      { id: "src", header: "Text source", cell: (r) => (r.source_mode ? <SourceModeChip mode={r.source_mode} /> : null), width: 110 },
      { id: "at", header: "Started", cell: (r) => fmtTime(r.started_at), width: 190 },
    ],
    [],
  );

  return (
    <>
      <PageHeader
        title="Traces"
        sub={unit ? "steps that name one unit" : rows ? `${rows.length} run(s)${next ? ", more available" : ""}` : "loading…"}
        scope={session?.me.authorities.length ? `Showing your scope: ${session.me.authorities.join(", ")}` : undefined}
        actions={
          <>
            <button
              type="button"
              className="primary"
              disabled={pick.length !== 2}
              onClick={() => router.push(`/runs/diff?a=${encodeURIComponent(pick[0]!)}&b=${encodeURIComponent(pick[1]!)}`)}
              title="Tick two runs to compare their outcomes"
            >
              Compare {pick.length === 2 ? "2 runs" : "two runs"}
            </button>
          </>
        }
      />
      <Toolbar>
        <SelectChip id="tr-kind" label="Kind" value={kind} options={RUN_KINDS.map((k) => ({ value: k, label: k }))} onChange={(v) => setParams(router, { ...params, kind: v, unit: "" })} />
        <form
          className="fld"
          onSubmit={(e) => {
            e.preventDefault();
            setParams(router, { ...params, pipeline: dagDraft.trim(), unit: "" });
          }}
        >
          <label htmlFor="tr-dag" style={{ margin: 0, fontWeight: 400, fontSize: "inherit", color: "inherit" }}>
            DAG
          </label>
          <input id="tr-dag" type="text" value={dagDraft} onChange={(e) => setDagDraft(e.target.value)} placeholder="pipeline id" style={{ width: 130 }} />
        </form>
        <span className="fld">
          <label htmlFor="tr-night" style={{ margin: 0, fontWeight: 400, fontSize: "inherit", color: "inherit" }}>
            Night
          </label>
          <input id="tr-night" type="date" value={night} onChange={(e) => setParams(router, { ...params, night: e.target.value, unit: "" })} style={{ height: 22, border: 0, padding: 0 }} />
        </span>
        <form
          className="fld q"
          onSubmit={(e) => {
            e.preventDefault();
            setParams(router, { kind: "", pipeline: "", night: "", unit: unitDraft.trim() });
          }}
        >
          <label htmlFor="tr-unit" style={{ margin: 0, fontWeight: 400, fontSize: "inherit", color: "inherit" }}>
            Unit
          </label>
          <input id="tr-unit" type="text" data-page-filter value={unitDraft} onChange={(e) => setUnitDraft(e.target.value)} placeholder="unit key, then Enter" />
        </form>
        <span className="sp" />
        {kind || pipeline || night || unit ? (
          <button type="button" onClick={() => setParams(router, {})}>
            Clear filters
          </button>
        ) : null}
      </Toolbar>
      <PageBody>
        {unit ? (
          <UnitTraceView unitKey={unit} />
        ) : (
          <>
            <ErrorNote error={err} what="the runs" />
            {rows && rows.length === 0 ? (
              <EmptyState
                title="No runs match."
                line="Engine-wide runs (nights, publish cycles, recovery) are visible with the operations or audit duty only; your scope may hide them."
              />
            ) : rows ? (
              <div style={{ display: "flex", flexDirection: "column", height: "calc(100vh - 250px)", border: "1px solid var(--border)", borderRadius: 5 }}>
                <DataTable
                  columns={cols}
                  rows={rows}
                  rowKey={(r) => r.trace_id}
                  caption="Recorded runs, newest first"
                  checked={checked}
                  onCheck={(k, on) =>
                    setChecked((s) => {
                      const n = new Set(s);
                      if (on) n.add(k);
                      else n.delete(k);
                      return n;
                    })
                  }
                  onOpen={(k) => router.push(`/runs/${encodeURIComponent(k)}`)}
                  primary
                />
                <div className="statusfoot">
                  <span>
                    {rows.length} run(s) · {checked.size} ticked
                  </span>
                  <span className="sp" />
                  {next ? (
                    <button type="button" onClick={() => void load(next)} disabled={busy}>
                      {busy ? "Loading…" : "Load more"}
                    </button>
                  ) : (
                    <span>end of list</span>
                  )}
                </div>
              </div>
            ) : (
              <p className="hint">Loading runs&hellip;</p>
            )}
          </>
        )}
      </PageBody>
    </>
  );
}

export default function Page() {
  const { session } = useAuth();
  if (!session)
    return (
      <>
        <PageHeader title="Traces" />
        <PageBody>
          <SignIn />
        </PageBody>
      </>
    );
  return <Traces />;
}
