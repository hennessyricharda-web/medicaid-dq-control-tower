"""Maker / checker decisions (DESIGN §2.4, §7, §8).

  * EVERY decision (maker and checker) is a step-up token whose `nonce` equals a challenge
    computed over exactly what is being decided (SEC-W1-4/10):
        verb, seat, maker_decision_id (checker), and per unit
        (unit_key, round = HELD state_version, fingerprint, epoch, qset_hash, disposition vector).
    The session bearer is never stored and never authorises a decision.
  * The api verifies the JWS (signature, aud, iss, exp, auth_time ≤ 5 min). The JWS is stored
    with jti (single use: a replay is a 409, not a 500).
  * Effective duty = token group claim AND an active CP ROSTER grant. Every approver holds the
    release duty for EVERY authority of the unit; when a pre-split (AUTH_INTAKE) hop of the
    batch is non-PASS, at least one approver must also hold the INTAKE duty (clause 7).
  * SoD: a checker is a different person; the checker confirms the maker's decision id and verb
    and the SAME quarantine set.
  * Completion moves HELD → PENDING_RELEASE. Only the publisher reaches RELEASED, and only if
    can_release() holds — which re-checks every decision against its verifier attestation.
  * Part E §7: the explanation shown to the person is bound to the decision. The api takes the
    explanation_ref {facts_hash, text_hash} from the challenge request, binds it to that step-up challenge
    (server side, by nonce) and passes it to decide(), which validates its shape and stores it in
    decisions.explanation_ref. A ref may be one object for every unit of the decision or a map {unit_key: ref}.
"""
from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import datetime, timezone

import psycopg

from dqagent.hitl.idp import STEP_UP_ACR, verify

DUTY = "release"
INTAKE = "AUTH_INTAKE"
STEP_UP_MAX_AGE_S = 300


class Denied(Exception):
    pass


class Stale(Exception):
    pass


class Replay(Exception):
    pass


def duty_name(authority: str) -> str:
    return f"dq-duty-{DUTY}-{authority.removeprefix('AUTH_').lower()}"


def disposition_vector(c, unit_key: str) -> dict:
    rows = c.execute(
        "SELECT DISTINCT ON (rule_id) rule_id, rule_hash, disposition FROM dq_ctl.results "
        "WHERE unit_key=%s ORDER BY rule_id, eval_version DESC, id DESC", (unit_key,)).fetchall()
    return {r["rule_id"]: [r["rule_hash"], r["disposition"]] for r in rows}


_HEX64 = re.compile(r"^[0-9a-f]{64}$")


def _ref_ok(ref) -> bool:
    return isinstance(ref, dict) and set(ref) == {"facts_hash", "text_hash"} and \
        all(isinstance(ref[k], str) and _HEX64.match(ref[k]) for k in ref)


def explanation_refs(explanation_ref, unit_keys: list[str]) -> dict[str, dict | None]:
    """{unit_key: {facts_hash, text_hash} | None}. Accepts None, one ref for every unit, or {unit_key: ref}.
    Anything else is refused (Denied): a malformed binding is never silently dropped."""
    if explanation_ref is None:
        return {uk: None for uk in unit_keys}
    if _ref_ok(explanation_ref):
        return {uk: dict(explanation_ref) for uk in unit_keys}
    if isinstance(explanation_ref, dict) and set(explanation_ref) <= set(unit_keys) and \
            all(v is None or _ref_ok(v) for v in explanation_ref.values()):
        return {uk: (dict(explanation_ref[uk]) if explanation_ref.get(uk) else None) for uk in unit_keys}
    raise Denied("explanation_ref is {facts_hash, text_hash} (sha256 hex), or a map unit_key -> that object")


def challenge_for(units: list[dict], *, verb: str, seat: str, maker_decision_id: str | None) -> str:
    """units: [{unit_key, round, fingerprint, fp_epoch, qset_hash, disposition_vector}]."""
    keep = ("unit_key", "round", "fingerprint", "fp_epoch", "qset_hash", "disposition_vector")
    body = {"verb": verb, "seat": seat, "maker_decision_id": maker_decision_id,
            "units": sorted(({k: u[k] for k in keep} for u in units), key=lambda u: u["unit_key"])}
    return hashlib.sha256(json.dumps(body, sort_keys=True, default=str).encode()).hexdigest()


