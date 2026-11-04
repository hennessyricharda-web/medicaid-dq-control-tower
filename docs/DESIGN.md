# Medicaid / HHS Data Quality Agent — Platform-Independent Design v2.1

| | |
|---|---|
| **FRAME** | A portable, configuration-driven Data Quality control plane. It sits **behind the state's EDI translator and MMIS** and governs the medallion (raw → bronze → silver → gold) and the **outbound** files the agency is liable for. It can attach to any set of DAG pipelines feeding Snowflake, Databricks or BigQuery. It serves any state Medicaid client plus HHS programs, tailored by configuration plus a per-state adapter pack. |
| **SHAPE** | Python package `dqagent`, split into separately deployed services: api, seal-worker, publisher, evaluator, verifier. A Postgres control-plane DB. A Next.js Command Tower. Per-platform data-plane kits. Eight sample pipelines. A synthetic generator emitting inbound and outbound files. Tests and per-platform T2 kits. |
| **DONE (iteration 1)** | A "T1-verified deploy candidate" (DEC-003), built in waves W0–W4 (§15). ★ invariants are implemented and tested at T0/T1. ◇ stubs fail closed and are listed in EXIT_CRITERIA. Every DP artefact is tagged T2-REQUIRED:&lt;platform&gt;. "Equal depth" across platforms means equal ARTIFACT coverage generated from one IR; it does not mean equal verification. |

**History.** v1.1 (Snowflake-native) is kept as `history_DESIGN_v1.1_snowflake_native.md`. v2.0 is kept as `history_DESIGN_v2.0.md`. This file supersedes both. Findings and decisions live in the blackboard.

**Decisions in force:**

| ID | Decision |
|---|---|
| DEC-001 | Review cap of 5 rounds |
| DEC-003 | Deploy candidate + T2 kits |
| DEC-004 | ALL domains |
| DEC-005 | Validate the state identity index; provider shadow clusters |
| DEC-006 | ALL HHS programs |
| DEC-007 | Not inside Snowflake; Databricks/BigQuery equal |
| DEC-008 | FastAPI + Next.js |
| DEC-009 | Three platforms + raw ingestion |
| DEC-010 | Any state, portable via configuration |
| DEC-011 | Approved |

---

## 1. What the agent is

A deterministic engine binds, evaluates, balances, scores, sets the mode, gates and releases. An LLM advises only, using a closed taxonomy (§9). It never feeds mode() and is never evidence or a decision of record.

### 1.1 Roles are interfaces (`dqagent/roles`)

Iteration 1 uses a native runner. ◇ A Microsoft Agent Framework adapter may later host the advisory and routing roles. Decisions always stay in the control plane (CP).

| Role | Module | Decides? |
|---|---|---|
| Metadata Agent (Observer) | adapters/dag (dbt manifest ★), adapters/metadata (DataHub ◇ read-only) | no |
| Profiling Agent | profiling/ under the §5.6 safety rules | no |
| Quality Rules Agent | registry/proposals: PROPOSE_RULE cards at H-IN-2, each with a backtest; never auto-activated | no |
| Binder | contracts/binder | refuses only |
| Executor / ABC / Terminology / Identity | compiler, engine, abc, terminology, identity | no |
| Policy Compliance (Supervisor) | hitl/modes | **yes (deterministic)** |
| Remediation (Intervention Planner) | intervention: closed verb set, dry-run diffs, never rewrites data | proposes |
| Human Approval | Command Tower + API. A HUMAN decides; routing only queues and escalates | human |
| Data Product Steward | steward duty + scorecard (§6) | human |
| Triage Advisor / Evidence Scribe | llm, evidence | no |

### 1.2 Loop per (pipeline, node, batch)

`INTAKE → LAND → SEAL → BIND → PROFILE → EVALUATE → BALANCE → ASSESS → MODE → PROPOSE → [HITL] → RELEASE → PUBLISH → VERIFY → EVIDENCE → LEARN`

## 2. Architecture and trust model

### 2.1 Control plane (CP) and data planes (DP)

**CP services.** Each is its own deployment with its own workload identity and no static secrets (S10).

| Service | Holds | Has no |
|---|---|---|
| **api** (FastAPI) | Decision logic and the human-facing endpoints | DP credentials |
| **evaluator** | DP read for evaluation (`dq_eval`) | — |
| **seal-worker** | DP gated-write (`dq_seal`) | — |
| **publisher** | DP manifest-write (`dq_publish`) | — |
| **verifier** | Independent identity on separate compute. Re-verifies decision JWS signatures, recomputes fingerprints, diffs CP against DP publications, hashes DP function bodies, grants and storage IAM. Writes VERIFIER_ATTESTATION rows that `can_release` requires (S1, S2, S6). | — |

- Per-DP credentials are workload identities: Snowflake key-pair or External OAuth, Databricks OAuth M2M with federation, GCP Workload Identity Federation.
- The state profile names the **hosting tenancy**, approved at H-IN-2 (S10).
- If FTI-scoped units are processed, CP operators fall under Pub 1075 review (T2 checklist).

**CP DB = Postgres.** It enforces UNIQUE / FK / CHECK constraints, row locks and SERIALIZABLE isolation.

**DPs = Snowflake | Databricks (Unity Catalog) | BigQuery**, each behind `adapters/dataplane/<platform>`.
- Checks are pushed down as SQL.
- Only keyed aggregates, tokens, counts and shape classes leave the DP. **Raw values never enter the CP.** Sample keys are tokenized in the DP. Fingerprints and hash totals are keyed in the DP (§5.2).

**Network.** CP to DP connectivity is private (PrivateLink / Private Service Connect). This is a deployment note and a T2 item.

### 2.2 Trust boundary (stated honestly; S-frame)

The boundary is **the CP + the publisher channel + each DP's permission model, including storage-layer IAM**.

The TCB (trusted computing base) includes:
- the CP DB superuser
- cloud and k8s admins of the hosting tenancy
- **IdP administrators**: anyone who can manage groups or users (S4)
- per DP: Snowflake ACCOUNTADMIN / SECURITYADMIN, Databricks metastore / workspace / account admins plus cloud storage admins, BigQuery project owner / IAM admin

Against everyone outside the TCB, the system is tamper-resistant. Against the TCB it is tamper-evident only once the external anchor and verifier are live.

### 2.3 Legal-authority trust domains

