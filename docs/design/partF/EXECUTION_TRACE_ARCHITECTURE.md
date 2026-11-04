# Architect seat: agent execution traceability and per-DAG configuration

**Provenance.** `/home/claude/medicaid-dq-agent` is not a git repository (`git rev-parse` fails), so there is no SHA. Every file:line below is from my read on 2026-10-23, and lines are moving while the fix seat works.

## 0. Findings and scope challenges, before any design

**SC-1. Correctness bug: a crash between seal and ABC fails open. Fix this before tracing.**
- `seal()` sets `batches.sealed=true` at `engine/seal.py:90`. ABC is only recorded afterwards, at `runtime.py:347-348`, and the intake ABC rows (`it.abc`) exist only in memory until then.
- If the process dies in between, `_ingest_state` reports SEALED (`runtime.py:926`). `_resume_file` (`:930-939`) never re-records ABC.
- Two independent paths then read "no ABC rows" as "ABC passed":
  - `reevaluate_pending` (`:369-372`) passes `abc_failed=False`;
  - the publisher's `effective_mode` calls `gating_abc_failed` (`hitl/modes.py:120-122, 156`), which returns False when there are no rows.
- A batch whose ABC failed can therefore be released autonomously after a restart. Any API decision triggers this through `after_decision → verify_and_publish → reevaluate_pending`, even without the simulator.
- Resume also skips the INTAKE, EVALUATOR and TRIAGE seats for that batch (`:357-359`).
- **Your candidate (d) is mostly built already.** The simulator already has STARTED/RESUMED/FAILED and the never-re-ingest resume (`runtime.py:1167-1209`, `sim_night_events` at `governance_schema.py:925`). What it lacks is this fail-closed guard and a journal for the CLI full-load path (`cli/main.py:61`, `record=False`).
- **Label collision:** the blackboard's "E6" is the p-chart crash at n=0 (`docs/blackboard.md:1369`). The code uses "E6" for resumable nights. Confirm which one is "in progress".

**SC-2. "Add a new DAG and test it" cannot evaluate a new source today.** Six things are hard-coded:
- intake at `runtime.py:901-913`;
- parsers for IE, 834 and 837P only (`intake.py:165, 189`);
- the WAIT join, which is W1-specific (`evaluate.py:76-89`, marked as a STUB);
- `X12_RELATIONS` (`evaluate.py:30-31`);
- TRIAGE's lookup of `bronze.ie_segment` (`seats.py:167`);
- the dbt hold list (`dbt_runner.py:15-18`).

Making it work is DEC-012(b), "a new pipeline added by YAML only", which is not built.

**Recommendation:** the demo's new DAG should be one that *consumes datasets that are already gated*, for example a new dbt T-MSIS outbound project over `bronze.ie_segment` and `bronze.encounter_header`. Profile, binding, shadow, verdict, trace and backtest are then all honest with no intake change. A new *source* domain becomes a named next wave. **Decision needed from you.**

**SC-3. "Shadow" is the wrong unit, because the agent gates datasets, not DAGs.**
- A relation's units are shared across every DAG that reads it (`seal.py:57-92`).
- A shadow *unit lifecycle*, meaning evaluate but never queue or publish, would touch the lifecycle, the publisher and the queue. That is a large blast radius.
- Your sequence "shadow → advisory → block, tier L1" conflates two axes:
  - **gate enforcement**: the GATE_BINDING `mode` (advisory or block) in Part F §3;
  - **release autonomy**: the tier ceiling, `release_ceiling` from L1 upwards.
- **Recommendation:**
  - shadow = binding `mode: advisory`, which Part F already provides;
  - backtest = offline, in a sandbox control plane;
  - promotion = two separate change requests, one per axis.
- Also, a new binding at L1 does not mean "quiet": L1 sends every unit to a person (`modes.py:19`).

**SC-4. Replaying "historical nights" needs the raw files to be retained.** Only synthetic runs are replayable today. A backtest on real history depends on a raw-retention policy, so label backtests SYNTHESIZED for now.

**SC-5. Some per-DAG configuration is not governed, so the profile must say so rather than imply it is.**
- The MONITOR spec and bindings come from `monitor_history.json` (`runtime.py:634-643`). The MONITOR artifact carries only `run_rules`, `min_periods` and `psi` (`:614-625`).
- The pipeline-to-feed mapping lives in code (`_file_plan`).

