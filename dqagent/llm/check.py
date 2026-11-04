"""The structured fact check (Part E §7, hardened after the Part E post-build red team, E1). A provider's output is
used only if EVERY test passes; otherwise the deterministic template is shown and the explanation is FALLBACK.

The provider must return JSON {text, claims: [{fact_key, value}]}. Tests:
  0 claims       claims is a list; every claim's fact_key exists (dotted path) and its value equals that fact
  1 numbers      every number in the text is BOUND to exactly one valid claim IN ITS OWN CLAUSE (F-21): digits (1,234.5 / $1,234.50 / 12% / dates such
                 as 2026-01-15) and spelled-out English cardinals up to at least 9,999 ("thirty", "two thousand four
                 hundred and six", "a dozen"), with "%", "percent" and "per cent". A number no claim covers is a
                 rejection, even when the same number appears somewhere in the facts. Every number must also be a
                 fact value (defence in depth). Clauses come from the one clause map that test 3 also reads
                 (newlines, table pipes and bullets are boundaries). A number binds through the NEAREST word in its
                 clause that names a numeric fact (token distance; in "X of Y" the base Y skips X's word); a clause
                 with no such word reads its neighbour, left first. Sign and unit must agree ("-23" never binds 23,
                 "23%" never binds a count; an unsigned number binds a negative fact only beside a direction word).
                 Digits inside identifiers (L3, R-ENC-003) are never numbers. Reasons: "number misbound",
                 "number binding ambiguous", "number sign mismatch", "number unit mismatch", and
                 "number arithmetic inconsistent" (X of Y with X > Y; claimed parts above their claimed whole).
  2 identifiers  every identifier-shaped token (letters/digits joined by - _ . : @ /, a letter+digit code such as L3,
                 a digit-led code such as 837P, or a hyphenated code such as b-cmp) equals, case-insensitively and as
                 a WHOLE token, a string fact or a whole token / component of one. Never a substring match.
  3 dispositions every disposition word (passed, failed, released, published, held, blocked, withheld, vetoed,
                 approved, rejected, denied, withdrawn, ...) is BOUND to a valid claim in its clause: a claim whose
                 value is that disposition, or a count claim whose key names it (released_units, failed, ...) and,
                 when the clause has numbers, whose value is one of them. Its polarity must agree with the claim:
                 negation is read per CLAUSE (split on punctuation and conjunctions; a negator anywhere in the
                 clause negates every disposition in it), so "it is not true that this unit was held" is negated.
  4 polarity     against the facts too: no negated disposition that IS a fact, and no asserted disposition whose
                 opposite is a fact and which is not itself one
  5 causal       no causal wording (because, since, due to, as a result, owing to, led to, resulted in, ...) unless
                 a HYPOTHESIS fact exists
  6 advice       no release / approval advice ("safe to release", "you may release it", "can proceed",
                 "ok to release", "should approve", ...)
  7 simulated    a nightly_run explanation (Part E §10b) must say the night was simulated (the demo label)

check(text, claims, facts) -> (ok, reasons); reasons is empty when ok.
check(text, claims, facts, require_claims=False) skips the claim binding of tests 1 and 3 and the polarity reading
of tests 3-4 (numbers and identifiers must still be fact values; causal / advice / simulated still apply): used only
to lint the deterministic TEMPLATE in tests. Template output is never labelled fact-checked
(fact_check "TEMPLATE"); this mode is not a fact check of anything a provider wrote.
"""
from __future__ import annotations

import json
import re
from decimal import Decimal, InvalidOperation

from dqagent.llm.facts import flatten

# ------------------------------------------------------------------------------------------------ spelled numbers
UNITS = {"zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
         "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
         "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19}
TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fourty": 40, "fifty": 50, "sixty": 60, "seventy": 70,
        "eighty": 80, "ninety": 90}
SCALES = {"hundred": 100, "thousand": 1000, "million": 1_000_000}
NUMBER_WORDS = set(UNITS) | set(TENS) | set(SCALES) | {"dozen"}
SPELLED = {**UNITS, **TENS}                    # kept for callers of the earlier module

# ------------------------------------------------------------------------------------------------ dispositions
# disposition words as they appear in prose -> canonical disposition
DISPOSITION_WORDS = {
    "pass": "PASS", "passed": "PASS", "passes": "PASS", "passing": "PASS", "succeeded": "PASS",
    "fail": "FAIL", "failed": "FAIL", "fails": "FAIL", "failing": "FAIL", "failure": "FAIL", "failures": "FAIL",
    "release": "RELEASED", "released": "RELEASED", "releases": "RELEASED", "releasing": "RELEASED",
    "published": "RELEASED", "publish": "RELEASED", "publishes": "RELEASED", "publishing": "RELEASED",
    "cleared": "RELEASED", "unblocked": "RELEASED",
    "held": "HELD", "hold": "HELD", "holds": "HELD", "holding": "HELD", "blocked": "HELD", "withheld": "HELD",
    "stopped": "HELD", "paused": "HELD", "quarantined": "HELD", "unreleased": "HELD", "parked": "HELD",
    "vetoed": "VETOED", "veto": "VETOED", "vetoes": "VETOED", "overruled": "VETOED",
    "withdrawn": "WITHDRAWN", "withdrew": "WITHDRAWN", "retracted": "WITHDRAWN", "revoked": "WITHDRAWN",
    "recalled": "WITHDRAWN",
    "approved": "APPROVED", "approve": "APPROVED", "approves": "APPROVED", "approval": "APPROVED",
    "accepted": "APPROVED",
    "rejected": "REJECTED", "reject": "REJECTED", "rejects": "REJECTED", "declined": "REJECTED",
    "refused": "REJECTED",
    "denied": "DENIED", "deny": "DENIED", "denies": "DENIED",
    "match": "MATCH", "matches": "MATCH", "matched": "MATCH", "agree": "MATCH", "agrees": "MATCH",
    "break": "BREAK", "breaks": "BREAK", "mismatch": "BREAK", "mismatched": "BREAK", "disagree": "BREAK",
    "meets": "MEETS", "met": "MEETS", "below": "BELOW",
}
OPPOSITES = {
    "PASS": {"FAIL", "ERROR"}, "FAIL": {"PASS"}, "RELEASED": {"HELD", "VETOED", "DENIED", "WITHDRAWN"},
    "HELD": {"RELEASED"}, "VETOED": {"RELEASED"}, "WITHDRAWN": {"RELEASED", "CONFIRMED", "ACCEPTED"},
    "APPROVED": {"REJECTED", "DENIED"}, "REJECTED": {"APPROVED"}, "DENIED": {"APPROVED", "RELEASED"},
    "MATCH": {"BREAK"}, "BREAK": {"MATCH", "WITHIN"}, "MEETS": {"BELOW"}, "BELOW": {"MEETS"},
}
# a count fact whose KEY names a disposition binds that disposition word (released_units, failed, held_units, ...)
KEY_STEMS = {
    "PASS": ("pass",), "FAIL": ("fail",), "RELEASED": ("releas", "publish"), "HELD": ("held", "hold"),
    "VETOED": ("veto",), "WITHDRAWN": ("withdraw",), "APPROVED": ("approv",), "REJECTED": ("reject",),
    "DENIED": ("denied", "deny", "denies"), "MATCH": ("match",), "BREAK": ("break",), "MEETS": ("meet",),
    "BELOW": ("below",),
}
NEGATIONS = {"not", "no", "never", "without", "neither", "nor", "none", "nothing", "nobody", "cannot", "isn't",
             "wasn't", "aren't", "weren't", "didn't", "doesn't", "don't", "hasn't", "haven't", "hadn't", "won't",
             "can't", "couldn't", "shouldn't", "wouldn't", "false", "untrue", "incorrect", "nor"}
