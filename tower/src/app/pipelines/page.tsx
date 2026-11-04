"use client";
import Link from "next/link";
import { useAuth } from "@/lib/auth";
import { useLoad } from "@/lib/useLoad";
import { Gate } from "@/components/SignIn";
import { ErrorNote } from "@/components/ErrorNote";
import { StateChip, ToneChip } from "@/components/Chip";
import { buildEdges, PipelineDag } from "@/components/PipelineDag";
import { PageBody, PageHeader } from "@/components/layout/PageHeader";

function PipelineMapView() {
  const { session, client } = useAuth();
  const { data: p, error } = useLoad(session ? client.pipelines : null, [session?.token], 30_000);
  if (error) return <ErrorNote error={error} what="pipelines" />;
  if (!p) return <p aria-live="polite">Loading pipelines&hellip;</p>;
  const { edges, unresolved } = buildEdges(p);
  const violations = edges.filter((e) => !e.orderOk);
  return (
    <>
      <section className="panel" aria-labelledby="order-h">
        <h2 id="order-h">Global order</h2>
        <ol className="row" style={{ listStyle: "none", padding: 0, margin: 0 }}>
          {p.order.map((id, i) => (
            <li key={id} className="mono">
              {i + 1}. {id}
              {i < p.order.length - 1 ? " →" : ""}
            </li>
          ))}
        </ol>
        <p style={{ marginTop: 8 }}>
          {violations.length === 0 ? (
            <ToneChip tone="ok" label="Order consistent with every dependency" />
          ) : (
            <ToneChip tone="bad" label={`${violations.length} dependency edge(s) run against the global order`} />
          )}
          {unresolved.length ? <span className="hint"> Unresolved domains: {unresolved.join("; ")}</span> : null}
        </p>
        <p className="hint">This is the Tower&rsquo;s own cross-check of /pipelines; the top band shows the API&rsquo;s pipeline_order_ok.</p>
      </section>
      <section className="panel" aria-labelledby="dag-h">
        <h2 id="dag-h">Dependency graph</h2>
        <PipelineDag p={p} />
      </section>
      <section className="panel" aria-labelledby="deps-h">
        <h2 id="deps-h">Dependencies</h2>
        <table>
          <caption className="skip">Dependency edges with policy and temporal binding</caption>
          <thead>
            <tr>
              <th scope="col">Domain</th>
              <th scope="col">Depends on</th>
              <th scope="col">Policy</th>
              <th scope="col">Temporal binding</th>
              <th scope="col">Order</th>
            </tr>
          </thead>
          <tbody>
            {p.dependencies.map((d, i) => {
              const e = edges.find((x) => x.domain === d.domain && x.depends_on === d.depends_on);
              return (
                <tr key={i}>
                  <td>{d.domain}</td>
                  <td>{d.depends_on}</td>
                  <td className="mono">{d.policy}</td>
                  <td>{d.temporal_binding}</td>
                  <td>{e ? <ToneChip tone={e.orderOk ? "ok" : "bad"} label={e.orderOk ? "OK" : "VIOLATION"} /> : <ToneChip tone="warn" label="unresolved" />}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </section>
      <section className="panel" aria-labelledby="gated-h">
        <h2 id="gated-h">Gated nodes</h2>
        <table>
          <caption className="skip">Gated node states per pipeline, with a link to each pipeline&rsquo;s agent profile</caption>
          <thead>
            <tr>
              <th scope="col">Pipeline</th>
              <th scope="col">Platform / orchestrator</th>
              <th scope="col">Node</th>
              <th scope="col">State</th>
              <th scope="col">Profile</th>
            </tr>
          </thead>
          <tbody>
            {p.pipelines.flatMap((pl) =>
              pl.gated_nodes.map((g, i) => (
                <tr key={`${pl.id}:${g.node}`}>
                  <td className="mono">{pl.id}</td>
                  <td>
                    {pl.platform} / {pl.orchestrator}
                  </td>
                  <td className="mono">{g.node}</td>
                  <td>
                    <StateChip value={g.state} />
                  </td>
                  <td>
                    {i === 0 ? <Link className="mono" href={`/pipelines/${encodeURIComponent(pl.id)}/profile`}>Agent profile</Link> : null}
                  </td>
                </tr>
              )),
            )}
          </tbody>
        </table>
      </section>
    </>
  );
}

export default function Page() {
  return (
    <>
      <PageHeader title="Pipeline map" />
      <PageBody>
      <Gate>
        <PipelineMapView />
      </Gate>
      </PageBody>
    </>
  );
}
