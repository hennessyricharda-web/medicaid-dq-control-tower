"""Part E §7: explainer, facts sanitisation, structured check, and provider contract tests against a LOCAL stub
HTTP server (the providers are "not exercised live")."""
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from dqagent import llm
from dqagent.llm import check as chk
from dqagent.llm import facts as F
from dqagent.llm import providers
from dqagent.llm.template import KINDS, render

CLASSIFICATION = {"msis_id": "identity", "member_id": "identity", "ELG.MEMBER_ID": "identity", "dx1": "clinical",
                  "CLM.DX1": "clinical", "sud_flag": "part2", "program_code": "program"}

SAMPLES = {
    "unit_card": {"unit_key": "B-ie_extract_20260101:MEDICAID:064645daea03f9e8c76c9684", "relation": "bronze.ie_segment",
                  "authority": "AUTH_MEDICAID", "state": "HELD", "tier": "L2", "recommended_action": "RELEASE",
                  "rules_checked": 42, "rules_not_passed": 3, "approvals_required": 1, "approvals_recorded": 0},
    "rule_disposition": {"rule_id": "B-CMP-CLM-POS", "title": "CLM.POS is present", "disposition": "FAIL",
                         "failed": 23, "evaluated": 164, "relation": "bronze.encounter_header", "gating": "advisory"},
    "decision": {"decision_id": 77, "verb": "VETO", "role": "checker", "unit_key": "B-x:MEDICAID:aa11",
                 "resulting_state": "HELD", "reason_code": "EVIDENCE_INCOMPLETE"},
    "change_request": {"cr_id": "CR-12", "kinds": ["AUTONOMY"], "status": "PROPOSED", "approvals_required": 2,
                       "approvals_recorded": 0,
                       "impact": {"tier_from": "L2", "tier_to": "L3", "units_reopened": 0,
                                  "track_record_hash": "9f2c41aa"}},
    "lookback": {"lookback_id": "LB-4", "unit_key": "B-x:MEDICAID:bb22", "rule_id": "R-834-001",
                 "status": "WITHDRAWN", "in_live_manifest": False},
    "monitor_signal": {"relation": "bronze.encounter_header", "metric": "rows", "measure": "rows", "period": "2026-Q1",
                       "status": "SIGNAL", "chart": "xmr", "rule": "R1", "direction": "LOW", "value": 164,
                       "cl": 235.33, "ucl": 260.48, "lcl": 210.18, "baseline_periods": 12},
    "parallel_break": {"relation": "bronze.encounter_header", "measure": "amount:total_charge", "period": "2026-Q1",
                       "as_of": "2026-04-01", "legacy": 51429.41, "new": 50179.41, "delta": -1250.0,
                       "tolerance": 0.005, "status": "BREAK", "system": "LEGACY_MMIS"},
    "feed_item": {"item_id": 7, "feed": "IE_EXTRACT", "kind": "EMPTY_DELIVERY", "file": "ie_extract_20260401.txt",
                  "status": "OPEN"},
    "demotion": {"agent": "dq-reconciliation", "action_class": "RELEASE", "authority": "AUTH_MEDICAID",
                 "from_tier": "L3", "to_tier": "L2", "reason_code": "VETO", "ref": "unit:B-x:MEDICAID:cc33"},
    "autonomy": {"agent": "dq-reconciliation", "action_class": "RELEASE", "authority": "AUTH_SNAP", "tier": "L2",
                 "track_record": {"autonomous": 0, "after_veto_window": 0, "vetoes": 0, "overrides": 0, "denies": 0,
                                  "lookback_withdrawn": 0}},
    "cde": {"element": "CLM.POS", "status": "BELOW", "completeness_rate": 0.9123, "completeness_min": 0.98,
            "validity_rate": 0.99, "validity_min": 0.95, "steward_domain": "CLAIMS"},
    # Part E §10b (added by the nightly-simulator seat: every kind needs a sample)
    "nightly_run": {"run_id": "SIM-20260115", "night_date": "2026-01-15", "files": 1, "abc_gating": 0,
                    "abc_advisory": 0, "created_units": 24, "released_units": 14, "veto_windows": 0,
                    "held_units": 12, "held_reasons": [{"reason": "floor: ABC failed in batch -> H_IN_2", "units": 12}],
                    "pending_units": 0, "feed_items": 2, "monitor_signals": 0, "demotions": 0},
}
TEMPLATE = {"provider": "template"}


def _f(kind):
    return F.build(kind, F.adapt(kind, SAMPLES[kind]), classification=CLASSIFICATION)