**Other traceability gaps that motivate (a):**
- `audit_log` stores `event` and `payload_digest` only, with no subject (`schema.py:272-274`). The MODE_A and VETO_WINDOW_OPENED rows (`evaluate.py:340-347`) cannot be joined back to their unit.
- The publisher's per-unit "why not released" reasons are returned and then discarded (`publish.py:268-270`).
- The calendar-driven ruleset reload re-opens units silently (`runtime.py:276-282`).

## 1. Data model

**Trace linkage: a column, not a duplicate and not the payload.**
- Add `trace_id text` and `span_id text`, both `DEFAULT current_setting('dq.trace_id'|'dq.span_id', true)`, to:
  - `unit_events`, `results`, `abc`, `measure_results`;
  - `agent_blackboard`, `audit_log`, `veto_windows`, `decisions`;
  - `attestations`, `manifest_versions`, `monitor_metrics`.
- Set them in `CP.connect_as` (`engine/cp.py:24-25`) through psycopg `options="-c dq.trace_id=… -c dq.span_id=…"`, read from a contextvar. This costs no extra round trip, and each connection lives for exactly one `as_role` (`cp.py:30-40`).
- SECURITY DEFINER writers (`append_audit`, `_bb_append`, `_transition`) inherit the setting through the column DEFAULT.
- The new columns stay **outside** `bb_hash` (`agent_schema.py:113-118`) and the audit hash (`schema.py:286`). The tables are already append-only.
- **Never put trace data in `unit_events.payload`.** Its `mode_trace` is in the eval digest (`test_agent_eval.py:200-202`).
- The trace is a correlation label, not a security control. Any role could set the setting, which is acceptable here and should be documented.

**New append-only tables** (in `dqagent/cp/trace_schema.py`, using the trigger pattern at `governance_schema.py:932-944`, with RLS):

| Table | Columns |
|---|---|
| `runs` | `trace_id` PK; `kind` (NIGHT, PIPELINE_RUN, REEVALUATE, PUBLISH_CYCLE, DECISION, APPLY_CHANGE, BACKTEST); `parent_trace_id`; `state`; `pipeline_id`; `night_date`; `attempt`; `person_id`; `config_digest` (ruleset_version plus the hash of the effective artifact versions); `code_digest` (`catalog_digest()`); `profile_digests jsonb`; `started_at` |
| `run_events` | `trace_id`; `event` (STARTED, RESUMED, COMPLETED, FAILED); `error_class`; `at` |
| `spans` | `span_id` PK; `trace_id`; `parent_span_id`; `ordinal`; `name`; `seat`; `subject` (a typed ref: `batch:`, `rel:`, `rule:`, `unit:`); `authorities text[]`; `status` (OK, ERROR, SKIPPED); `started_at`; `ended_at`; `attrs jsonb` (counts only, whitelisted like `agent_body_ok`); `refs text[]` (`refs_ok`). Inserted once, at span close. |
| `release_checks` | `span_id`; `unit_key`; `ok`; `reasons text[]`. A row is written **only when a unit's reasons change**, using the `gate_checks` dedupe pattern from Part F §2. |
| `backtest_reports` | `report_id`; `cr_id`; `inputs_digest` (sha256 of the data files plus both config digests plus `code_digest`); `report jsonb`; `report_digest` |

**Profile:** a pure function `compose_profile(pipeline_id, as_of)`, with no table. It reads `config_artifacts` at versions ≤ `as_of`, which is possible because the table is append-only with PK `(artifact_id, version)` (`governance_schema.py:25-29`).

## 2. Where spans are emitted

A `tracer.span(name, seat=, subject=)` context manager sets the contextvar.

**Night and pipeline runs:**
- `run_night` `runtime.py:1184-1198`: root NIGHT, with `attempt` = the number of STARTED/RESUMED events. Leave `sim_night_events` unchanged, because `test_sim_nightly` depends on it.
- `_run_night`:
  - one `file:<name>` span per file (`:1203-1210`);
  - `missing_deliveries` (`:1213`);
  - `monitor_close` (`:1216`);
  - `drain[i]` (`:1218-1223`);
  - `summary` (`:1226`).
