"""Part F P1-1 §E and §F: ExplainService (cache, traces, guardrails, llm_calls) and the observability routes.

Runs on a fresh control plane per test (tests/t1/conftest.py). The routes are mounted on a bare FastAPI app with a
stub principal, so scope is exercised exactly as the api applies it (dq_api under the release scope; the audit and
operations duties as dq_auditor)."""
import json
from types import SimpleNamespace

import psycopg
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from dqagent import llm
from dqagent.api.contracts import obs as M
from dqagent.api.obs_routes import HIDDEN_LABEL, obs_routes
from dqagent.engine.cp import CP
from dqagent.llm.transports import RecordedTransport, ScriptedTransport
from dqagent.obs import explain_service as ES
from dqagent.obs import tracer

RULE = {"rule_id": "B-CMP-CLM-POS", "disposition": "FAIL", "failed": 23, "evaluated": 164,
        "relation": "bronze.encounter_header", "gating": "advisory"}
SCRIPTED = {"provider": "anthropic", "transport": "scripted", "providers": {"anthropic": {"max_tokens": 300}},
            "script": {"bad_every": 0, "bad_subjects": []}}
SUBJ = "rule:B-CMP-CLM-POS"
A, B = ["AUTH_A"], ["AUTH_B"]


@pytest.fixture
def cp(fresh_cp_dsn, monkeypatch):
    # tests here break the trace store on purpose: keep the tracer's and the service's global counters per test
    for k, v in list(tracer.STATS.items()):
        monkeypatch.setitem(tracer.STATS, k, v)
    for k, v in list(ES.STATS.items()):
        monkeypatch.setitem(ES.STATS, k, v)
    return CP(fresh_cp_dsn)


def rows(cp, sql, args=()):
    with cp.as_role("dq_auditor") as c:
        return c.execute(sql, args).fetchall()


def svc(cp, cfg=None, transport=None, deployment="demo"):
    return ES.ExplainService(cp, cfg=cfg if cfg is not None else {"provider": "template"}, deployment=deployment,
                             state="STATE_A", transport=transport)


# ------------------------------------------------------------------------------------------------ ExplainService
def test_template_render_is_cached_and_a_hit_writes_nothing(cp):
    s = svc(cp)
    e1 = s.explain("rule_disposition", RULE, subject=SUBJ, authorities=A)
    assert e1["fact_check"] == "TEMPLATE" and not e1["cached"] and e1["source_mode"] == "TEMPLATE"
    n_calls = len(rows(cp, "SELECT 1 FROM dq_ctl.llm_calls"))
    e2 = s.explain("rule_disposition", RULE, subject=SUBJ, authorities=A)
    assert e2["cached"] and e2["text"] == e1["text"] and e2["explanation_ref"] == e1["explanation_ref"]
    assert len(rows(cp, "SELECT 1 FROM dq_ctl.llm_calls")) == n_calls == 1          # a hit records nothing
    call = rows(cp, "SELECT * FROM dq_ctl.llm_calls")[0]
    assert call["transport"] == "none" and call["source_mode"] == "TEMPLATE" and call["verdict"] == "NOT_CHECKED"
    assert s.issued(SUBJ, e1["facts_hash"], e1["text_hash"])
    assert not s.issued(SUBJ, e1["facts_hash"], "0" * 64)


