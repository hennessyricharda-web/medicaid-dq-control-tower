from dqagent.ingest.mappers import map_837p, parse_ie
from dqagent.ingest.x12 import tokenize


def isa(isa13="000000101", usage="T"):
    s = ("ISA*00*          *00*          *ZZ*ZZMCOA         *ZZ*ZZMEDICAID     *260115*1200*^*00501*"
         f"{isa13}*0*{usage}*:~")
    assert len(s) == 106, len(s)
    return s


def x837(se_count=None, freq="1", f8=True, hl_parent="1"):
    body = ["ST*837*0001*005010X222A1", "BHT*0019*00*REF1*20260115*1200*RP",
            "HL*1**20*1", "NM1*85*2*CLINIC*****XX*1234567893",
            f"HL*2*{hl_parent}*22*0", "SBR*P*18*******MC", "NM1*IL*1*DOE*JANE****MI*M0001",
            f"CLM*E100*150.00***11:B:{freq}*Y*A*Y*Y"]
    if f8 and freq in ("7", "8"):
        body.append("REF*F8*E099")
    body += ["HI*ABK:Z0000", "AMT*D*90.00", "LX*1", "SV1*HC:Z9001*100.00*UN*1***1", "DTP*472*D8*20260110",
             "LX*2", "SV1*HC:Z9002*50.00*UN*1***1", "DTP*472*D8*20260110"]
    n = len(body) + 1 if se_count is None else se_count
    body.append(f"SE*{n}*0001")
    return (isa() + "GS*HC*ZZMCOA*ZZMEDICAID*20260115*1200*7*X*005010X222A1~" + "~".join(body) +
            "~GE*1*7~IEA*1*000000101~").encode()


def test_clean_file_has_no_envelope_findings():
    pf = tokenize(x837(), "f1")
    assert [f.check_id for f in pf.findings] == []
    h, l, fx = map_837p(pf, "ZZ-MCO-A")
    assert len(h) == 1 and len(l) == 2 and h[0]["total_charge"] == sum(x["line_charge"] for x in l)
    assert fx == []


def test_se_count_mismatch_detected_and_segments_still_produced():
    pf = tokenize(x837(se_count=5), "f2")
    assert "ENV-SE-CNT" in [f.check_id for f in pf.findings]
    assert len(pf.segments) > 10


def test_hl_parent_broken():
    pf = tokenize(x837(hl_parent="9"), "f3")
    _, _, fx = map_837p(pf, "ZZ-MCO-A")
    assert "TX-HL-PARENT" in [f.check_id for f in fx]


def test_isa_width_violation_is_a_finding_not_an_exception():
    data = x837().replace(b"ZZMCOA         ", b"ZZMCOA ", 1)
    pf = tokenize(data, "f4")
    assert "ENV-ISA-WIDTH" in [f.check_id for f in pf.findings]


def test_ie_trailer_count():
    data = b"HDR|ZZ-IE|20260101|1\nDTL|P1|M1|A|B|19900101|F|MEDICAID|G1|FULL|20260101|99991231|001|H1|20260101000000\nTRL|2\n"
    raw, rows, fx, ctl = parse_ie(data, "ie1")
    assert {k: ctl[k] for k in ("trailer_count", "detail_count")} == {"trailer_count": 2, "detail_count": 1}
    assert [f.check_id for f in fx] == ["FLAT-TRL-COUNT"]