CONJUNCTIONS = {"and", "but", "or", "while", "whereas", "although", "though", "however", "yet", "so", "because",
                "then", "also", "plus", "whilst", "otherwise", "instead", "except", "unless"}
CAUSAL = re.compile(
    r"\b(because|since|caused\s+by|causes?d?\s+(the|this|a|an)|due\s+to|root\s+cause|as\s+a\s+(result|consequence)|"
    r"result(ed|s|ing)?\s+(from|in)|led\s+to|leads?\s+to|leading\s+to|owing\s+to|attributable\s+to|thanks\s+to|"
    r"on\s+account\s+of|stems?\s+from|stemm(ed|ing)\s+from|driven\s+by|triggered\s+by|consequently|therefore|"
    r"hence|thus|which\s+is\s+why|that\s+is\s+why|the\s+reason\s+(is|was|being)|explained\s+by)\b", re.IGNORECASE)
_ADVICE_VERBS = (r"(release|released|releasing|approve|approved|approving|proceed|proceeded|publish|published|"
                 r"sign\s+off|signed\s+off|accept|accepted|let\s+(it|them|this|these)\s+(through|go))")
ADVICE = re.compile(
    r"\b(safe\s+to\s+" + _ADVICE_VERBS + r"|"
    r"(may|can|could|should|must|might|ought\s+to|need\s+to|needs\s+to|is\s+free\s+to|are\s+free\s+to)\s+"
    r"(now\s+|safely\s+|simply\s+|just\s+|confidently\s+|immediately\s+|go\s+ahead\s+and\s+)?(be\s+)?" + _ADVICE_VERBS +
    r"|ok(ay)?\s+to\s+" + _ADVICE_VERBS + r"|fine\s+to\s+" + _ADVICE_VERBS + r"|clear(ed)?\s+to\s+" + _ADVICE_VERBS +
    r"|good\s+to\s+go|recommend(ed|s|ing)?\s+(that\s+you\s+)?(approv|releas|publish|accept)\w*"
    r"|go\s+ahead\s+(and|with)|please\s+(approve|release|publish|accept)"
    r"|(approve|release|publish)\s+(it|this|them|these|the\s+\w+)\s+now|no\s+need\s+to\s+(hold|review|wait|check)"
    r"|nothing\s+(stops|prevents|blocks)\s+(you|release|the\s+release)|(i|we)\s+(suggest|advise)\s+(approv|releas)\w*"
    r"|you\s+(should|can|may)\s+(go\s+ahead|move\s+forward))\b", re.IGNORECASE)
SIMULATED = re.compile(r"\bsimulat(ed|ion)\b", re.IGNORECASE)

DATE = re.compile(r"(?<![\w-])\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?)?(?![\w-])")
IDENT = re.compile(r"(?<![\w$])(?:[A-Za-z0-9]+(?:[-_.:@/][A-Za-z0-9]+)+|[A-Za-z]+[0-9][A-Za-z0-9]*|"
                   r"[0-9]+[A-Za-z][A-Za-z0-9]*)(?![\w])")
NUMBER = re.compile(r"(?<![A-Za-z0-9_])-?\d{1,3}(?:,\d{3})+(?:\.\d+)?|(?<![A-Za-z0-9_.])-?\d+(?:\.\d+)?|"
                    r"(?<![A-Za-z0-9_])\.\d+")
PERCENT_AFTER = re.compile(r"\s*(%|percent\b|per\s+cent\b|pct\b)", re.IGNORECASE)
WORD = re.compile(r"[A-Za-z]+(?:'[A-Za-z]+)?")
BOUNDARY = re.compile(r"[.;:!?,()\[\]{}—–|•·\n\r\t]|\s-\s|\s--\s|(?m:^\s*[-*+]\s)")
_VOWELS = set("aeiouy")


# ------------------------------------------------------------------------------------------------ helpers
def _is_ident(tok: str) -> bool:
    """Identifier-shaped: has a letter and a digit, or one of _ . : @ /, or is a hyphenated code. A hyphenated word
    is prose (look-back, Look-back, two-person) only when every part is a word of at least two letters with a vowel,
    in lower case (the first letter may be a capital); anything else (B-CMP, b-cmp, b-val-x, clm-pos) is an
    identifier."""
    if not re.search(r"[A-Za-z]", tok):
        return False
    if re.search(r"\d", tok) or re.search(r"[_.:@/]", tok):
        return True
    if "-" in tok:
        parts = tok.split("-")
        prose = (all(len(p) >= 2 and p.isalpha() and (set(p.lower()) & _VOWELS) for p in parts)
                 and parts[0][1:] == parts[0][1:].lower() and all(p == p.lower() for p in parts[1:]))
        return not prose
    return False


def _dec(s) -> Decimal | None:
    try:
        return abs(Decimal(str(s).replace(",", "").replace("$", ""))).normalize()
    except (InvalidOperation, ValueError):
        return None


def _is_num(v) -> bool:
    return isinstance(v, (int, float, Decimal)) and not isinstance(v, bool)


