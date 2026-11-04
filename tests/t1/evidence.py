"""R4-5 test helpers: record eval runs, propose and confirm an evidence bundle, set the promotion minimums.

Everything goes through the paths the product uses: eval runs through dq_ctl.eval_run_record (the eval service),
attestations through dq_ctl.eval_attest / eval_attest_confirm (a named governance person through the api role),
minimums through a governed POLICY change. Nothing writes the tables directly.

Part F K0 (Decision D5): `record_run`'s DETERMINISTIC/default path delegates to the product recorder,
`dqagent.eval.recorder.run_suite` (see that module's own docstring) -- there is one product path, not two, for an
honest run recording its own truth. This helper still exists, and still writes `eval_runs` directly, for callers
that need a SCRIPTED/TEMPLATE/LIVE row, or a specific (possibly forged-looking) cases/passed/code/prompt_version
shape, on demand -- including the NEGATIVE evidence-gate tests that prove the database refuses such a row as
evidence. Those cases cannot go through `run_suite` (which derives everything from a real run); they call the
database function directly, as `dq_eval_svc`, exactly as before."""
from __future__ import annotations

import hashlib
import uuid
from pathlib import Path

from dqagent.llm import PROMPT_VERSION
from dqagent.obs import tracer

_STUB_SUITE = Path(__file__).resolve().parent / "_stub_agent_eval_det_suite.py"


def _record_run_direct(s, *, suite, source_mode, cases, passed, code, prompt_version, run_id) -> str:
    """The direct writer: unconditionally inserts the exact row asked for, bypassing any real suite run. Used by
    the default/DETERMINISTIC path below ONLY when the caller overrides something a real run cannot honestly
    produce (a custom run_id, cases, or an explicit code/passed), and always for SCRIPTED/TEMPLATE/LIVE/forged rows."""
    run_id = run_id or f"run-{uuid.uuid4().hex[:10]}"
    junit = hashlib.sha256(run_id.encode()).hexdigest()
    with s.cp.as_role("dq_eval_svc") as c:
        c.execute("SELECT dq_ctl.eval_run_record(%s,%s,%s,%s,%s,%s,%s,%s)",
                  (run_id, suite, source_mode, cases, cases if passed is None else passed, code or tracer.code_sha256(),
                   prompt_version, junit))
    return run_id


def record_run(s, *, suite="agent_eval", source_mode="DETERMINISTIC", cases=12, passed=None, code=None,
               prompt_version=None, run_id=None) -> str:
    """The literal DETERMINISTIC/default call -- `record_run(s)`, agent_eval, a fully-passing run of the running
    code, no overrides -- delegates to the product recorder (`dqagent.eval.recorder.run_suite`), which actually
    runs a suite (a real pytest subprocess, never simulated) and derives its own source_mode; the same mechanism
    `dqagent eval run` uses. It runs a small fixed 12-case stub (`_stub_agent_eval_det_suite.py`), not the real
    `agent_eval` suite, so every existing caller's recorded shape (12 cases, 0 failures) is exactly preserved --
    the real suite's case count is not a stable constant to pin a test fixture to, and running it on every
    DETERMINISTIC `record_run()` call would also be needlessly slow. Any OTHER shape (a non-DETERMINISTIC
    source_mode, a non-default suite, or an explicit cases/passed/code/run_id override -- needed by the negative
    evidence-gate tests that must construct SCRIPTED/TEMPLATE/LIVE or forged-looking rows) writes the row
    directly, as before: `run_suite` cannot honestly produce a forged or non-real-run shape, by design."""
    direct_only = (source_mode != "DETERMINISTIC" or suite != "agent_eval" or passed is not None or code is not None
                   or run_id is not None or cases != 12)
    if direct_only:
        return _record_run_direct(s, suite=suite, source_mode=source_mode, cases=cases, passed=passed, code=code,
                                  prompt_version=prompt_version, run_id=run_id)
    from dqagent.eval.recorder import run_suite
    row = run_suite(s.cp, suite, paths=[str(_STUB_SUITE)], prompt_version=prompt_version)
    return row["run_id"]


def attest(s, det_run_id, llm_run_id=None, *, proposer="E100", code=None, label="suite-r45") -> int:
    with s.cp.as_role("dq_api") as c:
        return c.execute("SELECT dq_ctl.eval_attest(%s,%s,%s,%s,%s) i",
                         (label, code or tracer.code_sha256(), det_run_id, llm_run_id, proposer)).fetchone()["i"]


def confirm(s, att_id, confirmer="E200") -> None:
    with s.cp.as_role("dq_api") as c:
        c.execute("SELECT dq_ctl.eval_attest_confirm(%s,%s)", (att_id, confirmer))


def grant_evidence(s, *, llm=False) -> int:
    """A confirmed bundle for the RUNNING code: deterministic, and (llm=True) a LIVE explainer half."""
    det = record_run(s)
    lr = record_run(s, suite="explainer", source_mode="LIVE", prompt_version=PROMPT_VERSION) if llm else None
    att = attest(s, det, lr)
    confirm(s, att)
    return att


ZERO_MINIMUMS = {"label": "test minimums (zero)",
                 "L2": {"human_decisions": 0, "override_rate_max": 1.0, "denies": 100, "lookback_withdrawn": 100},
                 "L3": {"human_releases": 0, "overrides": 100, "denies": 100, "lookback_withdrawn": 100},
                 "L4": {"released_after_window": 0, "vetoes": 100, "denies": 100, "lookback_withdrawn": 100}}


def set_minimums(s, minimums: dict, approvers=("checker", "checker2")):
    """A governed POLICY change of promotion_minimums (lowering needs two approvers, raising one)."""
    item = s.effective_item("POLICY", "policy")
    item["content"]["promotion_minimums"] = minimums
    return s.govern([item], approvers=approvers, rationale="Set the promotion minimums for this test plane")


def allow_promotions(s, *, llm=False):
    """For tests whose subject is NOT the evidence gate: a confirmed bundle and zero minimums, through the product's
    own governed paths (two approvers for the lowering)."""
    cr, status = set_minimums(s, ZERO_MINIMUMS)
    assert status == "APPLIED", status
    return grant_evidence(s, llm=llm)
