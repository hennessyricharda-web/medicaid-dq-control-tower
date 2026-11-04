"""PFP P1-2: a per-pipeline governance configuration snapshot (read-only; K1_DESIGN.md §3/§4).

compose_profile(registry, state, pipeline_id, as_of) assembles, for one pipeline:
  pipeline      the harness entry (until P1-3 switches this to PIPELINE items, PFP :710);
  contracts     the gated relations' contract entries (config/state/<ST>/contracts_w1.yaml);
  rules         the relations' rules, with status / gating / effective_hash against the APPROVED hash (the house
                pattern Registry._impact already uses for change-request impact, registry.py:438-446);
  tiers         the 4-argument autonomy_tier per authority (RELEASE; PFP :714);
  datasets      Part F P1-3 (K2c, PFP :839), additive: {relation: {introduced_by, agent_tier, effective_tier}} for
                every gated relation, via `dataset_governance` below (ORCH-1's ceiling-aware tier, present-tense);
  monitors      the MONITOR artifact's content, labelled "not governed" (L-10, BACKLOG.md:120);
  dependencies  the harness dependency edges that name one of this pipeline's domains;
  order         the GLOBAL pipeline order (harness.plan.order), for context;
  routing       the FEEDS artifact's routing block, filtered to this pipeline's routes (governed since P1-0);
  contributors  {artifact_id: [{version, content_hash, cr_id, approvers}]} for every artifact this pipeline's
                profile depends on (PFP :718) — approvers come from config_cr_events (event='APPROVED'), the same
                join gov_apply's SQL already performs server-side (governance_schema.py:213-217);
  profile_digest  sha256 over the whole dict (Registry._h's pattern, registry.py:51-52).

as_of resolution (D9, RESOLVED): as_of is None ("now": the latest approved artifacts) or a trace_id. A trace_id
resolves to a point-in-time cutoff by looking up that trace's stamped config_digest (dq_ctl.runs.config_digest,
tracer.py:153/160 — the ruleset_version at the moment the root opened, runtime.py:549), finding the config_artifacts
row whose own ruleset recomputes to that same digest, and using THAT row's `at` timestamp as the cutoff for every
other artifact (only versions with at <= cutoff are "current" as of that moment). Multiple config_artifacts rows can
share one ruleset_version digest (CONTRACTS / HARNESS / POLICY changes never move the ruleset); per the design's
own flagged risk, this module takes the MOST RECENT such row (the moment just before the ruleset next moved), so
every artifact that existed without changing the ruleset is correctly included in the "as of" snapshot.
"""
from __future__ import annotations

import hashlib
import json

from dqagent.governance.registry import cfg_from_artifacts, ruleset

AUTONOMY_ACTION_CLASS = "RELEASE"     # the only action class a read-only profile reports (PFP :714 shows RELEASE)


