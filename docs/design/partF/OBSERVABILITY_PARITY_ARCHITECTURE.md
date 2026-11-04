# Architect seat: DEC-022 agent observability with mock and production parity

**Provenance.** `/home/claude/medicaid-dq-agent` is not a git repository (`git rev-parse` fails), so there is no SHA to stamp. Every file:line below comes from my read on 2026-10-23. Each "absent" claim was confirmed twice, once by directory listing and once by a class/import grep.

## 0. Scope challenges first

**Yes, the TypeScript mock server is the parity risk, and it is already wrong in ways that break DEC-022.**

- **Mock hashes are not real hashes.** The mock explainer hashes with FNV (`fixtures.ts:1136-1138`, `fx` at `:623`). The API uses sha256 over canonical facts (`facts.py:34-39`). Recorded responses, citations and feedback would all be keyed by `facts_hash`, so none of them can resolve in mock mode as things stand.
- **Mock explanations skip the real pipeline.** They are hand-written strings (`fixtures.ts:1201-1573`). They never pass through `facts.build` or `check`.
- **The mock `/explainer` shape differs from the API.** The mock returns `{provider, configured, fallback_count}` (`server.ts:995`). The API returns `{provider, configured, fallback_count, decisions, label}` (`llm/__init__.py:113-115`).
- **The production API shows a false label today.** `app.py:323` and `app.py:734` hard-code `"llm_provider": "NullProvider"` even when a real provider is configured.

**Three seams have no production implementation, so the rule can only be met there by failing closed.**

- **IdP.** `Runtime.__init__` always builds `LocalIdP` (`runtime.py:163`). `verify` pins the issuer to `https://idp.t1.local` (`idp.py:18`, `:53`).
- **Data plane.** Only DuckDB exists (`adapters/dataplane/duckdb_dp.py`).
- **Orchestrator and DAG adapters.** Both packages are empty (`adapters/orchestrator/__init__.py` and `adapters/dag/__init__.py`, 0 lines each).

**One seam fails open.** If no keys are passed, the runtime silently uses literal test keys: `keys or {"K_EV": b"t1-ev-key", ...}` at `runtime.py:158`.

**A defect that only shows up with a real model.**
- Explanations are rendered synchronously on every card read (`app.py:256-261`), and the Tower re-reads an open card every 20 s (`config.ts:5`).
- With the template provider this costs nothing. With a real provider, each open card makes one model call every 20 s, with up to 8 s of latency.
- A non-deterministic model also produces a new `text_hash` on every call. Only the `issued` map (`app.py:140, 188`) keeps decisions working.
- An explanation cache is therefore required for production. It is not optional polish.

## 1. Seam manifest (E1): `docs/seams.yaml`

| Seam | Mock | Production | Fail-closed behaviour | Test that pins it |
|---|---|---|---|---|
| `llm.provider` | `template`; `recorded` (new, §4) | anthropic, watsonx, azure_openai, cortex (`providers/*.py`) | `complete` returns None, the template is shown labelled FALLBACK (`__init__.py:89-102`). This is fail-*safe*. The label must name the provider that was attempted. | Stub HTTP server tests: `test_parte_explainer.py:166-286`; new `test_seam_llm_parity` |
| `llm.transport` (new) | `RecordedTransport` | `post_json` (`_http.py:7`) | A missing recording returns None, which becomes FALLBACK with reason `NO_RECORDING` | new |
| `idp` | `LocalIdP`; Tower `mockIdp` (`api.ts:288`) | **none**. New `OidcIdP`: JWKS from `DQ_OIDC_JWKS_URI`, issuer and audience from the state profile | Raises `NotConfiguredError` when neither demo nor test mode is set and OIDC is not configured | new `test_idp_unconfigured_raises` |
| `secrets` | Literal test keys, allowed only in test/demo mode | New `EnvSecrets` (`DQ_K_EV`, `DQ_K_FP`, `DQ_K_ABC`) | Raises; this removes the default at `runtime.py:158` | new |
| `dataplane` | DuckDB | DuckDB is `real-impl` (local). Snowflake, Databricks and BigQuery are `fail-closed-stub` entries. | A stub raises on construction | new |
| `orchestrator`, `dag` | none | none | Disposition `planned`. The gate fails if code imports either package before the manifest entry is promoted. | new |
| `clock` | `FrozenClock` / simulator date | `SystemClock` | Not applicable. It must be injectable at `runtime.py:405, 469, 474`, `app.py:298`, `idp.py:37, 55` and `registry.py:71`. `evaluate.py:69` is already injectable. | new determinism test |
| `tower.datasource` | `mockApi` (`api.ts:142`) | `fetch` (`api.ts:152`) | `MOCK` is true only when the value is `"1"` (`config.ts:3`), which is correct | E15 parity test (§2) |
| `alert_sink` | — | — | Two searches (alert, webhook, notify, smtp) found no sink, only an unverified hit in `engine/run.py`. Disposition `absent`, to be confirmed before it is recorded as fact. | — |
| `explanation_cache` (new) | in-memory | control-plane table | Bypassed on error, which means re-rendering through the checked path | new |

