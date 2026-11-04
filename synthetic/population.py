"""Synthetic state-ZZ population: persons, households, IE eligibility segments, providers and MCOs.

No real PHI. Names come from the small synthetic lists below. All identifiers are synthetic.
"""
from __future__ import annotations

import datetime as dt
from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal

from .util import OPEN_END, add_years, days, make_npi, money, rand_date, rand_ts

FIRST_NAMES = [
    "AVERY", "BLAKE", "CAMERON", "DAKOTA", "EMERSON", "FINLEY", "GRAY", "HARPER", "INDIGO", "JULES",
    "KAI", "LANE", "MARLO", "NOVA", "OAKLEY", "PARKER", "QUINN", "REESE", "SAGE", "TATUM",
    "VALE", "WREN", "YARROW", "ROWAN", "SKYLER", "ELLIS", "MORGAN", "SHILOH", "ARDEN", "LENNOX",
]
LAST_NAMES = [
    "ALDERWICK", "BRAMBLEFORD", "CINDERMOSS", "DAPPLEGATE", "EMBERLY", "FERNHOLLOW", "GLIMMERSTONE",
    "HAZELTOFT", "IVYMERE", "JUNIPERFIELD", "KESTRELAND", "LARKSPURNE", "MOSSBANK", "NETTLEFORD",
    "OAKSHADE", "PEBBLEWORTH", "QUILLBROOK", "RUSHMEADE", "SORRELTON", "THISTLEDOWN", "UMBERVALE",
    "VERBENAULT", "WILLOWICK", "YEWBRIDGE", "ZEPHYRTON",
]
CITIES = ["PINEHAVEN", "LAKEMONT", "CEDARVALE", "MILLBROOK", "STONEFORD", "RIVERTON FALLS"]
STREETS = ["MAPLE", "BIRCH", "ASPEN", "LINDEN", "HAWTHORN", "SYCAMORE", "ORCHARD", "PRAIRIE"]
ORG_WORDS = [
    "MAPLE RIDGE", "CEDAR HOLLOW", "RIVERBEND", "STONEGATE", "LANTERN HILL", "BLUE HERON", "FOXGLOVE",
    "GRANITE PEAK", "WILLOW CREEK", "SUNPORCH", "IRONWOOD", "MEADOWLARK", "SILVER LAKE", "NORTHFIELD",
    "BRIARWOOD", "COPPER CANYON", "HARBORVIEW", "PINE KNOLL", "QUARRY ROAD", "RED OAK",
]
ORG_TYPES = ["FAMILY MEDICINE", "PEDIATRICS", "COMMUNITY HEALTH CENTER", "INTERNAL MEDICINE",
             "BEHAVIORAL HEALTH", "WOMENS HEALTH"]

# Synthetic CPT-shaped procedure space (reserved prefix Z9, 5 chars) + public HCPCS Level II codes.
Z9_CODES = [f"Z9{n:03d}" for n in range(1, 61)]
# public HCPCS Level II: code -> (paired ICD-10-CM dx or None, unit fee)
HCPCS_MEDICAL = {"G0008": ("Z23", "25.00"), "G0444": ("F329", "30.00"),
                 "A4253": ("E119", "42.00"), "Q3014": (None, "28.00")}
# atypical (non-NPI) provider services, public HCPCS Level II
ATYPICAL_SERVICES = {"T2003": ("99", "35.00", (1, 2)),     # non-emergency transport, per trip
                     "T1019": ("12", "6.50", (4, 16)),     # personal care, per 15 min
                     "S5170": ("12", "9.75", (1, 1))}      # home delivered meals
# public ICD-10-CM codes (no decimal point, X12 form)
DX_ADULT = ["E119", "I10", "J069", "Z0000", "M5450", "F329", "E785", "K219", "N390"]
DX_CHILD = ["Z00129", "J069", "N390", "K219"]


@dataclass
class Person:
    psk: str
    msis_id: str
    first: str
    last: str
    dob: dt.date
    sex: str
    household_id: str
    county: str
    kind: str                      # 'medicaid' | 'snap_only'
    addr1: str
    city: str
    zip9: str
    newborn: bool = False
    elderly: bool = False
    base_dob: dt.date | None = None    # dob before any seeded defect (drives clinical choices)
    pattern: str = ""
    full_group: str = ""
    has_snap: bool = False
    mco: str | None = None
    plan_id: str | None = None


