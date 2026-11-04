"""Code-set loaders (docs/rules/RULES_DESIGN.md §4; sources in docs/rules/SOURCES.md).

Each loader turns an OFFICIAL file into rows of
    (system, code, billable, eff_from, eff_to, description_hash, provenance)
Descriptions are hashed, not stored: the agent needs validity, not the (sometimes licensed) text.

Layout status:
  * ICD10CM  — CMS order file; fixed positions VERIFIED against the CMS order-file layout PDF.
  * HCPCS2   — CMS Alpha-Numeric HCPCS file; column map is CONFIG (layout UNVERIFIED, SOURCES.md).
  * POS, NUCC_TAXONOMY, NPPES_DEACT, STATE_LOCAL — CSV with a declared column map.
  * CPT      — licensed (AMA): no loader; zero rows by design.

A load becomes visible to rules only after two-person approval in the CP registry
(artifact_kind='codeset', semantic_hash = sha256 of the file).
"""
from __future__ import annotations

import csv
import datetime as dt
import hashlib
import io
from dataclasses import dataclass

PROVENANCE = {"OFFICIAL_FILE", "FIXTURE_VERIFIED", "FIXTURE_SYNTHETIC", "STATE_LOCAL"}


@dataclass(frozen=True)
class Row:
    system: str
    code: str
    billable: bool
    eff_from: dt.date
    eff_to: dt.date | None
    description_hash: str


def _h(s: str | None) -> str:
    return hashlib.sha256((s or "").encode()).hexdigest()[:16]


def _date(s: str | None) -> dt.date | None:
    s = (s or "").strip()
    if not s:
        return None
    for fmt in ("%Y%m%d", "%Y-%m-%d", "%m/%d/%Y"):
        try:
            return dt.datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    raise ValueError(f"unparseable date {s!r}")


def icd10cm_fiscal_year(fy: int) -> tuple[dt.date, dt.date]:
    """ICD-10-CM FY<fy> is effective Oct 1 (fy-1) through Sep 30 (fy) (SA-ICD10-FY2026 states the same window)."""
    return dt.date(fy - 1, 10, 1), dt.date(fy, 9, 30)


def load_icd10cm_order(text: str, fy: int) -> list[Row]:
    """CMS order file: pos 1-5 order no, 7-13 code (no dot), 15 billable flag, 17-76 short, 78- long."""
    eff_from, eff_to = icd10cm_fiscal_year(fy)
    out = []
    for ln, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        if len(line) < 17 or line[5] != " " or line[13] != " " or line[14] not in "01":
            raise ValueError(f"line {ln}: not the CMS ICD-10-CM order-file layout")
        out.append(Row("ICD10CM", line[6:13].strip(), line[14] == "1", eff_from, eff_to, _h(line[77:])))
    return out


def load_csv(text: str, system: str, colmap: dict, *, default_from: dt.date | None = None) -> list[Row]:
    """colmap: {code: <col>, eff_from?: <col>, eff_to?: <col>, billable?: <col>, description?: <col>,
    billable_true?: [values]}. Used for HCPCS2 (map from config), POS, NUCC, NPPES_DEACT, STATE_LOCAL."""
    rd = csv.DictReader(io.StringIO(text))
    missing = [v for k, v in colmap.items() if k != "billable_true" and v and v not in (rd.fieldnames or [])]
    if missing:
        raise ValueError(f"{system}: columns {missing} not in file header {rd.fieldnames}")
    out = []
    for r in rd:
        code = (r[colmap["code"]] or "").strip()
        if not code:
            continue
        ef = _date(r.get(colmap.get("eff_from") or "", "")) or default_from
        if ef is None:
            raise ValueError(f"{system}: no effective-from for {code} and no default")
        bt = colmap.get("billable_true")
        billable = True if not colmap.get("billable") else (r[colmap["billable"]] or "").strip() in (bt or ["1", "Y"])
        out.append(Row(system, code, billable, ef, _date(r.get(colmap.get("eff_to") or "", "")),
                       _h(r.get(colmap.get("description") or "", ""))))
    return out


def file_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
