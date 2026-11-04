"""Shared helpers: dates, money, Luhn/NPI and X12 segment serialisation. Stdlib only."""
from __future__ import annotations

import calendar
import datetime as dt
from decimal import ROUND_HALF_UP, Decimal

OPEN_END = dt.date(9999, 12, 31)
CENT = Decimal("0.01")

# Spine §1.2 delimiters
ELE, SUB, SEGT, REP = "*", ":", "~", "^"


def ymd(d: dt.date) -> str:
    return d.strftime("%Y%m%d")


def parse_ymd(s: str) -> dt.date:
    return dt.datetime.strptime(s, "%Y%m%d").date()


def days(n: int) -> dt.timedelta:
    return dt.timedelta(days=n)


def add_months(d: dt.date, n: int) -> dt.date:
    m = d.month - 1 + n
    y = d.year + m // 12
    m = m % 12 + 1
    return dt.date(y, m, min(d.day, calendar.monthrange(y, m)[1]))


def add_years(d: dt.date, n: int) -> dt.date:
    try:
        return d.replace(year=d.year + n)
    except ValueError:  # Feb 29
        return d.replace(year=d.year + n, day=28)


def money(x) -> Decimal:
    return Decimal(str(x)).quantize(CENT, rounding=ROUND_HALF_UP)


def fmt_money(x: Decimal) -> str:
    return str(Decimal(x).quantize(CENT, rounding=ROUND_HALF_UP))


def luhn_check_digit(payload: str) -> str:
    """Luhn check digit for a numeric payload (check digit not included)."""
    total = 0
    for i, ch in enumerate(reversed(payload)):
        n = int(ch)
        if i % 2 == 0:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return str((10 - total % 10) % 10)


def make_npi(base9: str) -> str:
    """NPI = 9-digit base + Luhn check digit computed over the '80840' prefix + base."""
    return base9 + luhn_check_digit("80840" + base9)


def npi_is_valid(npi: str) -> bool:
    return (len(npi) == 10 and npi.isdigit()
            and luhn_check_digit("80840" + npi[:9]) == npi[9])


def seg_str(elements: list[str]) -> str:
    """Serialise one segment; trailing empty elements are suppressed (X12 rule)."""
    els = [str(e) for e in elements]
    while len(els) > 1 and els[-1] == "":
        els.pop()
    return ELE.join(els) + SEGT


def rand_date(rng, a: dt.date, b: dt.date) -> dt.date:
    if b < a:
        return a
    return a + days(rng.randint(0, (b - a).days))


def rand_ts(rng, d: dt.date) -> dt.datetime:
    return dt.datetime(d.year, d.month, d.day, rng.randint(1, 22), rng.randint(0, 59), rng.randint(0, 59))