def _str_numbers(s: str) -> set[Decimal]:
    """Numbers in a string fact. Dates and identifier-shaped tokens are blanked first, so the digits of a code
    (L3, R-ENC-003, 837P, 2026-Q1) are never numbers (F-21 round 2)."""
    out: set[Decimal] = set()
    masked = DATE.sub(" ", s)
    masked = IDENT.sub(lambda m: " " if _is_ident(m.group(0)) else m.group(0), masked)
    for tok in NUMBER.findall(masked):
        d = _dec(tok)
        if d is not None:
            out.add(d)
    return out


def _fact_numbers(flat: dict) -> set[Decimal]:
    out: set[Decimal] = set()
    for v in flat.values():
        if isinstance(v, bool) or v is None:
            continue
        if _is_num(v):
            d = _dec(v)
            if d is not None:
                out.add(d)
        else:
            out |= _str_numbers(str(v))
    return out


def _tokens_of(s: str) -> set[str]:
    """Whole tokens of a string fact (lower case): the whole value, each identifier / date in it, and the
    components of those split on the hierarchical separators . : @ / (never arbitrary substrings)."""
    s = str(s)
    out = {s.strip().lower()}
    for m in list(IDENT.finditer(s)) + list(DATE.finditer(s)):
        t = m.group(0).lower()
        out.add(t)
        out.update(p for p in re.split(r"[.:@/]", t) if p)
    return out


def _fact_tokens(flat: dict) -> set[str]:
    out: set[str] = set()
    for v in flat.values():
        if isinstance(v, str):
            out |= _tokens_of(v)
    return out


def _fact_dispositions(flat: dict) -> set[str]:
    out = set()
    for v in flat.values():
        if isinstance(v, str):
            u = v.strip().upper()
            if u in OPPOSITES or u in {"ERROR", "CONFIRMED", "WITHIN", "VETO"}:
                out.add("VETOED" if u == "VETO" else u)
    return out


def _eq(a, b) -> bool:
    if (_is_num(a) or _is_num(b)) and not isinstance(a, bool) and not isinstance(b, bool):
        da, db = _dec(a), _dec(b)
        if da is not None and db is not None:
            return da == db and (str(a).lstrip().startswith("-") == str(b).lstrip().startswith("-"))
    if isinstance(a, bool) or isinstance(b, bool):
        return a is b or str(a).strip().lower() == str(b).strip().lower()
    return str(a).strip().upper() == str(b).strip().upper()


def _spelled_spans(text: str, masked: str) -> list[tuple[int, int, Decimal]]:
    """Spelled-out English cardinals in `masked` (identifier / digit spans already blanked) -> (start, end, value).
    Handles units, teens, tens (hyphenated or not), "hundred", "thousand", "a hundred", "a dozen", "and" after a
    scale ("one hundred and five"), and "point" decimals ("ninety-one point two")."""
    words = [(m.start(), m.end(), m.group(0).lower()) for m in re.finditer(r"[A-Za-z]+", masked)]
    out: list[tuple[int, int, Decimal]] = []
    i = 0
    while i < len(words):
        s0, _, w = words[i]
        start = i
        if w == "a" and i + 1 < len(words) and words[i + 1][2] in ("hundred", "thousand", "dozen", "million"):
            i += 1
            w = words[i][2]
        if w not in NUMBER_WORDS:
            i += 1
            continue
        # "no one", "any one", "some one", "every one", "one another": a pronoun, not the number one
        if w == "one" and i == start:
            prev = words[i - 1][2] if i > 0 else ""
            nxt = words[i + 1][2] if i + 1 < len(words) else ""
            if prev in ("no", "any", "every", "some", "each", "which", "this", "that", "the") or nxt == "another":
                i += 1
                continue
        total, cur, j, last_end, prev_w = 0, 0, i, words[i][1], None
        while j < len(words):
            wj = words[j][2]
            gap = masked[last_end:words[j][0]] if j > i else ""
            if j > i and not re.fullmatch(r"[\s-]*", gap):
                break
            if wj == "and" and prev_w in SCALES and j + 1 < len(words) and words[j + 1][2] in NUMBER_WORDS:
                last_end, j, prev_w = words[j][1], j + 1, "and"
                continue
            if wj in UNITS and (prev_w is None or prev_w in TENS or prev_w in SCALES or prev_w == "and"):
                cur += UNITS[wj]
            elif wj in TENS and (prev_w is None or prev_w in SCALES or prev_w == "and"):
                cur += TENS[wj]
            elif wj == "hundred" and prev_w != "hundred":
                cur = (cur or 1) * 100
            elif wj in ("thousand", "million"):
                total += (cur or 1) * SCALES[wj]
                cur = 0
            elif wj == "dozen":
                cur = (cur or 1) * 12
            else:
                break
            last_end, prev_w, j = words[j][1], wj, j + 1
        value = Decimal(total + cur)
        # decimals: "<number> point <digit words>"
        if j + 1 < len(words) and words[j][2] == "point" and words[j + 1][2] in UNITS and UNITS[words[j + 1][2]] < 10:
            digits, k = "", j + 1
            while k < len(words) and words[k][2] in UNITS and UNITS[words[k][2]] < 10:
                digits += str(UNITS[words[k][2]])
                last_end, k = words[k][1], k + 1
            value = Decimal(f"{total + cur}.{digits}")
            j = k
        out.append((s0, last_end, value.normalize()))
        i = max(j, i + 1)
    return out


def _blank(s: str, a: int, b: int) -> str:
    return s[:a] + ("x" * (b - a)) + s[b:]


def _clauses(masked: str) -> list[tuple[int, int]]:
    """Clause spans: split on punctuation, dashes and conjunctions (spans of numbers / identifiers are blanked
    first, so a decimal point or a colon inside an identifier never splits a clause)."""
    cuts = {0, len(masked)}
    for m in BOUNDARY.finditer(masked):
        cuts.update((m.start(), m.end()))
    for m in WORD.finditer(masked):
        if m.group(0).lower() in CONJUNCTIONS:
            cuts.update((m.start(), m.end()))
    pts = sorted(cuts)
    return [(a, b) for a, b in zip(pts, pts[1:]) if b > a]


def _clause_of(spans: list[tuple[int, int]], pos: int) -> int:
    for i, (a, b) in enumerate(spans):
        if a <= pos < b:
            return i
    return len(spans) - 1


def _key_negative(key: str) -> bool:
    """A count key that names the NEGATION of its disposition (rules_not_passed, non_matching, unreleased_units)."""
    parts = re.split(r"[._]", key.lower())
    return any(p in ("not", "non", "no", "never") or re.match(r"(un|non)(releas|approv|match|pass|publish|block|"
                                                             r"held|veto|withdraw)", p) for p in parts)


