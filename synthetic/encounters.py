"""MCO encounter generation (originals, frequency-7 replacements, frequency-8 voids).

Eligibility discipline (what keeps realistic mode defect-free by construction):
an encounter service date d in MCO file dated fd is drawn only when some non-defect FULL
MEDICAID key of the member has EVERY recorded version covering d, the key's first version was
recorded before fd, and that first version was conveyed to the MCO by a broker 834 dated <= fd.
Dates covered by only some versions (retro terms, reinstatements) are never used, so a rule
reading latest-version-per-key and a rule reading as-of state agree on every clean encounter.
"""
from __future__ import annotations

import copy
import datetime as dt
from dataclasses import dataclass, field
from decimal import Decimal

from .population import (ATYPICAL_SERVICES, DX_ADULT, DX_CHILD, HCPCS_MEDICAL, Z9_CODES, Population,
                         _age_on)
from .util import days, money, rand_date


@dataclass
class Line:
    proc: str
    charge: Decimal
    units: int
    svc: dt.date
    paid: Decimal
    dx_ptr: int = 1


@dataclass
class Encounter:
    eid: str
    mco: str
    psk: str
    member_id: str
    first: str
    last: str
    dob: dt.date
    sex: str
    addr1: str
    city: str
    zip9: str
    provider: object
    pos: str
    dx: list
    lines: list
    slot: int
    fd: dt.date
    adj_date: dt.date
    freq: str = "1"
    orig_ref: str | None = None
    emit_f8: bool = True
    clm02_override: Decimal | None = None
    superseded: bool = False
    chained: bool = False          # participates in a 7/8 chain (as original or as a version)
    omit_573: bool = False         # PART C seed MISSING_ADJUD_DATE: no 2430 DTP*573 on any line
    tags: list = field(default_factory=list)

    @property
    def line_sum(self) -> Decimal:
        return sum((ln.charge for ln in self.lines), Decimal("0.00"))

    @property
    def clm02(self) -> Decimal:
        return self.clm02_override if self.clm02_override is not None else self.line_sum

    @property
    def paid(self) -> Decimal:
        return sum((ln.paid for ln in self.lines), Decimal("0.00"))


@dataclass
class Slot:
    mco: str
    idx: int
    fd: dt.date


class Elig:
    def __init__(self, pop: Population, full_date: dt.date, change_date: dt.date):
        self.pop, self.f0, self.c = pop, full_date, change_date

    def conveyed(self, rec: dt.datetime):
        if rec.date() < self.f0:
            return self.f0
        if rec.date() < self.c:
            return self.c
        return None

    def mco_ok(self, psk, d, fd) -> bool:
        for vers in self.pop.versions_by_key(psk, "FULL", include_defect=False).values():
            if all(v.covers(d) for v in vers):
                cd = self.conveyed(vers[0].recorded_at)
                if cd is not None and cd <= fd and vers[0].recorded_at.date() < fd:
                    return True
        return False


def _member_fields(p):
    return dict(psk=p.psk, member_id=p.msis_id, first=p.first, last=p.last, dob=p.dob, sex=p.sex,
                addr1=p.addr1, city=p.city, zip9=p.zip9)


def _rate(rng) -> Decimal:
    return Decimal(rng.randint(55, 90)) / 100


def _make_lines(pop, rng, prov, p, d, lo, hi, elig, fd):
    """Return (pos, dx list, lines)."""
    if prov.atypical:
        pos, fee, (umin, umax) = ATYPICAL_SERVICES[prov.service]
        dx = [rng.choice(DX_ADULT if _age_on(p.base_dob, d) >= 19 else DX_CHILD)]
        n = rng.choice([1, 1, 2, 3])
        lines = []
        for i in range(n):
            ld = d + days(i * 7)
            if ld > hi or not elig.mco_ok(p.psk, ld, fd):
                ld = d
            units = rng.randint(umin, umax)
            charge = money(Decimal(fee) * units)
            lines.append(Line(prov.service, charge, units, ld, money(charge * _rate(rng))))
        return pos, dx, lines
    adult = _age_on(p.base_dob, d) >= 19
    dx = [rng.choice(DX_ADULT if adult else DX_CHILD)]
    pos = "02" if rng.random() < 0.08 else ("50" if prov.is_chc else "11")
    n = rng.choices([1, 2, 3, 4], weights=[45, 30, 17, 8])[0]
    lines = []
    for _ in range(n):
        ptr = 1
        if rng.random() < 0.2:
            proc = rng.choice(sorted(HCPCS_MEDICAL))
            pdx = HCPCS_MEDICAL[proc][0]
            if pdx:
                if pdx not in dx and len(dx) < 2:
                    dx.append(pdx)
                ptr = dx.index(pdx) + 1 if pdx in dx else 1
            units = 1 if proc != "A4253" else rng.randint(1, 2)
        else:
            proc = rng.choice(Z9_CODES)
            units = rng.choices([1, 2], weights=[85, 15])[0]
        ld = d
        if rng.random() < 0.15:
            cand = d + days(rng.choice([-3, -1, 1, 2]))
            if lo <= cand <= hi and elig.mco_ok(p.psk, cand, fd):
                ld = cand
        charge = money(pop.fees[proc] * units)
        lines.append(Line(proc, charge, units, ld, money(charge * _rate(rng)), ptr))
    return pos, dx, lines


