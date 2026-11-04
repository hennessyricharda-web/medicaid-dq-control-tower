"""Part F P1-3 (K2a): PIPELINE / TASK_GATE kinds, the database two-person rule for them (and the pre-existing HARNESS
gap this wave also closes), ORCH-1 (`gated_dataset_introductions`, `_introduce_datasets`, `task_gate_ceiling`) and
the INHERIT-at-GENESIS-only check. All 11 tests PFP names (`docs/rules/PARTF_IMPLEMENTATION_PLAN.md:849-860`)."""
import copy

import psycopg
import pytest

from dqagent.governance.registry import lower_only

from .w1_harness import Slice

W1_RELATIONS = ("bronze.ie_segment", "bronze.enrollment_834", "bronze.encounter_header", "bronze.encounter_line")


def _approve(s, cr, *users):
    for u in users:
        tok = s.idp.token(u, step_up=True, challenge=s.registry.challenge(cr["cr_id"], "APPROVED"))
        s.registry.decide(cr["cr_id"], "APPROVED", tok, s.idp.users[u]["emp_id"], ["governance:AUTH_RULES"])
    return s.apply_change(cr["cr_id"])


def _effective_task_gate(s, pipeline, task):
    return s.effective_item("TASK_GATE", f"taskgate:{pipeline}:{task}")


def test_lower_only_matrix_task_gate(fresh_cp_dsn):
    """PFP :754: TASK_GATE is lower-only only for an EXISTING gate where datasets, ceiling, max_wait, on_timeout,
    mode and correlate all stay the same or tighten, and reintroduce is empty. Both directions, both layers."""
    s = Slice(fresh_cp_dsn)
    # a brand-new gate is never lower-only, whatever its content
    new_gate = {"artifact_id": "ZZ:taskgate:tmsis_outbound:ie_segment", "kind": "TASK_GATE", "op": "PUT", "content": {
        "task_id": "ie_segment", "datasets": ["bronze.ie_segment"], "mode": "advisory", "correlate": "LATEST_SEALED",
        "max_wait": "PT2H", "on_timeout": "PASS_ADVISORY", "release_ceiling": "L1", "reintroduce": []}}
    cr = s.registry.propose("ZZ", "E100", "Wire a new consumer task gate", [new_gate])
    assert cr["approvals_required"] == 2
    assert lower_only("TASK_GATE", None, new_gate["content"], "PUT") is False

    # baseline: set the existing w1 gate to a concrete L2 ceiling (two-person, since INHERIT is not tier-comparable)
    base = _effective_task_gate(s, "p4_eligibility_ie", "ie_segment")
    base["content"] = dict(base["content"]) | {"release_ceiling": "L2"}
    assert _approve(s, s.registry.propose("ZZ", "E100", "Baseline: concrete ceiling", [base]), "checker",
                    "checker2") == "APPLIED"

    old_content = _effective_task_gate(s, "p4_eligibility_ie", "ie_segment")["content"]

    # tightening-only (shorter max_wait, same everything else): lower-only, one approver
    tighten = copy.deepcopy(_effective_task_gate(s, "p4_eligibility_ie", "ie_segment"))
    tighten["content"]["max_wait"] = "PT1H"
    assert lower_only("TASK_GATE", old_content, tighten["content"], "PUT") is True
    cr_t = s.registry.propose("ZZ", "E100", "Tighten max_wait only", [tighten])
    assert cr_t["approvals_required"] == 1
    assert _approve(s, cr_t, "checker") == "APPLIED"

    # mode change (ADJ-2): always two, even otherwise identical
    old_content = _effective_task_gate(s, "p4_eligibility_ie", "ie_segment")["content"]
    mode_chg = copy.deepcopy(_effective_task_gate(s, "p4_eligibility_ie", "ie_segment"))
    mode_chg["content"]["mode"] = "advisory"
    assert lower_only("TASK_GATE", old_content, mode_chg["content"], "PUT") is False
    assert s.registry.propose("ZZ", "E100", "Change mode", [mode_chg])["approvals_required"] == 2

    # raising the ceiling: always two
    raise_ceiling = copy.deepcopy(_effective_task_gate(s, "p4_eligibility_ie", "ie_segment"))
    raise_ceiling["content"]["release_ceiling"] = "L3"
    assert lower_only("TASK_GATE", old_content, raise_ceiling["content"], "PUT") is False
    assert s.registry.propose("ZZ", "E100", "Raise ceiling", [raise_ceiling])["approvals_required"] == 2

    # dropping a dataset the gate covers: always two (datasets must be a SUPERSET of old)
    old_hdr = s.effective_item("TASK_GATE", "taskgate:p6_claims:encounter_header")["content"]
    drop_ds = copy.deepcopy(s.effective_item("TASK_GATE", "taskgate:p6_claims:encounter_header"))
    drop_ds["content"]["release_ceiling"] = "L1"               # a concrete ceiling: INHERIT is genesis-only
    drop_ds["content"]["datasets"] = ["bronze.encounter_line"]  # a different, still non-empty set: not a superset
    assert lower_only("TASK_GATE", old_hdr, drop_ds["content"], "PUT") is False
    assert s.registry.propose("ZZ", "E100", "Drop a dataset", [drop_ds])["approvals_required"] == 2

    # on_timeout changing to something other than HOLD: always two
    ot = copy.deepcopy(_effective_task_gate(s, "p4_eligibility_ie", "ie_segment"))
    ot["content"]["on_timeout"] = "ESCALATE"
    assert lower_only("TASK_GATE", old_content, ot["content"], "PUT") is False
    assert s.registry.propose("ZZ", "E100", "Escalate on timeout", [ot])["approvals_required"] == 2

    # DELETE of an existing gate: always two
    delete = {"artifact_id": "ZZ:taskgate:p4_eligibility_ie:ie_segment", "kind": "TASK_GATE", "op": "DELETE"}
    assert lower_only("TASK_GATE", old_content, None, "DELETE") is False
    assert s.registry.propose("ZZ", "E100", "Delete a gate", [delete])["approvals_required"] == 2


