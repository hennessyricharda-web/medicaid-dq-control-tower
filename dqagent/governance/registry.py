"""Configuration registry service (Part D, D1). See dqagent/cp/governance_schema.py for the database side.

The EFFECTIVE configuration of a state is the latest applied version of each governed artifact in the CP. The
runtime compiles rules only from it. Files under config/state/<ST>/ are the SEED, proposed once at genesis, and
are never authority afterwards. The rule catalog, canonical elements and the IR / compiler code are governed by an
approved CATALOG digest; when the code on disk differs, the runtime reports DRIFT and release fails closed.

A change request carries server-computed IMPACT (every rule whose effective hash, status or gating changes) and the
BASE it was computed against (artifact versions, catalog digest, resulting ruleset version). The approver's
step-up signs sha256(signed_hash, verdict[, explanation hashes]), so they approve exactly the diff, the impact and
the explanation they saw. On apply the registry recompiles and refuses (STALE) when anything moved.

Part E adds the agent registry kinds (AGENT, ENTITLEMENT, AUTONOMY) and the CDE, CLASSIFICATION and MONITOR kinds.
For AUTONOMY / ENTITLEMENT / AGENT / CLASSIFICATION the DATABASE decides whether one approver suffices
(dq_ctl.gov_item_lower_only): only a change SQL proves lower-only gets one, and anything else (including a brand-new
grant) needs two. classify() below mirrors that rule for display; it can make a request stricter, never looser.
"""
from __future__ import annotations

import copy
import datetime as dt
import hashlib
import json
import re
import uuid

import psycopg
import yaml

from dqagent.hitl.decisions import STEP_UP_MAX_AGE_S, Denied, Replay
from dqagent.hitl.idp import STEP_UP_ACR, verify
from dqagent.rules.ir import IRError, canonical
from dqagent.rules.model import ROOT, compile_rules

KINDS = ("OVERLAY", "PROFILE", "FEEDS", "BINDINGS", "POLICY", "CONTRACTS", "HARNESS", "CATALOG", "REVOCATION",
         "AUTONOMY", "AGENT", "ENTITLEMENT", "CDE", "CLASSIFICATION", "MONITOR", "PIPELINE", "TASK_GATE")
AGENT_KINDS = ("AUTONOMY", "AGENT", "ENTITLEMENT", "CLASSIFICATION")      # lower-only rule, enforced in the database
TIERS = ("L1", "L2", "L3", "L4")
ACTION_CLASSES = ("RELEASE", "POST_BOARD", "PIN_MEMORY")
AGENT_STATUSES = ("REGISTERED", "ACTIVE", "SUSPENDED")
CLASS_RANK = {"none": 0, "coverage": 1, "program": 1, "payment": 2, "identity": 3, "clinical": 4, "part2": 5}
MONITOR_DEFAULTS = {"advisory": True, "min_periods": 8, "run_rules": {"R1": True, "R2": False}, "sigma": 3,
                    "psi": {"watch": 0.10, "shift": 0.25, "min_expected": 5}, "rebaseline": []}
GOV_DUTY = "governance"
GOV_AUTHORITY = "AUTH_RULES"
GOV_GROUP = "dq-duty-governance-rules"
CATALOG_FILES = ["rules/elements.yaml", "dqagent/rules/ir.py", "dqagent/rules/model.py", "dqagent/rules/basic.py"]
MODE_ORDER = ["A", "H_ON", "H_IN", "H_IN_2"]


def _h(obj) -> str:
    return hashlib.sha256(canonical(obj).encode()).hexdigest()


def catalog_digest() -> dict:
    files = sorted(str(p.relative_to(ROOT)) for p in (ROOT / "rules" / "catalog").rglob("*.yaml")) + CATALOG_FILES
    per = {f: hashlib.sha256((ROOT / f).read_bytes()).hexdigest() for f in files}
    return {"digest": _h(per), "files": per}


def iso_duration_seconds(s: str) -> int:
    """ISO 8601 duration (PnW | PnDTnHnMnS) -> seconds. Used for policy `veto_window` and `sla`."""
    m = re.fullmatch(r"P(?:(\d+)W)?(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?", str(s or ""))
    if not m or not any(m.groups()) or str(s).endswith("T"):
        raise ValueError(f"not an ISO 8601 duration: {s!r}")
    w, d, h, mi, se = (int(x or 0) for x in m.groups())
    return (((w * 7 + d) * 24 + h) * 60 + mi) * 60 + se


def _day(now: dt.datetime | None = None) -> dt.datetime:
    now = now or dt.datetime.now(dt.timezone.utc)
    return dt.datetime(now.year, now.month, now.day, tzinfo=dt.timezone.utc)


def agent_seed(state: str, now: dt.datetime | None = None) -> list[dict]:
    """AGENT / ENTITLEMENT / AUTONOMY genesis items from config/agents.yaml. Entitlements run from the genesis day for
    `entitlement_days`."""
    reg = yaml.safe_load(open(ROOT / "config" / "agents.yaml"))
    nb = _day(now)
    na = nb + dt.timedelta(days=int(reg.get("entitlement_days", 365)))
    items = []
    for aid, a in sorted((reg.get("agents") or {}).items()):
        items.append({"artifact_id": f"{state}:agent:{aid}", "kind": "AGENT", "op": "PUT",
                      "content": {"name": a["name"], "version": str(a["version"]), "status": a["status"],
                                  "seats": list(a.get("seats") or [])}})
        for cls, auths in sorted((a.get("entitlements") or {}).items()):
            items.append({"artifact_id": f"{state}:entitlement:{aid}:{cls}", "kind": "ENTITLEMENT", "op": "PUT",
                          "content": {"authorities": sorted(auths), "not_before": nb.isoformat(),
                                      "not_after": na.isoformat()}})
        for cls, tiers in sorted((a.get("autonomy") or {}).items()):
            items.append({"artifact_id": f"{state}:autonomy:{aid}:{cls}", "kind": "AUTONOMY", "op": "PUT",
                          "content": dict(sorted(tiers.items()))})
    return items


def classification_seed(state: str, elements_cfg: dict | None, contracts: dict | None) -> dict:
    """Defaults (rules/classification_defaults.yaml) MERGED with the state's contract control_tags, element by element;
    the more sensitive class wins, so the merge never downgrades a default."""
    out = dict((yaml.safe_load(open(ROOT / "rules" / "classification_defaults.yaml")) or {}).get("classes") or {})
    canon = (yaml.safe_load(open(ROOT / "rules" / "elements.yaml")) or {}).get("elements") or {}
    ents, cols = (elements_cfg or {}).get("entities") or {}, (elements_cfg or {}).get("columns") or {}
    rels = (contracts or {}).get("relations") or {}
    for el, meta in sorted(canon.items()):
        rel, col = ents.get(meta.get("entity")), cols.get(el)
        tags = ((rels.get(rel) or {}).get("control_tags") or {}).get(col) or []
        for t in tags:
            if t in CLASS_RANK and CLASS_RANK[t] > CLASS_RANK.get(out.get(el, "none"), 0):
                out[el] = t
    return dict(sorted(out.items()))


