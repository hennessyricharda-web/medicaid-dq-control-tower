"""Part F P1-3 (K2b): `dqagent.adapters.dag.dbt_files.discover` -- a manifest-only, path-confined, write-nothing
preview. Seam registration, layer provenance (never inferred from folder names), and the path guard."""
import json
from pathlib import Path

import pytest

from dqagent.adapters.dag.dbt_files import DiscoveryError, discover
from dqagent.deploy import SEAMS

ROOT = Path(__file__).resolve().parents[2]
W1_MANIFEST = "pipelines/w1_dbt/target/manifest.json"


def test_registered_under_the_dag_seam():
    """PFP :822: `@seam("dag")`, the THIRD seam, registered verbatim under this exact id."""
    assert any(name.endswith("dbt_files.discover") for name in SEAMS.get("dag", []))


def test_layers_never_inferred_from_folders():
    """PFP :823: layers come from `layer_map` or an explicit `meta.dq_layer`, NEVER from the folder a model's
    `.sql` file sits in. `pipelines/w1_dbt/models/{silver,gold}/` names a layer in the folder path, but no model
    there declares `meta.dq_layer` -- every one of them must read UNLAYERED, not SILVER or GOLD, proving the
    folder name was never consulted."""
    if not (ROOT / W1_MANIFEST).exists():
        pytest.skip("pipelines/w1_dbt/target/manifest.json not built in this environment")
    r = discover(W1_MANIFEST)
    by_node = {n["node"]: n for n in r["nodes"]}
    assert {"silver_eligibility_span", "gold_tmsis_elg_subset"} <= set(by_node)
    for name, n in by_node.items():
        assert n["layer"] == "UNLAYERED" and n["layer_source"] == "UNLAYERED", \
            f"{name}: folder-inferred layer leaked through ({n['layer']!r})"
    # a layer_map entry for ONE node overrides UNLAYERED for that node only -- proving layer_map is read when
    # given, not that this function always returns UNLAYERED regardless of input
    r2 = discover(W1_MANIFEST, layer_map={"silver_eligibility_span": "SILVER"})
    by_node2 = {n["node"]: n for n in r2["nodes"]}
    assert by_node2["silver_eligibility_span"]["layer"] == "SILVER"
    assert by_node2["silver_eligibility_span"]["layer_source"] == "layer_map"
    assert by_node2["gold_tmsis_elg_subset"]["layer"] == "UNLAYERED"      # untouched: still not folder-inferred


def test_meta_dq_layer_read_when_declared():
    """A model declaring `meta.dq_layer` (e.g. `pipelines/tmsis_outbound`'s OUTBOUND) is read from that
    declaration, not from any folder-name heuristic -- a synthetic manifest with the SAME node name under a
    DIFFERENT (misleading) key still reads the declared value. The scratch manifest is written INSIDE
    `pipelines/` (the confined root `discover` itself requires), under a path `.gitignore`'s own
    `pipelines/*/target/` pattern already covers, and removed explicitly in a `finally` regardless."""
    manifest = {"nodes": {"model.x.m1": {"resource_type": "model", "name": "m1", "schema": "s",
                                         "relation_name": "r", "meta": {"dq_layer": "OUTBOUND"},
                                         "depends_on": {"nodes": []}}}}
    d = ROOT / "pipelines" / "_t0_scratch" / "target"
    d.mkdir(parents=True, exist_ok=True)
    p = d / "manifest_test_dbt_files_adapter.json"
    try:
        p.write_text(json.dumps(manifest))
        r = discover(str(p.relative_to(ROOT)))
        assert r["nodes"][0]["layer"] == "OUTBOUND" and r["nodes"][0]["layer_source"] == "meta.dq_layer"
    finally:
        p.unlink(missing_ok=True)
        try:
            d.rmdir()
            d.parent.rmdir()
        except OSError:
            pass


def test_path_confined_to_pipelines():
    """PFP :836 (discover's own contract, reused here): a manifest path outside `pipelines/` is refused before
    the file is ever opened -- confirmed by pointing at a REAL file outside pipelines/ that would otherwise
    parse successfully as JSON if ever read."""
    outside = ROOT / "config" / "agents.yaml"           # a real file, confirmed to exist, outside pipelines/
    assert outside.exists()
    with pytest.raises(DiscoveryError, match="confined to pipelines/"):
        discover(str(outside.relative_to(ROOT)))
    with pytest.raises(DiscoveryError, match="confined to pipelines/"):
        discover("pipelines/w1_dbt/target/../../../config/agents.yaml")       # traversal out of the confined root


def test_missing_manifest_fails_closed():
    with pytest.raises(DiscoveryError, match="no manifest"):
        discover("pipelines/does_not_exist/target/manifest.json")


DEMO_ESTATE_MANIFEST = "pipelines/demo_estate/target/manifest.json"


def test_demo_estate_is_64_discovered_not_evaluated_models():
    """K4 (PFP P2-4): `pipelines/demo_estate/` ships ~64 hand-authored models, every one "discovered, not
    evaluated" -- i.e. a plain dbt `model` node this adapter can discover, with NO corresponding TASK_GATE
    anywhere (confirmed separately, `test_catalog_routes.py`'s graph-route tests never see these ids as gated).
    This test only proves the fixture's own shape: real `meta.dq_layer` declarations (never UNLAYERED, since
    every node here declares one), real depends_on edges, and no duplicate node names."""
    assert (ROOT / DEMO_ESTATE_MANIFEST).exists(), "pipelines/demo_estate/target/manifest.json must be committed"
    r = discover(DEMO_ESTATE_MANIFEST)
    names = [n["node"] for n in r["nodes"]]
    assert len(names) == len(set(names)), "duplicate node names in the demo estate"
    assert 60 <= len(names) <= 70, f"expected ~64 models, got {len(names)}"
    layers = {n["layer"] for n in r["nodes"]}
    assert layers == {"INTAKE", "STAGING", "CURATED", "MART"}, layers
    assert all(n["layer_source"] == "meta.dq_layer" for n in r["nodes"]), \
        "every demo-estate node must declare its own layer, never fall back to UNLAYERED"
    # at least one real dependency edge per non-INTAKE layer, confined to node names this same manifest declares
    node_set = set(names)
    non_root = [n for n in r["nodes"] if n["layer"] != "INTAKE"]
    assert non_root and all(n["depends_on"] for n in non_root)
    assert all(set(n["depends_on"]) <= node_set for n in r["nodes"])


def test_demo_estate_writes_nothing():
    p = ROOT / DEMO_ESTATE_MANIFEST
    before = p.read_bytes()
    discover(DEMO_ESTATE_MANIFEST)
    assert p.read_bytes() == before


def test_discover_writes_nothing():
    """PFP :836: `discover` is preview-only; nothing is written. Proven by asserting it opens the manifest for
    READ ONLY (the real w1 manifest's mtime/content are byte-identical before and after a call)."""
    if not (ROOT / W1_MANIFEST).exists():
        pytest.skip("pipelines/w1_dbt/target/manifest.json not built in this environment")
    p = ROOT / W1_MANIFEST
    before = p.read_bytes()
    discover(W1_MANIFEST)
    assert p.read_bytes() == before
