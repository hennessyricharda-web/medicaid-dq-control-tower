"""Intake: file registry → raw → landing (DESIGN §13, §3.3).

Each inbound file is registered in the CP file registry with its direction and
interchange keys. The parser always produces raw records. Bronze-mapped rows are written
to LANDING under a registered load_id: pipelines write landing only, and only SEAL writes
gated relations.

Translator acknowledgments are an ABC source, matched to the parsed ST set on
(isa13, gs06, st02) (ENG-W1-3): an ack row with no parsed ST, or a parsed ST with no ack row,
is residual; a MISSING ack file makes the TRANSLATOR hop UNBALANCEABLE, never absent. Rejected
STs are excluded from landing and counted as `filtered`.

Every X12 file also gets a FILE->RAW hop (ENG-W1-2): src_in = ST count in the envelope
(or None when the file cannot be tokenized), tgt_out = STs that produced bronze rows plus STs
legitimately excluded. A garbled file therefore fails the batch instead of vanishing.

Part E §3.2 (I2): a delivered file of an EXPECTED feed (feeds manifest `cadence: {expected: true}`) with zero
detail rows records a NON-gating ABC row (FILE->RAW expect_nonzero, FAIL) and opens a feed-level item
(dq_ctl.feed_item_open, kind EMPTY_DELIVERY) for a person to acknowledge. It never raises the batch floor. A feed
marked `empty_ok` (834 deltas) opens nothing. Defaults when a feed declares no cadence: IE and 837 feeds expected,
834 empty_ok.
"""
from __future__ import annotations

import csv
import io
import json
import uuid
from datetime import date
from decimal import Decimal
from pathlib import Path

from dqagent.ingest import mappers
from dqagent.ingest.x12 import Finding, tokenize

BRONZE_COLUMNS = {
    "bronze.ie_segment": mappers.IE_FIELDS,
    "bronze.enrollment_834": ["member_id", "last_name", "first_name", "dob", "sex", "maint_code", "plan_id",
                              "coverage_start", "coverage_end", "bgn08", "ins03", "isa13", "gs06", "st02"],
    "bronze.encounter_header": ["encounter_id", "submitter_id", "billing_npi", "billing_alt_id", "member_id", "freq_code",
                                "orig_encounter_id", "chain_root", "total_charge", "mco_paid", "pos", "dx1",
                                "isa13", "gs06", "st02"],
    "bronze.encounter_line": ["encounter_id", "submitter_id", "isa13", "gs06", "st02", "line_no", "proc_code",
                              "line_charge", "units", "svc_date", "paid_amt", "adjud_date"],
    # K5 / F-13 (PFP P2-6): a delimited (CSV) source, added by configuration alongside the X12/flat formats.
    "bronze.provider_enrollment": ["provider_id", "npi", "legal_name", "provider_type", "enrollment_date",
                                   "effective_date", "status"],
}
TYPES = {"dob": "DATE", "eff_start": "DATE", "eff_end": "DATE", "recorded_at": "TIMESTAMP",
         "coverage_start": "DATE", "coverage_end": "DATE", "svc_date": "DATE", "total_charge": "DECIMAL(18,2)",
         "mco_paid": "DECIMAL(18,2)", "line_charge": "DECIMAL(18,2)", "units": "DECIMAL(10,2)", "line_no": "INTEGER",
         "paid_amt": "DECIMAL(18,2)", "adjud_date": "DATE", "enrollment_date": "DATE", "effective_date": "DATE"}
CANON = ["_dq_batch_id", "_dq_load_id", "_dq_file_id", "_dq_source_system", "_dq_source_record_key"]
FEED_OF_FORMAT = {"IE": "IE_EXTRACT", "IE_FLAT": "IE_EXTRACT", "DELIMITED": "PROVIDER_FILE"}  # format -> feeds key
FEED_AUTHORITY = "AUTH_INTAKE"                                       # a feed-level (pre-split) item


