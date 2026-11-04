/**
 * Mock of dqagent/api/obs_routes.py (Part F P1-1 §F): runs, the run tree, OTLP export, unit traces, citations and
 * guardrail events, in exactly the shapes of dqagent/api/contracts/obs.py (lib/types/obs.ts).
 *
 * The traces are DERIVED from the mock's own state on every request, never free-standing fixtures:
 *   - one NIGHT root per simulated night that has run (files, intake / seal / abc / monitor / evaluate, the release
 *     checks of that night's units, the summary counts);
 *   - one EXPLAIN root per issued unit explanation (llm.explain + llm.validate, a TEMPLATE llm call, a SUPPRESSED
 *     guardrail when small cells were suppressed), plus the SCRIPTED provider text that was rejected and fell back.
 * Scope follows the API: engine-wide roots (NIGHT: empty authorities) and SCOPE_DENIED rows are read only with the
 * audit or operations duty; a scoped reviewer sees only in-scope spans, and hidden steps are one placeholder flag.
 * source_mode is never LIVE here: the mock calls no model.
 */
import type { DecisionCard, Me, SimRunSummary } from "../../types";
import type { Citation, Guardrail, LlmCall, ReleaseCheck, Run, RunEvent, Span, SpanAttr } from "../../types/obs";
import { ISSUED, MOCK_STATE, fx } from "../fixtures";

type R = { status: number; json: unknown };
const ok = (json: unknown): R => ({ status: 200, json });
const err = (status: number, detail: unknown): R => ({ status, json: { detail } });

const HIDDEN_LABEL = "steps outside your scope are hidden";
const INTAKE = "AUTH_INTAKE";
const MAX_LIMIT = 200;
/** dqagent/obs/explain_service.py FAIL_MODE: these writes fail closed. */
const CLOSED = new Set(["SCOPE_DENIED", "ENTITLEMENT_REFUSED", "CHECK_REJECTED", "RECOVERY_REFUSED"]);
const T0 = Date.now();
const DIGEST = { config: fx("mock:config_digest"), code: fx("mock:code_digest") };
const VERSIONS = { prompt_version: `p-${fx("prompt", 12)}`, check_version: `c-${fx("check", 12)}`, template_version: `t-${fx("template", 12)}` };
/** obs_routes.py NO_MODEL (F-35 / P2-1): TEMPLATE and SCRIPTED rows are labelled; the mock calls no model, so
 * RECORDED's own label never appears here, same as the real server would never show it for a mock-only run. */
const NO_MODEL: Record<string, string> = { TEMPLATE: "no model is called: 0 tokens", SCRIPTED: "no model is called: 0 tokens", RECORDED: "no model is called: recorded token counts" };

interface MSpan {
  span: Span;
  authorities: string[];
}
interface MRun {
  run: Run;
  authorities: string[];
  events: RunEvent[];
  spans: MSpan[];
  span_count: number;
}

export interface ObsCtx {
  me: Me;
  cards: DecisionCard[];
  nights: SimRunSummary[];
  /** A card decorated as GET /units/{k} serves it (its explanation and per-disposition explanations). */
  decorated: (c: DecisionCard) => DecisionCard;
}

/** SCOPE_DENIED rows written by the mock (audit only), kept for this tab. */
const denied: Guardrail[] = [];
let deniedSeq = 900000;

const iso = (ms: number) => new Date(ms).toISOString();
const sid = (trace: string, name: string, ord: number) => fx(`${trace}|${name}|${ord}`, 16);

function elevated(me: Me) {
  return me.duties.some((d) => d.startsWith("audit:") || d.startsWith("operations:"));
}
function isAudit(me: Me) {
  return me.duties.some((d) => d.startsWith("audit:"));
}
/** dq_api RLS: a NON-EMPTY authority set inside the caller's scope. */
function inScope(me: Me, auths: string[]) {
  const a = auths.filter((x) => x !== INTAKE);
  return auths.length > 0 && a.every((x) => me.authorities.includes(x));
}