# ------------------------------------------------------------------------------------------------ template
def test_every_kind_has_a_sample_and_a_template():
    assert set(SAMPLES) == set(KINDS)
    assert "arrived with no records" in llm.explain("feed_item", SAMPLES["feed_item"], cfg=TEMPLATE)["text"]
    missing = {**SAMPLES["feed_item"], "kind": "MISSING_DELIVERY", "file": None}
    assert "No delivery from feed IE_EXTRACT" in llm.explain("feed_item", missing, cfg=TEMPLATE)["text"]


@pytest.mark.parametrize("kind", KINDS)
def test_template_is_byte_stable_labelled_template_and_prints_only_facts(kind):
    """E2: template output is deterministic and generated only from facts; it is labelled TEMPLATE, never PASS (it
    is not fact-checked). The lint below only proves it prints no number or identifier that is not a fact."""
    a = llm.explain(kind, SAMPLES[kind], cfg=TEMPLATE, classification=CLASSIFICATION)
    b = llm.explain(kind, json.loads(json.dumps(SAMPLES[kind])), cfg=TEMPLATE, classification=CLASSIFICATION)
    assert a == b and a["source"] == "template" and a["fact_check"] == "TEMPLATE"
    assert set(a) == {"text", "source", "fact_check", "facts_hash", "text_hash", "source_mode"}   # + F-35
    assert a["source_mode"] == "TEMPLATE"
    ok, why = chk.check(a["text"], [], _f(kind), require_claims=False)
    assert ok, why
    for jargon in ("XmR", "sigma", "p-chart", "PSI", "disposition", "None", "{", "}"):
        assert jargon not in a["text"], (kind, jargon, a["text"])


def test_template_text_is_pinned():
    """Byte-stability regression: a template change must update this text deliberately."""
    t = llm.explain("monitor_signal", SAMPLES["monitor_signal"], cfg=TEMPLATE)["text"]
    assert t == ("The number of records for bronze.encounter_header in 2026-Q1 was 164. The usual range over the "
                 "previous 12 periods is 210.18 to 260.48, around 235.33. This is below the usual range. This is an "
                 "advisory signal: it compares this delivery with earlier ones and never holds any records on its "
                 "own. You can check with the sending team whether anything changed or is missing, and note what you "
                 "find so the next review has the context.")
    p = llm.explain("parallel_break", SAMPLES["parallel_break"], cfg=TEMPLATE)["text"]
    assert "$51,429.41" in p and "$50,179.41" in p and "$1,250.00 lower" in p and "0.5%" in p
    assert "unexplained" in p


