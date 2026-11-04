# Medicaid / HHS Data Quality Agent + Command Tower — Consolidated Design v1.1

> v1.1 = v1.0 plus the round-5 blockers (B5-1 Tower read path, B5-2 unit_key derivation, B5-3 quarantine key) and build notes N1–N14. Review cap reached (DEC-001); the orchestrator verified the changes by diff.

FRAME: deployable code, no documents. A deterministic Data Quality engine with an LLM advisor attaches to any DAG over a Snowflake medallion pipeline (raw → bronze → silver → gold) for state Medicaid and all HHS programs. A human-in-the-loop Command Tower runs in Streamlit in Snowflake.
SHAPE: Python package `dqagent`, generated Snowflake DDL and procs, SiS app, synthetic data generator, tests, and a T2 verification kit.
DONE (iteration 1): a "T1-verified deploy candidate" (DEC-003). Every safety invariant (★) is implemented and tested at T0/T1. Every deferred mechanism (◇) fails closed and is listed in EXIT_CRITERIA. Everything that runs only inside Snowflake is tagged T2-REQUIRED.

This document SUPERSEDES DESIGN v0.1–v0.4 on the blackboard. The blackboard keeps the history (findings, decisions). This file is the single source of truth for the build. Mechanism read/write sets are in `docs/mechanisms.yaml`; the pair matrix is reproducible with `tools/pair_matrix.py`.

User decisions in force:
- DEC-001: review cap of 5 rounds.
- DEC-002: no Snowflake; SiS; `C:\demo\medicaid-dq-agent`; carry across states; switchable LLM.
- DEC-003: deploy candidate plus T2 kit.
- DEC-004: ALL domains in iteration 1.
- DEC-005: validate the state's identity index; provider shadow clusters.
- DEC-006: ALL HHS programs.

---

## 1. What the agent is

A deterministic engine decides: bind, evaluate, balance, score, mode, gate. The LLM advises only. It produces root-cause hypotheses from a closed taxonomy (§9). It never feeds mode() and is never evidence or a decision of record.

Runtime loop per (node, batch):

```
OBSERVE → LAND → SEAL → BIND → EVALUATE → BALANCE → ASSESS → MODE → PROPOSE → [HITL] → RELEASE → PUBLISH → EVIDENCE → LEARN
```

Runtime roles are modules:

| Role | Module |
|---|---|
| Observer | adapters |
| Binder | contracts/binder |
| Executor | compiler, engine |
| ABC Controller | abc |
| Terminology Validator | terminology |
| Identity Sentinel | identity |
| Assessor | model/outcome |
| Supervisor | hitl/modes |
| Intervention Planner | intervention |
| Triage Advisor | llm |
| Evidence Scribe | evidence |

The human seat is the Command Tower.

## 2. Trust model

- **The database is the trust boundary. The app is an untrusted client.** ACCOUNTADMIN and SECURITYADMIN are in the TCB. Against everyone else the property is tamper-resistant; against admins it is tamper-evident only once the external anchor is live.
- **Trust domains are legal authorities:**
  - AUTH_MEDICAID (incl. MCHIP)
  - AUTH_CHIP (separate CHIP)
  - AUTH_SNAP
  - AUTH_TANF
  - AUTH_FTI (IRC §6103)
  - AUTH_SSA
  - AUTH_PART2
  - AUTH_TRIBAL:&lt;id&gt;
  - AUTH_INTAKE (IE agency; pre-split)
  - AUTH_STATE:&lt;x&gt;
- **FTI duties may be held only by state employees** (IdP attribute `employment_type=STATE`). Contractors, including the delivery team, cannot hold AUTH_FTI. Verify against IRS Pub 1075 in the T2 checklist.

### 2.1 Roles (`governance/roles.yaml` ★ generates DDL + T2 assertions; its hash is a REGISTRY artifact at H-IN-2)

| Role | Humans | Holds |
|---|---|---|
| DQ_GOV | none | Owns DQ_CTL (managed-access schema) and the procs (SEAL_BATCH, FINGERPRINT, _decide, PUBLISH, APPEND_AUDIT, APPLY_ROSTER, TOKENIZE). NEVER holds MANAGE GRANTS or user ownership (T0 invariant). Not under SYSADMIN. |
| DQ_DATA_OWNER | none | Owns gated relations. Only SEAL_BATCH writes them. |
| DQ_ENGINE | service | Runs evaluation. Writes results via procs only. |
| DQ_APP_&lt;AUTH&gt; | service, none | Owns that authority's SiS app. USAGE on DECIDE_&lt;AUTH&gt; and TOWER_READ_&lt;AUTH&gt; only; no table SELECT (B5-1). |
| DQ_DEPLOY | service, CI only | PUTs content-addressed wheels to @DQ_CTL.CODE. Creates objects in managed-access schemas, transferring ownership to DQ_GOV / DQ_DATA_OWNER. Holds no data privileges. Ownership transfer is a T2 check (N8). |
| DQ_DUTY_&lt;DUTY&gt;_&lt;AUTH&gt; | yes (SCIM-pushed from IdP groups) | USAGE on CONFIRM_&lt;AUTH&gt; only (B3). |
| DQ_PIPELINE | yes/service | INSERT on LANDING tables only. USAGE on SEAL_BATCH. SELECT on V_BATCH_STATE. NO privilege on gated relations (B2). |
| DQ_CONSUMER | yes/service | SELECT on *_RELEASED only. |
| DQ_AUDITOR | yes | Read on audit and the verifier. |

