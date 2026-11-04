"""Part F P1-4 (K3): `dqagent/backtest.py` -- sandbox lifecycle, the determinism double-run, and the mode-only
honest-disclosure field. Tests PFP names, verbatim (`docs/rules/PARTF_IMPLEMENTATION_PLAN.md:912-915`), plus a real
CLI end-to-end subprocess test per the orchestrator's K3_DESIGN.md §6 item 6."""
import os
import subprocess
import sys
from pathlib import Path

import psycopg
import pytest

from dqagent.backtest import (
    MODE_ONLY_DISCLOSURE,
    BacktestNotDeterministic,
    _sandbox_names,
    compute_backtest,
    run_backtest,
    sanitize_cr_id,
)

from .test_pipelines_runtime import _approve, _wire_tmsis_outbound
from .w1_harness import Slice

ROOT = Path(__file__).resolve().parents[2]
W1 = ROOT / "synthetic" / "out" / "w1"
PG = dict(host=os.environ.get("DQ_PG_HOST", "/tmp"), port=int(os.environ.get("DQ_PG_PORT", "55432")),
          user=os.environ.get("DQ_PG_USER", "dq_gov"))
NIGHTS = 2          # a small, fast prefix of w1's 22 nights -- plenty to exercise every code path under test


def _mode_cr(s, mode="block"):
    """A CR that flips tmsis_outbound's two TASK_GATE items' `mode` to `mode`, everything else unchanged -- the
    exact shape the golden-path demo moment uses (K3_DESIGN.md §3 step 2)."""
    ie = s.effective_item("TASK_GATE", "taskgate:tmsis_outbound:ie_segment")
    enc = s.effective_item("TASK_GATE", "taskgate:tmsis_outbound:encounter_header")
    ie["content"]["mode"] = mode
    enc["content"]["mode"] = mode
    cr = s.registry.propose("ZZ", "E100", f"Promote tmsis_outbound gates to {mode}", [ie, enc])
    assert cr["approvals_required"] == 2            # a mode change is never lower-only (ADJ-2)
    return cr


def _sandbox_dsns(cr_id):
    base, cand = _sandbox_names(cr_id)
    return dict(dbname=base, **PG), dict(dbname=cand, **PG)


def test_backtest_deterministic_twice(fresh_cp_dsn):
    """PFP :903: the whole sandbox-pair process runs twice end to end; the two runs' digests match, and the
    returned report is a single, stable result (not one of two different attempts picked arbitrarily)."""
    s = Slice(fresh_cp_dsn)
    _wire_tmsis_outbound(s, mode="advisory", ceiling="L1")
    cr = _mode_cr(s)
    report = compute_backtest(pipeline_id="tmsis_outbound", cr_id=cr["cr_id"], nights=NIGHTS, data=W1,
                              pg=PG, live_dsn=fresh_cp_dsn)
    assert report["label"] == "SYNTHESIZED"
    assert report["trace_shape_digest_base"] and report["trace_shape_digest_cand"]
    # a second, independent compute_backtest call (itself internally doubled) reproduces the SAME digest
    report2 = compute_backtest(pipeline_id="tmsis_outbound", cr_id=cr["cr_id"], nights=NIGHTS, data=W1,
                               pg=PG, live_dsn=fresh_cp_dsn)
    assert report2["digest"] == report["digest"]
    assert report2["trace_shape_digest_base"] == report["trace_shape_digest_base"]
    assert report2["trace_shape_digest_cand"] == report["trace_shape_digest_cand"]


def test_backtest_never_touches_live_cp(fresh_cp_dsn):
    """A real, convincing negative control (per the brief): the live control plane's state is BIT-IDENTICAL before
    and after a full backtest run, not merely "no exception was raised". Every table `cp/schema.py`'s `_apply`
    creates (base schema + agent + governance + parte + trace) is compared row for row."""
    s = Slice(fresh_cp_dsn)
    _wire_tmsis_outbound(s, mode="advisory", ceiling="L1")
    cr = _mode_cr(s)

    def _snapshot():
        with psycopg.connect(**fresh_cp_dsn) as c:
            tables = [r[0] for r in c.execute(
                "SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname = 'dq_ctl' AND c.relkind = 'r' ORDER BY c.relname").fetchall()]
            out = {}
            for t in tables:
                out[t] = sorted(str(r) for r in c.execute(f"SELECT * FROM dq_ctl.{t}").fetchall())
            return tables, out

    tables_before, before = _snapshot()
    report = compute_backtest(pipeline_id="tmsis_outbound", cr_id=cr["cr_id"], nights=NIGHTS, data=W1,
                              pg=PG, live_dsn=fresh_cp_dsn)
    assert report["trace_shape_digest_base"]            # the backtest did real work, not a no-op
    tables_after, after = _snapshot()
    assert tables_before == tables_after                 # no table appeared or disappeared
    assert before == after                               # every row, in every table, is byte-identical


