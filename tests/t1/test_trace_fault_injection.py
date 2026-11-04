"""Part F T1 red team C1: the trace store going away (dq_trace_svc cannot log in: a connection limit, a rotated password)
never changes or blocks what tracing observes. The same scenario - w1 nights, a decision, a veto and a recovery - runs
once traced and once with every dq_trace_svc login failing; the outcomes are identical, the decision / veto / recovery
calls still succeed, and the degradation is counted and logged (I-8 on the decision and veto paths, not only w1)."""
import contextlib
import json
import logging
import os
import uuid
from pathlib import Path

import psycopg
import pytest
from fastapi.testclient import TestClient

import dqagent.runtime as runtime_mod
from dqagent.api.app import create_app
from dqagent.cp import schema
from dqagent.engine.cp import CP
from dqagent.obs import tracer
from dqagent.runtime import seed_routing, synthetic_nights

from .fixtures_w1 import ie_file, person
from .test_trace_shape import _outcome, _q
from .w1_harness import Slice

PG = dict(host=os.environ.get("DQ_PG_HOST", "/tmp"), port=int(os.environ.get("DQ_PG_PORT", "55432")),
          user=os.environ.get("DQ_PG_USER", "dq_gov"))
W1 = Path(__file__).resolve().parents[2] / "synthetic" / "out" / "w1"
AGENT = "dq-reconciliation"


@contextlib.contextmanager
def _cp(tmp):
    tmp.mkdir(parents=True, exist_ok=True)
    admin = psycopg.connect(dbname="postgres", autocommit=True, **PG)
    db = "dqcp_fi_" + uuid.uuid4().hex[:8]
    admin.execute(f"CREATE DATABASE {db}")
    dsn = dict(dbname=db, **PG)
    with psycopg.connect(**dsn) as c:
        schema.apply(c)
    try:
        yield dsn, Slice(dsn, dp_path=str(tmp / f"{db}.duckdb"))
    finally:
        admin.execute(f"DROP DATABASE {db} WITH (FORCE)")
        admin.close()


def _norm(outcome) -> str:
    """Random surrogates (change-request / decision ids, minted per run) and the board hash chain over them differ
    between any two runs; everything else must be identical."""
    import json
    import re
    o = dict(outcome)
    o["board"] = [b[:-1] for b in o["board"]]                     # entry_hash chains over the random cr ids
    t = json.dumps(o, sort_keys=True, default=str)
    t = re.sub(r"(CR|DEM)-[0-9a-f]{6,}", r"\1-*", t)
    return re.sub(r"\bD[0-9a-f]{12}\b", "D*", t)


def H(rt, user, **kw):
    return {"Authorization": f"Bearer {rt.idp.token(user, **kw)}"}


