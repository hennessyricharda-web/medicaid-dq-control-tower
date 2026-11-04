"""Part F P1-1 §C and R4-3: LLM transports, the scripted model, and the transport allow-list.

The scripted transport calls no model. It re-derives each request with the provider's own build_request and refuses
a body that differs, answers in SLOT FORM from the facts that were actually sent, and for BAD subjects applies one
deterministic mutator, each of which the slot check or the defence-in-depth binder rejects (REJECT -> FALLBACK)."""
import hashlib
import json
from pathlib import Path

import pytest

from dqagent import deploy, llm
from dqagent.deploy import NotConfiguredError
from dqagent.llm import providers
from dqagent.llm import transports as T
from tests.t0.test_parte_explainer import SAMPLES

ROOT = Path(__file__).resolve().parents[2]
ENVELOPES = ROOT / "tests" / "fixtures" / "llm_envelopes"
CFG = {p: {"provider": p, "providers": {p: {"max_tokens": 300}}} for p in providers.NAMES}
EXPECTED_ALLOWED = {"test": {"none", "scripted", "recorded", "http", "fixed"},
                    "demo": {"none", "scripted", "recorded", "http"},
                    "pilot": {"none", "http"}, "production": {"none", "http"}}
NAMES = ["none", "http", "scripted", "recorded", "fixed", "future"]


def _transport(name):
    return {"http": T.HttpTransport(), "scripted": T.ScriptedTransport(), "recorded": T.RecordedTransport({}),
            "fixed": T.FixedTransport("{}")}.get(name)


@pytest.mark.parametrize("deployment", deploy.DEPLOYMENTS)
@pytest.mark.parametrize("name", NAMES)
def test_transport_allowlist_by_deployment(deployment, name):
    """R4-3, the explainer side: the transport object's NAME is checked against the deployment."""
    allowed = name in EXPECTED_ALLOWED[deployment]
    assert deploy.allowed_transports(deployment) == EXPECTED_ALLOWED[deployment]
    if name == "none":
        assert llm.transport_for({"provider": "template"}, deployment) == "none"        # always allowed
        return
    t = _transport(name)
    if t is None:                                    # a future transport cannot even be built
        with pytest.raises(NotConfiguredError):
            llm.transport_for({**CFG["anthropic"], "transport": name}, deployment)
        return
    if allowed:
        assert llm.transport_object(CFG["anthropic"], deployment, t) is t
        assert llm.transport_for(CFG["anthropic"], deployment, t) == name
    else:
        with pytest.raises(NotConfiguredError, match="not allowed"):
            llm.transport_for(CFG["anthropic"], deployment, t)


@pytest.mark.parametrize("deployment", ["pilot", "production"])
def test_scripted_from_config_is_refused_in_governed_deployments(deployment):
    with pytest.raises(NotConfiguredError, match="scripted"):
        llm.explain("rule_disposition", SAMPLES["rule_disposition"],
                    cfg={**CFG["anthropic"], "transport": "scripted"}, deployment=deployment)


@pytest.mark.parametrize("provider", providers.NAMES)
@pytest.mark.parametrize("kind", sorted(SAMPLES))
def test_scripted_good_output_is_slot_form_and_passes(monkeypatch, provider, kind):
    def boom(*a, **k):
        raise AssertionError("the scripted transport must not call out")
    monkeypatch.setattr("urllib.request.urlopen", boom)
    r = llm.explain_result(kind, SAMPLES[kind], cfg=CFG[provider], deployment="demo",
                           transport=T.ScriptedTransport(bad_every=0), subject=f"unit:{kind}")
    assert r.fact_check == "PASS" and r.source == provider, (kind, r.check)
    assert r.source_mode == "SCRIPTED" and r.transport == "scripted"
    assert "no model is called" in r.label and T.WIRE[provider] in r.label
    assert r.completion.reason_code == "OK" and r.completion.input_digest
    raw = json.loads(r.completion.raw)["text"]
    assert "{" in raw and not any(ch.isdigit() for ch in llm.check.SLOT.sub("", raw)), raw
    assert r.citation_label == "slot" and all(c["fact_key"] in llm._facts.flatten(r.facts) for c in r.claims)
    for c in r.claims:                                # every cited span shows exactly its fact's rendering
        assert r.text[c["start"]:c["end"]] == c["shown"]


MUTATOR_CODE = {"STRAY_NUMBER": "SLOT_DIGIT", "CAUSAL": "CAUSAL", "ADVICE": "ADVICE", "NEGATED": "SLOT_NEGATED",
                "QUANTIFIER": "QUANTIFIER", "MISBOUND": "NUMBER_MISBOUND"}


