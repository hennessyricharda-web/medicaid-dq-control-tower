"""Synthetic inbound-file generator for the Medicaid DQ Agent W1 slice (state ZZ).

    python -m synthetic.generate --run-id w1 --mode seeded --seed 42 --start 20260101 --months 3
    python -m synthetic.generate --provider

Writes synthetic/out/<run_id>/ per docs/W1_SPINE.md §1 (with the deviations in synthetic/DEVIATIONS.md),
plus ledger.json / ledger.sha256 / negative_controls.json, and appends the ledger hash to
synthetic/LEDGER_COMMITMENTS.log. Stdlib only; deterministic for a given (seed, start, months, mode).

`--provider` (K5 / F-13, PFP P2-6, "a new source by YAML"): writes synthetic/out/w1_provider/ -- the SAME
seeded w1 run (seed 42, start 20260101, 3 months: byte-identical IE/834/837P/ack files to synthetic/out/w1)
plus one new `provider_enrollment_<yyyymmdd>.csv` file. Routing-driven discovery picks that CSV up as
UNROUTED_FILE before the provider feed is onboarded by configuration, and ingests it after.
"""
from __future__ import annotations

import argparse
import copy
import csv
import datetime as dt
import hashlib
import io
import json
import random
import shutil
import sys
from pathlib import Path

from .edi import Interchange, Group, build_834_st, build_837_st, serialize
from .encounters import Elig, Slot, generate_encounters
from .population import build_population
from .seeding import PARTC_CLASSES, TIMELY_FILING_DAYS, W1_CLASSES, PartCSeeder, Seeder
from .util import OPEN_END, add_months, days, fmt_money, parse_ymd, ymd

PKG_DIR = Path(__file__).resolve().parent
DEFAULT_OUT_ROOT = PKG_DIR / "out"
DEFAULT_COMMIT_LOG = PKG_DIR / "LEDGER_COMMITMENTS.log"
BROKER_ID, BROKER_PARTNER = "ZZBROKER", "ZZ-BROKER"
CLASS_SETS = {"w1": W1_CLASSES, "w1+partc": W1_CLASSES + PARTC_CLASSES}

# K5 / F-13: the canonical w1 generation parameters, reused for the --provider run so its IE/834/837P/ack files
# are byte-identical to synthetic/out/w1 (same seed) -- only the new provider_enrollment CSV is added.
PROVIDER_RUN = {"run_id": "w1_provider", "mode": "seeded", "seed": 42, "start": "20260101", "months": 3}
PROVIDER_TYPES = ["FQHC", "PCP", "SPECIALIST", "HOSPITAL_OUTPT", "BEHAVIORAL_HEALTH"]
PROVIDER_STATUS = ["ACTIVE", "ACTIVE", "ACTIVE", "ACTIVE", "TERMED"]
PROVIDER_ORG_PREFIX = ["ZZ Community", "Northfield", "Riverbend", "Lakeside", "Prairie View", "Cedar Grove",
                      "Union County", "Mapleton", "Harborview", "Sunrise Valley"]
PROVIDER_ORG_SUFFIX = ["Health Partners", "Family Medicine", "Pediatric Group", "Behavioral Health",
                       "Primary Care Clinic", "Community Clinic", "Rural Health Center", "Medical Associates"]
PROVIDER_COLS = ["provider_id", "npi", "legal_name", "provider_type", "enrollment_date", "effective_date", "status"]


def _gen_npi(rng: random.Random) -> str:
    """A Luhn-valid (dqagent.engine.evaluate.npi_valid) 10-digit NPI that is NOT a real assigned NPI: 9 random
    digits plus whichever check digit the formula requires (there are only 10 candidates, so brute force is
    cheap and avoids re-deriving the Luhn arithmetic a second time here)."""
    from dqagent.engine.evaluate import npi_valid
    base = "".join(str(rng.randint(0, 9)) for _ in range(9))
    for d in range(10):
        cand = base + str(d)
        if npi_valid(cand):
            return cand
    raise AssertionError("unreachable: one of the 10 possible check digits always satisfies the Luhn formula")


def build_provider_enrollment(rng: random.Random, n: int, as_of: dt.date) -> list[dict]:
    """n invented provider-enrollment rows (fictional names and organizations, Luhn-valid but not real NPIs)."""
    rows = []
    for i in range(n):
        org = f"{rng.choice(PROVIDER_ORG_PREFIX)} {rng.choice(PROVIDER_ORG_SUFFIX)}"
        enrolled = as_of - days(rng.randint(120, 900))
        effective = enrolled + days(rng.randint(0, 30))
        rows.append({"provider_id": f"ZZPRV{i + 1:05d}", "npi": _gen_npi(rng), "legal_name": org,
                    "provider_type": rng.choice(PROVIDER_TYPES), "enrollment_date": ymd(enrolled),
                    "effective_date": ymd(effective), "status": rng.choice(PROVIDER_STATUS)})
    return rows