**Entry shape:**

```
id, disposition: real-impl|fail-closed-stub|mock-only|planned|absent,
mock: {impl: module:attr}, production: {impl, env: [...]},
fail_closed_test: <nodeid>, parity_test: <nodeid>
```

**Gate: `tests/t0/test_seam_manifest.py`.**
- A `@seam("id")` marker sits on each implementation. The test fails when:
  - a marked implementation has no manifest entry;
  - an entry lacks either side;
  - a `fail-closed-stub` returns a value instead of raising;
  - a cited test id does not resolve under `pytest --collect-only` (the E12 check);
  - a `real-impl` falls back to its mock when production is configured.
- A companion test asserts that every mock or demo flag defaults off. That covers `DQ_ALLOW_TEST_GENESIS`, `NEXT_PUBLIC_DQ_MOCK`, and `recorded` refused under governed genesis, following the same pattern as `cli/main.py:83`.

## 2. API contract source of truth, and the E15 parity gate

**Define shapes once.**
- Pydantic v2 is already a dependency (`pyproject.toml:6`). Put the models in `dqagent/api/contracts/`, one module per area, with `extra="forbid"`.
- Today `app.py` has no `BaseModel` anywhere. Every shape is an implicit dict across about 57 routes.
- **Phase A (zero runtime blast radius):** the models validate only in tests, plus an opt-in `DQ_CONTRACT_STRICT=1` middleware that returns 500 on any mismatch, used in CI and demo runs.
- **Phase B:** attach `response_model` route by route, once each route is green. Do not attach it earlier, because pydantic silently strips undeclared keys, which would hide drift.

**TypeScript types.**
- `tools/gen_contract.py` writes `model_json_schema()` to `docs/contract/schema.json`, then to `tower/src/lib/contract.gen.ts`.
- I recommend a small Python emitter so there is no new npm dependency.
- `types.ts` (831 lines) is not rewritten. It gains compile-time equality checks, e.g. `const _e: Eq<Explanation, G.Explanation> = true`, so `tsc` catches drift and no screen has to change.

**Fixtures are recorded, not hand-written.**
- `tools/record_fixtures.py` builds a fresh control plane (the agent-eval pattern) and seeds the demo with a `FrozenClock`.
- It runs scripted transcripts of (user, method, path, body) through `TestClient`.
- It normalises surrogate ids the way `test_agent_eval.py:213-217` already does.
- It writes `tower/src/lib/mock/recorded/*.json`. `fixtures.ts` then loads these JSON files instead of building literals, one endpoint at a time.

**Parity tests.**
1. **Live → schema.** Every recorded live response validates against its contract model.
2. **Mock → schema and transcript.** `tower/scripts/mock-parity.mjs` replays the same transcripts through `mockApi` and writes the responses to JSON. Python validates them against the same models and checks shape equivalence with the live transcript.
   - Running TypeScript from Node needs `tsx` as a devDependency. Node 22.22 strips types, but `server.ts` uses extensionless imports, which Node's ESM resolver rejects.
   - **Decision needed from you:** add the dependency.