| Authority | Notes |
|---|---|
| AUTH_MEDICAID | Includes MCHIP |
| AUTH_CHIP | Separate CHIP |
| AUTH_SNAP | |
| AUTH_TANF | |
| AUTH_FTI (IRC §6103) | Duties held by state employees only (`employment_type=STATE`) |
| AUTH_SSA | |
| AUTH_PART2 | Part 2 program records only |
| AUTH_TRIBAL:&lt;id&gt; | Tribal / IHS / 638 / urban program data or a tribal DUA |
| AUTH_INTAKE | |
| AUTH_STATE:&lt;x&gt; | |

Crossing authorities requires a DATA_SHARING_BASIS row at H-IN-2.

### 2.4 Human identity, duties and roster (S1, S4)

**Authentication.** OIDC with the state IdP. `aud` = the api. ID tokens are rejected as bearer tokens.

**Effective duty** = IdP group claim **AND** an active CP ROSTER entry.
- ROSTER is append-only, changed only at H-IN-2 (it is on the floor), with its seed hash in GENESIS.
- On decision endpoints, revocation is checked by token introspection or CAEP/SSF signals. Claim staleness until `exp` is NOT trusted for decisions.
- Group overage (e.g. Entra `_claim_names`) fails closed.
- The heartbeat diffs IdP group membership (SCIM / Graph) against ROSTER.

**person_id** = IdP subject + employee-id claim, mapped in ROSTER. Users without a mapping hold no duty.

**Decision binding (S1).** For every DECIDE and CONFIRM:
1. The api issues a `challenge = H(unit, fingerprint, fp_key_version, proposed_quarantine_set_hash, bound_disposition_vector)`.
2. The checker's step-up request carries the challenge (OIDC `nonce` or RAR `authorization_details`).
3. The api verifies:
   - the signature against the IdP's JWKS
   - `aud`
   - `exp`
   - `auth_time` ≤ 5 min
   - the step-up claim (`acr`, or `acrs` for Entra, or `amr`, as configured in the state profile)
   - that the challenge matches
   - effective duty
   - SoD
4. The decision row stores the **raw signed JWS**, `jti` (UNIQUE), `auth_time`, `kid` and the challenge.
5. The **verifier** independently re-verifies each JWS against an archived JWKS and writes a VERIFIER_ATTESTATION.
6. `can_release` requires that attestation.

Forging a decision therefore requires compromising both the api and the verifier, or the IdP, which is a declared TCB member.

### 2.5 Principals (`governance/roles.yaml` ★)

`roles.yaml` generates the CP grants, the per-DP DDL / IAM for all three platforms, and the per-platform T2 assertions. Its hash is a REGISTRY artifact at H-IN-2.

**Per-DP principals (S5):**

| Principal | Type | Holds | Must not hold |
|---|---|---|---|
| dq_data_owner | no-login | Owns gated relations, manifest tables and released views | — |
| dq_deploy | CI only | Creates objects and transfers ownership to dq_data_owner | Data privileges |
| dq_seal | service | Write on gated relations; read on landing | — |
| dq_publish | service | Write on the manifest tables only | — |
| dq_eval | service | Read on gated relations, landing and reference; execute on DQ functions | Write on gated |
| dq_tokenize | service | Key access; tokenizer EXECUTE (rate-capped) | — |
| dq_verify | service, separate compute | Read only | — |
| pipeline_&lt;id&gt; | service | INSERT on its OWN landing tables (append-only, §3.3); read on `<rel>_RELEASED` for its upstream relations | ANY read or write on gated base relations (explicit deny, D10) |
| consumers | — | Read on `<rel>_RELEASED` only | Landing (included in the base-table invariant) |

**Per-platform hardening:**

| Platform | Controls |
|---|---|
| Snowflake | Managed-access schemas. dq_data_owner owns. No time travel or clone privileges for consumers. |
| Databricks | Compute policies allow only UC access modes (dedicated compute ≥ 15.4 with serverless filtering, or standard mode). Storage IAM denies everyone except the UC storage credential. Inventory and lifecycle rule for the DBFS-root staging of filtered results (PHI at rest). |
| BigQuery | Datasets created with explicit `access` (no `specialGroup` entries), plus an org policy. Released views are authorized views in consumer datasets. Copy/export denied by IAM on gated datasets. |

**CP Postgres (S8):**
- `dq_gov` owns the schemas; no human holds it.
- `dq_api` and the other service roles have no CREATE on any schema.
- TRUNCATE is revoked, with a BEFORE TRUNCATE trigger.
- `ALTER DEFAULT PRIVILEGES … REVOKE EXECUTE … FROM PUBLIC`.
- Every SECURITY DEFINER function has `SET search_path = pg_catalog, dq_ctl, pg_temp`.
- `dq_migrate` is a CI-only role. Event triggers write DDL to the audit chain.

**Row-level security (S9)** with FORCE on every results, evidence and unit table, keyed by authority.
- A definer function sets the request scope (`SET LOCAL`) from the verified token record.
- An unscoped request sees zero rows.
- `dq_auditor` sees only the audit tables and suppressed views.

**T0 invariants:**
- No human holds dq_gov or dq_data_owner.
- Pipeline principals have no read or write on gated base relations.
- Consumers have no base or landing read.
- dq_publish is the only writer of manifests.
- The api has no DP credential.
- CONFIRM requires a challenge-bound step-up.
- RLS is FORCE'd on every scoped table.

**Pipeline → CP authentication (S-missed).** Each pipeline_&lt;id&gt; authenticates to the CP with its workload identity. It may register loads, mark commits and request SEAL **only for relations bound to it in harness.yaml**.

## 3. Data model

### 3.1 Canonical columns on every gated row

| Column | Meaning |
|---|---|
| `_dq_batch_id` | Batch |
| `_dq_unit_key` | Release unit (§3.2) |
| `_dq_load_id` | Load the row came from |
| `_dq_row_seq` | Per-row seal sequence. Snowflake: sequence. Databricks: identity column. BigQuery: (load_id, ordinal) (S3) |
| `_dq_source_system`, `_dq_source_record_key` | Source identity |
| `_dq_file_id` | Lineage to the file registry (§13), including direction and translator acceptance |

Facts are keyed by source record key. Identity resolves through the xref view.

### 3.2 Unit grain

