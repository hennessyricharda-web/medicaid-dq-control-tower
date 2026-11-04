# Rule source register

Every rule in `rules/catalog/` cites one entry here by `source_id` plus a locator (section, page, loop, element). Retrieval date: 2026-10-21.

**Status values:**
- **VERIFIED:** fetched, and the locator was read in the document.
- **CITED-ONLY:** the reference is known but the text is licensed or was not fetched. The rule cites the reference without quoting it.
- **UNVERIFIED:** the format or locator comes from practitioner knowledge and must be confirmed before a rule citing it can leave DRAFT.

## Federal (CMS)

| source_id | Document | Locator notes | Status |
|---|---|---|---|
| CMS-SHO-25-002 | CMS State Health Official Letter #25-002, "Data Reporting Compliance for T-MSIS", 28 May 2025 — https://www.medicaid.gov/federal-policy-guidance/downloads/sho25002.pdf | p.3: the Outcomes Based Assessment (OBA) replaced the TPIs (from Jan 2022); "rescinds the use of TPIs established in … SHO #18-008"; OBA = "more than 500 critical and high-priority measures". 2 consecutive months below target → notice; 2 more → corrective action plan within 30 days. p.4: file layout v4 in production by 2026-09-30 | VERIFIED (orchestrator + scope reviewer) |
| CMS-TMSIS-DQ-MEASURES | T-MSIS Data Guide, Data Quality Measures Directory (Data Guide v3.38.0, Directory v3.13.1, read 2026-10-21) — https://www.medicaid.gov/tmsis/dataguide/data-quality-measures/ | Per measure: ID, name, priority, file type, claim type, adjustment type, crossover indicator, category, numerator / denominator, thresholds, validation type, DD element numbers (e.g. ALL-2-006-6) | VERIFIED (index + sample pages); extraction in rules/inventory/sources/ |
| CMS-SHO-18-008 | **SUPERSEDED for TPIs by CMS-SHO-25-002.** CMS State Health Official Letter #18-008, "Transformed-Medicaid Statistical Information System (T-MSIS)", 10 Aug 2018 — https://Medicaid.gov/federal-policy-guidance/downloads/SHO18008.pdf | See the TPI note below. | VERIFIED |
| CMS-CIB-2019-03-18 | CMCS Informational Bulletin "T-MSIS State Compliance", 18 Mar 2019 — https://www.medicaid.gov/federal-policy-guidance/downloads/cib031819.pdf | Monthly production submissions; resolve TPI and "key missing data elements critical for file linking". | VERIFIED |
| CMS-DQ-ATLAS | Medicaid DQ Atlas (TAF data-quality assessments) — https://www.medicaid.gov/dq-atlas/welcome (via ResDAC https://www.resdac.org/TAF-data-quality-resources) | Topic list not yet read. | UNVERIFIED (topic-level) |

TPI note for CMS-SHO-18-008:
- p.2: two review methods, system business rules and inferential validation.
- p.3: 12 Top Priority Items. Footnote 7 examples: managed care encounter data, eligibility group coding, CHIP coding, duplicate claims, cross-file linkages.
- The full TPI list is not in the letter.

## X12 (licensed text: cite, never quote)

| source_id | Document | Status |
|---|---|---|
| X12-005010X222A1 | ASC X12N 837 Professional TR3, 005010X222A1 | CITED-ONLY |
| X12-005010X220A1 | ASC X12N 834 TR3, 005010X220A1 | CITED-ONLY |
| X12-ENVELOPE-005010 | ASC X12 interchange/group/transaction envelope (ISA/IEA, GS/GE, ST/SE) | CITED-ONLY |
| X12-RFI-1668 | X12 RFI 1668 (2330B REF*F8) — https://x12.org/resources/requests-for-interpretation/rfi-1668-2330b-reff8 | VERIFIED (W1 engineering review) |

## STATE_A (exemplar state pack `config/state/STATE_A/`)

STATE_A is a generic exemplar. The repository contains no state names, agency names, contract numbers or
document URLs. Each source below has a generic id. The title, URL and page-level read notes for each id are held
in the private sources bundle, which is kept outside the repository and supplied by the engagement team. A real
implementation replaces this pack with the client state's own sources.

| source_id | Kind | Status |
|---|---|---|
| SA-PLAN-CONTRACT-AMD | Managed care plan contract: encounter SLAs (timeliness, acceptance, reconciliation) | SOURCED |
| SA-CG-837P-2026-06 | State 837P companion guide, current version | SOURCED (member-id wording PARTIAL) |
| SA-CG-837P-2021-03 | State 837P companion guide, legacy-MMIS era | SUPERSEDED |
| SA-CG-834O-DWM-2025-10 | State 834 outbound companion guide (daily / weekly / monthly) | SOURCED |
| SA-CG-834O-MONTHLY-2021-05 | State 834 outbound companion guide (behavioral health plan, monthly) | SOURCED |
| SA-CG-834O-2019-05 | State 834 outbound companion guide, earlier version | SUPERSEDED |
| SA-BHP-ENC-MANUAL-2022 | Behavioral health plan encounter manual (encounters submitted up to the legacy cut-over) | VERIFIED, historical scope |
| SA-ENC-SUBMISSION-GUIDE | Governing encounter submission guide | NOT_INGESTED (not public; client to supply) |
| SA-ICD10-FY2026 | State notice adopting the FY2026 ICD-10-CM code set from 2025-10-01 | VERIFIED |

## Code sets

| source_id | Source | Format | Status |
|---|---|---|---|
| CS-ICD10CM | CMS ICD-10-CM "Code Descriptions in Tabular Order" (annual, effective Oct 1) — https://www.cms.gov/medicare/coding-billing/icd-10-codes | Order file: pos 1-5 order no, 7-13 code (no dot), 15 billable flag (1 = valid for HIPAA transactions, 0 = header), 17-76 short, 78- long (CMS order-file layout PDF, https://www.cms.gov/files/document/icd-10-cm-pcs-order-files-fy17-pdf.pdf) | VERIFIED (layout) |
| CS-HCPCS-II | CMS Alpha-Numeric HCPCS quarterly files (Jan/Apr/Jul/Oct; latest listed "October 2026", updated 2026-09-23) — https://www.cms.gov/medicare/coding/hcpcsreleasecodesets/hcpcs-quarterly-update | ZIP; the record layout (add / effective / termination dates, action code) was NOT confirmed on the page | UNVERIFIED (layout) |
| CS-CPT | AMA CPT (licensed) | Ships with ZERO rows; CPT-shaped codes evaluate as LICENSED_NOT_LOADED, never PASS | CITED-ONLY |
| CS-POS | CMS Place of Service code set | Not fetched in this budget | UNVERIFIED |
| CS-NUCC-TAXONOMY | NUCC Health Care Provider Taxonomy (CSV, semi-annual) | Not fetched in this budget | UNVERIFIED |
| CS-NPPES | CMS NPPES data dissemination (full file + deactivation file) | Not fetched in this budget | UNVERIFIED |

**Network note:** neither this workspace nor the linked computer can download from cms.gov, nucc.org or download.cms.gov. Loaders are built against the published formats. Real files are dropped in by the operator. The committed fixtures contain only codes whose validity was read in a fetched source, or are clearly synthetic.
