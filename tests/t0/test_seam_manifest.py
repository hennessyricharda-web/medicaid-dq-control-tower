"""tests/t0/test_seam_manifest.py (K7, PFP P1-M, PARTF_IMPLEMENTATION_PLAN.md:386-392, binding).

docs/seams.yaml was confirmed ABSENT before this build (K7_K8_K9_DESIGN.md §1.1; re-confirmed independently this
dispatch by Glob + Grep, both zero matches). This file is its manifest test, built from scratch, not extended.

The six checks, verbatim from PFP:
  1. every @seam("id") implementation has a manifest entry;
  2. every entry names both a mock and a production side;
  3. a fail-closed-stub's implementation raises rather than returning a value;
  4. every test node id the manifest cites resolves under `pytest --collect-only -q`;
  5. a real-impl seam never falls back to its mock when production is configured;
  6. code never imports a `planned` seam's adapter package.
"""
from __future__ import annotations

import inspect
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

# SEAMS (dqagent.deploy.SEAMS) is populated lazily: a @seam(...)-decorated function/class only registers itself
# once its OWN module has been imported somewhere in the process. Force every module known to carry a marker to
# import here, so this file's own assertions do not depend on import order elsewhere in the suite (a real risk:
# running this file alone, or first, would otherwise see a SEAMS dict missing entries that exist in the tree but
# whose module nothing has imported yet).
import dqagent.secrets  # noqa: F401  (@seam("secrets"))
import dqagent.llm.transports  # noqa: F401  (@seam("llm.transport") on HttpTransport)
import dqagent.adapters.dag.dbt_files  # noqa: F401  (@seam("dag") on discover)
import dqagent.llm.providers.anthropic  # noqa: F401  (@seam("llm.provider") on configured, added this dispatch)
import dqagent.llm.providers.watsonx  # noqa: F401
import dqagent.llm.providers.azure_openai  # noqa: F401
import dqagent.llm.providers.cortex  # noqa: F401
import dqagent.alerts  # noqa: F401  (@seam("alert_sink") on LogSink, WebhookSink)

from dqagent.deploy import NotConfiguredError, SEAMS

ROOT = Path(__file__).resolve().parents[2]
MANIFEST_PATH = ROOT / "docs" / "seams.yaml"
MANIFEST = yaml.safe_load(MANIFEST_PATH.read_text())["seams"]
BY_ID = {e["id"]: e for e in MANIFEST}

# Entries that legitimately carry no @seam() marker at all (docs/seams.yaml's own per-entry "note" explains each):
# `deployment` and `dataplane` are infrastructure / unmarked-but-functioning implementations PFP P1-M still lists
# by name (:376); `explanation_cache` has a real production table (dq_ctl.explanations) but no @seam() call site
# anywhere in dqagent/ (confirmed by grep). These three are deliberately excluded from check 1's reverse
# direction (an entry with no marker is fine for these three specifically; check 1 itself only runs forward, from
# SEAMS to the manifest, so this set is documentation, not a loophole in the assertion below).
UNMARKED_OK = {"deployment", "dataplane", "explanation_cache"}


def test_every_seam_marker_has_an_entry():
    """Every @seam("id") call site registered in dqagent.deploy.SEAMS has a docs/seams.yaml entry. Confirmed
    this dispatch: with every marked module force-imported above, SEAMS holds exactly
    {"secrets", "llm.transport", "dag", "llm.provider"} (3 pre-existing @seam() call sites, plus llm.provider
    added this dispatch on the 4 provider modules' configured())."""
    missing = [seam_id for seam_id in SEAMS if seam_id not in BY_ID]
    assert not missing, f"@seam() markers with no docs/seams.yaml entry: {missing}"
    assert set(SEAMS) == {"secrets", "llm.transport", "dag", "llm.provider", "alert_sink"}, (
        f"a new @seam() id appeared ({set(SEAMS)}) that this test's own expectation has not been updated for -- "
        "add its docs/seams.yaml entry AND update this assertion together, not just one of the two")
    # Sanity: the three deliberately-unmarked entries really are absent from SEAMS (if one of them gained a
    # marker, that is fine functionally, but the manifest's own notes would then need updating by a human).
    marked_but_documented_unmarked = UNMARKED_OK & set(SEAMS)
    assert not marked_but_documented_unmarked, (
        f"{marked_but_documented_unmarked} now carries an @seam() marker; docs/seams.yaml's notes for these "
        "entries (written assuming no marker exists) need a human update, not a silent pass")


def test_every_entry_has_both_sides():
    """Every entry whose disposition commits to an implementation (real-impl, fail-closed-stub, mock-only) names
    both a mock and a production side. `absent` and `planned` entries are exempt by definition (there is nothing
    to name on either side)."""
    committed = {"real-impl", "fail-closed-stub", "mock-only"}
    bad = [e["id"] for e in MANIFEST
           if e["disposition"] in committed and not (e["mock"].get("impl") and e["production"].get("impl"))]
    assert not bad, f"entries committed to an implementation but missing a mock or production side: {bad}"