@dataclass
class IERow:
    row_id: int
    psk: str
    program: str
    elig_group: str
    segment_type: str
    eff_start: dt.date
    eff_end: dt.date
    recorded_at: dt.datetime
    tag: str = "base"              # base | correction | defect

    @property
    def key(self):
        return (self.psk, self.program, self.segment_type, self.eff_start)

    def covers(self, d: dt.date) -> bool:
        return self.eff_start <= d <= self.eff_end


@dataclass
class Provider:
    idx: int
    name: str
    npi: str | None
    atypical: bool
    state_id: str
    ein: str
    addr1: str
    city: str
    zip9: str
    is_chc: bool = False
    service: str | None = None     # atypical providers bill one HCPCS service


@dataclass
class MCO:
    code: str                      # 'A' | 'B'
    partner: str                   # ZZ-MCO-A
    edi_id: str                    # ZZMCOA (ISA06 / GS02 / filename token)
    name: str
    plans: list[str]
    network: list[Provider] = field(default_factory=list)


@dataclass
class Population:
    start: dt.date
    end: dt.date
    persons: list[Person] = field(default_factory=list)
    by_psk: dict = field(default_factory=dict)
    rows: list[IERow] = field(default_factory=list)
    providers: list[Provider] = field(default_factory=list)
    mcos: dict = field(default_factory=dict)
    fees: dict = field(default_factory=dict)
    used_ids: set = field(default_factory=set)

    def add_person(self, p: Person):
        self.persons.append(p)
        self.by_psk[p.psk] = p

    def add_row(self, p: Person, program, group, stype, s, e, rec, tag="base") -> IERow:
        r = IERow(len(self.rows) + 1, p.psk, program, group, stype, s, e, rec, tag)
        self.rows.append(r)
        return r

    def medicaid_rows(self, psk=None):
        return [r for r in self.rows if r.program == "MEDICAID" and (psk is None or r.psk == psk)]

    def medicaid_ids(self) -> set:
        return {p.msis_id for p in self.persons if p.kind == "medicaid"}

    def versions_by_key(self, psk, stype=None, include_defect=True):
        out = defaultdict(list)
        for r in self.medicaid_rows(psk):
            if stype and r.segment_type != stype:
                continue
            if not include_defect and r.tag == "defect":
                continue
            out[r.key].append(r)
        for k in out:
            out[k].sort(key=lambda r: r.recorded_at)
        return dict(out)

    def any_cover(self, psk, d) -> bool:
        return any(r.covers(d) for r in self.medicaid_rows(psk))


def _age_on(dob: dt.date, d: dt.date) -> int:
    return d.year - dob.year - ((d.month, d.day) < (dob.month, dob.day))


def build_population(rng, start: dt.date, end: dt.date, target: int = 300) -> Population:
    pop = Population(start=start, end=end)

    def new_msis():
        while True:
            m = f"ZZ{rng.randint(10_000_000, 99_999_999)}"
            if m not in pop.used_ids:
                pop.used_ids.add(m)
                return m

    hh = 0
    while len(pop.persons) < target:
        hh += 1
        hid = f"HH{100000 + hh * 3}"
        county = f"{rng.randrange(1, 30, 2):03d}"
        city = rng.choice(CITIES)
        addr1 = f"{rng.randint(10, 9999)} {rng.choice(STREETS)} {rng.choice(['ST', 'AVE', 'RD', 'LN'])}"
        zip9 = f"000{rng.randint(10, 99)}{rng.randint(1000, 9999)}"
        snap_only = rng.random() < 0.20
        size = rng.choices([1, 2, 3, 4, 5], weights=[28, 26, 22, 15, 9])[0]
        elderly = (not snap_only) and size <= 2 and rng.random() < 0.30
        last = rng.choice(LAST_NAMES)
        ages = []
        for i in range(size):
            if elderly:
                ages.append(rng.randint(65, 92))
            elif i == 0 or (i == 1 and rng.random() < 0.6):
                ages.append(rng.randint(19, 64))
            else:
                ages.append(rng.randint(0, 18))
        members = [(start - days(a * 365 + rng.randint(0, 364)), False) for a in ages]
        if not snap_only and not elderly and size >= 2 and rng.random() < 0.15:
            members.append((rand_date(rng, start - days(120), start + days(40)), True))
        hh_snap = snap_only or rng.random() < 0.25
        hh_mco = rng.choice(["A", "B"]) if (not snap_only and rng.random() < 0.80) else None
        for dob, newborn in members:
            p = Person(
                psk=f"IE{1000000 + len(pop.persons) + 1}",
                msis_id="" if snap_only else new_msis(),
                first=rng.choice(FIRST_NAMES), last=last, dob=dob,
                sex=rng.choices(["F", "M", "U"], weights=[49, 49, 2])[0],
                household_id=hid, county=county, kind="snap_only" if snap_only else "medicaid",
                addr1=addr1, city=city, zip9=zip9, newborn=newborn, elderly=elderly, base_dob=dob,
            )
            pop.add_person(p)
            if snap_only:
                _snap_row(pop, rng, p)
                p.pattern = "snap_only"
                continue
            _medicaid_spans(pop, rng, p)
            if hh_snap:
                _snap_row(pop, rng, p)
                p.has_snap = True
            if hh_mco and any(r.segment_type == "FULL" for r in pop.medicaid_rows(p.psk)):
                p.mco = hh_mco
    _providers_and_mcos(pop, rng)
    for p in pop.persons:
        if p.mco:
            p.plan_id = rng.choice(pop.mcos[p.mco].plans)
    return pop


