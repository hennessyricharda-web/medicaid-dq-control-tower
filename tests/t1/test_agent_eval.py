"""Deterministic agent evaluations (Part C WS4). See tests/agent_eval/README.md.

Each scenario in tests/agent_eval/scenarios.yaml is a RECIPE (inbound files, human actions). Its `expect` block is
derived by hand from the defects the recipe seeds. The runner never reads a previous run of the agent.
Determinism: every scenario runs twice in two fresh control planes and must yield the same canonical digest.
"""
import datetime as dt
import os
import subprocess
import sys
import zlib
import hashlib
import json
import uuid
from pathlib import Path

import psycopg
import pytest
import yaml
from fastapi.testclient import TestClient

from dqagent.api.app import create_app
from dqagent.cp import schema

from .conftest import PG
from .evidence import grant_evidence
from .fixtures_w1 import acks, ie_file, person, x837
from .w1_harness import Slice

SCEN = yaml.safe_load(open(Path(__file__).resolve().parents[1] / "agent_eval" / "scenarios.yaml"))["scenarios"]
EXCLUDED = {"PASS", "ADVISORY", "NOT_APPLICABLE", "STATE_WAIVED", "NOT_IN_SCOPE"}
SCOPE = ["AUTH_MEDICAID"]
USERS = {"maker": "E100", "checker": "E200", "checker2": "E210"}


def _fresh_db():
    admin = psycopg.connect(dbname="postgres", autocommit=True, **PG)
    db = "dqev_" + uuid.uuid4().hex[:8]
    admin.execute(f"CREATE DATABASE {db}")
    dsn = dict(dbname=db, **PG)
    with psycopg.connect(**dsn) as c:
        schema.apply(c)
    return admin, db, dsn


