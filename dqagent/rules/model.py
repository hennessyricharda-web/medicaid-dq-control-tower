"""Rule model (Part B): catalog + canonical elements + state profile -> compiled, applicable rules.

  catalog   rules/catalog/<pack>.yaml   rules written against CANONICAL elements (rules/elements.yaml)
  state     config/state/<ST>/profile.yaml   packs enabled + params
            config/state/<ST>/elements.yaml  entity -> relation, element -> column (this state's data plane)
            config/state/<ST>/feeds.yaml     which feeds / elements are actually delivered (and when)
            config/state/<ST>/overlays.yaml  tighten / narrow / raise_priority / waive (two-person approved)

compile_rules(state) returns one CompiledRule per (rule, entity) with:
  status          ACTIVE | NOT_APPLICABLE (a required element / feed is not delivered)
                  | STATE_WAIVED (overlay) | NOT_BOUND (an element is not mapped by the state) | NOT_LOADED
  effective_hash  H(semantic_hash, resolved params, element bindings, overlay, emitter + parser versions).
                  This is what results carry and what the registry approves: changing ANY input to the
                  rule's behaviour changes the hash, so it needs re-approval (RED-2).
"""
from __future__ import annotations

import copy
import datetime as dt
import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from dqagent.rules import basic
from dqagent.rules.ir import EMITTERS, IRError, canonical, semantic_hash

ROOT = Path(__file__).resolve().parents[2]
EMITTER_VERSION = "ir-1"
PARSER_BUILD = "x12-tokenizer-2 / mappers-2"     # bump when ingest checks change: PARSER rules' hashes move with it
SYS_COLS = {"_dq_batch_id", "_dq_file_id", "_dq_unit_key", "_dq_row_seq", "_dq_load_id", "_dq_source_record_key"}


@dataclass
class CompiledRule:
    id: str
    pack: str
    kind: str                     # row | measure | file (PARSER) | not_loaded
    engine: str
    entity: str
    relation: str | None
    status: str
    status_reason: str
    header: dict
    semantic_hash: str
    effective_hash: str
    assert_ir: dict | None = None
    applies_ir: dict | None = None
    numerator_ir: dict | None = None
    denominator_ir: dict | None = None
    thresholds: dict = field(default_factory=dict)
    distinct_col: str | None = None          # measure counts DISTINCT values of this column (e.g. member ids)
    longitudinal: dict | None = None         # {tolerance, compare: absolute|relative, period_col}

    # compatibility with the W1 evaluator / exporter dict access
    def __getitem__(self, k):
        return {"id": self.id, "kind": self.kind, "relation": self.relation, "check_class": self.header["check_class"],
                "priority": self.header["priority"]}[k]

    def get(self, k, default=None):
        try:
            return self[k]
        except KeyError:
            return self.header.get(k, default)

    def sql(self, dialect: str, part: str = "assert") -> str:
        """The (resolved) predicate over alias h for one dialect."""
        from dqagent.rules.ir import Ctx
        node = {"assert": self.assert_ir, "applies": self.applies_ir, "numerator": self.numerator_ir,
                "denominator": self.denominator_ir}[part]
        return EMITTERS[dialect].expr(node, Ctx({}))

    def fail_sql(self, dialect: str = "duckdb") -> str:
        return f"(NOT {self.sql(dialect)})"


def _load_yaml(p: Path):
    with open(p) as f:
        return yaml.safe_load(f) or {}


def load_state(state: str) -> dict:
    d = ROOT / "config" / "state" / state
    return {"profile": _load_yaml(d / "profile.yaml"), "elements": _load_yaml(d / "elements.yaml"),
            "feeds": _load_yaml(d / "feeds.yaml"), "overlays": (_load_yaml(d / "overlays.yaml").get("overlays") or {})}