def write_provider_csv(out_dir: Path, seed, as_of: dt.date, n: int = 12) -> Path:
    """Writes provider_enrollment_<yyyymmdd>.csv (header row = bronze column names) into an EXISTING out_dir,
    beside whatever w1 files already live there (additive; never regenerates them)."""
    rng = random.Random(f"{seed}:provider")
    rows = build_provider_enrollment(rng, n, as_of)
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=PROVIDER_COLS, lineterminator="\n")
    w.writeheader()
    w.writerows(rows)
    path = out_dir / f"provider_enrollment_{ymd(as_of)}.csv"
    path.write_text(buf.getvalue(), encoding="ascii", newline="")
    return path


def _hhmm(rng) -> str:
    return f"{rng.randint(0, 23):02d}{rng.randint(0, 59):02d}"


# ----------------------------------------------------------------------------------------- 834 members
def _member_rec(p, ins03, ins04, row, plan=None, start=None, end=None):
    return {"ins03": ins03, "ins04": ins04, "member_id": p.msis_id, "first": p.first, "last": p.last,
            "dob": p.dob, "sex": p.sex, "plan": plan or p.plan_id,
            "start": start or row.eff_start, "end": row.eff_end if end is None else end}


def build_834_members(pop, rl, f0, c_date):
    full, change = [], []
    for p in pop.persons:
        if not p.mco:
            continue
        vbk = pop.versions_by_key(p.psk, "FULL", include_defect=False)
        prior_key = False
        for key in sorted(vbk, key=lambda k: k[3]):
            vers = vbk[key]
            asof = [v for v in vers if v.recorded_at.date() < f0]
            if asof and asof[-1].eff_end >= f0:
                full.append(_member_rec(p, "030", "AI", asof[-1]))
            window = [v for v in vers if f0 <= v.recorded_at.date() < c_date]
            if window:
                latest = window[-1]
                if not asof:
                    change.append(_member_rec(p, "021", "41" if prior_key else "28", latest))
                elif latest.eff_end != OPEN_END:
                    change.append(_member_rec(p, "024", "07", latest))
                else:
                    change.append(_member_rec(p, "025", "41", latest))
            prior_key = True
    full.sort(key=lambda m: m["member_id"])
    # plan changes within the same MCO (INS03/HD01 = 001)
    active = [m for m in full if m["end"] == OPEN_END]
    for m in rl.sample(active, min(3, len(active))):
        p = next(x for x in pop.persons if x.msis_id == m["member_id"])
        other = [pl for pl in pop.mcos[p.mco].plans if pl != m["plan"]][0]
        change.append(dict(m, ins03="001", ins04="AI", plan=other,
                           start=f0 + days(rl.randint(5, max(6, (c_date - f0).days - 5))), end=OPEN_END))
    rl.shuffle(change)
    return full, change


# ----------------------------------------------------------------------------------------- IE extract
def layout_ie(pop, rl):
    start, end = pop.start, pop.end
    dates = [start]
    k = 1
    while True:
        nd = start + days(7 * k + (rl.randint(1, 3) if rl.random() < 0.25 else 0))  # occasional late file
        if nd >= end:
            break
        dates.append(nd)
        k += 1
    dates.append(end)
    files = []
    for i, d in enumerate(dates):
        lo = dates[i - 1] if i else None
        rows = [r for r in pop.rows if (r.recorded_at.date() < d and (lo is None or r.recorded_at.date() >= lo))]
        rows.sort(key=lambda r: (pop.by_psk[r.psk].household_id, r.psk, r.program, r.segment_type,
                                 r.eff_start, r.recorded_at))
        files.append({"name": f"ie_extract_{ymd(d)}.txt", "date": d, "seq": i + 1, "rows": rows,
                      "trailer_delta": 0, "line_of": {}})
    return files


def render_ie(pop, f) -> str:
    out = [f"HDR|ZZ-IE|{ymd(f['date'])}|{f['seq']}"]
    for r in f["rows"]:
        p = pop.by_psk[r.psk]
        msis = p.msis_id if p.kind == "medicaid" else ""
        f["line_of"][r.row_id] = len(out) + 1
        out.append("|".join(["DTL", p.psk, msis, p.first, p.last, ymd(p.dob), getattr(p, "ie_sex", None) or p.sex, r.program, r.elig_group,
                             r.segment_type, ymd(r.eff_start), ymd(r.eff_end), p.county, p.household_id,
                             r.recorded_at.strftime("%Y%m%d%H%M%S")]))
    out.append(f"TRL|{len(f['rows']) + f['trailer_delta']}")
    return "\n".join(out) + "\n"