class Run:
    def __init__(self, dsn, tmp: Path):
        self.s = Slice(dsn)
        self.tmp = tmp
        self.api = TestClient(create_app(self.s))
        self.log = []

    def H(self, user, **kw):
        return {"Authorization": f"Bearer {self.s.idp.token(user, **kw)}"}

    # -- recipe steps ----------------------------------------------------------------------------------
    def eligibility(self, batch, persons, trailer=None):
        p = self.tmp / f"{batch}.txt"
        p.write_bytes(ie_file([person(a, b) for a, b in persons], trailer, seq=zlib.crc32(batch.encode()) % 10_000))
        self.s.run_pipeline("p4_eligibility_ie", batch, [("IE", p)], ["bronze.ie_segment"])

    def encounters(self, batch, isa13, claims, submitter="A"):
        cl = [{"lines": [("Z9001", 100.0, "20260110")], **c} for c in claims]
        p = self.tmp / f"{batch}.x12"
        p.write_bytes(x837(f"ZZMCO{submitter}", isa13, cl))
        a = self.tmp / f"{batch}.ack.csv"
        a.write_bytes(acks(isa13, ["A"] * len(cl)))
        self.s.run_pipeline("p6_claims", batch, [("X12_837P", p, f"ZZ-MCO-{submitter}", a)],
                            ["bronze.encounter_header", "bronze.encounter_line"])

    def publish(self, n):
        for _ in range(int(n)):
            self.s.verify_and_publish()

    def _decide(self, user, uk, verb):
        card = self.api.get(f"/api/v1/units/{uk}", headers=self.H(user)).json()
        chal = self.api.post(f"/api/v1/units/{uk}/challenge", json={"decision": verb, "quarantine_row_seqs": []},
                             headers=self.H(user)).json()["challenge"]
        body = {"decision": verb, "rationale": "Reviewed the evidence on the card for the evaluation scenario.",
                "expected_state_version": card["state_version"], "bound_fingerprint": card["fingerprint"],
                "quarantine_row_seqs": [], "step_up_token": self.s.idp.token(user, step_up=True, challenge=chal)}
        r = self.api.post(f"/api/v1/units/{uk}/decide", json=body, headers=self.H(user))
        assert r.status_code == 200, r.text
        return r.json()

    def release_held(self):
        for u in self.s.units(state="HELD"):
            r = self._decide("maker", u["unit_key"], "RELEASE")
            if r["status"] == "AWAITING_CHECKER":
                uk = u["unit_key"]
                pend = self.api.get(f"/api/v1/units/{uk}", headers=self.H("checker")).json()["x_pending_decision"]
                chal = self.api.post(f"/api/v1/units/{uk}/challenge", json={}, headers=self.H("checker")).json()["challenge"]
                ok = self.api.post(f"/api/v1/units/{uk}/confirm", headers=self.H("checker"),
                                   json={"decision_id": pend["decision_id"],
                                         "step_up_token": self.s.idp.token("checker", step_up=True, challenge=chal)})
                assert ok.status_code == 200, ok.text

    def maker_decides(self, verb):
        for u in self.s.units(state="HELD"):
            self._decide("maker", u["unit_key"], verb)

    def checker_confirms(self, user, expect):
        for u in self.s.units(state="HELD"):
            uk = u["unit_key"]
            pend = self.api.get(f"/api/v1/units/{uk}", headers=self.H(user)).json()["x_pending_decision"]
            chal = self.api.post(f"/api/v1/units/{uk}/challenge", json={}, headers=self.H(user)).json()["challenge"]
            r = self.api.post(f"/api/v1/units/{uk}/confirm", headers=self.H(user),
                              json={"decision_id": pend["decision_id"],
                                    "step_up_token": self.s.idp.token(user, step_up=True, challenge=chal)})
            assert r.status_code == expect, (user, r.status_code, r.text)
            self.log.append(("checker_confirms", user, r.status_code))

    def known_issue(self, proposer, approver, rule_id, expect):
        exp = (dt.date.today() + dt.timedelta(days=30)).isoformat()
        r = self.api.post("/api/v1/agent/memory/known-issues", headers=self.H(proposer),
                          json={"rule_id": rule_id, "authority": "AUTH_MEDICAID",
                                "reason_code": "UPSTREAM_DEFECT_TICKETED", "expires": exp})
        assert r.status_code == 200, r.text
        mid = r.json()["memory_id"]
        chal = self.api.post(f"/api/v1/agent/memory/{mid}/challenge", json={"verdict": "APPROVED"},
                             headers=self.H(approver)).json()["challenge"]
        d = self.api.post(f"/api/v1/agent/memory/{mid}/decide", headers=self.H(approver),
                          json={"verdict": "APPROVED",
                                "step_up_token": self.s.idp.token(approver, step_up=True, challenge=chal)})
        got = "APPROVED" if d.status_code == 200 else "REFUSED"
        assert got == expect, (got, d.text)
        self.log.append(("known_issue", rule_id, got))

    # -- Part E §1.5 (S6): earned autonomy -----------------------------------------------------------------------
    AGENT, AUTH = "dq-reconciliation", "AUTH_MEDICAID"

    def _tier(self):
        return self.s.registry.tier("ZZ", self.AGENT, "RELEASE", self.AUTH)

    def set_tier(self, tier):
        """The test state: lower RELEASE on AUTH_MEDICAID (automatic, lower-only demotion by the registry service)."""
        self.s.registry.demote(self.AGENT, "RELEASE", self.AUTH, tier, "TEST_SETUP")

    def promote(self, tier):
        """Part F K0 (retired step 2, re-homed to step 3 per D6): before proposing, create AND CONFIRM a fresh
        DETERMINISTIC evidence bundle for the running code version -- R4-5's `gov_decide` refuses to APPROVE a
        rank-rising AUTONOMY item otherwise (promotion_refusal, trace_schema.py). `grant_evidence` is safe to call
        more than once per run: eval_attestations is append-only, so a second call writes a second confirmed row
        for the same code version rather than reusing the first, but `evidence_status().complete` reads TRUE
        either way (an EXISTS check, not a count) -- S6-EARN-AUTONOMY's second `promote` step relies on exactly
        this. This does not touch anything `observe()` reads (results/units/traces/measures/board/memory/log):
        eval_runs/eval_attestations/eval_attestation_confirmations are not queried by observe(), so the I-3 digest
        is unaffected.
        The maker proposes the promotion with the track record; checker and checker2 approve, applying after each."""
        grant_evidence(self.s)
        item = self.s.effective_item("AUTONOMY", f"autonomy:{self.AGENT}:RELEASE")
        item["content"][self.AUTH] = tier
        cr = self.s.registry.propose("ZZ", "E100", "Promote release autonomy with the attached track record", [item])
        assert cr["impact"]["track_record"]["hash"]
        status = []
        for a in ("checker", "checker2"):
            tok = self.s.idp.token(a, step_up=True, challenge=self.s.registry.challenge(cr["cr_id"], "APPROVED"))
            self.s.registry.decide(cr["cr_id"], "APPROVED", tok, USERS[a], ["governance:AUTH_RULES"])
            status.append(self.s.apply_change(cr["cr_id"]))
        self.log.append(["promote", cr["approvals_required"], status, self._tier()])

    def veto_window(self, reason):
        """Units waiting in veto windows: none releases one second before its deadline; the first releases at its
        deadline (database-computed); the second is vetoed."""
        with self.s.cp.as_role("dq_publish_svc") as c:
            ws = c.execute("SELECT w.unit_key, w.deadline FROM dq_ctl.veto_window_status w JOIN dq_ctl.units u "
                           "USING (unit_key) WHERE u.state='EVALUATING' ORDER BY w.deadline").fetchall()
        first, second = ws[0], ws[1]
        self.s.publisher.clock = lambda: first["deadline"] - dt.timedelta(seconds=1)
        early = [k for k in self.s.verify_and_publish()["released"] if k in {w["unit_key"] for w in ws}]
        self.s.publisher.clock = lambda: first["deadline"]
        at = self.s.verify_and_publish()["released"]
        self.s.publisher.clock = None
        assert at == [first["unit_key"]], at
        sv = self.s.units(unit_key=second["unit_key"])[0]["state_version"]
        self.s.veto(SCOPE, second["unit_key"], sv, USERS["checker"], reason)
        self.log.append(["veto_window", len(ws), len(early), len(at),
                         self.s.units(unit_key=second["unit_key"])[0]["state"], self._tier()])

    def lookback_withdraw(self, rule, entity):
        """Revoke the rule version the L3 release ran under (with its replacement); withdraw that unit's item."""
        r = next(x for x in self.s.rules if x.id == rule)
        rev = {"artifact_id": f"ZZ:revocation:{rule}@{entity}:{r.effective_hash[:12]}", "kind": "REVOCATION",
               "op": "PUT", "content": {"rule": f"ZZ:{rule}@{entity}", "hash": r.effective_hash,
                                        "reason": "rule logic found defective after release"}}
        fix = {"artifact_id": f"ZZ:overlay:{rule}", "kind": "OVERLAY", "op": "PUT",
               "content": {"verb": "raise_priority", "priority": "Critical", "reason": "corrected version",
                           "approved_ref": "SCR-S6"}}
        _, st = self.s.govern([rev, fix])
        assert st == "APPLIED", st
        with self.s.cp.as_role("dq_api", SCOPE) as c:
            item = c.execute("SELECT i.item_id FROM dq_ctl.lookback_items i WHERE EXISTS (SELECT 1 FROM dq_ctl.unit_events e "
                             "WHERE e.unit_key=i.unit_key AND e.from_state='EVALUATING' AND e.to_state='RELEASED')"
                             ).fetchone()["item_id"]
            c.execute("SELECT dq_ctl.lookback_decide(%s,%s,%s,'WITHDRAWN',%s,NULL)",
                      (item, USERS["checker"], "jti-s6", "Released under the revoked rule version; withdraw it."))
        self.log.append(["lookback_withdraw", self._tier()])

    def run(self, steps):
        for st in steps:
            (k, v), = st.items()
            getattr(self, k)(**v) if isinstance(v, dict) else getattr(self, k)(v)

    # -- observation (canonical, timestamp- and surrogate-free) ---------------------------------------
    def observe(self) -> dict:
        with self.s.cp.as_role("dq_triage_svc") as c:
            res = c.execute("SELECT DISTINCT ON (r.unit_key, r.rule_id) r.unit_key, r.rule_id, r.disposition, "
                            "r.evaluated, r.failed FROM dq_ctl.results r ORDER BY r.unit_key, r.rule_id, "
                            "r.eval_version DESC, r.id DESC").fetchall()
            units = c.execute("SELECT unit_key, node, state FROM dq_ctl.units ORDER BY unit_key").fetchall()
            traces = c.execute("SELECT DISTINCT ON (unit_key) unit_key, to_state, payload->'mode_trace' AS t, "
                               "payload->>'mode' AS m FROM dq_ctl.unit_events ORDER BY unit_key, state_version DESC"
                               ).fetchall()
            meas = c.execute("SELECT DISTINCT ON (batch_id, node, rule_id) batch_id, rule_id, numerator, denominator, "
                             "value, disposition, long_disposition FROM dq_ctl.measure_results "
                             "ORDER BY batch_id, node, rule_id, id DESC").fetchall()
            board = c.execute("SELECT batch_id, seat, person_id, kind, subject_type, subject_id, template, body, entry_hash "
                              "FROM dq_ctl.agent_blackboard ORDER BY seq").fetchall()
            mem = c.execute("SELECT kind, scope_type, scope_id, content, "
                            "(SELECT event FROM dq_ctl.agent_memory_events e WHERE e.memory_id=m.memory_id "
                            "ORDER BY id DESC LIMIT 1) status FROM dq_ctl.agent_memory m ORDER BY memory_id").fetchall()
        crs: dict = {}
        for b in board:
            b["body"] = {k: v for k, v in b["body"].items() if k != "memory_id"}
            if "cr_id" in b["body"]:          # change-request ids are random: canonical by order of appearance
                b["body"]["cr_id"] = crs.setdefault(b["body"]["cr_id"], f"CR#{len(crs) + 1}")
        if crs:                               # ... and the hash chain over them is then not reproducible either
            for b in board:
                b["entry_hash"] = None
        mem = [{**m, "content": {k: v for k, v in m["content"].items() if k != "expires"}} for m in mem]
        return {"results": [dict(r) for r in res], "units": [dict(u) for u in units],
                "traces": [dict(t) for t in traces],
                "measures": [dict(m) | {"value": None if m["value"] is None else round(float(m["value"]), 9)}
                             for m in meas],
                "board": [dict(b) for b in board], "memory": [dict(m) for m in mem], "log": self.log}


