"""Part F P1-1 §D: CheckResult, citations (slot -> fact_key -> evidence_ref), template citations, fact sources."""
import pytest

from dqagent import llm
from dqagent.llm import check as chk
from dqagent.llm import facts as F
from dqagent.llm import template as T
from dqagent.llm.transports import FixedTransport
from tests.t0.test_parte_explainer import SAMPLES

RULE = SAMPLES["rule_disposition"]


def test_check_keeps_its_tuple_form_and_check_result_adds_codes_bindings_and_hashes():
    f = F.build("rule_disposition", RULE)
    text = "Check B-CMP-CLM-POS found 23 of 164 records."
    claims = [{"fact_key": "failed", "value": 23}, {"fact_key": "evaluated", "value": 164}]
    assert chk.check(text, claims, f) == (True, [])
    r = chk.check_result(text, claims, f)
    assert r.ok and r.reasons == [] and r.reason_codes == []
    assert [(b["shown"], b["fact_key"]) for b in r.bindings] == [("23", "failed"), ("164", "evaluated")]
    assert all(text[b["start"]:b["end"]] == b["shown"] for b in r.bindings)
    assert len(r.token_sha256) == len(text.split()) and all(len(h) == 64 for h in r.token_sha256)
    bad = chk.check_result("It is safe to release 31 records because of a change.", [], f)
    assert not bad.ok and {"NUMBER_NOT_IN_FACTS", "ADVICE", "CAUSAL"} <= set(bad.reason_codes)
    assert "CHECK_OTHER" not in bad.reason_codes


@pytest.mark.parametrize("reason,code", [
    ("slot form: digit outside a slot: 7", "SLOT_DIGIT"), ("number misbound: 23 is ...", "NUMBER_MISBOUND"),
    ("number binding ambiguous: 0 ...", "NUMBER_AMBIGUOUS"), ("disposition 'held' contradicts fact ['RELEASED']",
                                                              "DISPOSITION_CONTRADICTS_FACT"),
    ("disposition 'x' is not bound to a claim in: 'y'", "DISPOSITION_UNBOUND"), ("quantifier word: 'all'", "QUANTIFIER"),
    ("a nightly-run explanation must say the night was simulated", "NOT_SIMULATED")])
def test_reason_codes_are_stable(reason, code):
    assert chk.reason_code(reason) == code


def test_slot_citations_point_at_the_rendered_span_and_its_fact():
    text = "Check {rule_id} {disposition} for {failed} records."
    r = llm.explain_result("rule_disposition", RULE, cfg={"provider": "anthropic"}, deployment="test",
                           transport=FixedTransport('{"text": "%s"}' % text))
    assert r.fact_check == "PASS" and r.citation_label == "slot"
    assert [(c["fact_key"], c["shown"], c["value"]) for c in r.claims] == [
        ("rule_id", "B-CMP-CLM-POS", "B-CMP-CLM-POS"), ("disposition", "failed", "FAIL"), ("failed", "23", 23)]
    assert all(r.text[c["start"]:c["end"]] == c["shown"] for c in r.claims)
    src = F.fact_sources("rule_disposition", RULE)
    assert src["rule_id"] == "rule:B-CMP-CLM-POS" and src["relation"] == "rel:bronze.encounter_header"
    assert src["failed"] == "results:B-CMP-CLM-POS@bronze.encounter_header"


@pytest.mark.parametrize("kind", sorted(SAMPLES))
def test_template_citations_are_generated_from_facts_and_the_text_is_byte_identical(kind):
    f = F.build(kind, F.adapt(kind, SAMPLES[kind]))
    text, claims = T.render_with_claims(kind, f)
    assert text == T.render(kind, f)
    flat = F.flatten(f)
    assert claims and all(flat[c["fact_key"]] == c["value"] for c in claims)
    r = llm.explain_result(kind, SAMPLES[kind], cfg={"provider": "template"})
    assert r.citation_label == T.CITATION_LABEL == "generated_from_facts" and r.claims == claims


def test_fact_sources_are_never_part_of_facts_hash():
    f = F.build("rule_disposition", RULE)
    h = F.facts_hash(f)
    F.fact_sources("rule_disposition", RULE)
    assert F.facts_hash(F.build("rule_disposition", RULE)) == h
    assert not any(k.startswith("evidence") or k == "sources" for k in F.flatten(f))
    assert llm.explain("rule_disposition", RULE, cfg={"provider": "template"})["facts_hash"] == h


def test_versions_are_content_digests():
    assert llm.VERSIONS["prompt_version"].startswith("p-") and llm.VERSIONS["check_version"].startswith("c-")
    assert llm.VERSIONS["template_version"].startswith("t-")
    assert llm.VERSIONS["check_version"] == chk.CHECK_VERSION and llm.VERSIONS["template_version"] == T.TEMPLATE_VERSION


def test_masking_counts_are_returned_with_the_facts():
    f, m = F.build_result("rule_disposition", {**RULE, "failed": 4, "member_id": "ZZ1"},
                          classification={"member_id": ["identity"]})
    assert m == {"masked": 1, "suppressed": 1, "part2_withheld": 0} and "member_id" not in f
    assert F.build_result("unit_card", {"part2": True})[1]["part2_withheld"] == 1
