# Part C design: basic DQ across all domains, measures, agent state/blackboard/memory, deterministic agent evals

Status: BUILT 2026-10-22. The pre-build red team changed the design; see §6 for what was built. Naming is generic to the DQ estate. The exemplar
state pack is `STATE_A`, and no client or organisation names appear anywhere.

## 1. Basic DQ first, for every domain (WS1)

Basic data quality is the floor. CMS measures sit on top of it, and the agent must never be measure-only.

### 1.1 Element contracts (`rules/elements.yaml`)

Each canonical element may carry a `contract`:

```yaml
CLM.FREQ_CODE: {entity: ENCOUNTER_HEADER, type: str, x12: "2300/CLM/05-3",
                contract: {required: true, values: ["1", "7", "8"]}}
ELG.EFF_END:   {entity: ELIG_SEGMENT, type: date, contract: {after_or_eq: ELG.EFF_START}}
```

Each entity may carry `keys` (uniqueness within a batch) and `refs` (referential: element → entity.element).

### 1.2 Templates (`dqagent/rules/basic.py`)

The templates generate catalog rules in memory. The output is deterministic: rules come out sorted by entity, then element, then dimension. Every generated rule passes through `compile_rules` UNCHANGED, so state binding, feed applicability (NOT_APPLICABLE), overlays (waive / tighten / narrow) and the effective hash all apply to it. Each rule's `template_version` is part of its semantic hash.

| Dimension | Template | Generated rule id |
|---|---|---|
| completeness | `required` → value is not null and not blank | `B-CMP-<EL>` |
| validity | `pattern`, `values`, `min`/`max`, `max_len` (checked only when the value is present) | `B-VAL-<EL>` |
| validity (code set) | `codeset` → `codeset_valid`; NOT_LOADED until Part E | `B-CS-<EL>` |
| uniqueness | entity `keys` → no other row in the batch shares the key | `B-UNQ-<ENTITY>` |
| referential | `refs` → the target row exists (released view for cross-feed refs) | `B-REF-<EL>` |
| consistency | `after_or_eq` / `before_or_eq` / `le_sum` between two fields | `B-CON-<EL>` |
| timeliness | `lag_max_days: {from: EL, to: EL}` | `B-TML-<EL>` |
| volume / reconciliation | unchanged: the existing ABC hops (FILE→RAW→LANDING) | n/a |

### 1.3 Domains

Domains are declared as entities, together with the feeds they arrive on.

- **Delivered today (synthetic):** professional encounters (837P header and line), IE eligibility (all HHS programs, including SNAP and TANF), and 834 enrollment.
- **Declared, with no synthetic feed yet:** institutional encounters (837I), dental (837D), pharmacy (NCPDP), prior authorization (278), the provider file, TPL / coverage, and the case and household records of non-Medicaid HHS programs.
  - These carry contracts like every other domain. Their feed is `delivered: false`, so their rules are NOT_APPLICABLE: never a pass, never a hold.
  - **Change in compile order:** the feed check now runs BEFORE the relation lookup, so an undelivered domain reads NOT_APPLICABLE rather than NOT_BOUND.

### 1.4 Basic-DQ coverage

`basic_coverage(rules)` returns domain × dimension × status. It is exposed on `/api/v1/coverage` under `basic`.

## 2. Measures (WS2)

We run 11 CMS measures plus one longitudinal stretch measure. The list is in `docs/blackboard.md` under REFERENCE-CODEBASE ASSESSMENT.

- **IR additions:**
  - a measure-level `distinct: <EL>`, which counts distinct values in the numerator and denominator;
  - a `longitudinal` block, `{tolerance, compare: relative|pp, period_key: [EL..], prior: released}`. The value is compared with the most recent prior RELEASED period value for the same key, taken from `dq_ctl.measure_results`. When there is no prior period, the result is NOT_EVALUATED.
- **Measures on child relations:** measures on the line relation are evaluated with their parent root batch.
- **New elements:** `LINE.ADJUD_DATE` (2430 DTP*573) and `LINE.PAID_AMT` (2430 SVD02).
- **Snowflake:** the emitter decorrelates. A measure becomes `COUNT_IF` over a `LEFT JOIN` of pre-aggregated keyed subqueries, so there is no correlated subquery in the SELECT list. A `sqlglot` parse check runs per dialect.

## 3. Agent state, blackboard, memory (WS3, control plane)

- **`dq_ctl.agent_blackboard`**
  - Append-only and hash-chained: each entry stores `prev_hash` and `entry_hash`.
  - Fields: `seat`, `kind` (FINDING | HYPOTHESIS | DECISION_REF | NOTE), `subject`, `body jsonb`, `refs`.
  - The ONLY write path is `dq_ctl.bb_post()`, a SECURITY DEFINER function. It derives `seat` from `session_user` (the service role), so attribution cannot be forged. There are no UPDATE or DELETE grants. Humans read through the API.
  - The services that post are intake, eval, publish and the steward (human notes, via the API role).
- **`dq_ctl.agent_memory`**
  - `BASELINE` (system, advisory): derived deterministically from RELEASED measure results.
  - `KNOWN_ISSUE` (human):
    - The status lifecycle is PROPOSED → ACTIVE → EXPIRED.
    - A KNOWN_ISSUE needs two persons (approver ≠ proposer), a step-up, and an expiry date.
    - It ANNOTATES a decision card and NEVER changes a disposition or a mode. This is the no-loosening rule.
  - `PRIOR_DISPOSITION` (system): the outcome of the last human decision per (rule, submitter), recalled on the card.
