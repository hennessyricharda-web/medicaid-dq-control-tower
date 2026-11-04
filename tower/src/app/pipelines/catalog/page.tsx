"use client";
/**
 * Catalog (K2c, PFP :834): every pipeline the caller may see (HARNESS or WIRED), grouped by provenance, with a
 * dependency-order strip. Table first (DEC-020, the accessible default). K2c-1 (RESOLVED): this is a NEW page,
 * coexisting with the existing flat `/pipelines` map -- that page is untouched.
 *
 * Row selection opens the nodes drawer in the detail pane: the Effective tier column (K2a/K2b's ceiling-aware
 * `autonomy_tier_for`), honestly split into its two row kinds -- a TASK_GATE-governed dataset (carries the gate
 * and the tier) and a discovered dbt-manifest-only node (carries only its layer, never a fabricated tier).
 */
import Link from "next/link";
import { useMemo } from "react";
import { useAuth } from "@/lib/auth";
import { useCatalog } from "@/lib/api/catalog";
import { useLoad } from "@/lib/useLoad";
import { SignIn } from "@/components/SignIn";
import { ErrorNote } from "@/components/ErrorNote";
import { StateChip, ToneChip } from "@/components/Chip";
import { shortHash } from "@/lib/format";
import type { CatalogNode, CatalogPipeline } from "@/lib/types/catalog";
import { PageBody, PageHeader } from "@/components/layout/PageHeader";
import { DataTable, type Column, type Group } from "@/components/layout/DataTable";
import { SplitPane, useSel } from "@/components/layout/SplitPane";
import { EmptyState } from "@/components/layout/EmptyState";

function ProvenanceChip({ p }: { p: string }) {
  return p === "HARNESS" ? (
    <ToneChip tone="neutral" label="HARNESS" title="A pipeline the genesis configuration already knows" />
  ) : p === "WIRED" ? (
    <ToneChip tone="info" label="WIRED" title="A pipeline introduced later, through the two-person wiring flow" />
  ) : (
    <ToneChip tone="warn" label={`UNKNOWN: ${p}`} />
  );
}

function NodeStateChip({ node }: { node: CatalogPipeline["nodes"][number] }) {
  return <StateChip value={node.state} />;
}

/** The dependency-order strip: `order[]` is the whole state's own global order, the same convention
 *  `PipelineProfile.order` already uses -- context for every row, not just one pipeline's position. */
function OrderStrip({ order }: { order: string[] }) {
  if (!order.length) return null;
  return (
    <section className="panel" aria-labelledby="cat-order-h" style={{ marginBottom: 12 }}>
      <h2 id="cat-order-h" className="sr">
        Pipeline order
      </h2>
      <ol className="row" style={{ listStyle: "none", padding: 0, margin: 0, flexWrap: "wrap" }}>
        {order.map((id, i) => (
          <li key={id} className="mono">
            {i + 1}. {id}
            {i < order.length - 1 ? " →" : ""}
          </li>
        ))}
      </ol>
    </section>
  );
}

function DatasetGovCell({ node }: { node: CatalogNode }) {
  if (node.layer_source !== "TASK_GATE") {
    return (
      <span className="hint" title="Discovered from a dbt manifest; this codebase keeps no derivable link between a manifest node and a governed dataset">
        discovered, not evaluated
      </span>
    );
  }
  const g = node.datasets[0];
  if (!g || g.effective_tier === null) return <span className="muted">no authority resolved</span>;
  return (
    <span className="row" style={{ gap: 6 }}>
      <ToneChip tone="info" label={g.effective_tier} title="Effective tier: the ceiling-aware autonomy_tier_for" />
      {g.agent_tier && g.agent_tier !== g.effective_tier ? (
        <span className="hint" title="The plain RELEASE tier before this gate's own ceiling was applied">
          (agent tier {g.agent_tier})
        </span>
      ) : null}
    </span>
  );
}

