"""X12 005010 interchange model, 834 / 837P transaction builders and serialiser (spine §1.2-1.3,
with the X12-conformance additions listed in synthetic/DEVIATIONS.md)."""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

from .util import OPEN_END, fmt_money, seg_str, ymd

RECEIVER_ID = "ZZMEDICAID"


@dataclass
class ST:
    st02: str
    tset: str
    segs: list                      # segments ST..(last before SE), each a list of elements
    meta: list                      # parallel per-segment metadata dicts
    se_delta: int = 0
    ack_status: str = "A"           # translator 999 IK5 / AK9 outcome for this ST
    ack_code: str = ""


@dataclass
class Group:
    gs06: str
    func: str
    sender: str
    receiver: str
    version: str
    date: dt.date
    time: str
    sts: list = field(default_factory=list)
    ge_delta: int = 0


@dataclass
class Interchange:
    kind: str                       # '834' | '837P'
    partner: str                    # registry partner, e.g. ZZ-MCO-A
    sender: str                     # ISA06 / GS02 id, e.g. ZZMCOA
    date: dt.date
    time: str
    isa13: str
    groups: list = field(default_factory=list)
    receiver: str = RECEIVER_ID
    usage: str = "P"
    isa_variant: str | None = None
    filename: str = ""
    ack_filename: str = ""
    seq_n: int = 0
    role: str = "regular"           # regular | resend
    arrival: dt.date | None = None  # file arrival date (filename); defaults to ISA09 date
    order: int = 0


ISA_VARIANTS = {
    # name: (field index, transform) -- each changes ISA width away from 106
    "sender_pad_14": (6, lambda v: v.rstrip().ljust(14)),
    "receiver_pad_16": (8, lambda v: v.rstrip().ljust(16)),
    "auth_info_9": (2, lambda v: " " * 9),
    "security_info_11": (4, lambda v: " " * 11),
}


def isa_fields(ic: Interchange) -> list[str]:
    f = ["ISA", "00", " " * 10, "00", " " * 10, "ZZ", ic.sender.ljust(15), "ZZ", ic.receiver.ljust(15),
         ic.date.strftime("%y%m%d"), ic.time, "^", "00501", ic.isa13, "0", ic.usage, ":"]
    if ic.isa_variant:
        i, fn = ISA_VARIANTS[ic.isa_variant]
        f[i] = fn(f[i])
    return f


def serialize(ic: Interchange) -> str:
    out = ["*".join(isa_fields(ic)) + "~"]
    for g in ic.groups:
        out.append(seg_str(["GS", g.func, g.sender, g.receiver, ymd(g.date), g.time, g.gs06, "X", g.version]))
        for st in g.sts:
            out.extend(seg_str(s) for s in st.segs)
            out.append(seg_str(["SE", str(len(st.segs) + 1 + st.se_delta), st.st02]))
        out.append(seg_str(["GE", str(len(g.sts) + g.ge_delta), g.gs06]))
    out.append(seg_str(["IEA", str(len(ic.groups)), ic.isa13]))
    return "".join(out)


# --------------------------------------------------------------------------------------------- 834
def build_834_st(st02: str, bgn_ref: str, d: dt.date, t: str, bgn08: str, members: list[dict]) -> ST:
    segs = [["ST", "834", st02, "005010X220A1"],
            ["BGN", "00", bgn_ref, ymd(d), t, "", "", "", bgn08],
            ["N1", "P5", "ZZ MEDICAID AGENCY", "FI", "009900001"],
            ["N1", "IN", "ZZ MEDICAID MANAGED CARE", "FI", "009900002"]]
    meta = [{} for _ in segs]
    for k, m in enumerate(members, start=1):
        mm = {"member_id": m["member_id"], "ins_ordinal": k, "ins03": m["ins03"]}
        block = [["INS", "Y", "18", m["ins03"], m["ins04"], "A", "", "", "AC"],
                 ["REF", "0F", m["member_id"]],
                 ["NM1", "IL", "1", m["last"], m["first"]],
                 ["DMG", "D8", ymd(m["dob"]), m["sex"]],
                 ["HD", m["ins03"], "", "HMO", m["plan"]],
                 ["DTP", "348", "D8", ymd(m["start"])]]
        if m.get("end") and m["end"] != OPEN_END:
            block.append(["DTP", "349", "D8", ymd(m["end"])])
        segs.extend(block)
        meta.extend(dict(mm) for _ in block)
    return ST(st02, "834", segs, meta)


