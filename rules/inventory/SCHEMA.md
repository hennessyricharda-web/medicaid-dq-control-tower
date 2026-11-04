# Requirements inventory: schema (Part A)

The inventory is the list of data-quality REQUIREMENTS that the sources impose. Rules are written against it. Coverage = requirements with an approved rule ÷ requirements in scope.

A requirement without a source is never invented. A domain or program with no source is listed as a `NOT_SOURCED` row with a reason, so the gap stays visible.

## Files

| File | Holds |
|---|---|
| `rules/inventory/sources/tmsis_dq_measures.csv` | Verbatim extraction of the CMS T-MSIS Data Quality Measures Directory (one row per measure) |
| `rules/inventory/federal.csv` | Federal requirements (CMS OBA measures + X12 TR3 structural requirements + code-set requirements) |
| `rules/inventory/state/<ST>.csv` | State requirements (STATE_A first), each cited to that state's sources |
| `rules/inventory/scope.csv` | Every domain × program in scope (DEC-004, DEC-006), with status SOURCED / NOT_SOURCED / NOT_INGESTED and a reason |

## Columns (federal.csv and state/<ST>.csv)

| Column | Meaning |
|---|---|
| `req_id` | Stable id. Federal measures keep the CMS measure id (e.g. `ALL-2-006-6`). Others use `X12-837P-…`, `CS-…`, `STATE_A-…` |
| `title` | The requirement in the source's words (short) |
| `source_id` | Key into `docs/rules/SOURCES.md` |
| `locator` | Page / section / table / measure URL |
| `kind` | `file` (envelope/file), `row` (per record), `batch` (within a batch), `measure` (population ratio with thresholds) |
| `domain` | CLAIMS, ELIGIBILITY, ENROLLMENT, PRIOR_AUTH, MEMBERSHIP, PROVIDER, TPL, MANAGED_CARE, FINANCE, REFERENCE |
| `file_type` | T-MSIS file (CIP, CLT, COT, CRX, ELG, PRV, TPL, MCR, FTX, Multiple) or X12 transaction (837P, 837I, 837D, 834, 835, 270/271, 278, NCPDP) |
| `programs` | `MEDICAID`, `CHIP`, `SNAP`, `TANF`, … (`;`-separated) |
| `claim_types` | e.g. `Medicaid,Enc`, as the source states them; empty if not applicable |
| `earliest_stage` | FILE, ROW, BATCH, REFERENCE (needs reference snapshots: code sets, member keys, eligibility spans) or WAREHOUSE (needs released history, transformed data, or prior periods) |
| `dimension` | completeness, validity, consistency, uniqueness, timeliness, integrity, plausibility |
| `required_elements` | T-MSIS DD numbers (e.g. `ELG162;COT157`) and/or X12 `loop/segment/element` (e.g. `2300/CLM/02`). Drives applicability when a feed or element is absent. |
| `priority` | As the source states it (High / Medium / N/A); empty if the source gives none |
| `thresholds` | As the source states them (min, max, longitudinal); empty for row checks |
| `validation_type` | As the source states it (e.g. Longitudinal and Inferential); empty if absent |
| `status` | `SOURCED` (read in the source), `LOCATOR_APPROX` (read, page approximate), `UNVERIFIED` (not yet re-read), `ILLUSTRATIVE` (an example the source gives, not a requirement) |
| `rule_ids` | Filled in Part B |
| `notes` | Anything that limits use (e.g. "applies only to managed care states") |

## Rules of the inventory

1. A requirement row quotes or closely paraphrases its source. Interpretation goes in `notes`, marked as such.
2. Licensed text (X12, AMA) is never quoted: cite the loop/segment/element only.
3. `earliest_stage` is the earliest point at which the requirement CAN be checked. It decides whether the requirement runs in the platform-neutral pre-load engine or in the warehouse.
4. Every domain × program in DEC-004 / DEC-006 appears in `scope.csv`. An empty domain shows as NOT_SOURCED, never as missing.