def _prompt_setting(c) -> None:
    """R4-5: the database checks the LLM half of the evidence against the CONFIGURED prompt version; the running code
    version (dq.code_sha256) is set by CP.connect_as."""
    from dqagent.llm import PROMPT_VERSION
    c.execute("SELECT set_config('dq.prompt_version', %s, true)", (PROMPT_VERSION,))


MINIMUM_AT_LEAST = ("human_decisions", "human_releases", "released_after_window")


def minimums_loosening(old: dict | None, new: dict | None) -> list[str]:
    """= dq_ctl.minimums_lower_only (SQL): lowering the promotion minimums needs two approvers, raising one. With no
    minimums before, a new set is compared with the production defaults."""
    from dqagent.cp.trace_schema import DEFAULT_PROMOTION_MINIMUMS
    if old is None and new is None:
        return []
    if new is None:
        return ["promotion_minimums removed"]
    why = []
    for tier, o in (old if old is not None else DEFAULT_PROMOTION_MINIMUMS).items():
        if not isinstance(o, dict):
            continue
        n = new.get(tier)
        if not isinstance(n, dict):
            why.append(f"promotion_minimums[{tier}] removed")
            continue
        for k, v in o.items():
            nv = n.get(k)
            if not isinstance(nv, (int, float)) or isinstance(nv, bool):
                why.append(f"promotion_minimums[{tier}][{k}] removed")
            elif (k in MINIMUM_AT_LEAST and nv < v) or (k not in MINIMUM_AT_LEAST and nv > v):
                why.append(f"promotion_minimums[{tier}][{k}] {v} -> {nv} lowers the promotion minimum")
    return why


def pipeline_task_gate_seed(state: str) -> list[dict]:
    """Part F P1-3 (K2a), genesis split (:805-810): 2 PIPELINE items (one per harness_w1.yaml pipeline) and 6
    TASK_GATE items, all `release_ceiling: INHERIT` (refused outside GENESIS by gov_item_valid's p_origin check):
    four `block` gates for the four w1 relations first (so ORCH-1's _introduce_datasets sees them before any
    consumer could claim FIRST), then the two `hold_until_terminal` gates from dbt_runner.HOLD_UNTIL_TERMINAL.
    K2a's own scope boundary (reported, not silently assumed): this does NOT change the existing HARNESS artifact's
    content (still the full harness_w1.yaml, pipelines and dependencies together) -- that recomposition
    (`cfg_from_artifacts`'s "harness" key feeding `_load_effective` / `plan.py` / `pipelines_view` unchanged) spans
    files this wave does not own (`runtime.py`, `harness/plan.py`) and no K2a-required test exercises it; it is
    flagged in this build's report for K2b, not attempted here to avoid an unreviewed cross-wave edit."""
    from dqagent.harness.dbt_runner import HOLD_UNTIL_TERMINAL

    harness = yaml.safe_load(open(ROOT / "config" / "harness_w1.yaml"))
    items: list[dict] = []
    for p in harness.get("pipelines") or []:
        items.append({"artifact_id": f"{state}:pipeline:{p['id']}", "kind": "PIPELINE", "op": "PUT", "content": {
            "adapter": "dbt_files", "scope": {"manifest_path": p["dag_source"]["path"]}, "platform": p["platform"],
            "domains": list(p.get("domains") or []), "layer_map": {}, "run_on": "manual"}})
    # the naming convention resolved for this wave (K2a-2): taskgate:<pipeline_id>:<relation_short_name>, the part
    # of the relation after its LAST dot. Collision-checked below, not merely trusted.
    seen: dict[str, str] = {}
    block_gates = []
    for p in harness.get("pipelines") or []:
        for rel in p.get("gated_nodes") or []:
            short = rel.rsplit(".", 1)[-1]
            if short in seen and seen[short] != p["id"]:
                raise IRError(f"TASK_GATE naming collision: {short!r} is a relation short name under both "
                              f"{seen[short]!r} and {p['id']!r}; taskgate:<pipeline>:<relation_short_name> is not unique")
            seen[short] = p["id"]
            block_gates.append((p["id"], short, rel))
    for pid, short, rel in block_gates:
        items.append({"artifact_id": f"{state}:taskgate:{pid}:{short}", "kind": "TASK_GATE", "op": "PUT", "content": {
            "task_id": short, "datasets": [rel], "mode": "block", "correlate": "LATEST_SEALED", "max_wait": "PT2H",
            "on_timeout": "HOLD", "release_ceiling": "INHERIT", "reintroduce": []}})
    # the two hold_until_terminal gates (dbt_runner.py's own HOLD_UNTIL_TERMINAL): these gate a gold dbt MODEL on
    # bronze relations spanning both w1 pipelines, so they are not scoped to a single pipeline id; "w1_dbt" (the
    # dbt project both pipelines' manifests build from) is this build's own default for the first segment, since
    # neither PFP nor the design names one -- reported, not silently assumed.
    for model, rels in sorted(HOLD_UNTIL_TERMINAL.items()):
        items.append({"artifact_id": f"{state}:taskgate:w1_dbt:{model}", "kind": "TASK_GATE", "op": "PUT", "content": {
            "task_id": model, "datasets": list(rels), "mode": "hold_until_terminal", "correlate": "LATEST_SEALED",
            "max_wait": "PT2H", "on_timeout": "HOLD", "release_ceiling": "INHERIT", "reintroduce": []}})
    return items


def seed_from_files(state: str) -> list[dict]:
    """The genesis items: what the files say today (proposed once, then never authority again)."""
    d = ROOT / "config" / "state" / state

    def y(p):
        return yaml.safe_load(open(p)) if p.exists() else None
    items = [{"artifact_id": f"{state}:profile", "kind": "PROFILE", "op": "PUT", "content": y(d / "profile.yaml")},
             {"artifact_id": f"{state}:feeds", "kind": "FEEDS", "op": "PUT", "content": y(d / "feeds.yaml")},
             {"artifact_id": f"{state}:elements", "kind": "BINDINGS", "op": "PUT", "content": y(d / "elements.yaml")},
             {"artifact_id": f"{state}:catalog", "kind": "CATALOG", "op": "PUT", "content": catalog_digest()}]
    for name, kind in (("policy_w1.yaml", "POLICY"), ("contracts_w1.yaml", "CONTRACTS")):
        if (d / name).exists():
            items.append({"artifact_id": f"{state}:{kind.lower()}", "kind": kind, "op": "PUT", "content": y(d / name)})
    items.append({"artifact_id": f"{state}:harness", "kind": "HARNESS", "op": "PUT",
                  "content": y(ROOT / "config" / "harness_w1.yaml")})
    for rid, ov in sorted(((y(d / "overlays.yaml") or {}).get("overlays") or {}).items()):
        items.append({"artifact_id": f"{state}:overlay:{rid}", "kind": "OVERLAY", "op": "PUT", "content": ov})
    # Part E: agent registry, critical data elements, sensitivity classification, monitor configuration
    items += agent_seed(state)
    items.append({"artifact_id": f"{state}:cde", "kind": "CDE", "op": "PUT",
                  "content": (y(ROOT / "rules" / "cde_defaults.yaml") or {}).get("cdes") or {}})
    items.append({"artifact_id": f"{state}:classification", "kind": "CLASSIFICATION", "op": "PUT",
                  "content": classification_seed(state, y(d / "elements.yaml"), y(d / "contracts_w1.yaml"))})
    items.append({"artifact_id": f"{state}:monitor", "kind": "MONITOR", "op": "PUT", "content": copy.deepcopy(MONITOR_DEFAULTS)})
    # Part F P1-3 (K2a), genesis split (:805-810): 2 PIPELINE + 6 TASK_GATE items, the 4 block gates FIRST so
    # ORCH-1's _introduce_datasets sees the w1 relations introduced by them, never by a later consumer gate.
    items += pipeline_task_gate_seed(state)
    return items


