# Part F design: execution traceability, the per-DAG agent profile, a Tower density redesign, a Pipelines workspace, and trialling a new DAG

**Status:** APPROVED for build on 2026-10-23 (DEC-021), in the wave order below.
- The design was reviewed by two adversarial rounds. Supporting documents are in `docs/design/partF/`.
- The clickable wireframes sit outside the repo, at `C:\demo\partF_design_pack\`.
- Naming is generic to the DQ estate. Technology names (Snowflake, Databricks, BigQuery, Airflow, dbt, ADF) may appear; client and agency names may not (DEC-020).

## Why
The user's assessment, confirmed by the red team (`EXECUTION_GAP_ASSESSMENT.md`), was that the agent looked weak on execution and traceability:
- a "run" existed only in the simulator;
- there was no step-by-step trace;
- the decision card had stubs;
- per-DAG configuration was scattered across files;
- there was no safe way to trial a new DAG.

Two correctness defects were also found:
- an unrecognised inbound file was routed to claims (fail-open);
- a crash between seal and ABC let a batch read as "ABC passed".

The first Part F draft (density and pipelines) was aimed at chrome, and is re-ordered here.

## Waves (in order, each with red team, live smoke and a receipt; check in with the user between waves)

| Wave | Content | Source |
|---|---|---|
| **T0** | Fix the defects:<br>• SEALED-without-ABC is treated as PARTIAL, so recovery is needed;<br>• the evaluator skips units of a batch that has no ABC row;<br>• seats re-run on resume;<br>• intake routing is driven by the feeds config, and an unmatched file opens a feed item instead of reaching claims;<br>• crash-injection test: no release after a restart. | EXECUTION_TRACE_ARCHITECTURE §0 SC-1, §7 T0; gap assessment #4 |
| **T1** | Run traces:<br>• `runs`, `run_events`, `spans`, `release_checks` tables;<br>• `trace_id`/`span_id` correlation columns, set through the connection setting and kept outside every hash;<br>• spans at the documented points;<br>• deterministic ids;<br>• `GET /runs`, `/runs/{trace_id}` (tree), `/runs/{id}/otlp`, `/units/{key}/trace`, scoped by authority (E5 lesson);<br>• Run detail screen and trace drawer;<br>• the decision card stubs fixed (metric/threshold, dependency, dry run, SLA due);<br>• the trace-shape digest is stable across fresh control planes. | EXECUTION_TRACE_ARCHITECTURE §1–5 |
| **Shell** | 44 px header, 28 px status strip (exception-first, "All 11 signals" popover), grouped collapsible left rail, Nightly runs under Operate; dense split-pane Queue, Card and Look-back. | LAYOUT_SYSTEM.md, AUDIT.md |
| **T2** | Agent profile per DAG: `compose_profile(pipeline, as_of)`; the profile digest stamped on every run root; "edit" drafts a change request; anything not governed is labelled (monitor specs, intake routing). | EXECUTION_TRACE_ARCHITECTURE §3 |
| **Pipelines** | Catalog plus node-level graph (React Flow, DEC-020) with a Table view; the demo dbt estate (non-w1 nodes "discovered, not evaluated", with empty glyphs); a wiring wizard that starts ADVISORY; PIPELINE / GATE_BINDING kinds with database-enforced two-person approval; newly bound datasets start at release ceiling L1. | PIPELINES_UX.md, WIRING_ARCHITECTURE.md §3 |
| **T3** | Backtest CLI in a sandbox control plane (baseline vs candidate, would-hold / would-release, rule hit rates, labelled SYNTHESIZED), with the report ref in the change-request impact. Promotion is two separate two-person change requests: gate mode advisory→block, then release ceiling L1→L2. | EXECUTION_TRACE_ARCHITECTURE §5, SC-3 |
| **New DAG demo, part 1 (consumer)** | A new dbt project that consumes already-gated datasets (e.g. a T-MSIS outbound build): wire → advisory → run a night → trace → profile → backtest → promote. Every step is real. | DEC-021 |
| **New DAG demo, part 2 (new source)** | Intake routing and parsers driven by YAML (DEC-012(b), "a new pipeline added by YAML only"), demonstrated by onboarding a new feed type. | DEC-021 |
| **Later** | Connections screen, DAG drift, Airflow discovery (SYNTHESIZED fixture); the HTTP gate endpoint and orchestrator shims; ADF, Databricks, Snowflake Tasks and dbt Cloud adapters as interfaces only; data-plane adapters for Snowflake / Databricks / BigQuery (a separate workstream; without them, real-estate nodes stay "discovered, not evaluated"). | WIRING_ARCHITECTURE.md §5 |

## Invariants
- `tests/t1/test_w1_outcomes.py` stays at 420 / 291 / 47.
- The simulator invariants (K=15, K=0) hold.
- Agent-eval digests are unchanged. A new trace-shape determinism test is added.
- Tracing only observes. It never changes an outcome.
- No path wires, un-gates or raises autonomy with fewer than two people. The database enforces this.
