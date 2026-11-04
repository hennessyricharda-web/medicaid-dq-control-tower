"""T1 negative tests for the data path (W1 engineering review ENG-W1-1..8). Each test is the
concrete failing input the reviewer named, run end to end through intake → seal → evaluate."""
import pytest

from dqagent.engine.seal import SealIntegrityError
from dqagent.ingest.x12 import tokenize

from .fixtures_w1 import acks, ie_file, isa, person, x837
from .test_w1_slice import SCOPE, _eligibility, _write, decide, s  # noqa: F401  (fixture reuse)


def _enc(s, batch, files):
    return s.run_pipeline("p6_claims", batch, files, ["bronze.encounter_header", "bronze.encounter_line"])


def _hops(r, hop):
    return [a for a in r["abc"] if a["hop"] == hop]


# ENG-W1-1 -------------------------------------------------------------------------------
def test_unparseable_end_date_is_rejected_not_open_coverage(s):
    bad = person("P1", "M0001", end="2026-06-30")                 # dashes: not YYYYMMDD
    r = _eligibility(s, rows=[bad, person("P2", "M0002")])
    raw = [a for a in _hops(r, "RAW->LANDING") if a["measure"] == "records"][0]
    assert raw["rejected"] == 1 and raw["disposition"] == "FAIL"   # balanced, but lossy ⇒ held
    assert s.dp.fetchall("SELECT count(*) FROM bronze.ie_segment WHERE msis_id='M0001'")[0][0] == 0


def test_same_grain_termination_supersedes_instead_of_overlapping(s):
    _eligibility(s, batch="B1", rows=[person("P1", "M0001", start="20250101")])
    s.verify_and_publish()
    term = person("P1", "M0001", start="20250101", end="20260630")
    term[-1] = "20260301000000"                                     # later recorded_at
    r = _eligibility(s, batch="B2", rows=[term])
    assert r["summary"]["bronze.ie_segment"]["autonomous"] == 1     # R-ELG-002 does NOT call it an overlap
    s.verify_and_publish()
    cur = s.dp.fetchall("SELECT eff_end FROM dq_rel.ie_segment_current WHERE msis_id='M0001'")
    assert [str(c[0]) for c in cur] == ["2026-06-30"]               # one current version: the termination


# ENG-W1-2 -------------------------------------------------------------------------------
def test_lf_terminated_837_parses_every_claim(s):
    data = x837("ZZMCOA", "000000201", [{"id": "E1", "member": "M0001", "lines": [("Z9001", 10.0, "20260110")]}])
    pf = tokenize(data.replace(b"~", b"\n"), "f")
    assert pf.delimiters["segment"] == "\n"
    assert sum(1 for x in pf.segments if x.seg_id == "CLM") == 1


def test_untokenizable_x12_file_fails_the_batch_instead_of_vanishing(s):
    _eligibility(s)
    s.verify_and_publish()
    p = _write(s.tmp, "garbled.x12", b"IS*garbage-with-no-envelope")
    a = _write(s.tmp, "garbled.ack.csv", acks("000000301", []))
    r = _enc(s, "B-G", [("X12_837P", p, "ZZ-MCO-A", a)])
    fr = _hops(r, "FILE->RAW")[0]
    assert fr["disposition"] == "UNBALANCEABLE" and r["abc_failed"]


# ENG-W1-3 -------------------------------------------------------------------------------
def test_translator_keys_rejections_by_gs06_and_missing_ack_is_unbalanceable(s):
    _eligibility(s)
    s.verify_and_publish()
    g1 = x837("ZZMCOA", "000000401", [{"id": "E1", "member": "M0001", "lines": [("Z9001", 10.0, "20260110")]}], gs06="1")
    g2 = x837("ZZMCOA", "000000401", [{"id": "E2", "member": "M0002", "lines": [("Z9001", 20.0, "20260110")]}], gs06="2")
    gs1 = g1.decode().split("GS*", 1)[1].rsplit("~IEA", 1)[0]
    gs2 = g2.decode().split("GS*", 1)[1].rsplit("~IEA", 1)[0]
    two_groups = (isa("ZZMCOA", "000000401") + "GS*" + gs1 + "~GS*" + gs2 + "~IEA*2*000000401~").encode()
    p = _write(s.tmp, "two.x12", two_groups)
    a = _write(s.tmp, "two.ack.csv", b"isa13,gs06,st02,transaction_set,status,error_code\n"
               b"000000401,1,0001,837,R,IK502:5\n000000401,2,0001,837,A,\n")
    r = _enc(s, "B-T", [("X12_837P", p, "ZZ-MCO-A", a)])
    landed = {x[0] for x in s.dp.fetchall("SELECT encounter_id FROM bronze.encounter_header WHERE _dq_batch_id='B-T'")}
    assert landed == {"E2"}                                          # only GS 1 / ST 0001 was rejected
    assert _hops(r, "TRANSLATOR")[0]["disposition"] == "PASS"
    p2 = _write(s.tmp, "noack.x12", x837("ZZMCOB", "000000402", [{"id": "E9", "member": "M0001",
                                                                   "lines": [("Z9001", 10.0, "20260110")]}]))
    r2 = _enc(s, "B-N", [("X12_837P", p2, "ZZ-MCO-B")])
    assert _hops(r2, "TRANSLATOR")[0]["disposition"] == "UNBALANCEABLE" and r2["abc_failed"]


