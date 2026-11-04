# Rule establishment: design (wave 2, slice 0)

**Status:** DRAFT for the pre-build adversarial review.

**Scope**, as the user confirmed on 2026-10-21:
- a cited catalog;
- a portable rule IR with DuckDB and Snowflake emitters (Databricks and BigQuery are wave 3);
- a two-person rule lifecycle;
- code sets with effective dates;
- a coverage report;
- STATE_A as the example state pack, with every state configurable.

## 1. Rule = cited, typed, versioned artifact

```yaml
id: X12-837P-CLM-001            # stable; pack prefix
version: 1                      # semantic_hash = sha256(canonical(rule minus version/status)) -> registry
title: Claim total equals the sum of service line charges
pack: federal/x12_837p          # federal/* | codeset | state/<ST>
domain: CLAIMS                  # CLAIMS | ELIGIBILITY | ENROLLMENT | PROVIDER | ...
stage: bronze                   # raw | bronze | silver | gold (where it is bound)
relation: bronze.encounter_header
dimension: consistency          # completeness | validity | consistency | uniqueness | timeliness | integrity
check_class: integrity          # existing mode/policy classes (unchanged)
priority: High
engine: IR                      # IR (portable predicate) | PARSER (computed by the ingest code; catalog holds citation + id)
applies_when: <expr>            # optional; rows outside it are NOT counted as evaluated
assert: <expr>                  # TRUE = conforms. fail = applies AND NOT assert. NULL = NOT_EVALUATED (never PASS)
params: {member_id_pattern: {type: regex_ere, from: state_profile}}   # bound per state
depends_on: [ELIGIBILITY]
citations:
  - {source_id: SA-CG-837P-2021-03, locator: "p.5, 2010BA NM109", note: "10-character ID ending in an alpha character"}
status_hint: DRAFT              # the REGISTRY is the source of truth for state, not this file
```

**A rule with no citation cannot be proposed.** Each citation's source_id must exist in `docs/rules/SOURCES.md`. A rule that cites an UNVERIFIED source can be proposed, and the Tower shows it with an "unverified source" badge. Approving it is a human decision.

## 2. IR expression grammar (closed set; no raw SQL)

| Group | Nodes |
|---|---|
| Scalars | `col`, `lit` (str/int/dec/date/bool), `param`, `coalesce`, `upper`, `trim`, `length`, `substr` |
| Comparison | `eq`, `ne`, `lt`, `le`, `gt`, `ge`, `between`, `distinct_from`, `in` (literal list), `is_null`, `not_null` |
| Boolean | `and`, `or`, `not` |
| Patterns | `matches` with a restricted ERE: literals, `[...]` classes, `{m,n}`, `?`, `*`, `+`, `|`, groups. No backrefs and no lookaround. Full-match semantics. |
| Domain functions (emitted as SQL, no UDFs) | `npi_luhn(expr)` in pure SQL; `codeset_valid(system, code_expr, date_expr, billable_only)` |
| Set / correlated | `exists(source, where)`; `count_of(source, where)`; `sum_of(source, expr, where)` |
| Sources | relation, or `latest_per(relation, key[], order[], where)` (row_number subquery), or `union_all(src...)` |

Correlation: inside a source's `where`, `col(name, of: outer)` refers to the evaluated row (`h`).

**Emitters:**
- DuckDB: `regexp_full_match`; aggregates via `count(*) FILTER`.
- Snowflake: `REGEXP_LIKE` (implicitly anchored); aggregates via `COUNT_IF`.

The evaluator asks the emitter for the whole per-unit aggregation query, not just the predicate.

**Golden tests:**
- Every catalog rule compiles to both dialects, compared against `tests/golden/<dialect>/<rule>.sql`.
- Snowflake SQL is EXECUTED under fakesnow on fixture rows, and its per-unit (evaluated, failed, undefined) must equal DuckDB's.
- Honest limit: fakesnow runs on DuckDB, so Snowflake-specific planner limits on correlated subqueries are T2-REQUIRED.

## 3. Lifecycle (CP registry, append-only)

```
PROPOSED --approve(person 1, step-up)--> PROPOSED(1/2) --approve(person 2 ≠ 1, step-up)--> APPROVED
APPROVED --retire(2 persons)--> RETIRED            PROPOSED --reject(1 person)--> REJECTED
```

- **Approval binding.** The approval challenge binds (artifact_id, semantic_hash, verb). Duty: `dq-duty-steward-rules` (token claim AND roster). The verifier attests each approval exactly as it attests unit decisions.
- **Evaluation.** Only APPROVED rules count toward mode and release. PROPOSED rules run in SHADOW: results are stored with `shadow=true`, visible in the Tower, and have no effect on release.
- **Release.** can_release clause 2 requires every non-shadow rule hash to be APPROVED with two attested approvals.
- **No auto-approval.** The test genesis no longer approves rules. Tests approve through the service (two people, step-up), and the TEST genesis only grants roster entries.
- **Code-set loads** are registry artifacts too (`artifact_kind=codeset`, semantic_hash = sha256 of the file), with the same two-person approval. An unapproved load is invisible to `codeset_valid`.

