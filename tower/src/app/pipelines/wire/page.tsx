"use client";
/**
 * Wiring wizard (K2c, PFP :844): proposes a new consumer PIPELINE + its TASK_GATE items. This route never
 * decides or applies anything -- the SAME two-person `/governance/change-requests/{cr_id}/challenge` + `/decide`
 * mechanism every other change request in this codebase goes through comes next. The demo's load-bearing moment
 * (EXECUTION_PLAN.md K2 "Demo moments"): a single approver is refused, and once two different people approve,
 * `effective_tier_after` for an already-gated dataset does not move. No causal wording is added to what the
 * response itself says.
 */
import Link from "next/link";
import { useState } from "react";
import { ApiError } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useCatalog } from "@/lib/api/catalog";
import { SignIn } from "@/components/SignIn";
import { ToneChip } from "@/components/Chip";
import type { DiscoverResponse, WireResponse, WireTaskGate } from "@/lib/types/catalog";
import { PageBody, PageHeader } from "@/components/layout/PageHeader";

/** The server's own minimum (`catalog_routes.py:catalog_wire`, `len(rationale) < 10`); mirrored client-side for a
 *  good error message only, never relied on alone -- the server re-checks it regardless. */
const WIRE_RATIONALE_MIN = 10;
const MODES = ["advisory", "block", "hold_until_terminal"] as const;
const TIERS = ["L1", "L2", "L3", "L4"] as const;

interface GateDraft {
  task_id: string;
  datasets: string;
  mode: (typeof MODES)[number];
  max_wait: string;
  on_timeout: string;
  release_ceiling: (typeof TIERS)[number];
}

const emptyGate = (): GateDraft => ({ task_id: "", datasets: "", mode: "advisory", max_wait: "PT2H", on_timeout: "", release_ceiling: "L1" });

function DiscoverPreview({ disc, error }: { disc: DiscoverResponse | null; error: ApiError | null }) {
  if (error) {
    const refused = error.status === 422;
    return (
      <div role="alert" className="banner t-warn">
        <strong>{refused ? "Refused" : "Could not preview"}:</strong> {error.message}
      </div>
    );
  }
  if (!disc) return null;
  return (
    <div className="tablewrap" style={{ marginTop: 8 }}>
      <table>
        <caption className="skip">Discovered dbt manifest nodes, honestly layered (never inferred from folder names)</caption>
        <thead>
          <tr>
            <th scope="col">Node</th>
            <th scope="col">Schema</th>
            <th scope="col">Depends on</th>
            <th scope="col">Layer</th>
            <th scope="col">Layer source</th>
          </tr>
        </thead>
        <tbody>
          {disc.nodes.map((n) => (
            <tr key={n.node}>
              <td className="mono">{n.node}</td>
              <td className="mono">{n.schema ?? "—"}</td>
              <td className="mono">{n.depends_on.join(", ") || "—"}</td>
              <td>{n.layer}</td>
              <td>
                {n.layer_source === "UNLAYERED" ? <ToneChip tone="warn" label="UNLAYERED" title="No layer_map entry and no meta.dq_layer; never inferred from folder names" /> : <span className="hint">{n.layer_source}</span>}
              </td>
            </tr>
          ))}
          {!disc.nodes.length ? (
            <tr>
              <td colSpan={5}>
                <span className="dash">–</span> No MODEL node discovered in this manifest.
              </td>
            </tr>
          ) : null}
        </tbody>
      </table>
      <p className="hint" style={{ marginTop: 6 }}>
        Manifest digest <code title={disc.manifest_digest}>{disc.manifest_digest.slice(0, 16)}…</code>. This preview writes nothing.
      </p>
    </div>
  );
}