def load_catalog(packs: list[str], elements: dict | None = None) -> list[dict]:
    out = []
    for pack in packs:
        if pack == basic.PACK:               # generated from element contracts (Part C WS1)
            out.extend(basic.generate(elements or _load_yaml(ROOT / "rules" / "elements.yaml")))
            continue
        doc = _load_yaml(ROOT / "rules" / "catalog" / f"{pack}.yaml")
        if doc.get("pack") != pack:
            raise IRError(f"catalog file for {pack} declares pack {doc.get('pack')!r}")
        for r in doc["rules"]:
            rule = {**copy.deepcopy(doc.get("defaults", {})), **r, "pack": pack}
            out.append(rule)
    return out


class Resolver:
    """Canonical IR (el / sys / pos / entity sources) -> emitter IR (col / relation) for one state."""

    def __init__(self, elements: dict, state_map: dict):
        self.elements = elements["elements"]
        self.entities = state_map["entities"]
        self.columns = state_map["columns"]

    def column(self, el: str) -> str:
        if el not in self.elements:
            raise IRError(f"unknown canonical element {el}")
        if el not in self.columns:
            raise KeyError(el)
        return self.columns[el]

    def relation(self, entity: str) -> str:
        if entity not in self.entities:
            raise KeyError(entity)
        return self.entities[entity]

    def resolve(self, n):
        if isinstance(n, list):
            return [self.resolve(x) for x in n]
        if not isinstance(n, dict):
            return n
        if "el" in n:
            out = {"col": self.column(n["el"])}
            if n.get("of") is not None:
                out["of"] = n["of"]
            return out
        if "sys" in n:
            if n["sys"] not in SYS_COLS:
                raise IRError(f"{n['sys']} is not a platform system column")
            out = {"col": n["sys"]}
            if n.get("of") is not None:
                out["of"] = n["of"]
            return out
        if "pos" in n:
            return {"col": f"c{int(n['pos'])}", "of": n["of"]}
        if "entity" in n and len(n) == 1:
            return self.relation(n["entity"])
        if "latest_per" in n:
            lp = n["latest_per"]
            return {"latest_per": {"source": self.resolve(lp["source"]),
                                   "key": [self.column(k) if "." in k else k for k in lp["key"]],
                                   "order": [self._order(o) for o in lp["order"]],
                                   "where": self.resolve(lp.get("where"))}}
        if "union_all" in n:
            return {"union_all": [{"source": self.resolve(p["source"]), **({"as": p["as"]} if p.get("as") else {}),
                                   "select": [self.column(c) for c in p["select"]],
                                   **({"where": self.resolve(p["where"])} if p.get("where") else {})}
                                  for p in n["union_all"]]}
        return {k: (v if k in ("as", "lit", "date", "param", "of", "col") else self.resolve(v)) for k, v in n.items()}

    def _order(self, o: str) -> str:
        name, *rest = o.split()
        col = self.column(name) if "." in name else name
        return " ".join([col, *rest])

    def elements_used(self, n, acc=None) -> set[str]:
        acc = set() if acc is None else acc
        if isinstance(n, list):
            for x in n:
                self.elements_used(x, acc)
        elif isinstance(n, dict):
            if "el" in n:
                acc.add(n["el"])
            for k, v in n.items():
                if k == "latest_per":
                    acc.update(x for x in v["key"] if "." in x)
                    acc.update(x.split()[0] for x in v["order"] if "." in x.split()[0])
                if k == "union_all":
                    for p in v:
                        acc.update(p["select"])
                if k not in ("lit", "date"):
                    self.elements_used(v, acc)
        return acc


def _feed_delivered(feeds: dict, feed: str) -> tuple[bool, str]:
    f = (feeds.get("feeds") or {}).get(feed)
    if not f or not f.get("delivered"):
        return False, f"feed {feed} not delivered" + (f": {f.get('reason')}" if f and f.get("reason") else "")
    return True, ""


