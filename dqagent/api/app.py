"""Control-plane API (DESIGN §8; W1_SPINE §3). The Tower is an untrusted client.

* Every request verifies the bearer JWS. Scope = authorities for which the person holds an
  EFFECTIVE duty (token group claim AND roster grant). RLS in Postgres enforces the same scope.
* Read endpoints never return raw values: sample tokens are opaque, and cross-authority
  aggregates pass through presentation.suppress.
* Decisions go through DecisionService. EVERY decision is a challenge-bound step-up:
  POST /challenge {decision, quarantine_row_seqs | (checker) maker_decision_id} → challenge;
  the Tower obtains an IdP step-up with nonce = challenge; POST /decide or /confirm carries
  that step_up_token. The session bearer only authenticates reads. Releases happen only in
  the publisher.
* A caller with no effective release scope gets 403 on scoped endpoints. /audit requires the
  audit duty and runs as dq_auditor. Reveal purposes are stored only as a hash.
"""
from __future__ import annotations

import datetime as _dt
import decimal
import hashlib
import json
import re
import threading
import time
from collections import OrderedDict, defaultdict
from types import SimpleNamespace

import psycopg
from fastapi import Body, Depends, FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from psycopg.rows import dict_row

from dqagent import llm as _llm
from dqagent.cp.trace_schema import EVAL_SUITES
from dqagent.hitl.decisions import STEP_UP_MAX_AGE_S, Denied, Replay, Stale
from dqagent.hitl.idp import STEP_UP_ACR, verify
from dqagent.model.outcome import Disposition as D, worst_of
from dqagent.presentation.suppress import suppress_cells

OPERATIONS_AUTHORITY = "AUTH_OPERATIONS"     # = runtime.OPERATIONS_AUTHORITY (§10b operations duty, platform-wide)
AUTH_OF_GROUP = {"medicaid": "AUTH_MEDICAID", "snap": "AUTH_SNAP", "chip": "AUTH_CHIP", "tanf": "AUTH_TANF"}

# Part E (PARTE_DESIGN §1.1, §3, §7, §9) --------------------------------------------------------------------------
TIER_ORDER = ("L1", "L2", "L3", "L4")
GENESIS_LABELS = {"NOT_RATIFIED": "NOT_RATIFIED", "TEST_GENESIS": "TEST_GENESIS (T1)",
                  "TEST_GENESIS_PLUS": "TEST_GENESIS + governed changes (T1)", "RATIFIED": "RATIFIED"}
NON_FAILING = ("PASS", "ADVISORY", "TA_WARN", "NOT_APPLICABLE", "STATE_WAIVED", "NOT_IN_SCOPE")
VETO_REASON_CODES = ("EVIDENCE_INCOMPLETE", "DATA_SUSPECT", "POLICY_CONCERN", "DEPENDENCY_CONCERN")
from dqagent.cp.governance_schema import FEED_ACK_CODES, FEED_ACK_CODES_BY_KIND  # noqa: E402 - one definition
EXPLAIN_CODES = ("TIMING", "LEGACY_DEFECT", "NEW_DEFECT", "DEFINITION", "FIXED")
STEP_UP_EXPLAIN_CODES = ("LEGACY_DEFECT", "DEFINITION")
CHART_LABEL = {"xmr": "XmR", "p": "p", "psi": "PSI"}
MASKED_CLASSES = ("identity", "clinical", "part2")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
AUDIT_LABELS = {
    "TRANSITION": "Unit changed state", "MODE_A": "Unit released automatically (mode A)",
    "MANIFEST_PUBLISHED": "Release manifest published", "MANIFEST_RECORD_RECOVERED": "Release manifest record recovered",
    "REVEAL": "Record values revealed for a stated purpose", "REVEAL_EXPIRED": "Reveal view expired and was dropped",
    "CONFIG_CR_PROPOSED": "Change request proposed", "CONFIG_CR_APPROVED": "Change request approved",
    "CONFIG_CR_REJECTED": "Change request rejected", "CONFIG_CR_APPLIED": "Change request applied",
    "AUTONOMY_DEMOTED": "Agent tier lowered automatically", "EVAL_ATTESTED": "Evaluation run attested",
    "FEED_ITEM_ACKNOWLEDGED": "Feed item acknowledged", "PARALLEL_RUN_EXPLAINED": "Parallel-run break explained",
    "LOOKBACK_ACCEPTED": "Look-back item accepted", "LOOKBACK_WITHDRAWN": "Look-back item withdrawn",
}


def audit_label(event: str) -> str:
    if event in AUDIT_LABELS:
        return AUDIT_LABELS[event]
    return event.replace("_", " ").lower().capitalize()


def _jsonable(v):
    if isinstance(v, decimal.Decimal):
        return float(v)
    if isinstance(v, (_dt.datetime, _dt.date)):
        return v.isoformat()
    return v


def _row(r) -> dict:
    return {k: _jsonable(v) for k, v in dict(r).items()}


def _small(n) -> bool:
    return n is not None and 1 <= int(n) <= 10


def ref_ok(r) -> bool:
    return (isinstance(r, dict) and sorted(r) == ["facts_hash", "text_hash"]
            and all(isinstance(r[k], str) and _HEX64.match(r[k]) for k in r))


INTAKE_AUTHORITY = "AUTH_INTAKE"     # feed-level (pre-split) items: the intake duty's authority


def feed_scope(p) -> list[str]:
    """Live-smoke L1: the authorities whose FEED items a caller sees and acknowledges. Feed items are filed under the
    feed's pre-split authority (AUTH_INTAKE) or a single program authority; a holder of the intake duty
    (release:AUTH_INTAKE, token claim AND roster) sees the intake ones, a release scope sees its own. Nobody else."""
    sc = set(p["_scope"]) | ({INTAKE_AUTHORITY} if f"release:{INTAKE_AUTHORITY}" in p["_duties"] else set())
    if not sc:
        raise HTTPException(403, "no effective release or intake duty (token claim AND roster) in any authority")
    return sorted(sc)


