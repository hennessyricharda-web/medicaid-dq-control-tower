"""W1 runtime wiring for state ZZ on the T1 stack (Postgres CP + DuckDB DP + local IdP).

In production each service (api, seal-worker, evaluator, publisher, verifier) is its own
deployment with its own workload identity. Here they share a process, but every CP call opens
its own connection that LOGS IN as that service's role (no shared connection, no SET ROLE), so
the database enforces each service's grants. The bootstrap admin login is used only by the
TEST genesis below, which refuses to run unless test_genesis=True is passed explicitly.

Part E (docs/rules/PARTE_DESIGN.md):
  * one gating-ABC definition (dq_ctl.abc_gating_failures) at both runtime sites;
  * the tier / entitlement ceiling and the veto window (engine + publisher); an applied AUTONOMY / ENTITLEMENT /
    AGENT change re-opens the units whose mode a ceiling can move, through the RULESET edge;
  * empty / missing deliveries open feed items (never the batch floor);
  * the MONITOR seat records aggregates after seal and before evaluation, and evaluates a period against frozen
    baselines (advisory only: no mode or outcome changes);
  * the parallel run compares a legacy-totals file with the new side (go/no-go evidence, never holds a unit);
  * explain_for() renders the mandatory plain-language explanation with the effective classification.

Part E §10b (DEC-018), the nightly-run simulator (demo mode only): a synthetic run is grouped into NIGHTS (one per
delivery date, files in arrival order); run_night() is the ONE night-runner used by both the full load
(cli.load_synthetic) and the simulator (sim_preload / sim_run_next), so there is no second copy of the pipeline.
"""
from __future__ import annotations

import datetime as dt
import functools
import hashlib
import json
import os
import re
import threading
import uuid
from pathlib import Path

import psycopg
import yaml

from dqagent import deploy
from dqagent.adapters.dataplane.duckdb_dp import DuckDBDataPlane
from dqagent.deploy import NotConfiguredError
from dqagent.engine.cp import CP
from dqagent.engine.evaluate import Evaluator
from dqagent.rules.model import compile_rules
from dqagent.engine.intake import Intake, ensure_tables, feed_of
from dqagent.engine.run import landing_to_gated_abc, record_abc
from dqagent.hitl.modes import abc_missing, effective_mode, gating_abc_failed
from dqagent.engine.seal import Sealer
from dqagent.governance.verifier import Verifier
from dqagent.hitl.decisions import DecisionService
from dqagent.hitl.idp import LocalIdP
from dqagent.ingest.routing import UNROUTED_FEED, Routing, arrival
from dqagent.obs import tracer
from dqagent.intervention.publish import Publisher
from dqagent.secrets import TEST_KEYS, EnvSecrets, is_test_keys

ROOT = Path(__file__).resolve().parents[1]
CEILING_KINDS = ("AUTONOMY", "ENTITLEMENT", "AGENT")       # changes that move a unit's tier / entitlement ceiling
HISTORY_BATCH = "HISTORY:{period}"                       # monitor_metrics batch id of a loaded history period
PERIOD_BATCH = "PERIOD:{period}"                         # monitor_metrics batch id of a closed period's aggregates
OPERATIONS_AUTHORITY = "AUTH_OPERATIONS"   # §10b: the operations duty is platform-wide (token group dq-duty-operations)
DEFAULT_USERS = {
    "maker": {"sub": "u-maker", "emp_id": "E100", "name": "Alex Maker",
              "groups": ["dq-duty-release-medicaid", "dq-duty-governance-rules", "dq-duty-operations"]},
    "checker": {"sub": "u-checker", "emp_id": "E200", "name": "Casey Checker",
                "groups": ["dq-duty-release-medicaid", "dq-duty-release-intake", "dq-duty-governance-rules"]},
    "checker2": {"sub": "u-checker2", "emp_id": "E210", "name": "Morgan Medicaid",
                 "groups": ["dq-duty-release-medicaid", "dq-duty-governance-rules"]},
    "snap2": {"sub": "u-snap2", "emp_id": "E410", "name": "Riley Snap", "groups": ["dq-duty-release-snap"]},
    "auditor": {"sub": "u-aud", "emp_id": "E900", "name": "Ari Auditor", "groups": ["dq-duty-audit-audit"]},
    "outsider": {"sub": "u-out", "emp_id": "E300", "name": "No Roster", "groups": ["dq-duty-release-medicaid"]},
    "snap": {"sub": "u-snap", "emp_id": "E400", "name": "Sam Snap", "groups": ["dq-duty-release-snap"]},
}


def bootstrap_roster(admin_dsn: dict, tenant: dict | None, decision_ref: str) -> None:
    """T1 / demo: the platform operator's roster grants for the local-IdP users (and the tenant approver organisation),
    written once under `decision_ref` (TEST-GENESIS for test genesis; e.g. ROSTER-BOOTSTRAP before a governed genesis).
    The roster is not itself a configuration artifact; the configuration still needs a ratified genesis."""
    tenant = tenant or {}
    with psycopg.connect(**admin_dsn) as c:
        if not c.execute("SELECT 1 FROM dq_ctl.roster WHERE decision_ref=%s LIMIT 1", (decision_ref,)).fetchone():
            org = (tenant.get("people") or {}).get
            for emp, auth in (("E100", "AUTH_MEDICAID"), ("E200", "AUTH_MEDICAID"), ("E200", "AUTH_INTAKE"),
                              ("E210", "AUTH_MEDICAID"), ("E400", "AUTH_SNAP"), ("E410", "AUTH_SNAP")):
                c.execute("INSERT INTO dq_ctl.roster(person_id,duty,authority,action,decision_ref,org) "
                          "VALUES (%s,'release',%s,'GRANT',%s,%s)", (emp, auth, decision_ref, org(emp)))
            for emp in ("E100", "E200", "E210"):
                c.execute("INSERT INTO dq_ctl.roster(person_id,duty,authority,action,decision_ref,org) "
                          "VALUES (%s,'governance','AUTH_RULES','GRANT',%s,%s)", (emp, decision_ref, org(emp)))
            c.execute("INSERT INTO dq_ctl.roster(person_id,duty,authority,action,decision_ref,org) "
                      "VALUES ('E900','audit','AUTH_AUDIT','GRANT',%s,%s)", (decision_ref, org("E900")))
            # §10b: the operations duty (run the next simulated night) for the demo operator
            c.execute("INSERT INTO dq_ctl.roster(person_id,duty,authority,action,decision_ref,org) "
                      "VALUES ('E100','operations',%s,'GRANT',%s,%s)", (OPERATIONS_AUTHORITY, decision_ref, org("E100")))
            ao = (tenant.get("governance") or {}).get("approver_org")
            if ao:                     # tenant rule: configuration is approved by this organisation only
                c.execute("INSERT INTO dq_ctl.org_settings(key,value) VALUES ('approver_org',%s) "
                          "ON CONFLICT (key) DO NOTHING", (ao,))
        c.commit()


# -- synthetic runs: arrival order and nights (Part E §10b; Part F F-02: driven by the FEEDS routing) ----------------
# The ZZ seed's partner map, kept for callers that import it; routing reads `routing.partner_map` from FEEDS.
PARTNERS = {"ZZMCOA": "ZZ-MCO-A", "ZZMCOB": "ZZ-MCO-B", "ZZBROKER": "ZZ-BROKER"}


def file_date(name: str) -> str | None:
    """The delivery date token (YYYYMMDD) in an inbound file / batch name, or None."""
    return next((x for x in re.split(r"[_.\-]", name) if len(x) == 8 and x.isdigit()), None)


def seed_routing(state: str) -> dict:
    """The SEED routing block of a state (config/state/<state>/feeds.yaml), for tools and tests that list a
    synthetic run's nights without a runtime. A runtime always uses its EFFECTIVE routing (Runtime.routing)."""
    return yaml.safe_load(open(ROOT / "config" / "state" / state / "feeds.yaml"))["routing"]


def _routing(routing) -> Routing:
    return routing if isinstance(routing, Routing) else Routing(routing)


def arrival_order(data: Path, routing) -> list[Path]:
    """Inbound files in arrival order, from the routing (required: there is no file-name fallback). Every regular,
    non-ignored file is inbound; the order is (date, route index with unrouted last, seq, name). An UNDATED unrouted
    file is not listed here (see undated_unrouted)."""
    return arrival(data, _routing(routing))[0]


def undated_unrouted(data: Path, routing) -> list[Path]:
    """Inbound files no route matches and with no date token: they open an UNROUTED_FILE item on the first night."""
    return arrival(data, _routing(routing))[1]


def synthetic_nights(data: Path, routing) -> list[tuple[str, list[Path]]]:
    """§10b: one NIGHT per delivery date of a synthetic run, [(YYYY-MM-DD, files in arrival order)], in date order.
    An unrouted file with a date token joins that night; undated unrouted files join the FIRST night (after its
    files), so each opens its feed item when the first night runs."""
    r = _routing(routing)
    nights: dict[str, list[Path]] = {}
    for p in arrival_order(Path(data), r):
        d = r.date_of(p.name) or "00000000"
        nights.setdefault(f"{d[:4]}-{d[4:6]}-{d[6:]}", []).append(p)
    out = sorted(nights.items())
    undated = undated_unrouted(Path(data), r)
    if out and undated:
        out[0] = (out[0][0], out[0][1] + undated)
    return out


class SimUnavailable(Exception):
    """The simulator is not enabled (the runtime was not started in nightly demo mode): the api maps it to 404."""


class SimConflict(Exception):
    """No night is queued, or a night is already running (advisory lock): the api maps it to 409."""


class SimRecoveryNeeded(SimConflict):
    """E6: a night's file was PARTLY ingested (registered or landed, not sealed). Ingesting it again would load it
    twice, so the night is not resumed automatically: the api maps it to 409 with the file named.
    Part F F-01: also a file whose batch was SEALED but whose LANDING->GATED ABC was never recorded."""


class RecoveryRefused(Exception):
    """Part F F-01: ABC recovery is not applicable (the batch is unknown, not sealed, or its ABC is complete)."""


class RecoveryTransient(Exception):
    """Part F R5 L3: the data plane could not be read during recovery for a reason that is not proven loss. Nothing
    is recorded and no feed item is opened; the batch stays held. The api maps it to 503 RECOVERY_RETRY."""


class RecoveryDataLost(RecoveryRefused):
    """Part F R4-1: the batch's FROZEN loads are no longer in the data plane, so its ABC cannot be recomputed. The
    batch stays held; a RECOVERY_DATA_LOST feed item is opened. The api maps it to 409 RECOVERY_DATA_LOST."""


SIM_LOCK_KEY = "dq-sim:{state}"
PIPE_LOCK_KEY = "dq-pipe:{state}:{pipeline_id}"


def _serialized(fn):
    """One state-changing operation at a time per runtime (Part D: the API thread applying a change request must
    not interleave with the publish worker's transitions or a pipeline run). Re-entrant."""
    @functools.wraps(fn)
    def wrap(self, *a, **k):
        with self._lock:
            return fn(self, *a, **k)
    return wrap


