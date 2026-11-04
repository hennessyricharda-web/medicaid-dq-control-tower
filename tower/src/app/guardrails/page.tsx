"use client";
/**
 * Guardrail events (wireframe 21), read-only: masking and suppression, rejected checks and fallbacks, blocked
 * wording, scope denials (audit duty only). Each event carries counts only, never names or values.
 */
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { ApiError } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useObs } from "@/lib/api/obs";
import { fmtTime } from "@/lib/format";
import { GUARDRAIL_CODES, type Guardrail } from "@/lib/types/obs";
import { SignIn } from "@/components/SignIn";
import { ErrorNote } from "@/components/ErrorNote";
import { ToneChip } from "@/components/Chip";
import { PageBody, PageHeader } from "@/components/layout/PageHeader";
import { SelectChip, Toolbar } from "@/components/layout/Toolbar";
import { DataTable, type Column } from "@/components/layout/DataTable";
import { EmptyState } from "@/components/layout/EmptyState";
import { RefLink } from "@/components/obs/common";

const isRef = (s: string) => /^(batch|rel|rule|unit|file|cr|dec|pipeline|taskgate|abc|results|decisions|units|trace):/.test(s);

function Guardrails() {
  const { session } = useAuth();
  const obs = useObs();
  const audit = !!session?.me.duties.some((d) => d.startsWith("audit:"));
  const [code, setCode] = useState<string>("");
  const [subject, setSubject] = useState("");
  const [trace, setTrace] = useState("");
  const [applied, setApplied] = useState({ subject: "", trace: "" });
  const [rows, setRows] = useState<Guardrail[] | null>(null);
  const [next, setNext] = useState<string | null>(null);
  const [err, setErr] = useState<ApiError | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(
    async (cursor: string | null) => {
      setBusy(true);
      setErr(null);
      try {
        const r = await obs.guardrails({ code: code || undefined, subject: applied.subject || undefined, trace_id: applied.trace || undefined, limit: 100, cursor });
        setRows((p) => (cursor && p ? [...p, ...r.events] : r.events));
        setNext(r.next);
      } catch (e) {
        setErr(e instanceof ApiError ? e : new ApiError(0, { detail: String(e) }));
        if (!cursor) setRows([]);
      } finally {
        setBusy(false);
      }
    },
    [obs, code, applied],
  );
  useEffect(() => void load(null), [load]);

  const codes = GUARDRAIL_CODES.filter((c) => audit || c !== "SCOPE_DENIED");
  const cols: Column<Guardrail>[] = [
    { id: "at", header: "At", cell: (g) => fmtTime(g.at), width: 190 },
    { id: "code", header: "Code", cell: (g) => <ToneChip tone={g.fail_mode === "closed" ? "bad" : "info"} label={g.code} />, width: 190 },
    { id: "mode", header: "Fail mode", cell: (g) => g.fail_mode, width: 90 },
    { id: "subject", header: "Subject", cell: (g) => (isRef(g.subject) ? <RefLink refId={g.subject} /> : <span className="mono">{g.subject}</span>), title: (g) => g.subject },
    { id: "seat", header: "Seat", cell: (g) => g.seat, width: 100 },
    { id: "counts", header: "Counts", cell: (g) => Object.entries(g.detail).map(([k, v]) => `${k} ${v}`).join(", "), width: 200 },
    {
      id: "trace",
      header: "Trace",
      cell: (g) =>
        g.trace_id ? (
          <Link className="mono" href={`/runs/${encodeURIComponent(g.trace_id)}${g.span_id ? `?span=${g.span_id}` : ""}`}>
            {g.trace_id}
          </Link>
        ) : null,
      p: 2,
    },
  ];
  return (
    <>
      <PageHeader
        title="Guardrails"
        sub={rows ? `${rows.length} event(s)${next ? ", more available" : ""}; read-only` : "loading…"}
        scope={audit ? "Audit duty: every event, including scope denials" : "Scope denials are visible to the audit duty only"}
      />
      <Toolbar>
        <SelectChip id="gr-code" label="Code" value={code} options={codes.map((c) => ({ value: c, label: c }))} onChange={setCode} />
        <form
          className="fld q"
          onSubmit={(e) => {
            e.preventDefault();
            setApplied({ subject: subject.trim(), trace: trace.trim() });
          }}
        >
          <label htmlFor="gr-subj" style={{ margin: 0, fontWeight: 400, fontSize: "inherit", color: "inherit" }}>
            Subject
          </label>
          <input id="gr-subj" type="text" data-page-filter value={subject} onChange={(e) => setSubject(e.target.value)} placeholder="e.g. unit:… then Enter" />
        </form>
        <form
          className="fld"
          onSubmit={(e) => {
            e.preventDefault();
            setApplied({ subject: subject.trim(), trace: trace.trim() });
          }}
        >
          <label htmlFor="gr-trace" style={{ margin: 0, fontWeight: 400, fontSize: "inherit", color: "inherit" }}>
            Trace
          </label>
          <input id="gr-trace" type="text" value={trace} onChange={(e) => setTrace(e.target.value)} placeholder="trace id" style={{ width: 170 }} />
        </form>
      </Toolbar>
      <PageBody>
        <ErrorNote error={err} what="guardrail events" />
        {rows && rows.length === 0 && !err ? (
          <EmptyState title="No guardrail events match." line="Events are counts only: what was masked, suppressed, rejected or refused, never the values." />
        ) : rows ? (
          <div style={{ display: "flex", flexDirection: "column", height: "calc(100vh - 230px)", border: "1px solid var(--border)", borderRadius: 5 }}>
            <DataTable columns={cols} rows={rows} rowKey={(g) => String(g.id)} caption="Guardrail events, newest first" primary />
            <div className="statusfoot">
              <span>{rows.length} event(s)</span>
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
          <p className="hint">Loading&hellip;</p>
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
        <PageHeader title="Guardrails" />
        <PageBody>
          <SignIn />
        </PageBody>
      </>
    );
  return <Guardrails />;
}