def cfg_from_artifacts(state: str, arts: dict[str, dict]) -> dict | None:
    """arts: artifact_id -> {kind, op, content}. None when the state has no ratified configuration."""
    get = lambda k: (arts.get(f"{state}:{k}") or {}).get("content")   # noqa: E731
    if get("profile") is None or get("elements") is None or get("feeds") is None:
        return None
    overlays = {a.split(":overlay:", 1)[1]: v["content"] for a, v in arts.items()
                if a.startswith(f"{state}:overlay:") and v.get("op", "PUT") == "PUT" and v.get("content")}
    return {"profile": get("profile"), "elements": get("elements"), "feeds": get("feeds"), "overlays": overlays,
            "policy": get("policy"), "contracts": get("contracts"), "harness": get("harness"),
            "catalog": get("catalog")}


def ruleset(state: str, cfg: dict, on: dt.date | None = None) -> dict:
    rules = compile_rules(state, cfg=cfg, on=on or dt.date.today())
    rs = {f"{state}:{r.id}@{r.entity}": {"hash": r.effective_hash, "status": r.status, "gating": r.header.get("gating"),
                                          "relation": r.relation, "kind": r.kind} for r in rules}
    return {"rules": rs, "version": _h(sorted((k, v["hash"]) for k, v in rs.items())), "compiled": rules}


def _expected_kind(state: str, artifact_id: str) -> str | None:
    rest = artifact_id[len(state) + 1:]
    if rest.startswith("overlay:"):
        return "OVERLAY"
    if rest.startswith("revocation:"):
        return "REVOCATION"
    if re.fullmatch(r"autonomy:[a-z0-9-]{1,48}:(RELEASE|POST_BOARD|PIN_MEMORY)", rest):
        return "AUTONOMY"
    if re.fullmatch(r"agent:[a-z0-9-]{1,48}", rest):
        return "AGENT"
    if re.fullmatch(r"entitlement:[a-z0-9-]{1,48}:(RELEASE|POST_BOARD|PIN_MEMORY)", rest):
        return "ENTITLEMENT"
    if re.fullmatch(r"pipeline:[a-z0-9_-]{1,48}", rest):
        return "PIPELINE"
    if re.fullmatch(r"taskgate:[a-z0-9_-]{1,48}:[a-z0-9_-]{1,48}", rest):
        return "TASK_GATE"
    return {"profile": "PROFILE", "feeds": "FEEDS", "elements": "BINDINGS", "policy": "POLICY",
            "contracts": "CONTRACTS", "harness": "HARNESS", "catalog": "CATALOG", "cde": "CDE",
            "classification": "CLASSIFICATION", "monitor": "MONITOR"}.get(rest)


MANDATORY = ("PROFILE", "FEEDS", "BINDINGS", "POLICY", "CONTRACTS", "HARNESS", "CATALOG")
ALWAYS_LOOSENING = ("PROFILE", "CONTRACTS", "HARNESS", "CATALOG")    # default to loosening when unsure (F2)


def _mode_lower(a, b) -> bool:
    return a in MODE_ORDER and b in MODE_ORDER and MODE_ORDER.index(b) < MODE_ORDER.index(a)


def _ts(v):
    try:
        t = dt.datetime.fromisoformat(str(v))
        return t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)
    except (TypeError, ValueError):
        return None


def lower_only(kind: str, old, new, op: str) -> bool:
    """Python mirror of dq_ctl.gov_item_lower_only (display only; the database decides)."""
    if kind == "FEEDS":
        return not feeds_loosening(old, new if op != "DELETE" else None)
    if kind == "POLICY":
        return not minimums_loosening((old or {}).get("promotion_minimums") if old else None,
                                      (new or {}).get("promotion_minimums") if op != "DELETE" else None)
    rank = {t: i + 1 for i, t in enumerate(TIERS)}
    # K2a-4, pre-existing defect fix (not introduced by K2): this function's fallthrough `return True` at the end
    # used to make HARNESS default to lower-only, the OPPOSITE of dq_ctl.gov_item_lower_only's required rule. Fixed
    # here on the Python mirror side too (the database's own fix is in governance_schema.py's gov_item_lower_only),
    # since this function is documented as a display-only mirror of the database's real decision -- leaving only
    # the database fixed would make the Tower's impact preview lie about what will actually happen.
    if kind == "HARNESS":
        return False
    if kind == "PIPELINE":
        return False
    if kind == "EVAL_CASE":
        return False
    if kind == "TASK_GATE":
        if op == "DELETE":
            return False
        if old is None:
            return False
        new, old = new or {}, old or {}
        mw_new, mw_old = _cadence_s(new.get("max_wait")), _cadence_s(old.get("max_wait"))
        if mw_new is None or mw_old is None:
            return False
        return bool(
            set(old.get("datasets") or []) <= set(new.get("datasets") or [])
            and (new.get("release_ceiling") == old.get("release_ceiling")
                 or (new.get("release_ceiling") in rank and old.get("release_ceiling") in rank
                     and rank[new["release_ceiling"]] <= rank[old["release_ceiling"]]))
            and mw_new <= mw_old
            and (new.get("on_timeout") == old.get("on_timeout") or new.get("on_timeout") == "HOLD")
            and new.get("mode") == old.get("mode")            # ADJ-2: mode is unchanged
            and new.get("correlate") == old.get("correlate")
            and not (new.get("reintroduce") or []))
    if kind == "AUTONOMY":
        if old is None:
            return False
        if op == "DELETE":
            return True
        return all(t in rank and rank[t] <= rank.get(old.get(a), 1) for a, t in (new or {}).items())
    if kind == "ENTITLEMENT":
        if old is None:
            return False
        if op == "DELETE":
            return True
        rest = lambda c: {k: v for k, v in c.items() if k not in ("authorities", "not_before", "not_after")}  # noqa: E731
        try:
            return (set(new.get("authorities") or []) <= set(old.get("authorities") or [])
                    and _ts(new["not_after"]) <= _ts(old["not_after"]) and _ts(new["not_before"]) >= _ts(old["not_before"])
                    and rest(new) == rest(old))
        except (KeyError, TypeError):
            return False
    if kind == "AGENT":
        if old is None or op == "DELETE":
            return False
        rest = lambda c: {k: v for k, v in c.items() if k not in ("status", "seats")}  # noqa: E731
        return (rest(new or {}) == rest(old) and set((new or {}).get("seats") or []) <= set(old.get("seats") or [])
                and ((new or {}).get("status") == old.get("status") or (new or {}).get("status") in ("SUSPENDED", "REGISTERED")))
    if kind == "CLASSIFICATION":
        if op == "DELETE":
            return old is None
        if any(v not in CLASS_RANK for v in (new or {}).values()):
            return False
        return all(el in (new or {}) and (new[el] == c or CLASS_RANK[new[el]] > CLASS_RANK.get(c, 99))
                   for el, c in (old or {}).items())
    return True


