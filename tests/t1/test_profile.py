"""PFP P1-2 (:697-731): per-DAG agent profile (read-only). The four PFP tests plus a scope test (D7).

Demo moment: "This night ran under this exact configuration. Here is who approved each part, and what is not
governed." Exercised over the REAL app (create_app) and the real control plane, the house pattern of
tests/t1/test_obs_api.py and tests/t1/test_governance_api.py.
"""
from pathlib import Path

from fastapi.testclient import TestClient

from dqagent.api.app import create_app
from dqagent.governance.profile import compose_profile
from dqagent.runtime import seed_routing, synthetic_nights

from .w1_harness import Slice

W1 = Path(__file__).resolve().parents[2] / "synthetic" / "out" / "w1"


def H(rt, user, **kw):
    return {"Authorization": f"Bearer {rt.idp.token(user, **kw)}"}


def test_profile_digest_on_every_run_root(fresh_cp_dsn, tmp_path):
    """The profile page's own digest equals the night root's profile_digests[pipeline_id] (the central correctness
    property, K1_DESIGN.md §4 step 4): both must be the SAME function, compose_profile, called at both sites over
    the same effective configuration. Backend-Engine's step (populating runs.profile_digests at the NIGHT root,
    runtime.py:1552-1553) is out of THIS seat's scope (D6); this test asserts the half that IS in scope: calling
    compose_profile twice against an unchanged control plane returns identical dicts, byte for byte, which is the
    property Backend-Engine's stamp depends on."""
    rt = Slice(fresh_cp_dsn)
    a = compose_profile(rt.registry, rt.state, "p6_claims")
    b = compose_profile(rt.registry, rt.state, "p6_claims")
    assert a == b and a["profile_digest"] == b["profile_digest"]
    # order independence: composing a different pipeline first does not change this pipeline's own digest
    compose_profile(rt.registry, rt.state, "p4_eligibility_ie")
    c = compose_profile(rt.registry, rt.state, "p6_claims")
    assert c["profile_digest"] == a["profile_digest"]


def test_night_root_profile_digest_matches_the_route(fresh_cp_dsn):
    """Closes the K1 step-2 coverage gap (K1_DESIGN.md §4 step 4): a NIGHT root's stamped
    profile_digests[pipeline_id] (runtime.py's _night_profile_digests, D6) equals compose_profile(...)
    ["profile_digest"] for the SAME pipeline and the SAME effective configuration at that moment -- the central
    correctness property this whole package exists to deliver."""
    rt = Slice(fresh_cp_dsn)
    nights = synthetic_nights(W1, seed_routing("ZZ"))
    night, files = next((n, fs) for n, fs in nights if any(f.name.startswith("837P") for f in fs))
    rt.run_night(W1, night, files, last=False)
    with rt.cp.as_role("dq_auditor") as c:
        row = c.execute("SELECT profile_digests FROM dq_ctl.runs WHERE kind='NIGHT'").fetchone()
    stamped = row["profile_digests"]
    assert stamped.get("p6_claims"), stamped          # the night's stamp actually has an entry
    live = compose_profile(rt.registry, rt.state, "p6_claims")
    assert stamped["p6_claims"] == live["profile_digest"]


def test_monitor_marked_not_governed(fresh_cp_dsn):
    """L-10: the monitor spec / bindings are sourced from files, not governed; the profile's monitors chip carries
    that label literally (BACKLOG.md:120), not a comparison against anything external."""
    rt = Slice(fresh_cp_dsn)
    prof = compose_profile(rt.registry, rt.state, "p6_claims")
    assert prof["monitors"]["governed"] is False and prof["monitors"]["provenance"] == "FILE"
    assert prof["monitors"]["content"]       # the seeded MONITOR_DEFAULTS, confirmed real (registry.py:42-43)


def test_profile_as_of_old_trace_matches_config_then(fresh_cp_dsn, tmp_path):
    """D9: as_of=<trace_id> resolves to the config_artifacts cutoff in force when that trace's root opened, not to
    "now". A change request raised AFTER the captured trace must be invisible to the as_of snapshot, and visible
    to the as_of=None (now) snapshot."""
    from .fixtures_w1 import ie_file, person

    rt = Slice(fresh_cp_dsn)
    p = tmp_path / "ie.txt"
    p.write_bytes(ie_file([person("P1", "M0001")]))
    rt.run_pipeline("p4_eligibility_ie", "B-ELG-1", [("IE", p)], ["bronze.ie_segment"])
    # No root is minted by run_pipeline itself (it runs under whatever root the caller opened, or none); mint an
    # APPLY_CHANGE root explicitly so this test has a real, ratified trace stamped with the CURRENT config_digest.
    from dqagent.obs import tracer
    tid = tracer.mint(rt.cp, "APC-T1PROFILE")
    with tracer.root("APPLY_CHANGE", tid, parent_trace_id=None, **rt._root_args()):
        pass
    before = compose_profile(rt.registry, rt.state, "p6_claims", as_of=tid)
    item = {"artifact_id": "ZZ:overlay:R-ENC-001", "kind": "OVERLAY", "op": "PUT",
            "content": {"verb": "raise_priority", "priority": "Critical", "reason": "state priority",
                        "approved_ref": "SCR-7"}}
    rt.govern([item])
    after_asof = compose_profile(rt.registry, rt.state, "p6_claims", as_of=tid)
    after_now = compose_profile(rt.registry, rt.state, "p6_claims", as_of=None)
    assert after_asof["profile_digest"] == before["profile_digest"]      # the past is unchanged by a later CR
    assert after_now["profile_digest"] != before["profile_digest"]       # "now" sees the new overlay
    # ruleset() keys by entity, not relation (ZZ/elements.yaml:4: ENCOUNTER_HEADER -> bronze.encounter_header)
    assert after_now["rules"]["ZZ:R-ENC-001@ENCOUNTER_HEADER"]["gating"] in ("gate", "advisory")
    r1 = after_now["pipeline"]["id"], before["pipeline"]["id"]
    assert r1 == ("p6_claims", "p6_claims")


