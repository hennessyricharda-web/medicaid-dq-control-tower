/** Read-only evidence sections of the Decision Card. Nothing here can change state. */
import { Fragment } from "react";
import { DispositionChip, StateChip, ToneChip } from "./Chip";
import { Explanation } from "./Explanation";
import { TierBadge, VetoCountdown } from "./Autonomy";
import { GenericTable } from "./GenericTable";
import { dispositionRank } from "@/lib/dispositions";
import { describeMinutes, fmtNum, fmtThreshold, fmtTime, shortHash, slaMinutesFromNow } from "@/lib/format";
import type { DecisionCard, Disposition } from "@/lib/types";

export function UnitHeader({ card }: { card: DecisionCard }) {
  const m = slaMinutesFromNow(card.sla_due);
  return (
    <section className="panel" aria-labelledby="unit-h">
      <h2 id="unit-h">Unit</h2>
      <p className="mono" style={{ fontSize: "1rem", fontWeight: 650, overflowWrap: "anywhere" }}>
        {card.unit_key}
      </p>
      <dl className="kv">
        <dt>State</dt>
        <dd>
          <StateChip value={card.state} /> <span className="mono">state_version {card.state_version}</span>
        </dd>
        <dt>Fingerprint</dt>
        <dd>
          <code title={card.fingerprint}>{shortHash(card.fingerprint, 16)}</code>{" "}
          <span className="muted">
            epoch <span className="mono">e{String(card.fp_epoch)}</span>
          </span>
        </dd>
        <dt>Batch</dt>
        <dd className="mono">{card.batch_id}</dd>
        <dt>Pipeline / node</dt>
        <dd className="mono">
          {card.pipeline} / {card.node}
        </dd>
        <dt>Program</dt>
        <dd>{card.program}</dd>
        <dt>Authorities</dt>
        <dd>{card.authorities.join(", ")}</dd>
        <dt>Agent tier</dt>
        <dd>
          <TierBadge tier={card.tier} /> {card.veto_window ? <VetoCountdown deadline={card.veto_window.deadline} /> : null}
        </dd>
        <dt>SLA</dt>
        <dd>
          {card.sla_due === null ? (
            <span className="muted">no SLA{card.x_sla_reason ? `: ${card.x_sla_reason}` : ""}</span>
          ) : (
            <>
              {m !== null && m < 0 ? <ToneChip tone="bad" label={describeMinutes(m)} /> : describeMinutes(m)}{" "}
              <span className="muted">({fmtTime(card.sla_due)})</span>
            </>
          )}
        </dd>
      </dl>
    </section>
  );
}

/** F-26: the heading follows the unit's state; only a HELD unit is "held". */
export function whyHeading(state: string): string {
  if (state === "HELD") return "Why this is held";
  if (state === "EVALUATING") return "Why this mode applies";
  return `Why the mode is set (state ${state})`;
}

/** GET /units/{k} `mode_trace`: how the engine set this unit's required mode (SCP-W1-10). */
export function WhyHeld({ card }: { card: DecisionCard }) {
  const trace = Array.isArray(card.mode_trace) ? card.mode_trace : [];
  return (
    <section className="panel" aria-labelledby="why-held-h">
      <h2 id="why-held-h">{whyHeading(card.state)}</h2>
      <p className="hint">
        Required mode <span className="mono">{card.required_mode}</span>
      </p>
      {trace.length ? (
        <ol style={{ margin: "6px 0 0", paddingLeft: 22 }}>
          {trace.map((t, i) => (
            <li key={i} style={{ overflowWrap: "anywhere" }}>
              {t}
            </li>
          ))}
        </ol>
      ) : (
        <p className="muted">No mode trace was recorded for this unit.</p>
      )}
    </section>
  );
}

export function sortDispositions(ds: Disposition[]): Disposition[] {
  const pr = (p: string) => ({ High: 0, Medium: 1, Low: 2 })[p] ?? 3;
  return [...ds].sort((a, b) => dispositionRank(b.disposition) - dispositionRank(a.disposition) || pr(a.priority) - pr(b.priority));
}

