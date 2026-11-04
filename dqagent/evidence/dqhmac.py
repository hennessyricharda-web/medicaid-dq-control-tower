"""DQ_HMAC: the one keyed function used for tokens, fingerprints and ABC hash totals (DESIGN §5.2, §9).

Canonical construction, identical on every data plane (the cross-platform known-answer
test in the T2 kits checks this):

    msg   = b"".join(f"{len(b)}:".encode() + b for b in (utf8(field) for field in fields))
    token = HMAC-SHA256(key, msg).hexdigest()

Length prefixes remove concatenation ambiguity: ("AUTH_SNAP", "X1") and
("AUTH_SNAPX", "1") give different tokens. NULL is encoded as the sentinel b"\\x00NULL"
so it never collides with the string "NULL".

Fingerprint lanes: the first 240 bits of the per-row token, as 4 x 60-bit integers.
Each lane is summed in DECIMAL(38) inside the data plane, so no overflow occurs below
2**66 rows. Because the per-row value is keyed, crafting colliding row sets (Wagner's
generalized birthday attack) needs the key.
"""
from __future__ import annotations

import hashlib
import hmac
from typing import Iterable, Sequence

NULL_SENTINEL = b"\x00NULL"


def _enc(field) -> bytes:
    if field is None:
        b = NULL_SENTINEL
    elif isinstance(field, bytes):
        b = field
    else:
        b = str(field).encode("utf-8")
    return f"{len(b)}:".encode() + b


def canonical(fields: Sequence) -> bytes:
    return b"".join(_enc(f) for f in fields)


def dq_hmac(key: bytes, fields: Sequence) -> str:
    return hmac.new(key, canonical(fields), hashlib.sha256).hexdigest()


def lanes(token_hex: str) -> tuple[int, int, int, int]:
    return tuple(int(token_hex[i * 15:(i + 1) * 15], 16) for i in range(4))  # 4 x 60 bits


def fold(rows: Iterable[Sequence], key: bytes) -> tuple[tuple[int, int, int, int], int]:
    """Pure-Python oracle for the in-DP fingerprint: summed lanes and row count."""
    acc = [0, 0, 0, 0]
    n = 0
    for r in rows:
        for i, v in enumerate(lanes(dq_hmac(key, r))):
            acc[i] += v
        n += 1
    return tuple(acc), n


def bind(key: bytes, lanes_sum, count: int, batch_id: str, unit_key: str, max_row_seq: int, epoch: int) -> str:
    """CP-side binding of the DP digest. Recomputing it is pure; nothing is written."""
    return dq_hmac(key, [*lanes_sum, count, batch_id, unit_key, max_row_seq, epoch])


# Known-answer vectors (also emitted into every platform's T2 kit).
KAT_KEY = b"dq-kat-key-0001"
KAT = [
    (["AUTH_MEDICAID", "MEDICAID", "R-ELG-001", "202601", "M000123"], None),
    (["AUTH_SNAP", "X1"], None),
    (["AUTH_SNAPX", "1"], None),
    ([None, "", "NULL"], None),
]
KAT = [(f, dq_hmac(KAT_KEY, f)) for f, _ in KAT]