def feed_of(fmt: str) -> str:
    return FEED_OF_FORMAT.get(fmt, fmt)


def feed_policy(feeds: dict | None, feed: str) -> dict:
    """{expected: bool, empty_ok: bool, every: ISO duration | None} for one feed (manifest, else family default)."""
    f = ((feeds or {}).get("feeds") or {}).get(feed) or {}
    cad = f.get("cadence") or {}
    if "cadence" not in f and "empty_ok" not in f:          # family defaults (Part E §3.2)
        expected = feed.startswith(("IE_", "X12_837")) and f.get("delivered", True) is not False
        return {"expected": expected, "empty_ok": feed.startswith("X12_834"), "every": None}
    return {"expected": bool(cad.get("expected")) and f.get("delivered", True) is not False,
            "empty_ok": bool(f.get("empty_ok")), "every": cad.get("every")}


def ensure_tables(dp):
    dp.execute("CREATE TABLE IF NOT EXISTS raw.x12_segment(file_id VARCHAR, isa13 VARCHAR, gs06 VARCHAR, st02 VARCHAR,"
               " seg_idx INTEGER, seg_id VARCHAR, elements VARCHAR, hl_id VARCHAR, hl_parent VARCHAR, hl_level VARCHAR)")
    dp.execute("CREATE TABLE IF NOT EXISTS raw.flat_record(file_id VARCHAR, line_no INTEGER, record_type VARCHAR,"
               " fields VARCHAR)")
    dp.execute("CREATE TABLE IF NOT EXISTS raw.findings(file_id VARCHAR, check_id VARCHAR, severity VARCHAR,"
               " message VARCHAR, isa13 VARCHAR, gs06 VARCHAR, st02 VARCHAR)")
    for rel, cols in BRONZE_COLUMNS.items():
        tbl = rel.split(".")[1]
        defs = ", ".join(f"{c} {TYPES.get(c, 'VARCHAR')}" for c in cols)
        canon = ", ".join(f"{c} VARCHAR" for c in CANON)
        dp.execute(f"CREATE TABLE IF NOT EXISTS landing.{tbl}({defs}, {canon})")
        dp.execute(f"CREATE TABLE IF NOT EXISTS {rel}({defs}, {canon}, _dq_unit_key VARCHAR, _dq_row_seq BIGINT)")


