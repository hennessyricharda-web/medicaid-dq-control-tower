"""T1: dbt builds silver/gold from RELEASED views only; the runner holds T-MSIS gold until upstream units are terminal."""
from pathlib import Path

import pytest

from dqagent.harness import dbt_runner
from dqagent.runtime import Runtime

from .fixtures_w1 import ie_file, person

PROJECT = Path(__file__).resolve().parents[2] / "pipelines" / "w1_dbt"


@pytest.fixture
def rt(fresh_cp_dsn, tmp_path):
    dbfile = str(tmp_path / "dp.duckdb")
    r = Runtime(fresh_cp_dsn, dp_path=dbfile, test_genesis=True)
    r.dbfile = dbfile
    r.tmp = tmp_path
    return r


def _ie(rt, batch, rows, trailer=None):
    p = rt.tmp / f"{batch}.txt"
    p.write_bytes(ie_file(rows, trailer))
    return rt.run_pipeline("p4_eligibility_ie", batch, [("IE", p)], ["bronze.ie_segment"])


def test_gold_tmsis_held_while_upstream_held_then_built(rt):
    _ie(rt, "B1", [person("P1", "M0001"), person("P2", "M0002")], trailer=9)   # held (ABC fail)
    rt.verify_and_publish()
    rt.dp.con.execute("CHECKPOINT")
    out = dbt_runner.run(PROJECT, rt.dbfile, rt.cp.dsn)
    assert out["success"], out
    assert "gold_tmsis_elg_subset" in out["held"]
    assert "gold_tmsis_elg_subset" not in out["built"]
    assert "silver_eligibility_span" in out["built"]
    assert rt.dp.fetchall("SELECT count(*) FROM main_silver.silver_eligibility_span")[0][0] == 0   # nothing released


def test_released_rows_flow_to_gold_and_snap_needs_a_human_then_stays_out_of_tmsis(rt):
    _ie(rt, "B1", [person("P1", "M0001"), person("P2", "M0002", prog="SNAP")])
    rt.verify_and_publish()
    snap = [u for u in rt.units_held() if u["program_code"] == "SNAP"]
    assert len(snap) == 1                          # STRUCTURAL program: never autonomous
    rt.dp.con.execute("CHECKPOINT")
    assert "gold_tmsis_elg_subset" in dbt_runner.run(PROJECT, rt.dbfile, rt.cp.dsn)["held"]
    u = snap[0]
    chal = rt.decisions.challenge(["AUTH_SNAP"], [u["unit_key"]], "RELEASE")
    rt.decisions.decide(["AUTH_SNAP"], rt.idp.token("snap", step_up=True, challenge=chal), [u["unit_key"]], "RELEASE",
                        "Structural checks reviewed for the SNAP segment; no program rules sourced.",
                        {u["unit_key"]: u["state_version"]}, {u["unit_key"]: u["fingerprint"]})
    rt.verify_and_publish()
    rt.dp.con.execute("CHECKPOINT")
    out = dbt_runner.run(PROJECT, rt.dbfile, rt.cp.dsn)
    assert out["success"] and not out["held"], out
    rows = rt.dp.fetchall("SELECT msis_identification_num, program_code FROM main_gold.gold_tmsis_elg_subset")
    assert rows == [("M0001", "MEDICAID")]          # SNAP never enters the T-MSIS extract