3. **Route coverage, both directions.** Every path the client calls in `api.ts` exists both in `app.routes` and in the mock route table, and every mock route exists live. Today there are about 38 mock patterns against 59 decorators (`app.py`), which includes the IdP and JWKS routes.

**Limit, stated plainly.** Behavioural parity of the mock state machine is proven only along recorded transcripts. Paths with no transcript remain unproven, and the manifest must say so.

## 3. Observability data model (extends EXECUTION_TRACE §1)

**Spans.** These reuse the `spans` table and widen the `attrs` whitelist with typed keys.

- **`llm.explain` span:**
  - `gen_ai.system` (`template`, `recorded`, or a provider name);
  - `gen_ai.request.model`;
  - `gen_ai.usage.input_tokens` and `output_tokens`, parsed per vendor in each `parse_response` (written from documentation, unverified);
  - `gen_ai.response.finish_reasons`, latency in ms, `source_mode` (`TEMPLATE`, `RECORDED`, `LIVE`), `prompt_version` (a hash of `SYSTEM_PROMPT`), `input_digest` (sha256 of the prompt), `facts_hash`, `text_hash`;
  - the output text is stored, because it is already sanitised and shown to users.
- **Prompts are not stored verbatim.** They can be rebuilt from `prompt_version` plus `explain_facts(facts_hash, facts jsonb)`, a content-addressed table holding sanitised facts only.
- **`llm.validate` child span:** verdict `PASS`, `REJECT` or `NOT_CHECKED` (for the template), plus reason *codes*.
  - Today's reasons embed model-emitted tokens, e.g. `check.py:379` "identifier not in facts: {tok}".
  - The trace should store the code and a sha256 of the token, not the token itself.

**Guardrail events.** New table `guardrail_events(id, trace_id, span_id, at, code, subject, seat, detail jsonb counts-only)`. Emit points:

| Code | Emit point |
|---|---|
| MASKED | `facts.py:85-87` |
| SUPPRESSED | `facts.py:99-100` |
| PART2_WITHHELD | `facts.py:123-124` |
| CHECK_REJECTED | `llm/__init__.py:97` |
| FALLBACK | `:101` |
| ADVICE_BLOCKED and CAUSAL_BLOCKED (sub-codes of CHECK_REJECTED) | `check.py:475, 479` |
| FLOOR_APPLIED | `modes.py:53-62` |
| CEILING_APPLIED | `modes.py:68` |
| ENTITLEMENT_REFUSED | `modes.py:71` |
| SCOPE_DENIED | `app.py:101, 132, 386, 506` |
| BODY_REJECTED | `agent_schema.py:143-146` (from Python, on the raised exception) |
| EXPLANATION_MISMATCH | `app.py:189, 207` |
| STEP_UP_REQUIRED | `app.py:539` |

Writing these events only observes. No emit point may change control flow.

**Citations.**
- `check()` already binds every number and disposition to a claim, clause by clause (`check.py:281-466`), but it throws those bindings away.
- Return a `CheckResult(ok, reasons, bindings)` and keep a tuple shim, because the adversarial suite calls the tuple form (`test_explainer_adversarial.py:131-167`).
- Each binding maps a sentence to its claim, its `fact_key`, and an `evidence_ref`.
- `evidence_ref` comes from a new `fact_sources` map that `unit_facts` (`app.py:238-254`) and `facts.adapt` emit beside the facts. Examples: `results:<unit>@<eval_version>`, `abc:<batch>`, `decisions:<id>`.
- **The template must emit the same claims** from its render bindings, so citations appear in template and recorded modes alike.
- Template citations are labelled "generated from facts", never "checked". This preserves red-team finding E2 (`__init__.py:42`).

