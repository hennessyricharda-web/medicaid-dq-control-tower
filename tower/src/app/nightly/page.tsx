"use client";
import Link from "next/link";
import { Suspense, useEffect, useState } from "react";
import { useSearchParams } from "next/navigation";
import { useAuth } from "@/lib/auth";
import { useLoad } from "@/lib/useLoad";
import { Gate } from "@/components/SignIn";
import { ErrorNote } from "@/components/ErrorNote";
import { ToneChip } from "@/components/Chip";
import { Explanation } from "@/components/Explanation";
import { refHref, SIM_RAN_EVENT } from "@/lib/partE";
import { fmtTime } from "@/lib/format";
import type { SimCount, SimRunSummary } from "@/lib/types";
import { PageBody, PageHeader } from "@/components/layout/PageHeader";
import { NightTraceLink } from "@/components/obs/NightTraceLink";

type CountKey = keyof SimRunSummary["counts"];

const COUNTS: { k: CountKey; label: string; screen?: { href: string; label: string } }[] = [
  { k: "files", label: "Files arrived" },
  { k: "abc_breaks", label: "ABC breaks", screen: { href: "/abc", label: "ABC Board" } },
  { k: "units_created", label: "Units created" },
  { k: "released_autonomously", label: "Released autonomously" },
  { k: "veto_windows_opened", label: "Veto windows opened", screen: { href: "/", label: "Decision Queue" } },
  { k: "held", label: "Held for a person", screen: { href: "/", label: "Decision Queue" } },
  { k: "pending_dependency", label: "Pending dependency" },
  { k: "feed_items", label: "Feed items opened", screen: { href: "/reconciliation", label: "Reconciliation" } },
  { k: "monitor_signals", label: "Monitor signals", screen: { href: "/monitoring", label: "Monitoring" } },
  { k: "demotions", label: "Demotions", screen: { href: "/agents", label: "Agents" } },
];
const SHOW_IDS = 12;

function Ids({ c, plain, scoped }: { c: SimCount; plain?: boolean; scoped?: boolean }) {
  c = { ...c, ids_scoped: c.ids_scoped ?? scoped };
  if (!c.ids.length) return c.n ? <p className="hint">{c.ids_scoped ? "None of these are in your scope." : ""}</p> : null;
  const shown = c.ids.slice(0, SHOW_IDS);
  return (
    <ul>
      {shown.map((id) => {
        const r = refHref(id);
        return <li key={id}>{plain || r.href === "#" ? <span className="mono">{r.label}</span> : <Link className="mono" href={r.href}>{r.label}</Link>}</li>;
      })}
      {c.ids.length > SHOW_IDS ? <li className="hint">and {c.ids.length - SHOW_IDS} more</li> : null}
      {c.ids_scoped && c.n > c.ids.length ? <li className="hint">{c.n - c.ids.length} outside your scope (not listed)</li> : null}
    </ul>
  );
}

function RunDetail({ id }: { id: string }) {
  const { session, client } = useAuth();
  const { data: r, error } = useLoad(session ? () => client.simRun(id) : null, [session?.token, id]);
  if (error) return <ErrorNote error={error} what={`run ${id}`} />;
  if (!r || r.run_id !== id) return <p className="muted">Loading…</p>;
  const c = r.counts;
  return (
    <section className="panel" aria-labelledby="run-h">
      <h2 id="run-h">
        Night {r.night_date} <span className="sub mono">{r.run_id}</span> <span className="simflag">SIMULATED</span>{" "}
        <NightTraceLink nightDate={r.night_date} />
      </h2>
      <p className="hint">
        Run {fmtTime(r.started_at)}
        {r.finished_at ? ` → ${fmtTime(r.finished_at)}` : " (in progress)"} · by {r.person_id ?? "demo preload"}
      </p>
      <Explanation e={r.explanation} label={`Night ${r.night_date} explanation`} />
      <div className="countgrid" role="list" aria-label="Run counts">
        {COUNTS.map(({ k, label, screen }) => {
          const v = c[k];
          return (
            <div className="count" role="listitem" key={k}>
              <div className="k hint">{label}</div>
              <div className="n">{v.n}</div>
              {k === "abc_breaks" ? (
                <div className="hint">
                  {c.abc_breaks.gating} gating · {c.abc_breaks.advisory} advisory
                </div>
              ) : null}
              {k === "held" && Object.keys(c.held.mode_reasons).length ? (
                <div className="hint">
                  {Object.entries(c.held.mode_reasons)
                    .map(([m, n]) => `${m}: ${n}`)
                    .join(" · ")}
                </div>
              ) : null}
              {screen && v.n ? (
                <div className="hint">
                  <Link href={screen.href}>Open {screen.label}</Link>
                </div>
              ) : null}
              <Ids c={v} plain={k === "files" || k === "abc_breaks"} scoped={r.ids_scoped} />
            </div>
          );
        })}
      </div>
    </section>
  );
}