T0 invariants:
- DECIDE_* is app-only.
- CONFIRM_* is duty-only.
- No human holds DQ_GOV, DQ_DATA_OWNER or DQ_APP_*.
- Consumers have no base-table SELECT.
- DQ_PIPELINE has no gated-relation privilege.
- DQ_GOV has no MANAGE GRANTS.
- DQ_APP_* holds no table/view SELECT (all reads go through TOWER_READ_*).
- DQ_DEPLOY holds no data privileges.

## 3. Data model (★ day-one; changing it later = migration)

### 3.1 Canonical keys on every gated row
- `_dq_batch_id`
- `_dq_unit_key`, the canonical release unit (B1)
- `_dq_row_seq` (sequence)
- `_dq_source_system`, `_dq_source_record_key`

Facts are keyed by source record key. Identity resolves through the `IDR.V_XREF` view, never as a stored resolved ID.

### 3.2 Unit grain (single canonical definition, used by release, quarantine, authority and consumption)
`unit_key = (program_code, authority_set_hash, partition)`, computed ONCE at SEAL and never changed (B5-2):
- `authority_set_hash` = hash of the STATIC DECLARED authority set, i.e. the union over all rows in (batch, program_code, partition) of program authority ∪ field-provenance authorities ∪ sensitivity authorities ∪ tribal authority.
- Exactly one unit exists per (batch, program_code, partition).
- DYNAMIC authorities (INTAKE when a pre-split hop is non-PASS) are recorded on UNIT_EVENTS and unit_head, never in the key.
- PRIMARY authority = authority(program_code).

Partition is declared by the contract:

| Domain | Partition |
|---|---|
| Claims | chain_id |
| Eligibility | eligibility_unit key |
| Provider | provider key |
| Reference | `*` |

A batch is split into units at SEAL. SEAL also creates each unit's head row and first UNIT_EVENT, in the same transaction.

Tagging scope (N14): AUTH_TRIBAL applies to data originating from tribal, IHS, 638 or urban Indian programs or under a tribal DUA. It does NOT apply to every AI/AN member. AUTH_PART2 applies to records from Part 2 programs, not to every SUD diagnosis. Verify both against 42 CFR Part 2 and the tribal agreements before tagging synthetic contracts.

### 3.3 Load path (B2, R4-A1, R3-F1)
1. DQ_PIPELINE inserts into `LANDING.<rel>`. Every landing row gets `_landing_seq` from a sequence (N1).
2. `SEAL_BATCH(batch_id)` (DQ_GOV) runs in ONE transaction:
   - captures the landing high-water mark `landing_hwm = max(_landing_seq)` for the batch
   - moves ONLY landing rows with `_landing_seq ≤ landing_hwm` into the DQ_DATA_OWNER gated relation, assigning `_dq_row_seq` and `_dq_unit_key`, then deletes exactly those rows (N1)
   - creates the unit heads and first UNIT_EVENTs
   - records `sealed_max_seq`
   - computes FINGERPRINT
   - appends a BATCH_SEALED event
   - CAS on the head
   - writes the landing→gated ABC hop
3. A second SEAL for the same batch, or landing rows arriving after seal, is an ERROR and becomes a new batch.

Views filter `_dq_row_seq ≤ sealed_max_seq`.

### 3.4 Versioning
- Gated silver is insert-only and versioned.
- "Current" = max by the contract's `version_key` order, never batch_id.
- Duplicates are kept with `dup_of`, so the dedup count comes from rows.
- Late originals balance in their ARRIVAL batch.

### 3.5 Bitemporal eligibility
Effective plus recorded time. `recorded_to` is derived (LEAD), never UPDATEd. Overlap rules are keyed by (program, segment_type, eligibility_unit). eligibility_unit is one of INDIVIDUAL | HOUSEHOLD | ASSISTANCE_UNIT | CASE.

### 3.6 Lifecycle states (single table; terminal set explicit)

States:
- LANDED
- SEALED
- EVALUATING
- PENDING_DEPENDENCY
- RE_EVALUATE_PENDING
- HELD
- PENDING_RELEASE (H-ON veto window)
- RELEASED ✔terminal
- REJECTED ✔terminal
- ERROR

Rule: **the terminal set is defined in the transition table below. Any state not marked terminal is NON-TERMINAL** (R4-A4, N4). A batch is terminal when all its units are terminal.

Transition table (`model/lifecycle.py` ★; the helper rejects anything not listed):