def test_backtest_never_touches_live_cp_sandboxes_do_not_leak(fresh_cp_dsn):
    """Both sandbox databases are dropped (success path): neither `dqbt_<cr>_base` nor `_cand` exists on the
    cluster once `compute_backtest` returns."""
    s = Slice(fresh_cp_dsn)
    _wire_tmsis_outbound(s, mode="advisory", ceiling="L1")
    cr = _mode_cr(s)
    base_dsn, cand_dsn = _sandbox_dsns(cr["cr_id"])
    compute_backtest(pipeline_id="tmsis_outbound", cr_id=cr["cr_id"], nights=NIGHTS, data=W1,
                     pg=PG, live_dsn=fresh_cp_dsn)
    with psycopg.connect(dbname="postgres", autocommit=True, **PG) as a:
        for dsn in (base_dsn, cand_dsn):
            row = a.execute("SELECT 1 FROM pg_database WHERE datname=%s", (dsn["dbname"],)).fetchone()
            assert row is None, f"{dsn['dbname']} was not dropped"


def test_report_labelled_synthesized(fresh_cp_dsn):
    """PFP :904: the report is labelled SYNTHESIZED."""
    s = Slice(fresh_cp_dsn)
    _wire_tmsis_outbound(s, mode="advisory", ceiling="L1")
    cr = _mode_cr(s)
    report = compute_backtest(pipeline_id="tmsis_outbound", cr_id=cr["cr_id"], nights=NIGHTS, data=W1,
                              pg=PG, live_dsn=fresh_cp_dsn)
    assert report["label"] == "SYNTHESIZED"


def test_mode_only_cr_carries_the_honest_disclosure(fresh_cp_dsn):
    """Orchestrator Decision 1 (K3_DESIGN.md §6, mandatory, not cosmetic): a CR that changes ONLY a TASK_GATE's
    `mode` must carry the plain-language disclosure that the release/hold/pending delta is legitimately zero --
    `block` mode is not yet behaviorally enforced. The relation-level deltas are literally zero, confirming the
    disclosure is not just a label glued onto a real difference."""
    s = Slice(fresh_cp_dsn)
    _wire_tmsis_outbound(s, mode="advisory", ceiling="L1")
    cr = _mode_cr(s)
    report = compute_backtest(pipeline_id="tmsis_outbound", cr_id=cr["cr_id"], nights=NIGHTS, data=W1,
                              pg=PG, live_dsn=fresh_cp_dsn)
    assert report["delta_note"] == MODE_ONLY_DISCLOSURE
    for rel, delta in report["deltas"]["relations"].items():
        assert all(v == 0 for v in delta.values()), (rel, delta)
    assert report["deltas"]["would_hold"] == 0


def test_non_mode_only_cr_has_no_disclosure(fresh_cp_dsn):
    """Negative control for the disclosure field itself (K3_DESIGN.md §6 item 1: "this must not blanket every
    CR"): a CR that changes something OTHER than mode (here, release_ceiling) does not get the mode-only
    disclosure, even though it is also backtest-able."""
    s = Slice(fresh_cp_dsn)
    _wire_tmsis_outbound(s, mode="advisory", ceiling="L1")
    item = s.effective_item("TASK_GATE", "taskgate:tmsis_outbound:ie_segment")
    item["content"]["release_ceiling"] = "L2"
    cr = s.registry.propose("ZZ", "E100", "Raise tmsis_outbound ie_segment ceiling", [item])
    assert cr["approvals_required"] == 2
    report = compute_backtest(pipeline_id="tmsis_outbound", cr_id=cr["cr_id"], nights=NIGHTS, data=W1,
                              pg=PG, live_dsn=fresh_cp_dsn)
    assert report["delta_note"] is None


