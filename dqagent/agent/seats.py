"""The agent's seats: deterministic posts to the blackboard, triage hypotheses, and memory (Part C WS3).

Each seat posts AS its own service role, and the database derives the seat from the login (dq_ctl.bb_post), so
the code here cannot choose or forge a seat. Every post is produced by a deterministic template over stored
evidence (results, measures, ABC, unit states). No LLM is involved, and no data value enters a post: the
bodies are identifiers and counts, and prose is rendered from the template at read time.

  INTAKE     (dq_seal_svc)    ABC hops that did not PASS
  EVALUATOR  (dq_eval_svc)    rules with non-PASS dispositions in the batch; measure breaches
  TRIAGE     (dq_triage_svc)  hypotheses (eligibility not yet released; failures confined to one submitter;
                              transmission defect; measure off its baseline) and baseline maintenance
  PUBLISHER  (dq_publish_svc) release summaries
  MONITOR    (dq_monitor_svc) distribution / statistical-process-control signals (advisory; Part E §6)
  STEWARD    (dq_api)         human notes and known-issue proposals (person id from a verified token)
  REGISTRY   (dq_registry_svc) look-back flags and automatic demotions (a control seat, not the agent acting)

Part E: every seat belongs to an agent (dqagent/cp/agent_schema.SEATS: role -> (agent, seat)); an agent seat posts or
pins only while its agent is ACTIVE and holds a live POST_BOARD / PIN_MEMORY entitlement (the database refuses
otherwise). References between entries are typed text ids (bb:<seq>, mem:<id>, rule:<id>@<entity>, unit:<key>,
cr:<id>, lookback:<id>, feed:<id>, pr:<id>).

Memory:
  BASELINE     median of a measure over up to 6 ELIGIBLE periods, pinned by hash, advisory only (never gates).
               A period (batch, node) is eligible when every unit is RELEASED, no person released any of its
               units over a FAIL / ERROR / UNBALANCEABLE disposition or quarantined / rejected rows (no override),
               and no KNOWN_ISSUE was ever approved on the measure (an expired one does not re-admit its periods) (red-team F6: poisoning). A plain RELEASE
               of a unit held only by a floor (e.g. the code-set ADVISORY floor) is not an override.
  KNOWN_ISSUE  proposed by one person, approved by a different person with a step-up, expiry within 90 days.
               It is shown to the MAKER seat only, after the disposition, and never changes a disposition or a
               mode (red-team F7).
"""
from __future__ import annotations

import hashlib
import json
import statistics

from dqagent.engine.evaluate import relation_authority

TEMPLATES = {
    "ABC_HOP_NOT_PASS": "ABC hop {hop} is {disposition} for batch {batch_id} ({count} rows affected).",
    "RULE_NON_PASS": "Rule {rule_id} is {disposition} on {unit_count} unit(s) of {node}.",
    "MEASURE_BREACH": "Measure {measure_id} = {value} breaches its threshold {threshold}.",
    "MEMBER_UNKNOWN_ELIG_OPEN": ("{unit_count} unit(s) fail {rule_id} while {open_units} eligibility unit(s) are not "
                                 "yet released: the member may be eligible in data still under review."),
    "FAILURES_ONE_SUBMITTER": ("All {count} failing row(s) of {rule_id} come from submitter {submitter_id}; "
                               "{counts} other submitter(s) are clean on the same rule: likely a sender-side defect."),
    "TRANSMISSION_DEFECT": ("Envelope / transaction rules {rule_ids} fail on {unit_count} unit(s): the transmission is "
                            "defective; content findings in the same units are not reliable until it is re-sent."),
    "MEASURE_OFF_BASELINE": ("Measure {measure_id} = {value} differs from its baseline median {median} "
                             "(over {n_periods} clean periods) by {delta}. Advisory: baselines never gate."),
    "RELEASE_SUMMARY": "Batch {batch_id} / {node}: {released} released, {held} held, {pending} pending.",
    "STEWARD_NOTE": "Steward note on {rule_id}: reason code {reason_code}.",
    "KNOWN_ISSUE_PROPOSED": "Known issue {memory_id} proposed on {rule_id} (expires {expires}).",
    "LOOKBACK_FLAGGED": ("Look-back: {unit_count} released unit(s) of batch {batch_id} were released under {rule_id} "
                         "version {rule_hash}, revoked by {cr_id}. Flagged for human re-review."),
    # Part E
    "SPC_SIGNAL": ("Monitor: {metric} {key} on {relation} is {value} in period {period}, outside its control limits "
                   "[{lcl}, {ucl}] (rule {rule}). Advisory: monitoring never holds a unit."),
    "MIX_SHIFT": ("Monitor: the mix of {metric} on {relation} moved in period {period} (PSI {psi}, {disposition}). "
                  "Advisory: monitoring never holds a unit."),
    "BASELINE_BUILDING": ("Monitor: {metric} on {relation} has {n_periods} eligible period(s); control limits need "
                          "{min_periods}. No signal is raised until then."),
    "AUTONOMY_DEMOTED": ("Autonomy of {agent_id} for {action_class} on {authority} lowered from {from_tier} to "
                         "{to_tier} ({reason_code}, {cr_id}). Raising it again needs two people."),
    "EMPTY_DELIVERY": ("Feed {feed} delivered a file with no detail rows in batch {batch_id}; feed item {item_id} awaits "
                       "acknowledgement. An empty delivery never holds the batch."),
}