class Builder {
  spans: MSpan[] = [];
  private ord = 0;
  constructor(
    private trace: string,
    private t: number,
  ) {}
  add(
    name: string,
    o: { parent?: string | null; seat?: string | null; subject?: string | null; ms?: number; attrs?: Record<string, SpanAttr>; refs?: string[]; auths?: string[]; status?: Span["status"]; llm?: LlmCall | null; guardrails?: Omit<Guardrail, "trace_id" | "span_id">[]; checks?: ReleaseCheck[] },
  ): string {
    const ordinal = this.ord++;
    const id = sid(this.trace, `${name}|${o.subject ?? ""}`, ordinal);
    const start = this.t;
    const end = start + (o.ms ?? 40);
    this.t = end + 5;
    this.spans.push({
      authorities: o.auths ?? [],
      span: {
        span_id: id,
        parent_span_id: o.parent ?? null,
        ordinal,
        name,
        seat: o.seat ?? null,
        subject: o.subject ?? null,
        status: o.status ?? "OK",
        started_at: iso(start),
        ended_at: iso(end),
        attrs: o.attrs ?? {},
        refs: o.refs ?? [],
        linked: o.checks ? { release_checks: o.checks } : {},
        llm_call: o.llm ?? null,
        guardrails: (o.guardrails ?? []).map((g) => ({ ...g, trace_id: this.trace, span_id: id })),
      },
    });
    return id;
  }
}

let gid = 0;
let callId = 0;
function guard(code: string, subject: string, detail: Record<string, number>, seat: string | null, at: string): Omit<Guardrail, "trace_id" | "span_id"> {
  gid += 1;
  return { id: gid, code, subject, seat, detail, fail_mode: CLOSED.has(code) ? "closed" : "open", at };
}

const RELATION: [RegExp, string][] = [
  [/^837P/, "bronze.encounter_header"],
  [/^834/, "bronze.enrollment_834"],
  [/^ie_extract/, "bronze.ie_segment"],
];
const relOf = (file: string) => RELATION.find(([re]) => re.test(file))?.[1] ?? "raw.file";
/** dqagent/engine/intake.py feed_of, reduced to the mock's own 3 fixture filename patterns. */
const FEED: [RegExp, string][] = [
  [/^837P/, "837P"],
  [/^834/, "834"],
  [/^ie_extract/, "IE"],
];
const feedOf = (file: string) => FEED.find(([re]) => re.test(file))?.[1] ?? "UNROUTED";
const stripUnit = (id: string) => id.replace(/^unit:/, "");

