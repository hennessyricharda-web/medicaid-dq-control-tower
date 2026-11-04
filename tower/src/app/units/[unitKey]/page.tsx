"use client";
/**
 * Decision card (AUDIT §2, wireframe 07): a two-line header band, the evidence column (non-pass first) and a
 * fixed 330 px decision rail that never scrolls away. Behaviour is unchanged from Part E: the stale-card
 * reload, the own-change refresh (ownUnitChangeSince), veto, checker step-up, decide and reveal.
 * TraceSlot and CitationSlot are reserved for P1-1, which fills them without editing this page.
 */
import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, ownUnitChangeSince } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { CARD_POLL_MS } from "@/lib/config";
import { SignIn } from "@/components/SignIn";
import { ErrorNote } from "@/components/ErrorNote";
import { AbcBeforeAfter, AiPanel, Dependencies, ProposedActions, WhyHeld, dispositionCounts, sortDispositions } from "@/components/Evidence";
import { DecisionPanel, type Outcome } from "@/components/DecisionPanel";
import { CheckerFlow } from "@/components/CheckerFlow";
import { RevealPanel } from "@/components/RevealPanel";
import type { DecisionCard, Disposition } from "@/lib/types";
import { Explanation } from "@/components/Explanation";
import { TierBadge, VetoCountdown, VetoPanel } from "@/components/Autonomy";
import { DispositionChip, StateChip, ToneChip } from "@/components/Chip";
import { worstOf } from "@/lib/dispositions";
import { describeMinutes, fmtNum, fmtThreshold, fmtTime, shortHash, slaMinutesFromNow } from "@/lib/format";
import { normMode, tierBinding, whyFromTrace } from "@/lib/why";
import { PageBody, PageHeader } from "@/components/layout/PageHeader";
import { DecisionRail } from "@/components/layout/DecisionRail";
import { Seg } from "@/components/layout/Toolbar";
import { TraceSlot } from "@/components/obs/TraceSlot";
import { CitationSlot } from "@/components/obs/CitationSlot";
import { ExplanationFeedback } from "@/components/ExplanationFeedback";

interface StaleInfo {
  why: string;
  fromVersion: number;
  toVersion: number | null;
  fpChanged: boolean;
  notRecorded: boolean;
}

/** LAYOUT_SYSTEM §7b: record counts of 1–10 show as "<11" in tables. */
function small(n: number): string {
  return n >= 1 && n <= 10 ? "<11" : fmtNum(n);
}

function Crumb() {
  return (
    <span className="crumb">
      <Link href="/">Decision queue</Link> /
    </span>
  );
}

function CardHeader({ card }: { card: DecisionCard }) {
  const m = slaMinutesFromNow(card.sla_due);
  const worst = worstOf(card.dispositions.map((d) => d.disposition));
  const why = whyFromTrace(card.mode_trace, card.required_mode);
  const binding = tierBinding(card.tier, card.required_mode);
  return (
    <div className="cardhead">
      <div className="l">
        <div className="row" style={{ gap: 8, flexWrap: "nowrap", minWidth: 0 }}>
          <Crumb />
          <h1 id="unit-h">{card.unit_key}</h1>
        </div>
        <div className="row" style={{ gap: 6 }}>
          <StateChip value={card.state} />
          <DispositionChip value={worst} />
          <span className="chip t-neutral" title="Required mode">
            Mode {normMode(card.required_mode)}
          </span>
          {why ? (
            <span className="chip t-neutral" title="The first mode-trace step that set the mode">
              {why}
            </span>
          ) : null}
          {card.tier ? (
            binding === false ? (
              <span className="chip t-info" title="A floor or policy holds this unit stricter than the agent's tier ceiling">
                Agent tier {card.tier}, not binding
              </span>
            ) : (
              <TierBadge tier={card.tier} />
            )
          ) : null}
          {card.veto_window ? <VetoCountdown deadline={card.veto_window.deadline} /> : null}
          <span className="meta">
            {card.authorities.join(", ")} · <span className="mono">{card.pipeline}.{card.node}</span> · batch <span className="mono">{card.batch_id}</span>
          </span>
        </div>
      </div>
      <div className="r">
        {card.sla_due === null ? (
          <span className="chip t-neutral" title={card.x_sla_reason ?? undefined}>
            No SLA{card.x_sla_reason ? `: ${card.x_sla_reason}` : ""}
          </span>
        ) : m !== null && m < 0 ? (
          <ToneChip tone="bad" label={`SLA ${describeMinutes(m)}`} />
        ) : (
          <span className="chip t-neutral">SLA {describeMinutes(m)}</span>
        )}
        {card.sla_due ? <span>due {fmtTime(card.sla_due)}</span> : null}
        <span title={`fingerprint ${card.fingerprint}, epoch e${String(card.fp_epoch)}, program ${card.program}`}>
          state_version <span className="mono">{card.state_version}</span> · fingerprint <code>{shortHash(card.fingerprint, 10)}</code> · {card.program}
        </span>
      </div>
    </div>
  );
}