def test_scripted_pass_writes_llm_call_explanation_and_trace_once(cp):
    s = svc(cp, SCRIPTED)
    e = s.explain("rule_disposition", RULE, subject=SUBJ, authorities=A)
    assert e["fact_check"] == "PASS" and e["source"] == "anthropic" and e["source_mode"] == "SCRIPTED"
    assert "no model is called" in e["label"]
    (call,) = rows(cp, "SELECT * FROM dq_ctl.llm_calls")
    assert call["transport"] == "scripted" and call["verdict"] == "PASS" and call["output_text"] == e["text"]
    assert call["token_sha256"] and all(len(h) == 64 for h in call["token_sha256"])
    assert call["prompt_version"] == llm.PROMPT_VERSION and call["check_version"] == llm.VERSIONS["check_version"]
    assert call["input_digest"] and call["authorities"] == A and call["subject"] == SUBJ
    (ex,) = rows(cp, "SELECT * FROM dq_ctl.explanations")
    assert ex["text"] == e["text"] and ex["source_mode"] == "SCRIPTED"
    assert all(c.get("evidence_ref") for c in ex["claims"]) and {c["fact_key"] for c in ex["claims"]} >= {"failed"}
    (run,) = rows(cp, "SELECT * FROM dq_ctl.runs")
    assert run["kind"] == "EXPLAIN" and run["trace_id"].startswith("EXP-") and run["authorities"] == A
    spans = {r["name"]: r for r in rows(cp, "SELECT * FROM dq_ctl.spans WHERE trace_id=%s", (run["trace_id"],))}
    assert set(spans) == {"llm.explain", "llm.validate"} and call["span_id"] == spans["llm.explain"]["span_id"]
    assert rows(cp, "SELECT 1 FROM dq_ctl.prompt_versions WHERE prompt_version=%s", (llm.PROMPT_VERSION,))
    assert rows(cp, "SELECT 1 FROM dq_ctl.explain_facts WHERE facts_hash=%s", (e["facts_hash"],))


def test_fallback_is_never_cached_and_its_guardrails_are_written_every_render(cp):
    bad = ScriptedTransport(bad_every=0, bad_subjects=[SUBJ], mutator="ADVICE")
    s = svc(cp, SCRIPTED, transport=bad)
    for _ in range(2):
        e = s.explain("rule_disposition", RULE, subject=SUBJ, authorities=A)
        assert e["fact_check"] == "FALLBACK" and e["source"] == "template" and not e["cached"]
    assert rows(cp, "SELECT 1 FROM dq_ctl.explanations") == []
    calls = rows(cp, "SELECT * FROM dq_ctl.llm_calls ORDER BY call_id")
    assert len(calls) == 2 and all(c["verdict"] == "REJECT" and c["output_text"] == e["text"] for c in calls)
    assert "ADVICE" in calls[0]["reason_codes"] and calls[0]["token_sha256"]
    codes = sorted(r["code"] for r in rows(cp, "SELECT code FROM dq_ctl.guardrail_events"))
    assert codes == sorted(["CHECK_REJECTED", "ADVICE_BLOCKED", "FALLBACK"] * 2)


def test_cache_key_covers_prompt_check_and_template_versions(cp, monkeypatch):
    s = svc(cp)
    s.explain("rule_disposition", RULE, subject=SUBJ, authorities=A)
    for k in ("prompt_version", "check_version", "template_version"):
        monkeypatch.setitem(llm.VERSIONS, k, llm.VERSIONS[k] + "-next")
        assert not s.explain("rule_disposition", RULE, subject=SUBJ, authorities=A)["cached"], k
    assert len(rows(cp, "SELECT 1 FROM dq_ctl.explanations")) == 4


def test_cache_read_reruns_the_check(cp, monkeypatch):
    s = svc(cp, SCRIPTED)
    s.explain("rule_disposition", RULE, subject=SUBJ, authorities=A)
    before = ES.STATS["recheck_misses"]
    real = llm.check.check_result
    monkeypatch.setattr(llm.check, "check_result",
                        lambda *a, **k: real(*a, **k)._replace(ok=False))           # the check now refuses it
    e = s.explain("rule_disposition", RULE, subject=SUBJ, authorities=A)
    assert not e["cached"] and ES.STATS["recheck_misses"] == before + 1