def _truthy(v) -> bool:
    if isinstance(v, bool):
        return v
    if _is_num(v):
        return _dec(v) != 0
    if isinstance(v, str):
        return v.strip() not in ("", "0")          # "<11" (suppressed small cell) is a non-zero count
    return v is not None


def _claim_numbers(v) -> set[Decimal]:
    if isinstance(v, bool) or v is None:
        return set()
    if _is_num(v):
        d = _dec(v)
        return {d} if d is not None else set()
    return _str_numbers(str(v))


# ------------------------------------------------------------------------------------------------ number binding (F-21)
# Reason codes: a rejection reason starts with one of these.
NUMBER_UNBOUND = "number not bound to a claim"
NUMBER_MISBOUND = "number misbound"
NUMBER_AMBIGUOUS = "number binding ambiguous"
# key parts too generic to say which fact a clause is about (held_units / pending_units both name "units")
_GENERIC_KEY_PARTS = {"unit", "units", "rule", "rules", "record", "records", "count", "counts", "total", "number",
                      "num", "n", "pct", "not", "non", "no", "never", "of", "the", "and"}


NUMBER_SIGN = "number sign mismatch"
NUMBER_UNIT = "number unit mismatch"
NUMBER_ARITHMETIC = "number arithmetic inconsistent"
_RATE_PARTS = {"rate", "pct", "percent", "ratio", "share", "proportion", "tolerance"}
_PCT_PARTS = {"pct", "percent"}
# words that let an unsigned number stand for a negative fact ("$1,250.00 lower" for delta -1250)
DIRECTION_WORDS = {"lower", "less", "fewer", "decrease", "decreased", "down", "below", "short", "shortfall", "under",
                   "negative", "minus", "drop", "dropped", "fell", "reduced", "deficit", "lost"}
# part <= whole, checked whenever every key of one invariant is claimed
INVARIANTS = ((("failed",), "evaluated"), (("rules_not_passed",), "rules_checked"),
              (("released_units", "held_units", "pending_units"), "created_units"))


def _key_parts(key: str) -> list[str]:
    return [p for p in re.split(r"[._]", key.lower()) if p]


def _is_fraction(v) -> bool:
    return _is_num(v) and not isinstance(v, int) and 0 <= v <= 1


def _negative(v) -> bool:
    return (_is_num(v) and v < 0) or (isinstance(v, str) and v.strip().startswith("-"))


def _value_hit(d: Decimal, pct: bool, key: str, v) -> bool:
    """Value and unit agree (sign aside): a percent binds a 0..1 rate (x100) or a fact whose key says rate / pct;
    a plain number never binds a fact whose key says pct / percent, and a percent never binds a count."""
    parts = set(_key_parts(key))
    if pct:
        return ((_is_fraction(v) and _dec(round(float(v) * 100, 1)) == d)
                or (bool(parts & _RATE_PARTS) and d in _claim_numbers(v)))
    return not (parts & _PCT_PARTS) and d in _claim_numbers(v)


def _number_matches(d: Decimal, pct: bool, v, key: str = "", neg: bool = False, direction: bool = False) -> bool:
    """A number in the text matches a claim: value, unit and sign. "-23" never binds +23; an unsigned number binds a
    negative fact only when its clause carries a direction word ("1,250.00 lower")."""
    if not _value_hit(d, pct, key, v):
        return False
    if neg:
        return _negative(v)
    return not _negative(v) or direction


def _stem_eq(w: str, p: str) -> bool:
    """A prose word names a key part: equal, or one a prefix of the other over its first 3-5 letters
    (held / held, passed / pass, minimum / min, evaluated / evaluate)."""
    if w == p:
        return True
    k = min(len(w), len(p), 5)
    return k >= 3 and w[:k] == p[:k]


def _anchor_keys(w: str, negated: bool, numeric_keys) -> set[str]:
    """The numeric facts a prose word names: a distinctive part of the key (not "units", "rules", ...) equals the
    word's stem, or the word is a disposition whose count stem the key carries (blocked -> held_units). In a negated
    clause a disposition also names its opposite's counts ("were not released" -> held_units)."""
    canon = DISPOSITION_WORDS.get(w)
    stems = set(KEY_STEMS.get(canon, ())) if canon else set()
    if canon and negated:
        for o in OPPOSITES.get(canon, ()):
            stems |= set(KEY_STEMS.get(o, ()))
    out = set()
    for k in numeric_keys:
        parts = [p for p in _key_parts(k) if p not in _GENERIC_KEY_PARTS]
        if any(_stem_eq(w, p) for p in parts) or any(s in p for s in stems for p in parts):
            out.add(k)
    return out


def _bind_number(d: Decimal, pct: bool, neg: bool, shown: str, anchors: list[tuple[int, set[str]]],
                 direction: bool, valid: list[dict], clause_shown: str) -> tuple[str | None, int | None, str | None]:
    """Bind one number to exactly one claim through the NEAREST anchor word in its clause (token distance).
    Returns (reason or None, position of the anchor used, the fact_key bound or None).
      - unbound    no valid claim has this value;
      - sign/unit  a claim has the value but not its sign ("-23" for 23) or unit ("23%" for a count of 23);
      - misbound   the nearest word that names a numeric fact names a fact this number is not the value of
                   ("164 failed of 23 evaluated" when failed is 23);
      - ambiguous  claims with different fact_keys match and nothing singles one out."""
    match = sorted({c["fact_key"] for c in valid
                    if _number_matches(d, pct, c["value"], c["fact_key"], neg, direction)})
    where = f" in: '{clause_shown}'"
    if not match:
        if any(_value_hit(d, pct, c["fact_key"], c["value"]) for c in valid):
            return f"{NUMBER_SIGN}: {shown}{where}", None, None
        if any(_value_hit(d, not pct, c["fact_key"], c["value"]) for c in valid):
            return f"{NUMBER_UNIT}: {shown}{where}", None, None
        return f"{NUMBER_UNBOUND}: {shown}", None, None
    if anchors:
        dmin = min(a[0] for a in anchors)
        tied = [a for a in anchors if a[0] == dmin]
        near_pos = min(a[2] for a in tied)
        near = set().union(*(a[1] for a in tied))
        if len({frozenset(a[1]) for a in tied}) > 1:       # two words equally near, naming different facts
            return (f"{NUMBER_AMBIGUOUS}: {shown} is equally near words naming {', '.join(sorted(near))}"
                    f"{where}"), near_pos, None
        hit = sorted(set(match) & near)
        if not hit:
            return (f"{NUMBER_MISBOUND}: {shown} is the value of {', '.join(match)}, but the clause names "
                    f"{', '.join(sorted(near))}{where}"), near_pos, None
        if len(hit) > 1:
            return f"{NUMBER_AMBIGUOUS}: {shown} matches {', '.join(hit)}{where}", near_pos, None
        return None, near_pos, hit[0]
    if len(match) > 1:
        return f"{NUMBER_AMBIGUOUS}: {shown} matches {', '.join(match)}{where}", None, None
    return None, None, match[0]


