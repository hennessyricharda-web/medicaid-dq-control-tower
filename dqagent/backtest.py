"""Part F P1-4 (K3): backtest a change request (CR) against disposable sandbox control planes, before promoting a
TASK_GATE's `mode` from `advisory` to `block`. PFP `:898-904`; `docs/EXECUTION_PLAN.md:337-346`;
`docs/design/packages/K3_DESIGN.md`.

Mechanics only (orchestrator Decision 1, K3_DESIGN.md §6): this module measures what the CURRENT codebase already
does. It never adds real block-mode enforcement (`runtime.py`, `evaluate.py` and `modes.py` are untouched, per the
critical scope boundary). A CR that changes only a TASK_GATE's `mode` therefore produces IDENTICAL release / hold /
pending counts between the `base` and `cand` sandbox -- confirmed by direct grep of this tree: `content["mode"]` is
read only by the database's own `gov_item_valid` CHECK and `gov_item_lower_only`'s unchanged-mode comparison, never
by `harness/gate.py`, `runtime.py::_pipeline_run`, or `evaluate.py`/`modes.py`. The report says so plainly, in its
own `delta_note` field, for a mode-only CR -- never implying a causal block that is not real yet.

Sandbox lifecycle (PFP :898-904, K3_DESIGN.md §0.6-0.7): two disposable, single-use Postgres databases per backtest
attempt, `dqbt_<sanitized-cr>_base` / `_cand`. K3-4 (a correctness requirement, not a style preference): CR ids are
`"CR-" + uuid4().hex[:10]` (`registry.py:632`) -- the literal hyphen is stripped; only the hex suffix is used in a
database name (`sanitize_cr_id`). Each sandbox is schema-applied fresh, then pre-seeded with its own genesis item
list by `_seed_sandbox_genesis` (mirroring `Registry._write_test_genesis`'s body, `registry.py:848-866`, exactly --
NOT calling it: `registry.py` is read-only for this seat this package, Decision K3-2, and `_write_test_genesis` has
no parameter to override its item source). `base` gets the live control plane's effective items unchanged; `cand`
gets the same, plus the CR's own items layered on top (PFP :900). Each sandbox is then bound TEST/test the
ordinary way: constructing `Runtime(..., test_genesis=True, deployment="test")` sees the pre-seeded rows already
present, no-ops `_write_test_genesis`'s own seeding step, and still runs `bind_deployment` -- zero new binding
mechanism, exactly as K3_DESIGN.md §0.6 found.

Trace shape (K3_DESIGN.md §6 item 2, CORRECTED 2026-10-27 after this seat stopped before writing code and reported
the first resolution didn't hold -- independently re-verified by the orchestrator against both files before
accepting the correction). The first resolution assumed `tracer.root`'s auto-parent-to-enclosing-root default
(`tracer.py:352-354`) would nest each night's NIGHT root under this module's own BACKTEST root; it does not,
because `Runtime.run_night` (`runtime.py:1622-1632`) always passes `parent_trace_id` explicitly (`None` on a fresh
night, or a prior attempt's trace id on resumption), so the sentinel-based auto-parenting never fires. Corrected:
each sandbox control plane is disposable and single-use, written to by nothing but this one backtest attempt, so
the trace-shape digest does not need a parent/child tree walk at all -- it is defined over EVERY row the sandbox's
own `dq_ctl.runs` / `run_events` / `spans` / `release_checks` / `guardrail_events` tables ever recorded
(`_trace_shape_digest`, generalizing the exact query shape `tests/t1/test_trace_shape.py::_shape` already proves
stable). A `tracer.root("BACKTEST", ...)` span is still opened around the N `run_night` calls (the top-level trace
id and attribute holder for the backtest run itself); each night's own trace id is recorded on it via `Span.ref()`
for human/debugging traceability only -- never used by the digest, and never allowed to affect the outcome (I-8:
a ref-recording failure is caught and ignored, exactly like every other tracer call site in this codebase).

Determinism (PFP :903): `compute_backtest` runs the WHOLE sandbox-pair process (build, seed, run N nights, measure,
drop) TWICE, end to end, and returns a report only when the two runs' digests match exactly. A mismatch raises
`BacktestNotDeterministic` -- loud, not a silent pick of one run. `compute_backtest` never writes to the live
control plane (it only READS the live-effective items and the CR's own items); `run_backtest` adds exactly one
write, through `dq_ctl.backtest_put(...)` (Backend-API-Gov's SQL function; see `_store_report`'s docstring for the
cross-seat dependency this call site carries).
"""
from __future__ import annotations