def test_racing_render_serves_the_winning_row(cp, monkeypatch):
    s = svc(cp, SCRIPTED)
    key = s.key(SUBJ, llm._facts.facts_hash(llm._facts.build("rule_disposition", RULE)))
    with cp.as_role("dq_trace_svc", trace=False) as c:                  # another worker won the insert first
        c.execute("INSERT INTO dq_ctl.explanations(subject, authorities, facts_hash, provider_config_digest, "
                  "prompt_version, check_version, template_version, text, text_hash, source_mode, claims) "
                  "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,'SCRIPTED',%s::jsonb)",
                  (key[0], A, key[1], key[2], key[3], key[4], key[5], "Check B-CMP-CLM-POS failed.",
                   llm._sha("Check B-CMP-CLM-POS failed."),
                   json.dumps([{"fact_key": "disposition", "value": "FAIL"}])))
    first = {"n": 0}
    real = s._cached

    def miss_once(k):                                 # this worker looked before the winner committed
        first["n"] += 1
        return None if first["n"] == 1 else real(k)
    monkeypatch.setattr(s, "_cached", miss_once)
    e = s.explain("rule_disposition", RULE, subject=SUBJ, authorities=A)
    assert e["text"] == "Check B-CMP-CLM-POS failed." and len(rows(cp, "SELECT 1 FROM dq_ctl.explanations")) == 1
    (call,) = rows(cp, "SELECT output_text, text_hash FROM dq_ctl.llm_calls")
    assert call["output_text"] == e["text"] and call["text_hash"] == e["text_hash"]      # the text actually shown


def test_fail_closed_codes_refuse_and_fail_open_codes_do_not(cp, monkeypatch):
    real = cp.as_role

    def broken(role, *a, **k):
        if role == "dq_trace_svc" and not k.get("_probe"):
            raise psycopg.OperationalError("trace store down")
        return real(role, *a, **k)
    bad = svc(cp, SCRIPTED, transport=ScriptedTransport(bad_every=0, bad_subjects=[SUBJ], mutator="CAUSAL"))
    monkeypatch.setattr(ES.tracer, "numbered_trace_id", lambda cp_, base: base + "-1")
    monkeypatch.setattr(bad, "_cached", lambda k: None)
    monkeypatch.setattr(cp, "as_role", broken)
    with pytest.raises(ES.ExplainUnavailable):                           # CHECK_REJECTED fails closed
        bad.explain("rule_disposition", RULE, subject=SUBJ, authorities=A)
    ok = svc(cp)
    monkeypatch.setattr(ok, "_cached", lambda k: None)
    before = ES.STATS["fail_open_write_failures"]
    e = ok.explain("rule_disposition", RULE, subject=SUBJ, authorities=A)  # a template render: nothing fail-closed
    assert e["fact_check"] == "TEMPLATE" and ES.STATS["fail_open_write_failures"] == before + 1
    assert ES.FAIL_MODE["SCOPE_DENIED"] == ES.FAIL_MODE["ENTITLEMENT_REFUSED"] == ES.FAIL_MODE["CHECK_REJECTED"] \
        == ES.FAIL_CLOSED


def test_masking_guardrails_are_counts_of_fields(cp):
    s = svc(cp)
    raw = {**RULE, "failed": 3, "member_id": "ZZ12345678"}
    s.explain("rule_disposition", raw, subject=SUBJ, authorities=A, masking={"member_id": ["identity"]})
    ev = {r["code"]: r["detail"] for r in rows(cp, "SELECT code, detail FROM dq_ctl.guardrail_events")}
    assert ev["MASKED"] == {"fields": 1} and ev["SUPPRESSED"] == {"fields": 1}
    assert "ZZ12345678" not in json.dumps([dict(r) for r in rows(cp, "SELECT * FROM dq_ctl.explain_facts")])


def test_service_refuses_test_transports_in_governed_deployments(cp):
    from dqagent.deploy import NotConfiguredError
    with pytest.raises(NotConfiguredError, match="scripted"):
        svc(cp, SCRIPTED, deployment="pilot")
    with pytest.raises(NotConfiguredError, match="recorded"):
        svc(cp, SCRIPTED, transport=RecordedTransport({}), deployment="production")


# ------------------------------------------------------------------------------------------------ routes
def client(cp, principal):
    app = FastAPI()
    obs_routes(app, SimpleNamespace(cp=cp), lambda: principal)
    return TestClient(app)


def who(scope=(), duties=()):
    return {"emp_id": "E1", "_scope": list(scope), "_duties": list(duties)}