| From | To | Via |
|---|---|---|
| LANDED | SEALED | SEAL_BATCH |
| SEALED | EVALUATING | engine |
| EVALUATING | PENDING_DEPENDENCY / RE_EVALUATE_PENDING / HELD / PENDING_RELEASE / RELEASED / ERROR | engine, PUBLISH (mode A) |
| PENDING_DEPENDENCY / RE_EVALUATE_PENDING | EVALUATING | engine |
| HELD | PENDING_RELEASE / RELEASED / REJECTED / EVALUATING (RE_PIN) | DECIDE / CONFIRM / RE_PIN |
| PENDING_RELEASE | RELEASED / HELD (veto) | PUBLISH / veto |
| ERROR | REJECTED / SUPERSEDED / EVALUATING (retry) | REJECT / SUPERSEDE (both H-IN-2) / engine |
| RELEASED ✔ | — | terminal. Post-release problems go to the DENY table, never to a state change. |
| REJECTED ✔, SUPERSEDED ✔ | — | terminal |

**DENY** is an append-only table `DENY(unit_key, consumer?, reason, created_by_mechanism)`, not a lifecycle state (N3). The re-fingerprint job and DEPENDENCY_CHANGED write it, through the CAS pattern. `_RELEASED` views, WAIT and EALR all read it.

Transitions are events in the append-only BATCH_EVENTS / UNIT_EVENTS logs. Each event is appended in the same transaction as the CAS on the head row (`state_version` increments). A detective check asserts head == fold(events).

**Single-writer rule (from the write/write pair matrix).** Five mechanisms write unit_state / unit_head / unit_events after creation: evaluate, decide, confirm, publish and refingerprint (which writes DENY, not state). SEAL creates the heads. They do so ONLY through one helper, `lifecycle.transition(unit, expected_state_version, event)`. The helper:
- appends the event and CASes the head in ONE transaction
- on a CAS miss, returns `CONFLICT`
- checks the allowed transition against the lifecycle table, so an illegal transition is an ERROR

The caller re-reads state and re-runs its own preconditions (for confirm and publish, that means `can_release` again). No mechanism writes these tables directly; a T0 test greps for direct DML.

The audit log follows the same pattern: only `APPEND_AUDIT` writes, with an AUDIT_HEAD CAS. RELEASED_UNITS is written only inside `transition(..., RELEASED)`.

The same append-only + CAS-head pattern applies to (N6):
- DECISIONS: confirmations are rows, with a CAS on the decision head
- registry artifact_state
- breaker state
- the dispatcher pointer
- DENY
- the person_id roster mapping (written by GENESIS once, then only by APPLY_ROSTER)

GENESIS is the first APPEND_AUDIT entry; there is no second anchor writer. ABC hop keys are disjoint between SEAL (landing→gated) and the ABC module (all other hops). The fingerprint recompute in clause 1 is PURE; it never writes.

## 4. Contracts and registry

### 4.1 Contract (ODCS-like) fields — all present day one; values may be DRAFT

**Structure**
- grain, keys
- chain spec: chain_key, version_key + order, adjustment indicator, void semantics, reversal/rebill
- unit partition
- eligibility_unit
- ordering class: ORDERED | UNORDERED
- depends_on with policy: WAIT | EVALUATE_AGAINST_LAST_RELEASED
- consumer grain declarations
- incremental predicate class

**Counting**
- countable_terms per (format, claim_category)

**Tags and authority**
- field `control_tags` (inside the semantic hash; removal = loosening): clinical | payment | coverage | program | identity | free_text | llm_summarisable
- field `dimension_tags` (reporting only; cosmetic) (R4-A5)
- sensitivity: part2 | bh | tribal | minor | snap_confidential | tanf_confidential | fti | ssa
- field provenance authority (DECLARED). For IE sources, ANY undeclared field fails closed.

**Program**
- program_code: MEDICAID | MCHIP | SCHIP | WAIVER:&lt;id&gt; | SNAP | TANF | &lt;state&gt;

**Governance**
- required_check_classes (may only RAISE the federal floor)
- declared consumers

### 4.2 Rule IR v2

**Identity and provenance**
- rule_id, version, semantic hash
- origin: FEDERAL_TMSIS | FEDERAL_CMS0057F | FEDERAL_1927 | FEDERAL_FNS (declared-empty) | FEDERAL_ACF (declared-empty) | STATE | LOCAL
- artifact_vocabulary + artifact_type (T-MSIS is one vocabulary)
- content_area, category, entity

**Check definition**
- check_class: conformance | codeset_format | codeset_validity | validity | integrity | plausibility | reconciliation | timeliness | uniqueness | balance
- validation_type: INFERENTIAL (sourced) | DETERMINISTIC / LONGITUDINAL / BENCHMARK (COALITION-DEFINED)
- metric kind + scope class: BATCH | DELTA_VS_HISTORY | FULL. The gate blocks only on BATCH/DELTA.
- priority, thresholds (min/max, ta_min/max, longitudinal)
- data_elements

**Applicability**
- claim_category, payer_type, item_type, program_code, plan_id, submitter_id, format, authority
- explicit `*`; NULL is rejected

**Benchmarking and handling**
- benchmark_ref, wait_for_benchmark
- row_severable, remediation_policy
- governing-date rule ref

### 4.3 Binding rules

**Federal T-MSIS rules**
- Bind ONLY to the T-MSIS extract node, through a versioned state→T-MSIS crosswalk (a registry artifact).
- The extract contract declares a Medicaid/CHIP program filter as a counted ABC term.
- Silver and gold get STATE/LOCAL rules.

