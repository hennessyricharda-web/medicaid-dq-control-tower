"""Observability endpoints (Part F P1-1 §F): runs, the run tree, OTLP export, unit traces, citations, guardrails.

  GET /api/v1/runs?kind&pipeline&night&limit&cursor            -> RunList
  GET /api/v1/runs/{trace_id}?expand=rows                       -> RunDetail
  GET /api/v1/runs/{trace_id}/otlp                              -> OtlpExport (OTLP/JSON of the visible spans)
  GET /api/v1/units/{unit_key}/trace                            -> UnitTrace
  GET /api/v1/explanations/{subject}/citations?explanation_ref= -> Citations   (ref = <facts_hash>:<text_hash>)
  GET /api/v1/guardrail-events?code&subject&trace_id&limit&cursor -> GuardrailList

Scope (I1, C3). A reviewer reads as dq_api under their release scope: RLS returns only rows with a NON-EMPTY
authority set inside that scope. Engine-wide rows (empty authorities: NIGHT, PUBLISH_CYCLE and RECOVER roots) and
everything out of scope are read only by the audit duty (audit:*) or the operations duty (operations:*), as
dq_auditor, after this module has checked the duty. A scoped reviewer sees only in-scope spans; hidden steps are one
placeholder ("steps outside your scope are hidden") with no names, timings or counts (hidden_out_of_scope is 0 or 1).
A request for a run, unit trace or explanation that exists but is out of scope answers 404 exactly as a missing one
does, and writes a SCOPE_DENIED guardrail event with EMPTY authorities, so only the audit duty can read it; that
write fails CLOSED (503). SCOPE_DENIED rows are never returned to a non-audit caller. Citations re-check scope when
an evidence_ref is resolved. Registered by the app (BC) as obs_routes(app, services, principal).
"""
from __future__ import annotations

import base64
import datetime as dt
import hashlib
import json
import re
import threading
import time

from fastapi import Depends, FastAPI, HTTPException, Query

from dqagent.api.contracts import obs as M
from dqagent.cp.trace_schema import REF_RE, TRACE_ID_RE
from dqagent.obs.explain_service import FAIL_MODE, ExplainUnavailable, record_guardrail

HIDDEN_LABEL = "steps outside your scope are hidden"
_TRACE_RE, _REF_RE = re.compile(TRACE_ID_RE), re.compile(REF_RE)
_HASH = re.compile(r"^[0-9a-f]{64}$")
MAX_LIMIT = 200
DENY_EVERY_S = 60          # SCOPE_DENIED: at most one row per (principal, subject) per minute, per process


def _iso(v) -> str | None:
    return None if v is None else (v.isoformat() if hasattr(v, "isoformat") else str(v))


def _cursor_out(*parts) -> str:
    return base64.urlsafe_b64encode(json.dumps([str(p) for p in parts]).encode()).decode()


def _bad_cursor():
    raise HTTPException(400, {"status": "BAD_CURSOR", "reason": "cursor is not one this server issued"})


def _cursor_in(c: str | None, kind: str) -> list | None:
    """A cursor this server issued: ["<iso time>", "<trace id>"] for runs, ["g", "<id>"] for guardrail events.
    Anything else, including a well-formed but crafted one, is a 400 (never a 500 from the database)."""
    if not c:
        return None
    try:
        v = json.loads(base64.urlsafe_b64decode(c.encode()).decode())
    except Exception:  # noqa: BLE001
        _bad_cursor()
    if not (isinstance(v, list) and len(v) == 2 and all(isinstance(x, str) for x in v)):
        _bad_cursor()
    try:
        if kind == "runs":
            dt.datetime.fromisoformat(v[0])
            if not _TRACE_RE.fullmatch(v[1]):
                _bad_cursor()
        elif v[0] != "g" or not v[1].isdigit() or len(v[1]) > 18:
            _bad_cursor()
    except ValueError:
        _bad_cursor()
    return v


