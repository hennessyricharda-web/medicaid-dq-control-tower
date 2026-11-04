"""Part F P1-1 §F end to end: the observability routes on the REAL app (create_app), the explanations behind them
(ExplainService through `_explain_raw`), scope negative controls and the explanation binding.

A Medicaid reviewer (maker) opens cards, which renders and caches explanations under EXPLAIN traces; a SNAP reviewer
(snap) must see none of it; the audit duty (auditor) sees everything, including the SCOPE_DENIED events."""
import pytest
from fastapi.testclient import TestClient

from dqagent.api.app import create_app
from dqagent.api.contracts import obs as M

from .fixtures_w1 import ie_file, person
from .test_parte_explanations import cp_dsn_module  # noqa: F401 - module-scoped fresh control plane
from .w1_harness import Slice


def H(rt, user, **kw):
    return {"Authorization": f"Bearer {rt.idp.token(user, **kw)}"}


@pytest.fixture(scope="module")
def world(cp_dsn_module, tmp_path_factory):  # noqa: F811
    s = Slice(cp_dsn_module)
    tmp = tmp_path_factory.mktemp("obs")
    held = tmp / "held.txt"
    held.write_bytes(ie_file([person("P3", "M0003"), person("P4", "M0004")], trailer=7))   # ABC fail -> HELD
    s.run_pipeline("p4_eligibility_ie", "B-HELD", [("IE", held)], ["bronze.ie_segment"])
    api = TestClient(create_app(s))
    q = api.get("/api/v1/queue", headers=H(s, "maker")).json()
    uk = sorted({u for cl in q for u in cl["units"]})[0]
    card = api.get(f"/api/v1/units/{uk}", headers=H(s, "maker")).json()
    return s, api, uk, card


def explain_trace(api, s, uk):
    runs = api.get("/api/v1/runs", headers=H(s, "maker"), params={"kind": "EXPLAIN", "limit": 200}).json()["runs"]
    for r in runs:
        d = api.get(f"/api/v1/runs/{r['trace_id']}", headers=H(s, "maker")).json()
        if any(sp["subject"] == f"unit:{uk}" for sp in d["spans"]):
            return r["trace_id"], d
    raise AssertionError("no EXPLAIN trace for the unit card")


def test_card_explanation_goes_through_the_service_and_is_cached(world):
    s, api, uk, card = world
    e = card["explanation"]
    assert e["fact_check"] == "TEMPLATE" and e["source_mode"] == "TEMPLATE"            # F-35: the served row's mode
    assert set(e) == {"text", "source", "fact_check", "facts_hash", "text_hash", "source_mode"}
    again = api.get(f"/api/v1/units/{uk}", headers=H(s, "maker")).json()["explanation"]
    assert again == e                                         # byte-stable: a cache hit equals the render
    with s.cp.as_role("dq_auditor") as c:
        rows = c.execute("SELECT authorities, source_mode FROM dq_ctl.explanations WHERE subject=%s",
                         (f"unit:{uk}",)).fetchall()
        calls = c.execute("SELECT count(*) n FROM dq_ctl.llm_calls WHERE subject=%s", (f"unit:{uk}",)).fetchone()
    assert len(rows) == 1 and rows[0]["source_mode"] == "TEMPLATE" and "AUTH_MEDICAID" in rows[0]["authorities"]
    assert calls["n"] == 1                                    # the second view was a hit: nothing written