def make_run(cp, trace_id, auths, spans):
    with tracer.root("DECISION", trace_id, cp=cp, state="STATE_A", authorities=auths, deployment="test"):
        for name, sa, subject in spans:
            with tracer.span(name, seat="test", subject=subject, authorities=sa) as s:
                s.attr("n", 1)


def test_runs_are_scoped_and_out_of_scope_is_404_with_an_audit_only_event(cp):
    make_run(cp, "DEC-1", A, [("decide", A, "unit:U-1")])
    a, b = client(cp, who(A, ["release:AUTH_A"])), client(cp, who(B, ["release:AUTH_B"]))
    lst = a.get("/api/v1/runs").json()
    M.RunList.model_validate(lst)
    assert [r["trace_id"] for r in lst["runs"]] == ["DEC-1"] and lst["runs"][0]["status"] == "COMPLETED"
    assert b.get("/api/v1/runs").json()["runs"] == []
    d = a.get("/api/v1/runs/DEC-1")
    assert d.status_code == 200 and M.RunDetail.model_validate(d.json()).hidden_out_of_scope == 0
    r = b.get("/api/v1/runs/DEC-1")
    assert r.status_code == 404 and b.get("/api/v1/runs/DEC-404").status_code == 404     # same answer
    (ev,) = rows(cp, "SELECT * FROM dq_ctl.guardrail_events WHERE code='SCOPE_DENIED'")
    assert ev["authorities"] == [] and ev["subject"] == "trace:DEC-1"
    assert b.get("/api/v1/guardrail-events?code=SCOPE_DENIED").status_code == 403
    assert all(e["code"] != "SCOPE_DENIED" for e in a.get("/api/v1/guardrail-events").json()["events"])
    audit = client(cp, who((), ["audit:AUTH_AUDIT"])).get("/api/v1/guardrail-events?code=SCOPE_DENIED").json()
    M.GuardrailList.model_validate(audit)
    assert [e["subject"] for e in audit["events"]] == ["trace:DEC-1"] and audit["events"][0]["fail_mode"] == "closed"


def test_engine_wide_runs_are_read_only_through_audit_or_operations(cp):
    make_run(cp, "NIGHT-STATE_A-20260115-a1", [], [("summary", [], None)])
    assert client(cp, who(A, ["release:AUTH_A"])).get("/api/v1/runs").json()["runs"] == []
    for duty in ("audit:AUTH_AUDIT", "operations:AUTH_OPERATIONS"):
        got = client(cp, who((), [duty])).get("/api/v1/runs").json()["runs"]
        assert [r["trace_id"] for r in got] == ["NIGHT-STATE_A-20260115-a1"], duty


def test_hidden_steps_are_one_placeholder_without_counts(cp):
    make_run(cp, "DEC-2", A, [("a", A, "unit:U-1"), ("b", B, "unit:U-2"), ("c", B, "unit:U-3")])
    d = client(cp, who(A, ["release:AUTH_A"])).get("/api/v1/runs/DEC-2?expand=rows").json()
    M.RunDetail.model_validate(d)
    assert [s["name"] for s in d["spans"]] == ["a"] and d["hidden_out_of_scope"] == 1
    assert d["hidden_label"] == HIDDEN_LABEL and "U-2" not in json.dumps(d)
    full = client(cp, who((), ["audit:AUTH_AUDIT"])).get("/api/v1/runs/DEC-2").json()
    assert len(full["spans"]) == 3 and full["hidden_out_of_scope"] == 0


def test_otlp_export_of_visible_spans(cp):
    make_run(cp, "DEC-3", A, [("a", A, "unit:U-1"), ("b", B, "unit:U-2")])
    o = client(cp, who(A, ["release:AUTH_A"])).get("/api/v1/runs/DEC-3/otlp").json()
    M.OtlpExport.model_validate(o)
    spans = o["resourceSpans"][0]["scopeSpans"][0]["spans"]
    assert [s["name"] for s in spans] == ["a"] and len(spans[0]["traceId"]) == 32 and len(spans[0]["spanId"]) == 16
    assert int(spans[0]["endTimeUnixNano"]) >= int(spans[0]["startTimeUnixNano"]) > 0


