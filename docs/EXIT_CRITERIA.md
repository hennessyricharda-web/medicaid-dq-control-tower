# W1 exit criteria: honest status

This replaces the dangling "§14 first tests" reference in DESIGN §15. Each line carries a status, the test or artifact that proves it, and a receipt.

**Code under test:** the tree is not under git, so a content hash of `dqagent/ rules/ config/ pipelines/w1_dbt/models tests/` (see `tools/receipt.py`) stands in for the commit SHA.

**Labels:**
- **T1-verified deploy candidate** means it runs on DuckDB + Postgres 16 + a local IdP.
- **No platform is verified** (Snowflake / Databricks / BigQuery) until its T2 kit runs on a real account.

## Receipts

| Receipt | Result | Code hash |
|---|---|---|
| `docs/receipts/20261020T233907Z_w1-hardening.junit.xml` | 107 passed, 0 failed, 0 skipped | `bb3a9662…59c` (code under holdout scoring) |
| `docs/receipts/20261020T234702Z_w1-exit.junit.xml` | 107 passed, 0 failed, 0 skipped | `aaf3a317…6e5` (after the Tower-smoke fixes) |

Skipped tests never count as passed: if the T1 Postgres is unreachable, CP tests skip and the receipt shows it.

## Scope statement

- **Programs:** ALL HHS programs in the IE extract are ingested and EVALUATED. A unit is RELEASABLE only when every one of its authorities has a Tower scope, which in iteration 1 means MEDICAID and SNAP (can_release clause 9). Any other program fails closed with a NOT_IMPLEMENTED reason; it is never shown as PASS.
- **SNAP / TANF rule content:** not sourced. SNAP is held as a STRUCTURAL program (a human decides every unit).
- **Domains in W1:**
  - eligibility (IE extract)
  - enrollment (834)
  - MCO encounters (837P)

  Every other domain (all claim types, prior authorization, provider network, finance, TPL) is W2 breadth.

## W1 criteria (DESIGN §15)

| # | Criterion | Status | Evidence |
|---|---|---|---|
| 1 | T1 stack: state ZZ, DuckDB DP, Postgres CP, local IdP | PASS | all of `tests/t1` |
| 2 | MCO 837P encounters, loop-aware (2010BA / 2300 / 2320 / 2330A/B / 2400) | PASS | `test_w1_datapath.py::test_2330a…`, chain, two-MCO tests |
| 3 | Translator 999 | PARTIAL | Consumed as the spine §1.4 CSV summary keyed (isa13, gs06, st02), not a parsed X12 999. A missing ack = UNBALANCEABLE. |
| 4 | Translator 277CA | STUB (W2) | Not ingested. |
| 5 | Enrollment-broker 834 | PASS | `test_834_termination_of_unknown_member…`; exercised by both synthetic runs |
| 6 | IE extract (weekly series, supersession, duplicate-file detection) | PASS | supersession, duplicate, unparseable-date tests |
| 7 | raw → bronze (gated) → silver → gold via dbt over RELEASED views only | PASS | `test_dbt_gate.py` |
| 8 | Silver / gold as gated units | STUB (W2) | |
| 9 | T-MSIS ELG and COT subsets | PASS (subset) | ELG: the SNAP-exclusion test; COT: `test_cot_gold_carries_one_active_row_per_transitive_chain` |
| 10 | SEAL AS-OF | EMULATED (T1) | Copy plus a full-column keyed digest compared inside the copy transaction; FROZEN written only after COMMIT (`test_landing_mutated…`). Real AS-OF / time travel is T2-REQUIRED. |
| 11 | Keyed fingerprint | PASS | Every column except `_dq_unit_key`, written once (`test_fingerprint_covers_every_column…`, `test_units_born_landed_and_fingerprint_write_once`) |
| 12 | ABC from translator to gold | PARTIAL | FILE→RAW per X12 file (an untokenizable file fails the batch), TRANSLATOR (matched ack keys), RAW→LANDING (counts plus exact-Decimal CLM02 amounts; mapper rejections FAIL), LANDING→GATED. Silver and gold hops are STUB (W2). |
| 13 | One WAIT edge (CLAIMS waits on ELIGIBILITY), program-scoped, `max_wait` escalation | PASS (W1-specific join) | `test_encounters_wait…`, `test_dependency_wait_escalates…`. The config-driven resolver is W2 (SCP-W1-1). |
| 14 | Strict per-partition order for ORDERED domains | PASS | `test_per_partition_order…` (later versions wait; clause 11 defends in depth) |
| 15 | Chain from HOLD to DENY (see list below) | PASS | `test_w1_slice.py`, `test_api_contract.py` |
| 16 | dbt runner gate | PASS (node-level) | Holds are not yet scoped by authority or reporting period (SCP-W1-7, W2). |
| 17 | Tower queue / card / pipeline map / mode trace | PASS | build + lint exit 0; live Playwright smoke against the real API: maker release, SNAP release, 403 for unrostered and non-auditor, audit for auditor, no raw identifiers on the card |

Criterion 15 covers this chain:
- HOLD
- Decision Card
- challenge-bound step-up on EVERY decision
- verifier DECISION / TOKEN / FINGERPRINT / PUBLICATION attestations
- crash-safe manifest
- released-view hiding
- DENY

