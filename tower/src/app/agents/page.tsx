"use client";
import Link from "next/link";
import { useMemo, useState } from "react";
import { ApiError } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useLoad } from "@/lib/useLoad";
import { Gate } from "@/components/SignIn";
import { ErrorNote } from "@/components/ErrorNote";
import { ToneChip } from "@/components/Chip";
import { Explanation } from "@/components/Explanation";
import { TierBadge } from "@/components/Autonomy";
import { statusTone, TIER_INFO, tierRank, TIERS, evidenceLabel, fmtCheckValue, recordChecks, recordSummary } from "@/lib/partE";
import { orgLabel, useTenant } from "@/lib/tenant";
import { fmtTime, shortHash } from "@/lib/format";
import type { AgentRow, AutonomyRow, EvalRun, Tier } from "@/lib/types";
import { PageBody, PageHeader } from "@/components/layout/PageHeader";

const RATIONALE_MIN = 10;
const rowKey = (r: AutonomyRow) => `${r.agent_id}|${r.action_class}|${r.authority}`;

function daysLeft(iso: string | null): string {
  if (!iso) return "no expiry";
  const d = Math.floor((Date.parse(iso) - Date.now()) / 86_400_000);
  return d < 0 ? `expired ${-d} day(s) ago` : `${d} day(s) left`;
}