function DispositionRows({ card }: { card: DecisionCard }) {
  const rows = sortDispositions(card.dispositions);
  const nonPass = rows.filter((d) => d.disposition !== "PASS");
  const pass = rows.filter((d) => d.disposition === "PASS");
  const [view, setView] = useState<"nonpass" | "all">(nonPass.length ? "nonpass" : "all");
  const keyOf = (d: Disposition) => `${d.rule_id}:${d.rule_hash}`;
  const [open, setOpen] = useState<string | null>(() => {
    const first = nonPass.find((d) => d.explanation) ?? rows.find((d) => d.explanation);
    return first ? keyOf(first) : null;
  });
  const shown = view === "all" ? rows : nonPass;
  return (
    <section className="sec" aria-labelledby="disp-h">
      <div className="row" style={{ justifyContent: "space-between" }}>
        <h2 id="disp-h" style={{ margin: 0, fontSize: "var(--t-base)" }}>
          Dispositions{" "}
          <span className="muted" style={{ fontWeight: 400, fontSize: "var(--t-sm)" }}>
            {dispositionCounts(card.dispositions)
              .map(([d, n]) => `${n} ${d}`)
              .join(", ")}
          </span>
        </h2>
        <Seg
          label="Dispositions shown"
          value={view}
          options={[
            { value: "nonpass", label: "Non-pass first" },
            { value: "all", label: "All" },
          ]}
          onChange={setView}
        />
      </div>
      <div className="tablewrap">
        <table className="dt">
          <caption className="sr">Rule dispositions, worst first. Select a row to show its explanation and evidence tokens.</caption>
          <thead>
            <tr>
              <th scope="col">Rule</th>
              <th scope="col">Class</th>
              <th scope="col">Priority</th>
              <th scope="col">Disposition</th>
              <th scope="col" className="num">
                Failed / evaluated
              </th>
              <th scope="col" className="num p2">
                Metric vs threshold
              </th>
            </tr>
          </thead>
          <tbody>
            {shown.map((d) => {
              const k = keyOf(d);
              const isOpen = open === k;
              return (
                <DispRow key={k} d={d} open={isOpen} onToggle={() => setOpen(isOpen ? null : k)} />
              );
            })}
          </tbody>
        </table>
      </div>
      {view === "nonpass" && pass.length ? (
        <button type="button" className="linklike passline" onClick={() => setView("all")} style={{ textAlign: "left" }}>
          {pass.length} rule(s) passed: {pass.map((d) => d.rule_id).join(", ")}
        </button>
      ) : null}
    </section>
  );
}

