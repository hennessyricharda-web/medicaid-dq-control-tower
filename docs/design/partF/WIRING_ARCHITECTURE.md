# Part F architecture, revision 2 (in response to PARTF_REDTEAM)

**Provenance.** `/home/claude/medicaid-dq-agent` is not a git repository, so there is no commit SHA. I re-read it on 2026-10-23. The fix seat is editing files right now, and line numbers have already moved since draft 1 (for example, `app.py` moved by about 16 lines). Every file:line below is as of this read.

## 0. Corrections to draft 1

**Intake citation (I7).** Intake is hard-coded in `Runtime._file_plan`, at `dqagent/runtime.py:900-913`, with the pipeline and relation literals at 909-913. Draft 1's reference to 850-856 was wrong. In this part that code stays exactly as it is. Any pipeline wired in Part F is therefore display-only: "discovered, not evaluated".

**Draft 1 overstated how two-person approval works for HARNESS today.**
- HARNESS is two-person only because of the Python flag (`registry.py:176`, used at `:238`).
- The database function `gov_item_lower_only` returns TRUE for every kind except AUTONOMY, ENTITLEMENT, AGENT and CLASSIFICATION (`governance_schema.py:480-486`).
- So the database enforces two-person for HARNESS only when Python passes `p_loosening` (`governance_schema.py:146-149`).
- §3 moves this decision into the database.

**"No runtime change needed" is withdrawn.** Draft 1 claimed this, and the red team demoted it. `effective_mode` (`dqagent/hitl/modes.py:63-70`) and `dq_ctl.autonomy_tier` (`governance_schema.py:398`) both change to apply a per-binding ceiling (§3).

**Unchanged from draft 1:**
- The agent gates datasets, which are units sealed per relation per batch (`dqagent/engine/seal.py:57-81`), not orchestrator tasks.
- The released manifest is the guarantee (`docs/DESIGN.md:378-380`).
- DuckDB is the only data plane.
- Fixtures written without a live environment are SYNTHESIZED, not recorded.

## 1. Build order (adopting the red team's order)

1. **Close E1–E8 first.** The fix seat already has them (`docs/blackboard.md:1366-1376`). E5, counts leaking across scope, is a precondition for the scoping in §2.
2. **Shell, status strip and nav rail.**
3. **Split-pane Queue, Card and Look-back.**
4. **The Part F slice:**
   - the Graph and Catalog for the 2 real pipelines (`config/harness_w1.yaml:1-16`);
   - a demo dbt estate of about 64 nodes, with every non-w1 node marked "discovered, not evaluated" and its run and gate glyphs left empty, never ✓;
   - one SYNTHESIZED Airflow fixture;
   - Connections;
   - Drift.
5. **Deferred, interface-only with contract tests on SYNTHESIZED fixtures:**
   - the ADF, Databricks Workflows, Snowflake Tasks and dbt Cloud adapters;
   - the OpenLineage receiver;
   - every orchestrator shim;
   - binding-driven intake.

**Gate API recommendation: build the verdict, defer the HTTP endpoint.**

- **Build now:** a pure, read-only function `gate_verdict(binding, run)` and a Tower overlay that shows its result.
- **Defer:** the HTTP gate endpoint, together with its workload-identity tokens and the shims.

Reasons:

1. Nothing in the demo would call the endpoint. With the shims deferred, it would be an unexercised attack surface.
2. Its security depends on issuing workload-identity tokens scoped by pipeline and authority (§2). That is real IdP integration work and should arrive together with its first caller.
3. dbt already has a working gate for the demo: runner exclusion (`dqagent/harness/dbt_runner.py:22-40`).
4. Building the function now means the verdict semantics are written and tested once. The deferred endpoint then becomes a thin wrapper.

## 2. Gate semantics (fixes I3; the endpoint itself is deferred)

**The verdict reads the data-plane manifest only.** It uses `dq_rel.manifest` and `dq_rel.manifest_pointer` (`publish.py:55-59`).

| Verdict | When |
|---|---|
| PASS | Every unit of the bound datasets, for the batches matched to this run, is in the live manifest version |
| HOLD | Any such unit is HELD or REJECTED, or a drift on this binding is unratified |
| WAIT | Anything else, until `max_wait`; after that, `on_timeout` applies (default HOLD) |

`can_release` reasons are display-only and never produce PASS, which removes the race.

**Matching a run to batches** must be explicit on the binding. `correlate` is one of LATEST_SEALED (demo), LOGICAL_DATE_PERIOD or LINEAGE_PARENT (deferred). If the binding does not say, the verdict is HOLD.