def test_runs_list_and_detail_are_scoped(world):
    s, api, uk, _card = world
    lst = api.get("/api/v1/runs", headers=H(s, "maker"), params={"kind": "EXPLAIN"}).json()
    M.RunList.model_validate(lst)
    assert lst["runs"] and all(r["kind"] == "EXPLAIN" for r in lst["runs"])
    tid, d = explain_trace(api, s, uk)
    M.RunDetail.model_validate(d)
    names = {sp["name"] for sp in d["spans"]}
    assert {"llm.explain", "llm.validate"} <= names
    call = next(sp["llm_call"] for sp in d["spans"] if sp["name"] == "llm.explain")
    assert call["transport"] == "none" and call["verdict"] == "NOT_CHECKED"
    snap = api.get("/api/v1/runs", headers=H(s, "snap"), params={"kind": "EXPLAIN"}).json()["runs"]
    assert tid not in {r["trace_id"] for r in snap}           # a SNAP reviewer sees no Medicaid runs
    assert api.get(f"/api/v1/runs/{tid}", headers=H(s, "snap")).status_code == 404
    assert api.get("/api/v1/runs/EXP-0000000000000000-9", headers=H(s, "snap")).status_code == 404
    page = api.get("/api/v1/runs", headers=H(s, "maker"), params={"limit": 1}).json()
    if page["next"]:
        nxt = api.get("/api/v1/runs", headers=H(s, "maker"), params={"limit": 1, "cursor": page["next"]}).json()
        assert nxt["runs"] and nxt["runs"][0]["trace_id"] != page["runs"][0]["trace_id"]
    assert api.get("/api/v1/runs", headers=H(s, "maker"), params={"cursor": "junk"}).status_code == 400


def test_snap_reviewer_sees_no_medicaid_spans_anywhere(world):
    s, api, uk, _card = world
    out = api.get(f"/api/v1/units/{uk}/trace", headers=H(s, "snap"))
    none = api.get("/api/v1/units/NO-SUCH-UNIT/trace", headers=H(s, "snap"))
    assert out.status_code == none.status_code == 200 and out.json()["spans"] == none.json()["spans"] == []
    t = api.get(f"/api/v1/units/{uk}/trace", headers=H(s, "maker")).json()
    M.UnitTrace.model_validate(t)
    assert t["spans"] and all(sp["subject"] == f"unit:{uk}" or f"unit:{uk}" in sp["refs"] for sp in t["spans"])
    ev = api.get("/api/v1/guardrail-events", headers=H(s, "snap")).json()
    M.GuardrailList.model_validate(ev)
    assert all(uk not in e["subject"] for e in ev["events"])


def test_otlp_export(world):
    s, api, uk, _card = world
    tid, _d = explain_trace(api, s, uk)
    o = api.get(f"/api/v1/runs/{tid}/otlp", headers=H(s, "maker")).json()
    M.OtlpExport.model_validate(o)
    spans = o["resourceSpans"][0]["scopeSpans"][0]["spans"]
    assert {sp["name"] for sp in spans} >= {"llm.explain", "llm.validate"}
    assert api.get(f"/api/v1/runs/{tid}/otlp", headers=H(s, "snap")).status_code == 404


def test_citations_resolve_and_rescope(world):
    s, api, uk, card = world
    e = card["explanation"]
    ref = f"{e['facts_hash']}:{e['text_hash']}"
    r = api.get(f"/api/v1/explanations/unit:{uk}/citations", headers=H(s, "maker"), params={"explanation_ref": ref})
    assert r.status_code == 200, r.text
    c = M.Citations.model_validate(r.json())
    assert c.label == "generated_from_facts" and c.citations
    unit_cites = [x for x in c.citations if x.evidence_ref == f"unit:{uk}"]
    assert unit_cites and all(x.resolvable for x in unit_cites)
    assert api.get(f"/api/v1/explanations/unit:{uk}/citations", headers=H(s, "snap"),
                   params={"explanation_ref": ref}).status_code == 404    # re-scoped on resolve: SNAP cannot