def classify(old_arts: dict, approved: dict, new_rs: dict, items: list[dict]) -> tuple[bool, list[str]]:
    """LOOSENING (needs two approvers): anything that reduces what is checked or who must look. Conservative:
    when a change cannot be shown to be tightening it is classified as loosening (post-build F2)."""
    why = []
    for it in items:
        aid, kind, op = it["artifact_id"], it["kind"], it["op"]
        new, old = it.get("content") if op == "PUT" else None, (old_arts.get(aid) or {}).get("content")
        if kind in AGENT_KINDS and not lower_only(kind, old, new, op):
            why.append(f"{aid}: {'new grant' if old is None else 'not provably lower-only'} "
                       f"({'promotion / grant' if kind != 'CLASSIFICATION' else 'classification downgrade'}): two people")
        if kind in ALWAYS_LOOSENING and old is not None:
            why.append(f"{aid}: {kind} changes are reviewed as loosening by default")
        if kind == "OVERLAY":
            if op == "DELETE" and old and old.get("verb") not in ("waive", "narrow"):
                why.append(f"{aid}: removing a {old.get('verb')} overlay relaxes the rule")
            if new and new.get("verb") in ("narrow", "waive"):
                why.append(f"{aid}: verb {new.get('verb')} reduces what is checked")
            if old and new:
                if old.get("verb") != new.get("verb"):
                    why.append(f"{aid}: overlay verb {old.get('verb')} -> {new.get('verb')}")
                for k, v in (new.get("thresholds") or {}).items():
                    ov = (old.get("thresholds") or {}).get(k)
                    if ov is not None and ((k == "max" and v > ov) or (k == "min" and v < ov)):
                        why.append(f"{aid}: {k} {ov} -> {v} relaxes the current overlay")
                if old.get("priority") and new.get("priority") and old["priority"] != new["priority"]:
                    order = ["Low", "Medium", "High", "Critical"]
                    if order.index(new["priority"]) < order.index(old["priority"]):
                        why.append(f"{aid}: priority lowered")
                if new.get("expires") and (not old.get("expires") or str(new["expires"]) < str(old["expires"])) \
                        and new.get("verb") not in ("waive", "narrow"):
                    why.append(f"{aid}: a tightening overlay now expires sooner")
        if kind == "FEEDS":
            why += feeds_loosening(old, new if op != "DELETE" else None)    # = dq_ctl.feeds_lower_only (SQL)
        if kind == "BINDINGS" and old:
            gone = set(old.get("columns", {})) - set(((new or {}).get("columns") or {}))
            if gone:
                why.append(f"bindings removed: {sorted(gone)[:5]}")
        if kind == "POLICY":                                     # R4-5 (= dq_ctl.minimums_lower_only)
            why += minimums_loosening((old or {}).get("promotion_minimums") if old else None,
                                      (new or {}).get("promotion_minimums") if op != "DELETE" else None)
        if kind == "POLICY" and old:
            npol = (new or {}).get("policy") or {}
            for auth, pa in (old.get("policy") or {}).items():
                npa = npol.get(auth)
                if npa is None:
                    why.append(f"policy[{auth}] removed")
                    continue
                for k, m in pa.items():
                    if k not in npa:
                        why.append(f"policy[{auth}][{k}] removed (falls back to a default)")
                    elif _mode_lower(m, npa[k]):
                        why.append(f"policy[{auth}][{k}] {m} -> {npa[k]} lowers human review")
            for k, m in (old.get("floor") or {}).items():
                nm = ((new or {}).get("floor") or {}).get(k)
                if nm is None or _mode_lower(m, nm):
                    why.append(f"floor[{k}] {m} -> {nm} lowers human review")
    # rule-level, against what is APPROVED today (catches calendar-driven changes too)
    for k, o in approved.items():
        n = new_rs["rules"].get(k)
        if (o.get("status") == "ACTIVE") and (n is None or n["status"] != "ACTIVE"):
            why.append(f"{k} leaves ACTIVE ({'removed' if n is None else n['status']})")
        elif n and o.get("gating") == "gate" and n["gating"] == "advisory":
            why.append(f"{k} gate -> advisory")
    return bool(why), sorted(set(why))


FEEDS_TOP_TIGHTENING = ("feeds", "not_delivered_elements", "part2_programs", "part2_relations")
FEED_TIGHTENING = ("part2_program", "delivered", "empty_ok", "cadence")


def _cadence_s(every) -> int | None:
    m = re.fullmatch(r"P(\d+)M", str(every or ""))
    if m:
        return int(m.group(1)) * 31 * 86400                 # = runtime.cadence_seconds / dq_ctl.cadence_seconds
    try:
        return iso_duration_seconds(every)
    except ValueError:
        return None


def feeds_loosening(old: dict | None, new: dict | None) -> list[str]:
    """Part F R5 (HIGH): the FEEDS reasons a change is NOT lower-only (empty = lower-only). The same rules as
    dq_ctl.feeds_lower_only, which the database applies whatever the caller says: every key outside the tightening
    set is unchanged (routing included), and each tightening key moves only in its tightening direction."""
    if old is None or new is None:
        return ["FEEDS artifact created or deleted"]
    why = []
    rest = lambda d, ks: {k: v for k, v in d.items() if k not in ks}        # noqa: E731
    if rest(new, FEEDS_TOP_TIGHTENING) != rest(old, FEEDS_TOP_TIGHTENING):
        changed = sorted(k for k in set(new) | set(old) if k not in FEEDS_TOP_TIGHTENING and new.get(k) != old.get(k))
        why += ["inbound file routing changed" if k == "routing" else f"FEEDS {k} changed" for k in changed]
    for k in ("part2_programs", "part2_relations"):
        if set(old.get(k) or []) - set(new.get(k) or []):
            why.append(f"{k} shrinks (Part 2 protection removed)")
    if set(new.get("not_delivered_elements") or []) - set(old.get("not_delivered_elements") or []):
        why.append("elements declared not delivered")
    of, nf = old.get("feeds") or {}, new.get("feeds") or {}
    if set(of) != set(nf):
        why.append("feeds added or removed")
    for f in sorted(set(of) & set(nf)):
        o, n = of[f], nf[f]
        if not isinstance(o, dict) or not isinstance(n, dict):
            if o != n:
                why.append(f"feed {f} changed")
            continue
        if rest(n, FEED_TIGHTENING) != rest(o, FEED_TIGHTENING):
            why.append(f"feed {f}: " + ", ".join(sorted(k for k in set(n) | set(o)
                                                        if k not in FEED_TIGHTENING and n.get(k) != o.get(k))) + " changed")
        if n.get("part2_program") != o.get("part2_program") and n.get("part2_program") is not True:
            why.append(f"feed {f}: part2_program {o.get('part2_program')} -> {n.get('part2_program')}")
        if n.get("delivered") != o.get("delivered") and not (n.get("delivered") is True and o.get("delivered") is False):
            why.append(f"feed {f} becomes undelivered")
        if n.get("empty_ok") != o.get("empty_ok") and not (o.get("empty_ok") is True and n.get("empty_ok") is False):
            why.append(f"feed {f}: empty_ok {o.get('empty_ok')} -> {n.get('empty_ok')}")
        oc, nc = o.get("cadence"), n.get("cadence")
        if nc != oc:
            ok = isinstance(oc, dict) and isinstance(nc, dict) and rest(nc, ("every",)) == rest(oc, ("every",))
            so, sn = (_cadence_s(oc.get("every")), _cadence_s(nc.get("every"))) if ok else (None, None)
            if not ok or so is None or sn is None or sn >= so:
                why.append(f"feed {f}: cadence {oc} -> {nc} is not shorter")
    return why


