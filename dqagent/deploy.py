"""Deployment kinds and what each one permits (Part F T0: F-15, F-19 guard, E-06; plan r3 §F-15 and R4-2, R4-3).

A deployment is one of DEPLOYMENTS. `test` and `demo` may use labelled test keys, the local IdP, an in-memory data
plane and the scripted / recorded LLM transports. `pilot` and `production` fail closed instead: keys come from the
environment, production needs an OIDC IdP (none exists yet, L-16, so a production runtime never starts), the data plane
is a file, and only the `none` (template) and `http` transports are allowed.

The deployment a runtime STATES is checked against the deployment BOUND in the control plane
(dq_ctl.deployment_binding, written only by dq_ctl.bind_deployment, which derives the genesis kind itself: a TEST
genesis binds test | demo, a GOVERNED genesis binds pilot | production). A control plane with no binding row reads as
`production` (fail closed).
"""
from __future__ import annotations

import os

DEPLOYMENTS = ("test", "demo", "pilot", "production")
TEST_DEPLOYMENTS = ("test", "demo")            # labelled test keys, local IdP, :memory: data plane, test transports
DEFAULT_DEPLOYMENT = "production"              # nothing stated -> the strictest deployment
ENV_VAR = "DQ_DEPLOYMENT"

# R4-3: LLM transports allowed per deployment. Anything not listed (e.g. `fixed`, a future transport, or scripted /
# recorded under pilot or production, or `fixed` outside test) is refused with NotConfiguredError.
TRANSPORTS = {
    "test": frozenset({"none", "scripted", "recorded", "http", "fixed"}),   # fixed: a test-only helper (N-T0-4)
    "demo": frozenset({"none", "scripted", "recorded", "http"}),
    "pilot": frozenset({"none", "http"}),
    "production": frozenset({"none", "http"}),
}

SEAMS: dict[str, list[str]] = {}               # seam id -> qualified names of the implementations marked with @seam


class NotConfiguredError(RuntimeError):
    """A deployment that must fail closed is missing something it needs (keys, an IdP, a data-plane file, an allowed
    transport). Never caught to fall back to a test implementation."""


def seam(seam_id: str):
    """No-op marker: registers the decorated class or function as an implementation of `seam_id` (E1 seam manifest)."""
    def mark(obj):
        SEAMS.setdefault(seam_id, []).append(f"{obj.__module__}.{obj.__qualname__}")
        return obj
    return mark


def resolve(explicit: str | None = None) -> str:
    """The deployment: the explicit argument, else $DQ_DEPLOYMENT, else `production`. An unknown name raises."""
    d = explicit if explicit is not None else (os.environ.get(ENV_VAR) or DEFAULT_DEPLOYMENT)
    if d not in DEPLOYMENTS:
        raise NotConfiguredError(f"unknown deployment {d!r}; one of {', '.join(DEPLOYMENTS)}")
    return d


def is_test(deployment: str) -> bool:
    return resolve(deployment) in TEST_DEPLOYMENTS


def allowed_transports(deployment: str) -> frozenset[str]:
    """R4-3: the LLM transports a deployment may use."""
    return TRANSPORTS[resolve(deployment)]


def require_transport(transport: str, deployment: str) -> str:
    """Return `transport` when the deployment allows it; raise NotConfiguredError otherwise."""
    d = resolve(deployment)
    if transport not in TRANSPORTS[d]:
        raise NotConfiguredError(f"LLM transport {transport!r} is not allowed in a {d} deployment "
                                 f"(allowed: {', '.join(sorted(TRANSPORTS[d]))})")
    return transport


IDP_KINDS = ("LOCAL", "OIDC")


def label(deployment: str, idp_kind: str | None) -> str:
    """What the Tower shows for a binding. A pilot on the local IdP is never presented as a real pilot."""
    if deployment == "pilot":
        return "Pilot" if idp_kind == "OIDC" else "Pilot (local IdP \u2013 not production)"
    if deployment == "production":
        return "Production" if idp_kind == "OIDC" else "Production (no OIDC IdP: refused)"
    return {"test": "Test (local IdP)", "demo": "Demo (local IdP)"}.get(deployment, deployment)


def bound(c) -> dict:
    """The control plane's binding row {genesis_kind, deployment, idp_kind, at, label}, read on connection `c` (a role
    with SELECT on dq_ctl.deployment_binding). No row reads as production (R4-2: fail closed)."""
    r = c.execute("SELECT genesis_kind, deployment, idp_kind, at FROM dq_ctl.deployment_binding").fetchone()
    if r is None:
        return {"genesis_kind": None, "deployment": DEFAULT_DEPLOYMENT, "idp_kind": None, "at": None, "bound": False,
                "label": "Not bound (read as production)"}
    r = dict(r)
    return {**r, "bound": True, "label": label(r["deployment"], r["idp_kind"])}
