"""Seeded-defect injection (mode=seeded). Uses its own RNG stream so the realistic base population,
encounters and file layout are byte-identical between modes except where a defect is planted.

Every class gets >= 3 primary instances, with variants rotated so no two instances of a class
share the same shape or location pattern."""
from __future__ import annotations

import datetime as dt
from decimal import Decimal

from .population import FIRST_NAMES, LAST_NAMES
from .util import OPEN_END, days, npi_is_valid, rand_date, rand_ts

W1_CLASSES = [
    "DUP_INTERCHANGE", "SE_COUNT_MISMATCH", "GE_COUNT_MISMATCH", "ISA_WIDTH",
    "HL_PARENT_BROKEN", "CLM02_LINE_SUM", "FREQ7_NO_F8", "NPI_LUHN",
    "SVC_OUTSIDE_ELIG", "MEMBER_UNKNOWN",
    "DOB_AFTER_ELIG_START", "ELIG_OVERLAP_SAME_TYPE", "IE_TRAILER_COUNT",
    "INS_TERM_UNKNOWN_MEMBER", "TRANSLATOR_REJECT",
]


def _variants(rng, base: list[str], extra_p=0.35) -> list[str]:
    v = list(base)
    rng.shuffle(v)
    if rng.random() < extra_p:
        v.append(rng.choice(base))
    return v