# ENG-W1-4 -------------------------------------------------------------------------------
def test_two_mcos_same_claim_id_are_separate_units_and_lines_attach_correctly(s):
    _eligibility(s)
    s.verify_and_publish()
    c = [{"id": "100045", "member": "M0001", "lines": [("Z9001", 150.0, "20260110")]}]
    pa = _write(s.tmp, "a.x12", x837("ZZMCOA", "000000501", c))
    pb = _write(s.tmp, "b.x12", x837("ZZMCOB", "000000501", c))
    r = _enc(s, "B-M", [("X12_837P", pa, "ZZ-MCO-A", _write(s.tmp, "a.csv", acks("000000501", ["A"]))),
                        ("X12_837P", pb, "ZZ-MCO-B", _write(s.tmp, "b.csv", acks("000000501", ["A"])))])
    units = s.dp.fetchall("SELECT DISTINCT _dq_unit_key FROM bronze.encounter_header WHERE _dq_batch_id='B-M'")
    assert len(units) == 2
    assert s.dp.fetchall("SELECT count(*) FROM bronze.encounter_line WHERE _dq_batch_id='B-M'")[0][0] == 2
    with s.cp.as_role("dq_eval_svc") as c2:
        r1 = c2.execute("SELECT disposition FROM dq_ctl.results WHERE rule_id='R-ENC-001' AND unit_key LIKE 'B-M:%'").fetchall()
    assert r1 and all(x["disposition"] == "PASS" for x in r1)        # 150 = 150 for each MCO, not 300


def test_replacement_chain_depth_two_is_one_chain(s):
    _eligibility(s)
    s.verify_and_publish()
    claims = [{"id": "E1", "member": "M0001", "lines": [("Z9001", 10.0, "20260110")]},
              {"id": "E2", "member": "M0001", "freq": "7", "f8": "E1", "lines": [("Z9001", 12.0, "20260110")]},
              {"id": "E3", "member": "M0001", "freq": "7", "f8": "E2", "lines": [("Z9001", 14.0, "20260110")]}]
    p = _write(s.tmp, "chain.x12", x837("ZZMCOA", "000000601", claims))
    _enc(s, "B-C", [("X12_837P", p, "ZZ-MCO-A", _write(s.tmp, "chain.csv", acks("000000601", ["A"] * 3)))])
    roots = {x[0] for x in s.dp.fetchall("SELECT chain_root FROM bronze.encounter_header WHERE _dq_batch_id='B-C'")}
    assert roots == {"E1"}
    assert len({x[0] for x in s.dp.fetchall(
        "SELECT _dq_unit_key FROM bronze.encounter_header WHERE _dq_batch_id='B-C'")}) == 1


def test_2330a_other_subscriber_and_2330b_ref_do_not_overwrite_claim_fields(s):
    from dqagent.ingest.mappers import map_837p
    body = ("ST*837*0001*005010X222A1~BHT*0019*00*R*20260115*1200*RP~HL*1**20*1~NM1*85*2*C*****XX*1234567893~"
            "HL*2*1*22*0~SBR*P*18*******MC~NM1*IL*1*DOE*J****MI*M0001~CLM*E5*10.00***11:B:7*Y*A*Y*Y~HI*ABK:Z0000~"
            "SBR*S*18*******HM~AMT*D*8.00~OI***Y*P**Y~NM1*IL*1*DOE*J****MI*OTHER999~NM1*PR*2*MCO*****PI*ZZMCOA~"
            "REF*F8*MCOICN123~LX*1~SV1*HC:Z9001*10.00*UN*1***1~DTP*472*D8*20260110~DTP*573*D8*20260201~SE*20*0001")
    f = (isa("ZZMCOA", "000000701") + "GS*HC*ZZMCOA*ZZMEDICAID*20260115*1200*7*X*005010X222A1~" + body +
         "~GE*1*7~IEA*1*000000701~").encode()
    h, ln, _ = map_837p(tokenize(f, "f"), "ZZ-MCO-A")
    assert h[0]["member_id"] == "M0001" and h[0]["orig_encounter_id"] is None and h[0]["mco_paid"] == 8
    assert str(ln[0]["svc_date"]) == "2026-01-10"                     # DTP*472, never DTP*573


