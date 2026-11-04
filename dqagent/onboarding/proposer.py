"""Metadata-only element-binding proposer (Part D, D3; reference-codebase pattern 4, re-implemented clean-room).

Onboarding a new state means writing its config/state/<ST>/elements.yaml: which physical relation holds each
canonical entity and which column holds each canonical element. This module DRAFTS that map; it never applies
it. The draft becomes a change request that a second person must approve (two-person, step-up).

What is read (profile_relation): column names and SQL types, and AGGREGATES only.
  * null fraction (rounded to 0.01);
  * distinct count as a BUCKET ("0", "1", "2-10", "11-100", "101-1000", ">1000");
  * value SHAPES: each value reduced to a character-class run string (digits -> N, letters -> A, other
    characters kept: "ZZ12345678" -> "2A8N", "2026-01-10" -> DATE). A shape seen fewer than 11 times is
    suppressed (small-cell rule), so a rare shape cannot single out a person. Shapes DO encode the length of a
    format, so they are shown only for format-like columns (one shape covers at least half the rows); a free-text
    column shows none. Null fraction is suppressed for relations under 11 rows.
No value, and no sample row, ever leaves the data plane. There is no LLM in this path.

Scoring (deterministic, explainable):
  name   0.50  token overlap between the column name and the element (id, locator words, synonym table)
  type   0.20  SQL type compatible with the element type
  shape  0.30  dominant shape compatible with the element contract (pattern / values / npi / date)
Identity, provider and money elements are ALWAYS flagged `needs_attention` (a wrong binding there is the
dangerous kind), as is any element whose runner-up column scores within 0.10 (ambiguous).
"""
from __future__ import annotations

import re

SMALL_CELL = 11
SYN = {"mbr": "member", "msis": "member", "recip": "member", "rcpnt": "member", "bene": "member", "subscriber": "member",
       "clm": "claim", "enc": "claim", "encounter": "claim", "icn": "claim", "tcn": "claim", "chg": "charge",
       "amt": "amount", "tot": "total", "dt": "date", "svc": "service", "dos": "service", "prov": "provider",
       "prv": "provider", "freq": "frequency", "cd": "code", "num": "id", "nbr": "id", "no": "id", "key": "id",
       "ln": "line", "proc": "procedure", "hcpcs": "procedure", "cpt": "procedure", "diag": "diagnosis",
       "dx": "diagnosis", "dx1": "diagnosis", "mco": "plan", "php": "plan", "pos": "place", "sbmtr": "submitter", "sender": "submitter", "tp": "submitter",
       "elig": "eligibility", "eff": "effective", "beg": "start", "begin": "start", "strt": "start", "end": "end",
       "term": "end", "thru": "end", "dob": "birth", "birth": "birth", "gender": "sex", "pgm": "program",
       "prog": "program", "seg": "segment", "typ": "type", "rec": "recorded", "ts": "recorded", "paid": "paid",
       "pd": "paid", "adj": "adjudication", "adjud": "adjudication", "orig": "original", "ref": "original",
       "npi": "npi", "atyp": "alternate", "alt": "alternate", "plan": "plan", "hd": "plan", "ins": "maintenance",
       "maint": "maintenance", "cov": "coverage", "units": "units", "unit": "units", "qty": "units", "person": "person",
       "psk": "person", "chain": "chain", "root": "chain", "isa": "isa13", "gs": "gs06", "st": "st02"}
LEAF_WORDS = {"CLM.ID": {"claim", "id"}, "ENR.INS03": {"maintenance", "ins03"}, "ID": {"id"}, "MEMBER_ID": {"member", "id"}, "SUBMITTER_ID": {"submitter", "id"},
              "TOTAL_CHARGE": {"total", "charge"}, "POS": {"place", "service"}, "FREQ_CODE": {"frequency", "code"},
              "ORIG_ID": {"original", "id"}, "CHAIN_ROOT": {"chain"}, "DX1": {"diagnosis"},
              "MCO_PAID": {"plan", "paid"}, "BILLING_NPI": {"billing", "npi"}, "BILLING_ALT_ID": {"alternate", "id"},
              "CLM_ID": {"claim", "id"}, "NO": {"line", "id"}, "PROC_CODE": {"procedure", "code"},
              "CHARGE": {"line", "charge"}, "UNITS": {"units"}, "SVC_DATE": {"service", "date"},
              "PAID_AMT": {"paid", "amount"}, "ADJUD_DATE": {"adjudication", "date"},
              "PERSON_KEY": {"person", "id"}, "PROGRAM": {"program"}, "SEGMENT_TYPE": {"segment", "type"},
              "EFF_START": {"effective", "start"}, "EFF_END": {"effective", "end"}, "DOB": {"birth"},
              "SEX": {"sex"}, "RECORDED_AT": {"recorded"}, "INS03": {"maintenance"}, "PLAN_ID": {"plan", "id"},
              "COV_START": {"coverage", "start"}, "COV_END": {"coverage", "end"}, "ISA13": {"isa13"},
              "GS06": {"gs06"}, "ST02": {"st02"}}
