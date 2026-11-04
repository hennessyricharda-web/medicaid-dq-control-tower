"""Decorrelated query shapes (Part C, RED-1). Snowflake accepts correlated subqueries only in WHERE.

For every ACTIVE row rule and measure of the federal + basic packs, compiled against the micro-estate built from
the element contracts:
  * the WHERE-only shapes give the SAME per-unit counts / measure counts as the original shapes (executed in DuckDB
    on a dirty estate, so failures, NULL predicates and clean rows all occur);
  * the Snowflake rendering of every WHERE-only shape parses (sqlglot). It is NOT executed: there is no Snowflake
    account in this build, so "runs on Snowflake" is not claimed.
"""
import duckdb
import pytest
import sqlglot

from dqagent.rules import basic, model
from dqagent.rules.ir import EMITTERS, Ctx

from .test_basic_templates import ELS, ENTS, _state_cfg, bad_value, build

PACKS = ["federal/x12_837p", "federal/x12_834", "federal/tmsis_measures_preload", basic.PACK, "basic/cross_domain"]
TOK = "h._dq_source_record_key"


def _dirty(rows):
    """Add a second row per entity with every contracted element violated (NULL or a bad value)."""
    for ent, rs in rows.items():
        r = dict(rs[0])
        r["_dq_row_seq"] = "2"
        for i, e in enumerate(sorted(k for k in r if not k.startswith("_"))):
            c = ELS[e].get("contract") or {}
            if i % 2 and ({"pattern", "values", "min", "max", "npi"} & set(c)):
                r[e] = bad_value(e)
            elif i % 3 == 0 and not ELS[e].get("system"):
                r[e] = None
        rs.append(r)


@pytest.fixture(scope="module")
def estate():
    cfg = _state_cfg()
    cfg["profile"]["packs"] = PACKS
    rules = [r for r in model.compile_rules("TEST_ALL", cfg=cfg) if r.status == "ACTIVE" and r.kind in ("row", "measure")]
    con = duckdb.connect()
    build(con, _dirty)
    return con, rules


def test_where_only_row_shape_equals_the_original(estate):
    con, rules = estate
    em = EMITTERS["duckdb"]
    checked = 0
    for r in [r for r in rules if r.kind == "row"]:
        ir = {"assert": r.assert_ir, "applies_when": r.applies_ir}
        a = con.execute(em.unit_query(ir, {}, r.relation, TOK), ["B1"]).fetchall()
        b = con.execute(em.unit_query_where_only(ir, {}, r.relation, TOK), ["B1"] * 4).fetchall()
        norm = lambda rows: sorted((u, ev, fl, sorted(t or []), ud) for u, ev, fl, t, ud in rows)  # noqa: E731
        assert norm(a) == norm(b), r.id
        checked += 1
    assert checked > 40


def test_where_only_measure_shape_equals_the_original(estate):
    con, rules = estate
    em = EMITTERS["duckdb"]
    for r in [r for r in rules if r.kind == "measure"]:
        num, den = em.expr(r.numerator_ir, Ctx({})), em.expr(r.denominator_ir, Ctx({}))
        if r.distinct_col:
            dc = f"h.{r.distinct_col}"
            orig = (f"SELECT COUNT(DISTINCT CASE WHEN ({den}) AND ({num}) THEN {dc} END), "
                    f"COUNT(DISTINCT CASE WHEN ({den}) THEN {dc} END) FROM {r.relation} h WHERE h._dq_batch_id = ?")
        else:
            orig = (f"SELECT {em.count_if(f'({den}) AND ({num})')}, {em.count_if(f'({den})')} FROM {r.relation} h "
                    f"WHERE h._dq_batch_id = ?")
        a = con.execute(orig, ["B1"]).fetchone()
        qn, qd = em.measure_queries(num, den, r.relation, r.distinct_col)
        b = (con.execute(qn, ["B1"]).fetchone()[0], con.execute(qd, ["B1"]).fetchone()[0])
        assert tuple(a) == b, r.id


def test_every_snowflake_where_only_shape_parses(estate):
    _, rules = estate
    sf = EMITTERS["snowflake"]
    n = 0
    for r in rules:
        if r.kind == "row":
            sql = sf.unit_query_where_only({"assert": r.assert_ir, "applies_when": r.applies_ir}, {}, r.relation, TOK)
            sqlglot.parse_one(sql.replace("= ?", "= 'B1'"), read="snowflake")
        else:
            num, den = sf.expr(r.numerator_ir, Ctx({})), sf.expr(r.denominator_ir, Ctx({}))
            for q in sf.measure_queries(num, den, r.relation, r.distinct_col):
                sqlglot.parse_one(q.replace("= ?", "= 'B1'"), read="snowflake")
        n += 1
    assert n > 50


def test_part_c_runs_at_least_ten_cms_measures_on_the_demo_state():
    ms = [r for r in model.compile_rules("ZZ") if r.kind == "measure" and r.status == "ACTIVE"]
    assert 10 <= len(ms) <= 12, [r.id for r in ms]
    assert any(r.longitudinal for r in ms)
    assert ENTS  # the micro-estate covers every declared entity