function Nightly() {
  const { session, client } = useAuth();
  const { data: nights, error: nErr, reload } = useLoad(session ? client.simNights : null, [session?.token], 30_000);
  const q = useSearchParams().get("run");
  const [sel, setSel] = useState<string | null>(q);
  // A ?run= link (e.g. "View run" in the top band) selects that run, even when this screen is already open.
  useEffect(() => setSel(q), [q]);
  // A night run from the top band changes the list: re-read it.
  useEffect(() => {
    const on = () => void reload();
    window.addEventListener(SIM_RAN_EVENT, on);
    return () => window.removeEventListener(SIM_RAN_EVENT, on);
  }, [reload]);
  const ran = (nights?.nights ?? []).filter((n) => n.run_id);
  const latest = ran.length ? ran[ran.length - 1]!.run_id : null;
  const current = sel ?? latest;
  const queued = (nights?.nights ?? []).filter((n) => n.status === "QUEUED").length;
  return (
    <>
      <p className="hint">
        Demo mode: each night ingests that date&rsquo;s deliveries, then the agent seals, monitors, evaluates and publishes as it would overnight.
        The business date is <strong>SIMULATED</strong>. Operators run the next night from the top band.
      </p>
      <ErrorNote error={nErr} what="the simulated nights" />
      {current ? <RunDetail key={current} id={current} /> : null}
      <section className="panel" aria-labelledby="nights-h">
        <h2 id="nights-h">
          Nights{" "}
          <span className="sub" aria-live="polite">
            {nights ? `current date ${nights.current_date ?? "none"} · ${ran.length} run · ${queued} queued` : "loading…"}
          </span>
        </h2>
        <div className="tablewrap">
          <table>
            <caption className="skip">Simulated nights, queued and run</caption>
            <thead>
              <tr>
                <th scope="col">Night</th>
                <th scope="col">Status</th>
                <th scope="col">Files</th>
                <th scope="col">Run</th>
              </tr>
            </thead>
            <tbody>
              {(nights?.nights ?? []).map((n) => (
                <tr key={n.night_date} className={n.run_id && n.run_id === current ? "selected" : undefined}>
                  <td className="mono">{n.night_date}</td>
                  <td>
                    <ToneChip tone={n.status === "RUN" ? "ok" : n.status === "FAILED" ? "crit" : n.status === "STARTED" ? "info" : "neutral"} label={n.status} />
                    {n.error_class ? <div className="hint mono">{n.error_class}</div> : null}
                  </td>
                  <td className="mono hint">{n.files.join(", ")}</td>
                  <td>
                    {n.run_id ? (
                      <button type="button" className="linklike mono" aria-pressed={n.run_id === current} onClick={() => setSel(n.run_id)}>
                        {n.run_id}
                      </button>
                    ) : (
                      <span className="muted">queued</span>
                    )}
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
      <PageHeader title="Nightly runs" sub="Demo mode only" />
      <PageBody>
      <Gate>
        <Suspense fallback={<p className="muted">Loading…</p>}>
          <Nightly />
        </Suspense>
      </Gate>
      </PageBody>
    </>
  );
}