function nightRun(r: SimRunSummary): MRun {
  const date = r.night_date.replace(/-/g, "");
  const trace = `NIGHT-${MOCK_STATE}-${date}-a1`;
  const t0 = Date.parse(r.started_at);
  const b = new Builder(trace, t0);
  r.files.forEach((f, i) => {
    const batch = `B-${MOCK_STATE}-${date}-${String(i + 1).padStart(2, "0")}`;
    const bref = `batch:${batch}`;
    const rel = relOf(f);
    const units = f.startsWith("837P") ? r.counts.units_created.n : Math.max(1, Math.round(r.counts.units_created.n / r.files.length));
    // runtime.py:1666-1669 "I5: the span names the FEED, never the file name" — name "file", subject feed:<feed>.
    const file = b.add("file", { seat: "INTAKE", subject: `feed:${feedOf(f)}`, ms: 20, attrs: { ingest_state: "NEW", routed: true } });
    b.add("intake", { parent: file, seat: "INTAKE", subject: bref, ms: 900, attrs: { files: 1, abc_rows: 4, feed_items: r.counts.feed_items.n } });
    b.add("seal", { parent: file, seat: "INTAKE", subject: bref, ms: 220, attrs: { units, relations: 1 } });
    b.add("abc", { parent: file, seat: "INTAKE", subject: bref, ms: 310, attrs: { rows: 6, late_loads: 0, gating_failed: r.counts.abc_breaks.gating > 0 } });
    b.add("monitor", { parent: file, seat: "MONITOR", subject: bref, ms: 180, attrs: { rows: 12 } });
    b.add(`evaluate rel:${rel}`, {
      parent: file,
      seat: "EVALUATOR",
      subject: `rel:${rel}`,
      ms: 2400,
      attrs: { summary: { units, held: r.counts.held.n, pending: r.counts.pending_dependency.n } },
    });
    for (const seat of ["INTAKE", "EVALUATOR", "TRIAGE"]) b.add(`seat:${seat}`, { parent: file, seat, subject: bref, ms: 60 });
  });
  b.add("missing_deliveries", { seat: "INTAKE", ms: 80, attrs: { opened: r.counts.feed_items.n } });
  if (r.counts.monitor_signals.n) b.add("monitor_close", { seat: "MONITOR", ms: 400 });
  const drain = b.add("drain[0]", { ms: 30 });
  const auths = ["AUTH_MEDICAID"];
  const check = (ids: string[], okv: boolean, reasons: string[]) => {
    for (const id of ids) {
      const key = stripUnit(id);
      b.add("release_check", {
        parent: drain,
        seat: "PUBLISHER",
        subject: `unit:${key}`,
        ms: 12,
        auths,
        attrs: { ok: okv, reasons: reasons.length },
        checks: [{ unit_key: key, ok: okv, reasons }],
      });
    }
  };
  check(r.counts.released_autonomously.ids, true, []);
  check(r.counts.held.ids, false, ["4: recomputed mode is H_IN, above the H_ON ceiling"]);
  const finished = r.finished_at ? Date.parse(r.finished_at) : t0 + 95_000;
  const deadline = new Date(finished + 10 * 60_000).toISOString();
  check(r.counts.veto_windows_opened.ids, false, [`5: veto window open until ${deadline} (database clock)`]);
  b.add("summary", { ms: 40, attrs: { counts: Object.fromEntries(Object.entries(r.counts).map(([k, v]) => [k, v.n])) } });
  const end = finished;
  return {
    run: {
      trace_id: trace,
      kind: "NIGHT",
      parent_trace_id: null,
      state: MOCK_STATE,
      pipeline_id: null,
      night_date: r.night_date,
      attempt: 1,
      person_id: r.person_id,
      config_digest: DIGEST.config,
      code_digest: DIGEST.code,
      deployment: "demo",
      source_mode: null,
      started_at: r.started_at,
    },
    authorities: [],
    events: [
      { event: "STARTED", error_class: null, at: r.started_at },
      { event: "COMPLETED", error_class: null, at: iso(Math.max(end, t0 + 1000)) },
    ],
    spans: b.spans,
    span_count: b.spans.length,
  };
}

function explainRun(subject: string, auths: string[], e: NonNullable<DecisionCard["explanation"]>, at: number, scripted: boolean, kind = "unit_card"): MRun {
  const trace = `EXP-${fx(`${subject}|${e.facts_hash}|pcd`, 16)}-1`;
  const b = new Builder(trace, at);
  const reg = ISSUED.get(`${e.facts_hash}:${e.text_hash}`);
  const sref = /^(batch|rel|rule|unit|file|cr|dec|pipeline|taskgate|abc|results|decisions|units|trace):[A-Za-z0-9_.:@|/+=-]{1,200}$/.test(subject) ? subject : null;
  callId += 1;
  const call: LlmCall = scripted
    ? {
        call_id: callId,
        kind: "monitor_signal",
        provider: "anthropic",
        model: null,
        transport: "scripted",
        source_mode: "SCRIPTED",
        ...VERSIONS,
        facts_hash: e.facts_hash,
        verdict: "REJECT",
        reason_codes: ["NUMBER_UNBOUND"],
        output_text: null,
        input_tokens: null,
        output_tokens: null,
        latency_ms: 3,
        finish_reason: null,
      }
    : {
        call_id: callId,
        kind,
        provider: "template",
        model: null,
        transport: "none",
        source_mode: "TEMPLATE",
        ...VERSIONS,
        facts_hash: e.facts_hash,
        verdict: "NOT_CHECKED",
        reason_codes: [],
        output_text: e.text,
        input_tokens: null,
        output_tokens: null,
        latency_ms: null,
        finish_reason: null,
      };
  b.add("llm.explain", { seat: "explainer", subject: sref, auths, ms: scripted ? 3 : 2, attrs: { source_mode: call.source_mode, transport: call.transport }, llm: call });
  const g: Omit<Guardrail, "trace_id" | "span_id">[] = [];
  const t = iso(at + 10);
  if (reg?.suppressed) g.push(guard("SUPPRESSED", subject, { fields: reg.suppressed }, "explainer", t));
  if (scripted) {
    g.push(guard("CHECK_REJECTED", subject, { NUMBER_UNBOUND: 1 }, "explainer", t));
    g.push(guard("FALLBACK", subject, { no_output: 0, rejected: 1 }, "explainer", t));
  }
  b.add("llm.validate", {
    seat: "explainer",
    subject: sref,
    auths,
    ms: 1,
    attrs: { verdict: call.verdict, fact_check: e.fact_check, reason_codes: call.reason_codes.length },
    guardrails: g,
  });
  return {
    run: {
      trace_id: trace,
      kind: "EXPLAIN",
      parent_trace_id: null,
      state: MOCK_STATE,
      pipeline_id: null,
      night_date: null,
      attempt: 1,
      person_id: null,
      config_digest: fx(`pcd:${call.source_mode}`),
      code_digest: DIGEST.code,
      deployment: "demo",
      source_mode: call.source_mode,
      started_at: iso(at),
    },
    authorities: auths,
    events: [
      { event: "STARTED", error_class: null, at: iso(at) },
      { event: "COMPLETED", error_class: null, at: iso(at + 20) },
    ],
    spans: b.spans,
    span_count: b.spans.length,
  };
}

