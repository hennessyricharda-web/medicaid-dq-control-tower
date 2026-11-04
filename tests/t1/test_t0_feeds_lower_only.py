"""Part F red team R5 (HIGH): a FEEDS change is lower-only (ONE approver) only when it tightens, decided in the
DATABASE (dq_ctl.feeds_lower_only, used by gov_item_lower_only) whatever the caller says, with the same rules in the
Python classifier (registry.feeds_loosening / lower_only). Each tightening key is tested in both directions."""
import copy
import json
from pathlib import Path

import psycopg
import pytest
import yaml

from dqagent.governance.registry import feeds_loosening, lower_only

from .w1_harness import Slice

SEED = yaml.safe_load(open(Path(__file__).resolve().parents[2] / "config" / "state" / "ZZ" / "feeds.yaml"))


def _with(**over):
    d = copy.deepcopy(SEED)
    for path, v in over.items():
        cur = d
        keys = path.split("__")
        for k in keys[:-1]:
            cur = cur[k]
        if v is _DEL:
            cur.pop(keys[-1], None)
        else:
            cur[keys[-1]] = v
    return d


_DEL = object()
CASES = [   # (name, old, new, lower_only)
    ("unchanged", SEED, SEED, True),
    ("part2_program false->true", SEED, _with(feeds__X12_837P__part2_program=True), True),
    ("part2_program true->false", _with(feeds__X12_837P__part2_program=True), SEED, False),
    ("part2_programs grows", SEED, _with(part2_programs=["MCHIP"]), True),
    ("part2_programs shrinks", _with(part2_programs=["MCHIP"]), SEED, False),
    ("part2_relations grows", SEED, _with(part2_relations=["bronze.encounter_header"]), True),
    ("part2_relations shrinks", _with(part2_relations=["bronze.encounter_header"]), _with(part2_relations=[]), False),
    ("delivered false->true", SEED, _with(feeds__X12_837I__delivered=True), True),
    ("delivered true->false", SEED, _with(feeds__X12_837P__delivered=False), False),
    ("delivered removed", SEED, _with(feeds__X12_837P__delivered=_DEL), False),
    ("delivered absent->true", _with(feeds__X12_837I__delivered=_DEL), _with(feeds__X12_837I__delivered=True), False),
    ("part2_program removed", _with(feeds__X12_837P__part2_program=True), _with(feeds__X12_837P__part2_program=_DEL),
     False),
    ("not_delivered_elements shrinks", _with(not_delivered_elements=["ELG.DOB"]), SEED, True),
    ("not_delivered_elements grows", SEED, _with(not_delivered_elements=["ELG.DOB"]), False),
    ("empty_ok true->false", SEED, _with(feeds__X12_834__empty_ok=False), True),
    ("empty_ok false->true", _with(feeds__X12_834__empty_ok=False), SEED, False),
    ("empty_ok removed", SEED, _with(feeds__X12_834__empty_ok=_DEL), False),
    ("cadence shorter", SEED, _with(feeds__X12_837P__cadence={"expected": True, "every": "P1D"}), True),
    ("cadence longer", SEED, _with(feeds__IE_EXTRACT__cadence={"expected": True, "every": "P1W"}), False),
    ("cadence monthly -> weekly", _with(feeds__X12_837P__cadence={"expected": True, "every": "P1M"}), SEED, True),
    ("cadence no longer expected", SEED, _with(feeds__X12_837P__cadence={"expected": False, "every": "P1W"}), False),
    ("cadence removed", SEED, _with(feeds__X12_837P__cadence=_DEL), False),
    ("cadence unparsable", SEED, _with(feeds__X12_837P__cadence={"expected": True, "every": "soon"}), False),
    ("routing changed", SEED, _with(routing__ignore=["^x$"]), False),
    ("feed elements changed", SEED, _with(feeds__X12_837P__elements=["CLM.ID"]), False),
    ("feed reason text changed", SEED, _with(feeds__X12_837I__reason="changed"), False),
    ("feed added", SEED, _with(feeds__NEW_FEED={"delivered": False, "reason": "not yet"}), False),
    ("feed removed", SEED, _with(feeds__X12_837D=_DEL), False),
    ("other top-level key", SEED, _with(note="x"), False),
]


@pytest.mark.parametrize("name,old,new,expected", CASES, ids=[c[0] for c in CASES])
def test_feeds_lower_only_matrix_database_and_python_agree(cp_dsn, name, old, new, expected):
    with psycopg.connect(**{**cp_dsn, "user": "dq_registry_svc"}) as c:
        got = c.execute("SELECT dq_ctl.feeds_lower_only(%s::jsonb, %s::jsonb)",
                        (json.dumps(old), json.dumps(new))).fetchone()[0]
    assert got is expected, name
    assert lower_only("FEEDS", old, new, "PUT") is expected, (name, feeds_loosening(old, new))
    assert (not feeds_loosening(old, new)) is expected, name


def test_creating_or_deleting_feeds_is_never_lower_only(cp_dsn):
    assert lower_only("FEEDS", None, SEED, "PUT") is False and lower_only("FEEDS", SEED, None, "DELETE") is False


def test_part2_flip_needs_two_approvers_even_when_the_caller_says_not_loosening(fresh_cp_dsn):
    """End to end through gov_propose with p_loosening = false: the database stores approvals_required = 2 for the
    loosening flip (part2_program true -> false after it was set) and for undelivering a feed; 1 for a tightening."""
    s = Slice(fresh_cp_dsn)
    base = s.effective_item("FEEDS", "feeds")
    cases = {"CR-UNDELIVER": ("feeds", "X12_837P", "delivered", False),
             "CR-TIGHTEN": ("feeds", "X12_837P", "part2_program", True),
             "CR-EMPTYOK": ("feeds", "X12_834", "empty_ok", True)}         # already true: unchanged -> one approver
    want = {"CR-UNDELIVER": 2, "CR-TIGHTEN": 1, "CR-EMPTYOK": 1}
    with psycopg.connect(**{**fresh_cp_dsn, "user": "dq_api"}) as c:
        for cr, (a, f, k, v) in cases.items():
            item = copy.deepcopy(base)
            item["content"][a][f][k] = v
            c.execute("SELECT dq_ctl.gov_propose(%s,'ZZ','E100','HUMAN','a feeds change said to be harmless',"
                      "%s::jsonb,'{}'::jsonb,'{}'::jsonb,false,'h','{}'::jsonb,'{}'::jsonb)", (cr, json.dumps([item])))
        got = dict(c.execute("SELECT cr_id, approvals_required FROM dq_ctl.config_change_requests").fetchall())
    assert got == want
    item = copy.deepcopy(base)
    item["content"]["feeds"]["X12_837P"]["part2_program"] = False                # Python classifier: same verdict
    cr = s.registry.propose("ZZ", "E100", "undeliver professional encounters",
                            [{**copy.deepcopy(base), "content": {**base["content"], "feeds": {
                                **base["content"]["feeds"], "X12_837P": {**base["content"]["feeds"]["X12_837P"],
                                                                         "delivered": False}}}}])
    assert cr["loosening"] and cr["approvals_required"] == 2