def test_default_config_is_template_and_needs_no_network(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("network used")
    monkeypatch.setattr("urllib.request.urlopen", boom)
    cfg = llm.load_config()
    assert cfg["provider"] == "template"
    e = llm.explain("unit_card", SAMPLES["unit_card"])
    assert e["source"] == "template" and e["fact_check"] == "TEMPLATE"
    assert llm.status()["provider"] == "template" and llm.status()["configured"] is True


# ------------------------------------------------------------------------------------------------ facts
def test_facts_drop_masked_classes_and_part2():
    raw = {"relation": "bronze.encounter_header", "member_id": "ZZ12345678", "dx1": "E119", "sud_flag": "Y",
           "failed": 40, "detail": {"msis_id": "ZZ00000001", "element": "CLM.DX1", "value": "E119", "rate": 0.5},
           "sample_tokens": ["t1"]}
    f = F.build("rule_disposition", raw, classification=CLASSIFICATION)
    s = json.dumps(f)
    for bad in ("member_id", "ZZ12345678", "dx1\"", "E119", "sud_flag", "msis_id", "ZZ00000001", "sample_tokens"):
        assert bad not in s, bad
    assert f["detail"]["element"] == "CLM.DX1" and f["detail"]["rate"] == 0.5     # names are metadata; values go
    assert F.build("unit_card", {"part2": True, "state": "HELD", "relation": "x"}) == \
        {"explanation_kind": "unit_card", "part2_withheld": True}
    t = llm.explain("unit_card", {"part2": True, "state": "HELD", "unit_key": "U-1"}, cfg=TEMPLATE)
    assert "U-1" not in t["text"] and "withheld" in t["text"]


def test_small_cells_are_suppressed_but_not_zero_or_eleven():
    f = F.build("rule_disposition", {"failed": 3, "evaluated": 11, "rows": 0, "unit_count": 10,
                                     "approvals_required": 2})
    assert f["failed"] == "<11" and f["unit_count"] == "<11"
    assert f["evaluated"] == 11 and f["rows"] == 0 and f["approvals_required"] == 2
    text = render("rule_disposition", {**f, "rule_id": "B-VAL-X", "disposition": "FAIL"})
    assert "fewer than 11" in text and "3" not in text.replace("B-VAL-X", "")


def test_facts_hash_is_deterministic_and_order_free():
    a = F.facts_hash(F.build("cde", {"element": "A.B", "status": "MEETS", "completeness_rate": 0.99}))
    b = F.facts_hash(F.build("cde", {"completeness_rate": 0.99, "status": "MEETS", "element": "A.B"}))
    assert a == b


# ------------------------------------------------------------------------------------------------ check
def test_check_unit_rules():
    f = _f("rule_disposition")
    both = [{"fact_key": "failed", "value": 23}, {"fact_key": "evaluated", "value": 164}]
    assert chk.check("Check B-CMP-CLM-POS found 23 of 164 records with a problem.", both, f)[0]
    assert not chk.check("Check B-CMP-CLM-POS found 23 of 164 records with a problem.", [], f)[0]  # unbound (E1)
    assert not chk.check("Check B-CMP-CLM-POS found 24 of 164 records.", both, f)[0]                # number
    assert not chk.check("Check B-CMP-CLM-XX found 23 records.", [], f)[0]                          # identifier
    assert not chk.check("Twenty-three records failed.", [], f)[0]                                   # spelled
    assert not chk.check("The check did not fail.", [], f)[0]                                        # negated
    assert not chk.check("The check passed.", [], f)[0]                                              # opposite
    assert not chk.check("It failed because the sender changed format.", [], f)[0]                   # causal
    assert chk.check("It failed because the sender changed format.", [{"fact_key": "disposition", "value": "FAIL"}],
                     {**f, "hypothesis": "sender format change"})[0]
    assert not chk.check("It is safe to release these records.", [], f)[0]                           # advice
    assert not chk.check("You should approve this.", [], f)[0]
    assert chk.check("It failed.", [{"fact_key": "failed", "value": 23}], f)[0]
    assert not chk.check("It failed.", [{"fact_key": "failed", "value": 22}], f)[0]                  # claim value
    assert not chk.check("It failed.", [{"fact_key": "nope", "value": 1}], f)[0]                     # claim key
    assert not chk.check("It failed.", None, f)[0]                                                   # claims missing


# ------------------------------------------------------------------------------------------------ providers (stub)
class Stub:
    def __init__(self):
        self.requests, self.reply, self.delay = [], "", 0.0
        stub = self

        class H(BaseHTTPRequestHandler):
            def do_POST(self):
                body = self.rfile.read(int(self.headers.get("content-length", 0))).decode()
                stub.requests.append({"path": self.path, "headers": dict(self.headers), "body": body})
                time.sleep(stub.delay)
                out = json.dumps(stub.reply).encode()
                try:
                    self.send_response(200)
                    self.send_header("content-type", "application/json")
                    self.send_header("content-length", str(len(out)))
                    self.end_headers()
                    self.wfile.write(out)
                except (BrokenPipeError, ConnectionResetError):
                    pass

            def log_message(self, *a):
                pass
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}"
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()


@pytest.fixture
def stub(monkeypatch):
    for v in ("http_proxy", "HTTP_PROXY", "all_proxy", "ALL_PROXY"):
        monkeypatch.delenv(v, raising=False)
    s = Stub()
    yield s
    s.close()


def _wrap(provider, payload_text):
    if provider == "anthropic":
        return {"content": [{"type": "text", "text": payload_text}]}
    if provider == "watsonx":
        return {"results": [{"generated_text": payload_text}]}
    return {"choices": [{"message": {"content": payload_text}}]}


def _env(provider, url):
    return {"anthropic": {"DQ_EXPLAINER_ANTHROPIC_ENDPOINT": url + "/v1/messages", "DQ_EXPLAINER_ANTHROPIC_KEY": "k",
                          "DQ_EXPLAINER_ANTHROPIC_MODEL": "m"},
            "watsonx": {"DQ_EXPLAINER_WATSONX_ENDPOINT": url, "DQ_EXPLAINER_WATSONX_TOKEN": "t",
                        "DQ_EXPLAINER_WATSONX_PROJECT": "p", "DQ_EXPLAINER_WATSONX_MODEL": "m"},
            "azure_openai": {"DQ_EXPLAINER_AZURE_OPENAI_ENDPOINT": url, "DQ_EXPLAINER_AZURE_OPENAI_KEY": "k",
                             "DQ_EXPLAINER_AZURE_OPENAI_DEPLOYMENT": "d"},
            "cortex": {"DQ_EXPLAINER_CORTEX_ENDPOINT": url, "DQ_EXPLAINER_CORTEX_TOKEN": "t",
                       "DQ_EXPLAINER_CORTEX_MODEL": "m"}}[provider]