def qset_hash(row_seqs: list[int]) -> str:
    return hashlib.sha256(json.dumps(sorted(int(r) for r in row_seqs)).encode()).hexdigest()


def intake_failed(c, batch_id: str) -> bool:
    """A GATING pre-split hop of the batch did not pass (Part F E-08): the same definition as publisher clause 7
    (dq_ctl.abc_gating_failures). A non-gating row (an empty delivery) never demands the intake duty."""
    return any(r["gating"] and r["disposition"] != "PASS" for r in c.execute(
        "SELECT disposition, gating FROM dq_ctl.intake_verdicts(%s)", (batch_id,)).fetchall())


class DecisionService:
    def __init__(self, cp, jwks_provider, policy, chain_expander=None):
        """chain_expander(unit, row_seqs) -> child row_seqs that must be quarantined with them (CHAIN)."""
        self.cp, self.jwks, self.policy = cp, jwks_provider, policy
        self.chain_expander = chain_expander or (lambda u, rs: [])
        self.state_of = None          # set by the runtime: () -> state code (for the tier lookup of a vetoed unit)

    def _effective_duties(self, c, claims) -> set[str]:
        if "_claim_names" in claims:                      # group overage: fail closed
            raise Denied("group overage in token; duties cannot be determined")
        roster = {duty_name(r["authority"]) for r in c.execute(
            "SELECT duty, authority FROM dq_ctl.roster_current WHERE person_id=%s AND duty=%s",
            (claims["emp_id"], DUTY)).fetchall()}
        return set(claims.get("groups", [])) & roster

    def _binding(self, c, unit_key: str, qrows: list[int]) -> dict:
        u = c.execute("SELECT * FROM dq_ctl.units WHERE unit_key=%s", (unit_key,)).fetchone()
        if u is None:
            raise Denied("unit not visible in your scope")
        return {"unit_key": unit_key, "round": u["state_version"], "fingerprint": u["fingerprint"],
                "fp_epoch": u["fp_epoch"], "qset_hash": qset_hash(qrows),
                "disposition_vector": disposition_vector(c, unit_key), "_unit": u}

    def required(self, mode: str) -> dict:
        return self.policy["approvals"][mode]

    def _mode_of(self, c, unit_key) -> str:
        """The mode the unit was HELD at. A VETO hold carries no mode: it is recomputed from stored rows exactly as
        the publisher does (vetoed -> at least H_IN). Anything unreadable fails closed to H_IN_2."""
        ev = c.execute("SELECT via, payload FROM dq_ctl.unit_events WHERE unit_key=%s AND to_state='HELD' "
                       "ORDER BY state_version DESC LIMIT 1", (unit_key,)).fetchone()
        if not ev:
            return "H_IN_2"
        m = (ev["payload"] or {}).get("mode")
        if m:
            return m
        if ev["via"] == "VETO" and self.state_of is not None:
            from dqagent.hitl.modes import effective_mode
            with self.cp.as_role("dq_publish_svc") as pc:          # read-only: the publisher's view of every hop
                u = pc.execute("SELECT * FROM dq_ctl.units WHERE unit_key=%s", (unit_key,)).fetchone()
                if u:
                    return effective_mode(pc, self.policy, u, self.state_of())[0]
        return "H_IN_2"

    def _maker(self, c, unit_key, rnd):
        return c.execute("SELECT d.*, qp.row_seqs FROM dq_ctl.decisions d JOIN dq_ctl.quarantine_proposals qp "
                         "USING (decision_id) WHERE d.unit_key=%s AND d.round=%s AND d.seat='MAKER'",
                         (unit_key, rnd)).fetchone()

    # -- the challenge the Tower sends to the IdP step-up ---------------------------------
    def challenge(self, scope, unit_keys: list[str], verb: str, qrows: dict[str, list[int]] | None = None,
                  maker_decision_id: str | None = None) -> str:
        qrows = qrows or {}
        with self.cp.as_role("dq_api", scope) as c:
            b = [self._binding(c, uk, qrows.get(uk, [])) for uk in unit_keys]
        seat = "CHECKER" if maker_decision_id else "MAKER"
        return challenge_for(b, verb=verb, seat=seat, maker_decision_id=maker_decision_id)

    def decide(self, scope, step_up_token: str, unit_keys: list[str], decision: str, rationale: str,
               expected_versions: dict[str, int], bound_fps: dict[str, str],
               qrows: dict[str, list[int]] | None = None, maker_decision_id: str | None = None,
               session_person: str | None = None, explanation_ref=None) -> dict:
        """One decision by one person over one or more HELD units. Maker when maker_decision_id is None.
        session_person (api): the step-up must be the SAME person as the authenticated session.
        explanation_ref: the {facts_hash, text_hash} the api bound to this step-up's challenge; stored on the decision."""
        qrows = qrows or {}
        refs = explanation_refs(explanation_ref, unit_keys)
        if not step_up_token:
            raise Denied("every decision requires a challenge-bound step-up token")
        claims = verify(step_up_token, self.jwks(), max_age_s=STEP_UP_MAX_AGE_S)
        if claims.get("acr") != STEP_UP_ACR:
            raise Denied("decision requires a step-up authentication")
        if session_person is not None and claims["emp_id"] != session_person:
            raise Denied("step-up identity differs from the signed-in user")
        seat = "CHECKER" if maker_decision_id else "MAKER"
        try:
            return self._decide(scope, claims, step_up_token, unit_keys, decision, rationale,
                                expected_versions, bound_fps, qrows, maker_decision_id, seat, refs)
        except psycopg.errors.UniqueViolation as e:
            raise Replay(f"token or seat already used: {e.diag.constraint_name}") from e

    def _decide(self, scope, claims, tok, unit_keys, decision, rationale, expected_versions, bound_fps,
                qrows, maker_decision_id, seat, refs=None):
        refs = refs or {uk: None for uk in unit_keys}
        with self.cp.as_role("dq_api", scope) as c:
            duties = self._effective_duties(c, claims)
            bindings = []
            for uk in unit_keys:
                u = c.execute("SELECT * FROM dq_ctl.units WHERE unit_key=%s", (uk,)).fetchone()
                if u is None:
                    raise Denied("unit not visible in your scope")
                if seat == "CHECKER":            # checker confirms the maker's proposal, never their own
                    # SoD (Part C F7): a person who proposed or approved a live known issue on a rule this unit
                    # does not pass cannot be its independent second person
                    ki = c.execute(
                        "SELECT m.memory_id FROM dq_ctl.agent_memory_current m WHERE m.kind='KNOWN_ISSUE' "
                        "AND m.status='APPROVED' AND m.expires_at > now() AND %s IN (m.proposed_by, m.approved_by) "
                        "AND m.scope_id IN (SELECT l.rule_id FROM (SELECT DISTINCT ON (rule_id) rule_id, disposition "
                        "FROM dq_ctl.results WHERE unit_key=%s ORDER BY rule_id, eval_version DESC, id DESC) l "
                        "WHERE l.disposition NOT IN ('PASS','NOT_IN_SCOPE','NOT_APPLICABLE','STATE_WAIVED'))",
                        (claims["emp_id"], uk)).fetchone()
                    if ki:
                        raise Denied("you proposed or approved a known issue on a failing rule of this unit; "
                                     "you cannot be its checker (separation of duties)")
                    mk = self._maker(c, uk, u["state_version"])
                    if not mk or mk["decision_id"] != maker_decision_id:
                        raise Denied("maker_decision_id is not the current round's MAKER decision for this unit")
                    if mk["decision"] != decision:
                        raise Denied("checker must confirm the maker's verb")
                    qrows = {**qrows, uk: list(mk["row_seqs"] or [])}
                bindings.append({**self._binding(c, uk, qrows.get(uk, [])), "explanation_ref": refs.get(uk)})
            for b in bindings:
                u = b["_unit"]
                if u["state"] != "HELD":
                    raise Stale(f"{u['unit_key']} is {u['state']}, not HELD")
                if u["state_version"] != expected_versions.get(u["unit_key"]):
                    raise Stale(f"{u['unit_key']} changed (state_version {u['state_version']})")
                if u["fingerprint"] != bound_fps.get(u["unit_key"]):
                    raise Stale(f"{u['unit_key']} evidence changed (fingerprint)")
                for auth in u["authorities"]:
                    if duty_name(auth) not in duties:
                        raise Denied(f"missing effective duty {duty_name(auth)} (token claim AND roster required)")
            chal = challenge_for(bindings, verb=decision, seat=seat, maker_decision_id=maker_decision_id)
            if claims.get("nonce") != chal:
                raise Denied("step-up token is not bound to this decision (challenge mismatch)")
            token_id = "T" + uuid.uuid4().hex[:12]
            c.execute("INSERT INTO dq_ctl.decision_tokens(token_id,person_id,jws,jti,auth_time,kid,challenge,step_up) "
                      "VALUES (%s,%s,%s,%s,%s,%s,%s,true)",
                      (token_id, claims["emp_id"], tok, claims["jti"],
                       datetime.fromtimestamp(claims["auth_time"], tz=timezone.utc), claims["_kid"], chal))
            results = []
            for b in bindings:
                u = b["_unit"]
                need = self.required(self._mode_of(c, u["unit_key"]))
                prior = c.execute("SELECT person_id, seat, authority FROM dq_ctl.decisions WHERE unit_key=%s AND round=%s",
                                  (u["unit_key"], u["state_version"])).fetchall()
                if any(p["person_id"] == claims["emp_id"] for p in prior):
                    raise Denied("separation of duties: the same person cannot approve twice")
                if seat == "MAKER" and prior:
                    raise Denied("a maker decision already exists; confirm it as checker")
                required = set(u["authorities"]) | ({INTAKE} if intake_failed(c, u["batch_id"]) else set())
                covers = sorted(a for a in required if duty_name(a) in duties)
                did = "D" + uuid.uuid4().hex[:12]
                c.execute("INSERT INTO dq_ctl.decisions(decision_id,unit_key,round,seat,person_id,authority,decision,"
                          "rationale,token_id,bound_fingerprint,fp_epoch,qset_hash,disposition_vector,maker_decision_id,"
                          "explanation_ref) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s::jsonb)",
                          (did, u["unit_key"], u["state_version"], seat, claims["emp_id"], ",".join(covers),
                           decision, rationale, token_id, b["fingerprint"], b["fp_epoch"], b["qset_hash"],
                           json.dumps(b["disposition_vector"]), maker_decision_id,
                           json.dumps(b["explanation_ref"]) if b.get("explanation_ref") else None))
                proposed = sorted(int(r) for r in qrows.get(u["unit_key"], []))
                if seat == "MAKER":
                    c.execute("INSERT INTO dq_ctl.quarantine_proposals(decision_id,row_seqs,qset_hash) VALUES (%s,%s::jsonb,%s)",
                              (did, json.dumps(proposed), b["qset_hash"]))
                covered = set(covers)
                for p in prior:
                    covered |= set(filter(None, (p["authority"] or "").split(",")))
                have = len(prior) + 1
                status = "AWAITING_CHECKER"
                complete = have >= max(1, need["persons"]) and required <= covered
                if decision == "REJECT" and complete:
                    self.cp.transition(c, u["unit_key"], u["state_version"], "REJECTED", "REJECT",
                                       claims["emp_id"], {"decision_id": did})
                    status = "APPLIED"
                elif complete:
                    for rs in proposed:
                        c.execute("INSERT INTO dq_ctl.quarantine(unit_key,row_seq,scope,decision_id) VALUES (%s,%s,'ROW',%s)",
                                  (u["unit_key"], rs, did))
                    for rs in sorted(set(self.chain_expander(u, proposed)) - set(proposed)):
                        c.execute("INSERT INTO dq_ctl.quarantine(unit_key,row_seq,scope,decision_id) VALUES (%s,%s,'CHAIN',%s)",
                                  (u["unit_key"], rs, did))
                    self.cp.transition(c, u["unit_key"], u["state_version"], "PENDING_RELEASE",
                                       "CONFIRM" if seat == "CHECKER" else "DECIDE", claims["emp_id"],
                                       {"decision_id": did, "token_id": token_id, "round": u["state_version"]})
                    status = "APPLIED"
                elif have >= need["persons"] and not required <= covered:
                    status = "AWAITING_COVERAGE"
                results.append({"unit_key": u["unit_key"], "decision_id": did, "seat": seat, "status": status,
                                "required": max(1, need["persons"]), "have": have, "challenge": chal,
                                "missing_duties": sorted(required - covered)})
            return {"token_id": token_id, "results": results}