def digest(obs) -> str:
    return hashlib.sha256(json.dumps(obs, sort_keys=True, default=str).encode()).hexdigest()


def _non_pass(obs) -> dict:
    out: dict = {}
    for r in obs["results"]:
        if r["disposition"] in EXCLUDED:
            continue
        e = out.setdefault(r["rule_id"], {"disposition": r["disposition"], "units": 0})
        assert e["disposition"] == r["disposition"], (r["rule_id"], e, r)
        e["units"] += 1
    return out


def _check(run: Run, obs: dict, exp: dict):
    if "non_pass" in exp:
        assert _non_pass(obs) == exp["non_pass"], f"non-PASS set differs from the recipe: {_non_pass(obs)}"
    for node, want in (exp.get("states") or {}).items():
        got: dict = {}
        for u in obs["units"]:
            if u["node"] == node:
                got[u["state"]] = got.get(u["state"], 0) + 1
        assert got == want, (node, got, want)
    for b in exp.get("board_has", []):
        assert any(all(x.get(k) == v for k, v in b.items()) for x in obs["board"]), f"board lacks {b}"
    for t in exp.get("board_absent", []):
        assert not [x for x in obs["board"] if x["template"] == t], f"board unexpectedly has {t}"
    if "held_reason_excludes" in exp:
        q = run.api.get("/api/v1/queue", headers=run.H("maker")).json()
        for cl in q:
            assert not set(cl["rule_ids"]) & set(exp["held_reason_excludes"]), cl
    for m in exp.get("measures", []):
        got = [x for x in obs["measures"] if x["batch_id"] == m["batch_id"] and x["rule_id"] == m["rule_id"]]
        assert len(got) == 1, (m, got)
        g = got[0]
        assert abs(float(g["value"]) - m["value"]) < 1e-9 and g["disposition"] == m["disposition"] \
            and g["long_disposition"] == m["long_disposition"], (m, g)
    if "baseline" in exp:
        b = exp["baseline"]
        bl = [m for m in obs["memory"] if m["kind"] == "BASELINE" and m["scope_id"] == b["scope_id"]]
        assert bl, "no baseline pinned"
        last = bl[-1]["content"]
        assert last["n_periods"] == b["n_periods"] and abs(last["median"] - b["median"]) < 1e-9, last
    if "memory" in exp:
        for kind, by_status in exp["memory"].items():
            got: dict = {}
            for m in obs["memory"]:
                if m["kind"] == kind:
                    got[m["status"]] = got.get(m["status"], 0) + 1
            assert got == by_status, (kind, got)
    if "card_known_issue" in exp:
        held = run.s.units(node="bronze.ie_segment")
        uk = held[0]["unit_key"]
        mk = run.api.get(f"/api/v1/units/{uk}", headers=run.H("maker")).json()
        ck = run.api.get(f"/api/v1/units/{uk}", headers=run.H("checker")).json()
        assert bool(mk["known_issues"]) is exp["card_known_issue"]["maker_sees"], mk["known_issues"]
        assert bool(ck["known_issues"]) is exp["card_known_issue"]["checker_sees"], ck["known_issues"]
        assert ck["x_known_issues_hidden_for_checker"] is True
    for auth, tier in (exp.get("autonomy") or {}).items():
        assert run.s.registry.tier("ZZ", "dq-reconciliation", "RELEASE", auth) == tier, auth
    if "demotion_reasons" in exp:
        got = [b["body"]["reason_code"] for b in obs["board"] if b["template"] == "AUTONOMY_DEMOTED"]
        assert got == exp["demotion_reasons"], got
    if "log" in exp:
        assert json.loads(json.dumps(obs["log"])) == exp["log"], obs["log"]
    if exp.get("checker_views_hide_known_issue"):
        mem = run.api.get("/api/v1/agent/memory", headers=run.H("checker")).json()
        assert not [m for m in mem if m["kind"] == "KNOWN_ISSUE" and m["status"] == "APPROVED"], mem
        board = run.api.get("/api/v1/agent/blackboard", headers=run.H("checker")).json()
        assert not [b for b in board if b["kind"] == "MEMORY_REF"], board


