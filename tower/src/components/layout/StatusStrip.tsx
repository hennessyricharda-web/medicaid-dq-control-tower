"use client";
/**
 * 28 px status strip (LAYOUT_SYSTEM §1.2). Fixed slots, exception-first: a healthy slot is a glyph and a label,
 * an alarmed slot shows its value with a tint. Slots never reorder. Every count is the viewer's scope (E5).
 * There is no DAG drift slot until L-01 builds drift detection.
 *
 * Screen readers: the strip is a labelled region; only transitions into or out of alarm are announced, through
 * a separate polite live region, never each poll (WCAG 4.1.3).
 */
import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { useAuth } from "@/lib/auth";
import { SUMMARY_POLL_MS } from "@/lib/config";
import { describeMinutes, shortHash } from "@/lib/format";
import type { Summary } from "@/lib/types";
import { openVetoWindows, useShellData } from "./ShellData";

/** DESIGN §7.2 default DENY-propagation SLO is 15 min; the state profile may differ (not exposed by the spine). */
const DENY_SLO_S = 15 * 60;

type SlotTone = "ok" | "plain" | "warn" | "bad" | "crit";
interface Slot {
  id: string;
  label: string;
  value?: string;
  extra?: string;
  tone: SlotTone;
  href: string;
  title: string;
  mono?: boolean;
}

const GLYPH: Record<SlotTone, string> = { ok: "✓", plain: "", warn: "!", bad: "!", crit: "✕" };

function useNow(ms: number): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), ms);
    return () => clearInterval(id);
  }, [ms]);
  return now;
}

function fmtLeft(s: number): string {
  const m = Math.floor(s / 60);
  return `${m}:${String(s % 60).padStart(2, "0")} left`;
}

function ago(ms: number): string {
  const s = Math.max(0, Math.round(ms / 1000));
  if (s < 90) return `${s} s ago`;
  const m = Math.round(s / 60);
  return m < 90 ? `${m} min ago` : `${Math.round(m / 60)} h ago`;
}

export function buildSlots(s: Summary | null, veto: { units: number; deadline: string | null } | null, now: number): Slot[] {
  const dash = "—";
  if (!s) {
    return ["Decisions due", "Veto window", "ABC", "Pipeline order", "Ruleset"].map((label, i) => ({
      id: `s${i}`,
      label,
      value: dash,
      tone: "plain" as SlotTone,
      href: "/",
      title: "Not loaded yet",
    }));
  }
  const slots: Slot[] = [];
  const overdue = typeof s.oldest_sla_remaining_min === "number" && s.oldest_sla_remaining_min < 0;
  slots.push(
    s.decisions_due > 0
      ? {
          id: "due",
          label: "Decisions due",
          value: String(s.decisions_due),
          extra: overdue ? `oldest ${describeMinutes(s.oldest_sla_remaining_min!)}` : undefined,
          tone: overdue ? "bad" : "plain",
          href: "/",
          title: overdue ? "At least one held unit is past its SLA" : "Held units waiting on a person; none past SLA",
        }
      : { id: "due", label: "Decisions due", tone: "ok", href: "/", title: "No decisions due in your scope" },
  );
  if (veto === null) {
    slots.push({ id: "veto", label: "Veto window", value: dash, tone: "plain", href: "/", title: "The queue is not available for your role" });
  } else if (veto.units > 0 && veto.deadline) {
    const left = Math.max(0, Math.round((Date.parse(veto.deadline) - now) / 1000));
    slots.push({
      id: "veto",
      label: "Veto window",
      value: `${veto.units} open`,
      extra: fmtLeft(left),
      tone: "warn",
      href: "/",
      title: "At L3 the agent releases these units when the window closes unless a person vetoes",
    });
  } else {
    slots.push({ id: "veto", label: "Veto window", tone: "ok", href: "/", title: "No veto window is open" });
  }
  slots.push(
    s.abc.out_of_balance > 0
      ? { id: "abc", label: "ABC", value: `${s.abc.out_of_balance} out of balance`, tone: "bad", href: "/abc", title: "Gating ABC failures; their batches hold at H_IN_2" }
      : {
          id: "abc",
          label: "ABC",
          tone: "ok",
          href: "/abc",
          title: `${s.abc.balanced} balanced${s.abc.advisory ? `; ${s.abc.advisory} advisory (non-gating) failure(s), which never hold a batch` : ""}`,
        },
  );
  slots.push(
    s.pipeline_order_ok
      ? { id: "order", label: "Pipeline order", tone: "ok", href: "/pipelines", title: "Pipeline order holds" }
      : { id: "order", label: "Pipeline order", value: "violated", tone: "bad", href: "/pipelines", title: "A pipeline ran out of order: every run is suspect" },
  );
  if (!s.ruleset_version) {
    slots.push({ id: "ruleset", label: "Ruleset", value: "none, every unit holds", tone: "crit", href: "/rules", title: "No effective ruleset (fail closed)" });
  } else if (s.drift) {
    slots.push({ id: "ruleset", label: "Ruleset drift", value: shortHash(s.ruleset_version, 8), tone: "warn", href: "/governance", title: s.drift, mono: true });
  } else {
    slots.push({ id: "ruleset", label: "Ruleset", value: shortHash(s.ruleset_version, 8), tone: "ok", href: "/rules", title: `Effective ruleset ${s.ruleset_version}`, mono: true });
  }
  if (s.deny_propagation_lag_s > DENY_SLO_S) {
    slots.push({ id: "deny", label: "DENY lag", value: `${s.deny_propagation_lag_s} s`, tone: "bad", href: "/audit", title: "DENY propagation is breaching its 900 s objective" });
  }
  return slots;
}