class Intake:
    def __init__(self, cp, dp, pipeline_id: str, batch_id: str, *, feeds: dict | None = None, state: str | None = None):
        """feeds: the EFFECTIVE feeds manifest (FEEDS artifact; before genesis the file seed); state: the state code."""
        self.cp, self.dp, self.pipeline_id, self.batch_id = cp, dp, pipeline_id, batch_id
        self.feeds, self.state = feeds, state
        self.abc: list[dict] = []
        self.feed_items: list[int] = []
        ensure_tables(dp)

    def _expect_nonzero(self, fmt: str, file_id: str, detail_rows: int, partner: str):
        """Part E §3.2: an expected feed's delivered file with zero detail rows -> non-gating ABC row + feed item."""
        feed = feed_of(fmt)
        pol = feed_policy(self.feeds, feed)
        if detail_rows != 0 or not pol["expected"] or pol["empty_ok"] or not self.state:
            return
        self.abc.append({"hop": "FILE->RAW", "measure": "expect_nonzero", "src_in": 0, "tgt_out": 0,
                         "expect_nonzero": True, "gating": False, "submitter_id": partner,
                         "authority": FEED_AUTHORITY})
        with self.cp.as_role("dq_seal_svc") as c:
            self.feed_items.append(c.execute(
                "SELECT dq_ctl.feed_item_open(%s,%s,'EMPTY_DELIVERY',%s,%s,%s,NULL) AS i",
                (self.state, feed, self.batch_id, file_id, FEED_AUTHORITY)).fetchone()["i"])

    # -- CP registrations ----------------------------------------------------------------
    def _register_file(self, file_id, sha, partner, direction, fmt, keys, translator=None) -> list[Finding]:
        """Register the file. Return duplicate-interchange findings (sender, receiver, ISA13)."""
        findings = []
        with self.cp.as_role("dq_seal_svc") as c:
            same = c.execute("SELECT file_id FROM dq_ctl.files WHERE sha256=%s AND partner=%s LIMIT 1", (sha, partner)).fetchone()
            if same and fmt == "IE_FLAT":
                findings.append(Finding("FLAT-DUP-FILE", "FAIL", f"identical file already received as {same['file_id']}",
                                        {"file_id": file_id}))
            for k in keys:
                dup = c.execute(
                    "SELECT file_id FROM dq_ctl.files, jsonb_array_elements(interchange_keys) ik "
                    "WHERE ik->>'sender' = %s AND ik->>'receiver' = %s AND ik->>'isa13' = %s LIMIT 1",
                    (k["sender"], k["receiver"], k["isa13"])).fetchone()
                if dup:
                    findings.append(Finding("ENV-DUP-INTERCHANGE", "FAIL",
                                            f"interchange {k['isa13']} from {k['sender']} already received in {dup['file_id']}",
                                            {"isa13": k["isa13"]}))
            c.execute("INSERT INTO dq_ctl.files(file_id,sha256,partner,direction,format,batch_id,interchange_keys,"
                      "translator_status) VALUES (%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb)",
                      (file_id, sha, partner, direction, fmt, self.batch_id, json.dumps(keys),
                       json.dumps(translator) if translator is not None else None))
        return findings

    def _register_load(self, relation: str) -> str:
        load_id = "L" + uuid.uuid4().hex[:12]
        with self.cp.as_role("dq_seal_svc") as c:
            c.execute("INSERT INTO dq_ctl.loads_events(load_id,pipeline_id,relation,batch_id,event) "
                      "VALUES (%s,%s,%s,%s,'REGISTERED')", (load_id, self.pipeline_id, relation, self.batch_id))
        return load_id

    def _commit_load(self, load_id: str, relation: str):
        with self.cp.as_role("dq_seal_svc") as c:
            c.execute("INSERT INTO dq_ctl.loads_events(load_id,pipeline_id,relation,batch_id,event) "
                      "VALUES (%s,%s,%s,%s,'COMMITTED')", (load_id, self.pipeline_id, relation, self.batch_id))

    # -- landing writes (pipeline principal) ------------------------------------------
    def _land(self, relation: str, rows: list[dict], file_id: str, source_system: str):
        mapped = [r for r in rows if r.get("_map_status", "MAPPED") == "MAPPED"]
        if not mapped:
            return None, 0
        load_id = self._register_load(relation)
        cols = BRONZE_COLUMNS[relation]
        tbl = relation.split(".")[1]
        data = [[r.get(c) for c in cols] + [self.batch_id, load_id, file_id, source_system, r["_source_key"]]
                for r in mapped]
        ph = ", ".join(["?"] * (len(cols) + len(CANON)))
        self.dp.cur().executemany(f"INSERT INTO landing.{tbl} VALUES ({ph})", data)
        self._commit_load(load_id, relation)
        return load_id, len(mapped)

    def _raw_findings(self, file_id, findings):
        for f in findings:
            self.dp.execute("INSERT INTO raw.findings VALUES (?,?,?,?,?,?,?)",
                            [file_id, f.check_id, f.severity, f.message, f.locator.get("isa13"), f.locator.get("gs06"),
                             f.locator.get("st02")])

    # -- formats ------------------------------------------------------------------------
    def ie_file(self, path: Path, partner="ZZ-IE"):
        data = path.read_bytes()
        file_id = "F" + uuid.uuid4().hex[:12]
        raw, rows, findings, ctl = mappers.parse_ie(data, file_id)
        hdr = (ctl.get("header") or {}).get("fields") or []
        seq_key = [{"sender": hdr[0], "receiver": "IE_EXTRACT", "isa13": f"SEQ:{hdr[2]}"}] if len(hdr) > 2 else []
        if not seq_key:
            findings.append(Finding("FLAT-TRL-MISSING", "FAIL", "IE header (HDR) missing or malformed", {"file_id": file_id}))
        reg = self._register_file(file_id, mappers_sha(data), partner, "INBOUND_POST_TRANSLATOR", "IE_FLAT", seq_key)
        findings += [Finding("FLAT-DUP-FILE", f.severity, f.message, {"file_id": file_id}) if f.check_id == "ENV-DUP-INTERCHANGE"
                     else f for f in reg]
        for r in raw:
            self.dp.execute("INSERT INTO raw.flat_record VALUES (?,?,?,?)",
                            [file_id, r["line_no"], r["record_type"], json.dumps(r["fields"])])
        self._raw_findings(file_id, findings)
        _, n = self._land("bronze.ie_segment", rows, file_id, partner)
        rejected = sum(1 for r in rows if r.get("_map_status") == "REJECTED")
        self.abc.append({"hop": "FILE->RAW", "measure": "detail_records", "src_in": ctl["trailer_count"],
                         "tgt_out": ctl["detail_count"], "submitter_id": partner, "authority": "AUTH_INTAKE"})
        self.abc.append({"hop": "RAW->LANDING", "measure": "records", "src_in": ctl["detail_count"],
                         "tgt_out": n, "rejected": rejected, "submitter_id": partner, "authority": "AUTH_INTAKE"})
        self._expect_nonzero("IE", file_id, ctl["detail_count"], partner)
        return file_id

    def delimited_file(self, path: Path, partner="ZZ-PROVIDER"):
        """A delimited (CSV, header row = column names) source added by configuration (K5 / F-13, PFP P2-6): the
        format `delimited_file` handles is driven entirely by `routing.yaml`'s `format: DELIMITED` / `parser:
        {type: delimited, ...}`, never by a code change. Same obligations as `ie_file`/`x12_file` (red team Fix 2):
        a FILE->RAW and a RAW->LANDING `self.abc` row in the same shape, and `_expect_nonzero`, not just `_land`."""
        partner = partner or "ZZ-PROVIDER"
        data = path.read_bytes()
        file_id = "F" + uuid.uuid4().hex[:12]
        records = list(csv.DictReader(io.StringIO(data.decode("utf-8-sig"))))
        # one pseudo-interchange key per file (no ISA/GS/ST envelope on a flat CSV): duplicate-file detection by
        # file_id keeps every file its own key, same as ie_file's HDR-trailer-derived seq_key.
        seq_key = [{"sender": partner, "receiver": "PROVIDER_FILE", "isa13": f"SEQ:{file_id}"}]
        findings = self._register_file(file_id, mappers_sha(data), partner, "INBOUND_POST_TRANSLATOR", "DELIMITED",
                                       seq_key)
        cols = BRONZE_COLUMNS["bronze.provider_enrollment"]
        rows = []
        for i, rec in enumerate(records):
            self.dp.execute("INSERT INTO raw.flat_record VALUES (?,?,?,?)",
                            [file_id, i, "PROVIDER_ENROLLMENT", json.dumps(rec)])
            row = {c: rec.get(c) for c in cols}
            row["_source_key"] = f"{file_id}:{i}"
            bad_date = False
            for dc in ("enrollment_date", "effective_date"):      # YYYYMMDD -> date; the DP's DATE columns need
                v, row[dc] = row.get(dc), None                    # a real date, never a bare digit string (ENG-W1-1
                if v:                                              # style: unparseable, not silently null)
                    try:
                        row[dc] = date(int(v[:4]), int(v[4:6]), int(v[6:8]))
                    except (ValueError, IndexError):
                        bad_date = True
            if row.get("provider_id") and row.get("npi") and not bad_date:
                row["_map_status"] = "MAPPED"
            else:
                row["_map_status"] = "REJECTED"
                row["_map_reason"] = "missing provider_id/npi, or an unparseable date"
            rows.append(row)
        self._raw_findings(file_id, findings)
        _, n = self._land("bronze.provider_enrollment", rows, file_id, partner)
        rejected = sum(1 for r in rows if r["_map_status"] == "REJECTED")
        self.abc.append({"hop": "FILE->RAW", "measure": "detail_records", "src_in": len(records),
                         "tgt_out": len(rows), "submitter_id": partner, "authority": "AUTH_INTAKE"})
        self.abc.append({"hop": "RAW->LANDING", "measure": "records", "src_in": len(rows),
                         "tgt_out": n, "rejected": rejected, "submitter_id": partner, "authority": "AUTH_INTAKE"})
        self._expect_nonzero("DELIMITED", file_id, len(records), partner)
        return file_id

    def x12_file(self, path: Path, partner: str, kind: str, ack_path: Path | None = None):
        data = path.read_bytes()
        file_id = "F" + uuid.uuid4().hex[:12]
        pf = tokenize(data, file_id)
        keys = [{"sender": i["sender"], "receiver": i["receiver"], "isa13": i["isa13"]} for i in pf.interchanges]
        acks = _read_acks(ack_path) if ack_path else None
        pf.findings += self._register_file(file_id, pf.sha256, partner, "INBOUND_PRE_TRANSLATOR", kind, keys,
                                           translator=acks)
        for s in pf.segments:
            self.dp.execute("INSERT INTO raw.x12_segment VALUES (?,?,?,?,?,?,?,?,?,?)",
                            [file_id, s.isa13, s.gs06, s.st02, s.idx, s.seg_id, json.dumps(s.elements),
                             s.hl_id, s.hl_parent, s.hl_level])
        parsed_st = {(i["isa13"], g["gs06"], t["st02"]) for i in pf.interchanges for g in i["groups"]
                     for t in g["transactions"]}
        st_total = sum(len(g["transactions"]) for i in pf.interchanges for g in i["groups"])
        tokenizable = not any(f.check_id == "ENV-STRUCT" and "cannot be tokenized" in f.message for f in pf.findings)
        rejected_st: set = set()
        if acks is None:
            self.abc.append({"hop": "TRANSLATOR", "measure": "transactions", "src_in": None, "tgt_out": None,
                             "submitter_id": partner, "authority": "AUTH_MEDICAID", "x_reason": "no ack file"})
        else:
            ack_keys = {(a["isa13"], a.get("gs06"), a["st02"]): a["status"] for a in acks}
            rejected_st = {k for k, st in ack_keys.items() if st == "R"}
            matched = parsed_st & set(ack_keys)
            self.abc.append({"hop": "TRANSLATOR", "measure": "transactions", "src_in": st_total,
                             "tgt_out": sum(1 for k in matched if ack_keys[k] == "A"),
                             "rejected": sum(1 for k in matched if ack_keys[k] == "R"),
                             "submitter_id": partner, "authority": "AUTH_MEDICAID",
                             "x_unmatched_acks": len(set(ack_keys) - parsed_st)})
            if set(ack_keys) - parsed_st:          # acks for STs not in this file: residual, never balance
                self.abc[-1]["src_in"] = st_total + len(set(ack_keys) - parsed_st)
        produced: set = set()
        detail_rows = None
        if kind == "X12_837P":
            headers, lines, fx = mappers.map_837p(pf, partner)
            pf.findings += fx
            _chain_roots(headers)
            tx = lambda r: (r["isa13"], r["gs06"], r["st02"])  # noqa: E731
            keep = [h for h in headers if tx(h) not in rejected_st]
            landed = {(*tx(h), h["encounter_id"]) for h in keep if h.get("_map_status") == "MAPPED"}
            filtered = len(headers) - len(keep)
            _, nh = self._land("bronze.encounter_header", keep, file_id, partner)
            # lines travel with their header: a line whose header was filtered or rejected is not landed
            self._land("bronze.encounter_line", [ln for ln in lines if (*tx(ln), ln["encounter_id"]) in landed],
                       file_id, partner)
            produced = {tx(h) for h in keep if h.get("_map_status") == "MAPPED"}
            clm_raw = sum(1 for s in pf.segments if s.seg_id == "CLM")
            detail_rows = clm_raw
            self.abc.append({"hop": "RAW->LANDING", "measure": "claims", "src_in": clm_raw, "tgt_out": nh,
                             "filtered": filtered,
                             "rejected": sum(1 for h in keep if h.get("_map_status") == "REJECTED"),
                             "submitter_id": partner, "authority": "AUTH_MEDICAID"})
            # amounts in exact Decimal arithmetic: a float residual of 1e-13 is not a control break
            amt_in = sum((h["total_charge"] or Decimal(0)) for h in keep if h.get("_map_status") == "MAPPED")
            self.abc.append({"hop": "RAW->LANDING", "measure": "clm02_amount",
                             "src_in": sum((h["total_charge"] or Decimal(0)) for h in headers),
                             "tgt_out": amt_in,
                             "filtered": sum((h["total_charge"] or Decimal(0)) for h in headers if tx(h) in rejected_st),
                             "rejected": sum((h["total_charge"] or Decimal(0)) for h in keep if h.get("_map_status") == "REJECTED"),
                             "submitter_id": partner, "authority": "AUTH_MEDICAID"})
        elif kind == "X12_834":
            rows, fx = mappers.map_834(pf)
            pf.findings += fx
            keep = [r for r in rows if (r["isa13"], r["gs06"], r["st02"]) not in rejected_st]
            _, n = self._land("bronze.enrollment_834", keep, file_id, partner)
            produced = {(r["isa13"], r["gs06"], r["st02"]) for r in keep if r.get("_map_status") == "MAPPED"}
            ins_raw = sum(1 for s in pf.segments if s.seg_id == "INS")
            detail_rows = ins_raw
            self.abc.append({"hop": "RAW->LANDING", "measure": "members", "src_in": ins_raw, "tgt_out": n,
                             "filtered": len(rows) - len(keep),
                             "rejected": sum(1 for r in keep if r.get("_map_status") == "REJECTED"),
                             "submitter_id": partner, "authority": "AUTH_MEDICAID"})
        # FILE->RAW: every ST in the envelope either produced rows, was rejected by the translator, or is residual
        self.abc.append({"hop": "FILE->RAW", "measure": f"transactions:{file_id}",
                         "src_in": st_total if tokenizable and st_total > 0 else None,
                         "tgt_out": len(produced & parsed_st),
                         "filtered": len(rejected_st & parsed_st - produced),
                         "submitter_id": partner, "authority": "AUTH_INTAKE"})
        self._raw_findings(file_id, pf.findings)
        if detail_rows is not None and tokenizable:
            self._expect_nonzero(kind, file_id, detail_rows, partner)
        return file_id, pf


def _chain_roots(headers: list[dict]) -> None:
    """chain_root = the ORIGINAL encounter of a replacement/void chain, resolved transitively over
    the headers of the same submitter in this file (ENG-W1-4). A reference to an encounter not in
    this file keeps that reference as the root (cross-batch resolution against released history: W2)."""
    by_id = {(h["submitter_id"], h["encounter_id"]): h for h in headers if h.get("encounter_id")}
    for h in headers:
        root, seen = h.get("orig_encounter_id") or h.get("encounter_id"), set()
        while (h["submitter_id"], root) in by_id and root not in seen:
            seen.add(root)
            nxt = by_id[(h["submitter_id"], root)].get("orig_encounter_id")
            if not nxt:
                break
            root = nxt
        h["chain_root"] = root


def mappers_sha(data: bytes) -> str:
    import hashlib
    return hashlib.sha256(data).hexdigest()


def _read_acks(path: Path):
    return [dict(r) for r in csv.DictReader(io.StringIO(path.read_text()))]