def _check_parte_item(state: str, it: dict, cand: dict) -> None:
    """Shape of the Part E kinds (the database re-checks in dq_ctl.gov_item_valid). Raises IRError."""
    kind, aid, c = it["kind"], it["artifact_id"], it.get("content")
    parts = aid.split(":")
    if it["op"] == "DELETE":
        if kind in ("CDE", "CLASSIFICATION", "MONITOR"):
            raise IRError(f"{kind} cannot be deleted (change it instead)")
        return
    if not isinstance(c, dict):
        raise IRError(f"{aid}: content must be an object")
    if kind in ("AUTONOMY", "ENTITLEMENT"):
        agent = parts[2]
        if (cand.get(f"{state}:agent:{agent}") or {}).get("content") is None:
            raise IRError(f"{aid}: agent {agent} is not registered (propose its AGENT artifact first)")
    if kind == "AUTONOMY":
        bad = {a: t for a, t in c.items() if t not in TIERS or not re.fullmatch(r"AUTH_[A-Z0-9_]{1,40}", str(a))}
        if bad:
            raise IRError(f"{aid}: tiers must be L1..L4 per AUTH_ authority: {bad}")
    elif kind == "ENTITLEMENT":
        nb, na = _ts(c.get("not_before")), _ts(c.get("not_after"))
        if not isinstance(c.get("authorities"), list) or not all(re.fullmatch(r"AUTH_[A-Z0-9_]{1,40}", str(a))
                                                                 for a in c["authorities"]):
            raise IRError(f"{aid}: authorities must be a list of AUTH_ ids")
        if nb is None or na is None or not nb < na:
            raise IRError(f"{aid}: not_before < not_after, ISO 8601 timestamps")
    elif kind == "AGENT":
        if c.get("status") not in AGENT_STATUSES or not isinstance(c.get("seats", []), list):
            raise IRError(f"{aid}: status {AGENT_STATUSES} and a list of seats")
    elif kind == "CLASSIFICATION":
        bad = {e: v for e, v in c.items() if v not in CLASS_RANK}
        if bad:
            raise IRError(f"{aid}: unknown classes {bad}; one of {sorted(CLASS_RANK)}")
    elif kind == "CDE":
        for el, v in c.items():
            mv = (v or {}).get("mvdq") or {}
            if not all(isinstance(mv.get(k), (int, float)) and 0 <= mv[k] <= 1 for k in ("completeness_min", "validity_min")):
                raise IRError(f"{aid}: {el} needs mvdq.completeness_min and validity_min in [0, 1]")
    elif kind == "MONITOR":
        if not isinstance(c.get("rebaseline", []), list):
            raise IRError(f"{aid}: rebaseline is a list of series ids")


def explanation_ok(ref) -> bool:
    return ref is None or (isinstance(ref, dict) and set(ref) == {"facts_hash", "text_hash"}
                           and all(re.fullmatch(r"[0-9a-f]{64}", str(v)) for v in ref.values()))


def _impact(approved: dict, new_rs: dict) -> list[dict]:
    """Every rule whose new version differs from the APPROVED one (not from a same-day recompile), so a change
    the calendar made (an expired overlay, a closed feed window) shows up here and needs its own approval."""
    out = []
    for k in sorted(set(approved) | set(new_rs["rules"])):
        o, n = approved.get(k), new_rs["rules"].get(k)
        if (o or {}).get("hash") != (n or {}).get("hash"):
            out.append({"rule": k, "before": o, "after": n})
    return out


