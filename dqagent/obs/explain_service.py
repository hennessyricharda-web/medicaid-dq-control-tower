"""ExplainService (Part F P1-1 §E; OBSERVABILITY adjudication I1, I3, I4): cached, traced, guarded explanations.

    svc = ExplainService(cp, cfg=..., deployment=..., state=...)
    svc.explain(kind, facts, *, subject, authorities, masking) -> {text, source, fact_check, facts_hash, text_hash,
                                                                  source_mode, label, cached, explanation_ref}
    svc.issued(subject, facts_hash, text_hash) -> bool   a cached row under the FULL key counts as issued (I2)
The cached row's claims carry each fact's evidence_ref (facts.fact_sources), which obs_routes resolves under the
caller's scope (citations).

The flow (plan §E):
  1. Build the facts and look `explanations` up by the FULL key: (subject, facts_hash, provider_config_digest,
     prompt_version, check_version, template_version).
  2. A hit re-runs the check on the cached text (a provider text: check_result over its stored slot claims; a
     template text: byte-equality with a fresh render), records no guardrails, and is served. A hit that no longer
     checks is not served: it is rendered again (and cannot be re-cached under the same key: the first row wins).
  3. A miss runs an EXPLAIN root with `llm.explain` and `llm.validate` spans, then writes explain_facts,
     prompt_versions, llm_calls and guardrail_events ONCE, in one transaction, as dq_trace_svc.
  4. A FALLBACK is never cached. The explanation is inserted ON CONFLICT DO NOTHING and the WINNING row is re-read
     and served, so two workers racing on one key show one text.
Guardrail events are collected on the result and written once per render (never on a cache hit). Each code
declares how its write fails (FAIL_MODE): the audit-relevant codes (SCOPE_DENIED, ENTITLEMENT_REFUSED,
CHECK_REJECTED) fail CLOSED (ExplainUnavailable, which the api maps to 503); the others fail open (logged, counted
in STATS, the explanation is still shown). llm_calls stores the model's untrusted tokens only as sha256
(token_sha256); `output_text` is the SHOWN text only when it came from the facts (PASS: rendered from slots), never
a rejected model text.
"""
from __future__ import annotations

import hashlib
import json
import logging
import uuid

from dqagent import llm
from dqagent.llm import facts as F
from dqagent.obs import tracer

log = logging.getLogger("dqagent.explain_service")
STATS = {"fail_open_write_failures": 0, "last_error": None, "cache_hits": 0, "renders": 0, "recheck_misses": 0,
         "degraded": 0}

FAIL_CLOSED = "closed"
FAIL_OPEN = "open"
FAIL_MODE = {"SCOPE_DENIED": FAIL_CLOSED, "ENTITLEMENT_REFUSED": FAIL_CLOSED, "CHECK_REJECTED": FAIL_CLOSED,
             "MASKED": FAIL_OPEN, "SUPPRESSED": FAIL_OPEN, "PART2_WITHHELD": FAIL_OPEN, "ADVICE_BLOCKED": FAIL_OPEN,
             "CAUSAL_BLOCKED": FAIL_OPEN, "FALLBACK": FAIL_OPEN, "FLOOR_APPLIED": FAIL_OPEN,
             "CEILING_APPLIED": FAIL_OPEN, "BODY_REJECTED": FAIL_OPEN, "EXPLANATION_MISMATCH": FAIL_OPEN,
             "STEP_UP_REQUIRED": FAIL_OPEN, "RECOVERY_REFUSED": FAIL_CLOSED}
SEAT = "explainer"


class ExplainUnavailable(RuntimeError):
    """A fail-closed guardrail event could not be written: the explanation is not shown (api: 503)."""