function ImpactView({ r }: { r: WireResponse }) {
  const i = r.impact;
  const tiers = new Set([...Object.keys(i.effective_tier_before), ...Object.keys(i.effective_tier_after)]);
  return (
    <section className="panel" aria-labelledby="wire-impact-h" style={{ borderColor: "var(--info-bd)", borderWidth: 2 }}>
      <h2 id="wire-impact-h">
        Proposed <Link className="mono" href={`/governance/${encodeURIComponent(r.cr_id)}`}>{r.cr_id}</Link>
      </h2>
      <p role="status">
        Needs <strong>{r.approvals_required}</strong> approval(s) from a <strong>different</strong> person before anything here takes effect.
        {r.approvals_required >= 2 ? " A single approver is refused: PIPELINE and TASK_GATE items are never lower-only." : null}
      </p>
      <dl className="kv">
        <dt>Gates added</dt>
        <dd className="mono">{i.gates_added.join(", ") || "none"}</dd>
        <dt>Datasets introduced</dt>
        <dd className="mono">{i.datasets_introduced.join(", ") || "none"}</dd>
        <dt>Datasets already gated</dt>
        <dd className="mono">{i.datasets_already_gated.map((g) => `${g.dataset} (by ${g.introduced_by})`).join(", ") || "none"}</dd>
        <dt>Ceiling</dt>
        <dd className="mono">{Array.isArray(i.ceiling.value) ? i.ceiling.value.join(", ") : i.ceiling.value ?? "none"}</dd>
        <dt>Mode</dt>
        <dd className="mono">{i.mode.join(", ")}</dd>
      </dl>
      {i.validation_errors.length ? (
        <div role="alert" className="banner t-warn">
          {i.validation_errors.join("; ")}
        </div>
      ) : null}
      <div className="tablewrap" style={{ marginTop: 8 }}>
        <table>
          <caption className="skip">Effective tier before and after this proposal, per requested dataset. Values are recorded exactly as computed; already-gated datasets do not move.</caption>
          <thead>
            <tr>
              <th scope="col">Dataset</th>
              <th scope="col">Effective tier before</th>
              <th scope="col">Effective tier after</th>
              <th scope="col">Moved</th>
            </tr>
          </thead>
          <tbody>
            {[...tiers].sort().map((ds) => {
              const before = i.effective_tier_before[ds] ?? null;
              const after = i.effective_tier_after[ds] ?? null;
              const moved = before !== after;
              const already = i.datasets_already_gated.some((g) => g.dataset === ds);
              return (
                <tr key={ds}>
                  <td className="mono">{ds}</td>
                  <td className="mono">{before ?? "—"}</td>
                  <td className="mono">{after ?? "—"}</td>
                  <td>
                    {moved ? (
                      <ToneChip tone="warn" label="moved" />
                    ) : already ? (
                      <ToneChip tone="ok" label="unchanged (already gated)" title="ORCH-1: an already-gated consumer reopens nothing" />
                    ) : (
                      <ToneChip tone="neutral" label="unchanged" />
                    )}
                  </td>
                </tr>
              );
            })}
            {!tiers.size ? (
              <tr>
                <td colSpan={4}>
                  <span className="dash">–</span>
                </td>
              </tr>
            ) : null}
          </tbody>
        </table>
      </div>
      <p className="hint" style={{ marginTop: 8 }}>
        Next: open {r.cr_id} and challenge it from a second person&rsquo;s session (sign out and back in, in the mock, to switch users in this tab).
      </p>
    </section>
  );
}