# ------------------------------------------------------------------------------------------------ the check
def check(text: str, claims, facts: dict, *, require_claims: bool = True) -> tuple[bool, list[str]]:
    ok, reasons, _ = _check(text, claims, facts, require_claims=require_claims)
    return ok, reasons


def _check(text: str, claims, facts: dict, *, require_claims: bool = True) -> tuple[bool, list[str], list[dict]]:
    reasons: list[str] = []
    bindings: list[dict] = []
    if not isinstance(text, str) or not text.strip():
        return False, ["empty text"], bindings
    flat = flatten(facts)
    nums = _fact_numbers(flat)
    toks = _fact_tokens(flat)

    # 0 claims
    valid: list[dict] = []
    if not isinstance(claims, list):
        reasons.append("claims missing or not a list")
        claims = []
    for c in claims:
        if not isinstance(c, dict) or "fact_key" not in c or "value" not in c or not isinstance(c["fact_key"], str):
            reasons.append(f"malformed claim: {c!r}")
            continue
        if c["fact_key"] not in flat:
            reasons.append(f"claim on unknown fact: {c['fact_key']}")
        elif not _eq(c["value"], flat[c["fact_key"]]):
            reasons.append(f"claim disagrees with fact {c['fact_key']}: {c['value']!r} != {flat[c['fact_key']]!r}")
        else:
            valid.append({"fact_key": c["fact_key"], "value": flat[c["fact_key"]]})

    # tokenise: dates, identifiers, digit numbers, spelled numbers (each blanked before the next pass)
    masked = text
    dates = [(m.start(), m.end(), m.group(0)) for m in DATE.finditer(masked)]
    for a, b, _ in dates:
        masked = _blank(masked, a, b)
    idents = [(m.start(), m.end(), m.group(0)) for m in IDENT.finditer(masked) if _is_ident(m.group(0))]
    for a, b, _ in idents:
        masked = _blank(masked, a, b)
    numbers: list[tuple[int, int, Decimal, bool, str]] = []          # (start, end, value, percent, shown)
    for m in NUMBER.finditer(masked):
        d = _dec(m.group(0))
        if d is not None:
            numbers.append((m.start(), m.end(), d, bool(PERCENT_AFTER.match(masked, m.end())), m.group(0)))
    for a, b, *_ in numbers:
        masked = _blank(masked, a, b)
    for m in re.finditer(r"\d+", masked):           # any digit run the patterns above did not take is a number too
        numbers.append((m.start(), m.end(), _dec(m.group(0)), False, m.group(0)))
        masked = _blank(masked, m.start(), m.end())
    for a, b, d in _spelled_spans(text, masked):
        numbers.append((a, b, d, bool(PERCENT_AFTER.match(masked, b)), text[a:b]))
        masked = _blank(masked, a, b)
    # ONE clause map, shared by number binding (test 1) and dispositions (tests 3/4)
    clauses = _clauses(masked)
    plain = masked                                    # masked with every token span blanked to spaces, not "x"
    for a, b, *_ in list(dates) + list(idents) + numbers:
        plain = plain[:a] + " " * (b - a) + plain[b:]
    cl_numbers: dict[int, set[Decimal]] = {}
    for a, _, d, _, _ in numbers:
        cl_numbers.setdefault(_clause_of(clauses, a), set()).add(d)

    # 2 identifiers: whole tokens, case-insensitive
    for _, _, tok in idents:
        t = tok.lower()
        if t not in toks:
            reasons.append(f"identifier not in facts: {tok}")
    for _, _, tok in dates:
        if tok.lower() not in toks:
            reasons.append(f"date not in facts: {tok}")
        elif require_claims and not any(isinstance(c["value"], str) and tok.lower() in _tokens_of(c["value"])
                                        for c in valid):
            reasons.append(f"date not bound to a claim: {tok}")

    # 1 numbers: fact values, and bound to exactly one claim through the nearest anchor in their clause (F-21)
    numeric_keys = [k for k, v in flat.items() if _is_num(v)]
    words = [(m.start(), m.group(0).lower()) for m in WORD.finditer(plain)]
    order = sorted([(p, "w", w) for p, w in words] + [(n[0], "n", i) for i, n in enumerate(numbers)])
    tok_at = {pos: t for t, (pos, _, _) in enumerate(order)}      # token index by POSITION, never by word
    cl_words: dict[int, list[tuple[int, str]]] = {}
    for p, w in words:
        cl_words.setdefault(_clause_of(clauses, p), []).append((p, w))
    cl_neg = {i: any(w in NEGATIONS or w.endswith("n't") for _, w in cl_words.get(i, [])) for i in range(len(clauses))}
    cl_anchor = {i: [(p, ks) for p, w in cl_words.get(i, []) for ks in [_anchor_keys(w, cl_neg[i], numeric_keys)]
                     if ks] for i in range(len(clauses))}

    def borrowable(j):                                # a neighbour clause with words of its own and no number
        return j not in cl_numbers and any(w not in CONJUNCTIONS for _, w in cl_words.get(j, []))

    def has_words(j):
        return any(w not in CONJUNCTIONS for _, w in cl_words.get(j, []))

    # "X of Y" / "X out of Y" inside one clause: Y is the base of X, never bound through X's anchor
    base_of: dict[int, int] = {}
    for t, (_, kind, ref) in enumerate(order):
        if kind != "n":
            continue
        nxt = [o for o in order[t + 1:t + 4]]
        seq = [o[2] if o[1] == "w" else None for o in nxt]
        for skip in (["of"], ["out", "of"]):
            if seq[:len(skip)] == skip and len(nxt) > len(skip) and nxt[len(skip)][1] == "n":
                y = nxt[len(skip)][2]
                if _clause_of(clauses, numbers[y][0]) == _clause_of(clauses, numbers[ref][0]):
                    base_of[y] = ref
    used_anchor: dict[int, int | None] = {}
    for ni in sorted(range(len(numbers)), key=lambda i: numbers[i][0]):
        a, _, d, pct, shown = numbers[ni]
        if d not in nums and not (pct and any(_is_num(v) and 0 <= v <= 1 and _dec(round(v * 100, 1)) == d
                                              for v in flat.values())):
            reasons.append(f"number not in facts: {shown}")
            continue
        if not require_claims:
            continue
        ci = _clause_of(clauses, a)
        ctx = [ci]
        anchors = list(cl_anchor.get(ci, []))
        if not anchors:                               # no anchor here ("Records evaluated - 23 in total"):
            for step in (-1, 1):                      # read the nearest neighbour clause, the label side (left)
                j = ci + step                         # first ("evaluated: 23"), then the right
                while 0 <= j < len(clauses) and not has_words(j):
                    j += step
                if 0 <= j < len(clauses) and borrowable(j):
                    ctx.append(j)
                    anchors += cl_anchor.get(j, [])
                if anchors:
                    break
        if ni in base_of:                             # the base of "X of Y" skips the anchor X was bound through
            anchors = [x for x in anchors if x[0] != used_anchor.get(base_of[ni])]
        me = tok_at[a]
        ranked = [(abs(tok_at[p] - me), ks, p) for p, ks in anchors]
        ctx_words = {w for j in ctx for _, w in cl_words.get(j, [])}
        ca, cb = clauses[min(ctx)][0], clauses[max(ctx)][1]
        why, used, key = _bind_number(d, pct, shown.lstrip().startswith("-"), shown, ranked,
                                 bool(ctx_words & DIRECTION_WORDS), valid, " ".join(text[ca:cb].split()))
        used_anchor[ni] = used
        if why:
            reasons.append(why)
        else:
            bindings.append({"start": a, "end": numbers[ni][1], "shown": shown, "fact_key": key})
    # arithmetic: "X of Y" needs X <= Y; claimed parts never exceed their claimed whole
    if require_claims:
        for y, x in base_of.items():
            if numbers[x][2] > numbers[y][2] and numbers[x][3] == numbers[y][3]:
                reasons.append(f"{NUMBER_ARITHMETIC}: {numbers[x][4]} of {numbers[y][4]}")
        claimed = {c["fact_key"]: c["value"] for c in valid if _is_num(c["value"])}
        for parts, whole in INVARIANTS:
            if whole in claimed and all(p in claimed for p in parts) and \
                    sum(Decimal(str(claimed[p])) for p in parts) > Decimal(str(claimed[whole])):
                reasons.append(f"{NUMBER_ARITHMETIC}: {' + '.join(parts)} exceeds {whole}")

    # 3 / 4 dispositions, read per clause (same clause map and per-clause numbers as test 1)
    cl_negated = {}
    for i, (a, b) in enumerate(clauses):
        ws = [w.lower() for w in WORD.findall(masked[a:b])]
        cl_negated[i] = any(w in NEGATIONS or w.endswith("n't") for w in ws)
    disp = _fact_dispositions(flat)
    for m in re.finditer(r"[A-Za-z]+", masked):
        w = m.group(0).lower()
        canon = DISPOSITION_WORDS.get(w)
        if not canon:
            continue
        ci = _clause_of(clauses, m.start())
        neg = cl_negated.get(ci, False)
        cn = cl_numbers.get(ci, set())
        a, b = clauses[ci]
        shown = " ".join(masked[a:b].split())
        if not require_claims:
            continue                                          # template lint: no polarity reading of template prose
        # 4 polarity against the facts
        if neg and canon in disp:
            reasons.append(f"negated disposition contradicts fact: '{shown}'")
            continue
        if not neg and canon not in disp and OPPOSITES.get(canon, set()) & disp:
            reasons.append(f"disposition '{w}' contradicts fact {sorted(OPPOSITES[canon] & disp)}")
            continue
        # 3 binding to a claim in this clause
        ok, contradicts = False, False
        for c in valid:
            v, key = c["value"], c["fact_key"]
            if isinstance(v, str) and not _str_numbers(v):
                u = v.strip().upper()
                u = DISPOSITION_WORDS.get(u.lower(), u) if u not in OPPOSITES else u
                if u == canon:
                    if neg:
                        contradicts = True
                    else:
                        ok = True
                elif neg and u in OPPOSITES.get(canon, set()):
                    ok = True                                 # "was not released" bound to state HELD
                continue
            if not any(s in key.lower() for s in KEY_STEMS.get(canon, ())):
                continue
            if cn and not (_claim_numbers(v) & cn):
                continue                                      # the clause's numbers belong to other claims
            kneg = _key_negative(key)
            if _truthy(v):
                if neg == kneg:
                    ok = True
                else:
                    contradicts = True
            else:                                             # a zero count
                if neg != kneg or (Decimal(0) in cn):
                    ok = True
                else:
                    contradicts = True
        if contradicts and not ok:
            reasons.append(f"disposition '{w}' contradicts its claim in: '{shown}'")
        elif not ok:
            reasons.append(f"disposition '{w}' is not bound to a claim in: '{shown}'")

    # 5 causal
    has_hyp = any(k.split(".")[0].lower().startswith("hypothesis") and flat[k] not in (None, "", False)
                  for k in flat)
    m = CAUSAL.search(text)
    if m and not has_hyp:
        reasons.append(f"causal wording without a HYPOTHESIS fact: '{m.group(0)}'")
    # 6 advice
    m = ADVICE.search(text)
    if m:
        reasons.append(f"release advice: '{m.group(0)}'")
    # 7 simulated
    if flat.get("explanation_kind") == "nightly_run" and not SIMULATED.search(text):
        reasons.append("a nightly-run explanation must say the night was simulated")
    # 8 quantifiers (provider text only: the template's fixed prose is not a provider claim)
    if require_claims:
        for w in sorted({w.lower() for w in WORD.findall(text)} & QUANTIFIERS):
            reasons.append(f"{QUANTIFIER}: '{w}'")
    return (not reasons), reasons, bindings