def _delivered(feeds: dict, elements: dict, el: str, on: dt.date | None) -> tuple[bool, str]:
    meta = elements["elements"].get(el)
    if meta is None:
        return False, f"unknown element {el}"
    if meta.get("system"):
        return True, ""          # envelope/system keys exist on every X12 entity; the entity's own feed decides
    if el in (feeds.get("not_delivered_elements") or []):
        return False, f"element {el} declared not delivered"
    feed = elements["entities"][meta["entity"]]["feed"]
    f = (feeds.get("feeds") or {}).get(feed)
    if not f or not f.get("delivered"):
        return False, f"feed {feed} not delivered" + (f": {f.get('reason')}" if f and f.get("reason") else "")
    els = f.get("elements", "all")
    if els != "all" and el not in els:
        return False, f"element {el} not in feed {feed}"
    win = (f.get("windows") or {}).get(el)
    if win and on is not None:
        lo, hi = win.get("from"), win.get("to")
        if (lo and on < dt.date.fromisoformat(lo)) or (hi and on > dt.date.fromisoformat(hi)):
            return False, f"element {el} not delivered on {on}"
    return True, ""


def overlay_status(ov: dict | None, today: dt.date | None = None) -> str | None:
    if not ov:
        return None
    exp = ov.get("expires")
    if exp and dt.date.fromisoformat(str(exp)) < (today or dt.date.today()):
        return "EXPIRED"
    return "ACTIVE"


def _apply_overlay(rule: dict, ov: dict, on: dt.date | None = None) -> tuple[dict, str | None]:
    if not ov:
        return rule, None
    r = copy.deepcopy(rule)
    verb = ov["verb"]
    for k in ("reason", "approved_ref"):
        if not ov.get(k):
            raise IRError(f"overlay on {rule['id']} lacks {k}")
    if overlay_status(ov, on) == "EXPIRED":      # Part B gap: EVERY verb expires, not only waive
        return r, None
    if verb == "waive":
        return r, f"waived: {ov['reason']} ({ov['approved_ref']})"
    if verb == "tighten":
        th = r.get("thresholds", {})
        for k, v in (ov.get("thresholds") or {}).items():   # never loosen: only narrower bounds are accepted
            if k == "max" and k in th and v > th[k] or k == "min" and k in th and v < th[k]:
                raise IRError(f"overlay on {rule['id']} would LOOSEN {k} ({th[k]} -> {v}); not allowed")
            th[k] = v
        r["thresholds"] = th
    elif verb == "narrow":
        extra = ov["applies_when"]
        r["applies_when"] = {"and": [r["applies_when"], extra]} if r.get("applies_when") else extra
    elif verb == "promote":
        r["gating"] = "gate"                 # advisory -> gating is a TIGHTENING (two-person approved overlay)
    elif verb == "raise_priority":
        order = ["Low", "Medium", "High", "Critical"]
        if order.index(ov["priority"]) < order.index(r.get("priority", "Medium")):
            raise IRError(f"overlay on {rule['id']} would LOWER priority")
        r["priority"] = ov["priority"]
    else:
        raise IRError(f"unknown overlay verb {verb}")
    return r, None


