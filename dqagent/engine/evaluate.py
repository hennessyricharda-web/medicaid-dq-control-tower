"""Evaluation (DESIGN §5): rules pushed down to the data plane, results to the CP.

Per gated root relation and batch:
  * dependency check (WAIT edges): unit → PENDING_DEPENDENCY when an intersecting depended
    unit is non-terminal. The dependency vector (unit keys + state_versions) is recorded.
  * each rule evaluates per unit IN THE DP. Only counts and keyed sample tokens return.
  * dispositions → results (CP, dq_eval_svc); the mode is computed deterministically.
  * transition SEALED → EVALUATING → (HELD | PENDING_DEPENDENCY), or remains EVALUATING for
    mode A, which the publisher releases (EVALUATING → RELEASED via PUBLISH).
  * Part E §1: the mode is capped from below by the agent's autonomy tier and RELEASE entitlement (read from the
    database for the agent whose seat publishes). Mode H_ON (an L3 tier) opens a veto window
    (dq_ctl.veto_windows; the DATABASE clock stamps opened_at) and the unit stays EVALUATING; the publisher
    releases it only after the window by the database clock, with no veto. A dependency wait that exceeded
    max_wait escalates to _max(mode, H_IN) (C2), and the escalation is persisted on the evaluation event.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import re

import yaml

from dqagent.obs import tracer
from dqagent.hitl.modes import ORDER, abc_missing, autonomy_for, ceiling_label, mode as compute_mode, was_vetoed
from dqagent.rules.ir import EMITTERS
from dqagent.model.lifecycle import TERMINAL
from dqagent.model.outcome import Disposition as D

FLAT_RELATIONS = {"bronze.ie_segment"}
X12_RULE_PREFIXES = ("ENV-", "TX-")
FLAT_RULE_PREFIXES = ("FLAT-",)
TERMINAL_STATES = {s.value for s in TERMINAL}


def npi_valid(npi) -> bool:
    if npi is None or len(npi) != 10 or not npi.isdigit():
        return False
    digits = [int(c) for c in "80840" + npi]
    total = 0
    for i, d in enumerate(reversed(digits)):
        if i % 2 == 1:
            d *= 2
            d = d - 9 if d > 9 else d
        total += d
    return total % 10 == 0


def iso_duration_s(v: str | None, default: str = "P2D") -> int:
    """PnDTnHnM (the subset the harness uses) -> seconds."""
    m = re.fullmatch(r"P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?)?", v or default)
    if not m:
        raise ValueError(f"unsupported ISO-8601 duration {v!r}")
    d, h, mi = (int(x or 0) for x in m.groups())
    return d * 86400 + h * 3600 + mi * 60


def load_rules(path) -> list[dict]:
    return yaml.safe_load(open(path))["rules"]


def relation_authority(contracts: dict, rel: str) -> str:
    """The authority a relation-level aggregate (a measure result, a RELEASE_SUMMARY board post) is filed under,
    when the units of one (batch_id, node) can carry DIFFERENT `authorities` (contracts_w1.yaml :13:
    `bronze.ie_segment` splits one batch's units across AUTH_MEDICAID / AUTH_CHIP / AUTH_SNAP / AUTH_TANF by
    program): a relation's own single authority, else its pre-split authority (AUTH_INTAKE). This mirrors
    `Runtime._relation_authority` (runtime.py:683-693) exactly, so the two independent paths that file a
    relation/batch-level aggregate (the agent's seats here in `dqagent/engine/evaluate.py`, and the monitor /
    parallel-run paths in `runtime.py`) agree on one deterministic answer, instead of reading back an unordered
    `authorities[1] ... LIMIT 1` row from `dq_ctl.units` (a race: Postgres gives no ordering guarantee over which
    physical row an unordered LIMIT 1 returns when several units of the same batch/node carry different
    authorities arrays)."""
    ct = contracts.get(rel) or {}
    parent = (ct.get("unit_from_parent") or {}).get("relation")
    if parent:
        ct = contracts.get(parent) or ct
    auths = sorted({a for v in (ct.get("authorities_by_program") or {}).values() for a in v})
    if len(auths) == 1:
        return auths[0]
    return ct.get("pre_split_authority") or "AUTH_INTAKE"


class Evaluator:
    def __init__(self, cp, dp, contracts, rules, policy, sealer, *, advisory_classes=("codeset_validity",),
                 dependencies=None):
        self.cp, self.dp, self.contracts, self.rules, self.policy, self.sealer = cp, dp, contracts, rules, policy, sealer
        self.advisory_classes = set(advisory_classes)
        self.dependencies = dependencies or []
        self.now = lambda: dt.datetime.now(dt.timezone.utc)      # injectable clock (tests)
        try:
            dp.con.create_function("dq_npi_valid", npi_valid, ["VARCHAR"], "BOOLEAN", null_handling="special")
        except Exception:
            pass

    # -- dependency intersection (computed in the DP; only unit keys leave) ------------------
    def _depended_relation(self, dep: dict) -> str | None:
        """The relation that owns a WAIT dependency's `depends_on` domain (config-driven; replaces the
        bronze.ie_segment hard-code). None when no contract declares that domain -- the caller must then
        return [] cleanly rather than assume the relation exists."""
        want = dep.get("depends_on")
        return next((name for name, ct in self.contracts.items() if ct.get("domain") == want), None)

    def _intersecting_elig_units(self, rel: str, batch_id: str, unit_key: str, authorities: list[str],
                                 dep: dict) -> list[str]:
        """Only depended units of programs whose authorities the dependent unit holds (ENG-W1-9): a
        Medicaid encounter never waits on, nor records the key of, a SNAP eligibility unit.
        Config-driven (W2, SCP-W1-1): the depended relation comes from `dep["depends_on"]` (a domain, resolved
        against the contracts) and the join columns come from `dep["key_map"]` (`dependent_col=depended_col`),
        in place of the old hard-coded bronze.encounter_header/bronze.ie_segment join on msis_id=member_id. A
        relation with no WAIT dependency (e.g. bronze.provider_enrollment) never reaches this method (the caller
        only calls it when `deps` is non-empty); the depended-relation lookup still returns [] cleanly if a WAIT
        entry ever named a domain no contract declares, rather than raising."""
        dep_rel = self._depended_relation(dep)
        if dep_rel is None:
            return []
        dep_ct = self.contracts.get(dep_rel) or {}
        progs = [p for p, a in (dep_ct.get("authorities_by_program") or {}).items() if set(a) <= set(authorities)]
        pairs = [tuple(x.strip() for x in kv.split("=", 1)) for kv in (dep.get("key_map") or [])]
        if not progs or not pairs:
            return []
        dependent_col, depended_col = pairs[0]
        prog_col = dep_ct.get("program_column") or "program_code"
        ph = ",".join("?" * len(progs))
        rows = self.dp.fetchall(
            f"SELECT DISTINCT e._dq_unit_key FROM {rel} h JOIN {dep_rel} e "
            f"ON e.{depended_col} = h.{dependent_col} WHERE h._dq_batch_id = ? AND h._dq_unit_key = ? "
            f"AND e.{prog_col} IN ({ph})",
            [batch_id, unit_key, *progs])
        return [r[0] for r in rows]

    def _rule_applies(self, rule, rel) -> bool:
        return rule.relation == rel

    def _eval_rule(self, rule, rel, batch_id) -> dict[str, tuple[int, int, list[str], int]]:
        """Return {unit_key: (evaluated, failed, sample_tokens, undefined)} for one ACTIVE row / file rule."""
        # tokens are keyed by the unit's own program, so a SNAP token never links to a Medicaid token
        auth_expr = "split_part(h._dq_unit_key, ':', 2)"
        tok = self.dp.hmac_sql("K_EV", [auth_expr, f"'{rule.id}'", f"'{batch_id}'", "h._dq_source_record_key"])
        if rule.kind == "row":
            em, ir = EMITTERS[self.dp.dialect], {"assert": rule.assert_ir, "applies_when": rule.applies_ir}
            if self.dp.dialect == "snowflake":          # RED-1: correlated subqueries only in WHERE
                rows = self.dp.fetchall(em.unit_query_where_only(ir, {}, rel, tok), [batch_id] * 4)
            else:
                rows = self.dp.fetchall(em.unit_query(ir, {}, rel, tok), [batch_id])
            return {r[0]: (r[1], r[2], (r[3] or [])[:5], r[4]) for r in rows}
        if rule.kind != "file":
            return {}
        if (self.contracts.get(rel) or {}).get("source_format") == "X12":
            pred = (f"EXISTS (SELECT 1 FROM raw.findings f WHERE f.check_id = '{rule.id}' "
                    f"AND f.file_id = h._dq_file_id "
                    f"AND (f.isa13 IS NULL OR f.isa13 = h.isa13) AND (f.gs06 IS NULL OR f.gs06 = h.gs06) "
                    f"AND (f.st02 IS NULL OR f.st02 = h.st02))")
        else:
            pred = (f"EXISTS (SELECT 1 FROM raw.findings f WHERE f.check_id = '{rule.id}' "
                    f"AND f.file_id = h._dq_file_id)")
        # evaluated = rows whose predicate is DEFINED; a NULL predicate is never a silent PASS (ENG-W1-8)
        rows = self.dp.fetchall(
            f"SELECT h._dq_unit_key, count(*) FILTER (WHERE ({pred}) IS NOT NULL), count(*) FILTER (WHERE {pred}), "
            f"list({tok} ORDER BY {tok}) FILTER (WHERE {pred}), count(*) FILTER (WHERE ({pred}) IS NULL) "
            f"FROM {rel} h WHERE h._dq_batch_id = ? GROUP BY h._dq_unit_key", [batch_id])
        return {r[0]: (r[1], r[2], (r[3] or [])[:5], r[4]) for r in rows}

    def evaluate_measures(self, batch_id: str, rel: str, unit_node: str | None = None) -> list[dict]:
        """Batch-level population measures (CMS-style ratios with thresholds). They are recorded per batch and
        shown in the Tower and on the blackboard. In this build a measure breach NEVER changes a unit's mode (no
        `measure_breach` floor exists in the policy); gating on measures is a later policy decision.

        distinct:      numerator / denominator count DISTINCT values of one element (e.g. member ids).
        longitudinal:  the value is also compared with the most recent PRIOR value of the same measure, node and
                       period key (e.g. submitter) whose batch is fully RELEASED. FAIL when the change exceeds the
                       tolerance (absolute change of the ratio, or relative); NOT_EVALUATED when there is no prior.
        unit_node:     the gated root whose units own this relation (measures on a child relation, e.g. lines).
        """
        out = []
        em = EMITTERS[self.dp.dialect]
        from dqagent.rules.ir import Ctx
        for r in [r for r in self.rules if r.relation == rel and r.kind == "measure"]:
            if r.status != "ACTIVE":
                out.append({"rule_id": r.id, "status": r.status, "reason": r.status_reason, "numerator": None,
                            "denominator": None, "value": None, "disposition": D.NOT_APPLICABLE.value
                            if r.status == "NOT_APPLICABLE" else D.SYSTEM_NOT_LOADED.value, "rule": r,
                            "period_key": None, "prior_value": None, "long_disposition": None})
                continue
            den = em.expr(r.denominator_ir, Ctx({}))
            num = em.expr(r.numerator_ir, Ctx({}))
            qn, qd = em.measure_queries(num, den, rel, r.distinct_col)     # predicates only in WHERE (RED-1)
            n = self.dp.fetchall(qn, [batch_id])[0][0]
            d = self.dp.fetchall(qd, [batch_id])[0][0]
            v = (n / d) if d else None
            lo, hi = r.thresholds.get("min"), r.thresholds.get("max")
            disp = (D.NOT_EVALUATED if v is None else
                    D.FAIL if (lo is not None and v < lo) or (hi is not None and v > hi) else D.PASS)
            pk = prior = ld = None
            if r.longitudinal:
                pc = r.longitudinal.get("period_col")
                if pc:
                    keys = self.dp.fetchall(f"SELECT DISTINCT h.{pc} FROM {rel} h WHERE h._dq_batch_id = ? "
                                            f"ORDER BY 1", [batch_id])
                    pk = "|".join(str(k[0]) for k in keys) if keys else None
                prior = self._prior_value(r.id, r.effective_hash, rel, unit_node or rel, pk, batch_id)
                if v is None or prior is None:
                    ld = D.NOT_EVALUATED
                else:
                    tol = float(r.longitudinal["tolerance"])
                    delta = abs(v - prior) if r.longitudinal.get("compare", "absolute") == "absolute" else (
                        abs(v - prior) / prior if prior else (0.0 if v == prior else float("inf")))
                    ld = D.FAIL if delta > tol else D.PASS
                if ld is D.FAIL:
                    disp = D.FAIL
            out.append({"rule_id": r.id, "status": "ACTIVE", "numerator": n, "denominator": d, "value": v,
                        "disposition": disp.value, "rule": r, "period_key": pk, "prior_value": prior,
                        "long_disposition": ld.value if ld else None})
        if out:
            # the relation's (or its pre-split) authority (C2): deterministic from the contract, never an unordered
            # row read of dq_ctl.units (a (batch_id, node) can hold units of DIFFERENT authorities, e.g.
            # bronze.ie_segment split by program; an unordered `LIMIT 1` is a race, not a representative row)
            auth = relation_authority(self.contracts, unit_node or rel)
            with self.cp.as_role("dq_eval_svc") as c:
                for m in out:
                    r = m.pop("rule")
                    c.execute("INSERT INTO dq_ctl.measure_results(batch_id,node,authority,rule_id,rule_hash,numerator,"
                              "denominator,value,thresholds,disposition,period_key,prior_value,long_disposition) "
                              "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s,%s)",
                              (batch_id, rel, auth, m["rule_id"], r.effective_hash,
                               m["numerator"], m["denominator"], m["value"],
                               json.dumps({**r.thresholds, **({"longitudinal": r.longitudinal["tolerance"]}
                                                              if r.longitudinal else {})}),
                               m["disposition"], m["period_key"], m["prior_value"], m["long_disposition"]))
        return out

    def _prior_value(self, rule_id, rule_hash, rel, unit_node, period_key, batch_id):
        """Most recent earlier PERIOD's value (periods ordered by first computation, i.e. arrival; only the latest
        value of each period counts) of the SAME rule version, node and period key, from a batch whose units on the
        owning node are ALL released (a held / rejected period is never a reference)."""
        with self.cp.as_role("dq_eval_svc") as c:
            r = c.execute(
                "SELECT m.value FROM (SELECT DISTINCT ON (batch_id, node, rule_id) *, min(id) OVER (PARTITION BY batch_id, node, rule_id) AS first_id FROM dq_ctl.measure_results "
                "ORDER BY batch_id, node, rule_id, id DESC) m WHERE m.rule_id=%s AND m.rule_hash=%s AND m.node=%s "
                "AND m.period_key IS NOT DISTINCT FROM %s AND m.batch_id <> %s AND m.value IS NOT NULL "
                "AND EXISTS (SELECT 1 FROM dq_ctl.units u WHERE u.batch_id=m.batch_id AND u.node=%s) "
                "AND NOT EXISTS (SELECT 1 FROM dq_ctl.units u WHERE u.batch_id=m.batch_id AND u.node=%s "
                "AND u.state <> 'RELEASED') ORDER BY m.first_id DESC LIMIT 1",
                (rule_id, rule_hash, rel, period_key, batch_id, unit_node, unit_node)).fetchone()
        return float(r["value"]) if r else None

    governance = None        # set by the runtime: () -> {ratified, ruleset_version, ...}

    def veto_window_s(self) -> int:
        """Policy `veto_window` (ISO 8601 duration) in seconds; default PT10M."""
        from dqagent.governance.registry import iso_duration_seconds
        return iso_duration_seconds(self.policy.get("veto_window") or "PT10M")

    def evaluate(self, batch_id: str, rel: str, *, abc_failed: bool) -> dict:
        gov = self.governance() if self.governance else {"ratified": True, "ruleset_version": None}
        if not gov["ratified"]:
            # Part D (C6): no ratified configuration -> nothing is evaluated; units stay SEALED and undecidable
            return {"held": 0, "autonomous": 0, "pending_dependency": 0, "x_not_ratified": True}
        with self.cp.as_role("dq_eval_svc") as c:
            if abc_missing(c, batch_id):
                # Part F F-01: sealed but its LANDING->GATED ABC was never recorded (crash between seal and ABC).
                # "No ABC rows" must never read as "ABC passed": nothing is evaluated until the batch is recovered.
                return {"held": 0, "autonomous": 0, "pending_dependency": 0, "x_abc_missing": True}
        self._ruleset_version = gov["ruleset_version"]
        # deterministic order (unit key): veto windows of one batch then open in a reproducible order
        units = self.dp.fetchall(f"SELECT DISTINCT _dq_unit_key FROM {rel} WHERE _dq_batch_id = ? ORDER BY 1", [batch_id])
        unit_keys = [u[0] for u in units]
        bound = [r for r in self.rules if self._rule_applies(r, rel) and r.kind in ("row", "file", "not_loaded")]
        applicable = [r for r in bound if r.status == "ACTIVE" and r.kind in ("row", "file")]
        per_rule = {}
        for r in applicable:                          # Part F P1-1: one span per rule evaluation (counts only)
            with tracer.span(f"rule:{r.id}@{rel}", seat="EVALUATOR", subject=f"rule:{r.id}@{rel}") as sp:
                per_rule[r.id] = self._eval_rule(r, rel, batch_id)
                sp.attr("units", len(per_rule[r.id]))
                sp.attr("failed", sum(1 for v in per_rule[r.id].values() if v[1] > 0))
        with tracer.span("measures", seat="EVALUATOR", subject=f"rel:{rel}"):
            if any(r.relation == rel and r.kind == "measure" for r in self.rules):
                self.evaluate_measures(batch_id, rel)
            for child, cc in self.contracts.items():  # measures on child relations (e.g. lines) run with their root
                if (cc.get("unit_from_parent") or {}).get("relation") == rel and \
                        any(r.relation == child and r.kind == "measure" for r in self.rules):
                    self.evaluate_measures(batch_id, child, unit_node=rel)
        summary = {"held": 0, "autonomous": 0, "pending_dependency": 0, "veto_window": 0}
        state = gov.get("state")
        auto_cache: dict = {}
        deps = [d for d in self.dependencies if d["domain"] == self.contracts[rel]["domain"] and d["policy"] == "WAIT"]
        with self.cp.as_role("dq_eval_svc") as c:
            states = {r["unit_key"]: r for r in c.execute(
                "SELECT unit_key, state, state_version, authorities, program_code FROM dq_ctl.units WHERE batch_id=%s",
                (batch_id,)).fetchall()}
        ordered = self.contracts[rel].get("ordering") == "ORDERED"
        max_wait = iso_duration_s(deps[0].get("max_wait") if deps else self.contracts[rel].get("order_max_wait"))
        escalate_to = (deps[0].get("escalation") if deps else None) or "dq-duty-steward"
        for uk in unit_keys:
            u = states[uk]
            if u["state"] not in ("SEALED", "PENDING_DEPENDENCY", "RE_EVALUATE_PENDING"):
                continue                      # already evaluated (HELD / EVALUATING / terminal): never re-enter
            dep_vector, waiting = {}, []
            if ordered:
                # Strict per-partition order: a later version of the same partition is evaluated only after
                # every earlier unit of that partition is terminal. Otherwise its rules would run against stale
                # history (e.g. closures still held in an earlier file) and raise false failures.
                with self.cp.as_role("dq_eval_svc") as c:
                    for r in c.execute("SELECT e.unit_key, e.state, e.state_version FROM dq_ctl.units e, dq_ctl.units me "
                                       "WHERE me.unit_key=%s AND e.node=me.node AND e.partition_key=me.partition_key "
                                       "AND e.created_at < me.created_at", (uk,)).fetchall():
                        dep_vector[r["unit_key"]] = r["state_version"]
                        if r["state"] not in TERMINAL_STATES:
                            waiting.append(r["unit_key"])
            if deps:
                with self.cp.as_role("dq_eval_svc") as c:
                    for eu in self._intersecting_elig_units(rel, batch_id, uk, u["authorities"], deps[0]):
                        r = c.execute("SELECT state, state_version FROM dq_ctl.units WHERE unit_key=%s", (eu,)).fetchone()
                        if r:
                            dep_vector[eu] = r["state_version"]
                            if r["state"] not in TERMINAL_STATES:
                                waiting.append(eu)
            with self.cp.as_role("dq_eval_svc") as c:
                v = u["state_version"]
                if u["state"] == "PENDING_DEPENDENCY" and waiting:
                    # Part F P1-1 (I9): a unit still waiting on exactly the same dependency versions is not
                    # re-evaluated. Before this, every worker cycle wrote PENDING -> EVALUATING -> PENDING for every
                    # waiting unit (two events each), so no cycle was ever idle. The wait clock (the FIRST pending
                    # event) is unchanged, so max_wait escalation is unaffected.
                    first = c.execute("SELECT min(at) t FROM dq_ctl.unit_events WHERE unit_key=%s "
                                      "AND to_state='PENDING_DEPENDENCY'", (uk,)).fetchone()["t"]
                    if not (first is not None and (self.now() - first).total_seconds() > max_wait):
                        prev = c.execute("SELECT payload FROM dq_ctl.unit_events WHERE unit_key=%s "
                                         "ORDER BY state_version DESC LIMIT 1", (uk,)).fetchone()
                        pp = (prev["payload"] or {}) if prev else {}
                        if sorted(pp.get("waiting_on") or []) == sorted(waiting) and \
                                (pp.get("dependency_vector") or {}) == dep_vector:
                            summary["pending_dependency"] += 1
                            continue
                if u["state"] == "SEALED":
                    v = self.cp.transition(c, uk, v, "EVALUATING", "ENGINE", "dq_eval_svc")
                elif u["state"] in ("PENDING_DEPENDENCY", "RE_EVALUATE_PENDING"):
                    v = self.cp.transition(c, uk, v, "EVALUATING", "ENGINE", "dq_eval_svc")
                escalated = None
                if waiting:
                    first = c.execute("SELECT min(at) t FROM dq_ctl.unit_events WHERE unit_key=%s "
                                      "AND to_state='PENDING_DEPENDENCY'", (uk,)).fetchone()["t"]
                    if first is not None and (self.now() - first).total_seconds() > max_wait:
                        # max_wait exceeded: stop waiting, evaluate against what is released, and require a human
                        escalated = f"dependency wait exceeded {max_wait}s on {len(waiting)} unit(s); escalated to {escalate_to}"
                    else:
                        self.cp.transition(c, uk, v, "PENDING_DEPENDENCY", "ENGINE", "dq_eval_svc",
                                           {"waiting_on": waiting, "dependency_vector": dep_vector})
                        summary["pending_dependency"] += 1
                        continue
                eval_version = v
                disps = []
                advisory_waived = False
                for rule in bound:
                    advisory = rule.header.get("gating") == "advisory"
                    if advisory and rule.status != "ACTIVE":
                        continue                 # an inactive ADVISORY rule never touches a unit (coverage shows it)
                    if rule.status == "ACTIVE" and rule.kind in ("row", "file"):
                        in_scope = uk in per_rule[rule.id]
                        ev, fl, toks, undefined = per_rule[rule.id].get(uk, (0, 0, [], 0))
                        if not in_scope and rule.kind == "row" and rule.applies_ir is not None:
                            # applies_when selected no row of this unit: nothing to judge. Excluded from ranking, never
                            # a PASS and never a hold (post-build F4/F5). A rule with no applies_when that sees no
                            # row stays NOT_EVALUATED (fail closed).
                            d = D.NOT_IN_SCOPE
                        else:
                            d = D.FAIL if fl > 0 else D.NOT_EVALUATED if undefined or ev == 0 else D.PASS
                        if advisory and d not in (D.PASS, D.NOT_IN_SCOPE):
                            # Basic DQ is ADVISORY unless promoted (Part C F3): recorded as a technical-assistance
                            # warning (rank 1, never changes the mode); the counts and tokens are kept as evidence.
                            d = D.TA_WARN
                    else:
                        ev, fl, toks = 0, 0, []
                        d = {"NOT_APPLICABLE": D.NOT_APPLICABLE, "STATE_WAIVED": D.STATE_WAIVED}.get(rule.status)
                        if d is None:            # NOT_LOADED / NOT_BOUND: fail closed unless policy marks the class advisory
                            d = D.ADVISORY if rule["check_class"] in self.advisory_classes else D.SYSTEM_NOT_LOADED
                            advisory_waived |= d is D.ADVISORY
                    disps.append(d)
                    # F-29: the measured value (the failing share of the evaluated rows) and the rule's own
                    # thresholds, for the decision card. Outside every digest and observed set (I-3).
                    metric = (fl / ev) if ev else None
                    c.execute("INSERT INTO dq_ctl.results(unit_key,eval_version,authority,rule_id,rule_hash,check_class,"
                              "priority,disposition,evaluated,failed,sample_tokens,ruleset_version,metric,threshold) "
                              "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s::jsonb)",
                              (uk, eval_version, u["authorities"][0], rule.id, rule.effective_hash, rule["check_class"],
                               rule["priority"], d.value, ev, fl, json.dumps(toks), self._ruleset_version, metric,
                               json.dumps(rule.thresholds) if rule.thresholds else None))
                structural = u["program_code"] in ("SNAP", "TANF")
                ak = tuple(sorted(u["authorities"]))
                if ak not in auto_cache:
                    # Part F P1-3 (K2b, PFP :793-804 -- current line, not PFP's stale :318): `rel` is this
                    # evaluate() call's own fixed relation (the method parameter, set at :235); auto_cache is
                    # per-call (:266), so caching by authority set alone is still correct with `relation` added.
                    auto_cache[ak] = autonomy_for(c, state, list(ak), relation=rel)
                # C2: escalation is _max(m, H_IN) inside mode() (never H_ON replaced by itself), and persisted below
                m, trace = compute_mode(self.policy, u["authorities"], disps, abc_failed=abc_failed,
                                        structural=structural, advisory_waived=advisory_waived,
                                        autonomy=auto_cache[ak], escalated=escalated, vetoed=was_vetoed(c, uk))
                payload = {"mode": m, "mode_trace": trace, "dependency_vector": dep_vector,
                           "eval_version": eval_version}
                if escalated:
                    payload["escalated"] = escalated
                m0, _ = compute_mode(self.policy, u["authorities"], disps, abc_failed=abc_failed, structural=structural,
                                     advisory_waived=advisory_waived, escalated=escalated, vetoed=was_vetoed(c, uk))
                if ORDER.index(m) > ORDER.index(m0):          # the tier / entitlement ceiling bound the mode: say so
                    payload["ceiling"] = ceiling_label(trace)
                # Part F P1-1: guardrail events derived from the returned trace (observe only; modes.py unchanged)
                if any(t.startswith("floor:") for t in trace):
                    tracer.guardrail("FLOOR_APPLIED", f"unit:{uk}", authorities=u["authorities"], seat="EVALUATOR",
                                     detail={"eval_version": eval_version})
                if payload.get("ceiling"):
                    tracer.guardrail("CEILING_APPLIED", f"unit:{uk}", authorities=u["authorities"], seat="EVALUATOR",
                                     detail={"eval_version": eval_version})
                if m == "H_ON":
                    # L3: act after a veto window. The window row is stamped by the database clock; the publisher
                    # releases only once it has elapsed by that clock, with no veto, and the tier still >= L3.
                    # E8: the window records the state whose tier allowed it, so a veto demotes THAT state only
                    c.execute("INSERT INTO dq_ctl.veto_windows(unit_key, eval_version, window_s, policy_hash, state) "
                              "VALUES (%s,%s,%s,%s,%s) ON CONFLICT (unit_key, eval_version) DO NOTHING",
                              (uk, eval_version, self.veto_window_s(),
                               hashlib.sha256(json.dumps(self.policy, sort_keys=True, default=str).encode()).hexdigest(),
                               state))
                    c.execute("SELECT dq_ctl.append_audit('VETO_WINDOW_OPENED', %s)",
                              (hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest(),))
                    summary["veto_window"] += 1
                elif m == "A":
                    # Mode A: the unit stays EVALUATING. The publisher RECOMPUTES the mode from stored
                    # results before releasing, so nothing written here is trusted by the release path.
                    c.execute("SELECT dq_ctl.append_audit('MODE_A', %s)",
                              (hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest(),))
                    summary["autonomous"] += 1
                else:
                    self.cp.transition(c, uk, v, "HELD", "ENGINE", "dq_eval_svc", payload)
                    summary["held"] += 1
        return summary


