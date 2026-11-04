"""Publisher: the single release predicate and atomic manifest publication (DESIGN §7).

can_release(unit) is evaluated by the publisher, the ONLY writer of RELEASED and of DP
manifests. Every clause returns (ok, reason), and the reasons are shown in the Tower.

Publication (S2): ONE immutable, versioned manifest per DP. The allow-list and the
quarantined row_seqs live in the same version, and a pointer row (version + content hash) is
flipped last, all in a single DuckDB transaction. The version number is RESERVED in the CP
first (dq_ctl.manifest_reservations), so a crash between the DP commit and the CP record can
never cause a version to be reused (SEC-W1-6); the next run compares against the DP pointer's
content hash, not the CP's last record. A DENY republishes without the unit.

Clause status (each either implemented or an explicit, listed stub — EXIT_CRITERIA.md):
  1 fingerprint ✔  2 registry ✔ / DEPLOY_MANIFEST stub  3 WAIT vector ✔  4 mode/decisions ✔
  5 veto window ✔ (Part E §1.4, folded into clause 4: H_ON releases only after its window by the DATABASE clock)
  6 DENY breaker ✔ / program breaker stub  7 approver coverage incl. INTAKE ✔  8 period stub
  (no FINAL-period model exists at W1)  9 Tower scope ✔  10 publication health ✔  11 order ✔
  12 batch ABC complete ✔ (Part F F-01: a sealed batch with no LANDING->GATED ABC needs recovery first)

Part F E-10: each candidate runs in its own savepoint; a stale transition (another writer moved the unit since it
was read) skips THAT unit for this cycle instead of aborting the whole cycle.

Clause 4 (Part E C2/C3, rewritten). The mode is RECOMPUTED from stored rows (hitl.modes.effective_mode: latest
results, the one gating-ABC view, the persisted dependency escalation, vetoes, the database's tier and RELEASE
entitlement). An EVALUATING unit releases when the recomputed mode is A, or H_ON when its veto window for the
current evaluation has elapsed by the database clock (`now() >= opened_at + window_s`), it was never vetoed, the tier
is still >= L3 and nothing escalated (all three are inside the recomputed H_ON). When the recomputed mode exceeds
H_ON, or is H_ON with no window for the current evaluation, the publisher moves the unit EVALUATING -> HELD via
CEILING, so a unit never sits silently in EVALUATING. Tests may inject `clock` (the instant compared with the
database deadline); production compares with the database's clock_timestamp().
"""
from __future__ import annotations

import hashlib
import json

from dqagent.hitl.decisions import INTAKE, qset_hash
import psycopg

from dqagent.hitl.modes import ORDER, abc_missing, ceiling_label, effective_mode
from dqagent.obs import tracer
from dqagent.model.outcome import Disposition as D, worsened

ENABLED_SCOPES = {"AUTH_MEDICAID", "AUTH_SNAP"}       # iteration 1 Tower scopes (clause 9)
STALE_ABC_REASON = "12: batch ABC incomplete (recovery needed)"
STALE_SKIP_REASON = "stale: skipped this cycle"        # E-10