- `run_pipeline` (`:339-361`):
  - `intake` (`:340-345`), `seal` (`:346`), `abc` (`:347-350`), `monitor` (`:351`);
  - `evaluate rel:<r>` (`:353`);
  - `seat INTAKE / EVALUATOR / TRIAGE` (`:357-359`).

**Evaluator:**
- One `rule:<id>@<rel>` span per rule at `evaluate.py:222`, with units in scope, failed count and `effective_hash`.
- One `measures` span at `:223-228`.
- **There are no per-unit spans.** Unit-level detail comes from the linked `unit_events` and `results` rows.

**Publish cycle and re-evaluation:**
- `verify_and_publish` `runtime.py:376-386`: PUBLISH_CYCLE, with children for each attest, `publisher.run` (released, blocked and ceiling_held counts, plus a clause histogram of reasons), `publish_manifest` (version and content_hash, `publish.py:277-318`) and `after_publish`.
- Write `release_checks` at `publish.py:259-270`.
- `reevaluate_pending` `runtime.py:364-373`: REEVALUATE.

**Configuration changes:**
- `governance_ctx` calendar reload `:276-282`: a `ruleset_reload` span listing the moved relations.
- `apply_change` `:310-327`: APPLY_CHANGE, carrying the `cr_id`.

**API:**
- decide, confirm and veto (`app.py:530, 557, 1398`): a DECISION trace, so `after_decision` nests under it.

**Deterministic ids:**
- `trace_id` = `NIGHT-<state>-<yyyymmdd>-a<attempt>`, `PCY-<parent>-<n>`, or `DEC-<decision_id>`.
- `span_id` = `sha256(trace_id|parent|name|subject|ordinal)[:16]`.

## 3. API

| Endpoint | What it returns |
|---|---|
| `GET /api/v1/runs?kind=&pipeline=&night=` | Run list |
| `GET /api/v1/runs/{trace_id}` | A span tree with linked-row counts per span, and `?expand=rows` for typed refs |
| `GET /api/v1/runs/{trace_id}/otlp` | OTLP/JSON `resourceSpans`, export only. traceId = the first 32 hex characters of sha256(trace_id); no OpenTelemetry dependency |
| `GET /api/v1/units/{key}/trace` | Every span that touched the unit, in order |
| `GET /api/v1/catalog/pipelines/{id}/profile?as_of=<trace_id\|cr_id>` | The composed profile (below) |
| `POST /api/v1/catalog/pipelines/{id}/profile/draft` | Builds `items[]` and calls the existing `POST /governance/change-requests` (`app.py:946`) with the person as author |
| `GET /api/v1/backtests/{report_id}` | A stored backtest report |

**Scope (the E5 lesson).** Spans are filtered by `authorities` exactly as `scope_summary` does it (`runtime.py:1085-1149`), failing closed when a span has no recorded authority. Engine-wide spans, such as `publish_manifest`, are visible with full counts only to the operations or audit duty.

**Profile content:**
- the PIPELINE and GATE_BINDING items, falling back to the HARNESS entry until Part F F0 lands;
- per gated dataset, the contract: domain, `authorities_by_program`, ordering, supersession;
- rules for the relation from the compiled ruleset: status, gating or advisory, thresholds, `effective_hash` against the approved `registry_events` hash, and the overlay artifact ids;
- policy and floors per authority;
- tiers from `autonomy_current`, with the binding ceiling and the **effective** ceiling shown;
- monitors and pinned baselines (memory ids and hashes), with `provenance: FILE` where SC-5 applies;
- dependencies and `order()`;
- FEEDS cadence, or `intake: CODE (runtime._file_plan)` for W1;
- the connection;
- `contributors[{artifact_id, version, content_hash, cr_id, approvers}]` and `profile_digest`;
- `edit_targets` for drafting a change.

Every run's root records `profile_digests`. That is the link you asked for: this night ran under this exact configuration.

## 4. How this composes with Part F and the Part E simulator

**Part F:**
- Catalog: the profile is the catalog node detail, extended.
- Wiring: the impact block gains `backtest_report_ref` and the `profile_digest` before and after.
- Drift: an OPEN drift appears as a profile banner and as a span attribute on the NIGHT that observed it.
- `gate_verdict()` gets its own span when it is called.

**Part E simulator:**
- `sim_run_next` (`runtime.py:1301`) becomes NIGHT traces with attempts.
- The Nightly runs screen links each `run_id` to its trace.
- `nightly_facts` is unchanged.