def _scenario(rt, tmp, monkeypatch) -> dict:
    out = {}
    api = TestClient(create_app(rt))
    # a veto first, on a fresh plane: L4 -> L3 (lower-only), a clean batch opens veto windows, a person vetoes one
    tiers = rt.effective_item("AUTONOMY", f"autonomy:{AGENT}:RELEASE")
    tiers["content"]["AUTH_MEDICAID"] = "L3"
    rt.govern([tiers])
    p = tmp / "veto.txt"
    # explicit extract seqs: the fixture's counter is process-global, so both runs must name the same extracts
    p.write_bytes(ie_file([person("PV1", "M8001"), person("PV2", "M8002")], seq=900001))
    rt.run_pipeline("p4_eligibility_ie", "B-VETO-1", [("IE", p)], ["bronze.ie_segment"])
    st = {u["unit_key"]: u["state"] for u in rt.units(batch_id="B-VETO-1")}
    vu = sorted(k for k, v in st.items() if v == "EVALUATING")[0]
    vcard = api.get(f"/api/v1/units/{vu}", headers=H(rt, "maker")).json()
    v = api.post(f"/api/v1/units/{vu}/veto", headers=H(rt, "maker"), json={
        "reason_code": "DATA_SUSPECT", "explanation_ref": {k: vcard["explanation"][k] for k in
                                                           ("facts_hash", "text_hash")}})
    out["veto"] = (v.status_code, v.json().get("state"))
    for night, files in synthetic_nights(W1, seed_routing("ZZ"))[:3]:                # w1 nights
        rt.run_night(W1, night, files, last=False)
    # a decision (maker) on a held unit
    uk = sorted(u["unit_key"] for u in rt.units(state="HELD") if not u["unit_key"].startswith("B-VETO"))[0]
    card = api.get(f"/api/v1/units/{uk}", headers=H(rt, "maker")).json()
    chal = api.post(f"/api/v1/units/{uk}/challenge", json={"decision": "RELEASE"}, headers=H(rt, "maker")).json()
    d = api.post(f"/api/v1/units/{uk}/decide", headers=H(rt, "maker"), json={
        "decision": "RELEASE", "rationale": "Reviewed the evidence on the card for this test.",
        "expected_state_version": card["state_version"], "bound_fingerprint": card["fingerprint"],
        "step_up_token": rt.idp.token("maker", step_up=True, challenge=chal["challenge"])})
    out["decide"] = (d.status_code, d.json()["status"])
    # a recovery of a batch sealed without its ABC
    real = runtime_mod.landing_to_gated_abc
    calls = {"n": 0}

    def boom(*a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("simulated crash between seal and ABC")
        return real(*a, **k)
    monkeypatch.setattr(runtime_mod, "landing_to_gated_abc", boom)
    p = tmp / "rcv.txt"
    p.write_bytes(ie_file([person("PR1", "M7001")], seq=900002))
    with pytest.raises(RuntimeError):
        rt.run_pipeline("p4_eligibility_ie", "B-RCV-1", [("IE", p)], ["bronze.ie_segment"])
    out["recover"] = rt.recover_batch_abc("B-RCV-1", "E100", reason="operator recovery after a crash")
    monkeypatch.setattr(runtime_mod, "landing_to_gated_abc", real)
    rt.verify_and_publish()
    return out


def test_trace_store_down_changes_no_outcome_and_blocks_nothing(tmp_path, monkeypatch, caplog):
    results = {}
    with _cp(tmp_path / "on") as (dsn, rt):
        results["on"] = (_scenario(rt, tmp_path / "on", monkeypatch), _outcome(dsn))
        assert _q(dsn, "SELECT count(*) FROM dq_ctl.runs WHERE kind IN ('NIGHT','DECISION','RECOVER')")[0][0] >= 5
    real_connect = CP.connect_as

    def no_trace_login(self, role, trace=True):
        if role == "dq_trace_svc":
            raise psycopg.OperationalError('FATAL: password authentication failed for user "dq_trace_svc"')
        return real_connect(self, role, trace=trace)
    before = tracer.STATS["degraded"]
    with _cp(tmp_path / "off") as (dsn, rt):
        monkeypatch.setattr(CP, "connect_as", no_trace_login)
        with caplog.at_level(logging.WARNING, logger="dqagent.tracer"):
            results["down"] = (_scenario(rt, tmp_path / "off", monkeypatch), _outcome(dsn))
        monkeypatch.setattr(CP, "connect_as", real_connect)
        assert _q(dsn, "SELECT count(*) FROM dq_ctl.runs")[0][0] == 0                  # nothing was traced ...
    assert tracer.STATS["degraded"] > before                                          # ... and that was recorded
    assert any("tracing degraded" in r.getMessage() for r in caplog.records)
    on, down = results["on"], results["down"]
    assert on[0]["decide"] == down[0]["decide"] and on[0]["decide"][0] == 200
    assert on[0]["veto"] == down[0]["veto"] == (200, "HELD")
    assert on[0]["recover"] == down[0]["recover"] and on[0]["recover"]["abc_rows"] == 1
    a, b = json.loads(_norm(on[1])), json.loads(_norm(down[1]))                       # identical outcomes
    for k in a:                                     # per table, with the first differing row (no giant diff)
        diff = next(((x, y) for x, y in zip(a[k], b[k]) if x != y), None)
        assert len(a[k]) == len(b[k]) and diff is None, (k, len(a[k]), len(b[k]), json.dumps(diff)[:2000])


def test_explainer_with_the_trace_store_down_renders_degraded_and_fails_closed_on_reject(fresh_cp_dsn, monkeypatch):
    """C1, the explainer side (Backend-B's hook): with every dq_trace_svc login failing, a template render still returns
    its text and reports what it could not do; a REJECT, whose CHECK_REJECTED guardrail must be written, fails closed
    (ExplainUnavailable, which the api maps to 503)."""
    from dqagent.llm.transports import ScriptedTransport
    from dqagent.obs import explain_service as ES

    from .test_obs_explain import RULE, SCRIPTED, SUBJ, A
    cp = CP(fresh_cp_dsn)
    real_connect = CP.connect_as

    def no_trace_login(self, role, trace=True):
        if role == "dq_trace_svc":
            raise psycopg.OperationalError('FATAL: password authentication failed for user "dq_trace_svc"')
        return real_connect(self, role, trace=trace)
    monkeypatch.setattr(CP, "connect_as", no_trace_login)
    for k, v in list(ES.STATS.items()):
        monkeypatch.setitem(ES.STATS, k, v)
    good = ES.ExplainService(cp, cfg={"provider": "template"}, deployment="demo", state="STATE_A")
    e = good.explain("rule_disposition", RULE, subject=SUBJ, authorities=A)
    assert e["text"] and e["degraded"] == ["cache_read", "id_mint", "write"]
    bad = ES.ExplainService(cp, cfg=SCRIPTED, deployment="demo", state="STATE_A",
                            transport=ScriptedTransport(bad_every=0, bad_subjects=[SUBJ], mutator="CAUSAL"))
    with pytest.raises(ES.ExplainUnavailable):
        bad.explain("rule_disposition", RULE, subject=SUBJ, authorities=A)
