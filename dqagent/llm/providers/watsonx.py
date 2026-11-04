"""watsonx.ai text generation (POST {endpoint}/ml/v1/text/generation, bearer token). Not exercised live.
The bearer token is read from the environment as-is (the IAM exchange is the deployer's job)."""
from __future__ import annotations

from dqagent.deploy import seam

NAME = "watsonx"
ENV = {"model": "DQ_EXPLAINER_WATSONX_MODEL", "endpoint": "DQ_EXPLAINER_WATSONX_ENDPOINT", "key": "DQ_EXPLAINER_WATSONX_TOKEN",
       "project": "DQ_EXPLAINER_WATSONX_PROJECT"}


@seam("llm.provider")
def configured(pcfg: dict, env: dict) -> bool:
    return bool(env.get("endpoint") and env.get("key") and env.get("project") and (env.get("model") or pcfg.get("model")))


def build_request(prompt: dict, pcfg: dict, env: dict):
    url = env["endpoint"].rstrip("/") + "/ml/v1/text/generation?version=" + pcfg.get("api_version", "2023-05-29")
    body = {"model_id": (env.get("model") or pcfg["model"]), "project_id": env["project"],
            "input": prompt["system"] + "\n\n" + prompt["user"],
            "parameters": {"decoding_method": "greedy", "max_new_tokens": int(pcfg.get("max_tokens", 600))}}
    return url, {"authorization": f"Bearer {env['key']}"}, body


def parse_response(obj: dict) -> str | None:
    res = obj.get("results") or []
    return res[0].get("generated_text") if res and isinstance(res[0], dict) else None