**Federal pack (project-authored baseline, release-hashed; NOT a CMS-signed artefact)**
- Per-domain floors.
- M-18: SCHIP × MEDICAID concurrency is required (SSA §2110(b)(1)(C)), scoped to separate CHIP. It has a retro-eligibility exception class at H-IN.
- PA:
  - CMS-0057-F with item_type=MEDICAL: 72h / 7 days. Covers Medicaid/CHIP FFS and managed care.
  - item_type=DRUG goes under FEDERAL_1927: 24h (UNVERIFIED, labelled).
  - No 14-day extension (secondary source only).
- NDC:
  - Remediation NEVER; tagged payment.
  - Shape by format: claims = 11-digit unhyphenated 5-4-2; FDA reference = hyphenated 4-4-2 / 5-3-2 / 5-4-1.
- Governing date via GOVERNING_DATE_RULE(code_system, claim_type, level). Oct 1 is never hard-coded.

**Floors for STRUCTURAL programs (SNAP/TANF)**
- The floor is the implemented structural classes only: span by eligibility_unit, identity xref, ABC.
- Federal-pack classes report NOT_IMPLEMENTED.
- Releases are never mode A in iteration 1.
- The tile shows "STRUCTURAL — program rules 0/0 sourced", and the aggregate is NOT_EVALUATED.

### 4.4 Registry ★
- Covers: contracts, rule packs, overlays, crosswalks, GOVERNING_DATE_RULE, POLICY, provider/model policy, DATA_SHARING_BASIS, roles.yaml, DEPLOY_MANIFEST.
- Each artifact is content-hashed (per-rule semantic hash over decision-relevant fields + control_tags) with a state of PENDING | APPROVED | REVOKED.
- **Deploy ≠ activate.** New hashes land PENDING, and the engine uses the last APPROVED.
- Fail-closed if no approved predecessor exists, or if the hash was REVOKED (affected batches get an automatic RE_PIN card).
- The pack version is pinned per batch at SEAL. RE_PIN verb: tightening = H-IN, loosening = H-IN-2.
- The evaluation digest includes the POLICY hash.
- **Iteration-1 ladder:** cosmetic (dimension_tags/description only) → A + notify. Everything else, including unknown classes → H-IN-2. ◇ tiered ladder.
- **Validator:**
  - depends_on graph must be acyclic; every cycle is broken by one EVALUATE_AGAINST_LAST_RELEASED edge. Default: XREF→ELG is EALR, ELG→XREF is WAIT.
  - NULL applicability rejected.
  - Contracts may not lower federal floors.
- `dqagent registry plan` runs in CI before deploy and opens cards.

### 4.5 Overlay ADVISORY (R4-A7, R3-F7)
- `ADVISORY` is an explicit outcome treatment in `model/outcome`.
- **What it does:**
  - The class is excluded from mode() with a reason code.
  - It still shows in coverage.
  - The floor is NOT met. The Tower shows "floor waived: class X", and any unit whose floor is waived requires mode ≥ H-IN; it is never A (C6).
- **Constraints:**
  - Mandatory expiry.
  - Auto-revoked when that code system's registry is loaded.
  - REFUSED outside `deployment_profile: DEMO`.
  - H-IN-2.
- T0 tests cover all of the above.

### 4.6 Genesis ★
- The install writes a GENESIS record containing:
  - roster seed hash
  - registry seed hash
  - manifest hash
  - roles.yaml hash
  - the initial dispatcher pointer
- The seed is diffed against the vendored federal baseline. The ratifiers see:
  - every overlay delta
  - the COALITION-DEFINED rule list
- The bootstrap roster is ratified by an out-of-band two-signature artefact, verified against the external anchor. Only this record type is exempt from the self-affecting check.
- Until ratification, all gated nodes fail closed and the Tower shows UNRATIFIED GENESIS.
- The scripted test genesis lives in `tests/` only; a T0 test asserts the wheel excludes it.

## 5. Evaluation

**Compiler** ★
- Metric kinds are sqlglot AST builders rendered in the Snowflake dialect.
- Portability allowlist, golden SQL, and a pure-Python reference oracle per kind.
- ◇ fusion.

**Fingerprint** (M-03, R-12)
- `HMAC(K_FP_v, canonical content digest of sealed rows ≤ sealed_max_seq over the pinned contract's declared columns)`.
- `fp_key_version` is stored with the fingerprint and the decision.
- K_FP is a separate key family under DQ_GOV. Rotating it is a REBIND ceremony (H-IN-2) that records an old→new mapping. It is never a STALE trigger.

**ABC** ★
- Per (hop, measure, program_code, plan_id, submitter_id, claim_category, format, authority):
  `residual = src_in − (tgt_out + rejected + quarantined + deduplicated + filtered_by_contract + held)`
