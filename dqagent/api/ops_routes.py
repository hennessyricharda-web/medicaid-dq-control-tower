"""Operations endpoints (Part F T0: F-01 recovery, R4-1 refusal when the data plane lost the batch).

  POST /api/v1/recovery/batches/{batch_id}/challenge  {reason}                -> {challenge, expires_in_s, step_up_url}
  POST /api/v1/recovery/batches/{batch_id}/abc        {step_up_token, reason} -> {batch_id, abc_rows, evaluated_units}

Both need the operations duty (token group dq-duty-operations AND the roster grant, as /sim/run-next). The challenge is
a random, server-side, single-use row (dq_ctl.recovery_challenges) bound to the batch, the person and sha256(reason),
expiring after STEP_UP_MAX_AGE_S. Recovery needs a step-up whose nonce is that challenge, for the same person; the
database consumes the challenge exactly once (dq_ctl.recovery_challenge_consume), across processes. Recovery is
API-only: there is no CLI path, because only the api holds the duty check and the step-up.

  409 RECOVERY_NOT_APPLICABLE  unknown / unsealed batch, or its ABC is complete
  409 RECOVERY_DATA_LOST       a frozen load of the batch is no longer in the data plane (R4-1): the batch stays held
                               and a RECOVERY_DATA_LOST feed item is opened for the intake duty
"""
from __future__ import annotations

import psycopg
from fastapi import Body, Depends, FastAPI, HTTPException

from dqagent.hitl.decisions import STEP_UP_MAX_AGE_S
from dqagent.hitl.idp import STEP_UP_ACR, verify


def ops_routes(app: FastAPI, services, principal) -> None:
    from dqagent.runtime import RecoveryDataLost, RecoveryRefused, RecoveryTransient

    def operations(p):
        if not any(d.startswith("operations:") for d in p["_duties"]):
            raise HTTPException(403, "operations duty required (token group dq-duty-operations AND roster grant)")

    def reason_of(body) -> str:
        reason = str((body or {}).get("reason") or "")
        if len(reason.strip()) < 10:
            raise HTTPException(422, {"status": "REFUSED", "reason": "a reason of at least 10 characters is required"})
        return reason

    @app.post("/api/v1/recovery/batches/{batch_id}/challenge")
    def recovery_challenge(batch_id: str, body: dict = Body(default={}), p=Depends(principal)):
        operations(p)
        reason = reason_of(body)
        with services.cp.as_role("dq_api") as c:
            ch = c.execute("SELECT dq_ctl.recovery_challenge_issue(%s,%s,%s,%s) AS ch",
                           (batch_id, p["emp_id"], reason, STEP_UP_MAX_AGE_S)).fetchone()["ch"]
        return {"challenge": ch, "expires_in_s": STEP_UP_MAX_AGE_S, "step_up_url": services.idp_url + "/token"}

    @app.post("/api/v1/recovery/batches/{batch_id}/abc")
    def recover_abc(batch_id: str, body: dict = Body(default={}), p=Depends(principal)):
        operations(p)
        reason = reason_of(body)
        tok = body.get("step_up_token")
        if not tok:
            raise HTTPException(428, {"status": "STEP_UP_REQUIRED", "reason": "obtain a step-up over /challenge first"})
        try:
            claims = verify(tok, services.idp.jwks(), max_age_s=STEP_UP_MAX_AGE_S)
        except Exception as e:
            raise HTTPException(401, {"status": "STEP_UP_INVALID", "reason": type(e).__name__}) from e
        if claims.get("acr") != STEP_UP_ACR or claims["emp_id"] != p["emp_id"] or not claims.get("nonce"):
            raise HTTPException(403, {"status": "DENIED", "reason": "step-up not bound to this recovery"})
        try:                                             # single use, bound to batch, person and reason, unexpired
            with services.cp.as_role("dq_api") as c:
                c.execute("SELECT dq_ctl.recovery_challenge_consume(%s,%s,%s,%s)",
                          (claims["nonce"], batch_id, p["emp_id"], reason))
        except psycopg.errors.UniqueViolation as e:
            raise HTTPException(409, {"status": "REFUSED", "reason": "recovery challenge already used"}) from e
        except psycopg.errors.InsufficientPrivilege as e:
            raise HTTPException(403, {"status": "DENIED", "reason": str(e).splitlines()[0]}) from e
        try:
            out = services.recover_batch_abc(batch_id, p["emp_id"], reason=reason)
        except RecoveryDataLost as e:
            raise HTTPException(409, {"status": "RECOVERY_DATA_LOST", "reason": str(e),
                                      "feed_item": getattr(e, "feed_item", None)}) from e
        except RecoveryRefused as e:
            raise HTTPException(409, {"status": "RECOVERY_NOT_APPLICABLE", "reason": str(e)}) from e
        except RecoveryTransient as e:
            raise HTTPException(503, {"status": "RECOVERY_RETRY", "reason": str(e)}) from e
        return {"batch_id": out["batch_id"], "abc_rows": out["abc_rows"], "evaluated_units": out["evaluated_units"]}