def test_profile_as_of_unknown_trace_404(fresh_cp_dsn):
    """Guard fails closed: an as_of naming a trace id this control plane never minted is 404 over the real route,
    not a silent 'now' (compose_profile raises KeyError; catalog_routes.py turns it into 404)."""
    rt = Slice(fresh_cp_dsn)
    api = TestClient(create_app(rt))
    r = api.get("/api/v1/catalog/pipelines/p6_claims/profile", headers=H(rt, "maker"),
               params={"as_of": "NO-SUCH-TRACE-1"})
    assert r.status_code == 404


def test_profile_scoped(fresh_cp_dsn):
    """D7 (CRITICAL): the pipeline's authority set is the union of its gated relations' authorities_by_program
    (contracts_w1.yaml :13,27,37 — p6_claims's gated relations are Medicaid-only). A caller who holds that
    authority sees the real profile, with the pipeline's own tier map in it."""
    rt = Slice(fresh_cp_dsn)
    api = TestClient(create_app(rt))
    ok = api.get("/api/v1/catalog/pipelines/p6_claims/profile", headers=H(rt, "maker"))
    assert ok.status_code == 200, ok.text
    body = ok.json()
    assert body["pipeline"]["id"] == "p6_claims" and body["profile_digest"]
    assert set(body["tiers"]) == {"AUTH_MEDICAID"}


def test_profile_scoped_snap_reviewer_cannot_see_medicaid_rules(fresh_cp_dsn):
    """D7's negative control, per EXECUTION_PLAN.md:292: a SNAP-only reviewer requesting a Medicaid-ONLY pipeline's
    profile gets 403, not a redacted 200. p6_claims gates bronze.encounter_header / bronze.encounter_line, whose
    authorities_by_program is {MEDICAID: [AUTH_MEDICAID]} only (contracts_w1.yaml :37); "snap" (scope ==
    ["AUTH_SNAP"] only, runtime.py:71) does not intersect it. p4_eligibility_ie is the CONTROL on the same route:
    its bronze.ie_segment relation's authorities_by_program also includes SNAP (contracts_w1.yaml :13-14), so a
    SNAP reviewer legitimately sees it, with only Medicaid-and-the-pipeline's-other-authorities' rules visible."""
    rt = Slice(fresh_cp_dsn)
    api = TestClient(create_app(rt))
    r = api.get("/api/v1/catalog/pipelines/p6_claims/profile", headers=H(rt, "snap"))
    assert r.status_code == 403, r.text
    assert "profile_digest" not in r.text          # not a redacted 200 wearing a 403's clothes
    control = api.get("/api/v1/catalog/pipelines/p4_eligibility_ie/profile", headers=H(rt, "snap"))
    assert control.status_code == 200, control.text          # the authority check is real, not a blanket deny
    assert "AUTH_SNAP" in control.json()["tiers"]


def test_profile_unknown_pipeline_404(fresh_cp_dsn):
    rt = Slice(fresh_cp_dsn)
    api = TestClient(create_app(rt))
    r = api.get("/api/v1/catalog/pipelines/does-not-exist/profile", headers=H(rt, "maker"))
    assert r.status_code == 404


def test_profile_outsider_no_scope_403(fresh_cp_dsn):
    """Guard fails closed, degenerate input: a caller with no effective release duty at all (the existing
    scope(p) primitive, app.py:131-134) is refused before the pipeline's own authority set is even consulted."""
    rt = Slice(fresh_cp_dsn)
    api = TestClient(create_app(rt))
    r = api.get("/api/v1/catalog/pipelines/p6_claims/profile", headers=H(rt, "outsider"))
    assert r.status_code == 403