def test_fail_closed_stub_raises(monkeypatch, tmp_path):
    """PFP's own two named exemplars (:387-389), exercised directly against the real implementations -- not
    merely cited by test id: an unconfigured secrets seam raises, and the OIDC-unconfigured path raises. Neither
    is caught and turned into a returned sentinel value anywhere on these two call paths."""
    from dqagent.runtime import Runtime
    from dqagent.secrets import EnvSecrets

    # 1. secrets, unconfigured: EnvSecrets().keys() raises rather than returning a dict with missing/empty keys.
    for v in ("DQ_K_EV", "DQ_K_FP", "DQ_K_ABC"):
        monkeypatch.delenv(v, raising=False)
    with pytest.raises(NotConfiguredError, match="not configured"):
        EnvSecrets().keys()

    # 2. idp, production, no OIDC issuer: Runtime raises before ever touching the control plane or data plane
    # (dqagent/runtime.py:224-229). The dsn is a dummy -- CP(cp_dsn) (engine/cp.py:20-22) stores it without
    # connecting, and is only constructed AFTER this raise, so no real Postgres is needed for this check.
    monkeypatch.setenv("DQ_K_EV", "pilot-ev-key")
    monkeypatch.setenv("DQ_K_FP", "pilot-fp-key")
    monkeypatch.setenv("DQ_K_ABC", "pilot-abc-key")
    monkeypatch.delenv("DQ_OIDC_ISSUER", raising=False)
    with pytest.raises(NotConfiguredError, match="OIDC"):
        Runtime({"dbname": "unused-this-raise-fires-before-any-connection"},
                dp_path=str(tmp_path / "dp.duckdb"), deployment="production")

    # The one fail-closed-stub manifest entry is idp; confirm the manifest hasn't drifted from this test's scope.
    stubs = [e["id"] for e in MANIFEST if e["disposition"] == "fail-closed-stub"]
    assert stubs == ["idp"], f"a new fail-closed-stub entry {stubs} needs its own raise exercised here too"


_CITED_NODE_IDS = sorted({e[k] for e in MANIFEST for k in ("fail_closed_test", "parity_test") if e.get(k)})


@pytest.mark.parametrize("nodeid", _CITED_NODE_IDS)
def test_cited_test_ids_resolve(nodeid):
    """Every fail_closed_test / parity_test node id in docs/seams.yaml must resolve under
    `pytest --collect-only -q`, run for real as a subprocess (not assumed from the citation's own text)."""
    out = subprocess.run([sys.executable, "-m", "pytest", "--collect-only", "-q", nodeid],
                          cwd=ROOT, capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, (
        f"{nodeid!r} does not resolve under pytest --collect-only -q "
        f"(exit {out.returncode}):\nSTDOUT:\n{out.stdout}\nSTDERR:\n{out.stderr}")


def test_real_impl_never_falls_back_to_mock():
    """PFP: testable for `dag` and `dataplane`, both DuckDB/dbt-local already (no credentials or network
    needed). Both have mock.impl == "n/a" in docs/seams.yaml because there is no second, mock implementation to
    fall back to: confirmed structurally (neither production callable accepts a deployment- or mock-selecting
    parameter, so there is no branch point where a hidden fallback could live) and, where a real fixture exists,
    behaviorally (the real call's result does not change under DQ_DEPLOYMENT=production)."""
    from dqagent.adapters.dag.dbt_files import discover
    from dqagent.adapters.dataplane.duckdb_dp import DuckDBDataPlane

    for fn, label in ((discover, "dag"), (DuckDBDataPlane.__init__, "dataplane")):
        params = set(inspect.signature(fn).parameters)
        assert not (params & {"deployment", "mock", "transport"}), (
            f"{label}'s real-impl callable accepts a mock-selecting parameter "
            f"{params & {'deployment', 'mock', 'transport'}} -- re-examine whether a hidden fallback branch "
            "now exists")

    manifest = ROOT / "pipelines" / "w1_dbt" / "target" / "manifest.json"
    if not manifest.exists():
        pytest.skip("pipelines/w1_dbt/target/manifest.json not built in this environment")
    rel = str(manifest.relative_to(ROOT))
    a = discover(rel)
    old = os.environ.get("DQ_DEPLOYMENT")
    os.environ["DQ_DEPLOYMENT"] = "production"
    try:
        b = discover(rel)
    finally:
        if old is None:
            os.environ.pop("DQ_DEPLOYMENT", None)
        else:
            os.environ["DQ_DEPLOYMENT"] = old
    assert a == b, "dag's discover() result changed under DQ_DEPLOYMENT=production -- a hidden fallback exists"


def test_planned_seam_is_not_imported():
    """PFP: code never imports a `planned` seam's adapter package. `orchestrator` is the one such entry today
    (dqagent/adapters/orchestrator/__init__.py is confirmed empty, and is the only file in that package)."""
    planned_with_package = [e["id"] for e in MANIFEST
                            if e["disposition"] == "planned" and (ROOT / "dqagent" / "adapters" / e["id"]).is_dir()]
    assert planned_with_package == ["orchestrator"], (
        "update this test's package-name mapping if a new planned, adapter-package-backed seam is added: "
        f"found {planned_with_package}")
    needle_dir = os.path.join("adapters", "orchestrator")
    bad = []
    for p in (ROOT / "dqagent").rglob("*.py"):
        if needle_dir in str(p):
            continue  # the package's own (empty) file, not an importer of it
        text = p.read_text(encoding="utf-8", errors="ignore")
        if "adapters.orchestrator" in text or "adapters import orchestrator" in text:
            bad.append(str(p.relative_to(ROOT)))
    assert not bad, f"code imports the planned 'orchestrator' adapter package: {bad}"