def _snap_row(pop, rng, p):
    lo = max(p.dob, dt.date(2018, 1, 1))
    s = rand_date(rng, lo, pop.start - days(20))
    rec = min(s + days(rng.randint(0, 25)), pop.end - days(2))
    pop.add_row(p, "SNAP", "SN", "FULL", s, OPEN_END, rand_ts(rng, rec))


def _medicaid_spans(pop: Population, rng, p: Person):
    start, end = pop.start, pop.end
    age = _age_on(p.dob, start)
    earliest = max(p.dob, dt.date(2016, 1, 1))

    def rec_after(d, lo, hi):
        x = min(d + days(rng.randint(lo, hi)), end - days(2))
        return rand_ts(rng, x)

    if p.newborn:
        p.pattern, p.full_group = "newborn", "06"
        pop.add_row(p, "MEDICAID", "06", "FULL", p.dob, OPEN_END, rec_after(p.dob, 2, 30))
        return
    if p.elderly and rng.random() < 0.6:
        p.pattern = "partial_dual"
        s = rand_date(rng, max(earliest, add_years(p.dob, 65)), start - days(30))
        pop.add_row(p, "MEDICAID", rng.choice(["23", "24"]), "PARTIAL", s, OPEN_END, rec_after(s, 0, 30))
        return

    if p.elderly:
        group = "11"
    elif age <= 18:
        group = "07"
    elif p.sex == "F" and age <= 40 and rng.random() < 0.15:
        group = "05"
    else:
        group = rng.choice(["72", "72", "01"])
    p.full_group = group

    pat = rng.choices(["stable", "churn", "term_in_run", "new_in_run", "reinstate", "renewal_adjacent"],
                      weights=[52, 18, 10, 8, 4, 8])[0]
    need = {"churn": 200, "term_in_run": 60, "reinstate": 90, "renewal_adjacent": 400}
    if pat in need and earliest > start - days(need[pat]):
        pat = "stable"
    p.pattern = pat

    if pat == "stable":
        s = rand_date(rng, earliest, start - days(45)) if earliest <= start - days(45) else earliest
        pop.add_row(p, "MEDICAID", group, "FULL", s, OPEN_END, rec_after(s, 0, 30))
    elif pat == "churn":
        s1 = rand_date(rng, earliest, start - days(200))
        e1 = rand_date(rng, max(s1 + days(60), start - days(150)), start + days(50))
        s2 = e1 + days(rng.randint(20, 60) + 1)
        cr_date = None
        if e1 + days(25) < start:
            pop.add_row(p, "MEDICAID", group, "FULL", s1, e1, rand_ts(rng, e1 + days(rng.randint(1, 20))))
            last_rec = e1 + days(20)
        else:
            pop.add_row(p, "MEDICAID", group, "FULL", s1, OPEN_END, rec_after(s1, 0, 30))
            cr_date = max(e1 + days(rng.randint(1, 20)), start)
            if cr_date >= end - days(2):
                p.pattern = "churn_unrecorded"
                return
            pop.add_row(p, "MEDICAID", group, "FULL", s1, e1, rand_ts(rng, cr_date), "correction")
            last_rec = cr_date
        if s2 <= end - days(25):
            r2 = max(s2 + days(rng.randint(0, 25)), last_rec + days(1))
            if r2 <= end - days(2):
                pop.add_row(p, "MEDICAID", group, "FULL", s2, OPEN_END, rand_ts(rng, r2))
    elif pat == "term_in_run":
        s = rand_date(rng, earliest, start - days(60))
        pop.add_row(p, "MEDICAID", group, "FULL", s, OPEN_END, rec_after(s, 0, 30))
        t = rand_date(rng, start + days(10), end - days(25))
        cr = min(t + days(rng.randint(3, 20)), end - days(2))
        pop.add_row(p, "MEDICAID", group, "FULL", s, t, rand_ts(rng, cr), "correction")
    elif pat == "new_in_run":
        s = rand_date(rng, max(start + days(5), earliest), end - days(25))
        pop.add_row(p, "MEDICAID", group, "FULL", s, OPEN_END, rec_after(s, 0, 30))
    elif pat == "reinstate":
        s = rand_date(rng, earliest, start - days(90))
        e = rand_date(rng, start - days(20), start + days(15))
        rec1 = rand_date(rng, max(s, start - days(60)), start - days(3))
        pop.add_row(p, "MEDICAID", group, "FULL", s, e, rand_ts(rng, rec1))
        cr = min(max(e, start) + days(rng.randint(5, 30)), end - days(2))
        pop.add_row(p, "MEDICAID", group, "FULL", s, OPEN_END, rand_ts(rng, cr), "correction")
    elif pat == "renewal_adjacent":
        s1 = rand_date(rng, earliest, start - days(400))
        e1 = rand_date(rng, s1 + days(180), start - days(30))
        pop.add_row(p, "MEDICAID", group, "FULL", s1, e1,
                    rand_ts(rng, rand_date(rng, max(s1, e1 - days(30)), e1 - days(1))))
        pop.add_row(p, "MEDICAID", group, "FULL", e1 + days(1), OPEN_END,
                    rand_ts(rng, rand_date(rng, e1 - days(5), e1 + days(10))))

    # HCBS waiver segment overlapping FULL (legitimate cross-type overlap)
    if pat in ("stable", "renewal_adjacent") and rng.random() < 0.12:
        a = max(r.eff_start for r in pop.medicaid_rows(p.psk) if r.segment_type == "FULL")
        w = rand_date(rng, max(a, start - days(500)), end - days(30))
        w_end = OPEN_END if rng.random() < 0.6 else w + days(364)
        pop.add_row(p, "MEDICAID", group, "WAIVER", w, w_end, rec_after(w, 0, 20))