ATTENTION = {"MEMBER_ID", "PERSON_KEY", "BILLING_NPI", "SUBMITTER_ID", "TOTAL_CHARGE", "MCO_PAID", "PAID_AMT",
             "CHARGE", "DOB"}
TYPE_OK = {"str": ("VARCHAR", "CHAR", "TEXT", "STRING"), "dec": ("DECIMAL", "NUMERIC", "DOUBLE", "FLOAT", "INTEGER", "BIGINT"),
           "int": ("INTEGER", "BIGINT", "SMALLINT", "TINYINT"), "date": ("DATE",), "ts": ("TIMESTAMP",)}


def _bucket(n: int) -> str:
    return "0" if n == 0 else "1" if n == 1 else "2-10" if n <= 10 else "11-100" if n <= 100 else \
        "101-1000" if n <= 1000 else ">1000"


def _shape_sql(col: str) -> str:
    """Character-class run shape computed IN the data plane (DuckDB): digits -> N, letters -> A, runs counted."""
    return (f"CASE WHEN {col} IS NULL THEN NULL WHEN TRY_CAST({col} AS DATE) IS NOT NULL "
            f"AND regexp_full_match(CAST({col} AS VARCHAR), '[0-9]{{4}}-[0-9]{{2}}-[0-9]{{2}}.*') THEN 'DATE' "
            f"ELSE upper(regexp_replace(regexp_replace(CAST({col} AS VARCHAR), '[A-Za-z]', 'a', 'g'), '[0-9]', 'N', 'g')) END")


def _compress(s: str) -> str:
    return "".join(f"{len(m.group(0))}{m.group(0)[0]}" if m.group(0)[0] in "NA" else m.group(0)
                   for m in re.finditer(r"N+|A+|[^NA]+", s)) if s and s != "DATE" else s


def profile_relation(dp, relation: str) -> dict:
    """Aggregates only. Shapes under the small-cell threshold are suppressed."""
    cols = [(r[0], str(r[1]).upper()) for r in dp.fetchall(f"DESCRIBE {relation}") if not r[0].startswith("_dq_")]
    total = dp.fetchall(f"SELECT count(*) FROM {relation}")[0][0]
    out = {"relation": relation, "rows_bucket": _bucket(total), "columns": []}
    for name, typ in cols:
        nn, nd = dp.fetchall(f"SELECT count({name}), count(DISTINCT {name}) FROM {relation}")[0]
        shapes = dp.fetchall(f"SELECT s, count(*) n FROM (SELECT {_shape_sql(name)} s FROM {relation}) "
                             f"WHERE s IS NOT NULL GROUP BY 1 ORDER BY 2 DESC, 1 LIMIT 5")
        shown = [{"shape": _compress(s), "share": round(n / nn, 2)} for s, n in shapes if n >= SMALL_CELL]
        # shapes only for FORMAT-like columns (one dominant shape): a free-text column's shapes would leak lengths
        if shown and shown[0]["share"] < 0.5:
            shown = []
        out["columns"].append({"name": name, "type": typ.split("(")[0],
                               "null_fraction": round(1 - (nn / total), 2) if total >= SMALL_CELL else None,
                               "distinct_bucket": _bucket(nd), "shapes": shown,
                               "x_suppressed_shapes": sum(1 for _, n in shapes if n < SMALL_CELL)})
    return out


def _tokens(name: str) -> set[str]:
    raw = re.findall(r"[a-z0-9]+", re.sub(r"([a-z])([A-Z])", r"\1_\2", name).lower())
    toks = set()
    for t in raw:
        if t in ("x", "stg", "tbl", "col", "fld"):
            continue
        toks.add(SYN.get(t, t))
    return toks


def _shape_score(meta: dict, col: dict) -> float:
    c = meta.get("contract") or {}
    top = col["shapes"][0]["shape"] if col["shapes"] else None
    if top is None:
        return 0.5                                  # unknown (all suppressed / empty): neutral
    if meta.get("type") in ("date", "ts"):
        return 1.0 if top == "DATE" else 0.0
    if c.get("npi"):
        return 1.0 if top == "10N" else 0.0
    if "values" in c:
        lens = {len(str(v)) for v in c["values"]}
        m = re.fullmatch(r"(\d+)[NA]", top)
        return 1.0 if m and int(m.group(1)) in lens else 0.3
    if "pattern" in c:
        m = re.fullmatch(r"\[0-9\]\{(\d+)\}", c["pattern"])
        if m:
            return 1.0 if top == f"{m.group(1)}N" else 0.2
    return 0.6


