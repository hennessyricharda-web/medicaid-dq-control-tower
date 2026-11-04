**Bottom line: no, not today.** A presenter can show what a night counted and why one unit is held. They can't show what the agent did, step by step, or bring a new DAG through a safe trial into gating. The tree is not a git repository, so I have no commit SHA. Every citation below refers to the working tree as read on 2026-10-23.

## Frame question

The user is asking how they can prove what the agent did and safely add a new DAG to it. Part F answers a different question: how to show the pipeline estate. Of its 15 wireframes, only 05 (nightly runs) and 13 (blackboard) touch execution. None covers a run trace, a per-DAG agent profile, or shadow-then-promote. Most of the Airflow surface is `SYNTHESIZED` fixture data (PIPELINES_UX.md:18). **The frame does not match the concern.**

A deeper problem sits underneath. **A "run" exists only in demo mode.** `sim_runs` holds one row per simulated night (governance_schema.py:916-920), and the sim endpoints return 404 outside demo (app.py:1243). `batches`, `units` and `results` have no run_id column (schema.py:141-228). In production there is nothing to hang a trace on.

## Gap table

| Capability | Status | Evidence | Demo needs |
|---|---|---|---|
| Run entity outside demo | ABSENT | Searches: (1) grep `run_id` hits only runtime/app/governance_schema/llm template/legacy_totals, all sim or parallel-run; (2) no run column in schema.py:141-228 | A `runs` table, with a run_id stamped on batches |
| Per-step trace (timings, inputs/outputs) | ABSENT | (1) grep otel/tracer/span_id in dqagent finds only `getLogger` at cli/main.py:132; (2) grep trace/span in tower/src finds only `mode_trace` | A step ledger: run → file → batch → seal → evaluate → mode → publish → veto |
| Night summary | PARTIAL | runtime.py:1226 and 1156-1165 hold counts plus ids only; nightly page links to whole screens, not records (nightly/page.tsx:17-27) | A drill-down from night to unit to rule result |
| Run-detail route | ABSENT | (1) the API exposes only `/sim/runs/{id}`, a summary (app.py:1270); (2) no `/nightly/[id]` or `/runs` route among tower/src/app pages | A run-detail page |
| Unit "why" card | PARTIAL, with stubs | Real `mode_trace` (app.py:433). Hard-coded: `metric/threshold: None` (438), dependencies always `ELIGIBILITY`/`WAIT` (442), `sla_due = created_at` (451), fabricated `dry_run` (445-446). No `unit_events` timeline, no ruleset_version, no manifest version | Fix the stubs, then add timeline and versions |
| Rule hash → approving CR | PARTIAL (in the database, no API) | results.rule_hash (schema.py:224); registry_events.decision_ref (266-269); config_artifacts.cr_id (governance_schema.py:29) | A join exposed per result |
| Unit → manifest version | PARTIAL | Only by scanning the `manifest_versions.content` jsonb (publish.py:283-316) | Index it and return it on the card |
| Downstream consumer record | ABSENT | (1) grep consumer/read_receipt in dqagent finds nothing; (2) no such table in the CREATE TABLE list | Out of scope for the demo; say so on screen |
| Replay / re-evaluation | PARTIAL | Determinism is proven only inside tests (agent_eval/README.md:10). At runtime the only re-evaluation is reopen-on-config (runtime.py:299-307) | A replay endpoint into a scratch schema |
| Per-DAG agent profile | ABSENT (scattered) | harness_w1.yaml (gated nodes, dependencies), contracts_w1, policy_w1, feeds, elements, overlays, agents.yaml. Tier is set per authority, not per DAG (agents.yaml:23-26; governance_schema.py:398-403) | A read-model that assembles them |
| PIPELINE / GATE_BINDING / CONNECTION config kinds | ABSENT (design only) | (1) grep count is 0 in dqagent; (2) the kind CHECK at governance_schema.py:27-28 and KINDS at registry.py:35 exclude them | Wave-1 build |
| File → pipeline routing from config | ABSENT | (1) `_file_plan` hard-codes filename prefixes, and an unrecognised file falls through to `p6_claims` (runtime.py:908-913); (2) intake formats IE/834/837P are fixed at the same site | Routing table in feeds config; an unmatched file becomes a feed item |
| Shadow / dry-run for a new DAG | ABSENT | (1) grep shadow/backtest/observe in dqagent finds only the fake dry_run at app.py:446; (2) grep across the Part F .md files finds nothing. Part F's "new bindings start at L1" (PIPELINES_UX.md:129) still holds every unit for a person, so it gates rather than shadows | A SHADOW mode |
| Isolated DAG addition | ABSENT | A HARNESS change is structural and reopens every non-terminal unit on every pipeline (runtime.py:318-320). The harness is one artifact per state (registry.py:125-126) | One artifact per pipeline, with scoped reopen |
| Partial-failure resume | PARTIAL | Works only with `record=True`, i.e. in the simulator (runtime.py:1179-1209). A partly ingested batch stops the night, and app.py has no recovery route | Recovery endpoint |
| Concurrency | PARTIAL | An in-process RLock (runtime.py:142-158), plus an advisory lock for sim only (1310). Nothing guards two runtimes | An advisory lock per pipeline |
| SLA per DAG | ABSENT | (1) SLA is keyed by mode, not pipeline (app.py:301-308); (2) card `sla_due` is a stub (451) | Per-pipeline SLA in harness config |
| Outbound alerting | ABSENT | (1) grep webhook/pager/smtp finds nothing; (2) PARTE_DESIGN.md:206 describes a Tower banner only | One webhook sink |