function DispRow({ d, open, onToggle }: { d: Disposition; open: boolean; onToggle: () => void }) {
  return (
    <>
      <tr className={`r${open ? " sel" : ""}`} onClick={onToggle} aria-expanded={open}>
        <td>
          <button type="button" className="linklike mono" onClick={(e) => {
              e.stopPropagation();
              onToggle();
            }} aria-expanded={open} title={d.rule_hash} style={{ textDecoration: "none", color: "var(--text)" }}>
            {d.rule_id}
          </button>
        </td>
        <td>{d.check_class}</td>
        <td>{d.priority}</td>
        <td>
          <DispositionChip value={d.disposition} />
        </td>
        <td className="num">
          <strong>{small(d.failed)}</strong> / {small(d.evaluated)}
        </td>
        <td className="num p2">
          {fmtNum(d.metric)} <span className="muted">vs</span> {fmtThreshold(d.threshold)}
        </td>
      </tr>
      {open ? (
        <tr className="xrow">
          <td colSpan={6}>
            {d.explanation ? <Explanation e={d.explanation} label={`Explanation for ${d.rule_id}`} compact /> : <p className="hint">No explanation for this rule.</p>}
            <div className="hint">
              Rule version <code title={d.rule_hash}>{shortHash(d.rule_hash, 18)}</code> · sample tokens (opaque):{" "}
              {d.sample_tokens.length === 0
                ? "none"
                : d.sample_tokens.map((t) => (
                    <span key={t} className="token" title="Opaque evidence token. Not a value; use Reveal records to inspect records.">
                      {t}
                    </span>
                  ))}
            </div>
          </td>
        </tr>
      ) : null}
    </>
  );
}