def test_harness_two_person_without_python_flag(fresh_cp_dsn):
    """K2a-4 (pre-existing defect, found and fixed by this package, not new K2 scope): HARNESS used to fall through
    to `gov_item_lower_only`'s final `RETURN true` (and the Python mirror's final `return True`), so a HARNESS
    change incorrectly needed only one approver. Both sides are fixed here; a HARNESS change needs two even when
    the CALLER's own loosening flag claims false."""
    s = Slice(fresh_cp_dsn)
    h = s.effective_item("HARNESS", "harness")
    h["content"] = dict(h["content"])
    h["content"]["domains_extra_noop"] = ["NOOP"]
    assert lower_only("HARNESS", {"a": 1}, {"a": 1, "b": 2}, "PUT") is False      # Python mirror
    cr = s.registry.propose("ZZ", "E100", "A harmless-looking harness tweak", [h])
    assert cr["approvals_required"] == 2                                          # database decision
    with s.cp.as_role("dq_api") as c:                                             # the caller's flag cannot lower it
        c.execute("SELECT dq_ctl.gov_propose('CR-FORGED-H','ZZ','E100','HUMAN','forged harness not-loosening',"
                  "%s::jsonb,'{}'::jsonb,'{}'::jsonb,false,'h','{}'::jsonb,NULL)",
                  (__import__("json").dumps([h]),))
        row = c.execute("SELECT approvals_required FROM dq_ctl.config_change_requests WHERE cr_id='CR-FORGED-H'"
                        ).fetchone()
    assert row["approvals_required"] == 2


def test_pipeline_always_two_person(fresh_cp_dsn):
    """PFP :753: PIPELINE returns FALSE (never lower-only) from `gov_item_lower_only`, whatever it changes."""
    s = Slice(fresh_cp_dsn)
    pipe = s.effective_item("PIPELINE", "pipeline:p4_eligibility_ie")
    assert lower_only("PIPELINE", pipe["content"], pipe["content"], "PUT") is False   # even a no-op content change
    cr = s.registry.propose("ZZ", "E100", "Touch an existing pipeline item", [pipe])
    assert cr["approvals_required"] == 2
    new_pipe = {"artifact_id": "ZZ:pipeline:tmsis_outbound", "kind": "PIPELINE", "op": "PUT", "content": {
        "adapter": "dbt_files", "scope": {"manifest_path": "pipelines/tmsis_outbound/target/manifest.json"},
        "platform": "duckdb", "domains": ["CLAIMS"], "layer_map": {}, "run_on": "night_end"}}
    assert s.registry.propose("ZZ", "E100", "Wire a new consumer pipeline", [new_pipe])["approvals_required"] == 2


