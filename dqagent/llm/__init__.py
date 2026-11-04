"""Explainer (Part E §7): plain-language explanations for a non-technical State reviewer.

    explain(kind, facts, *, cfg=None, classification=None, env=None, deployment=None)
        -> {text, source, fact_check: "TEMPLATE" | "PASS" | "FALLBACK", facts_hash, text_hash}

The facts are sanitised first (facts.build: masked classes and Part 2 units withheld, small cells suppressed).
provider "template" (default) renders the deterministic template: source "template", fact_check TEMPLATE. The
template is generated only from the facts and is NOT fact-checked, so it is never labelled PASS (red team E2).
Any other provider (it needs an explicit `deployment`, R4-3) is asked for JSON {text, claims} in SLOT FORM: the
text names facts as {fact_key} slots and writes no number, quantity, identifier or disposition word itself
(check.slot_check, on the raw text). The slots are then filled from the facts (check.render_slots) and the rendered
text is checked again by the structured check with each slot as a claim (defence in depth). Only when both pass is
the rendered text used (source = provider name, fact_check PASS). On any failure (not configured, timeout, HTTP error, malformed output, check failure) the template text is
returned with source "template" and fact_check FALLBACK.
The LLM is never in the decision path: callers bind facts_hash / text_hash to the decision that showed it.
"""
from __future__ import annotations

import hashlib
import json
import threading
from pathlib import Path

import yaml

from dataclasses import dataclass, field

from dqagent import deploy as _deploy
from dqagent.llm import facts as _facts
from dqagent.llm import providers as _providers
from dqagent.llm import transports as _transports
from dqagent.llm.check import CHECK_VERSION, CheckResult, check_slot_result, parse_provider_output
from dqagent.llm.check import check as fact_check
from dqagent.llm.template import CITATION_LABEL, KINDS, TEMPLATE_VERSION, render_with_claims

ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = ROOT / "config" / "explainer.yaml"
DEFAULT_CONFIG = {"provider": "template", "timeout_s": 8, "providers": {}}

SYSTEM_PROMPT = (
    "You explain data-quality events to a non-technical State reviewer in plain language: what happened, what it "
    "means, and what the reviewer can do. Use ONLY the facts given, and write them ONLY as slots: {fact_key} "
    "(a dotted fact key from the facts), or {fact_key:SUBJECT} where SUBJECT is an identifier that appears in the "
    "facts. Slots are filled from the facts after you reply. Never write a digit, a number in words, a quantity "
    "word (all, none, every, half, twice, most, few, several, ...), an identifier, or a status or outcome word "
    "(passed, failed, held, released, approved, pending, ...) yourself: use the slot, e.g. {failed}, {held_units}, "
    "{rule_id}, {disposition}. Do not negate a slot. Do not say why something happened unless a 'hypothesis' fact "
    "is given. Never advise releasing or approving. Reply with JSON only: "
    '{"text": "<at most 4 sentences, in slot form>", "claims": []}.'
)
PROMPT_VERSION = "p-" + hashlib.sha256(SYSTEM_PROMPT.encode()).hexdigest()[:12]
VERSIONS = {"prompt_version": PROMPT_VERSION, "check_version": CHECK_VERSION, "template_version": TEMPLATE_VERSION}
SLOT_CITATION_LABEL = "slot"          # a provider's slot: the citation is the fact the slot names

FACT_CHECK_TEMPLATE = "TEMPLATE"     # deterministic, generated only from facts; never claims to be fact-checked
FACT_CHECK_VALUES = ("TEMPLATE", "PASS", "FALLBACK")

_lock = threading.Lock()
_stats = {"calls": 0, "fallback_count": 0, "last_fallback_reasons": []}


def load_config(path: str | Path | None = None) -> dict:
    p = Path(path) if path else CONFIG_PATH
    cfg = dict(DEFAULT_CONFIG)
    if p.exists():
        cfg.update(yaml.safe_load(p.read_text()) or {})
    return cfg