def run_once(sc_id: str, tmp: Path, check: bool = False) -> str:
    sc = next(s for s in SCEN if s["id"] == sc_id)
    admin, db, dsn = _fresh_db()
    try:
        tmp.mkdir(parents=True, exist_ok=True)
        run = Run(dsn, tmp)
        run.run(sc["steps"])
        obs = run.observe()
        if check:
            _check(run, obs, sc["expect"])
        return digest(obs)
    finally:
        admin.execute(f"DROP DATABASE {db} WITH (FORCE)")
        admin.close()


@pytest.mark.parametrize("sc", SCEN, ids=[s["id"] for s in SCEN])
def test_agent_scenario_matches_recipe_and_is_deterministic(sc, tmp_path):
    """Pass 1 in this process (checks the recipe expectations); pass 2 in a SEPARATE interpreter with a different
    hash seed, so any dependence on process state (hash order, caches, clocks) shows up as a digest difference."""
    try:
        d1 = run_once(sc["id"], tmp_path / "a", check=True)
    except psycopg.OperationalError as e:  # pragma: no cover
        pytest.skip(f"T1 Postgres not reachable: {e}")
    root = Path(__file__).resolve().parents[2]
    out = subprocess.run([sys.executable, "-c", "import sys; from tests.t1.test_agent_eval import run_once; "
                          "from pathlib import Path; print(run_once(sys.argv[1], Path(sys.argv[2])))",
                          sc["id"], str(tmp_path / "b")], cwd=root, capture_output=True, text=True,
                         env={**os.environ, "PYTHONHASHSEED": "12345", "PYTHONPATH": str(root)}, timeout=600)
    assert out.returncode == 0, out.stderr[-2000:]
    assert d1 == out.stdout.strip().splitlines()[-1], "the agent is not deterministic on this recipe"