def test_scope_denials_are_audit_only(world):
    s, api, uk, _card = world
    api.get(f"/api/v1/units/{uk}/trace", headers=H(s, "snap"))
    assert api.get("/api/v1/guardrail-events", headers=H(s, "maker"),
                   params={"code": "SCOPE_DENIED"}).status_code == 403
    assert all(e["code"] != "SCOPE_DENIED" for e in
               api.get("/api/v1/guardrail-events", headers=H(s, "maker")).json()["events"])
    aud = api.get("/api/v1/guardrail-events", headers=H(s, "auditor"), params={"code": "SCOPE_DENIED"}).json()
    assert any(e["subject"] == f"unit:{uk}" for e in aud["events"])
    assert all(e["fail_mode"] == "closed" for e in aud["events"])


def test_binding_is_per_subject_config_and_version_across_servers(world):
    """I1 / I2: a second server (empty in-memory issued set) binds the explanation it shows; a text cached for the
    same facts under ANOTHER provider configuration (or a superseded version) binds nothing; nor does an unknown
    text. Full-key semantics of ExplainService.issued are tested in test_obs_explain::test_i2_*."""
    s, api, uk, card = world
    e = card["explanation"]
    other_text = "Unit text cached under another configuration."
    import hashlib
    th = hashlib.sha256(other_text.encode()).hexdigest()
    with s.cp.as_role("dq_trace_svc", trace=False) as c:
        k = c.execute("SELECT * FROM dq_ctl.explanations WHERE subject=%s", (f"unit:{uk}",)).fetchone()
        c.execute("INSERT INTO dq_ctl.explanations(subject, authorities, facts_hash, provider_config_digest, "
                  "prompt_version, check_version, template_version, text, text_hash, source_mode, claims) "
                  "VALUES (%s,%s,%s,'pc-other',%s,%s,%s,%s,%s,'SCRIPTED','[]')",
                  (k["subject"], k["authorities"], k["facts_hash"], k["prompt_version"], k["check_version"],
                   k["template_version"], other_text, th))
    fresh = TestClient(create_app(s))
    ok = fresh.post(f"/api/v1/units/{uk}/challenge", headers=H(s, "maker"),
                    json={"decision": "RELEASE", "explanation_ref": {"facts_hash": e["facts_hash"],
                                                                     "text_hash": e["text_hash"]}})
    assert ok.status_code == 200, ok.text
    for bad_th in (th, "0" * 64):
        bad = fresh.post(f"/api/v1/units/{uk}/challenge", headers=H(s, "maker"),
                         json={"decision": "RELEASE", "explanation_ref": {"facts_hash": e["facts_hash"],
                                                                          "text_hash": bad_th}})
        assert bad.status_code == 422 and bad.json()["detail"]["status"] == "EXPLANATION_MISMATCH"


def test_feed_ack_codes_are_per_kind(world):
    s, api, _uk, _card = world
    with s.cp.as_role("dq_seal_svc") as c:
        item = c.execute("SELECT dq_ctl.feed_item_open('ZZ','UNROUTED','UNROUTED_FILE','B-UNR',%s,'AUTH_INTAKE',NULL)"
                         " AS i", ("ab" * 32,)).fetchone()["i"]
    h = H(s, "checker")
    r = api.post(f"/api/v1/feeds/items/{item}/ack", headers=h, json={"reason_code": "HOLIDAY"})
    assert r.status_code == 422 and "UNROUTED_FILE" in r.text and "ROUTE_PROPOSED" in r.text
    assert api.post(f"/api/v1/feeds/items/{item}/ack", headers=h, json={"reason_code": "NOPE"}).status_code == 422
    items = {i["item_id"]: i for i in api.get("/api/v1/feeds/items", headers=h).json()}
    assert "matched no route" in items[item]["explanation"]["text"]


# ------------------------------------------------------------------------------------------------ F-35
def test_explainer_reports_source_mode_and_transport(world):
    s, api, _uk, _card = world
    e = api.get("/api/v1/explainer", headers=H(s, "maker")).json()
    assert e["source_mode"] == "TEMPLATE" and e["transport"] == "none" and e["label"]


