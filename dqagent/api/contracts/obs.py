"""Response contracts of dqagent/api/obs_routes.py (Part F P1-1 §F). Every model forbids extra keys, so a field that
is added on the server and not here fails the contract tests instead of reaching the Tower unannounced."""
from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RunRow(_M):
    trace_id: str
    kind: str
    state: str
    pipeline_id: Optional[str]
    night_date: Optional[str]
    attempt: int
    status: Optional[Literal["STARTED", "RESUMED", "COMPLETED", "FAILED"]]
    started_at: str
    span_count: int
    source_mode: Optional[str]


class RunList(_M):
    runs: list[RunRow]
    next: Optional[str]


class RunEvent(_M):
    event: Literal["STARTED", "RESUMED", "COMPLETED", "FAILED"]
    error_class: Optional[str]
    at: str


class Run(_M):
    trace_id: str
    kind: str
    parent_trace_id: Optional[str]
    state: str
    pipeline_id: Optional[str]
    night_date: Optional[str]
    attempt: int
    person_id: Optional[str]
    config_digest: str
    code_digest: str
    deployment: str
    source_mode: Optional[str]
    started_at: str


class LlmCall(_M):
    call_id: int
    kind: str
    provider: str
    model: Optional[str]
    transport: str
    source_mode: Literal["TEMPLATE", "SCRIPTED", "RECORDED", "LIVE"]
    prompt_version: str
    check_version: str
    template_version: str
    facts_hash: str
    verdict: Literal["PASS", "REJECT", "NOT_CHECKED", "NO_OUTPUT"]
    reason_codes: list[str]
    output_text: Optional[str]
    input_tokens: Optional[int]
    output_tokens: Optional[int]
    latency_ms: Optional[int]
    finish_reason: Optional[str]


class Guardrail(_M):
    id: int
    trace_id: Optional[str]
    span_id: Optional[str]
    code: str
    subject: str
    seat: Optional[str]
    detail: dict[str, int]
    fail_mode: Literal["open", "closed"]
    at: str


class ReleaseCheck(_M):
    unit_key: str
    ok: bool
    reasons: list[str]


class Span(_M):
    trace_id: str                       # F-35: the run a step belongs to (unit traces link back to it)
    span_id: str
    parent_span_id: Optional[str]
    ordinal: int
    name: str
    seat: Optional[str]
    subject: Optional[str]
    status: Literal["OK", "ERROR", "SKIPPED"]
    started_at: str
    ended_at: str
    attrs: dict[str, Any]
    refs: list[str]
    linked: dict[str, list[ReleaseCheck]]
    llm_call: Optional[LlmCall]
    guardrails: list[Guardrail]


class RunDetail(_M):
    run: Run
    events: list[RunEvent]
    spans: list[Span]
    hidden_out_of_scope: int            # 1 when some steps of this run are outside the caller's scope, else 0 (never
                                        # a count: no names, timings or counts of hidden steps are disclosed)
    hidden_label: Optional[str]


class UnitTrace(_M):
    unit_key: str
    spans: list[Span]


class Citation(_M):
    span: Optional[dict[str, Any]]      # {start, end, shown} in the explanation text, or None (template citations)
    fact_key: str
    evidence_ref: Optional[str]
    resolvable: bool


class Citations(_M):
    subject: str
    explanation_ref: dict[str, str]
    label: Literal["generated_from_facts", "slot"]
    source_mode: str
    citations: list[Citation]


class GuardrailList(_M):
    events: list[Guardrail]
    next: Optional[str]


class OtlpExport(_M):
    resourceSpans: list[dict[str, Any]]


class UsageRow(_M):
    day: str
    provider: str
    transport: str
    source_mode: str
    calls: int
    verdicts: dict[str, int]
    input_tokens: int
    output_tokens: int
    latency_ms_p50: Optional[int]
    latency_ms_p95: Optional[int]
    tokens_label: Optional[str]          # TEMPLATE / SCRIPTED / RECORDED: "no model is called: 0 tokens"


class Usage(_M):
    days: int
    rows: list[UsageRow]


class PromptVersion(_M):
    prompt_version: str
    system_prompt: str
    check_version: str
    template_version: str
    first_seen: str
    calls: int
    traces: list[str]
    current: bool


class Prompts(_M):
    prompts: list[PromptVersion]


class SpanDelta(_M):
    key: str                             # parent path / name / subject: how the two runs' steps are matched
    in_a: bool
    in_b: bool
    status: dict[str, Optional[str]]     # {a, b}
    verdict: dict[str, Optional[str]]    # the llm call verdict, {a, b}
    fact_check: dict[str, Optional[str]]


class RunDiff(_M):
    a: str
    b: str
    status: dict[str, Optional[str]]
    spans_only_in_a: int
    spans_only_in_b: int
    span_deltas: list[SpanDelta]
    guardrail_deltas: dict[str, dict[str, int]]          # code -> {a, b}
    release_check_deltas: list[dict[str, Any]]           # [{unit_key, a, b}] where ok differs
    hidden_out_of_scope: int