ENVELOPE_PREFIXES = ("ENV-", "TX-", "FLAT-")
ELIG_RULES = ("R-ENC-004", "R-ENC-005", "M-RULE-884")
OFF_BASELINE_DELTA = 0.10
BASELINE_PERIODS = 6


# F-25: counts of units, rows or submitters shown on the board are small-cell suppressed like the explainer facts
# (llm.facts: 1..10 -> "fewer than 11"); zero stays zero. Display only: the stored body and bb_hash are unchanged.
SMALL_CELL_FIELDS = {"count", "unit_count", "open_units", "counts", "released", "held", "pending"}
SMALL_CELL_MAX = 10


def _shown(field: str, v):
    if field in SMALL_CELL_FIELDS and isinstance(v, int) and not isinstance(v, bool) and 1 <= v <= SMALL_CELL_MAX:
        return f"fewer than {SMALL_CELL_MAX + 1}"
    return v


def render(template: str, body: dict) -> str:
    return TEMPLATES[template].format(**{k: _shown(k, body.get(k, "?")) for k in _fields(template)})


def _fields(template: str):
    import string
    return [f for _, f, _, _ in string.Formatter().parse(TEMPLATES[template]) if f]


def _num(v):
    return None if v is None else round(float(v), 6)


def ref(kind: str, ident) -> str:
    """A typed blackboard reference, e.g. ref('mem', 12) -> 'mem:12'."""
    from dqagent.cp.agent_schema import REF_KINDS
    assert kind in REF_KINDS, kind
    return f"{kind}:{ident}"


class Board:
    def __init__(self, cp):
        self.cp = cp

    def post(self, c, batch_id, authority, kind, subject_type, subject_id, template, body, refs=None, person=None,
             agent=None):
        """`refs`: typed ids (see ref()). `agent`: only a control seat (STEWARD / REGISTRY) may name the agent whose
        namespace it writes in; an agent seat always writes in its own (the database enforces both)."""
        assert template in TEMPLATES
        return c.execute("SELECT dq_ctl.bb_post(%s,%s,%s,%s,%s,%s,%s::jsonb,%s::text[],%s,%s) AS seq",
                         (batch_id, authority, kind, subject_type, subject_id, template,
                          json.dumps(body, sort_keys=True, default=str), list(refs) if refs else None, person,
                          agent)).fetchone()["seq"]


