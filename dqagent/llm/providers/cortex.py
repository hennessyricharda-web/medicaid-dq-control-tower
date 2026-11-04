"""Cortex inference REST (POST {endpoint}/api/v2/cortex/inference:complete, bearer token, non-streaming).
Not exercised live."""
from __future__ import annotations

from dqagent.deploy import seam

NAME = "cortex"
ENV = {"model": "DQ_EXPLAINER_CORTEX_MODEL", "endpoint": "DQ_EXPLAINER_CORTEX_ENDPOINT", "key": "DQ_EXPLAINER_CORTEX_TOKEN"}


@seam("llm.provider")
def configured(pcfg: dict, env: dict) -> bool:
    return bool(env.get("endpoint") and env.get("key") and (env.get("model") or pcfg.get("model")))


def build_request(prompt: dict, pcfg: dict, env: dict):
    url = env["endpoint"].rstrip("/") + "/api/v2/cortex/inference:complete"
    body = {"model": (env.get("model") or pcfg["model"]), "stream": False, "temperature": 0,
            "max_tokens": int(pcfg.get("max_tokens", 600)),
            "messages": [{"role": "system", "content": prompt["system"]},
                         {"role": "user", "content": prompt["user"]}]}
    return url, {"authorization": f"Bearer {env['key']}"}, body


def parse_response(obj: dict) -> str | None:
    ch = obj.get("choices") or []
    if ch and isinstance(ch[0], dict):
        msg = ch[0].get("message") or ch[0].get("messages") or {}
        if isinstance(msg, dict):
            return msg.get("content")
        if isinstance(msg, str):
            return msg
    return None