## 4. Code sets (DP reference schema `dq_ref`)

```
dq_ref.codeset(system, code, billable, eff_from, eff_to, description_hash, load_id, provenance)
```

**Provenance** values: `OFFICIAL_FILE` | `FIXTURE_VERIFIED` | `FIXTURE_SYNTHETIC` | `STATE_LOCAL`.

| System | Loader | Status |
|---|---|---|
| ICD10CM | CMS order-file layout (positions verified). FY = Oct 1 – Sep 30. Billable flag 1 = valid. | |
| HCPCS2 | Column map in config | layout UNVERIFIED |
| POS | | |
| NUCC_TAXONOMY | CSV | |
| NPPES_DEACT | NPI + deactivation date | |
| CPT | Licensed; ZERO rows | Returns NULL, so NOT_EVALUATED, never PASS |
| STATE_LOCAL | Per state pack. ZZ declares Z9001–Z9060 (the synthetic seat's CPT-shaped space) | `FIXTURE_SYNTHETIC` |

`codeset_valid` semantics: TRUE if a row exists with `eff_from ≤ date ≤ coalesce(eff_to, ∞)` (and billable when required). FALSE if the system is loaded and no such row exists. NULL if the system has no approved load, or if the code or date is NULL.

**Procedure-code system by shape:**
- `^[0-9]{4}[0-9FTU]$` → CPT
- `^[A-V][0-9]{4}$` → HCPCS2
- otherwise → STATE_LOCAL (state pack), and if not found there → FALSE

**R-ENC-008 (not_loaded) is retired.** It is replaced by:
- dx validity on the claim's first service date;
- procedure validity per line;
- POS validity;
- billing-taxonomy validity (NUCC);
- billing NPI not deactivated as of the service date.

## 5. State packs (configurable to any state)

```
config/state/<ST>/profile.yaml:  packs: [federal/*, codeset, state/<ST>]
                                 params: {member_id_pattern: ..., encounter_bht06: RP, ...}
config/state/<ST>/rules/*.yaml   state rules (same schema, pack state/<ST>, cited to that state's sources)
config/state/<ST>/codesets/*     STATE_LOCAL lists
```

**STATE_A pack** (cited to SA-CG-837P-2021-03, SA-BHP-ENC-MANUAL-2022, SA-CG-834O-2019-05, SA-ICD10-FY2026):
- member-ID shape;
- BHT06 = RP on encounters;
- billing taxonomy required;
- atypical provider must carry REF*G2;
- CN102 MCO paid amount present (STATE_A encounters report the paid amount in 2300 CN1, not only 2320 AMT*D);
- void/replacement F8 is a 16-digit TCN;
- 834 INS03 ∈ {001, 021, 024} and BGN08 = 2;
- submission timeliness (≤ 180 days from paid date, within 15 days of month close): **SHADOW only**, because the paid date is not yet mapped.

**ZZ pack:** the W1 synthetic state. Its STATE rules are re-homed here with citation `ZZ-SYNTHETIC-SPEC` (`docs/W1_SPINE.md`), honestly labelled as test-state rules.

**Activation:** a new state is a new folder plus a profile. No code changes.

## 6. Coverage

Requirements are traceable. `rules/catalog/expectations.yaml` lists checks the SOURCES require; each expectation is cited and maps to rule ids. Examples:
- "duplicate encounters are non-repairable" (STATE_A manual p.21);
- the TPI categories (SHO 18-008, fn. 7).

An expectation with no active rule shows as a **GAP**, never as a pass.

The API and Tower show a matrix of `domain × stage × dimension`. Each cell shows:
- active rules;
- shadow rules;
- gaps;
- last disposition counts.

## 7. Migration of the 29 W1 rules

| Group | Destination |
|---|---|
| 15 envelope/flat checks | `engine: PARSER` entries with citations (X12 envelope / ZZ spine) |
| 13 row rules | re-expressed in IR |
| R-ENC-008 | retired (see §4) |

R-ELG-002 uses `union_all(latest_per(batch rows), current-minus-batch-grains)`.

**Acceptance:**
- DuckDB evaluation results from the IR are identical to W1 on all 99 T0/T1 tests.
- The holdout7 blind run scores no worse: 42 detected and contained, 1 detected but released, 6 contained by the translator, 0 missed.

## 8. Out of scope (explicit)

- Databricks and BigQuery emitters (wave 3).
- Profiling-generated proposals (W4); the "propose" path is human or the agent here.
- Downloading real code-set files: neither the workspace nor the linked computer can reach cms.gov or nucc.org. Loaders are built and tested on fixtures; the operator supplies the files.
- The full T-MSIS TPI list: not public in the letters read.

---

## Revision 0.2: re-frame approved by the user (2026-10-21/22), Parts A and B as built

**User decisions recorded here:**
- Inventory first.
- Rules are configurable on whether they apply, because feeds can be incomplete.
- Checks run BEFORE the warehouse load wherever possible.
- The demo uses a ~100-measure CMS sample.

### Part A: requirements inventory (`rules/inventory/`)

| File | Contents |
|---|---|
| `federal.csv` | 129 rows: 104 sampled CMS T-MSIS measures + 25 X12 / code-set requirements |
| `state/STATE_A.csv` | 78 rows |
| `scope.csv` | domain × program, including NOT_SOURCED / NOT_INGESTED rows |

Every row carries `earliest_stage` (FILE / ROW / BATCH / REFERENCE / WAREHOUSE).

### Part B: rule model (`dqagent/rules/model.py`)

**Canonical elements.** Rules reference canonical elements and entities from `rules/elements.yaml`, never tables. Each element carries its X12 locator and, where read on a CMS page, its T-MSIS DD number. The state binds elements to physical names in `config/state/<ST>/elements.yaml`. The XX test fixture proves the same catalog compiles against different names with zero rule edits.

**Applicability.** `config/state/<ST>/feeds.yaml` declares the feeds and elements delivered. Applicability is decided by the compiled entity's own feed first, then by each element the rule needs. A rule that needs an undelivered feed or element is NOT_APPLICABLE: a recorded disposition, excluded from the roll-up and never a pass. A unit with nothing evaluable is still held as NOT_EVALUATED. Per-period windows are parsed but not yet applied at evaluation time (gap, Part D). STATE_A example: the managed care plan paid-amount rule is NOT_APPLICABLE until the non-public Encounters Submission Guide arrives.

**Overlays** (`overlays.yaml`): tighten / narrow / raise_priority / waive, each with a reason and an approval ref.
- Loosening a threshold or lowering a priority is refused.
- An expired waiver lapses.
- A waived rule records STATE_WAIVED.

**Rule kinds:**
- `row`: portable IR, unqualified columns = the evaluated row, source rows named.
- `measure`: population ratio against CMS thresholds. Recorded per batch in `dq_ctl.measure_results`; whether a breach affects unit modes is a policy decision, not wired yet.
- `file`: PARSER checks, with the parser build in their hash.
- `not_loaded`: code sets, until Part E.

**Effective hash** = H(semantic hash over the WHOLE rule minus presentation fields, the params the rule references, element bindings, relation, overlay, emitter version, parser build, thresholds). Results carry it and the registry approves it per (rule, entity). Changing any input to a rule's behaviour therefore needs re-approval (RED-2).

**Pre-load analogs.** `federal/tmsis_measures_preload.yaml` implements CMS measures on the INBOUND feed (RULE-884, RULE-7907, MIS-25-022-22, RULE-2105 family, RULE-7245). Each is labelled with `analog_of`, which records the orchestrator's interpretation of the mapping.

**Coverage** is computed against the inventory (`/api/v1/coverage`). Coverage states:
- ACTIVE: a full rule covers the requirement.
- ANALOG_ACTIVE: only a pre-load analog covers it.
- PARTIAL: an analog covers part of it.
- NOT_APPLICABLE / STATE_WAIVED / NOT_LOADED / NOT_BOUND.
- GAP: no rule.

A CMS measure is never closed by its analog; it stays open for the warehouse stage (Part C).

### Behaviour preserved

The 13 W1 row rules were re-expressed in the IR and still pass all W1 tests unchanged. Totals: 109 T0/T1 tests pass, and the clean synthetic run gives identical unit outcomes (485 released, 242 held, 18 pending).

### Part B review (one pass, GO-WITH-FIXES)

**Fixed before reporting:**
- Hash deny-list.
- The entity feed now decides applicability.
- Analog coverage is honest, and the wrong RULE-2105 mapping is removed.
- The NPI rule is split so a missing REF*G2 feed no longer disables the check digit.

**Open (Part C/D):**
- File-rule evaluation still hard-codes envelope column names.
- `narrow` overlays need their own status and an expiry; overlays on unknown rule ids must be rejected.
- One disposition per compiled rule per unit (NOT_BOUND entity-level); clause 2 keyed on rule@entity.
- Per-period feed windows; program / claim-type gating; the code-set version in the hash (Part E).

### Still to build

- **Part C:** warehouse emitters (Snowflake now; Databricks / BigQuery lowering on paper) for WAREHOUSE-stage requirements, including the decorrelated query shape (RED-1).
- **Part D:** two-person rule / overlay / profile approval with author exclusion (replaces TEST-GENESIS); release fails closed if a required approved rule did not run (RED-3); Tower Rules and Coverage screens.
- **Part E:** code-set registry and the qualifier-based system choice (replaces R-ENC-008).