`unit_key = (program_code, authority_set_hash, partition)`.
- Computed ONCE at SEAL.
- `authority_set_hash` is the STATIC declared set: program ∪ provenance ∪ sensitivity ∪ tribal.
- Exactly one unit per (batch, program, partition).
- Dynamic authorities (INTAKE) are recorded on events and the head.
- PRIMARY authority = authority(program_code).

Partitions by domain:

| Domain | Partition |
|---|---|
| Claims | chain_id |
| Eligibility | eligibility_unit key |
| Provider | provider key |
| Finance | payment / remit id |
| Reference | `*` |

### 3.3 Load path (S3)

1. **Landing is append-only.**
   - Snowflake: INSERT-only grant.
   - Databricks: `delta.appendOnly=true`. The heartbeat asserts the property is still set.
   - BigQuery: writes go through load jobs / the Storage Write API into landing. Tamper is caught DETECTIVELY: freeze digests are re-checked with `FOR SYSTEM_TIME AS OF`, and BigQuery offers no insert-only IAM. This is stated in EXIT_CRITERIA.
2. **Register then write.** Every write first registers a `load_id` (`POST /loads`, pipeline workload identity). Rows carry `_dq_load_id`.
3. **SEAL(batch)** runs on the seal-worker:
   - The CP freezes the load set in SERIALIZABLE isolation. Per load_id it stores the landing **table version / timestamp** (Delta `VERSION`, BigQuery snapshot time, Snowflake `AT` timestamp) and the **keyed landing digest**.
   - dq_seal copies rows **AS OF** the frozen version into the gated relation, assigning `_dq_row_seq` and `_dq_unit_key`. This is an atomic single-table commit (Delta `REPLACE WHERE` for re-SEAL).
   - The fingerprint is computed in the DP (§5.2).
   - The CP records BATCH_SEALED, unit heads, first events and the per-unit `max_row_seq` in ONE transaction.
4. **Re-SEAL** reads AS OF the frozen versions and must reproduce the frozen landing digests. Otherwise it is an ERROR plus a DENY.
5. **Late rows.** Unregistered or post-seal load_ids cause an ERROR alert and become a new batch.

### 3.4 Versioning and eligibility

- **Versioning.** Silver is versioned: current = max by the contract `version_key` order. Duplicates are kept with `dup_of`. Late originals balance in their arrival batch.
- **Bitemporal eligibility.** `recorded_to` is derived. Overlap is keyed by (program, segment_type, eligibility_unit). Continuous-eligibility rules come from the state profile.

### 3.5 Lifecycle (`model/lifecycle.py` ★; this table is the only definition)

| From | To | Via |
|---|---|---|
| LANDED | SEALED | SEAL |
| SEALED | EVALUATING | engine |
| EVALUATING | PENDING_DEPENDENCY / RE_EVALUATE_PENDING / HELD / PENDING_RELEASE / ERROR | engine |
| EVALUATING | RELEASED | PUBLISH (mode A) |
| PENDING_DEPENDENCY / RE_EVALUATE_PENDING | EVALUATING | engine |
| HELD | PENDING_RELEASE | DECIDE / CONFIRM |
| HELD | RELEASED | DECIDE / CONFIRM |
| HELD | REJECTED | REJECT |
| HELD | EVALUATING | RE_PIN |
| PENDING_RELEASE | RELEASED | PUBLISH |
| PENDING_RELEASE | HELD | VETO |
| ERROR | REJECTED | REJECT (H-IN-2) |
| ERROR | SUPERSEDED | SUPERSEDE (H-IN-2) |
| ERROR | EVALUATING | engine retry |
| RELEASED ✔ / REJECTED ✔ / SUPERSEDED ✔ | — | terminal |

**Single writer.** `transition()` appends the event and CASes the head in ONE Postgres transaction. A DB trigger plus a CHECK, generated from this table, enforce it. Append-only + CAS also applies to:
- decisions
- registry state
- breaker
- active-version pointer
- DENY
- ROSTER
- AUDIT_HEAD
- manifest versions

**DENY** is append-only. A DENY removes the unit from the published allow-list (§7.2).

**DEPENDENCY_CHANGED ★ (D2).** When a depended unit is released, denied, rejected or superseded, each intersecting dependent unit gets:
- a re-evaluation, if it is not yet consumed; or
- a read-only **RESTATE_REQUIRED** card, if consumed. Execution is ◇ RESTATE.

## 4. Configuration, portability, harness

### 4.1 Portability in three tiers (D8)

| Tier | What it is | Change cost for a new state |
|---|---|---|
| (a) | CP, engine, Tower, rules engine | Zero code |
| (b) | **Canonical silver contracts** aligned to T-MSIS v4 segments, so federal rules are portable | — |
| (c) | **Per-state adapter pack** (`config/state/<ST>/adapter/`): dbt mapping models from the state's MMIS-vendor extracts, translator outputs, IE system, PBM extract and MCO encounter formats into canonical bronze/silver | This is code, versioned, approved at H-IN-2 |

So "a new state" = a profile (zero code) plus an adapter pack (mapping code).

### 4.2 State profile (`config/state/<ST>/profile.yaml`)

**Identity and programs**
- state code
- fiscal-year start
- programs and program_code mapping
- aid category → T-MSIS eligibility group map
- delivery-system mix (program × population × FFS / MCO / ASO / PCCM)
- waiver catalog (1115 / 1915(b) / 1915(c) → T-MSIS waiver id/type)

**Eligibility and rosters**
- county-administered eligibility (per-county sources and trading partners)
- continuous-eligibility rules
- MCO roster
- tribal roster

**Claims**
- claim-number structure and adjustment linkage
- state claim-type codes
- timely filing per claim category
- MCO encounter SLAs

**Formats and identifiers**
- ID formats
- pharmacy standard version and dates (NCPDP D.0 / Batch 1.2 until F6 / Batch 15 is mandatory; dates to be verified in the T2 checklist)

**Calendars (D2):** financial / checkwrite cycle, capitation run, T-MSIS submission cut-offs, holidays.

**Governance, security and deployment**
- authorities in use; DATA_SHARING_BASIS
- overlays: thresholds, run-out windows, small-cell threshold
- IdP config: issuer, duty-claim names, step-up claim type
- hosting tenancy
- `deployment_profile` ∈ {DEMO, PILOT, PROD}

### 4.3 Harness (`config/harness.yaml` ★)