def test_night_profile_digests_distinguishes_touched_nothing_from_degraded(fresh_cp_dsn):
    """Red-team fix (K1, round 1): `_night_profile_digests` used to return `{}` both when a night genuinely touched
    zero pipelines and when it touched pipelines but every `compose_profile` call failed - indistinguishable from
    the caller's side. The richer shape makes the two cases provably different without touching a database."""
    rt = Slice(fresh_cp_dsn)
    # degenerate input: no files at all is a genuine "touched nothing", not a degrade
    nothing = rt._night_profile_digests(Path("."), [])
    assert nothing == {"digests": {}, "attempted": 0, "degraded_pipelines": [], "route_degraded": False}

    # a routing failure (before any pipeline id is even known) is reported, not silently swallowed into {}
    def _boom_plan(data, p):
        raise RuntimeError("synthetic routing failure")
    rt._file_plan = _boom_plan
    route_degraded = rt._night_profile_digests(Path("."), [Path("f1.txt")])
    assert route_degraded == {"digests": {}, "attempted": 0, "degraded_pipelines": [], "route_degraded": True}


def test_night_profile_digests_compose_failure_is_visible_and_queryable(fresh_cp_dsn, monkeypatch):
    """The compose_profile half: when routing resolves pipeline ids but every compose call fails, `digests` is
    still `{}` (I-8: the NIGHT root is stamped exactly as before), but `attempted` and `degraded_pipelines` now
    prove it was a degrade, not an empty night - and the fire-and-forget recorder writes that as span attributes
    any dq_api/dq_auditor reader of `dq_ctl.spans` can query (not merely an in-process STATS counter nothing
    outside this process ever reads)."""
    import dqagent.governance.profile as profile_mod
    from dqagent.obs import tracer

    rt = Slice(fresh_cp_dsn)

    def _boom_compose(registry, state, pipeline_id, **kw):
        raise RuntimeError("synthetic compose failure")
    monkeypatch.setattr(profile_mod, "compose_profile", _boom_compose)

    class _Plan:
        pipeline = "p6_claims"
    rt._file_plan = lambda data, p: _Plan()

    profile = rt._night_profile_digests(Path("."), [Path("f1.txt")])
    assert profile["digests"] == {}                   # I-8: still an empty stamp, the night's outcome is untouched
    assert profile["attempted"] == 1 and profile["degraded_pipelines"] == ["p6_claims"]
    assert profile["route_degraded"] is False          # routing itself succeeded; only compose failed

    tid = tracer.mint(rt.cp, "NIGHT-ZZ-TESTDEGRADE")
    with tracer.root("NIGHT", tid, night_date="2024-01-01", person_id=None, attempt=1,
                     profile_digests=profile["digests"], **rt._root_args()):
        rt._record_profile_digest_degradation(profile)

    with rt.cp.as_role("dq_auditor") as c:
        rows = c.execute("SELECT name, attrs FROM dq_ctl.spans WHERE trace_id=%s", (tid,)).fetchall()
    degrade_spans = [r for r in rows if r["name"] == "profile_digests"]
    assert len(degrade_spans) == 1, rows
    attrs = degrade_spans[0]["attrs"]
    assert attrs == {"route_degraded": False, "attempted": 1, "degraded": 1}


def test_night_profile_digests_degradation_recorder_is_fire_and_forget(fresh_cp_dsn, monkeypatch):
    """I-8: a failure while WRITING the degradation signal itself must never raise into the night. A span attribute
    the tracer itself would reject (here: patched to throw) degrades-and-continues exactly like every other
    tracer-side observation in this file, instead of blocking the run."""
    from dqagent.obs import tracer

    rt = Slice(fresh_cp_dsn)
    degraded_profile = {"digests": {}, "attempted": 1, "degraded_pipelines": ["p6_claims"], "route_degraded": False}

    def _boom_span(*a, **kw):
        raise RuntimeError("synthetic span failure")
    monkeypatch.setattr(tracer, "span", _boom_span)
    rt._record_profile_digest_degradation(degraded_profile)          # must not raise
    assert tracer.STATS["degraded"] >= 1
    assert "night_profile_digests:record" in (tracer.STATS["last_degraded"] or "")


def test_night_profile_digests_no_degrade_writes_no_span(fresh_cp_dsn):
    """Precision, not recall: the common case (nothing degraded) must not grow a span on every night - only an
    actual degrade is worth a reader's attention."""
    rt = Slice(fresh_cp_dsn)
    clean_profile = {"digests": {"p6_claims": "abc123"}, "attempted": 1, "degraded_pipelines": [],
                      "route_degraded": False}

    from dqagent.obs import tracer
    tid = tracer.mint(rt.cp, "NIGHT-ZZ-TESTCLEAN")
    with tracer.root("NIGHT", tid, night_date="2024-01-01", person_id=None, attempt=1,
                     profile_digests=clean_profile["digests"], **rt._root_args()):
        rt._record_profile_digest_degradation(clean_profile)

    with rt.cp.as_role("dq_auditor") as c:
        rows = c.execute("SELECT name FROM dq_ctl.spans WHERE trace_id=%s", (tid,)).fetchall()
    assert not any(r["name"] == "profile_digests" for r in rows)
