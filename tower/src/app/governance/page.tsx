"use client";
import Link from "next/link";
import { useState } from "react";
import { useAuth } from "@/lib/auth";
import { useLoad } from "@/lib/useLoad";
import { Gate } from "@/components/SignIn";
import { ErrorNote } from "@/components/ErrorNote";
import { CrStatusChip, LooseningBadge, OriginChip } from "@/components/GovChips";
import { fmtTime, shortHash } from "@/lib/format";
import { PageBody, PageHeader } from "@/components/layout/PageHeader";

const STATUSES = ["", "AWAITING_APPROVAL", "APPROVED", "APPLIED", "REJECTED", "STALE", "WITHDRAWN"];

function EffectiveConfig() {
  const { session, client } = useAuth();
  const { data, error } = useLoad(session ? client.govState : null, [session?.token]);
  if (error) return <ErrorNote error={error} what="the effective configuration" />;
  if (!data) return <p className="muted">Loading effective configuration…</p>;
  return (
    <details className="panel">
      <summary>
        <strong>Effective configuration</strong>{" "}
        <span className="hint">
          {data.state} · {data.ratified ? "ratified" : "NOT ratified (fail closed)"} · ruleset{" "}
          <code title={data.ruleset_version ?? ""}>{data.ruleset_version ? shortHash(data.ruleset_version, 10) : "none"}</code> ·{" "}
          {data.artifacts.length} artifact(s)
        </span>
      </summary>
      <div className="tablewrap" style={{ marginTop: 8 }}>
        <table>
          <caption className="skip">Effective configuration artifacts</caption>
          <thead>
            <tr>
              <th scope="col">Artifact</th>
              <th scope="col">Kind</th>
              <th scope="col" className="num">
                Version
              </th>
              <th scope="col">Content hash</th>
              <th scope="col">Approved by</th>
              <th scope="col">Change request</th>
              <th scope="col">At</th>
            </tr>
          </thead>
          <tbody>
            {data.artifacts.map((a) => (
              <tr key={a.artifact_id}>
                <td className="mono">{a.artifact_id}</td>
                <td>{a.kind}</td>
                <td className="num">{a.version}</td>
                <td>
                  <code title={a.content_hash}>{shortHash(a.content_hash, 10)}</code>
                </td>
                <td className="mono">{a.approved_by ?? "—"}</td>
                <td>
                  {a.cr_id === "TEST-GENESIS" ? (
                    <span className="mono">TEST-GENESIS</span>
                  ) : (
                    <Link className="mono" href={`/governance/${encodeURIComponent(a.cr_id)}`}>
                      {a.cr_id}
                    </Link>
                  )}
                </td>
                <td className="hint">{fmtTime(a.at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </details>
  );
}

function List() {
  const { session, client } = useAuth();
  const [status, setStatus] = useState("");
  const { data, error, loading, reload } = useLoad(session ? () => client.crList(status || undefined) : null, [session?.token, status], 30_000);
  const me = session?.me.person_id;
  return (
    <>
      <div className="row" style={{ marginBottom: 10 }}>
        <label htmlFor="cr-status" style={{ margin: 0 }}>
          Status
        </label>
        <select id="cr-status" value={status} onChange={(e) => setStatus(e.target.value)}>
          {STATUSES.map((s) => (
            <option key={s} value={s}>
              {s || "all"}
            </option>
          ))}
        </select>
        <button type="button" onClick={() => void reload()} disabled={loading}>
          {loading ? "Refreshing…" : "Refresh"}
        </button>
        <span className="hint">The runtime compiles rules only from approved configuration. An edit to a file changes nothing until approved here.</span>
      </div>
      <ErrorNote error={error} what="change requests" />
      <EffectiveConfig />
      <section className="panel" aria-labelledby="cr-h">
        <h2 id="cr-h">
          Change requests <span className="sub">{data ? `${data.length} request(s)` : "loading…"}</span>
        </h2>
        {data && data.length === 0 ? <p className="muted">No change requests{status ? ` with status ${status}` : ""}.</p> : null}
        {data && data.length > 0 ? (
          <div className="tablewrap">
            <table>
              <caption className="skip">Change requests, newest first</caption>
              <thead>
                <tr>
                  <th scope="col">Request</th>
                  <th scope="col">Status</th>
                  <th scope="col">Approvals</th>
                  <th scope="col">Origin / author</th>
                  <th scope="col">Items</th>
                  <th scope="col">Impact</th>
                  <th scope="col">Rationale</th>
                </tr>
              </thead>
              <tbody>
                {data.map((r) => (
                  <tr key={r.cr_id}>
                    <td>
                      <Link className="mono" href={`/governance/${encodeURIComponent(r.cr_id)}`}>
                        {r.cr_id}
                      </Link>
                      <div className="hint">
                        {r.state} · {fmtTime(r.at)}
                      </div>
                    </td>
                    <td>
                      <div className="row" style={{ gap: 4 }}>
                        <CrStatusChip status={r.status} />
                        {r.loosening ? <LooseningBadge /> : null}
                      </div>
                    </td>
                    <td className="mono">
                      {r.approvals}/{r.approvals_required}
                    </td>
                    <td>
                      <OriginChip origin={r.origin} />{" "}
                      <span className="mono">{r.author}</span>
                      {r.author === me ? <div className="hint">you are the author</div> : null}
                    </td>
                    <td>
                      <ul style={{ margin: 0, paddingLeft: 16 }}>
                        {r.items.map((i) => (
                          <li key={i.artifact_id + i.op}>
                            <span className="mono">{i.op}</span> {i.kind} <span className="mono">{i.artifact_id}</span>
                          </li>
                        ))}
                      </ul>
                    </td>
                    <td>
                      {r.impact_summary.changed ?? "—"} rule(s) changed
                      {r.impact_summary.loosening_reasons.length ? (
                        <div className="hint">{r.impact_summary.loosening_reasons.length} loosening reason(s)</div>
                      ) : null}
                    </td>
                    <td style={{ maxWidth: 360 }}>{r.rationale}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : null}
      </section>
    </>
  );
}

export default function Page() {
  return (
    <>
      <PageHeader title="Change requests" />
      <PageBody>
      <Gate>
        <List />
      </Gate>
      </PageBody>
    </>
  );
}
