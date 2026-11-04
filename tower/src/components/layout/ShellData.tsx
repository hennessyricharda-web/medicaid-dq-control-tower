"use client";
/**
 * The data the shell itself shows: the summary (status strip, header clock, rail badges), the queue (veto-window
 * slot, palette entity search) and the open look-back count (rail badge). Every endpoint is scoped by the server
 * to the viewer's authorities, so every count here is the viewer's scope only (E5).
 *
 * "Run next night" lives here so the header button and the command palette share one code path and one result.
 */
import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from "react";
import { ApiError } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { SUMMARY_POLL_MS } from "@/lib/config";
import { isOperator, SIM_RAN_EVENT } from "@/lib/partE";
import { useLoad } from "@/lib/useLoad";
import type { QueueCluster, Summary } from "@/lib/types";

export interface SimMessage {
  ok: boolean;
  text: string;
  run?: string;
}

interface ShellDataCtx {
  summary: Summary | null;
  summaryError: ApiError | null;
  /** When the summary was last read successfully (ms epoch), for "Updated 8 s ago" and staleness. */
  summaryAt: number | null;
  reloadSummary: () => Promise<unknown>;
  queue: QueueCluster[] | null;
  queueError: ApiError | null;
  lookbackOpen: number | null;
  /** The nightly-run simulator is on (demo mode only). */
  demo: boolean;
  /** The viewer holds the operations duty: may run the next simulated night. */
  operator: boolean;
  simBusy: boolean;
  simMsg: SimMessage | null;
  clearSimMsg: () => void;
  runNextNight: () => Promise<void>;
}

const Ctx = createContext<ShellDataCtx | null>(null);

export function ShellDataProvider({ children }: { children: ReactNode }) {
  const { session, client } = useAuth();
  const [summaryAt, setSummaryAt] = useState<number | null>(null);
  const summaryFn = useMemo(
    () =>
      session
        ? async () => {
            const s = await client.summary();
            setSummaryAt(Date.now());
            return s;
          }
        : null,
    [session, client],
  );
  const { data: summary, error: summaryError, reload: reloadSummary } = useLoad(summaryFn, [session?.token], SUMMARY_POLL_MS);
  const { data: queue, error: queueError, reload: reloadQueue } = useLoad(session ? client.queue : null, [session?.token], SUMMARY_POLL_MS);
  const { data: lookback, reload: reloadLookback } = useLoad(session ? client.lookback : null, [session?.token], 60_000);

  const [simBusy, setSimBusy] = useState(false);
  const [simMsg, setSimMsg] = useState<SimMessage | null>(null);
  const operator = isOperator(session?.me.duties);
  const demo = !!summary?.sim?.enabled;

  const runNextNight = useCallback(async () => {
    setSimBusy(true);
    setSimMsg(null);
    try {
      const r = await client.simRunNext();
      setSimMsg({ ok: true, text: `Ran ${r.night_date}: ${r.counts.files.n} file(s), ${r.counts.held.n} held.`, run: r.run_id });
      void reloadSummary();
      void reloadQueue();
      void reloadLookback();
      window.dispatchEvent(new CustomEvent(SIM_RAN_EVENT, { detail: r.run_id }));
    } catch (e) {
      const ae = e instanceof ApiError ? e : new ApiError(0, { detail: String(e) });
      setSimMsg({ ok: false, text: ae.status === 409 ? `Nothing run (409): ${ae.message}` : `Run failed: ${ae.message}` });
    } finally {
      setSimBusy(false);
    }
  }, [client, reloadSummary, reloadQueue, reloadLookback]);

  const value = useMemo<ShellDataCtx>(
    () => ({
      summary: session ? summary : null,
      summaryError: session ? summaryError : null,
      summaryAt: session ? summaryAt : null,
      reloadSummary,
      queue: session ? queue : null,
      queueError: session ? queueError : null,
      lookbackOpen: session && lookback ? lookback.filter((i) => i.status === "OPEN").length : null,
      demo: !!session && demo,
      operator,
      simBusy,
      simMsg,
      clearSimMsg: () => setSimMsg(null),
      runNextNight,
    }),
    [session, summary, summaryError, summaryAt, reloadSummary, queue, queueError, lookback, demo, operator, simBusy, simMsg, runNextNight],
  );
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useShellData(): ShellDataCtx {
  const v = useContext(Ctx);
  if (!v) throw new Error("useShellData outside ShellDataProvider");
  return v;
}

/** Units inside an open veto window, and the earliest deadline among them (database clock, from the queue). */
export function openVetoWindows(queue: QueueCluster[] | null, now: number): { units: number; deadline: string | null } {
  let units = 0;
  let deadline: string | null = null;
  for (const c of queue ?? []) {
    if (!c.veto_deadline) continue;
    const t = Date.parse(c.veto_deadline);
    if (Number.isNaN(t) || t <= now) continue;
    units += c.unit_count;
    if (!deadline || t < Date.parse(deadline)) deadline = c.veto_deadline;
  }
  return { units, deadline };
}
