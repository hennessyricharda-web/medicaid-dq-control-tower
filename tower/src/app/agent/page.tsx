"use client";
import { useMemo, useState } from "react";
import { ApiError } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useLoad } from "@/lib/useLoad";
import { Gate } from "@/components/SignIn";
import { ErrorNote } from "@/components/ErrorNote";
import { ToneChip } from "@/components/Chip";
import { VerdictFlow, type VerdictOutcome } from "@/components/VerdictFlow";
import { memoryStatusTone, REASON_CODES } from "@/lib/governance";
import { fmtNum, fmtTime, shortHash } from "@/lib/format";
import type { Tone } from "@/lib/dispositions";
import type { BoardEntry, MemoryRow } from "@/lib/types";
import { PageBody, PageHeader } from "@/components/layout/PageHeader";

const SEAT_TONE: Record<string, Tone> = { STEWARD: "info", TRIAGE: "warn", EVALUATOR: "neutral", INTAKE: "neutral", PUBLISHER: "ok", VERIFIER: "ok" };
const KIND_TONE: Record<string, Tone> = { FINDING: "warn", HYPOTHESIS: "info", DECISION_REF: "ok", NOTE: "neutral", MEMORY_REF: "info" };

function State({ onPick }: { onPick: (b: string) => void }) {
  const { session, client } = useAuth();
  const { data, error } = useLoad(session ? client.agentState : null, [session?.token], 30_000);
  const byBatch = useMemo(() => {
    const m = new Map<string, NonNullable<typeof data>["batches"]>();
    for (const r of data?.batches ?? []) m.set(r.batch_id, [...(m.get(r.batch_id) ?? []), r]);
    return [...m.entries()];
  }, [data]);
  return (
    <section className="panel" aria-labelledby="st-h">
      <h2 id="st-h">
        Agent state per batch <span className="sub">{data ? `LLM provider ${data.llm_provider} · ${byBatch.length} batch(es)` : ""}</span>
      </h2>
      <ErrorNote error={error} what="agent state" />
      <div className="tablewrap">
        <table>
          <caption className="skip">Units by state per batch and node, with blackboard entries and hypotheses</caption>
          <thead>
            <tr>
              <th scope="col">Batch</th>
              <th scope="col">Node</th>
              {["units", "released", "held", "pending", "in_flight", "board_entries", "hypotheses"].map((k) => (
                <th key={k} scope="col" className="num">
                  {k.replace("_", " ")}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {byBatch.flatMap(([b, rows]) =>
              rows.map((r, i) => (
                <tr key={`${b}|${r.node}`} className={r.held > 0 ? "rowbad" : undefined}>
                  {i === 0 ? (
                    <th scope="rowgroup" rowSpan={rows.length} style={{ textTransform: "none", fontSize: "0.9rem", color: "var(--text)", background: "var(--surface)" }}>
                      <button type="button" className="linklike mono" onClick={() => onPick(b)} aria-label={`Show blackboard for batch ${b}`}>
                        {b}
                      </button>
                    </th>
                  ) : null}
                  <td className="mono">{r.node}</td>
                  <td className="num">{fmtNum(r.units)}</td>
                  <td className="num">{fmtNum(r.released)}</td>
                  <td className="num">{r.held > 0 ? <strong>{fmtNum(r.held)}</strong> : fmtNum(r.held)}</td>
                  <td className="num">{fmtNum(r.pending)}</td>
                  <td className="num">{fmtNum(r.in_flight)}</td>
                  <td className="num">{fmtNum(r.board_entries)}</td>
                  <td className="num">{fmtNum(r.hypotheses)}</td>
                </tr>
              )),
            )}
          </tbody>
        </table>
      </div>
    </section>
  );
}

/** prev_hash must equal the entry_hash of seq-1. Checked only when seq-1 is in view; hashes are not recomputed here. */
function linkStatus(e: BoardEntry, bySeq: Map<number, BoardEntry>): { tone: Tone; label: string; title: string } {
  const prev = bySeq.get(e.seq - 1);
  if (!prev) return { tone: "neutral", label: "link n/a", title: "Previous entry not in this view" };
  return prev.entry_hash === e.prev_hash
    ? { tone: "ok", label: "linked", title: `prev_hash = entry_hash of seq ${prev.seq}` }
    : { tone: "crit", label: "CHAIN BREAK", title: `prev_hash differs from entry_hash of seq ${prev.seq}` };
}

function Blackboard({ batch, setBatch, batches }: { batch: string; setBatch: (b: string) => void; batches: string[] }) {
  const { session, client } = useAuth();
  const { data, error, loading, reload } = useLoad(session ? () => client.blackboard(batch) : null, [session?.token, batch]);
  const [seat, setSeat] = useState("");
  const bySeq = useMemo(() => new Map((data ?? []).map((e) => [e.seq, e])), [data]);
  const seats = useMemo(() => [...new Set((data ?? []).map((e) => e.seat))].sort(), [data]);
  const rows = (data ?? []).filter((e) => !seat || e.seat === seat);
  const breaks = (data ?? []).filter((e) => linkStatus(e, bySeq).tone === "crit").length;
  return (
    <section className="panel" aria-labelledby="bb-h">
      <h2 id="bb-h">
        Blackboard{" "}
        <span className="sub" aria-live="polite">
          {data ? `${rows.length} entr${rows.length === 1 ? "y" : "ies"}${batch ? ` · batch ${batch}` : ""} · ${breaks ? `${breaks} chain break(s)` : "no chain breaks in view"}` : "loading…"}
        </span>
      </h2>
      <div className="filters" style={{ marginBottom: 8 }}>
        <div>
          <label htmlFor="bb-batch">Batch</label>
          <select id="bb-batch" value={batch} onChange={(e) => setBatch(e.target.value)}>
            <option value="">all batches</option>
            {batches.map((b) => (
              <option key={b}>{b}</option>
            ))}
          </select>
        </div>
        <div>
          <label htmlFor="bb-seat">Seat</label>
          <select id="bb-seat" value={seat} onChange={(e) => setSeat(e.target.value)}>
            <option value="">all seats</option>
            {seats.map((s) => (
              <option key={s}>{s}</option>
            ))}
          </select>
        </div>
        <button type="button" onClick={() => void reload()} disabled={loading}>
          Refresh
        </button>
      </div>
      <p className="hint">
        Append-only and hash-chained. Seats write templated text only; known-issue posts are shown to their proposer only.
      </p>
      <ErrorNote error={error} what="the blackboard" />
      <ol className="timeline" aria-label="Blackboard entries, newest first">
        {rows.map((e) => {
          const ls = linkStatus(e, bySeq);
          return (
            <li key={e.seq}>
              <div className="row" style={{ gap: 6 }}>
                <span className="mono hint">#{e.seq}</span>
                <ToneChip tone={SEAT_TONE[e.seat] ?? "neutral"} label={e.seat} title={`Seat ${e.seat}`} />
                <ToneChip tone={KIND_TONE[e.kind] ?? "neutral"} label={e.kind} title={`Kind ${e.kind}`} />
                <span className="hint">
                  {e.subject_type} <span className="mono">{e.subject_id}</span> · {e.authority}
                  {e.person_id ? (
                    <>
                      {" "}
                      · <span className="mono">{e.person_id}</span>
                    </>
                  ) : null}{" "}
                  · {fmtTime(e.at)}
                </span>
              </div>
              <div style={{ margin: "2px 0" }}>{e.text}</div>
              <div className="hint">
                template <span className="mono">{e.template}</span> · entry <code title={e.entry_hash}>{shortHash(e.entry_hash, 10)}</code> ·{" "}
                <ToneChip tone={ls.tone} label={ls.label} title={ls.title} />
                {e.batch_id && !batch ? (
                  <>
                    {" "}
                    · batch <span className="mono">{e.batch_id}</span>
                  </>
                ) : null}
              </div>
            </li>
          );
        })}
      </ol>
      {data && !rows.length ? <p className="muted">No entries.</p> : null}
    </section>
  );
}

function ProposeKnownIssue({ onDone }: { onDone: () => void }) {
  const { session, client } = useAuth();
  const { data: rules } = useLoad(session ? client.rules : null, [session?.token]);
  const auths = session?.me.authorities ?? [];
  const [ruleId, setRuleId] = useState("");
  const [authority, setAuthority] = useState(auths[0] ?? "");
  const [reason, setReason] = useState<string>(REASON_CODES[0]);
  const [expires, setExpires] = useState(() => new Date(Date.now() + 30 * 86_400_000).toISOString().slice(0, 10));
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const ruleIds = useMemo(() => [...new Set((rules?.rules ?? []).map((r) => r.id))].sort(), [rules]);

  async function submit() {
    setBusy(true);
    setMsg(null);
    try {
      const r = await client.kiPropose({ rule_id: ruleId.trim(), authority, reason_code: reason, expires });
      setMsg({ ok: true, text: `Known issue ${r.memory_id} ${r.status}. A second person must approve it with a step-up.` });
      setRuleId("");
      onDone();
    } catch (e) {
      const ae = e instanceof ApiError ? e : new ApiError(0, { detail: String(e) });
      setMsg({ ok: false, text: `Refused: ${ae.message}` });
    } finally {
      setBusy(false);
    }
  }

  return (
    <form
      className="panel"
      aria-labelledby="ki-h"
      onSubmit={(e) => {
        e.preventDefault();
        void submit();
      }}
    >
      <h3 id="ki-h">Propose a known issue</h3>
      <p className="hint">
        A known issue annotates a rule for the maker&rsquo;s view. It never changes a disposition or a gate, it expires, and it is never shown to
        a checker.
      </p>
      <fieldset disabled={busy} className="filters" style={{ border: "none", padding: 0, margin: 0 }}>
        <div style={{ minWidth: 200 }}>
          <label htmlFor="ki-rule">Rule id</label>
          <input id="ki-rule" type="text" list="ki-rules" value={ruleId} onChange={(e) => setRuleId(e.target.value)} autoComplete="off" required />
          <datalist id="ki-rules">
            {ruleIds.map((r) => (
              <option key={r} value={r} />
            ))}
          </datalist>
        </div>
        <div>
          <label htmlFor="ki-auth">Authority</label>
          <select id="ki-auth" value={authority} onChange={(e) => setAuthority(e.target.value)} required>
            {auths.map((a) => (
              <option key={a}>{a}</option>
            ))}
          </select>
        </div>
        <div>
          <label htmlFor="ki-reason">Reason code</label>
          <select id="ki-reason" value={reason} onChange={(e) => setReason(e.target.value)}>
            {REASON_CODES.map((r) => (
              <option key={r}>{r}</option>
            ))}
          </select>
        </div>
        <div>
          <label htmlFor="ki-exp">Expires</label>
          <input id="ki-exp" type="date" value={expires} onChange={(e) => setExpires(e.target.value)} required />
        </div>
        <button type="submit" className="primary" disabled={!ruleId.trim() || !authority || !expires}>
          Propose
        </button>
      </fieldset>
      {msg ? (
        <p role={msg.ok ? "status" : "alert"} className={msg.ok ? "hint" : "err"}>
          {msg.text}
        </p>
      ) : null}
    </form>
  );
}

function KnownIssueRow({ m, onChanged }: { m: MemoryRow; onChanged: () => void }) {
  const { session, client } = useAuth();
  const [open, setOpen] = useState(false);
  const [outcome, setOutcome] = useState<VerdictOutcome | null>(null);
  const me = session?.me.person_id;
  const c = m.content as Record<string, unknown>;
  const mine = m.proposed_by === me;
  return (
    <tr>
      <td className="mono">{m.memory_id}</td>
      <td className="mono">{String(c.rule_id ?? m.scope_id)}</td>
      <td>{m.authority ?? "—"}</td>
      <td className="mono">{String(c.reason_code ?? "—")}</td>
      <td>{m.expires_at ? fmtTime(m.expires_at).slice(0, 10) : "—"}</td>
      <td>
        <ToneChip tone={memoryStatusTone(m.status)} label={m.status} />
      </td>
      <td>
        <span className="mono">{m.proposed_by ?? "—"}</span>
        {m.approved_by ? (
          <div className="hint">
            approved by <span className="mono">{m.approved_by}</span>
          </div>
        ) : null}
      </td>
      <td style={{ minWidth: 280 }}>
        {outcome ? <p className={`banner t-${outcome.tone}`} role="status">{outcome.title}</p> : null}
        {m.status !== "PROPOSED" ? (
          <span className="muted">—</span>
        ) : mine ? (
          <span className="hint">Awaiting a second person. You proposed it.</span>
        ) : !open ? (
          <button type="button" onClick={() => setOpen(true)} aria-expanded={false}>
            Review…
          </button>
        ) : (
          <VerdictFlow
            idPrefix={`ki-${m.memory_id}`}
            what={`known issue ${m.memory_id}`}
            reviewText="I checked the rule, the reason code and the expiry; this is an annotation for makers, not a waiver."
            challenge={(v) => client.kiChallenge(m.memory_id, v)}
            decide={(v, t) => client.kiDecide(m.memory_id, v, t)}
            onOutcome={(o) => {
              setOutcome(o);
              setOpen(false);
              onChanged();
            }}
            onStale={(why) => {
              setOutcome({ tone: "bad", title: `Refused: ${why}`, body: "" });
              setOpen(false);
              onChanged();
            }}
            onFailed={onChanged}
          />
        )}
      </td>
    </tr>
  );
}

function Memory() {
  const { session, client } = useAuth();
  const { data, error, reload } = useLoad(session ? client.memory : null, [session?.token]);
  const base = (data ?? []).filter((m) => m.kind === "BASELINE");
  const ki = (data ?? []).filter((m) => m.kind === "KNOWN_ISSUE");
  return (
    <>
      <section className="panel" aria-labelledby="mem-h">
        <h2 id="mem-h">
          Memory: baselines <span className="sub">advisory medians, pinned by hash, never gate</span>
        </h2>
        <ErrorNote error={error} what="agent memory" />
        <div className="tablewrap">
          <table>
            <caption className="skip">Baselines</caption>
            <thead>
              <tr>
                <th scope="col">Id</th>
                <th scope="col">Scope</th>
                <th scope="col">Content</th>
                <th scope="col">Hash</th>
                <th scope="col">Seat</th>
                <th scope="col">Status</th>
                <th scope="col">At</th>
              </tr>
            </thead>
            <tbody>
              {base.map((m) => (
                <tr key={m.memory_id}>
                  <td className="mono">{m.memory_id}</td>
                  <td>
                    {m.scope_type} <span className="mono">{m.scope_id}</span>
                  </td>
                  <td className="mono hint">
                    {Object.entries(m.content)
                      .map(([k, v]) => `${k}=${typeof v === "object" ? JSON.stringify(v) : String(v)}`)
                      .join(" · ")}
                  </td>
                  <td>
                    <code title={m.content_hash}>{shortHash(m.content_hash, 10)}</code>
                  </td>
                  <td>{m.created_seat}</td>
                  <td>
                    <ToneChip tone={memoryStatusTone(m.status)} label={m.status} />
                  </td>
                  <td className="hint">{fmtTime(m.at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {data && !base.length ? <p className="muted">No baselines yet: a baseline needs eligible prior periods.</p> : null}
      </section>
      <section className="panel" aria-labelledby="ki-list-h">
        <h2 id="ki-list-h">
          Memory: known issues <span className="sub">maker-only by design</span>
        </h2>
        <p className="hint">
          You see PROPOSED known issues, so a second person can review them, and APPROVED ones only if you proposed or approved them. An
          approved known issue never reaches a checker&rsquo;s screen.
        </p>
        <div className="tablewrap">
          <table>
            <caption className="skip">Known issues</caption>
            <thead>
              <tr>
                <th scope="col">Id</th>
                <th scope="col">Rule</th>
                <th scope="col">Authority</th>
                <th scope="col">Reason</th>
                <th scope="col">Expires</th>
                <th scope="col">Status</th>
                <th scope="col">Proposed by</th>
                <th scope="col">Decision</th>
              </tr>
            </thead>
            <tbody>
              {ki.map((m) => (
                <KnownIssueRow key={m.memory_id} m={m} onChanged={() => void reload()} />
              ))}
            </tbody>
          </table>
        </div>
        {data && !ki.length ? <p className="muted">No known issues visible to you.</p> : null}
      </section>
      <ProposeKnownIssue onDone={() => void reload()} />
    </>
  );
}

function Agent() {
  const { session, client } = useAuth();
  const [batch, setBatch] = useState("");
  const { data: st } = useLoad(session ? client.agentState : null, [session?.token]);
  const batches = useMemo(() => [...new Set((st?.batches ?? []).map((b) => b.batch_id))].sort(), [st]);
  return (
    <>
      <State onPick={setBatch} />
      <Blackboard batch={batch} setBatch={setBatch} batches={batches} />
      <Memory />
    </>
  );
}

export default function Page() {
  return (
    <>
      <PageHeader title="Blackboard" sub="Batch state, blackboard, memory and known issues" />
      <PageBody>
      <Gate>
        <Agent />
      </Gate>
      </PageBody>
    </>
  );
}