```yaml
pipelines:
  - id: claims
    platform: databricks          # snowflake | databricks | bigquery | duckdb (T1)
    orchestrator: airflow         # dbt | airflow | databricks_workflows | snowflake_tasks | composer | manual
    dag_source: {type: dbt_manifest, path: pipelines/p6_claims/target/manifest.json}
    contracts: contracts/claims/
    gated_nodes: [brz_encounter_header, slv_claim_chain, gld_claim_final]
    domains: [CLAIMS]
dependencies:
  - domain: CLAIMS
    depends_on: ELIGIBILITY
    key_map: [member_source_key]            # only keys present on BOTH sides
    temporal_binding: {type: PERIOD, window: service_date}
    policy: WAIT
    max_wait: P2D
    escalation: dq-duty-steward-medicaid
  - domain: FINANCE_CAPITATION
    depends_on: ELIGIBILITY
    key_map: [member_source_key]
    temporal_binding: {type: AS_OF_CUTOFF, calendar: cap_run}
    policy: WAIT
  - domain: ELIGIBILITY
    depends_on: XREF
    key_map: [member_source_key]
    temporal_binding: {type: LATEST_RELEASED}
    policy: EVALUATE_AGAINST_LAST_RELEASED  # D3: eligibility does not need the xref to load
```

**Dependency rules:**
- A key map resolved through the xref is forced to EALR.
- `temporal_binding` ∈ {LATEST_RELEASED, AS_OF_CUTOFF(calendar_id), PERIOD(window)}.
- On `max_wait` expiry, the harness escalates to H-IN. It never auto-releases.

**The harness:**
- validates DAG nodes, APPROVED contracts, an acyclic domain graph, platform/dialect match and orchestrator trigger rules (D10)
- computes the global processing order (topological sort plus intra-DAG order, with calendars)
- enforces the order through gates
- exposes the CLI `harness validate | plan | run | gate | status`

harness.yaml is a REGISTRY artifact.

### 4.4 Orchestrator gates (D10)

| Orchestrator | Gate behaviour |
|---|---|
| dbt | The HARNESS RUNNER excludes HELD nodes and their descendants from the `dbt build` selection. `dq_gate` is a run-operation the runner calls first. No pre-hooks (a pre-hook error would fail the model). |
| Airflow / Composer | `DQGateSensor` + `ShortCircuit`-style skipping. Harness validation REJECTS any downstream task of a gate whose trigger_rule ≠ `all_success`. |
| Databricks Workflows | Gate task sets a task value; downstream runs an if/else condition task. |
| Snowflake Tasks | Gate task reads the DP manifest pointer and sets a return value. Downstream uses `WHEN SYSTEM$GET_PREDECESSOR_RETURN_VALUE(...)='PASS'`. |
| manual | Exit code 0 / 75 / 1 |

The gate is a liveness/ordering convenience. **The data-plane manifest + released views are the guarantee.** Pipelines can read gated data ONLY through `<rel>_RELEASED`.

### 4.5 Contracts and rule IR

**Contracts.** All fields are present day one; values may be DRAFT.
- Structure: grain, keys, chain spec (chain_key, version_key + order, adjustment indicator, void semantics, reversal/rebill), unit partition, eligibility_unit, ordering class, consumer grains, incremental class.
- Counting: countable_terms per (format, claim_category).
- Tags:
  - `control_tags` (in the semantic hash): clinical | payment | coverage | program | identity | free_text | llm_summarisable | quantile_ok
  - `dimension_tags`
  - sensitivity: part2 | bh | tribal | minor | snap_confidential | tanf_confidential | fti | ssa
  - provenance authority (an undeclared IE field fails closed)
- Platform dialect.
- Governance: required_check_classes (may only raise the floor), declared consumers, data product + steward duty.

**Rule IR.**
- Identity: rule_id, version, semantic hash.
- origin: FEDERAL_TMSIS | FEDERAL_CMS0057F | FEDERAL_1927 | FEDERAL_X12_IG | FEDERAL_FNS / FEDERAL_ACF (declared-empty) | STATE | LOCAL
- artifact_vocabulary + type (T-MSIS v4 with all 9 file types incl. **FTX**, X12 IG sections, NCPDP-like, state)
- check_class: + `envelope`, `transaction_structure`
- validation_type: INFERENTIAL (sourced) | DETERMINISTIC / LONGITUDINAL / BENCHMARK (COALITION-DEFINED)
- scope: BATCH | DELTA | FULL. The gate blocks only on BATCH / DELTA.
- thresholds
- applicability: claim_category, payer_type, item_type, program_code, plan_id, submitter_id, format, **direction**, authority. Wildcards are explicit `*`; NULL is rejected.
- benchmark, row_severable, remediation_policy, governing-date rule

### 4.6 Federal pack, registry, genesis

**Federal pack ★** (`rules/federal_pack`). A project-authored, release-hashed baseline. It is NOT CMS-signed. Contents:
- per-domain floors
- SCHIP × MEDICAID concurrency (SSA §2110(b)(1)(C)), with a retro exception at H-IN
- CMS-0057-F MEDICAL PA 72h / 7d; DRUG under FEDERAL_1927 24h (UNVERIFIED)
- NDC: NEVER remediate; shape by format
- GOVERNING_DATE_RULE seed
- envelope and transaction rules (§13)
- the COALITION-DEFINED list
- STRUCTURAL programs (SNAP / TANF): never mode A; tile "program rules 0/0 sourced"

**Registry ★** covers: contracts, packs, overlays, crosswalks, POLICY, provider / model policy, DATA_SHARING_BASIS, roles.yaml, harness.yaml, state profile, **adapter packs**, **calendars**, TRADING_PARTNER, **profiling baselines** (§5.6), deploy manifest and ROSTER.
- Semantic hashes; states PENDING / APPROVED / REVOKED.
- Deploy ≠ activate. Pinned at SEAL. RE_PIN to tighten is H-IN; to loosen is H-IN-2.
- The digest includes the POLICY and baseline hashes.
- Validator: acyclic graph, no NULL applicability, no floor lowering.
- Ladder: cosmetic → A + notify; everything else → H-IN-2 (◇ tiered).
- `registry plan` runs in CI.
- ADVISORY is DEMO-only, expires, auto-revokes, does not meet the floor, and requires mode ≥ H-IN.

**Genesis ★.**
- The first audit entry.
- Contains the roster seed hash, registry seed, manifest, roles.yaml and the initial active versions.
- Diffed against the federal baseline. The ratifiers see the overlay deltas and the COALITION-DEFINED list.
- Two-signature out-of-band ratification, verified against the anchor.
- Gated nodes fail closed until ratified. The test genesis lives in `tests/` only.