export function dispositionCounts(ds: Disposition[]): [string, number][] {
  const m = new Map<string, number>();
  for (const d of ds) m.set(d.disposition, (m.get(d.disposition) ?? 0) + 1);
  return [...m.entries()].sort(([a], [b]) => dispositionRank(b) - dispositionRank(a));
}

export function DispositionsTable({ card }: { card: DecisionCard }) {
  const rows = sortDispositions(card.dispositions);
  return (
    <section className="panel" aria-labelledby="disp-h">
      <h2 id="disp-h">
        Dispositions{" "}
        <span className="sub">
          {dispositionCounts(card.dispositions)
            .map(([d, n]) => `${n} ${d}`)
            .join(" · ")}
        </span>
      </h2>
      <div className="tablewrap">
        <table>
          <caption className="skip">Rule dispositions, worst first</caption>
          <thead>
            <tr>
              <th scope="col">Rule</th>
              <th scope="col">Class</th>
              <th scope="col">Priority</th>
              <th scope="col">Disposition</th>
              <th scope="col" className="num">
                Failed / evaluated
              </th>
              <th scope="col" className="num">
                Metric vs threshold
              </th>
              <th scope="col">Sample tokens (opaque)</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((d) => {
              const bad = dispositionRank(d.disposition) >= 7;
              return (
                <Fragment key={`${d.rule_id}:${d.rule_hash}`}>
                <tr className={bad ? "rowbad" : undefined}>
                  <th scope="row" style={{ background: "transparent", textTransform: "none", letterSpacing: 0, fontSize: "0.92rem", color: "var(--text)" }}>
                    <span className="mono">{d.rule_id}</span>
                    <div className="hint mono" title={d.rule_hash}>
                      {shortHash(d.rule_hash, 18)}
                    </div>
                  </th>
                  <td>{d.check_class}</td>
                  <td>{d.priority}</td>
                  <td>
                    <DispositionChip value={d.disposition} />
                  </td>
                  <td className="num">
                    <strong>{fmtNum(d.failed)}</strong> / {fmtNum(d.evaluated)}
                  </td>
                  <td className="num">
                    {fmtNum(d.metric)} <span className="muted">vs</span> {fmtThreshold(d.threshold)}
                  </td>
                  <td>
                    {d.sample_tokens.length === 0 ? (
                      <span className="muted">none</span>
                    ) : (
                      d.sample_tokens.map((t) => (
                        <span key={t} className="token" title="Opaque evidence token. Not a value; use REVEAL to inspect records.">
                          {t}
                        </span>
                      ))
                    )}
                  </td>
                </tr>
                {d.explanation ? (
                  <tr>
                    <td colSpan={7} style={{ paddingTop: 0 }}>
                      <Explanation e={d.explanation} label={`Explanation for ${d.rule_id}`} compact />
                    </td>
                  </tr>
                ) : null}
                </Fragment>
              );
            })}
          </tbody>
        </table>
      </div>
    </section>
  );
}

