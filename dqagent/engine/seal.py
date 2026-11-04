"""SEAL (DESIGN §3.3) at T1 on DuckDB.

Steps, in this order:
 1. The committed load set for (batch, relation) is read and a keyed digest over ALL landing
    columns is taken per load.
 2. dq_seal copies exactly those loads from landing into the gated relation, assigning
    _dq_row_seq and _dq_unit_key, and INSIDE the same DuckDB transaction recomputes the digest
    over the gated copy's landing columns. A mismatch (landing mutated between digest and copy)
    rolls back and raises SealIntegrityError (ENG-W1-6). Only after COMMIT are the FROZEN
    events written to the CP, so a failed copy never strands its loads.
 3. A fingerprint (keyed lanes) is computed per unit, IN THE DATA PLANE.
 4. In ONE CP transaction: units are created (LANDED), transitioned to SEALED, and their
    fingerprint and max_row_seq recorded; the batch is marked sealed.

unit_key never contains raw identifiers. It is (batch, program, partition token), and the
partition token is DQ_HMAC(K_EV, authority, 'PARTITION', partition) computed in the DP.
"""
from __future__ import annotations

import json

import re

from dqagent.evidence import dqhmac


def qualify(expr: str, columns: list[str], alias: str) -> str:
    """Prefix bare column references in `expr` with `alias.` (word-boundary safe)."""
    names = sorted(columns, key=len, reverse=True)
    pat = re.compile(r"(?<![\w.'])(" + "|".join(map(re.escape, names)) + r")\b(?!')")
    return pat.sub(lambda m: f"{alias}.{m.group(1)}", expr)


class SealIntegrityError(RuntimeError):
    pass


