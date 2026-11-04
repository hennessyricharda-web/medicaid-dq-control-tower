"""K5 / F-13 (PFP P2-6): a new source added by YAML ("Demo 2").

Demo moment:
  1. synthetic/out/w1_provider/provider_enrollment_<date>.csv appears as an UNROUTED_FILE (routing-driven
     discovery picks it up; no route matches it yet).
  2. The provider feed is onboarded by a governed change request touching `config/` only: a FEEDS route +
     parser, a CONTRACTS entry (with the new `source_format` field), a HARNESS pipeline (`p8_provider`), and
     an advisory TASK_GATE at L1 that introduces `bronze.provider_enrollment` -- no code, rule or test file
     changes (`test_new_feed_by_yaml_only`).
  3. After onboarding, the same files are ingested and held at H_IN under "task gate ceiling L1".

w1 itself stays at 420 RELEASED / 291 HELD / 47 PENDING_DEPENDENCY; w1_provider's own outcome (identical,
since the extra CSV never reaches a gated pipeline before onboarding) is pinned here on its first green run.
"""
import hashlib

import psycopg

from dqagent.cli.main import load_synthetic
from dqagent.engine.intake import Intake
from dqagent.runtime import ROOT, Runtime

from .w1_harness import Slice

SYN = ROOT / "synthetic" / "out"
W1_PROVIDER = SYN / "w1_provider"
EXPECTED = {"RELEASED": 420, "HELD": 291, "PENDING_DEPENDENCY": 47}
PROVIDER_CSV = next(p for p in sorted(W1_PROVIDER.iterdir()) if p.name.startswith("provider_enrollment_"))

# -- the onboarding change request (config/ only): built ADDITIVELY off the live effective artifacts, never by
# hand-typing a parallel copy of feeds.yaml / contracts_w1.yaml / harness_w1.yaml.
PROVIDER_RELATION = {
    "domain": "PROVIDER", "pipeline": "p8_provider", "source_format": "DELIMITED",
    "grain": ["provider_id"], "partition": "provider_id", "program_constant": "MEDICAID",
    "authorities_by_program": {"MEDICAID": ["AUTH_MEDICAID"]},
    "control_tags": {"npi": ["identity"], "legal_name": ["identity"]}, "status": "DRAFT",
}
PROVIDER_PIPELINE = {
    "id": "p8_provider", "platform": "duckdb", "orchestrator": "none", "dag_source": {"type": "none"},
    "contracts": "config/state/ZZ/contracts_w1.yaml", "gated_nodes": ["bronze.provider_enrollment"],
    "domains": ["PROVIDER"],
}
PROVIDER_FEED = {"delivered": True, "elements": "all", "cadence": {"expected": True, "every": "P1M"},
                 "part2_program": False}
PROVIDER_ROUTE = {"match": r"^provider_enrollment_(?P<date>\d{8})\.csv$", "format": "DELIMITED",
                  "pipeline": "p8_provider", "relations": ["bronze.provider_enrollment"]}
PROVIDER_GATE = {"artifact_id": "ZZ:taskgate:p8_provider:provider_enrollment", "kind": "TASK_GATE", "op": "PUT",
                 "content": {"task_id": "provider_enrollment", "datasets": ["bronze.provider_enrollment"],
                             "mode": "advisory", "correlate": "LATEST_SEALED", "max_wait": "PT2H",
                             "on_timeout": "PASS_ADVISORY", "release_ceiling": "L1", "reintroduce": []}}


def _provider_cr_items(s: Slice) -> list[dict]:
    """The onboarding CR's items, each an ADDITIVE copy of the live effective artifact (never a hand-typed
    parallel copy): CONTRACTS gains bronze.provider_enrollment, HARNESS gains p8_provider, FEEDS gains the
    route/parser and flips PROVIDER_FILE to delivered, plus the new advisory TASK_GATE."""
    contracts = s.effective_item("CONTRACTS", "contracts")
    contracts["content"]["relations"]["bronze.provider_enrollment"] = PROVIDER_RELATION
    harness = s.effective_item("HARNESS", "harness")
    harness["content"]["pipelines"].append(PROVIDER_PIPELINE)
    feeds = s.effective_item("FEEDS", "feeds")
    feeds["content"]["feeds"]["PROVIDER_FILE"] = PROVIDER_FEED
    feeds["content"]["routing"]["routes"].append(PROVIDER_ROUTE)
    return [contracts, harness, feeds, PROVIDER_GATE]


def _onboard_provider(s: Slice):
    cr, status = s.govern(_provider_cr_items(s), approvers=("checker", "checker2"))
    return cr, status


def _unrouted(dsn):
    with psycopg.connect(**dsn) as c:
        return c.execute("SELECT batch_id, feed, authority FROM dq_ctl.feed_items WHERE kind='UNROUTED_FILE' "
                         "ORDER BY item_id").fetchall()