## Release-path invariants (adversarial review SEC-W1, all with hostile tests)

| Invariant | Test |
|---|---|
| Only the publisher reaches RELEASED. No human edge; the role↔verb binding is in SQL; the core function is executable by nobody. | `test_every_role_refused…`, `test_own_wrapper_refuses…`, `test_no_human_edge_reaches_released` |
| Units are born LANDED / v0; the fingerprint is written once | `test_units_born_landed…` |
| One connection per call, logging in AS the service role; no RESET ROLE escalation | `test_service_logins_cannot_reset…`, `test_concurrent_scoped_readers…` |
| Forged decision rows written by a compromised api never release (per-decision verifier attestation recomputes the challenge) | `test_forged_decision_rows…` |
| Step-up replay → 409; session bearer never decides; checker cannot change the verb or confirm someone else's decision | slice and API tests |
| INTAKE approver coverage when a pre-split hop fails (clause 7) | `test_intake_failure_needs…` |
| The written quarantine set equals the signed set; CHAIN quarantine takes the lines with their header | slice tests |
| A version is never reused after a crash between the DP commit and the CP record; clause 10 blocks releases until healthy; the record is back-filled | `test_manifest_crash…` |

## Stubs and accepted risks (fail closed unless stated)

1. **can_release clauses:**
   - Clause 2: DEPLOY_MANIFEST half missing.
   - Clause 6: no program breaker.
   - Clause 8: no FINAL-period model exists at W1.

   Clause 5 is satisfied by construction: there is no timed release, and H_ON needs an explicit decision.
2. **Scope is still passed to the database by the api** (`set_scope(text[])`, and the `dq.scope` GUC is settable). RLS now also covers decisions, quarantine, unit_events and deny. The rest of the fix (scope derived in SQL from a verified token) is W2 (SEC-W1-7).
3. **The T1 bootstrap login is a Postgres superuser.** It is used only by schema apply and the TEST genesis, which never runs unless `test_genesis=True` is passed explicitly. Services never use it.
4. **The verifier shares the process and an archived JWKS snapshot with the api.** It becomes a separate deployment with its own identity in W4.
5. **IdP session revocation and introspection (CAEP) are absent.**
6. **Reveal:**
   - Done: masked identity and clinical columns, including MSIS / member ID; row selection; a 15-minute expiry sweeper; audit stores only a hash of the purpose.
   - Not done: binding the view to the reviewer's own DP identity (grants) is T2.
7. **Decision rationale is free text in an append-only CP table.** PHI risk; W2 adds a structured rationale (SCP-W1-9).
8. **No SEAL recovery outbox.** If the DP commits and then the CP FROZEN write fails, the batch needs manual recovery. W2.
9. **Chain roots resolve within a file.** Resolution across batches against released history is W2. A DENY does not bump the dependants' state_version (ENG-W1-9).
10. **R-ENC-008 (codeset validity) is not loaded**, so every encounter is HITL (H_IN). A W2 codeset registry removes this floor.
11. **Not modelled yet:**
    - retro-eligibility re-evaluation of RELEASED encounters
    - the CMS T-MSIS feedback loop
    - MCO-enrollment-on-date-of-service (834 ↔ encounter)
    - Part 2 / FTI / tribal origin tagging
12. **Platform limits:**
    - BigQuery landing immutability is detective only.
    - Append-only landing and grants are not enforceable in DuckDB (T2-REQUIRED).
13. **The harness is still W1-specific** (SCP-W1-1/2): the WAIT join, the dbt hold list and the DuckDB-SQL rule pack. The proposed W2 re-order (DEC-012) addresses this first.

## Blind detection evaluation (synthetic ledger)

See the blackboard "BLIND EVALUATION PROTOCOL" entry. Round 1 (seed 42) recall on primary entries: 43/49 detected and contained, 1 detected but released, 5 missed. Round 2 (holdout seed 7, ledger committed before the run, code `bb3a9662…`) on 49 primary entries:

| Outcome | Count |
|---|---|
| Detected and contained | 42 |
| Detected but released | 1 |
| Contained by the translator | 6 |
| Missed | 0 |

The 6 translator-contained entries are content defects inside STs the translator rejected: the data never lands, but the defect class is not diagnosed. Secondary entries: 9 detected and contained, 3 contained by the translator, 0 missed.

**Open findings from round 2** (all W2; none releases bad data, each costs reviewer workload or delays a verdict):

1. **ELIG_OVERLAP_SAME_TYPE.** When a LATER batch creates the overlap, the earlier, already-released span is not re-flagged or reopened (1 detected but released). This needs retro re-evaluation of released units.
2. **8 false positives on unmarked data and 5 on negative controls.** All trace to one cause: when an IE extract is held whole (for a trailer mismatch), its spans are not current, so dependants are evaluated, or wait, against stale history. Fixes:
   - partial release of a held file's clean partitions;
   - treat HELD eligibility as "unknown", not "absent".
3. **Wall-clock timeout in replay.** The dependency `max_wait` uses the wall clock, so it never fires in a replay. In the clean run, 18 later encounter versions therefore wait behind earlier versions held by the codeset floor. Those 18 would be HELD anyway. Clean-run throughput is limited by the R-ENC-008 floor (item 10 under Stubs), not by detection.
