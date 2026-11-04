"use client";
/**
 * Per-DAG agent profile (PFP P1-2, K1): "This night ran under this exact configuration. Here is who approved each
 * part, and what is not governed." Dense two-column layout (LAYOUT_SYSTEM §6, `.twocol`); tables first (DEC-020).
 *
 * D10 (accepted mitigation, superseded by K8): the Edit affordance was visibly disabled through K7; K8 wires it to
 * a drawer that proposes a config change through the SAME propose-a-CR mechanism `/pipelines/wire` already uses
 * for real (`catalogWire` / `server.ts`'s `proposeCr`), producing a CR in the same shape as every other change
 * request. K8 ships the REAL route (`POST /catalog/pipelines/{id}/profile/draft`, `catalog_routes.py`) this
 * drawer now calls -- the PRE-BACKEND banner and the `PREVIEW_K7_K12` gate on the Edit button are both removed
 * for THIS drawer only (K7_K8_K9_DESIGN.md §2.1 point 5); the profile VIEW above it is unaffected and stays real.
 * Review of the proposed CR routes to the EXISTING `/governance/[crId]` screen; this file builds no review UI.
 * Does not touch E-09's baseline-key rename (BACKLOG:26, gated by ADJ-6): no baseline field is offered here.
 * L-10: the monitors chip reads "not governed" literally, from monitors.governed, never implied by its absence.
 * D7: a caller whose scope does not intersect this pipeline's authorities gets the API's 403 verbatim, handled
 * here as an honest access message, never a blank page or a crash.
 */
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useEffect, useMemo, useRef, useState } from "react";
import { ApiError } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useCatalog } from "@/lib/api/catalog";
import { useObs } from "@/lib/api/obs";
import { shortHash } from "@/lib/format";
import { useUrlParam } from "@/components/layout/SplitPane";
import type { PipelineProfile } from "@/lib/types/catalog";
import type { RunRow } from "@/lib/types/obs";
import { SignIn } from "@/components/SignIn";
import { ErrorNote } from "@/components/ErrorNote";
import { ToneChip } from "@/components/Chip";
import { PageBody, PageHeader } from "@/components/layout/PageHeader";

const DRAFT_RATIONALE_MIN = 10;

/**
 * K8: the propose-a-config-change drawer, reusing the SAME `.overlay`/`.dialog` modal pattern `ShortcutSheet`
 * already uses (this codebase has no separate slide-over drawer component) and the SAME propose/CR mechanism
 * `/pipelines/wire` already exercises. Submitting never decides or applies anything -- it returns a cr_id that
 * routes to the EXISTING `/governance/[crId]` review screen, the only place a CR is ever approved.
 */