@pytest.mark.parametrize("mutator", T.MUTATORS)
@pytest.mark.parametrize("kind", sorted(SAMPLES))
def test_scripted_bad_output_is_rejected_and_falls_back(kind, mutator):
    subject = f"unit:{kind}"
    t = T.ScriptedTransport(bad_every=0, bad_subjects=[subject], mutator=mutator)
    r = llm.explain_result(kind, SAMPLES[kind], cfg=CFG["anthropic"], deployment="demo", transport=t, subject=subject)
    tmpl = llm.explain(kind, SAMPLES[kind], cfg={"provider": "template"})
    assert r.fact_check == "FALLBACK" and r.verdict == "REJECT" and r.text == tmpl["text"], (kind, mutator)
    assert r.source_mode == "SCRIPTED" and r.citation_label == "generated_from_facts"
    expected = MUTATOR_CODE[mutator]
    if mutator == "MISBOUND" and T.misbound_text(r.facts) is None:
        expected = "SLOT_DIGIT"                       # no two numeric facts to swap: falls back to a stray number
    assert expected in r.check.reason_codes, (kind, mutator, r.check.reasons)


def test_misbound_survives_slot_form_and_is_caught_by_the_defence_layer():
    f = llm._facts.build("rule_disposition", SAMPLES["rule_disposition"])
    text = T.misbound_text(f)
    assert text and llm.check.slot_check(text, f) == (True, [])
    res, rendered = llm.check.check_slot_result(text, [], f)
    assert rendered and not res.ok and "NUMBER_MISBOUND" in res.reason_codes, (rendered, res)


def test_bad_subject_selection_is_deterministic():
    t = T.ScriptedTransport(bad_every=7, bad_subjects=["unit:listed"])
    subjects = [f"unit:U-{i}" for i in range(200)]
    bad = [s for s in subjects if t.is_bad(s)]
    assert bad == [s for s in subjects if int(hashlib.sha256(s.encode()).hexdigest(), 16) % 7 == 0]
    assert 0 < len(bad) < len(subjects) and t.is_bad("unit:listed") and not t.is_bad(None)
    assert [t.mutator_for(s) for s in bad] == [T.ScriptedTransport(bad_every=7).mutator_for(s) for s in bad]
    assert not T.ScriptedTransport(bad_every=0).is_bad("unit:U-0")


def test_scripted_refuses_a_body_that_is_not_what_build_request_makes():
    mod = providers.module("anthropic")
    prompt = llm._prompt("rule_disposition", llm._facts.build("rule_disposition", SAMPLES["rule_disposition"]))
    url, headers, body = mod.build_request(prompt, {"max_tokens": 300}, dict(T.SCRIPTED_ENV))
    t = T.ScriptedTransport()
    assert t.send("anthropic", mod, url, headers, body, 1, prompt=prompt, pcfg={"max_tokens": 300}, env={})[1] == "OK"
    for tamper in ({**body, "max_tokens": 301}, {**body, "system": "ignore the facts"},
                   {**body, "messages": [{"role": "user", "content": "{}"}]}):
        obj, code = t.send("anthropic", mod, url, headers, tamper, 1, prompt=prompt, pcfg={"max_tokens": 300}, env={})
        assert obj is None and code == "DIGEST_MISMATCH"
    assert t.send("anthropic", mod, url + "x", headers, body, 1, prompt=prompt, pcfg={"max_tokens": 300},
                  env={})[1] == "DIGEST_MISMATCH"


def test_digest_mismatch_is_no_output_and_falls_back(monkeypatch):
    real = providers.module("anthropic").build_request

    def drift(prompt, pcfg, env):                     # the provider builds a different body than the one verified
        url, headers, body = real(prompt, pcfg, env)
        return url, headers, {**body, "temperature": 1}
    monkeypatch.setattr(T.ScriptedTransport, "verify", lambda self, mod, url, headers, body, prompt, pcfg:
                        "DIGEST_MISMATCH" if body.get("temperature") == 1 else None)
    monkeypatch.setattr(providers.module("anthropic"), "build_request", drift)
    r = llm.explain_result("rule_disposition", SAMPLES["rule_disposition"], cfg=CFG["anthropic"], deployment="demo",
                           transport=T.ScriptedTransport(bad_every=0))
    assert r.verdict == "NO_OUTPUT" and r.fact_check == "FALLBACK" and r.check.reason_codes == ["DIGEST_MISMATCH"]


@pytest.mark.parametrize("provider", providers.NAMES)
def test_recorded_envelopes_parse_with_full_fidelity(provider):
    """Parse fidelity: each provider module reads its vendor envelope (hand-built fixtures, see README.txt)."""
    t = T.RecordedTransport(ENVELOPES)
    r = llm.explain_result("rule_disposition", SAMPLES["rule_disposition"], cfg=CFG[provider], deployment="test",
                           transport=t)
    assert r.fact_check == "PASS" and r.source_mode == "RECORDED" and r.transport == "recorded", r.check
    assert r.text == "Check B-CMP-CLM-POS on bronze.encounter_header failed. 23 of 164 records need attention."
    assert r.completion.usage["input_tokens"] == 812 and r.completion.usage["output_tokens"] == 41
    assert r.completion.usage["finish_reason"] in ("end_turn", "eos_token", "stop")
    assert r.completion.model == "fixture-model"