**Deduplicating `gate_checks`.** Unique on `(pipeline_id, task_id, run_key, binding_version)`, with `first_at`, `last_at`, `polls int` and `verdict`. A poll is an `UPDATE ... SET polls = polls + 1`. A new row is written only when the verdict changes, and the change is appended to `gate_check_transitions`. The table grows with runs, not with polls.

**Token scope (deferred endpoint, specified now).**
- A token carries `gate:read:<pipeline>:<AUTH_X>`.
- The verdict is computed over all units, because correctness needs that.
- The response lists only units whose authorities fall inside the token's scope. Units outside it contribute a single `other_scope_blocking: bool`, with no counts and no keys.
- In-scope counts go through the 1–10 suppression in `dqagent/adapters/metadata/_common.py:9-14`.

**OpenLineage (deferred).**
- It needs a separate `lineage:write` scope that no gate token holds.
- A COMPLETE event can only *request* a seal. The seal still requires complete intake ABC (`runtime.py` `run_pipeline` → `record_abc` / `gating_abc_failed`) and the existing seal path.
- An event can never release anything.

## 3. Configuration model and two-person enforcement in the database (fixes C3)

**Kinds.**

| Kind | artifact_id | Content |
|---|---|---|
| PIPELINE | `ST:pipeline:<id>` | `{adapter, connection, scope{dag_id \| manifest_path}, platform, domains[], contracts, layer_map{node_id \| glob: RAW\|BRONZE\|SILVER\|GOLD\|OUTBOUND}, ratified_shape_hash}` |
| CONNECTION | `ST:connection:<id>` | `{adapter, base_url, auth{method: oidc_workload \| bearer \| m2m, secret_ref}}` |
| GATE_BINDING | `ST:gate:<pipeline>:<task>` | `{task_id, datasets[], mode: block \| hold_until_terminal \| advisory, correlate, max_wait, on_timeout: HOLD \| ESCALATE \| PASS_ADVISORY, release_ceiling: L1..L4 \| INHERIT}` |
| HARNESS (kept) | `ST:harness` | The `dependencies[]` graph only, so it is validated whole (`plan.py:65-66`) |

**Secrets.** `secret_ref` must match `^(env|vault|workload)://`. This is enforced twice: in Python in `_candidate` (`registry.py:384`), and in SQL in `gov_item_valid` (`governance_schema.py:432`). `gov_item_valid` is extended to the four kinds above and refuses literal values under the keys `token`, `password`, `secret` or `key`.

**Who decides the approver count: the database.** `gov_item_lower_only` (`governance_schema.py:480`) is extended as follows.

| Kind | Rule |
|---|---|
| HARNESS | Always FALSE. Two people, which now matches `registry.py:176` in the database itself. |
| PIPELINE | Always FALSE. |
| CONNECTION | Always FALSE. A redirected endpoint could report green. |
| GATE_BINDING | TRUE only for an **existing** binding where the PUT is provably tighter (all six conditions below). |

The six conditions for a one-approver GATE_BINDING change:

