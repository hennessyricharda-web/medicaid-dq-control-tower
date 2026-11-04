"""Basic DQ for EVERY declared domain is exercised, not just declared (Part C red-team F9 + F8).

For each entity in rules/elements.yaml, a micro-table is built from the element CONTRACTS alone, and one clean
row is synthesised to satisfy every contract. Then:
  * clean control: zero generated rules fail on the clean estate;
  * mutation controls: for each generated rule, ONE targeted mutation makes that rule fail, and nothing else
    fails except rules that legitimately depend on the mutated value (refs to it). The expectations come from
    the contract (the fixture recipe), never from a previous run of the agent;
  * both dialects render; the Snowflake text parses (sqlglot), DuckDB executes;
  * generation is deterministic.
Nothing here reads the synthetic ledger.
"""
import copy
import datetime as dt
import re

import duckdb
import pytest
import sqlglot
import yaml

from dqagent.engine.evaluate import npi_valid as npi_valid_py
from dqagent.rules import basic, model
from dqagent.rules.ir import EMITTERS, Ctx, canonical

ELEMENTS = yaml.safe_load(open(model.ROOT / "rules" / "elements.yaml"))
ENTS = ELEMENTS["entities"]
ELS = ELEMENTS["elements"]
SYS = ["_dq_batch_id", "_dq_file_id", "_dq_unit_key", "_dq_row_seq", "_dq_source_record_key"]
SQLT = {"str": "VARCHAR", "dec": "DECIMAL(18,3)", "int": "INTEGER", "date": "DATE", "ts": "TIMESTAMP"}
GOOD_NPI = "1234567893"


def col(el):
    return el.lower().replace(".", "_")


def rel(ent):
    return f"t.{ent.lower()}"


def _state_cfg():
    return {"profile": {"packs": [basic.PACK], "params": {}},
            "elements": {"entities": {e: rel(e) for e in ENTS},
                         "columns": {el: col(el) for el in ELS}},
            "feeds": {"feeds": {m["feed"]: {"delivered": True, "elements": "all"} for m in ENTS.values()}},
            "overlays": {}}


def sample(pattern: str) -> str:
    """Smallest string matching a restricted ERE (first branch, first class member, minimum repeat)."""
    out, i = [], 0
    while i < len(pattern):
        ch = pattern[i]
        if ch == "(":
            depth, j = 1, i + 1
            while depth:
                depth += {"(": 1, ")": -1}.get(pattern[j], 0)
                j += 1
            group = pattern[i + 1:j - 1]
            if j < len(pattern) and pattern[j] == "?":
                i = j + 1
                continue
            first = re.split(r"\|(?![^\[]*\])", group)[0]
            out.append(sample(first))
            i = j
            continue
        if ch == "[":
            j = pattern.index("]", i)
            cls = pattern[i + 1:j]
            atom, i = cls[0], j + 1
        else:
            atom, i = ch, i + 1
        rep = 1
        if i < len(pattern) and pattern[i] == "{":
            k = pattern.index("}", i)
            rep = int(pattern[i + 1:k].split(",")[0])
            i = k + 1
        out.append(atom * rep)
    s = "".join(out)
    assert re.fullmatch(pattern, s), (pattern, s)
    return s


def _date_depth(el, seen=()):
    c = (ELS[el].get("contract") or {})
    if "after_or_eq" in c and el not in seen:
        return 1 + _date_depth(c["after_or_eq"], seen + (el,))
    return 0


def clean_value(el):
    m = ELS[el]
    c = m.get("contract") or {}
    t = m.get("type", "str")
    if "ref" in c:
        return clean_value(c["ref"])
    if t == "date":
        return dt.date(2000 + 5 * _date_depth(el), 1, 1)
    if t == "ts":
        return dt.datetime(2026, 1, 1)
    if t in ("dec", "int"):
        v = max(c.get("min", 1), 1)
        return min(v, c.get("max", v))
    if c.get("npi"):
        return GOOD_NPI
    if "values" in c:
        return c["values"][0]
    if "pattern" in c:
        return sample(c["pattern"])
    return "ID1"


def bad_value(el):
    """A value violating exactly the element's VALIDITY contract (first applicable check)."""
    c = ELS[el]["contract"]
    if "pattern" in c:
        return "!"
    if "values" in c:
        return "ZZZ_NOT_IN_LIST"
    if "min" in c:
        return c["min"] - 1
    if "max" in c:
        return c["max"] + 1
    if c.get("npi"):
        return "1234567890"
    raise AssertionError(el)


@pytest.fixture(scope="module")
def estate():
    rules = [r for r in model.compile_rules("TEST_ALL", cfg=_state_cfg()) if r.pack == basic.PACK]
    assert rules and all(r.status == "ACTIVE" for r in rules), [(r.id, r.status_reason) for r in rules
                                                                 if r.status != "ACTIVE"]
    return rules