function WireWizard() {
  const catalog = useCatalog();
  const [pipelineId, setPipelineId] = useState("");
  const [manifestPath, setManifestPath] = useState("pipelines/tmsis_outbound/target/manifest.json");
  const [platform, setPlatform] = useState("duckdb");
  const [domains, setDomains] = useState("");
  const [runOn, setRunOn] = useState("manual");
  const [gates, setGates] = useState<GateDraft[]>([emptyGate()]);
  const [rationale, setRationale] = useState("");

  const [disc, setDisc] = useState<DiscoverResponse | null>(null);
  const [discErr, setDiscErr] = useState<ApiError | null>(null);
  const [discBusy, setDiscBusy] = useState(false);

  const [wireResult, setWireResult] = useState<WireResponse | null>(null);
  const [wireErr, setWireErr] = useState<ApiError | null>(null);
  const [busy, setBusy] = useState(false);

  const gatesValid = gates.every((g) => g.task_id.trim() && g.datasets.trim());
  const canSubmit = pipelineId.trim() && manifestPath.trim() && gates.length > 0 && gatesValid && rationale.trim().length >= WIRE_RATIONALE_MIN && !busy;

  async function discover() {
    setDiscBusy(true);
    setDiscErr(null);
    setDisc(null);
    try {
      const d = await catalog.catalogDiscover({ adapter: "dbt_files", manifest_path: manifestPath.trim() });
      setDisc(d);
    } catch (e) {
      setDiscErr(e instanceof ApiError ? e : new ApiError(0, { detail: String(e) }));
    } finally {
      setDiscBusy(false);
    }
  }

  function setGate(i: number, patch: Partial<GateDraft>) {
    setGates((gs) => gs.map((g, j) => (j === i ? { ...g, ...patch } : g)));
  }

  async function submit() {
    setBusy(true);
    setWireErr(null);
    setWireResult(null);
    try {
      const task_gates: WireTaskGate[] = gates.map((g) => ({
        task_id: g.task_id.trim(),
        datasets: g.datasets.split(",").map((s) => s.trim()).filter(Boolean),
        mode: g.mode,
        max_wait: g.max_wait.trim() || undefined,
        on_timeout: g.on_timeout.trim() || undefined,
        release_ceiling: g.release_ceiling,
      }));
      const r = await catalog.catalogWire({
        pipeline: {
          id: pipelineId.trim(),
          adapter: "dbt_files",
          scope: { manifest_path: manifestPath.trim() },
          platform: platform.trim() || undefined,
          domains: domains.split(",").map((s) => s.trim()).filter(Boolean),
          run_on: runOn.trim() || undefined,
        },
        task_gates,
        rationale: rationale.trim(),
      });
      setWireResult(r);
    } catch (e) {
      setWireErr(e instanceof ApiError ? e : new ApiError(0, { detail: String(e) }));
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <PageHeader
        title="Wire a pipeline"
        sub="Propose a new consumer PIPELINE and its TASK_GATE items"
        crumb={<Link href="/pipelines/catalog">Catalog /</Link>}
      />
      <PageBody>
        <div className="twocol">
          <div>
            <section className="panel" aria-labelledby="wire-pipe-h">
              <h2 id="wire-pipe-h">1. Pipeline</h2>
              <form onSubmit={(e) => e.preventDefault()}>
                <label htmlFor="w-id">Pipeline id</label>
                <input id="w-id" type="text" value={pipelineId} onChange={(e) => setPipelineId(e.target.value)} placeholder="tmsis_outbound" />
                <label htmlFor="w-manifest">Manifest path (confined to pipelines/)</label>
                <input id="w-manifest" type="text" value={manifestPath} onChange={(e) => setManifestPath(e.target.value)} />
                <div className="row" style={{ marginBottom: 10 }}>
                  <button type="button" onClick={() => void discover()} disabled={discBusy || !manifestPath.trim()}>
                    {discBusy ? "Previewing…" : "Preview (discover)"}
                  </button>
                  <span className="hint">Writes nothing; path confinement to pipelines/ is enforced server-side.</span>
                </div>
                <DiscoverPreview disc={disc} error={discErr} />
                <label htmlFor="w-platform">Platform</label>
                <input id="w-platform" type="text" value={platform} onChange={(e) => setPlatform(e.target.value)} />
                <label htmlFor="w-domains">Domains (comma-separated)</label>
                <input id="w-domains" type="text" value={domains} onChange={(e) => setDomains(e.target.value)} placeholder="CLAIMS, ELIGIBILITY" />
                <label htmlFor="w-run-on">Run on</label>
                <select id="w-run-on" value={runOn} onChange={(e) => setRunOn(e.target.value)}>
                  <option value="manual">manual</option>
                  <option value="schedule">schedule</option>
                </select>
              </form>
            </section>

            <section className="panel" aria-labelledby="wire-rat-h">
              <h2 id="wire-rat-h">3. Rationale</h2>
              <label htmlFor="w-why">Reason (at least {WIRE_RATIONALE_MIN} characters)</label>
              <textarea id="w-why" value={rationale} onChange={(e) => setRationale(e.target.value)} aria-describedby="w-why-n" spellCheck />
              <p id="w-why-n" className={rationale.trim().length >= WIRE_RATIONALE_MIN ? "hint" : "hint err"}>
                {rationale.trim().length}/{WIRE_RATIONALE_MIN} characters
              </p>
              <div className="row">
                <button type="button" className="primary" onClick={() => void submit()} disabled={!canSubmit}>
                  {busy ? "Proposing…" : "Propose wiring"}
                </button>
              </div>
              {wireErr ? (
                <div role="alert" className="banner t-bad" style={{ marginTop: 8 }}>
                  <strong>{wireErr.status === 422 ? "Refused" : "Failed"}:</strong> {wireErr.message}
                </div>
              ) : null}
            </section>
          </div>

          <div>
            <section className="panel" aria-labelledby="wire-gates-h">
              <h2 id="wire-gates-h">
                2. Task gates <span className="sub">{gates.length}</span>
              </h2>
              {gates.map((g, i) => (
                <fieldset key={i} style={{ border: "1px solid var(--border)", borderRadius: 5, padding: 10, marginBottom: 10 }}>
                  <legend className="hint">Gate {i + 1}</legend>
                  <label htmlFor={`w-task-${i}`}>Task id</label>
                  <input id={`w-task-${i}`} type="text" value={g.task_id} onChange={(e) => setGate(i, { task_id: e.target.value })} placeholder="outbound_eligibility_extract" />
                  <label htmlFor={`w-ds-${i}`}>Datasets (comma-separated)</label>
                  <input id={`w-ds-${i}`} type="text" value={g.datasets} onChange={(e) => setGate(i, { datasets: e.target.value })} placeholder="silver.eligibility_span" />
                  <div className="filters" style={{ marginBottom: 0 }}>
                    <div>
                      <label htmlFor={`w-mode-${i}`}>Mode</label>
                      <select id={`w-mode-${i}`} value={g.mode} onChange={(e) => setGate(i, { mode: e.target.value as GateDraft["mode"] })}>
                        {MODES.map((m) => (
                          <option key={m} value={m}>
                            {m}
                          </option>
                        ))}
                      </select>
                    </div>
                    <div>
                      <label htmlFor={`w-ceil-${i}`}>Release ceiling</label>
                      <select id={`w-ceil-${i}`} value={g.release_ceiling} onChange={(e) => setGate(i, { release_ceiling: e.target.value as GateDraft["release_ceiling"] })}>
                        {TIERS.map((t) => (
                          <option key={t} value={t}>
                            {t}
                          </option>
                        ))}
                      </select>
                    </div>
                    <div>
                      <label htmlFor={`w-wait-${i}`}>Max wait (ISO 8601)</label>
                      <input id={`w-wait-${i}`} type="text" value={g.max_wait} onChange={(e) => setGate(i, { max_wait: e.target.value })} style={{ width: 90 }} />
                    </div>
                    <div>
                      <label htmlFor={`w-timeout-${i}`}>On timeout</label>
                      <input id={`w-timeout-${i}`} type="text" value={g.on_timeout} onChange={(e) => setGate(i, { on_timeout: e.target.value })} placeholder={g.mode === "advisory" ? "PASS_ADVISORY" : "HOLD"} style={{ width: 120 }} />
                    </div>
                  </div>
                  {gates.length > 1 ? (
                    <button type="button" onClick={() => setGates((gs) => gs.filter((_, j) => j !== i))} style={{ marginTop: 6 }}>
                      Remove gate
                    </button>
                  ) : null}
                </fieldset>
              ))}
              <button type="button" onClick={() => setGates((gs) => [...gs, emptyGate()])}>
                Add another gate
              </button>
            </section>
            {wireResult ? <ImpactView r={wireResult} /> : null}
          </div>
        </div>
      </PageBody>
    </>
  );
}

export default function Page() {
  const { session } = useAuth();
  if (!session)
    return (
      <>
        <PageHeader title="Wire a pipeline" />
        <PageBody>
          <SignIn />
        </PageBody>
      </>
    );
  return <WireWizard />;
}