## Highest-value additions, ranked by demo value per effort

1. **Fix the unit card stubs and add an evidence-chain endpoint (S).** `GET /units/{k}/chain` would return: file sha → batch → each result's rule_hash, artifact version and cr_id → `unit_events` timeline → mode_trace → decision with explanation_ref → first manifest version containing the unit → audit seq. Almost all of this data already exists. It answers "why" in one screen.
2. **Run entity plus step ledger (M).** Add `dq_ctl.runs` and `dq_ctl.run_steps(run_id, seq, step, pipeline, batch, unit, seat, started, ended, in_digest, out_digest, ref)`, written from `run_night`, `run_pipeline` and `verify_and_publish`. Make run_id real in production, not `SIM-` only.
3. **Run-detail page (S once item 2 exists).** A timeline per file and batch, drilling through to item 1 and back up to the DAG node.
4. **Config-driven intake routing (S; a correctness fix as well as demo value).** Today an unknown feed is silently routed to the claims pipeline, a fail-open defect. Without this fix you cannot add a DAG at all.
5. **One artifact per pipeline, with scoped reopen (S-M).** Wiring a new DAG must not reopen every in-flight unit across the state.
6. **SHADOW gate mode plus replay (M-L).** Add `mode: SHADOW` on the pipeline. It evaluates and records everything but never HOLDs, never becomes a publish candidate and never enters the manifest. Replay runs historical nights into a scratch schema and produces a comparison report. Promotion from SHADOW to GATING is a two-person CR carrying the shadow track record. This is the whole "add a DAG and test it" story.
7. **Agent profile read-model (S).** `GET /pipelines/{id}/profile` assembles gated nodes, contracts, rules per relation, overlays, cadence, dependencies, monitors, and tier. The tier must be labelled "per authority", which is honest.
8. **Recovery endpoint, per-pipeline SLA and one alert webhook (M).** This is what evaluators ask about.

## What Part F must change

- Add three wireframes: **Run detail / trace**, an **Agent profile** tab on the pipeline and node panel, and a **Shadow results and promote** screen.
- Rework the wiring wizard (PIPELINES_UX.md:99-133):
  - Add steps for **intake routing** and **contracts/rules for the new dataset**. Step 4 says a dataset without a contract "cannot be gated" but gives no way to author one.
  - Add a **SHADOW start**. L1 is not safe testing; it is gating with a human in every loop.
- Move Connections (10), DAG drift (12) and the Airflow discovery surface to a later wave. They rest on synthesized data and do not answer the user.
- ARCHITECTURE.md:114 assumes per-binding tier ceilings. The current tier function is per authority only (governance_schema.py:398-403). The design should say so plainly or schedule the build.

## What this work missed entirely

- Production has no notion of "a run", and the demo makes it look as if it does.
- The hard-coded fail-open intake default (runtime.py:913).
- The blast radius of a HARNESS change: adding one DAG reopens every in-flight unit.
- Nothing records what downstream consumers read.

**For memory:** the medicaid-dq-agent run concept is demo-only (`sim_runs`). Intake routing is hard-coded with a claims fallback. Tier is per authority. Part F has no trace or shadow design.