def _sha(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def _prompt(kind: str, f: dict) -> dict:
    return {"system": SYSTEM_PROMPT,
            "user": json.dumps({"kind": kind, "facts": f}, sort_keys=True, ensure_ascii=True)}


def _record_fallback(reasons: list[str]):
    with _lock:
        _stats["fallback_count"] += 1
        _stats["last_fallback_reasons"] = list(reasons)[:5]


def provider_config_digest(cfg: dict) -> str:
    """What decides a provider's output, minus secrets (none are in cfg): provider, transport, its pcfg, script."""
    provider = (cfg.get("provider") or "template").lower()
    keep = {"provider": provider, "transport": None if provider == "template" else cfg.get("transport") or "http",
            "pcfg": (cfg.get("providers") or {}).get(provider) or {},
            "script": cfg.get("script") if (cfg.get("transport") == "scripted") else None}
    return "pc-" + hashlib.sha256(_facts.canonical(keep).encode()).hexdigest()[:16]


def transport_for(cfg: dict, deployment: str | None = None, transport=None) -> str:
    """R4-3: the allow-list NAME of the transport this explainer will use (`none` for the template), checked exactly
    as explain() checks it. Raises NotConfiguredError on a refusal (callers such as /explainer show it)."""
    t = transport_object(cfg, deployment, transport)
    return "none" if t is None else t.NAME


def transport_object(cfg: dict, deployment: str | None = None, transport=None):
    """R4-3: the transport OBJECT this explainer will use, checked against the deployment's allow-list by its own
    NAME (never by a declaration). The template uses none (returns None, allowed in every deployment). Any other
    provider uses the injected `transport`, else the one config/explainer.yaml names (`http` or `scripted`); a
    configured name that cannot be built (none, recorded, fixed, unknown) is refused. Any provider that calls out
    needs an explicit `deployment` (no fallback to explainer.yaml or $DQ_DEPLOYMENT). A refusal raises
    NotConfiguredError: never caught, never a fallback."""
    provider = (cfg.get("provider") or "template").lower()
    if provider == "template":
        if deployment is not None:
            _deploy.require_transport("none", deployment)
        return None
    if provider not in _providers.NAMES:
        raise _deploy.NotConfiguredError(f"unknown explainer provider {provider!r}")
    t = transport if transport is not None else _transports.from_config(cfg)
    if t is None:
        raise _deploy.NotConfiguredError(f"LLM transport {cfg.get('transport')!r} cannot be built from configuration "
                                         f"for provider {provider!r} (http or scripted)")
    if deployment is None:
        raise _deploy.NotConfiguredError(f"provider {provider!r} needs an explicit deployment for the transport "
                                         "allow-list (R4-3)")
    _deploy.require_transport(t.NAME, deployment)
    return t


@dataclass
class ExplainResult:
    """Everything one render decided. explain() returns public(); ExplainService persists the rest."""
    kind: str
    text: str
    source: str
    fact_check: str
    facts_hash: str
    text_hash: str
    facts: dict
    provider: str
    transport: str                     # the allow-list NAME of the transport used (none for the template)
    source_mode: str                   # TEMPLATE | LIVE | SCRIPTED | RECORDED (of the transport object)
    label: str
    verdict: str                       # PASS | REJECT | NOT_CHECKED (template) | NO_OUTPUT
    check: CheckResult | None
    claims: list = field(default_factory=list)          # [{fact_key, value, start?, end?}] of the SHOWN text
    citation_label: str = CITATION_LABEL
    masking: dict = field(default_factory=dict)          # {masked, suppressed, part2_withheld}
    completion: object = None                            # providers.Completion
    raw_text: str | None = None                          # the model's untrusted text (never persisted as text)
    provider_config_digest: str = ""
    versions: dict = field(default_factory=lambda: dict(VERSIONS))

    def public(self) -> dict:
        """The explanation an object carries (Part E §7, plus source_mode since F-35: the source mode of the text
        SHOWN; a FALLBACK shows the template, so it reads TEMPLATE)."""
        return {"text": self.text, "source": self.source, "fact_check": self.fact_check,
                "facts_hash": self.facts_hash, "text_hash": self.text_hash,
                "source_mode": "TEMPLATE" if self.source == "template" else self.source_mode}


def explain_result(kind: str, facts: dict, *, cfg: dict | None = None, classification: dict | None = None,
                   env=None, deployment: str | None = None, transport=None,
                   subject: str | None = None) -> ExplainResult:
    if kind not in KINDS:
        raise ValueError(f"unknown explanation kind {kind!r}; known: {', '.join(KINDS)}")
    cfg = cfg if cfg is not None else load_config()
    t = transport_object(cfg, deployment, transport)     # R4-3 guard: before any facts are built or any call is made
    f, masking = _facts.build_result(kind, _facts.adapt(kind, facts),
                                     classification=classification or cfg.get("classification"))
    fh = _facts.facts_hash(f)
    tmpl, tmpl_claims = render_with_claims(kind, f)
    provider = (cfg.get("provider") or "template").lower()
    pcd = provider_config_digest(cfg)
    with _lock:
        _stats["calls"] += 1
    if t is None:
        return ExplainResult(kind, tmpl, "template", FACT_CHECK_TEMPLATE, fh, _sha(tmpl), f, "template", "none",
                             "TEMPLATE", "built-in template (offline)", "NOT_CHECKED", None, tmpl_claims,
                             masking=masking, provider_config_digest=pcd)
    comp = _providers.complete_via(provider, _prompt(kind, f), (cfg.get("providers") or {}).get(provider) or {},
                                   transport=t, timeout_s=float(cfg.get("timeout_s", 8)), env=env, subject=subject)
    label = t.label(provider)
    res, raw_text, rendered = None, comp.raw, None
    if comp.raw is None:
        verdict, reasons = "NO_OUTPUT", [f"provider gave no output ({comp.reason_code})"]
        res = CheckResult(False, reasons, [comp.reason_code], [], [])
    else:
        parsed = parse_provider_output(comp.raw)
        if parsed is None:
            reasons = ["provider output is not JSON {text, claims}"]
            res = CheckResult(False, reasons, ["MALFORMED_OUTPUT"], [], [])
        else:
            raw_text, claims = parsed
            res, rendered = check_slot_result(raw_text, claims, f)    # primary slot form, then the defence layer
        verdict = "PASS" if res.ok else "REJECT"
    if verdict == "PASS":
        flat = _facts.flatten(f)
        shown = [{**b, "value": flat[b["fact_key"]]} for b in res.bindings]
        return ExplainResult(kind, rendered, provider, "PASS", fh, _sha(rendered), f, provider, t.NAME,
                             t.SOURCE_MODE, label, verdict, res, shown, SLOT_CITATION_LABEL, masking, comp,
                             raw_text, pcd)
    _record_fallback(res.reasons)
    return ExplainResult(kind, tmpl, "template", "FALLBACK", fh, _sha(tmpl), f, provider, t.NAME, t.SOURCE_MODE,
                         label, verdict, res, tmpl_claims, CITATION_LABEL, masking, comp, raw_text, pcd)


def explain(kind: str, facts: dict, *, cfg: dict | None = None, classification: dict | None = None,
            env=None, deployment: str | None = None, transport=None, subject: str | None = None) -> dict:
    return explain_result(kind, facts, cfg=cfg, classification=classification, env=env, deployment=deployment,
                          transport=transport, subject=subject).public()


def status(cfg: dict | None = None, env=None) -> dict:
    """For GET /api/v1/explainer: {provider, transport, source_mode, configured, fallback_count, decisions, label}.
    The label names the transport (I8): a scripted transport says that no model is called."""
    cfg = cfg if cfg is not None else load_config()
    provider = (cfg.get("provider") or "template").lower()
    with _lock:
        fb = _stats["fallback_count"]
    if provider == "template":
        return {"provider": provider, "transport": "none", "source_mode": "TEMPLATE", "configured": True,
                "fallback_count": fb, "decisions": "deterministic", "label": "built-in template (offline)"}
    t = _transports.from_config(cfg) if provider in _providers.NAMES else None
    if t is None:
        return {"provider": provider, "transport": str(cfg.get("transport")), "source_mode": None,
                "configured": False, "fallback_count": fb, "decisions": "deterministic",
                "label": "not configured"}
    configured = (not t.CALLS_OUT) or _providers.configured(provider, (cfg.get("providers") or {}).get(provider) or {},
                                                           env)
    if not configured:                    # I6: nothing can be called, so what is shown is the template, never LIVE
        return {"provider": provider, "transport": t.NAME, "source_mode": "TEMPLATE", "configured": False,
                "fallback_count": fb, "decisions": "deterministic",
                "label": "not configured: the built-in template (offline) is shown"}
    return {"provider": provider, "transport": t.NAME, "source_mode": t.SOURCE_MODE, "configured": configured,
            "fallback_count": fb, "decisions": "deterministic", "label": t.label(provider)}


def reset_stats():
    with _lock:
        _stats.update(calls=0, fallback_count=0, last_fallback_reasons=[])


__all__ = ["FACT_CHECK_TEMPLATE", "FACT_CHECK_VALUES", "KINDS", "PROMPT_VERSION", "VERSIONS", "ExplainResult",
           "explain", "explain_result", "fact_check", "load_config", "provider_config_digest", "status",
           "transport_for", "transport_object"]
