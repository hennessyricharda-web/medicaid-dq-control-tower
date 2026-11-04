"use client";
/**
 * Decision queue (AUDIT §1, wireframe 01): a grouped dense list of clusters and a 440 px detail pane.
 * Veto-window clusters sit in their own group at the top. Unit keys live in the detail pane, never in the row.
 */
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useState } from "react";
import { useAuth } from "@/lib/auth";
import { useLoad } from "@/lib/useLoad";
import { SignIn } from "@/components/SignIn";
import { PageBody, PageHeader } from "@/components/layout/PageHeader";
import { DispositionChip, StateChip, ToneChip } from "@/components/Chip";
import { ErrorNote } from "@/components/ErrorNote";
import { Explanation } from "@/components/Explanation";
import { GenericTable } from "@/components/GenericTable";
import { TierBadge, VetoCountdown } from "@/components/Autonomy";
import { describeMinutes, encUnit, fmtTime, slaMinutesFromNow } from "@/lib/format";
import { ApiError } from "@/lib/api";
import { SIM_RAN_EVENT } from "@/lib/partE";
import { PREF, usePref, type Density } from "@/lib/prefs";
import { layerOf, normMode, whyFromCluster, whyFromTrace } from "@/lib/why";
import type { DecisionCard, QueueCluster } from "@/lib/types";
import { FilterField, Seg, SelectChip, Toolbar } from "@/components/layout/Toolbar";
import { SplitPane, useSel, useUrlParam } from "@/components/layout/SplitPane";
import { DataTable, type Column, type Group } from "@/components/layout/DataTable";
import { EmptyState } from "@/components/layout/EmptyState";
import { useShellData } from "@/components/layout/ShellData";

const DENSITY: { value: Density; label: string }[] = [
  { value: "compact", label: "Compact" },
  { value: "default", label: "Default" },
  { value: "comfortable", label: "Comfortable" },
];

function vetoOpen(c: QueueCluster, now: number): boolean {
  if (!c.veto_deadline) return false;
  const t = Date.parse(c.veto_deadline);
  return !Number.isNaN(t) && t > now;
}

function SlaCell({ c }: { c: QueueCluster }) {
  if (c.veto_deadline) return <VetoCountdown deadline={c.veto_deadline} />;
  const m = slaMinutesFromNow(c.sla_due);
  if (m !== null && m < 0) return <ToneChip tone="bad" label={describeMinutes(m)} title={`SLA due ${fmtTime(c.sla_due)}`} />;
  return (
    <span className="chip t-neutral" title={`SLA due ${fmtTime(c.sla_due)}`}>
      {describeMinutes(m)}
    </span>
  );
}

function Node({ node }: { node: string }) {
  const layer = layerOf(node);
  return (
    <span className="mono" title={layer ? `${layer} layer: ${node}` : node}>
      <span className={`tick ${layer ?? ""}`} aria-hidden="true" />
      {layer ? node.slice(layer.length + 1) : node}
    </span>
  );
}

/** The cluster's first unit, read for its explanation, mode trace, ABC and dependencies (the queue row carries none). */
function useLeadCard(unit: string | null) {
  const { client } = useAuth();
  const [card, setCard] = useState<DecisionCard | null>(null);
  const [err, setErr] = useState<ApiError | null>(null);
  useEffect(() => {
    let live = true;
    setCard(null);
    setErr(null);
    if (!unit) return;
    client.card(unit).then(
      (c) => live && setCard(c),
      (e: unknown) => live && setErr(e instanceof ApiError ? e : new ApiError(0, { detail: String(e) })),
    );
    return () => {
      live = false;
    };
  }, [unit, client]);
  return { card, err };
}