class AgentSeats:
    def __init__(self, cp, dp, rules, state_columns: dict, contracts: dict | None = None, dependencies=None):
        self.cp, self.dp, self.rules = cp, dp, rules
        self.contracts = contracts or {}
        self.dependencies = dependencies or []
        # a measure on a child relation (lines) belongs to its root's units
        self.owner = {rel: (c.get("unit_from_parent") or {}).get("relation", rel) for rel, c in (contracts or {}).items()}
        self.board = Board(cp)
        self.cols = state_columns

    def _depended_relation(self, rel: str | None) -> str | None:
        """The relation `rel`'s units WAIT on, if any (dependency-graph lookup; K5 red team, replaces the TRIAGE
        H1 hard-code on `node='bronze.ie_segment'`). None when `rel` is unknown, or its domain carries no WAIT
        dependency (e.g. bronze.provider_enrollment, which has no eligibility dependency) -- so H1 never fires,
        and never queries a relation that does not exist, for a feed with no such dependency."""
        dom = (self.contracts.get(rel) or {}).get("domain") if rel else None
        dep = next((d for d in self.dependencies if d["domain"] == dom and d["policy"] == "WAIT"), None)
        if dep is None:
            return None
        want = dep.get("depends_on")
        return next((name for name, ct in self.contracts.items() if ct.get("domain") == want), None)

    # -- INTAKE ------------------------------------------------------------------------------------------
    def after_intake(self, batch_id: str, abc: list[dict] | None = None):
        """Posts every GATING ABC hop that did not pass (the one definition, dq_ctl.abc_gating_failures; Part E I1),
        then one EMPTY_DELIVERY note per feed item opened for the batch. `abc` is kept for the caller's signature;
        the rows are read back from the control plane."""
        with self.cp.as_role("dq_seal_svc") as c:
            rows = c.execute("SELECT * FROM dq_ctl.abc_gating_failures WHERE batch_id=%s ORDER BY id", (batch_id,)).fetchall()
            for h in rows:
                n = sum(int(h.get(k) or 0) for k in ("rejected", "quarantined", "filtered", "residual"))
                self.board.post(c, batch_id, h.get("authority") or "AUTH_INTAKE", "FINDING", "BATCH", batch_id,
                                "ABC_HOP_NOT_PASS", {"hop": h["hop"], "disposition": h["disposition"],
                                                     "batch_id": batch_id, "count": n})
            for it in c.execute("SELECT item_id, feed, authority FROM dq_ctl.feed_items_status WHERE batch_id=%s "
                                "AND kind='EMPTY_DELIVERY' AND status='OPEN' ORDER BY item_id", (batch_id,)).fetchall():
                self.board.post(c, batch_id, it["authority"], "FINDING", "FEED", it["feed"], "EMPTY_DELIVERY",
                                {"feed": it["feed"], "batch_id": batch_id, "item_id": it["item_id"]},
                                refs=[ref("feed", it["item_id"])])

    # -- EVALUATOR ---------------------------------------------------------------------------------------
    def after_evaluation(self, batch_id: str):
        with self.cp.as_role("dq_eval_svc") as c:
            rows = c.execute(
                "WITH latest AS (SELECT DISTINCT ON (r.unit_key, r.rule_id) r.unit_key, r.rule_id, r.disposition, "
                "r.authority FROM dq_ctl.results r JOIN dq_ctl.units u USING (unit_key) WHERE u.batch_id=%s "
                "ORDER BY r.unit_key, r.rule_id, r.eval_version DESC, r.id DESC) "
                "SELECT l.rule_id, l.disposition, l.authority, u.node, count(*) n FROM latest l "
                "JOIN dq_ctl.units u USING (unit_key) WHERE l.disposition NOT IN ('PASS','NOT_APPLICABLE','STATE_WAIVED','ADVISORY','NOT_IN_SCOPE') "
                "GROUP BY 1,2,3,4 ORDER BY 1,2,3,4", (batch_id,)).fetchall()
            for r in rows:
                self.board.post(c, batch_id, r["authority"], "FINDING", "RULE", r["rule_id"], "RULE_NON_PASS",
                                {"rule_id": r["rule_id"], "disposition": r["disposition"],
                                 "unit_count": r["n"], "node": r["node"]})
            # a measure is re-computed when a pending batch is re-evaluated: only its LATEST value per batch counts
            for m in c.execute("SELECT rule_id, authority, value, thresholds FROM (SELECT DISTINCT ON (batch_id, node, rule_id) *, min(id) OVER (PARTITION BY batch_id, node, rule_id) AS first_id FROM dq_ctl.measure_results ORDER BY batch_id, node, rule_id, id DESC) mr "
                               "WHERE batch_id=%s AND disposition='FAIL' ORDER BY rule_id", (batch_id,)).fetchall():
                self.board.post(c, batch_id, m["authority"], "FINDING", "MEASURE", m["rule_id"], "MEASURE_BREACH",
                                {"measure_id": m["rule_id"], "value": _num(m["value"]),
                                 "threshold": json.dumps(m["thresholds"], sort_keys=True).replace(" ", "")
                                 .replace('"', "").replace("{", "").replace("}", "").replace(",", "|")})

    # -- TRIAGE ------------------------------------------------------------------------------------------
    def triage(self, batch_id: str):
        with self.cp.as_role("dq_triage_svc") as c:
            fails = c.execute(
                "WITH latest AS (SELECT DISTINCT ON (r.unit_key, r.rule_id) r.unit_key, r.rule_id, r.disposition, "
                "r.authority FROM dq_ctl.results r JOIN dq_ctl.units u USING (unit_key) WHERE u.batch_id=%s "
                "ORDER BY r.unit_key, r.rule_id, r.eval_version DESC, r.id DESC) "
                "SELECT rule_id, authority, count(*) n FROM latest WHERE disposition IN ('FAIL','TA_WARN') "
                "GROUP BY 1,2 ORDER BY 1,2", (batch_id,)).fetchall()
            # H1 eligibility not yet released: dependency-graph driven (K5 red team) -- fires only for a failing
            # rule whose OWN relation actually WAITs on an eligibility-like dependency, never for a relation with
            # none (e.g. bronze.provider_enrollment), and never by assuming bronze.ie_segment exists.
            open_elig_by_dep_rel: dict[str, int] = {}
            for f in fails:
                if f["rule_id"] not in ELIG_RULES:
                    continue
                rule = next((r for r in self.rules if r.id == f["rule_id"]), None)
                dep_rel = self._depended_relation(rule.relation if rule else None)
                if dep_rel is None:
                    continue
                if dep_rel not in open_elig_by_dep_rel:
                    open_elig_by_dep_rel[dep_rel] = c.execute(
                        "SELECT count(*) n FROM dq_ctl.units WHERE node=%s "
                        "AND state NOT IN ('RELEASED','REJECTED','SUPERSEDED')", (dep_rel,)).fetchone()["n"]
                open_elig = open_elig_by_dep_rel[dep_rel]
                if open_elig:
                    self.board.post(c, batch_id, f["authority"], "HYPOTHESIS", "RULE", f["rule_id"],
                                    "MEMBER_UNKNOWN_ELIG_OPEN", {"rule_id": f["rule_id"], "unit_count": f["n"],
                                                                 "open_units": open_elig})
            # H3 transmission defect (envelope / transaction / flat-file structure)
            env = [f for f in fails if f["rule_id"].startswith(ENVELOPE_PREFIXES)]
            for auth in sorted({f["authority"] for f in env}):
                ids = sorted({f["rule_id"] for f in env if f["authority"] == auth})
                self.board.post(c, batch_id, auth, "HYPOTHESIS", "BATCH", batch_id, "TRANSMISSION_DEFECT",
                                {"rule_ids": ids, "unit_count": sum(f["n"] for f in env if f["authority"] == auth)})
            # H2 failures confined to one submitter WHILE other submitters are clean on the same rule (computed in
            # the DP over every loaded batch; only submitter ids and counts leave). A batch is one inbound file from
            # one submitter, so a per-batch version would be a tautology (post-build F7).
            sub_col = self.cols.get("CLM.SUBMITTER_ID")
            for f in fails:
                rule = next((r for r in self.rules if r.id == f["rule_id"] and r.entity == "ENCOUNTER_HEADER"
                             and r.kind == "row" and r.status == "ACTIVE"), None)
                if rule is None or not sub_col:
                    continue
                pred = rule.sql(self.dp.dialect)
                app = rule.sql(self.dp.dialect, "applies") if rule.applies_ir is not None else "TRUE"
                rows = self.dp.fetchall(
                    f"SELECT h.{sub_col}, count(*) FILTER (WHERE ({pred}) IS FALSE), count(*) FROM {rule.relation} h "
                    f"WHERE ({app}) IS TRUE GROUP BY 1 ORDER BY 1")
                failing = [(sid, n) for sid, n, _ in rows if n]
                clean_others = [sid for sid, n, tot in rows if not n and tot]
                if len(failing) == 1 and failing[0][0] and clean_others:
                    self.board.post(c, batch_id, f["authority"], "HYPOTHESIS", "SUBMITTER", failing[0][0],
                                    "FAILURES_ONE_SUBMITTER", {"rule_id": f["rule_id"], "submitter_id": failing[0][0],
                                                               "count": failing[0][1],
                                                               "counts": len(clean_others)})
            # H4 measure off its (advisory) baseline
            for m in c.execute("SELECT rule_id, node, authority, value FROM (SELECT DISTINCT ON (batch_id, node, rule_id) *, min(id) OVER (PARTITION BY batch_id, node, rule_id) AS first_id FROM dq_ctl.measure_results ORDER BY batch_id, node, rule_id, id DESC) mr WHERE batch_id=%s "
                               "AND value IS NOT NULL ORDER BY rule_id", (batch_id,)).fetchall():
                b = self._baseline(c, m["rule_id"], m["node"])
                if b and b["n_periods"] >= 2 and abs(float(m["value"]) - b["median"]) > OFF_BASELINE_DELTA:
                    self.board.post(c, batch_id, m["authority"], "HYPOTHESIS", "MEASURE", m["rule_id"],
                                    "MEASURE_OFF_BASELINE",
                                    {"measure_id": m["rule_id"], "value": _num(m["value"]), "median": b["median"],
                                     "delta": _num(float(m["value"]) - b["median"]), "n_periods": b["n_periods"]},
                                    refs=[ref("mem", b["memory_id"])])

    def _baseline(self, c, measure_id, node):
        r = c.execute("SELECT memory_id, content FROM dq_ctl.agent_memory WHERE kind='BASELINE' AND scope_type='MEASURE' "
                      "AND scope_id=%s ORDER BY memory_id DESC LIMIT 1", (f"{measure_id}@{node}",)).fetchone()
        return {**r["content"], "memory_id": r["memory_id"]} if r else None

    # -- PUBLISHER + baseline maintenance ---------------------------------------------------------------
    def after_publish(self):
        with self.cp.as_role("dq_publish_svc") as c:
            for s in c.execute("SELECT * FROM dq_ctl.agent_state WHERE released > 0 ORDER BY batch_id, node").fetchall():
                body = {"batch_id": s["batch_id"], "node": s["node"], "released": s["released"],
                        "held": s["held"], "pending": s["pending"]}
                last = c.execute("SELECT body FROM dq_ctl.agent_blackboard WHERE template='RELEASE_SUMMARY' "
                                 "AND batch_id=%s AND body->>'node'=%s ORDER BY seq DESC LIMIT 1",
                                 (s["batch_id"], s["node"])).fetchone()
                if last and last["body"] == body:
                    continue
                # the node's (or its pre-split) authority (C2): deterministic from the contract, never an unordered
                # row read of dq_ctl.units (a (batch_id, node) can hold units of DIFFERENT authorities, e.g.
                # bronze.ie_segment split by program; an unordered `LIMIT 1` is a race, not a representative row)
                auth = relation_authority(self.contracts, s["node"])
                self.board.post(c, s["batch_id"], auth, "NOTE", "BATCH", s["batch_id"], "RELEASE_SUMMARY", body)
        self.refresh_baselines()

    def refresh_baselines(self):
        with self.cp.as_role("dq_triage_svc") as c:
            eligible = c.execute(
                "SELECT u.batch_id, u.node FROM dq_ctl.units u GROUP BY 1,2 "
                "HAVING bool_and(u.state='RELEASED') AND NOT EXISTS (SELECT 1 FROM dq_ctl.decisions d "
                "JOIN dq_ctl.units x USING (unit_key) WHERE x.batch_id=u.batch_id AND x.node=u.node "
                "AND (d.decision <> 'RELEASE' OR d.disposition_vector::text ~ '\"(FAIL|ERROR|UNBALANCEABLE)\"'))"
                ).fetchall()
            ok = {(r["batch_id"], r["node"]) for r in eligible}
            known = {r["scope_id"] for r in c.execute(
                "SELECT m.scope_id FROM dq_ctl.agent_memory m WHERE m.kind='KNOWN_ISSUE' "
                "AND EXISTS (SELECT 1 FROM dq_ctl.agent_memory_events e WHERE e.memory_id=m.memory_id "
                "AND e.event='APPROVED')").fetchall()}
            series: dict = {}
            for m in c.execute("SELECT batch_id, node, rule_id, authority, value FROM (SELECT DISTINCT ON (batch_id, node, rule_id) *, min(id) OVER (PARTITION BY batch_id, node, rule_id) AS first_id FROM dq_ctl.measure_results ORDER BY batch_id, node, rule_id, id DESC) mr "
                               "WHERE value IS NOT NULL ORDER BY first_id").fetchall():
                if (m["batch_id"], self.owner.get(m["node"], m["node"])) in ok and m["rule_id"] not in known:
                    series.setdefault((m["rule_id"], m["node"], m["authority"]), []).append((m["batch_id"], float(m["value"])))
            for (rid, node, auth), pts in sorted(series.items()):
                pts = pts[-BASELINE_PERIODS:]
                content = {"measure_id": rid, "node": node, "median": round(statistics.median(v for _, v in pts), 6),
                           "n_periods": len(pts),
                           "periods": hashlib.sha256("|".join(b for b, _ in pts).encode()).hexdigest()[:16]}
                c.execute("SELECT dq_ctl.mem_pin_baseline('MEASURE', %s, %s, %s::jsonb)",
                          (f"{rid}@{node}", auth, json.dumps(content, sort_keys=True)))
