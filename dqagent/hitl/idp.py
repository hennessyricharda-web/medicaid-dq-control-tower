"""Local test IdP (T1 ONLY; excluded from any production deployment).

Issues RS256 JWS access tokens with these claims:
  sub, emp_id, name, groups (dq-duty-<duty>-<authority>), auth_time, iat, exp, jti, aud,
  plus `acr` = "urn:dq:stepup" and `nonce` = challenge on a step-up.
It publishes a JWKS so the api and the verifier verify signatures exactly as they would
against the state IdP. The real IdP's claim names come from the state profile (§4.2).
"""
from __future__ import annotations

import time
import uuid

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

AUD = "dqagent-api"
ISS = "https://idp.t1.local"
STEP_UP_ACR = "urn:dq:stepup"


class LocalIdP:
    def __init__(self, users: dict):
        """users: {username: {"sub":..., "emp_id":..., "name":..., "groups":[...]}}"""
        self.users = users
        self._key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.kid = "t1-" + uuid.uuid4().hex[:8]

    def jwks(self) -> dict:
        pub = jwt.algorithms.RSAAlgorithm.to_jwk(self._key.public_key(), as_dict=True)
        pub.update({"kid": self.kid, "use": "sig", "alg": "RS256"})
        return {"keys": [pub]}

    def token(self, username: str, *, step_up: bool = False, challenge: str | None = None, ttl: int = 600,
              auth_time: int | None = None) -> str:
        u = self.users[username]
        now = int(time.time())
        claims = {"iss": ISS, "aud": AUD, "sub": u["sub"], "emp_id": u["emp_id"], "name": u["name"],
                  "groups": u["groups"], "iat": now, "exp": now + ttl, "jti": uuid.uuid4().hex,
                  "auth_time": auth_time if auth_time is not None else now}
        if step_up:
            claims["acr"] = STEP_UP_ACR
            claims["nonce"] = challenge
        return jwt.encode(claims, self._key, algorithm="RS256", headers={"kid": self.kid})


def verify(token: str, jwks: dict, *, max_age_s: int | None = None) -> dict:
    """Verify signature, audience, issuer and expiry. With max_age_s, also enforce auth_time freshness."""
    kid = jwt.get_unverified_header(token).get("kid")
    key = next((k for k in jwks["keys"] if k["kid"] == kid), None)
    if key is None:
        raise PermissionError("unknown signing key")
    pub = jwt.algorithms.RSAAlgorithm.from_jwk(key)
    claims = jwt.decode(token, pub, algorithms=["RS256"], audience=AUD, issuer=ISS)
    if max_age_s is not None and int(time.time()) - int(claims["auth_time"]) > max_age_s:
        raise PermissionError("authentication too old for step-up")
    claims["_kid"] = kid
    return claims