function Detail({ c, onWhy }: { c: QueueCluster; onWhy: (id: string, why: string) => void }) {
  const lead = c.units[0] ?? null;
  const { card, err } = useLeadCard(lead);
  const traceWhy = card ? whyFromTrace(card.mode_trace, card.required_mode) : null;
  useEffect(() => {
    if (traceWhy) onWhy(c.cluster_id, traceWhy);
  }, [traceWhy, c.cluster_id, onWhy]);
  const open = vetoOpen(c, Date.now());
  const why = traceWhy ?? whyFromCluster(c);
  return (
    <>
      <div className="detail" aria-labelledby="qd-h">
        <div className="dhead">
          <div className="row">
            <DispositionChip value={c.worst_disposition} />
            <span className="chip t-neutral" title="Required mode">
              {normMode(c.required_mode)}
            </span>
            <TierBadge tier={c.tier} />
            <span className="hint">{c.authority}</span>
          </div>
          <h2 id="qd-h">{c.node}</h2>
          <span className="hint">
            {card ? (
              <>
                Batch <span className="mono">{card.batch_id}</span>,{" "}
              </>
            ) : null}
            {c.unit_count} unit(s) · cluster <span className="mono">{c.cluster_id}</span> · SLA {fmtTime(c.sla_due)}
          </span>
        </div>
        {c.veto_deadline ? (
          <div className={`callout ${open ? "t-warn" : "t-neutral"}`} role="note">
            <strong>
              Veto window closes at {fmtTime(c.veto_deadline)} <VetoCountdown deadline={c.veto_deadline} />
            </strong>{" "}
            When it closes the agent releases these units unless a person vetoes. A veto holds them for a person and lowers the agent to L2 for{" "}
            {c.authority}.
          </div>
        ) : null}
        {err ? <ErrorNote error={err} what={`unit ${lead}`} /> : null}
        {card ? (
          <section aria-labelledby="qd-expl">
            <h3 id="qd-expl" className="sr">
              In plain language
            </h3>
            {c.unit_count > 1 ? <p className="hint" style={{ margin: "0 0 4px" }}>First unit of {c.unit_count}:</p> : null}
            <Explanation e={card.explanation} label={`Explanation for unit ${card.unit_key}`} />
          </section>
        ) : !err && lead ? (
          <p className="hint" aria-live="polite">
            Loading the lead unit&hellip;
          </p>
        ) : null}
        <section className="sec" aria-labelledby="qd-why">
          <h3 id="qd-why">
            {c.veto_deadline ? "Why this mode applies" : "Why held"} <span className="muted">{why}</span>
          </h3>
          {card?.mode_trace?.length ? (
            <ol className="mono" style={{ margin: 0, paddingLeft: 22, fontSize: "var(--t-sm)" }}>
              {card.mode_trace.map((t, i) => (
                <li key={i}>{t}</li>
              ))}
            </ol>
          ) : card ? (
            <p className="muted">No mode trace was recorded for this hold.</p>
          ) : null}
          <dl className="kv">
            <dt>Rules</dt>
            <dd className="mono">{c.rule_ids.join(", ")}</dd>
            <dt>Failure signature</dt>
            <dd>{c.failure_signature}</dd>
          </dl>
        </section>
        <section className="sec" aria-labelledby="qd-units">
          <h3 id="qd-units">
            Units <span className="muted">{c.unit_count}</span>
          </h3>
          <ul style={{ margin: 0, paddingLeft: 18 }}>
            {c.units.map((u) => (
              <li key={u} style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                <Link href={`/units/${encUnit(u)}`} className="mono" title={u}>
                  {u}
                </Link>
                {card && u === card.unit_key ? (
                  <>
                    {" "}
                    <StateChip value={card.state} />
                  </>
                ) : null}
              </li>
            ))}
          </ul>
        </section>
        {card ? (
          <section className="sec" aria-labelledby="qd-bal">
            <h3 id="qd-bal">Balance and dependencies</h3>
            <GenericTable rows={card.abc_before_after} caption="ABC before and after the proposed action" empty="No ABC rows supplied for this unit." />
            {card.dependencies.length ? (
              <dl className="kv">
                {card.dependencies.map((d, i) => (
                  <div key={i} style={{ display: "contents" }}>
                    <dt>{d.domain}</dt>
                    <dd>
                      <span className="mono">{d.policy}</span> <StateChip value={d.state} />
                    </dd>
                  </div>
                ))}
              </dl>
            ) : (
              <p className="muted" style={{ margin: 0 }}>
                No declared dependencies.
              </p>
            )}
          </section>
        ) : null}
      </div>
      <div className="actbar">
        {open && lead ? (
          <Link className="btn danger" href={`/units/${encUnit(lead)}#veto-h`} style={{ textDecoration: "none" }}>
            Veto release…
          </Link>
        ) : null}
        {lead ? (
          <Link className="btn pri" href={`/units/${encUnit(lead)}`} style={{ textDecoration: "none" }}>
            Open decision card
          </Link>
        ) : null}
      </div>
    </>
  );
}