function SignalsPopover({ s, onClose }: { s: Summary; onClose: () => void }) {
  const { session } = useAuth();
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    ref.current?.focus();
    const key = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    const down = (e: MouseEvent) => {
      const t = e.target as HTMLElement;
      if (ref.current && !ref.current.contains(t) && !t.closest(".more")) onClose();
    };
    document.addEventListener("keydown", key);
    document.addEventListener("mousedown", down);
    return () => {
      document.removeEventListener("keydown", key);
      document.removeEventListener("mousedown", down);
    };
  }, [onClose]);
  const genesis = s.genesis_state ?? s.genesis_status;
  const held = s.oldest_held_age_min;
  const sla = s.oldest_sla_remaining_min;
  const rows: [string, string, boolean][] = [
    ["Genesis", s.genesis_label ?? genesis, genesis !== "RATIFIED"],
    ["Held units", String(s.held_units), false],
    ["Decisions due", String(s.decisions_due), false],
    ["Oldest held", held === undefined ? "not reported" : held === null ? "none held" : describeMinutes(held).replace(/^in /, ""), false],
    ["SLA remaining", sla === undefined ? "not reported" : sla === null ? "nothing due" : describeMinutes(sla), typeof sla === "number" && sla < 0],
    ["Assigned to me", String(s.mine), false],
    ["ABC", `${s.abc.balanced} balanced, ${s.abc.out_of_balance} out${s.abc.advisory ? `, ${s.abc.advisory} advisory` : ""}`, s.abc.out_of_balance > 0],
    ["DENY lag", `${s.deny_propagation_lag_s} s (objective 900 s)`, s.deny_propagation_lag_s > DENY_SLO_S],
    ["Pipeline order", s.pipeline_order_ok ? "holds" : "violated", !s.pipeline_order_ok],
    ["Explainer", `${s.explainer?.provider ?? s.llm_provider}; decisions ${s.explainer?.decisions ?? "deterministic"}`, false],
    ["Ruleset", s.ruleset_version ? `${shortHash(s.ruleset_version, 12)}${s.drift ? " (drift)" : ""}` : "none (fail closed)", !s.ruleset_version || !!s.drift],
  ];
  return (
    <div className="sigpop" role="dialog" aria-label="All 11 signals" tabIndex={-1} ref={ref}>
      <div className="row" style={{ justifyContent: "space-between", marginBottom: 6 }}>
        <strong>All 11 signals</strong>
        <button type="button" className="sm ghost" onClick={onClose} aria-label="Close signals">
          ✕
        </button>
      </div>
      <p className="hint" style={{ margin: "0 0 6px" }}>
        Counts cover your authority scope only{session?.me.authorities.length ? `: ${session.me.authorities.join(", ")}` : ""}. The explainer writes
        plain language over facts; it is never in the decision path.
      </p>
      <table>
        <tbody>
          {rows.map(([k, v, alarm]) => (
            <tr key={k}>
              <th scope="row" style={{ width: 130 }}>
                {k}
              </th>
              <td>
                {alarm ? (
                  <span className="chip t-bad">
                    <span className="g" aria-hidden="true">
                      !
                    </span>
                    {v}
                  </span>
                ) : (
                  v
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {s.drift ? <p className="hint">Ruleset drift: {s.drift}. The runtime compiles only the approved configuration.</p> : null}
    </div>
  );
}

export function StatusStrip() {
  const { session } = useAuth();
  const { summary: s, summaryError: error, summaryAt, queue, queueError } = useShellData();
  const now = useNow(1000);
  const [pop, setPop] = useState(false);
  const veto = queueError ? null : openVetoWindows(queue, now);
  const slots = buildSlots(s, veto, now);

  // Announce only transitions into or out of alarm.
  const [announce, setAnnounce] = useState("");
  const prev = useRef<Map<string, boolean> | null>(null);
  const alarmKey = slots.map((x) => `${x.id}:${x.tone === "bad" || x.tone === "crit" || x.tone === "warn"}`).join("|");
  useEffect(() => {
    if (!s) return;
    const cur = new Map(slots.map((x) => [x.id, x.tone === "bad" || x.tone === "crit" || x.tone === "warn"] as [string, boolean]));
    const before = prev.current;
    prev.current = cur;
    if (!before) return;
    const msgs: string[] = [];
    for (const x of slots) {
      const was = before.get(x.id) ?? false;
      const is = cur.get(x.id) ?? false;
      if (is && !was) msgs.push(`${x.label} alarm: ${[x.value, x.extra].filter(Boolean).join(", ")}`);
      if (!is && was) msgs.push(`${x.label} cleared`);
    }
    if (msgs.length) setAnnounce(msgs.join(". "));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [alarmKey, !!s]);

  if (!session) {
    return (
      <section className="strip" aria-label="Operational status">
        <span className="sig">Sign in to see the operational status for your scope.</span>
      </section>
    );
  }
  const age = summaryAt ? now - summaryAt : null;
  const stale = age !== null && age > 2 * SUMMARY_POLL_MS;
  return (
    <section className="strip" aria-label="Operational status">
      {slots.map((x) => (
        <Link key={x.id} href={x.href} className={`sig ${x.tone}`} title={x.title} data-slot={x.id}>
          {GLYPH[x.tone] ? (
            <span className="g" aria-hidden="true">
              {GLYPH[x.tone]}
            </span>
          ) : null}
          <span>{x.label}</span>
          {x.value ? <b className={x.mono ? "mono" : undefined}>{x.value}</b> : null}
          {x.extra ? <span>{x.extra}</span> : null}
          {x.tone === "bad" || x.tone === "crit" || x.tone === "warn" ? <span className="sr"> (attention)</span> : null}
        </Link>
      ))}
      {error && error.status !== 401 ? (
        <span className={`sig ${error.status === 403 ? "warn" : "bad"}`} title={error.message}>
          <span className="g" aria-hidden="true">
            !
          </span>
          Summary <b>{error.status === 403 ? "no release scope" : "unavailable"}</b>
        </span>
      ) : null}
      <span className="fill" />
      <span className={`live${stale ? " stale" : ""}`}>
        <span className="dot" aria-hidden="true" />
        {age === null ? "Loading…" : stale ? `Stale, last updated ${ago(age)}` : `Updated ${ago(age)}`}
      </span>
      <button type="button" className="more" aria-expanded={pop} aria-haspopup="dialog" onClick={() => setPop((v) => !v)} disabled={!s}>
        All 11 signals
      </button>
      {pop && s ? <SignalsPopover s={s} onClose={() => setPop(false)} /> : null}
      <div role="status" aria-live="polite" className="sr">
        {announce}
      </div>
    </section>
  );
}