def obs_routes(app: FastAPI, services, principal) -> None:

    def elevated(p) -> bool:
        return any(d.startswith(("audit:", "operations:")) for d in p["_duties"])

    def is_audit(p) -> bool:
        return any(d.startswith("audit:") for d in p["_duties"])

    def reader(p):
        """(role, scope): the audit / operations duty reads as dq_auditor; anyone else as dq_api in scope."""
        if elevated(p):
            return services.cp.as_role("dq_auditor")
        if not p["_scope"]:
            raise HTTPException(403, "no effective release duty (token claim AND roster) in any authority")
        return services.cp.as_role("dq_api", p["_scope"])

    denied_at: dict = {}
    deny_lock = threading.Lock()

    def record_denial(p, subject: str):
        """SCOPE_DENIED (audit-only: empty authorities), fail closed (503). At most one row per (principal, subject)
        per DENY_EVERY_S in this process, so one denied caller polling a page cannot flood the audit log."""
        k = (p.get("emp_id"), subject)
        now = time.monotonic()
        with deny_lock:
            if now - denied_at.get(k, -1e9) < DENY_EVERY_S:
                return
            denied_at[k] = now
            if len(denied_at) > 50000:
                denied_at.clear()
        try:
            record_guardrail(services.cp, "SCOPE_DENIED", subject, authorities=(), seat="api", detail={"requests": 1})
        except ExplainUnavailable as e:
            with deny_lock:
                denied_at.pop(k, None)
            raise HTTPException(503, {"status": "UNAVAILABLE", "reason": "audit write failed"}) from e

    def deny(p, subject: str, what: str):
        """The caller sees the same 404 as a miss."""
        record_denial(p, subject)
        raise HTTPException(404, f"{what} not found")

    def exists_anywhere(sql: str, args) -> bool:
        with services.cp.as_role("dq_auditor") as c:
            return c.execute(sql, args).fetchone() is not None

    def visible_codes(p) -> str:
        """SQL: SCOPE_DENIED is filtered IN the query for a non-audit caller, so pages are never short."""
        return "true" if is_audit(p) else "code <> 'SCOPE_DENIED'"

    def guardrail_rows(c, p, where: str, args, *, order: str = "id", limit: int | None = None) -> list[dict]:
        q = (f"SELECT id, trace_id, span_id, code, subject, seat, detail, at FROM dq_ctl.guardrail_events "
             f"WHERE ({where}) AND {visible_codes(p)} ORDER BY {order}" + (" LIMIT %s" if limit else ""))
        rows = c.execute(q, (*args, limit) if limit else args).fetchall()
        return [{"id": r["id"], "trace_id": r["trace_id"], "span_id": r["span_id"], "code": r["code"],
                 "subject": r["subject"], "seat": r["seat"], "detail": dict(r["detail"] or {}),
                 "fail_mode": FAIL_MODE.get(r["code"], "open"), "at": _iso(r["at"])} for r in rows]

    def span_rows(c, p, rows, expand: bool) -> list[dict]:
        """The spans with their llm call, guardrails and (expand) release checks, in three queries in all (no N+1).
        A span's llm call is the one recorded under THAT span of THAT trace (span ids are unique per call, I4)."""
        ids = [s["span_id"] for s in rows]
        calls, guards, checks = {}, {}, {}
        if ids:
            for r in c.execute("SELECT call_id, trace_id, span_id, kind, provider, model, transport, source_mode, "
                               "prompt_version, check_version, template_version, facts_hash, verdict, reason_codes, "
                               "output_text, input_tokens, output_tokens, latency_ms, finish_reason "
                               "FROM dq_ctl.llm_calls WHERE span_id = ANY(%s) ORDER BY call_id", (ids,)).fetchall():
                d = {k: v for k, v in dict(r).items() if k not in ("trace_id", "span_id")}
                calls[(r["trace_id"], r["span_id"])] = {**d, "reason_codes": list(r["reason_codes"] or [])}
            for g in guardrail_rows(c, p, "span_id = ANY(%s)", (ids,)):
                guards.setdefault(g["span_id"], []).append(g)
            if expand:
                for x in c.execute("SELECT span_id, unit_key, ok, reasons FROM dq_ctl.release_checks "
                                   "WHERE span_id = ANY(%s) ORDER BY id", (ids,)).fetchall():
                    checks.setdefault(x["span_id"], []).append(
                        {"unit_key": x["unit_key"], "ok": x["ok"], "reasons": list(x["reasons"])})
        return [{"trace_id": s["trace_id"], "span_id": s["span_id"], "parent_span_id": s["parent_span_id"],
                 "ordinal": s["ordinal"],
                 "name": s["name"], "seat": s["seat"], "subject": s["subject"], "status": s["status"],
                 "started_at": _iso(s["started_at"]), "ended_at": _iso(s["ended_at"]),
                 "attrs": dict(s["attrs"] or {}), "refs": list(s["refs"] or []),
                 "linked": {"release_checks": checks.get(s["span_id"], [])} if expand else {},
                 "llm_call": calls.get((s["trace_id"], s["span_id"])),
                 "guardrails": guards.get(s["span_id"], [])} for s in rows]

    # ------------------------------------------------------------------------------------------------ runs
    @app.get("/api/v1/runs", response_model=M.RunList)
    def runs(kind: str | None = None, pipeline: str | None = None, night: str | None = None,
             unit: str | None = None, limit: int = Query(50, ge=1, le=MAX_LIMIT), cursor: str | None = None,
             p=Depends(principal)):
        cur = _cursor_in(cursor, "runs")
        where, args = ["true"], []
        for col, v in (("kind", kind), ("pipeline_id", pipeline)):
            if v is not None:
                where.append(f"r.{col} = %s")
                args.append(v)
        if unit is not None:                          # F-35: the runs that touched a unit (F-34's unit_runs, plus spans
            ref = f"unit:{unit}"                      # naming it: EXPLAIN roots); RLS applies inside both lookups
            if not _REF_RE.fullmatch(ref):
                return {"runs": [], "next": None}
            via = ("r.trace_id IN (SELECT trace_id FROM dq_ctl.unit_runs WHERE unit_key = %s) OR "
                   if not elevated(p) else "")       # the auditor role reads no unit_events / decisions: spans only
            where.append(f"({via}EXISTS (SELECT 1 FROM dq_ctl.spans s WHERE s.trace_id = r.trace_id AND "
                         "(s.subject = %s OR %s = ANY(s.refs))) OR EXISTS (SELECT 1 FROM dq_ctl.release_checks k "
                         "JOIN dq_ctl.spans s USING (span_id) WHERE s.trace_id = r.trace_id AND k.unit_key = %s))")
            args += ([unit] if via else []) + [ref, ref, unit]
        if night is not None:
            try:
                dt.date.fromisoformat(night)
            except ValueError:
                raise HTTPException(400, {"status": "BAD_NIGHT", "reason": "night is YYYY-MM-DD"}) from None
            where.append("r.night_date = %s::date")
            args.append(night)
        if cur:
            where.append("(r.started_at, r.trace_id) < (%s::timestamptz, %s)")
            args += cur
        with reader(p) as c:
            rows = c.execute(
                "SELECT r.*, (SELECT e.event FROM dq_ctl.run_events e WHERE e.trace_id=r.trace_id ORDER BY e.id DESC "
                "LIMIT 1) AS status, (SELECT count(*) FROM dq_ctl.spans s WHERE s.trace_id=r.trace_id) AS span_count "
                f"FROM dq_ctl.runs r WHERE {' AND '.join(where)} ORDER BY r.started_at DESC, r.trace_id DESC "
                "LIMIT %s", (*args, limit + 1)).fetchall()
        page = rows[:limit]
        out = [{"trace_id": r["trace_id"], "kind": r["kind"], "state": r["state"], "pipeline_id": r["pipeline_id"],
                "night_date": _iso(r["night_date"]), "attempt": r["attempt"], "status": r["status"],
                "started_at": _iso(r["started_at"]), "span_count": r["span_count"], "source_mode": r["source_mode"]}
               for r in page]
        nxt = _cursor_out(_iso(page[-1]["started_at"]), page[-1]["trace_id"]) if len(rows) > limit else None
        return {"runs": out, "next": nxt}

    def _run_detail(trace_id: str, p, expand: bool) -> dict:
        if not _TRACE_RE.fullmatch(trace_id):
            raise HTTPException(404, "run not found")
        with reader(p) as c:
            run = c.execute("SELECT * FROM dq_ctl.runs WHERE trace_id=%s", (trace_id,)).fetchone()
            if run is not None:
                events = c.execute("SELECT event, error_class, at FROM dq_ctl.run_events WHERE trace_id=%s ORDER BY id",
                                   (trace_id,)).fetchall()
                srows = c.execute("SELECT * FROM dq_ctl.spans WHERE trace_id=%s ORDER BY ordinal, started_at, span_id",
                                  (trace_id,)).fetchall()
                spans = span_rows(c, p, srows, expand)
        if run is None:
            if exists_anywhere("SELECT 1 FROM dq_ctl.runs WHERE trace_id=%s", (trace_id,)):
                deny(p, f"trace:{trace_id}", "run")
            raise HTTPException(404, "run not found")
        hidden = 0
        if not elevated(p):
            with services.cp.as_role("dq_auditor") as c:
                total = c.execute("SELECT count(*) n FROM dq_ctl.spans WHERE trace_id=%s", (trace_id,)).fetchone()["n"]
            hidden = int(total > len(spans))
        return {"run": {k: (_iso(run[k]) if k in ("night_date", "started_at") else run[k])
                        for k in ("trace_id", "kind", "parent_trace_id", "state", "pipeline_id", "night_date",
                                  "attempt", "person_id", "config_digest", "code_digest", "deployment",
                                  "source_mode", "started_at")},
                "events": [{"event": e["event"], "error_class": e["error_class"], "at": _iso(e["at"])}
                           for e in events],
                "spans": spans, "hidden_out_of_scope": hidden, "hidden_label": HIDDEN_LABEL if hidden else None}

    def _keyed(spans: list[dict]) -> dict:
        """Match steps across runs by their path of names and subject (ids differ per run by construction)."""
        by_id = {s["span_id"]: s for s in spans}

        def path(s):
            out, cur, seen = [], s, set()
            while cur is not None and cur["span_id"] not in seen:
                seen.add(cur["span_id"])
                out.append(cur["name"])
                cur = by_id.get(cur["parent_span_id"])
            return "/".join(reversed(out))
        keyed: dict = {}
        for s in spans:
            k = f"{path(s)}|{s['subject'] or ''}"
            n = 1
            while k in keyed:                          # repeated steps keep their order
                n += 1
                k = f"{path(s)}|{s['subject'] or ''}#{n}"
            keyed[k] = s
        return keyed

    @app.get("/api/v1/runs/diff", response_model=M.RunDiff)
    def runs_diff(a: str, b: str, p=Depends(principal)):
        """Two runs side by side: OUTCOME deltas only (step status, llm verdicts, guardrail counts, release-check
        outcomes). No reasons and no causal fields: what differs, never why. Out of scope answers like a miss."""
        da, db = _run_detail(a, p, True), _run_detail(b, p, True)
        ka, kb = _keyed(da["spans"]), _keyed(db["spans"])
        deltas = []
        for k in sorted(set(ka) | set(kb)):
            sa, sb = ka.get(k), kb.get(k)

            def call(s, f):
                return (s or {}).get("llm_call", {}) and (s.get("llm_call") or {}).get(f)
            row = {"key": k, "in_a": sa is not None, "in_b": sb is not None,
                   "status": {"a": sa and sa["status"], "b": sb and sb["status"]},
                   "verdict": {"a": call(sa, "verdict") or None, "b": call(sb, "verdict") or None},
                   "fact_check": {"a": (sa or {}).get("attrs", {}).get("fact_check"),
                                  "b": (sb or {}).get("attrs", {}).get("fact_check")}}
            if not (row["in_a"] and row["in_b"]) or row["status"]["a"] != row["status"]["b"] or \
                    row["verdict"]["a"] != row["verdict"]["b"] or row["fact_check"]["a"] != row["fact_check"]["b"]:
                deltas.append(row)

        def gcount(d):
            out: dict = {}
            for s in d["spans"]:
                for g in s["guardrails"]:
                    out[g["code"]] = out.get(g["code"], 0) + 1
            return out
        ga, gb = gcount(da), gcount(db)
        guards = {code: {"a": ga.get(code, 0), "b": gb.get(code, 0)} for code in sorted(set(ga) | set(gb))
                  if ga.get(code, 0) != gb.get(code, 0)}

        def checks(d):
            out = {}
            for s in d["spans"]:
                for x in (s["linked"] or {}).get("release_checks", []):
                    out[x["unit_key"]] = x["ok"]                 # the last check of each unit in the run
            return out
        ca, cb = checks(da), checks(db)
        rc = [{"unit_key": u, "a": ca.get(u), "b": cb.get(u)} for u in sorted(set(ca) | set(cb)) if ca.get(u) != cb.get(u)]
        last = lambda d: d["events"][-1]["event"] if d["events"] else None  # noqa: E731
        return {"a": a, "b": b, "status": {"a": last(da), "b": last(db)},
                "spans_only_in_a": sum(1 for k in ka if k not in kb), "spans_only_in_b": sum(1 for k in kb if k not in ka),
                "span_deltas": deltas, "guardrail_deltas": guards, "release_check_deltas": rc,
                "hidden_out_of_scope": int(da["hidden_out_of_scope"] or db["hidden_out_of_scope"])}

    @app.get("/api/v1/runs/{trace_id}", response_model=M.RunDetail)
    def run_detail(trace_id: str, expand: str | None = None, p=Depends(principal)):
        return _run_detail(trace_id, p, expand == "rows")

    @app.get("/api/v1/runs/{trace_id}/otlp", response_model=M.OtlpExport)
    def run_otlp(trace_id: str, p=Depends(principal)):
        d = _run_detail(trace_id, p, False)
        tid = hashlib.sha256(trace_id.encode()).hexdigest()[:32]

        def ns(iso):
            return str(int(dt.datetime.fromisoformat(iso).timestamp() * 1_000_000_000))

        def attr(k, v):
            if isinstance(v, bool):
                return {"key": k, "value": {"boolValue": v}}
            if isinstance(v, int):
                return {"key": k, "value": {"intValue": str(v)}}
            if isinstance(v, float):
                return {"key": k, "value": {"doubleValue": v}}
            if isinstance(v, dict):
                return {"key": k, "value": {"stringValue": json.dumps(v, sort_keys=True)}}
            return {"key": k, "value": {"stringValue": str(v)}}
        def gen_ai(call) -> list:
            """OTel GenAI semantic-convention attributes for a span that carries an llm call (F-05)."""
            if not call:
                return []
            a = [attr("gen_ai.operation.name", "chat"), attr("gen_ai.system", call["provider"]),
                 attr("gen_ai.request.model", call["model"] or call["transport"]),
                 attr("dq.llm.transport", call["transport"]), attr("dq.llm.source_mode", call["source_mode"]),
                 attr("dq.llm.verdict", call["verdict"]), attr("dq.llm.prompt_version", call["prompt_version"]),
                 attr("dq.llm.check_version", call["check_version"])]
            if call["model"]:
                a.append(attr("gen_ai.response.model", call["model"]))
            if call["input_tokens"] is not None:
                a.append(attr("gen_ai.usage.input_tokens", int(call["input_tokens"])))
            if call["output_tokens"] is not None:
                a.append(attr("gen_ai.usage.output_tokens", int(call["output_tokens"])))
            if call["finish_reason"]:
                a.append({"key": "gen_ai.response.finish_reasons",
                          "value": {"arrayValue": {"values": [{"stringValue": call["finish_reason"]}]}}})
            return a
        spans = [{"traceId": tid, "spanId": s["span_id"], "parentSpanId": s["parent_span_id"] or "",
                  "name": s["name"], "kind": 3 if s["llm_call"] else 1, "startTimeUnixNano": ns(s["started_at"]),
                  "endTimeUnixNano": ns(s["ended_at"]),
                  "attributes": [attr(k, v) for k, v in sorted(s["attrs"].items())]
                  + ([attr("dq.subject", s["subject"])] if s["subject"] else [])
                  + ([attr("dq.seat", s["seat"])] if s["seat"] else []) + gen_ai(s["llm_call"]),
                  "status": {"code": 2 if s["status"] == "ERROR" else 1}} for s in d["spans"]]
        run = d["run"]
        res = {"attributes": [attr("service.name", "dq-agent"), attr("dq.trace_id", trace_id),
                              attr("dq.kind", run["kind"]), attr("dq.deployment", run["deployment"]),
                              attr("dq.hidden_out_of_scope", d["hidden_out_of_scope"])]}
        return {"resourceSpans": [{"resource": res, "scopeSpans": [{"scope": {"name": "dqagent.obs.tracer"},
                                                                    "spans": spans}]}]}

    @app.get("/api/v1/units/{unit_key}/trace", response_model=M.UnitTrace)
    def unit_trace(unit_key: str, p=Depends(principal)):
        ref = f"unit:{unit_key}"
        if not _REF_RE.fullmatch(ref):
            return {"unit_key": unit_key, "spans": []}
        q = "SELECT * FROM dq_ctl.spans WHERE subject=%s OR %s = ANY(refs) ORDER BY started_at, trace_id, ordinal LIMIT 500"
        with reader(p) as c:
            rows = c.execute(q, (ref, ref)).fetchall()
            spans = span_rows(c, p, rows, True)
        if not rows and exists_anywhere(q, (ref, ref)):           # traced, but none of it in the caller's scope:
            record_denial(p, ref)                                  # audited, and answered exactly like an untraced
        return {"unit_key": unit_key, "spans": spans}             # or unknown unit (I3)

    # ------------------------------------------------------------------------------------------------ citations
    def resolvable(c, p, evidence_ref: str | None) -> bool:
        """Re-check scope on resolve: a unit ref resolves only if the caller can read that unit now; a batch ref only
        if the caller can read a unit of it; configuration refs (rule, rel, results, cr, dec, file, trace, ...) resolve
        when the explanation itself was readable (it was: it is in scope)."""
        if not evidence_ref or not _REF_RE.fullmatch(evidence_ref):
            return False
        kind, _, ident = evidence_ref.partition(":")
        if elevated(p):                                   # the audit / operations duty reads every object
            return True
        if kind == "unit":
            return c.execute("SELECT 1 FROM dq_ctl.units WHERE unit_key=%s", (ident,)).fetchone() is not None
        if kind == "batch":
            return c.execute("SELECT 1 FROM dq_ctl.units WHERE batch_id=%s LIMIT 1", (ident,)).fetchone() is not None
        return True

    @app.get("/api/v1/explanations/{subject}/citations", response_model=M.Citations)
    def citations(subject: str, explanation_ref: str = Query(...), p=Depends(principal)):
        fh, _, th = explanation_ref.partition(":")
        if not (_HASH.fullmatch(fh) and _HASH.fullmatch(th)):
            raise HTTPException(422, {"status": "EXPLANATION_REF", "reason": "explanation_ref is <facts_hash>:<text_hash>"})
        q = ("SELECT text, source_mode, claims FROM dq_ctl.explanations WHERE subject=%s AND facts_hash=%s AND "
             "text_hash=%s ORDER BY at LIMIT 1")
        with reader(p) as c:
            row = c.execute(q, (subject, fh, th)).fetchone()
            cites = []
            if row is not None:
                claims = row["claims"] if isinstance(row["claims"], list) else json.loads(row["claims"])
                for cl in claims:
                    span = ({"start": cl["start"], "end": cl["end"], "shown": cl.get("shown")}
                            if "start" in cl and "end" in cl else None)
                    cites.append({"span": span, "fact_key": cl["fact_key"], "evidence_ref": cl.get("evidence_ref"),
                                  "resolvable": resolvable(c, p, cl.get("evidence_ref"))})
        if row is None:
            if exists_anywhere(q, (subject, fh, th)):
                deny(p, subject, "explanation")
            raise HTTPException(404, "explanation not recorded")
        return {"subject": subject, "explanation_ref": {"facts_hash": fh, "text_hash": th},
                "label": "generated_from_facts" if row["source_mode"] == "TEMPLATE" else "slot",
                "source_mode": row["source_mode"], "citations": cites}

    # ------------------------------------------------------------------------------------------------ guardrails
    @app.get("/api/v1/guardrail-events", response_model=M.GuardrailList)
    def guardrail_events(code: str | None = None, subject: str | None = None, trace_id: str | None = None,
                         limit: int = Query(100, ge=1, le=MAX_LIMIT), cursor: str | None = None,
                         p=Depends(principal)):
        if code == "SCOPE_DENIED" and not is_audit(p):
            raise HTTPException(403, "SCOPE_DENIED events are visible to the audit duty only")
        cur = _cursor_in(cursor, "guardrails")
        where, args = ["true"], []
        for col, v in (("code", code), ("subject", subject), ("trace_id", trace_id)):
            if v is not None:
                where.append(f"{col} = %s")
                args.append(v)
        if cur:
            where.append("id < %s")
            args.append(int(cur[1]))
        with reader(p) as c:                          # scope (RLS) and SCOPE_DENIED filtering both precede the LIMIT
            rows = guardrail_rows(c, p, " AND ".join(where), tuple(args), order="id DESC", limit=limit + 1)
        page = rows[:limit]
        nxt = _cursor_out("g", page[-1]["id"]) if len(rows) > limit else None
        return {"events": page, "next": nxt}

    # ------------------------------------------------------------------------------------------------ F-35 / P2-1
    NO_MODEL = {"TEMPLATE": "no model is called: 0 tokens", "SCRIPTED": "no model is called: 0 tokens",
                "RECORDED": "no model is called: recorded token counts"}

    @app.get("/api/v1/llm/usage", response_model=M.Usage)
    def llm_usage(days: int = Query(30, ge=1, le=366), p=Depends(principal)):
        """Calls, verdicts, tokens and latency per day, provider, transport and source mode, over the llm calls the
        caller may read (scope by RLS). TEMPLATE and SCRIPTED rows are labelled: no model is called."""
        with reader(p) as c:
            rows = c.execute(
                "SELECT to_char(date_trunc('day', at), 'YYYY-MM-DD') AS day, provider, transport, source_mode, "
                "count(*) AS calls, coalesce(sum(input_tokens), 0) AS input_tokens, "
                "coalesce(sum(output_tokens), 0) AS output_tokens, "
                "percentile_disc(0.5) WITHIN GROUP (ORDER BY latency_ms) AS p50, "
                "percentile_disc(0.95) WITHIN GROUP (ORDER BY latency_ms) AS p95, "
                "jsonb_object_agg(verdict, n) AS verdicts FROM ("
                "  SELECT *, count(*) OVER (PARTITION BY date_trunc('day', at), provider, transport, source_mode, "
                "  verdict) AS n FROM dq_ctl.llm_calls WHERE at >= clock_timestamp() - make_interval(days => %s)) x "
                "GROUP BY 1, 2, 3, 4 ORDER BY 1 DESC, 2, 3, 4", (days,)).fetchall()
        return {"days": days, "rows": [
            {"day": r["day"], "provider": r["provider"], "transport": r["transport"], "source_mode": r["source_mode"],
             "calls": r["calls"], "verdicts": {k: int(v) for k, v in (r["verdicts"] or {}).items()},
             "input_tokens": int(r["input_tokens"]), "output_tokens": int(r["output_tokens"]),
             "latency_ms_p50": r["p50"], "latency_ms_p95": r["p95"], "tokens_label": NO_MODEL.get(r["source_mode"])}
            for r in rows]}

    @app.get("/api/v1/prompts", response_model=M.Prompts)
    def prompts(p=Depends(principal)):
        """Prompt-version history: each prompt_version with its text, check and template versions, and the calls and
        traces (the caller may read) that used it."""
        from dqagent import llm as _llm
        with reader(p) as c:
            pv = c.execute("SELECT * FROM dq_ctl.prompt_versions ORDER BY first_seen, prompt_version").fetchall()
            used = {r["prompt_version"]: r for r in c.execute(
                "SELECT prompt_version, count(*) AS calls, (array_agg(DISTINCT trace_id))[1:20] AS traces "
                "FROM dq_ctl.llm_calls GROUP BY prompt_version").fetchall()}
        return {"prompts": [
            {"prompt_version": r["prompt_version"], "system_prompt": r["system_prompt"],
             "check_version": r["check_version"], "template_version": r["template_version"],
             "first_seen": _iso(r["first_seen"]), "calls": int((used.get(r["prompt_version"]) or {}).get("calls") or 0),
             "traces": sorted((used.get(r["prompt_version"]) or {}).get("traces") or []),
             "current": r["prompt_version"] == _llm.PROMPT_VERSION} for r in pv]}