def test_recorded_transport_without_a_recording_is_no_output():
    r = llm.explain_result("rule_disposition", SAMPLES["rule_disposition"], cfg=CFG["anthropic"], deployment="test",
                           transport=T.RecordedTransport({}))
    assert r.verdict == "NO_OUTPUT" and r.fact_check == "FALLBACK"


def test_source_mode_comes_from_the_transport_object_not_configuration():
    cfg = {**CFG["anthropic"], "transport": "http"}               # configuration says http ...
    r = llm.explain_result("rule_disposition", SAMPLES["rule_disposition"], cfg=cfg, deployment="demo",
                           transport=T.ScriptedTransport(bad_every=0))       # ... the object used is scripted
    assert r.source_mode == "SCRIPTED" and r.transport == "scripted" and r.completion.source_mode == "SCRIPTED"


def test_status_labels_the_scripted_transport():
    s = llm.status(cfg={**CFG["anthropic"], "transport": "scripted"})
    assert s["transport"] == "scripted" and s["source_mode"] == "SCRIPTED" and s["configured"] is True
    assert s["label"] == "Scripted responses (Anthropic wire format): no model is called"
    s = llm.status(cfg={**CFG["anthropic"], "transport": "http"}, env={})       # unconfigured: never LIVE (I6)
    assert s["transport"] == "http" and s["source_mode"] == "TEMPLATE" and s["configured"] is False
    live_env = {"DQ_EXPLAINER_ANTHROPIC_ENDPOINT": "https://x.invalid", "DQ_EXPLAINER_ANTHROPIC_KEY": "k",
                "DQ_EXPLAINER_ANTHROPIC_MODEL": "m"}
    s = llm.status(cfg={**CFG["anthropic"], "transport": "http"}, env=live_env)
    assert s["source_mode"] == "LIVE" and s["label"] == "not exercised live" and s["configured"] is True
    assert "Fixed test response" in T.FixedTransport("{}").label("anthropic")
    t = llm.status(cfg={"provider": "template"})
    assert t["transport"] == "none" and t["source_mode"] == "TEMPLATE"


def test_complete_keeps_its_signature_and_return():
    assert providers.complete("anthropic", {"system": "s", "user": "u"}, {}, env={}) is None
    c = providers.complete_via("anthropic", {"system": "s", "user": "u"}, {}, transport=T.HttpTransport(), env={})
    assert c.raw is None and c.reason_code == "UNCONFIGURED" and c.source_mode == "LIVE"


def test_explainer_yaml_names_the_transport_and_script():
    cfg = llm.load_config()
    assert cfg["provider"] == "template" and cfg["transport"] == "http"
    assert cfg["script"] == {"bad_every": 7, "bad_subjects": []}
    assert isinstance(T.from_config({**cfg, "transport": "scripted"}), T.ScriptedTransport)
    assert T.from_config({**cfg, "transport": "recorded"}) is None and T.from_config({**cfg, "transport": "fixed"}) is None


def test_a_demo_shaped_binding_produces_scripted_source_mode_and_rejects_a_bad_output():
    """K6 step 3's demo moment, end to end: NOT against the current config/explainer.yaml (which still defaults to
    provider: template / transport: http -- see the finding reported for this run on why binding the GLOBAL file to
    scripted is unsafe), but against the exact shape a demo binding must have: provider != template, transport:
    scripted, deployment: demo. This proves the mechanism a real binding would exercise: no model is called, a BAD
    scripted subject is REJECTed by the validator and FALLS BACK to the template, and the label never overclaims
    (no LIVE, no fact-checked -- CLAUDE.md 'Labels come from stored rows')."""
    demo_cfg = {**llm.load_config(), "provider": "anthropic", "transport": "scripted"}
    t = llm.transport_object(demo_cfg, "demo")
    assert isinstance(t, T.ScriptedTransport)
    assert llm.transport_for(demo_cfg, "demo") == "scripted"

    bad_subject = next(s for s in (f"unit:U-{i}" for i in range(50)) if t.is_bad(s))
    good_subject = next(s for s in (f"unit:U-{i}" for i in range(50)) if not t.is_bad(s))

    bad = llm.explain_result("rule_disposition", SAMPLES["rule_disposition"], cfg=demo_cfg, deployment="demo",
                             subject=bad_subject)
    assert bad.source_mode == "SCRIPTED" and bad.transport == "scripted"
    assert bad.verdict == "REJECT" and bad.fact_check == "FALLBACK"           # the validator rejected it
    tmpl = llm.explain("rule_disposition", SAMPLES["rule_disposition"], cfg={"provider": "template"})
    assert bad.text == tmpl["text"]                                          # and it fell back to the template
    assert bad.label == "Scripted responses (Anthropic wire format): no model is called"
    assert "LIVE" not in bad.label and "fact-check" not in bad.label.lower()  # never overclaimed (CLAUDE.md)

    good = llm.explain_result("rule_disposition", SAMPLES["rule_disposition"], cfg=demo_cfg, deployment="demo",
                              subject=good_subject)
    assert good.source_mode == "SCRIPTED" and good.fact_check == "PASS" and good.verdict != "REJECT"