# ------------------------------------------------------------------------------------------------ slot form (F-21 r3)
# A provider never writes a number, a disposition / status word or an identifier itself. Its text names facts with
# SLOTS, {fact_key} or {fact_key:SUBJECT}, and render_slots fills them from the facts, so every number and
# disposition word in the shown text comes from a fact by construction. slot_check runs on the RAW text first.
SLOT = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z0-9_]+)*)(?::([A-Za-z0-9][A-Za-z0-9_.:@/-]*))?\}")
SLOT_NOUN = "slotref"                                  # what a slot reads as in the raw-text checks: an opaque noun
QUANTIFIER = "quantifier word"
QUANTIFIERS = {"all", "none", "every", "half", "twice", "double", "majority", "most", "few", "several", "both",
               "many", "thrice", "triple", "quarter", "dozen", "entire", "whole", "minority"}
STATUS_WORDS = {"pending", "error", "errored", "success", "successful", "valid", "invalid", "compliant",
                "noncompliant", "breach", "breached", "within", "exceeded", "exceeds", "outside", "complete",
                "completed", "incomplete", "missing", "late", "ok", "okay", "clean"}
DISPOSITION_PROSE = {"PASS": "passed", "FAIL": "failed", "RELEASED": "released", "HELD": "held", "VETOED": "vetoed",
                     "WITHDRAWN": "withdrawn", "APPROVED": "approved", "REJECTED": "rejected", "DENIED": "denied",
                     "MATCH": "matched", "BREAK": "a break", "MEETS": "meets", "BELOW": "below", "WITHIN": "within",
                     "ERROR": "an error", "CONFIRMED": "confirmed", "VETO": "vetoed"}