export function Dependencies({ card }: { card: DecisionCard }) {
  return (
    <section className="panel" aria-labelledby="dep-h">
      <h2 id="dep-h">Dependencies</h2>
      {card.dependencies.length === 0 ? (
        <p className="muted">No declared dependencies.</p>
      ) : (
        <table>
          <caption className="skip">Upstream dependencies</caption>
          <thead>
            <tr>
              <th scope="col">Domain</th>
              <th scope="col">Policy</th>
              <th scope="col">State</th>
            </tr>
          </thead>
          <tbody>
            {card.dependencies.map((d, i) => {
              const terminal = ["RELEASED", "REJECTED", "SUPERSEDED"].includes(d.state);
              return (
                <tr key={`${d.domain}-${i}`}>
                  <td>{d.domain}</td>
                  <td className="mono">{d.policy}</td>
                  <td>
                    <StateChip value={d.state} />
                    {d.policy === "WAIT" && !terminal ? <div className="hint">WAIT blocks while the depended unit is non-terminal (DESIGN §5.4).</div> : null}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
    </section>
  );
}

export function AbcBeforeAfter({ card }: { card: DecisionCard }) {
  return (
    <section className="panel" aria-labelledby="abc-h">
      <h2 id="abc-h">
        ABC before / after <span className="sub">residual = src_in − (tgt_out + rejected + quarantined + deduplicated + filtered + held)</span>
      </h2>
      <GenericTable rows={card.abc_before_after} caption="ABC before and after the proposed action" empty="No ABC rows supplied for this unit." />
    </section>
  );
}

export function ProposedActions({ card }: { card: DecisionCard }) {
  return (
    <section className="panel" aria-labelledby="pa-h">
      <h2 id="pa-h">
        Proposed actions <span className="sub">dry-run only; nothing has been applied</span>
      </h2>
      {card.proposed_actions.length === 0 ? (
        <p className="muted">No proposed actions.</p>
      ) : (
        <table>
          <caption className="skip">Proposed actions with dry-run counts</caption>
          <thead>
            <tr>
              <th scope="col">Verb</th>
              <th scope="col">Params</th>
              <th scope="col" className="num">
                Rows quarantined
              </th>
              <th scope="col" className="num">
                Chains
              </th>
            </tr>
          </thead>
          <tbody>
            {card.proposed_actions.map((a, i) => {
              const { x_row_seqs, ...rest } = a.params ?? {};
              return (
                <tr key={`${a.verb}-${i}`}>
                  <td className="mono">{a.verb}</td>
                  <td>
                    <code>{JSON.stringify(rest)}</code>
                    {Array.isArray(x_row_seqs) ? <div className="hint">x_row_seqs: {x_row_seqs.length} row(s)</div> : null}
                  </td>
                  {a.dry_run ? (
                    <>
                      <td className="num">{fmtNum(a.dry_run.rows_quarantined)}</td>
                      <td className="num" title={a.dry_run.chains === null ? a.dry_run.x_chains_reason : undefined}>
                        {a.dry_run.chains === null ? <span className="muted">{a.dry_run.x_chains_reason ?? "not computed"}</span> : fmtNum(a.dry_run.chains)}
                      </td>
                    </>
                  ) : (
                    <td className="muted" colSpan={2}>
                      No dry run{a.x_dry_run_reason ? `: ${a.x_dry_run_reason}` : ""}
                    </td>
                  )}
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
    </section>
  );
}

/** Collapsed by default. No accept / apply / copy control by design. */
export function AiPanel({ card }: { card: DecisionCard }) {
  const h = card.llm_hypothesis;
  const known = new Set(card.dispositions.map((d) => d.rule_id));
  return (
    <details className="ai" style={{ marginBottom: 14 }}>
      <summary>AI suggestion — not evidence, not a decision</summary>
      <div>
        {h ? (
          <>
            <p className="hint">
              A closed-taxonomy hypothesis from the configured provider. It is not evidence and it does not decide anything. Base your decision on the
              dispositions, ABC and dry-run above.
            </p>
            <dl className="kv">
              <dt>Provider</dt>
              <dd>{h.provider}</dd>
              <dt>Category</dt>
              <dd className="mono">{h.category}</dd>
              <dt>Confidence band</dt>
              <dd>{h.confidence_band}</dd>
              <dt>Cited rules</dt>
              <dd>
                {h.cited_rules.map((r) => (
                  <div key={r} className="mono">
                    {r}
                    {known.has(r) ? "" : " — not among this card’s dispositions"}
                  </div>
                ))}
              </dd>
            </dl>
          </>
        ) : (
          <p className="muted">No AI suggestion for this unit.</p>
        )}
      </div>
    </details>
  );
}
