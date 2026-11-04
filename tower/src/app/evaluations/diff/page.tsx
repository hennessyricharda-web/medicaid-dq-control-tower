"use client";
/**
 * K11 (PARTF_K7_K12_MOCK_CONTRACT.md): eval-run diff. PRE-BACKEND -- calls the INVENTED `GET /eval-runs/diff`
 * (`client.evalRunDiff`), which has no real counterpart. A SEPARATE screen from `app/runs/diff/page.tsx`, which
 * is a different, already-real screen for nightly pipeline-run outcome diffs (`GET /api/v1/runs/diff`) and is
 * untouched by this package -- do not confuse the two.
 *
 * Same honesty pattern that existing screen's own header comment states: this view states what differs between
 * the two runs; it never says why, and it carries no reason the server itself does not return. Built against
 * exactly two explicitly-fabricated, clearly-labelled synthetic eval runs (one is named *-synthetic in
 * `initialEvalRuns()`); neither compared run is ever presented as a real historical production run.
 */
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { useAuth } from "@/lib/auth";
import { useLoad } from "@/lib/useLoad";
import { SignIn } from "@/components/SignIn";
import { ErrorNote } from "@/components/ErrorNote";
import { fmtTime, shortHash } from "@/lib/format";
import type { EvalRunDiff } from "@/lib/types";
import { PageBody, PageHeader } from "@/components/layout/PageHeader";
import { EmptyState } from "@/components/layout/EmptyState";
import { useUrlParam } from "@/components/layout/SplitPane";
import { PreBackendBanner } from "@/components/layout/PreBackendBanner";
import { NotInDemo } from "@/components/layout/NotInDemo";
import { PREVIEW_K7_K12 } from "@/lib/config";

const FIELD_LABEL: Record<string, string> = {
  suite: "Suite",
  source_mode: "Source mode",
  cases: "Cases",
  passed: "Passed",
  code_sha256: "Code",
  prompt_version: "Prompt version",
  junit_sha256: "Junit",
};

function fmtField(key: string, v: EvalRunDiff["a"][keyof EvalRunDiff["a"]]): string {
  if (v === null || v === undefined) return "—";
  if (key === "code_sha256" || key === "junit_sha256") return shortHash(String(v), 12);
  return String(v);
}

function Diff({ a, b }: { a: string; b: string }) {
  const { client } = useAuth();
  const { data: diff, error } = useLoad<EvalRunDiff>(() => client.evalRunDiff(a, b), [client, a, b]);
  if (error) return <ErrorNote error={error} what="the eval-run diff" />;
  if (!diff) return <p className="hint">Loading the diff&hellip;</p>;
  return (
    <>
      <div className="row" style={{ alignItems: "stretch", gap: 12, marginBottom: 12, flexWrap: "nowrap" }}>
        <div className="panel" style={{ flex: 1, margin: 0 }}>
          <h2>
            Run A <span className="mono">{diff.a.run_id}</span>
          </h2>
          <p className="hint">
            {diff.a.suite} · {diff.a.source_mode} · {fmtTime(diff.a.at)}
          </p>
        </div>
        <div className="panel" style={{ flex: 1, margin: 0 }}>
          <h2>
            Run B <span className="mono">{diff.b.run_id}</span>
          </h2>
          <p className="hint">
            {diff.b.suite} · {diff.b.source_mode} · {fmtTime(diff.b.at)}
          </p>
        </div>
      </div>
      <p className="hint">Differences only, B compared with A. This view states what differs; it does not say why.</p>
      <section className="panel" aria-labelledby="ed-fields">
        <h2 id="ed-fields">
          Fields that differ <span className="sub">{diff.fields.length} field(s)</span>
        </h2>
        {diff.fields.length ? (
          <table className="dt" id="ed-fields-table">
            <thead>
              <tr>
                <th scope="col">Field</th>
                <th scope="col">A</th>
                <th scope="col">B</th>
              </tr>
            </thead>
            <tbody>
              {diff.fields.map((f) => (
                <tr key={f} className="alarm">
                  <td>{FIELD_LABEL[f] ?? f}</td>
                  <td className="mono">{fmtField(f, diff.a[f as keyof EvalRunDiff["a"]])}</td>
                  <td className="mono">{fmtField(f, diff.b[f as keyof EvalRunDiff["b"]])}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <EmptyState inline title="No field differs between these two runs." />
        )}
      </section>
    </>
  );
}

function Picker({ a, b }: { a: string; b: string }) {
  const { client, session } = useAuth();
  const router = useRouter();
  const gov = !!session?.me.duties.some((d) => d.startsWith("governance:"));
  const { data: runs } = useLoad(gov ? () => client.listEvals() : null, [session?.token, gov]);
  const [x, setX] = useState(a);
  const [y, setY] = useState(b);
  const opts = (runs ?? []).map((r) => (
    <option key={r.run_id} value={r.run_id}>
      {r.run_id} ({r.suite}, {fmtTime(r.at)})
    </option>
  ));
  return (
    <form
      className="row"
      onSubmit={(e) => {
        e.preventDefault();
        if (x && y) router.replace(`/evaluations/diff?a=${encodeURIComponent(x)}&b=${encodeURIComponent(y)}`);
      }}
    >
      <label htmlFor="ed-a" style={{ margin: 0 }}>
        Run A
      </label>
      <select id="ed-a" value={x} onChange={(e) => setX(e.target.value)}>
        <option value="">choose…</option>
        {opts}
      </select>
      <label htmlFor="ed-b" style={{ margin: 0 }}>
        Run B
      </label>
      <select id="ed-b" value={y} onChange={(e) => setY(e.target.value)}>
        <option value="">choose…</option>
        {opts}
      </select>
      <button type="submit" className="primary" disabled={!x || !y || x === y}>
        Compare
      </button>
    </form>
  );
}

function EvalDiffPage() {
  const a = useUrlParam("a") ?? "";
  const b = useUrlParam("b") ?? "";
  return (
    <>
      <PageHeader crumb={<Link href="/evaluations">Evaluations /</Link>} title="Eval-run diff" sub="field differences between two recorded eval runs" />
      <PageBody>
        <PreBackendBanner detail="Invented mock route (GET /eval-runs/diff); no real backend route exists for this yet. Built against two explicitly synthetic runs — never a real historical pair." />
        <div style={{ marginBottom: 12 }}>
          <Picker key={`${a}|${b}`} a={a} b={b} />
        </div>
        {a && b ? <Diff a={a} b={b} /> : <EmptyState title="Choose two runs." line="Tick two runs on Evaluations, or choose them here." />}
      </PageBody>
    </>
  );
}

export default function Page() {
  const { session } = useAuth();
  // K8-K12 preview gate: OFF by default; run_demo.ps1 never sets NEXT_PUBLIC_DQ_PREVIEW_K7_K12. Self-guarded
  // here (not just hidden from the nav rail) so a guessed /evaluations/diff URL shows nothing live during the
  // demo -- checked before the sign-in gate below, so it never even reveals that a sign-in form exists here.
  if (!PREVIEW_K7_K12) return <NotInDemo title="Eval-run diff" />;
  if (!session)
    return (
      <>
        <PageHeader title="Eval-run diff" />
        <PageBody>
          <SignIn />
        </PageBody>
      </>
    );
  return <EvalDiffPage />;
}