def compile_rules(state: str, *, on: dt.date | None = None, cfg: dict | None = None) -> list[CompiledRule]:
    """cfg: an in-memory state config (tests / onboarding previews); default reads config/state/<state>."""
    st = cfg if cfg is not None else load_state(state)
    elements = _load_yaml(ROOT / "rules" / "elements.yaml")
    res = Resolver(elements, st["elements"])
    params = st["profile"].get("params", {})
    out = []
    catalog = load_catalog(st["profile"]["packs"], elements)
    unknown = sorted(set(st["overlays"]) - {r["id"] for r in catalog})
    if unknown:                                   # Part B gap: an overlay must name a rule of the enabled catalog
        raise IRError(f"overlays name rule ids not in the enabled catalog: {unknown}")
    for raw in catalog:
        ov = st["overlays"].get(raw["id"])
        rule, waived = _apply_overlay(raw, ov, on)
        entities = rule.get("entities") or [rule["entity"]]
        for ent in entities:
            kind = {"PARSER": "file"}.get(rule.get("engine"), rule.get("kind", "row"))
            header = {k: rule.get(k) for k in ("title", "origin", "check_class", "priority", "dimension", "stage",
                                                "validation_type", "programs", "claim_types", "req_ids", "analog_of",
                                                "citation", "depends_on", "coverage", "gating", "template_version")}
            header["gating"] = header["gating"] or "gate"
            header["overlay"] = ({"verb": ov["verb"], "status": overlay_status(ov, on), "expires": ov.get("expires"),
                                  "approved_ref": ov.get("approved_ref")} if ov else None)
            header["check_class"] = header["check_class"] or "integrity"
            header["priority"] = header["priority"] or "Medium"
            status, why = "ACTIVE", ""
            feed_ok, feed_why = _feed_delivered(st["feeds"], elements["entities"][ent]["feed"])
            try:
                relation = res.relation(ent)
            except KeyError:
                relation = None
            if not feed_ok:                  # the compiled ENTITY's feed first (review finding 2; Part C F10)
                status, why = "NOT_APPLICABLE", feed_why + ("" if relation else f"; state maps no relation for entity {ent}")
            elif relation is None:
                status, why = "NOT_BOUND", f"state maps no relation for entity {ent}"
            needed = set(rule.get("requires", []))
            if rule.get("distinct"):
                needed.add(rule["distinct"])
            for el in (rule.get("longitudinal") or {}).get("period_key", []):
                needed.add(el)
            for part in ("assert", "applies_when", "numerator", "denominator"):
                if rule.get(part) is not None:
                    needed |= res.elements_used(rule[part])
            resolved, distinct_col, longitudinal = {}, None, None
            if status == "ACTIVE":
                for el in sorted(needed):
                    ok, reason = _delivered(st["feeds"], elements, el, on)
                    if not ok:
                        status, why = "NOT_APPLICABLE", reason
                        break
            if status == "ACTIVE":
                try:
                    for part in ("assert", "applies_when", "numerator", "denominator"):
                        if rule.get(part) is not None:
                            resolved[part] = res.resolve(rule[part])
                    distinct_col = res.column(rule["distinct"]) if rule.get("distinct") else None
                    lg = rule.get("longitudinal")
                    longitudinal = ({**{k: v for k, v in lg.items() if k != "period_key"},
                                     "period_col": res.column(lg["period_key"][0]) if lg.get("period_key") else None}
                                    if lg else None)
                except KeyError as e:
                    status, why = "NOT_BOUND", f"state maps no column for element {e.args[0]}"
            if waived and status == "ACTIVE":
                status, why = "STATE_WAIVED", waived
            if kind == "not_loaded" and status == "ACTIVE":
                status, why = "NOT_LOADED", "code-set registry not loaded (Part E)"
            sem = semantic_hash({**rule, "entity": ent})
            bindings = {el: st["elements"]["columns"].get(el) for el in sorted(needed)}
            used_params = {k: v for k, v in params.items() if f'"param":"{k}"' in canonical(rule)}
            eff = hashlib.sha256(canonical({"sem": sem, "params": used_params, "bindings": bindings, "relation": relation,
                                            "overlay": ov, "overlay_status": overlay_status(ov, on),
                                            "status": status,   # a calendar status move changes the hash (F3)
                                            "emitter": EMITTER_VERSION,
                                            "parser": PARSER_BUILD if kind == "file" else None,
                                            "thresholds": rule.get("thresholds")}).encode()).hexdigest()[:16]
            out.append(CompiledRule(
                id=rule["id"], pack=rule["pack"], kind=kind, engine=rule.get("engine", "IR"), entity=ent,
                relation=relation, status=status, status_reason=why, header=header, semantic_hash=sem,
                effective_hash=eff, assert_ir=resolved.get("assert"), applies_ir=resolved.get("applies_when"),
                numerator_ir=resolved.get("numerator"), denominator_ir=resolved.get("denominator"),
                thresholds=rule.get("thresholds") or {}, distinct_col=distinct_col, longitudinal=longitudinal))
    _bind_params(out, params)
    seen: dict = {}
    for r in out:                                 # Part B gap: one disposition per compiled rule per unit
        if r.relation is not None:             # ALL statuses (Part D C4): an inactive twin would mask a FAIL
            k = (r.id, r.relation)
            if k in seen:
                raise IRError(f"rule {r.id} compiles twice onto {r.relation} ({seen[k]} and {r.entity})")
            seen[k] = r.entity
    return out