class Sealer:
    def __init__(self, cp, dp, contracts: dict, fp_key: bytes, epoch: int = 1):
        self.cp, self.dp, self.contracts, self.fp_key, self.epoch = cp, dp, contracts, fp_key, epoch

    def _frozen_loads(self, batch_id: str, relation: str) -> list[str]:
        with self.cp.as_role("dq_seal_svc") as c:
            rows = c.execute(
                "SELECT load_id FROM dq_ctl.loads_events WHERE batch_id=%s AND relation=%s AND event='COMMITTED'"
                " AND load_id NOT IN (SELECT load_id FROM dq_ctl.loads_events WHERE event='FROZEN')",
                (batch_id, relation)).fetchall()
        return [r["load_id"] for r in rows]

    @staticmethod
    def batch_where(batch_id: str) -> str:
        """The batch filter every load digest uses: at the seal, in the copy check and at recovery (one definition)."""
        return "AND _dq_batch_id = '" + batch_id.replace("'", "''") + "'"

    def _assert_load_in_batch(self, con, tbl: str, load_id: str, batch_id: str) -> None:
        """A load belongs to exactly one batch (engine.intake registers a fresh load id per batch and relation), so a
        landing row of the load tagged with another batch can only be tampering or a bug: the seal refuses it."""
        n = con.execute(f"SELECT count(*) FROM landing.{tbl} WHERE _dq_load_id = ? AND _dq_batch_id IS DISTINCT FROM ?",
                        [load_id, batch_id]).fetchall()[0][0]
        if n:
            raise SealIntegrityError(f"landing load {load_id} has {n} row(s) tagged with another batch than {batch_id}")

    def _digest(self, con, source: str, cols: list[str], load_id: str, extra_where: str = "") -> str:
        """Keyed digest over ALL `cols` (no arity cap) of one load in `source`, on connection `con`."""
        tok = self.dp.hmac_sql("K_FP", cols)
        r = con.execute(f"SELECT {self.dp.lanes_sql(tok)}, count(*) FROM {source} WHERE _dq_load_id = ? {extra_where}",
                        [load_id]).fetchall()[0]
        return dqhmac.dq_hmac(self.fp_key, [*(int(x or 0) for x in r[:4]), r[4]])

    def seal(self, batch_id: str, pipeline_id: str, relations: list[str]) -> list[str]:
        """Seal the relations of one batch, in the order given (parents before children).
        Returns the unit_keys created."""
        created: list[str] = []
        with self.cp.as_role("dq_seal_svc") as c:
            c.execute("INSERT INTO dq_ctl.batches(batch_id,pipeline_id,node) VALUES (%s,%s,%s) "
                      "ON CONFLICT (batch_id) DO NOTHING", (batch_id, pipeline_id, ",".join(relations)))
        freeze_record = {}
        for rel in relations:
            tbl = rel.split(".")[1]
            loads = self._frozen_loads(batch_id, rel)
            cols = self.dp.columns(f"landing.{tbl}")
            for ld in loads:
                self._assert_load_in_batch(self.dp.cur(), tbl, ld, batch_id)
            # R5 L4: the same batch-filtered digest the recovery recomputes (identical for a load of one batch)
            digests = {ld: self._digest(self.dp.cur(), f"landing.{tbl}", cols, ld, self.batch_where(batch_id))
                       for ld in loads}
            self._copy(batch_id, rel, loads, digests)
            with self.cp.as_role("dq_seal_svc") as c:
                for ld, dg in digests.items():
                    c.execute("INSERT INTO dq_ctl.loads_events(load_id,pipeline_id,relation,batch_id,event,"
                              "landing_version,landing_digest) VALUES (%s,%s,%s,%s,'FROZEN',%s,%s)",
                              (ld, pipeline_id, rel, batch_id, "T1-EMULATED", dg))
            freeze_record[rel] = digests
        units = self._units(batch_id, relations)
        with self.cp.as_role("dq_seal_svc") as c:
            for u in units:
                c.execute(
                    "INSERT INTO dq_ctl.units(unit_key,batch_id,pipeline_id,node,program_code,authorities,"
                    "partition_key,state) VALUES (%s,%s,%s,%s,%s,%s,%s,'LANDED')",
                    (u["unit_key"], batch_id, pipeline_id, u["relation"], u["program"], u["authorities"],
                     u["partition_token"]))
                self.cp.transition(c, u["unit_key"], 0, "SEALED", "SEAL", "dq_seal_svc",
                                   {"frozen": {k: list(v) for k, v in freeze_record.items()}})
                c.execute("UPDATE dq_ctl.units SET fingerprint=%s, fp_epoch=%s, max_row_seq=%s WHERE unit_key=%s",
                          (u["fingerprint"], self.epoch, u["max_row_seq"], u["unit_key"]))
                created.append(u["unit_key"])
            c.execute("UPDATE dq_ctl.batches SET sealed=true, load_set=%s::jsonb, fp_epoch=%s WHERE batch_id=%s",
                      (json.dumps({k: list(v) for k, v in freeze_record.items()}), self.epoch, batch_id))
        return created

    # -- data-plane side ------------------------------------------------------------------
    def _partition_sql(self, contract) -> str:
        return contract.get("partition_expr") or contract["partition"]

    def _program_sql(self, contract) -> str:
        if "program_column" in contract:
            return contract["program_column"]
        return f"'{contract['program_constant']}'"

    def _copy(self, batch_id: str, rel: str, loads: list[str], digests: dict[str, str]):
        if not loads:
            return
        tbl = rel.split(".")[1]
        c = self.contracts[rel]
        cols = [x for x in self.dp.columns(f"landing.{tbl}")]
        sel = ", ".join(f"l.{x}" for x in cols)
        in_list = ", ".join(f"'{x}'" for x in loads)
        con = self.dp.cur()
        con.execute("BEGIN")
        try:
            if "unit_from_parent" in c:
                p = c["unit_from_parent"]
                keys = p.get("join_keys") or [p["join"]]
                on = " AND ".join(f"ph.{k} IS NOT DISTINCT FROM l.{k}" for k in keys)
                con.execute(
                    f"INSERT INTO {rel} SELECT {sel}, ph._dq_unit_key, nextval('dq_rel.row_seq') "
                    f"FROM landing.{tbl} l JOIN (SELECT DISTINCT {', '.join(keys)}, _dq_batch_id, _dq_unit_key "
                    f"FROM {p['relation']} WHERE _dq_batch_id = '{batch_id}') ph "
                    f"ON {on} AND ph._dq_batch_id = l._dq_batch_id "
                    f"WHERE l._dq_load_id IN ({in_list})")
            else:
                prog = qualify(self._program_sql(c), cols, "l")
                part_l = qualify(self._partition_sql(c), cols, "l")
                auth_case = "CASE " + " ".join(
                    f"WHEN {prog} = '{pg}' THEN '{'|'.join(a)}'" for pg, a in c["authorities_by_program"].items()) + \
                    " ELSE 'AUTH_UNKNOWN' END"
                token = self.dp.hmac_sql("K_EV", [auth_case, "'PARTITION'", part_l])
                con.execute(
                    f"INSERT INTO {rel} SELECT {sel}, "
                    f"'{batch_id}:' || {prog} || ':' || substr({token}, 1, 24), nextval('dq_rel.row_seq') "
                    f"FROM landing.{tbl} l WHERE l._dq_load_id IN ({in_list}) ORDER BY l._dq_source_record_key")
            for ld, before in digests.items():       # same transaction: what was copied is what was digested
                after = self._digest(con, rel, cols, ld, self.batch_where(batch_id))
                if after != before:
                    raise SealIntegrityError(f"landing load {ld} changed between digest and copy (or a row failed "
                                             f"to attach to its parent); seal of {rel} rolled back")
            con.execute("COMMIT")
        except Exception:
            con.execute("ROLLBACK")
            raise

    def fingerprint_columns(self, rel: str) -> list[str]:
        excl = set(self.contracts[rel].get("fingerprint_exclude", [])) | {"_dq_unit_key"}
        return [c for c in self.dp.columns(rel) if c not in excl]

    def fingerprint_sql(self, rel: str, where: str) -> str:
        tok = self.dp.hmac_sql("K_FP", self.fingerprint_columns(rel))
        return f"SELECT {self.dp.lanes_sql(tok)}, count(*), max(_dq_row_seq) FROM {rel} WHERE {where}"

    def unit_fingerprint(self, batch_id: str, unit_key: str, relations: list[str], max_row_seq: int | None = None):
        """Fingerprint over all gated relations contributing to the unit. Pure recompute (the verifier reuses it)."""
        acc, n, mx = [0, 0, 0, 0], 0, 0
        for rel in relations:
            w = f"_dq_batch_id = '{batch_id}' AND _dq_unit_key = '{unit_key}'"
            if max_row_seq is not None:
                w += f" AND _dq_row_seq <= {int(max_row_seq)}"
            r = self.dp.fetchall(self.fingerprint_sql(rel, w))[0]
            if r[4]:
                acc = [a + int(b) for a, b in zip(acc, r[:4])]
                n += r[4]
                mx = max(mx, r[5] or 0)
        return acc, n, mx

    def _units(self, batch_id: str, relations: list[str]):
        out = []
        roots = [r for r in relations if "unit_from_parent" not in self.contracts[r]]
        for rel in roots:
            c = self.contracts[rel]
            prog = self._program_sql(c)
            members = [r for r in relations if r == rel or
                       self.contracts[r].get("unit_from_parent", {}).get("relation") == rel]
            rows = self.dp.fetchall(
                f"SELECT DISTINCT _dq_unit_key, {prog} FROM {rel} WHERE _dq_batch_id = ?", [batch_id])
            for unit_key, program in rows:
                lanes, n, mx = self.unit_fingerprint(batch_id, unit_key, members)
                fp = dqhmac.bind(self.fp_key, lanes, n, batch_id, unit_key, mx, self.epoch)
                auths = sorted(set(c["authorities_by_program"].get(program, ["AUTH_UNKNOWN"])))
                out.append({"unit_key": unit_key, "relation": rel, "program": program, "authorities": auths,
                            "partition_token": unit_key.split(":")[-1], "fingerprint": fp, "max_row_seq": mx,
                            "members": members})
        return out
