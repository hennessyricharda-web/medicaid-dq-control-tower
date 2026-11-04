"""Advisory dbt gate verdicts (Part F P1-3, K2b; PFP :816-820).

`gate_verdict(dp, gate, run_key)` reads the DATA-PLANE manifest only (`dq_rel.manifest` /
`dq_rel.manifest_pointer`, the same objects `intervention/publish.py:64-69` owns, and the same
"live manifest" pattern `api/app.py:1236-1238`'s `live_manifest()` already uses) -- never the
control plane, and never a live dbt invocation. K2 is advisory-only (block-mode promotion is K3's
scope, per EXECUTION_PLAN.md's own K3 description): the only two verdicts this module can produce
are PASS and WOULD_HOLD, matching `trace_schema.ATTR_VOCAB["gate_verdict"]`'s K2-scoped
reservation exactly (K2b-3, RESOLVED: no real HOLD value exists yet in K2).

Correlation (PFP :818, "correlation LATEST_SEALED, otherwise HOLD"): the gate's own `correlate`
field names how a run is matched to batches. Every gate `gov_item_valid` accepts today requires
`correlate = 'LATEST_SEALED'` (confirmed, `governance_schema.py`'s TASK_GATE branch); any other
value is a shape this module has never been asked to support, and it fails closed to WOULD_HOLD
(this package's advisory analogue of PFP's "otherwise HOLD") rather than guessing a match.

K2b-2 (REVERSED, K2_DESIGN.md §9): this module computes the verdict from the COMMITTED data-plane
manifest state only. It never invokes `dbtRunner()` and never calls `dbt_runner.run`; the
underlying dbt BUILD EXECUTION stays deferred to K3 (which is also where a real `block`-mode
promotion -- the first thing that would actually need a build gated on this verdict -- is in
scope)."""
from __future__ import annotations


def gate_verdict(dp, gate: dict, run_key: str) -> str:
    """PASS when every unit this gate's `datasets` sealed under batch `run_key` (LATEST_SEALED) is in the live
    manifest version; WOULD_HOLD otherwise -- fail closed, including when `correlate` is not LATEST_SEALED, the
    gate names no datasets, or a named dataset has never sealed any unit under this batch at all (nothing yet to
    confirm released).

    dp: a DataPlane (DuckDB at T1). Only read: `dq_rel.manifest`/`dq_rel.manifest_pointer`, and the gated relation
        tables named in `gate["datasets"]` (to find which unit_keys this batch sealed into each one). Writes none.
    gate: a TASK_GATE's content (PFP :748): {task_id, datasets[], mode, correlate, ...}. Only `datasets` and
          `correlate` are read here; `mode`/`on_timeout`/`release_ceiling` are ORCH-1's business, not this
          function's.
    run_key: PFP's own LATEST_SEALED convention -- at T1, the batch_id this run corresponds to."""
    if gate.get("correlate") != "LATEST_SEALED":
        return "WOULD_HOLD"
    datasets = gate.get("datasets") or []
    if not datasets:
        return "WOULD_HOLD"
    live = {r[0] for r in dp.fetchall(
        "SELECT unit_key FROM dq_rel.manifest WHERE version = "
        "(SELECT version FROM dq_rel.manifest_pointer WHERE id = 1)")}
    for ds in datasets:
        try:
            rows = dp.fetchall(f"SELECT DISTINCT _dq_unit_key FROM {ds} WHERE _dq_batch_id = ?", [run_key])
        except Exception:                       # noqa: BLE001 - the dataset has never sealed anything: fail closed
            return "WOULD_HOLD"
        units = {r[0] for r in rows}
        if not units or not units.issubset(live):
            return "WOULD_HOLD"
    return "PASS"
