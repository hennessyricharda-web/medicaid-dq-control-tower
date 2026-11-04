"""Generic-naming guard (user rule, 2026-10-22): no client or organisation names, and no client specifics,
anywhere in the build. Naming is generic to the DQ estate; the exemplar state pack is STATE_A.

The banned list is stored as SHA-256 hashes of lower-cased word unigrams and bigrams, so the names themselves
never enter the repository. Case-sensitive tokens (bare state abbreviations) are hashed separately.
Scope: every text file in the repo, except vendored / generated directories and lockfiles.
"""
import hashlib
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
# Generated, gitignored tool output only (never repo content): caches, build output, local demo state.
SKIP_DIRS = {"node_modules", ".next", "target", "__pycache__", ".pytest_cache", ".git",
             ".ruff_cache", ".smoke-shots", ".venv", ".pgdata"}
SKIP_FILES = {"package-lock.json", "tsconfig.tsbuildinfo"}
TEXT_EXT = {".py", ".md", ".yaml", ".yml", ".csv", ".json", ".ts", ".tsx", ".js", ".mjs", ".sql", ".txt", ".toml",
            ".cfg", ".ini", ".html", ".css", ".sh", ".x12", ".edi", ".dat", ""}
BANNED = {
    "28afe5c5f088aff66a89f7417920d7d523a0ca5920085874a853cfef24c0bd8f",
    "835a80f9e0a3db11597a0aaaf19e86a5022ccc5cc3e6e0a0cc24dbde327d238e",
    "eb4e9effb9d2b023f54a6837da79015c03b81eb2d64600b2000d4c6d444960eb",
    "c28c3229b9fa5ccbef4ec337440e0e522e05098dcfa431ddd1c9b6bab8fcf49c",
    "db4dc186aedf33d7d3115bfe996a7b1890007f5826cf389d6b8767e13d03cfa7",
    "d30b4b991d372bab611661f1d64fc950a35daee8a61b3812e4610c22d5d70a81",
    "f66f87d87754f4a79fe54376bd0c491aa050dcf491374b2ea10cd1172e8cbe84",
    "22970beb32ef5182d0a8ef83472f085689eac2923551c56b631d403e88bbc0e9",
    "5e9ceb28244d06451ffa713a7552d8d89519875f3db22c7626d04f5379c30710",
    "635c9d771ebf49b4435dced38e7b0f6eff1b6b8b57e6040f7350572c0ecc8e35",
    "a7d324a4b53ea3878089c268660a04874f40e805f6f74b28bb448073625ea6f0",
    "3017280a59735725e78daf17768661393917c26ad7b99478360e6d964917f1cf",
    "2deb699f25f9b74f8126c753de68c58996b8a0d3b99b6daaa38c7ebda89a364c",
    "c4cd515f6b1e1463f42f5f53680962fc608961931d87e971758c1c8668f8f6c2",
    "88d86038972ab44ff68d1615900e662c3b37939f9ce73730cd85657f799ee5dd",
    "ece494b26a40b981d046fbbb3447733ddfbcc1c828b5700e8f59af209e60e31c",
    "e226bb99c808128125e22fe677a77975f6a814115d69560effaadb05b03dd7c9",
    "011f1fbaee26d2639aa99f07d4cdb9b5d8cbc65c958c5f0c0bad5c82315adbf9",
    "830c2d1f0da7bf8f5fb5c1bb093b42debb33918deae7d559f7fc418549043f18",
    "72a4798cb0abe42d287309a5ff3d271db2f067238558cf19e7a18e315d8cc789",
    "d0b3e17ddd4e05ee9fadfd7535a93d2b20937c692fac65e6aea6fb395dd6a895",
    "66287d6ad6e568a782a60f37dfc91b8df92026b7c64115dd5bc658e5d64c923b",
    "161a2bbf67036e9e2ef19db1ea5c2cb238205bd5e2a95af43ad8747cc7d4bd2f",
    "93babfec2f0c9778de88e6d0a303448a194353c425497bab45f7ba0a0dbe842d",
    "20cd0cd5561b7df589741b649547fb853436f3b02e243e53eddca0eae5621336",
    "f19cb2e90d327cf8d54cf0307707324a23e0d80cf542483b9bbfda4c6e7f9a77",
    "04c2998f3d458c7724f8f3dde10dd431b4f2103e71db8b1e39aa2093f5c32e54",
}
# Client SPECIFICS that are not names (post-build red-team F1): contract section/table locators and penalty
# schedules identify a source contract as surely as its name does.
SPEC_PATTERNS = [r"\bLD \$", r"[Ll]iquidated damages", r"\bSec(?:tion)?\.? [IVX]+\.[A-Z]", r"\bTable [0-9]+ item [0-9]+",
                 r"\bContract #"]
BANNED_CASE_SENSITIVE = {
    "b629074b98f63057767c88e2d36691bb5c8b9ceafcc461971d215342baa0a90f",
}


def _h(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def _files():
    for p in ROOT.rglob("*"):
        if not p.is_file() or set(p.relative_to(ROOT).parts) & SKIP_DIRS or p.name in SKIP_FILES:
            continue
        if p.suffix.lower() in TEXT_EXT and p.stat().st_size < 20_000_000:
            yield p


def test_no_client_names_or_specifics():
    hits, undecodable = [], []
    n = 0
    for p in _files():
        try:
            text = p.read_text(encoding="utf-8", errors="strict")
        except UnicodeDecodeError:
            undecodable.append(str(p.relative_to(ROOT)))
            continue
        n += 1
        low = re.findall(r"[a-z0-9]+", text.lower())
        grams = set(low) | {a + " " + b for a, b in zip(low, low[1:])}
        bad = {g for g in grams if _h(g) in BANNED}
        bad |= {t for t in set(re.findall(r"[A-Za-z0-9]+", text)) if _h(t) in BANNED_CASE_SENSITIVE}
        bad |= {"spec:" + pat for pat in SPEC_PATTERNS if re.search(pat, text)}
        names = re.findall(r"[a-z0-9]+", str(p.relative_to(ROOT)).lower())        # file and folder names too
        bad |= {g for g in set(names) | {a + " " + b for a, b in zip(names, names[1:])} if _h(g) in BANNED}
        if bad:
            # report the file and the HASH only, so a failing run does not print the name either
            hits.append((str(p.relative_to(ROOT)), sorted(b if b.startswith("spec:") else _h(b)[:12] for b in bad)))
    assert n > 50, "guard scanned too few files; scope is wrong"
    assert not undecodable, f"guard could not read (and so cannot vouch for) {undecodable}"
    assert not hits, f"client names / specifics found (hash prefixes): {hits}"


def test_guard_detects_a_planted_name(tmp_path, monkeypatch):
    """Negative control: the guard must fire on a planted banned bigram (built from hashes, not literals)."""
    planted = ROOT / "tests" / "t0" / "_planted_name_control.txt"
    try:
        # the bigram is reconstructed by search over a tiny alphabet would be slow; instead use a known
        # non-banned word plus check that hashing logic flags a banned hash injected into BANNED.
        planted.write_text("estate qualitycontrolsentinel")
        monkeypatch.setitem(globals(), "BANNED", BANNED | {_h("qualitycontrolsentinel")})
        try:
            test_no_client_names_or_specifics()
        except AssertionError as e:
            assert "_planted_name_control.txt" in str(e)
        else:
            raise AssertionError("guard did not fire on a planted banned token")
    finally:
        planted.unlink(missing_ok=True)
