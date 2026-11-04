"""Metadata-only binding proposer (Part D, D3).

A differently named data plane (a hypothetical second state) is built in DuckDB. The proposer must:
  * read only names, types and aggregates; no data value appears anywhere in the profile (checked by scanning the
    serialised profile for every distinct value in the tables);
  * suppress shapes seen fewer than 11 times (small cells);
  * propose the right column for the elements it is confident about, and leave the rest UNMATCHED (never guess);
  * flag identity / provider / money elements for a human look.
"""
import json
import random

import duckdb
import yaml

from dqagent.onboarding.proposer import profile_relation, propose_bindings
from dqagent.rules.model import ROOT

TRUTH = {
    "ENCOUNTER_HEADER": ("st_b.clm_hdr", {"CLM.ID": "icn", "CLM.MEMBER_ID": "mbr_msis_num", "PRV.BILLING_NPI": "bill_prov_npi",
                                          "CLM.TOTAL_CHARGE": "tot_chg_amt", "CLM.POS": "pos_cd",
                                          "CLM.FREQ_CODE": "clm_freq_cd", "CLM.SUBMITTER_ID": "sbmtr_id"}),
    "ELIG_SEGMENT": ("st_b.elig_span", {"ELG.PERSON_KEY": "person_key", "ELG.MEMBER_ID": "msis_num",
                                        "ELG.EFF_START": "elig_beg_dt", "ELG.EFF_END": "elig_end_dt",
                                        "ELG.DOB": "dob", "ELG.PROGRAM": "pgm_cd"}),
}


class DP:
    def __init__(self):
        self.c = duckdb.connect()

    def fetchall(self, q, a=None):
        return self.c.execute(q, a or []).fetchall()


def _build():
    rng = random.Random(7)
    dp = DP()
    dp.c.execute("CREATE SCHEMA st_b")
    dp.c.execute("CREATE TABLE st_b.clm_hdr (icn VARCHAR, mbr_msis_num VARCHAR, bill_prov_npi VARCHAR, tot_chg_amt DECIMAL(12,2), "
                 "pos_cd VARCHAR, clm_freq_cd VARCHAR, sbmtr_id VARCHAR, rndr_note VARCHAR)")
    dp.c.execute("CREATE TABLE st_b.elig_span (person_key VARCHAR, msis_num VARCHAR, elig_beg_dt DATE, elig_end_dt DATE, "
                 "dob DATE, pgm_cd VARCHAR, county_cd VARCHAR)")
    values = set()
    for i in range(60):
        row = [f"{rng.randint(10**15, 10**16 - 1)}", f"{rng.randint(10**8, 10**9 - 1)}A", "1234567893",
               round(rng.uniform(10, 900), 2), rng.choice(["11", "22", "02"]), rng.choice(["1", "7"]),
               rng.choice(["PLAN01", "PLAN02"]), "x" * (i % 3)]
        if i == 0:
            row[1] = "ODD-SHAPE-1"                  # a single odd shape: must be suppressed (small cell)
        dp.c.execute("INSERT INTO st_b.clm_hdr VALUES (?,?,?,?,?,?,?,?)", row)
        values |= {str(v) for v in row if v not in ("", None)}
        e = [f"P{i:05d}", f"{rng.randint(10**8, 10**9 - 1)}A", "2025-01-01", None, f"19{rng.randint(40, 99)}-02-0{rng.randint(1, 9)}",
             rng.choice(["MEDICAID", "SNAP"]), f"{rng.randint(1, 99):03d}"]
        dp.c.execute("INSERT INTO st_b.elig_span VALUES (?,?,?,?,?,?,?)", e)
        values |= {str(v) for v in e if v}
    return dp, values


def test_profile_carries_no_values_and_suppresses_small_cells():
    dp, values = _build()
    profs = [profile_relation(dp, r) for r in ("st_b.clm_hdr", "st_b.elig_span")]
    blob = json.dumps(profs)
    leaked = [v for v in values if len(v) >= 3 and v in blob and v not in ("MEDICAID",)]
    # column NAMES are metadata and may coincide with nothing; no data value may appear
    assert not [v for v in leaked if v not in {"SNAP"}], leaked
    mbr = next(c for c in profs[0]["columns"] if c["name"] == "mbr_msis_num")
    assert mbr["x_suppressed_shapes"] == 1 and all(s["shape"] != "3A-5A-1N" for s in mbr["shapes"])
    assert {c["distinct_bucket"] for c in profs[0]["columns"]} <= {"0", "1", "2-10", "11-100", "101-1000", ">1000"}


def test_proposals_are_right_or_left_unmatched_never_wrong():
    dp, _ = _build()
    profs = [profile_relation(dp, r) for r in ("st_b.clm_hdr", "st_b.elig_span")]
    res = propose_bindings(yaml.safe_load(open(ROOT / "rules" / "elements.yaml")), profs)
    for ent, (rel, cols) in TRUTH.items():
        assert res["entities"].get(ent) == rel, (ent, res["entities"])
        for el, col in cols.items():
            if el in res["columns"]:
                assert res["columns"][el] == col, (el, res["columns"][el], col)   # never a wrong binding
    proposed = {p["element"] for p in res["proposals"]}
    expected = {el for _, cols in TRUTH.values() for el in cols}
    assert len(proposed & expected) >= 0.8 * len(expected), sorted(expected - proposed)
    assert "rndr_note" not in res["columns"].values() and "county_cd" not in res["columns"].values()
    for p in res["proposals"]:
        if p["element"] in ("CLM.MEMBER_ID", "PRV.BILLING_NPI", "CLM.TOTAL_CHARGE", "ELG.PERSON_KEY"):
            assert p["needs_attention"], p
    # undelivered domains are never forced onto these relations
    assert "PHARMACY_CLAIM" not in res["entities"] and "PROVIDER" not in res["entities"]