# -------------------------------------------------------------------------------------------- 837P
def build_837_st(st02: str, mco, d: dt.date, t: str, bht_ref: str, encs: list) -> ST:
    segs = [["ST", "837", st02, "005010X222A1"],
            ["BHT", "0019", "00", bht_ref, ymd(d), t, "RP"],
            ["NM1", "41", "2", mco.name, "", "", "", "", "46", mco.edi_id],
            ["PER", "IC", "EDI SUPPORT", "TE", "5555550142"],
            ["NM1", "40", "2", "ZZ MEDICAID", "", "", "", "", "46", RECEIVER_ID]]
    meta = [{} for _ in segs]

    prov_order, by_prov = [], {}
    for e in encs:
        k = e.provider.idx
        if k not in by_prov:
            prov_order.append(k)
            by_prov[k] = []
        by_prov[k].append(e)

    hl = 0
    for k in prov_order:
        pencs = by_prov[k]
        prov = pencs[0].provider
        hl += 1
        bill_hl = hl
        cur = {"hl_id": str(bill_hl), "hl_level": "20"}

        def add(s, extra=None, _cur=None):
            segs.append(s)
            m = dict(_cur if _cur is not None else cur)
            if extra:
                m.update(extra)
            meta.append(m)

        add(["HL", str(bill_hl), "", "20", "1"])
        if prov.npi:
            add(["NM1", "85", "2", prov.name, "", "", "", "", "XX", prov.npi])
        else:
            add(["NM1", "85", "2", prov.name])           # atypical: NM108/NM109 not used
        add(["N3", prov.addr1])
        add(["N4", prov.city, "ZZ", prov.zip9])
        add(["REF", "EI", prov.ein])

        mem_order, by_mem = [], {}
        for e in pencs:
            if e.member_id not in by_mem:
                mem_order.append(e.member_id)
                by_mem[e.member_id] = []
            by_mem[e.member_id].append(e)
        for mid in mem_order:
            mencs = by_mem[mid]
            e0 = mencs[0]
            hl += 1
            sub = {"hl_id": str(hl), "hl_level": "22", "hl_parent": str(bill_hl)}
            add(["HL", str(hl), str(bill_hl), "22", "0"], _cur=sub)
            add(["SBR", "P", "18", "", "", "", "", "", "", "MC"], _cur=sub)
            add(["NM1", "IL", "1", e0.last, e0.first, "", "", "", "MI", mid], _cur=sub)
            add(["N3", e0.addr1], _cur=sub)
            add(["N4", e0.city, "ZZ", e0.zip9], _cur=sub)
            add(["DMG", "D8", ymd(e0.dob), e0.sex], _cur=sub)
            add(["NM1", "PR", "2", "ZZ MEDICAID", "", "", "", "", "PI", RECEIVER_ID], _cur=sub)
            if not prov.npi:
                add(["REF", "G2", prov.state_id], _cur=sub)
            for e in mencs:
                c = dict(sub, encounter_id=e.eid)
                clm05 = f"{e.pos}:B:{e.freq}" if e.freq else f"{e.pos}:B"   # empty CLM05-3: trailing ':' suppressed
                add(["CLM", e.eid, fmt_money(e.clm02), "", "", clm05, "Y", "A", "Y", "Y"], _cur=c)
                if e.freq in ("7", "8") and e.emit_f8:
                    add(["REF", "F8", e.orig_ref], _cur=c)
                hi = ["HI", f"ABK:{e.dx[0]}"] + [f"ABF:{x}" for x in e.dx[1:]]
                add(hi, _cur=c)
                # Loop 2320/2330A/2330B: the MCO's adjudication (encounter reporting)
                add(["SBR", "S", "18", "", "", "", "", "", "", "HM"], _cur=c)
                add(["AMT", "D", fmt_money(e.paid)], _cur=c)
                add(["OI", "", "", "Y", "P", "", "Y"], _cur=c)
                add(["NM1", "IL", "1", e.last, e.first, "", "", "", "MI", e.member_id], _cur=c)
                add(["NM1", "PR", "2", mco.name, "", "", "", "", "PI", mco.edi_id], _cur=c)
                for n, ln in enumerate(e.lines, start=1):
                    lc = dict(c, lx=n)
                    add(["LX", str(n)], _cur=lc)
                    add(["SV1", f"HC:{ln.proc}", fmt_money(ln.charge), "UN", str(ln.units), "", "",
                         str(ln.dx_ptr)], _cur=lc)
                    add(["DTP", "472", "D8", ymd(ln.svc)], _cur=lc)
                    add(["SVD", mco.edi_id, fmt_money(ln.paid), f"HC:{ln.proc}", "", str(ln.units)], _cur=lc)
                    if ln.charge > ln.paid:
                        add(["CAS", "CO", "45", fmt_money(ln.charge - ln.paid)], _cur=lc)
                    if not e.omit_573:
                        add(["DTP", "573", "D8", ymd(e.adj_date)], _cur=lc)
    return ST(st02, "837", segs, meta)