class Registry:
    def __init__(self, cp, jwks_provider):
        self.cp, self.jwks = cp, jwks_provider

    # -- reads ---------------------------------------------------------------------------------------------
    def artifacts(self, state: str, c=None) -> dict[str, dict]:
        q = "SELECT artifact_id, version, kind, op, content, content_hash, cr_id, at FROM dq_ctl.config_effective WHERE state=%s"
        if c is not None:
            rows = c.execute(q, (state,)).fetchall()
        else:
            with self.cp.as_role("dq_registry_svc") as cc:
                rows = cc.execute(q, (state,)).fetchall()
        return {r["artifact_id"]: dict(r) for r in rows if r["op"] == "PUT"} | \
            {r["artifact_id"]: dict(r) | {"content": None} for r in rows if r["op"] == "DELETE"}

    def effective(self, state: str) -> dict | None:
        return cfg_from_artifacts(state, self.artifacts(state))

    def approved(self, state: str) -> dict:
        """artifact -> {hash, status, gating, relation} of the latest APPROVED version (registry)."""
        with self.cp.as_role("dq_registry_svc") as c:
            rows = c.execute("SELECT DISTINCT ON (artifact_id) artifact_id, semantic_hash, state, rule_meta "
                             "FROM dq_ctl.registry_events WHERE artifact_kind='rule' AND artifact_id LIKE %s "
                             "ORDER BY artifact_id, id DESC", (state + ":%",)).fetchall()
        return {r["artifact_id"]: {"hash": r["semantic_hash"], **(r["rule_meta"] or {})} for r in rows
                if r["state"] == "APPROVED"}

    # -- propose -------------------------------------------------------------------------------------------
    def _candidate(self, state: str, arts: dict, items: list[dict]) -> dict:
        new = copy.deepcopy({k: {"op": v["op"], "content": v["content"], "kind": v["kind"]} for k, v in arts.items()})
        for it in items:
            if it["kind"] not in KINDS:
                raise IRError(f"unknown artifact kind {it['kind']}")
            if not it["artifact_id"].startswith(f"{state}:"):
                raise IRError(f"artifact {it['artifact_id']} is not in state {state}")
            if _expected_kind(state, it["artifact_id"]) != it["kind"]:      # post-build F2: no kind spoofing
                raise IRError(f"artifact {it['artifact_id']} is not of kind {it['kind']}")
            if it["kind"] in MANDATORY and (it["op"] == "DELETE" or not it.get("content")):
                raise IRError(f"{it['kind']} cannot be deleted or emptied (post-build F1)")
            if it["op"] not in ("PUT", "DELETE"):
                raise IRError("op PUT | DELETE")
            if it["kind"] == "REVOCATION":
                c = it.get("content") or {}
                if it["op"] != "PUT" or not c.get("rule", "").startswith(f"{state}:") or not c.get("hash") \
                        or len(c.get("reason", "")) < 10:
                    raise IRError("a REVOCATION names {rule: <state>:<rule>@<entity>, hash, reason (10+ chars)}")
            new[it["artifact_id"]] = {"op": it["op"], "content": it.get("content") if it["op"] == "PUT" else None,
                                      "kind": it["kind"]}
        for it in items:
            if it["kind"] in ("AUTONOMY", "AGENT", "ENTITLEMENT", "CDE", "CLASSIFICATION", "MONITOR"):
                _check_parte_item(state, it, new)
        return new

    def _base(self, state: str, arts: dict, cand_rs_version: str) -> dict:
        return {"versions": {k: v.get("version", 0) for k, v in sorted(arts.items())},
                "catalog_digest": catalog_digest()["digest"], "ruleset_after": cand_rs_version}

    def propose(self, state: str, author: str, rationale: str, items: list[dict], origin: str = "HUMAN",
                origin_detail: dict | None = None) -> dict:
        arts = self.artifacts(state)
        new_arts = self._candidate(state, arts, items)
        new_cfg = cfg_from_artifacts(state, new_arts)
        if new_cfg is None:
            raise IRError("the change leaves the state without profile / feeds / bindings")
        if new_cfg.get("policy") is not None or any(i["kind"] == "POLICY" for i in items):
            from dqagent.hitl.modes import validate_policy
            try:
                validate_policy(new_cfg.get("policy") or {})
                pol = new_cfg.get("policy") or {}
                if "veto_window" in pol:
                    iso_duration_seconds(pol["veto_window"])
                for m, d in (pol.get("sla") or {}).items():
                    iso_duration_seconds(d)
            except (KeyError, ValueError, TypeError, AttributeError) as e:
                raise IRError(f"policy refused: {e}") from e
        if any(i["kind"] == "FEEDS" for i in items):           # Part F F-02: the routing is governed with FEEDS
            from dqagent.ingest.routing import Routing
            Routing.of(new_cfg.get("feeds")).validate(new_cfg.get("contracts") or {}, new_cfg.get("harness") or {})
        self.revocation_check(state, items)
        approved = self.approved(state)
        new_rs = ruleset(state, new_cfg)                        # raises IRError on loosening thresholds / unknown ids
        for it in items:                                         # red-team I1: a revocation brings its replacement
            if it["kind"] == "REVOCATION":
                rv = it["content"]
                if (new_rs["rules"].get(rv["rule"]) or {}).get("hash") == rv["hash"]:
                    raise IRError(f"revoking {rv['rule']} needs a replacement in the same request (an overlay that "
                                  "changes it, or a waive): otherwise the revoked version would stay in force")
        revoked = self.revoked(state)
        loosening, why = classify(arts, approved, new_rs, items)
        items = [{**it, "content_hash": _h(it.get("content"))} for it in items]
        impact_rules = _impact(approved, new_rs)
        for ch in impact_rules:                                  # a revoked version can never come back (C1)
            if ch["after"] and (ch["rule"], ch["after"]["hash"]) in revoked:
                raise IRError(f"{ch['rule']} version {ch['after']['hash'][:12]} was REVOKED; change the rule so it "
                              "compiles to a new version instead")
        impact = {"rules": impact_rules, "loosening_reasons": why, "ruleset_after": new_rs["version"],
                  "summary": {"changed": len(impact_rules), "rules_total": len(new_rs["rules"])}}
        tr = self.track_record_snapshot(state, items)            # Part E §1.2: approvers sign the evidence they saw
        if tr is not None:
            impact["track_record"] = tr
        signed_rules = {k: {kk: v[kk] for kk in ("hash", "status", "gating", "relation")}
                        for k, v in sorted(new_rs["rules"].items())}
        base = self._base(state, arts, new_rs["version"])
        cr_id = "CR-" + uuid.uuid4().hex[:10]
        signed = _h({"cr_id": cr_id, "state": state, "items": items, "impact": impact, "base": base,
                     "signed_rules": signed_rules})
        with self.cp.as_role("dq_api") as c:
            c.execute("SELECT dq_ctl.gov_propose(%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s::jsonb,%s,%s,%s::jsonb,%s::jsonb)",
                      (cr_id, state, author, origin, rationale, json.dumps(items, default=str),
                       json.dumps(impact, default=str), json.dumps(base), loosening, signed,
                       json.dumps(signed_rules), json.dumps(origin_detail or {})))
            # the DATABASE decides how many approvers this request needs (Part E C4); report what it stored
            db = c.execute("SELECT loosening, approvals_required FROM dq_ctl.config_change_requests WHERE cr_id=%s",
                           (cr_id,)).fetchone()
        return {"cr_id": cr_id, "impact": impact, "loosening": db["loosening"],
                "approvals_required": db["approvals_required"]}

    def track_record_snapshot(self, state: str, items: list[dict]) -> dict | None:
        """{rows: {autonomy artifact: [track_record rows, each with its current_version block]}, minimums, evidence,
        task_gate, hash} for every AUTONOMY item, else None (Part E §1.2; R4-5 §4). K9 (ORCH-1 step b,
        K7_K8_K9_DESIGN.md §3.3 point 3): `task_gate` is a NEW, additive top-level key (not folded into `evidence`,
        per the orchestrator's decision), {artifact_id: has_evidence bool} for every TASK_GATE item in THIS
        proposal, so approvers see the gate evidence alongside the AUTONOMY evidence in the one snapshot they sign.
        `has_evidence` here checks `dq_ctl.cr_evidence` against a digest of ONLY the TASK_GATE PUT items (not the
        whole proposal), since `ceiling_promotion_refusal`/`mode_promotion_refusal` both digest the full `r.items`
        at decide-time via `dq_ctl._items_digest`; a backtest report linked before this proposal's final item list
        is known would not match that full-items digest, so this field is an advance, best-effort signal for the
        approver (an item already has a linked report under SOME digest), not a guarantee the gov_decide check
        passes -- the gov_decide/gov_apply checks remain the actual enforcement, in the database, never bypassed by
        what this snapshot shows. Returns None only when there is NEITHER an AUTONOMY item NOR a TASK_GATE item
        (this function's prior behavior for AUTONOMY-only or empty proposals is unchanged)."""
        arts = sorted({it["artifact_id"] for it in items if it["kind"] == "AUTONOMY"})
        gates = sorted({it["artifact_id"] for it in items if it["kind"] == "TASK_GATE" and it["op"] == "PUT"})
        if not arts and not gates:
            return None
        rows = {}
        task_gate = {}
        with self.cp.as_role("dq_registry_svc") as c:
            _prompt_setting(c)
            for aid in arts:
                _, _, agent, cls = aid.split(":")
                rows[aid] = [{k: (v.isoformat() if isinstance(v, dt.datetime) else v) for k, v in r.items()}
                             for r in c.execute("SELECT * FROM dq_ctl.track_record WHERE agent_id=%s AND action_class=%s "
                                                "ORDER BY authority", (agent, cls)).fetchall()]
            mins = c.execute("SELECT dq_ctl.promotion_minimums(%s) m", (state,)).fetchone()["m"]
            evidence = c.execute("SELECT dq_ctl.evidence_status() e").fetchone()["e"]
            for aid in gates:
                task_gate[aid] = bool(c.execute(
                    "SELECT 1 FROM dq_ctl.cr_evidence e WHERE e.items_digest = dq_ctl._items_digest(%s::jsonb) LIMIT 1",
                    (json.dumps([it for it in items if it["kind"] == "TASK_GATE" and it["op"] == "PUT"],
                                default=str),)).fetchone())
        return {"rows": rows, "minimums": mins, "evidence": evidence, "task_gate": task_gate,
                "hash": _h([rows, mins, evidence, task_gate])}

    # -- decide --------------------------------------------------------------------------------------------
    def challenge(self, cr_id: str, verdict: str, explanation_ref: dict | None = None) -> str:
        """The step-up nonce. With an explanation, its facts / text hashes are bound into the nonce too (Part E §7),
        so the approver signs the explanation they were shown."""
        with self.cp.as_role("dq_api") as c:
            r = c.execute("SELECT signed_hash FROM dq_ctl.config_change_requests WHERE cr_id=%s", (cr_id,)).fetchone()
        if not r:
            raise Denied("unknown change request")
        if not explanation_ok(explanation_ref):
            raise Denied("explanation_ref is {facts_hash, text_hash} (sha256 hex)")
        tail = f"|{explanation_ref['facts_hash']}|{explanation_ref['text_hash']}" if explanation_ref else ""
        return hashlib.sha256(f"{r['signed_hash']}|{verdict}{tail}".encode()).hexdigest()

    def decide(self, cr_id: str, verdict: str, step_up_token: str, session_person: str, duties: list[str],
               explanation_ref: dict | None = None) -> dict:
        if verdict not in ("APPROVED", "REJECTED"):
            raise Denied("verdict APPROVED | REJECTED")
        if not step_up_token:
            raise Denied("a configuration decision requires a challenge-bound step-up token")
        claims = verify(step_up_token, self.jwks(), max_age_s=STEP_UP_MAX_AGE_S)
        if claims.get("acr") != STEP_UP_ACR:
            raise Denied("decision requires a step-up authentication")
        if claims["emp_id"] != session_person:
            raise Denied("step-up identity differs from the signed-in user")
        if f"{GOV_DUTY}:{GOV_AUTHORITY}" not in duties:
            raise Denied("missing the effective governance duty (token claim AND roster)")
        if claims.get("nonce") != self.challenge(cr_id, verdict, explanation_ref):
            raise Denied("step-up token is not bound to this change request (challenge mismatch)")
        with self.cp.as_role("dq_api") as c:
            _prompt_setting(c)                                   # R4-5: the configured prompt for the evidence check
            try:
                c.execute("SELECT dq_ctl.gov_decide(%s,%s,%s,%s,%s::jsonb)",
                          (cr_id, claims["emp_id"], claims["jti"], verdict,
                           json.dumps(explanation_ref) if explanation_ref else None))
            except psycopg.errors.CheckViolation as e:
                raise Denied(str(e).splitlines()[0]) from e
            except psycopg.errors.UniqueViolation as e:
                raise Replay(str(e).splitlines()[0]) from e
        return {"cr_id": cr_id, "verdict": verdict}

    # -- apply ---------------------------------------------------------------------------------------------
    def apply(self, cr_id: str, expected_state: str | None = None, deployment: str | None = None,
              idp_kind: str | None = None) -> str:
        """Recompile the candidate NOW and hand the database only the recomputed BASE. The database applies the
        SIGNED items and rule list stored with the request (post-build F5); a base that moved means STALE.
        Part F R4-2: `deployment` is the applying runtime's deployment; applying a GENESIS request binds it in the
        same transaction (dq_ctl.bind_deployment derives the genesis kind and refuses a disallowed pairing)."""
        with self.cp.as_role("dq_registry_svc") as c:
            r = c.execute("SELECT state, items FROM dq_ctl.config_change_requests WHERE cr_id=%s", (cr_id,)).fetchone()
            if r is None:
                raise Denied("unknown change request")
            if expected_state and r["state"] != expected_state:
                raise Denied(f"change request is for state {r['state']}, not {expected_state}")
            arts = self.artifacts(r["state"], c)
        try:
            new_cfg = cfg_from_artifacts(r["state"], self._candidate(r["state"], arts, r["items"]))
            base = self._base(r["state"], arts, ruleset(r["state"], new_cfg)["version"])
        except IRError:
            base = {"recompile": "failed"}
        with self.cp.as_role("dq_registry_svc") as c:
            _prompt_setting(c)
            try:
                return c.execute("SELECT dq_ctl.gov_apply(%s,%s::jsonb,%s,%s) AS s",
                                 (cr_id, json.dumps(base), deployment, idp_kind)).fetchone()["s"]
            except psycopg.errors.CheckViolation as e:
                # K1 defect fix: a GENESIS apply can ALSO be refused by bind_deployment's own R4-2 checks (an
                # unknown/disallowed deployment, a missing OIDC IdP, a re-binding conflict) -- those are not R4-5
                # promotion refusals, and callers (this test suite's direct registry.apply() calls, and runtime.py's
                # _apply_change, which re-catches CheckViolation to relabel it NotConfiguredError) need the RAW
                # exception, not Denied. Only an R4-5 evidence/minimum-record refusal is wrapped here -- both of
                # promotion_refusal's RETURN format(...) messages start literally with "promotion requires"
                # (trace_schema.py), which is the one reliable discriminator (message substrings like "deployment"
                # or "genesis" are not: e.g. "production needs an OIDC IdP" names neither).
                if str(e).startswith("promotion requires"):
                    raise Denied(str(e).splitlines()[0]) from e
                raise

    # -- Part F R4-2: the deployment binding ---------------------------------------------------------------------
    def bind_deployment(self, deployment: str, idp_kind: str) -> str:
        """Bind (or confirm) this control plane's deployment and IdP kind (LOCAL | OIDC) as the registry service. The
        database derives the genesis kind (TEST | GOVERNED) and refuses a disallowed or conflicting binding
        (CheckViolation). Returns the kind."""
        with self.cp.as_role("dq_registry_svc") as c:
            return c.execute("SELECT dq_ctl.bind_deployment(%s,%s) AS g", (deployment, idp_kind)).fetchone()["g"]

    def deployment_binding(self) -> dict:
        """The bound deployment; no row reads as production (dqagent.deploy.bound)."""
        from dqagent import deploy
        with self.cp.as_role("dq_registry_svc") as c:
            return deploy.bound(c)

    def deployment_event(self, event: str, person_id: str | None) -> int:
        with self.cp.as_role("dq_registry_svc") as c:
            return c.execute("SELECT dq_ctl.deployment_event(%s,%s) AS i", (event, person_id)).fetchone()["i"]

    def revoked(self, state: str) -> set:
        with self.cp.as_role("dq_registry_svc") as c:
            return {(r["artifact_id"], r["semantic_hash"]) for r in c.execute(
                "SELECT artifact_id, semantic_hash FROM dq_ctl.registry_events WHERE state='REVOKED' AND artifact_id LIKE %s",
                (state + ":%",)).fetchall()}

    def revocation_check(self, state: str, items: list[dict]):
        """A revocation must name a version that was APPROVED at some point (it cannot invent hashes)."""
        revs = [i["content"] for i in items if i["kind"] == "REVOCATION"]
        if not revs:
            return
        with self.cp.as_role("dq_registry_svc") as c:
            for r in revs:
                if not c.execute("SELECT 1 FROM dq_ctl.registry_events WHERE artifact_id=%s AND semantic_hash=%s "
                                 "AND state='APPROVED'", (r["rule"], r["hash"])).fetchone():
                    raise IRError(f"{r['rule']} version {r['hash']} was never approved; nothing to revoke")

    def open_lookback(self, cr_id: str, dp) -> list[dict]:
        """Look-back over EARLIER releases made under each revoked version: every RELEASED unit whose evaluation at
        release used that version is flagged OPEN for human re-review (never withdrawn automatically)."""
        out = []
        with self.cp.as_role("dq_registry_svc") as c:
            cr = c.execute("SELECT items FROM dq_ctl.config_change_requests WHERE cr_id=%s", (cr_id,)).fetchone()
            live = {r[0] for r in dp.fetchall("SELECT unit_key FROM dq_rel.manifest WHERE version = "
                                              "(SELECT version FROM dq_rel.manifest_pointer WHERE id=1)")}
            for it in cr["items"]:
                if it["kind"] != "REVOCATION":
                    continue
                rule_art, h = it["content"]["rule"], it["content"]["hash"]
                rule_id = rule_art.split(":", 1)[1].split("@", 1)[0]
                rows = c.execute(
                    "SELECT DISTINCT u.unit_key, u.batch_id, u.node, u.authorities[1] AS authority, "
                    "(SELECT max(e.at) FROM dq_ctl.unit_events e WHERE e.unit_key=u.unit_key AND e.to_state='RELEASED') AS rel_at "
                    "FROM dq_ctl.units u JOIN dq_ctl.results r ON r.unit_key=u.unit_key "
                    "WHERE u.state='RELEASED' AND r.rule_id=%s AND r.rule_hash=%s "
                    "AND r.eval_version = (SELECT max(x.eval_version) FROM dq_ctl.results x WHERE x.unit_key=u.unit_key) "
                    "ORDER BY u.unit_key", (rule_id, h)).fetchall()
                for u in rows:
                    item = c.execute(
                        "INSERT INTO dq_ctl.lookback_items(cr_id,unit_key,batch_id,node,authority,rule_artifact,rule_hash,"
                        "released_at,in_live_manifest) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                        "ON CONFLICT DO NOTHING RETURNING item_id",
                        (cr_id, u["unit_key"], u["batch_id"], u["node"], u["authority"], rule_art, h, u["rel_at"],
                         u["unit_key"] in live)).fetchone()
                    if item:
                        c.execute("INSERT INTO dq_ctl.lookback_events(item_id,event) VALUES (%s,'OPEN')", (item["item_id"],))
                        out.append(dict(u) | {"item_id": item["item_id"], "rule": rule_art})
                by_batch: dict = {}
                for u in rows:
                    by_batch.setdefault((u["batch_id"], u["authority"]), []).append(u)
                for (b, auth), us in sorted(by_batch.items()):
                    c.execute("SELECT dq_ctl.bb_post(%s,%s,'FINDING','RULE',%s,'LOOKBACK_FLAGGED',%s::jsonb,NULL,NULL)",
                              (b, auth, rule_id, json.dumps({"rule_id": rule_id, "unit_count": len(us), "batch_id": b,
                                                             "cr_id": cr_id, "rule_hash": h[:16]})))
        return out

    def unapproved(self, state: str, compiled) -> list[str]:
        """Rules of the CURRENT ruleset whose version is not the approved one (e.g. changed by the calendar)."""
        appr = self.approved(state)
        return sorted(f"{state}:{r.id}@{r.entity}" for r in compiled
                      if appr.get(f"{state}:{r.id}@{r.entity}", {}).get("hash") != r.effective_hash)

    # -- Part E: earned autonomy -------------------------------------------------------------------------------
    def demote(self, agent: str, action_class: str, authority: str, to_tier: str, reason_code: str,
               ref: str | None = None, state: str | None = None) -> int:
        """Automatic, lower-only demotion as the registry service (dq_ctl.autonomy_demote). Raising fails."""
        with self.cp.as_role("dq_registry_svc") as c:
            return c.execute("SELECT dq_ctl.autonomy_demote(%s,%s,%s,%s,%s,%s,%s) AS n",
                             (agent, action_class, authority, to_tier, reason_code, ref, state)).fetchone()["n"]

    def tier(self, state: str | None, agent: str, action_class: str, authority: str) -> str:
        """Effective tier (a missing entry is L1, never L4)."""
        with self.cp.as_role("dq_registry_svc") as c:
            return c.execute("SELECT dq_ctl.autonomy_tier(%s,%s,%s,%s) AS t",
                             (state, agent, action_class, authority)).fetchone()["t"]

    # -- genesis -------------------------------------------------------------------------------------------
    def propose_genesis(self, state: str, author: str) -> dict:
        return self.propose(state, author, "Genesis: ratify the seed configuration of this state", seed_from_files(state),
                            origin="GENESIS")

    def write_test_genesis(self, admin_dsn: dict, state: str, deployment: str, idp_kind: str = "LOCAL"):
        """TEST-ONLY (T1): writes the seed as effective and approves its ruleset WITHOUT two people. Labelled
        TEST-GENESIS everywhere; the runtime refuses a CP holding it unless it runs in test mode.
        Part F R4-2: then binds the deployment (test | demo only; the database derives TEST and refuses any other),
        also on a control plane whose test genesis was written before the binding existed."""
        self._write_test_genesis(admin_dsn, state)
        return self.bind_deployment(deployment, idp_kind)

    def _write_test_genesis(self, admin_dsn: dict, state: str):
        items = seed_from_files(state)
        cfg = cfg_from_artifacts(state, {i["artifact_id"]: i for i in items})
        rs = ruleset(state, cfg)
        with psycopg.connect(**admin_dsn) as c:
            if c.execute("SELECT 1 FROM dq_ctl.config_artifacts WHERE state=%s LIMIT 1", (state,)).fetchone():
                return
            for it in items:
                c.execute("INSERT INTO dq_ctl.config_artifacts(artifact_id,version,state,kind,op,content,content_hash,cr_id) "
                          "VALUES (%s,1,%s,%s,'PUT',%s::jsonb,%s,'TEST-GENESIS')",
                          (it["artifact_id"], state, it["kind"], json.dumps(it["content"], default=str), _h(it["content"])))
            for k, v in sorted(rs["rules"].items()):
                c.execute("INSERT INTO dq_ctl.registry_events(artifact_kind,artifact_id,semantic_hash,state,decision_ref,"
                          "rule_meta) VALUES ('rule',%s,%s,'APPROVED','TEST-GENESIS',%s::jsonb)",
                          (k, v["hash"], json.dumps({kk: v[kk] for kk in ("status", "gating", "relation")})))
            # Part F P1-3 (K2a), ORCH-1 (C1): the SAME helper the governed path calls (dq_ctl.gov_apply), with the
            # SAME items in the SAME order, so the test and governed genesis produce identical introduction rows
            c.execute("SELECT dq_ctl._introduce_datasets(%s, %s::jsonb, 'TEST-GENESIS')", (state, json.dumps(items, default=str)))
            c.commit()