SLOT_FAIL = "slot form"


def slots(text: str) -> list[tuple[int, int, str, str | None]]:
    return [(m.start(), m.end(), m.group(1), m.group(2)) for m in SLOT.finditer(text)]


_STATUS_KEYS = {"status", "state", "disposition", "resulting_state", "verdict", "outcome"}


def _slot_value(v, key: str = "") -> str:
    """A slot's text: dispositions as prose (FAIL -> failed), a status-like code (PROPOSED, IN_REVIEW) in lower-case
    words, anything else exactly as the template prints it."""
    from dqagent.llm.template import fmt
    if isinstance(v, str) and v.strip().upper() in DISPOSITION_PROSE:
        return DISPOSITION_PROSE[v.strip().upper()]
    if isinstance(v, str) and key.rsplit(".", 1)[-1] in _STATUS_KEYS and re.fullmatch(r"[A-Z][A-Z_]*", v.strip()):
        return v.strip().lower().replace("_", " ")
    return fmt(v)


def render_slots(text: str, facts: dict) -> str:
    """Fill every slot from the facts (a slot that names no fact raises KeyError: run slot_check first)."""
    flat = flatten(facts)
    return SLOT.sub(lambda m: _slot_value(flat[m.group(1)], m.group(1)), text)


def slot_claims(text: str, facts: dict) -> list[dict]:
    """One claim per slot, its value taken from the facts (the slot IS the citation)."""
    flat = flatten(facts)
    return [{"fact_key": k, "value": flat[k]} for _, _, k, _ in slots(text) if k in flat]


def slot_check(text: str, facts: dict) -> tuple[bool, list[str]]:
    """The primary check of a provider's RAW slot text, before rendering. Every reason starts with "slot form".
      a  no digit outside a slot
      b  no spelled-out number and no quantifier word (all, none, every, half, twice, double, majority, most, ...)
      c  no disposition or status word outside a slot (the synonym list of test 3, plus status words)
      d  every slot names an existing fact_key; a SUBJECT qualifier must be a whole identifier token of the facts;
         no stray brace
      e  causal and advice wording, read with each slot as an opaque noun; a negator in a clause that holds a slot
         (a negated fact) is refused
      f  no identifier-shaped token outside a slot
    Any failure means the caller falls back to the template."""
    reasons: list[str] = []
    if not isinstance(text, str) or not text.strip():
        return False, [f"{SLOT_FAIL}: empty text"]
    flat = flatten(facts)
    toks = _fact_tokens(flat)
    found = slots(text)
    for _, _, key, subj in found:                                                       # d
        if key not in flat:
            reasons.append(f"{SLOT_FAIL}: slot names an unknown fact: {{{key}}}")
        if subj is not None and subj.lower() not in toks:
            reasons.append(f"{SLOT_FAIL}: slot subject not in facts: {subj}")
    opaque = SLOT.sub(SLOT_NOUN, text)
    if "{" in opaque or "}" in opaque:
        reasons.append(f"{SLOT_FAIL}: malformed slot or stray brace")
    for m in re.finditer(r"\d+", opaque):                                                # a
        reasons.append(f"{SLOT_FAIL}: digit outside a slot: {m.group(0)}")
    for m in IDENT.finditer(opaque):                                                     # f
        if _is_ident(m.group(0)):
            reasons.append(f"{SLOT_FAIL}: identifier outside a slot: {m.group(0)}")
    words = [w.lower() for w in WORD.findall(opaque)]
    for a, b, _ in _spelled_spans(opaque, opaque):                                       # b
        reasons.append(f"{SLOT_FAIL}: spelled-out number outside a slot: {opaque[a:b]}")
    for w in sorted(set(words) & QUANTIFIERS):
        reasons.append(f"{SLOT_FAIL}: {QUANTIFIER}: '{w}'")
    for w in sorted({w for w in words if w in DISPOSITION_WORDS or w in STATUS_WORDS}):  # c
        reasons.append(f"{SLOT_FAIL}: disposition or status word outside a slot: '{w}'")
    has_hyp = any(k.split(".")[0].lower().startswith("hypothesis") and flat[k] not in (None, "", False)
                  for k in flat)
    m = CAUSAL.search(opaque)                                                            # e
    if m and not has_hyp:
        reasons.append(f"{SLOT_FAIL}: causal wording without a HYPOTHESIS fact: '{m.group(0)}'")
    m = ADVICE.search(opaque)
    if m:
        reasons.append(f"{SLOT_FAIL}: release advice: '{m.group(0)}'")
    for a, b in _clauses(opaque):
        ws = [w.lower() for w in WORD.findall(opaque[a:b])]
        if SLOT_NOUN in ws and any(w in NEGATIONS or w.endswith("n't") for w in ws):
            reasons.append(f"{SLOT_FAIL}: negation beside a slot: '{' '.join(opaque[a:b].split())}'")
    return (not reasons), reasons


def parse_provider_output(raw: str) -> tuple[str, list] | None:
    """Provider text -> (text, claims). Accepts the JSON object alone or wrapped in a ```json fence. In slot form the
    claims may be omitted (the slots are the claims); any claims given are still checked against the facts."""
    if not isinstance(raw, str):
        return None
    s = raw.strip()
    m = re.search(r"\{.*\}", s, re.DOTALL)
    if not m:
        return None
    try:
        obj = json.loads(m.group(0))
    except ValueError:
        return None
    if not isinstance(obj, dict) or not isinstance(obj.get("text"), str):
        return None
    claims = obj.get("claims", [])
    return obj["text"], claims