# ENG-W1-6 / 7 ---------------------------------------------------------------------------
def test_landing_mutated_between_digest_and_copy_rolls_back_the_seal(s, monkeypatch):
    from dqagent.engine import seal as sealmod
    orig = sealmod.Sealer._digest
    calls = {"n": 0}

    def mutate_after_first(self, con, source, cols, load_id, extra=""):
        out = orig(self, con, source, cols, load_id, extra)
        calls["n"] += 1
        if calls["n"] == 1:
            self.dp.execute("UPDATE landing.ie_segment SET eff_end = NULL")   # the tamper the reviewer named
        return out
    monkeypatch.setattr(sealmod.Sealer, "_digest", mutate_after_first)
    with pytest.raises(SealIntegrityError):
        _eligibility(s)
    assert s.dp.fetchall("SELECT count(*) FROM bronze.ie_segment")[0][0] == 0


def test_fingerprint_covers_every_column_including_household_and_file_keys(s):
    _eligibility(s)
    s.verify_and_publish()
    s.dp.execute("UPDATE bronze.ie_segment SET household_id = 'HX' WHERE msis_id='M0002'")
    assert s.verifier.attest_fingerprints()["denied"] == 1


# ENG-W1-8 -------------------------------------------------------------------------------
def test_null_predicate_is_not_evaluated_never_pass(s):
    _eligibility(s)
    s.verify_and_publish()
    c = [{"id": "E1", "member": "M0001", "freq": "", "lines": [("Z9001", 10.0, "20260110")]}]   # CLM05-3 empty
    p = _write(s.tmp, "nofreq.x12", x837("ZZMCOA", "000000801", c))
    _enc(s, "B-F", [("X12_837P", p, "ZZ-MCO-A", _write(s.tmp, "nofreq.csv", acks("000000801", ["A"])))])
    with s.cp.as_role("dq_eval_svc") as c2:
        d = c2.execute("SELECT disposition FROM dq_ctl.results WHERE rule_id='R-ENC-002' AND unit_key LIKE 'B-F:%'").fetchone()
    assert d["disposition"] == "NOT_EVALUATED"


def test_duplicate_ie_extract_is_detected(s):
    rows = [person("P1", "M0001")]
    _write(s.tmp, "x.txt", ie_file(rows, seq=42))
    s.run_pipeline("p4_eligibility_ie", "B1", [("IE", s.tmp / "x.txt")], ["bronze.ie_segment"])
    s.verify_and_publish()
    _write(s.tmp, "y.txt", ie_file(rows, seq=42))
    r = s.run_pipeline("p4_eligibility_ie", "B2", [("IE", s.tmp / "y.txt")], ["bronze.ie_segment"])
    assert r["summary"]["bronze.ie_segment"]["held"] == 1
    with s.cp.as_role("dq_eval_svc") as c:
        d = c.execute("SELECT disposition FROM dq_ctl.results WHERE rule_id='FLAT-DUP-FILE' AND unit_key LIKE 'B2:%'").fetchone()
    assert d["disposition"] == "FAIL"


# SCP-W1 (834 path was untested) ----------------------------------------------------------
def _x834(isa13, members):
    body = ["ST*834*0001*005010X220A1", "BGN*00*REF1*20260115*1200****2", "N1*P5*ZZ MEDICAID AGENCY*FI*009900001",
            "N1*IN*ZZ MEDICAID MANAGED CARE*FI*009900002"]
    for mid, code in members:
        body += [f"INS*Y*18*{code}*07*A***AC", f"REF*0F*{mid}", "NM1*IL*1*DOE*JANE", "DMG*D8*19800101*F",
                 "HD*024**HMO*PLAN1", "DTP*348*D8*20260101", "DTP*349*D8*20260131"]
    body.append(f"SE*{len(body) + 1}*0001")
    return (isa("ZZBROKER", isa13) + "GS*BE*ZZBROKER*ZZMEDICAID*20260115*1200*9*X*005010X220A1~" + "~".join(body) +
            f"~GE*1*9~IEA*1*{isa13}~").encode()


