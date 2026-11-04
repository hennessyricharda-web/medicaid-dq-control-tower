"use client";
/**
 * Trace detail (wireframe 17): the run's facts, its span tree as a table (the accessible default, DEC-020) or with a
 * timing waterfall, and the selected span: attributes, refs, release checks, the LLM call and guardrail events.
 */
import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import { ApiError } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useObs } from "@/lib/api/obs";
import { fmtTime, shortHash } from "@/lib/format";
import { PREF, usePref } from "@/lib/prefs";
import type { RunDetail, Span } from "@/lib/types/obs";
import type { Pipelines } from "@/lib/types";
import { SignIn } from "@/components/SignIn";
import { ErrorNote } from "@/components/ErrorNote";
import { ToneChip } from "@/components/Chip";
import { PageBody, PageHeader } from "@/components/layout/PageHeader";
import { FilterField, Seg, Toolbar } from "@/components/layout/Toolbar";
import { SplitPane, useSel } from "@/components/layout/SplitPane";
import { DataTable, type Column } from "@/components/layout/DataTable";
import { EmptyState } from "@/components/layout/EmptyState";
import { RefLink, SourceModeChip, StatusChip, durMs, fmtAttr, fmtDur } from "@/components/obs/common";

type View = "table" | "waterfall";
interface Row {
  s: Span;
  depth: number;
}

/** Depth-first order of the span tree (children by ordinal); orphans (parent out of scope) become roots. */
function treeOrder(spans: Span[]): Row[] {
  const ids = new Set(spans.map((s) => s.span_id));
  const kids = new Map<string | null, Span[]>();
  for (const s of spans) {
    const p = s.parent_span_id && ids.has(s.parent_span_id) ? s.parent_span_id : null;
    const l = kids.get(p) ?? [];
    l.push(s);
    kids.set(p, l);
  }
  for (const l of kids.values()) l.sort((a, b) => a.ordinal - b.ordinal);
  const out: Row[] = [];
  const walk = (p: string | null, d: number) => {
    for (const s of kids.get(p) ?? []) {
      out.push({ s, depth: d });
      walk(s.span_id, d + 1);
    }
  };
  walk(null, 0);
  return out;
}

const VERDICT_TONE = { PASS: "ok", REJECT: "bad", NOT_CHECKED: "neutral", NO_OUTPUT: "warn" } as const;

