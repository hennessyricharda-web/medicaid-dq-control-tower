"""LLM transports (Part F P1-1 §C; OBSERVABILITY adjudication C1/C2; R4-3).

A transport carries one provider request and returns the provider's vendor-shaped JSON envelope. The provider
module still builds the request (`build_request`) and parses the reply (`parse_response`), so both are exercised
on every transport. `source_mode` is a property of the transport OBJECT actually used, never of configuration.

    NAME          the allow-list name checked by deploy.require_transport (R4-3)
    SOURCE_MODE   LIVE | SCRIPTED | RECORDED (written to llm_calls; the Tower shows it beside the provider)
    CALLS_OUT     True only for HttpTransport: it needs the provider's credentials from the environment
    send(...)     -> (envelope | None, reason_code)

HttpTransport      the real POST (stdlib urllib, hard timeout). Allowed everywhere `http` is.
ScriptedTransport  no model is called. It re-derives the request with the provider's own build_request and refuses
                   a body whose sha256 differs (DIGEST_MISMATCH), then scripts a reply in SLOT FORM from the facts
                   that were actually sent. A subject is BAD when sha256(subject) mod bad_every == 0 or when it is
                   listed in bad_subjects; a BAD reply carries one deterministic mutator (MUTATORS), each of which
                   breaks slot form or the defence-in-depth binder, so REJECT -> FALLBACK is visible on demo cards.
                   test and demo only.
RecordedTransport  replays stored vendor envelopes (parse-fidelity tests). test and demo only.
FixedTransport     a test helper: one fixed model text wrapped in the vendor envelope. test only (N-T0-4).
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from dqagent.deploy import seam
from dqagent.llm.providers._http import post_json

# placeholder connection values for transports that never call out: no secret, and visibly not a real endpoint
SCRIPTED_ENV = {"endpoint": "scripted://no-model-is-called", "key": "", "model": "scripted", "project": "scripted",
                "deployment": "scripted"}
WIRE = {"anthropic": "Anthropic", "watsonx": "watsonx.ai", "azure_openai": "Azure OpenAI",
        "cortex": "Snowflake Cortex"}
MUTATORS = ("STRAY_NUMBER", "CAUSAL", "ADVICE", "NEGATED", "MISBOUND", "QUANTIFIER")


def body_digest(body) -> str:
    return hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=True).encode()).hexdigest()


def envelope(provider: str, text: str, *, model: str = "scripted", input_tokens: int = 0,
             output_tokens: int = 0) -> dict:
    """The vendor-shaped reply each provider module's parse_response reads."""
    if provider == "anthropic":
        return {"type": "message", "model": model, "content": [{"type": "text", "text": text}],
                "stop_reason": "end_turn", "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens}}
    if provider == "watsonx":
        return {"model_id": model, "results": [{"generated_text": text, "stop_reason": "eos_token",
                                                "input_token_count": input_tokens,
                                                "generated_token_count": output_tokens}]}
    return {"model": model, "choices": [{"index": 0, "finish_reason": "stop",
                                         "message": {"role": "assistant", "content": text}}],
            "usage": {"prompt_tokens": input_tokens, "completion_tokens": output_tokens}}


def usage_of(obj) -> tuple[int | None, int | None, str | None, str | None]:
    """(input_tokens, output_tokens, finish_reason, model) from any of the vendor envelopes."""
    if not isinstance(obj, dict):
        return None, None, None, None
    u = obj.get("usage") or {}
    it = u.get("input_tokens", u.get("prompt_tokens"))
    ot = u.get("output_tokens", u.get("completion_tokens"))
    fin = obj.get("stop_reason")
    res = obj.get("results")
    if isinstance(res, list) and res and isinstance(res[0], dict):
        it = res[0].get("input_token_count", it)
        ot = res[0].get("generated_token_count", ot)
        fin = res[0].get("stop_reason", fin)
    ch = obj.get("choices")
    if isinstance(ch, list) and ch and isinstance(ch[0], dict):
        fin = ch[0].get("finish_reason", fin)
    model = obj.get("model") or obj.get("model_id")
    return (it if isinstance(it, int) else None, ot if isinstance(ot, int) else None,
            fin if isinstance(fin, str) else None, model if isinstance(model, str) else None)


class Transport:
    NAME = "?"
    SOURCE_MODE = "?"
    CALLS_OUT = False

    def label(self, provider: str) -> str:
        return f"{WIRE.get(provider, provider)} ({self.SOURCE_MODE.lower()})"

    def send(self, provider: str, mod, url: str, headers: dict, body: dict, timeout_s: float, *, prompt: dict,
             pcfg: dict, env: dict, subject: str | None = None) -> tuple[dict | None, str]:
        raise NotImplementedError


@seam("llm.transport")
class HttpTransport(Transport):
    NAME = "http"
    SOURCE_MODE = "LIVE"
    CALLS_OUT = True

    def label(self, provider: str) -> str:
        return "not exercised live"

    def send(self, provider, mod, url, headers, body, timeout_s, *, prompt, pcfg, env, subject=None):
        obj = post_json(url, headers, body, timeout_s)
        return (obj, "OK") if obj is not None else (None, "NO_OUTPUT")


class _NoCallTransport(Transport):
    """Shared by the transports that never call out: the request must be exactly what build_request makes."""

    def verify(self, mod, url, headers, body, prompt, pcfg) -> str | None:
        e_url, e_headers, e_body = mod.build_request(prompt, pcfg, dict(SCRIPTED_ENV))
        if body_digest(body) != body_digest(e_body) or url != e_url:
            return "DIGEST_MISMATCH"
        if prompt["user"] not in json.dumps(body, ensure_ascii=True) and \
                json.dumps(prompt["user"], ensure_ascii=True)[1:-1] not in json.dumps(body, ensure_ascii=True):
            return "DIGEST_MISMATCH"                                    # the facts sent are not in the body
        return None


class ScriptedTransport(_NoCallTransport):
    NAME = "scripted"
    SOURCE_MODE = "SCRIPTED"

    def __init__(self, bad_every: int = 7, bad_subjects=(), mutator: str | None = None):
        self.bad_every = int(bad_every) if bad_every else 0
        self.bad_subjects = {s if isinstance(s, str) else str(s) for s in (bad_subjects or ())}
        if mutator is not None and mutator not in MUTATORS:
            raise ValueError(f"unknown mutator {mutator!r}; one of {', '.join(MUTATORS)}")
        self.mutator = mutator

    def label(self, provider: str) -> str:
        return f"Scripted responses ({WIRE.get(provider, provider)} wire format): no model is called"

    def is_bad(self, subject: str | None) -> bool:
        if subject is None:
            return False
        if subject in self.bad_subjects:
            return True
        return bool(self.bad_every) and int(hashlib.sha256(subject.encode()).hexdigest(), 16) % self.bad_every == 0

    def mutator_for(self, subject: str) -> str:
        if self.mutator:
            return self.mutator
        return MUTATORS[int(hashlib.sha256(("m|" + subject).encode()).hexdigest(), 16) % len(MUTATORS)]

    def send(self, provider, mod, url, headers, body, timeout_s, *, prompt, pcfg, env, subject=None):
        bad = self.verify(mod, url, headers, body, prompt, pcfg)
        if bad:
            return None, bad
        sent = json.loads(prompt["user"])
        kind, facts = sent.get("kind"), sent.get("facts") or {}
        text = good_slot_text(kind, facts)
        if self.is_bad(subject):
            text = mutate(text, self.mutator_for(subject), facts)
        reply = json.dumps({"text": text, "claims": []})
        return envelope(provider, reply, output_tokens=0), "OK"


class RecordedTransport(_NoCallTransport):
    """Replays stored vendor envelopes: {provider: envelope} or a directory of <provider>.json files."""
    NAME = "recorded"
    SOURCE_MODE = "RECORDED"

    def __init__(self, recordings: dict | str | Path):
        if isinstance(recordings, (str, Path)):
            recordings = {p.stem: json.loads(p.read_text()) for p in sorted(Path(recordings).glob("*.json"))}
        self.recordings = dict(recordings)

    def send(self, provider, mod, url, headers, body, timeout_s, *, prompt, pcfg, env, subject=None):
        bad = self.verify(mod, url, headers, body, prompt, pcfg)
        if bad:
            return None, bad
        obj = self.recordings.get(provider)
        return (obj, "OK") if isinstance(obj, dict) else (None, "NO_OUTPUT")


class FixedTransport(_NoCallTransport):
    """Test helper: the model text is fixed; it is wrapped in the provider's own envelope."""
    NAME = "fixed"
    SOURCE_MODE = "RECORDED"          # the llm_calls source_mode enum has no FIXED; the transport column says "fixed"

    def __init__(self, text: str):
        self.text = text

    def label(self, provider: str) -> str:
        return f"Fixed test response ({WIRE.get(provider, provider)} wire format): no model is called"

    def send(self, provider, mod, url, headers, body, timeout_s, *, prompt, pcfg, env, subject=None):
        bad = self.verify(mod, url, headers, body, prompt, pcfg)
        if bad:
            return None, bad
        return envelope(provider, self.text), "OK"


def from_config(cfg: dict) -> Transport | None:
    """The transport named by config/explainer.yaml (`transport: http | scripted`). Recorded and fixed transports
    are never built from configuration: tests construct them."""
    name = str(cfg.get("transport") or "http").lower()
    if name == "http":
        return HttpTransport()
    if name == "scripted":
        s = cfg.get("script") or {}
        return ScriptedTransport(bad_every=s.get("bad_every", 7), bad_subjects=s.get("bad_subjects") or ())
    return None


# ------------------------------------------------------------------------------------------------ scripted text
# One or two plain-language sentences per kind, in slot form. A sentence is used only when every slot it names is
# a fact; each passes slot_check and the defence-in-depth binder (tests/t1/test_scripted_transport.py).
GOOD_SENTENCES: dict[str, list[str]] = {
    "unit_card": ["Unit {unit_key} in {relation} is {state}.",
                  "{rules_not_passed} of {rules_checked} rules need attention.",
                  "It is {state}."],
    "rule_disposition": ["Check {rule_id} on {relation} {disposition}.",
                         "{failed} of {evaluated} records need attention.",
                         "Check {rule_id} {disposition}."],
    "decision": ["Decision {decision_id} on unit {unit_key} is recorded.",
                 "Decision {decision_id} is recorded."],
    "change_request": ["Change request {cr_id} is {status}.",
                       "It has {approvals_recorded} of {approvals_required} approvals."],
    "lookback": ["Look-back {lookback_id} for unit {unit_key} is open for review.",
                 "Look-back {lookback_id} is open for review."],
    "monitor_signal": ["The monitor for {relation} in {period} reports a reading for a person to look at.",
                       "The monitor for {relation} reports a reading for a person to look at."],
    "parallel_break": ["The comparison of {measure} for {relation} in {period} shows {status}.",
                       "The comparison of {measure} for {relation} shows {status}."],
    "feed_item": ["Feed {feed} has item {item_id} for a person to look at.",
                  "Feed {feed} has an item for a person to look at."],
    "demotion": ["The autonomy of agent {agent} moves to tier {to_tier}.",
                 "The autonomy of agent {agent} has changed."],
    "autonomy": ["Agent {agent} works at tier {tier} for {authority}.",
                 "Agent {agent} works at tier {tier}."],
    "cde": ["Completeness for {element} is {completeness_rate_pct}%, under the minimum of {completeness_min_pct}%.",
            "The critical data element {element} is reported for review."],
    "nightly_run": ["Simulated night {night_date}: {created_units} units were created.",
                    "Simulated night: {held_units} units are waiting for review.",
                    "Simulated night {night_date} has a summary for review."],
}
_SLOT_KEY = re.compile(r"\{([A-Za-z_][A-Za-z0-9_.]*)")


def _flat(facts: dict) -> dict:
    from dqagent.llm.facts import flatten
    return flatten(facts)


def good_slot_text(kind: str | None, facts: dict) -> str:
    """Slot-form text for `kind` from the sentences whose slots are all facts (suppressed '<11' counts are skipped,
    so the scripted text never prints a small-cell marker as a number)."""
    flat = _flat(facts)
    used: list[str] = []
    for s in GOOD_SENTENCES.get(kind or "", []):
        keys = _SLOT_KEY.findall(s)
        if all(k in flat and flat[k] not in (None, "", "<11") for k in keys):
            same = [u for u in used if set(_SLOT_KEY.findall(u)) & set(keys)]
            if not same:
                used.append(s)
    if not used:
        return "Simulated details are on the record for review." if kind == "nightly_run" else \
            "The details are on the record for review."
    return " ".join(used[:2])


def mutate(text: str, mutator: str, facts: dict) -> str:
    """One deterministic BAD mutation. Each breaks slot form (STRAY_NUMBER, CAUSAL, ADVICE, NEGATED, QUANTIFIER)
    or survives slot form and is caught by the binder over the rendered text (MISBOUND)."""
    if mutator == "STRAY_NUMBER":
        return text + " About 7 more records may be affected."
    if mutator == "CAUSAL":
        return text + " This happened because the sender changed the file layout."
    if mutator == "ADVICE":
        return text + " It is safe to release them."
    if mutator == "NEGATED":
        slot = re.search(r"\{[^}]+\}", text)
        return f"It is not true that {slot.group(0) if slot else 'this'} is on the record. " + text
    if mutator == "QUANTIFIER":
        return text + " All of them need attention."
    if mutator == "MISBOUND":
        m = misbound_text(facts)
        return m if m else text + " About 7 more records may be affected."
    raise ValueError(mutator)


_PLAIN_PART = re.compile(r"^[a-z]{4,}$")


def misbound_text(facts: dict) -> str | None:
    """Slot-valid text whose prose names one numeric fact beside the slot of another with a different value:
    "The evaluated count is {failed}." renders the failed count next to the word "evaluated"."""
    from dqagent.llm.check import DISPOSITION_WORDS, NEGATIONS, QUANTIFIERS, STATUS_WORDS, _GENERIC_KEY_PARTS
    flat = _flat(facts)
    nums = {k: v for k, v in flat.items() if isinstance(v, (int, float)) and not isinstance(v, bool)}
    stop = set(DISPOSITION_WORDS) | STATUS_WORDS | QUANTIFIERS | NEGATIONS | _GENERIC_KEY_PARTS

    def word(k):
        parts = [p for p in re.split(r"[._]", k.lower()) if p]
        ok = [p for p in parts if _PLAIN_PART.match(p) and p not in stop]
        return ok[0] if ok and len(ok) == len([p for p in parts if p not in _GENERIC_KEY_PARTS]) else None

    for a in sorted(nums):
        for b in sorted(nums):
            if a == b or nums[a] == nums[b]:
                continue
            w = word(b)
            if w and not (set(re.split(r"[._]", a.lower())) & {w}):
                return f"The {w} figure is {{{a}}}."
    return None