def test_mode_change_always_two_person(fresh_cp_dsn):
    """ADJ-2, PFP :754: `mode` changing is never lower-only even when every other field tightens."""
    s = Slice(fresh_cp_dsn)
    base = _effective_task_gate(s, "p6_claims", "encounter_header")
    base["content"] = dict(base["content"]) | {"release_ceiling": "L2"}
    assert _approve(s, s.registry.propose("ZZ", "E100", "Baseline concrete ceiling", [base]), "checker",
                    "checker2") == "APPLIED"
    item = copy.deepcopy(_effective_task_gate(s, "p6_claims", "encounter_header"))
    item["content"]["mode"] = "hold_until_terminal"       # otherwise a tightening-looking move
    item["content"]["max_wait"] = "PT1H"
    assert s.registry.propose("ZZ", "E100", "Mode change despite a tightening max_wait", [item]
                              )["approvals_required"] == 2


def test_literal_secret_refused(fresh_cp_dsn):
    """PFP :749: a literal value under a key matching token|password|secret|key anywhere in PIPELINE / TASK_GATE
    content is refused, fail closed. A reference (not a bare literal) under an unrelated key is fine."""
    s = Slice(fresh_cp_dsn)
    bad_pipeline = {"artifact_id": "ZZ:pipeline:tmsis_outbound", "kind": "PIPELINE", "op": "PUT", "content": {
        "adapter": "dbt_files", "scope": {"manifest_path": "pipelines/tmsis_outbound/target/manifest.json"},
        "platform": "duckdb", "domains": ["CLAIMS"], "layer_map": {}, "run_on": "night_end",
        "api_key": "sk-literal-should-be-refused"}}
    with pytest.raises(psycopg.errors.CheckViolation):
        s.registry.propose("ZZ", "E100", "Wire with a literal secret", [bad_pipeline])
    nested_bad = copy.deepcopy(bad_pipeline)
    del nested_bad["content"]["api_key"]
    nested_bad["content"]["scope"]["connection_secret"] = "literal-nested-secret"
    with pytest.raises(psycopg.errors.CheckViolation):
        s.registry.propose("ZZ", "E100", "Wire with a nested literal secret", [nested_bad])
    ok_pipeline = copy.deepcopy(bad_pipeline)
    del ok_pipeline["content"]["api_key"]
    cr = s.registry.propose("ZZ", "E100", "Wire without any literal secret", [ok_pipeline])
    assert cr["cr_id"]


def test_inherit_only_at_genesis(fresh_cp_dsn):
    """PFP :750: a TASK_GATE's release_ceiling of INHERIT is accepted only when the proposing change request's own
    origin is GENESIS; a HUMAN-origin proposal with INHERIT is refused."""
    s = Slice(fresh_cp_dsn)
    gate = s.effective_item("TASK_GATE", "taskgate:p4_eligibility_ie:ie_segment")   # genesis content: INHERIT
    assert gate["content"]["release_ceiling"] == "INHERIT"
    with pytest.raises(psycopg.errors.CheckViolation):
        s.registry.propose("ZZ", "E100", "Re-propose INHERIT under a HUMAN origin", [gate])
    concrete = copy.deepcopy(gate)
    concrete["content"]["release_ceiling"] = "L1"
    cr = s.registry.propose("ZZ", "E100", "Re-propose with a concrete ceiling instead", [concrete])
    assert cr["cr_id"]