function SpanDetail({ s }: { s: Span }) {
  const checks = s.linked.release_checks ?? [];
  const c = s.llm_call;
  return (
    <div className="detail" aria-labelledby="sd-h">
      <div className="dhead">
        <div className="row">
          <StatusChip status={s.status} />
          {s.seat ? <span className="chip t-neutral">seat {s.seat}</span> : null}
          <span className="hint">ordinal {s.ordinal}</span>
        </div>
        <h2 id="sd-h">{s.name}</h2>
        <span className="hint">
          {fmtTime(s.started_at)} → {fmtTime(s.ended_at)} · {fmtDur(durMs(s.started_at, s.ended_at))}
        </span>
        {s.subject ? (
          <span>
            Subject <RefLink refId={s.subject} />
          </span>
        ) : null}
      </div>
      <section className="sec" aria-labelledby="sd-attrs">
        <h3 id="sd-attrs">
          Attributes <span className="muted">{Object.keys(s.attrs).length}</span>
        </h3>
        {Object.keys(s.attrs).length ? (
          <dl className="kv">
            {Object.entries(s.attrs)
              .sort(([a], [b]) => a.localeCompare(b))
              .map(([k, v]) => (
                <div key={k} style={{ display: "contents" }}>
                  <dt className="mono">{k}</dt>
                  <dd className="mono">{fmtAttr(v)}</dd>
                </div>
              ))}
          </dl>
        ) : (
          <p className="muted" style={{ margin: 0 }}>
            None recorded.
          </p>
        )}
        {s.refs.length ? (
          <div className="cite-list">
            <span className="hint">Refs</span>
            {s.refs.map((r) => (
              <span key={r} className="cite">
                <RefLink refId={r} />
              </span>
            ))}
          </div>
        ) : null}
      </section>
      {checks.length ? (
        <section className="sec" aria-labelledby="sd-rc">
          <h3 id="sd-rc">
            Release checks <span className="muted">{checks.length}</span>
          </h3>
          <table className="dt">
            <thead>
              <tr>
                <th scope="col">Unit</th>
                <th scope="col">Result</th>
                <th scope="col">Reasons</th>
              </tr>
            </thead>
            <tbody>
              {checks.map((x, i) => (
                <tr key={`${x.unit_key}-${i}`}>
                  <td>
                    <RefLink refId={`unit:${x.unit_key}`} />
                  </td>
                  <td>{x.ok ? <ToneChip tone="ok" label="may release" /> : <ToneChip tone="warn" label="not released" />}</td>
                  <td style={{ whiteSpace: "normal" }}>{x.reasons.length ? x.reasons.join("; ") : <span className="dash">–</span>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      ) : null}
      {c ? (
        <section className="sec" aria-labelledby="sd-llm">
          <h3 id="sd-llm">
            Text generation <SourceModeChip mode={c.source_mode} />
            <ToneChip tone={VERDICT_TONE[c.verdict] ?? "warn"} label={`check ${c.verdict}`} />
          </h3>
          {c.source_mode !== "LIVE" ? <p className="hint" style={{ margin: 0 }}>No model was called for this text ({c.source_mode}).</p> : null}
          <dl className="kv">
            <dt>Provider / transport</dt>
            <dd>
              {c.provider} / <span className="mono">{c.transport}</span>
              {c.model ? (
                <>
                  {" "}
                  · model <span className="mono">{c.model}</span>
                </>
              ) : null}
            </dd>
            <dt>Explanation kind</dt>
            <dd className="mono">{c.kind}</dd>
            <dt>Reason codes</dt>
            <dd>{c.reason_codes.length ? c.reason_codes.map((r) => <span key={r} className="chip t-bad" style={{ marginRight: 4 }}>{r}</span>) : <span className="dash">–</span>}</dd>
            <dt>Versions</dt>
            <dd className="mono">
              {c.prompt_version} · {c.check_version} · {c.template_version}
            </dd>
            <dt>Facts hash</dt>
            <dd>
              <code title={c.facts_hash}>{shortHash(c.facts_hash, 16)}</code>
            </dd>
            <dt>Tokens in / out</dt>
            <dd>
              {c.input_tokens ?? "–"} / {c.output_tokens ?? "–"}
            </dd>
            <dt>Latency</dt>
            <dd>{c.latency_ms === null ? "–" : `${c.latency_ms} ms`}</dd>
            {c.finish_reason ? (
              <>
                <dt>Finish reason</dt>
                <dd className="mono">{c.finish_reason}</dd>
              </>
            ) : null}
          </dl>
          {c.output_text ? (
            <div className="explanation compact">
              <p className="explanation-text">{c.output_text}</p>
            </div>
          ) : (
            <p className="hint" style={{ margin: 0 }}>
              No output text is stored for this call (only text that passed, or template text, is kept).
            </p>
          )}
        </section>
      ) : null}
      {s.guardrails.length ? (
        <section className="sec" aria-labelledby="sd-gr">
          <h3 id="sd-gr">
            Guardrail events <span className="muted">{s.guardrails.length}</span>
          </h3>
          <ul style={{ margin: 0, paddingLeft: 0, listStyle: "none", display: "flex", flexDirection: "column", gap: 4 }}>
            {s.guardrails.map((g) => (
              <li key={g.id}>
                <ToneChip tone={g.fail_mode === "closed" ? "bad" : "info"} label={g.code} title={`fail ${g.fail_mode}`} />{" "}
                <span className="hint">
                  fail {g.fail_mode} · {Object.entries(g.detail).map(([k, v]) => `${k} ${v}`).join(", ") || "no counts"}
                </span>
              </li>
            ))}
          </ul>
        </section>
      ) : null}
    </div>
  );
}

/**
 * PFP P1-2 / K1, FINAL (reversed) D4: "ran under profile X" is a client-side-only link, never a server-asserted
 * field. `dq_ctl.runs.profile_digests` (stamped on NIGHT roots only, D6) is genuinely not exposed by ANY existing
 * read path -- confirmed directly: `obs_routes.py`'s `_run_detail` SELECTs a named tuple of columns that excludes
 * it, and `contracts/obs.py`'s `Run` model has no such field and forbids extra keys (`ConfigDict(extra="forbid")`).
 * So the Tower cannot fetch the stamped digest to compare it against the profile route's own digest -- there is
 * only one side of that comparison available here, not two. Rather than fabricate a match/mismatch indicator this
 * screen cannot honestly compute, it instead links to each touched pipeline's profile AS OF this trace (the
 * already-real `as_of=<trace_id>` resolution, D9, which anchors on the trace's `config_digest` -- a column this
 * run detail DOES carry). This is a genuine, confirmed gap for the literal digest-equality demo moment; see the
 * seat's report to the orchestrator.
 */
function profileLinks(run: RunDetail["run"], spans: Span[], pipelines: Pipelines | null): string[] {
  if (run.kind !== "NIGHT" || !pipelines) return [];
  const relations = new Set(spans.map((s) => s.subject).filter((s): s is string => !!s && s.startsWith("rel:")).map((s) => s.slice(4)));
  if (!relations.size) return [];
  return pipelines.pipelines.filter((p) => p.gated_nodes.some((g) => relations.has(g.node))).map((p) => p.id);
}

function TraceDetail({ traceId }: { traceId: string }) {
  const obs = useObs();
  const { client } = useAuth();
  const [d, setD] = useState<RunDetail | null>(null);
  const [err, setErr] = useState<ApiError | null>(null);
  const [pipelines, setPipelines] = useState<Pipelines | null>(null);
  const [view, setView] = usePref<View>(PREF.density("trace-view"), "table");
  const [q, setQ] = useState("");
  const [sel, setSel] = useSel("span");
  const [exporting, setExporting] = useState(false);

  useEffect(() => {
    let live = true;
    setD(null);
    setErr(null);
    obs.run(traceId, true).then(
      (x) => live && setD(x),
      (e: unknown) => live && setErr(e instanceof ApiError ? e : new ApiError(0, { detail: String(e) })),
    );
    return () => {
      live = false;
    };
  }, [obs, traceId]);

  // Best-effort only, for the "ran under profile" links below; a failure here never blocks the trace itself.
  useEffect(() => {
    let live = true;
    client.pipelines().then(
      (p) => live && setPipelines(p),
      () => live && setPipelines(null),
    );
    return () => {
      live = false;
    };
  }, [client]);

  const rows = useMemo(() => (d ? treeOrder(d.spans) : []), [d]);
  const shown = useMemo(() => {
    const t = q.trim().toLowerCase();
    return t ? rows.filter((r) => `${r.s.name} ${r.s.subject ?? ""} ${r.s.seat ?? ""}`.toLowerCase().includes(t)) : rows;
  }, [rows, q]);
  useEffect(() => {
    if (d && (!sel || !d.spans.some((s) => s.span_id === sel)) && rows[0]) setSel(rows[0].s.span_id);
  }, [d, sel, rows, setSel]);

  if (err) {
    return (
      <>
        <PageHeader crumb={<Link href="/runs">Traces /</Link>} title={<span className="mono">{traceId}</span>} />
        <PageBody>
          <ErrorNote error={err} what={`run ${traceId}`} />
          {err.status === 404 ? <p className="hint">A run outside your scope answers exactly as a missing one does.</p> : null}
        </PageBody>
      </>
    );
  }
  if (!d) {
    return (
      <>
        <PageHeader crumb={<Link href="/runs">Traces /</Link>} title={<span className="mono">{traceId}</span>} />
        <PageBody>
          <p className="hint">Loading the run&hellip;</p>
        </PageBody>
      </>
    );
  }
  const t0 = Math.min(...d.spans.map((s) => Date.parse(s.started_at)), Date.parse(d.run.started_at));
  const t1 = Math.max(...d.spans.map((s) => Date.parse(s.ended_at)), t0 + 1);
  const total = t1 - t0;
  const last = d.events[d.events.length - 1];
  const selected = d.spans.find((s) => s.span_id === sel) ?? null;
  const pipelineIds = profileLinks(d.run, d.spans, pipelines);

  async function exportOtlp() {
    setExporting(true);
    try {
      const o = await obs.otlp(traceId);
      const url = URL.createObjectURL(new Blob([JSON.stringify(o, null, 2)], { type: "application/json" }));
      const a = document.createElement("a");
      a.href = url;
      a.download = `${traceId}.otlp.json`;
      a.click();
      URL.revokeObjectURL(url);
    } catch (e) {
      setErr(e instanceof ApiError ? e : new ApiError(0, { detail: String(e) }));
    } finally {
      setExporting(false);
    }
  }

  const cols: Column<Row>[] = [
    {
      id: "name",
      header: "Step",
      cell: (r) => (
        <span className="tree-name" style={{ paddingLeft: r.depth * 16 }} aria-level={r.depth + 1}>
          <span className="mono">{r.s.name}</span>
        </span>
      ),
      title: (r) => r.s.name,
    },
    ...(view === "table"
      ? ([
          { id: "seat", header: "Seat", cell: (r: Row) => r.s.seat, width: 100 },
          { id: "subject", header: "Subject", cell: (r: Row) => r.s.subject, mono: true, title: (r: Row) => r.s.subject ?? undefined },
        ] as Column<Row>[])
      : []),
    { id: "status", header: "Status", cell: (r) => <StatusChip status={r.s.status} />, width: 90 },
    { id: "start", header: "Start", cell: (r) => `+${fmtDur(Date.parse(r.s.started_at) - t0)}`, num: true, width: 90 },
    { id: "dur", header: "Duration", cell: (r) => fmtDur(durMs(r.s.started_at, r.s.ended_at)), num: true, width: 90 },
    ...(view === "waterfall"
      ? ([
          {
            id: "wf",
            header: "Timing",
            cell: (r: Row) => {
              const a = ((Date.parse(r.s.started_at) - t0) / total) * 100;
              const w = (durMs(r.s.started_at, r.s.ended_at) / total) * 100;
              return (
                <span className="wf" aria-hidden="true" style={{ display: "block" }}>
                  <i className={r.s.status} style={{ left: `${a}%`, width: `${Math.max(w, 0.4)}%` }} />
                </span>
              );
            },
            width: 320,
          },
        ] as Column<Row>[])
      : []),
    { id: "gr", header: "Guardrails", cell: (r) => (r.s.guardrails.length ? r.s.guardrails.map((g) => g.code).join(", ") : null), p: 2 },
  ];

  return (
    <>
      <PageHeader
        crumb={<Link href="/runs">Traces /</Link>}
        title={<span className="mono">{d.run.trace_id}</span>}
        sub={
          <>
            {d.run.kind} · state {d.run.state}
            {d.run.night_date ? ` · night ${d.run.night_date}` : ""} · {d.spans.length} step(s) shown
          </>
        }
        actions={
          <button type="button" onClick={() => void exportOtlp()} disabled={exporting} title="Download the visible spans as OTLP/JSON">
            {exporting ? "Exporting…" : "Export OTLP"}
          </button>
        }
      />
      <div className="tb" style={{ flexWrap: "wrap", gap: 12, fontSize: "var(--t-sm)" }} aria-label="Run facts">
        <StatusChip status={last?.event} />
        {d.run.source_mode ? <SourceModeChip mode={d.run.source_mode} /> : null}
        <span>
          deployment <b>{d.run.deployment}</b>
        </span>
        <span>attempt {d.run.attempt}</span>
        {d.run.pipeline_id ? (
          <span>
            DAG <span className="mono">{d.run.pipeline_id}</span>
          </span>
        ) : null}
        {d.run.person_id ? <span>by {d.run.person_id}</span> : <span>by the engine</span>}
        {d.run.parent_trace_id ? (
          <span>
            parent <RefLink refId={`trace:${d.run.parent_trace_id}`} />
          </span>
        ) : null}
        <span title={d.run.config_digest}>
          config <code>{shortHash(d.run.config_digest, 10)}</code>
        </span>
        <span title={d.run.code_digest}>
          code <code>{shortHash(d.run.code_digest, 10)}</code>
        </span>
        <span>started {fmtTime(d.run.started_at)}</span>
        <span>
          events: {d.events.map((e) => `${e.event}${e.error_class ? ` (${e.error_class})` : ""}`).join(" → ") || "none"}
        </span>
      </div>
      {pipelineIds.length ? (
        <p className="hint" role="note">
          View the profile as of this trace (no digest match is checked here) for{" "}
          {pipelineIds.map((pid, i) => (
            <span key={pid}>
              {i > 0 ? ", " : ""}
              <Link className="mono" href={`/pipelines/${encodeURIComponent(pid)}/profile?as_of=${encodeURIComponent(d.run.trace_id)}`}>
                {pid}
              </Link>
            </span>
          ))}
          {". The stamped digest on this run is not exposed by this API; see the profile page's own digest."}
        </p>
      ) : null}
      <Toolbar>
        <FilterField value={q} onChange={setQ} placeholder="Filter steps by name, subject or seat" label="Filter steps" />
        <span className="sp" />
        <Seg
          label="View"
          value={view}
          options={[
            { value: "table", label: "Table" },
            { value: "waterfall", label: "Waterfall" },
          ]}
          onChange={setView}
        />
      </Toolbar>
      <SplitPane
        id="trace"
        defaultSize={420}
        label={["Steps", "Step detail"]}
        drawerable
        first={
          <>
            <DataTable
              columns={cols}
              rows={shown}
              rowKey={(r) => r.s.span_id}
              selected={sel}
              onSelect={setSel}
              caption={`Steps of run ${d.run.trace_id}, as a tree in start order`}
              rowClass={(r) => (r.s.status === "ERROR" ? "alarm" : undefined)}
              empty={<EmptyState inline title="No steps match this filter." />}
              primary
            />
            <div className="statusfoot">
              <span>
                {shown.length} of {d.spans.length} step(s) · total {fmtDur(total)}
              </span>
              {d.hidden_out_of_scope ? (
                <span className="chip t-info" role="note">
                  {d.hidden_label ?? "steps outside your scope are hidden"}
                </span>
              ) : null}
            </div>
          </>
        }
        second={selected ? <SpanDetail key={selected.span_id} s={selected} /> : <EmptyState inline title="Select a step." />}
      />
    </>
  );
}

export default function Page() {
  const params = useParams<{ traceId: string }>();
  const traceId = decodeURIComponent(params.traceId ?? "");
  const { session } = useAuth();
  if (!session)
    return (
      <>
        <PageHeader crumb={<Link href="/runs">Traces /</Link>} title={<span className="mono">{traceId}</span>} />
        <PageBody>
          <SignIn />
        </PageBody>
      </>
    );
  return <TraceDetail key={traceId} traceId={traceId} />;
}