def _cfg(provider, timeout=3.0):
    return {"provider": provider, "timeout_s": timeout, "providers": {provider: {"max_tokens": 300}}}


# slot form (F-21 round 3): the provider names facts, the slots are filled from the facts
GOOD = {"text": "Check {rule_id} found {failed} of {evaluated} records in {relation} that need attention. "
                "It is advisory and does not stop the data going out.",
        "claims": []}
GOOD_RENDERED = ("Check B-CMP-CLM-POS found 23 of 164 records in bronze.encounter_header that need attention. "
                 "It is advisory and does not stop the data going out.")
PATHS = {"anthropic": "/v1/messages", "watsonx": "/ml/v1/text/generation", "azure_openai":
         "/openai/deployments/d/chat/completions", "cortex": "/api/v2/cortex/inference:complete"}


@pytest.mark.parametrize("provider", providers.NAMES)
def test_provider_good_output_passes(stub, provider):
    stub.reply = _wrap(provider, json.dumps(GOOD))
    e = llm.explain("rule_disposition", SAMPLES["rule_disposition"], cfg=_cfg(provider),
                    classification=CLASSIFICATION, env=_env(provider, stub.url), deployment="test")
    assert e["fact_check"] == "PASS" and e["source"] == provider and e["text"] == GOOD_RENDERED
    assert stub.requests and stub.requests[0]["path"].startswith(PATHS[provider])
    assert providers.LABEL == "not exercised live"


BAD = {
    "invented number": "Check B-CMP-CLM-POS found 31 of 164 records with a problem.",
    "spelled-out number": "Check B-CMP-CLM-POS found twenty-three records with a problem.",
    "negated disposition": "Check B-CMP-CLM-POS did not fail on any of the 164 records.",
    "causal wording": "Check B-CMP-CLM-POS has the result FAIL because the plan changed its software.",
    "release advice": "Check B-CMP-CLM-POS has the result FAIL, but it is safe to release the records.",
}


@pytest.mark.parametrize("why", sorted(BAD))
@pytest.mark.parametrize("provider", providers.NAMES)
def test_provider_bad_output_falls_back_to_template(stub, provider, why):
    stub.reply = _wrap(provider, json.dumps({"text": BAD[why], "claims": []}))
    llm.reset_stats()
    e = llm.explain("rule_disposition", SAMPLES["rule_disposition"], cfg=_cfg(provider),
                    classification=CLASSIFICATION, env=_env(provider, stub.url), deployment="test")
    t = llm.explain("rule_disposition", SAMPLES["rule_disposition"], cfg=TEMPLATE, classification=CLASSIFICATION)
    assert e["fact_check"] == "FALLBACK" and e["source"] == "template", why
    assert e["text"] == t["text"] and e["facts_hash"] == t["facts_hash"]
    assert llm.status(cfg=_cfg(provider), env=_env(provider, stub.url))["fallback_count"] == 1


@pytest.mark.parametrize("provider", providers.NAMES)
def test_provider_timeout_falls_back(stub, provider):
    stub.reply = _wrap(provider, json.dumps(GOOD))
    stub.delay = 1.5
    t0 = time.monotonic()
    e = llm.explain("rule_disposition", SAMPLES["rule_disposition"], cfg=_cfg(provider, timeout=0.3),
                    classification=CLASSIFICATION, env=_env(provider, stub.url), deployment="test")
    assert e["fact_check"] == "FALLBACK" and time.monotonic() - t0 < 1.4


@pytest.mark.parametrize("provider", providers.NAMES)
def test_provider_malformed_and_unconfigured_fall_back(stub, provider):
    stub.reply = _wrap(provider, "not json at all")
    e = llm.explain("decision", SAMPLES["decision"], cfg=_cfg(provider), env=_env(provider, stub.url), deployment="test")
    assert e["fact_check"] == "FALLBACK"
    n = len(stub.requests)
    e = llm.explain("decision", SAMPLES["decision"], cfg=_cfg(provider), env={}, deployment="test")  # no creds
    assert e["fact_check"] == "FALLBACK" and len(stub.requests) == n
    assert providers.complete(provider, {"system": "s", "user": "u"}, {}, env={}) is None