- Every term is counted.
- An uncountable term = UNBALANCEABLE. A term not countable for the format = NOT_APPLICABLE_FOR_FORMAT.
- **Hash totals:** HMAC(K_ABC_v) 60-bit fold, with `abc_key_version` stored on abc_hops. Rotation is a REBIND-style ceremony (N13); over a canonical typed / delimited / NULL-sentinel key, computed in a DQ_GOV proc. The NULL-key count is its own measure (R4-A8).
- **Hop ownership:**
  - Pre-split hops are owned by AUTH_INTAKE.
  - Other authorities see only the verdict plus their own leg.
  - When any pre-split hop is non-PASS, authorities(unit) ∪= {INTAKE}, and an INTAKE co-signer is required (R3-M11).

**Dependency vector** (M-06)
- At evaluation, record the intake high-water mark (HWM) per depended domain.
- **WAIT** blocks while any depended batch with intake_seq ≤ HWM is NON-TERMINAL (§3.6) and intersects the unit. Intersection is over the member ∪ eligibility_unit key, or an overlapping period.
- **EVALUATE_AGAINST_LAST_RELEASED** labels the result APPROXIMATE:dependency_held instead.

**Cost**
- QUERY_TAG `dq:<rule>:<run>`; DQ warehouse plus resource monitor DDL.
- Credit budget ★: exhaustion → NOT_EVALUATED.

**Identity** (DEC-005)
- Member checks validate the state xref: split/merge drift, orphans, overlapping enrolment across IDs (within an authority, or IE-INTEGRITY scope via DATA_SHARING_BASIS).
- If the xref is not loaded → SYSTEM_NOT_LOADED.
- Identity-dependent measures are labelled APPROXIMATE:identity=xref_unverified unless this run's xref validation passes.
- ◇ provider shadow clusters.

**Terminology**
- Format checks are real.
- Validity uses an interval registry. Outcomes: VALID | VALID_NOT_BILLABLE | NOT_EFFECTIVE_ON_DATE | UNKNOWN_CODE | SYSTEM_NOT_LOADED.
- ◇ validity load (the DEMO profile uses ADVISORY).
- Licensed systems (CPT = HCPCS Level I, CDT, NUBC, X12 CARC/RARC, APR-DRG) ship ZERO real rows; a CI test enforces this.

## 6. Outcomes and scoring (`model/outcome` ★)

Total order (T0 exhaustiveness test):

| Rank | Dispositions |
|---|---|
| 0 | PASS |
| 1 | TA_WARN |
| 2 | KNOWN_ISSUE |
| 3 | APPROXIMATE |
| 4 | PENDING_BENCHMARK |
| 5 | NOT_EVALUATED, SYSTEM_NOT_LOADED, NOT_IMPLEMENTED |
| 6 | UNBALANCEABLE |
| 7 | FAIL |
| 8 | ERROR |

- NOT_APPLICABLE_FOR_FORMAT and ADVISORY are excluded from rollup, and neither satisfies a floor.
- Only PASS counts for scores and coverage (R-01).
- Worst-of over an empty set = NOT_EVALUATED.
- KNOWN_ISSUE creation = WAIVE (H-IN-2, with expiry and a CMS reference).
- High-priority rules roll up worst-of. Med/Low show band counts. No blended index.
- Coverage, dollar/member exposure, as_of and period are always shown.
- Periods stay PROVISIONAL in iteration 1. FINAL is reachable only via PERIOD_CLOSE (◇).

**FULL depth is computed by CI, never asserted.** A cell (domain × check class × program) is FULL iff:
1. Contract semantics are declared.
2. Every required class is EVALUABLE (not merely bound).
3. Hops balance on clean data.
4. Oracle = SQL on 100% of cases.
5. Seeded-defect detection Wilson lower bound ≥ 0.6 (95%), over ≥ 5 defect classes × 3 instances.
6. Recall against a ground-truth ledger authored by a SEPARATE seat and hash-committed before rules are written. Otherwise the cell is labelled SELF-AUTHORED.

Otherwise the cell is PARTIAL, with the failing predicate shown.

## 7. Release (the single predicate ★)

`can_release(unit)` and `_decide` live in the wheel. DQ_GOV Python procs IMPORT the staged wheel from a **content-addressed path** (`@DQ_CTL.CODE/dqagent-<sha256>.whl`). The deploy tool refuses OVERWRITE (R4-A2).

- Procs are versioned (`_DECIDE_V<n>`). A DQ_GOV dispatcher pointer flips only on REGISTRY approval; genesis sets the first pointer.
- Thin wrappers per authority, each with the authority as a constant:
  - `DECIDE_<AUTH>` → app role
  - `CONFIRM_<AUTH>` → duty roles

`can_release(unit)` is TRUE iff ALL of the following hold:

1. **Fingerprint:** the recomputed fingerprint (≤ sealed_max_seq) equals the bound one, under the same fp_key_version.
2. **Approved artifacts:** every artifact hash in the evaluation digest (rules, contract, overlays, POLICY, crosswalks) is APPROVED, and the active proc and wheel hashes match DEPLOY_MANIFEST.
3. **Dependencies:** WAIT edges are satisfied (§5). For WAIT edges only, the dependency state_version vector stored at evaluation EQUALS the current state_versions of every intersecting depended unit; otherwise the unit is RE_EVALUATE_PENDING. EALR edges compare only against upstream RELEASED-state changes and DENY rows (N12).
4. **Authority:** either:
   - mode() == A, recomputed from the digest (never read from a status), with floor checks: rank ≥ 4 is never A; STRUCTURAL programs are never A; or
   - a DECISION of record bound to (fingerprint + fp_key_version, proposed_quarantine_set_hash, bound_disposition_vector), still valid (no rule worse than bound; no new bound rule worse than PASS), with the required distinct person_ids. For H-IN-2 the checker is DB-verified via CONFIRM_&lt;AUTH&gt; in their own session (T2).
