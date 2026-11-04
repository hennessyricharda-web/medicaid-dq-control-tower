"use client";
/**
 * Promote a gate to block (K3, PFP P1-4 step (a), `docs/design/packages/K3_DESIGN.md` §3 step 9). This is the
 * PFP's "step (a)" only: a TASK_GATE's `mode` field flips from `advisory` to `block`, as an ordinary two-person
 * change request -- a LABEL / governance change, not a behavior change. Ceiling promotion (step (b)) is P2-5,
 * out of scope here.
 *
 * CRITICAL SCOPE BOUNDARY (K3_DESIGN.md §6 Decision 1, mandatory, re-stated on this page, not softened): a
 * `block`-mode TASK_GATE has ZERO behavioral effect anywhere in this codebase today. The gate's `mode` field is
 * read only by the database's own validity and lower-only checks, never by the evaluation or publish path. This
 * page therefore never implies the pipeline's actual release/hold behavior will change once promoted -- it
 * promotes a LABEL, under governance, pending real enforcement (deferred, recorded as hardening).
 *
 * This is NOT the backtest trigger: the backtest itself runs through the `dqagent backtest` CLI, demo-narrated
 * live, outside this page (PFP :898). This page is for PROPOSING the promotion CR once that evidence already
 * exists in `dq_ctl.cr_evidence` (or seeing the server's honest refusal when it does not yet). Reuses the SAME
 * propose-then-two-approve pattern `/pipelines/wire` already built -- but NOT via `client.crPropose` directly
 * (BACKLOG F-11b: a TASK_GATE PUT item is a full-content replace, and the Catalog row this page holds never
 * carries `correlate`/`max_wait`/`on_timeout`/`reintroduce`, so a client-built item is deterministically
 * malformed). This page instead calls the dedicated `catalogPromoteGate` route (mirrors `catalogWire`'s own
 * pattern: server reads the gate's current full content and patches only `mode`), then the SAME existing
 * `/governance/change-requests/{cr_id}/challenge` + `/decide` flow every other CR goes through.
 */
import Link from "next/link";
import { useParams } from "next/navigation";
import { useMemo, useState } from "react";
import { ApiError } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useCatalog } from "@/lib/api/catalog";
import { useLoad } from "@/lib/useLoad";
import { SignIn } from "@/components/SignIn";
import { ErrorNote } from "@/components/ErrorNote";
import { ToneChip } from "@/components/Chip";
import type { CatalogTaskGate } from "@/lib/types/catalog";
import { PageBody, PageHeader } from "@/components/layout/PageHeader";

function ModeChip({ mode }: { mode: string | null }) {
  if (!mode) return <span className="dash">–</span>;
  return mode === "advisory" ? (
    <ToneChip tone="neutral" label="advisory" title="Recorded but never holds a unit" />
  ) : mode === "block" ? (
    <ToneChip tone="info" label="block" title="A governance LABEL only in this build -- not yet enforced by the evaluation or publish path" />
  ) : (
    <ToneChip tone="info" label={mode} />
  );
}

function GatePicker({ gates, pickedId, onPick }: { gates: CatalogTaskGate[]; pickedId: string | null; onPick: (id: string) => void }) {
  const eligible = gates.filter((g) => g.mode === "advisory");
  return (
    <div className="tablewrap">
      <table>
        <caption className="skip">This pipeline&rsquo;s TASK_GATE items, advisory-mode gates selectable for promotion</caption>
        <thead>
          <tr>
            <th scope="col" aria-label="select" />
            <th scope="col">Task</th>
            <th scope="col">Datasets</th>
            <th scope="col">Current mode</th>
          </tr>
        </thead>
        <tbody>
          {gates.map((g) => (
            <tr key={g.id} className={pickedId === g.id ? "r" : undefined} aria-selected={pickedId === g.id}>
              <td>
                <input
                  type="radio"
                  name="gate"
                  aria-label={`Select gate ${g.task_id ?? g.id}`}
                  checked={pickedId === g.id}
                  disabled={g.mode !== "advisory"}
                  onChange={() => onPick(g.id)}
                />
              </td>
              <td className="mono">{g.task_id ?? "—"}</td>
              <td className="mono">{(g.datasets ?? []).join(", ") || "—"}</td>
              <td>
                <ModeChip mode={g.mode} />
              </td>
            </tr>
          ))}
          {!gates.length ? (
            <tr>
              <td colSpan={4}>
                <span className="dash">–</span> No TASK_GATE item on this pipeline.
              </td>
            </tr>
          ) : null}
        </tbody>
      </table>
      {gates.length && !eligible.length ? (
        <p className="hint" style={{ marginTop: 6 }}>
          No gate on this pipeline is in <span className="mono">advisory</span> mode. Only an advisory gate can be promoted to block here.
        </p>
      ) : null}
    </div>
  );
}

