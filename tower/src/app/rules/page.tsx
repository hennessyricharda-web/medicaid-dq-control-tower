"use client";
import Link from "next/link";
import { useEffect, useMemo, useRef, useState } from "react";
import { ApiError } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useLoad } from "@/lib/useLoad";
import { Gate } from "@/components/SignIn";
import { ErrorNote } from "@/components/ErrorNote";
import { ToneChip } from "@/components/Chip";
import { ruleStatusTone } from "@/lib/governance";
import { shortHash } from "@/lib/format";
import type { CoverageResponse, RuleRow } from "@/lib/types";
import { ClassChip } from "@/components/ClassChip";
import { PageBody, PageHeader } from "@/components/layout/PageHeader";

/**
 * /rules carries entity, not domain. Domain is read from /coverage: basic.matrix cells list "ID@entity:STATUS"
 * under their domain, and requirements carry a domain per req_id. Nothing is guessed: unmatched rules show "—".
 */
function domainIndex(cov: CoverageResponse | null) {
  const byRule = new Map<string, string>();
  const byReq = new Map<string, string>();
  if (!cov) return { byRule, byReq };
  for (const [dom, row] of Object.entries(cov.basic?.matrix ?? {}))
    for (const cell of Object.values(row)) for (const r of cell.rules ?? []) byRule.set(r.slice(0, r.lastIndexOf(":")), dom);
  for (const q of cov.requirements ?? []) byReq.set(q.req_id, q.domain);
  return { byRule, byReq };
}

function domainOf(r: RuleRow, idx: ReturnType<typeof domainIndex>): string {
  return idx.byRule.get(`${r.id}@${r.entity}`) ?? (r.req_ids ?? []).map((q) => idx.byReq.get(q)).find(Boolean) ?? "—";
}

const REASON_MIN = 10;

type Replacement = "raise_priority" | "tighten" | "waive";

/**
 * Drafts a REVOCATION change request for one approved rule version, together with the replacement the backend
 * requires in the same request (an overlay that changes the rule into a new version). Nothing changes until a
 * second person from the approver organisation approves.
 */