function Queue() {
  const { session, client } = useAuth();
  const router = useRouter();
  const { demo, operator, summary, runNextNight, simBusy } = useShellData();
  const { data, error, loading, reload } = useLoad(session ? client.queue : null, [session?.token], 30_000);
  const [asc, setAsc] = useState(true);
  const urlQ = useUrlParam("q");
  const [q, setQ] = useState("");
  const [mode, setMode] = useState<string>("");
  const [whyF, setWhyF] = useState<string>("");
  const [node, setNode] = useState<string>("");
  const [density, setDensity] = usePref<Density>(PREF.density("queue"), "default");
  const [sel, setSel] = useSel();
  const [traceWhy, setTraceWhy] = useState<Record<string, string>>({});
  const onWhy = useCallback((id: string, why: string) => setTraceWhy((m) => (m[id] === why ? m : { ...m, [id]: why })), []);
  const now = Date.now();

  useEffect(() => {
    if (urlQ !== null) setQ(urlQ);
  }, [urlQ]);
  useEffect(() => {
    const on = () => void reload();
    window.addEventListener(SIM_RAN_EVENT, on);
    return () => window.removeEventListener(SIM_RAN_EVENT, on);
  }, [reload]);

  const whyOf = useCallback((c: QueueCluster) => traceWhy[c.cluster_id] ?? whyFromCluster(c), [traceWhy]);
  const all = useMemo(() => data ?? [], [data]);
  const filtered = useMemo(() => {
    const terms = q.toLowerCase().split(/\s+/).filter(Boolean);
    return all.filter((c) => {
      if (mode && normMode(c.required_mode) !== mode) return false;
      if (node && c.node !== node) return false;
      if (whyF && whyOf(c) !== whyF) return false;
      if (!terms.length) return true;
      const hay = [c.node, c.cluster_id, c.failure_signature, c.authority, ...c.rule_ids, ...c.units].join(" ").toLowerCase();
      return terms.every((t) => hay.includes(t));
    });
  }, [all, q, mode, node, whyF, whyOf]);

  const groups = useMemo<Group<QueueCluster>[]>(() => {
    const t = (c: QueueCluster) => {
      const v = Date.parse(c.sla_due);
      return Number.isNaN(v) ? Number.POSITIVE_INFINITY : v;
    };
    const sort = (l: QueueCluster[]) => l.sort((a, b) => (asc ? t(a) - t(b) : t(b) - t(a)));
    const veto = sort(filtered.filter((c) => vetoOpen(c, now)));
    const by = new Map<string, QueueCluster[]>();
    for (const c of filtered) {
      if (vetoOpen(c, now)) continue;
      const l = by.get(c.authority) ?? [];
      l.push(c);
      by.set(c.authority, l);
    }
    const out: Group<QueueCluster>[] = [];
    if (veto.length)
      out.push({
        id: "veto",
        label: (
          <>
            Veto window open <span className="muted">the agent releases these when the window closes unless a person vetoes</span>
          </>
        ),
        rows: veto,
      });
    for (const [auth, list] of [...by.entries()].sort(([a], [b]) => a.localeCompare(b))) {
      sort(list);
      out.push({
        id: `held-${auth}`,
        label: (
          <>
            Held for a person · {auth}{" "}
            <span className="muted">
              {list.reduce((n, c) => n + c.unit_count, 0)} unit(s) in {list.length} cluster(s)
            </span>
          </>
        ),
        rows: list,
      });
    }
    return out;
    // `now` changes every render; the grouping only needs the data and the controls.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filtered, asc]);

  const ordered = useMemo(() => groups.flatMap((g) => g.rows), [groups]);
  const selected = ordered.find((c) => c.cluster_id === sel) ?? null;
  // Select the first row when nothing (or something no longer listed) is selected.
  useEffect(() => {
    if (!data) return;
    if ((!sel || !ordered.some((c) => c.cluster_id === sel)) && ordered[0]) setSel(ordered[0].cluster_id);
  }, [data, sel, ordered, setSel]);

  const columns: Column<QueueCluster>[] = [
    { id: "sla", header: <span aria-sort={asc ? "ascending" : "descending"}>SLA</span>, cell: (c) => <SlaCell c={c} />, width: 124 },
    { id: "worst", header: "Worst", cell: (c) => <DispositionChip value={c.worst_disposition} />, width: 136 },
    { id: "mode", header: "Mode", cell: (c) => normMode(c.required_mode), mono: true, width: 80 },
    { id: "why", header: "Why", cell: (c) => whyOf(c), width: 150, title: (c) => (traceWhy[c.cluster_id] ? "From the mode trace" : "From the row's facts; select for the trace") },
    { id: "node", header: "Node", cell: (c) => <Node node={c.node} />, title: (c) => c.node },
    { id: "rule", header: "Rule", cell: (c) => c.rule_ids.join(", "), mono: true, title: (c) => c.rule_ids.join(", ") },
    { id: "sig", header: "Failure signature", cell: (c) => c.failure_signature, p: 2, title: (c) => c.failure_signature },
    { id: "tier", header: "Agent tier", cell: (c) => <TierBadge tier={c.tier} />, p: 3 },
    { id: "units", header: "Units", cell: (c) => c.unit_count, num: true, width: 64 },
  ];

  const units = all.reduce((n, c) => n + c.unit_count, 0);
  const vetoUnits = all.filter((c) => vetoOpen(c, now)).reduce((n, c) => n + c.unit_count, 0);
  const modes = [...new Set(all.map((c) => normMode(c.required_mode)))].sort();
  const whys = [...new Set(all.map(whyOf))].sort();
  const nodes = [...new Set(all.map((c) => c.node))].sort();
  const scope = session?.me.authorities.join(", ");
  const openCard = (id: string) => {
    const c = ordered.find((x) => x.cluster_id === id);
    if (c?.units[0]) router.push(`/units/${encUnit(c.units[0])}`);
  };

  return (
    <>
      <PageHeader
        title="Decision queue"
        sub={
          data
            ? `${units - vetoUnits} unit(s) held in ${all.length - all.filter((c) => vetoOpen(c, now)).length} cluster(s)${vetoUnits ? `, plus ${vetoUnits} in a veto window` : ""}`
            : "Loading…"
        }
        scope={scope ? `Showing ${scope}, your scope` : undefined}
        actions={
          <button type="button" onClick={() => void reload()} disabled={loading}>
            {loading ? "Refreshing…" : "Refresh"}
          </button>
        }
      />
      <Toolbar>
        <FilterField value={q} onChange={setQ} placeholder="Filter by node, rule or unit" label="Filter clusters" />
        <SelectChip id="f-mode" label="Mode" value={mode} options={modes.map((m) => ({ value: m, label: m }))} onChange={setMode} />
        <SelectChip id="f-why" label="Why held" value={whyF} options={whys.map((m) => ({ value: m, label: m }))} onChange={setWhyF} />
        <SelectChip id="f-node" label="Node" value={node} options={nodes.map((m) => ({ value: m, label: m }))} onChange={setNode} />
        <span className="sp" />
        <button type="button" className="fld" onClick={() => setAsc((v) => !v)} aria-label={`Sort by SLA, currently ${asc ? "soonest first" : "latest first"}`}>
          Sort <b>SLA, {asc ? "soonest first" : "latest first"}</b>
        </button>
        <Seg label="Row density" value={density} options={DENSITY} onChange={setDensity} />
      </Toolbar>
      {error ? (
        <div style={{ padding: "8px 16px 0" }}>
          <ErrorNote error={error} what="the queue" />
        </div>
      ) : null}
      {data && data.length === 0 ? (
        <EmptyState
          title="No decisions due."
          line="Nothing in your scope is waiting on a person."
          action={
            demo && operator && summary?.sim?.queued ? (
              <button type="button" className="primary" onClick={() => void runNextNight()} disabled={simBusy}>
                Run next night
              </button>
            ) : undefined
          }
        />
      ) : (
        <SplitPane
          id="queue"
          defaultSize={440}
          label={["Clusters", "Cluster detail"]}
          drawerable
          first={
            <>
              <DataTable
                columns={columns}
                groups={groups}
                rowKey={(c) => c.cluster_id}
                selected={sel}
                onSelect={setSel}
                onOpen={openCard}
                density={density}
                caption="Clusters waiting on a person, grouped, sorted by SLA"
                rowClass={(c) => {
                  const m = slaMinutesFromNow(c.sla_due);
                  return !c.veto_deadline && m !== null && m < 0 ? "alarm" : undefined;
                }}
                empty={
                  data ? (
                    <EmptyState
                      inline
                      title="No clusters match these filters."
                      action={
                        <button
                          type="button"
                          onClick={() => {
                            setQ("");
                            setMode("");
                            setWhyF("");
                            setNode("");
                          }}
                        >
                          Clear filters
                        </button>
                      }
                    />
                  ) : (
                    <span className="hint">Loading…</span>
                  )
                }
                primary
              />
              <div className="statusfoot">
                <span>
                  {filtered.length} of {all.length} cluster(s)
                </span>
                <span className="sp" />
                <span>
                  <span className="kbd">j</span> <span className="kbd">k</span> move
                </span>
                <span>
                  <span className="kbd">Enter</span> open card
                </span>
                <span>
                  <span className="kbd">/</span> filter
                </span>
              </div>
            </>
          }
          second={selected ? <Detail key={selected.cluster_id} c={selected} onWhy={onWhy} /> : <EmptyState inline title="Nothing selected." line="Pick a cluster to see its units and why it is held." />}
        />
      )}
    </>
  );
}

export default function Page() {
  const { session } = useAuth();
  if (!session)
    return (
      <>
        <PageHeader title="Decision queue" />
        <PageBody>
          <SignIn />
        </PageBody>
      </>
    );
  return <Queue />;
}
