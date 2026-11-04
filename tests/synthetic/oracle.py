"""Independent reference checker used ONLY to verify the synthetic seat's ground truth.

It re-parses the emitted files from bytes (it never imports generator internals) and reports every
W1-class anomaly it can see. The tests assert: realistic run -> no findings; seeded run -> findings
equal the ledger exactly. This proves each seeded defect is present and that nothing else in the
files trips a W1 check by accident of the generator.

NOTE for the orchestrator: this file encodes reference detection logic. If rule authoring is meant to
be blind, treat tests/synthetic/ like ledger.json until evaluation.
"""
from __future__ import annotations

import csv
import re
from collections import defaultdict
from decimal import Decimal
from pathlib import Path

X12_NAME = re.compile(r"^(834|837P)_([A-Z0-9]+)_(\d{8})_(\d+)\.x12$")
ACK_NAME = re.compile(r"^ack_([A-Z0-9]+)_(\d{8})_(\d+)\.csv$")


def luhn_npi_ok(npi: str) -> bool:
    if len(npi) != 10 or not npi.isdigit():
        return False
    total = 0
    for i, ch in enumerate(reversed("80840" + npi)):
        n = int(ch)
        if i % 2 == 1:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return total % 10 == 0


def tokenize(text: str):
    """Tolerant X12 tokenizer: delimiters derived from the 16th element separator, not fixed offsets."""
    assert text.startswith("ISA")
    ele = text[3]
    cnt = 0
    for i, ch in enumerate(text):
        if ch == ele:
            cnt += 1
            if cnt == 16:
                break
    term = text[i + 2]
    isa_len = i + 3
    segs = [s.split(ele) for s in text.split(term) if s.strip()]
    return isa_len, segs


def structure(segs):
    isa, groups, iea = segs[0], [], None
    g = st = None
    for s in segs[1:]:
        tag = s[0]
        if tag == "GS":
            g = {"gs": s, "sts": [], "ge": None}
            groups.append(g)
        elif tag == "ST":
            st = {"st": s, "segs": [s], "se": None}
            g["sts"].append(st)
        elif tag == "SE":
            st["segs"].append(s)
            st["se"] = s
            st = None
        elif tag == "GE":
            g["ge"] = s
        elif tag == "IEA":
            iea = s
        else:
            st["segs"].append(s)
    return isa, groups, iea


def load_ie(run: Path):
    rows = []        # (file, line_no, fields)
    trailer = {}
    for f in sorted(run.glob("ie_extract_*.txt")):
        lines = f.read_text(encoding="ascii").splitlines()
        n_dtl = 0
        for ln, line in enumerate(lines, start=1):
            parts = line.split("|")
            if parts[0] == "DTL":
                rows.append((f.name, ln, parts))
                n_dtl += 1
            elif parts[0] == "TRL":
                trailer[f.name] = (int(parts[1]), n_dtl)
    return rows, trailer


def findings(run: Path) -> dict:
    run = Path(run)
    out = defaultdict(set)
    ie_rows, trailer = load_ie(run)
    for fname, (trl, n) in trailer.items():
        if trl != n:
            out["IE_TRAILER_COUNT"].add(fname)

    med = [(f, ln, p) for f, ln, p in ie_rows if p[7] == "MEDICAID"]
    msis = {p[2] for _, _, p in med if p[2]}
    psk_of = {p[2]: p[1] for _, _, p in med if p[2]}
    for f, ln, p in med:
        if p[5] > p[10]:                       # dob > eff_start (YYYYMMDD compares lexically)
            out["DOB_AFTER_ELIG_START"].add((f, ln))
    latest = {}
    for f, ln, p in med:
        k = (p[1], p[9], p[10])
        if k not in latest or p[14] > latest[k][14]:
            latest[k] = p
    spans = defaultdict(list)
    for (psk, stype, _), p in latest.items():
        spans[(psk, stype)].append((p[10], p[11]))
    for (psk, stype), lst in spans.items():
        lst.sort()
        for i in range(len(lst)):
            for j in range(i + 1, len(lst)):
                if lst[j][0] <= lst[i][1] and lst[i][0] <= lst[j][1]:
                    out["ELIG_OVERLAP_SAME_TYPE"].add((psk, stype, frozenset({lst[i][0], lst[j][0]})))
    cover = defaultdict(list)
    for (psk, _, _), p in latest.items():
        cover[psk].append((p[10], p[11]))

    x12 = []
    for f in run.glob("*.x12"):
        m = X12_NAME.match(f.name)
        x12.append((m.group(3), int(m.group(4)), f))
    x12.sort(key=lambda t: (t[0], t[1], t[2].name))
    seen = set()
    for _, _, f in x12:
        isa_len, segs = tokenize(f.read_text(encoding="ascii"))
        isa, groups, iea = structure(segs)
        if isa_len != 106:
            out["ISA_WIDTH"].add(f.name)
        dk = (isa[6].strip(), isa[8].strip(), isa[13])
        if dk in seen:
            out["DUP_INTERCHANGE"].add(f.name)
        seen.add(dk)
        isa13 = isa[13]
        for g in groups:
            gs06 = g["gs"][6]
            if int(g["ge"][1]) != len(g["sts"]):
                out["GE_COUNT_MISMATCH"].add((f.name, isa13, gs06))
            for st in g["sts"]:
                st02 = st["st"][2]
                if int(st["se"][1]) != len(st["segs"]):
                    out["SE_COUNT_MISMATCH"].add((f.name, isa13, gs06, st02))
                if st["st"][1] == "837":
                    _check_837(out, f.name, gs06, st02, st["segs"], msis, psk_of, cover)
                elif st["st"][1] == "834":
                    segs834 = st["segs"]
                    for i, s in enumerate(segs834):
                        if s[0] == "INS" and s[3] == "024":
                            mid = segs834[i + 1][2]
                            if mid not in msis:
                                out["INS_TERM_UNKNOWN_MEMBER"].add((f.name, gs06, st02, mid))

    by_key = {}
    for _, _, f in x12:
        m = X12_NAME.match(f.name)
        by_key[(m.group(2), m.group(3), m.group(4))] = f.name
    for f in run.glob("ack_*.csv"):
        m = ACK_NAME.match(f.name)
        xname = by_key[(m.group(1), m.group(2), m.group(3))]
        with f.open(newline="") as fh:
            for r in csv.DictReader(fh):
                if r["status"] == "R":
                    out["TRANSLATOR_REJECT"].add((xname, r["isa13"], r["gs06"], r["st02"]))
    return dict(out)