import hashlib
import json
import re
import uuid
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

from dqagent.cp import schema
from dqagent.engine.cp import CP
from dqagent.governance.registry import Registry, cfg_from_artifacts, ruleset
from dqagent.obs import tracer
from dqagent.rules.ir import canonical
from dqagent.runtime import Runtime, synthetic_nights

MODE_ONLY_DISCLOSURE = (
    "This change does not alter release/hold/pending outcomes in this backtest; `block` mode is not yet "
    "behaviorally enforced (the gate's `mode` field is read only by the database's own validity and lower-only "
    "checks, never by the evaluation or publish path -- confirmed by direct grep of this tree). The mechanics "
    "below (sandbox comparison, the determinism double-run, the evidence linkage) are real; the release/hold/"
    "pending delta is legitimately zero for a mode-only change, not a measurement failure."
)
CR_SUFFIX_RE = re.compile(r"^[0-9a-f]{1,32}$")
_RELEVANT_STATES = ("RELEASED", "HELD", "PENDING_DEPENDENCY")


class BacktestNotDeterministic(RuntimeError):
    """The same backtest, run twice end to end over identical inputs, produced two different digests (PFP :903).
    Never stored, never silently resolved by picking one of the two runs."""


# -- CR id sanitization (K3-4: a correctness requirement, re-grounded against registry.py:632) ----------------------
def sanitize_cr_id(cr_id: str) -> str:
    """A CR id is `"CR-" + uuid.uuid4().hex[:10]` (`registry.py:632`) -- a literal hyphen, unsafe to interpolate
    into `CREATE DATABASE dqbt_<cr>_base/_cand` unsanitized (Postgres has no parameterized DDL identifiers). Strips
    the `CR-` prefix and returns only the hex suffix; fails closed (ValueError) on anything else, rather than
    attempting to quote the full id."""
    if not isinstance(cr_id, str) or not cr_id.startswith("CR-"):
        raise ValueError(f"not a change-request id: {cr_id!r} (expected CR-<hex>)")
    suffix = cr_id[3:].lower()
    if not CR_SUFFIX_RE.fullmatch(suffix):
        raise ValueError(f"change-request id {cr_id!r} has a suffix unsafe for a database name "
                         "(expected lowercase hex only)")
    return suffix


def _sandbox_names(cr_id: str) -> tuple[str, str]:
    s = sanitize_cr_id(cr_id)
    return f"dqbt_{s}_base", f"dqbt_{s}_cand"


# -- reading the LIVE control plane (reads only; never written to except via _store_report) -------------------------
def _live_effective_items(live_dsn: dict, state: str) -> list[dict]:
    """The live control plane's effective items, in the {artifact_id, kind, op, content} shape a genesis item list
    needs (Registry.artifacts, read-only; `governance/registry.py:534-542`). `Registry.artifacts` folds a
    DELETEd artifact into the returned dict too (op='DELETE', content=None, `registry.py:541-542`) -- that
    artifact is correctly absent from the EFFECTIVE configuration (`cfg_from_artifacts` only reads PUT content),
    so it is dropped here rather than re-seeded: `_seed_sandbox_genesis` (mirroring `_write_test_genesis`'s own
    body exactly) always writes a literal 'PUT' row, and a fresh sandbox's genesis has nothing to delete."""
    reg = Registry(CP(live_dsn), jwks_provider=lambda: {"keys": []})
    arts = reg.artifacts(state)
    return [{"artifact_id": a, "kind": v["kind"], "op": "PUT", "content": v["content"]}
            for a, v in sorted(arts.items()) if v["op"] == "PUT"]


def _cr_items(live_dsn: dict, cr_id: str) -> list[dict]:
    """The CR's own items (`dq_ctl.config_change_requests.items`, K3_DESIGN.md §0.2), read as dq_registry_svc --
    the role governance_schema.py:471 already grants SELECT on this table to."""
    with CP(live_dsn).as_role("dq_registry_svc") as c:
        row = c.execute("SELECT items FROM dq_ctl.config_change_requests WHERE cr_id=%s", (cr_id,)).fetchone()
    if row is None:
        raise ValueError(f"unknown change request {cr_id!r}")
    return row["items"]