def test_otlp_llm_spans_carry_gen_ai_attributes(cp):
    e = svc(cp, SCRIPTED).explain("rule_disposition", RULE, subject=SUBJ, authorities=A)
    (run,) = rows(cp, "SELECT trace_id FROM dq_ctl.runs WHERE kind='EXPLAIN'")
    o = client(cp, who(A, ["release:AUTH_A"])).get(f"/api/v1/runs/{run['trace_id']}/otlp").json()
    sp = next(s for s in o["resourceSpans"][0]["scopeSpans"][0]["spans"] if s["name"] == "llm.explain")
    keys = {a["key"]: a["value"] for a in sp["attributes"]}
    assert keys["gen_ai.system"] == {"stringValue": "anthropic"} and keys["gen_ai.operation.name"]
    assert keys["gen_ai.request.model"] == {"stringValue": "scripted"} and keys["gen_ai.usage.output_tokens"]
    assert keys["dq.llm.source_mode"] == {"stringValue": "SCRIPTED"} and sp["kind"] == 3
    assert e["fact_check"] == "PASS"


def test_unit_trace_is_scoped(cp):
    make_run(cp, "DEC-4", A, [("decide", A, "unit:U-9")])
    t = client(cp, who(A, ["release:AUTH_A"])).get("/api/v1/units/U-9/trace").json()
    M.UnitTrace.model_validate(t)
    assert [s["name"] for s in t["spans"]] == ["decide"]
    b = client(cp, who(B, ["release:AUTH_B"]))
    out_of_scope, untraced, unknown = (b.get(f"/api/v1/units/{u}/trace") for u in ("U-9", "U-10", "NOPE"))
    assert out_of_scope.status_code == untraced.status_code == unknown.status_code == 200          # I3: identical
    assert out_of_scope.json()["spans"] == untraced.json()["spans"] == unknown.json()["spans"] == []
    assert rows(cp, "SELECT 1 FROM dq_ctl.guardrail_events WHERE code='SCOPE_DENIED' AND subject='unit:U-9'")


def test_citations_resolve_slots_to_evidence_and_rescope(cp):
    e = svc(cp, SCRIPTED).explain("rule_disposition", RULE, subject=SUBJ, authorities=A)
    ref = f"{e['facts_hash']}:{e['text_hash']}"
    got = client(cp, who(A, ["release:AUTH_A"])).get(f"/api/v1/explanations/{SUBJ}/citations",
                                                    params={"explanation_ref": ref})
    assert got.status_code == 200, got.text
    c = M.Citations.model_validate(got.json())
    assert c.label == "slot" and c.source_mode == "SCRIPTED"
    for cit in c.citations:
        assert e["text"][cit.span["start"]:cit.span["end"]] == cit.span["shown"]
        assert cit.evidence_ref and cit.resolvable
    assert {x.evidence_ref for x in c.citations if x.fact_key == "failed"} == \
        {"results:B-CMP-CLM-POS@bronze.encounter_header"}
    t = svc(cp).explain("rule_disposition", RULE, subject="rule:T", authorities=A)
    tc = client(cp, who(A, ["release:AUTH_A"])).get("/api/v1/explanations/rule:T/citations",
                                                   params={"explanation_ref": f"{t['facts_hash']}:{t['text_hash']}"})
    assert tc.json()["label"] == "generated_from_facts" and all(x["span"] is None for x in tc.json()["citations"])
    assert client(cp, who(B, ["release:AUTH_B"])).get(f"/api/v1/explanations/{SUBJ}/citations",
                                                     params={"explanation_ref": ref}).status_code == 404
    assert rows(cp, "SELECT 1 FROM dq_ctl.guardrail_events WHERE code='SCOPE_DENIED' AND subject=%s", (SUBJ,))
    assert client(cp, who(A, ["release:AUTH_A"])).get(f"/api/v1/explanations/{SUBJ}/citations",
                                                     params={"explanation_ref": "x"}).status_code == 422