@pytest.mark.parametrize("provider", providers.NAMES)
def test_spy_payload_never_carries_identity_clinical_or_part2(stub, provider):
    stub.reply = _wrap(provider, json.dumps(GOOD))
    raw = {**SAMPLES["rule_disposition"], "member_id": "ZZ12345678", "msis_id": "ZZ87654321", "dx1": "E119",
           "sud_flag": "Y", "rows_sample": [{"member_id": "ZZ12345678"}]}
    llm.explain("rule_disposition", raw, cfg=_cfg(provider), classification=CLASSIFICATION,
                env=_env(provider, stub.url), deployment="test")
    body = stub.requests[-1]["body"]
    for bad in ("member_id", "msis_id", "ZZ12345678", "ZZ87654321", "dx1", "E119", "sud_flag", "rows_sample"):
        assert bad not in body, (provider, bad)
    n = len(stub.requests)
    e = llm.explain("unit_card", {"part2": True, "unit_key": "U-P2", "state": "HELD"}, cfg=_cfg(provider),
                    env=_env(provider, stub.url), deployment="test")
    sent = stub.requests[n]["body"] if len(stub.requests) > n else ""
    assert "U-P2" not in sent and "U-P2" not in e["text"]


# ------------------------------------------------------------------------------------------------ engine shapes
def test_explains_real_monitor_results_and_parallel_rows():
    """The adapters accept the monitor seat's result dicts and parallel-run rows as-is."""
    from pathlib import Path

    from dqagent.adapters.metadata import legacy_totals as lt
    from dqagent.agent import monitor as m
    w1 = Path(__file__).resolve().parents[2] / "synthetic" / "out" / "w1"
    h = m.load_history(w1 / "monitor_history.json")
    man = json.loads((w1 / "parte_manifest.json").read_text())
    res = m.run(h["metrics"], man["w1_observed"], period=man["period"], exclude=h["exclude"])
    classification = {"ENR.COV_END": "coverage", "ELG.PROGRAM": "program"}
    for r in res:
        e = llm.explain("monitor_signal", r, cfg=TEMPLATE, classification=classification)
        f = F.build("monitor_signal", F.adapt("monitor_signal", r), classification=classification)
        assert e["fact_check"] == "TEMPLATE" and chk.check(e["text"], [], f, require_claims=False)[0], \
            (r["metric"], e["text"])
        assert r["relation"] in e["text"]
    drop = next(r for r in res if r["relation"] == "bronze.encounter_header" and r["metric"] == "rows")
    assert "below the usual range" in llm.explain("monitor_signal", drop, cfg=TEMPLATE)["text"]
    jump = next(r for r in res if r["metric"] == "null_rate:ENR.COV_END")
    assert "91.4%" in llm.explain("monitor_signal", jump, cfg=TEMPLATE)["text"]
    shift = next(r for r in res if r["metric"] == "mix:ELG.PROGRAM")
    t = llm.explain("monitor_signal", shift, cfg=TEMPLATE)["text"]
    assert "clearly shifted" in t and "SNAP: 21.9% now" in t
    doc = lt.load(w1 / "legacy_totals.json")
    for row in lt.compare(doc, lt.new_side_from_metrics(man["w1_observed"]), period=doc["period"],
                          as_of=doc["as_of"], tolerance=man["parallel_run"]["tolerance"]):
        e = llm.explain("parallel_break", {**row, "system": doc["system"]}, cfg=TEMPLATE)
        f = F.build("parallel_break", F.adapt("parallel_break", {**row, "system": doc["system"]}))
        assert chk.check(e["text"], [], f, require_claims=False)[0], e["text"]
    clinical = next(r for r in res if r["metric"] == "mix:CLM.POS")
    masked = llm.explain("monitor_signal", clinical, cfg=TEMPLATE, classification={"CLM.POS": "clinical"})
    assert "By group" not in masked["text"]                                   # category values withheld


def test_accepts_the_classification_defaults_file_shape():
    from pathlib import Path

    import yaml
    p = Path(__file__).resolve().parents[2] / "rules" / "classification_defaults.yaml"
    if not p.exists():
        pytest.skip("rules/classification_defaults.yaml not present yet")
    doc = yaml.safe_load(p.read_text())
    f = F.build("rule_disposition", {"rule_id": "B-CMP-CLM-DX1", "CLM.DX1": "E119", "failed": 0}, classification=doc)
    assert "CLM.DX1" not in f and f["rule_id"] == "B-CMP-CLM-DX1"