def _merge_items(base_items: list[dict], overlay_items: list[dict]) -> list[dict]:
    """PFP :900: the candidate gets the live effective items plus the CR's own items, layered on top (CR wins on a
    shared artifact_id). A DELETE in the overlay drops that artifact from the candidate entirely."""
    by_id = {it["artifact_id"]: it for it in base_items}
    for it in overlay_items:
        if it["op"] == "DELETE":
            by_id.pop(it["artifact_id"], None)
        else:
            by_id[it["artifact_id"]] = {"artifact_id": it["artifact_id"], "kind": it["kind"], "op": it["op"],
                                        "content": it.get("content")}
    return [by_id[k] for k in sorted(by_id)]


def _is_mode_only_cr(live_items_by_id: dict[str, dict], cr_items: list[dict]) -> bool:
    """True exactly when every item in the CR is a TASK_GATE PUT that changes `content.mode` and nothing else
    (the one case the honest-disclosure field (`MODE_ONLY_DISCLOSURE`) fires for -- K3_DESIGN.md §6 item 1: this
    must not blanket every CR, only a genuinely mode-only one)."""
    if not cr_items:
        return False
    for it in cr_items:
        if it.get("kind") != "TASK_GATE" or it.get("op") != "PUT":
            return False
        old = (live_items_by_id.get(it["artifact_id"]) or {}).get("content")
        new = it.get("content")
        if not isinstance(old, dict) or not isinstance(new, dict):
            return False
        if old.get("mode") == new.get("mode"):
            return False
        if {k: v for k, v in old.items() if k != "mode"} != {k: v for k, v in new.items() if k != "mode"}:
            return False
    return True


# -- sandbox genesis seeding (Decision K3-2: a sibling of Registry._write_test_genesis, not a call into it) ---------
def _seed_sandbox_genesis(admin_dsn: dict, state: str, items: list[dict]) -> None:
    """Mirrors `Registry._write_test_genesis`'s body (`registry.py:848-866`) exactly, except the item list is this
    module's own candidate (the live-effective items, or the live-effective items plus the CR's own items) instead
    of `seed_from_files`. `registry.py` is read-only for this seat this package (K3_DESIGN.md §5); it has no
    parameter to override its item source, so this is a sibling function, not an edit."""
    cfg = cfg_from_artifacts(state, {i["artifact_id"]: i for i in items})
    if cfg is None:
        raise ValueError("the candidate configuration is missing profile / feeds / bindings; refusing to seed")
    rs = ruleset(state, cfg)
    with psycopg.connect(**admin_dsn) as c:
        if c.execute("SELECT 1 FROM dq_ctl.config_artifacts WHERE state=%s LIMIT 1", (state,)).fetchone():
            return
        for it in items:
            c.execute("INSERT INTO dq_ctl.config_artifacts(artifact_id,version,state,kind,op,content,content_hash,"
                      "cr_id) VALUES (%s,1,%s,%s,'PUT',%s::jsonb,%s,'TEST-GENESIS')",
                      (it["artifact_id"], state, it["kind"], json.dumps(it["content"], default=str),
                       hashlib.sha256(canonical(it.get("content")).encode()).hexdigest()))
        for k, v in sorted(rs["rules"].items()):
            c.execute("INSERT INTO dq_ctl.registry_events(artifact_kind,artifact_id,semantic_hash,state,"
                      "decision_ref,rule_meta) VALUES ('rule',%s,%s,'APPROVED','TEST-GENESIS',%s::jsonb)",
                      (k, v["hash"], json.dumps({kk: v[kk] for kk in ("status", "gating", "relation")})))
        c.execute("SELECT dq_ctl._introduce_datasets(%s, %s::jsonb, 'TEST-GENESIS')",
                  (state, json.dumps(items, default=str)))
        c.commit()


# -- sandbox report reads: the plain admin connection, mirroring the proven tests/t1/test_trace_shape.py::_q /
#    ::_shape pattern (a disposable, single-use sandbox database -- there is no "wrong role" to worry about reading
#    from it, and this exact pattern is already independently confirmed working against FORCE-RLS tables by
#    test_trace_shape_digest_stable_across_two_fresh_cps, re-run by this seat before writing this module) -----------
def _q(dsn: dict, sql: str, *a) -> list[dict]:
    with psycopg.connect(**dsn, row_factory=dict_row) as c:
        return [dict(r) for r in c.execute(sql, a).fetchall()]