1. new `datasets` ⊇ old datasets;
2. `tier_rank(release_ceiling)` ≤ old (INHERIT counts as the agent's tier);
3. `max_wait` ≤ old;
4. `on_timeout` is unchanged or moves to HOLD;
5. `mode` is unchanged or moves from advisory to block;
6. `correlate` is unchanged.

Everything else on GATE_BINDING is FALSE: DELETE (removing a binding), a brand-new binding, and any loosening. This matches the existing rule that a brand-new AUTONOMY is not lower-only. Both the propose step (`:146-149`) and the apply step (`:209`) already route through `gov_items_lower_only`, so no other SQL changes.

**New bindings start at L1 (the C3 widening).**
- `release_ceiling` defaults to L1.
- `dq_ctl.autonomy_tier` (`governance_schema.py:398`) returns the minimum of the agent's tier and the ceilings of every binding covering the unit's relation. `modes.effective_mode` traces the result as "binding ceiling Lx".
- Raising a ceiling is not lower-only, so it is a two-person promotion change request. It carries a track-record snapshot, as AUTONOMY promotions do (`registry.py`, `track_record_snapshot`).
- `INHERIT` is accepted only when the origin is GENESIS. This is checked in SQL in `gov_propose` (`governance_schema.py:140`).

**Genesis.** `seed_from_files` (`registry.py:112-136`) splits `harness_w1.yaml` into:
- 2 PIPELINE items;
- 5 GATE_BINDING items: the 4 w1 relations, plus 2 `hold_until_terminal` bindings taken from `dbt_runner.py` `HOLD_UNTIL_TERMINAL`, grouped by consumer task;
- a HARNESS item holding only the dependencies.

All are seeded with `release_ceiling: INHERIT`. `cfg_from_artifacts` (`registry.py:139-149`) composes the same harness dictionary, so `runtime._load_effective`, `plan.py` and `pipelines_view` (`runtime.py:411`) see identical input.

**Impact block** for any wiring change request:
- gates added and removed, and datasets removed, per binding;
- ceiling changes;
- `plan.validate(h, dag_nodes=snapshot, trigger_rules=snapshot)` errors, which block the request;
- `order()` before and after;
- the connection's latest probe result.

## 4. Connections, schedules, drift and the explicit dataset-to-task model (fixes I4 and I5)

**Connections**
- **Listing:** `GET /api/v1/catalog/connections` returns the adapter, host, auth method, `secret_ref` scheme only (never the value), last probe, and the pipelines that use the connection.
- **Probe:** `POST /api/v1/catalog/connections/{id}/test` runs one read-only call.
  - Airflow: `GET /api/v2/version`, then `GET /api/v2/dags?limit=1`, authenticated with a Bearer token from `/auth/token` ([Airflow 3.1 API](https://airflow.apache.org/docs/apache-airflow/3.1.0/security/api.html)).
  - dbt files: stat the file, then check the manifest schema version.
  - It returns `{reachable, auth_ok, api_version, capabilities[], latency_ms, provenance: LIVE|SYNTHESIZED, probed_at}`.
  - Secrets are resolved inside the process and are never returned or logged.
  - The host must be in a governed egress allow-list, the `org_settings` key `egress_hosts`, which blocks server-side request forgery.
  - Results are appended to `connection_probes`.
  - Only callers with governance duty may probe.
- **Changing a connection** is always a two-person change request (§3).

**Schedules and cadence**
- The orchestrator's schedule is read only. It comes from Airflow `schedule` / `timetable_summary` or a Databricks cron and is shown on the node and catalog views.
- The expected-delivery cadence stays governed in FEEDS (`config/state/ZZ/feeds.yaml`, `cadence.every`; classification at `registry.py:259`).
- A mismatch between schedule and cadence opens an advisory `SCHEDULE_MISMATCH` drift item that never gates anything. Expected deliveries remain on their own screen, not under Nightly runs.

**Drift lifecycle (fixes C4)**

| State | Meaning |
|---|---|
| OPEN | A new `shape_hash` differs from `ratified_shape_hash`, with added, removed and rename candidates. Rename candidates are a suggestion only. |
| DRAFTED | The discovery service writes a **metadata-only** draft into `dag_drift.draft`: proposed binding re-points or removals. It is not a change request; there is no AGENT origin and no action class. |
| SUBMITTED | A named person with governance duty submits it through `/api/v1/governance/change-requests` (`app.py:946`) with origin **PROPOSER**. This follows the pattern at `app.py:1010-1044` and is allowed by `governance_schema.py:140`. The request's `cr_id` is linked. |
| RESOLVED | The request is applied and the snapshot is ratified. |
| DISMISSED | A person closes the item. Only allowed when no gated task is affected. |

While drift is OPEN with gated impact, the verdict is HOLD and the Tower shows a DRIFT banner.

Drift endpoints:
- `GET /api/v1/catalog/drift`
- `GET /api/v1/catalog/drift/{id}`
- `POST /api/v1/catalog/drift/{id}/draft` (service only)
- `POST /api/v1/catalog/drift/{id}/dismiss` (person, step-up)

**The dataset-to-task model is explicit in the API**
- `GET /api/v1/catalog/pipelines/{id}/graph` returns `tasks[]`, `datasets[]` and `edges[{task→dataset: produces | dataset→task: consumes | task→task: control}]`.
- A dbt model is one task producing one dataset of the same name.
- Bindings are listed as `(task, datasets[])`.
- In the wizard, the person picks datasets first. The tasks that consume them are shown as suggestions and must be confirmed explicitly.

**Wizard rules**
- **No pre-hook option.** dbt gating is runner exclusion only (`DESIGN.md:373`).
- **Layers are never inferred.** They come from `PIPELINE.layer_map` or an explicit `meta.dq_layer` declared in the dbt project. Anything else is UNLAYERED, so folder names are not used.
- **Airflow input is the REST API or serialised JSON of its responses only.** A `.py` DAG file is never accepted, because parsing one means executing it.
- **Rendering:** zero-dependency SVG with an Outline/table toggle that can be the default and carries gate state. React Flow is not used (I6).

**Other endpoints:**
- `GET /api/v1/catalog/pipelines`
- `GET /api/v1/catalog/pipelines/{id}/nodes/{node}`
- `POST /api/v1/catalog/discover`: a preview that writes nothing
- `POST /api/v1/catalog/wire`: builds the items and calls `Registry.propose` with the person as author

The existing `/api/v1/pipelines` (`app.py:602`) is unchanged.

**Control-plane tables.** In a new `dqagent/cp/catalog_schema.py`, behind RLS, with a new `dq_discovery_svc` role that can write snapshots, runs, drift and probes only:
- `dag_snapshots`
- `dag_runs`
- `dag_task_runs`
- `adapter_cursors`
- `gate_checks`
- `gate_check_transitions`
- `connection_probes`
- `dag_drift`

## 5. Waves and blast radius

| Wave | Contents | Exit criteria |
|---|---|---|
| F0 (after E1–E8 and the UX waves) | The kinds; the kind CHECK constraint (`governance_schema.py:27-28`); SQL `gov_item_valid` / `gov_item_lower_only` for HARNESS, PIPELINE, CONNECTION and GATE_BINDING; binding ceiling in `autonomy_tier` and `effective_mode`; genesis split with INHERIT; composition | Full suite green; **`tests/t1/test_w1_outcomes.py` stays at 420/291/47**. This holds because genesis bindings are INHERIT, the composed harness equals the YAML (a new equality test), and intake (`runtime.py:909-913`) is untouched. A SQL matrix test proves the lower-only rules; a test proves HARNESS needs two approvers in the database without the Python flag. |
| F1 | dbt files adapter and refactor of `dbt_runner.py` to read bindings, with an identical held set; SYNTHESIZED Airflow fixture adapter; Connections with the probe and egress allow-list; discovery tables; catalog, graph and node APIs; demo estate `pipelines/demo_estate` generated by `dbt parse` and wired by a post-genesis change request, not seeded | Demo estate visible with non-w1 glyphs empty |
| F2 | Drift detection and lifecycle; `gate_verdict()` with deduplicated `gate_checks`; Tower Catalog, Graph and Outline, Connections and Drift | A drift injected on a fixed nightly-simulator night is detected |
| Deferred | The HTTP gate endpoint and tokens; shims; OpenLineage; the ADF, Databricks, Snowflake Tasks and dbt Cloud adapters; binding-driven intake | None in this part |

**Files touched:**
- `dqagent/governance/registry.py:35, 112-176, 228-238, 384`
- `dqagent/cp/governance_schema.py:27-28, 140-149, 209, 398, 432-486`
- `dqagent/hitl/modes.py:63-70`
- `dqagent/harness/dbt_runner.py`
- `dqagent/cli/main.py:30-34`
- `dqagent/api/app.py`
- Tower `lib/types.ts` and `lib/api.ts`

**Tests at risk:**
- `tests/t1/test_w1_outcomes.py`: pinned at 420/291/47
- `tests/t0/test_harness.py`
- `tests/t0/test_parte_registry.py:36-39`
- `tests/t0/test_parte_export.py`
- `tests/t1/test_dbt_gate.py`
- `tests/t1/test_cp_invariants.py`: the hostile-role sweep must cover the new tables and role
- `tests/t1/test_api_contract.py`
- `tests/t1/test_governance*.py`

**Genesis approver count.** Making HARNESS two-person in the database does not change it: genesis already needs 2 approvers because it creates brand-new AUTONOMY items (`governance_schema.py:476`). This should still be confirmed by a run before F0 is called done.

## 6. Where the ask and the design remain wrong

1. **"Wire in real DAGs" delivers discovery, display, drift and governed bindings. It does not deliver evaluation.**
   - On a real estate, evaluation needs binding-driven intake plus a data-plane adapter for Snowflake, Databricks or BigQuery. Neither is in this part.
   - The Tower must say "discovered, not evaluated", and the evaluator answer is: display yes, gating not yet.
2. **Matching a run to batches is an open design problem for real estates.** LATEST_SEALED only works for the demo.
3. **Part F should not start until E1–E8 have a closing receipt.** The blackboard shows only E2 closed in the Tower.

### Critical Files for Implementation
- /home/claude/medicaid-dq-agent/dqagent/cp/governance_schema.py
- /home/claude/medicaid-dq-agent/dqagent/governance/registry.py
- /home/claude/medicaid-dq-agent/dqagent/hitl/modes.py
- /home/claude/medicaid-dq-agent/dqagent/harness/dbt_runner.py
- /home/claude/medicaid-dq-agent/dqagent/api/app.py

**Worth saving to memory:**
- Today the database enforces two-person approval for HARNESS only through the Python `p_loosening` flag. `gov_item_lower_only` returns TRUE for the other kinds.
- Line numbers in `app.py` and `runtime.py` are moving while the fix seat works, so re-anchor before building.