def propose_bindings(elements: dict, profiles: list[dict]) -> dict:
    """Return {entities: {ENTITY: relation}, columns: {EL: column}, proposals: [...], unmatched: [...]}."""
    els = elements["elements"]
    proposals, unmatched, entities, columns = [], [], {}, {}
    ents = elements["entities"]
    base_ents = sorted(e for e, m in ents.items() if not m.get("released_view"))
    ents_keys = {e: set(m.get("keys") or []) for e, m in ents.items()}
    cands = []                                      # (total, coverage, entity, relation, detail)
    for ent in base_ents:
        mine = sorted(e for e, m in els.items() if m["entity"] == ent)
        for prof in profiles:
            detail = {}
            for el in mine:
                meta = els[el]
                leaf = el.split(".", 1)[1]
                want = LEAF_WORDS.get(el) or LEAF_WORDS.get(leaf) or set(_tokens(leaf))
                scored = []
                for col in prof["columns"]:
                    toks = _tokens(col["name"])
                    # Jaccard: rewards the column that names the element and nothing else
                    name = len(want & toks) / len(want | toks) if want else 0.0
                    if name == 0:
                        continue
                    typ = 1.0 if col["type"].startswith(TYPE_OK.get(meta.get("type", "str"), ("VARCHAR",))) else 0.0
                    shp = _shape_score(meta, col)
                    scored.append((round(0.5 * name + 0.2 * typ + 0.3 * shp, 3), col["name"],
                                   {"name": round(name, 2), "type": typ, "shape": shp}))
                scored.sort(key=lambda x: (-x[0], x[1]))
                if scored and scored[0][0] >= 0.6:
                    detail[el] = scored
            # coverage over the entity's CORE elements (required or key, non-system): an entity is matched when
            # its essential elements are explained, not when every optional element happens to exist
            core = [e for e in mine if not els[e].get("system") and
                    (((els[e].get("contract") or {}).get("required")) or e in (ents[ent].get("keys") or []))] or mine
            cov = len([e for e in core if e in detail]) / len(core)
            cands.append((round(sum(v[0][0] for v in detail.values()), 3), cov, ent, prof["relation"], detail))
    used_rel = set()
    # an entity takes a relation only when it explains most of its elements, and each relation feeds one entity
    for total, cov, ent, rel, detail in sorted(cands, key=lambda c: (-c[0], -c[1], c[2], c[3])):
        if ent in entities or rel in used_rel or cov < 0.5:
            continue
        entities[ent] = rel
        used_rel.add(rel)
        used = set()
        for el in sorted(e for e, m in els.items() if m["entity"] == ent):
            cs = [c for c in detail.get(el, []) if c[1] not in used]
            if not cs:
                unmatched.append(el)
                continue
            top = cs[0]
            used.add(top[1])
            flags = []
            if el.split(".", 1)[1] in ATTENTION:
                flags.append("identity / provider / money element: verify against the state's data dictionary")
            if len(cs) > 1 and cs[0][0] - cs[1][0] < 0.10:
                flags.append(f"ambiguous: runner-up {cs[1][1]} scores {cs[1][0]}")
            prof = next((pp for pp in profiles if pp["relation"] == rel), None)
            col = next((cc for cc in (prof or {}).get("columns", []) if cc["name"] == top[1]), {})
            if el.split(".", 1)[1] in ("MEMBER_ID", "PERSON_KEY", "ID") and col.get("distinct_bucket") in ("0", "1", "2-10") \
                    and (prof or {}).get("rows_bucket") not in ("0", "1", "2-10"):
                flags.append("cardinality contradicts an identifier (very few distinct values)")
            columns[el] = top[1]
            proposals.append({"element": el, "entity": ent, "relation": rel, "column": top[1], "score": top[0],
                              "reasons": top[2], "needs_attention": flags})
    by_col: dict = {}
    for pr in proposals:                            # a column bound to more than one element needs a human look
        by_col.setdefault((pr["relation"], pr["column"]), []).append(pr)
    for prs in by_col.values():
        if len(prs) > 1:
            for pr in prs:
                pr["needs_attention"].append(f"column also bound to {[x['element'] for x in prs if x is not pr]}")
    for ent in base_ents:
        if ent not in entities:
            unmatched += sorted(e for e, m in els.items() if m["entity"] == ent)
    # released views are PLATFORM objects (dq_rel.*), derived from their base entity: mirror by element leaf
    for ent, m in sorted(ents.items()):
        if not m.get("released_view"):
            continue
        base, suffix = m.get("base_entity"), m.get("view_suffix")
        if base in entities and suffix:
            entities[ent] = "dq_rel." + entities[base].split(".", 1)[1] + suffix
            for el in sorted(e for e, mm in els.items() if mm["entity"] == ent):
                src = next((b for b, bm in els.items() if bm["entity"] == base
                            and b.split(".", 1)[1] == el.split(".", 1)[1] and b in columns), None)
                if src:
                    columns[el] = columns[src]
                    proposals.append({"element": el, "entity": ent, "relation": entities[ent], "column": columns[src],
                                      "score": None, "reasons": {"mirrors": src}, "needs_attention": []})
                else:
                    unmatched.append(el)
        else:
            unmatched += sorted(e for e, mm in els.items() if mm["entity"] == ent)
    return {"entities": entities, "columns": columns, "proposals": proposals, "unmatched": sorted(unmatched)}