def _h(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def _pipeline_entry(harness: dict, pipeline_id: str) -> dict | None:
    return next((p for p in harness.get("pipelines", []) if p["id"] == pipeline_id), None)


def _authorities_of(contracts: dict, relations: list[str]) -> list[str]:
    """The union of authorities_by_program across the pipeline's gated relations (D7's scope-enforcement input;
    contracts_w1.yaml :13,27,37 — authorities_by_program lives per relation, never at the pipeline level)."""
    out: set[str] = set()
    for rel in relations:
        entry = (contracts.get("relations") or contracts).get(rel) or {}
        for auths in (entry.get("authorities_by_program") or {}).values():
            out.update(auths)
        if entry.get("pre_split_authority"):
            out.add(entry["pre_split_authority"])
    return sorted(out)


def pipeline_authorities(harness: dict, contracts: dict, pipeline_id: str) -> list[str]:
    """Public helper for the route (D7): the authority set a caller's scope must intersect to read this pipeline's
    profile. Raises KeyError if the pipeline is unknown (the route turns that into 404)."""
    entry = _pipeline_entry(harness, pipeline_id)
    if entry is None:
        raise KeyError(pipeline_id)
    return _authorities_of(contracts, entry.get("gated_nodes") or [])


def dataset_governance(registry, state: str, dataset: str, authorities: list[str],
                       agent: str | None = None, action_class: str = AUTONOMY_ACTION_CLASS) -> dict:
    """Part F P1-3 (K2c): {introduced_by, agent_tier, effective_tier} for ONE gated dataset (PFP :839, reused by
    the `nodes` route, PFP :835). `introduced_by` is the dataset's LATEST `gated_dataset_introductions` row (NULL
    when the dataset has never been introduced by any gate -- a genesis-era w1 relation always has one, per K2a's
    4 block gates). `agent_tier`/`effective_tier` are each the LOWEST across `authorities` (this function's own
    build decision, not pinned by PFP's prose, which names a single scalar per dataset but never resolves which
    authority when a dataset's authorities_by_program names more than one; "lowest wins" matches this codebase's
    OWN existing fail-closed convention for exactly this kind of reduction -- `autonomy_tier`'s docstring, "across
    several states the LOWEST tier wins (fail closed)" -- applied here across authorities instead of states).
    `agent_tier` is the plain 4-argument `autonomy_tier` (no task-gate ceiling applied); `effective_tier` is
    `autonomy_tier_for` (the ceiling-aware one), so a caller can see both numbers and the gap between them."""
    agent = agent or _agent()
    with registry.cp.as_role("dq_registry_svc") as c:
        row = c.execute("SELECT introduced_by FROM dq_ctl.gated_dataset_introductions WHERE state=%s AND dataset=%s "
                        "ORDER BY version DESC LIMIT 1", (state, dataset)).fetchone()
        introduced_by = row["introduced_by"] if row else None
        if not authorities:
            return {"introduced_by": introduced_by, "agent_tier": None, "effective_tier": None}
        from dqagent.governance.registry import TIERS
        rank = {t: i + 1 for i, t in enumerate(TIERS)}
        agent_tiers = [c.execute("SELECT dq_ctl.autonomy_tier(%s,%s,%s,%s) t",
                                 (state, agent, action_class, auth)).fetchone()["t"] for auth in authorities]
        effective_tiers = [c.execute("SELECT dq_ctl.autonomy_tier_for(%s,%s,%s,%s,%s) t",
                                     (state, agent, action_class, auth, dataset)).fetchone()["t"]
                           for auth in authorities]
    return {"introduced_by": introduced_by,
            "agent_tier": min(agent_tiers, key=lambda t: rank.get(t, 1)),
            "effective_tier": min(effective_tiers, key=lambda t: rank.get(t, 1))}


def _as_of_cutoff(registry, state: str, as_of: str | None):
    """None (now) or a trace_id resolved to a config_artifacts.at cutoff (D9). Raises KeyError when the trace id is
    unknown or carries no usable config_digest (the route turns that into 404 / 422).

    `compose_profile`'s reserved signature carries no scope/principal (confirmed, K1_DESIGN.md §0/round-2 finding),
    so this lookup cannot run as a scoped `dq_api` (`dq_ctl.runs` carries a per-authority RLS policy keyed on
    `dq.scope`, and a NIGHT/PUBLISH_CYCLE/RECOVER root has EMPTY authorities, matching no scope at all — trace_schema.py
    :182-186). It reads only the one non-sensitive `config_digest` column of a trace_id the caller already supplied
    (no enumeration), as `dq_auditor`, the one role with an unconditional SELECT policy on this table
    (trace_schema.py :70-71, "{t}_audit ... USING (true)")."""
    if as_of is None:
        return None
    with registry.cp.as_role("dq_auditor") as c:
        run = c.execute("SELECT config_digest FROM dq_ctl.runs WHERE trace_id=%s", (as_of,)).fetchone()
    if run is None or not run["config_digest"] or run["config_digest"] == "unratified":
        raise KeyError(as_of)
    digest = run["config_digest"]
    with registry.cp.as_role("dq_api") as c:
        rows = c.execute(
            "SELECT artifact_id, version, kind, op, content, at FROM dq_ctl.config_artifacts "
            "WHERE state=%s ORDER BY at, version", (state,)).fetchall()
    # Walk the WHOLE artifact history forward, recomputing the ruleset AS OF each row's own moment (every artifact
    # at or before it), the same definition ruleset() always uses (registry.py:186-190). The ruleset version only
    # depends on PROFILE / FEEDS / BINDINGS / OVERLAY content (compile_rules never reads CONTRACTS or HARNESS), so
    # it typically stays stable across several LATER artifact rows (a HARNESS or POLICY change that does not touch
    # a rule). Taking the FIRST matching row would understate which other artifacts (contracts, harness, policy)
    # were actually in force by the time this exact ruleset was stamped on the trace; taking the LAST matching row
    # (architect's flagged mitigation, K1_DESIGN.md D9) is the moment just before the ruleset next moved, so every
    # artifact that existed without changing the ruleset is correctly included in the "as of" snapshot.
    by_id: dict = {}
    cutoff = None
    for r in rows:
        by_id[r["artifact_id"]] = r
        cfg = _cfg_from(by_id)
        if cfg is None:
            continue
        try:
            if ruleset(state, cfg)["version"] == digest:
                cutoff = r["at"]
        except Exception:  # noqa: BLE001 - an un-compilable intermediate snapshot is skipped, not fatal
            continue
    if cutoff is None:
        raise KeyError(as_of)
    return cutoff


def _cfg_from(by_id: dict) -> dict | None:
    arts = {aid: {"kind": r["kind"], "op": r["op"], "content": r["content"]} for aid, r in by_id.items()}
    # cfg_from_artifacts expects state-prefixed ids and strips the prefix itself; it is state-agnostic about the
    # actual prefix string, so pass the ids through unchanged (every row already carries its own state's prefix).
    state = next(iter(by_id), None)
    if state is None:
        return None
    state = state.split(":", 1)[0]
    return cfg_from_artifacts(state, arts)


def _artifacts_asof(registry, state: str, cutoff) -> dict:
    """artifacts(), restricted to versions at or before `cutoff` (the latest such version of each artifact_id); None
    cutoff means "now" (Registry.artifacts' own definition, unrestricted)."""
    if cutoff is None:
        return registry.artifacts(state)
    with registry.cp.as_role("dq_api") as c:
        rows = c.execute(
            "SELECT DISTINCT ON (artifact_id) artifact_id, version, kind, op, content, content_hash, cr_id, at "
            "FROM dq_ctl.config_artifacts WHERE state=%s AND at <= %s ORDER BY artifact_id, version DESC",
            (state, cutoff)).fetchall()
    return {r["artifact_id"]: dict(r) for r in rows if r["op"] == "PUT"} | \
        {r["artifact_id"]: dict(r) | {"content": None} for r in rows if r["op"] == "DELETE"}


def contributors_for(registry, state: str, artifact_ids: list[str]) -> dict:
    """{artifact_id: [{version, content_hash, cr_id, approvers}]} — every version of each named artifact, joined to
    its change request's APPROVED events (PFP :718). Mirrors gov_apply's own approver-counting join
    (governance_schema.py:213-217), read-only, as dq_api (granted SELECT on these tables)."""
    if not artifact_ids:
        return {}
    with registry.cp.as_role("dq_api") as c:
        arows = c.execute(
            "SELECT artifact_id, version, content_hash, cr_id FROM dq_ctl.config_artifacts "
            "WHERE state=%s AND artifact_id = ANY(%s) ORDER BY artifact_id, version", (state, artifact_ids)).fetchall()
        cr_ids = sorted({r["cr_id"] for r in arows})
        approvers: dict = {}
        if cr_ids:
            erows = c.execute(
                "SELECT cr_id, person_id FROM dq_ctl.config_cr_events WHERE cr_id = ANY(%s) AND event='APPROVED'",
                (cr_ids,)).fetchall()
            for e in erows:
                approvers.setdefault(e["cr_id"], set()).add(e["person_id"])
    out: dict = {}
    for r in arows:
        out.setdefault(r["artifact_id"], []).append({
            "version": r["version"], "content_hash": r["content_hash"], "cr_id": r["cr_id"],
            "approvers": sorted(approvers.get(r["cr_id"], set()))})
    return out


def compose_profile(registry, state: str, pipeline_id: str, as_of: str | None = None) -> dict:
    """PFP P1-2 (:706). Raises KeyError for an unknown pipeline_id or an unresolvable as_of (the route turns both
    into 404)."""
    from dqagent.harness.plan import order

    cutoff = _as_of_cutoff(registry, state, as_of)
    arts = _artifacts_asof(registry, state, cutoff)
    cfg = cfg_from_artifacts(state, arts)        # the one definition of "config from artifact rows" (registry.py:174)
    if cfg is None:
        raise KeyError(as_of if as_of is not None else state)   # an incomplete snapshot (post-build F1): no profile
    harness = cfg.get("harness") or {}
    entry = _pipeline_entry(harness, pipeline_id)
    if entry is None:
        raise KeyError(pipeline_id)
    gated = entry.get("gated_nodes") or []
    contracts_all = cfg["contracts"].get("relations") or cfg["contracts"]
    contracts_out = {rel: contracts_all.get(rel) for rel in gated if rel in contracts_all}

    approved = registry.approved(state)      # latest APPROVED, regardless of as_of (D9 scopes ARTIFACTS, not rules)
    rs = ruleset(state, cfg)
    rules_out = {k: {"hash": v["hash"], "status": v["status"], "gating": v["gating"],
                     "approved_hash": (approved.get(k) or {}).get("hash"),
                     "effective_equals_approved": v["hash"] == (approved.get(k) or {}).get("hash")}
                 for k, v in rs["rules"].items() if v.get("relation") in gated}

    tiers = {}
    for auth in pipeline_authorities(harness, cfg["contracts"], pipeline_id):
        tiers[auth] = registry.tier(state, _agent(), AUTONOMY_ACTION_CLASS, auth)

    # Part F P1-3 (K2c, PFP :839): per-dataset introduced_by / agent_tier / effective_tier, additive to the
    # existing return shape. `effective_tier` is present-tense (ORCH-1's ceiling is never as_of-scoped, same reason
    # `tiers` above is already present-tense), keyed by the pipeline's own gated relations only.
    datasets_out = {rel: dataset_governance(registry, state, rel, _authorities_of(cfg["contracts"], [rel]))
                    for rel in gated}

    monitors = {"content": (arts.get(f"{state}:monitor") or {}).get("content") or {}, "provenance": "FILE",
                "governed": False}     # L-10: monitor spec / bindings are sourced from files, not governed

    deps = [d for d in (harness.get("dependencies") or []) if d["domain"] in entry.get("domains", [])
            or d["depends_on"] in entry.get("domains", [])]

    routing_all = ((cfg.get("feeds") or {}).get("routing") or {}).get("routes") or []
    routing_out = [r for r in routing_all if r.get("pipeline") == pipeline_id]

    # contributors: the core governed artifacts this profile reads, plus any overlay naming one of this pipeline's
    # rules (an overlay artifact id is "<state>:overlay:<rule id>", registry.py:162-163 / :35 KINDS)
    rule_ids = {k.split(":", 1)[1].split("@", 1)[0] for k in rules_out}
    overlay_ids = {f"{state}:overlay:{rid}" for rid in rule_ids}
    artifact_ids = sorted(({f"{state}:harness", f"{state}:contracts", f"{state}:policy", f"{state}:feeds",
                           f"{state}:monitor"} | overlay_ids) & set(arts))
    contributors = contributors_for(registry, state, artifact_ids)

    out = {"pipeline": entry, "contracts": contracts_out, "rules": rules_out, "policy": cfg.get("policy") or {},
           "tiers": tiers, "datasets": datasets_out, "monitors": monitors, "dependencies": deps,
           "order": order(harness), "routing": routing_out, "contributors": contributors, "as_of": as_of}
    # D9: `approved_hash` / `effective_equals_approved` are explicitly present-tense (registry.approved() is NOT
    # as-of-scoped, by design -- D9 scopes ARTIFACTS, not rules). Hashing them into profile_digest would leak a
    # LATER rule approval into an "as of the past" snapshot's digest, breaking as_of stability for a property that
    # is honestly about "now", not "then". They stay in the response (still real, still live data) but are excluded
    # from what profile_digest hashes over.
    digest_rules = {k: {kk: vv for kk, vv in v.items() if kk not in ("approved_hash", "effective_equals_approved")}
                    for k, v in rules_out.items()}
    out["profile_digest"] = _h({**out, "rules": digest_rules})
    return out


def _agent() -> str:
    from dqagent.cp.agent_schema import DQ_AGENT
    return DQ_AGENT
