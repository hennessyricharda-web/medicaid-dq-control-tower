"""Known-issue memory: a two-person, step-up, expiring annotation (Part C WS3, red-team F7).

propose  (person A, through the api)  -> PROPOSED
decide   (person B != A, step-up bound to the memory's content hash, release duty on the authority)
         -> APPROVED | REJECTED  (the jti is single-use: a replayed token is refused by the database)

An approved known issue never changes a disposition or a mode. It is shown on a unit card only to the MAKER,
and only after the rule has failed; a checker never sees it (so it cannot nudge the second person).
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json

import psycopg

from dqagent.hitl.decisions import STEP_UP_MAX_AGE_S, Denied, Replay
from dqagent.hitl.idp import STEP_UP_ACR, verify

REASON_CODES = {"UPSTREAM_DEFECT_TICKETED", "SPEC_AMBIGUITY", "VENDOR_FIX_SCHEDULED", "REFERENCE_DATA_LAG",
                "EXPECTED_POLICY_SHIFT"}


def memory_challenge(memory_id: int, content_hash: str, verdict: str) -> str:
    return hashlib.sha256(json.dumps({"verb": "MEMORY_DECIDE", "memory_id": memory_id,
                                      "content_hash": content_hash, "verdict": verdict},
                                     sort_keys=True).encode()).hexdigest()


class MemoryService:
    def __init__(self, cp, jwks_provider):
        self.cp, self.jwks = cp, jwks_provider

    def propose(self, scope, person: str, rule_id: str, authority: str, reason_code: str, expires: dt.date) -> int:
        if reason_code not in REASON_CODES:
            raise Denied(f"reason_code must be one of {sorted(REASON_CODES)}")
        content = {"rule_id": rule_id, "reason_code": reason_code, "authority": authority,
                   "expires": expires.isoformat()}
        with self.cp.as_role("dq_api", scope) as c:
            mid = c.execute("SELECT dq_ctl.mem_propose_known_issue('RULE', %s, %s, %s::jsonb, %s, %s) AS m",
                            (rule_id, authority, json.dumps(content, sort_keys=True), person,
                             dt.datetime.combine(expires, dt.time(23, 59), tzinfo=dt.timezone.utc))).fetchone()["m"]
            c.execute("SELECT dq_ctl.bb_post(NULL,%s,'MEMORY_REF','RULE',%s,'KNOWN_ISSUE_PROPOSED',%s::jsonb,%s,%s)",
                      (authority, rule_id, json.dumps({"memory_id": str(mid), "rule_id": rule_id,
                                                       "expires": expires.isoformat()}), None, person))
        return mid

    def challenge(self, scope, memory_id: int, verdict: str) -> str:
        with self.cp.as_role("dq_api", scope) as c:
            m = c.execute("SELECT content_hash FROM dq_ctl.agent_memory WHERE memory_id=%s AND kind='KNOWN_ISSUE'",
                          (memory_id,)).fetchone()
        if not m:
            raise Denied("unknown known-issue in your scope")
        return memory_challenge(memory_id, m["content_hash"], verdict)

    def decide(self, scope, duties: list[str], session_person: str, memory_id: int, verdict: str, step_up_token: str):
        if not step_up_token:
            raise Denied("a known-issue decision requires a challenge-bound step-up token")
        claims = verify(step_up_token, self.jwks(), max_age_s=STEP_UP_MAX_AGE_S)
        if claims.get("acr") != STEP_UP_ACR:
            raise Denied("decision requires a step-up authentication")
        if claims["emp_id"] != session_person:
            raise Denied("step-up identity differs from the signed-in user")
        with self.cp.as_role("dq_api", scope) as c:
            m = c.execute("SELECT * FROM dq_ctl.agent_memory WHERE memory_id=%s AND kind='KNOWN_ISSUE'",
                          (memory_id,)).fetchone()
            if not m:
                raise Denied("unknown known-issue in your scope")
            if f"release:{m['authority']}" not in duties:
                raise Denied(f"missing effective release duty on {m['authority']}")
            if claims.get("nonce") != memory_challenge(memory_id, m["content_hash"], verdict):
                raise Denied("step-up token is not bound to this memory decision (challenge mismatch)")
            try:
                c.execute("SELECT dq_ctl.mem_decide_known_issue(%s,%s,%s,%s)",
                          (memory_id, claims["emp_id"], claims["jti"], verdict))
            except psycopg.errors.CheckViolation as e:
                raise Denied(str(e).splitlines()[0]) from e
            except psycopg.errors.UniqueViolation as e:
                raise Replay(str(e).splitlines()[0]) from e
        return {"memory_id": memory_id, "status": verdict}

    @staticmethod
    def for_maker_card(c, rule_ids: list[str]) -> list[dict]:
        if not rule_ids:
            return []
        rows = c.execute(
            "SELECT memory_id, scope_id, content, expires_at, proposed_by, approved_by FROM dq_ctl.agent_memory_current "
            "WHERE kind='KNOWN_ISSUE' AND status='APPROVED' AND expires_at > now() AND scope_id = ANY(%s) "
            "ORDER BY memory_id", (rule_ids,)).fetchall()
        return [{"memory_id": r["memory_id"], "rule_id": r["scope_id"], "reason_code": r["content"]["reason_code"],
                 "expires": r["expires_at"].date().isoformat(), "proposed_by": r["proposed_by"],
                 "approved_by": r["approved_by"], "x_effect": "annotation only; never changes a disposition or mode"}
                for r in rows]
