"""The eval recorder (Part F K0 step 1; PFP P2-1 :950, R4-5 §6): the one PRODUCT writer of `dq_ctl.eval_runs`.

`dqagent eval run --suite agent_eval|explainer` (cli/main.py) calls `run_suite` here. It:
  1. actually runs the named suite (a real pytest subprocess, the same mechanism tools/gate.py and
     tools/receipt.py use, so this is one real run, never a simulated one);
  2. derives `source_mode` -- NEVER a parameter, NEVER a caller-supplied label, NEVER read from configuration text
     alone (red-team I9, EXECUTION_PLAN.md K0 step 1): for `agent_eval` it is always DETERMINISTIC, because that
     suite's own scenarios call no LLM transport by construction (tests/agent_eval/README.md: "The LLM provider is
     NullProvider throughout"); for `explainer` it is `dqagent.llm.status(cfg, env)["source_mode"]`, the SAME
     honesty check `/api/v1/explainer` already relies on (I6: nothing can be called -> TEMPLATE, never LIVE). A
     SCRIPTED or unconfigured HTTP transport can therefore never be recorded as LIVE, by construction, not by a
     filter applied after the fact.
  3. writes ONE row through `dq_ctl.eval_run_record` as `dq_eval_svc` (the only writer the database accepts --
     trace_schema.py:301-311,557-560 -- `eval_runs` is append-only and role-restricted; this module relies on that,
     it does not re-implement it).

Before this module, the only caller of `eval_run_record` anywhere in the repo was the TEST helper
`tests/t1/evidence.py::record_run`, which still exists for tests that need to construct SCRIPTED/TEMPLATE/LIVE rows
directly (including NEGATIVE evidence-gate tests, where `source_mode` must be settable to prove the database
refuses a forged label) -- that helper now delegates its DETERMINISTIC/default path to this module (see its
docstring), so there is one product path, not two, for the case that matters: a real run recording its own truth.
"""
from __future__ import annotations

import hashlib
import subprocess
import sys
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

EVAL_SUITES = ("agent_eval", "explainer")   # mirrors dqagent.cp.trace_schema.EVAL_SUITES exactly (one definition there)

# The real pytest paths each suite runs. agent_eval needs a live control plane (it is DB-backed, deterministic code
# only, per tests/agent_eval/README.md); explainer is pure T0 (a local stub HTTP server, never exercised live) --
# see dqagent/llm/__init__.py and tests/t0/test_parte_explainer.py's own docstring.
SUITE_PATHS = {
    "agent_eval": ["tests/t1/test_agent_eval.py"],
    "explainer": ["tests/t0/test_parte_explainer.py", "tests/t0/test_explainer_adversarial.py",
                  "tests/t0/test_scripted_transport.py"],
}


class SuiteRunError(RuntimeError):
    """The suite could not be run or produced no collectible tests: never recorded as a row (a 0-case row would be
    misleading, not honest)."""


def _run_pytest(paths: list[str], junit: Path) -> int:
    cmd = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *paths, f"--junitxml={junit}"]
    return subprocess.call(cmd, cwd=ROOT)


def _junit_counts(junit: Path) -> dict:
    """{tests, failures, errors, skipped}, from the single aggregate <testsuite> element -- same convention as
    tools/gate.py::_junit_counts and tools/receipt.py, read independently here (dqagent/ product code does not
    import from tools/, a dev-only tree)."""
    root = ET.parse(junit).getroot()
    s = root if root.tag == "testsuite" else root.find("testsuite")
    return {k: int(s.get(k, 0)) for k in ("tests", "failures", "errors", "skipped")}


def _derive_source_mode(suite: str, cfg: dict | None, env=None) -> str:
    """The suite's REAL source_mode. `agent_eval` is always DETERMINISTIC (structural: no transport exists in that
    suite). `explainer` is `dqagent.llm.status(...)["source_mode"]`: TEMPLATE when the provider is template or not
    configured (I6), else the actually-configured transport's own SOURCE_MODE. This is the one place source_mode is
    decided; nothing upstream of this function may override it."""
    if suite == "agent_eval":
        return "DETERMINISTIC"
    from dqagent import llm
    return llm.status(cfg, env)["source_mode"]


def run_suite(cp, suite: str, *, cfg: dict | None = None, env=None, paths: list[str] | None = None,
             prompt_version: str | None = None) -> dict:
    """Runs `suite` for real and records one `dq_ctl.eval_runs` row. Returns {run_id, suite, source_mode, cases,
    passed, code_sha256, prompt_version, junit_sha256}.

    `paths` overrides SUITE_PATHS[suite] -- for tests only, so the recording mechanics can be proven against a tiny
    stub suite instead of the full real one; the CLI never passes it, so production always runs the real suite.
    `cfg`/`env` are the explainer configuration and environment to probe for `explainer`'s source_mode (defaults:
    dqagent.llm.load_config() / os.environ); ignored for `agent_eval`.
    `source_mode` is never a parameter here -- see _derive_source_mode. `code_sha256` is always the running code's
    own hash (dqagent.obs.tracer.code_sha256); a caller cannot supply a different one.
    """
    if suite not in EVAL_SUITES:
        raise ValueError(f"unknown eval suite {suite!r}; one of {', '.join(EVAL_SUITES)}")
    run_paths = paths if paths is not None else SUITE_PATHS[suite]
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        junit = Path(td) / "eval_run.junit.xml"
        _run_pytest(run_paths, junit)
        if not junit.exists():
            raise SuiteRunError(f"suite {suite!r} produced no junit report (pytest did not run)")
        counts = _junit_counts(junit)
        junit_bytes = junit.read_bytes()
    cases = counts["tests"]
    if cases == 0:
        raise SuiteRunError(f"suite {suite!r} collected zero tests at {run_paths}; nothing to record")
    passed = cases - counts["failures"] - counts["errors"] - counts["skipped"]
    source_mode = _derive_source_mode(suite, cfg, env)
    from dqagent.obs import tracer
    code = tracer.code_sha256()
    pv = prompt_version
    if pv is None and suite == "explainer":
        from dqagent.llm import PROMPT_VERSION
        pv = PROMPT_VERSION
    run_id = f"CLI-{suite}-{uuid.uuid4().hex[:12]}"
    junit_sha256 = hashlib.sha256(junit_bytes).hexdigest()
    with cp.as_role("dq_eval_svc") as c:
        c.execute("SELECT dq_ctl.eval_run_record(%s,%s,%s,%s,%s,%s,%s,%s)",
                  (run_id, suite, source_mode, cases, passed, code, pv, junit_sha256))
    return {"run_id": run_id, "suite": suite, "source_mode": source_mode, "cases": cases, "passed": passed,
            "code_sha256": code, "prompt_version": pv, "junit_sha256": junit_sha256}