# ------------------------------------------------------------------------------------------------ CheckResult (P1-1 §D)
import hashlib as _hashlib                               # noqa: E402 - kept beside its only users
from pathlib import Path as _Path                         # noqa: E402
from typing import NamedTuple as _NamedTuple              # noqa: E402

# a content digest of this module: any change to the check changes the version, so a cached explanation
# checked by an older check is never served without re-checking under the new key (adjudication I1)
# I1: the digest of the check's DEPENDENCY CLOSURE: this module, the facts it reads (flatten, the small-cell and
# rate rules) and the template module whose fmt() renders every slot. A change in any of them re-keys the cache.
CHECK_DEPENDENCIES = ("check.py", "facts.py", "template.py")
CHECK_VERSION = "c-" + _hashlib.sha256(b"".join(
    n.encode() + b"\0" + (_Path(__file__).parent / n).read_bytes() + b"\0" for n in CHECK_DEPENDENCIES)).hexdigest()[:12]


class CheckResult(_NamedTuple):
    ok: bool
    reasons: list[str]
    reason_codes: list[str]          # stable codes, one per reason (REASON_CODES), de-duplicated, in order
    bindings: list[dict]             # [{start, end, shown, fact_key}] number / slot spans of the checked text
    token_sha256: list[str]          # sha256 of each whitespace token of the untrusted text (never the tokens)


# (reason prefix, code): first match wins, so the more specific prefixes come first
REASON_CODES = (
    ("slot form: empty", "EMPTY"), ("slot form: digit", "SLOT_DIGIT"),
    ("slot form: spelled", "SLOT_SPELLED_NUMBER"), ("slot form: quantifier", "QUANTIFIER"),
    ("slot form: disposition", "SLOT_DISPOSITION_WORD"), ("slot form: slot names an unknown", "SLOT_UNKNOWN_FACT"),
    ("slot form: slot subject", "SLOT_UNKNOWN_SUBJECT"), ("slot form: malformed", "SLOT_MALFORMED"),
    ("slot form: identifier", "SLOT_IDENTIFIER"), ("slot form: causal", "CAUSAL"),
    ("slot form: release advice", "ADVICE"), ("slot form: negation", "SLOT_NEGATED"),
    ("number not in facts", "NUMBER_NOT_IN_FACTS"), (NUMBER_UNBOUND, "NUMBER_UNBOUND"),
    (NUMBER_MISBOUND, "NUMBER_MISBOUND"), (NUMBER_AMBIGUOUS, "NUMBER_AMBIGUOUS"), (NUMBER_SIGN, "NUMBER_SIGN"),
    (NUMBER_UNIT, "NUMBER_UNIT"), (NUMBER_ARITHMETIC, "NUMBER_ARITHMETIC"),
    ("identifier not in facts", "IDENT_NOT_IN_FACTS"), ("date not in facts", "DATE_NOT_IN_FACTS"),
    ("date not bound", "DATE_UNBOUND"), ("negated disposition", "DISPOSITION_NEGATED"),
    ("causal wording", "CAUSAL"), ("release advice", "ADVICE"), ("a nightly-run", "NOT_SIMULATED"),
    (QUANTIFIER, "QUANTIFIER"), ("claims missing", "CLAIM_INVALID"), ("malformed claim", "CLAIM_INVALID"),
    ("claim on unknown", "CLAIM_INVALID"), ("claim disagrees", "CLAIM_INVALID"), ("empty text", "EMPTY"),
    ("provider output", "MALFORMED_OUTPUT"),
)


def reason_code(reason: str) -> str:
    for prefix, code in REASON_CODES:
        if reason.startswith(prefix):
            return code
    if reason.startswith("disposition "):
        if "contradicts fact" in reason:
            return "DISPOSITION_CONTRADICTS_FACT"
        if "contradicts its claim" in reason:
            return "DISPOSITION_CONTRADICTS_CLAIM"
        return "DISPOSITION_UNBOUND"
    return "CHECK_OTHER"


def _codes(reasons: list[str]) -> list[str]:
    out: list[str] = []
    for r in reasons:
        c = reason_code(r)
        if c not in out:
            out.append(c)
    return out


def token_hashes(text) -> list[str]:
    if not isinstance(text, str):
        return []
    return [_hashlib.sha256(t.encode()).hexdigest() for t in text.split()][:512]


def check_result(text: str, claims, facts: dict, *, require_claims: bool = True) -> CheckResult:
    """check() with reason codes, the number bindings and the token hashes; check() keeps its tuple form."""
    ok, reasons, bindings = _check(text, claims, facts, require_claims=require_claims)
    return CheckResult(ok, reasons, _codes(reasons), bindings, token_hashes(text))


def render_slots_spans(text: str, facts: dict) -> tuple[str, list[dict]]:
    """render_slots plus the span of each filled slot in the rendered text: [{start, end, shown, fact_key}]."""
    flat = flatten(facts)
    out, spans, pos = [], [], 0
    for m in SLOT.finditer(text):
        out.append(text[pos:m.start()])
        start = sum(len(x) for x in out)
        shown = _slot_value(flat[m.group(1)], m.group(1))
        out.append(shown)
        spans.append({"start": start, "end": start + len(shown), "shown": shown, "fact_key": m.group(1)})
        pos = m.end()
    out.append(text[pos:])
    return "".join(out), spans


def check_slot_result(raw_text: str, claims, facts: dict) -> tuple[CheckResult, str | None]:
    """The provider path: slot_check on the RAW text (primary); when it passes, render the slots and run the
    structured check over the rendered text with each slot as a claim (defence in depth). Returns the result and
    the rendered text (None when the slot form failed). Bindings are the slot spans: slot -> fact_key."""
    ok, reasons = slot_check(raw_text, facts)
    if not ok:
        return CheckResult(False, reasons, _codes(reasons), [], token_hashes(raw_text)), None
    rendered, spans = render_slots_spans(raw_text, facts)
    given = claims if isinstance(claims, list) else ([] if claims is None else [claims])
    ok, reasons, _ = _check(rendered, given + slot_claims(raw_text, facts), facts)
    return CheckResult(ok, reasons, _codes(reasons), spans, token_hashes(raw_text)), rendered
