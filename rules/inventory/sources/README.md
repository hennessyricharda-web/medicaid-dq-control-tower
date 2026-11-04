# CMS T-MSIS DQ measures: what is here and how it was sampled

**Source.** The CMS T-MSIS Data Guide, Data Quality Measures Directory: https://www.medicaid.gov/tmsis/dataguide/data-quality-measures/ (Data Guide v3.38.0, Directory v3.13.1). It was read 2026-10-22 in the user's built-in browser. The SHO #25-002 citation is in docs/rules/SOURCES.md.

## The full directory, read from the page's own table (not stored)

- **4,058 measures**, each with a distinct ID.
- **SHA-256 of the full table as CSV:** `31c4cefac8b58d26aa06cd025e955144f0d5524dab6e1f81b848ada1d06138b4` (657,423 bytes).

| By | Counts |
|---|---|
| Priority | Critical 154, High 457, Medium 403, N/A 3,044 |
| File type | COT 1,641, CIP 666, CLT 604, CRX 452, ELG 353, Multiple Files 171, PRV 118, MCR 37, TPL 16 |
| ID prefix | MIS 991, EXP 788, MCR 767, FFS 648, RULE 418, EL 205, ALL 141, PRV 50, TPL 50 |

Critical + High = 611, which is consistent with SHO #25-002's "more than 500 critical and high-priority measures".

## Demo sample (user decision 2026-10-22: "a sampling of 100 or so")

**File:** `tmsis_dq_measures_sample104.csv`, 104 measures. Every field was read from each measure's own page: type, validation type, min, max, longitudinal threshold, and DD element numbers. The specification text is NOT stored. Instead, `spec_sha256_16` and `spec_chars` let anyone confirm the page has not changed. Re-read the page before implementing a measure.

**Method.** Deterministic and reproducible from the full table:
- **Quotas by file type** (weighted to the feeds the demo ingests): COT 22, ELG 18, CIP 10, CLT 6, CRX 10, MCR 10, PRV 10, TPL 6, Multiple 8.
- **Priority mix within each file type:** 40% Critical, 40% High, 15% Medium, 5% N/A, capped by what exists. Any remainder goes to Critical first, then High, Medium, N/A.
- **Order within each tier:** natural sort by ID, then evenly spaced picks.
- Rounding gives 104 rows: Critical 44, High 37, Medium 16, N/A 7.

## Other file

`tmsis_dq_measures_index_PARTIAL_551.csv`: an earlier text extraction, truncated at FFS-5-019-3. It is kept only as a record of the failed method. Do not use it.