## 5. Evaluation (pushed down per platform)

### 5.1 Compiler ★

- Per-dialect builders for snowflake, databricks, bigquery and duckdb. **No blind transpilation.** The probe found `TRY_TO_DATE` → `TO_DATE` and `SHA2` → `SHA256` (bytes) mistranslations.
- Portability allowlist, golden SQL per dialect, pure-Python oracle, divergence suite.
- DP function bodies are hashed and checked by the verifier.
- ◇ fusion.

### 5.2 Keyed digests in the DP (S6, S7)

**DQ_HMAC.** RFC 2104 HMAC-SHA256 with **length-prefixed fields**, implemented identically on every platform:

| Platform | Implementation | Verification |
|---|---|---|
| Snowflake | Python UDF + SECRET | T2 |
| Databricks | Python UDF + secret scope, owned by dq_tokenize | T2; UDF executes with the owner's privileges |
| BigQuery | Remote function (Cloud Run, Secret Manager) | T2 |
| DuckDB (T1) | Python UDF, TEST_KEY | T1 |

- A **cross-platform known-answer test** is a T2 gate: same input → same token on all platforms.
- EXECUTE is granted only to evaluation/seal identities, rate-capped.
- **Fingerprint**:
  - per row: `h = DQ_HMAC(K_FP_e, canonical row)`
  - split h into 4 × 60-bit lanes
  - sum each lane in DECIMAL(38) (no overflow below 2^66 rows)
  - return (lanes, count)
  - the CP binds `HMAC(K_FP_e, lanes‖count‖batch‖unit‖max_row_seq)`
  - a keyed per-row PRF defeats crafted-collision (Wagner) attacks
  - no unkeyed digest leaves the DP
- The verifier recomputes on separate compute (dq_verify).
- **Hash totals (ABC)** use the same construction under K_ABC_e.
- **Key epochs.** K_FP, K_ABC and K_EV rotate in LOCKSTEP across all DPs as one key epoch `e`. Each batch is pinned to the epoch at SEAL. Rotation is a REBIND ceremony (H-IN-2).

### 5.3 ABC ★

`residual = src_in − (tgt_out + rejected + quarantined + deduplicated + filtered_by_contract + held)` per (hop, measure, program, plan_id, submitter_id, claim_category, format, direction, authority).

**Hops:**
- TRANSLATOR (received = accepted + rejected, from the 999 / 277CA results) → FILE → RAW → BRONZE → SILVER → GOLD → OUTBOUND FILE.
- Finance hops (§11 P8) tie to payment and CMS-64 totals.

The NULL-key count is its own measure. INTAKE owns pre-split hops.

### 5.4 Dependencies

- The dependency vector records the intake HWM and the state_version vector of intersecting units, under the edge's key_map and temporal_binding.
- **WAIT:** blocks while an intersecting depended unit is non-terminal. Release requires vector equality.
- **EALR:** labels the result APPROXIMATE.
- **AS_OF_CUTOFF:** binds to the snapshot at the calendar cut-off.
- **PERIOD:** intersects only within the window.

### 5.5 Cost, identity, terminology

- **Cost:** per-platform query tags / labels. Budget ★: exhausted → NOT_EVALUATED.
- **Identity (DEC-005):** validates the state xref; APPROXIMATE unless validated. ◇ provider clusters.
- **Terminology:** format checks real; interval registry. ◇ validity load. Licensed systems ship zero real rows.

### 5.6 Profiling safety (D7, BLOCKER)

- **What may be computed is typed by control_tags / sensitivity:**
  - identity, free_text, fti, ssa, part2 and any sensitivity tag → counts, null rates, distinct-count buckets and SHAPE-CLASS enumerations only
  - quantiles / min / max only for fields tagged `quantile_ok` (declared non-identifying numerics), computed IN-DP, coarsened (rounded bins) and small-cell suppressed
- **The profile store schema carries** authority, sensitivity and value_class on every metric row. RLS applies.
- **Baselines** are pinned registry artifacts (`baseline_hash` in the digest), computed from RELEASED units only. A refresh that widens any tolerance is a LOOSENING (H-IN-2).
- **LONGITUDINAL rules** (Z-score / IQR; ◇ isolation forest) evaluate against the pinned baseline.
- **Every PROPOSE_RULE** carries a backtest against the separately authored ground-truth ledger.

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

- NOT_APPLICABLE_FOR_FORMAT and ADVISORY are excluded and meet no floor.
- Only PASS scores. Empty worst-of = NOT_EVALUATED. KNOWN_ISSUE = WAIVE (H-IN-2).
- High priority: worst-of. Medium / Low: bands. No blended index.
- Every view shows coverage, exposure, as_of and period. Periods stay PROVISIONAL in iteration 1.
- FULL is computed (evaluable, balanced, oracle = SQL, Wilson LB ≥ 0.6 over ≥ 5 × 3 seeded defects, ledger by a separate seat); otherwise PARTIAL.
- **Steward scorecard** per data product: bands WITH denominators and trend, coverage, open cards, waivers, SLA, encounter acceptance rate (277CA), and T-MSIS-shaped DQ measures. Mapping to CMS's state DQ assessment criteria is ◇ until sourced (SHO 18-008 / DQ Atlas — verify).

## 7. Release and publication

### 7.1 can_release(unit) — single predicate in the api ★

TRUE iff all of the following hold:

1. **Fingerprint.** The fingerprint recomputed by the verifier equals the bound fingerprint (same epoch), with a VERIFIER_ATTESTATION.
2. **Approved and current.** Digest artifacts are APPROVED, and the running service images / DP kit versions match DEPLOY_MANIFEST.
3. **Dependencies.** WAIT edges have vector equality; EALR / AS_OF / PERIOD per §5.4.
4. **Mode.** Either:
   - mode A, recomputed from the digest (rank ≥ 4, STRUCTURAL and floor-waived are never A); or
   - a DECISION bound to (fingerprint+epoch, proposed_quarantine_set_hash, bound_disposition_vector), still valid, with distinct person_ids, each with a stored JWS **and a VERIFIER_ATTESTATION of that JWS**, and a challenge-bound step-up for the H-IN-2 checker.