def guardrails_of(r: llm.ExplainResult) -> list[tuple[str, dict]]:
    """The guardrail events one render produced: [(code, counts detail)]."""
    out: list[tuple[str, dict]] = []
    m = r.masking or {}
    if m.get("masked"):
        out.append(("MASKED", {"fields": int(m["masked"])}))
    if m.get("suppressed"):
        out.append(("SUPPRESSED", {"fields": int(m["suppressed"])}))
    if m.get("part2_withheld"):
        out.append(("PART2_WITHHELD", {"explanations": 1}))
    codes = list(r.check.reason_codes) if r.check else []
    if r.verdict == "REJECT":
        out.append(("CHECK_REJECTED", {c: 1 for c in codes[:64]}))
        if "ADVICE" in codes:
            out.append(("ADVICE_BLOCKED", {"reasons": sum(1 for c in r.check.reasons if "advice" in c)}))
        if "CAUSAL" in codes:
            out.append(("CAUSAL_BLOCKED", {"reasons": sum(1 for c in r.check.reasons if "causal" in c)}))
    if r.fact_check == "FALLBACK":
        out.append(("FALLBACK", {"no_output": int(r.verdict == "NO_OUTPUT"), "rejected": int(r.verdict == "REJECT")}))
    return out


def _subject_ref(subject: str) -> str | None:
    import re
    from dqagent.cp.trace_schema import REF_RE
    return subject if re.fullmatch(REF_RE, subject) else None