def test_unit_evidence_resolves_only_in_scope(cp):
    s = svc(cp)
    e = s.explain("unit_card", {"unit_key": "U-77", "state": "HELD", "relation": "bronze.x"}, subject="unit:U-77",
                  authorities=A)
    ref = f"{e['facts_hash']}:{e['text_hash']}"
    c = client(cp, who(A, ["release:AUTH_A"])).get("/api/v1/explanations/unit:U-77/citations",
                                                  params={"explanation_ref": ref}).json()
    unit_cites = [x for x in c["citations"] if x["evidence_ref"] == "unit:U-77"]
    assert unit_cites and not any(x["resolvable"] for x in unit_cites)       # no such unit is readable in scope
    audit = client(cp, who((), ["audit:AUTH_AUDIT"])).get("/api/v1/explanations/unit:U-77/citations",
                                                         params={"explanation_ref": ref}).json()
    assert all(x["resolvable"] for x in audit["citations"] if x["evidence_ref"] == "unit:U-77")


def test_record_guardrail_recovery_refused_is_audit_only_and_fails_closed(cp, monkeypatch):
    """N-T0-1: the helper the runtime's recovery refusal calls (runtime.py is Backend-A's)."""
    assert ES.record_guardrail(cp, "RECOVERY_REFUSED", "batch:B-1", seat="recovery", detail={"loads_lost": 2})
    (ev,) = rows(cp, "SELECT * FROM dq_ctl.guardrail_events WHERE code='RECOVERY_REFUSED'")
    assert ev["authorities"] == [] and ev["detail"] == {"loads_lost": 2}
    real = cp.as_role
    monkeypatch.setattr(cp, "as_role", lambda role, *a, **k: (_ for _ in ()).throw(psycopg.OperationalError("down"))
                        if role == "dq_trace_svc" else real(role, *a, **k))
    with pytest.raises(ES.ExplainUnavailable):
        ES.record_guardrail(cp, "RECOVERY_REFUSED", "batch:B-1")
    assert ES.record_guardrail(cp, "FALLBACK", "rule:x") is False          # fail-open codes never raise
    with pytest.raises(ValueError):
        ES.record_guardrail(cp, "NOT_A_CODE", "rule:x")


def test_scope_denied_write_failure_is_503(cp, monkeypatch):
    make_run(cp, "DEC-5", A, [("decide", A, "unit:U-1")])
    real = cp.as_role
    monkeypatch.setattr(cp, "as_role", lambda role, *a, **k: (_ for _ in ()).throw(psycopg.OperationalError("down"))
                        if role == "dq_trace_svc" else real(role, *a, **k))
    assert client(cp, who(B, ["release:AUTH_B"])).get("/api/v1/runs/DEC-5").status_code == 503



# ------------------------------------------------------------------------------------------------ red-team round (T1)
def test_c1_trace_store_down_still_renders_and_reports_degradation(cp, monkeypatch):
    """C1: with the trace role unusable, a template explanation is still shown (fact_check as usual) and the
    degradation is returned (`degraded`) and counted; nothing is written."""
    real = cp.as_role
    monkeypatch.setattr(cp, "as_role", lambda role, *a, **k: (_ for _ in ()).throw(psycopg.OperationalError("down"))
                        if role == "dq_trace_svc" else real(role, *a, **k))
    before = ES.STATS["degraded"]
    e = svc(cp).explain("rule_disposition", RULE, subject=SUBJ, authorities=A)
    assert e["fact_check"] == "TEMPLATE" and e["text"] and set(e["degraded"]) == {"cache_read", "id_mint", "write"}
    assert ES.STATS["degraded"] == before + 3
    assert not svc(cp).issued(SUBJ, e["facts_hash"], e["text_hash"])         # a store that cannot be read binds nothing


