"""dbt manifest discovery adapter (Part F P1-3, K2b; PFP :821-824). The THIRD seam registered under
`dqagent.deploy.seam` (the other two, `dqagent/secrets.py:16` and `dqagent/llm/transports.py:93`, confirmed
pre-existing), under a new seam id `"dag"` (PFP's own literal text, reserved verbatim).

`discover(manifest_path)` is the pure, path-confined, write-nothing read `POST /catalog/discover` (K2c) calls for
its preview, and the same function the Tower's Catalog table calls LIVE every time it renders a pipeline's nodes
(K2b-5, RESOLVED: no persisted node list -- `dag_snapshots` carries only a manifest digest for drift detection,
never the nodes themselves)."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from dqagent.deploy import seam

ROOT = Path(__file__).resolve().parents[3]
PIPELINES_ROOT = (ROOT / "pipelines").resolve()


class DiscoveryError(RuntimeError):
    """A manifest path outside `pipelines/`, or no manifest at the given path. Fail closed: never read outside
    the confined root, and never silently return an empty discovery."""


@seam("dag")
def discover(manifest_path: str, layer_map: dict | None = None) -> dict:
    """Preview one dbt project's DAG from its committed `manifest.json`. Writes nothing (PFP :836, the discover
    route's own contract, confirmed this function is the thing that route calls). Confined to `pipelines/`: a
    manifest path outside it is refused before the file is ever opened (fail closed).

    Returns {"manifest_digest": sha256 hex of the raw manifest bytes, "nodes": [{"node", "resource_type",
    "schema", "relation_name", "depends_on": [node names], "layer", "layer_source"}]} for every `model` node.

    Layers are NEVER inferred from folder names (PFP :823, "Layers are never inferred", confirmed the design's own
    explicit anti-pattern -- `pipelines/w1_dbt/models/{silver,gold}/` folder names are never consulted). `layer`
    comes from `layer_map[node]` (an explicit PIPELINE.layer_map entry this call's caller supplies, keyed by node
    name) when given; otherwise from the node's own `meta.dq_layer` (checked under both `meta` and
    `config.meta`, the two shapes a dbt manifest stores it under, depending on dbt version); otherwise UNLAYERED.
    `layer_source` names which of the three applied, so a caller can tell a real declaration from the UNLAYERED
    default -- this function itself never guesses a layer."""
    p = Path(manifest_path)
    p = p if p.is_absolute() else (ROOT / p)
    try:
        p.resolve().relative_to(PIPELINES_ROOT)
    except ValueError:
        raise DiscoveryError(f"manifest_path must be confined to pipelines/: {manifest_path!r}") from None
    if not p.is_file():
        raise DiscoveryError(f"no manifest at {manifest_path!r}")
    raw = p.read_bytes()
    manifest_digest = hashlib.sha256(raw).hexdigest()
    manifest = json.loads(raw)
    layer_map = layer_map or {}
    nodes = []
    for uid, n in sorted((manifest.get("nodes") or {}).items()):
        if n.get("resource_type") != "model":
            continue
        name = n.get("name") or uid
        meta = n.get("meta") or (n.get("config") or {}).get("meta") or {}
        if name in layer_map:
            layer, source = layer_map[name], "layer_map"
        elif meta.get("dq_layer"):
            layer, source = meta["dq_layer"], "meta.dq_layer"
        else:
            layer, source = "UNLAYERED", "UNLAYERED"
        deps = sorted(x.rsplit(".", 1)[-1] for x in (n.get("depends_on") or {}).get("nodes") or [])
        nodes.append({"node": name, "resource_type": n["resource_type"], "schema": n.get("schema"),
                      "relation_name": n.get("relation_name"), "depends_on": deps,
                      "layer": layer, "layer_source": source})
    return {"manifest_digest": manifest_digest, "nodes": nodes}