function Card({ unitKey }: { unitKey: string }) {
  const { client, signOut } = useAuth();
  const [card, setCard] = useState<DecisionCard | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [stale, setStale] = useState<StaleInfo | null>(null);
  const [outcome, setOutcome] = useState<Outcome | null>(null);
  const shown = useRef<DecisionCard | null>(null);
  const bannerRef = useRef<HTMLDivElement>(null);

  const fetchCard = useCallback(async () => {
    try {
      const c = await client.card(unitKey);
      setError(null);
      return c;
    } catch (e) {
      const ae = e instanceof ApiError ? e : new ApiError(0, { detail: String(e) });
      if (ae.status === 401) signOut();
      setError(ae);
      return null;
    }
  }, [client, unitKey, signOut]);

  const show = (c: DecisionCard) => {
    shown.current = c;
    setCard(c);
  };

  /** Forced reload: replace what is on screen with the server's current card and lock decisions. */
  const forceReload = useCallback(
    async (why: string, notRecorded: boolean) => {
      const prev = shown.current;
      const c = await fetchCard();
      if (c) show(c);
      setOutcome(null);
      setStale({
        why,
        fromVersion: prev?.state_version ?? -1,
        toVersion: c?.state_version ?? null,
        fpChanged: !!prev && !!c && prev.fingerprint !== c.fingerprint,
        notRecorded,
      });
    },
    [fetchCard],
  );

  // Initial load.
  useEffect(() => {
    void fetchCard().then((c) => c && show(c));
  }, [fetchCard]);

  // Poll: if the unit moved under the reviewer, force a reload with a banner.
  useEffect(() => {
    const id = setInterval(async () => {
      const started = Date.now();
      const c = await fetchCard();
      // Read what is on screen AFTER the fetch, and ignore a change this tab made itself (onOutcome refreshes it).
      const prev = shown.current;
      if (!c || !prev || ownUnitChangeSince(unitKey, started)) return;
      if (c.state_version !== prev.state_version || c.fingerprint !== prev.fingerprint) {
        show(c);
        setStale((s) =>
          s ?? {
            why: "The unit changed while you were reviewing it.",
            fromVersion: prev.state_version,
            toVersion: c.state_version,
            fpChanged: c.fingerprint !== prev.fingerprint,
            notRecorded: false,
          },
        );
      }
    }, CARD_POLL_MS);
    return () => clearInterval(id);
  }, [fetchCard, unitKey]);

  useEffect(() => {
    if (stale || outcome) bannerRef.current?.focus();
  }, [stale, outcome]);

  // A link from the queue's "Veto release" lands on the veto panel.
  const loaded = !!card;
  useEffect(() => {
    if (loaded && window.location.hash === "#veto-h") document.getElementById("veto-h")?.scrollIntoView({ block: "start" });
  }, [loaded]);

  const onStale = useCallback((why: string) => void forceReload(why, true), [forceReload]);
  const onOutcome = useCallback(
    async (o: Outcome) => {
      const c = await fetchCard(); // our own change: refresh silently, no stale banner
      if (c) show(c);
      setOutcome(o);
    },
    [fetchCard],
  );

  if (error && !card)
    return (
      <>
        <PageHeader crumb={<Link href="/">Decision queue /</Link>} title={<span className="mono">{unitKey}</span>} />
        <PageBody>
          <ErrorNote error={error} what={`unit ${unitKey}`} />
        </PageBody>
      </>
    );
  if (!card)
    return (
      <>
        <PageHeader crumb={<Link href="/">Decision queue /</Link>} title={<span className="mono">{unitKey}</span>} />
        <PageBody>
          <p aria-live="polite">Loading the decision card&hellip;</p>
        </PageBody>
      </>
    );

  const bindKey = `${card.state_version}:${card.fingerprint}`;
  return (
    <>
      <CardHeader card={card} />
      <div className="sp">
        <section className="pane flex" aria-label="Evidence">
          <div className="detail flat">
            <div ref={bannerRef} tabIndex={-1} aria-live="assertive">
              {stale ? (
                <div role="alert" className="banner t-bad">
                  <h2>{stale.notRecorded ? "Your action was NOT recorded — the card has been reloaded" : "This card was reloaded because it changed"}</h2>
                  <p>
                    {stale.why} state_version {stale.fromVersion} &rarr; {stale.toVersion ?? "unknown"}
                    {stale.fpChanged ? "; the fingerprint also changed (the data is different)" : ""}. Any decision you had started was cleared. Re-read the
                    evidence below before deciding.
                  </p>
                  <button type="button" className="primary" onClick={() => setStale(null)}>
                    I have re-reviewed the current card
                  </button>
                </div>
              ) : null}
              {!stale && outcome ? (
                <div role="status" className={`banner t-${outcome.tone}`}>
                  <h2>{outcome.title}</h2>
                  <p>{outcome.body}</p>
                  <button type="button" onClick={() => setOutcome(null)}>
                    Dismiss
                  </button>
                </div>
              ) : null}
            </div>
            {error ? <ErrorNote error={error} what="the latest version of this card" /> : null}
            <section aria-labelledby="expl-h">
              <h2 id="expl-h" style={{ fontSize: "var(--t-base)", margin: "0 0 2px" }}>
                In plain language
              </h2>
              <Explanation e={card.explanation} label="Card explanation" />
              <CitationSlot explanation={card.explanation} subject={card.unit_key} />
              <ExplanationFeedback subject={card.unit_key} />
            </section>
            <div className="twocol sec">
              <div>
                <WhyHeld card={card} />
              </div>
              <div>
                <AbcBeforeAfter card={card} />
                <Dependencies card={card} />
              </div>
            </div>
            <DispositionRows card={card} />
            {card.tier === "L1" ? (
              <section className="sec" aria-labelledby="l1-h">
                <h2 id="l1-h">Agent recommendation</h2>
                <p className="hint">
                  Suppressed: at tier L1 (Assist) the agent shows evidence only. The proposed actions and hypothesis are withheld so the decision is
                  yours alone.
                </p>
              </section>
            ) : (
              <>
                <div className="sec">
                  <ProposedActions card={card} />
                </div>
                <AiPanel card={card} />
              </>
            )}
          </div>
        </section>
        <DecisionRail width={330} label="Decision rail">
          <VetoPanel
            card={card}
            onDone={(msg, ok) =>
              // Our own change: refresh the card first (as onOutcome does), so the poll never mistakes it for a stale card.
              void onOutcome({ tone: ok ? "info" : "bad", title: ok ? "Veto recorded" : "Veto not recorded", body: msg })
            }
          />
          <CheckerFlow key={`ck-${bindKey}`} card={card} locked={!!stale} onStale={onStale} onOutcome={onOutcome} />
          <DecisionPanel key={`dp-${bindKey}`} card={card} locked={!!stale} onStale={onStale} onOutcome={onOutcome} />
          <RevealPanel card={card} />
          <TraceSlot card={card} />
        </DecisionRail>
      </div>
    </>
  );
}

export default function UnitPage() {
  const params = useParams<{ unitKey: string }>();
  const unitKey = decodeURIComponent(params.unitKey ?? "");
  const { session } = useAuth();
  if (!session)
    return (
      <>
        <PageHeader crumb={<Link href="/">Decision queue /</Link>} title={<span className="mono">{unitKey}</span>} />
        <PageBody>
          <SignIn />
        </PageBody>
      </>
    );
  return <Card key={unitKey} unitKey={unitKey} />;
}
