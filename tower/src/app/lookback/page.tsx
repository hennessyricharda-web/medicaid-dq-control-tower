"use client";
/**
 * Look-back (AUDIT §7, wireframe 08): 32 px rows grouped by revoked rule version and change request, a detail
 * pane (explanation, release facts, revoking CR, reassess) and a decision rail (Accept / Withdraw with
 * rationale and step-up, explanation_ref bound as in Part E).
 */
import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import { ApiError } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useLoad } from "@/lib/useLoad";
import { SignIn } from "@/components/SignIn";
import { ErrorNote } from "@/components/ErrorNote";
import { ToneChip } from "@/components/Chip";
import { VerdictFlow, type VerdictOption, type VerdictOutcome } from "@/components/VerdictFlow";
import { Explanation } from "@/components/Explanation";
import { refOf, SIM_RAN_EVENT } from "@/lib/partE";
import { encUnit, fmtNum, fmtTime, shortHash } from "@/lib/format";
import { orgLabel, useTenant } from "@/lib/tenant";
import { PREF, usePref, type Density } from "@/lib/prefs";
import type { Tone } from "@/lib/dispositions";
import type { LookbackItem, LookbackReassessment, LookbackVerdict } from "@/lib/types";
import { PageBody, PageHeader } from "@/components/layout/PageHeader";
import { FilterField, Seg, SelectChip, Toolbar } from "@/components/layout/Toolbar";
import { SplitPane, useSel } from "@/components/layout/SplitPane";
import { DataTable, MidTrunc, type Column, type Group } from "@/components/layout/DataTable";
import { DecisionRail } from "@/components/layout/DecisionRail";
import { EmptyState } from "@/components/layout/EmptyState";

const RATIONALE_MIN = 20;
const STATUS_TONE: Record<string, Tone> = { OPEN: "warn", ACCEPTED: "ok", WITHDRAWN: "bad" };
const STATUSES = ["OPEN", "ACCEPTED", "WITHDRAWN"] as const;

const OPTIONS: VerdictOption<LookbackVerdict>[] = [
  { value: "ACCEPTED", label: "Accept", warning: "Accept: the release stands. The unit stays in the released views." },
  {
    value: "WITHDRAWN",
    label: "Withdraw",
    danger: true,
    warning:
      "Withdraw removes this unit from the released views. Downstream consumers stop seeing its rows (a DENY through the release breaker). It is recorded and cannot be undone here.",
  },
];

function LiveChip({ live }: { live: boolean }) {
  return live ? (
    <ToneChip tone="warn" label="Live" title="Computed live: the unit is in the current released manifest" />
  ) : (
    <ToneChip tone="neutral" label="Not live" title="Computed live: not in the current released manifest" />
  );
}