def _providers_and_mcos(pop: Population, rng):
    used_npi, used_names = set(), set()
    for i in range(40):
        while True:
            name = f"{rng.choice(ORG_WORDS)} {rng.choice(ORG_TYPES)}"
            if name not in used_names:
                used_names.add(name)
                break
        while True:
            npi = make_npi(str(rng.choice([1, 2])) + f"{rng.randint(0, 99_999_999):08d}")
            if npi not in used_npi:
                used_npi.add(npi)
                break
        pop.providers.append(Provider(
            idx=i, name=name, npi=npi, atypical=False, state_id=f"ZZP{1000 + i}",
            ein=f"00{rng.randint(1_000_000, 9_999_999)}", addr1=f"{rng.randint(1, 999)} {rng.choice(STREETS)} PLZ",
            city=rng.choice(CITIES), zip9=f"000{rng.randint(10, 99)}{rng.randint(1000, 9999)}",
            is_chc="COMMUNITY HEALTH" in name))
    atyp = [("ZZ RURAL RIDES TRANSPORT", "T2003"), ("ZZ HOMEHELP PERSONAL CARE", "T1019"),
            ("ZZ MEALS AND MORE", "S5170")]
    for j, (name, svc) in enumerate(atyp):
        pop.providers.append(Provider(
            idx=40 + j, name=name, npi=None, atypical=True, state_id=f"ZZAP{9001 + j}",
            ein=f"00{rng.randint(1_000_000, 9_999_999)}", addr1=f"{rng.randint(1, 999)} DEPOT RD",
            city=rng.choice(CITIES), zip9=f"000{rng.randint(10, 99)}{rng.randint(1000, 9999)}", service=svc))
    billing = pop.providers[:40]
    atypicals = pop.providers[40:]
    pop.mcos["A"] = MCO("A", "ZZ-MCO-A", "ZZMCOA", "ZZ MCO A HEALTH PLAN", ["ZZMCOA01", "ZZMCOA02"],
                        billing[0:28] + atypicals)
    pop.mcos["B"] = MCO("B", "ZZ-MCO-B", "ZZMCOB", "ZZ MCO B COMMUNITY CARE", ["ZZMCOB01", "ZZMCOB02"],
                        billing[12:40] + atypicals)
    for code in Z9_CODES:
        pop.fees[code] = money(Decimal(rng.randint(4500, 32000)) / 100)
    for code, (_, fee) in HCPCS_MEDICAL.items():
        pop.fees[code] = Decimal(fee)
    for code, (_, fee, _) in ATYPICAL_SERVICES.items():
        pop.fees[code] = Decimal(fee)