class Seeder:
    def __init__(self, pop, rng):
        self.pop, self.rng = pop, rng
        self.pend: list[tuple[str, dict]] = []
        self.used_psk: set = set()
        self.used_eids: set = set()
        self.fake_ids: set = set()

    # ------------------------------------------------------------------ identifiers
    def _unknown_id(self, variant: str, real: str) -> str:
        rng, known = self.rng, self.pop.used_ids
        for _ in range(500):
            if variant == "fabricated":
                cand = f"ZZ{rng.randint(10_000_000, 99_999_999)}"
            elif variant == "transposed":
                digs = list(real[2:])
                idx = [i for i in range(len(digs) - 1) if digs[i] != digs[i + 1]]
                i = rng.choice(idx)
                digs[i], digs[i + 1] = digs[i + 1], digs[i]
                cand = "ZZ" + "".join(digs)
            else:  # one_digit
                digs = list(real[2:])
                i = rng.randrange(len(digs))
                digs[i] = rng.choice([c for c in "0123456789" if c != digs[i]])
                cand = "ZZ" + "".join(digs)
            if cand not in known and cand not in self.fake_ids:
                self.fake_ids.add(cand)
                return cand
        raise RuntimeError("could not mint unknown id")

    # ------------------------------------------------------------------ phase A: IE data
    def seed_ie_data(self):
        pop, rng, start, end = self.pop, self.rng, self.pop.start, self.pop.end
        med = [p for p in pop.persons if p.kind == "medicaid" and not p.has_snap]

        def first_start(p):
            return min(r.eff_start for r in pop.medicaid_rows(p.psk))

        for v in _variants(rng, ["year_typo", "newborn_dob_shift", "child_dob_after_start"]):
            p = None
            if v == "newborn_dob_shift":
                c = [x for x in med if x.newborn and x.dob < start - days(10) and x.psk not in self.used_psk]
                if c:
                    p = rng.choice(c)
                    old = p.dob
                    p.dob = old + days(rng.randint(1, 9))
                else:
                    v = "year_typo"
            if v == "year_typo":
                c = []
                for x in med:
                    if x.newborn or x.psk in self.used_psk:
                        continue
                    s0 = first_start(x)
                    for k in (10, 20):
                        nd = x.dob.replace(year=x.dob.year + k) if not (x.dob.month == 2 and x.dob.day == 29) \
                            else dt.date(x.dob.year + k, 2, 28)
                        if s0 < nd < start - days(1):
                            c.append((x, nd))
                            break
                if c:
                    p, nd = rng.choice(c)
                    old = p.dob
                    p.dob = nd
                else:
                    v = "child_dob_after_start"
            if v == "child_dob_after_start":
                c = [x for x in med if not x.newborn and x.psk not in self.used_psk
                     and (start - x.dob).days < 18 * 365 and first_start(x) + days(62) < start]
                p = rng.choice(c)
                old = p.dob
                s0 = first_start(p)
                p.dob = s0 + days(rng.randint(30, min(400, (start - s0).days - 2)))
            self.used_psk.add(p.psk)
            self.pend.append(("DOB_AFTER_ELIG_START", {"person": p, "variant": v, "old_dob": old}))

        for v in _variants(rng, ["one_day", "nested", "same_type_partial"]):
            if v == "one_day":
                c = [x for x in med if x.pattern == "term_in_run" and x.psk not in self.used_psk]
                if c:
                    p = rng.choice(c)
                    vers = list(pop.versions_by_key(p.psk, "FULL").values())[0]
                    latest = vers[-1]
                    t = latest.eff_end
                    rec = min(latest.recorded_at + dt.timedelta(days=rng.randint(1, 8), minutes=rng.randint(1, 600)),
                              dt.datetime.combine(end - days(2), dt.time(23, 0)))
                    new = pop.add_row(p, "MEDICAID", latest.elig_group, "FULL", t, t + days(rng.randint(20, 60)),
                                      rec, "defect")
                    self._overlap(p, v, new, latest)
                    continue
                v = "nested"
            if v == "nested":
                c = [x for x in med if x.pattern == "stable" and x.psk not in self.used_psk
                     and len(pop.versions_by_key(x.psk, "FULL")) == 1]
                c = [x for x in c if list(pop.versions_by_key(x.psk, "FULL").values())[0][-1].eff_start
                     < start - days(200)]
                p = rng.choice(c)
                latest = list(pop.versions_by_key(p.psk, "FULL").values())[0][-1]
                x0 = rng.randint(30, (start - latest.eff_start).days - 100)
                ns = latest.eff_start + days(x0)
                rec = rand_ts(rng, rand_date(rng, ns, end - days(2)))
                new = pop.add_row(p, "MEDICAID", latest.elig_group, "FULL", ns, ns + days(rng.randint(30, 90)),
                                  rec, "defect")
                self._overlap(p, v, new, latest)
                continue
            # same_type_partial: PARTIAL (QMB/SLMB) or WAIVER span re-keyed with an earlier start
            c = []
            for x in med:
                if x.psk in self.used_psk:
                    continue
                for st in ("PARTIAL", "WAIVER"):
                    kv = pop.versions_by_key(x.psk, st)
                    if len(kv) == 1:
                        c.append((x, st))
            p, st = rng.choice(c)
            latest = list(pop.versions_by_key(p.psk, st).values())[0][-1]
            ns = latest.eff_start - days(rng.randint(20, 60))
            ne = latest.eff_start + days(rng.randint(5, 40))
            rec = rand_ts(rng, rand_date(rng, start, end - days(2)))
            new = pop.add_row(p, "MEDICAID", latest.elig_group, st, ns, ne, rec, "defect")
            self._overlap(p, f"same_type_{st.lower()}", new, latest)

    def _overlap(self, p, v, new, existing):
        self.used_psk.add(p.psk)
        self.pend.append(("ELIG_OVERLAP_SAME_TYPE", {"person": p, "variant": v, "new": new, "existing": existing}))

    # ------------------------------------------------------------------ phase B: encounters
    def uncovered(self, psk, d, fd) -> bool:
        """Outside MEDICAID eligibility under BOTH readings: latest version per key (silver) and
        the as-of-file-date version per key. Unambiguous whichever a correct rule uses."""
        for vers in self.pop.versions_by_key(psk).values():
            if vers[-1].covers(d):
                return False
            asof = [v for v in vers if v.recorded_at.date() < fd]
            if asof and asof[-1].covers(d):
                return False
        return True

    def seed_encounters(self, encs):
        pop, rng, start = self.pop, self.rng, self.pop.start
        pool = [e for e in encs if not e.chained and e.freq == "1" and "boundary" not in e.tags]

        def free():
            return [e for e in pool if e.eid not in self.used_eids]

        # SVC_OUTSIDE_ELIG
        for v in _variants(rng, ["gap_between_spans", "day_after_end", "before_first_start"]):
            cands = []
            for e in free():
                rows = pop.medicaid_rows(e.psk)
                lo, hi = start, e.fd - days(1)
                first = min(r.eff_start for r in rows)
                ds = []
                if v == "gap_between_spans":
                    d = lo
                    while d <= hi:
                        if (self.uncovered(e.psk, d, e.fd) and any(r.eff_end < d for r in rows)
                                and any(r.eff_start > d for r in rows)):
                            ds.append(d)
                        d += days(1)
                elif v == "day_after_end":
                    for r in rows:
                        if r.eff_end != OPEN_END:
                            d = r.eff_end + days(1)
                            if lo <= d <= hi and self.uncovered(e.psk, d, e.fd):
                                ds.append(d)
                else:
                    d = max(lo, first - days(45))
                    while d < first and d <= hi:
                        if self.uncovered(e.psk, d, e.fd):
                            ds.append(d)
                        d += days(1)
                if ds:
                    cands.append((e, sorted(set(ds))))
            if not cands:  # fallback: any uncovered date in window
                v = "any_uncovered"
                for e in free():
                    d, ds = start, []
                    while d <= e.fd - days(1):
                        if self.uncovered(e.psk, d, e.fd):
                            ds.append(d)
                        d += days(1)
                    if ds:
                        cands.append((e, ds))
            if not cands:  # last resort (short runs): allow a lagged service date up to 120 days back
                v = "any_uncovered_lagged"
                for e in free():
                    d, ds = e.fd - days(120), []
                    while d <= e.fd - days(1):
                        if self.uncovered(e.psk, d, e.fd):
                            ds.append(d)
                        d += days(1)
                    if ds:
                        cands.append((e, ds))
            if not cands:
                raise RuntimeError("SVC_OUTSIDE_ELIG: no encounter with an uncovered service date; "
                                   "increase --months or --persons")
            e, ds = rng.choice(cands)
            d = rng.choice(ds)
            ln_no = rng.randint(1, len(e.lines))
            e.lines[ln_no - 1].svc = d
            e.adj_date = min(max(e.adj_date, d + days(1)), e.fd)
            self.used_eids.add(e.eid)
            self.pend.append(("SVC_OUTSIDE_ELIG", {"enc": e, "line_no": ln_no, "svc": d, "variant": v}))

        # MEMBER_UNKNOWN
        for v in _variants(rng, ["fabricated", "transposed", "one_digit"]):
            e = rng.choice(free())
            real = e.member_id
            e.member_id = self._unknown_id(v, real)
            if v == "fabricated":
                e.first, e.last = rng.choice(FIRST_NAMES), rng.choice(LAST_NAMES)
                e.dob = rand_date(rng, dt.date(1960, 1, 1), dt.date(2015, 12, 31))
            self.used_eids.add(e.eid)
            self.pend.append(("MEMBER_UNKNOWN", {"enc": e, "variant": v, "real_member_id": real}))

        # CLM02_LINE_SUM
        for v in _variants(rng, ["plus_cent", "omit_line", "decimal_shift", "minus_cent"], extra_p=0.0)[
                 : 3 + (rng.random() < 0.5)]:
            c = [e for e in free() if v != "omit_line" or len(e.lines) >= 2]
            e = rng.choice(c)
            s = e.line_sum
            if v == "plus_cent":
                e.clm02_override = s + Decimal("0.01")
            elif v == "minus_cent":
                e.clm02_override = s - Decimal("0.01")
            elif v == "omit_line":
                e.clm02_override = s - rng.choice(e.lines).charge
            else:
                e.clm02_override = s * 10
            self.used_eids.add(e.eid)
            self.pend.append(("CLM02_LINE_SUM", {"enc": e, "variant": v}))

        # FREQ7_NO_F8 (CLM05-3 in {7,8} without REF*F8)
        chain = [e for e in encs if e.freq in ("7", "8")]
        voids = [e for e in chain if e.freq == "8"]
        n = 3 + (rng.random() < 0.34)
        picked = [rng.choice(voids)] if voids else []
        rest = [e for e in chain if e not in picked]
        picked += rng.sample(rest, n - len(picked))
        for e in picked:
            e.emit_f8 = False
            self.used_eids.add(e.eid)
            self.pend.append(("FREQ7_NO_F8", {"enc": e, "variant": f"freq_{e.freq}"}))

    # ------------------------------------------------------------------ 834 change file
    def seed_834(self, change_members: list, c_date):
        pop, rng, start = self.pop, self.rng, self.pop.start
        known = [p for p in pop.persons if p.kind == "medicaid"]
        for v in _variants(rng, ["fabricated", "transposed", "one_digit"]):
            real = rng.choice(known)
            mid = self._unknown_id(v, real.msis_id)
            if v == "fabricated":
                first, last = rng.choice(FIRST_NAMES), rng.choice(LAST_NAMES)
                dob, sex = rand_date(rng, dt.date(1950, 1, 1), dt.date(2020, 1, 1)), rng.choice("FM")
            else:
                first, last, dob, sex = real.first, real.last, real.dob, real.sex
            mco = rng.choice(sorted(pop.mcos))
            m = {"ins03": "024", "ins04": "07", "member_id": mid, "first": first, "last": last,
                 "dob": dob, "sex": sex, "plan": rng.choice(pop.mcos[mco].plans),
                 "start": rand_date(rng, dt.date(2019, 1, 1), start - days(30)),
                 "end": rand_date(rng, start, c_date - days(1)), "defect": True}
            change_members.insert(rng.randint(1, len(change_members)), m)
            self.pend.append(("INS_TERM_UNKNOWN_MEMBER", {"member": m, "variant": v}))

    # ------------------------------------------------------------------ phase C: envelope / structure
    def seed_envelope(self, ics: list):
        rng = self.rng
        x837 = [ic for ic in ics if ic.kind == "837P"]
        groups = [(ic, g) for ic in ics for g in ic.groups]
        sts = [(ic, g, st) for ic, g in groups for st in g.sts]

        # NPI_LUHN on a billing-provider NM1*85 occurrence
        occ = []
        for ic in x837:
            for g in ic.groups:
                for st in g.sts:
                    for i, s in enumerate(st.segs):
                        if s[0] == "NM1" and s[1] == "85" and len(s) > 9 and s[8] == "XX":
                            occ.append((ic, g, st, i))
        rng.shuffle(occ)
        used_st, used_npi = set(), set()
        for v in _variants(rng, ["check_digit", "transpose", "typo"]):
            for ic, g, st, i in occ:
                npi = st.segs[i][9]
                if id(st) in used_st or npi in used_npi:
                    continue
                bad = self._bad_npi(npi, v)
                st.segs[i][9] = bad
                used_st.add(id(st))
                used_npi.add(npi)
                self.pend.append(("NPI_LUHN", {"ic": ic, "g": g, "st": st, "seg": i, "variant": v,
                                               "valid_npi": npi, "hl_id": st.meta[i]["hl_id"]}))
                break

        # HL_PARENT_BROKEN on a subscriber (22) HL
        used_st = set()
        for v in _variants(rng, ["nonexistent", "points_to_subscriber", "forward_ref_billing"]):
            cands = []
            for ic in x837:
                for g in ic.groups:
                    for st in g.sts:
                        if id(st) in used_st:
                            continue
                        hls = [(i, s) for i, s in enumerate(st.segs) if s[0] == "HL"]
                        subs = [(i, s) for i, s in hls if s[3] == "22"]
                        bills = [(i, s) for i, s in hls if s[3] == "20"]
                        for i, s in subs:
                            if v == "nonexistent":
                                cands.append((ic, g, st, i, str(len(hls) + rng.randint(1, 5))))
                            elif v == "points_to_subscriber":
                                prior = [x for j, x in subs if j < i]
                                if prior:
                                    cands.append((ic, g, st, i, rng.choice(prior)[1]))
                            else:
                                later = [x for j, x in bills if j > i]
                                if later:
                                    cands.append((ic, g, st, i, rng.choice(later)[1]))
            ic, g, st, i, newp = rng.choice(cands)
            old = st.segs[i][2]
            st.segs[i][2] = newp
            used_st.add(id(st))
            self.pend.append(("HL_PARENT_BROKEN", {"ic": ic, "g": g, "st": st, "seg": i, "variant": v,
                                                   "hl_id": st.segs[i][1], "old_parent": old, "new_parent": newp}))

        # ISA_WIDTH
        for ic, v in zip(rng.sample(ics, 3), rng.sample(["sender_pad_14", "receiver_pad_16", "auth_info_9",
                                                         "security_info_11"], 3)):
            ic.isa_variant = v
            self.pend.append(("ISA_WIDTH", {"ic": ic, "variant": v}))

        # GE_COUNT_MISMATCH (ZZ translator: STs accepted, group error noted -> 999 AK901=E, AK905=5)
        ge_groups = rng.sample(groups, 3)
        for (ic, g), delta in zip(ge_groups, rng.sample([1, -1, 2], 3)):
            if len(g.sts) + delta < 0:
                delta = 1
            g.ge_delta = delta
            for st in g.sts:
                st.ack_code = "AK905:5"
            self.pend.append(("GE_COUNT_MISMATCH", {"ic": ic, "g": g, "delta": delta}))
        ge_ids = {id(g) for _, g in ge_groups}

        # SE_COUNT_MISMATCH (translator rejects the ST: IK5 R, IK502=4)
        free_sts = [x for x in sts if id(x[1]) not in ge_ids]
        se_pick = rng.sample(free_sts, 3 + (rng.random() < 0.4))
        for (ic, g, st) in se_pick:
            st.se_delta = rng.choice([-2, -1, 1, 2, 3])
            st.ack_status, st.ack_code = "R", "IK502:4"
            self.pend.append(("SE_COUNT_MISMATCH", {"ic": ic, "g": g, "st": st, "delta": st.se_delta}))

        # TRANSLATOR_REJECT (primary): ST clean against every W1 envelope check, rejected by the translator
        se_ids = {id(x[2]) for x in se_pick}
        free_sts = [x for x in free_sts if id(x[2]) not in se_ids and x[0].kind == "837P"]
        for (ic, g, st), code in zip(rng.sample(free_sts, 3), rng.sample(["IK502:5", "IK502:I5", "IK502:5"], 3)):
            st.ack_status, st.ack_code = "R", code
            self.pend.append(("TRANSLATOR_REJECT", {"ic": ic, "g": g, "st": st, "code": code}))

        # DUP_INTERCHANGE: one exact re-send + counter resets (same ISA05/06/07/08/13, same sender)
        by_sender = {}
        for ic in sorted(x837, key=lambda x: x.order):
            by_sender.setdefault(ic.sender, []).append(ic)
        src = rng.choice(x837)
        resend = None
        pairs = []
        for snd, lst in sorted(by_sender.items()):
            for j in range(1, len(lst)):
                for i in range(j):
                    if src not in (lst[i], lst[j]):
                        pairs.append((lst[i], lst[j]))
        rng.shuffle(pairs)
        resets, used_j = [], set()
        n_resets = 2 + (rng.random() < 0.3)
        for i_ic, j_ic in pairs:
            if len(resets) >= n_resets:
                break
            if (id(j_ic) in used_j or id(i_ic) in used_j
                    or any(id(j_ic) == id(r[0]) or id(i_ic) == id(r[0]) for r in resets)):
                continue
            used_j.add(id(j_ic))
            resets.append((i_ic, j_ic))
        for i_ic, j_ic in resets:
            j_ic.isa13 = i_ic.isa13
            gs_reset = rng.random() < 0.5
            if gs_reset:
                for k, g in enumerate(j_ic.groups):
                    g.gs06 = str(int(i_ic.groups[0].gs06) + k)
            self.pend.append(("DUP_INTERCHANGE", {"ic": j_ic, "dup_of": i_ic,
                                                  "variant": "counter_reset" + ("_gs06_too" if gs_reset else "")}))
        resend = src
        self.pend.append(("DUP_INTERCHANGE", {"ic": None, "dup_of": src, "variant": "exact_resend",
                                              "resend_lag": rng.randint(1, 5)}))
        return resend

    def _bad_npi(self, npi: str, v: str) -> str:
        rng = self.rng
        for _ in range(200):
            d = list(npi)
            if v == "check_digit":
                d[9] = str((int(d[9]) + rng.randint(1, 9)) % 10)
            elif v == "transpose":
                idx = [i for i in range(1, 9) if d[i] != d[i + 1]]
                i = rng.choice(idx)
                d[i], d[i + 1] = d[i + 1], d[i]
            else:
                i = rng.randint(1, 8)
                d[i] = rng.choice([c for c in "0123456789" if c != d[i]])
            bad = "".join(d)
            if not npi_is_valid(bad):
                return bad
        raise RuntimeError("could not corrupt NPI")

    # ------------------------------------------------------------------ phase D: IE trailers
    def seed_ie_trailer(self, ie_files):
        for f, delta in zip(self.rng.sample(ie_files, 3), self.rng.sample([1, -1, 2], 3)):
            f["trailer_delta"] = delta
            self.pend.append(("IE_TRAILER_COUNT", {"file": f, "delta": delta}))