def create_app(services) -> FastAPI:
    """services: object exposing cp, dp, idp (jwks), decisions, publisher, verifier, harness, policy."""
    app = FastAPI(title="DQ Agent Control Plane", version="0.1.0-w1")
    app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:3000"], allow_methods=["*"],
                       allow_headers=["*"])

    def principal(authorization: str = Header(default="")):
        if not authorization.startswith("Bearer "):
            raise HTTPException(401, "bearer token required")
        try:
            claims = verify(authorization[7:], services.idp.jwks())
        except Exception as e:  # noqa: BLE001
            raise HTTPException(401, f"invalid token: {type(e).__name__}") from e
        with services.cp.as_role("dq_api") as c:
            roster = c.execute("SELECT duty, authority FROM dq_ctl.roster_current WHERE person_id=%s",
                               (claims["emp_id"],)).fetchall()
        groups = set(claims.get("groups", []))
        eff = [r for r in roster
               if f"dq-duty-{r['duty']}-{r['authority'].removeprefix('AUTH_').lower()}" in groups
               # §10b: a platform-wide duty (operations) is claimed by the group dq-duty-<duty> without an authority
               or (r["authority"] == OPERATIONS_AUTHORITY and f"dq-duty-{r['duty']}" in groups)]
        claims["_scope"] = sorted({r["authority"] for r in eff if r["duty"] == "release" and r["authority"] != "AUTH_INTAKE"})
        claims["_duties"] = sorted({f"{r['duty']}:{r['authority']}" for r in eff})
        return claims

    def scope(p) -> list[str]:
        if not p["_scope"]:
            raise HTTPException(403, "no effective release duty (token claim AND roster) in any authority")
        return p["_scope"]

    # -- Part E §7 / Part F P1-1 §E: mandatory explanations -------------------------------------------------------
    # The explainer is called through ONE adapter, `_explain_raw`, which goes through ExplainService: the cache
    # (keyed by subject, facts and the prompt / check / template versions), the EXPLAIN trace, llm_calls and the
    # guardrail events. Every call site names the object's `subject` and the `authorities` its rows are read under.
    # Facts are built only by dqagent.llm.facts: masked classes and Part 2 units withheld, small cells suppressed.
    # The LLM never decides anything.
    llm_cfg = _llm.load_config()
    issued: OrderedDict = OrderedDict()          # (subject, facts_hash, text_hash) this server has shown
    shown_subject: OrderedDict = OrderedDict()   # (facts_hash, text_hash) shown -> its subject (I2: binding per subject)
    bound: OrderedDict = OrderedDict()           # step-up challenge -> explanation_ref bound at /challenge
    _elock = threading.Lock()

    _svc: dict = {}

    def explain_service():
        """Built on first use (the bound runtime's deployment governs the transport allow-list, R4-3, never
        $DQ_DEPLOYMENT); a refusal raises NotConfiguredError on the explain path, as before."""
        from dqagent.obs.explain_service import ExplainService
        with _elock:
            if "svc" not in _svc:
                _svc["svc"] = ExplainService(services.cp, cfg=llm_cfg, deployment=getattr(services, "deployment", None),
                                             state=services.state)
            return _svc["svc"]

    def _explain_raw(kind: str, facts: dict, masking: dict, *, subject: str, authorities) -> dict:
        from dqagent.obs.explain_service import public
        return public(explain_service().explain(kind, facts, subject=subject, authorities=authorities,
                                                masking=masking))

    def explainer_view() -> dict:
        """F-16: the REAL explainer status for every `llm_provider` / `explainer` field (never a literal). The
        transport is derived by llm.transport_for against the bound deployment (R4-3); a refusal is reported as
        status here (the explain path itself still refuses), never swallowed into a default. `source_mode` is passed
        through from llm.status() when that function supplies it, else None (it is not re-derived here)."""
        s = _llm.status(llm_cfg)
        try:
            transport, refused = _llm.transport_for(llm_cfg, getattr(services, "deployment", None)), None
        except Exception as e:  # noqa: BLE001 - NotConfiguredError: shown, not hidden
            transport, refused = None, str(e)
        return {"provider": s["provider"], "label": s["label"], "configured": s["configured"],
                "decisions": s.get("decisions", "deterministic"), "source_mode": s.get("source_mode"),
                "transport": transport, "x_transport_refused": refused}

    def _remember(store: OrderedDict, k, v, cap=20000):
        with _elock:
            store[k] = v
            store.move_to_end(k)
            while len(store) > cap:
                store.popitem(last=False)

    def ctx():
        """Per-request explanation context: effective classification (+ contract control tags) and Part 2 programs."""
        st = services.state
        arts = services.registry.artifacts(st)
        classes = dict((arts.get(f"{st}:classification") or {}).get("content") or {})
        from dqagent.runtime import Runtime
        masking = Runtime.masking_of(classes, services.contracts)        # one definition (runtime + api)
        feeds = (arts.get(f"{st}:feeds") or {}).get("content") or {}
        p2_programs = set(feeds.get("part2_programs") or [])
        p2_relations = set(feeds.get("part2_relations") or [])

        def explain(kind: str, facts: dict, *, subject: str, authorities) -> dict:
            e = _explain_raw(kind, facts, masking, subject=subject, authorities=sorted(set(authorities or ())))
            _remember(issued, (subject, e["facts_hash"], e["text_hash"]), True)
            _remember(shown_subject, (e["facts_hash"], e["text_hash"]), subject)
            return e

        def part2(u) -> bool:
            u = dict(u)
            return bool(u.get("part2")) or u.get("program_code") in p2_programs or u.get("node") in p2_relations

        return SimpleNamespace(arts=arts, classes=classes, masking=masking, explain=explain, part2=part2)

    def eref(e: dict) -> dict:
        return {"facts_hash": e["facts_hash"], "text_hash": e["text_hash"]}

    def check_ref(given, current: dict) -> dict:
        """At /challenge: the caller's explanation_ref must be the explanation this server shows for the object NOW
        (same facts; a text this server issued for them). No ref: the server binds the explanation it shows."""
        if given is None:
            return eref(current)
        if not ref_ok(given):
            raise HTTPException(422, {"status": "EXPLANATION_REF", "reason": "explanation_ref is {facts_hash, text_hash}"
                                                                           " (sha256 hex)"})
        subj = shown_subject.get((current["facts_hash"], current["text_hash"]))       # the object shown NOW
        if given["facts_hash"] != current["facts_hash"] or (
                given["text_hash"] != current["text_hash"] and (
                    subj is None or ((subj, given["facts_hash"], given["text_hash"]) not in issued
                                     and not explain_service().issued(subj, given["facts_hash"], given["text_hash"])))):
            raise HTTPException(422, {"status": "EXPLANATION_MISMATCH",
                                      "reason": "the explanation shown is not the current explanation of this object; "
                                                "reload it"})
        return {"facts_hash": given["facts_hash"], "text_hash": given["text_hash"]}

    def decide_ref(body: dict, nonce: str | None, current=None) -> dict:
        """At /decide: the explanation_ref bound to the step-up's challenge (or sent again in the body). Missing or
        different from the bound one -> 422."""
        given = body.get("explanation_ref")
        if given is not None and not ref_ok(given):
            raise HTTPException(422, {"status": "EXPLANATION_REF", "reason": "explanation_ref is {facts_hash, text_hash}"
                                                                           " (sha256 hex)"})
        b = bound.get(nonce) if nonce else None
        if given is None and b is None:
            raise HTTPException(422, {"status": "EXPLANATION_REF_MISSING",
                                      "reason": "no explanation is bound to this decision: request the challenge with "
                                                "the explanation_ref shown"})
        if given is not None and b is not None and given != b:
            raise HTTPException(422, {"status": "EXPLANATION_MISMATCH",
                                      "reason": "explanation_ref differs from the one bound at the challenge"})
        if b is None and current is not None:
            check_ref(given, current())
        return dict(b or given)

    def peek(tok: str):
        try:
            return verify(tok, services.idp.jwks(), max_age_s=STEP_UP_MAX_AGE_S)
        except Exception:  # noqa: BLE001 - the decision path reports the invalid token itself
            return None

    def publish_agent(c) -> str:
        return c.execute("SELECT dq_ctl.seat_agent('dq_publish_svc') a").fetchone()["a"]

    def tier_of(c, authorities, agent=None) -> str:
        """Effective RELEASE tier for a unit: the LOWEST over its authorities (missing entry = L1)."""
        agent = agent or publish_agent(c)
        auths = [a for a in authorities if a != "AUTH_INTAKE"] or list(authorities)
        tiers = [c.execute("SELECT dq_ctl.autonomy_tier(%s,%s,'RELEASE',%s) t", (services.state, agent, a)
                           ).fetchone()["t"] for a in auths]
        return min(tiers or ["L1"], key=TIER_ORDER.index)

    def veto_window(c, unit_key):
        w = c.execute("SELECT * FROM dq_ctl.veto_window_status WHERE unit_key=%s ORDER BY eval_version DESC, "
                      "window_id DESC LIMIT 1", (unit_key,)).fetchone()
        if not w or w["vetoed"]:
            return None
        return {"opened_at": w["opened_at"].isoformat(), "deadline": w["deadline"].isoformat(),
                "remaining_s": w["remaining_s"], "window_id": w["window_id"]}

    def unit_facts(c, u, res, held, x, tier, vw) -> dict:
        m = held.get("mode") or ("H_ON" if vw else "H_IN_2")
        need = services.policy.get("approvals", {}).get(m, {"persons": 2})
        have = c.execute("SELECT count(*) n FROM dq_ctl.decisions WHERE unit_key=%s AND round=%s",
                         (u["unit_key"], u["state_version"])).fetchone()["n"]
        applicable = [r for r in res if r["disposition"] not in ("NOT_APPLICABLE", "NOT_IN_SCOPE", "STATE_WAIVED")]
        f = {"unit_key": u["unit_key"], "relation": u["node"],
             "authority": next((a for a in u["authorities"] if a != "AUTH_INTAKE"), u["authorities"][0]),
             "state": u["state"], "tier": tier, "rules_checked": len(applicable),
             "rules_not_passed": sum(1 for r in applicable if r["disposition"] not in NON_FAILING),
             "part2": x.part2(u)}
        if u["state"] == "HELD":
            f["approvals_required"] = max(1, need["persons"])
            f["approvals_recorded"] = have
        if vw:
            f["veto_deadline"] = vw["deadline"]
        return f

    def unit_explanation(c, x, u):
        res = _latest(c, u["unit_key"])
        held = _held_mode(c, u["unit_key"]) if u["state"] != "EVALUATING" else {}
        vw = veto_window(c, u["unit_key"]) if u["state"] == "EVALUATING" else None
        tier = tier_of(c, u["authorities"])
        return (x.explain("unit_card", unit_facts(c, u, res, held, x, tier, vw), subject=f"unit:{u['unit_key']}",
                          authorities=u["authorities"]), res, held, vw, tier)

    def deployment_view() -> dict | None:
        """The control plane's derived binding (deployment, IdP kind, genesis kind) and its display label. Non-sensitive
        (R4-2). A pilot on the local IdP reads "Pilot (local IdP - not production)"."""
        if not hasattr(services, "deployment_info"):
            return None
        return services.deployment_info()

    @app.get("/api/v1/tenant")
    def tenant():
        """Public, display-only: who this agent serves (from the tenant profile kept outside the repository)."""
        from dqagent.tenant import public_view
        return {**public_view(services.tenant), "deployment": deployment_view()}

    @app.get("/api/v1/me")
    def me(p=Depends(principal)):
        org = (services.tenant.get("people") or {}).get(p["emp_id"])
        return {"person_id": p["emp_id"], "name": p["name"], "duties": p["_duties"], "authorities": p["_scope"],
                "org": org, "org_label": (services.tenant.get("organisations") or {}).get(org)}

    @app.get("/api/v1/summary")
    def summary(p=Depends(principal)):
        with services.cp.as_role("dq_api", scope(p)) as c:
            held = c.execute("SELECT count(*) n, min(created_at) oldest FROM dq_ctl.units WHERE state='HELD'").fetchone()
            # I1 nit: the gating test is the ONE view (dq_ctl.abc_gating_failures), never re-derived here
            abc = c.execute("SELECT count(*) FILTER (WHERE a.disposition='PASS') ok, "
                            "count(*) FILTER (WHERE g.id IS NOT NULL) bad, "
                            "count(*) FILTER (WHERE a.disposition<>'PASS' AND g.id IS NULL) advisory "
                            "FROM dq_ctl.abc a LEFT JOIN dq_ctl.abc_gating_failures g ON g.id = a.id").fetchone()
            mine = c.execute("SELECT count(DISTINCT unit_key) n FROM dq_ctl.decisions WHERE person_id=%s",
                             (p["emp_id"],)).fetchone()
            genesis = c.execute("SELECT count(*) n FROM dq_ctl.config_artifacts WHERE cr_id <> 'TEST-GENESIS'"
                                ).fetchone()
            tg = c.execute("SELECT count(*) n FROM dq_ctl.config_artifacts WHERE cr_id = 'TEST-GENESIS'").fetchone()
            awaiting = c.execute("SELECT count(DISTINCT d.unit_key) n FROM dq_ctl.decisions d JOIN dq_ctl.units u "
                                 "USING (unit_key) WHERE u.state='HELD'").fetchone()
            # Part E A1: per HELD unit, its HELD time and mode (from the HELD event), and approvals this round
            hu = c.execute(
                "SELECT u.unit_key, u.created_at, h.at AS held_at, coalesce(h.payload->>'mode','H_IN_2') AS mode, "
                "(SELECT count(*) FROM dq_ctl.decisions d WHERE d.unit_key=u.unit_key AND d.round=u.state_version) AS have, "
                "clock_timestamp() AS now FROM dq_ctl.units u LEFT JOIN LATERAL (SELECT e.at, e.payload FROM "
                "dq_ctl.unit_events e WHERE e.unit_key=u.unit_key AND e.to_state='HELD' ORDER BY e.state_version DESC "
                "LIMIT 1) h ON true WHERE u.state='HELD'").fetchall()
        oldest = int((time.time() - held["oldest"].timestamp()) / 60) if held["oldest"] else 0
        gov = services.governance_ctx()
        from dqagent.governance.registry import iso_duration_seconds
        sla = services.policy.get("sla") or {}
        appr = services.policy.get("approvals") or {}
        ages, remaining, due = [], [], 0
        for r in hu:
            t0 = r["held_at"] or r["created_at"]
            ages.append(int((r["now"] - t0).total_seconds() // 60))
            if r["mode"] in sla:
                left = (t0 - r["now"]).total_seconds() + iso_duration_seconds(sla[r["mode"]])
                remaining.append(int(left // 60))           # floor: negative means overdue
            if r["have"] < max(1, (appr.get(r["mode"]) or {"persons": 2})["persons"]):
                due += 1
        gstate = ("NOT_RATIFIED" if not gov["ratified"] else "TEST_GENESIS" if tg["n"] and not genesis["n"] else
                  "TEST_GENESIS_PLUS" if tg["n"] else "RATIFIED")
        st = explainer_view()
        return {"genesis_state": gstate, "genesis_label": GENESIS_LABELS[gstate],
                "genesis_status": GENESIS_LABELS[gstate],
                "ruleset_version": gov["ruleset_version"], "drift": gov["drift"],
                "held_units": held["n"], "decisions_due": due, "awaiting_checker": awaiting["n"],
                "oldest_sla_minutes": oldest, "oldest_held_age_min": max(ages) if ages else None,
                "oldest_sla_remaining_min": min(remaining) if remaining else None, "mine": mine["n"],
                "abc": {"balanced": abc["ok"], "out_of_balance": abc["bad"], "advisory": abc["advisory"]},
                "deny_propagation_lag_s": services.deny_lag_s(), "pipeline_order_ok": services.harness_ok(),
                "llm_provider": st["provider"],
                "explainer": {"provider": st["provider"], "decisions": "deterministic", "label": st["label"],
                              "source_mode": st["source_mode"], "transport": st["transport"],
                              "x_transport_refused": st["x_transport_refused"]},
                "sim": services.sim_status() if hasattr(services, "sim_status") else
                {"enabled": False, "current_date": None, "queued": 0},
                "deployment": deployment_view()}

    def _latest(c, uk):
        return c.execute("SELECT DISTINCT ON (rule_id) * FROM dq_ctl.results WHERE unit_key=%s "
                         "ORDER BY rule_id, eval_version DESC, id DESC", (uk,)).fetchall()

    def _held_mode(c, uk):
        ev = c.execute("SELECT payload FROM dq_ctl.unit_events WHERE unit_key=%s AND to_state='HELD' "
                       "ORDER BY state_version DESC LIMIT 1", (uk,)).fetchone()
        return (ev["payload"] or {}) if ev else {}

    @app.get("/api/v1/queue")
    def queue(p=Depends(principal)):
        clusters = defaultdict(lambda: {"units": [], "dispositions": []})
        with services.cp.as_role("dq_api", scope(p)) as c:
            for u in c.execute("SELECT * FROM dq_ctl.units WHERE state='HELD' ORDER BY created_at").fetchall():
                res = _latest(c, u["unit_key"])
                # advisory basic-DQ warnings (TA_WARN) never define why a unit is held, so they do not cluster it
                failing = sorted(r["rule_id"] for r in res if r["disposition"] not in
                                 ("PASS", "ADVISORY", "TA_WARN", "NOT_APPLICABLE", "STATE_WAIVED", "NOT_IN_SCOPE"))
                sig = ",".join(failing) or "FLOOR_ONLY"
                key = (u["authorities"][0], u["node"], sig)
                cl = clusters[key]
                cl["units"].append(u["unit_key"])
                cl["dispositions"] += [D(r["disposition"]) for r in res]
                um = _held_mode(c, u["unit_key"]).get("mode", "H_IN_2")      # fail closed, same default as the card
                order = ["A", "H_ON", "H_IN", "H_IN_2"]
                cl["mode"] = max(cl.get("mode", "A"), um, key=order.index)   # cluster shows its STRICTEST unit
                cl["created"] = min(cl.get("created", u["created_at"]), u["created_at"])
            # Part E §1.4 / §9: an L3 unit waiting in EVALUATING behind an open (not vetoed) veto window is listed too
            for u in c.execute("SELECT u.* FROM dq_ctl.units u WHERE u.state='EVALUATING' AND EXISTS (SELECT 1 FROM "
                               "dq_ctl.veto_windows w WHERE w.unit_key=u.unit_key) ORDER BY u.created_at").fetchall():
                vw = veto_window(c, u["unit_key"])
                if not vw:
                    continue
                res = _latest(c, u["unit_key"])
                failing = sorted(r["rule_id"] for r in res if r["disposition"] not in NON_FAILING)
                sig = ",".join(failing) or "FLOOR_ONLY"
                cl = clusters[(u["authorities"][0], u["node"], sig, "VETO_WINDOW")]
                cl["units"].append(u["unit_key"])
                cl["dispositions"] += [D(r["disposition"]) for r in res]
                cl["mode"] = "H_ON"
                cl["created"] = min(cl.get("created", u["created_at"]), u["created_at"])
                cl["deadline"] = min(cl.get("deadline", vw["deadline"]), vw["deadline"])
            tiers = {a: tier_of(c, [a]) for a in {k[0] for k in clusters}}
        out = []
        sla = services.policy.get("sla") or {}
        from dqagent.governance.registry import iso_duration_seconds
        for key, cl in clusters.items():
            auth, node, sig = key[:3]
            m = cl["mode"]
            if m in sla and cl.get("created"):
                sla_due = (cl["created"] + _dt.timedelta(seconds=iso_duration_seconds(sla[m]))).isoformat()
            else:
                sla_due = cl["created"].isoformat()
            out.append({"cluster_id": "|".join(key), "authority": auth, "rule_ids": sig.split(","),
                        "node": node, "failure_signature": sig, "unit_count": len(cl["units"]),
                        "worst_disposition": worst_of(cl["dispositions"]).value, "required_mode": cl["mode"],
                        "sla_due": sla_due, "units": cl["units"], "tier": tiers[auth],
                        "veto_deadline": cl.get("deadline")})
        return sorted(out, key=lambda x: x["sla_due"])

    @app.get("/api/v1/units/{unit_key}")
    def card(unit_key: str, p=Depends(principal)):
        with services.cp.as_role("dq_api", scope(p)) as c:
            u = c.execute("SELECT * FROM dq_ctl.units WHERE unit_key=%s", (unit_key,)).fetchone()
            if not u:
                raise HTTPException(404, "not found in your scope")
            x = ctx()
            expl, res, _h, vw, tier = unit_explanation(c, x, u)
            held = _held_mode(c, unit_key)
            m = "H_ON" if vw else held.get("mode", "H_IN_2")
            need = services.policy["approvals"].get(m, {"persons": 2})
            p2 = x.part2(u)
            rel_rules = {r.id: r for r in services.rules if r.relation == u["node"]}
            disp_expl = {r["rule_id"]: x.explain("rule_disposition", {
                "rule_id": r["rule_id"], "disposition": r["disposition"], "failed": r["failed"],
                "evaluated": r["evaluated"], "relation": u["node"], "part2": p2,
                "gating": (rel_rules[r["rule_id"]].header.get("gating") if r["rule_id"] in rel_rules else None)},
                subject=f"unit:{unit_key}|rule:{r['rule_id']}", authorities=u["authorities"]) for r in res}
            decs = c.execute("SELECT person_id, seat, decision, created_at FROM dq_ctl.decisions WHERE unit_key=%s "
                             "AND round=%s", (unit_key, u["state_version"])).fetchall()
            abc = c.execute("SELECT a.hop, a.measure, a.src_in, a.tgt_out, a.rejected, a.filtered, a.residual, "
                            "a.disposition, (g.id IS NOT NULL) AS gating_failure FROM dq_ctl.abc a "
                            "LEFT JOIN dq_ctl.abc_gating_failures g ON g.id = a.id WHERE a.batch_id=%s ORDER BY a.id",
                            (u["batch_id"],)).fetchall()
            abc = list(abc) + [{"hop": r["hop"], "measure": r["measure"], "src_in": None, "tgt_out": None,
                                "rejected": None, "filtered": None, "residual": None, "disposition": r["disposition"],
                                "gating_failure": None, "x_owner": "AUTH_INTAKE (verdict only)"}
                               for r in c.execute("SELECT * FROM dq_ctl.intake_verdicts(%s)", (u["batch_id"],)).fetchall()]
            # known issues: MAKER only, never the checker (red-team F7). A viewer is the checker when another
            # person already holds this round's MAKER decision.
            mk = next((d for d in decs if d["seat"] == "MAKER"), None)
            hide_ki = bool(mk and mk["person_id"] != p["emp_id"])
            non_pass = sorted(r["rule_id"] for r in res if r["disposition"] in ("FAIL", "TA_WARN", "NOT_EVALUATED"))
            from dqagent.agent.memory import MemoryService
            known = [] if hide_ki else MemoryService.for_maker_card(c, non_pass)
            board = c.execute("SELECT seq, seat, kind, template, body FROM dq_ctl.agent_blackboard WHERE batch_id=%s "
                              "ORDER BY seq", (u["batch_id"],)).fetchall()
            deps = held.get("dependency_vector", {})
            dep_states = [c.execute("SELECT unit_key, state, node FROM dq_ctl.units WHERE unit_key=%s", (k,)).fetchone()
                          for k in deps]
            # F-07 SLA due: the SAME arithmetic as /summary (HELD event time, else created_at, + policy.sla[mode])
            hev = c.execute("SELECT at, coalesce(payload->>'mode','H_IN_2') AS mode FROM dq_ctl.unit_events "
                            "WHERE unit_key=%s AND to_state='HELD' ORDER BY state_version DESC LIMIT 1",
                            (unit_key,)).fetchone()
            # F-07 dry run: only a stored quarantine proposal (a MAKER decision this round) is a dry run
            qprops = c.execute("SELECT qp.decision_id, qp.row_seqs, qp.qset_hash FROM dq_ctl.quarantine_proposals qp "
                               "JOIN dq_ctl.decisions d USING (decision_id) WHERE d.unit_key=%s AND d.round=%s "
                               "ORDER BY qp.decision_id", (unit_key, u["state_version"])).fetchall()
        # F-07 dependencies: from services.dependencies matched to THIS node's contract domain; the depended unit's
        # own contract domain selects the declared edge. A same-domain predecessor is the contract's ordering edge.
        contracts = getattr(services, "contracts", {}) or {}
        my_domain = (contracts.get(u["node"]) or {}).get("domain")
        edges = {d["depends_on"]: d for d in (getattr(services, "dependencies", None) or [])
                 if d.get("domain") == my_domain}

        def dep_row(d):
            up = (contracts.get(d["node"]) or {}).get("domain")
            e = edges.get(up)
            if e:
                return {"domain": up, "policy": e["policy"], "unit": d["unit_key"], "state": d["state"],
                        "x_for_domain": my_domain, "x_basis": "harness.dependencies"}
            if up == my_domain and d["node"] == u["node"]:
                return {"domain": up, "policy": (contracts.get(u["node"]) or {}).get("ordering"),
                        "unit": d["unit_key"], "state": d["state"], "x_for_domain": my_domain,
                        "x_basis": "contract.ordering"}
            return {"domain": up, "policy": None, "unit": d["unit_key"], "state": d["state"],
                    "x_for_domain": my_domain, "x_basis": "undeclared"}

        sla = services.policy.get("sla") or {}
        if hev and hev["mode"] in sla:
            from dqagent.governance.registry import iso_duration_seconds
            t0 = hev["at"] or u["created_at"]
            sla_due, sla_reason = (t0 + _dt.timedelta(seconds=iso_duration_seconds(sla[hev["mode"]]))).isoformat(), None
        else:
            sla_due = None
            sla_reason = "unit never held" if not hev else f"no policy.sla for mode {hev['mode']}"
        if qprops:
            seqs = sorted({int(s) for qp in qprops for s in (qp["row_seqs"] or [])})
            dry_run, dry_reason = {"rows_quarantined": len(seqs), "chains": None,
                                   "x_chains_reason": "not computed",
                                   "x_proposals": [{"decision_id": qp["decision_id"], "qset_hash": qp["qset_hash"]}
                                                   for qp in qprops]}, None
        else:
            dry_run, dry_reason = None, "no proposal"
        rows = services.dp.fetchall(
            f"SELECT _dq_row_seq FROM {u['node']} WHERE _dq_unit_key = ? ORDER BY _dq_row_seq", [unit_key])
        pending = _pending(unit_key, scope(p))
        allowed = ["HOLD", "REJECT"] if u["state"] == "HELD" else []
        if u["state"] == "HELD":
            allowed[:0] = ["RELEASE", "QUARANTINE_AND_RELEASE"]
        if pending:                                   # a maker decision exists: only HOLD here; the checker uses /confirm
            allowed = ["HOLD"] if u["state"] == "HELD" else []
        return {
            "unit_key": unit_key, "batch_id": u["batch_id"], "pipeline": u["pipeline_id"], "node": u["node"],
            "program": u["program_code"], "authorities": u["authorities"], "state": u["state"],
            "state_version": u["state_version"], "fingerprint": u["fingerprint"], "fp_epoch": u["fp_epoch"],
            "required_mode": m, "mode_trace": held.get("mode_trace", []),
            "approvals": {"required": need["persons"], "have": len(decs),
                          "makers": [{"person_id": d["person_id"], "seat": d["seat"]} for d in decs]},
            "dispositions": [{"rule_id": r["rule_id"], "rule_hash": r["rule_hash"], "check_class": r["check_class"],
                              "priority": r["priority"], "disposition": r["disposition"],
                              "evaluated": r["evaluated"], "failed": r["failed"],
                              "metric": None if r["metric"] is None else float(r["metric"]),
                              "threshold": r["threshold"],
                              "sample_tokens": r["sample_tokens"], "explanation": disp_expl[r["rule_id"]]}
                             for r in res],
            "abc_before_after": [dict(a) for a in abc],
            "dependencies": [dep_row(d) for d in dep_states if d],
            "x_pending_decision": pending or None,
            "proposed_actions": [{"verb": "QUARANTINE_ROWS", "params": {"x_row_seqs": [r[0] for r in rows]},
                                  "dry_run": dry_run, "x_dry_run_reason": dry_reason}],
            "llm_hypothesis": None, "allowed_decisions": allowed,
            "advisory_warnings": [r["rule_id"] for r in res if r["disposition"] == "TA_WARN"],
            "agent_board": [{"seq": b["seq"], "seat": b["seat"], "kind": b["kind"], "template": b["template"],
                             "text": _render(b["template"], b["body"])} for b in board],
            "known_issues": known, "x_known_issues_hidden_for_checker": hide_ki, "sla_due": sla_due,
            "x_sla_reason": sla_reason,
            "x_row_seqs": [r[0] for r in rows],
            # Part E: the plain-language explanation (bound to the decision via /challenge), tier and veto window
            "explanation": expl, "tier": tier,
            "veto_window": {k: vw[k] for k in ("opened_at", "deadline", "remaining_s")} if vw else None,
            "x_part2": p2,
        }

    def _pending(unit_key, sc) -> dict:
        with services.cp.as_role("dq_api", sc) as c:
            u = c.execute("SELECT state_version FROM dq_ctl.units WHERE unit_key=%s", (unit_key,)).fetchone()
            if not u:
                return {}
            d = c.execute("SELECT d.decision_id, d.decision, d.rationale, d.person_id, qp.row_seqs FROM dq_ctl.decisions d "
                          "LEFT JOIN dq_ctl.quarantine_proposals qp USING (decision_id) "
                          "WHERE d.unit_key=%s AND d.round=%s AND d.seat='MAKER'", (unit_key, u["state_version"])).fetchone()
        if not d:
            return {}
        q = d["row_seqs"] or []
        return {"decision_id": d["decision_id"], "decision": d["decision"], "rationale": d["rationale"],
                "maker_person_id": d["person_id"], "quarantine_row_seqs": q}

    def _err(e):
        if isinstance(e, Stale):
            raise HTTPException(409, {"status": "STALE", "reason": str(e)})
        if isinstance(e, Replay):
            raise HTTPException(409, {"status": "REPLAY", "reason": str(e)})
        if isinstance(e, Denied):
            raise HTTPException(403, {"status": "DENIED", "reason": str(e)})
        raise e

    DMAP = {"RELEASE": "RELEASE", "QUARANTINE_AND_RELEASE": "QUARANTINE_AND_RELEASE", "REJECT": "REJECT"}

    @app.post("/api/v1/units/{unit_key}/challenge")
    def challenge(unit_key: str, body: dict = Body(default={}), p=Depends(principal)):
        """Maker: {decision, quarantine_row_seqs}. Checker: {} (binds to the pending maker decision)."""
        sc = scope(p)
        ref = check_ref(body.get("explanation_ref"), current_unit_expl(unit_key, sc))
        pend = _pending(unit_key, sc)
        if pend:
            chal = services.decisions.challenge(sc, [unit_key], pend["decision"],
                                                {unit_key: pend["quarantine_row_seqs"]}, pend["decision_id"])
        else:
            if body.get("decision") not in DMAP:
                raise HTTPException(422, "decision required (RELEASE | QUARANTINE_AND_RELEASE | REJECT)")
            chal = services.decisions.challenge(sc, [unit_key], DMAP[body["decision"]],
                                                {unit_key: body.get("quarantine_row_seqs", [])})
        _remember(bound, chal, ref)
        return {"challenge": chal, "step_up_url": services.idp_url + "/token",
                "x_seat": "CHECKER" if pend else "MAKER", "explanation_ref": ref}

    def current_unit_expl(unit_key, sc) -> dict:
        with services.cp.as_role("dq_api", sc) as c:
            u = c.execute("SELECT * FROM dq_ctl.units WHERE unit_key=%s", (unit_key,)).fetchone()
            if not u:
                raise HTTPException(404, "not found in your scope")
            return unit_explanation(c, ctx(), u)[0]

    def unit_ref_for_decision(unit_key, body, sc) -> dict | None:
        """Stale evidence is reported first (409, as before Part E); then the explanation bound at the challenge."""
        with services.cp.as_role("dq_api", sc) as c:
            u = c.execute("SELECT state, state_version, fingerprint FROM dq_ctl.units WHERE unit_key=%s",
                          (unit_key,)).fetchone()
        if u and u["state"] == "HELD" and "expected_state_version" in body and (
                u["state_version"] != body.get("expected_state_version")
                or ("bound_fingerprint" in body and u["fingerprint"] != body.get("bound_fingerprint"))):
            raise HTTPException(409, {"status": "STALE", "reason": f"{unit_key} changed (state_version "
                                                                  f"{u['state_version']})"})
        claims = peek(body["step_up_token"])
        if claims is None:
            return None                       # DecisionService reports the invalid step-up (401)
        return decide_ref(body, claims.get("nonce"), lambda: current_unit_expl(unit_key, sc))

    def _with_ref(fn, ref) -> dict:
        """Seat 2a: DecisionService.decide gains explanation_ref (stored in decisions.explanation_ref); passed once
        it exists."""
        import inspect
        return {"explanation_ref": ref} if ref and "explanation_ref" in inspect.signature(fn).parameters else {}

    def decision_root(unit_key: str, sc, person: str, base: str):
        """Part F P1-1: a DECISION root around a person's decision, so the decision rows and the publish cycle that
        follows (after_decision) nest under it. Its id is fixed at entry from the step-up's single-use jti (DEC-J<jti>;
        the decision id is only minted inside the decision service), and it carries the unit's authorities, so an
        in-scope reviewer can read it."""
        from dqagent.obs import tracer
        if not hasattr(services, "_root_args") or not tracer.enabled(getattr(services, "deployment", None)):
            return tracer.root("DECISION", None, cp=None, state=services.state)          # the no-op root
        # C1: everything here only feeds the trace; a failure runs the decision untraced, never refuses it
        try:
            with services.cp.as_role("dq_api", sc) as c:
                u = c.execute("SELECT authorities FROM dq_ctl.units WHERE unit_key=%s", (unit_key,)).fetchone()
        except Exception:  # noqa: BLE001 - observe only
            u = None
        tid = tracer.mint(services.cp, base)       # None when the trace store is unreachable: a no-op root
        return tracer.root("DECISION", tid, parent_trace_id=None, person_id=person,
                           authorities=(u["authorities"] if u else []), **services._root_args())

    def _dec_base(token: str | None, *parts) -> str:
        claims = peek(token) if token else None
        jti = (claims or {}).get("jti") or ""
        if re.fullmatch(r"[A-Za-z0-9_.:-]{1,100}", jti):
            return f"DEC-J{jti}"
        return "DEC-H" + hashlib.sha256("|".join(map(str, parts)).encode()).hexdigest()[:16]

    @app.post("/api/v1/units/{unit_key}/decide")
    def decide(unit_key: str, body: dict = Body(...), p=Depends(principal)):
        if body.get("decision") == "HOLD":
            return {"decision_id": None, "status": "APPLIED", "reason": "unit remains HELD"}
        if body.get("decision") not in DMAP:
            raise HTTPException(422, "unknown decision")
        if len(body.get("rationale", "").strip()) < 20:
            raise HTTPException(422, "rationale must be at least 20 characters")
        if not body.get("step_up_token"):
            raise HTTPException(428, {"status": "STEP_UP_REQUIRED",
                                      "reason": "obtain a step-up over POST /challenge first"})
        ref = unit_ref_for_decision(unit_key, body, scope(p))
        with decision_root(unit_key, scope(p), p["emp_id"],
                           _dec_base(body["step_up_token"], unit_key, p["emp_id"])) as droot:
            try:
                r = services.decisions.decide(
                    scope(p), body["step_up_token"], [unit_key], DMAP[body["decision"]], body["rationale"],
                    {unit_key: body["expected_state_version"]}, {unit_key: body["bound_fingerprint"]},
                    {unit_key: body.get("quarantine_row_seqs", [])}, session_person=p["emp_id"],
                    **_with_ref(services.decisions.decide, ref))
            except (Stale, Denied, Replay) as e:
                _err(e)
            except PermissionError as e:
                raise HTTPException(401, {"status": "STEP_UP_INVALID", "reason": str(e)}) from e
            res = r["results"][0]
            services.after_decision()
        return {"decision_id": res["decision_id"], "status": res["status"],
                "reason": f"{res['have']}/{res['required']} approvals", "x_missing_duties": res["missing_duties"],
                "trace_id": getattr(droot, "trace_id", None)}           # F-34: the DECISION root (None: untraced)

    @app.post("/api/v1/units/{unit_key}/confirm")
    def confirm(unit_key: str, body: dict = Body(...), p=Depends(principal)):
        sc = scope(p)
        with services.cp.as_role("dq_api", sc) as c:
            u = c.execute("SELECT * FROM dq_ctl.units WHERE unit_key=%s", (unit_key,)).fetchone()
        pend = _pending(unit_key, sc)
        if not u or not pend:
            raise HTTPException(404, "no pending maker decision on this unit in your scope")
        if not body.get("step_up_token"):
            raise HTTPException(428, {"status": "STEP_UP_REQUIRED", "reason": "obtain a step-up over POST /challenge first"})
        if body.get("decision_id") != pend["decision_id"]:
            raise HTTPException(409, {"status": "STALE", "reason": "decision_id is not this unit's pending maker decision"})
        claims = peek(body["step_up_token"])
        ref = decide_ref(body, claims.get("nonce"), lambda: current_unit_expl(unit_key, sc)) if claims else None
        with decision_root(unit_key, sc, p["emp_id"], _dec_base(body["step_up_token"], unit_key, p["emp_id"])) as droot:
            try:
                r = services.decisions.decide(
                    sc, body.get("step_up_token"), [unit_key], pend["decision"],
                    body.get("rationale") or "Checker independently reviewed the evidence on the card.",
                    {unit_key: body.get("expected_state_version", u["state_version"])}, {unit_key: u["fingerprint"]},
                    maker_decision_id=pend["decision_id"], session_person=p["emp_id"],
                    **_with_ref(services.decisions.decide, ref))
            except (Stale, Denied, Replay) as e:
                _err(e)
            except PermissionError as e:
                raise HTTPException(401, {"status": "STEP_UP_INVALID", "reason": str(e)}) from e
            services.after_decision()
        res = r["results"][0]
        return {"decision_id": res["decision_id"], "status": res["status"],
                "reason": f"{res['have']}/{res['required']} approvals", "x_missing_duties": res["missing_duties"],
                "trace_id": getattr(droot, "trace_id", None)}

    @app.post("/api/v1/units/{unit_key}/reveal")
    def reveal(unit_key: str, body: dict = Body(...), p=Depends(principal)):
        """Governed reveal: an authority-scoped, column-masked, expiring view IN THE DP. Values never pass through the CP."""
        if len(body.get("purpose", "")) < 10:
            raise HTTPException(422, "purpose required")
        with services.cp.as_role("dq_api", scope(p)) as c:
            u = c.execute("SELECT * FROM dq_ctl.units WHERE unit_key=%s", (unit_key,)).fetchone()
            if not u:
                raise HTTPException(404, "not found in your scope")
            # the purpose text may contain PHI: only its hash enters the immutable chain (SEC-W1-8 / SCP-W1-9)
            c.execute("SELECT dq_ctl.append_audit('REVEAL', %s)", (hashlib.sha256(
                f"{p['emp_id']}|{unit_key}|{body['purpose']}".encode()).hexdigest(),))
        ref, exp = services.reveal(u, p["emp_id"], body.get("row_seqs"))
        return {"reveal_ref": ref, "expires_at": exp}

    @app.get("/api/v1/pipelines")
    def pipelines(p=Depends(principal)):
        return services.pipelines_view(scope(p))

    @app.get("/api/v1/abc")
    def abc(batch_id: str | None = None, p=Depends(principal)):
        with services.cp.as_role("dq_api", scope(p)) as c:
            q = "SELECT * FROM dq_ctl.abc" + (" WHERE batch_id=%s" if batch_id else "") + " ORDER BY id"
            rows = c.execute(q, (batch_id,) if batch_id else ()).fetchall()
            intake = c.execute("SELECT * FROM dq_ctl.intake_verdicts(%s)", (batch_id,)).fetchall()
        cells = [{**dict(r), "value": r["src_in"]} for r in rows]
        cells += [{"batch_id": r["batch_id"], "hop": r["hop"], "measure": r["measure"], "program_code": None,
                   "submitter_id": "INTAKE (verdict only)", "value": None, "tgt_out": None, "rejected": None,
                   "quarantined": None, "deduplicated": None, "filtered": None, "held": None, "residual": None,
                   "disposition": r["disposition"], "authority": "AUTH_INTAKE", "suppressed": True} for r in intake]
        cells = suppress_cells(cells, set(scope(p)))
        return [{"batch_id": r["batch_id"], "hop": r["hop"], "measure": r["measure"], "program": r["program_code"],
                 "submitter_id": r["submitter_id"], "src_in": r["value"], "tgt_out": r["tgt_out"],
                 "rejected": r["rejected"], "quarantined": r["quarantined"], "deduplicated": r["deduplicated"],
                 "filtered": r["filtered"], "held": r["held"], "residual": r["residual"],
                 "disposition": r["disposition"], "x_suppressed": r.get("suppressed", False)} for r in cells]

    @app.get("/api/v1/rules")
    def rules(p=Depends(principal)):
        """The compiled catalog for this state: status (ACTIVE / NOT_APPLICABLE / STATE_WAIVED / NOT_BOUND /
        NOT_LOADED) with reason, gating, overlay (status / expiry), effective hash and the change request that
        approved this exact version."""
        scope(p)
        from dqagent.rules.model import describe
        gov = services.governance_ctx()
        with services.cp.as_role("dq_api") as c:
            appr = {(r["artifact_id"], r["semantic_hash"]): r["decision_ref"] for r in c.execute(
                "SELECT DISTINCT ON (artifact_id) artifact_id, semantic_hash, decision_ref FROM dq_ctl.registry_events "
                "WHERE artifact_id LIKE %s ORDER BY artifact_id, id DESC", (services.state + ":%",)).fetchall()}
        out = describe(services.rules)
        x = ctx()
        binds = rule_bindings_cached()
        for r in out:
            r["approved_by_cr"] = appr.get((f"{services.state}:{r['id']}@{r['entity']}", r["effective_hash"]))
            # Part E §5 / §9: sensitivity classes of the elements this rule binds ('none' is not listed)
            r["classification"] = sorted({x.classes.get(el, "none") for el in binds.get(r["id"], [])} - {"none"})
        return {"state": services.state, "ruleset_version": gov["ruleset_version"], "drift": gov["drift"],
                "ratified": gov["ratified"], "rules": out}

    _binds_cache: dict = {}

    def rule_bindings_cached() -> dict:
        """{rule_id: [elements]} for the effective configuration (recomputed when the ruleset version moves)."""
        key = services.ruleset_version
        if key not in _binds_cache:
            from dqagent.rules.cde import rule_bindings
            cfg = services.registry.effective(services.state)
            _binds_cache.clear()
            _binds_cache[key] = rule_bindings(services.state, cfg) if cfg else {}
        return _binds_cache[key]

    @app.get("/api/v1/coverage")
    def coverage(cde: bool = False, p=Depends(principal)):
        """Requirements (inventory) vs rules: a requirement without an ACTIVE rule shows as GAP or with its
        not-applicable / waived / not-loaded status — never as covered. ?cde=true adds the CDE / MVDQ view (Part E §4)."""
        sc = scope(p)
        out = _coverage()
        if cde:
            out["cde"] = cde_rows(sc)
        return out

    def cde_rows(sc) -> list[dict]:
        from dqagent.rules.cde import cde_status
        x = ctx()
        content = (x.arts.get(f"{services.state}:cde") or {}).get("content") or {}
        with services.cp.as_role("dq_api", sc) as c:
            res = [dict(r) for r in c.execute(
                "SELECT DISTINCT ON (unit_key, rule_id) unit_key, eval_version, rule_id, disposition, evaluated, failed "
                "FROM dq_ctl.results ORDER BY unit_key, rule_id, eval_version DESC, id DESC").fetchall()]
        out = []
        for r in cde_status(content, services.rules, res, rule_bindings_cached()):
            th = r["thresholds"]
            out.append({"element": r["element"], "domain": r.get("steward_domain"),
                        "steward_domain": r.get("steward_domain"), "completeness": r["completeness"],
                        "validity": r["validity"], "thresholds": th, "status": r["status"], "reason": r.get("reason"),
                        "class": x.classes.get(r["element"], "none"), "rules": r["rules"],
                        "explanation": x.explain("cde", {
                            "element": r["element"], "status": r["status"], "steward_domain": r.get("steward_domain"),
                            "completeness_rate": r["completeness"], "validity_rate": r["validity"],
                            "completeness_min": th.get("completeness_min"), "validity_min": th.get("validity_min")},
                            subject=f"cde:{r['element']}", authorities=sc)})
        return out

    def _coverage():
        from pathlib import Path

        from dqagent.rules.model import ROOT, coverage as cov
        inv = [ROOT / "rules" / "inventory" / "federal.csv"]
        st = ROOT / "rules" / "inventory" / "state" / f"{services.state}.csv"
        if st.exists():
            inv.append(st)
        import yaml

        from dqagent.rules.basic import basic_coverage
        out = cov(services.rules, [Path(x) for x in inv])
        # Basic DQ floor: domain x dimension. Undelivered domains show DECLARED_NOT_EXERCISED, never as covered.
        out["basic"] = basic_coverage(services.rules, yaml.safe_load(open(ROOT / "rules" / "elements.yaml")))
        return out

    @app.get("/api/v1/measures")
    def measures(batch_id: str | None = None, p=Depends(principal)):
        with services.cp.as_role("dq_api", scope(p)) as c:
            q = "SELECT batch_id,node,rule_id,numerator,denominator,value,thresholds,disposition,at FROM dq_ctl.measure_results"
            rows = c.execute(q + (" WHERE batch_id=%s" if batch_id else "") + " ORDER BY id DESC LIMIT 500",
                             (batch_id,) if batch_id else ()).fetchall()
        out = []
        for r in rows:
            # small cells are suppressed ON THE SERVER (denominator, numerator or complement 1..10): a raw count
            # never reaches the browser (post-build F8, extended to this endpoint after the Tower smoke)
            n, d = r["numerator"], r["denominator"]
            small = d is not None and (d < 11 or (n is not None and (0 < n < 11 or 0 < d - n < 11)))
            out.append(dict(r) | {"at": r["at"].isoformat(), "suppressed": small,
                                  "numerator": None if small else n, "denominator": None if small else d,
                                  "value": None if small or r["value"] is None else float(r["value"])})
        return out

    # -- agent state / blackboard / memory (Part C WS3) ---------------------------------------------------
    def _render(t, body):
        from dqagent.agent.seats import render
        try:
            return render(t, body)
        except Exception:  # noqa: BLE001 - an unknown template is shown raw, never dropped
            return t

    @app.get("/api/v1/agent/state")
    def agent_state(p=Depends(principal)):
        with services.cp.as_role("dq_api", scope(p)) as c:
            rows = c.execute("SELECT * FROM dq_ctl.agent_state ORDER BY batch_id, node").fetchall()
        st = explainer_view()
        return {"llm_provider": st["provider"], "explainer": st, "batches": [dict(r) for r in rows]}

    @app.get("/api/v1/agent/blackboard")
    def agent_board(batch_id: str | None = None, agent: str | None = None, limit: int = 200, p=Depends(principal)):
        """Part E §8 B1: entries carry their agent namespace; ?agent= filters by it. refs are typed text ids."""
        with services.cp.as_role("dq_api", scope(p)) as c:
            q = ("SELECT seq, agent, batch_id, authority, seat, person_id, kind, subject_type, subject_id, template, "
                 "body, refs, prev_hash, entry_hash, at FROM dq_ctl.agent_blackboard")
            where, args = [], []
            if batch_id:
                where.append("batch_id=%s")
                args.append(batch_id)
            if agent:
                where.append("agent=%s")
                args.append(agent)
            rows = c.execute(q + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY seq DESC LIMIT %s",
                             (*args, min(limit, 1000))).fetchall()
        # known-issue posts are visible to their proposer only (F7: they must never reach a checker)
        rows = [r for r in rows if r["kind"] != "MEMORY_REF" or r["person_id"] == p["emp_id"]]
        return [dict(r) | {"at": r["at"].isoformat(), "text": _render(r["template"], r["body"])} for r in rows]

    @app.post("/api/v1/agent/blackboard/notes")
    def agent_note(body: dict = Body(...), p=Depends(principal)):
        from dqagent.agent.memory import REASON_CODES
        sc = scope(p)
        if body.get("authority") not in sc or body.get("reason_code") not in REASON_CODES:
            raise HTTPException(422, f"authority in your scope and reason_code in {sorted(REASON_CODES)} required")
        with services.cp.as_role("dq_api", sc) as c:
            seq = c.execute("SELECT dq_ctl.bb_post(%s,%s,'NOTE','RULE',%s,'STEWARD_NOTE',%s::jsonb,NULL,%s) s",
                            (body.get("batch_id"), body["authority"], body["rule_id"],
                             __import__("json").dumps({"rule_id": body["rule_id"], "reason_code": body["reason_code"]}),
                             p["emp_id"])).fetchone()["s"]
        return {"seq": seq}

    @app.get("/api/v1/agent/memory")
    def agent_memory(p=Depends(principal)):
        with services.cp.as_role("dq_api", scope(p)) as c:
            rows = c.execute("SELECT memory_id, kind, scope_type, scope_id, authority, content, content_hash, "
                             "created_seat, proposed_by, approved_by, expires_at, status, at "
                             "FROM dq_ctl.agent_memory_current ORDER BY memory_id").fetchall()
        # KNOWN_ISSUE rows: a PROPOSED one is listed so a second person can review it; once APPROVED it is visible
        # only to its proposer and approver (F7: an approved known issue must never reach a checker's screen)
        rows = [r for r in rows if r["kind"] != "KNOWN_ISSUE" or r["status"] == "PROPOSED"
                or p["emp_id"] in (r["proposed_by"], r["approved_by"])]
        return [dict(r) | {"at": r["at"].isoformat(),
                           "expires_at": r["expires_at"].isoformat() if r["expires_at"] else None} for r in rows]

    @app.post("/api/v1/agent/memory/known-issues")
    def ki_propose(body: dict = Body(...), p=Depends(principal)):
        import datetime as _dt
        sc = scope(p)
        try:
            mid = services.memory.propose(sc, p["emp_id"], body["rule_id"], body["authority"], body["reason_code"],
                                          _dt.date.fromisoformat(body["expires"]))
        except Denied as e:
            _err(e)
        except Exception as e:  # noqa: BLE001 - database refusals (scope / expiry) are client errors
            raise HTTPException(422, {"status": "REFUSED", "reason": str(e).splitlines()[0]}) from e
        return {"memory_id": mid, "status": "PROPOSED"}

    @app.post("/api/v1/agent/memory/{memory_id}/challenge")
    def ki_challenge(memory_id: int, body: dict = Body(...), p=Depends(principal)):
        if body.get("verdict") not in ("APPROVED", "REJECTED"):
            raise HTTPException(422, "verdict APPROVED | REJECTED")
        try:
            return {"challenge": services.memory.challenge(scope(p), memory_id, body["verdict"]),
                    "step_up_url": services.idp_url + "/token"}
        except Denied as e:
            _err(e)

    @app.post("/api/v1/agent/memory/{memory_id}/decide")
    def ki_decide(memory_id: int, body: dict = Body(...), p=Depends(principal)):
        if not body.get("step_up_token"):
            raise HTTPException(428, {"status": "STEP_UP_REQUIRED", "reason": "obtain a step-up over /challenge first"})
        try:
            return services.memory.decide(scope(p), p["_duties"], p["emp_id"], memory_id, body.get("verdict"),
                                          body["step_up_token"])
        except (Denied, Replay) as e:
            _err(e)
        except PermissionError as e:
            raise HTTPException(401, {"status": "STEP_UP_INVALID", "reason": str(e)}) from e

    # -- governance (Part D) -------------------------------------------------------------------------------
    def gov_duty(p):
        if "governance:AUTH_RULES" not in p["_duties"]:
            raise HTTPException(403, "governance duty required (token claim AND roster)")

    @app.get("/api/v1/governance/state")
    def gov_state(p=Depends(principal)):
        gov_duty(p)
        g = services.governance_ctx()
        with services.cp.as_role("dq_api") as c:
            arts = c.execute("SELECT e.artifact_id, e.kind, e.version, e.op, e.content_hash, e.cr_id, e.at, "
                             "(SELECT string_agg(DISTINCT x.person_id, ',') FROM dq_ctl.config_cr_events x WHERE "
                             "x.cr_id = e.cr_id AND x.event='APPROVED') AS approved_by "
                             "FROM dq_ctl.config_effective e WHERE e.state=%s ORDER BY e.artifact_id",
                             (services.state,)).fetchall()
        return {"state": services.state, "ratified": g["ratified"], "ruleset_version": g["ruleset_version"],
                "drift": g["drift"], "unapproved_rules": g.get("unapproved", []),
                "artifacts": [dict(a) | {"at": a["at"].isoformat()} for a in arts]}

    def _cr_row(r):
        status = r["terminal"] or ("AWAITING_APPROVAL" if r["approvals"] < r["approvals_required"] else "APPROVED")
        return {"cr_id": r["cr_id"], "state": r["state"], "author": r["author"], "origin": r["origin"],
                "loosening": r["loosening"], "approvals": r["approvals"], "approvals_required": r["approvals_required"],
                "status": status, "at": r["at"].isoformat()}

    @app.get("/api/v1/governance/change-requests")
    def cr_list(status: str | None = None, p=Depends(principal)):
        gov_duty(p)
        with services.cp.as_role("dq_api") as c:
            rows = c.execute("SELECT s.*, r.rationale, r.items, r.impact FROM dq_ctl.config_cr_status s "
                             "JOIN dq_ctl.config_change_requests r USING (cr_id) ORDER BY s.at DESC").fetchall()
        out = []
        for r in rows:
            row = _cr_row(r) | {"rationale": r["rationale"],
                                "items": [{"artifact_id": i["artifact_id"], "kind": i["kind"], "op": i["op"]}
                                          for i in r["items"]],
                                "impact_summary": {**r["impact"].get("summary", {}),
                                                   "loosening_reasons": r["impact"].get("loosening_reasons", [])}}
            if status is None or row["status"] == status:
                out.append(row)
        return out

    def flat_cr_evidence(rows: list[dict]) -> list[dict]:
        """PFP :908, K3_DESIGN.md §6 item 7: mirrors `dq_ctl.cr_evidence`'s own columns 1:1, following the same
        "flatten the stored row for display" pattern `flat_track_record` already uses, above."""
        return [{"report_id": e["report_id"], "items_digest": e["items_digest"], "linked_by": e["linked_by"],
                 "at": _jsonable(e["at"])} for e in rows]

    @app.get("/api/v1/governance/change-requests/{cr_id}")
    def cr_detail(cr_id: str, p=Depends(principal)):
        gov_duty(p)
        with services.cp.as_role("dq_api") as c:
            r = c.execute("SELECT s.*, r.rationale, r.items, r.impact, r.base FROM dq_ctl.config_cr_status s "
                          "JOIN dq_ctl.config_change_requests r USING (cr_id) WHERE s.cr_id=%s", (cr_id,)).fetchone()
            if not r:
                raise HTTPException(404, "unknown change request")
            ev = c.execute("SELECT event, person_id, detail, at FROM dq_ctl.config_cr_events WHERE cr_id=%s ORDER BY id",
                           (cr_id,)).fetchall()
            cur = {a["artifact_id"]: a["content"] for a in c.execute(
                "SELECT artifact_id, content FROM dq_ctl.config_effective WHERE state=%s", (r["state"],)).fetchall()}
            # Part F P1-4 (K3-7, RESOLVED): additive -- the linked backtest evidence for this CR, oldest first
            evidence = c.execute("SELECT report_id, items_digest, linked_by, at FROM dq_ctl.cr_evidence "
                                 "WHERE cr_id=%s ORDER BY id", (cr_id,)).fetchall()
        items = [{**i, "current": cur.get(i["artifact_id"])} for i in r["items"]]
        main, imp_e = cr_explanations(ctx(), r)
        return _cr_row(r) | {"rationale": r["rationale"], "items": items,
                             "impact": tower_impact(r["items"], r["impact"]) | {"explanation": imp_e}, "base": r["base"],
                             "events": [dict(e) | {"at": e["at"].isoformat()} for e in ev],
                             "evidence": flat_cr_evidence(evidence),
                             "x_you_are_author": r["author"] == p["emp_id"], "explanation": main}

    @app.get("/api/v1/backtests/{report_id}")
    def backtest_detail(report_id: str, p=Depends(principal)):
        """PFP :906. Labels come from the stored row (never asserted by this route): `label` and `report` are
        read back exactly as `dq_ctl.backtest_put` wrote them."""
        gov_duty(p)
        with services.cp.as_role("dq_api") as c:
            r = c.execute("SELECT report_id, cr_id, report, label, digest, at FROM dq_ctl.backtest_reports "
                          "WHERE report_id=%s", (report_id,)).fetchone()
        if not r:
            raise HTTPException(404, "unknown backtest report")
        return {"report_id": r["report_id"], "cr_id": r["cr_id"], "label": r["label"], "digest": r["digest"],
                "at": _jsonable(r["at"]), "report": r["report"]}

    def flat_track_record(row: dict | None) -> dict:
        row = row or {}
        ev = ({"label": row.get("eval_label"), "code_sha256": row.get("eval_code_sha256"), "tests": row.get("eval_tests"),
               "failures": row.get("eval_failures"), "proposed_by": row.get("eval_proposed_by"),
               "confirmed_by": row.get("eval_confirmed_by"), "at": _jsonable(row.get("eval_confirmed_at")),
               "halves": {"deterministic": True, "llm": bool(row.get("eval_llm_half"))}}
              if row.get("eval_label") else None)
        # R4-5: the record never resets; `prior` holds every EARLIER code version's totals (API compatibility)
        prior = {"autonomous": int(row.get("prior_autonomous") or 0),
                 "h_on_released": int(row.get("prior_released_after_window") or 0),
                 "vetoes": int(row.get("prior_vetoes") or 0), "overrides": int(row.get("prior_overrides") or 0),
                 "denies": int(row.get("prior_denies") or 0),
                 "lookback_withdrawn": int(row.get("prior_lookback_withdrawn") or 0),
                 "since": _jsonable(row.get("prior_since")), "until": _jsonable(row.get("since"))}
        cv = row.get("current_version") or {}
        return {"autonomous": int(row.get("autonomous") or 0), "h_on_released": int(row.get("released_after_window") or 0),
                "vetoes": int(row.get("vetoes") or 0), "overrides": int(row.get("overrides") or 0),
                "denies": int(row.get("denies") or 0), "lookback_withdrawn": int(row.get("lookback_withdrawn") or 0),
                "human_released": int(row.get("human_released") or 0),
                "human_decisions": int(row.get("human_decisions") or 0),
                "since": _jsonable(row.get("since")), "eval_attestation": ev, "prior": prior,
                "current_version": {k: (v if k == "code_sha256" else int(v or 0)) for k, v in cv.items()}}

    def tier_change(items: list, impact: dict):
        """(artifact, authority, from, to, track-record row) of the first AUTONOMY tier this request changes."""
        if impact.get("demotion"):
            d = impact["demotion"]
            return (None, d.get("authority"), d.get("from_tier"), d.get("to_tier"), None)
        snap = ((impact.get("track_record") or {}).get("rows") or {})
        for it in items:
            if it.get("kind") != "AUTONOMY" or it.get("op") != "PUT":
                continue
            rows = {x["authority"]: x for x in snap.get(it["artifact_id"], [])}
            for auth, t in sorted((it.get("content") or {}).items()):
                frm = (rows.get(auth) or {}).get("tier") or "L1"
                if t != frm:
                    return (it["artifact_id"], auth, frm, t, rows.get(auth))
        return None

    def tower_impact(items: list, impact: dict) -> dict:
        """The stored impact unchanged, plus (for display only) the changed authority's track record flattened next
        to the SIGNED snapshot {rows, hash}; the hash stays the one the approvers sign."""
        out = dict(impact)
        if impact.get("track_record"):
            tc = tier_change(items, impact)
            out["track_record"] = {**flat_track_record(tc[4] if tc else None), **impact["track_record"]}
        return out

    def cr_explanations(x, r) -> tuple[dict, dict]:
        items, impact = r["items"], r["impact"] or {}
        kinds = sorted({i["kind"] for i in items})
        imp = {}
        tc = tier_change(items, impact)
        if tc:
            imp["tier_from"], imp["tier_to"] = tc[2], tc[3]
        if (impact.get("track_record") or {}).get("hash"):
            imp["track_record_hash"] = impact["track_record"]["hash"][:12]
        row = _cr_row(r)
        main = x.explain("change_request", {"cr_id": r["cr_id"], "kinds": kinds, "status": row["status"],
                                            "approvals_required": r["approvals_required"],
                                            "approvals_recorded": r["approvals"], "impact": imp},
                         subject=f"cr:{r['cr_id']}", authorities=())        # governance-wide: audit-only rows
        changed = len(impact.get("rules") or [])
        imp_e = x.explain("change_request", {"cr_id": r["cr_id"], "kinds": kinds,
                                             "impact": imp | ({"rules_changed": changed} if changed else {})},
                          subject=f"cr:{r['cr_id']}|impact", authorities=())
        return main, imp_e

    def cr_current(cr_id: str):
        with services.cp.as_role("dq_api") as c:
            r = c.execute("SELECT s.*, r.items, r.impact FROM dq_ctl.config_cr_status s JOIN dq_ctl.config_change_requests "
                          "r USING (cr_id) WHERE s.cr_id=%s", (cr_id,)).fetchone()
        if not r:
            raise HTTPException(404, "unknown change request")
        return cr_explanations(ctx(), r)[0]

    @app.post("/api/v1/governance/change-requests")
    def cr_propose(body: dict = Body(...), p=Depends(principal)):
        gov_duty(p)
        from dqagent.rules.ir import IRError
        if len((body.get("rationale") or "").strip()) < 10:
            raise HTTPException(422, "rationale of at least 10 characters required")
        if not isinstance(body.get("items"), list) or not body["items"] or not all(
                isinstance(i, dict) and {"artifact_id", "kind", "op"} <= set(i) for i in body["items"]):
            raise HTTPException(422, {"status": "REFUSED", "reason": "items: a non-empty list of {artifact_id, kind, op"
                                                                    "[, content]}"})
        try:
            r = services.registry.propose(body.get("state", services.state), p["emp_id"], body["rationale"], body["items"])
        except IRError as e:
            raise HTTPException(422, {"status": "REFUSED", "reason": str(e)}) from e
        except (psycopg.errors.CheckViolation, psycopg.errors.InsufficientPrivilege) as e:   # Part E item checks (C4)
            raise HTTPException(422, {"status": "REFUSED", "reason": str(e).splitlines()[0]}) from e
        # Part E §1.2: an AUTONOMY request carries the server's track-record snapshot (and its hash) in its impact
        return r | {"impact": tower_impact(body["items"], r["impact"])}

    @app.post("/api/v1/governance/change-requests/{cr_id}/challenge")
    def cr_challenge(cr_id: str, body: dict = Body(...), p=Depends(principal)):
        gov_duty(p)
        if body.get("verdict") not in ("APPROVED", "REJECTED"):
            raise HTTPException(422, "verdict APPROVED | REJECTED")
        ref = check_ref(body.get("explanation_ref"), cr_current(cr_id))
        try:
            chal = services.registry.challenge(cr_id, body["verdict"], ref)   # the explanation is bound into the nonce
        except Denied as e:
            _err(e)
        _remember(bound, chal, ref)
        return {"challenge": chal, "step_up_url": services.idp_url + "/token", "explanation_ref": ref}

    @app.post("/api/v1/governance/change-requests/{cr_id}/decide")
    def cr_decide(cr_id: str, body: dict = Body(...), p=Depends(principal)):
        gov_duty(p)
        if not body.get("step_up_token"):
            raise HTTPException(428, {"status": "STEP_UP_REQUIRED", "reason": "obtain a step-up over /challenge first"})
        claims = peek(body["step_up_token"])
        ref = decide_ref(body, claims.get("nonce")) if claims else None
        try:
            services.registry.decide(cr_id, body.get("verdict"), body["step_up_token"], p["emp_id"], p["_duties"],
                                     explanation_ref=ref)
        except (Denied, Replay) as e:
            _err(e)
        except PermissionError as e:
            raise HTTPException(401, {"status": "STEP_UP_INVALID", "reason": str(e)}) from e
        status = services.apply_change(cr_id)
        if status == "STALE":
            raise HTTPException(409, {"status": "STALE", "reason": "the configuration moved since this request was "
                                                                   "proposed; re-propose it"})
        return {"cr_id": cr_id, "status": status}

    # -- onboarding proposer (Part D, D3) -------------------------------------------------------------------
    @app.post("/api/v1/onboarding/profile")
    def ob_profile(body: dict = Body(...), p=Depends(principal)):
        gov_duty(p)
        from dqagent.onboarding.proposer import profile_relation
        allowed = set((services.governance_ctx() and services.onboarding_relations()) or [])
        rels = body.get("relations") or []
        bad = [r for r in rels if r not in allowed]
        if bad:                                   # red-team I4: only registered landing relations, never caller-named
            raise HTTPException(403, f"relations not in the onboarding allowlist: {bad}")
        return {"profiles": [profile_relation(services.dp, r) for r in rels]}

    @app.post("/api/v1/onboarding/propose-bindings")
    def ob_propose(body: dict = Body(...), p=Depends(principal)):
        """The server profiles the allowlisted relations itself (a caller cannot hand in a profile), stores the
        profile digest with the request, and MERGES the proposals into the current bindings (never removes)."""
        gov_duty(p)
        import copy as _c
        import hashlib as _hl
        import json as _j

        import yaml as _y

        from dqagent.onboarding.proposer import profile_relation, propose_bindings
        from dqagent.rules.ir import IRError
        from dqagent.rules.model import ROOT as _R
        state = body.get("state", services.state)
        if state != services.state:
            raise HTTPException(409, f"this control plane governs {services.state}; propose {state} on its own runtime")
        rels = body.get("relations") or []
        bad = [r for r in rels if r not in set(services.onboarding_relations())]
        if not rels or bad:
            raise HTTPException(403, f"relations must be in the onboarding allowlist: {bad or 'none given'}")
        profiles = [profile_relation(services.dp, r) for r in rels]
        res = propose_bindings(_y.safe_load(open(_R / "rules" / "elements.yaml")), profiles)
        cur = services.registry.artifacts(state).get(f"{state}:elements")
        if cur is None:
            raise HTTPException(409, "this state has no ratified configuration yet: propose its genesis first")
        merged = _c.deepcopy(cur["content"])
        merged.setdefault("entities", {}).update(res["entities"])
        merged.setdefault("columns", {}).update(res["columns"])
        item = {"artifact_id": f"{state}:elements", "kind": "BINDINGS", "op": "PUT", "content": merged}
        digest = _hl.sha256(_j.dumps(profiles, sort_keys=True).encode()).hexdigest()
        try:
            cr = services.registry.propose(state, p["emp_id"], body.get("rationale") or
                                           "Onboarding proposer draft: metadata-only element bindings", [item],
                                           origin="PROPOSER", origin_detail={"profile_digest": digest,
                                                                             "relations": rels,
                                                                             "proposals": res["proposals"],
                                                                             "unmatched": res["unmatched"]})
        except IRError as e:
            raise HTTPException(422, {"status": "REFUSED", "reason": str(e), "proposals": res["proposals"]}) from e
        return {"cr_id": cr["cr_id"], "proposals": res["proposals"], "unmatched": res["unmatched"],
                "profile_digest": digest, "impact": cr["impact"], "approvals_required": cr["approvals_required"]}

    @app.post("/api/v1/governance/change-requests/{cr_id}/withdraw")
    def cr_withdraw(cr_id: str, p=Depends(principal)):
        gov_duty(p)
        with services.cp.as_role("dq_api") as c:
            try:
                c.execute("SELECT dq_ctl.gov_withdraw(%s,%s)", (cr_id, p["emp_id"]))
            except Exception as e:  # noqa: BLE001
                raise HTTPException(403, {"status": "DENIED", "reason": str(e).splitlines()[0]}) from e
        return {"cr_id": cr_id, "status": "WITHDRAWN"}

    # -- measure series with small-cell suppression (Part D, D4 Measures screen) ---------------------------
    @app.get("/api/v1/measures/series")
    def measure_series(rule_id: str, node: str | None = None, p=Depends(principal)):
        with services.cp.as_role("dq_api", scope(p)) as c:
            rows = c.execute(
                "SELECT * FROM (SELECT DISTINCT ON (batch_id, node) batch_id, node, value, numerator, denominator, "
                "thresholds, disposition, long_disposition, min(id) OVER (PARTITION BY batch_id, node) first_id "
                "FROM dq_ctl.measure_results WHERE rule_id=%s AND (%s::text IS NULL OR node=%s) "
                "ORDER BY batch_id, node, id DESC) x ORDER BY first_id", (rule_id, node, node)).fetchall()
            bl = c.execute("SELECT content FROM dq_ctl.agent_memory WHERE kind='BASELINE' AND scope_id LIKE %s "
                           "ORDER BY memory_id DESC LIMIT 1", (rule_id + "@%",)).fetchone()
        pts = []
        for r in rows:
            # small cells (post-build F8): suppress when the denominator, the numerator or its complement is 1..10
            n, d = r["numerator"], r["denominator"]
            small = d is not None and (d < 11 or (n is not None and (0 < n < 11 or 0 < d - n < 11)))
            pts.append({"batch_id": r["batch_id"], "node": r["node"], "disposition": r["disposition"],
                        "long_disposition": r["long_disposition"], "suppressed": small,
                        "value": None if small or r["value"] is None else float(r["value"]),
                        "denominator": None if small else r["denominator"]})
        th = rows[-1]["thresholds"] if rows else {}
        return {"rule_id": rule_id, "thresholds": th, "baseline_median": (bl or {}).get("content", {}).get("median"),
                "points": pts}

    # -- look-back over releases made under a revoked rule version (Part D+) -------------------------------
    @app.get("/api/v1/lookback")
    def lookback(p=Depends(principal)):
        """Part E A7: in_live_manifest is computed LIVE from the current manifest; the value recorded when the item
        opened is kept as in_live_manifest_at_open."""
        with services.cp.as_role("dq_api", scope(p)) as c:
            rows = c.execute("SELECT * FROM dq_ctl.lookback_status ORDER BY item_id").fetchall()
            units = {u["unit_key"]: u for u in c.execute(
                "SELECT unit_key, node, program_code FROM dq_ctl.units WHERE unit_key = ANY(%s)",
                ([r["unit_key"] for r in rows],)).fetchall()}
        live = live_manifest()
        x = ctx()
        return [dict(r) | {"released_at": r["released_at"].isoformat() if r["released_at"] else None,
                           "at": r["at"].isoformat(), "status": r["resolution"] or "OPEN",
                           "in_live_manifest": r["unit_key"] in live, "in_live_manifest_at_open": r["in_live_manifest"],
                           "explanation": lookback_expl(x, r, live, units.get(r["unit_key"]))} for r in rows]

    def live_manifest() -> set:
        return {r[0] for r in services.dp.fetchall("SELECT unit_key FROM dq_rel.manifest WHERE version = "
                                                   "(SELECT version FROM dq_rel.manifest_pointer WHERE id=1)")}

    def lookback_expl(x, r, live, u) -> dict:
        rule_id = r["rule_artifact"].split(":", 1)[-1].split("@", 1)[0]
        return x.explain("lookback", {"lookback_id": r["item_id"], "unit_key": r["unit_key"], "rule_id": rule_id,
                                      "status": r["resolution"] or "OPEN", "in_live_manifest": r["unit_key"] in live,
                                      "part2": x.part2(u or {})},
                         subject=f"unit:{r['unit_key']}|lookback:{r['item_id']}",
                         authorities=(u or {}).get("authorities") or ())

    def lookback_current(item_id: int, sc) -> dict:
        with services.cp.as_role("dq_api", sc) as c:
            r = c.execute("SELECT * FROM dq_ctl.lookback_status WHERE item_id=%s", (item_id,)).fetchone()
            u = c.execute("SELECT unit_key, node, program_code FROM dq_ctl.units WHERE unit_key=%s",
                          (r["unit_key"],)).fetchone() if r else None
        if not r or not u:
            raise HTTPException(404, "unknown look-back item in your scope")
        return lookback_expl(ctx(), r, live_manifest(), u)

    def lookback_nonce(item_id, verdict, ref) -> str:
        tail = f"|{ref['facts_hash']}|{ref['text_hash']}" if ref else ""
        return hashlib.sha256(f"LOOKBACK|{item_id}|{verdict}{tail}".encode()).hexdigest()

    @app.post("/api/v1/lookback/{item_id}/reassess")
    def lookback_reassess(item_id: int, p=Depends(principal)):
        """Re-run the CURRENT approved rules of the unit's node against its released rows, read-only in the data
        plane (no lifecycle change). Records counts only."""
        sc = scope(p)
        with services.cp.as_role("dq_api", sc) as c:
            it = c.execute("SELECT * FROM dq_ctl.lookback_items WHERE item_id=%s", (item_id,)).fetchone()
        if not it:
            raise HTTPException(404, "unknown look-back item in your scope")
        g = services.governance_ctx()
        unapproved = set(g.get("unapproved") or [])
        out = []
        # the revoked version is never the yardstick (red-team I4): only rules whose CURRENT version is approved
        for r in [r for r in g["rules"] if f"{services.state}:{r.id}@{r.entity}" not in unapproved]:
            if r.relation != it["node"] or r.status != "ACTIVE" or r.kind != "row":
                continue
            app_sql = r.sql(services.dp.dialect, "applies") if r.applies_ir is not None else "TRUE"
            n_fail, n_eval = services.dp.fetchall(
                f"SELECT count(*) FILTER (WHERE ({r.fail_sql()}) IS TRUE), count(*) FROM {r.relation} h "
                f"WHERE h._dq_unit_key = ? AND ({app_sql}) IS TRUE", [it["unit_key"]])[0]
            if n_fail:
                out.append({"rule_id": r.id, "failed": n_fail, "evaluated": n_eval, "version": r.effective_hash[:12]})
        detail = {"failing_rules": out, "ruleset_version": g["ruleset_version"],
                  "replacement_version": next((r.effective_hash[:12] for r in g["rules"]
                                               if f"{services.state}:{r.id}@{r.entity}" == it["rule_artifact"]), None)}
        with services.cp.as_role("dq_api", sc) as c:
            c.execute("SELECT dq_ctl.lookback_reassessed(%s,%s,%s::jsonb)", (item_id, p["emp_id"],
                                                                         __import__("json").dumps(detail)))
        return {"item_id": item_id, **detail}

    @app.post("/api/v1/lookback/{item_id}/challenge")
    def lookback_challenge(item_id: int, body: dict = Body(...), p=Depends(principal)):
        sc = scope(p)
        if body.get("verdict") not in ("ACCEPTED", "WITHDRAWN"):
            raise HTTPException(422, "verdict ACCEPTED | WITHDRAWN")
        ref = check_ref(body.get("explanation_ref"), lookback_current(item_id, sc))
        chal = lookback_nonce(item_id, body["verdict"], ref)          # the explanation is bound into the nonce
        _remember(bound, chal, ref)
        return {"challenge": chal, "step_up_url": services.idp_url + "/token", "explanation_ref": ref}

    @app.post("/api/v1/lookback/{item_id}/decide")
    def lookback_decide(item_id: int, body: dict = Body(...), p=Depends(principal)):
        """A person resolves a look-back item: ACCEPTED (the release stands) or WITHDRAWN (DENY: removed from the
        released views by the existing breaker). Step-up bound to the item, verdict and the explanation shown."""
        sc = scope(p)
        tok = body.get("step_up_token")
        if not tok:
            raise HTTPException(428, {"status": "STEP_UP_REQUIRED", "reason": "obtain a step-up over /challenge first"})
        try:
            claims = verify(tok, services.idp.jwks(), max_age_s=STEP_UP_MAX_AGE_S)
        except Exception as e:  # noqa: BLE001
            raise HTTPException(401, {"status": "STEP_UP_INVALID", "reason": type(e).__name__}) from e
        ref = decide_ref(body, claims.get("nonce"))
        want = lookback_nonce(item_id, body.get("verdict"), ref)
        if claims.get("acr") != STEP_UP_ACR or claims.get("nonce") != want or claims["emp_id"] != p["emp_id"]:
            raise HTTPException(403, {"status": "DENIED", "reason": "step-up not bound to this look-back decision"})
        with services.cp.as_role("dq_api", sc) as c:
            try:
                c.execute("SELECT dq_ctl.lookback_decide(%s,%s,%s,%s,%s,%s::jsonb)",
                          (item_id, p["emp_id"], claims["jti"], body.get("verdict"), body.get("rationale") or "",
                           json.dumps(ref)))
            except Exception as e:  # noqa: BLE001
                raise HTTPException(409, {"status": "REFUSED", "reason": str(e).splitlines()[0]}) from e
        services.after_decision()
        return {"item_id": item_id, "status": body.get("verdict")}

    @app.get("/api/v1/audit")
    def audit(limit: int = 50, p=Depends(principal)):
        if "audit:AUTH_AUDIT" not in p["_duties"]:
            raise HTTPException(403, "audit duty required")
        with services.cp.as_role("dq_auditor") as c:
            rows = c.execute("SELECT seq, event, payload_digest, prev_hash, row_hash, at FROM dq_ctl.audit_log "
                             "ORDER BY seq DESC LIMIT %s", (min(limit, 500),)).fetchall()
        return [dict(r) | {"at": r["at"].isoformat(), "label": audit_label(r["event"])} for r in rows]

    parte_routes(app, services, principal, scope, SimpleNamespace(
        ctx=ctx, check_ref=check_ref, decide_ref=decide_ref, peek=peek, remember=lambda k, v: _remember(bound, k, v),
        tier_of=tier_of, veto_window=veto_window, unit_explanation=unit_explanation, current_unit_expl=current_unit_expl,
        flat_track_record=flat_track_record, gov_duty=gov_duty, live_manifest=live_manifest, err=_err,
        rule_bindings=rule_bindings_cached, llm_cfg=llm_cfg, decision_root=decision_root))
    sim_routes(app, services, principal, scope, ctx)
    from dqagent.api.ops_routes import ops_routes                # Part F F-01 / R4-1: batch ABC recovery
    ops_routes(app, services, principal)
    from dqagent.api.obs_routes import obs_routes                # Part F P1-1 §F: runs, spans, citations, guardrails
    obs_routes(app, services, principal)
    from dqagent.api.catalog_routes import catalog_routes        # PFP P1-2: per-DAG agent profile (read-only)
    catalog_routes(app, services, principal, scope)

    from fastapi.responses import JSONResponse

    from dqagent.obs.explain_service import ExplainUnavailable

    @app.exception_handler(ExplainUnavailable)
    def _explain_unavailable(_request, exc):                      # a fail-closed guardrail write failed (I3)
        return JSONResponse(status_code=503, content={"detail": {"status": "UNAVAILABLE",
                                                                 "reason": "explanation audit write failed"}})

    return app


def sim_routes(app: FastAPI, services, principal, scope, ctx) -> None:
    """Part E §10b nightly-run simulator (DEC-018). Demo mode only: every endpoint is 404 unless the runtime was
    started with --nightly. Run-next needs the operations duty (token group dq-duty-operations AND the roster grant)
    and is 409 when no night is queued or a night is already running (or a partly ingested file needs recovery, E6).
    E5: every endpoint needs a release scope (403 without one). Each summary is REBUILT per caller from the rows
    inside the caller's scope (Runtime.scope_summary over the stored per-id authority index): counts, ids, the
    gating / advisory split, held mode_reasons, totals and the explanation hold only in-scope numbers."""
    from dqagent.runtime import Runtime, SimConflict, SimUnavailable

    def enabled():
        if not getattr(services, "sim_enabled", False):
            raise HTTPException(404, "the nightly simulator runs only in demo mode (--nightly)")

    def scoped(p, runs: list[dict]) -> list[dict]:
        sc = scope(p)
        intake = f"release:{INTAKE_AUTHORITY}" in p["_duties"]
        x = ctx()
        out = []
        for r in runs:
            v = Runtime.scope_summary(r, sc, intake)
            v["explanation"] = x.explain("nightly_run", Runtime.nightly_facts(v),
                                         subject=f"nightly:{r.get('run_id') or r.get('night_date')}", authorities=sc)
            out.append(v)
        return out

    @app.get("/api/v1/sim/nights")
    def sim_nights(p=Depends(principal)):
        enabled()
        sc = scope(p)
        try:
            n = services.sim_nights()
            runs = {r["run_id"]: r for r in services.sim_runs()}
        except SimUnavailable as e:
            raise HTTPException(404, str(e)) from e
        intake = f"release:{INTAKE_AUTHORITY}" in p["_duties"]
        ops = any(d.startswith("operations:") for d in p["_duties"])
        auths = {}

        def queued_visible(name: str) -> bool:
            """E-04: a QUEUED night's file is listed when its route's relations' authorities meet the caller's
            scope (the intake duty sees every inbound file); an unrouted file only to the intake or operations
            duty. Fail closed: a file whose authorities cannot be resolved is hidden."""
            if name not in auths:
                auths[name] = services.file_authorities(name)
            a = auths[name]
            if a is None:
                return intake or ops
            return intake or bool(set(a) & set(sc))
        for night in n["nights"]:              # a night that ran lists only the files the caller may see
            if night["run_id"] in runs:
                night["files"] = Runtime.scope_summary(runs[night["run_id"]], sc, intake)["files"]
            else:
                night["files"] = [f for f in night["files"] if queued_visible(f)]
        return n

    @app.post("/api/v1/sim/run-next")
    def sim_run_next(p=Depends(principal)):
        enabled()
        if not any(d.startswith("operations:") for d in p["_duties"]):
            raise HTTPException(403, "operations duty required (token group dq-duty-operations AND roster grant)")
        scope(p)
        try:
            return scoped(p, [services.sim_run_next(p["emp_id"])])[0]
        except SimConflict as e:
            raise HTTPException(409, {"status": "CONFLICT", "reason": str(e)}) from e
        except SimUnavailable as e:
            raise HTTPException(404, str(e)) from e

    @app.get("/api/v1/sim/runs")
    def sim_runs(p=Depends(principal)):
        enabled()
        scope(p)
        return scoped(p, services.sim_runs())

    @app.get("/api/v1/sim/runs/{run_id}")
    def sim_run(run_id: str, p=Depends(principal)):
        enabled()
        scope(p)
        try:
            return scoped(p, [services.sim_runs(run_id)])[0]
        except KeyError as e:
            raise HTTPException(404, f"unknown run {run_id}") from e


def parte_routes(app: FastAPI, services, principal, scope, h) -> None:
    """Part E endpoints (PARTE_DESIGN §10 plus the shapes the Command Tower assumes; see the mock server)."""
    st = services.state

    def _sha(o) -> str:
        return hashlib.sha256(json.dumps(o, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()

    def _db_error(e: Exception):
        msg = str(e).splitlines()[0]
        if isinstance(e, psycopg.errors.NoDataFound):
            raise HTTPException(404, {"status": "NOT_FOUND", "reason": msg}) from e
        if isinstance(e, psycopg.errors.ObjectNotInPrerequisiteState):
            raise HTTPException(409, {"status": "REFUSED", "reason": msg}) from e
        if isinstance(e, (psycopg.errors.UniqueViolation, psycopg.errors.SerializationFailure)):
            raise HTTPException(409, {"status": "REFUSED", "reason": msg}) from e
        if isinstance(e, psycopg.errors.InsufficientPrivilege):
            raise HTTPException(403, {"status": "DENIED", "reason": msg}) from e
        if isinstance(e, psycopg.errors.CheckViolation):
            raise HTTPException(422, {"status": "REFUSED", "reason": msg}) from e
        raise e

    # -- explainer -------------------------------------------------------------------------------------------------
    @app.get("/api/v1/explainer")
    def explainer(p=Depends(principal)):
        s = _llm.status(h.llm_cfg)
        return {"provider": s["provider"], "configured": s["configured"], "fallback_count": s["fallback_count"],
                "decisions": s["decisions"], "label": s["label"], "source_mode": s.get("source_mode"),
                "transport": s.get("transport")}                        # F-35

    # -- agents, entitlements, autonomy (§1, §2) ---------------------------------------------------------------------
    @app.get("/api/v1/agents")
    def agents(p=Depends(principal)):
        scope(p)
        with services.cp.as_role("dq_api") as c:
            ags = c.execute("SELECT * FROM dq_ctl.agents_current WHERE state=%s ORDER BY agent_id", (st,)).fetchall()
            ents = c.execute("SELECT * FROM dq_ctl.entitlements_current WHERE state=%s ORDER BY agent_id, action_class",
                             (st,)).fetchall()
        return [{"agent_id": a["agent_id"], "name": a["name"], "version": a["version"], "status": a["status"],
                 "seats": list(a["seats"] or []), "cr_id": a["cr_id"],
                 "entitlements": [{"action_class": e["action_class"], "authorities": list(e["authorities"] or []),
                                   "not_before": _jsonable(e["not_before"]), "not_after": _jsonable(e["not_after"]),
                                   "live": bool(e["live"]), "cr_id": e["cr_id"]}
                                  for e in ents if e["agent_id"] == a["agent_id"]]} for a in ags]

    @app.get("/api/v1/autonomy")
    def autonomy(p=Depends(principal)):
        """Tier per (agent, action class, authority) in the caller's scope, with its track record (the counts since
        the latest tier change, ruleset change or eval attestation), the automatic demotions, and (K0 / B7) the
        SAME evidence and minimums a promotion change request's impact block would show -- `evidence` from
        `dq_ctl.evidence_status()` (the running code version's confirmed-attestation completeness) and `minimums`
        from `dq_ctl.promotion_minimums(state)` (the governed per-tier record requirement, e.g. the "demo minimums"
        label) -- so the Tower's Agents / attestation screen can show "evidence (check) (deterministic, demo)" and
        "record N/N (check)" without a second call. Both are state-wide, not per-authority, so every row in the
        response carries the same values; they are never hardcoded here, only read from the database functions
        `registry.py::track_record_snapshot` already uses for a change request's impact."""
        sc = scope(p)
        x = h.ctx()
        with services.cp.as_role("dq_api", sc) as c:
            c.execute("SELECT set_config('dq.prompt_version', %s, true)", (_llm.PROMPT_VERSION,))
            rows = c.execute("SELECT * FROM dq_ctl.autonomy_current WHERE state=%s ORDER BY agent_id, action_class, "
                             "authority", (st,)).fetchall()
            trs = {(t["agent_id"], t["action_class"], t["authority"]): dict(t)
                   for t in c.execute("SELECT * FROM dq_ctl.track_record").fetchall()}
            versions: dict = {}                       # R4-5: the per-code-version breakdown approvers see
            for v in c.execute("SELECT * FROM dq_ctl.track_record_by_version ORDER BY code_sha256 NULLS FIRST").fetchall():
                versions.setdefault((v["agent_id"], v["action_class"], v["authority"]), []).append(
                    {k: (v[k] if k == "code_sha256" else int(v[k] or 0)) for k in
                     ("code_sha256", "autonomous", "released_after_window", "human_released", "human_decisions",
                      "vetoes", "overrides", "denies", "lookback_withdrawn")})
            dems = c.execute("SELECT cr_id, impact, origin_detail, at FROM dq_ctl.config_change_requests "
                             "WHERE origin='DEMOTION' AND state=%s ORDER BY at, cr_id", (st,)).fetchall()
            evidence = c.execute("SELECT dq_ctl.evidence_status() e").fetchone()["e"]
            minimums = c.execute("SELECT dq_ctl.promotion_minimums(%s) m", (st,)).fetchone()["m"]
        out = []
        for r in rows:
            if r["authority"] not in sc:
                continue
            k = (r["agent_id"], r["action_class"], r["authority"])
            tr = h.flat_track_record(trs.get(k))
            tr["versions"] = versions.get(k, [])
            ds = []
            for d in dems:
                dm = (d["impact"] or {}).get("demotion") or {}
                if (dm.get("agent_id"), dm.get("action_class"), dm.get("authority")) != k:
                    continue
                od = d["origin_detail"] or {}
                ds.append({"at": d["at"].isoformat(), "from_tier": dm.get("from_tier"), "to_tier": dm.get("to_tier"),
                           "reason_code": od.get("reason_code"), "ref": od.get("ref"), "cr_id": d["cr_id"],
                           "explanation": x.explain("demotion", {
                               "agent": k[0], "action_class": k[1], "authority": k[2], "from_tier": dm.get("from_tier"),
                               "to_tier": dm.get("to_tier"), "reason_code": od.get("reason_code"), "ref": od.get("ref")},
                               subject=f"cr:{d['cr_id']}|demotion", authorities=[k[2]])})
            out.append({"agent_id": k[0], "action_class": k[1], "authority": k[2], "tier": r["tier"],
                        "ceiling_mode": r["ceiling_mode"], "cr_id": r["cr_id"], "track_record": tr,
                        "track_record_hash": _sha(tr), "demotions": ds, "evidence": evidence, "minimums": minimums,
                        "explanation": x.explain("autonomy", {
                            "agent": k[0], "action_class": k[1], "authority": k[2], "tier": r["tier"],
                            "ceiling_mode": r["ceiling_mode"],
                            "track_record": {"autonomous": tr["autonomous"], "after_veto_window": tr["h_on_released"],
                                             "vetoes": tr["vetoes"], "overrides": tr["overrides"],
                                             "denies": tr["denies"], "lookback_withdrawn": tr["lookback_withdrawn"]}},
                            subject=f"autonomy:{k[0]}|{k[1]}|{k[2]}", authorities=[k[2]])})
        return out

    @app.get("/api/v1/evals")
    def evals(suite: str | None = None, limit: int = Query(50, ge=1, le=200), p=Depends(principal)):
        """K0 / PFP P2-1 (B9): the recorded `eval_runs` a governance person picks a DETERMINISTIC run from when
        proposing an evidence bundle (POST /eval-attestations). A plain listing, most recent first -- no pending /
        status filtering semantics (that is the separate `GET /eval-attestations?status=PROPOSED` picker, E-13,
        deferred to K11). `dq_api` already holds SELECT on `dq_ctl.eval_runs` (trace_schema.py's R4-5 grants); no
        new grant is needed. Gated on the same governance duty as the attestation endpoints: an eval run names the
        running code's own truth, and only a governance person ever has a reason to pick one."""
        h.gov_duty(p)
        if suite is not None and suite not in EVAL_SUITES:
            raise HTTPException(422, f"suite one of {', '.join(EVAL_SUITES)}")
        with services.cp.as_role("dq_api") as c:
            rows = c.execute(
                "SELECT run_id, suite, source_mode, cases, passed, code_sha256, prompt_version, junit_sha256, at "
                "FROM dq_ctl.eval_runs WHERE %(suite)s::text IS NULL OR suite = %(suite)s "
                "ORDER BY at DESC, run_id DESC LIMIT %(limit)s", {"suite": suite, "limit": limit}).fetchall()
        return [{"run_id": r["run_id"], "suite": r["suite"], "source_mode": r["source_mode"], "cases": r["cases"],
                 "passed": r["passed"], "code_sha256": r["code_sha256"], "prompt_version": r["prompt_version"],
                 "junit_sha256": r["junit_sha256"], "at": _jsonable(r["at"])} for r in rows]

    @app.get("/api/v1/evals/diff")
    @app.get("/api/v1/eval-runs/diff")
    def eval_diff(left: str = Query(...), right: str = Query(...), p=Depends(principal)):
        """K11: compare two eval runs (left vs right) and report outcome delta."""
        h.gov_duty(p)
        with services.cp.as_role("dq_api") as c:
            l_run = c.execute("SELECT * FROM dq_ctl.eval_runs WHERE run_id=%s", (left,)).fetchone()
            r_run = c.execute("SELECT * FROM dq_ctl.eval_runs WHERE run_id=%s", (right,)).fetchone()
            if not l_run:
                raise HTTPException(404, f"left eval run {left} not found")
            if not r_run:
                raise HTTPException(404, f"right eval run {right} not found")
            try:
                l_cases = {cs["case_id"]: cs for cs in c.execute("SELECT * FROM dq_ctl.eval_cases WHERE run_id=%s", (left,)).fetchall()}
                r_cases = {cs["case_id"]: cs for cs in c.execute("SELECT * FROM dq_ctl.eval_cases WHERE run_id=%s", (right,)).fetchall()}
            except Exception:
                l_cases, r_cases = {}, {}
        all_case_ids = sorted(set(l_cases) | set(r_cases))
        diffs = []
        for cid in all_case_ids:
            lc = l_cases.get(cid)
            rc = r_cases.get(cid)
            if lc and rc:
                if lc["passed"] != rc["passed"] or lc["verdict"] != rc["verdict"]:
                    diffs.append({"case_id": cid, "left": {"verdict": lc["verdict"], "passed": lc["passed"]},
                                  "right": {"verdict": rc["verdict"], "passed": rc["passed"]}, "status": "CHANGED"})
            elif lc and not rc:
                diffs.append({"case_id": cid, "left": {"verdict": lc["verdict"], "passed": lc["passed"]}, "right": None, "status": "REMOVED"})
            elif rc and not lc:
                diffs.append({"case_id": cid, "left": None, "right": {"verdict": rc["verdict"], "passed": rc["passed"]}, "status": "ADDED"})
        return {
            "left": {"run_id": l_run["run_id"], "suite": l_run["suite"], "passed": l_run["passed"], "cases": l_run["cases"]},
            "right": {"run_id": r_run["run_id"], "suite": r_run["suite"], "passed": r_run["passed"], "cases": r_run["cases"]},
            "total_diffs": len(diffs),
            "diffs": diffs
        }

    @app.get("/api/v1/evals/{run_id}")
    def eval_run_detail(run_id: str, p=Depends(principal)):
        """K11: detailed view of an eval run and its individual cases."""
        h.gov_duty(p)
        with services.cp.as_role("dq_api") as c:
            r = c.execute("SELECT * FROM dq_ctl.eval_runs WHERE run_id=%s", (run_id,)).fetchone()
            if not r:
                raise HTTPException(404, f"eval run {run_id} not found")
            try:
                cases = c.execute("SELECT * FROM dq_ctl.eval_cases WHERE run_id=%s ORDER BY case_id", (run_id,)).fetchall()
            except Exception:
                cases = []
        return {
            "run_id": r["run_id"], "suite": r["suite"], "source_mode": r["source_mode"],
            "cases": r["cases"], "passed": r["passed"], "code_sha256": r["code_sha256"],
            "prompt_version": r["prompt_version"], "junit_sha256": r["junit_sha256"],
            "at": _jsonable(r["at"]),
            "eval_cases": [{"case_id": cs["case_id"], "kind": cs["kind"], "facts_hash": cs["facts_hash"],
                            "expected": cs["expected"], "verdict": cs["verdict"],
                            "reason_codes": cs.get("reason_codes", []), "passed": cs["passed"]} for cs in cases]
        }

    @app.get("/api/v1/eval-attestations")
    def list_eval_attestations(status: str | None = None, p=Depends(principal)):
        """E-13 / K11: List eval attestations with optional status filter (status=PROPOSED, CONFIRMED)."""
        h.gov_duty(p)
        with services.cp.as_role("dq_api") as c:
            if status == "PROPOSED":
                rows = c.execute(
                    "SELECT a.id, a.label, a.code_sha256, a.tests, a.failures, a.junit_sha256, a.person_id, "
                    "a.at, 'PROPOSED' as status, a.junit_sha256_llm, a.prompt_version, a.det_run_id, a.llm_run_id "
                    "FROM dq_ctl.eval_attestations a "
                    "WHERE a.id NOT IN (SELECT attestation_id FROM dq_ctl.eval_attestation_confirmations) "
                    "ORDER BY a.id DESC"
                ).fetchall()
            elif status == "CONFIRMED":
                rows = c.execute(
                    "SELECT a.*, 'CONFIRMED' as status FROM dq_ctl.eval_attestations_confirmed a ORDER BY a.id DESC"
                ).fetchall()
            else:
                rows = c.execute(
                    "SELECT a.id, a.label, a.code_sha256, a.tests, a.failures, a.junit_sha256, a.person_id, "
                    "a.at, CASE WHEN c.id IS NOT NULL THEN 'CONFIRMED' ELSE 'PROPOSED' END as status, "
                    "c.person_id as confirmed_by, c.at as confirmed_at, a.junit_sha256_llm, a.prompt_version, "
                    "a.det_run_id, a.llm_run_id "
                    "FROM dq_ctl.eval_attestations a "
                    "LEFT JOIN dq_ctl.eval_attestation_confirmations c ON c.attestation_id = a.id "
                    "ORDER BY a.id DESC"
                ).fetchall()
        return [{"id": r["id"], "label": r["label"], "code_sha256": r["code_sha256"], "tests": r["tests"],
                 "failures": r["failures"], "junit_sha256": r["junit_sha256"], "person_id": r["person_id"],
                 "status": r["status"], "confirmed_by": r.get("confirmed_by"),
                 "confirmed_at": _jsonable(r.get("confirmed_at")),
                 "halves": {"deterministic": True, "llm": r.get("junit_sha256_llm") is not None},
                 "det_run_id": r.get("det_run_id"), "llm_run_id": r.get("llm_run_id"),
                 "prompt_version": r.get("prompt_version")} for r in rows]

    @app.post("/api/v1/explanations/feedback")
    def explanation_feedback(body: dict = Body(...), p=Depends(principal)):
        """K11 / F-05 / F-22: record feedback on an explanation. Non-ACCURATE creates an eval candidate."""
        verdict = body.get("verdict")
        if verdict not in ("ACCURATE", "INACCURATE", "UNCLEAR", "UNSAFE"):
            raise HTTPException(422, "verdict must be one of ACCURATE, INACCURATE, UNCLEAR, UNSAFE")
        facts_hash = body.get("facts_hash")
        text_hash = body.get("text_hash")
        if not facts_hash or not text_hash:
            raise HTTPException(422, "facts_hash and text_hash required")
        comment = body.get("comment")
        trace_id = body.get("trace_id")
        span_id = body.get("span_id")
        with services.cp.as_role("dq_api", scope(p)) as c:
            fb_id = c.execute(
                "INSERT INTO dq_ctl.explanation_feedback(person_id, facts_hash, text_hash, trace_id, span_id, verdict, comment) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id",
                (p["emp_id"], facts_hash, text_hash, trace_id, span_id, verdict, comment)
            ).fetchone()["id"]
            cand_id = None
            if verdict != "ACCURATE":
                cand_id = c.execute(
                    "INSERT INTO dq_ctl.eval_candidates(feedback_id, facts_hash, verdict, reason_code, status) "
                    "VALUES (%s, %s, %s, %s, 'PROPOSED') RETURNING id",
                    (fb_id, facts_hash, verdict, body.get("reason_code"))
                ).fetchone()["id"]
                c.execute(
                    "INSERT INTO dq_ctl.eval_candidate_events(candidate_id, event, person_id) "
                    "VALUES (%s, 'PROPOSED', %s)",
                    (cand_id, p["emp_id"])
                )
        return {"status": "RECORDED", "feedback_id": fb_id, "candidate_id": cand_id}

    @app.get("/api/v1/annotations")
    @app.get("/api/v1/annotation-queue")
    def annotations(status: str | None = None, p=Depends(principal)):
        """K11: list annotation candidates and feedback queue for governance review."""
        h.gov_duty(p)
        with services.cp.as_role("dq_api") as c:
            rows = c.execute(
                "SELECT c.id, c.feedback_id, c.facts_hash, c.verdict, c.reason_code, c.status, c.cr_id, c.at, "
                "f.person_id as reviewer, f.trace_id, f.span_id "
                "FROM dq_ctl.eval_candidates c "
                "LEFT JOIN dq_ctl.explanation_feedback f ON f.id = c.feedback_id "
                "WHERE %(status)s::text IS NULL OR c.status = %(status)s "
                "ORDER BY c.at DESC LIMIT 100", {"status": status}
            ).fetchall()
        return [{"candidate_id": r["id"], "feedback_id": r["feedback_id"], "facts_hash": r["facts_hash"],
                 "verdict": r["verdict"], "reason_code": r["reason_code"], "status": r["status"],
                 "cr_id": r["cr_id"], "at": _jsonable(r["at"]), "reviewer": r["reviewer"],
                 "trace_id": r["trace_id"], "span_id": r["span_id"]} for r in rows]

    @app.post("/api/v1/eval-attestations")
    def eval_attest(body: dict = Body(...), p=Depends(principal)):
        """R4-5 / E4: a named governance person PROPOSES an evidence bundle {label, code_sha256, det_run_id,
        llm_run_id?} naming RECORDED eval runs (the database copies their junit hashes, tests, failures and prompt
        version). It is evidence only once a SECOND governance person confirms it: POST /eval-attestations/{id}/confirm."""
        h.gov_duty(p)
        try:
            llm_run = body.get("llm_run_id")
            args = (str(body["label"]), str(body["code_sha256"]), str(body["det_run_id"]),
                    None if llm_run is None else str(llm_run), p["emp_id"])
        except (KeyError, TypeError, ValueError) as e:
            raise HTTPException(422, "label, code_sha256, det_run_id (and optionally llm_run_id) required") from e
        if set(body) - {"label", "code_sha256", "det_run_id", "llm_run_id"}:
            raise HTTPException(422, "R4-5: the eval runs supply tests, failures and junit hashes; send only label, "
                                     "code_sha256, det_run_id, llm_run_id")
        with services.cp.as_role("dq_api") as c:
            try:
                i = c.execute("SELECT dq_ctl.eval_attest(%s,%s,%s,%s,%s) i", args).fetchone()["i"]
            except psycopg.Error as e:
                _db_error(e)
        return {"id": i, "status": "PROPOSED", "halves": {"deterministic": True, "llm": args[3] is not None}}

    @app.post("/api/v1/eval-attestations/{att_id}/confirm")
    def eval_attest_confirm(att_id: int, p=Depends(principal)):
        """E4: the second person. The database refuses the proposer, a non-governance person and (when the tenant
        names one) a person outside the approver organisation. A CONFIRMED attestation is promotion EVIDENCE for its
        code version (R4-5); it never resets the track record."""
        h.gov_duty(p)
        with services.cp.as_role("dq_api") as c:
            try:
                c.execute("SELECT dq_ctl.eval_attest_confirm(%s,%s)", (att_id, p["emp_id"]))
                r = c.execute("SELECT * FROM dq_ctl.eval_attestations_confirmed WHERE id=%s", (att_id,)).fetchone()
            except psycopg.Error as e:
                _db_error(e)
        return {"id": r["id"], "label": r["label"], "code_sha256": r["code_sha256"], "tests": r["tests"],
                "failures": r["failures"], "junit_sha256": r["junit_sha256"], "person_id": r["person_id"],
                "status": "CONFIRMED", "confirmed_by": r["confirmed_by"], "confirmed_at": _jsonable(r["confirmed_at"]),
                "halves": {"deterministic": True, "llm": r["junit_sha256_llm"] is not None},
                "det_run_id": r["det_run_id"], "llm_run_id": r["llm_run_id"], "prompt_version": r["prompt_version"]}

    # -- veto (§1.4) ------------------------------------------------------------------------------------------------
    @app.post("/api/v1/units/{unit_key}/veto")
    def veto(unit_key: str, body: dict = Body(default={}), p=Depends(principal)):
        """A person vetoes an L3 (H_ON) unit that has a veto window: EVALUATING -> HELD, and the RELEASE tier of its
        authorities drops to L2 in the window's state (in the database). Accepted while the unit is still EVALUATING,
        including after the window's deadline until the publisher releases it (a late veto only holds more); 409
        once the unit has released (or is otherwise past veto)."""
        sc = scope(p)
        if body.get("reason_code") not in VETO_REASON_CODES:
            raise HTTPException(422, f"reason_code one of {list(VETO_REASON_CODES)}")
        with services.cp.as_role("dq_api", sc) as c:
            u = c.execute("SELECT * FROM dq_ctl.units WHERE unit_key=%s", (unit_key,)).fetchone()
        if not u:
            raise HTTPException(404, "not found in your scope")
        if u["state"] != "EVALUATING":
            raise HTTPException(409, {"status": "REFUSED",
                                      "reason": f"unit is {u['state']} (a veto applies only before release)"})
        if body.get("explanation_ref") is None:
            raise HTTPException(422, {"status": "EXPLANATION_REF_MISSING",
                                      "reason": "send the explanation_ref of the card you are vetoing"})
        ref = h.check_ref(body["explanation_ref"], h.current_unit_expl(unit_key, sc))
        ev = int(body.get("expected_state_version", u["state_version"]))
        if hasattr(services, "veto"):                    # seat 2a's runtime: veto_unit, then verify-and-publish
            # Part F P1-1: a DECISION root (a veto is a person's decision): DEC-V<sha(unit|version|person)>
            base = "DEC-V" + hashlib.sha256(f"{unit_key}|{ev}|{p['emp_id']}".encode()).hexdigest()[:16]
            with h.decision_root(unit_key, sc, p["emp_id"], base):
                try:
                    nv = services.veto(sc, unit_key, ev, p["emp_id"], body["reason_code"], None, ref)
                except Stale as e:
                    raise HTTPException(409, {"status": "REFUSED", "reason": str(e)}) from e
                except Denied as e:
                    raise HTTPException(404, "not found in your scope") from e
                except psycopg.Error as e:
                    _db_error(e)
        else:
            with services.cp.as_role("dq_api", sc) as c:
                try:
                    nv = c.execute("SELECT dq_ctl.veto_unit(%s,%s,%s,%s,NULL,%s::jsonb) v",
                                   (unit_key, ev, p["emp_id"], body["reason_code"], json.dumps(ref))).fetchone()["v"]
                except psycopg.Error as e:
                    _db_error(e)
        x = h.ctx()
        return {"unit_key": unit_key, "state": "HELD", "state_version": nv, "explanation_ref": ref,
                "explanation": x.explain("decision", {"verb": "VETO", "role": "reviewer", "unit_key": unit_key,
                                                      "reason_code": body["reason_code"], "resulting_state": "HELD",
                                                      "part2": x.part2(u)},
                                       subject=f"unit:{unit_key}|veto:{nv}", authorities=u["authorities"])}

    # -- feed items (§3.2) ------------------------------------------------------------------------------------------
    def feed_row(x, r) -> dict:
        status = "OPEN" if r["status"] == "OPEN" else "ACKED"
        return {"item_id": r["item_id"], "feed": r["feed"], "submitter_id": None, "kind": r["kind"],
                "period": r["period"], "expected_by": None, "status": status, "reason_code": r["reason_code"],
                "acked_by": r["acknowledged_by"], "acked_at": _jsonable(r["acknowledged_at"]),
                "batch_id": r["batch_id"], "file_id": r["file_id"], "authority": r["authority"],
                "at": r["opened_at"].isoformat(),
                "explanation": x.explain("feed_item", {"item_id": r["item_id"], "feed": r["feed"], "kind": r["kind"],
                                                       "file": r["file_id"], "status": status,
                                                       "reason_code": r["reason_code"], "batch_id": r["batch_id"]},
                                       subject=f"feed:{r['item_id']}", authorities=[r["authority"]])}

    def feed_get(sc, item_id=None):
        with services.cp.as_role("dq_api", sc) as c:
            q = "SELECT * FROM dq_ctl.feed_items_status WHERE state=%s" + (" AND item_id=%s" if item_id else "")
            return c.execute(q + " ORDER BY item_id", (st, item_id) if item_id else (st,)).fetchall()

    @app.get("/api/v1/feeds/items")
    def feed_items(p=Depends(principal)):
        x = h.ctx()
        return [feed_row(x, r) for r in feed_get(feed_scope(p))]

    @app.post("/api/v1/feeds/items/{item_id}/ack")
    def feed_ack(item_id: int, body: dict = Body(default={}), p=Depends(principal)):
        sc = feed_scope(p)
        if body.get("reason_code") not in FEED_ACK_CODES:
            raise HTTPException(422, f"reason_code one of {list(FEED_ACK_CODES)}")
        _kind_rows = feed_get(sc, item_id)
        if _kind_rows:
            allowed = FEED_ACK_CODES_BY_KIND.get(_kind_rows[0]["kind"], FEED_ACK_CODES)
            if body["reason_code"] not in allowed:
                raise HTTPException(422, f"reason_code for a {_kind_rows[0]['kind']} item is one of {list(allowed)}")
        cur = feed_get(sc, item_id)
        if not cur:
            raise HTTPException(404, "unknown feed item in your scope")
        # E3: the explanation shown is ALWAYS bound (check_ref binds the current one when the body sends none)
        ref = h.check_ref(body.get("explanation_ref"), feed_row(h.ctx(), cur[0])["explanation"])
        with services.cp.as_role("dq_api", sc) as c:
            try:
                c.execute("SELECT dq_ctl.feed_item_ack(%s,%s,%s,NULL,%s::jsonb)",
                          (item_id, p["emp_id"], body["reason_code"], json.dumps(ref)))
            except psycopg.Error as e:
                _db_error(e)
        return feed_row(h.ctx(), feed_get(sc, item_id)[0])

    # -- parallel run (§3.3) ----------------------------------------------------------------------------------------
    def pr_row(x, r) -> dict:
        d = _row(r)
        facts = {k: d.get(k) for k in ("relation", "measure", "period", "as_of", "legacy", "new", "delta", "tolerance",
                                       "status", "explain_code", "system")}
        return {k: d.get(k) for k in ("row_id", "run_id", "system", "period", "as_of", "relation", "measure", "legacy",
                                      "new", "delta", "tolerance", "status", "explain_code", "explained_by",
                                      "explained_at", "counts_for_go_no_go", "authority")} | {
            "explanation": x.explain("parallel_break", facts, subject=f"parallel:{d.get('row_id')}",
                                     authorities=[d["authority"]] if d.get("authority") else ())}

    def pr_get(sc, period=None, row_id=None):
        with services.cp.as_role("dq_api", sc) as c:
            q, args = "SELECT * FROM dq_ctl.parallel_run_status WHERE true", []
            if period:
                q += " AND period=%s"
                args.append(period)
            if row_id is not None:
                q += " AND row_id=%s"
                args.append(row_id)
            return c.execute(q + " ORDER BY period, relation, measure, row_id", args).fetchall()

    @app.get("/api/v1/parallel-run")
    def parallel_run(period: str | None = None, p=Depends(principal)):
        x = h.ctx()
        return [pr_row(x, r) for r in pr_get(scope(p), period)]

    def pr_nonce(row_id, code, ref) -> str:
        return hashlib.sha256(f"PR|{row_id}|{code}|{ref['facts_hash']}|{ref['text_hash']}".encode()).hexdigest()

    def pr_current(sc, row_id) -> dict:
        r = pr_get(sc, row_id=row_id)
        if not r:
            raise HTTPException(404, "unknown parallel-run row in your scope")
        return pr_row(h.ctx(), r[0])

    @app.post("/api/v1/parallel-run/{row_id}/challenge")
    def pr_challenge(row_id: int, body: dict = Body(default={}), p=Depends(principal)):
        """Step-up challenge for LEGACY_DEFECT / DEFINITION (bound to the row, the code and the explanation shown)."""
        sc = scope(p)
        if body.get("explain_code") not in EXPLAIN_CODES:
            raise HTTPException(422, f"explain_code one of {list(EXPLAIN_CODES)}")
        ref = h.check_ref(body.get("explanation_ref"), pr_current(sc, row_id)["explanation"])
        chal = pr_nonce(row_id, body["explain_code"], ref)
        h.remember(chal, ref)
        return {"challenge": chal, "step_up_url": services.idp_url + "/token", "explanation_ref": ref}

    @app.post("/api/v1/parallel-run/{row_id}/explain")
    def pr_explain(row_id: int, body: dict = Body(default={}), p=Depends(principal)):
        sc = scope(p)
        code = body.get("explain_code")
        if code not in EXPLAIN_CODES:
            raise HTTPException(422, f"explain_code one of {list(EXPLAIN_CODES)}")
        cur = pr_current(sc, row_id)
        jti, stepped, ref = None, False, None
        if code in STEP_UP_EXPLAIN_CODES:
            tok = body.get("step_up_token")
            if not tok:
                raise HTTPException(428, {"status": "STEP_UP_REQUIRED", "reason": f"{code} needs a step-up over "
                                                                                 "/challenge first"})
            claims = h.peek(tok)
            if claims is None:
                raise HTTPException(401, {"status": "STEP_UP_INVALID", "reason": "invalid or expired step-up"})
            ref = h.decide_ref(body, claims.get("nonce"))
            if (claims.get("acr") != STEP_UP_ACR or claims["emp_id"] != p["emp_id"]
                    or claims.get("nonce") != pr_nonce(row_id, code, ref)):
                raise HTTPException(403, {"status": "DENIED", "reason": "step-up not bound to this explain code"})
            jti, stepped = claims["jti"], True
        else:
            # E3: every explain code binds the explanation shown (the current one when the body sends none)
            ref = h.check_ref(body.get("explanation_ref"), cur["explanation"])
        with services.cp.as_role("dq_api", sc) as c:
            try:
                c.execute("SELECT dq_ctl.parallel_run_explain(%s,%s,%s,%s,%s,%s::jsonb)",
                          (row_id, p["emp_id"], code, jti, stepped, json.dumps(ref)))
            except psycopg.Error as e:
                _db_error(e)
        return pr_current(sc, row_id)

    # -- classification (§5) ----------------------------------------------------------------------------------------
    @app.get("/api/v1/classification")
    def classification(p=Depends(principal)):
        scope(p)
        x = h.ctx()
        art = x.arts.get(f"{st}:classification") or {}
        cr = art.get("cr_id")
        with services.cp.as_role("dq_api") as c:
            o = c.execute("SELECT origin FROM dq_ctl.config_change_requests WHERE cr_id=%s", (cr,)).fetchone() if cr else None
        source = "DEFAULT" if cr == "TEST-GENESIS" or (o and o["origin"] == "GENESIS") else "CR"
        return [{"element": el, "class": cls, "source": source, "cr_id": cr, "masked": cls in MASKED_CLASSES}
                for el, cls in sorted(x.classes.items())]

    # -- monitoring (§6) --------------------------------------------------------------------------------------------
    def monitor_eval(sc, x):
        from dqagent.agent import monitor as mon
        with services.cp.as_role("dq_api", sc) as c:
            raw = c.execute("SELECT id, batch_id, period, relation, metric, key, value, n FROM dq_ctl.monitor_metrics "
                            "ORDER BY id").fetchall()
            mem = c.execute("SELECT memory_id, content FROM dq_ctl.agent_memory_current WHERE kind='SPC_BASELINE' "
                            "ORDER BY memory_id").fetchall()
        def row(r, batch=None):
            return {"period": r["period"] or r["batch_id"], "relation": r["relation"], "metric": r["metric"],
                    "key": r["key"] or "", "value": None if r["value"] is None else float(r["value"]), "n": r["n"],
                    "batch_id": batch or r["batch_id"]}

        # whole periods only: HISTORY:<p> and closed PERIOD:<p> rows (a per-batch partial row is never a point)
        latest = {}
        for r in raw:
            if r["batch_id"].startswith(("HISTORY:", "PERIOD:")):
                x_ = row(r)
                latest[(x_["period"], x_["relation"], x_["metric"], x_["key"])] = x_
        partial = [dict(r) | {"value": float(r["value"] or 0)} for r in raw
                   if not r["batch_id"].startswith(("HISTORY:", "PERIOD:")) and r["period"]]
        periods = sorted({k[0] for k in latest} | {r["period"] for r in partial})
        if not periods:
            return [], [], {}
        cur_p = periods[-1]
        closed = any(r["batch_id"] == f"PERIOD:{cur_p}" for r in raw)
        rows = [latest[k] for k in sorted(latest) if k[0] < cur_p or closed]
        if not closed:                                        # L4: the open period so far, combined exactly
            pb = f"PROVISIONAL:{cur_p}"
            rows += [r | {"batch_id": pb} for r in mon.combine_partials([r for r in partial if r["period"] == cur_p])]
        pinned = {}
        for m in mem:                                          # frozen pinned baselines are reused as-is
            b = m["content"] or {}
            if mon.verify_baseline(b):
                pinned[(b.get("relation"), b.get("metric"), b.get("key") or "")] = b
        mcfg = (x.arts.get(f"{st}:monitor") or {}).get("content") or None
        exclude = services.monitor_exclusions() if hasattr(services, "monitor_exclusions") else {}
        res = mon.run([r for r in rows if r["period"] < cur_p], [r for r in rows if r["period"] == cur_p],
                      period=cur_p, baselines=pinned, exclude=exclude, cfg=mcfg)   # E7: live signals excluded
        if not closed:                                        # L4: SIGNAL appears only after the period closes
            prog = services.monitor_progress(cur_p) if hasattr(services, "monitor_progress") else {}
            res = [mon.provisional(r, share=prog.get("share"), through=prog.get("through"))
                   if r["status"] != "BASELINE_BUILDING" else r for r in res]
        batch_of = {r["period"]: r["batch_id"] for r in rows}
        return res, rows, batch_of

    def signal_list(x, r, batch_of, sc) -> tuple[dict, list]:
        e = x.explain("monitor_signal", {**r, "part2": x.part2({"node": r["relation"]})},
                      subject=f"rel:{r['relation']}|{r['metric']}|{r.get('key') or ''}|{r.get('period') or ''}",
                      authorities=sc)
        return e, [{"batch_id": batch_of.get(r["period"]), "period": r["period"], "rule": s["rule"],
                    "direction": s.get("direction"), "explanation": e} for s in r["signals"]]

    def masked_element(x, metric: str) -> bool:
        el = metric.split(":", 1)[1] if ":" in metric else None
        return bool(el) and x.classes.get(el) in MASKED_CLASSES

    @app.get("/api/v1/monitor")
    def monitor(p=Depends(principal)):
        x = h.ctx()
        sc = scope(p)
        res, _rows, batch_of = monitor_eval(sc, x)
        out = []
        for r in res:
            e, sigs = signal_list(x, r, batch_of, sc)
            out.append({"relation": r["relation"], "metric": r["metric"], "key": r["key"] or None,
                        "chart": CHART_LABEL[r["chart"]], "status": r["status"], "period": r["period"],
                        "n_periods": r.get("baseline_periods"), "last_signal": sigs[-1] if sigs else None,
                        "note": r.get("note"), "received_pct": r.get("received_pct"), "explanation": e})
        return out

    @app.get("/api/v1/monitor/series")
    def monitor_series(relation: str, metric: str, key: str | None = None, p=Depends(principal)):
        from dqagent.agent import monitor as mon
        x = h.ctx()
        sc = scope(p)
        res, rows, batch_of = monitor_eval(sc, x)
        r = next((r for r in res if r["relation"] == relation and r["metric"] == metric
                  and (r["key"] or None) == (key or None)), None)
        if r is None:
            raise HTTPException(404, "no such monitored metric in your scope")
        _e, sigs = signal_list(x, r, batch_of, sc)
        b = r.get("baseline") or {}
        frozen = b.get("status") == "FROZEN"
        out = {"relation": relation, "metric": metric, "key": r["key"] or None, "chart": CHART_LABEL[r["chart"]],
               "status": r["status"], "note": r.get("note"), "points": [], "cl": None, "ucl": None, "lcl": None, "sigma2": None,
               "signals": sigs, "baseline_ref": r.get("baseline_hash") if frozen else None,
               "baseline_periods": r.get("baseline_periods"), "excluded_periods": r.get("excluded_periods", []),
               "mix": None}
        if r["chart"] in ("xmr", "p"):
            for row in rows:
                if row["relation"] != relation or row["metric"] != metric or row["key"] != (r["key"] or ""):
                    continue
                pt = {"batch_id": row["batch_id"], "period": row["period"], "value": row["value"], "n": row["n"]}
                if r["chart"] == "p" and frozen and row["n"]:
                    pt["lcl"], pt["ucl"] = (round(v, 6) for v in mon.p_limits(b["p_bar"], int(row["n"])))
                out["points"].append(pt)
            if frozen:
                if r["chart"] == "xmr":
                    out.update(cl=r.get("cl"), ucl=r.get("ucl"), lcl=r.get("lcl"), sigma2=r.get("sigma2"))
                else:
                    out["cl"] = b.get("p_bar")
        else:
            withheld = masked_element(x, metric)          # category labels of a masked element are values: withheld
            bins = [] if withheld else [{"category": c, "expected": v["expected_share"], "actual": v["actual_share"]}
                                        for c, v in sorted((r.get("bins") or {}).items())]
            out["mix"] = {"psi": r.get("value"), "status": r["status"], "bins": bins, "bins_withheld": withheld}
        return out

    # -- audit chain verification (A6) ------------------------------------------------------------------------------
    @app.get("/api/v1/audit/verify")
    def audit_verify(p=Depends(principal)):
        if "audit:AUTH_AUDIT" not in p["_duties"]:
            scope(p)                                     # an auditor, or a person with a release scope
        with services.cp.as_role("dq_auditor") as c:
            rows = c.execute("SELECT seq, event, payload_digest, prev_hash, row_hash FROM dq_ctl.audit_log "
                             "ORDER BY seq").fetchall()
            bb_break = c.execute("SELECT dq_ctl.bb_verify() b").fetchone()["b"]
            bb = c.execute("SELECT seq, entry_hash FROM dq_ctl.agent_blackboard ORDER BY seq").fetchall()
        prev, through, brk, expect = "0" * 64, 0, None, 1
        for r in rows:
            nh = hashlib.sha256(f"{prev}|{r['seq']}|{r['event']}|{r['payload_digest']}".encode()).hexdigest()
            if r["seq"] != expect or r["prev_hash"] != prev or r["row_hash"] != nh:
                brk = r["seq"]
                break
            prev, through, expect = r["row_hash"], r["seq"], expect + 1
        ok_bb = [b for b in bb if bb_break is None or b["seq"] < bb_break]
        return {"audit": {"ok": brk is None, "through_seq": through, "head_hash": prev, "first_break": brk},
                "blackboard": {"ok": bb_break is None, "through_seq": ok_bb[-1]["seq"] if ok_bb else 0,
                               "head_hash": ok_bb[-1]["entry_hash"] if ok_bb else "0" * 64, "first_break": bb_break}}

    # -- exports (§8 B3): open formats, allow-listed fields, no values, small cells suppressed -----------------------
    def export_inputs(sc, x):
        with services.cp.as_role("dq_api", sc) as c:
            units = [dict(u) for u in c.execute("SELECT unit_key, batch_id, node, program_code, state FROM dq_ctl.units "
                                                "ORDER BY unit_key").fetchall()]
            results = [dict(r) for r in c.execute(
                "SELECT DISTINCT ON (unit_key, rule_id) unit_key, rule_id, rule_hash, eval_version, disposition, "
                "evaluated, failed, ruleset_version FROM dq_ctl.results ORDER BY unit_key, rule_id, eval_version DESC, "
                "id DESC").fetchall()]
            decisions = [dict(d) for d in c.execute(
                "SELECT decision_id, unit_key, person_id, decision AS verb FROM dq_ctl.decisions ORDER BY decision_id"
            ).fetchall()]
            cr = c.execute("SELECT decision_ref FROM dq_ctl.registry_events WHERE artifact_kind='rule' AND "
                           "artifact_id LIKE %s ORDER BY id DESC LIMIT 1", (st + ":%",)).fetchone()
        for u in units:
            u["part2"] = x.part2(u)
        return units, results, decisions, (cr or {}).get("decision_ref")

    @app.get("/api/v1/export/dq-assertions")
    def export_assertions(p=Depends(principal)):
        from dqagent.adapters.metadata._common import suppress
        from dqagent.adapters.metadata.assertions import FIELDS, build_assertions
        x = h.ctx()
        units, results, _d, cr = export_inputs(scope(p), x)
        rows = build_assertions(results, services.rules, units, h.rule_bindings(), cr_id=cr)
        # every count is small-cell suppressed (1-10), not only `failed`; fields stay the allow-list
        return [{k: (suppress(r[k]) if k in ("evaluated", "failed") else r[k]) for k in FIELDS} for r in rows]

    @app.get("/api/v1/export/lineage")
    def export_lineage(p=Depends(principal)):
        from dqagent.adapters.metadata.lineage import build_lineage
        x = h.ctx()
        units, results, decisions, _cr = export_inputs(scope(p), x)
        visible = {u["unit_key"] for u in units}
        rels = sorted({n for pl in services.harness.get("pipelines", []) for n in pl.get("gated_nodes", [])}
                      | {r.relation for r in services.rules if r.relation})
        cols = {}
        for rel in rels:
            try:
                cols[rel] = [c for c in services.dp.columns(rel) if not c.startswith("_dq_")]
            except Exception:  # noqa: BLE001 - a relation not yet created in the data plane has no schema facet
                cols[rel] = []
        try:
            man = services.dp.fetchall("SELECT DISTINCT unit_key, version FROM dq_rel.manifest ORDER BY 1, 2")
        except Exception:  # noqa: BLE001 - nothing published yet
            man = []
        releases = [{"unit_key": k, "release_id": f"manifest-{v}"} for k, v in man if k in visible]
        return build_lineage(state=st, harness=services.harness, rules=services.rules, bindings=h.rule_bindings(),
                             relation_columns=cols, units=units, results=results, decisions=decisions,
                             releases=releases)


def create_idp_app(idp) -> FastAPI:
    """T1-only local IdP endpoint (never deployed). POST /token {username, step_up?, challenge?}."""
    app = FastAPI(title="T1 local IdP (test only)")
    app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:3000"], allow_methods=["*"],
                       allow_headers=["*"])

    @app.post("/token")
    def token(body: dict = Body(...)):
        if body.get("username") not in idp.users:
            raise HTTPException(401, "unknown user")
        return {"access_token": idp.token(body["username"], step_up=bool(body.get("step_up")),
                                          challenge=body.get("challenge")), "token_type": "Bearer"}

    @app.get("/.well-known/jwks.json")
    def jwks():
        return idp.jwks()

    return app


_ = dict_row
