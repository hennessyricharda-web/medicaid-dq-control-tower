"""Thin LLM provider clients (Part E §7). Not exercised live: each is contract-tested against a local stub server.

Every provider module exposes:
    NAME
    ENV            default environment variable names (overridable in config/explainer.yaml)
    configured(pcfg, env) -> bool
    build_request(prompt, pcfg, env) -> (url, headers, body)
    parse_response(obj) -> str          the model's raw text (expected to be the JSON {text, claims})
complete_via(name, prompt, pcfg, *, transport, ...) -> Completion runs one call through a transport
(dqagent.llm.transports); complete() is the HTTP-only wrapper kept for its callers. Neither ever raises.
"""
from __future__ import annotations

import importlib
import os
import time
from typing import NamedTuple


NAMES = ("anthropic", "watsonx", "azure_openai", "cortex")
LABEL = "not exercised live"


def module(name: str):
    if name not in NAMES:
        raise ValueError(f"unknown explainer provider {name!r}; known: {', '.join(NAMES)}")
    return importlib.import_module(f"dqagent.llm.providers.{name}")


def _env(mod, pcfg: dict, env) -> dict:
    env = os.environ if env is None else env
    names = {**mod.ENV, **(pcfg.get("env") or {})}
    return {k: env.get(v) for k, v in names.items()}


def configured(name: str, pcfg: dict | None = None, env=None) -> bool:
    try:
        mod = module(name)
        return bool(mod.configured(pcfg or {}, _env(mod, pcfg or {}, env)))
    except Exception:  # noqa: BLE001 - a status probe never raises
        return False


class Completion(NamedTuple):
    """One provider call through one transport (Part F P1-1 §C). `source_mode` is the transport object's own."""
    raw: str | None
    reason_code: str            # OK | UNCONFIGURED | NO_OUTPUT | MALFORMED | DIGEST_MISMATCH | ERROR
    source_mode: str            # LIVE | SCRIPTED | RECORDED
    model: str | None
    usage: dict                 # {input_tokens, output_tokens, finish_reason}
    latency_ms: int
    transport: str              # the transport's NAME (R4-3 allow-list name)
    input_digest: str | None    # sha256 of the request body actually built


def complete_via(name: str, prompt: dict, pcfg: dict | None = None, *, transport, timeout_s: float = 8.0, env=None,
                 subject: str | None = None) -> Completion:
    """The provider's raw text for `prompt` through `transport`. Never raises. A transport that calls out needs the
    provider's credentials from the environment; one that does not (scripted, recorded, fixed) uses visible
    placeholder connection values (transports.SCRIPTED_ENV) and no secret."""
    from dqagent.llm.transports import SCRIPTED_ENV, body_digest, usage_of
    t0 = time.monotonic()
    sm, tn = getattr(transport, "SOURCE_MODE", "?"), getattr(transport, "NAME", "?")

    def done(raw, code, model=None, usage=None, digest=None):
        return Completion(raw, code, sm, model, usage or {}, int((time.monotonic() - t0) * 1000), tn, digest)
    try:
        pcfg = pcfg or {}
        mod = module(name)
        if transport.CALLS_OUT:
            e = _env(mod, pcfg, env)
            if not mod.configured(pcfg, e):
                return done(None, "UNCONFIGURED")
        else:
            e = dict(SCRIPTED_ENV)
        url, headers, body = mod.build_request(prompt, pcfg, e)
        digest = body_digest(body)
        obj, code = transport.send(name, mod, url, headers, body, timeout_s, prompt=prompt, pcfg=pcfg, env=e,
                                   subject=subject)
        if obj is None:
            return done(None, code if code != "OK" else "NO_OUTPUT", digest=digest)
        it, ot, fin, model = usage_of(obj)
        out = mod.parse_response(obj)
        usage = {"input_tokens": it, "output_tokens": ot, "finish_reason": fin}
        if not isinstance(out, str):
            return done(None, "MALFORMED", model, usage, digest)
        return done(out, "OK", model, usage, digest)
    except Exception:  # noqa: BLE001 - contract: a provider failure never reaches the caller
        return done(None, "ERROR")


def complete(name: str, prompt: dict, pcfg: dict | None = None, *, timeout_s: float = 8.0, env=None) -> str | None:
    """The provider's raw text for `prompt` ({system, user}) over HTTP, or None (unconfigured, HTTP error, timeout,
    malformed response). Never raises. A thin wrapper over complete_via(..., transport=HttpTransport())."""
    from dqagent.llm.transports import HttpTransport
    return complete_via(name, prompt, pcfg, transport=HttpTransport(), timeout_s=timeout_s, env=env).raw