**Evaluations.**
- `eval_runs(run_id, suite, code_sha256, config_digest, provider, model, source_mode, junit_sha256, attestation_id NULL)`.
- `eval_cases(run_id, case_id, kind, facts_hash, expected, verdict, reason_codes, passed)`.
- These are populated by a pytest plugin over `tests/agent_eval` (11 scenarios) and the adversarial explainer suite.
- A run is linked to the existing two-person attestation (`governance_schema.py:588-614`, `app.py:1364-1382`) through `junit_sha256`.

**Feedback.**
- `explanation_feedback(person_id, facts_hash, text_hash, trace_id, span_id, verdict ACCURATE|INACCURATE|UNCLEAR|UNSAFE, comment)`.
- A non-ACCURATE verdict creates an `eval_candidates` row.
- Promotion into the eval set is a governed change-request item of a new kind `EVAL_CASE`, through `POST /governance/change-requests` (`app.py:946`), so it is two-person and enforced by the database.
- **PHI risk:** a free-text comment cannot be screened reliably. Store it visible only to the governance duty, and **never** copy it into an eval case. The case carries only `facts_hash`, the verdict and a reason code.

**PHI.** Facts are sanitised before any provider sees them (`facts.py:120-136`). Traces store facts only through `explain_facts`. The existing spy test (`test_parte_explainer.py:286`) is extended to cover span rows and recordings.

**Explanation cache.** `explanations(facts_hash, provider_config_digest) → text, text_hash, source_mode, claims`. This makes `text_hash` stable with a real model and removes the per-poll calls.

## 4. Recorded-response provider

**Same path as production.**
- `recorded` is a transport, not a fake provider. `complete()` still runs the real vendor module's `build_request` and `parse_response` (`providers/__init__.py:165-181`). Only `post_json` is replaced.
- The parse step, `check`, the spans and the guardrail events are byte-for-byte the same path a live provider takes.

**Fixtures and labelling.**
- Fixtures live at `config/recordings/<vendor>/<kind>/<facts_hash>.json`, each holding the raw vendor wire response plus `{label: GOOD|BAD_<reason>, recorded_from: synthetic|live, model}`.
- The BAD set is derived from the 30-plus adversarial cases, so a demo can show REJECT turning into FALLBACK.
- RECORDED labelling is carried throughout: `source_mode` in the `explain()` return, `/explainer`, the Summary, spans, the eval runs and a Tower badge.

**Record mode.** `dqagent explainer record --provider X --corpus agent_eval` runs only when the keys are present. It captures responses, re-verifies that nothing beyond sanitised facts was sent, and writes the fixtures.

**Staleness gate.** A test reports the share of demo `facts_hash` values that have a recording. Any change to facts invalidates recordings, which then fall back to the template, honestly labelled.

## 5. Tower

**Routes and API.**
- New routes: `/runs/[traceId]`, the trace explorer, and `/evaluations`.
- New endpoints, contract-first:
  - `GET /runs/{id}?expand=spans`;
  - `GET /explanations/{facts_hash}/citations`;
  - `GET /guardrail-events`;
  - `GET /evals` and `GET /evals/{run_id}`;
  - `POST /explanations/feedback`.

**Trace explorer.**
- The span tree uses a waterfall built from `started_at`/`ended_at`. Timing is excluded from digests.
- The detail pane shows the sanitised facts, the output, a validator badge and guardrail chips.
- Explanation text renders citation chips that link to the evidence row.
- Feedback controls sit on every explanation.

**Both modes from the same files.** Mock mode serves all of this from the recorded JSON (§2), using real sha256 hashes. Live mode serves the same shapes from the API, and the parity test covers both.

## 6. Waves