def test_i1_a_cached_row_that_fails_todays_check_is_never_served(cp):
    s = svc(cp, SCRIPTED)
    key = s.key(SUBJ, llm._facts.facts_hash(llm._facts.build("rule_disposition", RULE)))
    bad = "Check B-CMP-CLM-POS found 99 records."                              # 99 is no fact: today's check refuses it
    with cp.as_role("dq_trace_svc", trace=False) as c:
        c.execute("INSERT INTO dq_ctl.explanations(subject, authorities, facts_hash, provider_config_digest, "
                  "prompt_version, check_version, template_version, text, text_hash, source_mode, claims) "
                  "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,'SCRIPTED','[]')",
                  (key[0], A, key[1], key[2], key[3], key[4], key[5], bad, llm._sha(bad)))
    for _ in range(2):
        e = s.explain("rule_disposition", RULE, subject=SUBJ, authorities=A)
        assert e["text"] != bad and e["fact_check"] == "PASS" and not e["cached"]
    calls = rows(cp, "SELECT output_text FROM dq_ctl.llm_calls ORDER BY call_id")
    assert [c["output_text"] for c in calls] == [e["text"], e["text"]]          # llm_calls = the text shown


def test_i1_check_version_covers_its_dependency_closure():
    from dqagent.llm import check as chk
    assert {"check.py", "facts.py", "template.py"} <= set(chk.CHECK_DEPENDENCIES)


def test_i2_issued_needs_the_full_key(cp, monkeypatch):
    s = svc(cp, SCRIPTED)
    e = s.explain("rule_disposition", RULE, subject=SUBJ, authorities=A)
    assert s.issued(SUBJ, e["facts_hash"], e["text_hash"])
    assert not s.issued("rule:OTHER", e["facts_hash"], e["text_hash"])        # a cross-subject row binds nothing
    key = s.key("rule:OTHER", e["facts_hash"])
    with cp.as_role("dq_trace_svc", trace=False) as c:                       # the same text cached for ANOTHER subject
        c.execute("INSERT INTO dq_ctl.explanations(subject, authorities, facts_hash, provider_config_digest, "
                  "prompt_version, check_version, template_version, text, text_hash, source_mode, claims) "
                  "VALUES (%s,%s,%s,%s,%s,%s,%s,'x',%s,'SCRIPTED','[]')",
                  (key[0], A, key[1], key[2], key[3], key[4], key[5], llm._sha("x")))
    assert not s.issued(SUBJ, e["facts_hash"], llm._sha("x"))
    monkeypatch.setitem(llm.VERSIONS, "check_version", llm.VERSIONS["check_version"] + "-next")
    assert not s.issued(SUBJ, e["facts_hash"], e["text_hash"])               # a superseded check_version binds nothing
    assert not svc(cp).issued(SUBJ, e["facts_hash"], e["text_hash"])         # nor another provider configuration


def test_i4_each_call_gets_its_own_trace_and_span_ids(cp):
    s1, s2 = svc(cp, SCRIPTED, transport=ScriptedTransport(bad_every=0, bad_subjects=[SUBJ], mutator="ADVICE")), \
        svc(cp, SCRIPTED, transport=ScriptedTransport(bad_every=0, bad_subjects=[SUBJ], mutator="ADVICE"))
    s1.explain("rule_disposition", RULE, subject=SUBJ, authorities=A)            # two workers, one subject, one facts
    s2.explain("rule_disposition", RULE, subject=SUBJ, authorities=A)
    calls = rows(cp, "SELECT call_id, trace_id, span_id FROM dq_ctl.llm_calls ORDER BY call_id")
    assert len({c["trace_id"] for c in calls}) == len({c["span_id"] for c in calls}) == 2
    assert all(c["trace_id"].endswith(f"-{c['call_id']}") for c in calls)
    spans = rows(cp, "SELECT span_id FROM dq_ctl.spans WHERE name='llm.explain'")
    assert {r["span_id"] for r in spans} == {c["span_id"] for c in calls}
    for c in calls:                                                              # each span shows ITS call
        d = client(cp, who(A, ["release:AUTH_A"])).get(f"/api/v1/runs/{c['trace_id']}").json()
        sp = next(x for x in d["spans"] if x["name"] == "llm.explain")
        assert sp["llm_call"]["call_id"] == c["call_id"]