class Runtime:
    def __init__(self, cp_dsn: dict, *, state="ZZ", dp_path=":memory:", keys=None, users=None,
                 idp_url="http://localhost:8081", test_genesis=False, tenant: dict | None = None,
                 deployment: str | None = None, explainer_cfg: dict | None = None):
        """deployment (Part F F-15 / F-19 / E-06): test | demo | pilot | production; default $DQ_DEPLOYMENT, else
        production (dqagent.deploy.resolve). Outside test and demo the runtime fails closed BEFORE writing anything:
        no test genesis, no :memory: data plane, keys from the environment (EnvSecrets), never a labelled test key,
        and production needs an OIDC IdP (none exists yet, L-16). The stated deployment must equal the deployment
        bound in the control plane (dq_ctl.deployment_binding).
        explainer_cfg (K6 Front 1, transport:scripted): an optional per-RUNTIME override of the explainer config
        `explain()` otherwise loads fresh from the single global `config/explainer.yaml` on every call. Exists so
        one deployment (e.g. this demo binding) can bind a non-default provider/transport WITHOUT touching that
        global file, which `--genesis governed`'s `pilot` deployment and several tests also read as-is — changing
        the file itself would silently flip every other deployment's explainer behavior too. None (the default)
        changes nothing: explain() falls back to llm.load_config() exactly as before this parameter existed."""
        self.explainer_cfg = explainer_cfg
        self.state = state
        self.deployment = deploy.resolve(deployment)
        test_dep = self.deployment in deploy.TEST_DEPLOYMENTS
        if test_genesis and not test_dep:
            raise NotConfiguredError(f"test genesis is refused in a {self.deployment} deployment")
        if not test_dep and str(dp_path) == ":memory:":
            # R4-1: an in-memory data plane loses every frozen load on restart; never in pilot / production
            raise NotConfiguredError(f"a {self.deployment} deployment needs a data-plane file, not :memory:")
        if keys is not None:
            keys = {k: (v.encode() if isinstance(v, str) else v) for k, v in keys.items()}   # Nit 8: str or bytes
            if not test_dep and is_test_keys(keys):
                raise NotConfiguredError(f"labelled TEST keys are refused in a {self.deployment} deployment")
            self.keys, self.keys_source = dict(keys), ("TEST" if is_test_keys(keys) else "SUPPLIED")
        elif test_dep:
            self.keys, self.keys_source = dict(TEST_KEYS), "TEST"
        else:
            self.keys, self.keys_source = EnvSecrets().keys(), "ENV"
        if self.deployment == "production":
            # F-19 guard: no production IdP exists yet (OIDC is L-16). Production refuses to start rather than run
            # the local IdP; pilot runs it with the Tower chip "Local IdP: not production".
            if not os.environ.get("DQ_OIDC_ISSUER"):
                raise NotConfiguredError("production needs an OIDC IdP: set DQ_OIDC_ISSUER")
            raise NotConfiguredError("the OIDC IdP is not implemented yet (L-16); production cannot start")
        from dqagent.tenant import load_tenant
        self.tenant = tenant or load_tenant()
        self._lock = threading.RLock()
        self.cp = CP(cp_dsn)
        self.dp = DuckDBDataPlane(dp_path, keys=self.keys)
        ensure_tables(self.dp)
        self.idp = LocalIdP(users or DEFAULT_USERS)
        self.idp_kind = "LOCAL"                          # pilot: shown as "Local IdP: not production"
        self.idp_url = idp_url
        self._reveals: dict[str, dt.datetime] = {}
        from dqagent.governance.registry import Registry
        self.registry = Registry(self.cp, self.idp.jwks)
        self.test_mode = test_genesis
        if test_genesis:
            self._test_genesis()
        if not test_genesis and (self.tenant.get("governance") or {}).get("approver_org"):
            self._check_tenant_org()
        self._check_deployment(test_genesis)
        # Part D: the EFFECTIVE configuration comes from the CP (approved artifacts), never from the files.
        # Before genesis is ratified there is none: units seal but are never evaluated, so nothing can be decided.
        self.contracts, self.policy, self.harness = {}, {}, {}
        self.dependencies, self.rules = [], []
        self.ruleset_version, self.ratified, self.drift = None, False, None
        self._compiled_on = None
        self._load_effective()
        self.sealer = Sealer(self.cp, self.dp, self.contracts, self.keys["K_FP"])
        self.evaluator = Evaluator(self.cp, self.dp, self.contracts, self.rules, self.policy, self.sealer,
                                   dependencies=self.dependencies)
        self.evaluator.governance = self.governance_ctx
        self.publisher = Publisher(self.cp, self.dp, self.policy, self.dependencies, contracts=self.contracts)
        self.publisher.governance = self.governance_ctx
        self.verifier = Verifier(self.cp, self.dp, self.sealer, self.idp.jwks(), self.keys["K_FP"])
        self.decisions = DecisionService(self.cp, self.idp.jwks, self.policy, chain_expander=self.chain_rows)
        self.decisions.state_of = lambda: self.state
        # MONITOR seat (Part E §6): configured by load_monitor_history(); until then it records nothing
        self.monitor_spec: dict = {}
        self.monitor_bindings: dict = {}
        self.monitor_period: str | None = None
        self.monitor_exclude: dict = {}
        # nightly-run simulator (Part E §10b): enabled only by sim_enable() (demo --nightly); otherwise off (404)
        self.sim_data: Path | None = None
        self.sim_parte = False
        self.sim_on_governed = False      # header: "Simulator on a governed control plane"
        from dqagent.agent.memory import MemoryService
        from dqagent.agent.seats import AgentSeats
        self.seats = AgentSeats(self.cp, self.dp, self.rules, self._columns, self.contracts,
                               dependencies=self.dependencies)
        self.memory = MemoryService(self.cp, self.idp.jwks)

    # -- governance (Part D) -------------------------------------------------------------------
    def _check_tenant_org(self):
        """Red-team I5: a tenant that requires an approver organisation must find that rule in the CP; otherwise the
        runtime refuses to start (the check would silently be off). T1: org_settings / roster.org are seeded at
        genesis by the platform operator; they are not yet themselves governed by change requests."""
        want = self.tenant["governance"]["approver_org"]
        with self.cp.as_role("dq_api") as c:
            got = c.execute("SELECT value FROM dq_ctl.org_settings WHERE key='approver_org'").fetchone()
        if not got or got["value"] != want:
            raise RuntimeError(f"tenant requires approver organisation {want}, but the control plane is not "
                               "configured with it")

    def _check_deployment(self, created_test_genesis: bool):
        """Part F F-15 / E-06 / R4-2: the stated deployment must match the control plane's DERIVED binding.
          * a control plane holding TEST-GENESIS that this runtime did not just write still needs
            DQ_ALLOW_TEST_GENESIS=1 (red-team C6), and its binding (test | demo) must equal this deployment;
          * a bound control plane refuses any other deployment;
          * an unbound control plane that already has a genesis (written before the binding existed) is bound now,
            the database deriving the genesis kind; one with no genesis yet is bound when its genesis is applied."""
        has_test = self._has_test_genesis()
        if has_test and not created_test_genesis and os.environ.get("DQ_ALLOW_TEST_GENESIS") != "1":
            # red-team C6: a CP that carries TEST genesis is a test database; a non-test runtime refuses it
            raise RuntimeError("this control plane holds TEST-GENESIS artifacts; refusing to run outside test mode")
        b = self.registry.deployment_binding()
        if not b["bound"] and (has_test or self._genesis_applied()):
            self._bind(self.deployment)
            b = self.registry.deployment_binding()
        if b["bound"] and (b["deployment"] != self.deployment or b["idp_kind"] != self.idp_kind):
            raise NotConfiguredError(f"this control plane is bound to the {b['deployment']} deployment "
                                     f"({b['genesis_kind']} genesis, {b['idp_kind']} IdP); refusing a "
                                     f"{self.deployment} runtime with the {self.idp_kind} IdP")
        self.genesis_kind = b["genesis_kind"]

    def deployment_info(self) -> dict:
        """{deployment, idp_kind, genesis_kind, bound, label, sim_on_governed}: the DERIVED binding as stored (no row
        reads as production), never the runtime's own claim."""
        b = self.registry.deployment_binding()
        return {"deployment": b["deployment"], "idp_kind": b["idp_kind"], "genesis_kind": b["genesis_kind"],
                "bound": b["bound"], "label": b["label"], "sim_on_governed": self.sim_on_governed}

    def _bind(self, deployment: str) -> str:
        try:
            return self.registry.bind_deployment(deployment, self.idp_kind)
        except psycopg.errors.CheckViolation as e:
            raise NotConfiguredError(str(e).splitlines()[0]) from e

    def _genesis_applied(self) -> bool:
        with self.cp.as_role("dq_registry_svc") as c:
            return bool(c.execute("SELECT 1 FROM dq_ctl.config_change_requests r JOIN dq_ctl.config_cr_events e "
                                  "USING (cr_id) WHERE r.origin='GENESIS' AND e.event='APPLIED' LIMIT 1").fetchone())

    def _has_test_genesis(self) -> bool:
        try:
            with self.cp.as_role("dq_registry_svc") as c:
                return bool(c.execute("SELECT 1 FROM dq_ctl.config_artifacts WHERE cr_id='TEST-GENESIS' LIMIT 1").fetchone())
        except psycopg.errors.UndefinedTable:      # only a pre-Part-D CP; any other error fails closed (raises)
            return False

    def _load_effective(self):
        """(Re)compile from the CP's effective configuration, IN PLACE so every service sees the same ruleset."""
        from dqagent.governance.registry import catalog_digest, ruleset
        cfg = self.registry.effective(self.state)
        if cfg is not None and not all(cfg.get(k) for k in ("contracts", "policy", "harness")):
            cfg = None                    # post-build F1: an incomplete configuration is NOT ratified (no file fallback)
        files = ROOT / "config" / "state" / self.state
        if cfg is None:                   # BEFORE ratification only: the seed lets data SEAL; nothing is evaluated
            contracts = yaml.safe_load(open(files / "contracts_w1.yaml"))
            policy = yaml.safe_load(open(files / "policy_w1.yaml"))
            harness = yaml.safe_load(open(ROOT / "config" / "harness_w1.yaml"))
        else:
            contracts, policy, harness = cfg["contracts"], cfg["policy"], cfg["harness"]
        from dqagent.hitl.modes import validate_policy
        validate_policy(policy)
        self.contracts.clear()
        self.contracts.update(contracts["relations"])
        self.policy.clear()
        self.policy.update(policy)
        self.harness.clear()
        self.harness.update(harness)
        self.dependencies[:] = harness["dependencies"]
        self._compiled_on = dt.date.today()
        # Part E: feeds manifest (cadence / empty_ok) and the profile come from the EFFECTIVE configuration
        self.feeds = (cfg or {}).get("feeds") or (yaml.safe_load(open(files / "feeds.yaml"))
                                                    if (files / "feeds.yaml").exists() else {})
        self.profile = (cfg or {}).get("profile") or {}
        if cfg is None:
            self.rules[:] = []
            self.ratified, self.ruleset_version, self.drift = False, None, None
            self._columns = {}
            return
        rs = ruleset(self.state, cfg, on=self._compiled_on)
        self.rules[:] = rs["compiled"]
        self.ruleset_version = rs["version"]
        self.ratified = True
        approved = (cfg.get("catalog") or {}).get("digest")
        self.drift = None if approved == catalog_digest()["digest"] else \
            "the rule catalog / IR code on disk differs from the approved CATALOG digest"
        self._columns = cfg["elements"].get("columns", {})
        if hasattr(self, "seats"):
            self.seats.cols = self._columns

    def onboarding_relations(self) -> list[str]:
        """Red-team I4: the proposer may profile only registered landing relations (state profile allowlist, plus
        the relations this state's contracts already know)."""
        cfg = self.registry.effective(self.state) or {}
        allow = ((cfg.get("profile") or {}).get("onboarding_relations") or [])
        return sorted(set(allow) | set(self.contracts))

    @_serialized
    def governance_ctx(self) -> dict:
        if self._compiled_on != dt.date.today():       # red-team C1/F3/F4: the calendar can change the ruleset
            before = {(r.id, r.relation): r.effective_hash for r in self.rules}
            with tracer.span("ruleset_reload", seat="REGISTRY"):
                self._load_effective()
            moved = {rel for (rid, rel), h in before.items()
                     if {(r.id, r.relation): r.effective_hash for r in self.rules}.get((rid, rel)) != h}
            if moved:                                  # re-open affected units; their new versions need approval
                self._reopen(moved, {"reason": "calendar", "ruleset_version": self.ruleset_version})
        return {"state": self.state, "rules": self.rules, "ruleset_version": self.ruleset_version,
                "ratified": self.ratified, "drift": self.drift,
                "unapproved": self.registry.unapproved(self.state, self.rules) if self.ratified else []}

    def _reopen_ceiling(self, payload: dict, relations: set | None = None):
        """`relations=None` (every existing call site, CEILING_KINDS): every unit a ceiling can move, as before.
        Part F P1-3 (K2b, PFP :811-815): a TASK_GATE ceiling/mode change passes `relations`, so only units of the
        datasets THAT gate governs are reopened -- never every EVALUATING/HELD unit state-wide."""
        with self.cp.as_role("dq_eval_svc") as c:
            q = ("SELECT u.unit_key, u.state_version FROM dq_ctl.units u WHERE (u.state='EVALUATING' OR (u.state='HELD' "
                 "AND (SELECT e.via = 'ENGINE' AND e.payload ? 'ceiling' FROM dq_ctl.unit_events e "
                 "WHERE e.unit_key=u.unit_key ORDER BY e.state_version DESC LIMIT 1)))")
            params: tuple = ()
            if relations is not None:
                q += " AND u.node = ANY(%s)"
                params = (sorted(relations),)
            units = c.execute(q + " ORDER BY u.created_at, u.unit_key", params).fetchall()
            for u in units:
                self.cp.transition(c, u["unit_key"], u["state_version"], "RE_EVALUATE_PENDING", "RULESET",
                                   "dq_eval_svc", payload)
        self.reevaluate_pending()

    def _affected_relations(self, cr_id: str, task_gate_artifact_ids: set[str] = frozenset()) -> set:
        """ORCH-1, scoped reopen (PFP :811-815, K2b). Returns:
          (a) datasets whose `gated_dataset_introductions` row carries THIS cr_id (newly introduced, reintroduced,
              or re-introduced after their introducer was deleted -- `_introduce_datasets`'s own three reasons);
          (b) datasets whose CURRENT (latest) introducing gate is one of `task_gate_artifact_ids` -- a TASK_GATE
              item THIS SAME change request just PUT. `task_gate_ceiling`/`autonomy_tier_for` read that gate's
              content LIVE (K2a, `governance_schema.py`), so an existing unit evaluated under the gate's OLD
              ceiling/mode must be re-opened to pick up the new one.
        A consumer of datasets that are ALREADY gated by an UNCHANGED gate matches neither (a) (it introduces
        nothing new) nor (b) (its own new gate is not any dataset's current introducer) -- confirmed by direct
        trace of `_introduce_datasets`'s own logic (`governance_schema.py:337-358`): reopens nothing, exactly PFP
        :815's "a consumer of datasets that are already gated reopens nothing"."""
        with self.cp.as_role("dq_registry_svc") as c:
            out = {r["dataset"] for r in c.execute(
                "SELECT DISTINCT dataset FROM dq_ctl.gated_dataset_introductions WHERE cr_id=%s", (cr_id,)).fetchall()}
            if task_gate_artifact_ids:
                out |= {r["dataset"] for r in c.execute(
                    "SELECT DISTINCT i.dataset FROM dq_ctl.gated_dataset_introductions i WHERE i.introduced_by = ANY(%s) "
                    "AND i.version = (SELECT max(i2.version) FROM dq_ctl.gated_dataset_introductions i2 "
                    "WHERE i2.state = i.state AND i2.dataset = i.dataset)",
                    (sorted(task_gate_artifact_ids),)).fetchall()}
        return out

    def _reopen(self, relations: set | None, payload: dict):
        with self.cp.as_role("dq_eval_svc") as c:
            units = c.execute("SELECT unit_key, node, state, state_version FROM dq_ctl.units WHERE state IN "
                              "('EVALUATING','HELD','PENDING_DEPENDENCY','PENDING_RELEASE')").fetchall()
            for u in units:
                if relations is None or u["node"] in relations:
                    self.cp.transition(c, u["unit_key"], u["state_version"], "RE_EVALUATE_PENDING", "RULESET",
                                       "dq_eval_svc", payload)
        self.reevaluate_pending()

    @_serialized
    def apply_change(self, cr_id: str) -> str:
        """Registry apply, then reload, then re-open every non-terminal unit of an affected relation (C5). Traced
        under an APPLY_CHANGE root APC-<cr_id> (a repeat apply of the same request gets APC-<cr_id>-<n>)."""
        tid = tracer.mint(self.cp, f"APC-{cr_id}") if tracer.enabled(self.deployment) else None
        with tracer.root("APPLY_CHANGE", tid, parent_trace_id=None, **self._root_args()):
            return self._apply_change(cr_id)

    def _apply_change(self, cr_id: str) -> str:
        try:
            status = self.registry.apply(cr_id, expected_state=self.state, deployment=self.deployment,
                                         idp_kind=self.idp_kind)
        except psycopg.errors.CheckViolation as e:
            if "deployment" in str(e) or "genesis" in str(e):        # R4-2: the binding refused this deployment
                raise NotConfiguredError(str(e).splitlines()[0]) from e
            raise
        if status != "APPLIED":
            return status
        if self.genesis_kind is None:
            self.genesis_kind = self.registry.deployment_binding()["genesis_kind"]
        with self.cp.as_role("dq_registry_svc") as c:
            cr = c.execute("SELECT impact, items FROM dq_ctl.config_change_requests WHERE cr_id=%s", (cr_id,)).fetchone()
        self._load_effective()
        structural = any(it["kind"] in ("POLICY", "CONTRACTS", "HARNESS", "BINDINGS", "PROFILE") for it in cr["items"])
        affected = {(ch.get("after") or ch.get("before") or {}).get("relation") for ch in cr["impact"]["rules"]}
        self._reopen(None if structural else affected, {"cr_id": cr_id, "ruleset_version": self.ruleset_version})
        if not structural and any(it["kind"] in CEILING_KINDS for it in cr["items"]):
            # Part E §1.4: a tier / entitlement / agent change moves the ceiling; re-open (RULESET edge) every unit
            # whose mode it can move: EVALUATING units (A or H_ON) and HELD units a ceiling held
            self._reopen_ceiling({"cr_id": cr_id, "ruleset_version": self.ruleset_version, "reason": "autonomy"})
        # Part F P1-3 (K2b), ORCH-1 scoped reopen (PFP :811-815): PIPELINE and TASK_GATE are NOT structural (left
        # out of the tuple above, unchanged); a TASK_GATE change moves ORCH-1's per-relation ceiling, so the units
        # of the datasets it actually governs (never every EVALUATING/HELD unit state-wide) are re-opened.
        if not structural:
            gate_items = {it["artifact_id"] for it in cr["items"] if it["kind"] == "TASK_GATE"}
            if gate_items:
                gated = self._affected_relations(cr_id, gate_items)
                if gated:
                    self._reopen_ceiling({"cr_id": cr_id, "ruleset_version": self.ruleset_version,
                                         "reason": "task_gate"}, relations=gated)
        if any(it["kind"] == "REVOCATION" for it in cr["items"]):
            self.registry.open_lookback(cr_id, self.dp)    # releases made under the revoked version -> re-review
        return status

    def _test_genesis(self):
        """TEST-ONLY genesis (T1): roster grants + the seed configuration written as effective WITHOUT two people.
        Labelled TEST-GENESIS everywhere; production genesis is a GENESIS change request, which the database requires
        TWO approvers other than the author to ratify (Part E C4: it grants the agent new entitlements and tiers);
        see ratify_genesis()."""
        bootstrap_roster(self.cp.dsn, self.tenant, "TEST-GENESIS")
        try:
            self.registry.write_test_genesis(self.cp.dsn, self.state, self.deployment, self.idp_kind)
        except psycopg.errors.CheckViolation as e:          # R4-2: bound to another deployment
            raise NotConfiguredError(str(e).splitlines()[0]) from e

    # -- pipeline execution -------------------------------------------------------------
    @_serialized
    def run_pipeline(self, pipeline_id: str, batch_id: str, files: list[tuple], relations: list[str]):
        lock = None
        if hasattr(self.cp, "connect_as"):
            try:
                lock = self.cp.connect_as("dq_seal_svc")
                lock.autocommit = True
                got = lock.execute("SELECT pg_try_advisory_lock(hashtext(%s)) ok",
                                   (PIPE_LOCK_KEY.format(state=self.state, pipeline_id=pipeline_id),)).fetchone()["ok"]
                if not got:
                    raise SimConflict(f"pipeline {pipeline_id} is already running (advisory lock held)")
            except (psycopg.Error, Exception) as e:
                if isinstance(e, SimConflict):
                    if lock:
                        lock.close()
                    raise
                if lock:
                    try:
                        lock.close()
                    except Exception:
                        pass
                lock = None
        try:
            return self._run_pipeline_body(pipeline_id, batch_id, files, relations)
        finally:
            if lock:
                try:
                    lock.execute("SELECT pg_advisory_unlock(hashtext(%s))",
                                 (PIPE_LOCK_KEY.format(state=self.state, pipeline_id=pipeline_id),))
                    lock.close()
                except Exception:
                    pass

    def _run_pipeline_body(self, pipeline_id: str, batch_id: str, files: list[tuple], relations: list[str]):
        bref = f"batch:{batch_id}"
        with tracer.span("intake", seat="INTAKE", subject=bref) as sp_intake:
            sp = sp_intake
            it = Intake(self.cp, self.dp, pipeline_id, batch_id, feeds=self.feeds, state=self.state)
            for f in files:
                if f[0] == "IE":
                    it.ie_file(f[1])
                elif f[0] == "DELIMITED":            # K5 / F-13: a new source added by configuration only
                    it.delimited_file(f[1], f[2])
                else:
                    it.x12_file(f[1], f[2], f[0], f[3] if len(f) > 3 else None)
            # Part F F-01: the intake ABC (parsed from the files; it cannot be recomputed later) is recorded BEFORE the
            # seal; only LANDING->GATED comes after it, and that one is deterministic over the frozen load set, so a
            # crash between the seal and it is recoverable (recover_batch_abc). Row order is unchanged: intake first.
            abc = record_abc(self.cp, batch_id, it.abc)
            sp.attr("files", len(files)).attr("abc_rows", len(abc)).attr("feed_items", len(it.feed_items))
        with tracer.span("seal", seat="INTAKE", subject=bref) as sp:
            units = self.sealer.seal(batch_id, pipeline_id, relations)
            sp.attr("units", len(units)).attr("relations", len(relations))
            # F-34: the batch's authorities, known once its units exist, on its spans (and the open file span)
            auths = self._batch_authorities(batch_id) if tracer.current_root() is not None else []
            sp_intake.authorize(auths)
            tracer.authorize_open(auths)
        with tracer.span("abc", seat="INTAKE", subject=bref, authorities=auths) as sp:
            lg, late = landing_to_gated_abc(self.cp, self.dp, batch_id, relations)
            abc = abc + record_abc(self.cp, batch_id, lg)
            with self.cp.as_role("dq_eval_svc") as c:       # Part E I1: the one definition of a gating ABC failure
                abc_failed = gating_abc_failed(c, batch_id)
            sp.attr("rows", len(abc)).attr("late_loads", len(late)).attr("gating_failed", bool(abc_failed))
        with tracer.span("monitor", seat="MONITOR", subject=bref, authorities=auths) as sp:
            monitored = self.monitor_record(batch_id, relations)   # MONITOR seat: after seal, before evaluation
            sp.attr("rows", monitored)
        roots = [r for r in relations if "unit_from_parent" not in self.contracts[r]]
        summary = {}
        for rel in roots:
            with tracer.span(f"evaluate rel:{rel}", seat="EVALUATOR", subject=f"rel:{rel}", authorities=auths) as sp:
                summary[rel] = self.evaluator.evaluate(batch_id, rel, abc_failed=abc_failed)
                sp.attr("summary", {k: v for k, v in summary[rel].items() if isinstance(v, int)
                                    and not isinstance(v, bool)})
        if self.ratified:
            # Part C WS3 seats post to the blackboard. Before genesis the agent is not registered, so the database
            # refuses any agent post (fail closed); nothing is evaluated then either, so there is nothing to post.
            with tracer.span("seat:INTAKE", seat="INTAKE", subject=bref, authorities=auths):
                self.seats.after_intake(batch_id, abc)
            with tracer.span("seat:EVALUATOR", seat="EVALUATOR", subject=bref, authorities=auths):
                self.seats.after_evaluation(batch_id)
            with tracer.span("seat:TRIAGE", seat="TRIAGE", subject=bref, authorities=auths):
                self.seats.triage(batch_id)
        return {"units": units, "abc": abc, "late_loads": late, "summary": summary, "abc_failed": abc_failed,
                "feed_items": it.feed_items, "monitor_rows": monitored}

    @_serialized
    def reevaluate_pending(self):
        """Re-evaluate PENDING_DEPENDENCY units whose dependencies may have become terminal."""
        with tracer.span("reevaluate", seat="EVALUATOR") as sp:
            with self.cp.as_role("dq_eval_svc") as c:
                rows = c.execute("SELECT DISTINCT batch_id, node FROM dq_ctl.units WHERE state IN "
                                 "('PENDING_DEPENDENCY','RE_EVALUATE_PENDING','SEALED') ORDER BY batch_id, node"
                                 ).fetchall()
                abcf = {r["batch_id"]: True for r in c.execute(            # Part E I1: the one definition
                    "SELECT DISTINCT batch_id FROM dq_ctl.abc_gating_failures").fetchall()}
            out = {f"{r['batch_id']}:{r['node']}": self.evaluator.evaluate(r["batch_id"], r["node"],
                                                                            abc_failed=abcf.get(r["batch_id"], False))
                   for r in rows}
            sp.attr("batches", len(rows))
            return out

    # -- Part F P1-1: trace roots -------------------------------------------------------------------------------
    def _batch_authorities(self, batch_id: str) -> list[str]:
        with self.cp.as_role("dq_eval_svc") as c:
            return sorted({a for r in c.execute("SELECT authorities FROM dq_ctl.units WHERE batch_id=%s",
                                                 (batch_id,)).fetchall() for a in r["authorities"]})

    def runs_for(self, scope, *, unit_key: str | None = None, decision_id: str | None = None) -> list[dict]:
        """F-34: the runs of a unit (its events' roots) or of a decision, as the CALLER may see them (dq_api with its
        scope: the runs' own row policies apply). For the Tower trace explorer."""
        with self.cp.as_role("dq_api", scope) as c:
            if decision_id is not None:
                ids = [r["trace_id"] for r in c.execute("SELECT trace_id FROM dq_ctl.decision_runs WHERE decision_id=%s",
                                                        (decision_id,)).fetchall()]
            else:
                ids = [r["trace_id"] for r in c.execute("SELECT trace_id FROM dq_ctl.unit_runs WHERE unit_key=%s",
                                                        (unit_key,)).fetchall()]
            return [dict(r) for r in c.execute(
                "SELECT trace_id, kind, parent_trace_id, state, night_date, attempt, person_id, started_at "
                "FROM dq_ctl.runs WHERE trace_id = ANY(%s) ORDER BY started_at, trace_id", (ids,)).fetchall()]

    def _root_args(self, **kw) -> dict:
        return {"cp": self.cp, "state": self.state, "config_digest": self.ruleset_version or "unratified",
                "code_digest": tracer.code_sha256(), "deployment": self.deployment, **kw}

    @_serialized
    def verify_and_publish(self):
        """One worker cycle under a DEFERRED PUBLISH_CYCLE root (I9): it is written only when the cycle changed
        something (an event, a release check, a manifest version, a result, a feed item, a swept reveal)."""
        # C1: every trace read and id mint is observe-only; an unreachable trace store runs the cycle untraced
        m0 = tracer.marks(self.cp) if tracer.enabled(self.deployment) else None
        if m0 is None:
            return self._verify_and_publish()
        tid = tracer.mint(self.cp, f"PCY-{self.state}-e{m0['e']}-c{m0['c']}")
        with tracer.root("PUBLISH_CYCLE", tid, deferred=True, **self._root_args()) as r:
            out, swept = self._verify_and_publish(with_sweep=True)
            m1 = tracer.marks(self.cp)
            r.mark_changed(bool(swept) or m1 is None or any(m1[k] != m0[k] for k in ("e", "c", "r", "m", "f")))
        return out

    def _verify_and_publish(self, with_sweep: bool = False):
        with tracer.span("verify", seat="VERIFIER"):
            self.verifier.attest_tokens()
            self.verifier.attest_decisions()
            self.verifier.attest_fingerprints()
            self.verifier.attest_publication(self.publisher.dp_name)
        with tracer.span("publish", seat="PUBLISHER") as sp:
            out = self.publisher.run()
            sp.attr("released", len(out["released"])).attr("blocked", len(out["blocked"]))
        self.reevaluate_pending()
        with tracer.span("seat:PUBLISHER", seat="PUBLISHER"):
            self.seats.after_publish()
        with tracer.span("missing_deliveries", seat="INTAKE"):
            self.check_missing_deliveries()
        with tracer.span("sweep_reveals"):
            swept = self.sweep_reveals()
        return (out, swept) if with_sweep else out

    def units_held(self):
        with self.cp.as_role("dq_eval_svc") as c:
            return c.execute("SELECT * FROM dq_ctl.units WHERE state='HELD' ORDER BY created_at").fetchall()

    # -- hooks used by the api ----------------------------------------------------------
    @_serialized
    def after_decision(self):
        self.verify_and_publish()

    def deny_lag_s(self) -> int:
        with self.cp.as_role("dq_publish_svc") as c:
            r = c.execute("SELECT min(at) t FROM dq_ctl.deny").fetchone()
            denied = {x["unit_key"] for x in c.execute("SELECT unit_key FROM dq_ctl.deny").fetchall()}
        live = {x[0] for x in self.dp.fetchall("SELECT unit_key FROM dq_rel.manifest WHERE version = "
                                               "(SELECT version FROM dq_rel.manifest_pointer WHERE id=1)")}
        if not (live & denied) or not r["t"]:
            return 0
        return int((dt.datetime.now(dt.timezone.utc) - r["t"]).total_seconds())

    def harness_ok(self) -> bool:
        from dqagent.harness.plan import validate
        return not validate(self.harness)

    def pipelines_view(self, scope):
        """Counts are computed UNDER the caller's scope (RLS), never as an admin (SEC-W1-8)."""
        from dqagent.harness.plan import order
        with self.cp.as_role("dq_api", scope) as c:
            states = c.execute("SELECT node, state, count(*) n FROM dq_ctl.units GROUP BY 1, 2").fetchall()
        by_node = {}
        for s in states:
            by_node.setdefault(s["node"], {})[s["state"]] = s["n"]
        pls = []
        for p in self.harness["pipelines"]:
            gn = []
            for n in p["gated_nodes"]:
                st = by_node.get(n, {})
                worst = ("HELD" if st.get("HELD") else "PENDING" if any(k in st for k in
                         ("PENDING_DEPENDENCY", "PENDING_RELEASE", "EVALUATING", "SEALED")) else
                         "RELEASED" if st else "EMPTY")
                gn.append({"node": n, "state": worst, "x_counts": st})
            pls.append({"id": p["id"], "platform": p["platform"], "orchestrator": p["orchestrator"],
                        "domains": p["domains"], "gated_nodes": gn})
        return {"pipelines": pls, "order": order(self.harness),
                "dependencies": [{"domain": d["domain"], "depends_on": d["depends_on"], "policy": d["policy"],
                                  "temporal_binding": d.get("temporal_binding", {}).get("type", "LATEST_RELEASED")}
                                 for d in self.dependencies]}

    def chain_rows(self, unit, row_seqs: list[int]) -> list[int]:
        """CHAIN quarantine (SCP-W1-8): rows of child relations whose parent row is quarantined."""
        if not row_seqs:
            return []
        out = []
        for rel, ct in self.contracts.items():
            p = ct.get("unit_from_parent")
            if not p or p["relation"] != unit["node"]:
                continue
            keys = p.get("join_keys") or [p["join"]]
            on = " AND ".join(f"c.{k} = h.{k}" for k in keys)
            ph = ",".join("?" * len(row_seqs))
            out += [r[0] for r in self.dp.fetchall(
                f"SELECT c._dq_row_seq FROM {rel} c JOIN {unit['node']} h ON {on} AND c._dq_unit_key = h._dq_unit_key "
                f"WHERE h._dq_unit_key = ? AND h._dq_row_seq IN ({ph})", [unit["unit_key"], *row_seqs])]
        return out

    REVEAL_TTL_MIN = 15
    REVEAL_MASKED_TAGS = ("identity", "clinical")

    def reveal(self, unit, person_id, row_seqs=None) -> tuple[str, str]:
        """Short-lived reveal view IN THE DP over the chosen rows (default: the unit's rows).
        Every identity- or clinical-tagged column is masked, including member and MSIS IDs; the
        reviewer recognises a record by _dq_row_seq and the unmasked coverage/payment columns.
        Views are dropped by sweep_reveals() after REVEAL_TTL_MIN (SEC-W1-9).
        STUB (EXIT_CRITERIA): binding the view to the reviewer's own DP identity (grants) is T2."""
        name = f"dq_reveal.r_{uuid.uuid4().hex[:10]}"
        tags = self.contracts[unit["node"]].get("control_tags", {})
        cols = [c for c in self.dp.columns(unit["node"]) if not c.startswith("_dq_") or c == "_dq_row_seq"]
        sel = ", ".join(f"'***' AS {c}" if set(tags.get(c, [])) & set(self.REVEAL_MASKED_TAGS) else c for c in cols)
        where = f"_dq_unit_key = '{unit['unit_key']}'"
        if row_seqs:
            where += " AND _dq_row_seq IN (" + ",".join(str(int(r)) for r in row_seqs) + ")"
        self.dp.execute(f"CREATE VIEW {name} AS SELECT {sel} FROM {unit['node']} WHERE {where}")
        exp = dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=self.REVEAL_TTL_MIN)
        self._reveals[name] = exp
        return name, exp.isoformat()

    def sweep_reveals(self, now=None) -> list[str]:
        now = now or dt.datetime.now(dt.timezone.utc)
        gone = [n for n, e in list(self._reveals.items()) if e <= now]
        for n in gone:
            self.dp.execute(f"DROP VIEW IF EXISTS {n}")
            self._reveals.pop(n, None)
        if gone:
            with self.cp.as_role("dq_api") as c:
                c.execute("SELECT dq_ctl.append_audit('REVEAL_EXPIRED', %s)",
                          (hashlib.sha256("|".join(sorted(gone)).encode()).hexdigest(),))
        return gone

    # -- Part E: helpers for the api and the CLI ---------------------------------------------------------------
    def _relation_authority(self, rel: str) -> str:
        """The authority a relation-level aggregate is filed under: a relation's own single authority, else its
        pre-split authority (AUTH_INTAKE). A child relation (lines) follows its root."""
        ct = self.contracts.get(rel) or {}
        parent = (ct.get("unit_from_parent") or {}).get("relation")
        if parent:
            ct = self.contracts.get(parent) or ct
        auths = sorted({a for v in (ct.get("authorities_by_program") or {}).values() for a in v})
        if len(auths) == 1:
            return auths[0]
        return ct.get("pre_split_authority") or "AUTH_INTAKE"

    def classification(self) -> dict:
        """The EFFECTIVE classification artifact content {element: class} (empty before genesis)."""
        arts = self.registry.artifacts(self.state)
        return dict((arts.get(f"{self.state}:classification") or {}).get("content") or {})

    @staticmethod
    def masking_of(classes: dict, contracts: dict | None) -> dict:
        """THE masking map the explainer uses (one definition for the api and the runtime): the effective
        classification plus every contract's control tags (a column tagged identity / clinical / part2 in a contract
        stays masked whatever the classification artifact says)."""
        masking = dict(classes or {})
        for ct in (contracts or {}).values():
            for col, tags in (ct.get("control_tags") or {}).items():
                masking[col] = list(tags) if isinstance(tags, (list, tuple, set)) else [tags]
        return masking

    def masking(self) -> dict:
        return self.masking_of(self.classification(), self.contracts)

    def explain_for(self, kind: str, obj: dict, *, cfg: dict | None = None, subject: str | None = None,
                    authorities=()) -> dict:
        """The mandatory plain-language explanation of `obj` (Part E §7), rendered by dqagent.llm.explain with the
        EFFECTIVE masking (classification plus contract control tags; masked classes and Part 2 units withheld by
        the facts builder).

        kind  one of dqagent.llm.KINDS (unit_card, rule_disposition, decision, change_request, lookback,
              monitor_signal, parallel_break, feed_item, demotion, autonomy, cde, nightly_run)
        obj   the object's facts (a control-plane row or aggregate; monitor results and parallel-run rows as-is)
        subject      the object explained (cache key and citations, Part F P1-1 §E); default `<kind>:<sha of obj>`
        authorities  the authorities its rows are read under; empty = engine-wide (read by the audit duty only)
        Returns {text, source, fact_check: TEMPLATE | PASS | FALLBACK, facts_hash, text_hash}.
        Never in the decision path. Goes through ExplainService (cache, EXPLAIN trace, llm_calls, guardrails)."""
        from dqagent import llm
        from dqagent.obs.explain_service import ExplainService, public
        if subject is None:
            subject = f"{kind}:" + hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()[:16]
        # the RUNTIME's deployment, never $DQ_DEPLOYMENT: the transport allow-list (R4-3) follows the bound runtime
        svc = ExplainService(self.cp, cfg=cfg if cfg is not None else (self.explainer_cfg or llm.load_config()), deployment=self.deployment,
                             state=self.state)
        return public(svc.explain(kind, obj, subject=subject, authorities=authorities, masking=self.masking()))

    def unit_mode(self, unit_key: str) -> dict:
        """WHY a unit has its mode, recomputed from stored rows as the publisher does: {mode, mode_trace, ceiling
        (e.g. 'L2 ceiling'), autonomy {authority: {tier, entitled}}, veto_window {opened_at, deadline, remaining_s,
        vetoed} | None}."""
        from dqagent.hitl.modes import ceiling_label
        with self.cp.as_role("dq_publish_svc") as c:
            u = c.execute("SELECT * FROM dq_ctl.units WHERE unit_key=%s", (unit_key,)).fetchone()
            if u is None:
                raise KeyError(unit_key)
            m, trace, auto = effective_mode(c, self.policy, u, self.state)
            w = c.execute("SELECT opened_at, deadline, remaining_s, vetoed FROM dq_ctl.veto_window_status "
                          "WHERE unit_key=%s ORDER BY eval_version DESC, window_id DESC LIMIT 1", (unit_key,)).fetchone()
        return {"mode": m, "mode_trace": trace, "ceiling": ceiling_label(trace), "autonomy": auto,
                "veto_window": dict(w) if w else None}

    @_serialized
    def veto(self, scope: list[str], unit_key: str, expected_version: int, person_id: str, reason_code: str,
             jti: str | None = None, explanation_ref: dict | None = None) -> int:
        """A person vetoes an EVALUATING unit inside its veto window (dq_ctl.veto_unit: EVALUATING -> HELD via VETO,
        RELEASE tier of the unit's authorities -> L2). Raises decisions.Stale when the unit is past veto (released /
        not EVALUATING / no window): the api maps it to 409. Returns the new state_version."""
        from dqagent.hitl.decisions import Denied, Stale
        try:
            with self.cp.as_role("dq_api", scope) as c:
                v = c.execute("SELECT dq_ctl.veto_unit(%s,%s,%s,%s,%s,%s::jsonb) AS v",
                              (unit_key, expected_version, person_id, reason_code, jti,
                               json.dumps(explanation_ref) if explanation_ref else None)).fetchone()["v"]
        except psycopg.errors.ObjectNotInPrerequisiteState as e:
            raise Stale(str(e).splitlines()[0]) from e
        except psycopg.errors.SerializationFailure as e:
            raise Stale(str(e).splitlines()[0]) from e
        except psycopg.errors.NoDataFound as e:
            raise Denied("unit not visible in your scope") from e
        self.verify_and_publish()
        return v

    def ratify_genesis(self, author: str = "maker", approvers=("checker", "checker2")) -> dict:
        """NON-test genesis (T1 / demo): the author proposes the seed as a GENESIS change request and two OTHER
        people approve it with step-ups (the database requires two: genesis grants the agent new entitlements and
        tiers, Part E C4). Users are local-IdP user names. Returns {cr_id, status}."""
        emp = lambda u: self.idp.users[u]["emp_id"]     # noqa: E731
        cr = self.registry.propose_genesis(self.state, emp(author))
        for a in approvers:
            tok = self.idp.token(a, step_up=True, challenge=self.registry.challenge(cr["cr_id"], "APPROVED"))
            self.registry.decide(cr["cr_id"], "APPROVED", tok, emp(a), ["governance:AUTH_RULES"])
        return {"cr_id": cr["cr_id"], "approvals_required": cr["approvals_required"],
                "status": self.apply_change(cr["cr_id"])}

    # -- Part E §3.2: missing expected deliveries -------------------------------------------------------------------
    @staticmethod
    def cadence_seconds(every: str) -> int:
        """P1D | P1W | PnM (months count as 31 days: a monthly feed is missing only after a full month) | any
        duration registry.iso_duration_seconds accepts."""
        import re
        from dqagent.governance.registry import iso_duration_seconds
        m = re.fullmatch(r"P(\d+)M", str(every or ""))
        return int(m.group(1)) * 31 * 86400 if m else iso_duration_seconds(every)

    def check_missing_deliveries(self, now: dt.datetime | None = None,
                                 received: dict[str, dt.datetime] | None = None) -> list[int]:
        """Open MISSING_DELIVERY for every EXPECTED feed (cadence.expected, delivered) with no file received inside
        its cadence, measured from its last file (or, if it never delivered, the first file of any feed). The item's
        period is the date the missed window started, so a window is flagged once. Never touches a batch floor.
        received: {feed: last delivery time} in place of dq_ctl.files.received_at (the simulator's business dates)."""
        from dqagent.engine.intake import FEED_AUTHORITY, feed_of, feed_policy
        feeds = ((self.feeds or {}).get("feeds") or {})
        opened = []
        with self.cp.as_role("dq_seal_svc") as c:
            now = now or c.execute("SELECT clock_timestamp() t").fetchone()["t"]
            last = dict(received) if received is not None else {feed_of(r["format"]): r["t"] for r in c.execute(
                "SELECT format, max(received_at) t FROM dq_ctl.files WHERE direction <> 'OUTBOUND' GROUP BY 1").fetchall()}
            first_any = min(last.values()) if last else None
            for feed in sorted(feeds):
                pol = feed_policy(self.feeds, feed)
                if not pol["expected"] or not pol["every"] or first_any is None:
                    continue
                anchor = last.get(feed, first_any)
                due = anchor + dt.timedelta(seconds=self.cadence_seconds(pol["every"]))
                if now > due:
                    opened.append(c.execute("SELECT dq_ctl.feed_item_open(%s,%s,'MISSING_DELIVERY',NULL,NULL,%s,%s) i",
                                            (self.state, feed, FEED_AUTHORITY, due.date().isoformat())).fetchone()["i"])
        return opened

    # -- Part E §6: the MONITOR seat (advisory only) ---------------------------------------------------------------
    def _monitor_cfg(self) -> dict:
        """The effective MONITOR artifact mapped onto dqagent.agent.monitor.config()."""
        arts = self.registry.artifacts(self.state)
        mc = (arts.get(f"{self.state}:monitor") or {}).get("content") or {}
        out: dict = {}
        if "run_rules" in mc:
            out["rules"] = dict(mc["run_rules"])
        if "min_periods" in mc:
            out["min_baseline_periods"] = int(mc["min_periods"])
        if "psi" in mc:
            out["psi"] = dict(mc["psi"])
        return out

    def _insert_metrics(self, c, batch_id: str, rows: list[dict]):
        for r in rows:
            c.execute("INSERT INTO dq_ctl.monitor_metrics(batch_id,period,relation,metric,key,value,n,authority) "
                      "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                      (batch_id, r["period"], r["relation"], r["metric"], r.get("key") or "", r["value"], r.get("n"),
                       self._relation_authority(r["relation"])))

    def load_monitor_history(self, path) -> dict:
        """Load a monitor_history/1 file as the MONITOR seat's history: its spec and bindings configure the seat, its
        periods are stored as aggregate rows (batch HISTORY:<period>), and a baseline is PINNED (frozen SPC_BASELINE
        memory) for every series with at least min_periods eligible periods (signalled periods excluded). Series with
        fewer stay BASELINE_BUILDING. Returns {periods, rows, pinned, building}."""
        from dqagent.agent import monitor as mon
        doc = mon.load_history(path)
        self.monitor_spec = dict(doc["spec"])
        self.monitor_bindings = dict(doc["bindings"])
        self.monitor_period = doc.get("current_period")
        self.monitor_exclude = doc["exclude"]
        with self.cp.as_role("dq_monitor_svc") as c:
            have = {r["batch_id"] for r in c.execute("SELECT DISTINCT batch_id FROM dq_ctl.monitor_metrics "
                                                     "WHERE batch_id LIKE 'HISTORY:%%'").fetchall()}
            by_p: dict = {}
            for r in doc["metrics"]:
                by_p.setdefault(r["period"], []).append(r)
            for period, rows in sorted(by_p.items()):
                if HISTORY_BATCH.format(period=period) not in have:
                    self._insert_metrics(c, HISTORY_BATCH.format(period=period), rows)
        pinned, building = self._pin_baselines(doc["metrics"], before=self.monitor_period)
        return {"periods": sorted(by_p), "rows": len(doc["metrics"]), "pinned": pinned, "building": building}

    def _pinned_baseline(self, c, scope_id: str) -> dict | None:
        r = c.execute("SELECT content FROM dq_ctl.agent_memory WHERE kind='SPC_BASELINE' AND scope_type='METRIC' "
                      "AND scope_id=%s ORDER BY memory_id DESC LIMIT 1", (scope_id,)).fetchone()
        return (r["content"].get("template_args") or None) if r else None

    @staticmethod
    def _series_id(rel: str, metric: str, key: str) -> str:
        return f"{rel}|{metric}|{key}"

    def _pin_baselines(self, history: list[dict], before: str | None) -> tuple[list[str], list[str]]:
        """Pin a frozen baseline for every series with enough eligible history (an already pinned one is kept as-is:
        frozen until a MONITOR change request lists it under `rebaseline`). The memory body uses the whitelisted keys;
        the full frozen baseline document rides under `template_args` and is verified by its own hash on use."""
        from dqagent.agent import monitor as mon
        cfg = self._monitor_cfg()
        pinned, building = [], []
        with self.cp.as_role("dq_monitor_svc") as c:
            exclude = self._exclusions(c)
            for rel, metric, key in mon.series_keys(history):
                sid = self._series_id(rel, metric, key)
                if self._pinned_baseline(c, sid) is not None:
                    pinned.append(sid)
                    continue
                b = mon.build_baseline(history, rel, metric, key, before=before,
                                       exclude_periods=exclude.get((rel, metric, key), ()), cfg=cfg)
                if b["status"] != "FROZEN":
                    building.append(sid)
                    continue
                content = {"relation": rel, "metric": metric, "key": key, "n_periods": len(b["periods"]),
                           "min_periods": b["min_periods"], "baseline_ref": b["baseline_hash"], "template_args": b}
                for k in ("cl", "ucl", "lcl", "mr_bar", "sigma", "p_bar"):
                    if k in b:
                        content[k] = b[k]
                c.execute("SELECT dq_ctl.mem_pin_baseline('METRIC', %s, %s, %s::jsonb, 'SPC_BASELINE')",
                          (sid, self._relation_authority(rel), json.dumps(content, sort_keys=True, default=str)))
                pinned.append(sid)
        return pinned, building

    def monitor_record(self, batch_id: str, relations: list[str]) -> int:
        """MONITOR seat, after seal and before evaluation: the batch's aggregates (computed in the data plane; no row
        value leaves it) for every monitored relation, stored as dq_monitor_svc under the current period. Advisory:
        nothing here is read by the evaluator or the publisher. Returns the number of rows stored."""
        if not self.monitor_spec or not self.monitor_period:
            return 0
        from dqagent.agent import monitor as mon
        rows = []
        for rel in relations:
            if rel in self.monitor_spec:
                rows += mon.query_aggregates(self.dp.cur(), rel, self.monitor_spec[rel], self.monitor_bindings,
                                             period=self.monitor_period, batch_ids=[batch_id])
        if rows:
            with self.cp.as_role("dq_monitor_svc") as c:
                self._insert_metrics(c, batch_id, rows)
        return len(rows)

    def _period_batches(self, c, period: str, rel: str) -> list[str]:
        return [r["batch_id"] for r in c.execute(
            "SELECT DISTINCT batch_id FROM dq_ctl.monitor_metrics WHERE period=%s AND relation=%s "
            "AND batch_id NOT LIKE 'HISTORY:%%' AND batch_id NOT LIKE 'PERIOD:%%' ORDER BY batch_id",
            (period, rel)).fetchall()]

    def _live_signalled(self, c) -> dict:
        """E7: {(relation, metric, key): [periods]} of every CLOSED live period that signalled, read from the MONITOR
        seat's own board posts (SPC_SIGNAL / MIX_SHIFT on PERIOD:<period>). These join the history file's signalled
        periods in the exclusion set, so a live signal never poisons a later (unpinned) baseline."""
        out: dict = {}
        for r in c.execute("SELECT DISTINCT subject_id, batch_id FROM dq_ctl.agent_blackboard WHERE seat='MONITOR' "
                           "AND template IN ('SPC_SIGNAL','MIX_SHIFT') AND batch_id LIKE 'PERIOD:%%'").fetchall():
            rel, metric, key = (r["subject_id"].split("|") + ["", "", ""])[:3]
            out.setdefault((rel, metric, key), set()).add(r["batch_id"].split(":", 1)[1])
        return out

    def _exclusions(self, c) -> dict:
        """The history file's signalled periods plus every live-signalled period (E7)."""
        ex = {k: set(v) for k, v in (self.monitor_exclude or {}).items()}
        for k, v in self._live_signalled(c).items():
            ex.setdefault(k, set()).update(v)
        return {k: sorted(v) for k, v in ex.items()}

    def monitor_exclusions(self) -> dict:
        """Public: the exclusion set every baseline built outside the seat uses (the api's Monitoring view)."""
        with self.cp.as_role("dq_monitor_svc") as c:
            return self._exclusions(c)

    def monitor_progress(self, period: str) -> dict:
        """Live smoke L4: how much of an OPEN period has been received, by BUSINESS date (the latest delivery date
        of any inbound file: the simulated clock in the nightly demo). {through, share | None}."""
        from dqagent.agent import monitor as mon
        dates = [t.date() for t in self._delivered_by_feed().values()]
        b = mon.period_bounds(period)
        if b:
            dates = [d for d in dates if d < b[1]]
        through = max(dates) if dates else None
        return {"through": through, "share": mon.received_share(period, through)}

    def _history_rows(self, c, before: str) -> list[dict]:
        return [dict(r) | {"value": float(r["value"])} for r in c.execute(
            "SELECT period, relation, metric, key, value, n FROM dq_ctl.monitor_metrics "
            "WHERE (batch_id LIKE 'HISTORY:%%' OR batch_id LIKE 'PERIOD:%%') AND period < %s "
            "ORDER BY period, relation, metric, key", (before,)).fetchall()]

    @_serialized
    def monitor_close_period(self, period: str | None = None) -> list[dict]:
        """Close a monitoring period: its aggregates over every batch recorded in it (recomputed in the data plane,
        stored once as PERIOD:<period>), evaluated against the PINNED baselines (frozen; BASELINE_BUILDING when there
        are too few periods), then SPC_SIGNAL / MIX_SHIFT / BASELINE_BUILDING posted by the MONITOR seat.
        Advisory only: no disposition, mode or unit state changes. Returns the evaluation results."""
        from dqagent.agent import monitor as mon
        period = period or self.monitor_period
        if not period or not self.monitor_spec:
            return []
        pb = PERIOD_BATCH.format(period=period)
        with self.cp.as_role("dq_monitor_svc") as c:
            closed = c.execute("SELECT 1 FROM dq_ctl.monitor_metrics WHERE batch_id=%s LIMIT 1", (pb,)).fetchone()
            if not closed:
                cur = []
                for rel in sorted(self.monitor_spec):
                    batches = self._period_batches(c, period, rel)
                    if batches:
                        cur += mon.query_aggregates(self.dp.cur(), rel, self.monitor_spec[rel], self.monitor_bindings,
                                                    period=period, batch_ids=batches)
                self._insert_metrics(c, pb, cur)
        results = self.monitor_status(period)
        with self.cp.as_role("dq_monitor_svc") as c:
            for res in results:
                bp = mon.board_post(res)
                if bp is None:
                    continue
                tmpl, _ = bp
                body = {"relation": res["relation"], "metric": res["metric"], "period": period}
                if tmpl == "SPC_SIGNAL":
                    body |= {"key": res["key"], "value": res["value"], "lcl": res["lcl"], "ucl": res["ucl"],
                             "cl": res["cl"], "rule": ",".join(s["rule"] for s in res["signals"]),
                             "baseline_ref": res["baseline_hash"]}
                elif tmpl == "MIX_SHIFT":
                    body |= {"psi": res["value"], "disposition": res["status"], "baseline_ref": res["baseline_hash"]}
                else:
                    body |= {"key": res["key"], "n_periods": res["baseline_periods"],
                             "min_periods": res["baseline"].get("min_periods")}
                sid = self._series_id(res["relation"], res["metric"], res["key"])
                if c.execute("SELECT 1 FROM dq_ctl.agent_blackboard WHERE template=%s AND subject_id=%s AND batch_id=%s "
                             "LIMIT 1", (tmpl, sid, pb)).fetchone():
                    continue                                   # one post per series and period
                self.seats.board.post(c, pb, self._relation_authority(res["relation"]),
                                      "NOTE" if tmpl == "BASELINE_BUILDING" else "FINDING", "METRIC", sid, tmpl, body)
        return results

    def monitor_status(self, period: str | None = None) -> list[dict]:
        """Evaluation of a CLOSED period against the pinned (frozen) baselines, recomputed from stored rows (no side
        effects; deterministic). Each result: {relation, metric, key, chart, period, status OK | SIGNAL | WATCH | SHIFT |
        BASELINE_BUILDING | NOT_COMPUTED, value, n, cl/ucl/lcl/sigma | psi bins, signals[], baseline_hash, baseline}."""
        from dqagent.agent import monitor as mon
        period = period or self.monitor_period
        if not period:
            return []
        cfg = self._monitor_cfg()
        with self.cp.as_role("dq_monitor_svc") as c:
            cur = [dict(r) | {"value": float(r["value"])} for r in c.execute(
                "SELECT period, relation, metric, key, value, n FROM dq_ctl.monitor_metrics WHERE batch_id=%s "
                "ORDER BY relation, metric, key", (PERIOD_BATCH.format(period=period),)).fetchall()]
            hist = self._history_rows(c, period)
            exclude = self._exclusions(c)                      # E7: live-signalled periods never feed a baseline
            baselines = {}
            for rel, metric, key in mon.series_keys(cur):
                b = self._pinned_baseline(c, self._series_id(rel, metric, key))
                if b is not None:
                    baselines[(rel, metric, key)] = b          # pinned: frozen as-is (never rebuilt here)
        return mon.run(hist, cur, period=period, exclude=exclude, baselines=baselines, cfg=cfg)

    def monitor_series(self, relation: str, metric: str, key: str = "") -> dict:
        """One series for a control chart: {points: [{period, value, n, source HISTORY | PERIOD}], baseline (pinned,
        frozen) | None}. Per-batch partial rows are not points (a period is the unit of the chart)."""
        with self.cp.as_role("dq_monitor_svc") as c:
            pts = [{"period": r["period"], "value": float(r["value"]), "n": r["n"],
                    "source": r["batch_id"].split(":", 1)[0]} for r in c.execute(
                "SELECT batch_id, period, value, n FROM dq_ctl.monitor_metrics WHERE relation=%s AND metric=%s AND key=%s "
                "AND (batch_id LIKE 'HISTORY:%%' OR batch_id LIKE 'PERIOD:%%') ORDER BY period, id",
                (relation, metric, key)).fetchall()]
            b = self._pinned_baseline(c, self._series_id(relation, metric, "" if metric.startswith("mix:") else key))
        return {"points": pts, "baseline": b}

    # -- Part E §3.3: parallel run (legacy vs new) -----------------------------------------------------------------
    def _new_side(self, legacy: dict) -> dict:
        """New-side values for the legacy rows' (relation, measure): records and amount sums from the GATED relation
        (aggregates computed in the data plane) over the period's batches (the monitor's record of the period when
        it has one, else every batch), and rule_failed counts from the latest results."""
        from dqagent.agent import monitor as mon
        from dqagent.adapters.metadata import legacy_totals as lt
        wanted: dict = {}
        for r in legacy["rows"]:
            fam, _, name = r["measure"].partition(":")
            spec = wanted.setdefault(r["relation"], {"rows": False, "amount": [], "rule_failed": []})
            if fam == "records":
                spec["rows"] = True
            elif fam == "amount":
                spec["amount"].append(name)
            else:
                spec["rule_failed"].append(name)
        metric_rows, rule_failed = [], {}
        with self.cp.as_role("dq_monitor_svc") as c:
            for rel, spec in sorted(wanted.items()):
                batches = self._period_batches(c, legacy["period"], rel) or None
                metric_rows += mon.query_aggregates(self.dp.cur(), rel,
                                                    {"rows": True, "amount": sorted(set(spec["amount"]))},
                                                    {}, period=legacy["period"], batch_ids=batches)
                for rid in spec["rule_failed"]:
                    rule_failed[f"{rel}|{rid}"] = int(c.execute(
                        "SELECT coalesce(sum(l.failed), 0) n FROM (SELECT DISTINCT ON (r.unit_key) r.failed FROM "
                        "dq_ctl.results r JOIN dq_ctl.units u USING (unit_key) WHERE r.rule_id=%s AND u.node=%s "
                        "ORDER BY r.unit_key, r.eval_version DESC, r.id DESC) l", (rid, rel)).fetchone()["n"])
        return lt.new_side_from_metrics(metric_rows, rule_failed)

    @_serialized
    def load_legacy_totals(self, path, period: str | None = None, as_of: str | None = None) -> list[dict]:
        """Parallel run (Part E §3.3): load a legacy_totals file, compare it with the new side for the SAME period and
        as_of (tolerances from PROFILE parallel_run.tolerance), and store the rows in dq_ctl.parallel_run_results
        (append-only; loading the same file twice is a no-op). Go/no-go evidence only: never holds a unit. A BREAK
        needs a person's explain code (api). Returns the stored rows (with row_id)."""
        import hashlib as _hl
        from dqagent.adapters.metadata import legacy_totals as lt
        doc = lt.load(path)
        period = period or doc["period"]
        as_of = str(as_of or doc["as_of"])
        tol = ((self.profile or {}).get("parallel_run") or {}).get("tolerance")
        run_id = f"{doc['system']}:{_hl.sha256(json.dumps(doc, sort_keys=True).encode()).hexdigest()[:12]}"
        rows = lt.compare(doc, self._new_side(doc), period=period, as_of=as_of, tolerance=tol, run_id=run_id)
        out = []
        with self.cp.as_role("dq_seal_svc") as c:
            for r in rows:
                got = c.execute(
                    "INSERT INTO dq_ctl.parallel_run_results(run_id,system,period,as_of,relation,measure,legacy,new,delta,"
                    "tolerance,status,explain_code,authority) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NULL,%s) "
                    "ON CONFLICT (run_id, period, as_of, relation, measure) DO NOTHING RETURNING row_id",
                    (run_id, doc["system"], period, as_of, r["relation"], r["measure"], r["legacy"], r["new"],
                     r["delta"], r["tolerance"], r["status"], self._relation_authority(r["relation"]))).fetchone()
                if got is None:
                    got = c.execute("SELECT row_id FROM dq_ctl.parallel_run_results WHERE run_id=%s AND period=%s AND "
                                    "as_of=%s AND relation=%s AND measure=%s",
                                    (run_id, period, as_of, r["relation"], r["measure"])).fetchone()
                out.append({**r, "row_id": got["row_id"], "system": doc["system"]})
        return out

    # -- Part F F-02: routing driven by the EFFECTIVE FEEDS configuration ------------------------------------------
    @property
    def routing(self) -> Routing:
        """The effective FEEDS routing (governed). A FEEDS without routing fails closed (no file-name fallback)."""
        return Routing.of(self.feeds)

    def _file_plan(self, data, p: Path):
        """The routing Plan (pipeline, batch B-<stem>, intake files, relations, route) of one inbound file, or None
        when no route matches it (UNROUTED)."""
        return self.routing.plan(data, p)

    def file_authorities(self, name: str) -> list[str] | None:
        """E-04: the authorities a routed file's data belongs to (its route's relations' contract authorities; a child
        relation follows its root). None when the file is unrouted."""
        hit = self.routing.match(name)
        if hit is None:
            return None
        out: set = set()
        for rel in self.routing.routes[hit[0]]["relations"]:
            ct = self.contracts.get(rel) or {}
            root = (ct.get("unit_from_parent") or {}).get("relation")
            ct = self.contracts.get(root) or ct
            out |= {a for v in (ct.get("authorities_by_program") or {}).values() for a in v}
        return sorted(out)

    def _open_unrouted(self, p: Path) -> int:
        from dqagent.engine.intake import FEED_AUTHORITY
        with self.cp.as_role("dq_seal_svc") as c:
            return c.execute("SELECT dq_ctl.feed_item_open(%s,%s,'UNROUTED_FILE',%s,%s,%s,NULL) AS i",
                             (self.state, UNROUTED_FEED, self.routing.batch_id(p),
                              hashlib.sha256(p.name.encode()).hexdigest(), FEED_AUTHORITY)).fetchone()["i"]

    def ingest_file(self, data, p: Path) -> dict:
        """One inbound file as its own batch (B-<stem>), routed by the effective FEEDS routing. An UNROUTED file is
        never ingested: it opens one UNROUTED_FILE feed item (idempotent) for the intake duty."""
        plan = self._file_plan(data, p)
        if plan is None:
            item = self._open_unrouted(p)
            return {"routed": False, "batch_id": self.routing.batch_id(p), "feed_items": [item],
                    "summary": {"routed": False, "feed_item": item}}
        return self.run_pipeline(plan.pipeline, plan.batch, plan.files, plan.relations)

    def _abc_missing(self, batch_id: str) -> bool:
        with self.cp.as_role("dq_seal_svc") as c:
            return abc_missing(c, batch_id)

    def _ingest_state(self, batch_id: str) -> str:
        """E6: NEW (nothing of the batch is in the control plane), SEALED (ingested and sealed: never ingest it again)
        or PARTIAL (the file was registered or the batch opened, but it was never sealed; or, Part F F-01, it was
        sealed but its LANDING->GATED ABC was never recorded: recovery is needed, see recover_batch_abc)."""
        with self.cp.as_role("dq_seal_svc") as c:
            b = c.execute("SELECT sealed FROM dq_ctl.batches WHERE batch_id=%s", (batch_id,)).fetchone()
            f = c.execute("SELECT 1 FROM dq_ctl.files WHERE batch_id=%s LIMIT 1", (batch_id,)).fetchone()
            missing = bool(b and b["sealed"]) and abc_missing(c, batch_id)
        if b and b["sealed"]:
            return "PARTIAL" if missing else "SEALED"
        return "PARTIAL" if (b or f) else "NEW"

    def _post_missing_seats(self, batch_id: str) -> list[str]:
        """F-01 step 6: run the INTAKE, EVALUATOR and TRIAGE seats for a batch only where the seat has no blackboard
        row for it yet (a crash may have skipped them). Never before genesis (the agent is not registered)."""
        if not self.ratified:
            return []
        with self.cp.as_role("dq_eval_svc") as c:
            have = {r["seat"] for r in c.execute("SELECT DISTINCT seat FROM dq_ctl.agent_blackboard WHERE batch_id=%s",
                                                 (batch_id,)).fetchall()}
        ran = []
        for seat, fn in (("INTAKE", self.seats.after_intake), ("EVALUATOR", self.seats.after_evaluation),
                         ("TRIAGE", self.seats.triage)):
            if seat not in have:
                fn(batch_id)
                ran.append(seat)
        return ran

    def _resume_file(self, data, p: Path) -> dict:
        """E6: a file of an interrupted night that is already SEALED (with its ABC) is not ingested again. What a crash
        after the seal could have skipped is completed from stored rows: the MONITOR seat's batch metrics (if
        missing), the evaluation of its still-SEALED units, then the seats that have not posted for it (F-01 step 6).
        An unrouted file was never ingested: nothing to resume (its feed item is idempotent)."""
        plan = self._file_plan(data, p)
        if plan is None:
            return {"resumed": True, "routed": False, "batch_id": self.routing.batch_id(p),
                    "summary": {"resumed": True}}
        batch, rels = plan.batch, plan.relations
        with self.cp.as_role("dq_monitor_svc") as c:
            have = c.execute("SELECT 1 FROM dq_ctl.monitor_metrics WHERE batch_id=%s LIMIT 1", (batch,)).fetchone()
        monitored = 0 if have else self.monitor_record(batch, rels)
        self.reevaluate_pending()
        seats = self._post_missing_seats(batch)
        return {"resumed": True, "batch_id": batch, "pipeline": plan.pipeline, "monitor_rows": monitored,
                "seats": seats, "summary": {"resumed": True}}

    # -- Part F F-01 / R4-1: recovery of a batch sealed without its ABC -------------------------------------------
    def _lost_loads(self, batch_id: str) -> dict[str, list[str]]:
        """R4-1 precondition, with integrity (red team C1): {relation: FROZEN load ids that are PROVABLY not intact}
        (empty = every frozen load intact). For each frozen load the keyed digest over ALL landing columns of THIS
        batch's rows (Sealer._digest with Sealer.batch_where, the exact computation of the seal) is recomputed and
        compared with the landing_digest stored at freeze time. Proven loss: the landing table is gone, the load has
        no rows of this batch, or the digest differs (half-written, re-tagged, altered). An empty or fresh data plane
        is never read as balanced. Any OTHER data-plane error is not proof of loss: RecoveryTransient (retry; no feed
        item, the batch stays held)."""
        import duckdb
        with self.cp.as_role("dq_seal_svc") as c:
            rows = c.execute("SELECT relation, load_id, landing_digest FROM dq_ctl.loads_events WHERE batch_id=%s "
                             "AND event='FROZEN' ORDER BY relation, load_id", (batch_id,)).fetchall()
        lost: dict[str, list[str]] = {}
        where = self.sealer.batch_where(batch_id)
        for r in rows:
            tbl = r["relation"].split(".")[1]
            try:
                cols = self.dp.columns(f"landing.{tbl}")
                present = self.dp.fetchall(f"SELECT count(*) FROM landing.{tbl} WHERE _dq_load_id = ? {where}",
                                           [r["load_id"]])[0][0]
                same = bool(present) and bool(cols) and self.sealer._digest(
                    self.dp.cur(), f"landing.{tbl}", cols, r["load_id"], where) == r["landing_digest"]
            except duckdb.CatalogException:                # the landing table does not exist: the load is gone
                same = False
            except duckdb.Error as e:                      # transient / unexpected: not proof of loss
                raise RecoveryTransient(f"batch {batch_id}: the data plane could not be read ({type(e).__name__}); "
                                        "retry the recovery") from e
            if not same:
                lost.setdefault(r["relation"], []).append(r["load_id"])
        return lost

    @_serialized
    def recover_batch_abc(self, batch_id: str, person_id: str, reason: str | None = None) -> dict:
        """Traced under a RECOVER root RCV-<batch_id>-<n> (see _recover_batch_abc)."""
        tid = tracer.mint(self.cp, f"RCV-{batch_id}") if tracer.enabled(self.deployment) else None
        with tracer.root("RECOVER", tid, parent_trace_id=None, person_id=person_id, **self._root_args()):
            return self._recover_batch_abc(batch_id, person_id, reason)

    def _recover_batch_abc(self, batch_id: str, person_id: str, reason: str | None = None) -> dict:
        """Recover a batch that was SEALED but whose LANDING->GATED ABC was never recorded (F-01). The ABC is
        recomputed by landing_to_gated_abc, deterministic over the FROZEN load set (nothing is re-parsed), then the
        monitor metrics (if missing), the evaluation and the seats that have not posted run as after an ordinary
        ingest. Refused (RecoveryRefused) when the batch is unknown, not sealed or its ABC is complete; refused with
        RecoveryDataLost (a RECOVERY_DATA_LOST feed item, the batch stays held) when any frozen load is no longer
        in the data plane (R4-1). Returns {batch_id, abc_rows, evaluated_units, late_loads}."""
        from dqagent.engine.intake import FEED_AUTHORITY
        with self.cp.as_role("dq_seal_svc") as c:
            b = c.execute("SELECT node, sealed FROM dq_ctl.batches WHERE batch_id=%s", (batch_id,)).fetchone()
            if b is None or not b["sealed"]:
                raise RecoveryRefused(f"batch {batch_id} is not a sealed batch")
            if not abc_missing(c, batch_id):
                raise RecoveryRefused(f"batch {batch_id} has its ABC: nothing to recover")
        relations = b["node"].split(",")
        lost = self._lost_loads(batch_id)
        if lost:
            digest = hashlib.sha256(json.dumps({"batch": batch_id, "lost": lost}, sort_keys=True).encode()).hexdigest()
            with self.cp.as_role("dq_seal_svc") as c:
                item = c.execute("SELECT dq_ctl.feed_item_open(%s,'RECOVERY','RECOVERY_DATA_LOST',%s,NULL,%s,NULL) AS i",
                                 (self.state, batch_id, FEED_AUTHORITY)).fetchone()["i"]
                c.execute("SELECT dq_ctl.append_audit('RECOVERY_REFUSED', %s)", (digest,))
            # N-T0-1: ONE guardrail event per refusal, written now through the one writer (fail-closed code),
            # correlated with the RECOVER root; engine-wide (no authorities): audit / operations only
            from dqagent.obs.explain_service import record_guardrail
            tid, sid = tracer.current()
            record_guardrail(self.cp, "RECOVERY_REFUSED", f"batch:{batch_id}", seat="recovery",
                             detail={"loads_lost": sum(len(v) for v in lost.values())}, trace_id=tid, span_id=sid)
            e = RecoveryDataLost(f"batch {batch_id}: {sum(len(v) for v in lost.values())} frozen load(s) are no "
                                 "longer in the data plane; its ABC cannot be recomputed, the batch stays held")
            e.feed_item, e.lost = item, lost
            raise e
        lg, late = landing_to_gated_abc(self.cp, self.dp, batch_id, relations)
        try:
            abc = record_abc(self.cp, batch_id, lg)
        except psycopg.errors.UniqueViolation as e:      # another process recorded it first (abc_landing_gated_once)
            raise RecoveryRefused(f"batch {batch_id} has its ABC: recorded concurrently") from e
        with self.cp.as_role("dq_monitor_svc") as c:
            have = c.execute("SELECT 1 FROM dq_ctl.monitor_metrics WHERE batch_id=%s LIMIT 1", (batch_id,)).fetchone()
        if not have:
            self.monitor_record(batch_id, relations)
        self.reevaluate_pending()
        self._post_missing_seats(batch_id)
        with self.cp.as_role("dq_seal_svc") as c:
            evaluated = c.execute("SELECT count(*) n FROM dq_ctl.units WHERE batch_id=%s AND state <> 'SEALED'",
                                  (batch_id,)).fetchone()["n"]
            c.execute("SELECT dq_ctl.append_audit('RECOVERY_ABC', %s)",
                      (hashlib.sha256(f"{batch_id}|{person_id}|{len(abc)}|{reason or ''}".encode()).hexdigest(),))
        return {"batch_id": batch_id, "abc_rows": len(abc), "evaluated_units": evaluated, "late_loads": late}

    def _night_events(self, night_date: str) -> list[dict]:
        with self.cp.as_role("dq_seal_svc") as c:
            return [dict(r) for r in c.execute("SELECT * FROM dq_ctl.sim_night_events WHERE night_date=%s ORDER BY id",
                                               (night_date,)).fetchall()]

    def _night_event(self, night_date: str, event: str, person_id: str | None, *, error_class: str | None = None,
                     detail: dict | None = None):
        with self.cp.as_role("dq_seal_svc") as c:
            c.execute("INSERT INTO dq_ctl.sim_night_events(run_id,night_date,event,person_id,error_class,detail) "
                      "VALUES (%s,%s,%s,%s,%s,%s::jsonb)",
                      (self.sim_run_id(night_date), night_date, event, person_id, error_class,
                       json.dumps(detail or {}, sort_keys=True, default=str)))

    def _delivered_by_feed(self) -> dict[str, dt.datetime]:
        """{feed: last BUSINESS delivery date} from the registered inbound files' batch names (B-<stem with YYYYMMDD>):
        the simulated clock, in place of received_at (which is the wall clock of the load)."""
        from dqagent.engine.intake import feed_of
        out: dict[str, dt.datetime] = {}
        with self.cp.as_role("dq_seal_svc") as c:
            for r in c.execute("SELECT DISTINCT format, batch_id FROM dq_ctl.files WHERE direction <> 'OUTBOUND'"
                               ).fetchall():
                d = file_date(r["batch_id"])
                if d:
                    t = dt.datetime(int(d[:4]), int(d[4:6]), int(d[6:]), tzinfo=dt.timezone.utc)
                    f = feed_of(r["format"])
                    out[f] = max(out.get(f, t), t)
        return out

    def _night_marks(self) -> dict:
        """High-water marks of the append-only rows a night's summary counts (read as dq_publish_svc, read-only)."""
        with self.cp.as_role("dq_publish_svc") as c:
            q = lambda sql: c.execute(sql).fetchone()["m"] or 0          # noqa: E731
            return {"event": q("SELECT max(event_id) m FROM dq_ctl.unit_events"),
                    "feed": q("SELECT max(item_id) m FROM dq_ctl.feed_items"),
                    "bb": q("SELECT max(seq) m FROM dq_ctl.agent_blackboard"),
                    "window": q("SELECT max(window_id) m FROM dq_ctl.veto_windows"),
                    "demotions": sorted(r["cr_id"] for r in c.execute(
                        "SELECT cr_id FROM dq_ctl.config_change_requests WHERE origin='DEMOTION'").fetchall())}

    @staticmethod
    def _held_reason(via: str, payload: dict) -> str:
        """The short WHY of a HELD transition: the ceiling that bound it, else the first trace step that set the final
        mode (policy / floor / escalation), else the mode; a veto says so."""
        if via == "VETO":
            return "vetoed by a person"
        if payload.get("ceiling"):
            return str(payload["ceiling"])
        m = payload.get("mode")
        for t in payload.get("mode_trace") or []:
            if m and (t.endswith(f"= {m}") or t.endswith(f"-> {m}")):
                return t
        return str(m or via)

    def _night_summary(self, night_date: str, files: list[Path], marks: dict) -> dict:
        """§10b run summary: every count with the typed ids it counts. Units: unit:<key>; feed items: feed:<id>;
        board posts: bb:<seq>; change requests: cr:<id>; ABC rows: abc:<id>; files: file:<name>.
        E5: the summary also stores, for EVERY id, the authorities it belongs to (`scope_index`), the held reason of
        each held unit, and the unit totals per authority set, so the api assembles each caller's summary from the
        rows inside that caller's scope only (scope_summary) - never by filtering totals summed across authorities.
        Gating vs advisory ABC breaks come from the ONE definition (dq_ctl.abc_gating_failures, I1)."""
        bid = {p.name: self.routing.batch_id(p) for p in files}
        batches = [bid[p.name] for p in files]
        with self.cp.as_role("dq_publish_svc") as c:
            abc = c.execute("SELECT id, authority, batch_id FROM dq_ctl.abc WHERE batch_id = ANY(%s) "
                            "AND disposition <> 'PASS' ORDER BY id", (batches,)).fetchall()
            gating = {r["id"] for r in c.execute("SELECT id FROM dq_ctl.abc_gating_failures WHERE batch_id = ANY(%s)",
                                                  (batches,)).fetchall()}
            created = [r["unit_key"] for r in c.execute(
                "SELECT unit_key FROM dq_ctl.units WHERE batch_id = ANY(%s) ORDER BY unit_key", (batches,)).fetchall()]
            released = [r["unit_key"] for r in c.execute(
                "SELECT DISTINCT unit_key FROM dq_ctl.unit_events WHERE event_id > %s AND to_state='RELEASED' "
                "AND from_state='EVALUATING' ORDER BY unit_key", (marks["event"],)).fetchall()]
            windows = [r["unit_key"] for r in c.execute(
                "SELECT DISTINCT unit_key FROM dq_ctl.veto_windows WHERE window_id > %s ORDER BY unit_key",
                (marks["window"],)).fetchall()]
            held_rows = c.execute(
                "SELECT DISTINCT ON (e.unit_key) e.unit_key, e.via, e.payload FROM dq_ctl.unit_events e "
                "JOIN dq_ctl.units u USING (unit_key) WHERE e.event_id > %s AND e.to_state='HELD' AND u.state='HELD' "
                "ORDER BY e.unit_key, e.event_id DESC", (marks["event"],)).fetchall()
            pending = [r["unit_key"] for r in c.execute(
                "SELECT unit_key FROM dq_ctl.units WHERE state='PENDING_DEPENDENCY' ORDER BY unit_key").fetchall()]
            feed = c.execute("SELECT item_id, authority FROM dq_ctl.feed_items WHERE item_id > %s ORDER BY item_id",
                             (marks["feed"],)).fetchall()
            signals = c.execute(
                "SELECT seq, authority FROM dq_ctl.agent_blackboard WHERE seq > %s AND seat='MONITOR' "
                "AND template IN ('SPC_SIGNAL','MIX_SHIFT') ORDER BY seq", (marks["bb"],)).fetchall()
            dem = c.execute(
                "SELECT cr_id, impact #>> '{demotion,authority}' AS authority FROM dq_ctl.config_change_requests "
                "WHERE origin='DEMOTION' AND NOT (cr_id = ANY(%s)) ORDER BY cr_id", (marks["demotions"],)).fetchall()
            totals_by = c.execute("SELECT authorities, state, count(*) n FROM dq_ctl.units GROUP BY 1, 2 "
                                  "ORDER BY 1, 2").fetchall()
            unit_auths = {r["unit_key"]: sorted(r["authorities"]) for r in c.execute(
                "SELECT unit_key, authorities FROM dq_ctl.units WHERE unit_key = ANY(%s)",
                (sorted(set(created) | set(released) | set(windows) | set(pending) |
                        {r["unit_key"] for r in held_rows}),)).fetchall()}
            batch_auths: dict = {}
            for r in c.execute("SELECT batch_id, authorities FROM dq_ctl.units WHERE batch_id = ANY(%s)",
                               (batches,)).fetchall():
                batch_auths.setdefault(r["batch_id"], set()).update(r["authorities"])
            # F-02: an unrouted file has no units or ABC; it is visible through its feed item's (intake) authority
            for r in c.execute("SELECT batch_id, authority FROM dq_ctl.feed_items WHERE kind='UNROUTED_FILE' "
                               "AND batch_id = ANY(%s)", (batches,)).fetchall():
                batch_auths.setdefault(r["batch_id"], set()).add(r["authority"])
        for r in abc:
            batch_auths.setdefault(r["batch_id"], set()).add(r["authority"])
        totals: dict = {}
        for r in totals_by:
            totals[r["state"]] = totals.get(r["state"], 0) + r["n"]
        reasons: dict[str, list[str]] = {}
        reason_of: dict[str, str] = {}
        for r in held_rows:
            why = self._held_reason(r["via"], r["payload"] or {})
            reasons.setdefault(why, []).append(r["unit_key"])
            reason_of[r["unit_key"]] = why
        unit = lambda ks: [f"unit:{k}" for k in ks]                       # noqa: E731
        held = sorted(reason_of)
        index: dict[str, dict] = {f"unit:{k}": {"a": v} for k, v in unit_auths.items()}
        index.update({f"abc:{r['id']}": {"a": [r["authority"]], "batch": r["batch_id"], "gating": r["id"] in gating}
                      for r in abc})
        index.update({f"feed:{r['item_id']}": {"a": [r["authority"]]} for r in feed})
        index.update({f"bb:{r['seq']}": {"a": [r["authority"]]} for r in signals})
        index.update({f"cr:{r['cr_id']}": {"a": [r["authority"]] if r["authority"] else []} for r in dem})
        index.update({f"file:{p.name}": {"a": sorted(batch_auths.get(bid[p.name], set())),
                                         "batch": bid[p.name]} for p in files})
        counts = {
            "files": {"n": len(files), "ids": [f"file:{p.name}" for p in files]},
            "abc_breaks": {"n": len(abc), "ids": [f"abc:{r['id']}" for r in abc],
                           "gating": sum(1 for r in abc if r["id"] in gating),
                           "advisory": sum(1 for r in abc if r["id"] not in gating)},
            "units_created": {"n": len(created), "ids": unit(created)},
            "released_autonomously": {"n": len(released), "ids": unit(released)},
            "veto_windows_opened": {"n": len(windows), "ids": unit(windows)},
            "held": {"n": len(held), "ids": unit(held),
                     "mode_reasons": {k: len(v) for k, v in sorted(reasons.items())}},
            "pending_dependency": {"n": len(pending), "ids": unit(pending)},
            "feed_items": {"n": len(feed), "ids": [f"feed:{r['item_id']}" for r in feed]},
            "monitor_signals": {"n": len(signals), "ids": [f"bb:{r['seq']}" for r in signals]},
            "demotions": {"n": len(dem), "ids": [f"cr:{r['cr_id']}" for r in dem]},
        }
        return {"run_id": self.sim_run_id(night_date), "night_date": night_date, "simulated": True,
                "files": [p.name for p in files], "counts": counts, "totals": totals,
                "scope_index": index, "held_reason_of": reason_of,
                "totals_by_scope": [{"authorities": sorted(r["authorities"]), "state": r["state"], "n": r["n"]}
                                    for r in totals_by]}

    # E5: what a caller may see of a stored summary --------------------------------------------------------------
    INTERNAL_SUMMARY_KEYS = ("scope_index", "held_reason_of", "totals_by_scope")

    @staticmethod
    def scope_summary(summary: dict, scope, intake: bool = False) -> dict:
        """The run summary as ONE caller may see it, rebuilt from the per-id authority index (E5): every count, id
        list, gating/advisory split, held mode_reasons and the unit totals use only rows inside the caller's scope.
          unit           every authority of the unit in scope (as unit_in_scope / RLS)
          abc row        its authority in scope; a pre-split (AUTH_INTAKE) row when the caller holds the intake duty
                         or sees a unit of its batch (as dq_ctl.intake_verdicts)
          feed item, board post   its authority in scope, AUTH_INTAKE with the intake duty (L1, as /feeds/items)
          demotion       its authority in scope
          file           any authority of its batch's units / ABC rows that the caller may see
        The internal index never leaves; the caller's explanation must be rebuilt from these counts."""
        vis = set(scope or [])
        intake_auth = "AUTH_INTAKE"
        idx = summary.get("scope_index") or {}
        unit_ok = {i for i, v in idx.items() if i.startswith("unit:") and v["a"] and set(v["a"]) <= vis}
        visible_unit_batch: set = set()
        # a batch's units are visible when any created unit of it is (the units_created ids carry the batch key)
        for i in summary["counts"]["units_created"]["ids"]:
            if i in unit_ok:
                visible_unit_batch.add(i[5:].split(":", 1)[0])

        def single_ok(a: list) -> bool:
            return bool(a) and (a[0] in vis or (a[0] == intake_auth and intake))

        def ok(i: str) -> bool:
            v = idx.get(i)
            if v is None:
                return False                                  # fail closed: an id with no recorded authority
            kind = i.split(":", 1)[0]
            if kind == "unit":
                return i in unit_ok
            if kind == "abc":
                if v["a"] and v["a"][0] == intake_auth:
                    return intake or v.get("batch") in visible_unit_batch
                return single_ok(v["a"])
            if kind == "file":
                return bool(set(v["a"]) & vis) or (intake and intake_auth in v["a"]) or \
                    v.get("batch") in visible_unit_batch
            return single_ok(v["a"])

        out = {k: v for k, v in summary.items() if k not in Runtime.INTERNAL_SUMMARY_KEYS}
        counts = {}
        for k, cnt in summary["counts"].items():
            ids = [i for i in cnt["ids"] if ok(i)]
            c = {"n": len(ids), "ids": ids}
            if k == "abc_breaks":
                c["gating"] = sum(1 for i in ids if idx[i].get("gating"))
                c["advisory"] = len(ids) - c["gating"]
            if k == "held":
                why: dict = {}
                for i in ids:
                    r = (summary.get("held_reason_of") or {}).get(i[5:])
                    if r is not None:
                        why[r] = why.get(r, 0) + 1
                c["mode_reasons"] = dict(sorted(why.items()))
            counts[k] = c
        out["counts"] = counts
        totals: dict = {}
        for t in summary.get("totals_by_scope") or []:
            if t["authorities"] and set(t["authorities"]) <= vis:
                totals[t["state"]] = totals.get(t["state"], 0) + t["n"]
        out["totals"] = totals
        out["files"] = [i[5:] for i in counts["files"]["ids"]]
        out.pop("explanation", None)                          # the caller's explanation is rebuilt from these counts
        out["ids_scoped"] = True
        return out

    @staticmethod
    def sim_run_id(night_date: str) -> str:
        return "SIM-" + night_date.replace("-", "")

    @staticmethod
    def nightly_facts(summary: dict) -> dict:
        """The explainer facts of a run summary (counts only; deterministic from the stored summary)."""
        k = summary["counts"]
        return {"run_id": summary["run_id"], "night_date": summary["night_date"], "files": k["files"]["n"],
                "abc_gating": k["abc_breaks"]["gating"], "abc_advisory": k["abc_breaks"]["advisory"],
                "created_units": k["units_created"]["n"], "released_units": k["released_autonomously"]["n"],
                "veto_windows": k["veto_windows_opened"]["n"], "held_units": k["held"]["n"],
                "held_reasons": [{"reason": r, "units": n} for r, n in sorted(k["held"]["mode_reasons"].items())],
                "pending_units": k["pending_dependency"]["n"], "feed_items": k["feed_items"]["n"],
                "monitor_signals": k["monitor_signals"]["n"], "demotions": k["demotions"]["n"]}

    def _night_profile_digests(self, data, files: list[Path]) -> dict:
        """P1-2/D6: {"digests": {pipeline_id: profile_digest}, "attempted": int, "degraded_pipelines": [...],
        "route_degraded": bool} for every pipeline this night's files route to, computed by the ONE definition
        (compose_profile, dqagent.governance.profile) the profile route itself uses - the central correctness
        property is that both sites call the same function over the same effective configuration (K1_DESIGN.md §4
        step 4). An unrouted file names no pipeline (skipped, not an error).
        I-8: tracing only observes - this is itself a trace-time computation (not an engine input anywhere), so it
        must never block or change the night's outcome. Any failure (a harness entry missing mid-governance-reload,
        a transient CP read) degrades to an empty map for the pipelines it could not resolve, exactly like every
        other tracer-side read in this file (tracer.count_runs/last_run's own C1 degrade-and-continue contract).
        Red-team fix (K1, round 1): "this night touched zero pipelines" and "this night touched pipelines but every
        compose_profile call failed" both used to collapse to the same empty dict, indistinguishable from the
        caller's side. `attempted` (how many pipeline ids were resolved from routing), `degraded_pipelines` (which
        of those failed to compose) and `route_degraded` (the routing step itself failed, before any pipeline id was
        even known) together make every case distinguishable from the stored row alone: attempted=0/degraded=0/
        route_degraded=false is a genuine "touched nothing"; anything else with an empty `digests` is a degrade.
        `digests` is unchanged in shape - it is still exactly what the caller stamps into runs.profile_digests."""
        from dqagent.governance.profile import compose_profile

        try:
            pipeline_ids = sorted({plan.pipeline for plan in (self._file_plan(data, p) for p in files) if plan})
        except Exception as e:  # noqa: BLE001 - observe only (I-8): a routing error here must not block the night
            tracer._degrade("night_profile_digests:route", e)
            return {"digests": {}, "attempted": 0, "degraded_pipelines": [], "route_degraded": True}
        out, degraded = {}, []
        for pid in pipeline_ids:
            try:
                out[pid] = compose_profile(self.registry, self.state, pid)["profile_digest"]
            except Exception as e:  # noqa: BLE001 - observe only (I-8); KeyError (unknown pipeline) included
                tracer._degrade("night_profile_digests:compose", e)
                degraded.append(pid)
                continue
        return {"digests": out, "attempted": len(pipeline_ids), "degraded_pipelines": degraded,
                "route_degraded": False}

    def run_night(self, data, night_date: str, files: list[Path], *, last: bool, parte: bool = False,
                  drain_passes: int = 3, record: bool = False, person_id: str | None = None) -> dict:
        """THE night-runner (Part E §10b), shared by the full load and the simulator. In order:
          1-3 each file that arrived that night is ingested and sealed (monitor metrics recorded after seal), then
              evaluated, and the publish cycle runs (autonomous releases, veto windows) — one batch per file;
          4   missing deliveries are checked against the SIMULATED clock (the end of night_date);
          5   the monitoring period is closed when the night crosses its period boundary or is the last night (the
              MONITOR seat files every batch under its current period, so for a one-period run: the last night);
          6   units waiting on a dependency are re-evaluated (worker-cycle drain passes: all of them on the last
              night, otherwise while publisher candidates remain);
              (last night, Part E extras: the parallel run against legacy_totals.json)
          7   the run summary is built (counts with ids, and the mandatory explanation, kind nightly_run).
        record (the simulator): a STARTED row (with the summary marks) is written before any file is ingested, a
        failure is recorded as FAILED with its error class, and a night that was STARTED before RESUMES: a file
        already sealed is never ingested again, and a file that was only partly ingested stops the night with
        SimRecoveryNeeded (409) instead of loading it twice (E6).
        Returns {summary, file_results: {file name: run_pipeline result}, parte: {monitor, parallel_run} | None}."""
        data = Path(data)
        prior = self._night_events(night_date) if record else []
        started = next((e for e in prior if e["event"] == "STARTED"), None)
        # a resumed night counts from the marks of its FIRST start, so nothing done before the failure is lost
        marks = dict(started["detail"]["marks"]) if started else self._night_marks()
        if record:                                            # E6: written BEFORE any file is ingested
            self._night_event(night_date, "RESUMED" if started else "STARTED", person_id,
                              detail={"marks": marks, "files": [p.name for p in files]})
        try:
            # Part F P1-1: the NIGHT root NIGHT-<state>-<yyyymmdd>-a<n>-<seq> (n = 1 + attempts already recorded; the
            # sequence makes it unique across processes, I4); a resumed night's root is parented to the previous
            # attempt. C1: an unreachable trace store runs the night untraced.
            prior = tracer.count_runs(self.cp, "NIGHT", night_date=night_date) \
                if tracer.enabled(self.deployment) else None
            tid = parent = None
            if prior is not None:
                tid = tracer.mint(self.cp, f"NIGHT-{self.state}-{night_date.replace('-', '')}-a{prior + 1}")
                parent = tracer.last_run(self.cp, "NIGHT", night_date=night_date) if prior else None
            # Part F P1-2 (D6, K1_DESIGN.md): the NIGHT root ALONE is stamped with the governance profile digests of
            # the pipelines this night touches ("this night ran under this exact configuration") - explicit kwarg at
            # this one call site only, never inside _root_args() (shared by APPLY_CHANGE/PUBLISH_CYCLE/RECOVER too,
            # none of which are about one night's pipeline run).
            profile = self._night_profile_digests(data, files)
            with tracer.root("NIGHT", tid, parent_trace_id=parent, night_date=night_date, person_id=person_id,
                             attempt=(prior or 0) + 1, profile_digests=profile["digests"], **self._root_args()):
                self._record_profile_digest_degradation(profile)
                return self._run_night(data, night_date, files, marks, last=last, parte=parte,
                                       drain_passes=drain_passes)
        except Exception as e:
            if record:
                self._night_event(night_date, "FAILED", person_id, error_class=type(e).__name__,
                                  detail={"message": str(e).splitlines()[0][:300] if str(e) else ""})
            raise

    def _record_profile_digest_degradation(self, profile: dict) -> None:
        """Red-team fix (K1, round 1): make "touched pipelines but every digest computation failed" visible and
        QUERYABLE (dq_ctl.spans, readable by the Tower / a presenter through obs_routes), distinct from "this night
        genuinely touched zero pipelines" - which `tracer._degrade`'s in-process STATS counter alone cannot do
        (nothing in dqagent/api/ reads it). Only numbers and booleans here (no new ATTR_VOCAB string needed, I5).
        I-8: this is itself trace-only and fire-and-forget - a span-attribute failure here must never block or
        change the night's outcome, exactly like every other tracer call in this file."""
        if not (profile["route_degraded"] or profile["degraded_pipelines"]):
            return                                    # the common case: nothing degraded, nothing to record
        try:
            with tracer.span("profile_digests", seat="REGISTRY") as sp:
                sp.attr("route_degraded", bool(profile["route_degraded"]))
                sp.attr("attempted", profile["attempted"])
                sp.attr("degraded", len(profile["degraded_pipelines"]))
        except Exception as e:  # noqa: BLE001 - observe only (I-8): recording the degradation must not itself fail
            tracer._degrade("night_profile_digests:record", e)

    def _run_night(self, data: Path, night_date: str, files: list[Path], marks: dict, *, last: bool, parte: bool,
                   drain_passes: int) -> dict:
        file_results = {}
        for p in files:
            # I5: the span names the FEED, never the file name
            plan = self._file_plan(data, p)
            feed = "UNROUTED" if plan is None else feed_of(plan.files[0][0])
            with tracer.span("file", seat="INTAKE", subject=f"feed:{feed}") as sp:
                b = self.routing.batch_id(p)
                st = self._ingest_state(b)
                sp.attr("ingest_state", st)
                if st == "PARTIAL" and self._abc_missing(b):
                    raise SimRecoveryNeeded(f"night {night_date}: {p.name} was sealed but ABC was never recorded; "
                                            f"POST /api/v1/recovery/batches/{b}/abc")
                if st == "PARTIAL":
                    raise SimRecoveryNeeded(f"night {night_date}: {p.name} was partly ingested (registered but never "
                                            "sealed); it is not ingested again automatically - recover the batch first")
                # E6: a sealed file is never ingested twice; an interrupted night resumes after it
                file_results[p.name] = self._resume_file(data, p) if st == "SEALED" else self.ingest_file(data, p)
                sp.attr("routed", file_results[p.name].get("routed", True) is not False)
                self.verify_and_publish()
        d = dt.date.fromisoformat(night_date)
        end = dt.datetime(d.year, d.month, d.day, tzinfo=dt.timezone.utc) + dt.timedelta(days=1)
        with tracer.span("missing_deliveries", seat="INTAKE") as sp:
            sp.attr("opened", len(self.check_missing_deliveries(now=end, received=self._delivered_by_feed())))
        extras = None
        if parte:
            with tracer.span("monitor_close", seat="MONITOR"):
                extras = {"monitor": self.monitor_close_period() if (last and self.monitor_period) else [],
                          "parallel_run": []}
        for i in range(drain_passes):
            # last night: the full drain (as the full load always ended); other nights: only while the re-evaluation
            # left publisher candidates (each cycle re-attests every unit, so idle passes are skipped)
            if not last and not self._publish_candidates():
                break
            with tracer.span(f"drain[{i}]"):
                self.verify_and_publish()
        if parte and last and (data / "legacy_totals.json").exists():
            with tracer.span("parallel_run"):
                # E-11 (K6 Fix, confirmed sound by before-build red team round 1): the parallel run compares the
                # new side as of the SIMULATED clock this whole night runs under (night_date, per this method's own
                # docstring: "missing deliveries are checked against the SIMULATED clock"), never the legacy
                # fixture's own stated `as_of` - without this, a night run with --preload-nights at a count that
                # does not happen to match the fixture's own date would silently compare the wrong period.
                extras["parallel_run"] = self.load_legacy_totals(data / "legacy_totals.json", as_of=night_date)
        # Part F P1-3 (K2b, PFP :825-828): after the drain, each PIPELINE with run_on: night_end runs under a NEW
        # PIPELINE_RUN root (K1's D6 precedent: a brand-new root kind stamps whatever it needs as an explicit kwarg
        # at this ONE new call site, never through the shared _root_args() helper's default shape). Genesis's own
        # 2 PIPELINE items are both run_on: manual (registry.py's pipeline_task_gate_seed), so this fires only for
        # a PIPELINE wired later with run_on: night_end -- none at genesis, so I-1/I-2 are unaffected (PFP :828).
        with self.cp.as_role("dq_registry_svc") as c:
            night_pipelines = c.execute(
                "SELECT artifact_id FROM dq_ctl.config_effective WHERE state=%s AND kind='PIPELINE' AND op='PUT' "
                "AND content->>'run_on' = 'night_end' ORDER BY artifact_id", (self.state,)).fetchall()
        for p in night_pipelines:
            self._pipeline_run(p["artifact_id"].split(":pipeline:", 1)[1], night_date)
        with tracer.span("summary") as sp:
            summary = self._night_summary(night_date, files, marks)
            summary["explanation"] = self.explain_for("nightly_run", self.nightly_facts(summary),
                                                      subject=f"nightly:{night_date}")
            sp.attr("counts", {k: v["n"] for k, v in summary["counts"].items()})
        return {"summary": summary, "file_results": file_results, "parte": extras}

    def _pipeline_run(self, pipeline_id: str, night_date: str) -> None:
        """Part F P1-3 (K2b, PFP :825-828): the PIPELINE_RUN root for one PIPELINE with run_on: night_end, opened
        AFTER the drain (K1's D6 precedent: a brand-new root kind stamps whatever it needs -- here, `pipeline_id`
        and `night_date` -- as an explicit kwarg at this ONE new call site, never through the shared _root_args()
        default shape). Nested inside the enclosing NIGHT root (tracer.root()'s own default: parent_trace_id is
        the current root unless overridden, so this is NOT overridden here).

        For each of this pipeline's TASK_GATE items (named `taskgate:<pipeline_id>:<task>` per the K2a-2 naming
        convention), computes an advisory gate_verdict (harness.gate.gate_verdict) from the COMMITTED data-plane
        manifest state and writes a REAL dq_ctl.gate_checks row (dq_discovery_svc, the one writer). K2b-2
        (REVERSED, K2_DESIGN.md §9): no dbt build runs here -- the verdict/trace/row are all real; only the
        underlying dbt BUILD EXECUTION is deferred to K3."""
        from dqagent.harness.gate import gate_verdict
        tid = tracer.mint(self.cp, f"PLR-{self.state}-{pipeline_id}-{night_date.replace('-', '')}") \
            if tracer.enabled(self.deployment) else None
        with tracer.root("PIPELINE_RUN", tid, pipeline_id=pipeline_id, night_date=night_date, **self._root_args()):
            with self.cp.as_role("dq_registry_svc") as c:
                gates = c.execute(
                    "SELECT artifact_id, version, content FROM dq_ctl.config_effective WHERE state=%s "
                    "AND kind='TASK_GATE' AND op='PUT' AND artifact_id LIKE %s ORDER BY artifact_id",
                    (self.state, f"{self.state}:taskgate:{pipeline_id}:%")).fetchall()
            for g in gates:
                content = g["content"]
                task_id = content.get("task_id") or g["artifact_id"].rsplit(":", 1)[-1]
                datasets = content.get("datasets") or []
                with self.cp.as_role("dq_eval_svc") as c:
                    row = c.execute("SELECT batch_id FROM dq_ctl.units WHERE node = ANY(%s) "
                                    "ORDER BY created_at DESC LIMIT 1", (datasets,)).fetchone()
                run_key = row["batch_id"] if row else f"NONE-{night_date}"
                verdict = gate_verdict(self.dp, content, run_key)
                with tracer.span(f"gate:{task_id}", seat="REGISTRY",
                                 subject=f"taskgate:{pipeline_id}:{task_id}") as sp:
                    sp.attr("gate_verdict", verdict)
                with self.cp.as_role("dq_discovery_svc") as c:
                    c.execute("SELECT dq_ctl.gate_check_record(%s,%s,%s,%s,%s)",
                              (pipeline_id, task_id, run_key, g["version"], verdict))

    def _publish_candidates(self) -> bool:
        with self.cp.as_role("dq_publish_svc") as c:
            return bool(c.execute("SELECT 1 FROM dq_ctl.units WHERE state IN ('EVALUATING','PENDING_RELEASE',"
                                  "'RE_EVALUATE_PENDING','SEALED') LIMIT 1").fetchone())

    # simulator state ----------------------------------------------------------------------------------------------
    def sim_enable(self, data, *, parte: bool = True, allow_governed: bool = False,
                   person_id: str | None = None) -> None:
        """Demo --nightly: the simulator serves this synthetic run. With parte, the MONITOR seat's history is loaded
        once, before any night (as the full load does).
        Part F (--allow-sim, exactly): on a GOVERNED control plane the simulator runs only when `allow_governed` is
        passed AND the deployment is pilot; that permits the /sim endpoints, preloading synthetic nights and the
        lower-only --earn-story demotion, and records deployment_events(SIM_ENABLED_ON_GOVERNED, person). It permits
        nothing else (no test keys, no test genesis, no DQ_ALLOW_TEST_GENESIS, no scripted transport, no local IdP
        under production) and is refused under production."""
        if self.genesis_kind == "GOVERNED" or (self.genesis_kind is None and not self.test_mode):
            if not allow_governed:
                raise NotConfiguredError("the simulator on a governed control plane needs --allow-sim")
            if self.deployment != "pilot":
                raise NotConfiguredError(f"the simulator is refused in a {self.deployment} deployment")
            self.registry.deployment_event("SIM_ENABLED_ON_GOVERNED", person_id)
            self.sim_on_governed = True
        self.sim_data, self.sim_parte = Path(data), parte
        if parte:
            self.load_run_history(self.sim_data)

    def load_run_history(self, data) -> dict | None:
        """Part E §6: a synthetic run's monitor_history.json (if present) becomes the MONITOR seat's history."""
        h = Path(data) / "monitor_history.json"
        return self.load_monitor_history(h) if h.exists() else None

    @property
    def sim_enabled(self) -> bool:
        return self.sim_data is not None

    def _sim_rows(self) -> list[dict]:
        with self.cp.as_role("dq_seal_svc") as c:
            return [dict(r) for r in c.execute("SELECT * FROM dq_ctl.sim_runs ORDER BY night_date").fetchall()]

    def sim_nights(self) -> dict:
        """{current_date, nights: [{night_date, files[], status QUEUED | RUN, run_id}]}; current_date is the latest
        night run (None before any)."""
        if not self.sim_enabled:
            raise SimUnavailable("the nightly simulator runs only in demo mode (--nightly)")
        ran = {r["night_date"].isoformat(): r["run_id"] for r in self._sim_rows()}
        with self.cp.as_role("dq_seal_svc") as c:
            last_ev = {r["night_date"].isoformat(): r for r in c.execute(
                "SELECT DISTINCT ON (night_date) night_date, event, error_class FROM dq_ctl.sim_night_events "
                "ORDER BY night_date, id DESC").fetchall()}

        def status(n):                    # E6: RUN | FAILED (error class) | STARTED (interrupted: resumes) | QUEUED
            if n in ran:
                return "RUN", None
            ev = last_ev.get(n)
            if ev is None:
                return "QUEUED", None
            return ("FAILED", ev["error_class"]) if ev["event"] == "FAILED" else ("STARTED", None)

        nights = []
        for n, fs in synthetic_nights(self.sim_data, self.routing):
            st, err = status(n)
            nights.append({"night_date": n, "files": [p.name for p in fs], "status": st, "run_id": ran.get(n),
                           "error_class": err})
        return {"current_date": max(ran) if ran else None, "nights": nights}

    @staticmethod
    def _sim_out(r: dict) -> dict:
        s = dict(r["summary"])
        s.update(run_id=r["run_id"], night_date=r["night_date"].isoformat(), person_id=r["person_id"],
                 started_at=r["started_at"].isoformat(), finished_at=r["finished_at"].isoformat())
        return s

    def sim_runs(self, run_id: str | None = None):
        """All run summaries (night order), or one (KeyError if unknown)."""
        if not self.sim_enabled:
            raise SimUnavailable("the nightly simulator runs only in demo mode (--nightly)")
        rows = [self._sim_out(r) for r in self._sim_rows()]
        if run_id is None:
            return rows
        for r in rows:
            if r["run_id"] == run_id:
                return r
        raise KeyError(run_id)

    def sim_run_next(self, person_id: str | None) -> dict:
        """Run the next QUEUED night (advisory-locked: a second caller while a night runs gets SimConflict, as does a
        caller when no night is queued). Records the run in dq_ctl.sim_runs (append-only) and returns its summary.
        person_id None = preloaded at demo start."""
        if not self.sim_enabled:
            raise SimUnavailable("the nightly simulator runs only in demo mode (--nightly)")
        lock = self.cp.connect_as("dq_seal_svc")
        lock.autocommit = True
        try:
            got = lock.execute("SELECT pg_try_advisory_lock(hashtext(%s)) ok",
                               (SIM_LOCK_KEY.format(state=self.state),)).fetchone()["ok"]
            if not got:
                raise SimConflict("a night is already running")
            try:
                nights = synthetic_nights(self.sim_data, self.routing)
                ran = {r["night_date"].isoformat() for r in self._sim_rows()}
                queued = [(i, n, fs) for i, (n, fs) in enumerate(nights) if n not in ran]
                if not queued:
                    raise SimConflict("no night is queued")
                i, night, files = queued[0]
                out = self.run_night(self.sim_data, night, files, last=(i == len(nights) - 1), parte=self.sim_parte,
                                     record=True, person_id=person_id)
                started = next(e["at"] for e in self._night_events(night) if e["event"] == "STARTED")
                summary = out["summary"]
                with self.cp.as_role("dq_seal_svc") as c:
                    c.execute("INSERT INTO dq_ctl.sim_runs(run_id,night_date,started_at,person_id,files,summary) "
                              "VALUES (%s,%s,%s,%s,%s::jsonb,%s::jsonb)",
                              (summary["run_id"], night, started, person_id, json.dumps(summary["files"]),
                               json.dumps(summary, sort_keys=True, default=str)))
                return self.sim_runs(summary["run_id"])
            finally:
                lock.execute("SELECT pg_advisory_unlock(hashtext(%s))", (SIM_LOCK_KEY.format(state=self.state),))
        finally:
            lock.close()

    def earn_story_seed(self) -> int:
        """Live smoke L2 (demo option --earn-story, nightly mode only): start the release tier for AUTH_MEDICAID at
        L2 instead of the w1 seed's L4, through the ordinary lower-only demotion path (registry service, reason
        DEMO_EARN_STORY, recorded and audited like any demotion). The presenter can then show the earning arc:
        preloaded nights hold clean (PASS) Medicaid units for a person (L2), a two-person promotion moves the tier
        to L3, and the next simulated night opens real veto windows on clean units. Run before any night."""
        from dqagent.cp.agent_schema import DQ_AGENT
        return self.registry.demote(DQ_AGENT, "RELEASE", "AUTH_MEDICAID", "L2", "DEMO_EARN_STORY",
                                    None, self.state)

    def sim_preload(self, k: int) -> list[dict]:
        """Demo start: run nights 1..k (recorded with no person); the rest stay QUEUED."""
        out = []
        for _ in range(max(0, min(k, len(synthetic_nights(self.sim_data, self.routing))))):
            out.append(self.sim_run_next(None))
        return out

    def sim_status(self) -> dict:
        """The /summary block: {enabled, current_date, queued}."""
        if not self.sim_enabled:
            return {"enabled": False, "current_date": None, "queued": 0}
        n = self.sim_nights()
        return {"enabled": True, "current_date": n["current_date"],
                "queued": sum(1 for x in n["nights"] if x["status"] != "RUN")}