def test_advisory_gate_records_would_hold_but_builds(rt):
    """PFP :872, `tests/t1/test_dbt_gate.py::test_advisory_gate_records_would_hold_but_builds`.

    Name/assertion reconsidered against K2_DESIGN.md §9's REVERSED Decision #6 (no real production
    `dbt_runner.run` call site is wired in K2; `gate.gate_verdict` computes advisory from the data-plane manifest,
    never from a live build). The test's LITERAL claim ("but builds") stays true at the mechanism level this test
    actually exercises: `held_models` with an advisory-mode gate excludes NOTHING (advisory gates never enter the
    held set, `dbt_runner.py`'s own `held_models` docstring), so the SAME `dbt_runner.run` this test file's other
    tests already call directly (test infrastructure, not a production call site -- confirmed, B6, zero
    production call sites exist) still builds every model, while `harness.gate.gate_verdict` independently
    records WOULD_HOLD for the gate's own dataset (its unit is HELD, not in the live manifest)."""
    from dqagent.harness.gate import gate_verdict
    _ie(rt, "B1", [person("P1", "M0001"), person("P2", "M0002")], trailer=9)   # held (ABC fail): never released
    rt.verify_and_publish()
    rt.dp.con.execute("CHECKPOINT")
    advisory_gate = {"task_id": "ie_segment_adv", "datasets": ["bronze.ie_segment"], "mode": "advisory",
                     "correlate": "LATEST_SEALED"}
    # advisory gates never enter the held set, whatever their dataset's unit state (held_models' own contract)
    held = dbt_runner.held_models(rt.cp.dsn, gates=[advisory_gate])
    assert "silver_eligibility_span" not in held and not any("ie_segment_adv" == m for m in held)
    out = dbt_runner.run(PROJECT, rt.dbfile, rt.cp.dsn)          # the SAME unmodified call site: nothing excluded
    assert out["success"] and "silver_eligibility_span" in out["built"], out
    # WOULD_HOLD: the gate's own unit never reached the live manifest (it is HELD, an ABC failure)
    assert gate_verdict(rt.dp, advisory_gate, "B1") == "WOULD_HOLD"
    # w1's existing hold_until_terminal behaviour is unchanged when `gates` carries no hold_until_terminal entry
    assert dbt_runner.held_models(rt.cp.dsn) == dbt_runner.held_models(rt.cp.dsn, gates=[advisory_gate])


def test_cot_gold_carries_one_active_row_per_transitive_chain(rt):
    from .fixtures_w1 import acks, x837
    _ie(rt, "B1", [person("P1", "M0001")])
    rt.verify_and_publish()
    claims = [{"id": "E1", "member": "M0001", "lines": [("Z9001", 10.0, "20260110")]},
              {"id": "E2", "member": "M0001", "freq": "7", "f8": "E1", "lines": [("Z9001", 12.0, "20260110")]},
              {"id": "E3", "member": "M0001", "freq": "7", "f8": "E2", "lines": [("Z9001", 14.0, "20260110")]},
              {"id": "E4", "member": "M0001", "lines": [("Z9002", 20.0, "20260111")]}]
    p = rt.tmp / "enc.x12"
    p.write_bytes(x837("ZZMCOA", "000000111", claims))
    a = rt.tmp / "enc.csv"
    a.write_bytes(acks("000000111", ["A"] * 4))
    rt.run_pipeline("p6_claims", "B2", [("X12_837P", p, "ZZ-MCO-A", a)], ["bronze.encounter_header", "bronze.encounter_line"])
    rt.verify_and_publish()
    with rt.cp.as_role("dq_eval_svc") as c:
        held = c.execute("SELECT * FROM dq_ctl.units WHERE state='HELD' AND node='bronze.encounter_header'").fetchall()
    for u in held:                                   # codeset ADVISORY floor ⇒ one human (H_IN)
        uk = u["unit_key"]
        chal = rt.decisions.challenge(["AUTH_MEDICAID"], [uk], "RELEASE", {uk: []})
        rt.decisions.decide(["AUTH_MEDICAID"], rt.idp.token("maker", step_up=True, challenge=chal), [uk], "RELEASE",
                            "Codeset registry not loaded in W1; claims reviewed manually.",
                            {uk: u["state_version"]}, {uk: u["fingerprint"]}, {uk: []})
    rt.verify_and_publish()
    rt.dp.con.execute("CHECKPOINT")
    out = dbt_runner.run(PROJECT, rt.dbfile, rt.cp.dsn)
    assert out["success"] and "gold_tmsis_cot_subset" in out["built"], out
    rows = sorted(rt.dp.fetchall("SELECT icn_orig, icn_adj, tot_bill_amt FROM main_gold.gold_tmsis_cot_subset"))
    assert [(r[0], r[1], float(r[2])) for r in rows] == [("E1", "E3", 14.0), ("E4", "E4", 20.0)]
