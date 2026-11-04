"use client";
/**
 * K11 (PARTF_K7_K12_MOCK_CONTRACT.md): evaluations history. Lists recorded `dq_ctl.eval_runs` rows, newest first,
 * by calling the REAL, already-wired `GET /evals` route (`client.listEvals`, dqagent/api/app.py; the SAME call
 * `/agents`' eval-attestation picker already makes). This screen is new UI, but its data is NOT invented -- it is
 * a real, tested endpoint, so it carries no PRE-BACKEND banner (that marker would misstate what backs it; see the
 * build report's "API gaps" note on this choice). "Compare" opens the separate eval-diff screen (PRE-BACKEND,
 * `/evaluations/diff`) -- never this file's own `runs/diff` namesake, which is a different, already-real screen
 * for nightly pipeline-run outcomes (`app/runs/diff/page.tsx`) and is untouched by this package.
 */
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { useAuth } from "@/lib/auth";
import { useLoad } from "@/lib/useLoad";
import { Gate } from "@/components/SignIn";
import { ErrorNote } from "@/components/ErrorNote";
import { fmtTime, shortHash } from "@/lib/format";
import type { EvalRun } from "@/lib/types";
import { PageBody, PageHeader } from "@/components/layout/PageHeader";
import { EmptyState } from "@/components/layout/EmptyState";
import { NotInDemo } from "@/components/layout/NotInDemo";
import { PREVIEW_K7_K12 } from "@/lib/config";

function EvaluationsView() {
  const { client, session } = useAuth();
  const router = useRouter();
  const gov = !!session?.me.duties.some((d) => d.startsWith("governance:"));
  const { data: runs, error, loading } = useLoad(gov ? () => client.listEvals() : null, [session?.token, gov]);
  const [picked, setPicked] = useState<string[]>([]);

  if (!gov) {
    return (
      <div role="alert" className="banner t-warn">
        <strong>Governance duty required.</strong> Recorded eval runs are visible only to a governance reviewer (the SAME boundary
        the real <span className="mono">GET /evals</span> route enforces).
      </div>
    );
  }
  if (error) return <ErrorNote error={error} what="recorded eval runs" />;
  if (loading && !runs) return <p className="hint">Loading&hellip;</p>;
  const rows: EvalRun[] = runs ?? [];

  function toggle(id: string) {
    setPicked((p) => (p.includes(id) ? p.filter((x) => x !== id) : p.length < 2 ? [...p, id] : [p[1]!, id]));
  }

  return (
    <section className="panel" aria-labelledby="ev-h">
      <h2 id="ev-h">
        Recorded eval runs <span className="sub">{rows.length} run(s), newest first</span>
      </h2>
      <p className="hint">
        Tick two rows to compare them. {picked.length === 2 ? null : `${picked.length}/2 picked.`}
        {picked.length === 2 ? (
          <button
            type="button"
            className="sm primary"
            style={{ marginLeft: 8 }}
            onClick={() => router.push(`/evaluations/diff?a=${encodeURIComponent(picked[0]!)}&b=${encodeURIComponent(picked[1]!)}`)}
          >
            Compare {picked[0]} vs {picked[1]}
          </button>
        ) : null}
      </p>
      {rows.length ? (
        <div className="tablewrap">
          <table>
            <caption className="skip">Recorded agent_eval / explainer runs</caption>
            <thead>
              <tr>
                <th scope="col" aria-label="compare" />
                <th scope="col">Run id</th>
                <th scope="col">Suite</th>
                <th scope="col">Source mode</th>
                <th scope="col" className="num">
                  Passed / cases
                </th>
                <th scope="col">Code</th>
                <th scope="col">Prompt version</th>
                <th scope="col">Junit</th>
                <th scope="col">At</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.run_id} className={picked.includes(r.run_id) ? "r" : undefined} aria-selected={picked.includes(r.run_id)}>
                  <td>
                    <input
                      type="checkbox"
                      aria-label={`Pick ${r.run_id} to compare`}
                      checked={picked.includes(r.run_id)}
                      onChange={() => toggle(r.run_id)}
                    />
                  </td>
                  <td className="mono">{r.run_id}</td>
                  <td>{r.suite}</td>
                  <td>{r.source_mode}</td>
                  <td className="num">
                    {r.passed}/{r.cases}
                  </td>
                  <td>
                    <code title={r.code_sha256}>{shortHash(r.code_sha256, 10)}</code>
                  </td>
                  <td>{r.prompt_version ?? <span className="dash">–</span>}</td>
                  <td>
                    <code title={r.junit_sha256}>{shortHash(r.junit_sha256, 10)}</code>
                  </td>
                  <td>{fmtTime(r.at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <EmptyState title="No recorded eval run." line="Nothing in dq_ctl.eval_runs yet." />
      )}
    </section>
  );
}

export default function Page() {
  // K8-K12 preview gate: OFF by default; run_demo.ps1 never sets NEXT_PUBLIC_DQ_PREVIEW_K7_K12. Self-guarded
  // here (not just hidden from the nav rail) so a guessed /evaluations URL shows nothing live during the demo.
  if (!PREVIEW_K7_K12) return <NotInDemo title="Evaluations" />;
  return (
    <>
      <PageHeader title="Evaluations" sub="recorded agent_eval / explainer runs" crumb={<Link href="/agents">Agents /</Link>} />
      <PageBody>
        <Gate>
          <EvaluationsView />
        </Gate>
      </PageBody>
    </>
  );
}
