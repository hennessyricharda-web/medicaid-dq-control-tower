"use client";
import { useState } from "react";
import { useAuth } from "@/lib/auth";
import { useLoad } from "@/lib/useLoad";
import { Gate } from "@/components/SignIn";
import { ErrorNote } from "@/components/ErrorNote";
import { DispositionChip } from "@/components/Chip";
import { hopRank } from "@/lib/dispositions";
import { fmtNum } from "@/lib/format";
import { MOCK } from "@/lib/config";
import { BATCH_ELIG, BATCH_ENC } from "@/lib/mock/fixtures";
import { PageBody, PageHeader } from "@/components/layout/PageHeader";

const NUMS = ["src_in", "tgt_out", "rejected", "quarantined", "deduplicated", "filtered", "held", "residual"] as const;

function Board() {
  const { session, client } = useAuth();
  const [input, setInput] = useState("");
  const [batch, setBatch] = useState("");
  const { data, error, loading } = useLoad(session ? () => client.abc(batch) : null, [session?.token, batch]);
  const rows = [...(data ?? [])].sort(
    (a, b) =>
      a.program.localeCompare(b.program) || a.submitter_id.localeCompare(b.submitter_id) || a.measure.localeCompare(b.measure) || hopRank(a.hop) - hopRank(b.hop),
  );
  const out = rows.filter((r) => r.residual !== 0).length;
  return (
    <>
      <form
        className="panel row"
        onSubmit={(e) => {
          e.preventDefault();
          setBatch(input.trim());
        }}
      >
        <label htmlFor="batch" style={{ margin: 0 }}>
          batch_id
        </label>
        <input id="batch" type="text" value={input} onChange={(e) => setInput(e.target.value)} style={{ maxWidth: 360 }} placeholder="blank = all batches the API returns" />
        <button type="submit" disabled={loading}>
          Load
        </button>
        {MOCK ? (
          <span className="hint">
            Mock batches: <code>{BATCH_ENC}</code>, <code>{BATCH_ELIG}</code>
          </span>
        ) : null}
      </form>
      <ErrorNote error={error} what="ABC" />
      <section className="panel" aria-labelledby="abc-h">
        <h2 id="abc-h">
          Hops TRANSLATOR &rarr; FILE &rarr; RAW &rarr; BRONZE &rarr; SILVER &rarr; GOLD{" "}
          <span className="sub">
            {rows.length} row(s) · {out} with non-zero residual{batch ? ` · batch ${batch}` : ""}
          </span>
        </h2>
        <div className="tablewrap">
          <table>
            <caption className="skip">ABC balance per hop</caption>
            <thead>
              <tr>
                <th scope="col">Hop</th>
                <th scope="col">Program</th>
                <th scope="col">Submitter</th>
                <th scope="col">Measure</th>
                {NUMS.map((n) => (
                  <th key={n} scope="col" className="num">
                    {n}
                  </th>
                ))}
                <th scope="col">Disposition</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r, i) => (
                <tr key={i} className={r.residual !== 0 ? "rowbad" : undefined}>
                  <td className="mono">{r.hop}</td>
                  <td>{r.program}</td>
                  <td className="mono">{r.submitter_id}</td>
                  <td>{r.measure}</td>
                  {NUMS.map((n) => (
                    <td key={n} className="num">
                      {n === "residual" && r.residual !== 0 ? <strong>{fmtNum(r[n])} ≠ 0</strong> : fmtNum(r[n])}
                    </td>
                  ))}
                  <td>
                    <DispositionChip value={r.disposition} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </>
  );
}

export default function Page() {
  return (
    <>
      <PageHeader title="ABC board" sub="Advisory failures are counted but never hold a batch" />
      <PageBody>
      <Gate>
        <Board />
      </Gate>
      </PageBody>
    </>
  );
}