def generate_encounters(pop: Population, rng, elig: Elig, slots: list[Slot]):
    seq = {m: rng.randint(400000, 600000) for m in sorted(pop.mcos)}
    yy = pop.start.year % 100

    def next_id(m):
        seq[m] += rng.randint(1, 4)
        return f"{m}{yy:02d}{seq[m]:07d}"

    encs: list[Encounter] = []
    controls = []
    for slot in slots:
        mco = pop.mcos[slot.mco]
        lo, hi = max(pop.start, slot.fd - days(80)), slot.fd - days(3)
        ok_dates = {}
        for p in pop.persons:
            if p.mco != slot.mco:
                continue
            ds = [lo + days(i) for i in range((hi - lo).days + 1) if elig.mco_ok(p.psk, lo + days(i), slot.fd)]
            if ds:
                ok_dates[p.psk] = ds
        members = [pop.by_psk[k] for k in ok_dates]
        for _ in range(rng.randint(19, 25)):
            p = rng.choice(members)
            d = rng.choice(ok_dates[p.psk])
            prov = rng.choice(mco.network)
            pos, dx, lines = _make_lines(pop, rng, prov, p, d, lo, hi, elig, slot.fd)
            mx = max(ln.svc for ln in lines)
            adj = rand_date(rng, mx + days(1), slot.fd - days(1))
            encs.append(Encounter(eid=next_id(slot.mco), mco=slot.mco, provider=prov, pos=pos, dx=dx,
                                  lines=lines, slot=slot.idx, fd=slot.fd, adj_date=adj, **_member_fields(p)))

    # Boundary negative controls: service exactly on a span boundary (inclusive) is IN eligibility.
    boundary_done = 0
    for e in rng.sample(encs, len(encs)):
        if boundary_done >= 3:
            break
        lo, hi = max(pop.start, e.fd - days(80)), e.fd - days(3)
        for vers in pop.versions_by_key(e.psk, "FULL", include_defect=False).values():
            latest = vers[-1]
            for bd, kind in ((latest.eff_start, "eff_start"), (latest.eff_end, "eff_end")):
                if lo <= bd <= hi and elig.mco_ok(e.psk, bd, e.fd) and e.lines[0].svc != bd:
                    e.lines[0].svc = bd
                    e.adj_date = max(e.adj_date, max(ln.svc for ln in e.lines) + days(1))
                    e.tags.append("boundary")
                    controls.append({"kind": "SVC_ON_SPAN_BOUNDARY", "encounter_id": e.eid,
                                     "line_no": 1, "svc_date": bd.isoformat(), "boundary": kind,
                                     "person_source_key": e.psk})
                    boundary_done += 1
                    break
            if "boundary" in e.tags:
                break

    # Chains: replacements (freq 7) and voids (freq 8) in later files, REF*F8 -> prior version.
    for slot in slots:
        if slot.idx == 0:
            continue
        live = [e for e in encs if e.mco == slot.mco and e.slot < slot.idx and not e.superseded
                and e.freq != "8"]
        k_rep, k_void = rng.randint(2, 3), rng.randint(1, 2)
        targets = rng.sample(live, min(len(live), k_rep + k_void))
        for i, t in enumerate(targets):
            new = copy.deepcopy(t)
            new.eid = next_id(slot.mco)
            new.freq = "7" if i < k_rep else "8"
            new.orig_ref = t.eid
            new.slot, new.fd = slot.idx, slot.fd
            new.tags = []
            if new.freq == "7":
                ln = rng.choice(new.lines)
                unit_fee = ln.charge / ln.units
                ln.units += 1
                ln.charge = money(unit_fee * ln.units)
                ln.paid = money(ln.charge * _rate(rng))
            new.adj_date = rand_date(rng, max(t.adj_date + days(1), slot.fd - days(20)), slot.fd - days(1))
            t.superseded = True
            t.chained = new.chained = True
            encs.append(new)
    return encs, controls