def test_mutation_scenarios_differ_from_their_base_by_exactly_the_seed():
    by = {s["id"]: s for s in SCEN}
    for s in SCEN:
        if "mutation_of" not in s:
            continue
        base = by[s["mutation_of"]]["expect"]["non_pass"]
        diff = set(base) ^ set(s["expect"]["non_pass"])
        assert len(diff) == 1, (s["id"], diff)


def test_seat_cannot_be_forged_and_steward_needs_a_person(fresh_cp_dsn):
    s = Slice(fresh_cp_dsn)
    body = json.dumps({"rule_id": "R-ENC-004", "unit_count": 1})
    with s.cp.as_role("dq_eval_svc") as c:   # a service cannot claim a person (would pose as a steward)
        with pytest.raises(psycopg.errors.CheckViolation):
            c.execute("SELECT dq_ctl.bb_post('B','AUTH_MEDICAID','NOTE','RULE','R','STEWARD_NOTE',%s::jsonb,NULL,'E100')",
                      (body,))
    with s.cp.as_role("dq_api", SCOPE) as c:  # the api is the STEWARD seat and must name the person
        with pytest.raises(psycopg.errors.CheckViolation):
            c.execute("SELECT dq_ctl.bb_post('B','AUTH_MEDICAID','NOTE','RULE','R','STEWARD_NOTE',%s::jsonb,NULL,NULL)",
                      (body,))
    with s.cp.as_role("dq_api", SCOPE) as c:  # ... and only within its scope
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            c.execute("SELECT dq_ctl.bb_post('B','AUTH_SNAP','NOTE','RULE','R','STEWARD_NOTE',%s::jsonb,NULL,'E100')",
                      (body,))
    for role in ("dq_eval_svc", "dq_triage_svc", "dq_api"):   # no direct write path for anyone
        with s.cp.as_role(role) as c:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                c.execute("INSERT INTO dq_ctl.agent_blackboard(seq,agent,authority,seat,kind,subject_type,subject_id,"
                          "template,body,prev_hash,entry_hash) VALUES (99,'dq-reconciliation','AUTH_MEDICAID','PUBLISHER',"
                          "'NOTE','RULE','x','T','{}','a','b')")
    with s.cp.as_role("dq_triage_svc") as c:  # free text / data values cannot enter a body
        with pytest.raises(psycopg.errors.CheckViolation):
            c.execute("SELECT dq_ctl.bb_post('B','AUTH_MEDICAID','NOTE','RULE','R','STEWARD_NOTE',%s::jsonb,NULL,NULL)",
                      (json.dumps({"rule_id": "dx F10.20 alcohol"}),))
    with s.cp.as_role("dq_triage_svc") as c:
        seq = c.execute("SELECT dq_ctl.bb_post('B','AUTH_MEDICAID','FINDING','RULE','R','RULE_NON_PASS',%s::jsonb,NULL,NULL) s",
                        (body,)).fetchone()["s"]
        row = c.execute("SELECT seat, prev_hash, entry_hash FROM dq_ctl.agent_blackboard WHERE seq=%s", (seq,)).fetchone()
        assert row["seat"] == "TRIAGE"
    with s.cp.as_role("dq_triage_svc") as c:  # append-only
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            c.execute("UPDATE dq_ctl.agent_blackboard SET seat='PUBLISHER'")
    with s.cp.as_role("dq_auditor") as c:      # the chain verifies end to end
        assert c.execute("SELECT dq_ctl.bb_verify() AS b").fetchone()["b"] is None
    # tamper with the chain as the database owner (a break-glass path no service holds): verification catches it
    with psycopg.connect(**fresh_cp_dsn) as adm:
        adm.execute("ALTER TABLE dq_ctl.agent_blackboard DISABLE TRIGGER agent_blackboard_no_mut")
        adm.execute("UPDATE dq_ctl.agent_blackboard SET subject_id='TAMPERED' WHERE seq=%s", (seq,))
        adm.execute("ALTER TABLE dq_ctl.agent_blackboard ENABLE TRIGGER agent_blackboard_no_mut")
    with s.cp.as_role("dq_auditor") as c:
        assert c.execute("SELECT dq_ctl.bb_verify() AS b").fetchone()["b"] == seq


