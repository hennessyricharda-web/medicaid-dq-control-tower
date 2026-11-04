"""Verifier (DESIGN §2.1): an independent identity on separate compute.

It writes VERIFIER_ATTESTATION rows that can_release requires:
  * TOKEN: re-verifies every decision token's JWS against the ARCHIVED JWKS. Signature,
    aud, iss, step-up acr and the challenge nonce must match the stored challenge.
  * DECISION (SEC-W1-3): for every decision row, recomputes the challenge from the decision
    rows that cite the same token (verb, seat, maker decision, per-unit round, fingerprint,
    epoch, qset, disposition vector) and requires it to equal the JWS nonce; requires the
    token's emp_id to be the decision's person and auth_time within 5 minutes of the
    decision. A forged decision row therefore needs an IdP-signed step-up over exactly it.
  * PUBLICATION (clause 10): the DP pointer's version and content hash equal the CP record.
  * FINGERPRINT: recomputes the unit fingerprint IN THE DP over rows ≤ max_row_seq and
    compares it with the bound value. A mismatch on a RELEASED unit writes DENY.
At T1 it runs as the dq_verify_svc DB role. In production it is a separate deployment
with its own workload identity.
"""
from __future__ import annotations

import json

import jwt

from dqagent.evidence import dqhmac
from dqagent.hitl.decisions import STEP_UP_MAX_AGE_S, challenge_for
from dqagent.hitl.idp import AUD, ISS, STEP_UP_ACR