def test_introductions_written_only_by_helper(fresh_cp_dsn):
    """PFP :759,:771: the table is written ONLY by `_introduce_datasets`, which is owner-only; no login role holds
    INSERT on the table directly, nor EXECUTE on the helper. (The T1 admin/bootstrap connection is cluster SUPERUSER
    -- confirmed by direct query, `rolsuper`/`rolbypassrls` both true -- so GRANT/RLS cannot be asserted against it;
    every real login ROLE is checked instead, which is the actual production-shaped boundary.)"""
    s = Slice(fresh_cp_dsn)
    for role in ("dq_api", "dq_registry_svc", "dq_eval_svc", "dq_publish_svc", "dq_auditor"):
        with s.cp.as_role(role) as c:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                c.execute("INSERT INTO dq_ctl.gated_dataset_introductions(state,dataset,version,introduced_by,"
                          "gate_version,cr_id,reason) VALUES ('ZZ','x',99,'y',1,'z','FIRST')")
        with s.cp.as_role(role) as c:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                c.execute("SELECT dq_ctl._introduce_datasets('ZZ','[]'::jsonb,'x')")


def test_introductions_append_only(fresh_cp_dsn):
    """Append-only: UPDATE, DELETE and TRUNCATE are refused even for the admin session (reject_mutation trigger)."""
    s = Slice(fresh_cp_dsn)                      # the genesis seed already wrote 4 rows
    with psycopg.connect(**fresh_cp_dsn) as adm:
        for stmt in ("UPDATE dq_ctl.gated_dataset_introductions SET version = version",
                     "DELETE FROM dq_ctl.gated_dataset_introductions",
                     "TRUNCATE dq_ctl.gated_dataset_introductions CASCADE"):
            with pytest.raises(psycopg.errors.InsufficientPrivilege, match="append-only"):
                adm.execute(stmt)
            adm.rollback()
        n = adm.execute("SELECT count(*) FROM dq_ctl.gated_dataset_introductions WHERE state='ZZ'").fetchone()[0]
    assert n == len(W1_RELATIONS)


def test_introductions_identical_under_test_and_governed_genesis(fresh_cp_dsn, tmp_path, monkeypatch):
    """C1 (PFP :779-782): the TEST-ONLY path (`write_test_genesis`) and the GOVERNED path (`propose_genesis` ->
    `gov_apply`) both call `_introduce_datasets` with the SAME items in the SAME order, so two fresh control planes
    produce IDENTICAL (dataset, version, introduced_by, gate_version, reason) rows, ignoring cr_id and at."""
    import uuid

    import psycopg as pg
    from psycopg.rows import dict_row

    from dqagent.cp import schema
    from dqagent.runtime import Runtime, bootstrap_roster

    for k, v in {"DQ_K_EV": "pilot-ev-key", "DQ_K_FP": "pilot-fp-key", "DQ_K_ABC": "pilot-abc-key"}.items():
        monkeypatch.setenv(k, v)

    Slice(fresh_cp_dsn)         # the TEST-ONLY path: write_test_genesis -> _write_test_genesis -> _introduce_datasets
    with pg.connect(**fresh_cp_dsn, row_factory=dict_row) as c:
        rows1 = sorted((r["dataset"], r["version"], r["introduced_by"], r["gate_version"], r["reason"])
                       for r in c.execute("SELECT * FROM dq_ctl.gated_dataset_introductions").fetchall())

    PG = {k: v for k, v in fresh_cp_dsn.items() if k != "dbname"}
    admin = pg.connect(dbname="postgres", autocommit=True, **PG)
    db2 = "dqcp_" + uuid.uuid4().hex[:8]
    admin.execute(f"CREATE DATABASE {db2}")
    dsn2 = dict(dbname=db2, **PG)
    try:
        with pg.connect(**dsn2) as c:
            schema.apply(c)
        bootstrap_roster(dsn2, {}, "ROSTER-BOOTSTRAP")
        rt2 = Runtime(dsn2, dp_path=str(tmp_path / "dp.duckdb"), deployment="pilot")
        cr = rt2.registry.propose_genesis("ZZ", "E100")
        for user, emp in (("checker", "E200"), ("checker2", "E210")):
            tok = rt2.idp.token(user, step_up=True, challenge=rt2.registry.challenge(cr["cr_id"], "APPROVED"))
            rt2.registry.decide(cr["cr_id"], "APPROVED", tok, emp, ["governance:AUTH_RULES"])
        assert rt2.apply_change(cr["cr_id"]) == "APPLIED"
        with pg.connect(**dsn2, row_factory=dict_row) as c:
            rows2 = sorted((r["dataset"], r["version"], r["introduced_by"], r["gate_version"], r["reason"])
                           for r in c.execute("SELECT * FROM dq_ctl.gated_dataset_introductions").fetchall())
    finally:
        admin.execute(f"DROP DATABASE {db2} WITH (FORCE)")
        admin.close()
    assert rows1 == rows2 and rows1    # non-empty: the 4 w1 relations, at minimum


