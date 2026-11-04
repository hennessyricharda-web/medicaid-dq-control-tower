"""K6 Front 1 (transport:scripted wiring, user decision 2026-10-29): the demo deployment binds `provider:
anthropic` / `transport: scripted` via a per-Runtime `explainer_cfg` override (runtime.py `__init__` and
`explain_for`'s cfg fallback chain), never by editing the single global `config/explainer.yaml` — that file is
also read as-is by `--genesis governed` (pilot) and by tests exercising pilot deployment directly (see
test_production_paths.py::test_pilot_runtime_explainer_refuses_scripted_whatever_the_environment_says), so
changing it would silently flip their explainer behavior too.

This file tests exactly the new wiring (the `explainer_cfg` constructor param and `cmd_demo`'s use of it), not
the ScriptedTransport mechanism itself, which tests/t0/test_scripted_transport.py already covers thoroughly
(good/bad output, every provider/kind, the source_mode contract).
"""
from pathlib import Path

import pytest

from dqagent import llm
from dqagent.runtime import Runtime


def test_demo_runtime_with_no_explainer_cfg_override_behaves_exactly_as_before(fresh_cp_dsn, tmp_path):
    """Regression guard: a Runtime built with explainer_cfg=None (the default, every call site before this
    package) still falls back to llm.load_config() exactly as before this parameter existed."""
    rt = Runtime(fresh_cp_dsn, dp_path=str(tmp_path / "x.duckdb"), test_genesis=True, deployment="demo")
    assert rt.explainer_cfg is None
    obj = {"rule_id": "ENV-1", "disposition": "FAIL", "unit_count": 3}
    out = rt.explain_for("rule_disposition", obj, subject="rule:ENV-1")
    assert out["source"] == "template"          # llm.load_config()'s real default, untouched


def test_demo_runtime_explainer_cfg_override_binds_scripted_without_a_per_call_cfg(fresh_cp_dsn, tmp_path):
    """The actual K6 fix: pass explainer_cfg at construction (as cmd_demo's demo branch now does), and an
    explain_for call with NO per-call cfg picks it up — proving the override is live, not dead code.

    subject="rule:ENV-1" (not explain_for's own default `f"{kind}:..."`, i.e. "rule_disposition:<hash>"): that
    default does not satisfy dq_ctl.span_refs_ok's REFS_PREFIXES allow-list for most `kind` values (only the one
    real production call site, runtime.py's `_run_night`, calls explain_for with an explicit, compliant subject —
    `f"nightly:{night_date}"`), so it crashes with a CheckViolation the one time a guardrail event is actually
    written (a scripted REJECT). That gap is real but out of this package's scope (it doesn't affect the demo's
    only production explain_for call site, which already passes a compliant subject) — logged to the backlog
    rather than fixed here; this test uses the same "rule:"-prefixed convention the rest of the codebase's
    explain_for callers and tests/t0/test_scripted_transport.py's subjects already use."""
    cfg = {**llm.load_config(), "provider": "anthropic", "transport": "scripted"}
    rt = Runtime(fresh_cp_dsn, dp_path=str(tmp_path / "x.duckdb"), test_genesis=True, deployment="demo",
                 explainer_cfg=cfg)
    assert rt.explainer_cfg == cfg
    obj = {"rule_id": "ENV-1", "disposition": "FAIL", "unit_count": 3}
    out = rt.explain_for("rule_disposition", obj, subject="rule:ENV-1")
    # ScriptedTransport deterministically marks some subjects BAD (sha256(subject) mod bad_every == 0); whichever
    # way "rule:ENV-1" falls, `source` and `fact_check` must agree with each other -- the public-facing contract
    # the ScriptedTransport mechanism itself already proves in tests/t0/test_scripted_transport.py (good -> PASS,
    # bad -> REJECT -> FALLBACK). What THIS test is actually proving is the wiring: the row persisted to
    # dq_ctl.llm_calls is SCRIPTED either way, never TEMPLATE -- i.e. the explainer_cfg override really put a real
    # (non-template) provider/transport in the loop, not a silent no-op.
    assert (out["source"], out["fact_check"]) in (("anthropic", "PASS"), ("template", "FALLBACK"))
    with rt.cp.as_role("dq_auditor") as c:
        row = c.execute("SELECT source_mode FROM dq_ctl.llm_calls ORDER BY call_id DESC LIMIT 1").fetchone()
    assert row["source_mode"] == "SCRIPTED"      # honest provenance label, confirmed live in the stored row -- the
                                                  # real, underlying transport, even when FALLBACK showed the template


def test_per_call_cfg_still_overrides_the_constructor_level_explainer_cfg(fresh_cp_dsn, tmp_path):
    """Precedence guard: explain_for's own cfg= argument must still win over the constructor's explainer_cfg —
    this is the existing behavior test_production_paths.py's pilot test relies on, unchanged by this package."""
    demo_cfg = {**llm.load_config(), "provider": "anthropic", "transport": "scripted"}
    rt = Runtime(fresh_cp_dsn, dp_path=str(tmp_path / "x.duckdb"), test_genesis=True, deployment="demo",
                 explainer_cfg=demo_cfg)
    obj = {"rule_id": "ENV-1", "disposition": "FAIL", "unit_count": 3}
    out = rt.explain_for("rule_disposition", obj, cfg={"provider": "template"}, subject="rule:ENV-1")
    assert out["source"] == "template"


# Note: cmd_demo itself is a full end-to-end CLI entrypoint (drops/creates a real database, deletes DuckDB
# files, and eventually serves HTTP) — not safely callable as a unit test. The three tests above exercise the
# actual mechanism cmd_demo's demo branch now uses (Runtime's explainer_cfg parameter); the exact line in
# cmd_demo (`dqagent/cli/main.py`, the demo-deployment branch) was confirmed by direct code read, not by an
# integration test of the whole launcher.
