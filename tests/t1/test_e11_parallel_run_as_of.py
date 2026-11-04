"""E-11 (K6 Fix 1, confirmed sound by the before-build red team round 1, blackboard "K6 phase-1 before-build red
team"): `run_night`'s parallel-run call at the last night (runtime.py, inside `_run_night`) must compare the new
side against the SIMULATED clock this whole night runs under (`night_date`, per `run_night`'s own docstring point 4:
"missing deliveries are checked against the SIMULATED clock"), never the legacy fixture's own stated `as_of`.

Before the fix, `self.load_legacy_totals(data / "legacy_totals.json")` passed no `as_of`, so
`load_legacy_totals` (runtime.py) defaulted to the fixture file's own `doc["as_of"]`. The fix passes
`as_of=night_date` explicitly.

synthetic/out/w1's last night happens to coincide with legacy_totals.json's own `as_of` (both 2026-04-01), so
running the ordinary last night of w1 would pass even with the bug. This test manufactures the mismatch directly
by running an EARLIER night (still inside the same parallel-run period) with `last=True, parte=True` forced, so
the fixture's own `as_of` and the simulated night_date are different dates.

`legacy_totals.compare()` (dqagent/adapters/metadata/legacy_totals.py) itself hard-refuses any as_of mismatch
between the legacy doc and the comparison being requested (a real, correct guard: never silently compare a
legacy snapshot against the wrong period) — so the PROOF the fix works is not that mismatched rows get stored
(compare() never lets that happen), it's that the refusal's own error message names `night_date`, not the
fixture's stale `as_of`. Before the fix, `as_of` always resolved to the fixture's OWN `as_of`, which trivially
matches itself, so compare() would have proceeded silently with no error at all — using the fixture's self-
declared date regardless of which night actually ran, the exact silent-wrong-answer bug E-11 exists to close.
After the fix, requesting a comparison at `night_date` against a fixture declared for a different date is
correctly, loudly refused, and the refusal's message is what confirms `night_date` (not `doc["as_of"]`) is what
is actually being compared.
"""
import json
from pathlib import Path

import pytest

from dqagent.runtime import seed_routing, synthetic_nights

from .w1_harness import Slice

ROOT = Path(__file__).resolve().parents[2]
W1 = ROOT / "synthetic" / "out" / "w1"


def test_parallel_run_as_of_matches_sim_clock(fresh_cp_dsn):
    rt = Slice(fresh_cp_dsn)
    nights = synthetic_nights(W1, seed_routing("ZZ"))
    fixture_as_of = json.loads((W1 / "legacy_totals.json").read_text())["as_of"]
    night_date, files = next((n, fs) for n, fs in nights if n != fixture_as_of)
    assert night_date != fixture_as_of, "the fixture needs a night that actually differs from its own as_of"

    # compare() correctly refuses the mismatch it was asked to make — the refusal naming night_date (not the
    # fixture's own stale as_of) is the proof that night_date is what load_legacy_totals now passes through.
    with pytest.raises(ValueError, match=f"not .* as of {night_date}"):
        rt.run_night(W1, night_date, files, last=True, parte=True)