def test_reintroduction_after_introducer_delete(fresh_cp_dsn):
    """I3 (PFP :776): delete the introducer, the dataset reads L1 (fail-closed interim), a two-person CR re-gates it
    at L2, and a version-2 row with reason INTRODUCER_REMOVED reads L2."""
    s = Slice(fresh_cp_dsn)
    with s.cp.as_role("dq_registry_svc") as c:
        before = c.execute("SELECT dq_ctl.task_gate_ceiling('ZZ','bronze.ie_segment') t").fetchone()["t"]
    assert before is None                              # INHERIT: no additional ceiling

    delete = {"artifact_id": "ZZ:taskgate:p4_eligibility_ie:ie_segment", "kind": "TASK_GATE", "op": "DELETE"}
    assert _approve(s, s.registry.propose("ZZ", "E100", "Remove the introducing gate", [delete]), "checker",
                    "checker2") == "APPLIED"
    with s.cp.as_role("dq_registry_svc") as c:
        interim = c.execute("SELECT dq_ctl.task_gate_ceiling('ZZ','bronze.ie_segment') t").fetchone()["t"]
    assert interim == "L1"

    new_gate = {"artifact_id": "ZZ:taskgate:tmsis_outbound:ie_segment", "kind": "TASK_GATE", "op": "PUT", "content": {
        "task_id": "ie_segment", "datasets": ["bronze.ie_segment"], "mode": "block", "correlate": "LATEST_SEALED",
        "max_wait": "PT2H", "on_timeout": "HOLD", "release_ceiling": "L2", "reintroduce": []}}
    cr = s.registry.propose("ZZ", "E100", "Re-gate at L2", [new_gate])
    assert cr["approvals_required"] == 2
    assert _approve(s, cr, "checker", "checker2") == "APPLIED"

    with s.cp.as_role("dq_registry_svc") as c:
        rows = c.execute("SELECT version, introduced_by, reason FROM dq_ctl.gated_dataset_introductions "
                         "WHERE state='ZZ' AND dataset='bronze.ie_segment' ORDER BY version").fetchall()
        after = c.execute("SELECT dq_ctl.task_gate_ceiling('ZZ','bronze.ie_segment') t").fetchone()["t"]
    assert [(r["version"], r["reason"]) for r in rows] == [(1, "FIRST"), (2, "INTRODUCER_REMOVED")]
    assert rows[-1]["introduced_by"] == "ZZ:taskgate:tmsis_outbound:ie_segment"
    assert after == "L2"


def test_explicit_reintroduce_needs_two(fresh_cp_dsn):
    """PFP :777: a dataset explicitly named in `content.reintroduce` is never lower-only, so it needs two approvers
    even when every other field of the SAME gate change tightens."""
    s = Slice(fresh_cp_dsn)
    item = s.effective_item("TASK_GATE", "taskgate:p4_eligibility_ie:ie_segment")
    item["content"] = dict(item["content"])
    item["content"]["release_ceiling"] = "L1"           # a concrete ceiling: a human cannot propose INHERIT
    item["content"]["max_wait"] = "PT1H"                 # otherwise a tightening-looking move
    item["content"]["reintroduce"] = ["bronze.ie_segment"]
    assert lower_only("TASK_GATE", s.effective_item("TASK_GATE", "taskgate:p4_eligibility_ie:ie_segment")["content"],
                      item["content"], "PUT") is False
    cr = s.registry.propose("ZZ", "E100", "Explicit reintroduce despite tightening", [item])
    assert cr["approvals_required"] == 2