# ----------------------------------------------------------------------------------------- main build
def generate(run_id: str, mode: str, seed: int, start: str, months: int, out_root=None, commit_log=None,
             persons: int = 300, class_set: str = "w1", provider: bool = False) -> dict:
    if mode not in ("realistic", "seeded"):
        raise ValueError("mode must be realistic|seeded")
    if class_set not in CLASS_SETS:
        raise ValueError(f"class_set must be one of {sorted(CLASS_SETS)}")
    out_root = Path(out_root) if out_root else DEFAULT_OUT_ROOT
    commit_log = Path(commit_log) if commit_log else DEFAULT_COMMIT_LOG
    start_d = parse_ymd(start)
    end_d = add_months(start_d, months)
    rb = random.Random(f"{seed}:base")
    rl = random.Random(f"{seed}:layout")
    rd = random.Random(f"{seed}:defects")
    seeded = mode == "seeded"

    pop = build_population(rb, start_d, end_d, persons)
    seeder = Seeder(pop, rd) if seeded else None
    partc = seeded and class_set == "w1+partc"

    f0 = start_d
    c_date = start_d + days((end_d - start_d).days // 2 + rl.randint(-6, 6))
    slots = []
    for m in sorted(pop.mcos):
        for k in range(months):
            fd = add_months(start_d, k) + days(rl.randint(14, 24))
            if rl.random() < 0.2:
                fd += days(rl.randint(3, 6))          # late MCO file
            slots.append(Slot(m, k, min(fd, end_d - days(1))))

    if seeded:
        seeder.seed_ie_data()
    elig = Elig(pop, f0, c_date)
    encs, controls = generate_encounters(pop, rb, elig, slots)
    if seeded:
        seeder.seed_encounters(encs)
    if partc:   # own RNG stream: every W1 seed lands exactly where it does in a w1-only run
        pc = PartCSeeder(seeder, random.Random(f"{seed}:partc"), elig)
        pc.seed_encounters(encs, controls)
        pc.seed_ie_sex()

    full_m, change_m = build_834_members(pop, rl, f0, c_date)
    if seeded:
        seeder.seed_834(change_m, c_date)

    # ---- interchanges
    ics: list[Interchange] = []
    b_isa = rl.randint(100000, 199999)
    b_gs = rl.randint(50, 500)
    for n, (d, members, bgn08) in enumerate([(f0, full_m, "4"), (c_date, change_m, "2")]):
        t = _hhmm(rl)
        ic = Interchange("834", BROKER_PARTNER, BROKER_ID, d, t, f"{b_isa + n:09d}")
        g = Group(str(b_gs + n), "BE", BROKER_ID, ic.receiver, "005010X220A1", d, t)
        g.sts.append(build_834_st("0001", f"ZZB{ymd(d)}{n + 1:02d}", d, t, bgn08, members))
        ic.groups.append(g)
        ics.append(ic)
    a_isa = rl.randint(2000, 8000)
    isa_base = {"A": a_isa, "B": a_isa + rl.choice([1, 2])}   # cross-sender ISA13 overlap is legitimate
    two_group_slots = set(rl.sample(range(len(slots)), 2))
    for m in sorted(pop.mcos):
        mco = pop.mcos[m]
        gs_counter = rl.randint(100, 9000)
        for si, s in enumerate([s for s in slots if s.mco == m]):
            fencs = sorted([e for e in encs if e.mco == m and e.slot == s.idx], key=lambda e: e.eid)
            rl.shuffle(fencs)
            n = len(fencs)
            n_st = max(1, min(n, round(n / rl.randint(4, 7))))
            cuts = sorted(rl.sample(range(1, n), n_st - 1))
            chunks = [fencs[a:b] for a, b in zip([0] + cuts, cuts + [n])]
            t = _hhmm(rl)
            ic = Interchange("837P", mco.partner, mco.edi_id, s.fd, t, f"{isa_base[m] + si:09d}")
            gidx = slots.index(s)
            split = rl.randint(1, n_st - 1) if (gidx in two_group_slots and n_st >= 2) else n_st
            for part in ([chunks[:split], chunks[split:]] if split < n_st else [chunks]):
                g = Group(str(gs_counter), "HC", mco.edi_id, ic.receiver, "005010X222A1", s.fd, t)
                gs_counter += 1
                for i, ch in enumerate(part, start=1):
                    st02 = f"{i:04d}"
                    g.sts.append(build_837_st(st02, mco, s.fd, t, f"{mco.edi_id}{ymd(s.fd)}{g.gs06}{st02}", ch))
                ic.groups.append(g)
            ics.append(ic)
    for i, ic in enumerate(ics):
        ic.order = i
        ic.arrival = ic.date

    resend_src = seeder.seed_envelope(ics) if seeded else None
    all_ics = list(ics)
    resend = None
    if resend_src is not None:
        lag = next(p["resend_lag"] for c, p in seeder.pend if c == "DUP_INTERCHANGE" and p["ic"] is None)
        resend = copy.deepcopy(resend_src)
        resend.role = "resend"
        resend.arrival = resend_src.arrival + days(lag)
        resend.order = len(all_ics)
        all_ics.append(resend)
        for c, p in seeder.pend:
            if c == "DUP_INTERCHANGE" and p["ic"] is None:
                p["ic"] = resend

    # ---- filenames (n = arrival sequence per sender)
    by_sender = {}
    for ic in all_ics:
        by_sender.setdefault(ic.sender, []).append(ic)
    for snd, lst in by_sender.items():
        lst.sort(key=lambda x: (x.arrival, x.order))
        for n, ic in enumerate(lst, start=1):
            ic.seq_n = n
            prefix = "834" if ic.kind == "834" else "837P"
            ic.filename = f"{prefix}_{snd}_{ymd(ic.arrival)}_{n}.x12"
            ic.ack_filename = f"ack_{snd}_{ymd(ic.arrival)}_{n}.csv"

    # ---- IE layout
    ie_files = layout_ie(pop, rl)
    if seeded:
        seeder.seed_ie_trailer(ie_files)

    # ---- write
    out_dir = out_root / run_id
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)
    for f in ie_files:
        (out_dir / f["name"]).write_text(render_ie(pop, f), encoding="ascii", newline="")
    for ic in all_ics:
        (out_dir / ic.filename).write_text(serialize(ic), encoding="ascii", newline="")
        buf = io.StringIO()
        w = csv.writer(buf, lineterminator="\n")
        w.writerow(["isa13", "gs06", "st02", "transaction_set", "status", "error_code"])
        for g in ic.groups:
            for st in g.sts:
                w.writerow([ic.isa13, g.gs06, st.st02, st.tset, st.ack_status, st.ack_code])
        (out_dir / ic.ack_filename).write_text(buf.getvalue(), encoding="ascii", newline="")
    provider_csv = write_provider_csv(out_dir, seed, c_date) if provider else None

    # ---- ledger + negative controls
    ledger = finalize_ledger(pop, seeder, all_ics, resend, ie_files, rd) if seeded else []
    neg = negative_controls(pop, encs, controls, all_ics, ie_files, ledger)
    ledger_bytes = (json.dumps(ledger, indent=2, sort_keys=True) + "\n").encode("ascii")
    neg_bytes = (json.dumps(neg, indent=2, sort_keys=True) + "\n").encode("ascii")
    (out_dir / "ledger.json").write_bytes(ledger_bytes)
    h = hashlib.sha256(ledger_bytes).hexdigest()
    (out_dir / "ledger.sha256").write_text(f"{h}  ledger.json\n", encoding="ascii", newline="")
    (out_dir / "negative_controls.json").write_bytes(neg_bytes)
    hn = hashlib.sha256(neg_bytes).hexdigest()
    ts = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    commit_log.parent.mkdir(parents=True, exist_ok=True)
    with commit_log.open("a", encoding="ascii") as fh:
        fh.write(f"{ts}\trun_id={run_id}\tmode={mode}\tseed={seed}\tstart={start}\tmonths={months}"
                 f"\tledger_sha256={h}\tnegative_controls_sha256={hn}\tentries={len(ledger)}"
                 + (f"\tclass_set={class_set}" if class_set != "w1" else "") + "\n")
    return {"out_dir": out_dir, "ledger_sha256": h, "negative_controls_sha256": hn, "pop": pop,
            "encounters": encs, "ledger_entries": len(ledger), "files": sorted(p.name for p in out_dir.iterdir()),
            "provider_csv": provider_csv.name if provider_csv else None}