function RevokePanel({ state, rule, onClose }: { state: string; rule: RuleRow; onClose: () => void }) {
  const { client } = useAuth();
  const [reason, setReason] = useState("");
  const [verb, setVerb] = useState<Replacement>("raise_priority");
  const [priority, setPriority] = useState("Critical");
  const [max, setMax] = useState("");
  const [expires, setExpires] = useState(() => new Date(Date.now() + 30 * 86_400_000).toISOString().slice(0, 10));
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; text: string; cr?: string } | null>(null);
  const h = useRef<HTMLHeadingElement>(null);
  useEffect(() => h.current?.focus(), [rule]);
  const key = `${rule.id}@${rule.entity}`;
  const h12 = rule.effective_hash.slice(0, 12);
  const ref = `REV-${h12}`;
  const why = reason.trim();
  const maxOk = verb !== "tighten" || (max.trim() !== "" && Number.isFinite(Number(max)));

  function replacement(): Record<string, unknown> {
    const base = { verb, reason: why, approved_ref: ref };
    if (verb === "raise_priority") return { ...base, priority };
    if (verb === "tighten") return { ...base, thresholds: { max: Number(max) } };
    return { ...base, expires };
  }

  async function submit() {
    setBusy(true);
    setMsg(null);
    try {
      const r = await client.crPropose({
        state,
        rationale: `Revoke ${key} version ${h12} and replace it (${verb}): ${why}`,
        items: [
          {
            artifact_id: `${state}:revocation:${key}:${h12}`,
            kind: "REVOCATION",
            op: "PUT",
            content: { rule: `${state}:${key}`, hash: rule.effective_hash, reason: why },
          },
          { artifact_id: `${state}:overlay:${rule.id}`, kind: "OVERLAY", op: "PUT", content: replacement() },
        ],
      });
      setMsg({
        ok: true,
        text: `Drafted ${r.cr_id}${r.loosening ? " (LOOSENING)" : ""}. It needs ${r.approvals_required} approval(s) from a second person before it takes effect.`,
        cr: r.cr_id,
      });
    } catch (e) {
      const m = (e instanceof ApiError ? e : new ApiError(0, { detail: String(e) })).message;
      setMsg({
        ok: false,
        text: /REVOKED/.test(m)
          ? `Refused: ${m}. A revoked version can never be approved again; choose a replacement that compiles to a new version.`
          : `Refused: ${m}`,
      });
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="panel" aria-labelledby="rv-h" style={{ borderColor: "var(--warn-bd)", borderWidth: 2 }}>
      <h2 id="rv-h" ref={h} tabIndex={-1}>
        Revoke version <span className="sub mono">{key} · {h12}</span>
      </h2>
      <p className="hint">
        A revocation must carry its replacement in the same request, so the rule compiles to a new version and the revoked one never stays in force.
      </p>
      <p className="hint">
        Once a second person approves, releases already made under {h12} each open a <Link href="/lookback">look-back</Link> item for re-review. A
        revoked version can never be approved again.
      </p>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          void submit();
        }}
      >
        <fieldset disabled={busy || !!msg?.ok} style={{ border: "none", padding: 0, margin: 0 }}>
          <label htmlFor="rv-reason">Reason (at least {REASON_MIN} characters; used for both items)</label>
          <textarea id="rv-reason" value={reason} onChange={(e) => setReason(e.target.value)} aria-describedby="rv-n" spellCheck />
          <p id="rv-n" className={why.length >= REASON_MIN ? "hint" : "hint err"}>
            {why.length}/{REASON_MIN} characters
          </p>
          <div className="filters" style={{ marginBottom: 10 }}>
            <div>
              <label htmlFor="rv-verb">Replacement</label>
              <select id="rv-verb" value={verb} onChange={(e) => setVerb(e.target.value as Replacement)}>
                <option value="raise_priority">raise_priority</option>
                <option value="tighten">tighten (narrower max)</option>
                <option value="waive">waive (LOOSENING: two approvers)</option>
              </select>
            </div>
            {verb === "raise_priority" ? (
              <div>
                <label htmlFor="rv-prio">Priority</label>
                <select id="rv-prio" value={priority} onChange={(e) => setPriority(e.target.value)}>
                  <option>Critical</option>
                  <option>High</option>
                </select>
              </div>
            ) : null}
            {verb === "tighten" ? (
              <div>
                <label htmlFor="rv-max">New max threshold</label>
                <input id="rv-max" type="text" inputMode="decimal" value={max} onChange={(e) => setMax(e.target.value)} />
              </div>
            ) : null}
            {verb === "waive" ? (
              <div>
                <label htmlFor="rv-exp">Waiver expires</label>
                <input id="rv-exp" type="date" value={expires} onChange={(e) => setExpires(e.target.value)} />
              </div>
            ) : null}
            <div>
              <span className="hint">
                overlay ref <code>{ref}</code>
              </span>
            </div>
          </div>
        </fieldset>
        <div className="row">
          <button type="submit" className="danger" disabled={busy || !!msg?.ok || why.length < REASON_MIN || !maxOk}>
            {busy ? "Drafting…" : "Draft revocation + replacement"}
          </button>
          <button type="button" onClick={onClose}>
            Close
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

const uniq = (xs: (string | null | undefined)[]) => [...new Set(xs.map((x) => x ?? "—"))].sort();

function Select({ id, label, value, set, options }: { id: string; label: string; value: string; set: (v: string) => void; options: string[] }) {
  return (
    <div>
      <label htmlFor={id}>{label}</label>
      <select id={id} value={value} onChange={(e) => set(e.target.value)}>
        <option value="">all</option>
        {options.map((o) => (
          <option key={o} value={o}>
            {o}
          </option>
        ))}
      </select>
    </div>
  );
}