function Reassessment({ r, live }: { r: LookbackReassessment; live: boolean }) {
  return (
    <div role={live ? "status" : undefined}>
      <strong>{live ? "Reassessment" : "Last reassessment"}</strong>{" "}
      <span className="hint">
        against approved current versions only (ruleset{" "}
        <code title={r.ruleset_version ?? ""}>{r.ruleset_version ? shortHash(r.ruleset_version, 10) : "none"}</code>), read-only
      </span>
      <div>
        Replacement version:{" "}
        {r.replacement_version ? (
          <code>{r.replacement_version}</code>
        ) : (
          <span className="muted">none approved yet: nothing replaces the revoked version today</span>
        )}
      </div>
      {r.failing_rules.length === 0 ? (
        <p style={{ margin: "4px 0 0" }}>
          <ToneChip tone="ok" label="No current rule fails" /> on this unit&rsquo;s released rows.
        </p>
      ) : (
        <ul style={{ margin: "4px 0 0", paddingLeft: 18 }}>
          {r.failing_rules.map((f) => (
            <li key={f.rule_id}>
              <span className="mono">{f.rule_id}</span>
              {f.version ? (
                <>
                  {" "}
                  version <code>{f.version}</code>
                </>
              ) : null}
              : {fmtNum(f.failed)} of {fmtNum(f.evaluated)} row(s) fail
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

/** Detail pane: explanation, release facts, the revoking CR and Reassess (read-only). */
function Detail({ it }: { it: LookbackItem }) {
  const { client } = useAuth();
  const [re, setRe] = useState<LookbackReassessment | null>(null);
  const [reErr, setReErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  async function reassess() {
    setBusy(true);
    setReErr(null);
    try {
      setRe(await client.lbReassess(it.item_id));
    } catch (e) {
      setReErr((e instanceof ApiError ? e : new ApiError(0, { detail: String(e) })).message);
    } finally {
      setBusy(false);
    }
  }
  const shown = re ?? it.reassessment;
  return (
    <>
      <div className="detail">
        <div className="dhead">
          <div className="row">
            <ToneChip tone={STATUS_TONE[it.status] ?? "warn"} label={it.status} title={`Look-back status ${it.status}`} />
            {it.in_live_manifest ? <ToneChip tone="warn" label="Live in released views" /> : <ToneChip tone="neutral" label="Not in released views" />}
            <span className="hint">{it.authority}</span>
          </div>
          <h2 id="lbd-h">
            <Link href={`/units/${encUnit(it.unit_key)}`} className="mono" title="Open the unit's decision card">
              {it.unit_key}
            </Link>
          </h2>
          <span className="hint">
            Item <span className="mono">{it.item_id}</span> · batch <span className="mono">{it.batch_id}</span>
            {it.released_at ? `, released ${fmtTime(it.released_at)}` : ""}
          </span>
        </div>
        <Explanation e={it.explanation} label={`Explanation for look-back item ${it.item_id}`} />
        <section className="sec" aria-labelledby="lbd-facts">
          <h3 id="lbd-facts">Release facts</h3>
          <dl className="kv">
            <dt>Released at</dt>
            <dd>{it.released_at ? fmtTime(it.released_at) : "–"}</dd>
            <dt>Node</dt>
            <dd className="mono">{it.node}</dd>
            <dt>Rule version</dt>
            <dd>
              <span className="mono">{it.rule_artifact}</span> @ <code title={it.rule_hash}>{shortHash(it.rule_hash, 12)}</code> (revoked)
            </dd>
            <dt>In live manifest</dt>
            <dd>
              live now: {it.in_live_manifest ? "yes" : "no"} · at open:{" "}
              {it.in_live_manifest_at_open === undefined ? "not reported" : it.in_live_manifest_at_open ? "yes" : "no"}
            </dd>
            <dt>Opened</dt>
            <dd>{fmtTime(it.at)}</dd>
            {it.resolution ? (
              <>
                <dt>Resolution</dt>
                <dd>{it.resolution}</dd>
              </>
            ) : null}
          </dl>
        </section>
        <section className="sec" aria-labelledby="lbd-cr">
          <h3 id="lbd-cr">Revoking change request</h3>
          <p style={{ margin: 0 }}>
            <Link className="mono" href={`/governance/${encodeURIComponent(it.cr_id)}`}>
              {it.cr_id}
            </Link>
          </p>
        </section>
        {reErr ? (
          <p role="alert" className="err">
            Reassess failed: {reErr}
          </p>
        ) : null}
        {shown ? (
          <section className="sec" aria-label="Reassessment">
            <Reassessment r={shown} live={!!re} />
          </section>
        ) : null}
      </div>
      <div className="actbar">
        <button type="button" onClick={() => void reassess()} disabled={busy} aria-label={`Reassess look-back item ${it.item_id}`}>
          {busy ? "Reassessing…" : "Reassess against current version"}
        </button>
      </div>
    </>
  );
}

/** Decision rail: who may resolve, and Accept / Withdraw with rationale and step-up. */
function Decide({ it, onChanged }: { it: LookbackItem; onChanged: () => void }) {
  const { client, session } = useAuth();
  const { tenant } = useTenant();
  const ao = tenant?.approver_org ?? null;
  const outsideOrg = !!ao && !!session?.me.org && session.me.org !== ao;
  const [outcome, setOutcome] = useState<VerdictOutcome | null>(null);
  const open = it.status === "OPEN";
  return (
    <>
      <dl className="kv">
        <dt>Requires</dt>
        <dd>{ao ? orgLabel(tenant, ao) : "any person in scope"}, step-up</dd>
        <dt>Scope</dt>
        <dd>Every one of the unit&rsquo;s authorities in your scope</dd>
        {session?.me.org_label ? (
          <>
            <dt>You</dt>
            <dd>{session.me.org_label}</dd>
          </>
        ) : null}
      </dl>
      <div className="callout t-info" role="note">
        <strong>Accept</strong> keeps the release. <strong>Withdraw</strong> removes the unit from released views. Withdrawing a unit the agent
        released on its own (L3 or L4) lowers the agent to L2 for the unit&rsquo;s authorities. Both need a step-up and a rationale.
      </div>
      {outcome ? (
        <p role="status" className={`banner t-${outcome.tone}`}>
          {outcome.title}. {outcome.body}
        </p>
      ) : null}
      {open ? (
        <div id={`lb-dec-${it.item_id}`}>
          {outsideOrg ? (
            <p className="banner t-warn" role="note">
              Look-back items are resolved by {orgLabel(tenant, ao)}. You are in {session?.me.org_label ?? session?.me.org}, so the server will refuse
              your decision.
            </p>
          ) : null}
          <VerdictFlow<LookbackVerdict>
            idPrefix={`lb-${it.item_id}`}
            what={`look-back item ${it.item_id}`}
            options={OPTIONS}
            rationaleMin={RATIONALE_MIN}
            reviewText="I reviewed the revoked rule version, this unit's release and the reassessment myself."
            forbiddenHint="The step-up is bound to another item or verdict; request a new challenge."
            challenge={(v) => client.lbChallenge(it.item_id, v, refOf(it.explanation))}
            decide={(v, t, r) => client.lbDecide(it.item_id, v, r, t)}
            onOutcome={(o) => {
              setOutcome(o);
              onChanged();
            }}
            onStale={(why) => {
              setOutcome({
                tone: "bad",
                title: "Refused (409)",
                body: `${why}.${/organisation/i.test(why) ? "" : " The item may already be resolved."}`,
              });
              onChanged();
            }}
            onFailed={onChanged}
          />
        </div>
      ) : (
        <p className="hint">This item is {it.status.toLowerCase()}; nothing to decide.</p>
      )}
    </>
  );
}

const DENSITY: { value: Density; label: string }[] = [
  { value: "compact", label: "Compact" },
  { value: "default", label: "Default" },
  { value: "comfortable", label: "Comfortable" },
];

function Lookback() {
  const { session, client } = useAuth();
  const { data, error, loading, reload } = useLoad(session ? client.lookback : null, [session?.token], 30_000);
  const [status, setStatus] = useState<string>("");
  const [live, setLive] = useState<"" | "LIVE" | "NOT_LIVE">("");
  const [version, setVersion] = useState<string>("");
  const [q, setQ] = useState("");
  const [density, setDensity] = usePref<Density>(PREF.density("lookback"), "default");
  const [sel, setSel] = useSel();

  useEffect(() => {
    const on = () => void reload();
    window.addEventListener(SIM_RAN_EVENT, on);
    return () => window.removeEventListener(SIM_RAN_EVENT, on);
  }, [reload]);

  const all = useMemo(() => data ?? [], [data]);
  const versionOf = (i: LookbackItem) => `${i.rule_artifact}@${i.rule_hash}`;
  const rows = useMemo(() => {
    const terms = q.toLowerCase().split(/\s+/).filter(Boolean);
    return all.filter((i) => {
      if (status && i.status !== status) return false;
      if (live && (live === "LIVE") !== i.in_live_manifest) return false;
      if (version && versionOf(i) !== version) return false;
      if (!terms.length) return true;
      const hay = [i.unit_key, i.batch_id, i.cr_id, i.rule_artifact, i.node, String(i.item_id)].join(" ").toLowerCase();
      return terms.every((t) => hay.includes(t));
    });
  }, [all, status, live, version, q]);

  const groups = useMemo<Group<LookbackItem>[]>(() => {
    const by = new Map<string, LookbackItem[]>();
    const decided: LookbackItem[] = [];
    for (const i of rows) {
      if (i.status !== "OPEN") {
        decided.push(i);
        continue;
      }
      const k = `${versionOf(i)}|${i.cr_id}`;
      const l = by.get(k) ?? [];
      l.push(i);
      by.set(k, l);
    }
    const out: Group<LookbackItem>[] = [...by.entries()].map(([k, l]) => {
      const f = l[0]!;
      return {
        id: k,
        label: (
          <>
            <span className="mono">{f.rule_artifact}</span> version <span className="mono">{shortHash(f.rule_hash, 8)}</span>{" "}
            <span className="muted">
              revoked by {f.cr_id}, {l.length} item(s)
            </span>
          </>
        ),
        rows: l,
      };
    });
    if (decided.length)
      out.push({
        id: "decided",
        label: (
          <>
            Decided <span className="muted">{decided.length} item(s)</span>
          </>
        ),
        rows: decided,
      });
    return out;
  }, [rows]);

  const ordered = useMemo(() => groups.flatMap((g) => g.rows), [groups]);
  const selected = ordered.find((i) => String(i.item_id) === sel) ?? null;
  useEffect(() => {
    if (!data) return;
    if ((!sel || !ordered.some((i) => String(i.item_id) === sel)) && ordered[0]) setSel(String(ordered[0].item_id));
  }, [data, sel, ordered, setSel]);

  const columns: Column<LookbackItem>[] = [
    { id: "unit", header: "Unit", cell: (i) => <MidTrunc value={i.unit_key} keep={26} />, title: (i) => i.unit_key },
    { id: "released", header: "Released", cell: (i) => (i.released_at ? fmtTime(i.released_at).slice(0, 16) : null), width: 128 },
    { id: "live", header: "Live now", cell: (i) => <LiveChip live={i.in_live_manifest} />, width: 84 },
    { id: "status", header: "Status", cell: (i) => <ToneChip tone={STATUS_TONE[i.status] ?? "warn"} label={i.status} />, width: 104 },
    { id: "batch", header: "Batch", cell: (i) => i.batch_id, mono: true, p: 2 },
    { id: "node", header: "Node", cell: (i) => i.node, mono: true, p: 3 },
  ];

  const counts = all.reduce<Record<string, number>>((m, i) => ({ ...m, [i.status]: (m[i.status] ?? 0) + 1 }), {});
  const versions = [...new Set(all.map(versionOf))].sort();
  const scope = session?.me.authorities.join(", ");
  const onChanged = useCallback(() => void reload(), [reload]);

  return (
    <>
      <PageHeader
        title="Look-back"
        subId="lb-h"
        sub={data ? `${rows.length} shown of ${data.length}; ${counts.OPEN ?? 0} released unit(s) to re-review after a revoked rule version` : "loading…"}
        scope={scope ? `Showing ${scope}, your scope` : undefined}
        actions={
          <button type="button" onClick={() => void reload()} disabled={loading}>
            {loading ? "Refreshing…" : "Refresh"}
          </button>
        }
      />
      <Toolbar>
        <span className={`fld${status ? " on" : ""}`}>
          <label htmlFor="lb-status" style={{ margin: 0, fontWeight: 400, fontSize: "inherit", color: "inherit" }}>
            Status
          </label>
          <select id="lb-status" value={status} onChange={(e) => setStatus(e.target.value)}>
            <option value="">All</option>
            {STATUSES.map((s) => (
              <option key={s} value={s}>
                {s} ({counts[s] ?? 0})
              </option>
            ))}
          </select>
        </span>
        <SelectChip
          id="lb-live"
          label="Live in manifest"
          value={live}
          options={[
            { value: "LIVE", label: "Live" },
            { value: "NOT_LIVE", label: "Not live" },
          ]}
          onChange={setLive}
        />
        <SelectChip id="lb-ver" label="Revoked version" value={version} options={versions.map((v) => ({ value: v, label: v.replace(/@(.{8}).*$/, " @ $1") }))} onChange={setVersion} />
        <span className="sp" />
        <FilterField value={q} onChange={setQ} placeholder="Filter by unit or batch" label="Filter look-back items" />
        <Seg label="Row density" value={density} options={DENSITY} onChange={setDensity} />
      </Toolbar>
      {error ? (
        <div style={{ padding: "8px 16px 0" }}>
          <ErrorNote error={error} what="look-back items" />
        </div>
      ) : null}
      {data && data.length === 0 ? (
        <EmptyState title="Nothing to re-review." line="No release was made under a rule version that has since been revoked, in your scope." />
      ) : (
        <div className="sp">
          <SplitPane
            id="lookback"
            defaultSize={420}
            label={["Look-back items", "Item detail"]}
            first={
              <>
                <DataTable
                  columns={columns}
                  groups={groups}
                  rowKey={(i) => String(i.item_id)}
                  selected={sel}
                  onSelect={setSel}
                  density={density}
                  caption="Releases made under a revoked rule version, grouped by version and change request"
                  empty={
                    data ? (
                      <EmptyState inline title={`No look-back items${status ? ` with status ${status}` : ""} match these filters.`} />
                    ) : (
                      <span className="hint">Loading…</span>
                    )
                  }
                  primary
                />
                <div className="statusfoot">
                  <span>
                    {rows.length} of {all.length} item(s)
                  </span>
                  <span className="sp" />
                  <span>
                    <span className="kbd">j</span> <span className="kbd">k</span> move
                  </span>
                  <span>
                    <span className="kbd">/</span> filter
                  </span>
                </div>
              </>
            }
            second={selected ? <Detail key={selected.item_id} it={selected} /> : <EmptyState inline title="Nothing selected." />}
          />
          <DecisionRail width={300} title="Decide" label="Look-back decision">
            {selected ? <Decide key={selected.item_id} it={selected} onChanged={onChanged} /> : <p className="hint">Select an item.</p>}
          </DecisionRail>
        </div>
      )}
    </>
  );
}

export default function Page() {
  const { session } = useAuth();
  if (!session)
    return (
      <>
        <PageHeader title="Look-back" />
        <PageBody>
          <SignIn />
        </PageBody>
      </>
    );
  return <Lookback />;
}