class ExplainService:
    def __init__(self, cp, *, cfg: dict | None = None, deployment: str, state: str, transport=None, env=None):
        self.cp, self.cfg = cp, (cfg if cfg is not None else llm.load_config())
        self.deployment, self.state, self.transport, self.env = deployment, state, transport, env
        llm.transport_object(self.cfg, deployment, transport)          # R4-3: refuse at construction, not per call
        self.pcd = llm.provider_config_digest(self.cfg)
        self._mem: dict = {}                       # key -> cached row this process has read or won (bounded); the
        self._mem_cap = 20000                      # check still re-runs on every read (I1), the DB stays the truth

    def _remember(self, key, row):
        self._mem[key] = row
        if len(self._mem) > self._mem_cap:
            self._mem.pop(next(iter(self._mem)))

    # ---------------------------------------------------------------------------------------------- the cache key
    def key(self, subject: str, facts_hash: str) -> tuple:
        v = llm.VERSIONS
        return (subject, facts_hash, self.pcd, v["prompt_version"], v["check_version"], v["template_version"])

    def _cached(self, key: tuple):
        if key in self._mem:
            return self._mem[key]
        with self.cp.as_role("dq_trace_svc", trace=False) as c:
            row = c.execute("SELECT * FROM dq_ctl.explanations WHERE subject=%s AND facts_hash=%s AND "
                            "provider_config_digest=%s AND prompt_version=%s AND check_version=%s AND "
                            "template_version=%s", key).fetchone()
        if row is not None:
            self._remember(key, dict(row))
        return row

    def _recheck(self, kind: str, row, facts: dict) -> bool:
        """I1: the check re-runs on every cache read."""
        claims = row["claims"] if isinstance(row["claims"], list) else json.loads(row["claims"])
        if row["source_mode"] == "TEMPLATE":
            return llm.render_with_claims(kind, facts)[0] == row["text"]
        res = llm.check.check_result(row["text"], [{"fact_key": c["fact_key"], "value": c["value"]} for c in claims],
                                     facts)
        return res.ok and hashlib.sha256(row["text"].encode()).hexdigest() == row["text_hash"]

    @staticmethod
    def _out(row_or_result, *, cached: bool, source: str, fact_check: str, label: str, facts_hash: str) -> dict:
        text = row_or_result["text"]
        th = hashlib.sha256(text.encode()).hexdigest()
        return {"text": text, "source": source, "fact_check": fact_check, "facts_hash": facts_hash, "text_hash": th,
                "source_mode": row_or_result["source_mode"], "label": label, "cached": cached,
                "explanation_ref": {"facts_hash": facts_hash, "text_hash": th}}

    # ---------------------------------------------------------------------------------------------- explain
    def _degrade(self, degraded: list, what: str, e: Exception):
        """C1: the trace store is down or failing. The explanation is still rendered and checked as usual; the
        degradation is counted (STATS["degraded"]), logged, and returned to the caller as `degraded`."""
        degraded.append(what)
        STATS["degraded"] += 1
        STATS["last_error"] = f"{what}: {type(e).__name__}"[:300]
        log.warning("explainer degraded (%s): %s", what, type(e).__name__)

    def label_of(self, source_mode: str) -> str:
        """The label of a stored row, from ITS source mode (I6), never from today's configuration."""
        provider = (self.cfg.get("provider") or "template").lower()
        from dqagent.llm import transports as T
        return {"TEMPLATE": "built-in template (offline)", "LIVE": T.HttpTransport().label(provider),
                "SCRIPTED": T.ScriptedTransport().label(provider),
                "RECORDED": f"Recorded responses ({T.WIRE.get(provider, provider)} wire format): no model is called"
                }.get(source_mode, source_mode)

    def explain(self, kind: str, facts: dict, *, subject: str, authorities, masking: dict | None = None) -> dict:
        auths = sorted(set(authorities or ()))
        f, _ = F.build_result(kind, F.adapt(kind, facts), classification=masking or self.cfg.get("classification"))
        fh = F.facts_hash(f)
        key = self.key(subject, fh)
        degraded: list[str] = []
        try:
            row = self._cached(key)
        except Exception as e:  # noqa: BLE001 - C1: a cache that cannot be read is a miss, not a 500
            row = None
            self._degrade(degraded, "cache_read", e)
        provider = (self.cfg.get("provider") or "template").lower()
        stale = False
        if row is not None:
            if self._recheck(kind, row, f):
                STATS["cache_hits"] += 1
                tmpl = row["source_mode"] == "TEMPLATE"
                return {**self._out(row, cached=True, source="template" if tmpl else provider,
                                    fact_check="TEMPLATE" if tmpl else "PASS", label=self.label_of(row["source_mode"]),
                                    facts_hash=fh), "degraded": degraded}
            STATS["recheck_misses"] += 1                # I1: a row that fails TODAY's check is never served
            self._mem.pop(key, None)
            stale = True
        return self._render(kind, facts, f, fh, key, subject, auths, masking, stale=stale, degraded=degraded)

    def _mint(self) -> int:
        """One call id from the llm_calls sequence: unique across workers (I4), and the same sequence of ids on a
        fresh control plane, so a trace is reproducible."""
        with self.cp.as_role("dq_trace_svc", trace=False) as c:
            return c.execute("SELECT nextval('dq_ctl.llm_calls_call_id_seq') AS n").fetchone()["n"]

    def _render(self, kind, facts, f, fh, key, subject, auths, masking, *, stale=False, degraded=None) -> dict:
        degraded = degraded if degraded is not None else []
        STATS["renders"] += 1
        base = "EXP-" + hashlib.sha256(f"{subject}|{fh}|{self.pcd}".encode()).hexdigest()[:16]
        try:
            call_id = self._mint()
            trace_id = f"{base}-{call_id}"                     # unique per call, so its span ids are too (I4)
        except Exception as e:  # noqa: BLE001 - C1
            call_id, trace_id = None, f"{base}-x{uuid.uuid4().hex[:12]}"
            self._degrade(degraded, "id_mint", e)
        sref = _subject_ref(subject)
        with tracer.root("EXPLAIN", trace_id, cp=self.cp, state=self.state, authorities=auths,
                         config_digest=self.pcd, deployment=self.deployment) as rt:
            with tracer.span("llm.explain", seat=SEAT, subject=sref, authorities=auths) as sp:
                r = llm.explain_result(kind, facts, cfg=self.cfg, classification=masking, env=self.env,
                                       deployment=self.deployment, transport=self.transport, subject=subject)
                sp.attr("source_mode", r.source_mode).attr("transport", r.transport)
                explain_span = sp.span_id or tracer.span_id_for(trace_id, None, "llm.explain", sref, 0)
            with tracer.span("llm.validate", seat=SEAT, subject=sref, authorities=auths) as sv:
                sv.attr("verdict", r.verdict).attr("fact_check", r.fact_check)
                sv.attr("reason_codes", len(r.check.reason_codes) if r.check else 0)
                validate_span = sv.span_id or tracer.span_id_for(trace_id, None, "llm.validate", sref, 1)
            rt.mark_changed()
        events = guardrails_of(r)
        sources = F.fact_sources(kind, facts)          # beside the facts, never in facts_hash (plan §D)
        cites = [{**c, "evidence_ref": sources.get(c["fact_key"])} for c in r.claims]
        shown = self._write(r, f, trace_id, call_id, explain_span, validate_span, subject, auths, events, key, cites,
                            stale=stale, degraded=degraded)
        if r.fact_check == "FALLBACK":
            return {**r.public(), "label": r.label, "cached": False,          # source_mode: the template's, shown
                    "explanation_ref": {"facts_hash": fh, "text_hash": r.text_hash}, "degraded": degraded}
        tmpl = shown["source_mode"] == "TEMPLATE"
        return {**self._out(shown, cached=False, source="template" if tmpl else r.source,
                            fact_check="TEMPLATE" if tmpl else "PASS",
                            label=r.label if shown is not None and shown.get("fresh") else
                            self.label_of(shown["source_mode"]), facts_hash=fh), "degraded": degraded}

    def _write(self, r: llm.ExplainResult, f, trace_id, call_id, explain_span, validate_span, subject, auths, events,
               key, cites, *, stale: bool, degraded: list) -> dict:
        """Write the render ONCE, in one transaction, and decide in it what is SHOWN: the winning cached row when one
        exists and passes today's check (two workers on one key show one text), else this render. A stale key (its
        row failed today's check, I1) is never served and not re-inserted. llm_calls.output_text is the text shown."""
        comp = r.completion
        usage = (comp.usage if comp is not None else {}) or {}
        closed = any(FAIL_MODE.get(code) == FAIL_CLOSED for code, _ in events)
        fresh = {"text": r.text, "source_mode": r.source_mode, "fresh": True}
        shown = fresh
        try:
            with self.cp.as_role("dq_trace_svc", trace=False) as c:
                c.execute("INSERT INTO dq_ctl.explain_facts(facts_hash, facts) VALUES (%s,%s::jsonb) "
                          "ON CONFLICT DO NOTHING", (r.facts_hash, F.canonical(r.facts)))
                c.execute("INSERT INTO dq_ctl.prompt_versions(prompt_version, system_prompt, check_version, "
                          "template_version) VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                          (r.versions["prompt_version"], llm.SYSTEM_PROMPT, r.versions["check_version"],
                           r.versions["template_version"]))
                if r.fact_check != "FALLBACK" and not stale:              # a FALLBACK is never cached
                    c.execute(
                        "INSERT INTO dq_ctl.explanations(subject, authorities, facts_hash, provider_config_digest, "
                        "prompt_version, check_version, template_version, text, text_hash, source_mode, claims) "
                        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb) ON CONFLICT DO NOTHING",
                        (key[0], auths, key[1], key[2], key[3], key[4], key[5], r.text, r.text_hash, r.source_mode,
                         json.dumps(cites, sort_keys=True, default=str)))
                    won = c.execute("SELECT * FROM dq_ctl.explanations WHERE subject=%s AND facts_hash=%s AND "
                                    "provider_config_digest=%s AND prompt_version=%s AND check_version=%s AND "
                                    "template_version=%s", key).fetchone()
                    if won is not None and won["text_hash"] != r.text_hash and self._recheck(r.kind, won, f):
                        shown = dict(won)                                 # another worker won the key first
                text = shown["text"]
                cols = ("trace_id, span_id, subject, authorities, kind, provider, model, transport, source_mode, "
                        "prompt_version, check_version, template_version, input_digest, facts_hash, output_text, "
                        "text_hash, verdict, reason_codes, token_sha256, input_tokens, output_tokens, latency_ms, "
                        "finish_reason")
                vals = (trace_id, explain_span, subject, auths, r.kind, r.provider,
                        comp.model if comp is not None else None, r.transport, r.source_mode,
                        r.versions["prompt_version"], r.versions["check_version"], r.versions["template_version"],
                        comp.input_digest if comp is not None else None, r.facts_hash, text,
                        hashlib.sha256(text.encode()).hexdigest(), r.verdict,
                        list(r.check.reason_codes) if r.check else [], list(r.check.token_sha256) if r.check else [],
                        usage.get("input_tokens"), usage.get("output_tokens"),
                        comp.latency_ms if comp is not None else None, usage.get("finish_reason"))
                if call_id is not None:
                    cols, vals = "call_id, " + cols, (call_id, *vals)
                c.execute(f"INSERT INTO dq_ctl.llm_calls({cols}) VALUES ({','.join(['%s'] * len(vals))})", vals)
                for code, detail in events:
                    c.execute("INSERT INTO dq_ctl.guardrail_events(trace_id, span_id, code, subject, seat, authorities, "
                              "detail) VALUES (%s,%s,%s,%s,%s,%s,%s::jsonb)",
                              (trace_id, validate_span, code, subject, SEAT, auths, json.dumps(detail, sort_keys=True)))
            if shown is not fresh:
                self._remember(key, shown)
        except Exception as e:
            if closed:
                raise ExplainUnavailable(f"a fail-closed guardrail event could not be written: "
                                         f"{type(e).__name__}") from e
            STATS["fail_open_write_failures"] += 1
            self._degrade(degraded, "write", e)
            log.exception("explanation trace write failed (fail-open) for %s", subject)
            shown = fresh                                 # nothing was committed: show what was rendered
        return shown

    # ---------------------------------------------------------------------------------------------- issued / cites
    def issued(self, subject: str, facts_hash: str, text_hash: str) -> bool:
        """I1 / I2: a cached row counts as issued for check_ref (so issuing works across workers) only under the FULL
        cache key: the same subject and facts, this provider configuration, and today's prompt, check and template
        versions. A row of another subject, or one cached under a superseded version, binds nothing. A store that
        cannot be read binds nothing either (fail closed)."""
        k = self.key(subject, facts_hash)
        try:
            with self.cp.as_role("dq_trace_svc", trace=False) as c:
                return c.execute("SELECT 1 FROM dq_ctl.explanations WHERE subject=%s AND facts_hash=%s AND "
                                 "provider_config_digest=%s AND prompt_version=%s AND check_version=%s AND "
                                 "template_version=%s AND text_hash=%s LIMIT 1", (*k, text_hash)).fetchone() is not None
        except Exception as e:  # noqa: BLE001
            self._degrade([], "issued_read", e)
            return False