def test_i6_cache_hit_takes_source_mode_and_label_from_the_row(cp):
    s = svc(cp, SCRIPTED)
    s.explain("rule_disposition", RULE, subject=SUBJ, authorities=A)
    hit = s.explain("rule_disposition", RULE, subject=SUBJ, authorities=A)
    assert hit["cached"] and hit["source_mode"] == "SCRIPTED" and "no model is called" in hit["label"]


def test_nits_cursor_trace_id_and_denial_rate_limit(cp):
    make_run(cp, "DEC-7", A, [("decide", A, "unit:U-1")])
    a, b = client(cp, who(A, ["release:AUTH_A"])), client(cp, who(B, ["release:AUTH_B"]))
    import base64
    crafted = base64.urlsafe_b64encode(json.dumps(["g", "abc"]).encode()).decode()
    assert a.get("/api/v1/guardrail-events", params={"cursor": crafted}).status_code == 400
    crafted = base64.urlsafe_b64encode(json.dumps(["not-a-time", "DEC-7"]).encode()).decode()
    assert a.get("/api/v1/runs", params={"cursor": crafted}).status_code == 400
    assert a.get("/api/v1/runs", params={"night": "2026-13-99"}).status_code == 400
    assert a.get("/api/v1/runs/DEC-7%0A").status_code == 404                  # fullmatch: no trailing newline
    for _ in range(3):
        assert b.get("/api/v1/runs/DEC-7").status_code == 404
    assert len(rows(cp, "SELECT 1 FROM dq_ctl.guardrail_events WHERE code='SCOPE_DENIED' AND subject='trace:DEC-7'")) == 1


def test_nits_guardrail_pages_are_full_after_scope_filtering(cp):
    for i in range(5):                                       # audit-only rows interleaved with visible ones
        ES.record_guardrail(cp, "SCOPE_DENIED", f"trace:X-{i}", seat="api", detail={"requests": 1})
        ES.record_guardrail(cp, "FALLBACK", f"rule:R-{i}", authorities=A, detail={"rejected": 1})
    a = client(cp, who(A, ["release:AUTH_A"]))
    page = a.get("/api/v1/guardrail-events", params={"limit": 2}).json()
    assert len(page["events"]) == 2 and page["next"] and all(e["code"] == "FALLBACK" for e in page["events"])
    seen = [e["id"] for e in page["events"]]
    while page["next"]:
        page = a.get("/api/v1/guardrail-events", params={"limit": 2, "cursor": page["next"]}).json()
        seen += [e["id"] for e in page["events"]]
    assert len(seen) == len(set(seen)) == 5



def test_run_diff_reports_verdict_and_guardrail_deltas(cp):
    """F-35: two renders of ONE subject, a PASS and a REJECT: same steps, outcome deltas only."""
    svc(cp, SCRIPTED).explain("rule_disposition", RULE, subject=SUBJ, authorities=A)
    bad = ScriptedTransport(bad_every=0, bad_subjects=[SUBJ], mutator="QUANTIFIER")
    svc(cp, {**SCRIPTED, "providers": {"anthropic": {"max_tokens": 301}}}, transport=bad).explain(
        "rule_disposition", RULE, subject=SUBJ, authorities=A)
    a, b = (r["trace_id"] for r in rows(cp, "SELECT trace_id FROM dq_ctl.llm_calls ORDER BY call_id"))
    d = client(cp, who(A, ["release:AUTH_A"])).get("/api/v1/runs/diff", params={"a": a, "b": b}).json()
    M.RunDiff.model_validate(d)
    assert d["spans_only_in_a"] == d["spans_only_in_b"] == 0
    ex = next(x for x in d["span_deltas"] if x["key"].startswith("llm.explain"))
    assert ex["verdict"] == {"a": "PASS", "b": "REJECT"}
    assert d["guardrail_deltas"]["CHECK_REJECTED"] == {"a": 0, "b": 1}