class Verifier:
    def __init__(self, cp, dp, sealer, jwks_archive: dict, fp_key: bytes):
        self.cp, self.dp, self.sealer, self.jwks, self.fp_key = cp, dp, sealer, jwks_archive, fp_key

    def _claims(self, jws):
        kid = jwt.get_unverified_header(jws).get("kid")
        key = next(k for k in self.jwks["keys"] if k["kid"] == kid)
        return jwt.decode(jws, jwt.algorithms.RSAAlgorithm.from_jwk(key), algorithms=["RS256"],
                          audience=AUD, issuer=ISS, options={"verify_exp": False})

    def _verify_jws(self, row) -> tuple[bool, str]:
        try:
            claims = self._claims(row["jws"])     # archived JWKS; exp at use time is checked by the api
            if claims["jti"] != row["jti"] or claims["emp_id"] != row["person_id"]:
                return False, "claims do not match the stored token row"
            if row["step_up"] and (claims.get("acr") != STEP_UP_ACR or claims.get("nonce") != row["challenge"]):
                return False, "step-up claims do not bind the stored challenge"
            return True, "ok"
        except Exception as e:  # noqa: BLE001
            return False, f"{type(e).__name__}: {e}"

    def attest_tokens(self) -> int:
        n = 0
        with self.cp.as_role("dq_verify_svc") as c:
            rows = c.execute("SELECT * FROM dq_ctl.decision_tokens t WHERE NOT EXISTS (SELECT 1 FROM dq_ctl.attestations a "
                             "WHERE a.subject_type='TOKEN' AND a.subject_id=t.token_id)").fetchall()
            for r in rows:
                ok, why = self._verify_jws(r)
                c.execute("INSERT INTO dq_ctl.attestations(subject_type,subject_id,verifier_identity,result,detail) "
                          "VALUES ('TOKEN',%s,'dq_verify_svc',%s,%s::jsonb)",
                          (r["token_id"], "VALID" if ok else "INVALID", json.dumps({"why": why})))
                n += 1
        return n

    def _decision_ok(self, c, d) -> tuple[bool, str]:
        t = c.execute("SELECT * FROM dq_ctl.decision_tokens WHERE token_id=%s", (d["token_id"],)).fetchone()
        if not t or not t["step_up"]:
            return False, "decision is not backed by a stored step-up token"
        try:
            cl = self._claims(t["jws"])
        except Exception as e:  # noqa: BLE001
            return False, f"JWS invalid: {type(e).__name__}"
        if cl.get("acr") != STEP_UP_ACR:
            return False, "token is not a step-up"
        if cl.get("emp_id") != d["person_id"] or t["person_id"] != d["person_id"]:
            return False, "token person differs from decision person"
        if (d["created_at"] - t["auth_time"]).total_seconds() > STEP_UP_MAX_AGE_S + 5:
            return False, "step-up older than 5 minutes at decision time"
        sib = c.execute("SELECT * FROM dq_ctl.decisions WHERE token_id=%s", (d["token_id"],)).fetchall()
        if len({(s["decision"], s["seat"], s["maker_decision_id"], s["person_id"]) for s in sib}) != 1:
            return False, "one token cites decisions with different verbs / seats"
        units = [{"unit_key": s["unit_key"], "round": s["round"], "fingerprint": s["bound_fingerprint"],
                  "fp_epoch": s["fp_epoch"], "qset_hash": s["qset_hash"],
                  "disposition_vector": s["disposition_vector"]} for s in sib]
        chal = challenge_for(units, verb=d["decision"], seat=d["seat"], maker_decision_id=d["maker_decision_id"])
        if chal != cl.get("nonce") or chal != t["challenge"]:
            return False, "recomputed challenge does not match the signed nonce"
        if d["seat"] == "CHECKER":
            mk = c.execute("SELECT decision_id, decision FROM dq_ctl.decisions WHERE unit_key=%s AND round=%s "
                           "AND seat='MAKER'", (d["unit_key"], d["round"])).fetchone()
            if not mk or mk["decision_id"] != d["maker_decision_id"] or mk["decision"] != d["decision"]:
                return False, "checker does not confirm this round's maker decision"
        return True, "ok"

    def attest_decisions(self) -> int:
        n = 0
        with self.cp.as_role("dq_verify_svc") as c:
            rows = c.execute("SELECT * FROM dq_ctl.decisions d WHERE NOT EXISTS (SELECT 1 FROM dq_ctl.attestations a "
                             "WHERE a.subject_type='DECISION' AND a.subject_id=d.decision_id)").fetchall()
            for d in rows:
                ok, why = self._decision_ok(c, d)
                c.execute("INSERT INTO dq_ctl.attestations(subject_type,subject_id,verifier_identity,result,detail) "
                          "VALUES ('DECISION',%s,'dq_verify_svc',%s,%s::jsonb)",
                          (d["decision_id"], "VALID" if ok else "INVALID", json.dumps({"why": why})))
                n += 1
        return n

    def attest_publication(self, dp_name: str) -> bool:
        """Clause 10: the live DP pointer matches the CP's latest manifest record (version AND content)."""
        ptr = self.dp.fetchall("SELECT version, content_hash FROM dq_rel.manifest_pointer WHERE id=1")
        with self.cp.as_role("dq_verify_svc") as c:
            last = c.execute("SELECT version, content_hash FROM dq_ctl.manifest_versions WHERE dp=%s "
                             "ORDER BY version DESC LIMIT 1", (dp_name,)).fetchone()
            v, h = (ptr[0][0], ptr[0][1]) if ptr else (0, None)
            ok = (last is None and v == 0) or (last is not None and last["version"] == v and last["content_hash"] == h)
            c.execute("INSERT INTO dq_ctl.attestations(subject_type,subject_id,verifier_identity,result,detail) "
                      "VALUES ('PUBLICATION',%s,'dq_verify_svc',%s,%s::jsonb)",
                      (dp_name, "VALID" if ok else "INVALID",
                       json.dumps({"dp_version": v, "cp_version": last["version"] if last else 0})))
        return ok

    def fingerprint_ok(self, unit: dict) -> bool:
        members = [unit["node"]] + [r for r, c in self.sealer.contracts.items()
                                    if c.get("unit_from_parent", {}).get("relation") == unit["node"]]
        lanes, n, mx = self.sealer.unit_fingerprint(unit["batch_id"], unit["unit_key"], members, unit["max_row_seq"])
        # Rows above max_row_seq must not exist for this unit (late-row fence).
        extra = sum(self.dp.fetchall(f"SELECT count(*) FROM {rel} WHERE _dq_unit_key = ? AND _dq_row_seq > ?",
                                     [unit["unit_key"], unit["max_row_seq"]])[0][0] for rel in members)
        fp = dqhmac.bind(self.fp_key, lanes, n, unit["batch_id"], unit["unit_key"], mx, unit["fp_epoch"])
        return fp == unit["fingerprint"] and extra == 0

    def attest_fingerprints(self, states=("EVALUATING", "PENDING_RELEASE", "RELEASED")) -> dict:
        out = {"valid": 0, "invalid": 0, "denied": 0}
        with self.cp.as_role("dq_verify_svc") as c:
            units = c.execute("SELECT * FROM dq_ctl.units WHERE state = ANY(%s)", (list(states),)).fetchall()
            for u in units:
                ok = self.fingerprint_ok(u)
                c.execute("INSERT INTO dq_ctl.attestations(subject_type,subject_id,verifier_identity,result,detail) "
                          "VALUES ('FINGERPRINT',%s,'dq_verify_svc',%s,%s::jsonb)",
                          (u["unit_key"], "VALID" if ok else "INVALID",
                           json.dumps({"state_version": u["state_version"], "fingerprint": u["fingerprint"]})))
                out["valid" if ok else "invalid"] += 1
                if not ok and u["state"] == "RELEASED":
                    c.execute("INSERT INTO dq_ctl.deny(unit_key,reason,mechanism) VALUES (%s,%s,'REFINGERPRINT')",
                              (u["unit_key"], "released content no longer matches its sealed fingerprint"))
                    out["denied"] += 1
        return out