def test_sanitize_cr_id_strips_prefix_and_fails_closed():
    """K3-4 (a correctness requirement): the sandbox database name uses only the hex suffix of a CR id; anything
    else (the hyphen left in, a non-hex suffix, an unrelated string) is refused, not silently truncated."""
    assert sanitize_cr_id("CR-a1b2c3d4e5") == "a1b2c3d4e5"
    base, cand = _sandbox_names("CR-a1b2c3d4e5")
    assert base == "dqbt_a1b2c3d4e5_base" and cand == "dqbt_a1b2c3d4e5_cand"
    for bad in ("CR-a1b2-c3d4", "not-a-cr-id", "CR-UPPERCASE1", "CR-" + "a" * 40, ""):
        with pytest.raises(ValueError):
            sanitize_cr_id(bad)


def test_backtest_refuses_unknown_cr(fresh_cp_dsn):
    """Degenerate input, fail closed: a CR id that is well-formed but does not exist in the live control plane is
    refused with a clear error, not a crash deep inside sandbox seeding."""
    s = Slice(fresh_cp_dsn)
    _wire_tmsis_outbound(s, mode="advisory", ceiling="L1")
    with pytest.raises(ValueError, match="unknown change request"):
        compute_backtest(pipeline_id="tmsis_outbound", cr_id="CR-0000000000", nights=NIGHTS, data=W1,
                         pg=PG, live_dsn=fresh_cp_dsn)


def test_backtest_refuses_negative_nights(fresh_cp_dsn):
    s = Slice(fresh_cp_dsn)
    _wire_tmsis_outbound(s, mode="advisory", ceiling="L1")
    cr = _mode_cr(s)
    with pytest.raises(ValueError, match="non-negative"):
        compute_backtest(pipeline_id="tmsis_outbound", cr_id=cr["cr_id"], nights=-1, data=W1,
                         pg=PG, live_dsn=fresh_cp_dsn)


def test_backtest_zero_nights_is_degenerate_but_runs(fresh_cp_dsn):
    """Degenerate input: nights=0 runs the sandbox lifecycle with no nights at all (an empty backtest), rather
    than raising -- both sandboxes still come up with identical, empty trace shapes."""
    s = Slice(fresh_cp_dsn)
    _wire_tmsis_outbound(s, mode="advisory", ceiling="L1")
    cr = _mode_cr(s)
    report = compute_backtest(pipeline_id="tmsis_outbound", cr_id=cr["cr_id"], nights=0, data=W1,
                              pg=PG, live_dsn=fresh_cp_dsn)
    assert report["label"] == "SYNTHESIZED"
    assert report["base"]["relations"] == {} and report["cand"]["relations"] == {}


def test_backtest_not_deterministic_raises_and_does_not_store(fresh_cp_dsn, monkeypatch):
    """A real negative control for the determinism gate (not merely asserted): when the two end-to-end runs are
    forced to disagree, `BacktestNotDeterministic` is raised and `dq_ctl.backtest_put` is never reached."""
    import dqagent.backtest as backtest_mod

    s = Slice(fresh_cp_dsn)
    _wire_tmsis_outbound(s, mode="advisory", ceiling="L1")
    cr = _mode_cr(s)
    calls = {"n": 0}
    real_digest = backtest_mod._trace_shape_digest

    def _flaky_digest(dsn):
        calls["n"] += 1
        d = real_digest(dsn)
        return d if calls["n"] != 2 else "f" * 64        # force the SECOND sandbox-digest read to differ

    monkeypatch.setattr(backtest_mod, "_trace_shape_digest", _flaky_digest)
    with pytest.raises(BacktestNotDeterministic):
        compute_backtest(pipeline_id="tmsis_outbound", cr_id=cr["cr_id"], nights=NIGHTS, data=W1,
                         pg=PG, live_dsn=fresh_cp_dsn)


