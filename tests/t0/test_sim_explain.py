"""Part E §10b: the nightly_run explanation (template, offline). It is byte-stable and pinned, it passes its own
structured check, it says the night was SIMULATED (check 7), and its facts come only from the summary's counts."""
import json

from dqagent import llm
from dqagent.llm import facts as F
from dqagent.runtime import Runtime

TEMPLATE = {"provider": "template"}


def _count(n, prefix="unit:"):
    return {"n": n, "ids": [f"{prefix}K{i}" for i in range(n)]}


SUMMARY = {
    "run_id": "SIM-20260223", "night_date": "2026-02-23", "files": ["837P_ZZMCOB_20260223_2.x12"],
    "counts": {
        "files": _count(1, "file:"), "abc_breaks": {**_count(0, "abc:"), "gating": 0, "advisory": 0},
        "units_created": _count(27), "released_autonomously": _count(0), "veto_windows_opened": _count(0),
        "held": {**_count(20), "mode_reasons": {"policy[AUTH_MEDICAID][FAIL] = H_IN": 20}},
        "pending_dependency": _count(31), "feed_items": _count(1, "feed:"), "monitor_signals": _count(0, "bb:"),
        "demotions": _count(0, "cr:")},
}
PINNED = ("Simulated night 2026-02-23 (run SIM-20260223): 1 file(s) arrived. The balance checks recorded 0 gating and "
          "0 advisory break(s). 27 unit(s) of records were created. During the night, 0 unit(s) were released by the "
          "agent on its own; 0 veto window(s) were opened; 20 unit(s) were held for a person to review. Held by "
          "reason: policy[AUTH_MEDICAID][FAIL] = H_IN: 20. 31 unit(s) are waiting until related eligibility data has "
          "been reviewed. Also: 1 feed item(s) were opened; 0 monitoring signal(s) were raised; 0 autonomy "
          "demotion(s) were recorded. This is a simulation over synthetic demo data; no real delivery was received. "
          "You can open the held units and signals from this run to review them, or run the next simulated night.")


def test_nightly_run_is_a_known_kind_with_pinned_byte_stable_text():
    assert "nightly_run" in llm.KINDS
    f = Runtime.nightly_facts(SUMMARY)
    a = llm.explain("nightly_run", f, cfg=TEMPLATE)
    b = llm.explain("nightly_run", json.loads(json.dumps(f)), cfg=TEMPLATE)
    assert a == b and a["text"] == PINNED and a["source"] == "template" and a["fact_check"] == "TEMPLATE"


def test_template_passes_its_own_check_and_check_7_requires_the_simulated_label():
    f = F.build("nightly_run", Runtime.nightly_facts(SUMMARY))
    text = llm.explain("nightly_run", Runtime.nightly_facts(SUMMARY), cfg=TEMPLATE)["text"]
    assert llm.fact_check(text, [], f, require_claims=False) == (True, [])        # template lint (not a fact check)
    plain = text.replace("Simulated night", "Night").replace("This is a simulation over synthetic demo data; ", "") \
        .replace("simulated night", "night")
    ok, why = llm.fact_check(plain, [], f, require_claims=False)
    assert not ok and any("simulated" in w for w in why)
    ok, why = llm.fact_check(text.replace("27 unit(s)", "28 unit(s)"), [], f, require_claims=False)  # invented
    assert not ok and any("28" in w for w in why)
    ok, why = llm.fact_check(text + " These were held because of the 837P file.", [], f,
                             require_claims=False)                                       # causal wording
    assert not ok and any("causal" in w for w in why)
    # the check is kind-specific: other kinds need no simulated label
    assert llm.fact_check("Check R1 has the result PASS.", [{"fact_key": "disposition", "value": "PASS"}],
                          {"rule_id": "R1", "disposition": "PASS", "explanation_kind": "rule_disposition"})[0]


def test_facts_are_counts_only_and_unit_counts_are_small_cell_suppressed():
    s = json.loads(json.dumps(SUMMARY))
    s["counts"]["released_autonomously"] = _count(4)
    f = Runtime.nightly_facts(s)
    assert not any("K0" in json.dumps(v) for v in f.values())               # no ids in the facts
    e = llm.explain("nightly_run", f, cfg=TEMPLATE)
    assert "fewer than 11 unit(s) were released by the agent on its own" in e["text"]
    assert F.build("nightly_run", f)["released_units"] == "<11"