5. **No open veto window.** Notify is ◇, so H-ON → H-IN.
6. **Breakers clear.** The program breaker is not tripped, and the **DENY-propagation breaker** is not tripped.
7. **Approver coverage.** Approver duties ⊇ authorities(unit), including INTAKE if a pre-split hop is non-PASS. The PRIMARY approval comes first; the LAST CONFIRM applies the quarantine and the release atomically.
8. **Period** not FINAL.
9. **Tower scope.** Every authority in the unit has a Tower scope enabled. Iteration 1: MEDICAID, SNAP.
10. **Publication health.** The latest verifier diff CP↔DP for this DP shows no divergence.

### 7.2 Quarantine and publication (S2)

**Quarantine key** = (batch, unit, `_dq_row_seq`), with scope ROW | CHAIN | UNIT.

**Publication = ONE immutable, versioned MANIFEST table per DP.** Each version is a single commit by dq_publish containing:
- the allowed units (batch, unit, fingerprint, max_row_seq)
- the quarantined row_seq set for each unit

Then a single-row **pointer** is flipped to the new version (the last commit).

**Released view = gated rows INNER JOIN the manifest at the pointer version on (batch, unit), WHERE `_dq_row_seq ≤ max_row_seq` AND row_seq NOT IN that unit's quarantined set.** The quarantined set is inside the same manifest version, so it is published atomically with the allow-list.

| Platform | View type |
|---|---|
| Snowflake | SECURE VIEW |
| Databricks | UC view |
| BigQuery | Authorized view |

**DENY = publish a new manifest version without the unit.** It is never a separate anti-join table.

**DENY propagation SLO** (state profile; default 15 min):
- breach → the DENY breaker trips → `can_release` refuses new releases on that DP until the denial is propagated
- the CP's RTO is tied to this SLO

**Lag and failure.** A lag or a failed publication leaves the PREVIOUS pointer in place. Allow-list and quarantine move together, so a lag never exposes quarantined rows. The verifier reads back every pointer flip and diffs it against the CP master.

### 7.3 Consumption, drain, refuse list

**Consumption** is derived per (unit, consumer) from (`_dq_batch_id`, `_dq_unit_key`) in targets. Aggregate gated targets are refused (◇).

**PUBLISH:** strict order per (ORDERED domain, partition); it stops at the first non-terminal unit. Liveness tiles. ◇ skip / groups.

**Re-fingerprint ★:** the verifier re-checks released, unconsumed units. A mismatch triggers DENY and a card.

**Binder ★ refuses:**
- gated writes by non-seal principals
- pipeline reads of gated base relations
- WATERMARK / UNKNOWN incrementals
- aggregate gated targets
- groups
- authorities without a Tower scope
- floor lowering
- undeclared IE fields
- downstream trigger rules ≠ `all_success`

## 8. HITL and Command Tower

**Modes:** A / H-ON / H-IN / H-IN-2. `mode = max(POLICY, FLOOR)`.

**Verbs:**
- HOLD, QUARANTINE_ROWS, RELEASE, REJECT, SUPERSEDE
- REQUEST_RESEND, NOTIFY_SUBMITTER
- WAIVE, CHANGE_RULE, RE_PIN, REBIND, UNFREEZE_BREAKER
- APPLY_SAFE_TRANSFORM, PROPOSE_MERGE, PROPOSE_RULE
- REVEAL (below)
- ◇ BREAK_GLASS, ◇ RESTATE, ◇ PERIOD_CLOSE, ◇ BACKFILL

**Floor (H-IN-2):**
- release after an ABC failure
- identity actions
- waivers
- loosening (incl. baseline widening)
- rule proposals
- **ROSTER**
- roles.yaml / harness.yaml / profile / adapter pack / calendars / TRADING_PARTNER / DATA_SHARING_BASIS / destinations / hosting tenancy
- release of outbound submission files (834-out, 820, 835, T-MSIS)
- genesis

**Injection points:** triage, hold, propose (dry-run), H-ON veto, release, learn.

- Timeouts fail closed and escalate.
- **SoD:** not the owner, not the author, no self-affecting change, distinct person_ids.

**REVEAL (S-missed).** Reviewers can see records without base-table access.
- REVEAL(unit, row_seq set, purpose) is a decision-like action at H-IN: duty must cover the unit's authorities, and it is logged.
- It creates a short-lived, authority-scoped **reveal view** in the DP for the reviewer's OWN DP identity, with columns masked by sensitivity.
- The Tower links to it. The CP never receives the values.
- It expires automatically.
- T2 per platform; at T1, DuckDB.

**Audit.**
- Append-only AUDIT_LOG with an AUDIT_HEAD CAS. The chain hashes digests.
- The external ANCHOR (T1: local file; prod: object lock) carries H(audit_head).
- The verifier writes its attestations into the chain.
- ◇ **DP audit-log ingestion** (Snowflake ACCESS_HISTORY, Databricks system.access.audit, BigQuery Data Access logs) detects base-table reads that bypass the views. Listed.

**Live checks (heartbeat, on the verifier deployment):**
- service image digests
- CP function definitions
- DP grants, storage IAM, view DDL and DQ function-body hashes
- landing appendOnly properties
- manifest pointer vs CP
- IdP membership vs ROSTER

**Command Tower** (Next.js). It is a client of the api and forwards the token; authorization happens per request, with RLS scope.

- **Read endpoints** apply authority scope and `presentation/suppress` server-side. This is the only suppression site. Small cells are &lt; overlay threshold (default 11) plus complementary suppression.
- **Top band:** genesis, held units, decisions due, oldest SLA, mine, ABC, liveness (incl. DENY-propagation lag), LLM health, pipeline order, calendar cut-offs.
- **Real screens:**
  - Decision Queue (deterministic clusters by authority)
  - Decision Card (evidence, dry-run diff, ABC before/after, collapsed LLM hypothesis, typed rationale, step-up confirm, reveal link)
  - Pipeline Map (harness order, gates, calendars)
  - ABC Board (translator → file → … → outbound)
  - Evidence
  - Steward Scorecard
- **Read-only screens:** interventions, registry, audit, agent activity.

## 9. Evidence and LLM

- **Tokens:** `DQ_HMAC(K_EV_e, len‖authority‖len‖program‖len‖rule_id‖len‖period‖len‖canon_key)`, in-DP (§5.2).
  - Samples are ordered by token.
  - EVIDENCE_PAYLOAD holds tokens and shape classes only, under RLS.
  - Per-retention-class keys. ◇ shred.
