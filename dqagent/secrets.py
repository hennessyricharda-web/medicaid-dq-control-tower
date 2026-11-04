"""Evidence keys (Part F T0, F-15): fail closed outside test and demo.

TEST_KEYS are the labelled literal keys the test and demo deployments use. Every other deployment reads its keys from
the environment through EnvSecrets and raises NotConfiguredError when any is missing; it never falls back to TEST_KEYS.
"""
from __future__ import annotations

import os

from dqagent.deploy import NotConfiguredError, seam

TEST_KEYS = {"K_EV": b"t1-ev-key", "K_FP": b"t1-fp-key", "K_ABC": b"t1-abc-key"}     # test / demo ONLY (labelled)
ENV_KEYS = {"K_EV": "DQ_K_EV", "K_FP": "DQ_K_FP", "K_ABC": "DQ_K_ABC"}


@seam("secrets")
class EnvSecrets:
    """Production side of the secrets seam: the three evidence keys from DQ_K_EV, DQ_K_FP and DQ_K_ABC."""

    def __init__(self, env=None):
        self.env = os.environ if env is None else env

    def keys(self) -> dict:
        missing = [v for v in ENV_KEYS.values() if not self.env.get(v)]
        if missing:
            raise NotConfiguredError(f"evidence keys not configured: set {', '.join(missing)}")
        out = {k: self.env[v].encode() for k, v in ENV_KEYS.items()}
        if any(out[k] == TEST_KEYS[k] for k in out):
            raise NotConfiguredError("a labelled TEST key was supplied through the environment")
        return out


def is_test_keys(keys: dict) -> bool:
    """True when any of `keys` is a labelled test key, given as bytes or as str (Nit 8)."""
    def b(x):
        return x.encode() if isinstance(x, str) else x
    return any(b(keys.get(k)) == v for k, v in TEST_KEYS.items())