# ----------------------------------------------------------------------------------------- ledger
def _x12_loc(ic, g=None, st=None, **kw):
    d = {"isa13": ic.isa13}
    if g is not None:
        d["gs06"] = g.gs06
    if st is not None:
        d["st02"] = st.st02
    d.update(kw)
    return d


def _index_encounters(ics):
    idx = {}
    for ic in ics:
        if ic.kind != "837P" or ic.role != "regular":
            continue
        for g in ic.groups:
            for st in g.sts:
                for i, s in enumerate(st.segs):
                    if s[0] == "CLM":
                        idx[s[1]] = (ic, g, st, st.meta[i]["hl_id"])
    return idx


def _hl_encounters(st, hl_id, level):
    out = []
    for i, s in enumerate(st.segs):
        m = st.meta[i]
        if s[0] == "CLM" and ((level == "22" and m.get("hl_id") == hl_id)
                              or (level == "20" and m.get("hl_parent") == hl_id)):
            out.append(s[1])
    return out


def finalize_ledger(pop, seeder, all_ics, resend, ie_files, rd):
    eidx = _index_encounters(all_ics)
    line_of = {}
    for f in ie_files:
        for rid, ln in f["line_of"].items():
            line_of[rid] = (f["name"], ln)
    prim = []

    def add(cls, file, loc, effect, ref=None):
        prim.append({"defect_class": cls, "file": file, "locator": loc, "expected_effect": effect, "_ref": ref})

    for cls, p in seeder.pend:
        if cls == "ISA_WIDTH":
            ic = p["ic"]
            from .edi import isa_fields
            width = len("*".join(isa_fields(ic))) + 1
            add(cls, ic.filename, _x12_loc(ic, isa06=ic.sender, isa_length=width, variant=p["variant"]),
                {"check": "ISA must be exactly 106 characters (fixed width, delimiters at fixed offsets)",
                 "observed": f"ISA is {width} characters", "disposition": "interchange-level envelope error",
                 "translator_ack": "accepted (ZZ translator tokenises by delimiter and does not enforce ISA width)"})
        elif cls == "DUP_INTERCHANGE":
            ic, src = p["ic"], p["dup_of"]
            add(cls, ic.filename, _x12_loc(ic, isa06=ic.sender, isa08=ic.receiver, variant=p["variant"],
                                           duplicate_of_file=src.filename),
                {"check": "duplicate (ISA05/06, ISA07/08, ISA13) within the lookback window",
                 "observed": f"ISA13 {ic.isa13} from {ic.sender} already received in {src.filename}",
                 "disposition": "hold/reject the later interchange; its transactions must not be double counted",
                 "translator_ack": "accepted (ZZ translator has no duplicate-interchange check)"}, ref=p)
        elif cls == "GE_COUNT_MISMATCH":
            ic, g = p["ic"], p["g"]
            add(cls, ic.filename, _x12_loc(ic, g, ge01_reported=len(g.sts) + g.ge_delta,
                                           transaction_sets_actual=len(g.sts)),
                {"check": "GE01 = number of ST in the group",
                 "observed": f"GE01={len(g.sts) + g.ge_delta}, actual={len(g.sts)}",
                 "disposition": "functional-group control total failure",
                 "translator_ack": "A with group error noted (999 AK901=E, AK905=5) on every ST in the group"},
                ref=p)
        elif cls == "SE_COUNT_MISMATCH":
            ic, g, st = p["ic"], p["g"], p["st"]
            actual = len(st.segs) + 1
            add(cls, ic.filename, _x12_loc(ic, g, st, se01_reported=actual + st.se_delta, segments_actual=actual),
                {"check": "SE01 = segments ST..SE inclusive",
                 "observed": f"SE01={actual + st.se_delta}, actual={actual}",
                 "disposition": "transaction set rejected", "translator_ack": "R / IK502:4"}, ref=p)
        elif cls == "TRANSLATOR_REJECT":
            ic, g, st = p["ic"], p["g"], p["st"]
            add(cls, ic.filename, _x12_loc(ic, g, st, ack_file=ic.ack_filename, error_code=p["code"]),
                {"check": "translator 999 result: received = accepted + rejected",
                 "observed": f"ack status R ({p['code']}) though the ST passes W1 structural checks",
                 "disposition": "ST excluded from accepted counts; ABC TRANSLATOR->FILE rejected += 1"}, ref=p)
        elif cls == "HL_PARENT_BROKEN":
            ic, g, st = p["ic"], p["g"], p["st"]
            add(cls, ic.filename, _x12_loc(ic, g, st, hl_id=p["hl_id"], hl02_reported=p["new_parent"],
                                           variant=p["variant"],
                                           encounter_ids=_hl_encounters(st, p["hl_id"], "22")),
                {"check": "HL02 of a 22-level HL must reference a prior 20-level HL",
                 "observed": f"HL*{p['hl_id']}*{p['new_parent']}*22 ({p['variant']})",
                 "disposition": "claims under this subscriber HL cannot be attributed to a billing provider"})
        elif cls == "NPI_LUHN":
            ic, g, st = p["ic"], p["g"], p["st"]
            add(cls, ic.filename, _x12_loc(ic, g, st, hl_id=p["hl_id"], npi=st.segs[p["seg"]][9],
                                           variant=p["variant"],
                                           encounter_ids=_hl_encounters(st, p["hl_id"], "20")),
                {"check": "NM109 (NM108=XX) passes Luhn over prefix 80840",
                 "observed": f"billing NPI {st.segs[p['seg']][9]} fails Luhn ({p['variant']})",
                 "disposition": "encounters under this billing provider HL fail provider validation"})
        elif cls in ("CLM02_LINE_SUM", "FREQ7_NO_F8", "SVC_OUTSIDE_ELIG", "MEMBER_UNKNOWN"):
            e = p["enc"]
            ic, g, st, hl = eidx[e.eid]
            if cls == "CLM02_LINE_SUM":
                loc = _x12_loc(ic, g, st, encounter_id=e.eid, clm02=fmt_money(e.clm02),
                               line_sum=fmt_money(e.line_sum), variant=p["variant"])
                eff = {"check": "CLM02 = sum of SV102 for the claim",
                       "observed": f"CLM02 {fmt_money(e.clm02)} vs line sum {fmt_money(e.line_sum)}",
                       "disposition": "claim fails financial balancing"}
            elif cls == "FREQ7_NO_F8":
                loc = _x12_loc(ic, g, st, encounter_id=e.eid, freq=e.freq, original_encounter_id=e.orig_ref)
                eff = {"check": "CLM05-3 in {7,8} requires REF*F8",
                       "observed": f"frequency {e.freq} without REF*F8",
                       "disposition": "replacement/void cannot be linked to its chain"}
            elif cls == "SVC_OUTSIDE_ELIG":
                loc = _x12_loc(ic, g, st, encounter_id=e.eid, line_no=p["line_no"], svc_date=ymd(p["svc"]),
                               member_id=e.member_id, person_source_key=e.psk, variant=p["variant"])
                eff = {"check": "line service date within a MEDICAID eligibility span of the member",
                       "observed": f"DTP*472 {ymd(p['svc'])} not covered by any MEDICAID segment",
                       "disposition": "encounter line outside eligibility"}
            else:
                loc = _x12_loc(ic, g, st, encounter_id=e.eid, hl_id=hl, member_id=e.member_id,
                               variant=p["variant"])
                eff = {"check": "NM109 member id (MI) exists in the IE MEDICAID population",
                       "observed": f"member id {e.member_id} unknown ({p['variant']})",
                       "disposition": "encounter cannot be attributed to a member; SVC_OUTSIDE_ELIG not applicable"}
            add(cls, ic.filename, loc, eff)
        elif cls == "INS_TERM_UNKNOWN_MEMBER":
            m = p["member"]
            ic = next(x for x in all_ics if x.kind == "834" and x.groups[0].sts[0].segs[1][8] == "2")
            g, st = ic.groups[0], ic.groups[0].sts[0]
            ins_i = next(i for i, s in enumerate(st.segs) if s[0] == "INS" and st.meta[i].get("member_id")
                         == m["member_id"])
            add(cls, ic.filename, _x12_loc(ic, g, st, member_id=m["member_id"],
                                           ins_ordinal=st.meta[ins_i]["ins_ordinal"], st_seg_pos=ins_i + 1,
                                           variant=p["variant"]),
                {"check": "INS03=024 termination must reference a member known to IE",
                 "observed": f"term for unknown member id {m['member_id']}",
                 "disposition": "834 term cannot be applied"})
        elif cls == "DOB_AFTER_ELIG_START":
            per = p["person"]
            rows = [r for r in pop.medicaid_rows(per.psk) if per.dob > r.eff_start]
            add(cls, line_of[rows[0].row_id][0], {
                "person_source_key": per.psk, "dob": ymd(per.dob), "variant": p["variant"],
                "rows": [{"file": line_of[r.row_id][0], "line_no": line_of[r.row_id][1],
                          "segment_type": r.segment_type, "eff_start": ymd(r.eff_start)} for r in rows]},
                {"check": "dob <= eff_start for MEDICAID segments",
                 "observed": f"dob {ymd(per.dob)} after eff_start on {len(rows)} row(s)",
                 "disposition": "eligibility rows fail demographic consistency"})
        elif cls == "ELIG_OVERLAP_SAME_TYPE":
            per, new, ex = p["person"], p["new"], p["existing"]
            add(cls, line_of[new.row_id][0], {
                "person_source_key": per.psk, "segment_type": new.segment_type, "variant": p["variant"],
                "rows": [{"file": line_of[r.row_id][0], "line_no": line_of[r.row_id][1],
                          "eff_start": ymd(r.eff_start), "eff_end": ymd(r.eff_end)} for r in (ex, new)]},
                {"check": "no two current MEDICAID segments of the same person and segment_type overlap",
                 "observed": f"{ymd(ex.eff_start)}-{ymd(ex.eff_end)} overlaps {ymd(new.eff_start)}-{ymd(new.eff_end)}",
                 "disposition": "eligibility span conflict"})
        elif cls in PARTC_CLASSES and cls != "ELIG_BAD_SEX":
            e = p["enc"]
            ic, g, st, hl = eidx[e.eid]
            land = {"st_ack": st.ack_status}
            if st.ack_status == "R":
                land["landing"] = "ST rejected by translator; encounter is filtered before bronze"
            if cls == "INVALID_POS":
                loc = _x12_loc(ic, g, st, encounter_id=e.eid, pos=e.pos, variant=p["variant"])
                eff = {"check": "CLM05-1 place of service is a 2-digit CMS POS code",
                       "observed": f"CLM05-1 '{e.pos}'", "disposition": "encounter fails field validity"}
            elif cls == "INVALID_DX_SHAPE":
                loc = _x12_loc(ic, g, st, encounter_id=e.eid, dx1=e.dx[0], variant=p["variant"])
                eff = {"check": "HI ABK principal dx is ICD-10-CM shaped as sent in X12 "
                                "(letter, digit, alnum, 0-4 alnum; no decimal point)",
                       "observed": f"HI ABK:{e.dx[0]}", "disposition": "encounter fails field validity"}
            elif cls == "INVALID_FREQ":
                loc = _x12_loc(ic, g, st, encounter_id=e.eid, freq=e.freq, variant=p["variant"])
                eff = {"check": "CLM05-3 in {1,7,8} for 837P",
                       "observed": f"CLM05-3 '{e.freq}'", "disposition": "encounter fails field validity"}
            elif cls == "MISSING_FREQ":
                loc = _x12_loc(ic, g, st, encounter_id=e.eid, freq="", variant=p["variant"])
                eff = {"check": "CLM05-3 present (required by 005010X222A1)",
                       "observed": "CLM05 has no third component",
                       "disposition": "lands with freq_code NULL (mapper does not reject); field-validity failure",
                       "translator_ack": "accepted (ZZ translator is syntax-only, D9)"}
            elif cls == "MISSING_ADJUD_DATE":
                loc = _x12_loc(ic, g, st, encounter_id=e.eid, line_nos=list(range(1, len(e.lines) + 1)),
                               variant=p["variant"])
                eff = {"check": "each 2430 loop carries DTP*573 line adjudication date",
                       "observed": f"no DTP*573 on any of {len(e.lines)} line(s)",
                       "disposition": "adjud_date NULL on every line; timeliness cannot be computed"}
            else:
                lines = [{"line_no": n, "svc_date": ymd(ln.svc), "adjud_date": ymd(e.adj_date),
                          "lag_days": (e.adj_date - ln.svc).days} for n, ln in enumerate(e.lines, start=1)]
                loc = _x12_loc(ic, g, st, encounter_id=e.eid, lines=lines, variant=p["variant"])
                eff = {"check": f"DTP*573 - DTP*472 <= {TIMELY_FILING_DAYS} days per line",
                       "observed": f"lag {min(x['lag_days'] for x in lines)}-{max(x['lag_days'] for x in lines)} days",
                       "disposition": "line adjudicated outside the 12-month filing window (review flag)"}
            eff.update(land)
            add(cls, ic.filename, loc, eff)
        elif cls == "ELIG_BAD_SEX":
            per = p["person"]
            rows = sorted((r for r in pop.rows if r.psk == per.psk and r.row_id in line_of),
                          key=lambda r: line_of[r.row_id])
            add(cls, line_of[rows[0].row_id][0], {
                "person_source_key": per.psk, "sex": per.ie_sex, "variant": p["variant"],
                "rows": [{"file": line_of[r.row_id][0], "line_no": line_of[r.row_id][1]} for r in rows]},
                {"check": "IE sex in {F, M, U}", "observed": f"sex '{per.ie_sex}' on {len(rows)} row(s)",
                 "disposition": "eligibility rows fail demographic field validity"})
        elif cls == "IE_TRAILER_COUNT":
            f = p["file"]
            add(cls, f["name"], {"trl_count": len(f["rows"]) + f["trailer_delta"], "detail_count": len(f["rows"])},
                {"check": "TRL count = number of DTL records",
                 "observed": f"TRL {len(f['rows']) + f['trailer_delta']} vs {len(f['rows'])} DTL",
                 "disposition": "file-level control total failure"})

    rd.shuffle(prim)
    entries = []
    for i, e in enumerate(prim, start=1):
        e["defect_id"] = f"D{i:04d}"
    # derived translator rejects caused by SE / GE defects
    derived = []
    for e in prim:
        ref = e["_ref"]
        if e["defect_class"] == "SE_COUNT_MISMATCH":
            causes = [(ref["ic"], ref["g"], ref["st"], "IK502:4")]
        else:
            continue
        for ic, g, st, code in causes:
            derived.append({"defect_class": "TRANSLATOR_REJECT", "file": ic.filename,
                            "locator": _x12_loc(ic, g, st, ack_file=ic.ack_filename, error_code=code),
                            "expected_effect": {"derived_from": e["defect_id"],
                                                "check": "translator 999 result: received = accepted + rejected",
                                                "observed": f"ack status R ({code}) caused by {e['defect_class']}",
                                                "disposition": "ST excluded from accepted counts"}})
    n = len(prim)
    for d in derived:
        n += 1
        d["defect_id"] = f"D{n:04d}"
    entries = [{k: v for k, v in e.items() if k != "_ref"} for e in prim] + derived
    # the exact re-send carries every defect of its source file
    if resend is not None:
        src_name = next(p["dup_of"].filename for c, p in seeder.pend
                        if c == "DUP_INTERCHANGE" and p["variant"] == "exact_resend")
        clones = []
        for e in entries:
            if e["file"] == src_name:
                n += 1
                c = json.loads(json.dumps(e))
                c["defect_id"] = f"D{n:04d}"
                c["file"] = resend.filename
                if "ack_file" in c["locator"]:
                    c["locator"]["ack_file"] = resend.ack_filename
                c["expected_effect"]["derived_from"] = e["defect_id"]
                c["expected_effect"]["note"] = "carried by exact re-send of " + src_name
                clones.append(c)
        entries += clones
    return entries