- **LLM:**
  - Typed opaque prompt builder. Failing values are never sent. `llm_summarisable` via suppress.
  - Output: a closed-taxonomy hypothesis + cited (rule_id, pinned hash) + a confidence band, rendered by template.
  - Never originates WAIVE, loosening or PROPOSE_RULE. No FTI.
  - Injection fuzz test.
  - NullProvider by default. Other providers are refused at T1. Cortex / Anthropic / Azure OpenAI / Vertex adapters are emitted (T2). Selection is H-IN-2.

## 10. Verification

| Tier | Coverage |
|---|---|
| T0 | Unit and invariant tests |
| T1 | Real Postgres CP + FastAPI services + DuckDB DP (dbt-duckdb pipelines) + fakesnow for Snowflake-dialect logic + golden / divergence for Databricks and BigQuery. A T1 pass is not evidence of warehouse compatibility. |
| T2 | Per-platform kits |

**Each T2 kit covers:**
- construct smoke
- grant AND storage-IAM assertions
- divergence suite
- released-view enforcement (no base or landing read; quarantined invisible; pointer-lag safety)
- cross-platform DQ_HMAC known-answer test
- SEAL AS-OF reproducibility
- appendOnly landing
- orchestrator gate
- manifest publication read-back
- reveal-view expiry
- IdP step-up / challenge binding against the state IdP

## 11. Sample pipelines (`pipelines/`, dbt; targets duckdb / snowflake / databricks / bigquery)

| # | Pipeline | Domains | Sources | Depends on |
|---|---|---|---|---|
| P1 | reference | MCR plans, code sets, crosswalks, governing-date rules, calendars | state reference | — (UNORDERED) |
| P2 | provider | enrollment spans, locations, taxonomy, affiliations | provider enrollment extract, NPPES | P1 |
| P3 | identity_xref | state MCI crosswalk | IE / MCI extract | P4 (WAIT, LATEST) |
| P4 | eligibility_ie | IE: Medicaid / MCHIP / SCHIP / SNAP / TANF, households, enrollment, MC enrollment | IE extract, enrollment-broker 834-in | P3 (EALR), P1 |
| P5 | pa_tpl | PA (278 / portal extract), TPL coverage, batch 270/271 TPL carrier matching | PA system extract, TPL vendor files | P4, P2 |
| P6 | claims | FFS from the **MMIS adjudicated extract** (CIP / CLT / COT / CRX, ICN / TCN, paid date, status); **encounters from raw MCO 837P/I/D**; pharmacy from the **PBM / POS adjudicated extract** (+ NCPDP-like, NOT_SPEC_CONFORMANT); adjustment / void / reversal chains | MMIS extract, translator outputs + 999 / 277CA, PBM extract | P4 (PERIOD), P2, P5, P1 |
| P8 | finance | FFS payment cycle (checkwrite / EFT register, **outbound 835** balancing: BPR02 = ΣCLP04 − ΣPLB, TRN02 unique), capitation (**outbound 820**, AS_OF cap-run eligibility), CMS-64 tie-out (PROVISIONAL), ◇ drug rebate | MMIS financial extract | P6, P4 (AS_OF_CUTOFF cap_run), financial calendar |
| P7 | tmsis_extract | T-MSIS v4 CIP / CLT / COT / CRX / ELG / PRV / TPL / MCR / **FTX** + gold marts + outbound 834-to-MCO | — | all (WAIT, T-MSIS cut-off calendar) |

**Order:** P1 → P2 → P4 (EALR on P3) → P3 → P5 → P6 → P8 → P7. The graph is acyclic by construction (D3).

**Scenarios exercised:**
- fan-in
- late original
- retro eligibility (unconsumed → re-eval; consumed → RESTATE_REQUIRED)
- held ELG holding only intersecting claim partitions
- cross-program household
- MCO lag by service period
- capitation snapshot at the cut-off
- duplicate interchange
- envelope count mismatch
- HL integrity failure
- frequency-7 claim without REF*F8
- 834 full file vs change file
- 835 out of balance
- SCHIP × Medicaid overlap
- drug vs medical PA clocks
- a T-MSIS cut-off with a held PRV batch

## 12. Synthetic data

A coherent state population:
- persons → households / cases → programs (Medicaid / MCHIP / SCHIP / SNAP / TANF)
- providers (NPI Luhn + atypical)
- MCOs, PA, TPL, claims and chains, payments

**Emits, with a direction tag on every file:**

| Direction | Files |
|---|---|
| Inbound | MCO 837P/I/D encounters + translator 999 / 277CA results; enrollment-broker 834; TPL carrier 271 |
| Extracts | MMIS adjudicated claim and financial extracts; IE extract; PBM extract; PA extract |
| Outbound (the generator also produces the "expected" outbound for verification) | 834 to MCOs, 820, 835 |

**Modes:** realistic defect-blind, and seeded (≥ 5 × 3 per cell). The ground-truth ledger is authored by a SEPARATE seat and hash-committed first. Licensed code systems use a reserved synthetic shape space.

## 13. Raw ingestion and file governance (D1, D5, D6) ★

**Positioning.**
- The agent sits **behind** the state translator and does not replace it.
- Inbound translator results (999 / TA1 / 277CA) are an ABC SOURCE: received = accepted + rejected.
- Raw 837 = MCO encounters. FFS claims come from the MMIS adjudicated extract.
- **Outbound** files are gated as submission extracts (H-IN-2 floor).

**TRADING_PARTNER registry:** (partner, transaction set, IG version, direction ∈ {INBOUND_PRE_TRANSLATOR, INBOUND_POST_TRANSLATOR, OUTBOUND}, county / MCO).

**File registry:** file_id, sha256, partner, direction, translator acceptance status, interchange keys.

**Parsing.**
- An in-house **tolerant tokenizer** always produces raw records (envelope, segments, loop positions). A parse failure is a disposition, not an exception.
- linuxforhealth-x12 0.57 (Apache 2.0; pinned, vendored; last release 2022, Beta) is used ONLY for bronze mapping of the transaction sets it supports. Module coverage (837D / 278 / 820) is verified by import at build time.
- NCPDP-like files are labelled NOT_SPEC_CONFORMANT.

**Checks, classified by X12 IG / ASC X12 section** (FEDERAL_X12_IG, COALITION-DEFINED unless cited):

