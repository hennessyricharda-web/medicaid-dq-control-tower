# W1 Spine: frozen interfaces for the vertical slice

Status: FROZEN for W1. Every W1 builder codes against this file, never against another builder's work in progress. Changes go through the orchestrator only. Design reference: DESIGN.md v2.1 §15 W1.

W1 scope:
- state **ZZ** (synthetic)
- program **MEDICAID** only (authority AUTH_MEDICAID)
- data plane **DuckDB**
- CP **Postgres**
- local test IdP

---

## 1. Synthetic inbound files (written by `synthetic/`, read by `dqagent/ingest/`)

Output directory: `synthetic/out/<run_id>/`. All dates are `YYYYMMDD`, all amounts have 2 decimals, and the encoding is ASCII.

### 1.1 IE extract (state flat file, direction = INBOUND_POST_TRANSLATOR, partner `ZZ-IE`)

File `ie_extract_<YYYYMMDD>.txt`. Pipe-delimited, one record per line.

- **Header:** `HDR|ZZ-IE|<file_date>|<file_seq>`
- **Detail:** `DTL|person_source_key|msis_id|first_name|last_name|dob|sex|program_code|elig_group|segment_type|eff_start|eff_end|county|household_id|recorded_at`
  - `program_code` ∈ {MEDICAID, MCHIP, SCHIP, SNAP, TANF}. W1 data is mostly MEDICAID, plus a few SNAP rows to prove the program split.
  - `segment_type` ∈ {FULL, PARTIAL, WAIVER}
  - `eff_end` may be `99991231`
  - `recorded_at` is `YYYYMMDDHHMMSS`
- **Trailer:** `TRL|<detail_record_count>`

### 1.2 Enrollment-broker 834 (X12 005010X220A1, direction = INBOUND_PRE_TRANSLATOR, partner `ZZ-BROKER`)

File `834_ZZBROKER_<YYYYMMDD>_<n>.x12`. Delimiters: element `*`, sub-element `:`, segment `~`, repetition `^`. ISA is exactly 106 characters.

**Envelope**

```
ISA*00*          *00*          *ZZ*ZZBROKER       *ZZ*ZZMEDICAID     *YYMMDD*HHMM*^*00501*<ISA13 9 digits>*0*<ISA15 T|P>*:~
GS*BE*ZZBROKER*ZZMEDICAID*CCYYMMDD*HHMM*<GS06>*X*005010X220A1~
ST*834*<ST02>*005010X220A1~
BGN*00*<ref>*CCYYMMDD*HHMM****<BGN08 2|4>~
```

**Per member**

```
INS*Y*18*<INS03 021|001|024|025|030>*<INS04>*A***AC~
REF*0F*<member_id = msis_id>~
NM1*IL*1*<last>*<first>~
DMG*D8*<dob>*<sex>~
HD*<maint>**HMO*<plan_id>~
DTP*348*D8*<coverage_start>~
DTP*349*D8*<coverage_end>~     (optional)
```

**Trailer**

```
SE*<segment count incl ST and SE>*<ST02>~
GE*<transaction count>*<GS06>~
IEA*<group count>*<ISA13>~
```

### 1.3 MCO 837P encounters (X12 005010X222A1, direction = INBOUND_PRE_TRANSLATOR, partners `ZZ-MCO-A` / `ZZ-MCO-B`)

File `837P_<MCO>_<YYYYMMDD>_<n>.x12`. Same delimiters as 834.

**Envelope**

```
GS*HC*<mco>*ZZMEDICAID*...*<GS06>*X*005010X222A1~
ST*837*<ST02>*005010X222A1~
BHT*0019*00*<ref>*CCYYMMDD*HHMM*RP~      (RP = reporting = encounter)
```

**Billing provider and subscriber**

```
HL*<id>**20*1~
NM1*85*2*<org>*****XX*<billing NPI>~
HL*<id>*<parent>*22*0~
SBR*P*18*******MC~
NM1*IL*1*<last>*<first>****MI*<member_id = msis_id>~
```

**Per claim**

```
CLM*<encounter_id>*<total charge>***<POS>:B:<freq 1|7|8>*Y*A*Y*Y~
REF*F8*<original encounter_id>~          (required when freq 7/8; omitted in seeded defects)
HI*ABK:<dx>~
AMT*D*<MCO paid amount>~                 (synthetic simplification, labelled)
```

**Per line**

```
LX*<n>~
SV1*HC:<proc 5 chars>*<charge>*UN*<units>***1~
DTP*472*D8*<svc_date>~
```

Close with SE / GE / IEA as for 834.

### 1.4 Translator results (direction = INBOUND_PRE_TRANSLATOR acknowledgement summary, partner = the MCO)

File `ack_<MCO>_<YYYYMMDD>_<n>.csv`, with header:
`isa13,gs06,st02,transaction_set,status,error_code`
- `status` ∈ {A, R} (999 IK5 accepted / rejected)
- one row per ST

### 1.5 Ground-truth ledger (written ONLY by the synthetic seat)

`ledger.json`: a list of `{defect_id, defect_class, file, locator, expected_effect}`.

`ledger.sha256` is written FIRST and committed before any rules are written. Rule authors must not read `ledger.json` until evaluation.

W1 defect classes (names only):