# ====================================================================================== PART C (opt-in)
# Additional classes for the agent's field-validity / timeliness checks. Opt-in (class_set="w1+partc") so a
# default run, and every committed W1 ledger hash, stays byte-identical. Own RNG stream ("<seed>:partc").
PARTC_CLASSES = [
    "INVALID_POS", "INVALID_DX_SHAPE", "INVALID_FREQ", "MISSING_FREQ",
    "MISSING_ADJUD_DATE", "LATE_SUBMISSION", "ELIG_BAD_SEX",
]
TIMELY_FILING_DAYS = 365


class PartCSeeder:
    """Plants PART C defects on encounters / persons not already used by a W1 class."""

    def __init__(self, base: Seeder, rng, elig):
        self.b, self.rng, self.elig, self.pop = base, rng, elig, base.pop

    def _free(self, encs, pred=lambda e: True):
        return [e for e in encs if not e.chained and e.freq == "1" and "boundary" not in e.tags
                and e.eid not in self.b.used_eids and e.psk not in self.b.used_psk and pred(e)]

    def _take(self, e, cls, **p):
        self.b.used_eids.add(e.eid)
        self.b.pend.append((cls, dict(p, enc=e)))

    def seed_encounters(self, encs, controls):
        rng = self.rng
        # INVALID_POS -- CLM05-1 values a real feed produces when a 2-digit code is mangled upstream;
        # all stay within X12 AN 1..2 so the translator (syntax-only, D9) accepts the ST.
        for v in _variants(rng, ["leading_zero_stripped", "truncated", "letter_o_for_zero"]):
            if v == "leading_zero_stripped":
                c = self._free(encs, lambda e: e.pos.startswith("0"))
                if not c:
                    v = "truncated"
            if v == "letter_o_for_zero":
                c = self._free(encs, lambda e: "0" in e.pos)
                if not c:
                    v = "truncated"
            if v == "truncated":
                c = self._free(encs, lambda e: e.pos[0] != "0")
            e = rng.choice(c)
            old = e.pos
            e.pos = {"leading_zero_stripped": old[1:], "truncated": old[0],
                     "letter_o_for_zero": old.replace("0", "O", 1)}[v]
            self._take(e, "INVALID_POS", variant=v, old=old)

        # INVALID_DX_SHAPE -- principal dx (HI ABK) not X12 ICD-10-CM shaped. U-codes are NOT used:
        # U07.1/U09.9 are valid ICD-10-CM, so a "starts with U" seed would be a false defect.
        for v in _variants(rng, ["decimal_point", "icd9_numeric", "lowercase"]):
            e = rng.choice(self._free(encs, lambda e: len(e.dx[0]) >= 4 or v != "decimal_point"))
            old = e.dx[0]
            if v == "decimal_point":
                new = old[:3] + "." + old[3:]
            elif v == "icd9_numeric":
                new = rng.choice(["4019", "25000", "4659", "5990", "311"])   # ICD-9-CM leaked under ABK
            else:
                new = old.lower()
            e.dx[0] = new
            self._take(e, "INVALID_DX_SHAPE", variant=v, old=old)

        # INVALID_FREQ -- CLM05-3 outside {1,7,8}: UB-04 (institutional) frequency codes bleeding into 837P
        for v in _variants(rng, ["2", "6", "5", "0"], extra_p=0.0)[:3]:
            e = rng.choice(self._free(encs))
            e.freq = v
            self._take(e, "INVALID_FREQ", variant=f"freq_{v}")

        # MISSING_FREQ -- CLM05-3 absent (CLM05 = "POS:B"). Mapper lands freq_code NULL; not rejected.
        for v in _variants(rng, ["single_line", "multi_line", "any"]):
            pred = {"single_line": lambda e: len(e.lines) == 1, "multi_line": lambda e: len(e.lines) >= 2,
                    "any": lambda e: True}[v]
            c = self._free(encs, pred) or self._free(encs)
            e = rng.choice(c)
            e.freq = ""
            self._take(e, "MISSING_FREQ", variant=v)

        # MISSING_ADJUD_DATE -- every 2430 loop of the encounter omits DTP*573
        for v in _variants(rng, ["single_line", "multi_line", "with_cas"]):
            pred = {"single_line": lambda e: len(e.lines) == 1, "multi_line": lambda e: len(e.lines) >= 2,
                    "with_cas": lambda e: any(ln.charge > ln.paid for ln in e.lines)}[v]
            e = rng.choice(self._free(encs, pred) or self._free(encs))
            e.omit_573 = True
            self._take(e, "MISSING_ADJUD_DATE", variant=v)

        # LATE_SUBMISSION -- whole claim's DOS moved back so DTP*573 - DTP*472 > 365 on every line;
        # every moved date stays inside eligibility (no SVC_OUTSIDE_ELIG side effect).
        for v in _variants(rng, ["just_over", "months_over", "years_over"]):
            lag = {"just_over": (366, 372), "months_over": (400, 500), "years_over": (700, 800)}[v]
            e = self._shift(encs, lag)
            self._take(e, "LATE_SUBMISSION", variant=v)
        # negative control: lag exactly 365 days is timely
        e = self._shift(encs, (TIMELY_FILING_DAYS, TIMELY_FILING_DAYS), exact_max=True)
        self.b.used_eids.add(e.eid)
        controls.append({"kind": "ADJUD_LAG_EXACTLY_365", "encounter_id": e.eid,
                         "svc_date": max(ln.svc for ln in e.lines).isoformat(),
                         "adjud_date": e.adj_date.isoformat()})

    def _shift(self, encs, lag, exact_max=False):
        """Move every line's DTP*472 back by the same offset so the LATEST line lags DTP*573 by `target`
        days (so every line lags >= target). exact_max: only claims whose lines share one DOS, so every
        line lags exactly `target`."""
        rng = self.rng
        cands = self._free(encs, (lambda e: len({ln.svc for ln in e.lines}) == 1) if exact_max else (lambda e: True))
        rng.shuffle(cands)
        for e in cands:
            p = self.pop.by_psk[e.psk]
            target = rng.randint(*lag)
            mx = max(ln.svc for ln in e.lines)
            shift = (e.adj_date - days(target)) - mx
            new = [ln.svc + shift for ln in e.lines]
            if any(d < p.dob for d in new):
                continue
            if not all(self.elig.mco_ok(e.psk, d, e.fd) and not self.b.uncovered(e.psk, d, e.fd) for d in new):
                continue
            for ln, d in zip(e.lines, new):
                ln.svc = d
            return e
        raise RuntimeError("LATE_SUBMISSION: no encounter whose member was eligible a year earlier")

    def seed_ie_sex(self):
        rng = self.rng
        med = [p for p in self.pop.persons if p.kind == "medicaid" and p.psk not in self.b.used_psk]
        for v in _variants(rng, ["numeric_code", "long_form", "lowercase"]):
            p = rng.choice([x for x in med if x.psk not in self.b.used_psk and x.sex in ("F", "M")])
            p.ie_sex = {"numeric_code": {"M": "1", "F": "2"}, "long_form": {"M": "MALE", "F": "FEMALE"},
                        "lowercase": {"M": "m", "F": "f"}}[v][p.sex]
            self.b.used_psk.add(p.psk)
            self.b.pend.append(("ELIG_BAD_SEX", {"person": p, "variant": v}))