def build(con, mutate=None):
    con.execute("DROP SCHEMA IF EXISTS t CASCADE")
    con.execute("CREATE SCHEMA t")
    rows = {}
    for ent in ENTS:
        els = sorted(e for e, m in ELS.items() if m["entity"] == ent)
        cols = ", ".join(f"{col(e)} {SQLT[ELS[e].get('type', 'str')]}" for e in els)
        con.execute(f"CREATE TABLE {rel(ent)} ({cols}, {', '.join(s + ' VARCHAR' for s in SYS)})")
        rows[ent] = [{**{e: clean_value(e) for e in els}, "_dq_row_seq": "1"}]
    if mutate:
        mutate(rows)
    for ent, rs in rows.items():
        for r in rs:
            els = [k for k in r if not k.startswith("_")]
            vals = [r[e] for e in els] + ["B1", "F1", "U1", r["_dq_row_seq"], "K" + r["_dq_row_seq"]]
            names = [col(e) for e in els] + SYS
            con.execute(f"INSERT INTO {rel(ent)} ({', '.join(names)}) VALUES ({', '.join('?' * len(vals))})", vals)


def failing(con, rules) -> set[str]:
    em = EMITTERS["duckdb"]
    out = set()
    for r in rules:
        applies, asserted = em.predicates({"assert": r.assert_ir, "applies_when": r.applies_ir}, {})
        n = con.execute(f"SELECT count(*) FROM {r.relation} h WHERE ({applies}) IS TRUE "
                        f"AND (({asserted}) IS NOT TRUE)").fetchone()[0]
        if n:
            out.add(r.id)
    return out


def test_every_declared_domain_has_basic_rules(estate):
    by_dom = {}
    for r in estate:
        by_dom.setdefault(ENTS[r.entity]["domain"], set()).add(r.header["dimension"])
    domains = {m["domain"] for m in ENTS.values()}
    assert domains == set(by_dom), domains - set(by_dom)
    assert all({"completeness", "uniqueness"} <= dims for d, dims in by_dom.items()
               if any(ENTS[e]["domain"] == d and ENTS[e].get("keys") for e in ENTS)), by_dom


def test_clean_estate_has_zero_failures_and_both_dialects_render(estate):
    con = duckdb.connect()
    build(con)
    assert failing(con, estate) == set()
    for r in estate:
        assert r.header["gating"] == "advisory"
        sql = EMITTERS["snowflake"].expr(r.assert_ir, Ctx({}))
        sqlglot.parse_one(f"SELECT 1 FROM x h WHERE {sql}", read="snowflake")


def _ref_dependents(el):
    return {f"B-REF-{e.replace('.', '-')}" for e, m in ELS.items() if (m.get("contract") or {}).get("ref") == el}


def test_each_generated_rule_fires_on_exactly_its_mutation(estate):
    con = duckdb.connect()
    for r in estate:
        _, dim, rest = r.id.split("-", 2)
        el = None if dim == "UNQ" else rest.replace("-", ".", 1)

        def mutate(rows, r=r, el=el, dim=dim):
            if dim == "UNQ":
                row = copy.deepcopy(rows[r.entity][0])
                row["_dq_row_seq"] = "2"
                rows[r.entity].append(row)
                return
            row = rows[ELS[el]["entity"]][0]
            if dim == "CMP":
                row[el] = None
            elif dim == "VAL":
                row[el] = bad_value(el)
            elif dim == "CON":
                other = ELS[el]["contract"]["after_or_eq"]
                row[el] = rows[ELS[other]["entity"]][0][other] - dt.timedelta(days=1)
            elif dim == "REF":
                row[el] = "NOPE"
            elif dim == "TML":
                lg = ELS[el]["contract"]["lag_max_days"]
                row[el] = row[lg["from"]] + dt.timedelta(days=int(lg["days"]) + 1)
            else:
                raise AssertionError(r.id)
        build(con, mutate)
        got = failing(con, estate)
        allowed = {r.id} | (_ref_dependents(el) if el and dim in ("VAL", "CMP", "REF") else set())
        assert r.id in got, f"{r.id}: its mutation did not fire it"
        assert got <= allowed, f"{r.id}: mutation also fired {sorted(got - allowed)}"


def test_generation_is_deterministic():
    a = canonical(basic.generate(ELEMENTS))
    b = canonical(basic.generate(copy.deepcopy(ELEMENTS)))
    assert a == b


def test_promote_overlay_makes_a_basic_rule_gating():
    cfg = _state_cfg()
    cfg["overlays"] = {"B-CMP-CLM-ID": {"verb": "promote", "reason": "state requires claim id", "approved_ref": "T-1"}}
    r = next(r for r in model.compile_rules("TEST_ALL", cfg=cfg) if r.id == "B-CMP-CLM-ID")
    assert r.header["gating"] == "gate"
    base = next(r for r in model.compile_rules("TEST_ALL", cfg=_state_cfg()) if r.id == "B-CMP-CLM-ID")
    assert base.effective_hash != r.effective_hash        # promotion changes the approved artifact


def test_good_npi_is_valid():
    assert npi_valid_py(GOOD_NPI) and not npi_valid_py("1234567890")