class Publisher:
    governance = None        # set by the runtime: () -> {state, rules, ruleset_version, ratified, drift}

    def __init__(self, cp, dp, policy, dependencies=None, dp_name="duckdb-t1", contracts=None):
        self.cp, self.dp, self.policy, self.dp_name = cp, dp, policy, dp_name
        self.dependencies = dependencies or []
        self.contracts = contracts or {}
        # gated relations come from the contracts (no hard-coded list)
        self.gated = {rel: rel.split(".")[1] for rel in self.contracts}
        self.clock = None        # TESTS ONLY: () -> datetime compared with the database deadline; None = DB clock
        self._ensure_dp()

    # -- DP objects ---------------------------------------------------------------------
    def _ensure_dp(self):
        d = self.dp
        d.execute("CREATE TABLE IF NOT EXISTS dq_rel.manifest(version INTEGER NOT NULL, batch_id VARCHAR, unit_key VARCHAR,"
                  " fingerprint VARCHAR, max_row_seq BIGINT, PRIMARY KEY (version, unit_key))")
        d.execute("CREATE TABLE IF NOT EXISTS dq_rel.manifest_quarantine(version INTEGER NOT NULL, unit_key VARCHAR NOT NULL,"
                  " row_seq BIGINT NOT NULL, PRIMARY KEY (version, unit_key, row_seq))")
        d.execute("CREATE TABLE IF NOT EXISTS dq_rel.manifest_pointer(id INTEGER PRIMARY KEY, version INTEGER, content_hash VARCHAR)")
        d.execute("INSERT INTO dq_rel.manifest_pointer VALUES (1, 0, NULL) ON CONFLICT DO NOTHING")
        for rel, short in self.gated.items():
            try:
                d.execute(f"SELECT 1 FROM {rel} LIMIT 0")
            except Exception:
                continue
            d.execute(f"""CREATE OR REPLACE VIEW dq_rel.{short}_released AS
                SELECT g.* FROM {rel} g
                JOIN dq_rel.manifest m
                  ON m.version = (SELECT version FROM dq_rel.manifest_pointer WHERE id = 1)
                 AND m.batch_id = g._dq_batch_id AND m.unit_key = g._dq_unit_key
                WHERE g._dq_row_seq <= m.max_row_seq
                  AND NOT EXISTS (SELECT 1 FROM dq_rel.manifest_quarantine q
                                  WHERE q.version = m.version AND q.unit_key = m.unit_key AND q.row_seq = g._dq_row_seq)""")
            sup = self.contracts[rel].get("supersession")
            if sup:   # latest released VERSION per grain key: the view rules and silver read (ENG-W1-1)
                key = ", ".join(sup["key"])
                order = ", ".join(f"{c} DESC NULLS LAST" for c in sup["order"])
                d.execute(f"""CREATE OR REPLACE VIEW dq_rel.{short}_current AS
                    SELECT * EXCLUDE (_dq_rank) FROM (
                      SELECT r.*, row_number() OVER (PARTITION BY {key} ORDER BY {order}) AS _dq_rank
                      FROM dq_rel.{short}_released r) WHERE _dq_rank = 1""")

    # -- predicate ------------------------------------------------------------------------
    def _latest_results(self, c, uk):
        """Results of the unit's LATEST evaluation only (post-build F9): a rule removed by an approved change keeps
        no stale FAIL in play."""
        return c.execute("SELECT DISTINCT ON (rule_id) rule_id, rule_hash, disposition FROM dq_ctl.results "
                         "WHERE unit_key=%s AND eval_version = (SELECT max(eval_version) FROM dq_ctl.results "
                         "WHERE unit_key=%s) ORDER BY rule_id, id DESC", (uk, uk)).fetchall()

    def can_release(self, c, u) -> tuple[bool, list[str]]:
        reasons = []
        uk = u["unit_key"]
        # 1 fingerprint attested by the verifier, against the CURRENT bound fingerprint
        att = c.execute("SELECT result, detail FROM dq_ctl.attestations WHERE subject_type='FINGERPRINT' "
                        "AND subject_id=%s ORDER BY att_id DESC LIMIT 1", (uk,)).fetchone()
        if not att or att["result"] != "VALID" or att["detail"].get("fingerprint") != u["fingerprint"]:
            reasons.append("1: no current VALID fingerprint attestation")
        # 2 every rule version in the results is the APPROVED version of ITS artifact, in THIS state (Part D C2)
        res = self._latest_results(c, uk)
        gov = self.governance() if self.governance else None
        state = gov["state"] if gov else None
        latest = {r["artifact_id"]: r["semantic_hash"] for r in c.execute(
            "SELECT DISTINCT ON (artifact_id) artifact_id, semantic_hash, state FROM dq_ctl.registry_events "
            "WHERE artifact_kind='rule' AND (%s::text IS NULL OR artifact_id LIKE %s || ':%%') "
            "ORDER BY artifact_id, id DESC", (state, state)).fetchall() if r["state"] == "APPROVED"}
        # per ARTIFACT (state:rule@entity): the entity is the one the rule compiles to on this unit's node (F11)
        ent = {r.id: r.entity for r in (gov["rules"] if gov else []) if r.relation == u["node"]}
        unapproved = [r["rule_id"] for r in res if latest.get(f"{state}:{r['rule_id']}@{ent.get(r['rule_id'])}")
                      != r["rule_hash"]] if gov else \
            [r["rule_id"] for r in res if r["rule_hash"] not in set(latest.values())]
        if unapproved:
            reasons.append(f"2: unapproved rule versions {unapproved[:3]}")
        if gov is not None:
            if not gov["ratified"]:
                reasons.append("2: no ratified configuration (genesis not approved)")
            if gov.get("drift"):
                reasons.append(f"2c: {gov['drift']}")
            # 2b every REQUIRED rule of the current ruleset wrote a result at the unit's latest evaluation
            required = {r.id for r in gov["rules"] if r.relation == u["node"] and r.status == "ACTIVE"
                        and r.kind in ("row", "file") and r.header.get("gating") != "advisory"}
            ev = c.execute("SELECT max(eval_version) v FROM dq_ctl.results WHERE unit_key=%s", (uk,)).fetchone()["v"]
            have = {r["rule_id"] for r in c.execute("SELECT DISTINCT rule_id FROM dq_ctl.results WHERE unit_key=%s "
                                                     "AND eval_version=%s", (uk, ev)).fetchall()} if ev is not None else set()
            missing = sorted(required - have)
            if missing:
                reasons.append(f"2b: approved rule(s) wrote no result: {missing[:3]}")
            # 2d the evaluation ran under the CURRENT ruleset
            rsv = {r["ruleset_version"] for r in c.execute("SELECT DISTINCT ruleset_version FROM dq_ctl.results "
                                                            "WHERE unit_key=%s AND eval_version=%s", (uk, ev)).fetchall()}
            if ev is not None and rsv != {gov["ruleset_version"]}:
                reasons.append("2d: evaluated under a superseded ruleset (re-evaluation required)")
        # 3 dependency vector unchanged (WAIT edges)
        ev = c.execute("SELECT payload FROM dq_ctl.unit_events WHERE unit_key=%s AND payload ? 'dependency_vector' "
                       "ORDER BY state_version DESC LIMIT 1", (uk,)).fetchone()
        dv = (ev["payload"] or {}).get("dependency_vector", {}) if ev else {}
        for dep_uk, ver in dv.items():
            cur = c.execute("SELECT state, state_version FROM dq_ctl.units WHERE unit_key=%s", (dep_uk,)).fetchone()
            if not cur or cur["state_version"] != ver:
                reasons.append(f"3: dependency {dep_uk[:18]}… changed since evaluation (RE_EVALUATE)")
                break
        # 4 mode A recomputed, or H_ON after its veto window, or a complete, attested decision set
        m, trace, _ = effective_mode(c, self.policy, u, state, results=res)
        u["_mode"], u["_trace"] = m, trace
        if u["state"] == "EVALUATING":
            if m == "H_ON":
                reasons += self._veto_window_reasons(c, u)
            elif m != "A":
                reasons.append(f"4: recomputed mode is {m}, above the H_ON ceiling")
        else:
            reasons += self._decision_reasons(c, u, m, res)
        # 6 breaker (DENY propagation): any denied unit still in the live manifest blocks new releases
        live = {r[0] for r in self.dp.fetchall("SELECT unit_key FROM dq_rel.manifest WHERE version = "
                                               "(SELECT version FROM dq_rel.manifest_pointer WHERE id=1)")}
        denied = {r["unit_key"] for r in c.execute("SELECT unit_key FROM dq_ctl.deny").fetchall()}
        if live & denied:
            reasons.append("6: DENY not yet propagated (breaker)")
        # 11 ordering (ORDERED domains): every earlier unit of the same node + partition is terminal
        prior = c.execute("SELECT unit_key FROM dq_ctl.units WHERE node=%s AND partition_key=%s AND created_at < %s "
                          "AND state NOT IN ('RELEASED','REJECTED','SUPERSEDED') LIMIT 1",
                          (u["node"], u["partition_key"], u["created_at"])).fetchone()
        if prior:
            reasons.append("11: an earlier unit of the same partition is not terminal (strict per-partition order)")
        # 10 publication health: the latest verifier diff of the DP pointer against the CP is VALID
        pub = c.execute("SELECT result FROM dq_ctl.attestations WHERE subject_type='PUBLICATION' AND subject_id=%s "
                        "ORDER BY att_id DESC LIMIT 1", (self.dp_name,)).fetchone()
        if not pub or pub["result"] != "VALID":
            reasons.append("10: no current VALID publication-health attestation for this DP")
        # 9 Tower scope enabled for every authority of the unit
        missing = set(u["authorities"]) - ENABLED_SCOPES
        if missing:
            reasons.append(f"9: no Tower scope for {sorted(missing)} in iteration 1 (NOT_IMPLEMENTED)")
        # 12 the batch's ABC is complete (Part F F-01): no rows never reads as passed
        if abc_missing(c, u["batch_id"]):
            reasons.append(STALE_ABC_REASON)
        return (not reasons), reasons

    def _veto_window(self, c, u):
        """The veto window of the unit's CURRENT evaluation, with `elapsed` judged by the database clock (or, in
        tests only, by the injected instant compared with the database-computed deadline)."""
        ev = c.execute("SELECT max(eval_version) v FROM dq_ctl.results WHERE unit_key=%s", (u["unit_key"],)).fetchone()["v"]
        at = self.clock() if self.clock else None
        return c.execute("SELECT window_id, opened_at, deadline, vetoed, "
                         "coalesce(%s::timestamptz, clock_timestamp()) >= deadline AS elapsed "
                         "FROM dq_ctl.veto_window_status WHERE unit_key=%s AND eval_version=%s "
                         "ORDER BY window_id DESC LIMIT 1", (at, u["unit_key"], ev)).fetchone()

    def _veto_window_reasons(self, c, u) -> list[str]:
        w = self._veto_window(c, u)
        if w is None:
            return ["4: mode H_ON but no veto window for the current evaluation"]
        if w["vetoed"]:
            return ["4: vetoed inside the window"]
        if not w["elapsed"]:
            return [f"5: veto window open until {w['deadline'].isoformat()} (database clock)"]
        return []

    def _needs_ceiling_hold(self, c, u, why) -> bool:
        """C3: an EVALUATING unit whose recomputed mode exceeds H_ON (or is H_ON without a window for its current
        evaluation) is HELD via CEILING instead of sitting silently in EVALUATING."""
        if u["state"] != "EVALUATING" or "_mode" not in u:
            return False
        m = u["_mode"]
        if ORDER.index(m) > ORDER.index("H_ON"):
            return True
        return m == "H_ON" and any(r.startswith("4: mode H_ON but no veto window") for r in why)

    def _decision_reasons(self, c, u, m, res) -> list[str]:
        """Clause 4 (decision branch) + clause 7. Every check is re-done from stored rows; nothing the
        api returned is trusted."""
        reasons, uk = [], u["unit_key"]
        ev = c.execute("SELECT via, payload FROM dq_ctl.unit_events WHERE unit_key=%s AND state_version=%s",
                       (uk, u["state_version"])).fetchone()
        if not ev or ev["via"] not in ("DECIDE", "CONFIRM") or u["state"] != "PENDING_RELEASE":
            return ["4: PENDING_RELEASE was not reached through a human decision"]
        rnd = u["state_version"] - 1                         # the HELD version the humans decided on
        need = self.policy["approvals"][m]
        decs = c.execute("SELECT d.*, a.result AS att FROM dq_ctl.decisions d LEFT JOIN LATERAL ("
                         " SELECT result FROM dq_ctl.attestations WHERE subject_type='DECISION' AND subject_id=d.decision_id"
                         " ORDER BY att_id DESC LIMIT 1) a ON true WHERE d.unit_key=%s AND d.round=%s", (uk, rnd)).fetchall()
        approving = [d for d in decs if d["decision"] != "REJECT"]
        persons = {d["person_id"] for d in approving}
        if len(persons) < max(1, need["persons"]):
            reasons.append(f"4: {len(persons)}/{max(1, need['persons'])} approvals for mode {m}")
        if need.get("checker_step_up") and not any(d["seat"] == "CHECKER" for d in approving):
            reasons.append("4: H_IN_2 requires a step-up checker")
        for d in decs:
            if d["att"] != "VALID":
                reasons.append(f"4: decision {d['decision_id']} lacks a VALID verifier attestation")
            if d["bound_fingerprint"] != u["fingerprint"]:
                reasons.append("4: decision bound to a different fingerprint")
            for r in res:
                b = d["disposition_vector"].get(r["rule_id"])
                if b is None and r["disposition"] != "PASS":
                    reasons.append(f"4: new non-PASS rule {r['rule_id']} since decision")
                elif b is not None and worsened(D(b[1]), D(r["disposition"])):
                    reasons.append(f"4: {r['rule_id']} worsened since decision")
        # the quarantine actually written equals the set every approver signed
        q = [r["row_seq"] for r in c.execute("SELECT row_seq FROM dq_ctl.quarantine WHERE unit_key=%s AND scope='ROW'",
                                             (uk,)).fetchall()]
        if any(d["qset_hash"] != qset_hash(q) for d in decs):
            reasons.append("4: written quarantine set differs from the signed quarantine set")
        # 7 approver coverage: authorities(unit) + INTAKE when a GATING pre-split hop of the batch failed (Part E I1)
        required = set(u["authorities"])
        if c.execute("SELECT 1 FROM dq_ctl.abc_gating_failures WHERE batch_id=%s AND authority=%s LIMIT 1",
                     (u["batch_id"], INTAKE)).fetchone():
            required.add(INTAKE)
        covered = set()
        for d in approving:
            covered |= set(filter(None, (d["authority"] or "").split(",")))
        if not required <= covered:
            reasons.append(f"7: approver duties do not cover {sorted(required - covered)}")
        return reasons

    # -- run --------------------------------------------------------------------------------
    def run(self) -> dict:
        out = {"released": [], "blocked": {}}
        with self.cp.as_role("dq_publish_svc") as c:
            cands = c.execute("SELECT * FROM dq_ctl.units WHERE state IN ('EVALUATING','PENDING_RELEASE') "
                              "ORDER BY created_at, unit_key").fetchall()
            # Part F P1-1: a release check is recorded only when a unit's (ok, reasons) differ from its last one, so a
            # unit blocked the same way cycle after cycle writes one row (I9)
            last = tracer.latest_release_checks(self.cp, [u["unit_key"] for u in cands]) \
                if tracer.current_root() is not None else {}
            for u in cands:
                try:
                    with c.transaction():                 # E-10: one savepoint per unit
                        ok, why = self.can_release(c, u)
                        if ok:
                            self._transition(c, u, "RELEASED", "PUBLISH")
                            out["released"].append(u["unit_key"])
                        elif self._needs_ceiling_hold(c, u, why):
                            self.cp.transition(c, u["unit_key"], u["state_version"], "HELD", "CEILING",
                                               "dq_publish_svc", {"mode": u["_mode"], "mode_trace": u["_trace"],
                                                                  "ceiling": ceiling_label(u["_trace"]),
                                                                  "reasons": why})
                            out.setdefault("ceiling_held", []).append(u["unit_key"])
                            out["blocked"][u["unit_key"]] = why
                        else:
                            out["blocked"][u["unit_key"]] = why
                    self._record_check(u, ok, why, last)
                except psycopg.errors.SerializationFailure:
                    # the unit moved since it was read (CAS): its savepoint is rolled back, the rest of the cycle goes
                    # on; the next cycle reads it again (released / ceiling_held are appended only after the
                    # transition succeeded, so neither lists it)
                    out["blocked"][u["unit_key"]] = [STALE_SKIP_REASON]
                    out.setdefault("stale", []).append(u["unit_key"])
                    self._record_check(u, False, [STALE_SKIP_REASON], last)
        self.publish_manifest()
        return out

    @staticmethod
    def _record_check(u, ok, why, last):
        if tracer.current_root() is None:
            return
        reasons = list(why or [])
        if last.get(u["unit_key"]) == (bool(ok), reasons):
            return
        with tracer.span("release_check", seat="PUBLISHER", subject=f"unit:{u['unit_key']}",
                         authorities=u["authorities"]) as sp:
            sp.attr("ok", bool(ok)).attr("reasons", len(reasons))
            tracer.release_check(u["unit_key"], ok, reasons, u["authorities"])
        last[u["unit_key"]] = (bool(ok), reasons)

    def _transition(self, c, u, to, via):
        self.cp.transition(c, u["unit_key"], u["state_version"], to, via, "dq_publish_svc")

    def publish_manifest(self) -> int:
        with self.cp.as_role("dq_publish_svc") as c:
            rel = c.execute("SELECT unit_key, batch_id, fingerprint, max_row_seq FROM dq_ctl.units WHERE state='RELEASED' "
                            "AND unit_key NOT IN (SELECT unit_key FROM dq_ctl.deny) ORDER BY unit_key").fetchall()
            q = c.execute("SELECT DISTINCT unit_key, row_seq FROM dq_ctl.quarantine ORDER BY unit_key, row_seq").fetchall()
        allowed = {r["unit_key"] for r in rel}
        content = {"units": [[r["batch_id"], r["unit_key"], r["fingerprint"], r["max_row_seq"]] for r in rel],
                   "quarantine": [[r["unit_key"], r["row_seq"]] for r in q if r["unit_key"] in allowed]}
        h = hashlib.sha256(json.dumps(content, sort_keys=True).encode()).hexdigest()
        ptr = self.dp.fetchall("SELECT version, content_hash FROM dq_rel.manifest_pointer WHERE id=1")[0]
        if ptr[1] == h:                                   # the LIVE pointer already carries this content
            with self.cp.as_role("dq_publish_svc") as c:  # recovery: back-fill a CP record lost after a DP commit
                if not c.execute("SELECT 1 FROM dq_ctl.manifest_versions WHERE dp=%s AND version=%s",
                                 (self.dp_name, ptr[0])).fetchone():
                    c.execute("INSERT INTO dq_ctl.manifest_versions(dp,version,content_hash,content) "
                              "VALUES (%s,%s,%s,%s::jsonb)", (self.dp_name, ptr[0], h, json.dumps(content)))
                    c.execute("SELECT dq_ctl.append_audit('MANIFEST_RECORD_RECOVERED', %s)", (h,))
            return ptr[0]
        with self.cp.as_role("dq_publish_svc") as c:      # 1. reserve: a version number is never reused
            top = c.execute("SELECT max(version) v FROM dq_ctl.manifest_reservations WHERE dp=%s",
                            (self.dp_name,)).fetchone()["v"] or 0
            v = max(top, ptr[0]) + 1
            c.execute("INSERT INTO dq_ctl.manifest_reservations(dp, version) VALUES (%s,%s)", (self.dp_name, v))
        con = self.dp.cur()                               # 2. one DP transaction, pointer flipped last
        con.execute("BEGIN")
        try:
            if content["units"]:
                con.executemany("INSERT INTO dq_rel.manifest VALUES (?,?,?,?,?)",
                                [[v, b, u, f, m] for b, u, f, m in content["units"]])
            if content["quarantine"]:
                con.executemany("INSERT INTO dq_rel.manifest_quarantine VALUES (?,?,?)",
                                [[v, u, s] for u, s in content["quarantine"]])
            con.execute("UPDATE dq_rel.manifest_pointer SET version = ?, content_hash = ? WHERE id = 1", [v, h])
            con.execute("COMMIT")
        except Exception:
            con.execute("ROLLBACK")
            raise
        with self.cp.as_role("dq_publish_svc") as c:      # 3. record (a crash before this is caught by clause 10)
            c.execute("INSERT INTO dq_ctl.manifest_versions(dp,version,content_hash,content) VALUES (%s,%s,%s,%s::jsonb)",
                      (self.dp_name, v, h, json.dumps(content)))
            c.execute("SELECT dq_ctl.append_audit('MANIFEST_PUBLISHED', %s)", (h,))
        return v
