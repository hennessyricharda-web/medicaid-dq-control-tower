"use client";
/**
 * Run diff (wireframe 18): two runs side by side, OUTCOME differences only — calling GET /api/v1/runs/diff
 * (obs_routes.py F-35 / P2-1), never re-deriving the diff from two raw run fetches in the browser. Release-check
 * deltas, span status / llm-verdict / fact-check deltas, guardrail-count deltas and the final event. The view
 * states what differs; it never says why, and it carries no reasons the server itself does not return.
 */
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { useAuth } from "@/lib/auth";
import { useObs } from "@/lib/api/obs";
import { useLoad } from "@/lib/useLoad";
import type { RunDiff, RunRow } from "@/lib/types/obs";
import { SignIn } from "@/components/SignIn";
import { ErrorNote } from "@/components/ErrorNote";
import { PageBody, PageHeader } from "@/components/layout/PageHeader";
import { EmptyState } from "@/components/layout/EmptyState";
import { useUrlParam } from "@/components/layout/SplitPane";
import { RefLink, StatusChip } from "@/components/obs/common";

const nameOf = (k: string) => k.split("|")[0]!.split("/").pop() ?? k;
const subjOf = (k: string) => k.split("|")[1] || null;

function Picker({ a, b }: { a: string; b: string }) {
  const obs = useObs();
  const router = useRouter();
  const [runs, setRuns] = useState<RunRow[] | null>(null);
  const [x, setX] = useState(a);
  const [y, setY] = useState(b);
  useEffect(() => {
    obs.runs({ limit: 100 }).then((r) => setRuns(r.runs), () => setRuns([]));
  }, [obs]);
  const opts = (runs ?? []).map((r) => (
    <option key={r.trace_id} value={r.trace_id}>
      {r.trace_id} ({r.kind}
      {r.night_date ? `, ${r.night_date}` : ""})
    </option>
  ));
  return (
    <form
      className="row"
      onSubmit={(e) => {
        e.preventDefault();
        if (x && y) router.replace(`/runs/diff?a=${encodeURIComponent(x)}&b=${encodeURIComponent(y)}`);
      }}
    >
      <label htmlFor="diff-a" style={{ margin: 0 }}>
        Run A
      </label>
      <select id="diff-a" value={x} onChange={(e) => setX(e.target.value)}>
        <option value="">choose…</option>
        {opts}
      </select>
      <label htmlFor="diff-b" style={{ margin: 0 }}>
        Run B
      </label>
      <select id="diff-b" value={y} onChange={(e) => setY(e.target.value)}>
        <option value="">choose…</option>
        {opts}
      </select>
      <button type="submit" className="primary" disabled={!x || !y || x === y}>
        Compare
      </button>
    </form>
  );
}