def _check_837(out, fname, gs06, st02, segs, msis, psk_of, cover):
    hls = {}
    hl_id = level = None
    sub_member = None
    claims = []
    cl = None
    line = None
    for s in segs:
        tag = s[0]
        if tag == "HL":
            hl_id, parent, level = s[1], s[2], s[3]
            want = {"20": None, "22": "20", "23": "22"}[level]
            ok = (parent == "") if want is None else (parent in hls and hls[parent] == want)
            if not ok:
                out["HL_PARENT_BROKEN"].add((fname, gs06, st02, hl_id))
            hls[hl_id] = level
            cl = None
            if level == "22":
                sub_member = None
        elif tag == "NM1" and s[1] == "85" and level == "20":
            if len(s) > 9 and s[8] == "XX" and not luhn_npi_ok(s[9]):
                out["NPI_LUHN"].add((fname, gs06, st02, hl_id))
        elif tag == "NM1" and s[1] == "IL" and level == "22" and cl is None:
            sub_member = s[9]
        elif tag == "CLM":
            cl = {"eid": s[1], "clm02": Decimal(s[2]), "freq": (s[5].split(":") + ["", ""])[2], "f8": False,
                  "lines": [], "member": sub_member}
            claims.append(cl)
            line = None
        elif tag == "REF" and s[1] == "F8" and cl is not None and not cl["lines"]:
            cl["f8"] = True
        elif tag == "LX":
            line = {"lx": int(s[1]), "charge": None, "svc": None}
            cl["lines"].append(line)
        elif tag == "SV1":
            line["charge"] = Decimal(s[2])
        elif tag == "DTP" and s[1] == "472" and line is not None:
            line["svc"] = s[3]
    for c in claims:
        k = (fname, gs06, st02, c["eid"])
        if sum(ln["charge"] for ln in c["lines"]) != c["clm02"]:
            out["CLM02_LINE_SUM"].add(k)
        if c["freq"] in ("7", "8") and not c["f8"]:
            out["FREQ7_NO_F8"].add(k)
        if c["member"] not in msis:
            out["MEMBER_UNKNOWN"].add(k)
            continue
        spans = cover[psk_of[c["member"]]]
        for ln in c["lines"]:
            if not any(a <= ln["svc"] <= b for a, b in spans):
                out["SVC_OUTSIDE_ELIG"].add(k + (ln["lx"],))


def ledger_keys(ledger: list) -> dict:
    out = defaultdict(set)
    for e in ledger:
        c, f, L = e["defect_class"], e["file"], e["locator"]
        if c in ("ISA_WIDTH", "DUP_INTERCHANGE", "IE_TRAILER_COUNT"):
            out[c].add(f)
        elif c in ("SE_COUNT_MISMATCH", "TRANSLATOR_REJECT"):
            out[c].add((f, L["isa13"], L["gs06"], L["st02"]))
        elif c == "GE_COUNT_MISMATCH":
            out[c].add((f, L["isa13"], L["gs06"]))
        elif c in ("HL_PARENT_BROKEN", "NPI_LUHN"):
            out[c].add((f, L["gs06"], L["st02"], L["hl_id"]))
        elif c in ("CLM02_LINE_SUM", "FREQ7_NO_F8", "MEMBER_UNKNOWN"):
            out[c].add((f, L["gs06"], L["st02"], L["encounter_id"]))
        elif c == "SVC_OUTSIDE_ELIG":
            out[c].add((f, L["gs06"], L["st02"], L["encounter_id"], L["line_no"]))
        elif c == "DOB_AFTER_ELIG_START":
            for r in L["rows"]:
                out[c].add((r["file"], r["line_no"]))
        elif c == "ELIG_OVERLAP_SAME_TYPE":
            out[c].add((L["person_source_key"], L["segment_type"],
                        frozenset(r["eff_start"] for r in L["rows"])))
        elif c == "INS_TERM_UNKNOWN_MEMBER":
            out[c].add((f, L["gs06"], L["st02"], L["member_id"]))
        else:
            raise AssertionError(f"unknown class {c}")
    return dict(out)
