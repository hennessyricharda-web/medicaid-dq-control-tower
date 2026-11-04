"use client";
import { useMemo, useState } from "react";
import { useAuth } from "@/lib/auth";
import { useLoad } from "@/lib/useLoad";
import { Gate } from "@/components/SignIn";
import { ErrorNote } from "@/components/ErrorNote";
import { ToneChip } from "@/components/Chip";
import { basicCellTone, DIMENSIONS, reqCoverageTone } from "@/lib/governance";
import type { BasicCell, CdeRow, ClassificationRow, CoverageResponse } from "@/lib/types";
import { ClassChip } from "@/components/ClassChip";
import { Explanation } from "@/components/Explanation";
import { statusTone } from "@/lib/partE";
import Link from "next/link";
import { PageBody, PageHeader } from "@/components/layout/PageHeader";

const pct = (v: number | null | undefined) => (v === null || v === undefined ? "—" : `${(v * 100).toFixed(1)}%`);

function Cde({ rows, cls }: { rows: CdeRow[]; cls: Map<string, string> }) {
  const [st, setSt] = useState("");
  const shown = rows.filter((r) => !st || r.status === st);
  return (
    <section className="panel" aria-labelledby="cde-h">
      <h2 id="cde-h">
        Critical data elements: minimum viable DQ <span className="sub">advisory; NOT_EXERCISED is never counted as meeting</span>
      </h2>
      <div className="row" style={{ marginBottom: 8 }}>
        <label htmlFor="cde-st" style={{ margin: 0 }}>
          Status
        </label>
        <select id="cde-st" value={st} onChange={(e) => setSt(e.target.value)}>
          <option value="">all</option>
          {["MEETS", "BELOW", "NOT_EXERCISED", "NOT_LOADED"].map((s) => (
            <option key={s}>{s}</option>
          ))}
        </select>
      </div>
      <div className="tablewrap">
        <table>
          <caption className="skip">Critical data elements with completeness and validity against their minimums</caption>
          <thead>
            <tr>
              <th scope="col">Element</th>
              <th scope="col">Class</th>
              <th scope="col">Domain</th>
              <th scope="col" className="num">
                Completeness (min)
              </th>
              <th scope="col" className="num">
                Validity (min)
              </th>
              <th scope="col">Status</th>
              <th scope="col">Explanation</th>
            </tr>
          </thead>
          <tbody>
            {shown.map((r) => (
              <tr key={r.element}>
                <td className="mono">{r.element}</td>
                <td>{cls.get(r.element) ? <ClassChip c={cls.get(r.element)!} /> : <span className="muted">—</span>}</td>
                <td>{r.domain}</td>
                <td className="num">
                  {pct(r.completeness)} <span className="hint">({pct(r.thresholds.completeness_min)})</span>
                </td>
                <td className="num">
                  {pct(r.validity)} <span className="hint">({pct(r.thresholds.validity_min)})</span>
                </td>
                <td>
                  <ToneChip tone={statusTone(r.status)} label={r.status} />
                </td>
                <td style={{ maxWidth: 380 }}>
                  <Explanation e={r.explanation} label={`${r.element} explanation`} compact />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function Classification({ rows }: { rows: ClassificationRow[] }) {
  const part2 = rows.filter((r) => r.class === "part2").length;
  return (
    <details className="panel">
      <summary>
        <strong>Sensitivity classification</strong>{" "}
        <span className="hint">
          {rows.length} element(s) · {part2} part2 · identity, clinical and part2 are masked on reveal; downgrading a class is two-person
        </span>
      </summary>
      <div className="tablewrap" style={{ marginTop: 8 }}>
        <table>
          <caption className="skip">Effective classification per element</caption>
          <thead>
            <tr>
              <th scope="col">Element</th>
              <th scope="col">Class</th>
              <th scope="col">Source</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.element}>
                <td className="mono">{r.element}</td>
                <td>
                  <ClassChip c={r.class} />
                </td>
                <td>
                  {r.source}
                  {r.cr_id ? (
                    <>
                      {" "}
                      <Link className="mono" href={`/governance/${encodeURIComponent(r.cr_id)}`}>
                        {r.cr_id}
                      </Link>
                    </>
                  ) : null}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </details>
  );
}

const CELL_HELP: Record<string, string> = {
  EXERCISED: "at least one ACTIVE rule checks this",
  DECLARED_NOT_EXERCISED: "rules exist, but the feed is not delivered: nothing is checked",
  NOT_ACTIVE: "rules exist but none is ACTIVE (not bound, waived or not loaded)",
  EMPTY: "no rule declared",
};

function Cell({ c }: { c: BasicCell | undefined }) {
  const status = c?.status ?? "EMPTY";
  const n = c ? (c.active ?? 0) + (c.not_applicable ?? 0) + (c.other ?? 0) : 0;
  return (
    <td title={(c?.rules ?? []).join("\n") || CELL_HELP[status]}>
      <ToneChip tone={basicCellTone(status)} label={status === "DECLARED_NOT_EXERCISED" ? "DECLARED, NOT EXERCISED" : status} />
      {n ? (
        <div className="hint">
          {c?.active ?? 0} active{c?.not_applicable ? ` · ${c.not_applicable} n/a` : ""}
          {c?.other ? ` · ${c.other} other` : ""}
        </div>
      ) : null}
    </td>
  );
}

function Matrix({ cov }: { cov: CoverageResponse }) {
  const m = cov.basic?.matrix ?? {};
  const doms = Object.keys(m).sort();
  return (
    <section className="panel" aria-labelledby="basic-h">
      <h2 id="basic-h">
        Basic DQ floor <span className="sub">domain × dimension over every non-measure rule</span>
      </h2>
      <div className="legend" style={{ marginBottom: 8 }}>
        {Object.entries(CELL_HELP).map(([k, v]) => (
          <span key={k}>
            <ToneChip tone={basicCellTone(k)} label={k === "DECLARED_NOT_EXERCISED" ? "DECLARED, NOT EXERCISED" : k} /> {v}
          </span>
        ))}
      </div>
      <div className="tablewrap">
        <table className="matrix">
          <caption className="skip">Basic data-quality coverage: rows are domains, columns are the six dimensions</caption>
          <thead>
            <tr>
              <th scope="col">Domain</th>
              {DIMENSIONS.map((d) => (
                <th key={d} scope="col">
                  {d}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {doms.map((d) => (
              <tr key={d}>
                <th scope="row" style={{ textTransform: "none", fontSize: "0.92rem", color: "var(--text)" }}>
                  {d}
                </th>
                {DIMENSIONS.map((dim) => (
                  <Cell key={dim} c={m[d]?.[dim]} />
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function Summary({ cov }: { cov: CoverageResponse }) {
  const bs = cov.basic?.summary ?? {};
  const totals = new Map<string, number>();
  for (const v of Object.values(cov.summary ?? {})) for (const [k, n] of Object.entries(v)) totals.set(k, (totals.get(k) ?? 0) + n);
  return (
    <section className="panel" aria-labelledby="sum-h">
      <h2 id="sum-h">Summary</h2>
      <div className="split">
        <dl className="kv">
          {Object.entries(bs).map(([k, v]) => (
            <div key={k} style={{ display: "contents" }}>
              <dt>{k.replace(/_/g, " ")}</dt>
              <dd>{v}</dd>
            </div>
          ))}
        </dl>
        <div>
          <p className="hint" style={{ marginTop: 0 }}>
            Requirements by coverage state ({cov.requirements.length} total)
          </p>
          <div className="row" style={{ gap: 6 }}>
            {[...totals.entries()]
              .sort()
              .map(([k, n]) => (
                <ToneChip key={k} tone={reqCoverageTone(k)} label={`${k}: ${n}`} />
              ))}
          </div>
        </div>
      </div>
    </section>
  );
}

function Requirements({ cov }: { cov: CoverageResponse }) {
  const [state, setState] = useState("");
  const [dom, setDom] = useState("");
  const states = useMemo(() => [...new Set(cov.requirements.map((r) => r.coverage))].sort(), [cov]);
  const doms = useMemo(() => [...new Set(cov.requirements.map((r) => r.domain))].sort(), [cov]);
  const rows = cov.requirements.filter((r) => (!state || r.coverage === state) && (!dom || r.domain === dom));
  return (
    <section className="panel" aria-labelledby="req-h">
      <h2 id="req-h">
        Requirement inventory <span className="sub" aria-live="polite">{rows.length} shown</span>
      </h2>
      <p className="hint">A requirement is covered only by an ACTIVE full rule. A pre-load analog of a CMS measure never covers the measure itself.</p>
      <div className="filters" style={{ marginBottom: 8 }}>
        <div>
          <label htmlFor="cst">Coverage</label>
          <select id="cst" value={state} onChange={(e) => setState(e.target.value)}>
            <option value="">all</option>
            {states.map((s) => (
              <option key={s}>{s}</option>
            ))}
          </select>
        </div>
        <div>
          <label htmlFor="cdom">Domain</label>
          <select id="cdom" value={dom} onChange={(e) => setDom(e.target.value)}>
            <option value="">all</option>
            {doms.map((s) => (
              <option key={s}>{s}</option>
            ))}
          </select>
        </div>
      </div>
      <div className="tablewrap">
        <table>
          <caption className="skip">Requirements and their coverage state</caption>
          <thead>
            <tr>
              <th scope="col">Requirement</th>
              <th scope="col">Domain</th>
              <th scope="col">Stage</th>
              <th scope="col">Dimension</th>
              <th scope="col">Priority</th>
              <th scope="col">Source</th>
              <th scope="col">Coverage</th>
              <th scope="col">Rules</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={`${r.req_id}|${r.source}`}>
                <td className="mono">{r.req_id}</td>
                <td>{r.domain}</td>
                <td>{r.stage}</td>
                <td>{r.dimension}</td>
                <td>{r.priority}</td>
                <td className="mono">{r.source}</td>
                <td>
                  <ToneChip tone={reqCoverageTone(r.coverage)} label={r.coverage} title={`Coverage ${r.coverage}`} />
                </td>
                <td className="mono hint">{r.rules.length ? r.rules.join(", ") : "none"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function Coverage() {
  const { session, client } = useAuth();
  const { data, error } = useLoad(session ? client.coverage : null, [session?.token]);
  const { data: cde, error: cdeErr } = useLoad(session ? client.cde : null, [session?.token]);
  const { data: cls, error: clsErr } = useLoad(session ? client.classification : null, [session?.token]);
  const clsMap = new Map((cls ?? []).map((r) => [r.element, r.class]));
  return (
    <>
      <ErrorNote error={error} what="coverage" />
      {!data && !error ? <p className="muted">Loading…</p> : null}
      {data ? (
        <>
          <ErrorNote error={cdeErr} what="critical data elements" />
          {cde?.cde ? <Cde rows={cde.cde} cls={clsMap} /> : null}
          <ErrorNote error={clsErr} what="classification" />
          {cls ? <Classification rows={cls} /> : null}
          <Summary cov={data} />
          <Matrix cov={data} />
          <Requirements cov={data} />
        </>
      ) : null}
    </>
  );
}

export default function Page() {
  return (
    <>
      <PageHeader title="Coverage" />
      <PageBody>
      <Gate>
        <Coverage />
      </Gate>
      </PageBody>
    </>
  );
}