*Envelope*
- ISA fixed 106-character width and derived delimiters; ISA11 repetition separator
- ISA13 = IEA02; IEA01 = group count
- GS06 = GE02; GE01 = transaction count; **GS08 = ST03**; GS01 consistent with ST01
- ST02 = SE02; SE01 = segment count
- duplicate interchange = (ISA05/06, ISA07/08, ISA13) within the lookback window, distinguished from a legitimate resubmission
- control-number gaps: TA_WARN only, never gating
- ISA15 usage indicator = environment
- partner ∈ TRADING_PARTNER with the matching direction
- arrival SLA; duplicate file hash

*837*
- HL integrity: HL01 unique; HL02 references a prior HL; HL03 20 → 22 → 23; HL04 consistent; 2000C only when SBR02 ≠ 18
- **CLM02 = Σ line charges, per claim**
- **CLM05-3 ∈ {7, 8} requires REF*F8** (chain linkage)

*834*
- **BGN08** (2 = change, 4 = verify / full) selects DELTA vs SNAPSHOT balancing
- INS03 maintenance codes (021 / 001 / 024 / 025 / 030) drive add / change / term counts

*835*
- BPR02 = ΣCLP04 − ΣPLB; TRN02 unique

*NCPDP-like and flat files*
- header / trailer counts and amounts

**File-level ABC:** TRANSLATOR → FILE → RAW → BRONZE on transactions / records and amounts. Formats without trailer amounts → NOT_APPLICABLE_FOR_FORMAT.

◇ 999 / TA1 / 277CA generation (the agent consumes these, not produces them).

## 14. Iteration-1 manifest

★ = invariant. ◇ = stub that fails closed and is listed.

**Core model and roles**
- `model/` ★
- `roles/` ★ (◇ MAF adapter)

**Registry, governance, configuration**
- `registry/` ★ (◇ tiered ladder)
- `governance/` ★: roles.yaml → CP + 3 DP generators incl. storage IAM; ROSTER; genesis; anchor; heartbeat; verifier
- `config/` ★: profile schema + sample state "ZZ" + adapter pack ZZ; calendars; TRADING_PARTNER
- `harness/` ★

**Adapters**
- dag: dbt ★; sf_tasks (T2); ◇ airflow parse
- metadata: ◇ DataHub
- orchestrator: dbt runner ★; airflow sensor + trigger-rule validator ★ (T1 unit); databricks / snowflake (T2)
- dataplane: duckdb ★; snowflake / databricks / bigquery (generated, T2)

**Compute and ingest**
- `compiler/` ★
- `engine/` ★: loads, SEAL AS-OF, fingerprint lanes, dependencies, budget
- `abc/` ★
- `profiling/` ★ (§5.6; ◇ isolation forest)
- `ingest/` ★: tolerant tokenizer, envelope / transaction checks, linuxforhealth bronze mappers, file registry; ◇ ack generation

**Release, HITL, evidence**
- `intervention/` ★: manifest publication + pointer, consumption, drain, quarantine, DENY, breakers, re-fingerprint
- `hitl/` ★: predicate, modes, floor, SoD, challenge-bound step-up, REVEAL
- `evidence/` ★: DQ_HMAC interface + 4 impls (T1 DuckDB)
- `terminology/`, `identity/`
- `llm/` ★ (NullProvider)

**API and Tower**
- `api/` ★: OIDC with a local test IdP issuing signed JWS + step-up at T1
- `tower/` (Next.js)
- `presentation/suppress` ★

**Pipelines, data, tests, deploy**
- `pipelines/` P1–P8 ★
- `synthetic/` ★
- `tests/`
- `deploy/`: docker-compose with 5 services + Postgres + Tower; DP kits
- `tools/`

**EXIT_CRITERIA must state:**
- ALL programs EVALUATED; RELEASABLE only for units whose authorities ⊆ {MEDICAID, SNAP}.
- No platform verified until its T2 kit passes.
- BigQuery landing immutability is detective.
- Stubs: DataHub, Airflow DAG parsing, the MAF adapter, DP audit-log ingestion, RESTATE / PERIOD_CLOSE / BACKFILL / BREAK_GLASS, acknowledgement generation, drug rebate.

**Deferred pre-PHI:**
- external WORM sink
- DP masking per authority beyond reveal views
- LLM BAA / region
- retention / shred
- image signing + SBOM
- CP DR (PITR, cross-region) tied to the DENY RTO
- pen test
- predicate cost at scale

## 15. Build waves (D9)

| Wave | Content | Exit |
|---|---|---|
| **W0** | This v2.1 (design amendments) | Done. Orchestrator diff-check against the v2.0 findings, logged on the blackboard. |
| **W1** | Vertical slice at T1, state ZZ, MEDICAID scope, DuckDB + Postgres + local IdP. MCO 837P encounters + translator 999/277CA + enrollment-broker 834 + IE extract → intake → raw → bronze → silver → gold → T-MSIS ELG/COT subset. SEAL AS-OF, keyed fingerprint, ABC translator → gold, one WAIT edge, HOLD → Decision Card → challenge-bound step-up CONFIRM → verifier attestation → manifest publication → released-view hiding + DENY; dbt runner gate; Tower queue / card / pipeline map. | `docs/EXIT_CRITERIA.md`: every W1 criterion PASS / PARTIAL / STUB with its test and receipt (JUnit + code hash under `docs/receipts/`), plus a scored blind detection run against the synthetic ledger. (v2.1 dropped the §14 "first tests" list this cell used to cite; SCP-W1 review.) |
| **W2** | (Proposed re-order DEC-012, pending user veto: FIRST the rule IR with DuckDB + Snowflake emitters and the config-driven dependency resolver / dbt-manifest DAG adapter — acceptance: a new pipeline added by YAML only; THEN breadth.) Breadth at shallow depth: P1–P8 with DRAFT contracts; all programs evaluated (SNAP / TANF structural); every format; retro-eligibility, cap-snapshot and T-MSIS cut-off scenarios. | — |
| **W3** | Platforms at equal artifact coverage: 3 dialect builders + golden + divergence; generated DDL / IAM / views / UDFs / manifests; T2 kits written, unexecuted. | — |
| **W4** | Depth: orchestrator adapters, profiling + proposals, scorecard, genesis ceremony, heartbeat / verifier depth, LLM NullProvider; T2 on the first platform that gets an account. | — |
