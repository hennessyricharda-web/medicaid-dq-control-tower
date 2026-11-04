"""Azure OpenAI chat completions (POST {endpoint}/openai/deployments/{deployment}/chat/completions, api-key).
Not exercised live."""
from __future__ import annotations

from dqagent.deploy import seam

NAME = "azure_openai"
ENV = {"endpoint": "DQ_EXPLAINER_AZURE_OPENAI_ENDPOINT", "key": "DQ_EXPLAINER_AZURE_OPENAI_KEY",
       "deployment": "DQ_EXPLAINER_AZURE_OPENAI_DEPLOYMENT"}


@seam("llm.provider")
def configured(pcfg: dict, env: dict) -> bool:
    return bool(env.get("endpoint") and env.get("key") and env.get("deployment"))


def build_request(prompt: dict, pcfg: dict, env: dict):
    url = (env["endpoint"].rstrip("/") + f"/openai/deployments/{env['deployment']}/chat/completions"
           f"?api-version={pcfg.get('api_version', '2024-02-01')}")
    body = {"messages": [{"role": "system", "content": prompt["system"]},
                         {"role": "user", "content": prompt["user"]}],
            "temperature": 0, "max_tokens": int(pcfg.get("max_tokens", 600)),
            "response_format": {"type": "json_object"}}
    return url, {"api-key": env["key"]}, body


def parse_response(obj: dict) -> str | None:
    ch = obj.get("choices") or []
    return ((ch[0].get("message") or {}).get("content")) if ch and isinstance(ch[0], dict) else None
