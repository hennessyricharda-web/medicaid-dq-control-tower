"use client";
import { Fragment, useMemo, useState } from "react";
import { ApiError } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useLoad } from "@/lib/useLoad";
import { Gate } from "@/components/SignIn";
import { ErrorNote } from "@/components/ErrorNote";
import { ToneChip } from "@/components/Chip";
import { Explanation } from "@/components/Explanation";
import { StepUpForm, StepUpWindow, useStepUpCountdown, type StepUpGrant } from "@/components/StepUp";
import { EXPLAIN_CODES, feedAckCodesFor, feedKindInfo, refOf, STEP_UP_EXPLAIN, statusTone } from "@/lib/partE";
import { fmtNum, fmtTime } from "@/lib/format";
import type { FeedItem, ParallelRunRow, VerdictChallenge } from "@/lib/types";
import { PageBody, PageHeader } from "@/components/layout/PageHeader";
import { useShellData } from "@/components/layout/ShellData";

const EXPLAIN_HELP: Record<string, string> = {
  TIMING: "the systems cut off at different times; expected to converge",
  LEGACY_DEFECT: "the legacy system is wrong (step-up)",
  NEW_DEFECT: "the new pipeline is wrong; needs a fix",
  DEFINITION: "the two sides define the measure differently (step-up)",
  FIXED: "the cause was fixed and re-run",
};

function ExplainBreak({ row, onDone }: { row: ParallelRunRow; onDone: () => void }) {
  const { client, session } = useAuth();
  const [code, setCode] = useState<string>("");
  const [ch, setCh] = useState<VerdictChallenge | null>(null);
  const [grant, setGrant] = useState<StepUpGrant | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const left = useStepUpCountdown(grant);
  const needsStepUp = STEP_UP_EXPLAIN.has(code);
  if (!session) return null;

  async function send(token?: string) {
    setBusy(true);
    setErr(null);
    try {
      await client.prExplain(row.row_id, code, token, refOf(row.explanation));
      onDone();
    } catch (e) {
      const ae = e instanceof ApiError ? e : new ApiError(0, { detail: String(e) });
      setErr(`Refused: ${ae.message}`);
      setCh(null);
      setGrant(null);
    } finally {
      setBusy(false);
    }
  }
  async function challenge() {
    setBusy(true);
    setErr(null);
    try {
      setCh(await client.prChallenge(row.row_id, code, refOf(row.explanation)));
    } catch (e) {
      setErr(`Challenge failed: ${(e instanceof ApiError ? e : new ApiError(0, { detail: String(e) })).message}`);
    } finally {
      setBusy(false);
    }
  }

  const id = `pr-${row.row_id}`;
  return (
    <div style={{ maxWidth: 560 }}>
      <label htmlFor={`${id}-code`}>Explain code for this break</label>
      <div className="row">
        <select
          id={`${id}-code`}
          value={code}
          onChange={(e) => {
            setCode(e.target.value);
            setCh(null);
            setGrant(null);
          }}
          disabled={busy}
        >
          <option value="">choose…</option>
          {EXPLAIN_CODES.map((c) => (
            <option key={c} value={c}>
              {c}
            </option>
          ))}
        </select>
        {code && !needsStepUp ? (
          <button type="button" className="primary" onClick={() => void send()} disabled={busy}>
            Record {code}
          </button>
        ) : null}
        {code && needsStepUp && !ch ? (
          <button type="button" onClick={() => void challenge()} disabled={busy}>
            Request challenge
          </button>
        ) : null}
      </div>
      {code ? <p className="hint">{EXPLAIN_HELP[code]}</p> : null}
      {ch && !grant ? (
        <StepUpForm ch={ch} sessionUsername={session.username} inputId={`${id}-stepup-user`} disabled={busy} onGranted={setGrant} onError={setErr} />
      ) : null}
      {grant ? (
        <>
          <StepUpWindow leftMs={left} />
          <button
            type="button"
            className="primary"
            onClick={() => {
              const t = grant.token;
              setGrant(null);
              void send(t);
            }}
            disabled={busy || left <= 0}
          >
            Record {code} with step-up
          </button>
        </>
      ) : null}
      {err ? (
        <p role="alert" className="err">
          {err}
        </p>
      ) : null}
    </div>
  );
}

