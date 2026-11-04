"use client";
import { useEffect, useState } from "react";
import { ApiError } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { refOf, TIER_INFO, tierTone, VETO_REASON_CODES } from "@/lib/partE";
import { fmtTime } from "@/lib/format";
import type { DecisionCard, Tier } from "@/lib/types";
import { ToneChip } from "./Chip";

export function TierBadge({ tier }: { tier: Tier | undefined | null }) {
  if (!tier) return <span className="muted" title="No tier reported">—</span>;
  const i = TIER_INFO[tier];
  return <ToneChip tone={tierTone(tier)} label={`${tier} ${i.name}`} title={`Tier ${tier}: ceiling ${i.ceiling}; ${i.card}`} />;
}

/** Seconds left until `deadline`, re-computed each second against the server's deadline (never extended locally). */
export function useRemaining(deadline: string | null | undefined): number | null {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!deadline) return;
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, [deadline]);
  if (!deadline) return null;
  const t = Date.parse(deadline);
  return Number.isNaN(t) ? null : Math.round((t - now) / 1000);
}

export function VetoCountdown({ deadline }: { deadline: string | null | undefined }) {
  const s = useRemaining(deadline);
  if (s === null) return null;
  if (s <= 0) return <ToneChip tone="neutral" label="veto window closed" title={`Deadline ${fmtTime(deadline!)}`} />;
  const m = Math.floor(s / 60);
  return <ToneChip tone="warn" label={`veto ${m}:${String(s % 60).padStart(2, "0")} left`} title={`Acts at ${fmtTime(deadline!)} unless vetoed`} />;
}

/** L3 / H_ON card: the agent acts at the deadline unless a person vetoes. A veto holds the unit and demotes the tier. */
export function VetoPanel({ card, onDone }: { card: DecisionCard; onDone: (msg: string, ok: boolean) => void }) {
  const { client } = useAuth();
  const [reason, setReason] = useState<string>(VETO_REASON_CODES[0]);
  const [busy, setBusy] = useState(false);
  const w = card.veto_window;
  const left = useRemaining(w?.deadline);
  if (!w) return null;
  // The server accepts a veto until the unit actually releases (database clock), even after the deadline.
  const open = card.state === "EVALUATING";
  async function veto() {
    setBusy(true);
    try {
      const r = await client.veto(card.unit_key, reason, refOf(card.explanation));
      onDone(`Vetoed: the unit is now ${r.state}. The agent's tier for this authority drops to L2 automatically.`, true);
    } catch (e) {
      const ae = e instanceof ApiError ? e : new ApiError(0, { detail: String(e) });
      onDone(ae.status === 409 ? `Veto refused (409): the window has passed and the unit released. ${ae.message}` : `Veto failed: ${ae.message}`, false);
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className="panel" aria-labelledby="veto-h" style={{ borderColor: "var(--warn-bd)", borderWidth: 2 }}>
      <h2 id="veto-h">
        Veto window <VetoCountdown deadline={w.deadline} />
      </h2>
      <p className="hint">
        At tier L3 the agent releases this unit at <strong>{fmtTime(w.deadline)}</strong> (database clock) unless a person vetoes. A veto holds the
        unit for a human decision and lowers the agent to L2 for this authority.
      </p>
      <div className="row">
        <label htmlFor="veto-reason" style={{ margin: 0 }}>
          Reason
        </label>
        <select id="veto-reason" value={reason} onChange={(e) => setReason(e.target.value)} disabled={!open || busy}>
          {VETO_REASON_CODES.map((c) => (
            <option key={c}>{c}</option>
          ))}
        </select>
        <button type="button" className="danger" onClick={() => void veto()} disabled={!open || busy}>
          {busy ? "Vetoing…" : "Veto"}
        </button>
      </div>
      {!open ? (
        <p className="hint">The unit is no longer evaluating; a veto is no longer possible.</p>
      ) : left !== null && left <= 0 ? (
        <p className="hint">The deadline has passed. A veto is still accepted until the publisher actually releases the unit.</p>
      ) : null}
    </section>
  );
}