def test_834_termination_of_unknown_member_is_held_known_member_released(s):
    _eligibility(s)
    s.verify_and_publish()
    p = _write(s.tmp, "834.x12", _x834("000000901", [("M0001", "024"), ("M9999", "024")]))
    a = _write(s.tmp, "834.csv", b"isa13,gs06,st02,transaction_set,status,error_code\n000000901,9,0001,834,A,\n")
    r = s.run_pipeline("p4_eligibility_ie", "B-834", [("X12_834", p, "ZZ-BROKER", a)], ["bronze.enrollment_834"])
    assert all(h["disposition"] == "PASS" for h in r["abc"]), r["abc"]
    s.verify_and_publish()
    vis = {x[0] for x in s.dp.fetchall("SELECT member_id FROM dq_rel.enrollment_834_released")}
    assert vis == {"M0001"}
    with s.cp.as_role("dq_eval_svc") as c:
        held = c.execute("SELECT count(*) n FROM dq_ctl.results r JOIN dq_ctl.units u USING (unit_key) "
                         "WHERE r.rule_id='R-834-001' AND r.disposition='FAIL' AND u.state='HELD'").fetchone()["n"]
    assert held == 1


# Part B: measures + applicability, end to end ---------------------------------------------
def test_batch_measure_records_ratio_against_cms_threshold(s):
    _eligibility(s)
    s.verify_and_publish()
    c = [{"id": "E1", "member": "M0001", "npi": "1234567890", "lines": [("Z9001", 10.0, "20260110")]},
         {"id": "E2", "member": "M0002", "lines": [("Z9001", 10.0, "20260110")]}]
    p = _write(s.tmp, "npi.x12", x837("ZZMCOA", "000000951", c))
    _enc(s, "B-NPI", [("X12_837P", p, "ZZ-MCO-A", _write(s.tmp, "npi.csv", acks("000000951", ["A", "A"])))])
    with s.cp.as_role("dq_eval_svc") as cx:
        m = {r["rule_id"]: r for r in cx.execute("SELECT * FROM dq_ctl.measure_results WHERE batch_id='B-NPI'").fetchall()}
    assert (m["M-RULE-7907"]["numerator"], m["M-RULE-7907"]["denominator"]) == (1, 2)
    assert m["M-RULE-7907"]["disposition"] == "FAIL"                 # 0.5 > CMS max 0.01
    assert m["M-MIS-25-022-22"]["disposition"] == "PASS"


def test_rule_on_an_undelivered_feed_records_not_applicable_and_does_not_hold(fresh_cp_dsn, tmp_path):
    """Feed applicability is CONFIGURATION: it changes only through an approved change request (Part D). Marking an
    element undelivered reduces what is checked, so it is LOOSENING and needs TWO approvers other than the author."""
    import pytest
    from dqagent.hitl.decisions import Denied
    from .w1_harness import Slice
    sl = Slice(fresh_cp_dsn)
    sl.tmp = tmp_path
    feeds = sl.effective_item("FEEDS", "feeds")
    feeds["content"]["not_delivered_elements"] = ["ELG.DOB"]
    cr, status = sl.govern([feeds], approvers=("checker",))
    assert cr["loosening"] and status == "AWAITING_APPROVAL"          # one approver is not enough for a loosening
    with pytest.raises(Denied):                                        # nor can the author add their own approval
        tok = sl.idp.token("maker", step_up=True, challenge=sl.registry.challenge(cr["cr_id"], "APPROVED"))
        sl.registry.decide(cr["cr_id"], "APPROVED", tok, "E100", ["governance:AUTH_RULES"])
    tok = sl.idp.token("checker2", step_up=True, challenge=sl.registry.challenge(cr["cr_id"], "APPROVED"))
    sl.registry.decide(cr["cr_id"], "APPROVED", tok, "E210", ["governance:AUTH_RULES"])
    assert sl.apply_change(cr["cr_id"]) == "APPLIED"
    r = _eligibility(sl, rows=[person("P1", "M0001", dob="")])        # DOB empty, but DOB is not delivered anyway
    assert r["summary"]["bronze.ie_segment"]["autonomous"] == 1      # R-ELG-001 would have failed; it does not apply
    with sl.cp.as_role("dq_eval_svc") as cx:
        d = cx.execute("SELECT disposition FROM dq_ctl.results WHERE rule_id='R-ELG-001'").fetchone()["disposition"]
    assert d == "NOT_APPLICABLE"
