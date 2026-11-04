# Medicaid / HHS Data Quality Agent: Platform-Independent Design v2.0

**FRAME.** A portable, configuration-driven Data Quality control plane. It attaches to any set of DAG pipelines that feed a medallion architecture (inbound raw files → raw → bronze → silver → gold → submission extracts). The data planes may be Snowflake, Databricks or BigQuery. It serves any state Medicaid client, plus HHS programs, and is tailored through configuration. It is not built for one named client.

**SHAPE.**
- A Python package `dqagent`. It holds the engine, adapters, harness and a FastAPI control-plane API.
- A Postgres control-plane database.
- A Next.js Command Tower.
- Per-platform data-plane kits (DDL, views, gates, tokenizers).
- Seven sample pipelines, plus raw-file ingestion and a synthetic data generator.
- Tests and per-platform T2 kits.

**DONE (iteration 1).** A "T1-verified deploy candidate" (DEC-003):
- Every ★ invariant is implemented and tested at T0/T1.
- Every ◇ stub fails closed and is listed in EXIT_CRITERIA.
- Every data-plane artefact is tagged T2-REQUIRED:&lt;platform&gt;.

**Supersedes.** This file supersedes DESIGN v1.1 (Snowflake-native), which is kept as `history_DESIGN_v1.1_snowflake_native.md`. The blackboard keeps findings and decisions.

**Decisions in force:**

| ID | Decision |
|---|---|
| DEC-001 | Review cap of 5 rounds |
| DEC-003 | Deploy candidate + T2 kits |
| DEC-004 | ALL domains |
| DEC-005 | Validate the state identity index; provider shadow clusters |
| DEC-006 | ALL HHS programs |
| DEC-007 | Must not run inside Snowflake; Databricks and BigQuery equal |
| DEC-008 | FastAPI + Next.js |
| DEC-009 | Three platforms at equal depth, plus raw ingestion |
| DEC-010 | Any state; portable via configuration |
| DEC-011 | Approved |

---

## 1. What the agent is

The engine is deterministic. It binds, evaluates, balances, scores, sets mode, gates and releases. The LLM only advises. It returns a root-cause hypothesis from a closed taxonomy (§9). It never feeds mode() and is never evidence or a decision of record.

### 1.1 Agent roles are interfaces

Roles live in `dqagent/roles/*.py` as Protocols. Iteration 1 ships a native runner. ◇ A Microsoft Agent Framework adapter can host the ADVISORY and ROUTING roles later. Decisions always stay in the control plane.