function world(ctx: ObsCtx): MRun[] {
  gid = 0;
  callId = 0;
  const runs: MRun[] = ctx.nights.map(nightRun);
  ctx.cards.forEach((c0, i) => {
    const c = ctx.decorated(c0);
    const at = T0 - (i + 1) * 61_000;
    if (c.explanation?.fact_check === "TEMPLATE") runs.push(explainRun(`unit:${c.unit_key}`, c.authorities, c.explanation, at, false));
    c.dispositions.forEach((d, j) => {
      // app.py: each disposition is explained under subject unit:<key>|rule:<rule_id>
      if (d.explanation?.fact_check === "TEMPLATE")
        runs.push(explainRun(`unit:${c.unit_key}|rule:${d.rule_id}`, c.authorities, d.explanation, at + (j + 1) * 1_000, false, "rule_disposition"));
    });
  });
  // The monitor text whose SCRIPTED provider wording failed the check (the FALLBACK chip on Monitors).
  for (const [ref, v] of ISSUED) {
    if (!v.fallback) continue;
    const [facts_hash, text_hash] = ref.split(":") as [string, string];
    const relation = typeof v.facts.relation === "string" ? v.facts.relation : "unknown";
    const metric = typeof v.facts.metric === "string" ? v.facts.metric : "";
    // app.py: monitor explanations use subject rel:<relation>|<metric>|<key>|<period>
    runs.push(
      explainRun(`rel:${relation}|${metric}||`, ["AUTH_MEDICAID"], { text: "", source: "template", fact_check: "FALLBACK", facts_hash, text_hash }, T0 - 30 * 60_000, true),
    );
  }
  return runs;
}

function visibleRun(me: Me, r: MRun) {
  return elevated(me) || inScope(me, r.authorities);
}
function spansFor(me: Me, r: MRun, expand: boolean): { spans: Span[]; hidden: number } {
  const vis = r.spans.filter((s) => elevated(me) || inScope(me, s.authorities));
  const spans = vis.map((s) => ({
    ...s.span,
    linked: expand ? s.span.linked : {},
    guardrails: s.span.guardrails.filter((g) => isAudit(me) || g.code !== "SCOPE_DENIED"),
  }));
  return { spans, hidden: !elevated(me) && vis.length < r.spans.length ? 1 : 0 };
}

function deny(me: Me, subject: string, what: string): R {
  deniedSeq += 1;
  denied.push({ id: deniedSeq, trace_id: null, span_id: null, code: "SCOPE_DENIED", subject, seat: "api", detail: { requests: 1 }, fail_mode: "closed", at: new Date().toISOString() });
  void me;
  return err(404, `${what} not found`);
}