def _relation_counts(dsn: dict) -> dict[str, dict[str, int]]:
    rows = _q(dsn, "SELECT node, state, count(*) n FROM dq_ctl.units WHERE state = ANY(%s) GROUP BY node, state",
             list(_RELEVANT_STATES))
    out: dict[str, dict[str, int]] = {}
    for r in rows:
        out.setdefault(r["node"], {})[r["state"]] = r["n"]
    return out


def _rule_hit_rates(dsn: dict) -> dict[str, dict]:
    rows = _q(dsn, "SELECT rule_id, disposition FROM (SELECT DISTINCT ON (unit_key, rule_id) rule_id, disposition "
                  "FROM dq_ctl.results ORDER BY unit_key, rule_id, eval_version DESC, id DESC) latest")
    out: dict[str, dict] = {}
    for r in rows:
        agg = out.setdefault(r["rule_id"], {"evaluated": 0, "failed": 0})
        agg["evaluated"] += 1
        if r["disposition"] == "FAIL":
            agg["failed"] += 1
    for agg in out.values():
        agg["hit_rate"] = round(agg["failed"] / agg["evaluated"], 4) if agg["evaluated"] else 0.0
    return dict(sorted(out.items()))


def _held_reason_histogram(dsn: dict) -> dict[str, int]:
    rows = _q(dsn, "SELECT DISTINCT ON (e.unit_key) e.via, e.payload FROM dq_ctl.unit_events e "
                  "JOIN dq_ctl.units u USING (unit_key) WHERE e.to_state='HELD' AND u.state='HELD' "
                  "ORDER BY e.unit_key, e.event_id DESC")
    hist: dict[str, int] = {}
    for r in rows:
        why = Runtime._held_reason(r["via"], r["payload"] or {})
        hist[why] = hist.get(why, 0) + 1
    return dict(sorted(hist.items()))


def _would_hold_count(dsn: dict, pipeline_id: str) -> int:
    rows = _q(dsn, "SELECT count(*) n FROM dq_ctl.gate_checks WHERE pipeline_id=%s AND verdict='WOULD_HOLD'",
             pipeline_id)
    return rows[0]["n"]


def _trace_shape_digest(dsn: dict) -> str:
    """Every row this (disposable, single-use) sandbox's own trace tables ever recorded -- K3_DESIGN.md §6 item 2's
    corrected definition: no parent/child tree walk, the exact query shape `test_trace_shape.py::_shape` proves
    stable, generalized from "this run" to "this whole, single-use sandbox CP"."""
    doc = {
        "runs": _q(dsn, "SELECT trace_id, kind, parent_trace_id, state, attempt, night_date::text, authorities "
                       "FROM dq_ctl.runs ORDER BY trace_id"),
        "events": _q(dsn, "SELECT trace_id, event, error_class FROM dq_ctl.run_events ORDER BY trace_id, id"),
        "spans": _q(dsn, "SELECT trace_id, span_id, parent_span_id, ordinal, name, seat, subject, authorities, "
                       "status, attrs::text, refs FROM dq_ctl.spans ORDER BY trace_id, span_id"),
        "checks": _q(dsn, "SELECT span_id, unit_key, ok, reasons FROM dq_ctl.release_checks ORDER BY id"),
        "guardrails": _q(dsn, "SELECT trace_id, span_id, code, subject, detail::text FROM dq_ctl.guardrail_events "
                             "ORDER BY id"),
    }
    return hashlib.sha256(json.dumps(doc, sort_keys=True, default=str).encode()).hexdigest()


def _inputs_digest(data: Path, nights: list[tuple[str, list[Path]]], cr_id: str, cr_items: list[dict]) -> str:
    """A hash of the synthetic data actually used (every file in the N nights run) plus the CR's own items (PFP
    :902, "inputs_digest")."""
    files_meta = sorted((str(p.relative_to(data)), p.stat().st_size, hashlib.sha256(p.read_bytes()).hexdigest())
                        for _, files in nights for p in files)
    doc = {"files": files_meta, "cr_id": cr_id, "cr_items": cr_items}
    return hashlib.sha256(canonical(doc).encode()).hexdigest()