function NodesDrawer({ pipelineId }: { pipelineId: string }) {
  const catalog = useCatalog();
  const { data, error } = useLoad(() => catalog.catalogPipelineNodes(pipelineId), [catalog, pipelineId]);
  const cols: Column<CatalogNode>[] = useMemo(
    () => [
      { id: "node", header: "Node", cell: (n) => n.node, mono: true },
      { id: "layer", header: "Layer", cell: (n) => n.layer, width: 110 },
      { id: "layer_source", header: "Layer source", cell: (n) => n.layer_source, width: 130, p: 2 },
      { id: "gate", header: "Gate", cell: (n) => (n.gate ? <span className="mono">{n.gate.mode}</span> : <span className="dash">–</span>), width: 110 },
      { id: "eval", header: "Evaluation", cell: (n) => (n.evaluation ? <StateChip value={n.evaluation.state} /> : <span className="dash">–</span>), width: 120 },
      { id: "tier", header: "Effective tier", cell: (n) => <DatasetGovCell node={n} /> },
      { id: "introduced", header: "Introduced by", cell: (n) => (n.datasets[0]?.introduced_by ? <span className="mono hint">{n.datasets[0].introduced_by}</span> : <span className="dash">–</span>), p: 3 },
    ],
    [],
  );
  if (error) return <ErrorNote error={error} what={`the nodes of ${pipelineId}`} />;
  if (!data) return <p className="hint">Loading nodes&hellip;</p>;
  const gated = data.nodes.filter((n) => n.layer_source === "TASK_GATE");
  const discovered = data.nodes.filter((n) => n.layer_source !== "TASK_GATE");
  return (
    <div className="tablewrap">
      <table>
        <caption className="skip">{`Nodes of ${pipelineId}: TASK_GATE-governed datasets and any discovered dbt-manifest nodes`}</caption>
        <thead>
          <tr>
            {cols.map((c) => (
              <th key={c.id} scope="col">
                {c.header}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {data.nodes.map((n, i) => (
            <tr key={`${n.node}:${i}`}>
              {cols.map((c) => (
                <td key={c.id} className={c.mono ? "mono" : undefined}>
                  {c.cell(n)}
                </td>
              ))}
            </tr>
          ))}
          {!data.nodes.length ? (
            <tr>
              <td colSpan={cols.length} style={{ whiteSpace: "normal" }}>
                <span className="dash">–</span> No node resolved for this pipeline.
              </td>
            </tr>
          ) : null}
        </tbody>
      </table>
      <p className="hint" style={{ marginTop: 8 }}>
        {gated.length} TASK_GATE-governed dataset(s); {discovered.length} discovered dbt-manifest node(s). The two
        kinds are never joined: this codebase keeps no per-node correspondence between a governed dataset and a
        dbt MODEL node.
      </p>
    </div>
  );
}

function PipelineDetail({ pl }: { pl: CatalogPipeline }) {
  return (
    <>
      <section className="panel" aria-labelledby="cat-detail-h">
        <h2 id="cat-detail-h" className="mono">
          {pl.id}
        </h2>
        <p className="hint">
          <ProvenanceChip p={pl.provenance} /> · {pl.platform ?? "—"} · {pl.domains.join(", ") || "no domain"} ·{" "}
          {pl.governance.authorities.join(", ") || "no authority resolved"}
        </p>
        {pl.source ? (
          <dl className="kv">
            {Object.entries(pl.source).map(([k, v]) => (
              <div key={k} style={{ display: "contents" }}>
                <dt className="mono">{k}</dt>
                <dd className="mono">{typeof v === "object" ? JSON.stringify(v) : String(v)}</dd>
              </div>
            ))}
          </dl>
        ) : (
          <p className="hint">No PIPELINE artifact on record for this id (a HARNESS pipeline that predates K2c).</p>
        )}
        <p className="hint">
          {pl.last_night ? (
            <>
              Last night: <Link className="mono" href={`/runs/${encodeURIComponent(pl.last_night.trace_id)}`}>{pl.last_night.trace_id}</Link>
            </>
          ) : (
            "No recorded PIPELINE_RUN for this pipeline yet."
          )}
        </p>
        <Link href={`/pipelines/${encodeURIComponent(pl.id)}/profile`}>Agent profile</Link>
        {" · "}
        <Link href={`/pipelines/graph?id=${encodeURIComponent(pl.id)}`}>Pipelines graph</Link>
      </section>
      <section className="panel" aria-labelledby="cat-gates-h">
        <h2 id="cat-gates-h">
          Task gates <span className="sub">{pl.task_gates.length}</span>
        </h2>
        <div className="tablewrap">
          <table>
            <caption className="skip">TASK_GATE items naming this pipeline</caption>
            <thead>
              <tr>
                <th scope="col">Task</th>
                <th scope="col">Datasets</th>
                <th scope="col">Mode</th>
                <th scope="col">Release ceiling</th>
              </tr>
            </thead>
            <tbody>
              {pl.task_gates.map((g) => (
                <tr key={g.id}>
                  <td className="mono">{g.task_id ?? "—"}</td>
                  <td className="mono">{(g.datasets ?? []).join(", ") || "—"}</td>
                  <td>{g.mode === "advisory" ? <ToneChip tone="neutral" label="advisory" /> : <ToneChip tone="info" label={g.mode ?? "—"} />}</td>
                  <td className="mono">{g.release_ceiling ?? "—"}</td>
                </tr>
              ))}
              {!pl.task_gates.length ? (
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
      <section className="panel" aria-labelledby="cat-nodes-h">
        <h2 id="cat-nodes-h">Nodes</h2>
        <NodesDrawer pipelineId={pl.id} />
      </section>
    </>
  );
}

function CatalogView() {
  const { session } = useAuth();
  const catalog = useCatalog();
  const { data, error, loading, reload } = useLoad(session ? catalog.catalogPipelines : null, [session?.token], 30_000);
  const [sel, setSel] = useSel();

  const rows: CatalogPipeline[] = useMemo(() => data?.groups.flatMap((g) => g.pipelines) ?? [], [data]);
  const groups: Group<CatalogPipeline>[] = useMemo(
    () => (data?.groups ?? []).map((g) => ({ id: g.provenance, label: `${g.provenance} · ${g.pipelines.length} pipeline(s)`, rows: g.pipelines })),
    [data],
  );
  const selected = rows.find((r) => r.id === sel) ?? null;

  const cols: Column<CatalogPipeline>[] = useMemo(
    () => [
      { id: "id", header: "Pipeline", cell: (p) => p.id, mono: true },
      { id: "prov", header: "Provenance", cell: (p) => <ProvenanceChip p={p.provenance} />, width: 110 },
      { id: "platform", header: "Platform", cell: (p) => p.platform ?? "—", width: 100, p: 2 },
      { id: "domains", header: "Domains", cell: (p) => p.domains.join(", "), p: 2 },
      { id: "nodes", header: "Nodes", cell: (p) => <span className="row" style={{ gap: 4 }}>{p.nodes.slice(0, 4).map((n) => <NodeStateChip key={n.node} node={n} />)}{p.nodes.length > 4 ? <span className="hint">+{p.nodes.length - 4}</span> : null}</span> },
      { id: "gates", header: "Task gates", cell: (p) => p.task_gates.length, num: true, width: 90 },
      { id: "auth", header: "Authorities", cell: (p) => p.governance.authorities.join(", ") || "—", p: 3 },
      { id: "night", header: "Last night", cell: (p) => (p.last_night ? <span className="mono">{shortHash(p.last_night.trace_id, 18)}</span> : <span className="dash">–</span>), p: 3 },
      {
        id: "graph",
        header: "Graph",
        width: 70,
        cell: (p) => (
          <Link
            href={`/pipelines/graph?id=${encodeURIComponent(p.id)}`}
            onClick={(e) => e.stopPropagation()}
            aria-label={`Open the pipelines graph for ${p.id}`}
          >
            Graph
          </Link>
        ),
      },
    ],
    [],
  );

  if (error) return <ErrorNote error={error} what="the catalog" />;

  return (
    <>
      <PageHeader
        title="Catalog"
        sub={data ? `${rows.length} pipeline(s) in your scope` : "loading…"}
        scope={session?.me.authorities.length ? `Showing your scope: ${session.me.authorities.join(", ")}` : undefined}
        actions={
          <>
            <Link href="/pipelines/wire" className="primary" role="button">
              Wire a pipeline
            </Link>
            <button type="button" onClick={() => void reload()} disabled={loading}>
              {loading ? "Refreshing…" : "Refresh"}
            </button>
          </>
        }
      />
      <PageBody>
        {data ? <OrderStrip order={data.order} /> : null}
        {data && rows.length === 0 ? (
          <EmptyState title="No pipeline is in your scope." line="Ask your duty administrator for release duty on at least one authority this catalog governs." />
        ) : (
          <SplitPane
            id="catalog"
            defaultSize={460}
            label={["Pipelines", "Pipeline detail"]}
            drawerable
            first={
              <DataTable
                columns={cols}
                groups={groups}
                rowKey={(p) => p.id}
                selected={sel}
                onSelect={setSel}
                onOpen={setSel}
                caption="Pipelines grouped by provenance (HARNESS, WIRED)"
                primary
                empty={data ? <span className="hint">No pipeline to show.</span> : <span className="hint">Loading&hellip;</span>}
              />
            }
            second={selected ? <PipelineDetail key={selected.id} pl={selected} /> : <EmptyState inline title="Nothing selected." line="Pick a pipeline to see its task gates and nodes." />}
          />
        )}
      </PageBody>
    </>
  );
}

export default function Page() {
  const { session } = useAuth();
  if (!session)
    return (
      <>
        <PageHeader title="Catalog" />
        <PageBody>
          <SignIn />
        </PageBody>
      </>
    );
  return <CatalogView />;
}
