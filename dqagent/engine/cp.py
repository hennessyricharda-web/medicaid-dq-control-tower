"""Control-plane client.

Each call to `as_role(role)` opens its OWN connection that logs in AS that service role
(SEC-W1-2). There is no shared connection, no SET ROLE and therefore no RESET ROLE: a request
thread can never observe another thread's role, scope or open transaction. In production
each service is a separate deployment with its own workload identity; at T1 the roles log in
over the local socket.
"""
from __future__ import annotations

import contextlib
import json

import psycopg
from psycopg.rows import dict_row

from dqagent.model.lifecycle import wrapper_for


class CP:
    def __init__(self, dsn: dict):
        self.dsn = dsn

    def connect_as(self, role: str, trace: bool = True):
        """A connection that logs in AS `role`. Part F P1-1: it also carries the session settings dq.trace_id,
        dq.span_id (the current trace root / span, when there is one) and dq.code_sha256 (the running code version),
        which the correlation columns take as their DEFAULT. Ids are regex-checked before they are passed."""
        kw = {**self.dsn, "user": role}
        if trace:
            from dqagent.obs.tracer import session_options
            opts = session_options()
            if opts:
                kw["options"] = (kw.get("options", "") + " " + opts).strip()
        return psycopg.connect(**kw, row_factory=dict_row)

    @contextlib.contextmanager
    def as_role(self, role: str, scope: list[str] | None = None, trace: bool = True):
        """One transaction, on a dedicated connection, as `role`. Commits on success."""
        c = self.connect_as(role, trace=trace)
        try:
            if scope is not None:
                c.execute("SELECT dq_ctl.set_scope(%s)", (list(scope),))
            yield c
            c.commit()
        except Exception:
            c.rollback()
            raise
        finally:
            c.close()

    def transition(self, c, unit_key: str, expected: int, to: str, via: str, actor: str, payload: dict | None = None) -> int:
        """Route to the role-bound wrapper for `via`; the database refuses a caller that does not hold it."""
        fn = wrapper_for(via)
        return c.execute(f"SELECT dq_ctl.{fn}(%s,%s,%s,%s,%s,%s::jsonb) AS v",
                         (unit_key, expected, to, via, actor, json.dumps(payload or {}, default=str))).fetchone()["v"]

    def audit(self, c, event: str, digest: str) -> int:
        return c.execute("SELECT dq_ctl.append_audit(%s,%s) AS s", (event, digest)).fetchone()["s"]