def _tree_hash_excl_config_and_synthetic() -> str:
    """A dedicated hash of the tree EXCLUDING config/ and synthetic/ -- `tracer.code_sha256` HASHES config/ (it
    is the very thing the onboarding CR changes), so it cannot be reused here. Same method (sorted files, \\r\\n
    normalized, .py/.yaml/.yml/.sql only) as `tracer.code_sha256`, over `dqagent`, `rules`,
    `pipelines/w1_dbt/models` and `tests` only."""
    h = hashlib.sha256()
    for top in ("dqagent", "rules", "pipelines/w1_dbt/models", "tests"):
        for p in sorted((ROOT / top).rglob("*")):
            if p.is_file() and "__pycache__" not in p.parts and p.suffix in (".py", ".yaml", ".yml", ".sql"):
                rel = p.relative_to(ROOT).as_posix()
                content = p.read_bytes().replace(b"\r\n", b"\n")
                h.update(rel.encode() + b"\0" + content + b"\0")
    return h.hexdigest()


# -- demo moment 1: the files appear as UNROUTED_FILE, and w1_provider's outcome -----------------------------

def test_w1_provider_outcome_pinned(fresh_cp_dsn, tmp_path):
    """E0 practice: w1_provider's own outcome is pinned here on the first green run. w1 itself (420/291/47) is
    unaffected: the extra provider_enrollment CSV is UNROUTED before onboarding (next test) and never reaches
    a gated pipeline."""
    rt = Runtime(fresh_cp_dsn, dp_path=str(tmp_path / "dp.duckdb"), test_genesis=True)
    load_synthetic(rt, W1_PROVIDER)
    with psycopg.connect(**fresh_cp_dsn) as c:
        got = dict(c.execute("SELECT state, count(*) FROM dq_ctl.units GROUP BY 1").fetchall())
    assert got == EXPECTED


def test_provider_files_unrouted_before_onboarding(fresh_cp_dsn, tmp_path):
    """Routing-driven discovery (P1-0) DOES pick the provider CSV up; before onboarding no route matches it, so
    it opens exactly one UNROUTED_FILE item and is never ingested into a batch -- the same mechanism, and the
    same assertions, as any other unknown file (test_t0_routing.py's own precedent)."""
    rt = Runtime(fresh_cp_dsn, dp_path=str(tmp_path / "dp.duckdb"), test_genesis=True)
    out = load_synthetic(rt, W1_PROVIDER)
    assert out[PROVIDER_CSV.name]["routed"] is False
    # an UNROUTED file keeps B-<full name> (Routing.batch_id, Nit 7), never B-<stem>
    assert _unrouted(fresh_cp_dsn) == [(f"B-{PROVIDER_CSV.name}", "UNROUTED", "AUTH_INTAKE")]
    with psycopg.connect(**fresh_cp_dsn) as c:
        assert not c.execute("SELECT 1 FROM dq_ctl.batches WHERE batch_id=%s",
                             (f"B-{PROVIDER_CSV.name}",)).fetchone()


# -- demo moment 2: onboarding is a governed CR touching config/ only -----------------------------------------

def test_onboarding_cr_needs_two_approvers(fresh_cp_dsn, tmp_path):
    """CONTRACTS and HARNESS are both in registry.ALWAYS_LOOSENING (they already exist at genesis, so a PUT to
    either is reviewed as loosening by default), and a FEEDS routing change is independently classified
    loosening (registry.feeds_loosening: "inbound file routing changed") -- this onboarding CR needs two
    approvers on EITHER ground, confirmed against the live database decision (not the Python display mirror),
    the same way K4's own work found for a comparable CR."""
    s = Slice(fresh_cp_dsn, dp_path=str(tmp_path / "dp.duckdb"))
    items = _provider_cr_items(s)
    cr = s.registry.propose("ZZ", s.idp.users["maker"]["emp_id"],
                            "Onboard the provider-enrollment feed by configuration (K5 / F-13)", items)
    assert cr["loosening"] is True
    assert cr["approvals_required"] == 2


def test_new_feed_by_yaml_only(fresh_cp_dsn, tmp_path):
    """Onboarding touches config/ ONLY: the tree hash excluding config/ and synthetic/ (dqagent, rules, the dbt
    models, tests) is byte-identical before and after the CR is applied."""
    before = _tree_hash_excl_config_and_synthetic()
    s = Slice(fresh_cp_dsn, dp_path=str(tmp_path / "dp.duckdb"))
    cr, status = _onboard_provider(s)
    assert status == "APPLIED"
    after = _tree_hash_excl_config_and_synthetic()
    assert before == after


# -- demo moment 3: after onboarding, ingested and held at H_IN under task gate ceiling L1 --------------------