function ParallelRun() {
  const { session, client } = useAuth();
  const [period, setPeriod] = useState("");
  const { data, error, reload } = useLoad(session ? () => client.parallelRun(period || undefined) : null, [session?.token, period]);
  const { data: all } = useLoad(session ? () => client.parallelRun() : null, [session?.token]);
  const [open, setOpen] = useState<number | null>(null);
  const periods = useMemo(() => [...new Set((all ?? []).map((r) => r.period))].sort(), [all]);
  const groups = useMemo(() => {
    const m = new Map<string, ParallelRunRow[]>();
    for (const r of data ?? []) m.set(`${r.period} · as of ${r.as_of}`, [...(m.get(`${r.period} · as of ${r.as_of}`) ?? []), r]);
    return [...m.entries()];
  }, [data]);
  const unexplained = (data ?? []).filter((r) => r.status === "BREAK" && !r.explain_code).length;
  // A break can be re-explained; the latest code counts.
  return (
    <section className="panel" aria-labelledby="pr-h">
      <h2 id="pr-h">
        Parallel run: legacy vs new{" "}
        <span className="sub" aria-live="polite">
          {data ? `${data.length} comparison(s) · ${unexplained} unexplained break(s)` : "loading…"}
        </span>
      </h2>
      <p className="hint">
        Go/no-go evidence for cutover. It never holds a unit. A break counts as evidence only once a named person records an explain code.
      </p>
      <div className="row" style={{ marginBottom: 8 }}>
        <label htmlFor="pr-period" style={{ margin: 0 }}>
          Period
        </label>
        <select id="pr-period" value={period} onChange={(e) => setPeriod(e.target.value)}>
          <option value="">all</option>
          {periods.map((p) => (
            <option key={p}>{p}</option>
          ))}
        </select>
      </div>
      <ErrorNote error={error} what="the parallel run" />
      {groups.map(([g, rows]) => (
        <div key={g} className="tablewrap" style={{ marginBottom: 12 }}>
          <table>
            <caption style={{ textAlign: "left", fontWeight: 650, padding: "4px 0" }}>{g}</caption>
            <thead>
              <tr>
                <th scope="col">Relation</th>
                <th scope="col">Measure</th>
                <th scope="col" className="num">
                  Legacy
                </th>
                <th scope="col" className="num">
                  New
                </th>
                <th scope="col" className="num">
                  Delta
                </th>
                <th scope="col" className="num">
                  Tolerance
                </th>
                <th scope="col">Status</th>
                <th scope="col">Explain code</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <Fragment key={r.row_id}>
                  <tr id={`pr-${r.row_id}`} className={r.status === "BREAK" && !r.explain_code ? "rowbad" : undefined}>
                    <td className="mono">{r.relation}</td>
                    <td className="mono">{r.measure}</td>
                    <td className="num">{fmtNum(r.legacy)}</td>
                    <td className="num">{fmtNum(r.new)}</td>
                    <td className="num">{fmtNum(r.delta)}</td>
                    <td className="num">{fmtNum(r.tolerance)}</td>
                    <td>
                      <ToneChip tone={statusTone(r.status)} label={r.status} />
                    </td>
                    <td>
                      {r.status === "BREAK" ? (
                        <>
                          {r.explain_code ? <span className="mono">{r.explain_code} </span> : null}
                          <button
                            type="button"
                            onClick={() => setOpen(open === r.row_id ? null : r.row_id)}
                            aria-expanded={open === r.row_id}
                            aria-label={`${r.explain_code ? "Re-explain" : "Explain"} break ${r.row_id}: ${r.relation} ${r.measure}`}
                          >
                            {r.explain_code ? "Re-explain…" : "Explain…"}
                          </button>
                        </>
                      ) : r.explain_code ? (
                        <span className="mono">{r.explain_code}</span>
                      ) : (
                        <span className="muted">—</span>
                      )}
                    </td>
                  </tr>
                  {r.status === "BREAK" || open === r.row_id ? (
                    <tr>
                      <td colSpan={8} style={{ paddingTop: 0 }}>
                        <Explanation e={r.explanation} label={`Break ${r.row_id} explanation`} compact />
                        {open === r.row_id ? (
                          <ExplainBreak
                            row={r}
                            onDone={() => {
                              setOpen(null);
                              void reload();
                            }}
                          />
                        ) : null}
                      </td>
                    </tr>
                  ) : null}
                </Fragment>
              ))}
            </tbody>
          </table>
        </div>
      ))}
    </section>
  );
}

