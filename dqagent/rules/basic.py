"""Basic DQ templates (Part C, WS1): element contracts -> generated catalog rules, for EVERY declared domain.

Basic data quality is the floor of the estate; CMS measures sit on top of it. A domain gets basic DQ by
DECLARING its elements with contracts in rules/elements.yaml. No rule is hand-written for the floor.

Contract keys (element level):
    required: true | {when: IR}        completeness: present and not blank
    pattern: "<restricted ERE>"         validity: full-match when present
    values: [..]                         validity: one of a closed list when present
    min / max: number                    validity: numeric bounds when present
    npi: true                            validity: NPI check digit when present
    after_or_eq: <EL>                    consistency: this value >= another field of the same entity
    lag_max_days: {from: <EL>, days: N}  timeliness: 0 <= this - from <= N days
    ref: <EL of another entity>          referential: a row with this value exists in that entity
    codeset: <system>                    declared only; compiled by the code-set registry (Part E)
    when: IR                             applicability predicate for every rule of this element
Entity level:
    keys: [<EL>, ..] (+ key_when: IR)    uniqueness within the batch

Generated rules are ADVISORY by default (Part C red-team F3): a non-PASS outcome is recorded as TA_WARN, which
never changes the unit's mode. A state promotes a rule to gating with the two-person overlay verb `promote`.
Every generated rule still passes through compile_rules unchanged (bindings, feed applicability, overlays,
effective hash); the template version is part of the semantic hash.
"""
from __future__ import annotations

TEMPLATE_VERSION = "basic-1"
PACK = "basic/templates"


def _slug(el: str) -> str:
    return el.replace(".", "-")


def _present(el: str) -> dict:
    return {"not_null": {"el": el}}


def _when(contract: dict, extra: dict | None = None) -> dict | None:
    w = contract.get("when")
    if w and extra:
        return {"and": [w, extra]}
    return w or extra


def _rule(rid, title, entity, dimension, requires, assert_, applies=None, **extra) -> dict:
    r = {"id": rid, "title": title, "kind": "row", "engine": "IR", "entity": entity, "origin": "BASIC_TEMPLATE",
         "dimension": dimension, "check_class": dimension, "priority": "Medium", "stage": "ROW",
         "validation_type": "DETERMINISTIC", "gating": "advisory", "template_version": TEMPLATE_VERSION,
         "requires": sorted(set(requires)), "assert": assert_, "pack": PACK, **extra}
    if applies:
        r["applies_when"] = applies
    return r


def generate(elements: dict) -> list[dict]:
    """Deterministic: entities and elements in sorted order, dimensions in a fixed order."""
    out: list[dict] = []
    els = elements["elements"]
    for ent in sorted(elements["entities"]):
        emeta = elements["entities"][ent]
        mine = sorted(e for e, m in els.items() if m.get("entity") == ent and not m.get("system"))
        for el in mine:
            m = els[el]
            c = m.get("contract") or {}
            typ = m.get("type", "str")
            if not c:
                continue
            # completeness
            req = c.get("required")
            if req:
                cond = req.get("when") if isinstance(req, dict) else None
                a = (_present(el) if typ != "str" else
                     {"and": [_present(el), {"ne": [{"trim": {"el": el}}, {"lit": ""}]}]})
                out.append(_rule(f"B-CMP-{_slug(el)}", f"{el} is present", ent, "completeness", [el], a,
                                 _when(c, cond)))
            # validity (only judged when the value is present: absence is completeness' job)
            checks = []
            if "pattern" in c:
                checks.append({"matches": [{"el": el}, c["pattern"]]})
            if "values" in c:
                checks.append({"in": [{"el": el}, list(c["values"])]})
            if "min" in c:
                checks.append({"ge": [{"el": el}, {"lit": c["min"]}]})
            if "max" in c:
                checks.append({"le": [{"el": el}, {"lit": c["max"]}]})
            if c.get("npi"):
                checks.append({"npi_luhn": {"el": el}})
            if checks:
                a = checks[0] if len(checks) == 1 else {"and": checks}
                out.append(_rule(f"B-VAL-{_slug(el)}", f"{el} is valid when present", ent, "validity", [el], a,
                                 _when(c, _present(el))))
            # consistency
            if "after_or_eq" in c:
                o = c["after_or_eq"]
                out.append(_rule(f"B-CON-{_slug(el)}", f"{el} is not before {o}", ent, "consistency", [el, o],
                                 {"ge": [{"el": el}, {"el": o}]},
                                 _when(c, {"and": [_present(el), _present(o)]})))
            # timeliness
            if "lag_max_days" in c:
                lg = c["lag_max_days"]
                diff = {"sub": [{"el": el}, {"el": lg["from"]}]}
                out.append(_rule(f"B-TML-{_slug(el)}", f"{el} is within {lg['days']} days after {lg['from']}", ent,
                                 "timeliness", [el, lg["from"]],
                                 {"and": [{"ge": [diff, {"lit": 0}]}, {"le": [diff, {"lit": int(lg["days"])}]}]},
                                 _when(c, {"and": [_present(el), _present(lg["from"])]}),
                                 citation=lg.get("citation")))
            # referential
            if "ref" in c:
                tgt = c["ref"]
                tent = els[tgt]["entity"]
                out.append(_rule(f"B-REF-{_slug(el)}", f"{el} refers to an existing {tent} ({tgt})", ent,
                                 "referential", [el, tgt],
                                 {"exists": {"source": {"entity": tent}, "as": "r",
                                             "where": {"eq": [{"el": tgt, "of": "r"}, {"el": el}]}}},
                                 _when(c, _present(el))))
        # uniqueness within the batch
        keys = emeta.get("keys")
        if keys:
            same = [{"eq": [{"el": k, "of": "u"}, {"el": k}]} for k in keys]
            same += [{"eq": [{"sys": "_dq_batch_id", "of": "u"}, {"sys": "_dq_batch_id"}]},
                     {"ne": [{"sys": "_dq_row_seq", "of": "u"}, {"sys": "_dq_row_seq"}]}]
            out.append(_rule(f"B-UNQ-{ent}", f"{ent} is unique on ({', '.join(keys)}) within the batch", ent,
                             "uniqueness", list(keys),
                             {"not": {"exists": {"source": {"entity": ent}, "as": "u", "where": {"and": same}}}},
                             {"and": [_present(k) for k in keys] + ([emeta["key_when"]] if emeta.get("key_when") else [])}))
    return out


