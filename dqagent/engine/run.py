"""Thin orchestration helpers used by the harness runner, the CLI and the tests."""
from __future__ import annotations

from dqagent.model.outcome import Disposition as D


def record_abc(cp, batch_id: str, rows: list[dict]) -> list[dict]:
    """ABC: residual = src_in - (tgt_out + rejected + quarantined + deduplicated + filtered + held).
    If src_in is unknown the hop is UNBALANCEABLE, never PASS. On a RAW->LANDING hop, records the
    mapper REJECTED (e.g. an unparseable date) balance the count but are still data loss: the hop
    FAILs, so the batch is held instead of the record disappearing silently (ENG-W1-1).

    Part E §3.1/§3.2: `gating` (default True) is stored on the row; a non-gating row (an empty delivery, a parallel
    run) is recorded but never raises the batch floor (dq_ctl.abc_gating_failures is the one definition).
    `expect_nonzero`: the hop FAILs when src_in is 0 (an expected feed delivered a file with no detail rows)."""
    out = []
    with cp.as_role("dq_seal_svc") as c:
        for r in rows:
            terms = [r.get(k) or 0 for k in ("tgt_out", "rejected", "quarantined", "deduplicated", "filtered", "held")]
            if r.get("src_in") is None:
                residual, disp = None, D.UNBALANCEABLE
            else:
                residual = r["src_in"] - sum(terms)
                lossy = r["hop"] == "RAW->LANDING" and (r.get("rejected") or 0) > 0
                empty = bool(r.get("expect_nonzero")) and r["src_in"] == 0
                disp = D.PASS if residual == 0 and not lossy and not empty else D.FAIL
            gating = bool(r.get("gating", True))
            c.execute("INSERT INTO dq_ctl.abc(batch_id,authority,hop,measure,program_code,submitter_id,src_in,tgt_out,"
                      "rejected,quarantined,deduplicated,filtered,held,residual,disposition,gating) "
                      "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                      (batch_id, r.get("authority", "AUTH_MEDICAID"), r["hop"], r["measure"], r.get("program"),
                       r.get("submitter_id"), r.get("src_in"), r.get("tgt_out"), r.get("rejected"),
                       r.get("quarantined"), r.get("deduplicated"), r.get("filtered"), r.get("held"), residual,
                       disp.value, gating))
            out.append({**r, "residual": residual, "disposition": disp.value, "gating": gating})
    return out


def landing_to_gated_abc(cp, dp, batch_id: str, relations: list[str]) -> tuple[list[dict], list[str]]:
    """LANDING->GATED balance over the FROZEN load set only. Landing rows of the same batch
    whose load was registered or committed after the freeze are returned as late loads.
    They are an ERROR alert and are carried into a new batch; they are never silently dropped."""
    rows, late = [], []
    with cp.as_role("dq_seal_svc") as c:
        frozen = {r["load_id"] for r in c.execute(
            "SELECT load_id FROM dq_ctl.loads_events WHERE batch_id=%s AND event='FROZEN'", (batch_id,)).fetchall()}
    for rel in relations:
        tbl = rel.split(".")[1]
        by_load = dp.fetchall(f"SELECT _dq_load_id, count(*) FROM landing.{tbl} WHERE _dq_batch_id = ? GROUP BY 1",
                              [batch_id])
        lan = sum(n for ld, n in by_load if ld in frozen)
        late += [ld for ld, _ in by_load if ld not in frozen]
        gat = dp.fetchall(f"SELECT count(*) FROM {rel} WHERE _dq_batch_id = ?", [batch_id])[0][0]
        rows.append({"hop": "LANDING->GATED", "measure": f"rows:{tbl}", "src_in": lan, "tgt_out": gat})
    return rows, late