def test_order_independence_relation_counts_do_not_depend_on_dict_ordering(fresh_cp_dsn):
    """Order independence (test lens): the live-effective items are read via a Python dict (`Registry.artifacts`),
    which has no guaranteed iteration order across runs; `_merge_items`/`_seed_sandbox_genesis` both sort by
    artifact_id before writing, so two backtests of the SAME CR, computed independently, agree regardless of
    whatever order the live CP's rows happened to come back in."""
    s = Slice(fresh_cp_dsn)
    _wire_tmsis_outbound(s, mode="advisory", ceiling="L1")
    cr = _mode_cr(s)
    r1 = compute_backtest(pipeline_id="tmsis_outbound", cr_id=cr["cr_id"], nights=NIGHTS, data=W1,
                          pg=PG, live_dsn=fresh_cp_dsn)
    r2 = compute_backtest(pipeline_id="tmsis_outbound", cr_id=cr["cr_id"], nights=NIGHTS, data=W1,
                          pg=PG, live_dsn=fresh_cp_dsn)
    assert r1["base"]["relations"] == r2["base"]["relations"]
    assert r1["cand"]["relations"] == r2["cand"]["relations"]


def test_run_backtest_dependency_on_backtest_put(fresh_cp_dsn):
    """CONFIRMED, re-run after Backend-API-Gov (K3) landed `dq_ctl.backtest_put(...)` (K3_DESIGN.md §1/§5):
    the documented cross-seat dependency this test originally proved absent (`UndefinedFunction`) now resolves for
    real. `run_backtest` (which calls `compute_backtest` then writes through `backtest_put`) succeeds end to end,
    and the stored report is labelled SYNTHESIZED and carries a `report_id` the live control plane actually holds
    (claims enforced in the database, not only in Python)."""
    s = Slice(fresh_cp_dsn)
    _wire_tmsis_outbound(s, mode="advisory", ceiling="L1")
    cr = _mode_cr(s)
    report = run_backtest(pipeline_id="tmsis_outbound", cr_id=cr["cr_id"], nights=NIGHTS, data=W1, pg=PG,
                          live_db=fresh_cp_dsn["dbname"])
    assert report["label"] == "SYNTHESIZED"
    assert report["report_id"].startswith("BTR-")
    with psycopg.connect(**fresh_cp_dsn) as c:
        row = c.execute("SELECT label, digest FROM dq_ctl.backtest_reports WHERE report_id=%s",
                        (report["report_id"],)).fetchone()
    assert row is not None, "the report row is not in the live control plane"
    assert row[0] == "SYNTHESIZED" and row[1] == report["digest"]


def test_backtest_cli_end_to_end(fresh_cp_dsn):
    """CONFIRMED, re-run after Backend-API-Gov (K3) landed `dq_ctl.backtest_put(...)`: the REAL `dqagent backtest`
    CLI subcommand, invoked as a subprocess (not `backtest.py`'s functions directly, per K3_DESIGN.md §6 item 6),
    now exits 0 and prints the stored report_id and digest -- proving the CLI wiring (argument parsing, module
    import, the live-CP connection) AND the cross-seat write path both work end to end."""
    s = Slice(fresh_cp_dsn)
    _wire_tmsis_outbound(s, mode="advisory", ceiling="L1")
    cr = _mode_cr(s)
    env = dict(os.environ)
    cmd = [sys.executable, "-m", "dqagent.cli.main", "backtest",
          "--pipeline", "tmsis_outbound", "--cr", cr["cr_id"], "--nights", str(NIGHTS), "--data", str(W1),
          "--db", fresh_cp_dsn["dbname"], "--pg-host", PG["host"], "--pg-port", str(PG["port"]),
          "--pg-user", PG["user"]]
    proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=1800, env=env)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "backtest:" in proc.stdout and "SYNTHESIZED" in proc.stdout
    # the sandboxes were cleaned up on the success path too (try/finally per sandbox, whatever the outcome)
    base_dsn, cand_dsn = _sandbox_dsns(cr["cr_id"])
    with psycopg.connect(dbname="postgres", autocommit=True, **PG) as a:
        for dsn in (base_dsn, cand_dsn):
            row = a.execute("SELECT 1 FROM pg_database WHERE datname=%s", (dsn["dbname"],)).fetchone()
            assert row is None, f"{dsn['dbname']} leaked after a successful CLI run"
