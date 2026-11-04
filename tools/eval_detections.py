"""Blind detection run (T1 evaluation harness; never deployed).

Loads one synthetic run through the agent exactly as the CLI demo does (one batch per inbound
file, in arrival order), then EXPORTS what the agent detected — without reading the ledger:

  * file_findings : every raw.findings row (envelope / transaction / flat-file checks), by file name
  * abc           : every ABC hop that is not PASS, by batch (= file)
  * row_failures  : every gated row on which a row rule's predicate is TRUE (or NULL), with locators
                    (file name, isa13, gs06, st02, encounter id, member id / person key, dates)
  * translator    : STs the translator rejected (filtered), by file
  * units         : final state of every unit and whether each row is visible in a released view

The scorer (a separate seat that alone may open ledger.json) maps these onto ledger entries.
This file has no knowledge of defect classes.

usage: python -m tools.eval_detections --data synthetic/out/w1 --out <detections.json> [--pg-port 55432]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import uuid
from pathlib import Path

import psycopg

from dqagent.cli.main import load_synthetic
from dqagent.cp import schema
from dqagent.runtime import Runtime

LOC = {
    "bronze.encounter_header": ["submitter_id", "encounter_id", "member_id", "billing_npi", "billing_alt_id",
                                "freq_code", "orig_encounter_id", "chain_root", "total_charge", "isa13", "gs06", "st02"],
    "bronze.encounter_line": ["submitter_id", "encounter_id", "line_no", "proc_code", "svc_date", "paid_amt",
                              "adjud_date", "isa13", "gs06", "st02"],
    "bronze.ie_segment": ["person_source_key", "msis_id", "program_code", "segment_type", "eff_start", "eff_end",
                          "dob", "recorded_at"],
    "bronze.enrollment_834": ["member_id", "ins03", "maint_code", "plan_id", "coverage_start", "coverage_end",
                              "isa13", "gs06", "st02"],
}


def _jsonable(v):
    return v if isinstance(v, (int, float, str, type(None), bool)) else str(v)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--pg-host", default="/tmp")
    ap.add_argument("--pg-port", type=int, default=55432)
    ap.add_argument("--pg-user", default="dq_gov")
    a = ap.parse_args(argv)
    pg = dict(host=a.pg_host, port=a.pg_port, user=a.pg_user)
    db = "dqeval_" + uuid.uuid4().hex[:8]
    with psycopg.connect(dbname="postgres", autocommit=True, **pg) as adm:
        adm.execute(f"CREATE DATABASE {db}")
    dsn = dict(dbname=db, **pg)
    with psycopg.connect(**dsn) as c:
        schema.apply(c)
    rt = Runtime(dsn, test_genesis=True, deployment="test")   # outside pytest: stated explicitly (Part F F-15)
    data = Path(a.data)
    captured: list[tuple] = []
    orig_run = rt.run_pipeline

    def run_and_capture(pipeline_id, batch_id, files, relations):
        """Row predicates are captured AT EVALUATION TIME for the batch just evaluated (later arrivals
        legitimately change e.g. the current eligibility view, so a post-hoc re-run would mislead)."""
        res = orig_run(pipeline_id, batch_id, files, relations)
        for rule in rt.rules:
            rel = rule.relation
            if rule.kind != "row" or rule.status != "ACTIVE" or rel not in LOC or rel not in relations:
                continue
            sel = ", ".join(f"h.{c}" for c in LOC[rel])
            fw = rule.fail_sql()
            app = rule.sql("duckdb", "applies") if rule.applies_ir is not None else "TRUE"   # in-scope rows only
            for row in rt.dp.fetchall(f"SELECT h._dq_file_id, h._dq_unit_key, ({fw}) AS v, {sel} "
                                      f"FROM {rel} h WHERE h._dq_batch_id = ? AND ({app}) IS TRUE "
                                      f"AND ({fw}) IS NOT FALSE",
                                      [batch_id]):
                captured.append((rule.id, rel, row))
        return res
    rt.run_pipeline = run_and_capture
    summary = load_synthetic(rt, data)
    name_of = {hashlib.sha256(p.read_bytes()).hexdigest(): p.name for p in data.iterdir() if p.is_file()}
    with rt.cp.as_role("dq_eval_svc") as c:
        # name each registered file by its BATCH (one batch per inbound file, "B-" + stem): two byte-identical
        # files (an exact re-send) have the same sha256 but different batches
        by_stem = {p.stem: p.name for p in data.iterdir() if p.is_file()}
        by_stem_all = by_stem
        files = {r["file_id"]: by_stem.get(r["batch_id"].removeprefix("B-"), name_of.get(r["sha256"], r["file_id"]))
                 for r in c.execute("SELECT file_id, sha256, batch_id FROM dq_ctl.files").fetchall()}
        abc = [dict(r) for r in c.execute("SELECT batch_id, authority, hop, measure, src_in, tgt_out, rejected, "
                                          "filtered, residual, disposition FROM dq_ctl.abc WHERE disposition <> 'PASS' "
                                          "ORDER BY id").fetchall()]
        units = {r["unit_key"]: r["state"] for r in c.execute("SELECT unit_key, state FROM dq_ctl.units").fetchall()}
        measures = [dict(r) for r in c.execute("SELECT batch_id, node, rule_id, numerator, denominator, value, "
                                               "disposition, long_disposition FROM dq_ctl.measure_results ORDER BY id")]
        results = c.execute("SELECT DISTINCT ON (unit_key, rule_id) unit_key, rule_id, disposition FROM dq_ctl.results "
                            "ORDER BY unit_key, rule_id, eval_version DESC, id DESC").fetchall()
    out = {"data": str(data), "file_findings": [], "abc": [], "row_failures": [], "translator_rejected": [],
           "unit_rule_dispositions": [], "rows": [], "measures": []}
    for m in measures:                       # batch-level measures, named by file (batch = "B-" + file stem)
        out["measures"].append({**{k: _jsonable(v) for k, v in m.items()},
                                "file": by_stem_all.get(m["batch_id"].removeprefix("B-"), m["batch_id"])})
    for f in rt.dp.fetchall("SELECT file_id, check_id, severity, message, isa13, gs06, st02 FROM raw.findings"):
        out["file_findings"].append({"file": files.get(f[0], f[0]), "check_id": f[1], "severity": f[2],
                                     "message": f[3], "isa13": f[4], "gs06": f[5], "st02": f[6]})
    for r in abc:
        out["abc"].append({k: _jsonable(v) for k, v in r.items()})
    for name, s in summary.items():
        for h in s["abc"]:
            if h["hop"] == "TRANSLATOR" and (h.get("rejected") or 0) > 0:
                out["translator_rejected"].append({"file": name, "rejected": h["rejected"]})
    for r in results:
        if r["disposition"] != "PASS":
            out["unit_rule_dispositions"].append({"unit_key": r["unit_key"], "rule_id": r["rule_id"],
                                                  "disposition": r["disposition"], "unit_state": units.get(r["unit_key"])})
    for rule_id, rel, row in captured:
        cols = LOC[rel]
        out["row_failures"].append({"rule_id": rule_id, "relation": rel, "file": files.get(row[0], row[0]),
                                    "predicate": "TRUE" if row[2] else "NULL", "unit_state": units.get(row[1]),
                                    **{c: _jsonable(v) for c, v in zip(cols, row[3:])}})
    for rel, cols in LOC.items():
        short = rel.split(".")[1]
        vis = {r[0] for r in rt.dp.fetchall(f"SELECT _dq_row_seq FROM dq_rel.{short}_released")}
        sel = ", ".join(cols)
        for row in rt.dp.fetchall(f"SELECT _dq_file_id, _dq_unit_key, _dq_row_seq, {sel} FROM {rel}"):
            out["rows"].append({"relation": rel, "file": files.get(row[0], row[0]), "unit_state": units.get(row[1]),
                                "released_visible": row[2] in vis, **{c: _jsonable(v) for c, v in zip(cols, row[3:])}})
    out["unit_state_counts"] = {}
    for st in units.values():
        out["unit_state_counts"][st] = out["unit_state_counts"].get(st, 0) + 1
    Path(a.out).write_text(json.dumps(out, indent=1, default=str))
    with psycopg.connect(dbname="postgres", autocommit=True, **pg) as adm:
        adm.execute(f"DROP DATABASE {db} WITH (FORCE)")
    print(json.dumps({"files": len(summary), "unit_states": out["unit_state_counts"],
                      "file_findings": len(out["file_findings"]), "abc_non_pass": len(out["abc"]),
                      "row_failures": len(out["row_failures"])}))


if __name__ == "__main__":
    main()
