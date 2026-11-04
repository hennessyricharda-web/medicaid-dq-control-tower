"""Bronze mappers for W1 formats (spine §1): IE flat file, 834 (005010X220A1), 837P (005010X222A1).

The mappers are tolerant: a record that cannot be mapped is emitted with mapping_status
REJECTED and a reason. It is never dropped silently, and it is counted as `rejected` in
the RAW→BRONZE ABC hop.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from dqagent.ingest.x12 import Finding, ParsedFile, check_hl_integrity, transactions


class BadValue(str):
    """A non-empty source value that failed to parse. The row carrying it is REJECTED (ENG-W1-1):
    an unparseable end date must never become NULL, because NULL means open-ended coverage."""


def _d(s: str | None):
    if s is None or s.strip() == "":
        return None
    if s == "99991231":
        return date(9999, 12, 31)
    try:
        return datetime.strptime(s, "%Y%m%d").date()
    except ValueError:
        return BadValue(s)


def _reject_bad(row: dict, fields) -> dict:
    bad = [f for f in fields if isinstance(row.get(f), BadValue)]
    if bad:
        row["_map_status"] = "REJECTED"
        row["_map_reason"] = "unparseable " + ",".join(bad)
        for f in bad:
            row[f] = None
    return row


def _amt(s):
    try:
        return Decimal(s) if s not in (None, "") else None
    except InvalidOperation:
        return None


# --- IE flat file ------------------------------------------------------------------
IE_FIELDS = ["person_source_key", "msis_id", "first_name", "last_name", "dob", "sex", "program_code",
             "elig_group", "segment_type", "eff_start", "eff_end", "county", "household_id", "recorded_at"]


def parse_ie(data: bytes, file_id: str):
    """Returns (raw_records, bronze_rows, findings, control). control = {trailer_count, detail_count}."""
    raw, rows, findings = [], [], []
    trailer = None
    header = None
    detail = 0
    for n, line in enumerate(data.decode("ascii", errors="replace").splitlines(), start=1):
        if not line.strip():
            continue
        f = line.split("|")
        raw.append({"line_no": n, "record_type": f[0], "fields": f[1:]})
        if f[0] == "DTL":
            detail += 1
            if len(f) - 1 != len(IE_FIELDS):
                rows.append({"_map_status": "REJECTED", "_map_reason": f"field count {len(f)-1}",
                             "_source_key": f"{file_id}:{n}"})
                continue
            r = dict(zip(IE_FIELDS, f[1:]))
            r["_line_no"] = n
            r["dob"] = _d(r["dob"])
            r["eff_start"] = _d(r["eff_start"])
            r["eff_end"] = _d(r["eff_end"])
            try:
                r["recorded_at"] = datetime.strptime(r["recorded_at"], "%Y%m%d%H%M%S")
            except ValueError:
                r["recorded_at"] = None
            r["_map_status"] = "MAPPED"
            if r["recorded_at"] is None and f[14].strip():
                r["recorded_at"] = BadValue(f[14])
            r["_source_key"] = f"{r['person_source_key']}|{r['segment_type']}|{f[10]}|{r['program_code']}|{f[14]}"
            rows.append(_reject_bad(r, ("dob", "eff_start", "eff_end", "recorded_at")))
        elif f[0] == "TRL":
            trailer = int(f[1]) if len(f) > 1 and f[1].isdigit() else None
        elif f[0] == "HDR":
            header = {"fields": f[1:]}
    if trailer is None:
        findings.append(Finding("FLAT-TRL-MISSING", "FAIL", "IE trailer missing", {"file_id": file_id}))
    elif trailer != detail:
        findings.append(Finding("FLAT-TRL-COUNT", "FAIL", f"trailer {trailer} != detail {detail}",
                                {"file_id": file_id}))
    return raw, rows, findings, {"trailer_count": trailer, "detail_count": detail, "header": header}


# --- 834 -----------------------------------------------------------------------------
def map_834(pf: ParsedFile):
    rows, findings = [], []
    for isa13, gs06, st02, segs in transactions(pf):
        bgn08 = None
        cur = None
        for s in segs:
            e = s.elements
            if s.seg_id == "BGN":
                bgn08 = e[7] if len(e) > 7 else None
            elif s.seg_id == "INS":
                if cur:
                    rows.append(cur)
                cur = {"ins03": e[2] if len(e) > 2 else None, "maint_code": e[2] if len(e) > 2 else None,
                       "bgn08": bgn08, "isa13": isa13, "gs06": gs06, "st02": st02, "_map_status": "MAPPED",
                       "member_id": None, "last_name": None, "first_name": None, "dob": None, "sex": None,
                       "plan_id": None, "coverage_start": None, "coverage_end": None}
            elif cur is not None:
                if s.seg_id == "REF" and e and e[0] == "0F":
                    cur["member_id"] = e[1] if len(e) > 1 else None
                elif s.seg_id == "NM1" and e and e[0] == "IL":
                    cur["last_name"] = e[2] if len(e) > 2 else None
                    cur["first_name"] = e[3] if len(e) > 3 else None
                elif s.seg_id == "DMG":
                    cur["dob"] = _d(e[1] if len(e) > 1 else None)
                    cur["sex"] = e[2] if len(e) > 2 else None
                elif s.seg_id == "HD":
                    cur["plan_id"] = e[3] if len(e) > 3 else None
                elif s.seg_id == "DTP" and e and e[0] == "348":
                    cur["coverage_start"] = _d(e[2] if len(e) > 2 else None)
                elif s.seg_id == "DTP" and e and e[0] == "349":
                    cur["coverage_end"] = _d(e[2] if len(e) > 2 else None)
        if cur:
            rows.append(cur)
    for r in rows:
        _reject_bad(r, ("dob", "coverage_start", "coverage_end"))
        if not r["member_id"]:
            r["_map_status"], r["_map_reason"] = "REJECTED", "INS without REF*0F member id"
        r["_source_key"] = f"{r['isa13']}|{r['gs06']}|{r['st02']}|{r['member_id']}|{r['maint_code']}"
    return rows, findings


# --- 837P ------------------------------------------------------------------------------
def map_837p(pf: ParsedFile, submitter_id: str):
    """Loop-aware 837P mapper (005010X222A1), ENG-W1-5.

    Loop is tracked from HL / CLM / SBR / LX boundaries:
      2010AA  NM1*85 (+ REF*G2 for atypical providers) under the level-20 HL
      2010BA  NM1*IL under the level-22 HL, BEFORE any CLM            -> member_id
      2300    CLM ... until the first 2320 SBR or the first LX        -> REF*F8, HI, AMT (2300)
      2320    SBR inside a claim (other payer: the MCO's adjudication) -> AMT*D = mco_paid
      2330A/B NM1*IL / NM1*PR inside 2320 -> never overwrite the subscriber or the claim
      2400    LX ... SV1, DTP*472 (service date)
      2430    SVD (line adjudication: SVD02 = the plan's paid amount) ... DTP*573 (line adjudication date)
    """
    headers, lines, findings = [], [], []
    for isa13, gs06, st02, segs in transactions(pf):
        loc = {"isa13": isa13, "gs06": gs06, "st02": st02}
        findings.extend(check_hl_integrity(segs, loc))
        billing_npi = billing_alt = member_id = None
        cur = line = None
        loop = None
        for s in segs:
            e = s.elements
            sid = s.seg_id
            if sid == "HL":
                if line:
                    lines.append(line)
                    line = None
                if cur:
                    headers.append(cur)
                    cur = None
                loop = {"20": "2000A", "22": "2000B", "23": "2000C"}.get(s.hl_level, "2000?")
                if s.hl_level == "20":
                    billing_npi = billing_alt = None
                if s.hl_level == "22":
                    member_id = None
            elif sid == "NM1" and e and e[0] == "85" and cur is None:
                loop = "2010AA"
                billing_npi = (e[8] if len(e) > 8 else None) or None
            elif sid == "REF" and e and e[0] == "G2" and cur is None and loop in ("2010AA", "2010BB"):
                billing_alt = e[1] if len(e) > 1 else None
            elif sid == "NM1" and e and e[0] == "IL" and cur is None:
                loop = "2010BA"
                member_id = (e[8] if len(e) > 8 else None) or None
            elif sid == "NM1" and e and e[0] == "PR" and cur is None:
                loop = "2010BB"
            elif sid == "CLM":
                if line:
                    lines.append(line)
                    line = None
                if cur:
                    headers.append(cur)
                loop = "2300"
                comp = (e[4] if len(e) > 4 else "").split(pf.delimiters["sub"])
                cur = {"encounter_id": e[0] if e else None, "submitter_id": submitter_id,
                       "billing_npi": billing_npi, "billing_alt_id": billing_alt, "member_id": member_id,
                       "total_charge": _amt(e[1] if len(e) > 1 else None),
                       "pos": comp[0] if comp and comp[0] else None,
                       "freq_code": comp[2] if len(comp) > 2 and comp[2] else None,
                       "orig_encounter_id": None, "mco_paid": None, "dx1": None,
                       "isa13": isa13, "gs06": gs06, "st02": st02, "_map_status": "MAPPED"}
                if len(e) > 1 and e[1] and cur["total_charge"] is None:
                    cur["_map_status"], cur["_map_reason"] = "REJECTED", "unparseable CLM02"
            elif cur is None:
                continue
            elif sid == "LX":
                if line:
                    lines.append(line)
                loop = "2400"
                line = {"encounter_id": cur["encounter_id"], "submitter_id": submitter_id, "isa13": isa13,
                        "gs06": gs06, "st02": st02,
                        "line_no": int(e[0]) if e and e[0].isdigit() else None,
                        "proc_code": None, "line_charge": None, "units": None, "svc_date": None,
                        "paid_amt": None, "adjud_date": None, "_map_status": "MAPPED"}
            elif sid == "SBR" and loop in ("2300", "2320", "2330A", "2330B"):
                loop = "2320"
            elif sid == "NM1" and loop in ("2320", "2330A", "2330B") and e:
                loop = "2330A" if e[0] == "IL" else "2330B" if e[0] == "PR" else loop
            elif loop == "2300":
                if sid == "REF" and e and e[0] == "F8":
                    cur["orig_encounter_id"] = (e[1] if len(e) > 1 else None) or None
                elif sid == "HI" and e:
                    cur["dx1"] = (e[0].split(pf.delimiters["sub"]) + [None])[1]
                elif sid == "AMT" and e and e[0] == "D":          # spine placement (W1_SPINE), tolerated
                    cur["mco_paid"] = _amt(e[1] if len(e) > 1 else None)
            elif loop == "2320":
                if sid == "AMT" and e and e[0] == "D" and cur["mco_paid"] is None:
                    cur["mco_paid"] = _amt(e[1] if len(e) > 1 else None)
            elif loop == "2400" and line is not None:
                if sid == "SV1":
                    line["proc_code"] = (e[0].split(pf.delimiters["sub"]) + [None, None])[1] if e else None
                    line["line_charge"] = _amt(e[1] if len(e) > 1 else None)
                    line["units"] = _amt(e[3] if len(e) > 3 else None)
                    if len(e) > 1 and e[1] and line["line_charge"] is None:
                        line["_map_status"], line["_map_reason"] = "REJECTED", "unparseable SV102"
                elif sid == "DTP" and e and e[0] == "472":
                    line["svc_date"] = _d(e[2] if len(e) > 2 else None)
                elif sid == "SVD":
                    loop = "2430"
                    line["paid_amt"] = _amt(e[1] if len(e) > 1 else None)
            elif loop == "2430" and line is not None:
                if sid == "DTP" and e and e[0] == "573":
                    line["adjud_date"] = _d(e[2] if len(e) > 2 else None)
        if line:
            lines.append(line)
        if cur:
            headers.append(cur)
    for h in headers:
        h["_source_key"] = f"{h['submitter_id']}|{h['encounter_id']}|{h['isa13']}|{h['gs06']}|{h['st02']}"
        if not h["encounter_id"]:
            h["_map_status"], h["_map_reason"] = "REJECTED", "CLM01 missing"
    for ln in lines:
        _reject_bad(ln, ("svc_date",))
        ln["_source_key"] = f"{submitter_id}|{ln['encounter_id']}|{ln['isa13']}|{ln['gs06']}|{ln['st02']}|{ln['line_no']}"
    return headers, lines, findings