def negative_controls(pop, encs, controls, all_ics, ie_files, ledger):
    """Legitimate look-alikes a correct rule must NOT flag (not defects; hash-committed with the ledger)."""
    out = list(controls)
    for p in pop.persons:
        rows = pop.medicaid_rows(p.psk)
        if p.newborn and any(r.eff_start == p.dob for r in rows):
            out.append({"kind": "NEWBORN_START_EQ_DOB", "person_source_key": p.psk})
        if p.pattern == "renewal_adjacent":
            out.append({"kind": "ADJACENT_SAME_TYPE_SPANS", "person_source_key": p.psk})
        types = {r.segment_type for r in rows if r.tag != "defect"}
        if {"FULL", "WAIVER"} <= types:
            out.append({"kind": "CROSS_TYPE_OVERLAP_FULL_WAIVER", "person_source_key": p.psk})
        if any(r.tag == "correction" for r in rows):
            out.append({"kind": "RETRO_CORRECTION_SAME_KEY", "person_source_key": p.psk, "pattern": p.pattern})
        if p.kind == "snap_only":
            out.append({"kind": "SNAP_ONLY_NO_MSIS", "person_source_key": p.psk})
    seen = {}
    for ic in all_ics:
        seen.setdefault(ic.isa13, set()).add(ic.sender)
    for isa13, senders in sorted(seen.items()):
        if len(senders) > 1:
            out.append({"kind": "CROSS_SENDER_SAME_ISA13", "isa13": isa13, "senders": sorted(senders)})
    for e in encs:
        if e.provider.atypical:
            out.append({"kind": "ATYPICAL_PROVIDER_NO_NPI", "encounter_id": e.eid,
                        "provider_state_id": e.provider.state_id})
        if e.freq in ("7", "8") and e.emit_f8:
            out.append({"kind": "CHAIN_WITH_F8", "encounter_id": e.eid, "freq": e.freq, "f8": e.orig_ref})
    return sorted(out, key=lambda x: json.dumps(x, sort_keys=True))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m synthetic.generate", description=__doc__.splitlines()[0])
    ap.add_argument("--run-id", default=None)
    ap.add_argument("--mode", choices=["realistic", "seeded"], default=None)
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--start", default=None, help="YYYYMMDD")
    ap.add_argument("--months", type=int, default=3)
    ap.add_argument("--persons", type=int, default=300)
    ap.add_argument("--class-set", choices=sorted(CLASS_SETS), default="w1",
                    help="w1 (default) or w1+partc (adds the PART C field-validity/timeliness classes)")
    ap.add_argument("--out-root", default=None, help="default synthetic/out")
    ap.add_argument("--commit-log", default=None, help="default synthetic/LEDGER_COMMITMENTS.log")
    ap.add_argument("--provider", action="store_true",
                    help="also write synthetic/out/w1_provider/ (w1 plus a provider_enrollment CSV; K5/F-13 demo "
                         "source). Used bare, it runs the canonical w1 parameters for you; combined with "
                         "--run-id/--mode/--seed/--start it appends the CSV to THAT run's out_dir instead.")
    a = ap.parse_args(argv)
    if a.provider and a.run_id is None:
        p = PROVIDER_RUN
        r = generate(p["run_id"], p["mode"], p["seed"], p["start"], p["months"], a.out_root, a.commit_log,
                    a.persons, a.class_set, provider=True)
    else:
        missing = [n for n, v in (("--run-id", a.run_id), ("--mode", a.mode), ("--seed", a.seed),
                                  ("--start", a.start)) if v is None]
        if missing:
            ap.error(f"the following arguments are required: {', '.join(missing)}")
        r = generate(a.run_id, a.mode, a.seed, a.start, a.months, a.out_root, a.commit_log, a.persons, a.class_set,
                    provider=a.provider)
    print(f"out_dir={r['out_dir']}")
    print(f"files={len(r['files'])} ledger_entries={r['ledger_entries']}")
    print(f"ledger_sha256={r['ledger_sha256']}")
    if r.get("provider_csv"):
        print(f"provider_csv={r['provider_csv']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