5. **No open veto window:** an H-ON release is PENDING_RELEASE until the window closes with a delivered-notice receipt. Notify is ◇ in iteration 1, so H-ON degrades to H-IN.
6. **Breaker:** the per-program release breaker is not tripped. Unfreezing is H-IN-2.
7. **Approver duties:** approver duties (real-time, §8) ⊇ authorities(unit) = the unit's program authority ∪ field-provenance authorities ∪ sensitivity authorities ∪ tribal ∪ (INTAKE if a pre-split hop is non-PASS). Release goes only through DECIDE_&lt;PRIMARY authority&gt;, i.e. authority(program_code). Co-signer CONFIRMs come from each additional authority's duty role. The LAST required CONFIRM applies the quarantine and the release atomically (N13).
8. **Period:** the unit's period is not FINAL. Iteration 1 never reaches FINAL.
9. **Built apps:** every authority in authorities(unit) has a built Tower app. Otherwise the unit is NOT_IMPLEMENTED and held (R4-A9). Iteration 1 builds MEDICAID and SNAP.

**Apply:**
- quarantine-set application and RELEASED_UNITS write happen atomically in the confirming call
- QUARANTINE key = (`_dq_batch_id`, `_dq_unit_key`, `_dq_row_seq`) + scope ROW | CHAIN | UNIT + nullable chain_id / version_key. This works for every domain (B5-3).
- already-published versions stay published

**Views:**
- `<rel>_RELEASED` = positive INNER JOIN to RELEASED_UNITS on (batch_id, unit_key), filtered `_dq_row_seq ≤ sealed_max_seq`, with ANTI-JOINs on open QUARANTINE (by row_seq / chain / unit scope) and DENY (for consumers that have not yet consumed) (N2). The view owner gets explicit SELECT on RELEASED_UNITS, QUARANTINE and DENY (N9).
- deny-overrides apply only to (unit, consumer) pairs not yet consumed
- streams and Dynamic Tables are UNSUPPORTED until T2

**Consumption:**
- derived per (unit, consumer) from `(_dq_batch_id, _dq_unit_key)` in the target (B1)
- aggregate gated targets are ◇ refused by the binder
- deny hides the unit from non-consumers; consumed units → RESTATE (◇ card only)

**PUBLISH:**
- drains strictly in order per (ORDERED domain, partition) and STOPS that partition at its first non-terminal unit (R4-A4, N5). A held SCHIP or PART2 unit therefore blocks only its own chain or member partition, not the whole domain.
- ◇ key-disjoint skip, consistency groups
- liveness tiles: held-age SLO, throughput, backlog age

**Re-fingerprint job ★:** released and unconsumed units are re-fingerprinted; a mismatch writes a DENY row. The unit's state stays RELEASED (terminal), and a card opens (C4).

**Gate (when attached to Tasks):**
- succeeds on HOLD with `SYSTEM$SET_RETURN_VALUE('HOLD')`
- dependents carry `WHEN SYSTEM$GET_PREDECESSOR_RETURN_VALUE('<gate>')='PASS'`
- RAISEs only on ERROR
- requires TASK_AUTO_RETRY_ATTEMPTS = 0
- NOTIFY on every HOLD

**Binder ★ refuses:**
- MERGE/UPDATE/DELETE/unguarded loads on gated paths
- WATERMARK/UNKNOWN incremental predicates
- aggregate gated targets
- consistency groups
- units with an authority that has no built app
- contracts lowering floors
- undeclared IE fields

## 8. HITL

**Modes:** A, H-ON, H-IN, H-IN-2.
`mode = max(POLICY[authority][domain][severity][reversibility] escalated by blast radius / confidence, FLOOR[verb])`. States may tighten, never loosen.

**Closed verb set:**
- HOLD, QUARANTINE_ROWS, RELEASE, REQUEST_RESEND, NOTIFY_SUBMITTER
- WAIVE (incl. KNOWN_ISSUE), CHANGE_RULE, RE_PIN, REBIND
- APPLY_SAFE_TRANSFORM, PROPOSE_MERGE
- REJECT (H-IN-2 when ABC failed or payment/eligibility is involved; otherwise per POLICY), SUPERSEDE (H-IN-2), UNFREEZE_BREAKER (H-IN-2) (N4, C5)
- ◇ BREAK_GLASS (refused), ◇ RESTATE, ◇ PERIOD_CLOSE, ◇ BACKFILL

**Floor (H-IN-2):**
- release after an ABC failure
- identity actions
- waivers
- loosening rules, overlays or POLICY
- roles.yaml, roster, DATA_SHARING_BASIS, destination changes
- release to a submission extract
- genesis items