def declared_codesets(elements: dict) -> list[dict]:
    """Code-set contracts are declared now and compiled by the registry in Part E (never a silent PASS)."""
    return [{"element": el, "entity": m["entity"], "system": m["contract"]["codeset"]}
            for el, m in sorted(elements["elements"].items()) if (m.get("contract") or {}).get("codeset")]


DIMENSIONS = ["completeness", "validity", "uniqueness", "referential", "consistency", "timeliness"]


def basic_coverage(rules, elements: dict) -> dict:
    """Domain x dimension matrix over every NON-measure rule (generated or hand-written).
    A cell is EXERCISED (>=1 ACTIVE rule), DECLARED_NOT_EXERCISED (rules exist, the feed is not delivered),
    NOT_BOUND / WAIVED, or EMPTY. NOT_APPLICABLE rules never count as coverage (red-team F9)."""
    ent_domain = {e: m.get("domain", e) for e, m in elements["entities"].items()}
    dim_alias = {"integrity": "referential", "conformance": "completeness"}
    cells: dict = {}
    for r in rules:
        if r.kind == "measure":
            continue
        dom = ent_domain.get(r.entity, r.entity)
        dim = r.header.get("dimension") or "validity"
        dim = dim_alias.get(dim, dim)
        cell = cells.setdefault(dom, {}).setdefault(dim, {"active": 0, "not_applicable": 0, "other": 0,
                                                          "rules": []})
        key = {"ACTIVE": "active", "NOT_APPLICABLE": "not_applicable"}.get(r.status, "other")
        cell[key] += 1
        cell["rules"].append(f"{r.id}@{r.entity}:{r.status}")
    matrix = {}
    for dom in sorted(set(ent_domain.values())):
        row = {}
        for dim in DIMENSIONS:
            c = cells.get(dom, {}).get(dim)
            if not c:
                row[dim] = {"status": "EMPTY"}
            elif c["active"]:
                row[dim] = {"status": "EXERCISED", **c}
            elif c["not_applicable"]:
                row[dim] = {"status": "DECLARED_NOT_EXERCISED", **c}
            else:
                row[dim] = {"status": "NOT_ACTIVE", **c}
        matrix[dom] = row
    exercised = sum(1 for r in matrix.values() for c in r.values() if c["status"] == "EXERCISED")
    declared = sum(1 for r in matrix.values() for c in r.values() if c["status"] == "DECLARED_NOT_EXERCISED")
    return {"matrix": matrix, "summary": {"domains": len(matrix), "exercised_cells": exercised,
                                          "declared_not_exercised_cells": declared,
                                          "codesets_declared_for_part_e": len(declared_codesets(elements))}}