def _deltas(base: dict, cand: dict) -> dict:
    rel_keys = sorted(set(base["relations"]) | set(cand["relations"]))
    rel_delta = {}
    for rel in rel_keys:
        b, c = base["relations"].get(rel, {}), cand["relations"].get(rel, {})
        states = sorted(set(b) | set(c))
        rel_delta[rel] = {st: c.get(st, 0) - b.get(st, 0) for st in states}
    return {"relations": rel_delta, "would_hold": cand["would_hold"] - base["would_hold"]}


# -- one sandbox's full lifecycle: create, apply schema, seed, run N nights under a BACKTEST root, measure, drop ---
def _run_sandbox(pg: dict, db_name: str, state: str, items: list[dict], data: Path, nights_n: int,
                 pipeline_id: str, label: str) -> tuple[dict, str, list[tuple[str, list[Path]]]]:
    admin_dsn = dict(dbname=db_name, **pg)
    with psycopg.connect(dbname="postgres", autocommit=True, **pg) as a:
        a.execute(f"DROP DATABASE IF EXISTS {db_name} WITH (FORCE)")
        a.execute(f"CREATE DATABASE {db_name}")
    try:
        with psycopg.connect(**admin_dsn) as c:
            schema.apply(c)
        _seed_sandbox_genesis(admin_dsn, state, items)
        rt = Runtime(admin_dsn, state=state, dp_path=":memory:", test_genesis=True, deployment="test")
        all_nights = synthetic_nights(data, rt.routing)
        run_nights = all_nights[:nights_n] if nights_n else []
        # trace id prefix BKT (TRACE_ID_RE needs 3-5 uppercase letters before the first hyphen, trace_schema.py:59;
        # "BT-" alone is only 2 and was caught and fixed before this module was first run)
        tid = tracer.mint(rt.cp, f"BKT-{state}-{label}") if tracer.enabled(rt.deployment) else None
        with tracer.root("BACKTEST", tid, parent_trace_id=None, **rt._root_args()):
            for i, (night, files) in enumerate(run_nights):
                with tracer.span(f"night:{night}", seat="REGISTRY", subject=f"nightly:{night}") as sp:
                    rt.run_night(data, night, files, last=(i == len(run_nights) - 1))
                    # the backtest's own pipeline is backtested regardless of its genesis `run_on` value (the
                    # demo's own tmsis_outbound wiring uses run_on: manual, confirmed
                    # tests/t1/test_pipelines_runtime.py:24 -- run_night only auto-calls _pipeline_run for
                    # run_on: night_end, PFP :825-828). `_pipeline_run` is this seat's read-only-reference
                    # infrastructure per the brief (runtime.py:1717-1752): called as-is, never modified.
                    rt._pipeline_run(pipeline_id, night)
                    try:                                       # I-8: a ref-recording failure never blocks anything
                        ntid = tracer.last_run(rt.cp, "NIGHT", night_date=night)
                        if ntid:
                            sp.ref(f"trace:{ntid}")
                    except Exception as e:                      # noqa: BLE001 - observe only
                        tracer._degrade("backtest:night_ref", e)
        report = {
            "relations": _relation_counts(admin_dsn),
            "rule_hit_rates": _rule_hit_rates(admin_dsn),
            "held_reasons": _held_reason_histogram(admin_dsn),
            "would_hold": _would_hold_count(admin_dsn, pipeline_id),
        }
        digest = _trace_shape_digest(admin_dsn)
        return report, digest, run_nights
    finally:
        with psycopg.connect(dbname="postgres", autocommit=True, **pg) as a:
            a.execute(f"DROP DATABASE IF EXISTS {db_name} WITH (FORCE)")


