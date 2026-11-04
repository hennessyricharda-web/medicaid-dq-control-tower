"""Small hand-built W1 fixtures (orchestrator-authored, for mechanism tests).

These are NOT the synthetic seat's data. The separately authored synthetic run plus its
hidden ledger are used only for the blind detection evaluation.
"""
from __future__ import annotations


def isa(sender: str, isa13: str) -> str:
    s = (f"ISA*00*          *00*          *ZZ*{sender:<15}*ZZ*{'ZZMEDICAID':<15}*260115*1200*^*00501*"
         f"{isa13}*0*T*:~")
    assert len(s) == 106
    return s


_SEQ = __import__("itertools").count(1)


def ie_file(rows: list[list[str]], trailer: int | None = None, seq: int | None = None) -> bytes:
    """Each call is a NEW extract (fresh HDR file_seq) unless `seq` is given: a repeated seq is a duplicate file."""
    lines = [f"HDR|ZZ-IE|20260101|{seq if seq is not None else next(_SEQ)}"] + ["DTL|" + "|".join(r) for r in rows]
    lines.append(f"TRL|{len(rows) if trailer is None else trailer}")
    return ("\n".join(lines) + "\n").encode()


def person(psk, msis, dob="19800101", prog="MEDICAID", start="20250101", end="99991231", seg="FULL"):
    return [psk, msis, "Jane", "Doe", dob, "F", prog, "G1", seg, start, end, "001", "H" + psk, "20260101000000"]


def x837(sender: str, isa13: str, claims: list[dict], gs06="7") -> bytes:
    """claims: {id, member, npi, freq, f8, lines:[(proc, charge, date)], total, pos, dx, adjud (optional; "" omits 2430)}"""
    tx, st = [], 1
    for c in claims:
        body = [f"ST*837*{st:04d}*005010X222A1", "BHT*0019*00*REF*20260115*1200*RP", "HL*1**20*1",
                f"NM1*85*2*CLINIC*****XX*{c.get('npi', '1234567893')}", "HL*2*1*22*0", "SBR*P*18*******MC",
                f"NM1*IL*1*DOE*JANE****MI*{c['member']}"]
        total = c.get("total", sum(x[1] for x in c["lines"]))
        body.append(f"CLM*{c['id']}*{total:.2f}***{c.get('pos', '11')}:B:{c.get('freq', '1')}*Y*A*Y*Y")
        if c.get("f8"):
            body.append(f"REF*F8*{c['f8']}")
        body += [f"HI*ABK:{c.get('dx', 'Z0000')}", f"AMT*D*{c.get('paid', '10.00')}"]
        for i, (proc, ch, dt) in enumerate(c["lines"], start=1):
            body += [f"LX*{i}", f"SV1*HC:{proc}*{ch:.2f}*UN*1***1", f"DTP*472*D8*{dt}"]
            if c.get("adjud", "20260120"):               # 2430: plan adjudication (paid amount, date)
                body += [f"SVD*ZZMCOA*{ch:.2f}*HC:{proc}**1", f"DTP*573*D8*{c.get('adjud', '20260120')}"]
        body.append(f"SE*{len(body) + 1}*{st:04d}")
        tx += body
        st += 1
    return (isa(sender, isa13) + f"GS*HC*{sender}*ZZMEDICAID*20260115*1200*{gs06}*X*005010X222A1~" +
            "~".join(tx) + f"~GE*{len(claims)}*{gs06}~IEA*1*{isa13}~").encode()


def acks(isa13: str, statuses: list[str]) -> bytes:
    rows = ["isa13,gs06,st02,transaction_set,status,error_code"]
    rows += [f"{isa13},7,{i:04d},837,{s}," for i, s in enumerate(statuses, start=1)]
    return ("\n".join(rows) + "\n").encode()