function Diff({ a, b }: { a: string; b: string }) {
  const obs = useObs();
  const { data: diff, error } = useLoad<RunDiff>(() => obs.diff(a, b), [obs, a, b]);

  if (error) return <ErrorNote error={error} what="the diff" />;
  if (!diff) return <p className="hint">Loading the diff&hellip;</p>;
  const outcome = (v: boolean | null) => (v === null ? "not checked" : v ? "may release" : "not released");
  const head = (tag: "a" | "b", trace: string) => (
    <div className="panel" style={{ flex: 1, margin: 0 }}>
      <h2>
        Run {tag.toUpperCase()}{" "}
        <Link className="mono" href={`/runs/${encodeURIComponent(trace)}`}>
          {trace}
        </Link>
      </h2>
      <div className="row" style={{ fontSize: "var(--t-sm)" }}>
        <StatusChip status={diff.status[tag]} />
      </div>
    </div>
  );
  return (
    <>
      <div className="row" style={{ alignItems: "stretch", gap: 12, marginBottom: 12, flexWrap: "nowrap" }}>
        {head("a", diff.a)}
        {head("b", diff.b)}
      </div>
      <p className="hint">
        Differences only, B compared with A. This view states what differs; it does not say why.
        {diff.hidden_out_of_scope ? <span className="chip t-info" style={{ marginLeft: 6 }}>steps outside your scope are hidden on one or both runs</span> : null}
      </p>

      <section className="panel" aria-labelledby="d-rel">
        <h2 id="d-rel">
          Release results <span className="sub">{diff.release_check_deltas.length} unit(s) differ</span>
        </h2>
        {diff.release_check_deltas.length ? (
          <table className="dt">
            <thead>
              <tr>
                <th scope="col">Unit</th>
                <th scope="col">A</th>
                <th scope="col">B</th>
              </tr>
            </thead>
            <tbody>
              {diff.release_check_deltas.map((r) => (
                <tr key={r.unit_key} className="alarm">
                  <td>
                    <RefLink refId={`unit:${r.unit_key}`} />
                  </td>
                  <td>{outcome(r.a)}</td>
                  <td>{outcome(r.b)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <EmptyState inline title="No unit has a different release result." />
        )}
      </section>

      <section className="panel" aria-labelledby="d-span">
        <h2 id="d-span">
          Step outcomes <span className="sub">{diff.span_deltas.length} difference(s)</span>
        </h2>
        {diff.span_deltas.length ? (
          <table className="dt">
            <thead>
              <tr>
                <th scope="col">Step</th>
                <th scope="col">Subject</th>
                <th scope="col">In A / B</th>
                <th scope="col">Status A</th>
                <th scope="col">Status B</th>
                <th scope="col">Verdict A</th>
                <th scope="col">Verdict B</th>
                <th scope="col">Fact-check A</th>
                <th scope="col">Fact-check B</th>
              </tr>
            </thead>
            <tbody>
              {diff.span_deltas.map((x) => (
                <tr key={x.key}>
                  <td className="mono">{nameOf(x.key)}</td>
                  <td>{subjOf(x.key) ? <RefLink refId={subjOf(x.key)!} /> : <span className="dash">–</span>}</td>
                  <td>
                    {x.in_a ? "A" : <span className="dash">–</span>} / {x.in_b ? "B" : <span className="dash">–</span>}
                  </td>
                  <td>
                    <StatusChip status={x.status.a} />
                  </td>
                  <td>
                    <StatusChip status={x.status.b} />
                  </td>
                  <td className="mono">{x.verdict.a ?? <span className="dash">–</span>}</td>
                  <td className="mono">{x.verdict.b ?? <span className="dash">–</span>}</td>
                  <td className="mono">{x.fact_check.a ?? <span className="dash">–</span>}</td>
                  <td className="mono">{x.fact_check.b ?? <span className="dash">–</span>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <EmptyState inline title="No step's status, llm verdict or fact-check differs on steps both runs have." />
        )}
        <p className="hint" style={{ marginTop: 8 }}>
          {diff.spans_only_in_a} step(s) in A only · {diff.spans_only_in_b} step(s) in B only (counts only; the server reports no further detail on
          steps found in one run but not the other).
        </p>
      </section>

      <section className="panel" aria-labelledby="d-gr">
        <h2 id="d-gr">Guardrail events by code</h2>
        {Object.keys(diff.guardrail_deltas).length ? (
          <table className="dt">
            <thead>
              <tr>
                <th scope="col">Code</th>
                <th scope="col" className="num">
                  A
                </th>
                <th scope="col" className="num">
                  B
                </th>
              </tr>
            </thead>
            <tbody>
              {Object.entries(diff.guardrail_deltas)
                .sort(([c1], [c2]) => c1.localeCompare(c2))
                .map(([code, g]) => (
                  <tr key={code}>
                    <td className="mono">{code}</td>
                    <td className="num">{g.a}</td>
                    <td className="num">{g.b}</td>
                  </tr>
                ))}
            </tbody>
          </table>
        ) : (
          <p className="muted">No guardrail code&rsquo;s count differs between the two runs, in your scope.</p>
        )}
      </section>
    </>
  );
}

function DiffPage() {
  const a = useUrlParam("a") ?? "";
  const b = useUrlParam("b") ?? "";
  return (
    <>
      <PageHeader crumb={<Link href="/runs">Traces /</Link>} title="Run diff" sub="outcome differences between two runs" />
      <PageBody>
        <div style={{ marginBottom: 12 }}>
          <Picker key={`${a}|${b}`} a={a} b={b} />
        </div>
        {a && b ? <Diff a={a} b={b} /> : <EmptyState title="Choose two runs." line="Tick two runs on Traces, or choose them here." />}
      </PageBody>
    </>
  );
}

export default function Page() {
  const { session } = useAuth();
  if (!session)
    return (
      <>
        <PageHeader title="Run diff" />
        <PageBody>
          <SignIn />
        </PageBody>
      </>
    );
  return <DiffPage />;
}
