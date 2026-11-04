"use client";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useState } from "react";
import { useAuth } from "@/lib/auth";
import { useLoad } from "@/lib/useLoad";
import { Gate } from "@/components/SignIn";
import { ErrorNote } from "@/components/ErrorNote";
import { ToneChip } from "@/components/Chip";
import { CrStatusChip, LooseningBadge, OriginChip, RevocationLine } from "@/components/GovChips";
import { VerdictFlow, type VerdictOutcome } from "@/components/VerdictFlow";
import { Explanation } from "@/components/Explanation";
import { refOf } from "@/lib/partE";
import { keyDiff, ruleStatusTone, type DiffKind } from "@/lib/governance";
import { fmtTime, shortHash } from "@/lib/format";
import { orgLabel, useTenant } from "@/lib/tenant";
import type { CrDetail, CrItem, RuleFacts, Tenant } from "@/lib/types";
import { PageBody, PageHeader } from "@/components/layout/PageHeader";
import { useBacktest } from "@/lib/api/backtest";
import type { BacktestDetailResponse, BacktestReport, CrEvidenceRow } from "@/lib/types/backtest";

const MARK: Record<DiffKind, string> = { added: "+", removed: "−", changed: "~", same: "=" };

function ItemDiff({ item, idx }: { item: CrItem; idx: number }) {
  const [view, setView] = useState<"keys" | "json">("keys");
  const [showSame, setShowSame] = useState(false);
  const proposed = item.op === "DELETE" ? null : item.content;
  const rows = keyDiff(item.current, proposed);
  const changed = rows.filter((r) => r.kind !== "same");
  const shown = showSame ? rows : changed;
  return (
    <section className="panel" aria-labelledby={`item-${idx}`}>
      <h3 id={`item-${idx}`} className="row" style={{ gap: 8 }}>
        <span className="mono">{item.op}</span> {item.kind} <span className="mono">{item.artifact_id}</span>
        <span className="hint">
          {item.current === null || item.current === undefined ? "new artifact" : `${changed.length} key(s) differ`}
          {item.content_hash ? (
            <>
              {" "}
              · content <code title={item.content_hash}>{shortHash(item.content_hash, 10)}</code>
            </>
          ) : null}
        </span>
      </h3>
      {item.kind === "REVOCATION" ? <RevocationLine content={item.content} /> : null}
      <div className="row" style={{ marginBottom: 8 }}>
        <button type="button" aria-pressed={view === "keys"} onClick={() => setView("keys")}>
          Key-level diff
        </button>
        <button type="button" aria-pressed={view === "json"} onClick={() => setView("json")}>
          Side by side JSON
        </button>
        {view === "keys" ? (
          <label style={{ fontWeight: 500, display: "inline-flex", gap: 6, margin: 0 }}>
            <input type="checkbox" checked={showSame} onChange={(e) => setShowSame(e.target.checked)} /> show unchanged keys
          </label>
        ) : null}
      </div>
      {view === "keys" ? (
        shown.length ? (
          <div className="tablewrap">
            <table>
              <caption className="skip">Key-level diff for {item.artifact_id}: current versus proposed</caption>
              <thead>
                <tr>
                  <th scope="col">Change</th>
                  <th scope="col">Key</th>
                  <th scope="col">Current</th>
                  <th scope="col">Proposed</th>
                </tr>
              </thead>
              <tbody>
                {shown.map((r) => (
                  <tr key={r.key} className={r.kind === "same" ? undefined : `diff-${r.kind}`}>
                    <td>
                      <span className="diff-mark" aria-hidden="true">
                        {MARK[r.kind]}{" "}
                      </span>
                      {r.kind}
                    </td>
                    <td className="mono">{r.key}</td>
                    <td className="mono">{r.before ?? <span className="muted">(absent)</span>}</td>
                    <td className="mono">{r.after ?? <span className="muted">(absent)</span>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="muted">No key differs from the current version.</p>
        )
      ) : (
        <div className="split">
          <div>
            <h4 className="hint" style={{ margin: "0 0 4px" }}>
              Current (effective)
            </h4>
            <pre className="json" tabIndex={0} aria-label="Current JSON">
              {item.current === null || item.current === undefined ? "(none: new artifact)" : JSON.stringify(item.current, null, 2)}
            </pre>
          </div>
          <div>
            <h4 className="hint" style={{ margin: "0 0 4px" }}>
              Proposed
            </h4>
            <pre className="json" tabIndex={0} aria-label="Proposed JSON">
              {item.op === "DELETE" ? "(deleted)" : JSON.stringify(item.content, null, 2)}
            </pre>
          </div>
        </div>
      )}
    </section>
  );
}

function Facts({ f }: { f: RuleFacts | null }) {
  if (!f) return <span className="muted">(absent)</span>;
  return (
    <>
      <ToneChip tone={ruleStatusTone(f.status)} label={f.status} /> <span className="mono">{f.gating ?? "—"}</span>{" "}
      <code title={f.hash}>{shortHash(f.hash, 10)}</code>
    </>
  );
}

function Impact({ d }: { d: CrDetail }) {
  const imp = d.impact;
  return (
    <section className="panel" aria-labelledby="impact-h">
      <h2 id="impact-h">
        Impact <span className="sub">{imp.rules.length} compiled rule(s) change · computed by the server at propose time</span>
      </h2>
      {imp.ruleset_before !== undefined || imp.ruleset_after !== undefined ? (
        <p className="hint">
          Ruleset <code title={imp.ruleset_before ?? ""}>{imp.ruleset_before ? shortHash(imp.ruleset_before, 10) : "none"}</code> →{" "}
          <code title={imp.ruleset_after ?? ""}>{imp.ruleset_after ? shortHash(imp.ruleset_after, 10) : "none"}</code>
          {imp.summary?.rules_total !== undefined ? ` · ${imp.summary.rules_total} rule(s) total after` : ""}
        </p>
      ) : null}
      <Explanation e={imp.explanation} label="Impact explanation" />
      {imp.track_record ? (
        <div className="banner t-info" role="note">
          <strong>Track-record evidence signed with this request</strong>
          {imp.track_record.hash ? (
            <>
              {" "}
              <code title={imp.track_record.hash}>{shortHash(imp.track_record.hash, 12)}</code>
            </>
          ) : null}
          <div className="hint" style={{ marginTop: 4 }}>
            autonomous {imp.track_record.autonomous} · after veto window {imp.track_record.h_on_released} · vetoes {imp.track_record.vetoes} · overrides{" "}
            {imp.track_record.overrides} · DENY on released {imp.track_record.denies} · look-back withdrawn {imp.track_record.lookback_withdrawn}
          </div>
          {imp.track_record.task_gate && Object.keys(imp.track_record.task_gate).length ? (
            <div className="hint" style={{ marginTop: 4 }}>
              <strong>TASK_GATE evidence:</strong>{" "}
              {Object.entries(imp.track_record.task_gate).map(([aid, has], i) => (
                <span key={aid}>
                  {i > 0 ? " · " : ""}
                  <span className="mono">{aid}</span>{" "}
                  <ToneChip tone={has ? "ok" : "warn"} label={has ? "report linked" : "no report linked"} />
                </span>
              ))}
            </div>
          ) : null}
        </div>
      ) : null}
      {imp.loosening_reasons.length ? (
        <div className="banner t-warn" role="note">
          <strong>Loosening: two approvers required.</strong>
          <ul style={{ margin: "4px 0 0", paddingLeft: 18 }}>
            {imp.loosening_reasons.map((r) => (
              <li key={r}>{r}</li>
            ))}
          </ul>
        </div>
      ) : (
        <p className="hint">No loosening: nothing checked is removed and no gate becomes advisory.</p>
      )}
      {imp.rules.length ? (
        <div className="tablewrap">
          <table>
            <caption className="skip">Rule changes: status, gating and effective hash before and after</caption>
            <thead>
              <tr>
                <th scope="col">Rule</th>
                <th scope="col">Before (status · gating · hash)</th>
                <th scope="col" aria-label="becomes">
                  →
                </th>
                <th scope="col">After (status · gating · hash)</th>
                <th scope="col">What changes</th>
              </tr>
            </thead>
            <tbody>
              {imp.rules.map((r) => {
                const b = r.before;
                const a = r.after;
                const what = [
                  !b ? "added" : !a ? "removed" : null,
                  b && a && b.status !== a.status ? `status ${b.status} → ${a.status}` : null,
                  b && a && b.gating !== a.gating ? `gating ${b.gating} → ${a.gating}` : null,
                  b && a && b.hash !== a.hash ? "effective hash" : null,
                ].filter(Boolean);
                return (
                  <tr key={r.rule}>
                    <td className="mono">{r.rule}</td>
                    <td>
                      <Facts f={b} />
                    </td>
                    <td aria-hidden="true">→</td>
                    <td>
                      <Facts f={a} />
                    </td>
                    <td>{what.join("; ") || "—"}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      ) : (
        <p className="muted">No compiled rule changes.</p>
      )}
    </section>
  );
}

/**
 * Part F P1-4 (K3), additive -- a new "Backtest" panel. `evidence[]` rides on the already-fetched CR-detail
 * object (additive field, `app.py::cr_detail`'s own `"evidence": flat_cr_evidence(evidence)`); this component
 * does not restructure ItemDiff / Impact / Events above it.
 *
 * MANDATORY (K3_DESIGN.md §6 Decision 1): when a linked report's `delta_note` is non-null, it is rendered
 * VERBATIM, prominently, never buried under a details/summary toggle and never re-worded. `label` is shown
 * exactly as the server sent it (always "SYNTHESIZED" today); this panel never implies LIVE.
 */
function DeltaCell({ v }: { v: number }) {
  if (v === 0) return <span className="mono hint">0</span>;
  return <span className="mono">{v > 0 ? `+${v}` : v}</span>;
}

function BacktestReportCard({ report }: { report: BacktestDetailResponse }) {
  const r: BacktestReport = report.report;
  const relations = [...new Set([...Object.keys(r.base.relations), ...Object.keys(r.cand.relations)])].sort();
  return (
    <div className="panel" style={{ marginTop: 10, borderColor: "var(--info-bd)" }}>
      <p className="row" style={{ gap: 8, marginTop: 0 }}>
        <span className="mono">{report.report_id}</span>
        <ToneChip tone="neutral" label={report.label} title="Labels come from the stored row, never asserted live: a backtest report is always SYNTHESIZED, never LIVE" />
        <span className="hint">
          {r.pipeline_id} · {r.nights} night(s) · {fmtTime(report.at)}
        </span>
      </p>
      {r.delta_note ? (
        <div role="alert" className="banner t-warn" style={{ margin: "8px 0" }}>
          <strong>No causal block, by design.</strong> {r.delta_note}
        </div>
      ) : null}
      <div className="tablewrap">
        <table>
          <caption className="skip">Backtest relation deltas: base (today&rsquo;s live-effective configuration) versus candidate (this change request&rsquo;s items layered on top)</caption>
          <thead>
            <tr>
              <th scope="col">Relation</th>
              <th scope="col" className="num">RELEASED Δ</th>
              <th scope="col" className="num">HELD Δ</th>
              <th scope="col" className="num">PENDING Δ</th>
            </tr>
          </thead>
          <tbody>
            {relations.map((rel) => {
              const d = r.deltas.relations[rel] ?? {};
              return (
                <tr key={rel}>
                  <td className="mono">{rel}</td>
                  <td className="num"><DeltaCell v={d.RELEASED ?? 0} /></td>
                  <td className="num"><DeltaCell v={d.HELD ?? 0} /></td>
                  <td className="num"><DeltaCell v={d.PENDING_DEPENDENCY ?? 0} /></td>
                </tr>
              );
            })}
            {!relations.length ? (
              <tr>
                <td colSpan={4}><span className="dash">–</span> No relation measured.</td>
              </tr>
            ) : null}
          </tbody>
        </table>
      </div>
      <p className="hint" style={{ marginTop: 6 }}>
        Advisory would-HOLD delta <span className="mono">{r.deltas.would_hold > 0 ? `+${r.deltas.would_hold}` : r.deltas.would_hold}</span> ·
        trace-shape digest base <code title={r.trace_shape_digest_base}>{shortHash(r.trace_shape_digest_base, 10)}</code> vs cand{" "}
        <code title={r.trace_shape_digest_cand}>{shortHash(r.trace_shape_digest_cand, 10)}</code> · inputs{" "}
        <code title={r.inputs_digest}>{shortHash(r.inputs_digest, 10)}</code>
      </p>
      {Object.keys(r.cand.held_reasons).length ? (
        <p className="hint">
          Held-reason histogram (candidate): {Object.entries(r.cand.held_reasons).map(([k, n]) => `${k}: ${n}`).join(", ")}
        </p>
      ) : null}
    </div>
  );
}

function EvidenceRow({ e }: { e: CrEvidenceRow }) {
  const bt = useBacktest();
  const [open, setOpen] = useState(false);
  const { data, error, loading } = useLoad(open ? () => bt.backtestDetail(e.report_id) : null, [bt, open, e.report_id]);
  return (
    <li>
      <div className="row" style={{ gap: 8 }}>
        <button type="button" aria-expanded={open} onClick={() => setOpen((o) => !o)}>
          {open ? "Hide" : "View"} report <span className="mono">{e.report_id}</span>
        </button>
        <span className="hint">
          linked by <span className="mono">{e.linked_by}</span> · {fmtTime(e.at)} · items digest{" "}
          <code title={e.items_digest}>{shortHash(e.items_digest, 10)}</code>
        </span>
      </div>
      {open ? (
        loading && !data && !error ? (
          <p className="hint">Loading backtest report&hellip;</p>
        ) : error ? (
          <ErrorNote error={error} what={`backtest report ${e.report_id}`} />
        ) : data ? (
          <BacktestReportCard report={data} />
        ) : null
      ) : null}
    </li>
  );
}

function BacktestPanel({ d }: { d: CrDetail & { evidence?: CrEvidenceRow[] } }) {
  const evidence = d.evidence ?? [];
  const changesGateMode = d.items.some((it) => {
    if (it.kind !== "TASK_GATE" || it.op !== "PUT") return false;
    const old = it.current as { mode?: unknown } | null;
    const next = (it.content ?? {}) as { mode?: unknown };
    return !!old && typeof old === "object" && old.mode !== undefined && old.mode !== next.mode;
  });
  return (
    <section className="panel" aria-labelledby="bt-h">
      <h2 id="bt-h">
        Backtest <span className="sub">{evidence.length} report(s) linked</span>
      </h2>
      {!evidence.length ? (
        changesGateMode ? (
          <p className="hint">
            No backtest evidence is linked to this request yet. This CR changes an existing TASK_GATE&rsquo;s mode; the database refuses
            an APPROVE on it (<span className="mono">dq_ctl.mode_promotion_refusal</span>) until a backtest report is linked here, with a
            message starting &ldquo;promotion requires a linked backtest report&rdquo;. Run the <span className="mono">dqagent backtest</span>{" "}
            CLI over this change request, then reload this page.
          </p>
        ) : (
          <p className="hint">No backtest evidence is linked to this request. This item does not change an existing TASK_GATE&rsquo;s mode, so none is required.</p>
        )
      ) : (
        <ol style={{ margin: 0, paddingLeft: 20 }}>
          {evidence.map((e, i) => (
            <EvidenceRow key={e.report_id + i} e={e} />
          ))}
        </ol>
      )}
    </section>
  );
}

function Events({ d, tenant, me }: { d: CrDetail; tenant: Tenant | null; me: { person_id: string; org?: string | null } | undefined }) {
  return (
    <section className="panel" aria-labelledby="ev-h">
      <h2 id="ev-h">Events</h2>
      <ol className="timeline">
        {d.events.map((e, i) => (
          <li key={i}>
            <strong>{e.event}</strong> <span className="mono">{e.person_id ?? "system"}</span>{" "}
            {e.event === "APPROVED" ? (
              e.person_id && me && e.person_id === me.person_id && me.org ? (
                <span className="chip t-neutral" title="Approver organisation">{orgLabel(tenant, me.org)}</span>
              ) : (
                <span className="hint">(organisation not published by the API)</span>
              )
            ) : null}{" "}
            <span className="hint">{fmtTime(e.at)}</span>
            {e.detail !== null && e.detail !== undefined && e.detail !== "" ? (
              <div className="hint mono">{typeof e.detail === "string" ? e.detail : JSON.stringify(e.detail)}</div>
            ) : null}
          </li>
        ))}
      </ol>
    </section>
  );
}

function Detail({ id }: { id: string }) {
  const { session, client } = useAuth();
  const { tenant } = useTenant();
  const { data: d, error, reload } = useLoad(session ? () => client.crGet(id) : null, [session?.token, id], 20_000);
  const [outcome, setOutcome] = useState<VerdictOutcome | null>(null);
  const [stale, setStale] = useState<string | null>(null);
  const [flowKey, setFlowKey] = useState(0);
  if (error) return <ErrorNote error={error} what={`change request ${id}`} />;
  if (!d) return <p className="muted">Loading…</p>;
  const me = session?.me.person_id;
  const open = d.status === "AWAITING_APPROVAL";
  const approverOrg = tenant?.approver_org ?? null;
  const outsideOrg = !!approverOrg && !!session?.me.org && session.me.org !== approverOrg;
  const iApproved = d.events.some((e) => e.event === "APPROVED" && e.person_id === me);

  return (
    <>
      <div className="row" style={{ gap: 8, marginBottom: 10 }}>
        <CrStatusChip status={d.status} />
        {d.loosening ? <LooseningBadge /> : null}
        <OriginChip origin={d.origin} />
        <span>
          approvals <strong className="mono">{d.approvals}/{d.approvals_required}</strong>
        </span>
        <span className="hint">
          author <span className="mono">{d.author}</span> · state {d.state} · {fmtTime(d.at)}
        </span>
      </div>
      {outcome ? (
        <div role="status" className={`banner t-${outcome.tone}`}>
          <h2>{outcome.title}</h2>
          <p>{outcome.body}</p>
        </div>
      ) : null}
      {stale ? (
        <div role="alert" className="banner t-bad">
          <strong>Refused (409).</strong> {stale} The configuration moved under this request, or the step-up was reused. Reload and,
          if STALE, re-propose.
        </div>
      ) : null}
      <div className="cardgrid">
        <div>
          <section className="panel" aria-labelledby="why-h">
            <h2 id="why-h">In plain language</h2>
            <Explanation e={d.explanation} label="Change request explanation" />
            <h3>Author&rsquo;s rationale</h3>
            <blockquote style={{ margin: 0, padding: "6px 10px", borderLeft: "3px solid var(--border-strong)" }}>{d.rationale}</blockquote>
          </section>
          <h2>Items ({d.items.length})</h2>
          {d.items.map((it, i) => (
            <ItemDiff key={it.artifact_id + i} item={it} idx={i} />
          ))}
          <Impact d={d} />
          <BacktestPanel d={d} />
          <Events d={d} tenant={tenant} me={session?.me} />
        </div>
        <aside className="decide" aria-label="Decision">
          <section className="panel" aria-labelledby="dec-h" style={open && !d.x_you_are_author ? { borderColor: "var(--focus)", borderWidth: 2 } : undefined}>
            <h2 id="dec-h">Decision</h2>
            <p className="hint" style={{ marginTop: 0 }}>
              Approval requires:{" "}
              <strong>{approverOrg ? orgLabel(tenant, approverOrg) : tenant ? "any second person with the governance duty" : "…"}</strong>
              {approverOrg ? " (a second person from this organisation, with the governance duty)" : ""}
            </p>
            {!open ? (
              <p>
                This request is <strong>{d.status}</strong>. No decision is open.
              </p>
            ) : d.x_you_are_author ? (
              <p className="banner t-info" style={{ margin: 0 }}>
                <strong>Awaiting a second person.</strong> You proposed this request, so you cannot approve it (separation of duties,
                enforced by the database). Another person with the governance duty must review and approve with a step-up.
              </p>
            ) : outsideOrg ? (
              <p className="banner t-warn" style={{ margin: 0 }}>
                Your organisation ({session?.me.org_label ?? session?.me.org}) cannot approve configuration changes in this tenant. Approval
                requires {orgLabel(tenant, approverOrg)}. The server enforces this and answers 403 to any attempt.
              </p>
            ) : iApproved ? (
              <p className="banner t-info" style={{ margin: 0 }}>
                You have already approved this request. It is loosening and needs {d.approvals_required} approvers; another person must
                approve.
              </p>
            ) : (
              <>
                <p className="hint">
                  Your step-up signs exactly the items, the impact and the base versions shown on this page. {d.approvals_required - d.approvals}{" "}
                  more approval(s) apply it.
                </p>
                <VerdictFlow
                  key={flowKey}
                  idPrefix="cr"
                  what={`change request ${d.cr_id}`}
                  reviewText="I reviewed every item diff, the impact table and any loosening reasons myself, not only the rationale."
                  challenge={(v) => client.crChallenge(d.cr_id, v, refOf(d.explanation))}
                  decide={(v, t) => client.crDecide(d.cr_id, v, t)}
                  onOutcome={(o) => {
                    setOutcome(o);
                    setStale(null);
                    setFlowKey((k) => k + 1);
                    void reload();
                  }}
                  onStale={(why) => {
                    setStale(why);
                    setFlowKey((k) => k + 1);
                    void reload();
                  }}
                  onFailed={() => void reload()}
                />
              </>
            )}
          </section>
        </aside>
      </div>
    </>
  );
}

export default function Page() {
  const params = useParams<{ crId: string }>();
  const id = decodeURIComponent(params.crId ?? "");
  return (
    <>
      <PageHeader crumb={<Link href="/governance">Change requests /</Link>} title={<span className="mono">{id}</span>} sub="Change request" />
      <PageBody>
        <Gate>
          <Detail id={id} />
        </Gate>
      </PageBody>
    </>
  );
}
