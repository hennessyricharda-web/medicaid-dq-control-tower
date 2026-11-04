"""Tolerant X12 tokenizer, envelope and transaction-structure checks (DESIGN §13).

Principles:
  * The tokenizer ALWAYS returns raw segments, even for malformed files. A structural
    problem becomes a finding (a disposition), never an exception that drops the file.
  * Delimiters are LOCATED, not read at fixed offsets (ENG-W1-2): element = the 4th
    character; ISA16 (sub-element) is the character after the 16th element separator, and
    the segment terminator is the character after ISA16 — which may itself be CR or LF.
    CR/LF are stripped only when they are NOT the terminator. A short or long ISA is still
    parsed with the located delimiters and reported as ENV-ISA-WIDTH; a file whose ISA cannot
    be located at all yields ENV-STRUCT and zero transactions (caught by the FILE->RAW hop).
  * Each check carries the IG/ASC X12 reference it implements. Unless a primary source
    is cited, the check is labelled COALITION-DEFINED.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field


@dataclass
class Segment:
    idx: int
    seg_id: str
    elements: list[str]
    isa13: str | None = None
    gs06: str | None = None
    st02: str | None = None
    hl_id: str | None = None
    hl_parent: str | None = None
    hl_level: str | None = None


@dataclass
class Finding:
    check_id: str
    severity: str           # FAIL | TA_WARN
    message: str
    locator: dict = field(default_factory=dict)


@dataclass
class ParsedFile:
    file_id: str
    sha256: str
    delimiters: dict
    segments: list[Segment]
    findings: list[Finding]
    interchanges: list[dict]     # [{isa13, sender, receiver, usage, groups:[{gs06, gs01, gs08, transactions:[{st01,st02,st03,segment_count_actual,se01}]}]}]


def file_sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def locate_delimiters(text: str) -> tuple[dict | None, int | None]:
    """Return ({element, repetition, sub, segment}, isa_length) or (None, None)."""
    if not text.startswith("ISA") or len(text) < 4:
        return None, None
    el = text[3]
    pos, n = -1, 0
    for i, ch in enumerate(text[:400]):
        if ch == el:
            n += 1
            if n == 16:
                pos = i
                break
    if pos < 0 or pos + 2 >= len(text):
        return None, None
    sub, term = text[pos + 1], text[pos + 2]
    isa_elems = text[:pos + 1].split(el)
    rep = isa_elems[11] if len(isa_elems) > 11 and len(isa_elems[11]) == 1 else "^"
    if term == "\r" and text[pos + 3:pos + 4] == "\n":
        term = "\r\n"
    return {"element": el, "repetition": rep, "sub": sub, "segment": term}, pos + 2 + 1


def tokenize(data: bytes, file_id: str) -> ParsedFile:
    text = data.decode("ascii", errors="replace").lstrip("\ufeff \t\r\n")
    findings: list[Finding] = []
    delims, isa_len = locate_delimiters(text)
    if delims is None:
        findings.append(Finding("ENV-STRUCT", "FAIL", "no locatable ISA header; file cannot be tokenized",
                                {"file_id": file_id}))
        delims = {"element": "*", "repetition": "^", "sub": ":", "segment": "~"}
    else:
        widths = [3, 2, 10, 2, 10, 2, 15, 2, 15, 6, 4, 1, 5, 9, 1, 1, 1]
        parts = text[:isa_len - len(delims["segment"]) if delims["segment"] == "\r\n" else isa_len - 1].split(delims["element"])
        if isa_len != 106 or len(parts) != 17 or [len(p) for p in parts] != widths:
            findings.append(Finding("ENV-ISA-WIDTH", "FAIL",
                                    f"ISA is not the fixed 106-character layout (located length {isa_len})",
                                    {"file_id": file_id}))
    if delims["segment"] not in ("\r", "\n", "\r\n"):
        text = text.replace("\r", "").replace("\n", "")
    raw = [s.strip("\r\n") for s in text.split(delims["segment"]) if s.strip()]
    segs: list[Segment] = []
    isa13 = gs06 = st02 = None
    for i, s in enumerate(raw):
        el = s.split(delims["element"])
        sid = el[0].strip()
        if sid == "ISA":
            isa13 = el[13] if len(el) > 13 else None
            gs06 = st02 = None
        elif sid == "GS":
            gs06 = el[6] if len(el) > 6 else None
        elif sid == "ST":
            st02 = el[2] if len(el) > 2 else None
        seg = Segment(i, sid, el[1:], isa13, gs06, st02)
        if sid == "HL":
            seg.hl_id = el[1] if len(el) > 1 else None
            seg.hl_parent = el[2] if len(el) > 2 and el[2] else None
            seg.hl_level = el[3] if len(el) > 3 else None
        segs.append(seg)
    pf = ParsedFile(file_id, file_sha256(data), delims, segs, findings, [])
    _envelope(pf)
    return pf


def _envelope(pf: ParsedFile) -> None:
    """Envelope integrity checks (ASC X12 envelope structure).

    Each check below is a COALITION-DEFINED rule implementing the standard envelope semantics:
      ENV-IEA-CTRL : ISA13 == IEA02
      ENV-IEA-CNT  : IEA01 == number of GS groups
      ENV-GE-CTRL  : GS06 == GE02
      ENV-GE-CNT   : GE01 == number of ST transactions
      ENV-GS08-ST03: GS08 (version / IG id) == ST03
      ENV-SE-CTRL  : ST02 == SE02
      ENV-SE-CNT   : SE01 == segment count including ST and SE
      ENV-STRUCT   : every ISA/GS/ST is closed by its trailer, in order
    """
    f = pf.findings
    cur_isa = cur_gs = cur_st = None
    for s in pf.segments:
        e = s.elements
        if s.seg_id == "ISA":
            cur_isa = {"isa13": e[12] if len(e) > 12 else None, "sender_q": e[4] if len(e) > 4 else None,
                       "sender": (e[5] if len(e) > 5 else "").strip(),
                       "receiver_q": e[6] if len(e) > 6 else None,
                       "receiver": (e[7] if len(e) > 7 else "").strip(),
                       "usage": e[14] if len(e) > 14 else None, "groups": [], "closed": False}
            pf.interchanges.append(cur_isa)
        elif s.seg_id == "GS":
            if cur_isa is None:
                f.append(Finding("ENV-STRUCT", "FAIL", "GS outside ISA", {"seg_idx": s.idx}))
                continue
            cur_gs = {"gs01": e[0] if e else None, "gs06": e[5] if len(e) > 5 else None,
                      "gs08": e[7] if len(e) > 7 else None, "transactions": [], "closed": False}
            cur_isa["groups"].append(cur_gs)
        elif s.seg_id == "ST":
            if cur_gs is None:
                f.append(Finding("ENV-STRUCT", "FAIL", "ST outside GS", {"seg_idx": s.idx}))
                continue
            cur_st = {"st01": e[0] if e else None, "st02": e[1] if len(e) > 1 else None,
                      "st03": e[2] if len(e) > 2 else None, "start": s.idx, "se01": None, "closed": False}
            cur_gs["transactions"].append(cur_st)
            if cur_gs["gs08"] and cur_st["st03"] and cur_gs["gs08"] != cur_st["st03"]:
                f.append(Finding("ENV-GS08-ST03", "FAIL", "GS08 does not equal ST03",
                                 {"isa13": cur_isa["isa13"], "gs06": cur_gs["gs06"], "st02": cur_st["st02"]}))
        elif s.seg_id == "SE":
            if cur_st is None:
                f.append(Finding("ENV-STRUCT", "FAIL", "SE without ST", {"seg_idx": s.idx}))
                continue
            actual = s.idx - cur_st["start"] + 1
            cur_st["segment_count_actual"] = actual
            cur_st["se01"] = e[0] if e else None
            loc = {"isa13": cur_isa["isa13"], "gs06": cur_gs["gs06"], "st02": cur_st["st02"]}
            if (e[1] if len(e) > 1 else None) != cur_st["st02"]:
                f.append(Finding("ENV-SE-CTRL", "FAIL", "SE02 does not equal ST02", loc))
            if str(actual) != (e[0] if e else ""):
                f.append(Finding("ENV-SE-CNT", "FAIL", f"SE01={e[0] if e else None} but actual={actual}", loc))
            cur_st["closed"] = True
            cur_st = None
        elif s.seg_id == "GE":
            if cur_gs is None:
                f.append(Finding("ENV-STRUCT", "FAIL", "GE without GS", {"seg_idx": s.idx}))
                continue
            loc = {"isa13": cur_isa["isa13"], "gs06": cur_gs["gs06"]}
            if (e[1] if len(e) > 1 else None) != cur_gs["gs06"]:
                f.append(Finding("ENV-GE-CTRL", "FAIL", "GE02 does not equal GS06", loc))
            if (e[0] if e else None) != str(len(cur_gs["transactions"])):
                f.append(Finding("ENV-GE-CNT", "FAIL",
                                 f"GE01={e[0] if e else None} but transactions={len(cur_gs['transactions'])}", loc))
            cur_gs["closed"] = True
            cur_gs = None
        elif s.seg_id == "IEA":
            if cur_isa is None:
                f.append(Finding("ENV-STRUCT", "FAIL", "IEA without ISA", {"seg_idx": s.idx}))
                continue
            loc = {"isa13": cur_isa["isa13"]}
            if (e[1] if len(e) > 1 else None) != cur_isa["isa13"]:
                f.append(Finding("ENV-IEA-CTRL", "FAIL", "IEA02 does not equal ISA13", loc))
            if (e[0] if e else None) != str(len(cur_isa["groups"])):
                f.append(Finding("ENV-IEA-CNT", "FAIL", "IEA01 does not equal the group count", loc))
            cur_isa["closed"] = True
            cur_isa = None
    for isa in pf.interchanges:
        if not isa["closed"]:
            f.append(Finding("ENV-STRUCT", "FAIL", "ISA not closed by IEA", {"isa13": isa["isa13"]}))
        for g in isa["groups"]:
            if not g["closed"]:
                f.append(Finding("ENV-STRUCT", "FAIL", "GS not closed by GE", {"gs06": g["gs06"]}))
            for t in g["transactions"]:
                if not t["closed"]:
                    f.append(Finding("ENV-STRUCT", "FAIL", "ST not closed by SE", {"st02": t["st02"]}))


def transactions(pf: ParsedFile):
    """Yield (isa13, gs06, st02, [segments]) per ST..SE transaction."""
    cur = None
    for s in pf.segments:
        if s.seg_id == "ST":
            cur = (s.isa13, s.gs06, s.elements[1] if len(s.elements) > 1 else None, [s])
        elif cur is not None:
            cur[3].append(s)
            if s.seg_id == "SE":
                yield cur
                cur = None


def check_hl_integrity(segs: list[Segment], loc: dict) -> list[Finding]:
    """837 HL integrity (COALITION-DEFINED, per the 837 IG hierarchical structure):
    HL01 unique; HL02 references a PRIOR HL01; HL03 levels: 20 has no parent, 22 has a 20 parent."""
    out, seen = [], {}
    for s in segs:
        if s.seg_id != "HL":
            continue
        if s.hl_id in seen:
            out.append(Finding("TX-HL-UNIQUE", "FAIL", f"duplicate HL01 {s.hl_id}", loc))
        if s.hl_level == "20" and s.hl_parent:
            out.append(Finding("TX-HL-PARENT", "FAIL", f"HL {s.hl_id} level 20 must not have a parent", loc))
        if s.hl_level in ("22", "23"):
            if not s.hl_parent or s.hl_parent not in seen:
                out.append(Finding("TX-HL-PARENT", "FAIL",
                                   f"HL {s.hl_id} parent {s.hl_parent} is not a prior HL", loc))
            elif s.hl_level == "22" and seen[s.hl_parent] != "20":
                out.append(Finding("TX-HL-PARENT", "FAIL", f"HL {s.hl_id} (22) parent is not level 20", loc))
        seen[s.hl_id] = s.hl_level
    return out
