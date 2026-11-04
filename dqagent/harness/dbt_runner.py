"""dbt runner gate (DESIGN §4.4).

HELD nodes are EXCLUDED from the `dbt build` selection, together with their descendants.
There are no pre-hooks: a failing pre-hook would error the model rather than skip it. The
released views remain the guarantee; this gate gives node-level ordering for models declared
`hold_until_terminal` (e.g. T-MSIS gold, which must not be built from a partial batch).
"""
from __future__ import annotations

import os
from pathlib import Path

import psycopg

HOLD_UNTIL_TERMINAL = {
    "gold_tmsis_elg_subset": ["bronze.ie_segment"],
    "gold_tmsis_cot_subset": ["bronze.encounter_header", "bronze.ie_segment"],
}


def held_models(cp_dsn: dict, gates: list[dict] | None = None) -> dict[str, list[str]]:
    """Part F P1-3 (K2b, PFP :816): `gates` is the TASK_GATE content list (task_id, datasets, mode), additional to
    the hard-coded `HOLD_UNTIL_TERMINAL`. Only `hold_until_terminal` gates name a dbt MODEL to hold (their task_id
    is the model name, e.g. `taskgate:w1_dbt:gold_tmsis_elg_subset`'s task_id `gold_tmsis_elg_subset`, PFP :807);
    `block` gates (the four w1 bronze relations) name no dbt model at all in the w1 project -- bronze is sealed
    directly, never dbt-built -- so they contribute nothing here, by construction, not by omission. With
    `gates=None` (the default, every existing call site), this is byte-for-byte the pre-K2 behaviour: the SAME
    held set for w1 (confirmed by `test_dbt_gate.py`'s 3 existing tests, unmodified)."""
    with psycopg.connect(**cp_dsn) as c:
        nonterm = {r[0] for r in c.execute(
            "SELECT DISTINCT node FROM dq_ctl.units WHERE state NOT IN ('RELEASED','REJECTED','SUPERSEDED')").fetchall()}
    held_until_terminal = dict(HOLD_UNTIL_TERMINAL)
    for g in gates or []:
        if g.get("mode") == "hold_until_terminal" and g.get("task_id"):
            held_until_terminal[g["task_id"]] = list(g.get("datasets") or [])
    return {m: [n for n in nodes if n in nonterm] for m, nodes in held_until_terminal.items()
            if any(n in nonterm for n in nodes)}


def run(project_dir: Path, duckdb_path: str, cp_dsn: dict) -> dict:
    from dbt.cli.main import dbtRunner
    held = held_models(cp_dsn)
    args = ["build", "--project-dir", str(project_dir), "--profiles-dir", str(project_dir)]
    if held:
        args += ["--exclude", *[f"{m}+" for m in held]]
    os.environ["DQ_DUCKDB_PATH"] = duckdb_path
    res = dbtRunner().invoke(args)
    built = []
    if res.result is not None:
        built = [r.node.name for r in res.result.results if str(r.status) in ("success", "pass")]
    return {"success": bool(res.success), "held": held, "built": built}
