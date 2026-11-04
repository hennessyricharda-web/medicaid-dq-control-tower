"""Portable rule IR (docs/rules/RULES_DESIGN.md §2).

A rule predicate is a small, CLOSED expression tree written in YAML/JSON. There is no raw SQL
in the catalog: each dialect emitter renders the tree, so one rule produces equivalent SQL for
DuckDB and Snowflake (Databricks / BigQuery emitters are wave 3).

Node shapes (YAML):
    {col: name}                       column of the evaluated row (alias h)
    {col: name, of: <alias>}          column of a source row inside exists/count_of/sum_of
    {col: name, of: outer}            the evaluated row, from inside a correlated source
    {lit: value}                      str | int | float | bool | null;  {date: "2025-10-01"}
    {param: name}                     bound from the state profile at compile time
    {op: [args]}                      see OPS below
    {exists: {source: S, as: x, where: E}}
    {count_of: {source: S, as: x, where: E}}
    {sum_of: {source: S, as: x, expr: E, where: E}}
    {npi_luhn: E}                     NPI check digit over the 80840 prefix, in pure SQL
    {codeset_valid: {system: str|E, code: E, date: E, billable: bool}}
Sources S:
    "bronze.encounter_line"                                  a relation
    {latest_per: {source: S, key: [..], order: [..desc..], where: E}}
    {union_all: [ {source: S, select: [cols]} , ... ]}
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass

COMPARE = {"eq": "=", "ne": "<>", "lt": "<", "le": "<=", "gt": ">", "ge": ">="}
BOOL = {"and", "or"}
UNARY = {"not", "is_null", "not_null", "upper", "trim", "length"}
NARY = {"coalesce"}
ALLOWED_KEYS = (set(COMPARE) | BOOL | UNARY | NARY |
                {"col", "lit", "date", "param", "between", "distinct_from", "in", "matches", "substr",
                 "exists", "count_of", "sum_of", "npi_luhn", "codeset_valid", "add", "sub"})
_ERE_OK = re.compile(r"^(?:[A-Za-z0-9 _\-.:/]|\[[^\]\\]+\]|\{\d+(?:,\d*)?\}|[?*+|()])*$")


class IRError(ValueError):
    pass


def canonical(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


PRESENTATION_FIELDS = {"title", "citation", "analog_of", "req_ids", "notes", "version", "status_hint", "description"}


def semantic_hash(rule: dict) -> str:
    """Hash of everything that can change MEANING: the whole normalised rule MINUS presentation fields
    (deny-list, Part B review finding 1). Editing numerator / denominator / kind / requires / entities / any
    logic therefore changes the hash and requires re-approval."""
    keep = {k: v for k, v in rule.items() if k not in PRESENTATION_FIELDS}
    return hashlib.sha256(canonical(keep).encode()).hexdigest()[:16]


def validate(node, path="$"):
    """Reject anything outside the closed grammar (unknown ops, raw SQL strings, unsafe regex)."""
    if isinstance(node, dict):
        if len(node) != 1 and not ({"col", "of"} >= set(node) or "exists" in node):
            if not (set(node) <= {"col", "of"}):
                raise IRError(f"{path}: a node has exactly one operator key, got {sorted(node)}")
        for k, v in node.items():
            if k == "of":
                continue
            if k not in ALLOWED_KEYS:
                raise IRError(f"{path}: unknown operator {k!r}")
            if k == "matches":
                if not (isinstance(v, list) and len(v) == 2 and isinstance(v[1], (str, dict))):
                    raise IRError(f"{path}.matches: [expr, pattern]")
                if isinstance(v[1], str) and not _ERE_OK.match(v[1]):
                    raise IRError(f"{path}.matches: pattern outside the restricted ERE: {v[1]!r}")
            if k == "col" and not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", str(v)):
                raise IRError(f"{path}.col: bad identifier {v!r}")
            if k in ("lit", "date", "param", "col"):
                continue
            validate(v, f"{path}.{k}")
    elif isinstance(node, list):
        for i, x in enumerate(node):
            validate(x, f"{path}[{i}]")
    elif isinstance(node, (str, int, float, bool)) or node is None:
        return
    else:
        raise IRError(f"{path}: unsupported node {type(node).__name__}")


@dataclass
class Ctx:
    params: dict
    outer: str = "h"
    scope: str = "h"
    n: int = 0

    def fresh(self, base="s"):
        self.n += 1
        return f"{base}{self.n}"


class Emitter:
    """Base SQL emitter. Subclasses override the dialect hooks only."""
    dialect = "base"

    # ---- dialect hooks ------------------------------------------------------------------
    def regex_full(self, expr: str, pattern: str) -> str:
        raise NotImplementedError

    def count_if(self, pred: str) -> str:
        raise NotImplementedError

    def list_agg_sample(self, tok: str, pred: str) -> str:
        raise NotImplementedError

    def date_lit(self, s: str) -> str:
        return f"DATE '{s}'"

    def str_lit(self, s: str) -> str:
        return "'" + str(s).replace("'", "''") + "'"

    # ---- expression rendering ---------------------------------------------------------
    def expr(self, n, cx: Ctx) -> str:
        if not isinstance(n, dict):
            raise IRError(f"expected a node, got {n!r}")
        if "col" in n:
            # Unqualified = the EVALUATED row (h). Rows of a correlated source must be named: of: <alias>,
            # or of: self for the source currently being filtered (latest_per / union_all part).
            of = n.get("of")
            alias = cx.scope if of == "self" else cx.outer if of in (None, "outer") else of
            return f"{alias}.{n['col']}"
        (k, v), = [(k, v) for k, v in n.items() if k != "of"]
        if k == "lit":
            if v is None:
                return "NULL"
            if isinstance(v, bool):
                return "TRUE" if v else "FALSE"
            if isinstance(v, (int, float)):
                return repr(v)
            return self.str_lit(v)
        if k == "date":
            return self.date_lit(v)
        if k == "param":
            if v not in cx.params:
                raise IRError(f"unbound parameter {v!r} (set it in the state profile)")
            p = cx.params[v]
            return self.str_lit(p) if isinstance(p, str) else repr(p)
        if k in COMPARE:
            a, b = v
            return f"({self.expr(a, cx)} {COMPARE[k]} {self.expr(b, cx)})"
        if k in BOOL:
            return "(" + f" {k.upper()} ".join(self.expr(x, cx) for x in v) + ")"
        if k == "not":
            return f"(NOT {self.expr(v, cx)})"
        if k == "is_null":
            return f"({self.expr(v, cx)} IS NULL)"
        if k == "not_null":
            return f"({self.expr(v, cx)} IS NOT NULL)"
        if k in ("upper", "trim", "length"):
            return f"{k.upper()}({self.expr(v, cx)})"
        if k == "coalesce":
            return "COALESCE(" + ", ".join(self.expr(x, cx) for x in v) + ")"
        if k in ("add", "sub"):
            return f"({self.expr(v[0], cx)} {'+' if k == 'add' else '-'} {self.expr(v[1], cx)})"
        if k == "substr":
            e, start, ln = v
            return f"SUBSTR({self.expr(e, cx)}, {int(start)}, {int(ln)})"
        if k == "between":
            e, lo, hi = v
            return f"({self.expr(e, cx)} BETWEEN {self.expr(lo, cx)} AND {self.expr(hi, cx)})"
        if k == "distinct_from":
            a, b = v
            return f"({self.expr(a, cx)} IS DISTINCT FROM {self.expr(b, cx)})"
        if k == "in":
            e, items = v
            return f"({self.expr(e, cx)} IN (" + ", ".join(self.expr({'lit': i}, cx) for i in items) + "))"
        if k == "matches":
            e, pat = v
            if isinstance(pat, dict):
                pat_s = cx.params.get(pat.get("param"))
                if not isinstance(pat_s, str) or not _ERE_OK.match(pat_s):
                    raise IRError(f"pattern parameter {pat} unbound or outside the restricted ERE")
            else:
                pat_s = pat
            return self.regex_full(self.expr(e, cx), pat_s)
        if k == "npi_luhn":
            return self.npi_luhn(self.expr(v, cx))
        if k == "codeset_valid":
            return self.codeset_valid(v, cx)
        if k in ("exists", "count_of", "sum_of"):
            return self.correlated(k, v, cx)
        raise IRError(f"unhandled operator {k}")

    def npi_luhn(self, e: str) -> str:
        """NPI check digit (Luhn over '80840' + first 9 digits) in portable SQL. NULL in -> NULL out."""
        dbl = []
        for i in range(9):                       # NPI positions 1..9; doubled positions are 1,3,5,7,9
            d = f"CAST(SUBSTR({e}, {i + 1}, 1) AS INTEGER)"
            if i % 2 == 0:
                dbl.append(f"(CASE WHEN {d} * 2 > 9 THEN {d} * 2 - 9 ELSE {d} * 2 END)")
            else:
                dbl.append(d)
        total = "24 + " + " + ".join(dbl)        # 24 = Luhn contribution of the 80840 prefix
        check = f"CAST(SUBSTR({e}, 10, 1) AS INTEGER)"
        return (f"(CASE WHEN {e} IS NULL THEN NULL WHEN NOT {self.regex_full(e, '[0-9]{10}')} THEN FALSE "
                f"ELSE MOD(10 - MOD({total}, 10), 10) = {check} END)")

    def codeset_valid(self, v: dict, cx: Ctx) -> str:
        """TRUE: a row is effective on the date. FALSE: the system is loaded (approved) and no row matches.
        NULL: system not loaded, or code/date NULL. Never PASS for an unloaded (e.g. licensed) system."""
        sysv = v["system"]
        sys_sql = self.expr(sysv, cx) if isinstance(sysv, dict) else self.str_lit(sysv)
        code, date = self.expr(v["code"], cx), self.expr(v["date"], cx)
        bill = " AND c.billable" if v.get("billable") else ""
        a = cx.fresh("c")
        return (f"(CASE WHEN {code} IS NULL OR {date} IS NULL THEN NULL "
                f"WHEN NOT EXISTS (SELECT 1 FROM dq_ref.codeset_loaded l WHERE l.system = {sys_sql}) THEN NULL "
                f"WHEN EXISTS (SELECT 1 FROM dq_ref.codeset_active {a} WHERE {a}.system = {sys_sql} "
                f"AND {a}.code = {code} AND {a}.eff_from <= {date} "
                f"AND ({a}.eff_to IS NULL OR {date} <= {a}.eff_to){bill.replace('c.', a + '.')}) THEN TRUE "
                f"ELSE FALSE END)")

    def source(self, s, cx: Ctx, alias: str) -> str:
        if isinstance(s, str):
            if not re.fullmatch(r"[a-z_]+\.[a-z0-9_]+", s):
                raise IRError(f"bad relation {s!r}")
            return f"{s} {alias}"
        if "latest_per" in s:
            lp = s["latest_per"]
            inner_alias = cx.fresh("lp")
            inner = self.source(lp["source"], cx, inner_alias)
            sub = Ctx(cx.params, outer=cx.outer, scope=inner_alias, n=cx.n)
            where = f" WHERE {self.expr(lp['where'], sub)}" if lp.get("where") else ""
            cx.n = sub.n
            order = ", ".join(f"{inner_alias}.{c.split()[0]} {'DESC' if c.lower().endswith(' desc') else 'ASC'} NULLS LAST"
                              for c in lp["order"])            # explicit NULLS LAST: Snowflake defaults differ (RED-1)
            key = ", ".join(f"{inner_alias}.{k}" for k in lp["key"])
            return (f"(SELECT * FROM (SELECT {inner_alias}.*, ROW_NUMBER() OVER (PARTITION BY {key} ORDER BY {order}) "
                    f"AS dq_rk FROM {inner}{where}) WHERE dq_rk = 1) {alias}")
        if "union_all" in s:
            parts = []
            for part in s["union_all"]:
                pa = part.get("as") or cx.fresh("u")
                src = self.source(part["source"], cx, pa)
                sub = Ctx(cx.params, outer=cx.outer, scope=pa, n=cx.n)
                where = f" WHERE {self.expr(part['where'], sub)}" if part.get("where") else ""
                cx.n = sub.n
                cols = ", ".join(f"{pa}.{c} AS c{i}" for i, c in enumerate(part["select"]))   # positional: {pos: i}
                parts.append(f"SELECT {cols} FROM {src}{where}")
            return "(" + " UNION ALL ".join(parts) + f") {alias}"
        raise IRError(f"bad source {s!r}")

    def correlated(self, k: str, v: dict, cx: Ctx) -> str:
        alias = v.get("as") or cx.fresh("x")
        src = self.source(v["source"], cx, alias)
        sub = Ctx(cx.params, outer=cx.outer, scope=alias, n=cx.n)
        where = f" WHERE {self.expr(v['where'], sub)}" if v.get("where") else ""
        out = None
        if k == "exists":
            out = f"EXISTS (SELECT 1 FROM {src}{where})"
        elif k == "count_of":
            out = f"(SELECT COUNT(*) FROM {src}{where})"
        else:
            out = f"(SELECT SUM({self.expr(v['expr'], sub)}) FROM {src}{where})"
        cx.n = sub.n
        return out

    # ---- whole-rule rendering ---------------------------------------------------------
    def predicates(self, rule: dict, params: dict) -> tuple[str, str]:
        """(applies_sql, assert_sql) over alias h."""
        cx = Ctx(params)
        applies = self.expr(rule["applies_when"], cx) if rule.get("applies_when") else "TRUE"
        asserted = self.expr(rule["assert"], cx)
        return applies, asserted

    def unit_query(self, rule: dict, params: dict, relation: str, token_sql: str) -> str:
        """Per-unit aggregation over one batch (bind ? = batch_id). Columns:
        unit_key, evaluated (assert defined), failed (assert FALSE), sample tokens, undefined (assert NULL).
        Rows where applies_when is not TRUE are out of scope and not counted."""
        applies, asserted = self.predicates(rule, params)
        fail = f"(NOT {asserted})"
        return (f"SELECT h._dq_unit_key, {self.count_if(f'({asserted}) IS NOT NULL')}, {self.count_if(fail)}, "
                f"{self.list_agg_sample(token_sql, fail)}, {self.count_if(f'({asserted}) IS NULL')} "
                f"FROM {relation} h WHERE h._dq_batch_id = ? AND ({applies}) IS TRUE GROUP BY h._dq_unit_key")


    # ---- decorrelated shapes (RED-1): every correlated subquery sits in a WHERE clause ---------------
    def unit_query_where_only(self, rule: dict, params: dict, relation: str, token_sql: str) -> str:
        """Same contract as unit_query (bind ? = batch_id FOUR times), but no correlated subquery appears in a
        SELECT list or aggregate: each of evaluated / failed / undefined is its own WHERE-filtered aggregate.
        Snowflake accepts correlated EXISTS / scalar subqueries only in WHERE."""
        applies, asserted = self.predicates(rule, params)
        base = f"FROM {relation} h WHERE h._dq_batch_id = ? AND ({applies}) IS TRUE"
        return (f"SELECT a.u, COALESCE(ev.n, 0), COALESCE(fl.n, 0), fl.toks, COALESCE(ud.n, 0) FROM "
                f"(SELECT DISTINCT h._dq_unit_key AS u {base}) a "
                f"LEFT JOIN (SELECT h._dq_unit_key AS u, COUNT(*) AS n {base} AND ({asserted}) IS NOT NULL "
                f"GROUP BY h._dq_unit_key) ev ON ev.u = a.u "
                f"LEFT JOIN (SELECT h._dq_unit_key AS u, COUNT(*) AS n, {self.list_agg_all(token_sql)} AS toks {base} "
                f"AND NOT ({asserted}) GROUP BY h._dq_unit_key) fl ON fl.u = a.u "
                f"LEFT JOIN (SELECT h._dq_unit_key AS u, COUNT(*) AS n {base} AND ({asserted}) IS NULL "
                f"GROUP BY h._dq_unit_key) ud ON ud.u = a.u")

    def measure_queries(self, num_sql: str, den_sql: str, relation: str, distinct_col: str | None) -> tuple[str, str]:
        """(numerator query, denominator query), each binding ? = batch_id once; predicates only in WHERE."""
        agg = f"COUNT(DISTINCT h.{distinct_col})" if distinct_col else "COUNT(*)"
        base = f"SELECT {agg} FROM {relation} h WHERE h._dq_batch_id = ?"
        return f"{base} AND ({den_sql}) AND ({num_sql})", f"{base} AND ({den_sql})"

    def list_agg_all(self, tok: str) -> str:
        raise NotImplementedError


class DuckDBEmitter(Emitter):
    dialect = "duckdb"

    def regex_full(self, expr, pattern):
        return f"regexp_full_match({expr}, {self.str_lit(pattern)})"

    def count_if(self, pred):
        return f"count(*) FILTER (WHERE {pred})"

    def list_agg_sample(self, tok, pred):
        return f"list({tok} ORDER BY {tok}) FILTER (WHERE {pred})"

    def list_agg_all(self, tok):
        return f"list({tok} ORDER BY {tok})"


class SnowflakeEmitter(Emitter):
    dialect = "snowflake"

    def regex_full(self, expr, pattern):
        return f"REGEXP_LIKE({expr}, {self.str_lit(pattern)})"      # Snowflake REGEXP_LIKE is implicitly anchored

    def count_if(self, pred):
        return f"COUNT_IF({pred})"

    def list_agg_sample(self, tok, pred):
        return f"ARRAY_AGG(CASE WHEN {pred} THEN {tok} END) WITHIN GROUP (ORDER BY {tok})"

    def list_agg_all(self, tok):
        return f"ARRAY_AGG({tok}) WITHIN GROUP (ORDER BY {tok})"


EMITTERS = {"duckdb": DuckDBEmitter(), "snowflake": SnowflakeEmitter()}