def test_run_pipeline_dispatches_delimited_file_and_holds_at_h_in_l1(fresh_cp_dsn, tmp_path, monkeypatch):
    """`run_pipeline`'s intake dispatch calls `Intake.delimited_file` for the DELIMITED format tag (spied, not
    just inferred from the outcome), and the resulting unit is HELD at H_IN under the new TASK_GATE's L1
    ceiling (`hitl.modes.TIER_CEILING['L1'] == 'H_IN'`) -- the demo moment's own wording."""
    import dqagent.engine.intake as intake_mod
    s = Slice(fresh_cp_dsn, dp_path=str(tmp_path / "dp.duckdb"))
    _onboard_provider(s)
    calls = []
    real = intake_mod.Intake.delimited_file

    def spy(self, path, partner="ZZ-PROVIDER"):
        calls.append((path, partner))
        return real(self, path, partner)
    monkeypatch.setattr(intake_mod.Intake, "delimited_file", spy)
    plan = s.routing.plan(W1_PROVIDER, PROVIDER_CSV)
    assert plan is not None and plan.pipeline == "p8_provider" and plan.relations == ["bronze.provider_enrollment"]
    s.run_pipeline(plan.pipeline, plan.batch, plan.files, plan.relations)
    assert len(calls) == 1 and calls[0][0] == PROVIDER_CSV
    with s.cp.as_role("dq_eval_svc") as c:
        rows = c.execute("SELECT state FROM dq_ctl.units WHERE node='bronze.provider_enrollment'").fetchall()
    assert rows and all(r["state"] == "HELD" for r in rows)
    with s.cp.as_role("dq_registry_svc") as c:
        ceiling = c.execute("SELECT dq_ctl.task_gate_ceiling('ZZ', 'bronze.provider_enrollment') t").fetchone()["t"]
    assert ceiling == "L1"


# -- Intake.delimited_file in isolation (red team Fix 1 / Fix 2) ----------------------------------------------

def test_delimited_file_lands_rows_and_records_abc(fresh_cp_dsn, tmp_path):
    """Fix 1: BRONZE_COLUMNS has a `bronze.provider_enrollment` entry (else `_land` KeyErrors at the exact demo
    moment). Fix 2: `delimited_file` carries the SAME FILE->RAW / RAW->LANDING `self.abc` obligations as
    `ie_file`/`x12_file`, not just a bare `_land` call."""
    s = Slice(fresh_cp_dsn, dp_path=str(tmp_path / "dp.duckdb"))
    it = Intake(s.cp, s.dp, "p8_provider", "B-TEST-DELIM", feeds=s.feeds, state=s.state)
    file_id = it.delimited_file(PROVIDER_CSV)
    assert it.abc, "delimited_file recorded no ABC rows"
    hops = {(r["hop"], r.get("measure")) for r in it.abc}
    assert ("FILE->RAW", "detail_records") in hops
    assert ("RAW->LANDING", "records") in hops
    n = s.dp.fetchall("SELECT count(*) FROM landing.provider_enrollment WHERE _dq_file_id = ?", [file_id])[0][0]
    assert n > 0


# -- the config-driven dependency resolver (red team: _intersecting_elig_units) -------------------------------

def test_intersecting_elig_units_returns_empty_with_no_dependency_entry(fresh_cp_dsn, tmp_path):
    """The config-driven resolver (`Evaluator._intersecting_elig_units`) returns [] cleanly -- never raises,
    never assumes bronze.ie_segment exists -- when a WAIT dependency names a domain no contract declares: the
    failure mode a provider feed would hit if the generalization were incomplete. Unit test of the resolver in
    isolation: a no-WAIT-dependency contract (bronze.provider_enrollment, domain PROVIDER) never reaches this
    call site at runtime (evaluate.py's own `if deps:` guard upstream) -- confirmed, not claimed as
    runtime-path coverage."""
    s = Slice(fresh_cp_dsn, dp_path=str(tmp_path / "dp.duckdb"))
    bogus_dep = {"domain": "PROVIDER", "depends_on": "NO_SUCH_DOMAIN", "key_map": ["provider_id=msis_id"],
                "policy": "WAIT"}
    out = s.evaluator._intersecting_elig_units("bronze.provider_enrollment", "B1", "B1:MEDICAID:deadbeef",
                                               ["AUTH_MEDICAID"], bogus_dep)
    assert out == []


def test_intersecting_elig_units_real_eligibility_dependency_unchanged(fresh_cp_dsn, tmp_path):
    """The generalized resolver reproduces the OLD hard-coded bronze.encounter_header/bronze.ie_segment join
    exactly, given the real CLAIMS->ELIGIBILITY WAIT dependency from harness_w1.yaml (key_map
    member_id=msis_id): a program with no matching authority still returns [] (no eligibility unit intersects),
    never raises."""
    s = Slice(fresh_cp_dsn, dp_path=str(tmp_path / "dp.duckdb"))
    dep = next(d for d in s.dependencies if d["domain"] == "CLAIMS" and d["policy"] == "WAIT")
    out = s.evaluator._intersecting_elig_units("bronze.encounter_header", "B-NONE", "B-NONE:MEDICAID:deadbeef",
                                               ["AUTH_SNAP"], dep)
    assert out == []