const RATIONALE_MIN = 10;

function PromoteView({ pipelineId }: { pipelineId: string }) {
  const { session } = useAuth();
  const catalog = useCatalog();
  const { data, error } = useLoad(catalog.catalogPipelines, [catalog]);
  const pl = useMemo(() => data?.groups.flatMap((g) => g.pipelines).find((p) => p.id === pipelineId) ?? null, [data, pipelineId]);
  const [pickedId, setPickedId] = useState<string | null>(null);
  const [rationale, setRationale] = useState("");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<{ cr_id: string; approvals_required: number } | null>(null);
  const [err, setErr] = useState<ApiError | null>(null);

  if (error) return <ErrorNote error={error} what={`pipeline ${pipelineId}`} />;
  if (!data) return <p className="muted">Loading&hellip;</p>;
  if (!pl)
    return (
      <div className="banner t-warn" role="alert">
        <strong>Unknown pipeline, or not in your scope.</strong> <span className="mono">{pipelineId}</span> is not listed in the Catalog for
        your authorities.
      </div>
    );

  const picked = pl.task_gates.find((g) => g.id === pickedId) ?? null;
  const canSubmit = !!picked && !!picked.task_id && rationale.trim().length >= RATIONALE_MIN && !busy && !result;

  async function submit() {
    if (!picked || !picked.task_id) return;
    setBusy(true);
    setErr(null);
    try {
      // F-11b fix: a TASK_GATE PUT item is a FULL-content replace, not a patch (registry._candidate), and
      // gov_item_valid requires every key (correlate/max_wait/on_timeout/reintroduce included) -- none of
      // which the Catalog row this page holds (CatalogTaskGate) ever carries. Sending a client-built {task_id,
      // datasets, mode, release_ceiling}-only object here was deterministically rejected as a malformed
      // TASK_GATE item before a CR was ever created. This page now calls a dedicated server route
      // (catalog_promote_gate, mirroring /catalog/wire's own pattern) that reads the gate's CURRENT full
      // content server-side and changes only `mode`: the browser sends nothing but the rationale.
      const r = await catalog.catalogPromoteGate(pipelineId, picked.task_id, { rationale: rationale.trim() });
      setResult({ cr_id: r.cr_id, approvals_required: r.approvals_required });
    } catch (e) {
      setErr(e instanceof ApiError ? e : new ApiError(0, { detail: String(e) }));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="twocol">
      <div>
        <section className="panel" aria-labelledby="promote-pipe-h">
          <h2 id="promote-pipe-h">
            {pipelineId} <span className="sub">{pl.task_gates.length} task gate(s)</span>
          </h2>
          <GatePicker gates={pl.task_gates} pickedId={pickedId} onPick={setPickedId} />
        </section>

        <section className="panel" aria-labelledby="promote-mode-h" style={{ borderColor: "var(--warn-bd)" }}>
          <h2 id="promote-mode-h">Mode change</h2>
          {picked ? (
            <dl className="kv">
              <dt>Current mode</dt>
              <dd>
                <ModeChip mode={picked.mode} />
              </dd>
              <dt>Proposed mode</dt>
              <dd>
                <ModeChip mode="block" />
              </dd>
            </dl>
          ) : (
            <p className="hint">Pick an advisory gate above to see the proposed change.</p>
          )}
          <div role="note" className="banner t-warn" style={{ marginTop: 8 }}>
            <strong>This promotes a label, not a behavior.</strong> In this build, a <span className="mono">block</span>-mode TASK_GATE has no
            behavioral effect anywhere in the system: the gate&rsquo;s <span className="mono">mode</span> field is read only by the
            database&rsquo;s own validity and lower-only checks, never by the evaluation or publish path. This page never implies the
            pipeline&rsquo;s actual release/hold behavior changes once this applies -- real block-mode enforcement is deferred, recorded as
            hardening.
          </div>
        </section>

        <section className="panel" aria-labelledby="promote-rat-h">
          <h2 id="promote-rat-h">Rationale</h2>
          <label htmlFor="pr-why">Reason (at least {RATIONALE_MIN} characters; cite the backtest report if one exists)</label>
          <textarea id="pr-why" value={rationale} onChange={(e) => setRationale(e.target.value)} disabled={!!result} spellCheck />
          <p className="hint" style={{ marginTop: 6 }}>
            Proposing never requires evidence. The database refuses the <strong>approval</strong> step instead (
            <span className="mono">dq_ctl.mode_promotion_refusal</span>, called by <span className="mono">gov_decide</span> /{" "}
            <span className="mono">gov_apply</span>): an APPROVE on this CR is refused, with a message starting &ldquo;promotion requires a
            linked backtest report&rdquo;, until a backtest report is linked to it in <span className="mono">dq_ctl.cr_evidence</span>. Run
            the <span className="mono">dqagent backtest</span> CLI over this change request&rsquo;s id (the demo&rsquo;s own live step, not
            this page) before a second person tries to approve it. If no evidence is linked, the change request&rsquo;s own Backtest panel
            states this honestly; the server&rsquo;s refusal itself surfaces there, on the real approve attempt, not pre-emptively blocked by
            this page.
          </p>
          <div className="row" style={{ marginTop: 8 }}>
            <button type="button" className="primary" onClick={() => void submit()} disabled={!canSubmit}>
              {busy ? "Proposing…" : "Propose promotion"}
            </button>
          </div>
          {err ? (
            <div role="alert" className="banner t-bad" style={{ marginTop: 8 }}>
              <strong>{err.status === 403 ? "Refused" : err.status === 422 ? "Rejected" : "Failed"}:</strong> {err.message}
            </div>
          ) : null}
        </section>
      </div>

      <div>
        <section className="panel" aria-labelledby="promote-result-h">
          <h2 id="promote-result-h">Change request</h2>
          {result ? (
            <>
              <p role="status">
                Proposed <Link className="mono" href={`/governance/${encodeURIComponent(result.cr_id)}`}>{result.cr_id}</Link>. Needs{" "}
                <strong>{result.approvals_required}</strong> approval(s) from different people before anything applies (a TASK_GATE mode
                change is never lower-only).
              </p>
              <p className="hint">
                Open the change request to see its Backtest panel: a linked report with a <span className="mono">delta_note</span> states
                plainly whether this backtest found a real delta, or -- for a mode-only change like this one -- that the delta is
                legitimately zero because block mode is not yet enforced.
              </p>
            </>
          ) : (
            <p className="hint">
              Nothing proposed yet. {session?.me.name ?? "You"} will not be able to approve your own proposal (two-person rule, enforced by
              the database).
            </p>
          )}
        </section>
      </div>
    </div>
  );
}

export default function Page() {
  const { session } = useAuth();
  const params = useParams<{ id: string }>();
  const id = decodeURIComponent(params.id ?? "");
  return (
    <>
      <PageHeader
        title="Promote to block"
        sub={`${id}: flip an advisory TASK_GATE's mode to block (step (a) only)`}
        crumb={<Link href="/pipelines/catalog">Catalog /</Link>}
      />
      <PageBody>{session ? <PromoteView pipelineId={id} /> : <SignIn />}</PageBody>
    </>
  );
}