function ProfileEditDrawer({ pipelineId, data, onClose }: { pipelineId: string; data: PipelineProfile; onClose: () => void }) {
  const catalog = useCatalog();
  const router = useRouter();
  const ref = useRef<HTMLDivElement>(null);
  const [platform, setPlatform] = useState(data.pipeline.platform);
  const [domains, setDomains] = useState(data.pipeline.domains.join(", "));
  const [rationale, setRationale] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<ApiError | null>(null);
  const [result, setResult] = useState<{ cr_id: string; approvals_required: number } | null>(null);

  useEffect(() => {
    const prev = document.activeElement;
    ref.current?.focus();
    return () => {
      if (prev instanceof HTMLElement) prev.focus();
    };
  }, []);

  const canSubmit = rationale.trim().length >= DRAFT_RATIONALE_MIN && !busy && !result;

  async function submit() {
    setBusy(true);
    setErr(null);
    try {
      const r = await catalog.catalogProfileDraft(pipelineId, {
        platform: platform.trim() || undefined,
        domains: domains.split(",").map((s) => s.trim()).filter(Boolean),
        rationale: rationale.trim(),
      });
      setResult(r);
    } catch (e) {
      setErr(e instanceof ApiError ? e : new ApiError(0, { detail: String(e) }));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="overlay" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div
        className="dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="edit-drawer-h"
        tabIndex={-1}
        ref={ref}
        style={{ width: "min(560px, calc(100vw - 32px))" }}
        onKeyDown={(e) => {
          if (e.key === "Escape") {
            e.preventDefault();
            onClose();
          }
        }}
      >
        <div className="dh">
          <h2 id="edit-drawer-h">Propose a profile change</h2>
          <button type="button" className="sm ghost" onClick={onClose} aria-label="Close">
            ✕
          </button>
        </div>
        <div className="sheet">
          <p className="hint">
            Proposes a change to <span className="mono">{pipelineId}</span>&rsquo;s PIPELINE configuration (platform, domains). This never
            decides or applies anything -- it goes through the SAME two-person change-request mechanism every other proposal here uses.
          </p>
          <label htmlFor="ed-platform">Platform</label>
          <input id="ed-platform" type="text" value={platform} onChange={(e) => setPlatform(e.target.value)} disabled={!!result} />
          <label htmlFor="ed-domains">Domains (comma-separated)</label>
          <input id="ed-domains" type="text" value={domains} onChange={(e) => setDomains(e.target.value)} disabled={!!result} />
          <label htmlFor="ed-why">Rationale (at least {DRAFT_RATIONALE_MIN} characters)</label>
          <textarea id="ed-why" value={rationale} onChange={(e) => setRationale(e.target.value)} disabled={!!result} spellCheck />
          {err ? (
            <div role="alert" className="banner t-bad" style={{ marginTop: 8 }}>
              <strong>{err.status === 422 ? "Refused" : "Failed"}:</strong> {err.message}
            </div>
          ) : null}
          {result ? (
            <div role="status" className="banner t-info" style={{ marginTop: 8 }}>
              Proposed{" "}
              <Link className="mono" href={`/governance/${encodeURIComponent(result.cr_id)}`}>
                {result.cr_id}
              </Link>
              . Needs <strong>{result.approvals_required}</strong> approval(s) from a different person before anything applies.
              <div className="row" style={{ marginTop: 8 }}>
                <button type="button" className="primary" onClick={() => router.push(`/governance/${encodeURIComponent(result.cr_id)}`)}>
                  Open change request
                </button>
              </div>
            </div>
          ) : (
            <div className="row" style={{ marginTop: 10 }}>
              <button type="button" className="primary" onClick={() => void submit()} disabled={!canSubmit}>
                {busy ? "Proposing…" : "Propose change"}
              </button>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function GatingChip({ gating }: { gating: string | null }) {
  if (!gating) return <span className="muted">—</span>;
  return gating === "advisory" ? (
    <ToneChip tone="neutral" label="advisory" title="Recorded but never holds a unit" />
  ) : (
    <ToneChip tone="info" label={gating} title="Holds a unit when it does not pass" />
  );
}

/** Effective vs. approved (PFP :712): the live rule and the latest APPROVED version of the same rule, never
 * compared causally -- just whether they match, stated as a fact. */
function EffectiveVsApproved({ r }: { r: PipelineProfile["rules"][string] }) {
  if (r.approved_hash === null) return <ToneChip tone="warn" label="no approved version" title="This rule has never been approved by the registry" />;
  return r.effective_equals_approved ? (
    <ToneChip tone="ok" label="matches approved" title={`Effective hash equals the approved hash ${shortHash(r.approved_hash, 10)}`} />
  ) : (
    <ToneChip tone="warn" label="differs from approved" title={`Effective ${shortHash(r.hash, 10)} vs. approved ${shortHash(r.approved_hash, 10)}`} />
  );
}

function AsOfPicker({ value, nights, onChange }: { value: string; nights: RunRow[]; onChange: (v: string) => void }) {
  return (
    <span className="fld">
      <label htmlFor="pp-asof" style={{ margin: 0, fontWeight: 400, fontSize: "inherit", color: "inherit" }}>
        As of
      </label>
      <select id="pp-asof" value={value} onChange={(e) => onChange(e.target.value)}>
        <option value="">now (latest approved)</option>
        {nights.map((n) => (
          <option key={n.trace_id} value={n.trace_id}>
            night {n.night_date ?? n.trace_id}
          </option>
        ))}
      </select>
    </span>
  );
}

function ProfileView({ pipelineId }: { pipelineId: string }) {
  const catalog = useCatalog();
  const obs = useObs();
  const router = useRouter();
  const asOf = useUrlParam("as_of") ?? "";
  const [data, setData] = useState<PipelineProfile | null>(null);
  const [err, setErr] = useState<ApiError | null>(null);
  const [nights, setNights] = useState<RunRow[]>([]);
  const [editOpen, setEditOpen] = useState(false);

  useEffect(() => {
    let live = true;
    setData(null);
    setErr(null);
    catalog.pipelineProfile(pipelineId, asOf || null).then(
      (x) => live && setData(x),
      (e: unknown) => live && setErr(e instanceof ApiError ? e : new ApiError(0, { detail: String(e) })),
    );
    return () => {
      live = false;
    };
  }, [catalog, pipelineId, asOf]);

  // The as-of picker is fed by night traces (NIGHT roots), so a presenter can pick a past night and see the
  // profile as it was then. Loaded best-effort: a failure here only disables the picker, never the page.
  useEffect(() => {
    let live = true;
    obs.runs({ kind: "NIGHT", limit: 50 }).then(
      (r) => live && setNights(r.runs),
      () => live && setNights([]),
    );
    return () => {
      live = false;
    };
  }, [obs]);

  function setAsOf(v: string) {
    const q = new URLSearchParams();
    if (v) q.set("as_of", v);
    router.replace(`/pipelines/${encodeURIComponent(pipelineId)}/profile${q.toString() ? `?${q}` : ""}`);
  }

  const contractRows = useMemo(() => Object.entries(data?.contracts ?? {}), [data]);
  const ruleRows = useMemo(() => Object.entries(data?.rules ?? {}), [data]);
  const contributorRows = useMemo(() => Object.entries(data?.contributors ?? {}).flatMap(([id, vs]) => vs.map((v) => ({ id, ...v }))), [data]);

  if (err) {
    return (
      <>
        <PageHeader crumb={<Link href="/pipelines">Pipeline map /</Link>} title={<span className="mono">{pipelineId}</span>} sub="Agent profile" />
        <PageBody>
          {err.status === 403 ? (
            <div role="alert" className="banner t-warn">
              <h2>You do not have access to this pipeline&rsquo;s authorities.</h2>
              <p>
                This pipeline&rsquo;s gated relations are governed under authorities your scope does not intersect. Ask your duty
                administrator for release duty on at least one of them if you expect to see this profile.
              </p>
              <p className="hint">{err.message}</p>
            </div>
          ) : (
            <ErrorNote error={err} what={`the profile of ${pipelineId}`} />
          )}
          {err.status === 404 ? <p className="hint">Unknown pipeline, or an &ldquo;as of&rdquo; trace this control plane never minted.</p> : null}
        </PageBody>
      </>
    );
  }
  if (!data) {
    return (
      <>
        <PageHeader crumb={<Link href="/pipelines">Pipeline map /</Link>} title={<span className="mono">{pipelineId}</span>} sub="Agent profile" />
        <PageBody>
          <p className="hint">Loading the profile&hellip;</p>
        </PageBody>
      </>
    );
  }

  return (
    <>
      <PageHeader
        crumb={
          <>
            <Link href="/pipelines">Pipeline map</Link> / <Link href="/pipelines/catalog">Catalog</Link> /{" "}
          </>
        }
        title={<span className="mono">{data.pipeline.id}</span>}
        sub={
          <>
            {data.pipeline.platform} / {data.pipeline.orchestrator} · {data.pipeline.domains.join(", ")} · digest{" "}
            <code title={data.profile_digest}>{shortHash(data.profile_digest, 12)}</code>
            {data.as_of ? (
              <>
                {" "}
                · as of <RefLink trace={data.as_of} />
              </>
            ) : (
              " · now (latest approved)"
            )}
          </>
        }
        actions={
          <>
            <AsOfPicker value={asOf} nights={nights} onChange={setAsOf} />
            {/* K8: the drawer now proposes through the REAL route; no preview gate needed any more (D10
                superseded). */}
            <button
              type="button"
              onClick={() => setEditOpen(true)}
              title="Propose a change to this pipeline's configuration"
            >
              Edit
            </button>
          </>
        }
      />
      {editOpen ? <ProfileEditDrawer pipelineId={pipelineId} data={data} onClose={() => setEditOpen(false)} /> : null}
      <PageBody>
        <div className="twocol">
          <div>
            <section className="panel" aria-labelledby="pp-contracts">
              <h2 id="pp-contracts">
                Contracts <span className="sub">{contractRows.length} gated relation(s)</span>
              </h2>
              <div className="tablewrap">
                <table>
                  <caption className="skip">Gated relations with domain, authorities and status</caption>
                  <thead>
                    <tr>
                      <th scope="col">Relation</th>
                      <th scope="col">Domain</th>
                      <th scope="col">Authorities by program</th>
                      <th scope="col">Status</th>
                    </tr>
                  </thead>
                  <tbody>
                    {contractRows.map(([rel, c]) => (
                      <tr key={rel}>
                        <td className="mono">{rel}</td>
                        <td>{c.domain}</td>
                        <td>
                          {Object.entries(c.authorities_by_program ?? {}).map(([prog, auths]) => (
                            <div key={prog} className="hint">
                              {prog}: {auths.join(", ")}
                            </div>
                          ))}
                          {c.pre_split_authority ? <div className="hint">pre-split: {c.pre_split_authority}</div> : null}
                        </td>
                        <td>{c.status}</td>
                      </tr>
                    ))}
                    {!contractRows.length ? (
                      <tr>
                        <td colSpan={4} style={{ whiteSpace: "normal" }}>
                          <span className="dash">–</span> No gated relation resolved to a contract entry.
                        </td>
                      </tr>
                    ) : null}
                  </tbody>
                </table>
              </div>
            </section>

            <section className="panel" aria-labelledby="pp-rules">
              <h2 id="pp-rules">
                Rules <span className="sub">{ruleRows.length} rule(s)</span>
              </h2>
              <div className="tablewrap">
                <table>
                  <caption className="skip">Rules on this pipeline&rsquo;s gated relations, with status, gating, and effective-vs-approved</caption>
                  <thead>
                    <tr>
                      <th scope="col">Rule</th>
                      <th scope="col">Status</th>
                      <th scope="col">Gating</th>
                      <th scope="col">Effective hash</th>
                      <th scope="col">Vs. approved</th>
                    </tr>
                  </thead>
                  <tbody>
                    {ruleRows.map(([key, r]) => (
                      <tr key={key}>
                        <td className="mono">{key}</td>
                        <td>{r.status}</td>
                        <td>
                          <GatingChip gating={r.gating} />
                        </td>
                        <td>
                          <code title={r.hash}>{shortHash(r.hash, 10)}</code>
                        </td>
                        <td>
                          <EffectiveVsApproved r={r} />
                        </td>
                      </tr>
                    ))}
                    {!ruleRows.length ? (
                      <tr>
                        <td colSpan={5} style={{ whiteSpace: "normal" }}>
                          <span className="dash">–</span> No compiled rule names a gated relation of this pipeline.
                        </td>
                      </tr>
                    ) : null}
                  </tbody>
                </table>
              </div>
            </section>

            <section className="panel" aria-labelledby="pp-tiers">
              <h2 id="pp-tiers">
                Autonomy tiers <span className="sub">per authority, RELEASE</span>
              </h2>
              <div className="tablewrap">
                <table>
                  <caption className="skip">The RELEASE autonomy tier per authority this pipeline touches</caption>
                  <thead>
                    <tr>
                      <th scope="col">Authority</th>
                      <th scope="col">Tier</th>
                    </tr>
                  </thead>
                  <tbody>
                    {Object.entries(data.tiers).map(([auth, tier]) => (
                      <tr key={auth}>
                        <td className="mono">{auth}</td>
                        <td>{tier}</td>
                      </tr>
                    ))}
                    {!Object.keys(data.tiers).length ? (
                      <tr>
                        <td colSpan={2}>
                          <span className="dash">–</span>
                        </td>
                      </tr>
                    ) : null}
                  </tbody>
                </table>
              </div>
            </section>

            <section className="panel" aria-labelledby="pp-monitors">
              <h2 id="pp-monitors">Monitors</h2>
              <p>
                <ToneChip
                  tone="neutral"
                  label="not governed"
                  title="L-10: the monitor spec and its bindings are sourced from files, not governed by the registry"
                />{" "}
                <span className="hint">provenance {data.monitors.provenance}</span>
              </p>
              <dl className="kv">
                {Object.entries(data.monitors.content).map(([k, v]) => (
                  <div key={k} style={{ display: "contents" }}>
                    <dt className="mono">{k}</dt>
                    <dd className="mono">{typeof v === "object" ? JSON.stringify(v) : String(v)}</dd>
                  </div>
                ))}
              </dl>
            </section>
          </div>

          <div>
            <section className="panel" aria-labelledby="pp-deps">
              <h2 id="pp-deps">
                Dependencies <span className="sub">{data.dependencies.length} edge(s)</span>
              </h2>
              <div className="tablewrap">
                <table>
                  <caption className="skip">Dependency edges naming one of this pipeline&rsquo;s domains</caption>
                  <thead>
                    <tr>
                      <th scope="col">Domain</th>
                      <th scope="col">Depends on</th>
                      <th scope="col">Policy</th>
                      <th scope="col">Temporal binding</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.dependencies.map((d, i) => (
                      <tr key={i}>
                        <td>{d.domain}</td>
                        <td>{d.depends_on}</td>
                        <td className="mono">{d.policy}</td>
                        <td>{typeof d.temporal_binding === "string" ? d.temporal_binding : JSON.stringify(d.temporal_binding ?? {})}</td>
                      </tr>
                    ))}
                    {!data.dependencies.length ? (
                      <tr>
                        <td colSpan={4}>
                          <span className="dash">–</span>
                        </td>
                      </tr>
                    ) : null}
                  </tbody>
                </table>
              </div>
            </section>

            <section className="panel" aria-labelledby="pp-order">
              <h2 id="pp-order">Global order</h2>
              <p className="hint">The whole state&rsquo;s pipeline order (context, not just this pipeline&rsquo;s own position).</p>
              <ol className="row" style={{ listStyle: "none", padding: 0, margin: 0 }}>
                {data.order.map((pid, i) => (
                  <li key={pid} className={pid === data.pipeline.id ? "mono" : "mono muted"} style={pid === data.pipeline.id ? { fontWeight: 600 } : undefined}>
                    {i + 1}. {pid}
                    {i < data.order.length - 1 ? " →" : ""}
                  </li>
                ))}
              </ol>
            </section>

            <section className="panel" aria-labelledby="pp-routing">
              <h2 id="pp-routing">
                Routing <span className="sub">{data.routing.length} route(s)</span>
              </h2>
              <div className="tablewrap">
                <table>
                  <caption className="skip">Inbound file routes naming this pipeline (FEEDS, governed)</caption>
                  <thead>
                    <tr>
                      <th scope="col">Match</th>
                      <th scope="col">Format</th>
                      <th scope="col">Relations</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.routing.map((r, i) => (
                      <tr key={i}>
                        <td className="mono" style={{ whiteSpace: "normal" }}>
                          {r.match}
                        </td>
                        <td>{r.format}</td>
                        <td className="mono">{r.relations.join(", ")}</td>
                      </tr>
                    ))}
                    {!data.routing.length ? (
                      <tr>
                        <td colSpan={3}>
                          <span className="dash">–</span>
                        </td>
                      </tr>
                    ) : null}
                  </tbody>
                </table>
              </div>
            </section>

            <section className="panel" aria-labelledby="pp-contributors">
              <h2 id="pp-contributors">
                Contributors <span className="sub">who approved each part</span>
              </h2>
              <div className="tablewrap">
                <table>
                  <caption className="skip">Every governed artifact version this profile depends on, with its change request and approvers</caption>
                  <thead>
                    <tr>
                      <th scope="col">Artifact</th>
                      <th scope="col">Version</th>
                      <th scope="col">Change request</th>
                      <th scope="col">Approvers</th>
                    </tr>
                  </thead>
                  <tbody>
                    {contributorRows.map((row) => (
                      <tr key={`${row.id}@${row.version}`}>
                        <td className="mono">{row.id}</td>
                        <td className="num">{row.version}</td>
                        <td>
                          {row.cr_id === "TEST-GENESIS" ? (
                            <span className="mono">TEST-GENESIS</span>
                          ) : (
                            <Link className="mono" href={`/governance/${encodeURIComponent(row.cr_id)}`}>
                              {row.cr_id}
                            </Link>
                          )}
                        </td>
                        <td>{row.approvers.length ? row.approvers.join(", ") : <span className="muted">none recorded</span>}</td>
                      </tr>
                    ))}
                    {!contributorRows.length ? (
                      <tr>
                        <td colSpan={4} style={{ whiteSpace: "normal" }}>
                          <span className="dash">–</span> No contributing artifact resolved.
                        </td>
                      </tr>
                    ) : null}
                  </tbody>
                </table>
              </div>
            </section>
          </div>
        </div>
      </PageBody>
    </>
  );
}

/** A trace id shown as a link to its trace page, without pulling in obs/common's full RefLink (that component
 * expects a generic ref string; here the id is always a trace id, already known). */
function RefLink({ trace }: { trace: string }) {
  return (
    <Link className="mono" href={`/runs/${encodeURIComponent(trace)}`}>
      {trace}
    </Link>
  );
}

export default function Page() {
  const params = useParams<{ id: string }>();
  const pipelineId = decodeURIComponent(params.id ?? "");
  const { session } = useAuth();
  if (!session)
    return (
      <>
        <PageHeader crumb={<Link href="/pipelines">Pipeline map /</Link>} title={<span className="mono">{pipelineId}</span>} />
        <PageBody>
          <SignIn />
        </PageBody>
      </>
    );
  return <ProfileView key={pipelineId} pipelineId={pipelineId} />;
}