def test_unit_trace_steps_link_back_to_their_run_and_runs_filter_by_unit(world):
    s, api, uk, _card = world
    t = api.get(f"/api/v1/units/{uk}/trace", headers=H(s, "maker")).json()
    M.UnitTrace.model_validate(t)
    tids = {sp["trace_id"] for sp in t["spans"]}
    assert tids
    for tid in tids:
        assert api.get(f"/api/v1/runs/{tid}", headers=H(s, "maker")).status_code == 200
    by_unit = api.get("/api/v1/runs", headers=H(s, "maker"), params={"unit": uk, "limit": 200}).json()
    M.RunList.model_validate(by_unit)
    assert tids <= {r["trace_id"] for r in by_unit["runs"]}
    snap = api.get("/api/v1/runs", headers=H(s, "snap"), params={"unit": uk}).json()
    none = api.get("/api/v1/runs", headers=H(s, "snap"), params={"unit": "NO-SUCH-UNIT"}).json()
    assert snap == none == {"runs": [], "next": None}                 # out of scope looks exactly like unknown
    aud = api.get("/api/v1/runs", headers=H(s, "auditor"), params={"unit": uk, "limit": 200}).json()
    assert tids <= {r["trace_id"] for r in aud["runs"]}


def test_usage_template_zero_tokens(world):
    s, api, _uk, _card = world
    u = api.get("/api/v1/llm/usage", headers=H(s, "maker")).json()
    M.Usage.model_validate(u)
    tmpl = [r for r in u["rows"] if r["source_mode"] == "TEMPLATE"]
    assert tmpl and all(r["input_tokens"] == r["output_tokens"] == 0 for r in tmpl)
    assert all(r["tokens_label"] == "no model is called: 0 tokens" and r["verdicts"].get("NOT_CHECKED") == r["calls"]
               for r in tmpl)
    assert api.get("/api/v1/llm/usage", headers=H(s, "snap")).json()["rows"] == []       # no Medicaid calls for SNAP


def test_prompt_version_history(world):
    s, api, _uk, _card = world
    from dqagent import llm
    pr = api.get("/api/v1/prompts", headers=H(s, "maker")).json()
    M.Prompts.model_validate(pr)
    cur = [x for x in pr["prompts"] if x["current"]]
    assert len(cur) == 1 and cur[0]["prompt_version"] == llm.PROMPT_VERSION and cur[0]["calls"] >= 1
    assert cur[0]["system_prompt"] == llm.SYSTEM_PROMPT and cur[0]["traces"]
    snap = api.get("/api/v1/prompts", headers=H(s, "snap")).json()["prompts"]
    assert all(x["calls"] == 0 and x["traces"] == [] for x in snap)                     # usage is scoped


def test_run_diff_reports_outcome_delta(world):
    s, api, uk, _card = world
    runs = api.get("/api/v1/runs", headers=H(s, "maker"), params={"kind": "EXPLAIN", "limit": 2}).json()["runs"]
    a, b = runs[0]["trace_id"], runs[1]["trace_id"]
    d = api.get("/api/v1/runs/diff", headers=H(s, "maker"), params={"a": a, "b": b})
    assert d.status_code == 200, d.text
    diff = M.RunDiff.model_validate(d.json())
    assert diff.a == a and diff.b == b and diff.spans_only_in_a == diff.spans_only_in_b == 2   # other subjects
    assert not any(k in json_keys(d.json()) for k in ("reason", "reasons", "cause", "because"))
    assert api.get("/api/v1/runs/diff", headers=H(s, "snap"), params={"a": a, "b": b}).status_code == 404
    assert api.get("/api/v1/runs/diff", headers=H(s, "maker"),
                   params={"a": a, "b": "EXP-0000000000000000-0"}).status_code == 404


def json_keys(o) -> set:
    if isinstance(o, dict):
        return set(o) | {k for v in o.values() for k in json_keys(v)}
    if isinstance(o, list):
        return {k for v in o for k in json_keys(v)}
    return set()