## 5. Determinism and replay

- Ids are deterministic (§2). Timing is excluded from every digest.
- A **trace-shape digest** is a canonical hash over (name, seat, subject, status, attrs, refs). It must be byte-identical across the two fresh control planes that agent_eval already runs (`tests/agent_eval/README.md`). Decision and change-request ids are canonicalised by order, as the eval already does at `test_agent_eval.py:213-217`.
- Profiles are reproducible `as_of` any trace.
- **Backtest** is a CLI command only, because it needs `CREATE DATABASE`:
  - `dqagent backtest --pipeline X --cr CR-n --nights N --data synthetic/w1`
  - It creates a baseline sandbox control plane and a candidate one (the live effective configuration plus the change request's items), then runs `run_night` N times in each.
  - It reports would-release, would-hold and pending per relation; non-PASS hit rates per rule; the held-reason histogram; baselines pinned or still building; the deltas; and both trace-shape digests.
  - It runs twice and stores the report only if the two `report_digest` values match.
  - It never touches the live control plane, and every report is labelled SYNTHESIZED.

## 6. Blast radius

- **`tests/t1/test_w1_outcomes.py` 420/291/47:** stays put, because tracing only observes. The SC-1 guard changes no normal path, since every sealed batch gets ABC rows from `landing_to_gated_abc` (`run.py:47-55`). The full suite must be re-run.
- **Agent eval digests:** `observe()` selects explicit columns (`test_agent_eval.py:196-210`), and `entry_hash` excludes the new columns, so the digests are unchanged. Add a trace-shape determinism test alongside.
- **Views that use `*` over altered tables** (`abc_gating_failures`, `governance_schema.py:791`): re-applying the schema must still succeed. Test this on a schema that is already applied.
- **`test_cp_invariants`:** the hostile sweep must cover the new tables, and a role must not be able to forge trace rows.
- **`test_api_contract`:** the routes are additive.
- **`test_sim_nightly`:** `sim_night_events` is untouched.
- **Performance:** no extra round trips. Spans add one insert per span, about 20 per batch.

## 7. Build waves

| Wave | Contents | Exit criteria |
|---|---|---|
| **T0** | SC-1 fix: `_ingest_state` treats SEALED-without-ABC as PARTIAL, which raises `SimRecoveryNeeded`; the Evaluator skips units of a batch with no ABC row (the second guard); seats re-run on resume behind a "no post yet from this seat" check | A crash-injection test that makes `record_abc` raise once, then restarts and decides, must produce no release. 420/291/47 holds. |
| **T1** | Trace schema; contextvar and `connect_as` options; spans at the §2 points; `runs/{id}` tree; unit trace; OTLP export; Tower trace drawer | The trace-shape digest is stable across fresh control planes |
| **T2** | `compose_profile` and the profile endpoints, with SC-5 labels; profile digest stamped on run roots | Profile `as_of` an old trace matches the configuration at that time |
| **T3** (after Part F F0) | Backtest CLI and reports; `backtest_report_ref` in the change-request impact; the demo script | The demo script runs end to end |

**Demo script:** wire the consuming DAG from a dbt manifest → bind it advisory → run a night → open the trace → open the profile → backtest → promote with two change requests (the gate mode, then the ceiling from L1 to L2).

## 8. What to cut

- Per-unit spans: linked rows already cover them.
- A shadow unit lifecycle: replaced by advisory binding plus the sandbox backtest.
- A live OTel collector and SDK.
- Backtests over real history (SC-4).
- A new-source-domain DAG in this part (SC-2).
- A generic resumable-step engine: T0's guard plus run_events is enough.

### Critical Files for Implementation
- /home/claude/medicaid-dq-agent/dqagent/runtime.py
- /home/claude/medicaid-dq-agent/dqagent/engine/cp.py
- /home/claude/medicaid-dq-agent/dqagent/engine/evaluate.py
- /home/claude/medicaid-dq-agent/dqagent/intervention/publish.py
- /home/claude/medicaid-dq-agent/dqagent/cp/governance_schema.py

**Worth saving to memory:**
- The seal-to-ABC crash window fails open (`seal.py:90` vs `runtime.py:348`, read as passed at `modes.py:120-122`). It must be fixed before tracing.
- The "E6" label means different things in the blackboard and in the code.
- Monitor spec and bindings are file-sourced, not governed.