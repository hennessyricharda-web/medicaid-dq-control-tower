"""T1 tests reuse the package runtime, so there is one wiring for the tests, the API and the CLI."""
from dqagent.runtime import Runtime


class Slice(Runtime):
    def __init__(self, dsn, **kw):
        kw.setdefault("test_genesis", True)          # explicit: TEST genesis only in tests
        super().__init__(dsn, **kw)

    def units(self, **where):
        with self.cp.as_role("dq_eval_svc") as c:
            q = "SELECT * FROM dq_ctl.units" + (" WHERE " + " AND ".join(f"{k}=%s" for k in where) if where else "")
            return c.execute(q, tuple(where.values())).fetchall()

    def step_up(self, user, unit_keys, verb, qrows=None, maker_decision_id=None, scope=None):
        """What the Tower does: ask the CP for the challenge, then step up at the IdP with it."""
        scope = scope or {"snap": ["AUTH_SNAP"]}.get(user, ["AUTH_MEDICAID"])
        chal = self.decisions.challenge(scope, unit_keys, verb, qrows, maker_decision_id)
        return self.idp.token(user, step_up=True, challenge=chal)

    def govern(self, items, author="maker", approvers=("checker",), rationale="Test change request for the T1 suite"):
        """Part D: propose -> step-up approvals by OTHER people -> apply (reload + re-open affected units)."""
        cr = self.registry.propose(self.state, self.idp.users[author]["emp_id"], rationale, items)
        for a in approvers:
            tok = self.idp.token(a, step_up=True, challenge=self.registry.challenge(cr["cr_id"], "APPROVED"))
            self.registry.decide(cr["cr_id"], "APPROVED", tok, self.idp.users[a]["emp_id"], ["governance:AUTH_RULES"])
        return cr, self.apply_change(cr["cr_id"])

    def effective_item(self, kind, suffix):
        """The current effective artifact as a PUT item to edit (copy)."""
        import copy
        a = self.registry.artifacts(self.state)[f"{self.state}:{suffix}"]
        return {"artifact_id": f"{self.state}:{suffix}", "kind": kind, "op": "PUT", "content": copy.deepcopy(a["content"])}