- **`dq_ctl.agent_state`** (a view): per batch, the units by state, open decisions, blackboard counts and memory hits.
- **Triage seat:** deterministic hypothesis templates, posted as HYPOTHESIS. Examples:
  - member-not-found failures concentrated on one submitter while that submitter's IE units are HELD;
  - NPI failures confined to one interchange.

  The LLM stays on NullProvider, and any LLM output would be labelled advisory.

## 4. Deterministic agent evaluations (WS4)

- **Scenarios:** `tests/agent_eval/scenarios/*.yaml`. Each one gives a fixture recipe and its expected:
  - rule dispositions;
  - measure dispositions;
  - unit end states;
  - blackboard entries (seat, kind, subject);
  - memory effects;
  - release outcome.
- **Runner:** builds a canonical, timestamp-free and UUID-free digest of everything the agent emitted. Each scenario runs twice; the two digests must be identical and must match the golden file.
- **Negative controls:** some scenarios contain no defect and must produce zero FAIL. In one scenario, an attempt to forge a blackboard seat must fail. In another, a KNOWN_ISSUE that tries to change a disposition must fail.

## 5. Naming guard (WS0)

`tests/t0/test_generic_naming.py` hashes every word token (and bigram) in the text files the repo tracks with SHA-256 and compares them with a list of banned hashes. The list stores hashes only, so the names never appear in the repo.

## Open questions for review

1. Should basic rules gate (hold units) by default, or should completeness/validity gate while uniqueness and referential are advisory?
2. Will adding basic rules change the clean-run outcomes (485 released / 242 held / 18 pending)? Every generated rule must be legitimate against the synthetic generator's clean data.
3. The timeliness template needs a receipt date element. None is bound in the current bronze relations, so it is declared now and activated where a state binds it.

## 6. As built (after the pre-build red team; docs/blackboard.md "PART C PRE-BUILD RED-TEAM")

**Basic DQ**
- **Generation.** `dqagent/rules/basic.py` generates the basic rules from the element contracts in `rules/elements.yaml`. Every declared domain has contracts.
- **Gating.** Generated rules are **advisory**: a non-PASS result is recorded as TA_WARN, which never changes a mode. The two-person overlay verb `promote` makes a rule gating.
- **Cross-domain rules.** The hand-written cross-domain rules live in `rules/catalog/basic/cross_domain.yaml`.
- **Undelivered domains.** These compile to NOT_APPLICABLE, with both reasons given. `basic_coverage()` shows them as DECLARED_NOT_EXERCISED and never as covered. It is exposed at `/api/v1/coverage` → `basic`.
- **Tests.**
  - `tests/t0/test_basic_templates.py` builds a micro-estate for every declared entity from its contracts alone and runs three checks:
    - the clean estate produces zero failures;
    - each generated rule fires on its single seeded mutation;
    - generation is deterministic.

**Measures**
- **Count.** 12 measures are compiled and ACTIVE in ZZ: the original 3, plus 8, plus the longitudinal stretch EXP-27-001-1. In STATE_A, EXP-27-001-1 is NOT_APPLICABLE because the plan's paid amount is not delivered.
- **IR additions.** Two keys were added:
  - `distinct: <EL>`;
  - `longitudinal: {tolerance, compare, period_key}`, compared against the prior fully RELEASED period of the same submitter. With no prior period, the result is NOT_EVALUATED.
- **Child relations.** Measures on a child relation (lines) run with their root.
- **Mapper.** The mapper now reads 2430 SVD02 (`paid_amt`) and DTP*573 (`adjud_date`).
- **EXP-27-001-1 citation is PARTIAL.** The CMS page gives the longitudinal threshold of 0.1 but not the basis of comparison. The estate convention used here is the absolute change in the ratio. Confirm this with CMS technical assistance.

**Decorrelated shapes (RED-1)**
- Two shapes were added:
  - `Emitter.unit_query_where_only`, used when the data plane dialect is Snowflake;
  - `Emitter.measure_queries`, used for measures in every dialect.
- `tests/t0/test_dialect_shapes.py` covers them:
  - on a dirty micro-estate in DuckDB, the WHERE-only shapes give results equal to the original shapes;
  - every Snowflake rendering parses with sqlglot.
- Nothing has been executed on Snowflake: there is no account.

**Agent state, blackboard and memory**
- **Where it lives.** `dqagent/cp/agent_schema.py` holds the tables, functions and views. The seats are in `dqagent/agent/seats.py`; memory is in `dqagent/agent/memory.py`.
- **API endpoints:**
  - `/api/v1/agent/state`
  - `/api/v1/agent/blackboard` (plus `/notes`)
  - `/api/v1/agent/memory`
  - `/known-issues`, `/{id}/challenge`, `/{id}/decide`
- **The card.** It gains `agent_board`, `advisory_warnings`, and `known_issues`, which are shown to the maker only.
- **The queue.** TA_WARN is excluded from the queue's failure signature.

**Deterministic agent evaluations**
- The scenarios are in `tests/agent_eval/scenarios.yaml`; the runner is `tests/t1/test_agent_eval.py`.
- There are six scenarios. Each also runs a determinism check over two fresh control planes.
- There is a seat-forgery negative control and a mutation-structure check.