def _bind_params(rules: list[CompiledRule], params: dict):
    """Params are substituted at compile time so the emitted SQL (and the effective hash) reflect them."""
    def sub(n):
        if isinstance(n, list):
            return [sub(x) for x in n]
        if isinstance(n, dict):
            if "param" in n:
                if n["param"] not in params:
                    raise IRError(f"unbound parameter {n['param']}")
                return {"lit": params[n["param"]]}
            if "matches" in n and isinstance(n["matches"][1], dict) and "param" in n["matches"][1]:
                return {"matches": [sub(n["matches"][0]), params[n["matches"][1]["param"]]]}
            return {k: sub(v) for k, v in n.items()}
        return n
    for r in rules:
        r.assert_ir, r.applies_ir = sub(r.assert_ir), sub(r.applies_ir)
        r.numerator_ir, r.denominator_ir = sub(r.numerator_ir), sub(r.denominator_ir)


def coverage(rules: list[CompiledRule], inventory_csvs: list[Path]) -> dict:
    """Requirements (the inventory) vs compiled rules. A requirement with no ACTIVE rule is a GAP or is explained."""
    import csv
    by_req: dict[str, list[CompiledRule]] = {}
    for r in rules:
        for q in r.header.get("req_ids") or []:
            by_req.setdefault(q, []).append(r)
    rows = []
    for p in inventory_csvs:
        for q in csv.DictReader(open(p)):
            rs = by_req.get(q["req_id"], [])
            active = [r for r in rs if r.status == "ACTIVE"]
            full = [r for r in active if (r.header.get("coverage") or ("analog" if r.header.get("analog_of") else "full")) == "full"]
            # A pre-load ANALOG of a CMS measure does not cover the measure itself (Part B review finding 3):
            # the requirement stays open at its own stage and shows ANALOG_ACTIVE / PARTIAL.
            st = ("ACTIVE" if full else
                  ("PARTIAL" if any(r.header.get("coverage") == "partial" for r in active) else "ANALOG_ACTIVE") if active else
                  next((r.status for r in rs), "GAP"))
            rows.append({"req_id": q["req_id"], "domain": q["domain"], "stage": q["earliest_stage"],
                         "dimension": q["dimension"], "priority": q["priority"], "source": q["source_id"],
                         "requirement_status": q.get("status"), "coverage": st,
                         "rules": sorted({f"{r.id}@{r.entity}" for r in rs})})
    return {"requirements": rows, "summary": _summ(rows)}


def _summ(rows):
    s: dict = {}
    for r in rows:
        k = (r["domain"], r["stage"])
        s.setdefault(k, {}).setdefault(r["coverage"], 0)
        s[k][r["coverage"]] += 1
    return {f"{d}|{st}": v for (d, st), v in sorted(s.items())}


def describe(rules: list[CompiledRule]) -> list[dict]:
    return [{"id": r.id, "pack": r.pack, "entity": r.entity, "kind": r.kind, "status": r.status,
             "reason": r.status_reason, "effective_hash": r.effective_hash, "relation": r.relation,
             **{k: r.header.get(k) for k in ("title", "priority", "stage", "dimension", "req_ids", "analog_of",
                                             "gating", "overlay", "origin")}} for r in rules]


_ = json