def record_guardrail(cp, code: str, subject: str, *, authorities=(), seat: str | None = None,
                     detail: dict | None = None, trace_id: str | None = None, span_id: str | None = None) -> bool:
    """Write ONE guardrail event now, outside any trace buffer, as dq_trace_svc, honouring FAIL_MODE: a fail-closed
    code (SCOPE_DENIED, ENTITLEMENT_REFUSED, CHECK_REJECTED, RECOVERY_REFUSED) raises ExplainUnavailable when the
    write fails; a fail-open code logs and returns False. Empty `authorities` makes the row audit-only.
    N-T0-1: the runtime's recovery refusal calls this with code RECOVERY_REFUSED."""
    if code not in FAIL_MODE:
        raise ValueError(f"unknown guardrail code {code!r}")
    try:
        with cp.as_role("dq_trace_svc", trace=False) as c:
            c.execute("INSERT INTO dq_ctl.guardrail_events(trace_id, span_id, code, subject, seat, authorities, detail) "
                      "VALUES (%s,%s,%s,%s,%s,%s,%s::jsonb)",
                      (trace_id, span_id, code, subject[:400], seat, sorted(set(authorities or ())),
                       json.dumps(detail or {}, sort_keys=True)))
        return True
    except Exception as e:
        if FAIL_MODE[code] == FAIL_CLOSED:
            raise ExplainUnavailable(f"fail-closed guardrail {code} could not be written: {type(e).__name__}") from e
        STATS["fail_open_write_failures"] += 1
        STATS["last_error"] = f"{type(e).__name__}"[:300]
        log.exception("fail-open guardrail %s not written", code)
        return False


PUBLIC_KEYS = ("text", "source", "fact_check", "facts_hash", "text_hash", "source_mode")


def public(e: dict) -> dict:
    """What an api object carries as its `explanation`: llm.explain()'s public dict (the Part E §7 contract) plus
    `source_mode` (F-35) of the text actually SERVED (a cache hit: the cached row's; a render: this render's; a
    FALLBACK: the transport that was tried). Stable across calls (no `cached` flag), so a cache hit and the render
    it repeats are byte-identical. The transport label is reported by /explainer and per call in llm_calls."""
    return {k: e[k] for k in PUBLIC_KEYS if k in e}