**Injection points:**
1. detect → triage
2. assess → hold
3. propose (dry-run diff + compensating action)
4. H-ON veto
5. release (ABC before/after)
6. learn: rule change / waiver / merge

Every timeout is a fail-closed escalation. Re-opens are rate-limited and the unit stays held.

**Duties:** Snowflake roles pushed by SCIM. The real-time check is `SHOW GRANTS TO USER` inside the proc (T2). If T2 shows this needs a privilege DQ_GOV must not hold, then EVERY approval tier requires CONFIRM in the user's own session.
- person_id comes from an IdP mapping written only by APPLY_ROSTER (outside-approver decision). Uniqueness is checked in the proc.
- SoD: not the owner (IdP group), not the author, no self-affecting change, distinct person_ids.

**Attribution:**
- maker: `st.user`, labelled APP_ASSERTED
- checker: DB_VERIFIED via CONFIRM

**Audit:**
- AUDIT_LOG is append-only via APPEND_AUDIT, with an AUDIT_HEAD CAS.
- The chain hashes digests, not payloads.
- The external ANCHOR carries only H(audit_head). The T1 sink is a local append-only file; the T2/prod sink is S3 Object Lock or a separate audit account.

**Live code checks:** PUBLISH and the heartbeat verify:
- active dispatcher target
- wrapper GET_DDL (as owner)
- stage file digests
- `SHOW GRANTS ON` each DQ_GOV, DQ_DATA_OWNER, LANDING and `_RELEASED` object, hashed and compared with the manifest (N9)

The external heartbeat compares LAST_ALTERED and QUERY_HISTORY CREATE/ALTER/GRANT on DQ_* against manifest timestamps (R3-M2, R4-A6). Detective latency (ACCOUNT_USAGE ~45 min–3 h) is stated.

**Tower read path (B5-1) ★:** `TOWER_READ_<AUTH>(view_name, params)` is a DQ_GOV owner's-rights proc per authority, with the authority as a constant.
- It imports the same wheel and applies row filtering by authority (the row access policy model, RAP) plus `presentation/suppress` SERVER-SIDE.
- It returns only suppressed, authority-scoped results. The app never touches tables.
- RAP = the row access policies keyed by authority; at T1 they are implemented in the proc and emitted as Snowflake ROW ACCESS POLICY DDL (T2).

**Command Tower:** one codebase, one app per authority (iteration 1: MEDICAID, SNAP).
- Top band: genesis status, held runs, decisions due, oldest SLA, mine, ABC status, liveness, LLM health.
- Real screens: decision queue (deterministic clustering: rule × node × failure signature × common ancestor, partitioned by authority), DAG health, evidence, ABC board.
- Read-only screens: interventions, waivers/rules, audit explorer, agent activity (mode() trace).
- The decision card holds everything needed to decide. The LLM panel is collapsed and labelled, with no accept button; the reviewer types the rationale.
- The checker gets a token plus a copyable `CALL DQ_GOV.CONFIRM_<AUTH>('<token>')`.

**Presentation:**
- `presentation/suppress` ★ is the ONLY suppression site.
- Small cells (overlay threshold, default &lt; 11, plus complementary suppression) are suppressed outside their own authority.
- The gate always computes unsuppressed values.

## 9. Evidence and LLM

**Tokens:**
- `HMAC(K_EV_v, authority ‖ program ‖ rule_id ‖ period ‖ canon_key)` with key_version. Tokens are non-linkable across rules and programs.
- Tokenisation happens in the TOKENIZE proc, so the engine sees tokens only. At T1 the same wheel function runs with TEST_KEY.
- Samples are ordered by token.
- EVIDENCE_PAYLOAD is separate from audit. Per-retention-class data keys are in the DDL; ◇ shred jobs.

**LLM (M-22):**
- Typed prompt builder: opaque logical names `^[A-Z0-9_]{1,64}$`; codes as registry ids; descriptions from FEDERAL rows only.
- Failing values are NEVER sent; only shape classes.
- Summaries only for fields tagged `llm_summarisable`, through `presentation/suppress`.
- Output: a hypothesis from the closed taxonomy, plus cited (rule_id, pinned semantic hash) and a confidence band, rendered by template.
  - Taxonomy: SUBMITTER_FORMAT_CHANGE, CODESET_VERSION_LAG, REFERENCE_NOT_LOADED, LATE_FILE, CROSSWALK_DRIFT, DEPENDENCY_HELD, VOLUME_ANOMALY, UNKNOWN.
- The LLM never originates WAIVE or loosening proposals.
- No FTI ever. Other authorities only with a DATA_SHARING_BASIS row.
- Injection fuzz test.
- **Providers:** NullProvider is the default. At T1, a POLICY row selecting anything else is refused. Cortex and Anthropic adapters are emitted, T2-REQUIRED.
  - Cortex needs a region check.
  - Anthropic needs a BAA/DUA flag plus EAI.
  - Provider selection is H-IN-2.

## 10. Verification