function FeedRow({ it, onDone }: { it: FeedItem; onDone: () => void }) {
  const { client } = useAuth();
  const ackCodes = feedAckCodesFor(it.kind);
  const [code, setCode] = useState<string>(ackCodes[0] ?? "");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const kindInfo = feedKindInfo(it.kind);
  async function ack() {
    setBusy(true);
    setErr(null);
    try {
      await client.feedAck(it.item_id, code);
      onDone();
    } catch (e) {
      setErr(`Refused: ${(e instanceof ApiError ? e : new ApiError(0, { detail: String(e) })).message}`);
    } finally {
      setBusy(false);
    }
  }
  return (
    <>
      <tr id={`feed-${it.item_id}`}>
        <td className="mono">{it.item_id}</td>
        <td>
          <ToneChip tone={kindInfo.tone} label={kindInfo.label} title={kindInfo.what} />
          <div className="hint">{kindInfo.what}</div>
        </td>
        <td>
          <span className="mono">{it.feed}</span>
          {it.submitter_id ? <div className="hint mono">{it.submitter_id}</div> : null}
        </td>
        <td>
          {it.period ?? "—"}
          {it.expected_by ? <div className="hint">expected by {fmtTime(it.expected_by)}</div> : null}
        </td>
        <td>
          <ToneChip tone={statusTone(it.status)} label={it.status} />
          {it.reason_code ? <div className="hint mono">{it.reason_code}</div> : null}
        </td>
        <td>
          {it.status === "OPEN" ? (
            <div className="row" style={{ gap: 6 }}>
              <label htmlFor={`ack-${it.item_id}`} className="skip">
                Reason code
              </label>
              <select id={`ack-${it.item_id}`} value={code} onChange={(e) => setCode(e.target.value)} disabled={busy}>
                {ackCodes.map((c) => (
                  <option key={c}>{c}</option>
                ))}
              </select>
              <button type="button" onClick={() => void ack()} disabled={busy} aria-label={`Acknowledge feed item ${it.item_id}`}>
                Acknowledge
              </button>
            </div>
          ) : (
            <span className="muted">acknowledged{it.acked_by ? ` by ${it.acked_by}` : ""}</span>
          )}
          {err ? (
            <p role="alert" className="err">
              {err}
            </p>
          ) : null}
        </td>
      </tr>
      <tr>
        <td />
        <td colSpan={5} style={{ paddingTop: 0 }}>
          <Explanation e={it.explanation} label={`Feed item ${it.item_id} explanation`} compact />
        </td>
      </tr>
    </>
  );
}

/** Small, consistent context chips: which deployment and IdP a reviewer is looking at the feed under. One binding
 * for the whole control plane (dq_ctl.deployment_binding has exactly one row), so this is a page-level pair of
 * chips, not a per-row field no backend data supports (CLAUDE.md: never show more than the data carries).
 *
 * After-build red team Finding 1 (2026-10-29): the first build read this off a `tenant.x_deployment` field that
 * was never real -- `/api/v1/tenant` (app.py) has no such key, only `/api/v1/summary` does, under the plain key
 * `deployment`. That mismatch was silent (an unchecked cast plus a mock fixture that matched the wrong client
 * expectation instead of the real server), so this now reads off the shell's own already-fetched, already-polled
 * summary (useShellData, the same data Header.tsx's GenesisChip/SimClock use) rather than a second, broken fetch. */
function DeploymentChips() {
  const { summary } = useShellData();
  if (summary === null) return null; // summary not loaded yet
  const d = summary.deployment;
  if (!d) return <ToneChip tone="neutral" label="deployment: not reported" title="This server reports no deployment binding." />;
  return (
    <span className="row" style={{ gap: 6, display: "inline-flex" }}>
      <ToneChip tone={d.bound ? "info" : "warn"} label={`deployment: ${d.label}`} title="dq_ctl.deployment_binding, as derived by the server (never the runtime's own claim)" />
      <ToneChip tone="neutral" label={`idp: ${d.idp_kind ?? "not bound"}`} title="The IdP kind bound alongside the deployment" />
    </span>
  );
}

function FeedItems() {
  const { session, client } = useAuth();
  const { data, error, reload } = useLoad(session ? client.feedItems : null, [session?.token], 60_000);
  return (
    <section className="panel" aria-labelledby="fi-h">
      <h2 id="fi-h">
        Feed items <span className="sub">{data ? `${data.filter((i) => i.status === "OPEN").length} open` : "loading…"}</span>
      </h2>
      <p className="hint">
        An expected feed that arrives empty, or not at all within its cadence, opens an item here, as does an inbound file no route matches
        (UNROUTED_FILE, which never holds a batch) or a recovery whose frozen loads are gone (RECOVERY_DATA_LOST, which leaves the batch held
        pending recovery). A person acknowledges each with a reason code of its own kind.
      </p>
      <p className="hint">
        Raised under <DeploymentChips />
      </p>
      <ErrorNote error={error} what="feed items" />
      <div className="tablewrap">
        <table>
          <caption className="skip">Empty and missing expected deliveries, unrouted files and lost-recovery data</caption>
          <thead>
            <tr>
              <th scope="col">Item</th>
              <th scope="col">Kind</th>
              <th scope="col">Feed / submitter</th>
              <th scope="col">Period</th>
              <th scope="col">Status</th>
              <th scope="col">Acknowledge</th>
            </tr>
          </thead>
          <tbody>
            {(data ?? []).map((it) => (
              <FeedRow key={it.item_id} it={it} onDone={() => void reload()} />
            ))}
          </tbody>
        </table>
      </div>
      {data && !data.length ? <p className="muted">No feed items.</p> : null}
    </section>
  );
}

export default function Page() {
  return (
    <>
      <PageHeader title="Reconciliation" sub="Parallel run and feed items" />
      <PageBody>
      <Gate>
        <ParallelRun />
        <FeedItems />
      </Gate>
      </PageBody>
    </>
  );
}
