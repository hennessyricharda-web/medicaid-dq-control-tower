"""Anthropic Messages API (POST {endpoint}, x-api-key). Not exercised live."""
from __future__ import annotations

from dqagent.deploy import seam

NAME = "anthropic"
ENV = {"model": "DQ_EXPLAINER_ANTHROPIC_MODEL", "endpoint": "DQ_EXPLAINER_ANTHROPIC_ENDPOINT", "key": "DQ_EXPLAINER_ANTHROPIC_KEY"}


@seam("llm.provider")
def configured(pcfg: dict, env: dict) -> bool:
    return bool(env.get("endpoint") and env.get("key") and (env.get("model") or pcfg.get("model")))


def build_request(prompt: dict, pcfg: dict, env: dict):
    body = {"model": (env.get("model") or pcfg["model"]), "max_tokens": int(pcfg.get("max_tokens", 600)), "temperature": 0,
            "system": prompt["system"], "messages": [{"role": "user", "content": prompt["user"]}]}
    headers = {"x-api-key": env["key"], "anthropic-version": pcfg.get("api_version", "2023-06-01")}
    return env["endpoint"], headers, body


def parse_response(obj: dict) -> str | None:
    parts = [c.get("text", "") for c in obj.get("content", []) if isinstance(c, dict) and c.get("type") == "text"]
    return "".join(parts) or None