| Wave | Content |
|---|---|
| **T0** | Unchanged and first (EXECUTION_TRACE §7) |
| **T1a: contract and seams** | `seams.yaml` and its gate; the clock, secrets and IdP fail-closed changes; contract models for the existing routes (validate only); the TypeScript generator and equality checks; the recorder; the parity test on read routes; replace FNV mock hashes with recorded values; fix the `NullProvider` label |
| **T1b: traces and GenAI** | EXECUTION_TRACE T1, plus LLM and validator spans, `guardrail_events`, `explain_facts`, the explanation cache, the recorded transport, `CheckResult` bindings and citations |
| **T1c: evals and feedback** | `eval_runs`, `eval_cases`, the plugin, attestation linkage, feedback, candidates and the `EVAL_CASE` change-request kind; the Tower trace explorer, Evaluations screen, chips and feedback |
| **Shell** | Runs in parallel, Tower-only. Its layout primitives should land before T1c's screens; otherwise T1c is re-skinned afterwards. Its only shared files with T1a are `types.ts` and `api.ts`. |

**Blast radius.**
- **`tests/t1/test_w1_outcomes.py`** (`EXPECTED` at `:19`, 420/291/47): tracing, guardrail events and the cache only observe. The risk lies in the clock refactor, so the full suite must be re-run after T1a.
- **Agent-eval digests:** `observe()` selects explicit columns (`test_agent_eval.py:194-227`), and the new tables are not among them, so the digests are unchanged.
  - The README claims `NullProvider`, which is only true while the provider is the template. Pin `provider: template` inside agent_eval.
- **`test_parte_explainer.py:83`** (pinned template text): unchanged, because citations are a separate field.
- **`check_ref` and `decide_ref`** read only the two hashes, so the additive keys in the `explain()` return are safe.
- **Mock fixtures:** the hash switch touches about 40 `explain(...)` call sites in `fixtures.ts`.
- **Contract models:** about 57 routes. This is the largest cost in the plan.

## 7. Where the rule costs or carries risk

- **No real model credentials exist here.** All four providers are contract-tested only against a local stub (`test_parte_explainer.py:166-183`) and labelled "not exercised live" (`providers/__init__.py:19`).
  - Token-usage parsing and record mode are written from vendor documentation and are unverified.
  - Wire versions are pinned: Anthropic API version `2023-06-01`, watsonx `2023-05-29`.
  - The E7 second number for the LLM seam is **0**.
- **IdP, warehouse data planes and orchestrators.** "Production-like" here means fail-closed, not working. The demo must not imply otherwise.
- **Mock behaviour.** Shape parity can be proven mechanically. Behavioural parity holds only along recorded transcripts.
- **A new devDependency (`tsx`) and the cost of about 57 contract models** are the price of removing hand-written mock shapes.
- **Freezing the clock across the runtime** is necessary for stable recordings. It is the one change in this plan with any outcome risk.

### Critical Files for Implementation
- /home/claude/medicaid-dq-agent/dqagent/llm/__init__.py
- /home/claude/medicaid-dq-agent/dqagent/llm/check.py
- /home/claude/medicaid-dq-agent/dqagent/api/app.py
- /home/claude/medicaid-dq-agent/dqagent/runtime.py
- /home/claude/medicaid-dq-agent/tower/src/lib/mock/fixtures.ts

**Worth saving to memory:**
- The mock explainer uses FNV hashes rather than sha256 (`fixtures.ts:1136`).
- `app.py:323` and `:734` hard-code `NullProvider`.
- `runtime.py:158` falls back to literal test keys, which fails open.
- There is no production IdP, no warehouse data plane and no orchestrator adapter.
- Card polling every 20 s with a real LLM requires an explanation cache.
---

# ADJUDICATION: red team round 1 (2026-10-23). This section is binding wherever it conflicts with the text above.

## Accepted

**C1. Replace fixture recordings with a scripted-model transport.**
- The transport builds vendor-shaped wire JSON from the current facts.
- Deterministic BAD mutators add a stray number, a causal word, advice or a negated outcome, so REJECT → FALLBACK is visible on real demo cards.
- A few real recordings are kept only for parse-fidelity tests.
- Everything is labelled RECORDED/SCRIPTED.