| Role | Module | Decides? |
|---|---|---|
| Metadata Agent (Observer) | adapters/dag/*, adapters/metadata/* (dbt manifest ★, DataHub ◇ read-only lineage and ownership) | no |
| Profiling Agent | profiling/: per-column baselines (null rate, distinct, min/max/quantiles, length/charset shape), stored as evidence, feeding LONGITUDINAL rules and drift | no |
| Quality Rules Agent | registry/proposals: proposes rules from profiles and history. A proposal is a registry card at H-IN-2 and is NEVER auto-activated | no |
| Binder | contracts/binder | refuses only |
| Executor | compiler, engine | no |
| ABC Controller | abc | no |
| Terminology Validator / Identity Sentinel | terminology, identity | no |
| Policy Compliance Agent (Supervisor) | hitl/modes (mode(), floor, authority) | **yes (deterministic)** |
| Remediation Agent (Intervention Planner) | intervention (closed verb set, dry-run diffs; never rewrites data) | proposes |
| Human Approval | Command Tower + API (a HUMAN decides; the routing role only queues and escalates) | human |
| Data Product Steward | steward duty + per-data-product scorecard (quality bands, coverage, open cards, waivers, SLA) | human |
| Triage Advisor | llm (closed taxonomy) | no |
| Evidence Scribe | evidence | no |

### 1.2 Runtime loop per (pipeline, node, batch)

`INTAKE (files) → LAND → SEAL → BIND → PROFILE → EVALUATE → BALANCE → ASSESS → MODE → PROPOSE → [HITL] → RELEASE → PUBLISH → EVIDENCE → LEARN`

## 2. Architecture and trust model

### 2.1 Control plane (CP) vs data planes (DP)

**CP** = the `dqagent` service plus a Postgres CP database. It holds:
- lifecycle state and events
- the registry
- decisions, released_units (master), DENY, quarantine
- results: aggregates and tokens only, never raw values
- audit and roster

It runs anywhere a container runs (any cloud or on-prem). Rationale (DEC-007): it is platform-neutral, and Postgres ENFORCES UNIQUE/FK/CHECK constraints, row locks and SERIALIZABLE isolation. That closes the round-1/2 concurrency findings that came from Snowflake not enforcing constraints.

**DP** = Snowflake | Databricks (Unity Catalog, Delta) | BigQuery, each reached through `adapters/dataplane/<platform>`. Checks are pushed down as SQL compiled for that dialect. Only aggregates, tokens and counts leave the DP.

**Raw values never enter the CP.** Sample keys are tokenized INSIDE the DP (§9.1) before they are returned.

### 2.2 Trust boundary

- **The CP database plus the CP service are the trust boundary. The Tower is an untrusted client.**
- All decision logic runs in the API: one wheel, no mirrored code.
- The CP DB role `dq_gov` owns the schemas. The API connects as `dq_api` with DML only through SECURITY DEFINER functions for state transitions. No human holds a DB login with DML.
- Append-only tables (events, audit, decisions, deny, registry state) have UPDATE/DELETE revoked plus a trigger guard.
- **TCB (stated honestly):**
  - CP DB superuser and cloud/k8s admins.
  - Per DP: ACCOUNTADMIN/SECURITYADMIN (Snowflake), metastore and workspace admins (Databricks), project owner / IAM admin (BigQuery).
  - The property is tamper-resistant against everyone else, and tamper-evident against admins only once the external anchor is live.

### 2.3 Trust domains are legal authorities

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

Rules:
- **FTI:** duties go only to state employees (`employment_type=STATE` IdP claim). This is a Pub 1075 check in T2.
- **AUTH_TRIBAL** applies only to data from tribal, IHS, 638 or urban Indian programs, or under a tribal DUA.
- **AUTH_PART2** applies only to Part 2 program records.
- Crossing authorities requires a DATA_SHARING_BASIS registry row at H-IN-2.

### 2.4 Identities and roles

**Humans** authenticate to the API with OIDC (the state IdP).
- Duties = IdP group claims (`dq-duty-<duty>-<auth>`), validated on EVERY request. This is real-time, with no latent view.
- `person_id` = an immutable IdP subject plus an employee-id claim. Users without one hold no duty.
- **Checker step-up:** an H-IN-2 second approval needs a fresh token with an MFA `acr`/`amr` claim (max_age ≤ 5 min).
- Both maker and checker are TOKEN_VERIFIED. There is no app-asserted identity anymore; the Tower forwards the user's token.

**Service principals (per DP):**

| Principal | Holds | Must not hold |
|---|---|---|
| dq_eval | read on gated relations, landing, reference; execute on DQ functions | write on gated |
| dq_seal | write on gated relations; read landing | — |
| dq_publish | write on the DQ release table and released views | — |
| dq_tokenize | key access for in-DP HMAC | — |
| pipeline | write on landing only | write on gated relations |
| consumers | read on released views only | — |

**CP roles:** dq_gov (owner, no humans), dq_api (service), dq_auditor (read-only, humans).

**`governance/roles.yaml` ★** declares everything above. It generates the CP grants, the per-DP grant DDL for each of the three platforms, and the per-platform T2 assertion scripts. Its hash is a REGISTRY artifact at H-IN-2.

T0 invariants:
- no human holds dq_gov
- pipeline principals have no write on gated relations
- consumers have no base-table read
- dq_publish is the only writer of DP release tables
- CONFIRM requires step-up

## 3. Data model

### 3.1 Canonical keys on every gated row

Every gated row carries:
- `_dq_batch_id`
- `_dq_unit_key`
- `_dq_load_id`, the CP-registered load that SEAL copied
- `_dq_source_system`, `_dq_source_record_key`
- `_dq_file_id`, lineage to the inbound file (§13)

Facts are keyed by source record key. Identity resolves through the xref view, never as a stored resolved ID.

### 3.2 Unit grain

`unit_key = (program_code, authority_set_hash, partition)`. It is computed ONCE at SEAL and never changes.
- `authority_set_hash` is over the STATIC declared authority set: the union across rows of program, provenance, sensitivity and tribal authorities.
- Exactly one unit exists per (batch, program, partition).
- Dynamic authorities (e.g. INTAKE when a pre-split hop is non-PASS) are recorded on unit events and head.
- PRIMARY authority = authority(program_code).

Partition by domain:

| Domain | Partition |
|---|---|
| Claims | chain_id |
| Eligibility | eligibility_unit key |
| Provider | provider key |
| Reference | `*` |

### 3.3 Load path (uniform across platforms; B2)

1. Pipelines write ONLY to `<rel>__landing` (dbt materialization `dq_landing`; Spark/Airflow helpers). Each write registers a `load_id` with the CP first (`POST /loads`). Rows carry `_dq_load_id`.
2. `SEAL(batch)` is run by the CP using the `dq_seal` principal:
   1. The CP freezes the batch's load set (all registered, committed load_ids). It is stored in CP Postgres in a SERIALIZABLE txn.
   2. dq_seal copies landing rows with `_dq_load_id ∈ sealed set` into the gated relation, assigning `_dq_unit_key`. Each platform does this atomically per table: Snowflake txn, Delta single-table commit, BigQuery DML txn.
   3. A FINGERPRINT is computed in the DP (§5), then keyed in the CP.
   4. The CP records BATCH_SEALED plus unit heads and the first unit events in ONE Postgres transaction.
3. **Partial copy.** If the DP copy commits but the CP record fails, the unit is not sealed in the CP, so it is invisible (the views join the release table). Re-SEAL is idempotent: it deletes and recopies rows for (batch, sealed set) as dq_seal.
4. **Late rows.** Landing rows with an unregistered or post-seal load_id are an ERROR alert and become a new batch.

### 3.4 Versioning

- Gated silver is versioned: "current" = max by the contract `version_key` order.
- Duplicates are kept with `dup_of`.
- Late originals balance in their arrival batch.

### 3.5 Bitemporal eligibility

- Effective plus recorded time; `recorded_to` is derived.
- Overlap rules are keyed by (program, segment_type, eligibility_unit).
- eligibility_unit ∈ {INDIVIDUAL, HOUSEHOLD, ASSISTANCE_UNIT, CASE}.

### 3.6 Lifecycle (`model/lifecycle.py` ★; the transition table is the only definition)

| From | To | Via |
|---|---|---|
| LANDED | SEALED | SEAL |
| SEALED | EVALUATING | engine |
| EVALUATING | PENDING_DEPENDENCY / RE_EVALUATE_PENDING / HELD / PENDING_RELEASE / RELEASED / ERROR | engine; PUBLISH (mode A) |
| PENDING_DEPENDENCY / RE_EVALUATE_PENDING | EVALUATING | engine |
| HELD | PENDING_RELEASE / RELEASED / REJECTED / EVALUATING (RE_PIN) | DECIDE / CONFIRM / RE_PIN |
| PENDING_RELEASE | RELEASED / HELD (veto) | PUBLISH / veto |
| ERROR | REJECTED / SUPERSEDED / EVALUATING (retry) | REJECT / SUPERSEDE (H-IN-2) / engine |
| RELEASED ✔, REJECTED ✔, SUPERSEDED ✔ | — | terminal; post-release problems go to DENY |

- Any state not marked ✔ is non-terminal. A batch is terminal when all its units are terminal.
- **Single writer:** `lifecycle.transition(unit, expected_state_version, event)` is the ONLY path. In ONE Postgres transaction it appends the event and does a CAS on the head. It is enforced by a DB trigger plus a CHECK on allowed transitions.
- The same append-only + CAS pattern applies to: decisions (confirmations are rows), registry artifact_state, breaker, dispatcher/active-version pointer, DENY, roster map, AUDIT_HEAD.
- DENY is an append-only table read by released-view publication, WAIT and EALR.

## 4. Configuration, portability and the harness

### 4.1 State profile (`config/state/<ST>/profile.yaml`) (DEC-010)

- state code
- programs and program_code mapping
- authorities in use
- overlays (rules, thresholds, run-out windows, small-cell threshold, holidays)
- IdP config (issuer, claim names)
- DATA_SHARING_BASIS rows
- crosswalks (state codes → T-MSIS valid values)
- `deployment_profile` ∈ {DEMO, PILOT, PROD}

A new state = a new profile directory plus registry approval. There are no code changes.

### 4.2 Harness (`config/harness.yaml`) ★: any DAG or set of DAG pipelines

```yaml
pipelines:
  - id: claims
    platform: databricks            # snowflake | databricks | bigquery | duckdb (T1)
    orchestrator: airflow           # dbt | airflow | databricks_workflows | snowflake_tasks | composer | manual
    dag_source: {type: dbt_manifest, path: pipelines/claims/target/manifest.json}
    contracts: contracts/claims/
    gated_nodes: [brz_claim_header, slv_claim_chain, gld_claim_final]
    domains: [CLAIMS]
domain_dependencies:                # cross-pipeline, at domain level
  - {domain: CLAIMS, depends_on: ELIGIBILITY, policy: WAIT}
  - {domain: XREF,   depends_on: ELIGIBILITY, policy: EVALUATE_AGAINST_LAST_RELEASED}   # cycle break
```

What the harness does:
- **Validates:** every pipeline and node exists in its DAG source; every gated node has an APPROVED contract; the domain graph is acyclic after declared breaks; the platform matches the contract dialect.
- **Computes the global processing order:** a topological sort over the domain graph, plus intra-DAG order from each DAG source.
- **Enforces the order:** `dqagent gate` refuses (HOLD) a node whose upstream domain batch for the intersecting keys and period is non-terminal (WAIT), and labels the result APPROXIMATE under EALR.
- **CLI:** `dqagent harness validate | plan | run | gate | status`.
- `harness.yaml` is a REGISTRY artifact; non-cosmetic changes are H-IN-2.

### 4.3 Orchestrator gates (`adapters/orchestrator/*`)

| Orchestrator | Integration |
|---|---|
| dbt | `dq_gate` pre-hook / run-operation (non-zero → skip downstream) |
| Airflow / Composer | `DQGateSensor` (deferrable) + `DQSealOperator` |
| Databricks Workflows | a gate task that sets a task value; downstream uses an if/else condition task |
| Snowflake Tasks | a gate task calls a stub proc that reads the DP release table and sets the return value; dependents use `WHEN SYSTEM$GET_PREDECESSOR_RETURN_VALUE(...)='PASS'` |
| manual | CLI exit codes 0 = PASS, 75 = HOLD, 1 = ERROR |

On HOLD the gate never fails a task; it skips. The DATA-PLANE hold (released views) remains the primary guarantee.

### 4.4 Contracts (ODCS-like; all fields present day one, values may be DRAFT)

**Structure**
- grain, keys
- chain spec: chain_key, version_key + order, adjustment indicator, void semantics, reversal/rebill
- unit partition, eligibility_unit
- ordering class, depends_on with policy

**Loading and counting**
- consumer grains
- incremental class
- countable_terms per (format, claim_category)

**Tags and authority**
- `control_tags`: clinical | payment | coverage | program | identity | free_text | llm_summarisable. These are inside the semantic hash; removing one is a loosening.
- `dimension_tags`: cosmetic
- sensitivity: part2 | bh | tribal | minor | snap_confidential | tanf_confidential | fti | ssa
- field provenance authority (IE sources: undeclared ⇒ fail closed)

**Platform and governance**
- `platform_dialect`
- required_check_classes (may only RAISE the federal floor)
- declared consumers
- data product + steward duty

### 4.5 Rule IR

**Identity**
- rule_id, version, semantic hash
- origin: FEDERAL_TMSIS | FEDERAL_CMS0057F | FEDERAL_1927 | FEDERAL_FNS / FEDERAL_ACF (declared-empty) | STATE | LOCAL
- artifact_vocabulary + type

**Check definition**
- check_class: conformance | codeset_format | codeset_validity | validity | integrity | plausibility | reconciliation | timeliness | uniqueness | balance | envelope
- validation_type: INFERENTIAL (sourced) | DETERMINISTIC / LONGITUDINAL / BENCHMARK (COALITION-DEFINED)
- metric kind + scope class: BATCH | DELTA_VS_HISTORY | FULL. The gate blocks only on BATCH/DELTA.
- priority, thresholds, data_elements

**Applicability**
- claim_category, payer_type, item_type, program_code, plan_id, submitter_id, format, authority
- explicit `*`; NULL rejected

**Handling**
- benchmark
- row_severable, remediation_policy, governing-date rule

### 4.6 Federal pack, registry, overlays and genesis

**Federal pack** (`rules/federal_pack` ★) is a project-authored baseline, release-hashed. It is NOT a CMS-signed artefact. Contents:
- per-domain floors
- SCHIP × MEDICAID concurrency (SSA §2110(b)(1)(C)) with a retro-eligibility exception at H-IN
- PA: CMS-0057-F MEDICAL 72h / 7d; DRUG under FEDERAL_1927 24h (UNVERIFIED)
- NDC: NEVER remediate; shape by format
- GOVERNING_DATE_RULE seed
- envelope rules (§13)
- the list of COALITION-DEFINED rules
- STRUCTURAL programs (SNAP/TANF): the floor is the structural classes only; they are never mode A in iteration 1; their tile reads "STRUCTURAL — program rules 0/0 sourced"

**Registry** ★ covers contracts, packs, overlays, crosswalks, POLICY, provider/model policy, DATA_SHARING_BASIS, roles.yaml, harness.yaml, state profile and the deploy manifest.
- Each item has a per-rule semantic hash and a state of PENDING | APPROVED | REVOKED.
- Deploy ≠ activate.
- Pinning happens per batch at SEAL, with RE_PIN (tightening H-IN, loosening H-IN-2).
- The evaluation digest includes the POLICY hash.
- Validator: acyclic depends_on, NULL applicability rejected, no floor lowering.
- Ladder: cosmetic → A + notify; everything else → H-IN-2 (◇ tiered).
- `dqagent registry plan` runs in CI.

**ADVISORY** is allowed only when `deployment_profile=DEMO`. It carries an expiry and auto-revokes when its system loads. The floor is NOT met: "floor waived", and mode ≥ H-IN.

**Genesis** ★:
- The GENESIS record is the first audit entry.
- It is diffed against the federal baseline, and the ratifiers see the overlay deltas and the COALITION-DEFINED list.
- It needs a two-signature out-of-band ratification verified against the anchor.
- Gated nodes fail closed until ratified.
- The test genesis lives in `tests/` only.

## 5. Evaluation (pushed down per platform)

### 5.1 Compiler ★

- Rule IR → per-dialect AST builders for snowflake, databricks, bigquery and duckdb (T1).
- **No blind transpilation.** A capability probe found sqlglot rewrites Snowflake `TRY_TO_DATE`→Databricks `TO_DATE` (error vs NULL) and `SHA2`→BigQuery `SHA256` (BYTES vs hex). Each dialect has its own builder for every construct on the portability allowlist.
- Golden SQL is kept per dialect.
- A pure-Python oracle per metric kind.
- A divergence suite per dialect.
- ◇ fusion.

### 5.2 Fingerprint

1. The DP computes a canonical content digest over the sealed rows' declared columns: SUM of a 60-bit fold of SHA-256 over canonical serialisation, returned as a number plus a row count.
2. The CP computes `HMAC(K_FP_v, digest‖count‖batch‖unit)` and stores it with `fp_key_version`.
3. Recompute is pure.

### 5.3 ABC ★

`residual = src_in − (tgt_out + rejected + quarantined + deduplicated + filtered_by_contract + held)` per (hop, measure, program, plan_id, submitter_id, claim_category, format, authority).

- Hops include FILE → RAW → BRONZE (§13).
- Hash totals are HMAC tokens computed IN-DP (§9.1, K_ABC_v), folded.
- The NULL-key count is its own measure.
- Pre-split hops are owned by INTAKE.

### 5.4 Dependencies

- The vector records the intake HWM per depended domain plus the state_version vector of intersecting units.
- **WAIT** blocks while an intersecting depended unit is non-terminal.
- **EALR** labels the result APPROXIMATE.
- Clause 3 requires vector equality for WAIT edges.

### 5.5 Other evaluation concerns

- **Cost:** per-platform query tags/labels (Snowflake QUERY_TAG, Databricks query comment + cluster tags, BigQuery job labels). A credit/slot budget ★; exhaustion gives NOT_EVALUATED.
- **Identity** (DEC-005): validate the state xref (split/merge drift, orphans, cross-ID overlap within an authority or IE-INTEGRITY scope). Not loaded → SYSTEM_NOT_LOADED. Identity-dependent measures are APPROXIMATE unless validated. ◇ provider shadow clusters (NPPES benchmark).
- **Terminology:** format checks are real. An interval registry is used for validity. ◇ the validity load (DEMO uses ADVISORY). Licensed systems ship zero real rows (CI test).
- **Profiling:** per-column baselines are persisted per (relation, column, period). The profiling agent feeds LONGITUDINAL rules (Z-score and IQR drift on volumes, rates and dollars; isolation forest ◇) and the rule-proposal agent.

## 6. Outcomes and scoring (`model/outcome` ★)

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

- Excluded from the ranking: NOT_APPLICABLE_FOR_FORMAT and ADVISORY. Neither satisfies a floor.
- Only PASS counts for scoring.
- Empty worst-of = NOT_EVALUATED.
- KNOWN_ISSUE = WAIVE (H-IN-2).
- High-priority rules use worst-of; Med/Low use bands. No blended index.
- Coverage, exposure, as_of and period are always shown.
- Periods stay PROVISIONAL in iteration 1.
- FULL is computed (evaluable, balanced, oracle = SQL, Wilson LB ≥ 0.6 over ≥5×3 seeded defects, ledger from a separate seat), else PARTIAL.
- **Steward scorecard per data product:** bands, coverage, open cards, waivers, SLA.

## 7. Release (single predicate in the API ★)

`can_release(unit)` is TRUE iff all of the following hold:
1. **Fingerprint:** the recomputed fingerprint equals the bound one (same fp_key_version).
2. **Approved and deployed:** every digest artifact hash is APPROVED, and the running API/worker version and DP kit versions match DEPLOY_MANIFEST.
3. **Dependencies:** WAIT edges have state_version vector equality; otherwise RE_EVALUATE_PENDING. EALR edges compare only RELEASED changes and DENY.
4. **Decision:** either
   - mode == A, recomputed from the digest (rank ≥ 4 is never A; STRUCTURAL is never A; floor waived is never A), or
   - a DECISION bound to (fingerprint+version, proposed_quarantine_set_hash, bound_disposition_vector) is still valid, with the required distinct person_ids, and a STEP-UP-verified checker for H-IN-2.
5. **Veto window:** no open window. Notify is ◇, so H-ON degrades to H-IN.
6. **Breaker:** the per-program breaker is not tripped.
7. **Approver authority:** approver duties ⊇ authorities(unit) = program ∪ provenance ∪ sensitivity ∪ tribal ∪ (INTAKE if a pre-split hop is non-PASS). The decision is made under the PRIMARY authority; co-signers confirm under theirs; the LAST CONFIRM applies the quarantine and the release atomically.
8. **Period:** the period is not FINAL (never reached in iteration 1).
9. **Tower coverage:** every authority in authorities(unit) has a Tower scope enabled. Iteration 1 enables MEDICAID and SNAP. Otherwise the unit is NOT_IMPLEMENTED and held.

**Quarantine.** Key = (batch, unit, `_dq_source_record_key`) with scope ROW | CHAIN | UNIT and a nullable chain/version.

**Publication to the DP.** On RELEASED, the CP publisher (dq_publish) writes `DQ_REL.RELEASED_UNITS(batch, unit, load_set_hash, fingerprint, released_at)` plus the open QUARANTINE and DENY keys into EACH DP that holds the relation. It is idempotent and versioned. Released views are then:

| Platform | Released view |
|---|---|
| Snowflake | SECURE VIEW `<rel>_RELEASED` |
| Databricks | Unity Catalog VIEW (consumers granted on the view only; row filters ◇) |
| BigQuery | AUTHORIZED VIEW in a consumer dataset |

Each released view is an INNER JOIN of gated rows to RELEASED_UNITS on (batch, unit), with ANTI-JOINs to open QUARANTINE and DENY.

**Publication guarantees:**
- A publication lag (CP released, DP not yet published) leaves the data invisible. That is fail-closed.
- The CP verifies each DP publication by reading it back and comparing a hash.

**Consumption.** Derived per (unit, consumer) from (`_dq_batch_id`, `_dq_unit_key`) in targets. Aggregate gated targets are ◇ refused.

**PUBLISH:**
- drains strictly in order per (ORDERED domain, partition) and stops at the first non-terminal unit
- ◇ skip/groups
- liveness tiles

**Re-fingerprint ★.** Released, unconsumed units are recomputed in the DP. A mismatch writes DENY and opens a card.

**Binder ★ refuses:**
- writes to gated relations by non-seal principals
- WATERMARK/UNKNOWN incrementals
- aggregate gated targets
- groups
- authorities without a Tower scope
- floor lowering
- undeclared IE fields

## 8. HITL and Command Tower

**Modes** A / H-ON / H-IN / H-IN-2: `mode = max(POLICY[...], FLOOR[verb])`. States may tighten, never loosen.

**Verbs:** HOLD, QUARANTINE_ROWS, RELEASE, REJECT, SUPERSEDE, REQUEST_RESEND, NOTIFY_SUBMITTER, WAIVE, CHANGE_RULE, RE_PIN, REBIND, UNFREEZE_BREAKER, APPLY_SAFE_TRANSFORM, PROPOSE_MERGE, PROPOSE_RULE (from the profiling agent). ◇ BREAK_GLASS (refused), ◇ RESTATE, ◇ PERIOD_CLOSE, ◇ BACKFILL.

**Floor (H-IN-2):**
- release after ABC failure
- identity actions
- waivers
- loosening
- rule proposals
- roles.yaml / harness.yaml / state profile / DATA_SHARING_BASIS / destination changes
- submission-extract release
- genesis

**Injection points:** triage → hold → propose (dry-run diff) → H-ON veto → release (ABC before/after) → learn (rule / waiver / merge / proposal). Timeouts fail closed and escalate.

**SoD** (in the API, with CP DB constraints as backstop):
- not the pipeline owner (IdP group)
- not the author
- no self-affecting change
- distinct person_ids (UNIQUE constraint)

**Audit:**
- Append-only AUDIT_LOG with an AUDIT_HEAD CAS.
- The chain hashes digests.
- The external ANCHOR carries H(audit_head). The T1 sink is a local append-only file; prod uses S3 Object Lock / Azure immutable blob / GCS bucket lock.

**Live checks.** The heartbeat verifies:
- deployed image digests vs manifest
- CP DB function definitions hash vs manifest
- per-DP grants and released-view DDL hashes vs manifest (`SHOW GRANTS` / Unity Catalog grants / BigQuery IAM policy)
- the DP release table vs CP master

It runs outside the CP's own runtime.

**Command Tower** (Next.js; DEC-008): a client of the API. The OIDC token is forwarded and the API authorizes per request.
- **Read path** = API endpoints that apply authority scoping and `presentation/suppress` SERVER-SIDE (the only suppression site). The Tower never connects to a database.
- **Top band:** genesis status, held units, decisions due, oldest SLA, mine, ABC status, liveness, LLM health, pipeline order status.
- **Real screens:**
  - Decision Queue (deterministic clusters partitioned by authority)
  - Decision Card (all evidence, dry-run diff, ABC before/after, collapsed LLM hypothesis panel, typed rationale, step-up for the checker)
  - Pipeline Map (the harness: pipelines, cross-DAG order, gate states)
  - ABC Board (file → raw → bronze → silver → gold)
  - Evidence
  - Steward Scorecard
- **Read-only screens:** interventions, registry/waivers, audit explorer, agent activity (mode() trace).
- Iteration 1 enables the MEDICAID and SNAP authority scopes in one Tower (scope selection by duty).

**Presentation.** Small-cell suppression (overlay threshold, default &lt; 11, plus complementary suppression) applies outside a cell's own authority. The gate computes unsuppressed values.

## 9. Evidence and LLM

### 9.1 In-DP tokenization ★ (raw keys never leave the DP)

`token = HMAC(K_EV_v, authority‖program‖rule_id‖period‖canon_key)`, implemented per platform:

| Platform | Mechanism | Tag |
|---|---|---|
| Snowflake | Python UDF with SECRET binding | T2 |
| Databricks | SQL UDF over `sha2(concat(secret('dq','k_ev'), …))` or a Python UDF with a secret scope | T2; HMAC construction verified at T2 |
| BigQuery | `DETERMINISTIC_ENCRYPT` with a KMS-wrapped keyset (KEYS.KEYSET_CHAIN), hashed | T2 |
| DuckDB (T1) | Python UDF with TEST_KEY | T1 |

- K_ABC_v is a separate key family for ABC hash totals.
- Key versions are stored. Samples are ordered by token.
- EVIDENCE_PAYLOAD (CP) holds tokens and shape classes only. Per-retention-class keys; ◇ shred.

### 9.2 LLM

- Typed prompt builder: opaque names `^[A-Z0-9_]{1,64}$`; codes as registry ids; descriptions from FEDERAL rows only.
- Failing values are never sent. `llm_summarisable` fields only, via suppress.
- Output: a closed-taxonomy hypothesis + cited (rule_id, pinned hash) + a confidence band, rendered by template.
- The LLM never originates WAIVE, loosening or PROPOSE_RULE. No FTI ever.
- Injection fuzz test.
- Providers: NullProvider by default. At T1 a POLICY row selecting another provider is refused. Cortex / Anthropic / Azure OpenAI / Vertex adapters are emitted, T2. Selection is H-IN-2, with region/BAA flags.

## 10. Verification

| Tier | Coverage |
|---|---|
| T0 | Unit and invariant tests |
| T1 | Real Postgres CP (local postgres 16) + FastAPI + DuckDB data plane (dbt-duckdb sample pipelines) + fakesnow for Snowflake-dialect logic + per-dialect golden SQL and divergence suite for Databricks/BigQuery. **A T1 pass is not evidence of Snowflake/Databricks/BigQuery compatibility.** |
| T2 | Per-platform kits: `dqagent verify --platform snowflake|databricks|bigquery` |

Each T2 kit covers:
- construct-coverage smoke
- grant assertions from roles.yaml
- the divergence suite
- released-view enforcement (a consumer cannot read the base table; quarantined rows are invisible)
- in-DP tokenization
- SEAL atomicity
- gate integration for that platform's orchestrator
- publication read-back

Every DP artefact is tagged T2-REQUIRED:&lt;platform&gt;.

## 11. Sample pipelines (`pipelines/`; dbt projects + harness config) ★

There are seven pipelines over all domains. Each is a dbt project that can target duckdb (T1) / snowflake / databricks / bigquery through profiles. Gated nodes are written through `dq_landing` plus SEAL.

| # | Pipeline | Domains | Key DAG features |
|---|---|---|---|
| P1 | reference | MCR plans, code sets, crosswalks, governing-date rules | UNORDERED; feeds everyone |
| P2 | provider | provider enrollment spans, locations, taxonomy, affiliations, NPPES extract | depends on P1 (taxonomy) |
| P3 | identity_xref | state MCI crosswalk (members across programs) | depends on P4 by EALR (cycle break) |
| P4 | eligibility_ie | integrated eligibility (Medicaid, MCHIP, SCHIP, SNAP, TANF; households), enrollment, MC enrollment, 834 inbound | depends on P3 (WAIT), P1; per-program split at bronze (INTAKE hop) |
| P5 | pa_tpl | prior authorization (278-like / portal), TPL coverage | depends on P4, P2 |
| P6 | claims | CIP / CLT / COT / CRX × FFS / encounter / capitation / subcap; 837P/I/D, NCPDP; adjustment/void/reversal chains | depends on P4, P2, P5, P1 (WAIT) |
| P7 | tmsis_extract | T-MSIS-shaped CIP/CLT/COT/CRX/ELG/PRV/TPL/MCR + gold marts | depends on all; federal T-MSIS rules bind here |

**Global order** (computed by the harness): P1 → P2 → P3 ⇄ P4 (cycle broken by EALR) → P5 → P6 → P7.

**Scenarios exercised:**
- fan-in
- a late original after its adjustment
- retro eligibility → claims re-evaluation
- a held ELG batch holding only intersecting claims partitions
- a cross-program household
- MCO encounter lag
- capitation vs encounter
- a duplicate file
- an envelope count mismatch
- a SCHIP × Medicaid overlap
- a drug PA vs a medical PA clock

## 12. Synthetic data

`synthetic/` generates a coherent state population:
- persons → households/cases → program enrollment across MEDICAID / MCHIP / SCHIP / SNAP / TANF
- providers with NPI (Luhn) and atypical providers without one
- MCOs, PA, TPL
- claims of all types, with chains

It emits INBOUND FILES:
- X12 005010 837P/837I/837D, 834, 835, 270/271, 278
- NCPDP-like batch
- state flat files with trailers
- IE extracts

It has two modes:
- **realistic defect-blind mode:** late files, MCO lag, retro eligibility, dual churn
- **seeded mode:** ≥5 defect classes × 3 per cell

The ground-truth ledger is authored by a SEPARATE seat and hash-committed before rules are written. Licensed code systems use a reserved synthetic shape space.

## 13. Raw ingestion: inbound files feeding the medallion (DEC-009) ★

The **file intake service** (`ingest/`) works as follows:
1. A landing zone (S3 / ADLS / GCS / local) is registered per trading partner.
2. Each file gets a `file_id`, a SHA-256 and the trading partner from a TRADING_PARTNER registry (registry artifact).
3. Raw structural records go to `raw_<format>__landing`.
4. SEAL writes them to `raw_<format>`.
5. Bronze mapping happens per transaction set.

**Parsers:**
- linuxforhealth-x12 (Apache 2.0; 834, 835, 270/271, 276/277, 837P, 837I) where it covers the transaction set
- an in-house structural parser for 837D and 278 (envelope + segment level)
- NCPDP-like batch and flat-file parsers with declared layouts

**Envelope / file-level checks** (check_class `envelope`, federal pack, COALITION-DEFINED unless cited):
- ISA/IEA control number match
- IEA group count = actual
- GS/GE control match and GE transaction count = actual
- ST/SE control match and SE segment count = actual
- an interchange control number that duplicates a prior file from the same sender → duplicate-file hold
- control-number sequence gaps per sender
- sender/receiver IDs ∈ TRADING_PARTNER
- usage indicator (P vs T) matches the environment
- file arrival SLA
- duplicate file hash
- NCPDP / flat-file header-trailer record counts and amount totals

**File-level ABC.** FILE → RAW (segments/records) → BRONZE (claims/members) balances on:
- record/transaction counts
- trailer amounts where the format carries them; otherwise NOT_APPLICABLE_FOR_FORMAT
- the CLM02 total-charge sum vs line sums

**Acknowledgments.** A ◇ 999 / TA1 generation stub (NOT_IMPLEMENTED, listed).

## 14. Iteration-1 manifest

★ = invariant. ◇ = fails closed and is listed.

**Model and roles**
- `model/` ★ (outcome, rule, contract, policy, lifecycle)
- `roles/` ★ (Protocols); ◇ MAF adapter

**Registry, governance and configuration**
- `registry/` ★; ◇ tiered ladder
- `governance/` ★ (roles.yaml → CP + 3 DP grant generators; genesis; anchor; heartbeat CLI)
- `config/` ★ (state profile schema + a sample state "ZZ"; harness.yaml)
- `harness/` ★ (validate, plan, order, gate)

**Adapters**
- dag: dbt manifest ★, snowflake_tasks (generated, T2), airflow (DAG file parse ◇)
- metadata: DataHub ◇ read-only
- orchestrator: dbt ★, airflow sensor/operator (T1 unit-tested, T2), databricks_workflows (T2), snowflake_tasks (T2)
- dataplane: duckdb ★ T1, snowflake / databricks / bigquery (generated + T2 kits)

**Compilation and evaluation**
- `compiler/` ★ (4 dialect builders, golden, oracle, divergence)
- `engine/` ★ (load registry, SEAL, fingerprint, dependency vector, budget)
- `abc/` ★
- `profiling/` ★ (baselines + Z/IQR drift); ◇ isolation forest

**Ingest**
- `ingest/` ★ (intake, envelope checks, X12 via linuxforhealth + structural 837D/278, NCPDP-like, flat files); ◇ 999/TA1

**Release and HITL**
- `intervention/` ★ (publish to DP, consumption, drain, quarantine, DENY, re-fingerprint, breaker)
- `hitl/` ★ (single predicate, modes, floor, SoD, step-up, CONFIRM)

**Evidence, terminology, identity, LLM**
- `evidence/` ★ (in-DP tokenizer interface + 4 impls; T1 duckdb)
- `terminology/` (format real; GOVERNING_DATE_RULE ★); ◇ validity load
- `identity/` (xref validation); ◇ provider clusters
- `llm/` ★ (builder, taxonomy, NullProvider); ◇ live providers

**API and Tower**
- `api/` ★ (FastAPI; OIDC with a local test IdP at T1; read endpoints with suppress)
- `tower/` (Next.js): queue, card, pipeline map, ABC board, evidence, scorecard real; others read-only
- `presentation/suppress` ★

**Pipelines, data, tests, deploy and tools**
- `pipelines/` P1–P7 (dbt) ★
- `synthetic/` ★
- `tests/`: t0, t1 (Postgres + DuckDB + fakesnow), oracle, divergence, golden; computed FULL/PARTIAL matrix → `docs/EXIT_CRITERIA.md`. The first T1 tests are the seal race, released-view hiding, DENY, transitions and per-partition drain.
- `deploy/`: docker-compose (CP + Postgres + Tower), per-platform DP kits; all DP artefacts T2-REQUIRED
- `tools/pair_matrix.py` + `docs/mechanisms.yaml`

**EXIT_CRITERIA must state:**
- "ALL programs are EVALUATED. In iteration 1, only units whose authorities ⊆ {MEDICAID, SNAP} are RELEASABLE."
- "No platform is verified until its T2 kit passes."
- "DataHub, Airflow DAG parsing and the MAF adapter are stubs."

**Deferred pre-PHI:**
- external WORM sink
- DP row filters / column masking per authority
- LLM provider BAA / region
- retention and shred
- CI-only image signing + SBOM
- DR for the CP DB (PITR, cross-region)
- pen test
- predicate cost budget at CLT scale
