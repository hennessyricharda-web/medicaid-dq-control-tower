import hashlib
import json
from collections import Counter

from oracle import findings, ledger_keys, structure, tokenize

from synthetic.generate import generate
from synthetic.seeding import W1_CLASSES
from synthetic.util import npi_is_valid

ARGS = dict(seed=42, start="20260101", months=3)


def _x12(run):
    return sorted(run["out_dir"].glob("*.x12"))


def test_envelope_counts_correct_in_realistic_run(realistic_run):
    files = _x12(realistic_run)
    assert len([f for f in files if f.name.startswith("834_")]) == 2
    for m in ("ZZMCOA", "ZZMCOB"):
        assert 2 <= len([f for f in files if f.name.startswith(f"837P_{m}_")]) <= 3
    n_st = 0
    for f in files:
        _, segs = tokenize(f.read_text(encoding="ascii"))
        isa, groups, iea = structure(segs)
        assert iea[1] == str(len(groups)), f.name                     # IEA01 = group count
        assert iea[2] == isa[13], f.name                              # IEA02 = ISA13
        for g in groups:
            assert g["ge"][1] == str(len(g["sts"])), f.name            # GE01 = ST count
            assert g["ge"][2] == g["gs"][6], f.name                    # GE02 = GS06
            for st in g["sts"]:
                n_st += 1
                assert st["se"][1] == str(len(st["segs"])), f.name     # SE01 = ST..SE inclusive
                assert st["se"][2] == st["st"][2], f.name              # SE02 = ST02
                assert st["st"][3] == g["gs"][8], f.name               # ST03 = GS08
    acks = sum(len(f.read_text().splitlines()) - 1 for f in realistic_run["out_dir"].glob("ack_*.csv"))
    assert acks == n_st                                                # one ack row per ST


def test_isa_width_is_106(realistic_run, seeded_run):
    for f in _x12(realistic_run):
        text = f.read_text(encoding="ascii")
        assert text.index("~") + 1 == 106, f.name
        assert tokenize(text)[0] == 106
    ledger = json.loads((seeded_run["out_dir"] / "ledger.json").read_text())
    seeded_bad = {e["file"] for e in ledger if e["defect_class"] == "ISA_WIDTH"}
    for f in _x12(seeded_run):
        assert (tokenize(f.read_text(encoding="ascii"))[0] == 106) == (f.name not in seeded_bad), f.name


def test_generated_provider_npis_pass_luhn(realistic_run):
    provs = realistic_run["pop"].providers
    billing = [p for p in provs if not p.atypical]
    assert len(billing) == 40 and len(provs) - len(billing) >= 3
    for p in billing:
        assert npi_is_valid(p.npi), p.npi
        assert p.npi[0] in "12"
    assert all(p.npi is None for p in provs if p.atypical)
    assert npi_is_valid("1234567893")  # CMS published example
    assert not findings(realistic_run["out_dir"]).get("NPI_LUHN")


def test_seeded_instances_per_class_at_least_3(seeded_run):
    ledger = json.loads((seeded_run["out_dir"] / "ledger.json").read_text())
    prim = Counter(e["defect_class"] for e in ledger if "derived_from" not in e["expected_effect"])
    assert set(prim) == set(W1_CLASSES)
    for c in W1_CLASSES:
        assert prim[c] >= 3, (c, prim[c])
    assert {e["defect_class"] for e in ledger} <= set(W1_CLASSES)
    for e in ledger:
        assert set(e) == {"defect_id", "defect_class", "file", "locator", "expected_effect"}
        assert (seeded_run["out_dir"] / e["file"]).exists()


def test_realistic_run_has_no_w1_findings(realistic_run):
    assert findings(realistic_run["out_dir"]) == {}
    assert json.loads((realistic_run["out_dir"] / "ledger.json").read_text()) == []


def test_seeded_findings_equal_ledger_exactly(seeded_run):
    """Every seeded defect is present, and nothing else in the files trips a W1 check."""
    ledger = json.loads((seeded_run["out_dir"] / "ledger.json").read_text())
    got, want = findings(seeded_run["out_dir"]), ledger_keys(ledger)
    for c in W1_CLASSES:
        assert got.get(c, set()) == want.get(c, set()), c


def test_determinism_by_seed(tmp_path, seeded_run):
    a = generate("w1", "seeded", out_root=tmp_path / "a", commit_log=tmp_path / "log", **ARGS)
    files_a = {p.name: p.read_bytes() for p in a["out_dir"].iterdir()}
    files_s = {p.name: p.read_bytes() for p in seeded_run["out_dir"].iterdir()}
    assert files_a == files_s
    b = generate("w1", "seeded", out_root=tmp_path / "b", commit_log=tmp_path / "log",
                 **dict(ARGS, seed=43))
    assert b["ledger_sha256"] != a["ledger_sha256"]


def test_ledger_hash_matches_and_is_committed(seeded_run, commit_log):
    out = seeded_run["out_dir"]
    h = hashlib.sha256((out / "ledger.json").read_bytes()).hexdigest()
    assert (out / "ledger.sha256").read_text().split()[0] == h == seeded_run["ledger_sha256"]
    hn = hashlib.sha256((out / "negative_controls.json").read_bytes()).hexdigest()
    lines = [ln for ln in commit_log.read_text().splitlines() if f"ledger_sha256={h}" in ln]
    assert lines and f"negative_controls_sha256={hn}" in lines[-1] and "run_id=w1\t" in lines[-1]
