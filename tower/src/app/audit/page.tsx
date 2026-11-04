"use client";
import { useState } from "react";
import { useAuth } from "@/lib/auth";
import { useLoad } from "@/lib/useLoad";
import { Gate } from "@/components/SignIn";
import { ErrorNote } from "@/components/ErrorNote";
import { GenericTable } from "@/components/GenericTable";
import { ApiError } from "@/lib/api";
import { shortHash } from "@/lib/format";
import type { AuditVerify, ChainVerify } from "@/lib/types";
import { PageBody, PageHeader } from "@/components/layout/PageHeader";

function ChainResult({ name, c }: { name: string; c: ChainVerify }) {
  return (
    <div>
      <h3 className={c.ok ? "verify-ok" : "verify-bad"}>
        <span aria-hidden="true">{c.ok ? "✓ " : "✕ "}</span>
        {name}: {c.ok ? "intact" : "BROKEN"}
      </h3>
      <dl className="kv">
        <dt>Verified through</dt>
        <dd className="mono">seq {c.through_seq}</dd>
        <dt>Head hash</dt>
        <dd>
          <code title={c.head_hash}>{shortHash(c.head_hash, 16)}</code>
        </dd>
        <dt>First break</dt>
        <dd>{c.first_break === null ? "none" : <strong className="mono">seq {c.first_break}</strong>}</dd>
      </dl>
    </div>
  );
}

function Verify() {
  const { client } = useAuth();
  const [r, setR] = useState<AuditVerify | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  async function run() {
    setBusy(true);
    setErr(null);
    try {
      setR(await client.auditVerify());
    } catch (e) {
      setErr((e instanceof ApiError ? e : new ApiError(0, { detail: String(e) })).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className="panel" aria-labelledby="ver-h" aria-live="polite">
      <h2 id="ver-h" className="row" style={{ justifyContent: "space-between" }}>
        <span>
          Hash chains <span className="sub">recomputed by the database, not by the Tower</span>
        </span>
        <button type="button" className="primary" onClick={() => void run()} disabled={busy}>
          {busy ? "Verifying…" : "Verify chain"}
        </button>
      </h2>
      {err ? (
        <p role="alert" className="err">
          Verify failed: {err}
        </p>
      ) : null}
      {r ? (
        <div className="split">
          <ChainResult name="Audit chain" c={r.audit} />
          <ChainResult name="Blackboard chain" c={r.blackboard} />
        </div>
      ) : (
        <p className="muted">Not verified in this session yet.</p>
      )}
    </section>
  );
}

function Audit() {
  const { session, client } = useAuth();
  const [limit, setLimit] = useState(50);
  const { data, error, loading, reload } = useLoad(session ? () => client.audit(limit) : null, [session?.token, limit]);
  return (
    <>
      <div className="row" style={{ marginBottom: 10 }}>
        <label htmlFor="limit" style={{ margin: 0 }}>
          Limit
        </label>
        <select id="limit" value={limit} onChange={(e) => setLimit(Number(e.target.value))}>
          {[25, 50, 100, 250].map((n) => (
            <option key={n} value={n}>
              {n}
            </option>
          ))}
        </select>
        <button type="button" onClick={() => void reload()} disabled={loading}>
          Refresh
        </button>
        <span className="hint">Read-only. The Tower cannot modify the audit chain.</span>
      </div>
      <ErrorNote error={error} what="audit" />
      <Verify />
      <section className="panel">
        <GenericTable rows={(data ?? []).map(({ label, ...rest }) => (label === undefined ? rest : { label, ...rest }))} caption="Audit log entries" empty={data ? "No audit entries." : "Loading…"} />
      </section>
    </>
  );
}

export default function Page() {
  return (
    <>
      <PageHeader title="Audit chain" />
      <PageBody>
      <Gate>
        <Audit />
      </Gate>
      </PageBody>
    </>
  );
}