function Registry({ agents }: { agents: AgentRow[] }) {
  return (
    <section className="panel" aria-labelledby="reg-h">
      <h2 id="reg-h">
        Agent registry <span className="sub">governed AGENT artifacts; a seat whose agent is not ACTIVE is refused</span>
      </h2>
      <div className="tablewrap">
        <table>
          <caption className="skip">Registered agents</caption>
          <thead>
            <tr>
              <th scope="col">Agent</th>
              <th scope="col">Status</th>
              <th scope="col">Version</th>
              <th scope="col">Seats</th>
              <th scope="col">Approved by</th>
            </tr>
          </thead>
          <tbody>
            {agents.map((a) => (
              <tr key={a.agent_id}>
                <td>
                  <span className="mono">{a.agent_id}</span>
                  <div className="hint">{a.name}</div>
                </td>
                <td>
                  <ToneChip tone={statusTone(a.status)} label={a.status} title={`Agent status ${a.status}`} />
                  {a.status === "REGISTERED" ? <div className="hint">next phase: registered, not entitled, cannot act</div> : null}
                </td>
                <td className="mono">{a.version}</td>
                <td className="mono">{a.seats.length ? a.seats.join(", ") : "—"}</td>
                <td>{a.cr_id ? <Link className="mono" href={`/governance/${encodeURIComponent(a.cr_id)}`}>{a.cr_id}</Link> : <span className="muted">—</span>}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function Entitlements({ agents }: { agents: AgentRow[] }) {
  return (
    <section className="panel" aria-labelledby="ent-h">
      <h2 id="ent-h">
        Entitlements <span className="sub">expiry lowers the ceiling to H_IN; it never stops human-approved releases</span>
      </h2>
      <div className="tablewrap">
        <table>
          <caption className="skip">Entitlements per agent and action class, with expiry and live state</caption>
          <thead>
            <tr>
              <th scope="col">Agent</th>
              <th scope="col">Action class</th>
              <th scope="col">Authorities</th>
              <th scope="col">Not before</th>
              <th scope="col">Not after</th>
              <th scope="col">Live now</th>
            </tr>
          </thead>
          <tbody>
            {agents.flatMap((a) =>
              a.entitlements.length
                ? a.entitlements.map((e) => (
                    <tr key={`${a.agent_id}|${e.action_class}`}>
                      <td className="mono">{a.agent_id}</td>
                      <td className="mono">{e.action_class}</td>
                      <td>{e.authorities.join(", ")}</td>
                      <td className="hint">{e.not_before ? fmtTime(e.not_before) : "—"}</td>
                      <td>
                        {e.not_after ? fmtTime(e.not_after) : "—"}
                        <div className="hint">{daysLeft(e.not_after)}</div>
                      </td>
                      <td>
                        {e.live ? <ToneChip tone="ok" label="live" /> : <ToneChip tone="bad" label="not live" title="Outside not_before / not_after" />}
                      </td>
                    </tr>
                  ))
                : [
                    <tr key={`${a.agent_id}|none`}>
                      <td className="mono">{a.agent_id}</td>
                      <td colSpan={5} className="muted">
                        No entitlements: this agent cannot post, pin memory or release.
                      </td>
                    </tr>,
                  ],
            )}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function Ladder({ tier }: { tier: Tier }) {
  return (
    <span className="tier-step" aria-hidden="true">
      {TIERS.map((t) => (
        <span key={t} className={tierRank(t) <= tierRank(tier) ? "on" : undefined}>
          {t}
        </span>
      ))}
    </span>
  );
}

function Tiers({ rows, sel, setSel }: { rows: AutonomyRow[]; sel: string; setSel: (k: string) => void }) {
  return (
    <section className="panel" aria-labelledby="tier-h">
      <h2 id="tier-h">
        Autonomy tiers <span className="sub">a ceiling per agent × action class × authority; floors can still raise any tier to H_IN_2</span>
      </h2>
      <p className="hint">
        {TIERS.slice()
          .reverse()
          .map((t) => `${t} ${TIER_INFO[t].name} (ceiling ${TIER_INFO[t].ceiling}: ${TIER_INFO[t].card})`)
          .join(" · ")}
        . A missing entry means L1, never L4. Promotion is never automatic.
      </p>
      <div className="tablewrap">
        <table className="ladder">
          <caption className="skip">Tier ladder L1 to L4 per action class and authority; select a row to see its track record</caption>
          <thead>
            <tr>
              <th scope="col">Agent / action class</th>
              <th scope="col">Authority</th>
              <th scope="col">Ladder</th>
              <th scope="col">Tier</th>
              <th scope="col">Ceiling mode</th>
              <th scope="col">Set by</th>
              <th scope="col">Track record</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => {
              const k = rowKey(r);
              return (
                <tr key={k} className={sel === k ? "selected" : undefined}>
                  <td>
                    <span className="mono">{r.agent_id}</span>
                    <div className="mono hint">{r.action_class}</div>
                  </td>
                  <td>{r.authority}</td>
                  <td>
                    <Ladder tier={r.tier} />
                  </td>
                  <td>
                    <TierBadge tier={r.tier} />
                  </td>
                  <td className="mono">{r.ceiling_mode}</td>
                  <td>{r.cr_id ? <Link className="mono" href={`/governance/${encodeURIComponent(r.cr_id)}`}>{r.cr_id}</Link> : <span className="muted">—</span>}</td>
                  <td>
                    <button type="button" aria-pressed={sel === k} onClick={() => setSel(k)} aria-label={`Show track record for ${r.agent_id} ${r.action_class} ${r.authority}`}>
                      Track record
                    </button>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function TrackRecordPanel({ r }: { r: AutonomyRow }) {
  const t = r.track_record;
  const p = t.prior ?? null;
  const cells: [string, number, number | null, string][] = [
    ["Autonomous releases", t.autonomous, p?.autonomous ?? null, "released at mode A"],
    ["Released after veto window", t.h_on_released, p?.h_on_released ?? null, "L3, no veto by the deadline"],
    ["Vetoes", t.vetoes, p?.vetoes ?? null, "a person stopped an L3 release"],
    ["Human overrides", t.overrides, p?.overrides ?? null, "human release over a FAIL"],
    ["DENY on released units", t.denies, p?.denies ?? null, "released, then denied"],
    ["Look-back withdrawn", t.lookback_withdrawn, p?.lookback_withdrawn ?? null, "a released unit later withdrawn"],
  ];
  // K0 / B7: the demo moment's two display strings, read live from this row's own evidence/minimums (never
  // hardcoded) -- the next tier up is the one a promotion from here would need to clear.
  const nextTier = TIERS[tierRank(r.tier) + 1] ?? null;
  const ev = evidenceLabel(r.evidence);
  const checks = nextTier ? recordChecks(t.current_version, r.minimums?.[nextTier as "L2" | "L3" | "L4"]) : [];
  const rec = recordSummary(checks);
  return (
    <section className="panel" aria-labelledby="tr-h">
      <h2 id="tr-h">
        Track record{" "}
        <span className="sub mono">
          {r.agent_id} · {r.action_class} · {r.authority}
        </span>
      </h2>
      <Explanation e={r.explanation} label="Tier explanation" compact />
      <p className="hint">
        Counting since {t.since ? fmtTime(t.since) : "the start"} (the current tier began then; R4-5: nothing resets the count itself) · evidence hash{" "}
        <code title={r.track_record_hash}>{shortHash(r.track_record_hash, 12)}</code>
      </p>
      {nextTier ? (
        <p className="banner t-info" role="note" style={{ marginBottom: 8 }}>
          <strong>Towards {nextTier}:</strong>{" "}
          <ToneChip tone={ev.ok ? "ok" : ev.ok === false ? "bad" : "neutral"} label={ev.text} title="dq_ctl.evidence_status() for the code version this control plane is running now" />{" "}
          <ToneChip tone={rec.ok ? "ok" : "bad"} label={rec.headline} title={`${r.minimums?.label ?? "governed minimums"}, checked on the running code version's own count (R4-5: a new code version starts over)`} />
          {rec.rest.length ? (
            <span className="hint">
              {" "}
              · also required: {rec.rest.map((c) => `${c.label} ${fmtCheckValue(c, c.have)}/${fmtCheckValue(c, c.need)} ${c.ok ? "✓" : "✗"}`).join(", ")}
            </span>
          ) : null}
        </p>
      ) : null}
      <div className="tablewrap">
        <table>
          <caption className="skip">Track record now, and before the last reset</caption>
          <thead>
            <tr>
              <th scope="col">Count</th>
              <th scope="col" className="num">
                Now
              </th>
              <th scope="col" className="num" title={p?.since ? `${fmtTime(p.since)} to ${p.until ? fmtTime(p.until) : "the reset"}` : "No earlier period"}>
                Before reset
              </th>
              <th scope="col">Meaning</th>
            </tr>
          </thead>
          <tbody>
            {cells.map(([k, v, pv, h]) => (
              <tr key={k}>
                <td>{k}</td>
                <td className="num mono">{v}</td>
                <td className="num mono">{pv === null ? "—" : pv}</td>
                <td className="hint">{h}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {p?.since ? (
        <p className="hint">
          &ldquo;Before reset&rdquo; (API-compatibility name) covers {fmtTime(p.since)} to {p.until ? fmtTime(p.until) : "now"}: every earlier code
          version&rsquo;s total. R4-5: the record itself is continuous and segmented by code version; nothing is cleared.
        </p>
      ) : null}
      <p className="hint" style={{ marginTop: 8 }}>
        Latest confirmed eval attestation (evidence for its code version; it never resets this track record):{" "}
        {t.eval_attestation ? (
          <>
            <ToneChip tone="ok" label="CONFIRMED" title="Two governance persons: proposer and confirmer" />{" "}
            <span className="mono">{t.eval_attestation.label}</span> · code <code>{shortHash(t.eval_attestation.code_sha256, 12)}</code> · proposed by{" "}
            <span className="mono">{t.eval_attestation.proposed_by ?? "?"}</span>, confirmed by{" "}
            <span className="mono">{t.eval_attestation.confirmed_by ?? "?"}</span>
            {t.eval_attestation.at ? ` ${fmtTime(t.eval_attestation.at)}` : ""} ·{" "}
            {t.eval_attestation.failures === 0 ? (
              <ToneChip tone="ok" label="0 failures" />
            ) : (
              <ToneChip tone="bad" label={`${t.eval_attestation.failures} failure(s)`} />
            )}
            {t.eval_attestation.halves ? (
              <>
                {" "}
                <ToneChip tone="ok" label="deterministic ✓" />{" "}
                {t.eval_attestation.halves.llm ? <ToneChip tone="ok" label="live LLM ✓" /> : <ToneChip tone="neutral" label="live LLM: not in this bundle" />}
              </>
            ) : null}
          </>
        ) : (
          <span className="muted">none confirmed</span>
        )}
      </p>
      {r.demotions?.length ? (
        <>
          <h3>Automatic demotions</h3>
          <ol className="timeline">
            {r.demotions.map((d, i) => (
              <li key={i}>
                <strong>
                  {d.from_tier} → {d.to_tier}
                </strong>{" "}
                <span className="mono">{d.reason_code}</span> {d.ref ? <span className="mono hint">{d.ref}</span> : null}{" "}
                <span className="hint">{fmtTime(d.at)}</span>
                <Explanation e={d.explanation} label="Demotion explanation" compact />
              </li>
            ))}
          </ol>
        </>
      ) : null}
    </section>
  );
}

function ProposeTier({ rows, sel }: { rows: AutonomyRow[]; sel: AutonomyRow | null }) {
  const { session, client } = useAuth();
  const { tenant } = useTenant();
  const { data: rules } = useLoad(session ? client.rules : null, [session?.token]);
  const [to, setTo] = useState<Tier | "">("");
  const [why, setWhy] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; text: string; cr?: string } | null>(null);
  if (!sel) return null;
  const target = to || sel.tier;
  const promote = tierRank(target) > tierRank(sel.tier);
  const same = target === sel.tier;
  const ao = tenant?.approver_org ?? null;
  const state = rules?.state ?? null;

  async function submit() {
    if (!sel || !state || same) return;
    setBusy(true);
    setMsg(null);
    // AUTONOMY content is the whole {authority: tier} map for this agent and action class.
    const content: Record<string, Tier> = {};
    for (const r of rows) if (r.agent_id === sel.agent_id && r.action_class === sel.action_class) content[r.authority] = r.tier;
    content[sel.authority] = target;
    try {
      const r = await client.crPropose({
        state,
        rationale: `${promote ? "Promote" : "Demote"} ${sel.agent_id} ${sel.action_class} for ${sel.authority}: ${sel.tier} -> ${target}. Track record ${sel.track_record_hash.slice(0, 12)}: ${why.trim()}`,
        items: [{ artifact_id: `${state}:autonomy:${sel.agent_id}:${sel.action_class}`, kind: "AUTONOMY", op: "PUT", content }],
      });
      setMsg({ ok: true, text: `Drafted ${r.cr_id}: needs ${r.approvals_required} approval(s).`, cr: r.cr_id });
    } catch (e) {
      setMsg({ ok: false, text: `Refused: ${(e instanceof ApiError ? e : new ApiError(0, { detail: String(e) })).message}` });
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="panel" aria-labelledby="pt-h">
      <h2 id="pt-h">Propose promotion or demotion</h2>
      <p className="hint">
        Creates an AUTONOMY change request. The server snapshots this track record into the request&rsquo;s impact, so approvers sign the evidence
        shown here. Automatic demotion needs no request; promotion is never automatic.
      </p>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          void submit();
        }}
      >
        <div className="filters" style={{ marginBottom: 8 }}>
          <div>
            <span className="hint">For</span>
            <span className="mono">
              {sel.agent_id} · {sel.action_class} · {sel.authority}
            </span>
          </div>
          <div>
            <span className="hint">Now</span>
            <TierBadge tier={sel.tier} />
          </div>
          <div>
            <label htmlFor="pt-to">New tier</label>
            <select id="pt-to" value={target} onChange={(e) => setTo(e.target.value as Tier)} disabled={!!msg?.ok}>
              {TIERS.map((t) => (
                <option key={t} value={t}>
                  {t} {TIER_INFO[t].name}
                </option>
              ))}
            </select>
          </div>
        </div>
        {same ? (
          <p className="hint">Choose a different tier.</p>
        ) : promote ? (
          <p className="banner t-warn" role="note">
            <strong>Promotion: needs two {ao ? orgLabel(tenant, ao) : "State"} approvers.</strong> Raising a tier is two-person, enforced by the
            database.
          </p>
        ) : (
          <p className="banner t-info" role="note">
            Demotion is lower-only, so one approver from {ao ? orgLabel(tenant, ao) : "the approving organisation"} is enough.
          </p>
        )}
        <label htmlFor="pt-why">Rationale (at least {RATIONALE_MIN} characters)</label>
        <textarea id="pt-why" value={why} onChange={(e) => setWhy(e.target.value)} disabled={!!msg?.ok} spellCheck />
        <div className="row" style={{ marginTop: 6 }}>
          <button type="submit" className="primary" disabled={busy || same || !state || why.trim().length < RATIONALE_MIN || !!msg?.ok}>
            {busy ? "Drafting…" : promote ? "Propose promotion" : "Propose demotion"}
          </button>
        </div>
      </form>
      {msg ? (
        <p role={msg.ok ? "status" : "alert"} className={msg.ok ? "hint" : "err"}>
          {msg.text}{" "}
          {msg.cr ? (
            <Link className="mono" href={`/governance/${encodeURIComponent(msg.cr)}`}>
              Open {msg.cr}
            </Link>
          ) : null}
        </p>
      ) : null}
    </section>
  );
}

function runLabel(r: EvalRun): string {
  return `${r.run_id} · ${r.suite} · ${r.source_mode} · ${r.passed}/${r.cases} passed · ${shortHash(r.code_sha256, 10)} · ${fmtTime(r.at)}`;
}

/**
 * Eval attestations are two-person (E4 / R4-5): a governance person proposes {label, code_sha256, det_run_id,
 * llm_run_id?} naming RECORDED `dq_ctl.eval_runs` (the database copies their tests/failures/junit hash); a SECOND
 * governance person (from the approver organisation) confirms. A confirmed attestation is evidence for its code
 * version; per DEC-024/R4-5 nothing resets -- the track record is continuous and segmented by code version.
 *
 * E-13 (GET /eval-attestations?status=PROPOSED, the pending-attestations list) is explicitly DEFERRED to K11 by
 * user decision: the API lists no pending attestations, so the confirmer still enters the id the proposer reports.
 * This is the intended K0 mechanism, not a shortcut -- do not build a picker for it here.
 */
function Attestations() {
  const { client, session } = useAuth();
  const gov = !!session?.me.duties.some((d) => d.startsWith("governance:"));
  const { data: runs, error: runsErr } = useLoad(gov ? () => client.listEvals() : null, [session?.token, gov]);
  const [label, setLabel] = useState("");
  const [detRunId, setDetRunId] = useState("");
  const [llmRunId, setLlmRunId] = useState("");
  const [cid, setCid] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  if (!gov) return null;
  const detRuns = (runs ?? []).filter((r) => r.suite === "agent_eval" && r.source_mode === "DETERMINISTIC");
  const llmRuns = (runs ?? []).filter((r) => r.suite === "explainer" && r.source_mode === "LIVE");
  const det = detRuns.find((r) => r.run_id === detRunId) ?? null;
  const ready = label.trim().length > 0 && !!det;
  async function run(fn: () => Promise<string>) {
    setBusy(true);
    setMsg(null);
    try {
      setMsg({ ok: true, text: await fn() });
    } catch (e) {
      // The demo moment's refusal beat: show the server's own message verbatim, not a generic failure.
      setMsg({ ok: false, text: `Refused: ${(e instanceof ApiError ? e : new ApiError(0, { detail: String(e) })).message}` });
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className="panel" aria-labelledby="ea-h">
      <h2 id="ea-h">
        Eval attestations <span className="sub">two-person: a confirmed attestation is evidence for its code version; nothing resets</span>
      </h2>
      <ErrorNote error={runsErr} what="recorded eval runs" />
      <form
        className="filters"
        style={{ marginBottom: 10 }}
        onSubmit={(e) => {
          e.preventDefault();
          void run(async () => {
            const r = await client.evalAttest({ label: label.trim(), code_sha256: det!.code_sha256, det_run_id: det!.run_id, ...(llmRunId ? { llm_run_id: llmRunId } : {}) });
            setCid(String(r.id));
            return `Attestation ${r.id} ${r.status} (halves: deterministic ${r.halves?.deterministic ? "✓" : "✗"}, live LLM ${r.halves?.llm ? "✓" : "✗"}). A second governance person must confirm it (id ${r.id}).`;
          });
        }}
      >
        <div style={{ minWidth: 160 }}>
          <label htmlFor="ea-label">Label</label>
          <input id="ea-label" type="text" value={label} onChange={(e) => setLabel(e.target.value)} autoComplete="off" />
        </div>
        <div style={{ minWidth: 340 }}>
          <label htmlFor="ea-det">Deterministic agent_eval run (required)</label>
          <select id="ea-det" value={detRunId} onChange={(e) => setDetRunId(e.target.value)}>
            <option value="">— choose a recorded run —</option>
            {detRuns.map((r) => (
              <option key={r.run_id} value={r.run_id}>
                {runLabel(r)}
              </option>
            ))}
          </select>
          {runs && detRuns.length === 0 ? <p className="hint">No DETERMINISTIC agent_eval run is recorded yet; run `dqagent eval run --suite agent_eval` first.</p> : null}
        </div>
        <div style={{ minWidth: 340 }}>
          <label htmlFor="ea-llm">Live explainer run (optional: the LLM half)</label>
          <select id="ea-llm" value={llmRunId} onChange={(e) => setLlmRunId(e.target.value)}>
            <option value="">— none (deterministic half only) —</option>
            {llmRuns.map((r) => (
              <option key={r.run_id} value={r.run_id}>
                {runLabel(r)}
              </option>
            ))}
          </select>
        </div>
        <button type="submit" disabled={busy || !ready}>
          Propose attestation
        </button>
      </form>
      <form
        className="filters"
        onSubmit={(e) => {
          e.preventDefault();
          void run(async () => {
            const r = await client.evalConfirm(Number(cid));
            return `Attestation ${r.id} ${r.status} by ${r.confirmed_by ?? "you"} (proposed by ${r.person_id}). Evidence for code ${shortHash(r.code_sha256, 12)}; the track record itself is not reset.`;
          });
        }}
      >
        <div>
          <label htmlFor="ea-confirm">Attestation id to confirm</label>
          <input id="ea-confirm" type="text" inputMode="numeric" value={cid} onChange={(e) => setCid(e.target.value)} autoComplete="off" />
        </div>
        <button type="submit" className="primary" disabled={busy || !/^\d+$/.test(cid)}>
          Confirm as second person
        </button>
        {/* The API lists no pending attestations (E-13, deferred to K11): the confirmer enters the id the proposer reports. */}
        <span className="hint">The proposer cannot confirm their own attestation; the server refuses it. Ask the proposer for the attestation id above.</span>
      </form>
      {msg ? (
        <p role={msg.ok ? "status" : "alert"} className={msg.ok ? "hint" : "err"}>
          {msg.text}
        </p>
      ) : null}
    </section>
  );
}

function Agents() {
  const { session, client } = useAuth();
  const { data: agents, error: aErr } = useLoad(session ? client.agents : null, [session?.token]);
  const { data: auto, error: tErr } = useLoad(session ? client.autonomy : null, [session?.token], 30_000);
  const [sel, setSel] = useState("");
  const rows = useMemo(() => auto ?? [], [auto]);
  const selected = rows.find((r) => rowKey(r) === sel) ?? rows[0] ?? null;
  return (
    <>
      <ErrorNote error={aErr} what="the agent registry" />
      <ErrorNote error={tErr} what="autonomy tiers" />
      {agents ? <Registry agents={agents} /> : <p className="muted">Loading…</p>}
      {agents ? <Entitlements agents={agents} /> : null}
      {auto ? <Tiers rows={rows} sel={selected ? rowKey(selected) : ""} setSel={setSel} /> : null}
      {selected ? (
        <div className="split">
          <TrackRecordPanel r={selected} />
          <ProposeTier key={rowKey(selected)} rows={rows} sel={selected} />
        </div>
      ) : null}
      <Attestations />
    </>
  );
}

export default function Page() {
  return (
    <>
      <PageHeader title="Agents" />
      <PageBody>
      <Gate>
        <Agents />
      </Gate>
      </PageBody>
    </>
  );
}