def test_promoted_template_rule_gates_only_units_in_scope(fresh_cp_dsn, tmp_path):
    """Post-build F4/F5: promoting a scoped (applies_when) basic rule to gating must hold ONLY units where it fails;
    units with no row in scope record NOT_IN_SCOPE (excluded), never NOT_EVALUATED (which would hold them)."""
    from dqagent.rules.model import compile_rules, load_state
    cfg = load_state("ZZ")
    cfg["overlays"] = {"B-X-ADJ-ORIG-KNOWN": {"verb": "promote", "reason": "state requires known originals",
                                             "approved_ref": "T-PROMOTE-1"}}
    run = Run(fresh_cp_dsn, tmp_path)
    rules = compile_rules("ZZ", cfg=cfg)
    run.s.rules[:] = rules                         # evaluator and seats share this list
    with psycopg.connect(**fresh_cp_dsn) as c:     # TEST genesis for the promoted version (Part D: two-person)
        for r in rules:
            c.execute("INSERT INTO dq_ctl.registry_events(artifact_kind,artifact_id,semantic_hash,state,decision_ref)"
                      " VALUES ('rule',%s,%s,'APPROVED','TEST-GENESIS')", (f"ZZ:{r.id}@{r.entity}", r.effective_hash))
    run.eligibility("B-ELG-1", [["P1", "M0001"], ["P2", "M0002"]])
    run.publish(1)
    run.encounters("B-ENC-1", "000000101", [{"id": "C1", "member": "M0001"},
                                            {"id": "C2", "member": "M0002", "freq": "7", "f8": "NOSUCH"}])
    obs = run.observe()
    got = sorted(r["disposition"] for r in obs["results"] if r["rule_id"] == "B-X-ADJ-ORIG-KNOWN")
    assert got == ["FAIL", "NOT_IN_SCOPE"], got          # gating now: a real FAIL, and the original is out of scope
    q = run.api.get("/api/v1/queue", headers=run.H("maker")).json()
    sigs = [cl["rule_ids"] for cl in q if cl["node"] == "bronze.encounter_header"]
    assert sum("B-X-ADJ-ORIG-KNOWN" in sg for sg in sigs) == 1, sigs