**C2. Make the transport an explicit `complete(..., transport)` parameter.**
- It returns `(raw|None, reason_code, source_mode)`.
- `source_mode` is derived from the transport object actually used, never from config.
- The transport checks the request digest, so `build_request` is exercised for real.
- No fake secrets are needed.

**C3. Scope everything by subject and authority.**
- Citations and feedback are keyed by `(subject, explanation_ref)`, never by `facts_hash` alone.
- Every new observability table carries `authorities`.
- Scope is re-checked when an `evidence_ref` is resolved.
- SCOPE_DENIED events are visible only to the audit duty.

**C4. Bind each number to a claim in its own clause.** An ambiguous binding is a REJECT reason. This is also a defect in the current validator (`check.py:396-403`) and is tracked as backlog F-21.

**I1. Explanation cache.**
- The key includes `prompt_version`, `check_version` and `template_version`.
- FALLBACK results are never cached.
- `check()` re-runs on every cache read.
- Insert with ON CONFLICT DO NOTHING, then re-read and serve the winning row.
- Any cached row counts as issued for `check_ref`, so issuing works across workers.

**I2. Eval cases come from the governed artifact.**
- The runner loads eval cases from the governed artifact. The EVAL_CASE kind gets a DDL migration of the kind CHECK.
- An attestation counts toward the track record only when `source_mode=LIVE`. TEMPLATE, RECORDED and SCRIPTED runs are recorded, but they never move autonomy evidence.

**I3. Guardrail events.**
- Events are collected in the result object and written once per render, not on cache hits.
- Each code declares whether its write fails open or closed. Audit-relevant codes fail closed: SCOPE_DENIED, ENTITLEMENT_REFUSED, CHECK_REJECTED.

**I4. LLM call detail goes in a typed `llm_calls` table, not in span attrs.**
- Covers model, output text, reason codes and tokens.
- Untrusted model tokens are stored only as sha256.

**I5. Demo mode needs explicit deployment mode.**
- The entrypoint passes `deployment=demo|pilot|production`.
- `/tenant` exposes a demo banner.
- Demo mode is refused under governed genesis.
- A build test asserts that `mockApi` and `mockIdp` are absent from a build with mock off: the imports become dynamic, behind MOCK.

**I6. Mock data never claims to be LIVE.** The mock overwrites `source_mode`. A test asserts there is no `LIVE` value in `tower/src/lib/mock/recorded`.

## Order (accepted)

1. T0: F-01, F-02, F-15, F-21.
2. Traces: EXECUTION_TRACE T1, plus LLM spans, `llm_calls`, guardrail events, the scripted transport and citations.
3. The trace explorer and Evaluations.
4. Parity, contracts and seams.

The shell runs in parallel, Tower-only.

## Cut or shrink (accepted)

- **No 57 contract models.** Instead, record live transcripts and diff keys and types against the mock. Pydantic models are written only for the new endpoints.
- **No `tsx`.** Compile the mock with the existing TypeScript 5.9.3 (`tsc --module commonjs`) into a temp directory. Backlog item F-20 is closed by this.
- **OIDC:** only the guard that raises when it is unconfigured. The `OidcIdP` implementation is a pilot item.
- **No repo-wide clock refactor.** The scripted transport removes the need for one. The clock stays injectable where it already is.
- **Seams manifest and its gate are kept.** They are cheap.

## Added ("LangSmith-like" expectations the red team found missing)

- Search and filter over traces: status, guardrail code, unit, pipeline, provider/source_mode, validator verdict.
- Run diff and eval-run diff: two traces or eval runs side by side, with span, verdict and outcome deltas.
- A latency, token and cost dashboard over time. TEMPLATE and SCRIPTED runs show 0 tokens and are labelled.
- A prompt-version history: `prompt_version` → text, `check_version`, and which runs used it.
- An annotation queue: non-ACCURATE feedback lands in a triage list with assign and resolve, and promotion to an eval case goes through a two-person CR.
- OTLP export, which §2 had dropped. Restored per F-05.