def _run_once(pg: dict, live_dsn: dict, state: str, pipeline_id: str, cr_id: str, nights: int, data: Path,
             base_name: str, cand_name: str) -> dict:
    live_items = _live_effective_items(live_dsn, state)
    live_by_id = {it["artifact_id"]: it for it in live_items}
    cr_items = _cr_items(live_dsn, cr_id)
    cand_items = _merge_items(live_items, cr_items)
    mode_only = _is_mode_only_cr(live_by_id, cr_items)
    base_report, base_digest, base_nights = _run_sandbox(pg, base_name, state, live_items, data, nights,
                                                         pipeline_id, "base")
    cand_report, cand_digest, _cand_nights = _run_sandbox(pg, cand_name, state, cand_items, data, nights,
                                                          pipeline_id, "cand")
    report = {
        "cr_id": cr_id, "pipeline_id": pipeline_id, "state": state, "nights": nights,
        "base": base_report, "cand": cand_report, "deltas": _deltas(base_report, cand_report),
        "trace_shape_digest_base": base_digest, "trace_shape_digest_cand": cand_digest,
        "inputs_digest": _inputs_digest(data, base_nights, cr_id, cr_items),
        "delta_note": MODE_ONLY_DISCLOSURE if mode_only else None,
    }
    report["digest"] = hashlib.sha256(canonical(report).encode()).hexdigest()
    return report


# -- public API --------------------------------------------------------------------------------------------------
def compute_backtest(*, pipeline_id: str, cr_id: str, nights: int, data: str | Path, state: str = "ZZ",
                     pg: dict, live_dsn: dict) -> dict:
    """Builds both sandboxes, runs N nights (truncated to what `data` has) in each TWICE end to end, and returns
    the determinism-checked report (PFP :902-903). Reads the live control plane (the effective items, and the CR's
    own items); never writes to it. Raises `BacktestNotDeterministic` if the two runs disagree, `ValueError` on a
    bad `cr_id` or `nights` < 0, or if the named CR does not exist."""
    if not isinstance(nights, int) or nights < 0:
        raise ValueError(f"nights must be a non-negative integer, got {nights!r}")
    data = Path(data)
    base_name, cand_name = _sandbox_names(cr_id)      # K3-4: raises ValueError closed on an unsafe cr_id
    attempts = [_run_once(pg, live_dsn, state, pipeline_id, cr_id, nights, data, base_name, cand_name)
               for _ in range(2)]
    if attempts[0]["digest"] != attempts[1]["digest"]:
        raise BacktestNotDeterministic(
            f"backtest for {cr_id} produced two different digests across two identical runs "
            f"({attempts[0]['digest'][:16]} != {attempts[1]['digest'][:16]}); refusing to store")
    report = attempts[0]
    report["label"] = "SYNTHESIZED"
    return report


def _store_report(live_dsn: dict, report: dict) -> str:
    """Writes the report to the LIVE control plane through `dq_ctl.backtest_put(...)` ONLY (PFP :904, SECURITY
    DEFINER, `dq_api` grantee) -- the one write path this module ever uses against the live CP.

    CROSS-SEAT DEPENDENCY (per this seat's brief: "write your Python call site against the signature PFP
    describes and report the dependency; do not stub or fake the write path yourself"): `dq_ctl.backtest_put(...)`
    is Backend-API-Gov's function to build (`dqagent/cp/governance_schema.py`, K3_DESIGN.md §1/§5); confirmed
    ABSENT from the tree as of this writing (grep: zero matches for `backtest_put` anywhere under `dqagent/`).
    This call site is written against PFP's own description (`:904`, "writes... only through
    dq_ctl.backtest_put(...) (dq_api, SECURITY DEFINER)") and WILL raise `psycopg.errors.UndefinedFunction` until
    Backend-API-Gov lands that function. This is not a defect of this module; it is the documented dependency."""
    report_id = f"BTR-{uuid.uuid4().hex[:12]}"
    with CP(live_dsn).as_role("dq_api") as c:
        c.execute("SELECT dq_ctl.backtest_put(%s,%s,%s::jsonb,%s,%s) AS id",
                  (report_id, report["cr_id"], json.dumps(report, default=str), report["label"], report["digest"]))
    return report_id


def run_backtest(*, pipeline_id: str, cr_id: str, nights: int, data: str | Path, state: str = "ZZ", pg: dict,
                 live_db: str) -> dict:
    """`compute_backtest(...)`, then writes the result to the live control plane through `dq_ctl.backtest_put(...)`
    only (PFP :904). This is the function the `backtest` CLI subcommand calls."""
    live_dsn = dict(dbname=live_db, **pg)
    report = compute_backtest(pipeline_id=pipeline_id, cr_id=cr_id, nights=nights, data=data, state=state, pg=pg,
                              live_dsn=live_dsn)
    report["report_id"] = _store_report(live_dsn, report)
    return report