| Area | Classes |
|---|---|
| Envelope | DUP_INTERCHANGE, SE_COUNT_MISMATCH, GE_COUNT_MISMATCH, ISA_WIDTH |
| 837 transaction | HL_PARENT_BROKEN, CLM02_LINE_SUM, FREQ7_NO_F8 |
| Provider | NPI_LUHN |
| Eligibility coverage | SVC_OUTSIDE_ELIG, MEMBER_UNKNOWN |
| Eligibility data | DOB_AFTER_ELIG_START, ELIG_OVERLAP_SAME_TYPE, IE_TRAILER_COUNT |
| 834 | INS_TERM_UNKNOWN_MEMBER |
| Translator | TRANSLATOR_REJECT |

---

## 2. Data-plane tables (DuckDB at T1; same logical schema on every platform)

Every landing and gated table carries the canonical columns:

| Column | Type | Notes |
|---|---|---|
| `_dq_batch_id` | VARCHAR | |
| `_dq_load_id` | VARCHAR | |
| `_dq_file_id` | VARCHAR | |
| `_dq_source_system` | VARCHAR | |
| `_dq_source_record_key` | VARCHAR | |
| `_dq_unit_key` | VARCHAR | Gated only |
| `_dq_row_seq` | BIGINT | Gated only |

Schemas: `landing`, `raw`, `bronze`, `silver`, `gold`, `dq_rel` (manifest + released views).

**Raw**
- `raw.x12_segment(file_id, isa13, gs06, st02, seg_idx, seg_id, elements VARCHAR /*JSON array*/, hl_id, hl_parent, hl_level)`
- `raw.flat_record(file_id, line_no, record_type, fields VARCHAR /*JSON array*/)`

**Bronze**
- `bronze.ie_segment(person_source_key, msis_id, first_name, last_name, dob DATE, sex, program_code, elig_group, segment_type, eff_start DATE, eff_end DATE, county, household_id, recorded_at TIMESTAMP)`
- `bronze.enrollment_834(member_id, last_name, first_name, dob DATE, sex, maint_code, plan_id, coverage_start DATE, coverage_end DATE, bgn08, ins03, isa13, st02)`
- `bronze.encounter_header(encounter_id, submitter_id, billing_npi, member_id, freq_code, orig_encounter_id, total_charge DECIMAL(18,2), mco_paid DECIMAL(18,2), pos, dx1, isa13, st02)`
- `bronze.encounter_line(encounter_id, line_no INT, proc_code, line_charge DECIMAL(18,2), units DECIMAL(10,2), svc_date DATE)`

**Silver**
- `silver.eligibility_span` (MEDICAID; key person_source_key + segment_type + eff_start)
- `silver.encounter_chain` (chain_id = original encounter id; version by freq / arrival)

**Gold**
- `gold.member_month`
- `gold.tmsis_elg_subset`
- `gold.tmsis_cot_subset`

---

## 3. Control-plane API (FastAPI; consumed by `tower/`)

Base `/api/v1`. Auth: `Authorization: Bearer <JWS>` from the local IdP at T1. All responses are JSON.

| Method | Path | Returns / purpose |
|---|---|---|
| GET | `/me` | `{person_id, name, duties:[...], authorities:[...]}` |
| GET | `/summary` | top band: `{genesis_status, held_units, decisions_due, oldest_sla_minutes, mine, abc:{balanced, out_of_balance}, deny_propagation_lag_s, pipeline_order_ok, llm_provider}` |
| GET | `/queue` | `[{cluster_id, authority, rule_ids[], node, failure_signature, unit_count, worst_disposition, required_mode, sla_due, units:[unit_key...]}]` |
| GET | `/units/{unit_key}` | the decision card (below) |
| POST | `/units/{unit_key}/decide` | maker; body below |
| POST | `/units/{unit_key}/challenge` | `{challenge, step_up_url}`; the checker obtains a step-up token bound to the challenge |
| POST | `/units/{unit_key}/confirm` | checker; body `{decision_id, step_up_token}` |
| POST | `/units/{unit_key}/reveal` | `{purpose}` → `{reveal_ref, expires_at}` (T1: DuckDB reveal view name) |
| GET | `/pipelines` | `{pipelines:[{id, platform, orchestrator, domains, gated_nodes:[{node, state}]}], order:[pipeline_id...], dependencies:[{domain, depends_on, policy, temporal_binding}]}` |
| GET | `/abc?batch_id=` | `[{hop, measure, program, submitter_id, src_in, tgt_out, rejected, quarantined, deduplicated, filtered, held, residual, disposition}]` |
| GET | `/audit?limit=` | read-only list |

**Decision card** (`GET /units/{unit_key}`):

```
{ unit_key, batch_id, pipeline, node, program, authorities[], state, state_version,
  fingerprint, fp_epoch, required_mode, approvals:{required, have, makers[]},
  dispositions:[{rule_id, rule_hash, check_class, priority, disposition,
                 evaluated, failed, metric, threshold, sample_tokens[]}],
  abc_before_after:[...], dependencies:[{domain, policy, state}],
  proposed_actions:[{verb, params, dry_run:{rows_quarantined, chains}}],
  llm_hypothesis:{provider, category, cited_rules[], confidence_band} | null,
  allowed_decisions:[...], sla_due }
```

**Decide body** (`POST /units/{unit_key}/decide`):

```
{ decision: "RELEASE"|"REJECT"|"HOLD"|"QUARANTINE_AND_RELEASE",
  quarantine_row_seqs: [int],
  rationale: str (min 20 chars),
  expected_state_version: int,
  bound_fingerprint: str }
```

Response: `{decision_id, status:"AWAITING_CHECKER"|"APPLIED"|"STALE"|"DENIED", reason}`.

Error model: HTTP 409 for STALE / CAS conflicts, 403 for duty or SoD failures, 422 for validation errors.