const b64 = (v: unknown) => btoa(JSON.stringify(v)).replace(/\+/g, "-").replace(/\//g, "_");
function cursorIn(c: string | null): string[] | null | "bad" {
  if (!c) return null;
  try {
    const v = JSON.parse(atob(c.replace(/-/g, "+").replace(/_/g, "/")));
    if (Array.isArray(v) && v.length === 2 && v.every((x) => typeof x === "string")) return v as string[];
  } catch {
    /* fall through */
  }
  return "bad";
}
const BAD_CURSOR = () => err(422, { status: "BAD_CURSOR", reason: "cursor is not one this server issued" });
const limitOf = (url: URL, d: number) => Math.max(1, Math.min(MAX_LIMIT, Number(url.searchParams.get("limit") || d)));

/** {fact_key: evidence_ref}, mirroring dqagent/llm/facts.py fact_sources (own object, else the subject object). */
const REF_OF: Record<string, string> = { unit_key: "unit", relation: "rel", rule_id: "rule", cr_id: "cr", decision_id: "dec", batch_id: "batch", file: "file", pipeline_id: "pipeline", run_id: "trace", lookback_id: "unit" };
const SUBJECT_ORDER = ["unit_key", "decision_id", "cr_id", "rule_id", "batch_id", "lookback_id", "run_id", "file", "relation"];
function factSources(facts: Record<string, unknown>): Record<string, string> {
  const own: Record<string, string> = {};
  for (const [k, v] of Object.entries(facts)) if (REF_OF[k] && v !== null && v !== "" && /^[A-Za-z0-9_.:@|/+=-]{1,200}$/.test(String(v))) own[k] = `${REF_OF[k]}:${String(v)}`;
  const subj = SUBJECT_ORDER.map((k) => own[k]).find(Boolean) ?? null;
  const out: Record<string, string> = {};
  for (const k of Object.keys(facts).sort()) {
    if (k === "explanation_kind" || k === "part2_withheld") continue;
    const ref = own[k] ?? subj;
    if (ref) out[k] = ref;
  }
  return out;
}

export function mockObs(ctx: ObsCtx, method: string, url: URL): R | null {
  if (method !== "GET") return null;
  const p = url.pathname;
  const me = ctx.me;
  const isObs =
    p === "/runs" ||
    p.startsWith("/runs/") ||
    /^\/units\/[^/]+\/trace$/.test(p) ||
    /^\/explanations\/.+\/citations$/.test(p) ||
    p === "/guardrail-events" ||
    p === "/llm/usage";
  if (!isObs) return null;
  if (!elevated(me) && me.authorities.length === 0) return err(403, "no effective release duty (token claim AND roster) in any authority");
  const runs = world(ctx);

  if (p === "/runs") {
    const cur = cursorIn(url.searchParams.get("cursor"));
    if (cur === "bad") return BAD_CURSOR();
    const kind = url.searchParams.get("kind");
    const pipeline = url.searchParams.get("pipeline");
    const night = url.searchParams.get("night");
    const limit = limitOf(url, 50);
    const rows = runs
      .filter((r) => visibleRun(me, r))
      .filter((r) => (!kind || r.run.kind === kind) && (!pipeline || r.run.pipeline_id === pipeline) && (!night || r.run.night_date === night))
      .sort((a, b) => b.run.started_at.localeCompare(a.run.started_at) || b.run.trace_id.localeCompare(a.run.trace_id))
      .filter((r) => !cur || r.run.started_at < cur[0]! || (r.run.started_at === cur[0] && r.run.trace_id < cur[1]!));
    const page = rows.slice(0, limit);
    const last = page[page.length - 1];
    return ok({
      runs: page.map((r) => ({
        trace_id: r.run.trace_id,
        kind: r.run.kind,
        state: r.run.state,
        pipeline_id: r.run.pipeline_id,
        night_date: r.run.night_date,
        attempt: r.run.attempt,
        status: r.events[r.events.length - 1]?.event ?? null,
        started_at: r.run.started_at,
        span_count: r.span_count,
        source_mode: r.run.source_mode,
      })),
      next: rows.length > limit && last ? b64([last.run.started_at, last.run.trace_id]) : null,
    });
  }

  // ---------------------------------------------------------------------------------------------------- F-35 / P2-1
  if (p === "/llm/usage") {
    const days = Math.max(1, Math.min(366, Number(url.searchParams.get("days") || 30)));
    const since = T0 - days * 86_400_000;
    const seen: { call: LlmCall; atMs: number }[] = [];
    for (const r of runs) {
      if (r.run.kind !== "EXPLAIN" || !visibleRun(me, r)) continue;
      for (const s of r.spans) {
        if (!s.span.llm_call || !(elevated(me) || inScope(me, s.authorities))) continue;
        const atMs = Date.parse(s.span.started_at);
        if (atMs >= since) seen.push({ call: s.span.llm_call, atMs });
      }
    }
    const groups = new Map<string, { call: LlmCall; atMs: number }[]>();
    for (const x of seen) {
      const k = `${iso(x.atMs).slice(0, 10)}|${x.call.provider}|${x.call.transport}|${x.call.source_mode}`;
      groups.set(k, [...(groups.get(k) ?? []), x]);
    }
    const pct = (xs: number[], q: number) => (xs.length ? xs[Math.min(xs.length - 1, Math.floor(q * xs.length))]! : null);
    const rows = [...groups.entries()]
      .map(([k, xs]) => {
        const [day, provider, transport, source_mode] = k.split("|") as [string, string, string, string];
        const verdicts: Record<string, number> = {};
        for (const x of xs) verdicts[x.call.verdict] = (verdicts[x.call.verdict] ?? 0) + 1;
        const lat = xs.map((x) => x.call.latency_ms).filter((n): n is number => n !== null).sort((a, b) => a - b);
        return {
          day,
          provider,
          transport,
          source_mode,
          calls: xs.length,
          verdicts,
          input_tokens: xs.reduce((a, x) => a + (x.call.input_tokens ?? 0), 0),
          output_tokens: xs.reduce((a, x) => a + (x.call.output_tokens ?? 0), 0),
          latency_ms_p50: pct(lat, 0.5),
          latency_ms_p95: pct(lat, 0.95),
          tokens_label: NO_MODEL[source_mode] ?? null,
        };
      })
      .sort((a, b) => b.day.localeCompare(a.day) || a.provider.localeCompare(b.provider) || a.transport.localeCompare(b.transport) || a.source_mode.localeCompare(b.source_mode));
    return ok({ days, rows });
  }

  if (p === "/runs/diff") {
    const aId = url.searchParams.get("a") ?? "";
    const bId = url.searchParams.get("b") ?? "";
    const ra = runs.find((x) => x.run.trace_id === aId);
    const rb = runs.find((x) => x.run.trace_id === bId);
    if (!ra) return err(404, "run not found (a)");
    if (!rb) return err(404, "run not found (b)");
    if (!visibleRun(me, ra)) return deny(me, `trace:${aId}`, "run");
    if (!visibleRun(me, rb)) return deny(me, `trace:${bId}`, "run");
    const { spans: sa, hidden: hiddenA } = spansFor(me, ra, true);
    const { spans: sb, hidden: hiddenB } = spansFor(me, rb, true);
    // dqagent/api/obs_routes.py _keyed: a step's identity across runs is its parent path of names, plus subject.
    const keyed = (spans: Span[]): Map<string, Span> => {
      const byId = new Map(spans.map((s) => [s.span_id, s]));
      const pathOf = (s: Span): string => {
        const out: string[] = [];
        const seen = new Set<string>();
        let cur: Span | undefined = s;
        while (cur && !seen.has(cur.span_id)) {
          seen.add(cur.span_id);
          out.push(cur.name);
          cur = cur.parent_span_id ? byId.get(cur.parent_span_id) : undefined;
        }
        return out.reverse().join("/");
      };
      const m = new Map<string, Span>();
      for (const s of spans) {
        let k = `${pathOf(s)}|${s.subject ?? ""}`;
        let n = 1;
        while (m.has(k)) {
          n += 1;
          k = `${pathOf(s)}|${s.subject ?? ""}#${n}`;
        }
        m.set(k, s);
      }
      return m;
    };
    const ka = keyed(sa);
    const kb = keyed(sb);
    const deltas: { key: string; in_a: boolean; in_b: boolean; status: { a: string | null; b: string | null }; verdict: { a: string | null; b: string | null }; fact_check: { a: string | null; b: string | null } }[] = [];
    for (const k of [...new Set([...ka.keys(), ...kb.keys()])].sort()) {
      const x = ka.get(k) ?? null;
      const y = kb.get(k) ?? null;
      const row = {
        key: k,
        in_a: x !== null,
        in_b: y !== null,
        status: { a: x?.status ?? null, b: y?.status ?? null },
        verdict: { a: x?.llm_call?.verdict ?? null, b: y?.llm_call?.verdict ?? null },
        fact_check: { a: (x?.attrs.fact_check as string | undefined) ?? null, b: (y?.attrs.fact_check as string | undefined) ?? null },
      };
      if (!(row.in_a && row.in_b) || row.status.a !== row.status.b || row.verdict.a !== row.verdict.b || row.fact_check.a !== row.fact_check.b) deltas.push(row);
    }
    const gcount = (spans: Span[]) => {
      const out: Record<string, number> = {};
      for (const s of spans) for (const g of s.guardrails) out[g.code] = (out[g.code] ?? 0) + 1;
      return out;
    };
    const ga = gcount(sa);
    const gb = gcount(sb);
    const guardrail_deltas: Record<string, { a: number; b: number }> = {};
    for (const c of new Set([...Object.keys(ga), ...Object.keys(gb)])) {
      const av = ga[c] ?? 0;
      const bv = gb[c] ?? 0;
      if (av !== bv) guardrail_deltas[c] = { a: av, b: bv };
    }
    const checksOf = (spans: Span[]) => {
      const out: Record<string, boolean> = {};
      for (const s of spans) for (const c of s.linked.release_checks ?? []) out[c.unit_key] = c.ok;
      return out;
    };
    const ca = checksOf(sa);
    const cb = checksOf(sb);
    const release_check_deltas = [...new Set([...Object.keys(ca), ...Object.keys(cb)])]
      .filter((u) => ca[u] !== cb[u])
      .sort()
      .map((u) => ({ unit_key: u, a: u in ca ? ca[u]! : null, b: u in cb ? cb[u]! : null }));
    const lastEvent = (r: MRun) => r.events[r.events.length - 1]?.event ?? null;
    return ok({
      a: aId,
      b: bId,
      status: { a: lastEvent(ra), b: lastEvent(rb) },
      spans_only_in_a: [...ka.keys()].filter((k) => !kb.has(k)).length,
      spans_only_in_b: [...kb.keys()].filter((k) => !ka.has(k)).length,
      span_deltas: deltas,
      guardrail_deltas,
      release_check_deltas,
      hidden_out_of_scope: hiddenA || hiddenB ? 1 : 0,
    });
  }

  const rm = p.match(/^\/runs\/([^/]+)(\/otlp)?$/);
  if (rm) {
    const id = decodeURIComponent(rm[1]!);
    const r = runs.find((x) => x.run.trace_id === id);
    if (!r || !/^[A-Z]{3,5}-[A-Za-z0-9_.:-]{1,120}$/.test(id)) return err(404, "run not found");
    if (!visibleRun(me, r)) return deny(me, `trace:${id}`, "run");
    const { spans, hidden } = spansFor(me, r, url.searchParams.get("expand") === "rows" && !rm[2]);
    if (rm[2]) {
      const tid = fx(id, 32);
      const ns = (s: string) => String(Date.parse(s) * 1_000_000);
      const attr = (k: string, v: unknown) =>
        typeof v === "boolean"
          ? { key: k, value: { boolValue: v } }
          : typeof v === "number" && Number.isInteger(v)
            ? { key: k, value: { intValue: String(v) } }
            : typeof v === "number"
              ? { key: k, value: { doubleValue: v } }
              : { key: k, value: { stringValue: typeof v === "object" ? JSON.stringify(v) : String(v) } };
      return ok({
        resourceSpans: [
          {
            resource: { attributes: [attr("service.name", "dq-agent"), attr("dq.trace_id", id), attr("dq.kind", r.run.kind), attr("dq.deployment", r.run.deployment), attr("dq.hidden_out_of_scope", hidden)] },
            scopeSpans: [
              {
                scope: { name: "dqagent.obs.tracer" },
                spans: spans.map((s) => ({
                  traceId: tid,
                  spanId: s.span_id,
                  parentSpanId: s.parent_span_id ?? "",
                  name: s.name,
                  kind: 1,
                  startTimeUnixNano: ns(s.started_at),
                  endTimeUnixNano: ns(s.ended_at),
                  attributes: [
                    ...Object.entries(s.attrs)
                      .sort(([a], [b]) => a.localeCompare(b))
                      .map(([k, v]) => attr(k, v)),
                    ...(s.subject ? [attr("dq.subject", s.subject)] : []),
                    ...(s.seat ? [attr("dq.seat", s.seat)] : []),
                  ],
                  status: { code: s.status === "ERROR" ? 2 : 1 },
                })),
              },
            ],
          },
        ],
      });
    }
    return ok({ run: r.run, events: r.events, spans, hidden_out_of_scope: hidden, hidden_label: hidden ? HIDDEN_LABEL : null });
  }

  const um = p.match(/^\/units\/([^/]+)\/trace$/);
  if (um) {
    const key = decodeURIComponent(um[1]!);
    const ref = `unit:${key}`;
    const all = runs.flatMap((r) => r.spans).filter((s) => s.span.subject === ref || s.span.refs.includes(ref));
    const vis = all.filter((s) => elevated(me) || inScope(me, s.authorities)).map((s) => s.span);
    if (!vis.length && all.length) return deny(me, ref, "unit trace");
    vis.sort((a, b) => a.started_at.localeCompare(b.started_at));
    return ok({ unit_key: key, spans: vis });
  }

  const cm = p.match(/^\/explanations\/(.+)\/citations$/);
  if (cm) {
    const subject = decodeURIComponent(cm[1]!);
    const [fh, th] = (url.searchParams.get("explanation_ref") ?? "").split(":");
    if (!fh || !th || !/^[0-9a-f]{64}$/.test(fh) || !/^[0-9a-f]{64}$/.test(th))
      return err(422, { status: "EXPLANATION_REF", reason: "explanation_ref is <facts_hash>:<text_hash>" });
    const reg = ISSUED.get(`${fh}:${th}`);
    if (!reg || reg.fallback) return err(404, "explanation not recorded"); // a FALLBACK is never cached
    if (subject.startsWith("unit:")) {
      const card = ctx.cards.find((c) => `unit:${c.unit_key}` === subject);
      if (!card) return err(404, "explanation not recorded");
      if (!elevated(me) && !inScope(me, card.authorities)) return deny(me, subject, "explanation");
    }
    const sources = factSources(reg.facts);
    const resolvable = (ref: string | null) => {
      if (!ref) return false;
      if (elevated(me)) return true;
      const [kind, ...rest] = ref.split(":");
      const id = rest.join(":");
      if (kind === "unit") return ctx.cards.some((c) => c.unit_key === id && inScope(me, c.authorities));
      if (kind === "batch") return ctx.cards.some((c) => c.batch_id === id && inScope(me, c.authorities));
      return true;
    };
    const citations: Citation[] = Object.keys(reg.facts)
      .sort()
      .filter((k) => k !== "explanation_kind" && k !== "part2_withheld")
      .map((k) => ({ span: null, fact_key: k, evidence_ref: sources[k] ?? null, resolvable: resolvable(sources[k] ?? null) }));
    return ok({ subject, explanation_ref: { facts_hash: fh, text_hash: th }, label: "generated_from_facts", source_mode: "TEMPLATE", citations });
  }

  if (p === "/guardrail-events") {
    const code = url.searchParams.get("code");
    if (code === "SCOPE_DENIED" && !isAudit(me)) return err(403, "SCOPE_DENIED events are visible to the audit duty only");
    const cur = cursorIn(url.searchParams.get("cursor"));
    if (cur === "bad") return BAD_CURSOR();
    const subject = url.searchParams.get("subject");
    const trace = url.searchParams.get("trace_id");
    const limit = limitOf(url, 100);
    const events = [
      ...runs.flatMap((r) => r.spans.filter((s) => elevated(me) || inScope(me, s.authorities)).flatMap((s) => s.span.guardrails)),
      ...(isAudit(me) ? denied : []),
    ]
      .filter((g) => (!code || g.code === code) && (!subject || g.subject === subject) && (!trace || g.trace_id === trace))
      .filter((g) => !cur || g.id < Number(cur[1]))
      .sort((a, b) => b.id - a.id);
    const page = events.slice(0, limit);
    return ok({ events: page, next: events.length > limit && page.length ? b64(["g", String(page[page.length - 1]!.id)]) : null });
  }
  return null;
}