| Tier | Meaning |
|---|---|
| T0 | Unit tests and invariant tests (role model, outcome order, prompt allowlist, no key literals, scripted genesis excluded, ADVISORY constraints, STRUCTURAL never PASS) |
| T1 | fakesnow logic: compiler vs oracle, golden SQL, ABC, release predicate, views, lifecycle, drain, divergence suite. **A T1 pass is NOT evidence of Snowflake compatibility.** |
| T2 | Not available. The kit (`dqagent verify --tier t2`) covers the items below. |

The T2 kit covers:
- smoke SQL over the construct-coverage matrix
- grant/ownership assertions generated from roles.yaml
- the divergence suite: regex anchoring, div0, NULL COUNT_IF, TZ, identifier case, JW scale, UNIQUE non-enforcement
- Tower smoke through the deployed SiS app: approve, reject, stale, self-approval denied, cross-program denied
- concurrent DECIDE fork
- AUDIT_HEAD CAS
- CURRENT_USER in CONFIRM
- SHOW GRANTS TO USER privilege
- IMPORTS of a content-addressed wheel
- SECRET binding / EAI
- fan-in skip
- LIST md5

Artifacts are tagged by where they run. Procs, tasks, policies, views-with-policies and SiS are T2-REQUIRED.

## 11. Iteration-1 manifest

★ = invariant (built and tested). ◇ = fails closed / refused and listed in EXIT_CRITERIA.

**Model**
- `model/{outcome, rule, contract, policy, lifecycle}` ★

**Registry and governance**
- `registry/` ★ — validator, RE_PIN, REBIND
  - ◇ tiered ladder
- `governance/{roles.yaml (incl. DQ_DEPLOY), genesis, anchor}` ★
- `governance/procs` ★: versioned procs `_DECIDE_V<n>` plus a CAS dispatcher pointer (M-12); live code check (M-11); `TOWER_READ_<AUTH>` (B5-1); RAP
- `governance/heartbeat` ★: an external heartbeat. At T1 it is a CLI command; in prod it runs outside the account.
- `rules/federal_pack` ★: project-authored baseline with floors, M-18, PA, NDC shapes and the GOVERNING_DATE_RULE seed, plus a list of COALITION-DEFINED rules

**Contracts**
- `contracts/{binder ★, onboard}` — onboard scaffolds DRAFT contracts from the dbt manifest

**Compilation and evaluation**
- `compiler/` ★
  - ◇ fusion
- `engine/` ★ — seal / landing, fingerprint, dependency HWM, budget, lease
- `abc/` ★

**Release and HITL**
- `intervention/` ★ — RELEASED_UNITS, views, consumption, drain, quarantine, re-fingerprint, breaker
  - ◇ skip / groups / aggregates
- `hitl/` ★ — single predicate, wrappers, binding, modes, floor, duties
  - ◇ BREAK_GLASS / RESTATE / PERIOD_CLOSE / BACKFILL

**Evidence, terminology, identity**
- `evidence/` ★
  - ◇ shred
- `terminology/` — format checks; GOVERNING_DATE_RULE ★
  - ◇ validity load
- `identity/` — xref validation
  - ◇ provider clusters

**LLM, notify, adapters**
- `llm/` ★ — builder, taxonomy, NullProvider
  - ◇ live providers (T2)
- `notify/` ◇ — no-op; H-ON → H-IN, labelled
- `adapters/` — dbt manifest real
  - sf_tasks generated, T2-REQUIRED
  - ◇ external evidence

**Presentation**
- `presentation/suppress` ★
- `streamlit/command_tower` — MEDICAID and SNAP instances

**Data, tests, deploy**
- `synthetic/` — ALL domains (claims CIP/CLT/COT/CRX × FFS/ENCOUNTER/CAPITATION/SUPPLEMENTAL/SUBCAP, ELG/enrollment incl. MCHIP/SCHIP, PA, TPL, provider, MCR, xref) and ALL programs (SNAP/TANF households, cross-program entity graph)
  - realistic defect-blind mode plus seeded mode
  - ground-truth ledger by a separate seat, hash-committed first
- `tests/` — t0, t1, oracle, divergence, golden; computed FULL/PARTIAL matrix → `docs/EXIT_CRITERIA.md`. The FIRST T1 tests written are N1 (seal race), N2 (released views hide quarantine/deny), N3 (DENY), N4 (transitions/REJECT) and N5 (per-partition drain).
- EXIT_CRITERIA must state (N11): "ALL programs are EVALUATED; only MEDICAID/MCHIP and SNAP units with no CHIP, TANF, INTAKE, PART2, TRIBAL, FTI or SSA authority are RELEASABLE in iteration 1 (no app for those authorities). A unit with a non-PASS pre-split hop stays held, because INTAKE has no app."
- `deploy/` — generated DDL / procs / tasks / SiS + t2 kit; all T2-REQUIRED
- `tools/pair_matrix.py` + `docs/mechanisms.yaml` (read/write sets; checks write/write and read/write pairs)

Deferred pre-PHI (listed as NOT DONE):
- external WORM sink
- viewer-scoped masking / Part 2 entitlements / column-level authority on consumer views
- Cortex region lock or BAA + egress pin
- retention / shred
- CI-only stage + SBOM
- DR of CAS heads and secrets
- pen test
- predicate cost budget at CLT scale