function Rules() {
  const { session, client } = useAuth();
  const { data, error, loading, reload } = useLoad(session ? client.rules : null, [session?.token]);
  const { data: cov } = useLoad(session ? client.coverage : null, [session?.token]);
  const [q, setQ] = useState("");
  const [dom, setDom] = useState("");
  const [dim, setDim] = useState("");
  const [status, setStatus] = useState("");
  const [gating, setGating] = useState("");
  const [kind, setKind] = useState("");
  const [ov, setOv] = useState("");
  const [revoking, setRevoking] = useState<RuleRow | null>(null);

  const idx = useMemo(() => domainIndex(cov), [cov]);
  const rows = useMemo(() => (data?.rules ?? []).map((r) => ({ r, domain: domainOf(r, idx) })), [data, idx]);
  const shown = rows.filter(({ r, domain }) => {
    const t = q.trim().toLowerCase();
    if (t && ![r.id, r.entity, r.title ?? "", r.relation ?? "", r.reason].some((x) => x.toLowerCase().includes(t))) return false;
    if (dom && domain !== dom) return false;
    if (dim && (r.dimension ?? "—") !== dim) return false;
    if (status && r.status !== status) return false;
    if (gating && (r.gating ?? "—") !== gating) return false;
    if (kind && r.kind !== kind) return false;
    if (ov === "any" && !r.overlay) return false;
    if (ov === "none" && r.overlay) return false;
    if ((ov === "ACTIVE" || ov === "EXPIRED") && r.overlay?.status !== ov) return false;
    return true;
  });
  const active = rows.filter(({ r }) => r.status === "ACTIVE").length;

  return (
    <>
      <ErrorNote error={error} what="the rule catalog" />
      {data ? (
        <p className="hint">
          State <strong>{data.state}</strong> · {data.ratified ? "ratified" : "NOT ratified: no rule compiles, every unit holds"} · ruleset{" "}
          <code title={data.ruleset_version ?? ""}>{data.ruleset_version ? shortHash(data.ruleset_version, 10) : "none"}</code> ·{" "}
          {active} of {rows.length} compiled rule(s) ACTIVE
        </p>
      ) : null}
      <form className="panel filters" role="search" aria-label="Filter rules" onSubmit={(e) => e.preventDefault()}>
        <div style={{ minWidth: 220, flex: 1 }}>
          <label htmlFor="rq">Search id, entity, title, relation, reason</label>
          <input id="rq" type="text" value={q} onChange={(e) => setQ(e.target.value)} />
        </div>
        <Select id="rdom" label="Domain" value={dom} set={setDom} options={uniq(rows.map((x) => x.domain))} />
        <Select id="rdim" label="Dimension" value={dim} set={setDim} options={uniq(rows.map((x) => x.r.dimension))} />
        <Select id="rkind" label="Kind" value={kind} set={setKind} options={uniq(rows.map((x) => x.r.kind))} />
        <Select id="rst" label="Status" value={status} set={setStatus} options={uniq(rows.map((x) => x.r.status))} />
        <Select id="rgat" label="Gating" value={gating} set={setGating} options={uniq(rows.map((x) => x.r.gating))} />
        <div>
          <label htmlFor="rov">Overlay</label>
          <select id="rov" value={ov} onChange={(e) => setOv(e.target.value)}>
            <option value="">all</option>
            <option value="any">has overlay</option>
            <option value="ACTIVE">overlay ACTIVE</option>
            <option value="EXPIRED">overlay EXPIRED</option>
            <option value="none">no overlay</option>
          </select>
        </div>
        <button type="button" onClick={() => void reload()} disabled={loading}>
          Refresh
        </button>
      </form>
      {revoking && data ? <RevokePanel key={`${revoking.id}@${revoking.entity}`} state={data.state} rule={revoking} onClose={() => setRevoking(null)} /> : null}
      <section className="panel" aria-labelledby="rules-h">
        <h2 id="rules-h">
          Compiled catalog <span className="sub" aria-live="polite">{data ? `${shown.length} of ${rows.length} shown` : "loading…"}</span>
        </h2>
        <div className="tablewrap">
          <table>
            <caption className="skip">Compiled rules with status, gating, overlay, effective hash and approving change request</caption>
            <thead>
              <tr>
                <th scope="col">Rule</th>
                <th scope="col">Entity / relation</th>
                <th scope="col">Domain / dimension</th>
                <th scope="col">Kind</th>
                <th scope="col">Status / reason</th>
                <th scope="col">Gating</th>
                <th scope="col">Classification</th>
                <th scope="col">Overlay</th>
                <th scope="col">Effective hash</th>
                <th scope="col">Approved by</th>
                <th scope="col">Action</th>
              </tr>
            </thead>
            <tbody>
              {shown.map(({ r, domain }) => (
                <tr key={`${r.id}@${r.entity}`}>
                  <td>
                    <span className="mono">{r.id}</span>
                    {r.title ? <div className="hint">{r.title}</div> : null}
                  </td>
                  <td>
                    <div className="mono">{r.entity}</div>
                    <div className="hint mono">{r.relation ?? "no relation"}</div>
                  </td>
                  <td>
                    {domain}
                    <div className="hint">{r.dimension ?? "—"}</div>
                  </td>
                  <td>{r.kind}</td>
                  <td>
                    <ToneChip tone={ruleStatusTone(r.status)} label={r.status} title={`Rule status ${r.status}`} />
                    {r.reason ? <div className="hint">{r.reason}</div> : null}
                  </td>
                  <td>
                    {r.gating === "advisory" ? (
                      <ToneChip tone="neutral" label="advisory" title="Recorded but never holds a unit" />
                    ) : (
                      <ToneChip tone="info" label={r.gating ?? "gate"} title="Holds a unit when it does not pass" />
                    )}
                  </td>
                  <td>
                    {r.classification?.length ? (
                      <span className="row" style={{ gap: 4 }}>
                        {r.classification.map((c) => (
                          <ClassChip key={c} c={c} />
                        ))}
                      </span>
                    ) : (
                      <span className="muted">—</span>
                    )}
                  </td>
                  <td>
                    {r.overlay ? (
                      <>
                        <span className="mono">{r.overlay.verb}</span>{" "}
                        <ToneChip
                          tone={r.overlay.status === "EXPIRED" ? "warn" : "ok"}
                          label={r.overlay.status ?? "?"}
                          title={r.overlay.status === "EXPIRED" ? "Expired overlays are ignored by the compiler" : "Overlay in force"}
                        />
                        <div className="hint">expires {r.overlay.expires ?? "never"}</div>
                      </>
                    ) : (
                      <span className="muted">—</span>
                    )}
                  </td>
                  <td>
                    <code title={r.effective_hash}>{shortHash(r.effective_hash, 10)}</code>
                  </td>
                  <td>
                    {r.approved_by_cr ? (
                      r.approved_by_cr === "TEST-GENESIS" ? (
                        <span className="mono">TEST-GENESIS</span>
                      ) : (
                        <Link className="mono" href={`/governance/${encodeURIComponent(r.approved_by_cr)}`}>
                          {r.approved_by_cr}
                        </Link>
                      )
                    ) : (
                      <span className="muted" title="No registry approval for this exact effective hash">
                        none
                      </span>
                    )}
                  </td>
                  <td>
                    {r.approved_by_cr ? (
                      <button type="button" onClick={() => setRevoking(r)} aria-label={`Revoke version ${r.effective_hash.slice(0, 12)} of ${r.id}@${r.entity}`}>
                        Revoke version
                      </button>
                    ) : (
                      <span className="muted" title="Only an approved version can be revoked">—</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </>
  );
}

export default function Page() {
  return (
    <>
      <PageHeader title="Rules" />
      <PageBody>
      <Gate>
        <Rules />
      </Gate>
      </PageBody>
    </>
  );
}
