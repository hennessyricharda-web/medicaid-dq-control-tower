"""DuckDB data plane: the T1 reference implementation of the DataPlane protocol.

DuckDB has no time travel and no grants, so at T1:
  * "AS OF the frozen landing version" is EMULATED. The frozen load set is copied, and a
    keyed digest over ALL landing columns is recomputed over the gated copy INSIDE the copy
    transaction and compared before COMMIT (ENG-W1-6).
  * Every thread uses its own DuckDB cursor (a duplicate connection on the same database),
    so a request thread never shares an open transaction with the publisher (SEC-W1-2).
  * Append-only landing and grant enforcement are NOT enforceable in DuckDB. They are
    covered by the per-platform T2 kits and tagged T2-REQUIRED. Nothing here claims them.
"""
from __future__ import annotations

import threading

import duckdb

from dqagent.evidence import dqhmac

CANON = ["_dq_batch_id", "_dq_load_id", "_dq_file_id", "_dq_source_system", "_dq_source_record_key"]
GATED_EXTRA = ["_dq_unit_key", "_dq_row_seq"]


class DuckDBDataPlane:
    platform = "duckdb"
    dialect = "duckdb"

    def __init__(self, path: str = ":memory:", keys: dict[str, bytes] | None = None):
        self.con = duckdb.connect(path)
        self._local = threading.local()
        self.keys = keys or {}
        for s in ("landing", "raw", "bronze", "silver", "gold", "dq_rel", "dq_reveal"):
            self.con.execute(f"CREATE SCHEMA IF NOT EXISTS {s}")
        self.con.execute("CREATE SEQUENCE IF NOT EXISTS dq_rel.row_seq START 1")
        self._register_udfs()

    # -- in-DP keyed functions (T1 Python UDFs; per-platform equivalents in the T2 kits) --
    def _register_udfs(self):
        for kname in ("K_EV", "K_FP", "K_ABC"):
            if kname not in self.keys:
                continue
            key = self.keys[kname]
            # ONE function over a VARCHAR[] list: any number of columns, no arity cap (ENG-W1-6).
            # The list is order-preserving and NULL elements are kept, matching dq_hmac(key, fields).
            self.con.create_function(f"dq_hmac_{kname.lower()}", (lambda k: lambda xs: dqhmac.dq_hmac(k, list(xs)))(key),
                                     ["VARCHAR[]"], "VARCHAR", null_handling="special")

    def hmac_sql(self, kname: str, exprs: list[str]) -> str:
        return f"dq_hmac_{kname.lower()}([{', '.join(f'CAST({e} AS VARCHAR)' for e in exprs)}])"

    def lanes_sql(self, token_sql: str) -> str:
        parts = [f"SUM(CAST(('0x' || substr({token_sql}, {i*15+1}, 15))::UBIGINT AS DECIMAL(38,0)))"
                 for i in range(4)]
        return ", ".join(parts)

    # -- execution (thread-local cursor) --
    def cur(self):
        c = getattr(self._local, "c", None)
        if c is None:
            c = self._local.c = self.con.cursor()
        return c

    def execute(self, sql: str, params=None):
        return self.cur().execute(sql, params or [])

    def fetchall(self, sql: str, params=None):
        return self.cur().execute(sql, params or []).fetchall()

    def columns(self, relation: str) -> list[str]:
        sch, tbl = relation.split(".")
        return [r[0] for r in self.fetchall(
            "SELECT column_name FROM information_schema.columns WHERE table_schema=? AND table_name=? "
            "ORDER BY ordinal_position", [sch, tbl])]
