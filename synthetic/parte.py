"""Part E synthetic data (docs/rules/PARTE_DESIGN.md §1.5, §3.3, §6). Additive: never regenerates w1.

    python -m synthetic.parte w1-extras [--verify-dp <w1 data-plane duckdb>]
    python -m synthetic.parte w2-earn

w1-extras writes, BESIDE the existing synthetic/out/w1 files (which are left byte-identical):
    legacy_totals.json    generic legacy-system totals for the w1 period {system, period, as_of, rows}
                          with exactly one planted TIMING break and one planted $ break (plus a WITHIN row)
    monitor_history.json  12 prior aggregate periods for the w1 relations (monitor_history/1)
    parte_manifest.json   the planted-signal / planted-break manifest and the w1 observed aggregates

The w1 period's own values are NOT invented here. They are the aggregates of the w1 data plane after the E0 load
(420 RELEASED / 291 HELD / 47 PENDING_DEPENDENCY), computed by dqagent.agent.monitor.query_aggregates and pinned
below as W1_OBSERVED. `--verify-dp` recomputes them from a data plane and refuses to write on any difference.
The planted signals are produced by the HISTORY: e.g. the volume drop is a history centred well above the w1
row count, so w1's real count falls below the lower limit.

w2-earn writes synthetic/out/w2_earn: a realistic-mode run (no seeded defects) with its own seed, used by the
earning scenario (§1.5), plus earn_manifest.json naming the batches of each phase. Aggregate-safe: the manifest
holds file names, counts and phase labels only, never a member value.

Stdlib only (the optional --verify-dp imports the package's monitor helper and duckdb).
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

PKG_DIR = Path(__file__).resolve().parent
OUT = PKG_DIR / "out"
PERIOD = "2026-Q1"
AS_OF = "2026-04-01"                    # the last w1 delivery (ie_extract_20260401)
PRIOR_PERIODS = [f"{y}-Q{q}" for y in (2023, 2024, 2025) for q in (1, 2, 3, 4)]
LEGACY_SYSTEM = "LEGACY_MMIS"
TOLERANCE = {"records": 0, "amount": 0.005, "rule_failed": 0}

# Which aggregates the MONITOR seat computes per w1 relation (element ids resolve through the state bindings).
SPEC = {
    "bronze.encounter_header": {"rows": True, "amount": ["total_charge", "mco_paid"], "mix": ["CLM.POS"]},
    "bronze.encounter_line": {"rows": True, "amount": ["line_charge", "paid_amt"]},
    "bronze.ie_segment": {"rows": True, "mix": ["ELG.PROGRAM", "ELG.SEGMENT_TYPE"]},
    "bronze.enrollment_834": {"rows": True, "null_rate": ["ENR.COV_END"], "mix": ["ENR.INS03"]},
}
BINDINGS = {"CLM.POS": "pos", "ELG.PROGRAM": "program_code", "ELG.SEGMENT_TYPE": "segment_type",
            "ENR.COV_END": "coverage_end", "ENR.INS03": "ins03"}

# w1 data-plane aggregates for PERIOD (query_aggregates over synthetic/out/w1 loaded by load_synthetic; 2026-10-23).
W1_OBSERVED = [
    ("bronze.encounter_header", "amount:mco_paid", "", 36158.36, 164),
    ("bronze.encounter_header", "amount:total_charge", "", 50179.41, 164),
    ("bronze.encounter_header", "mix:CLM.POS", "02", 0.079268, 164),
    ("bronze.encounter_header", "mix:CLM.POS", "11", 0.707317, 164),
    ("bronze.encounter_header", "mix:CLM.POS", "12", 0.054878, 164),
    ("bronze.encounter_header", "mix:CLM.POS", "50", 0.103659, 164),
    ("bronze.encounter_header", "mix:CLM.POS", "99", 0.054878, 164),
    ("bronze.encounter_header", "rows", "", 164, 164),
    ("bronze.encounter_line", "amount:line_charge", "", 49864.4, 311),
    ("bronze.encounter_line", "amount:paid_amt", "", 36158.36, 311),
    ("bronze.encounter_line", "rows", "", 311, 311),
    ("bronze.ie_segment", "mix:ELG.PROGRAM", "MEDICAID", 0.780851, 470),
    ("bronze.ie_segment", "mix:ELG.PROGRAM", "SNAP", 0.219149, 470),
    ("bronze.ie_segment", "mix:ELG.SEGMENT_TYPE", "FULL", 0.944681, 470),
    ("bronze.ie_segment", "mix:ELG.SEGMENT_TYPE", "PARTIAL", 0.02766, 470),
    ("bronze.ie_segment", "mix:ELG.SEGMENT_TYPE", "WAIVER", 0.02766, 470),
    ("bronze.ie_segment", "rows", "", 470, 470),
    ("bronze.enrollment_834", "mix:ENR.INS03", "001", 0.017241, 174),
    ("bronze.enrollment_834", "mix:ENR.INS03", "021", 0.068966, 174),
    ("bronze.enrollment_834", "mix:ENR.INS03", "024", 0.068966, 174),
    ("bronze.enrollment_834", "mix:ENR.INS03", "025", 0.04023, 174),
    ("bronze.enrollment_834", "mix:ENR.INS03", "030", 0.804598, 174),
    ("bronze.enrollment_834", "null_rate:ENR.COV_END", "", 0.913793, 174),
    ("bronze.enrollment_834", "rows", "", 174, 174),
]
W1_834_LATE_FILE_ROWS = 34          # rows of bronze.enrollment_834 from 834_ZZBROKER_20260209_2 (the TIMING break)

# Planted monitor signals (relation, metric, key, rule, direction) and the history design that plants them.
PLANTED_SIGNALS = [
    ("bronze.encounter_header", "rows", "", "R1", "LOW", "volume drop"),
    ("bronze.encounter_header", "amount:total_charge", "", "R1", "LOW", "volume drop (charges follow rows)"),
    ("bronze.encounter_header", "amount:mco_paid", "", "R1", "LOW", "volume drop (paid follows rows)"),
    ("bronze.enrollment_834", "null_rate:ENR.COV_END", "", "R1", "HIGH", "null-rate jump"),
    ("bronze.ie_segment", "mix:ELG.PROGRAM", "", "PSI", "SHIFT", "mix shift"),
]
VOLUME_RATIO = 0.70                 # history centre = w1 value / ratio (w1 is ~30% below normal)
NULL_RATE_HISTORY = 0.78            # history centre for ENR.COV_END null share (w1 is 0.9138)
PROGRAM_MIX_HISTORY = {"MEDICAID": 0.50, "SNAP": 0.50}
PAST_SIGNAL = ("bronze.encounter_line", "rows", "", "2024-Q3", 1.45)   # an old spike, signalled, so excluded


def observed_rows() -> list[dict]:
    return [{"period": PERIOD, "relation": r, "metric": m, "key": k, "value": v, "n": n}
            for r, m, k, v, n in W1_OBSERVED]


def _obs(relation, metric, key=""):
    return next(v for r, m, k, v, _ in W1_OBSERVED if (r, m, k) == (relation, metric, key))


def _n(relation):
    return next(n for r, m, k, v, n in W1_OBSERVED if r == relation and m == "rows")


def _largest_remainder(shares: dict[str, float], n: int) -> dict[str, int]:
    raw = {c: s * n for c, s in shares.items()}
    out = {c: int(v) for c, v in raw.items()}
    rest = n - sum(out.values())
    for c in sorted(raw, key=lambda c: (-(raw[c] - out[c]), c))[:rest]:
        out[c] += 1
    return out


def _noise(rng: random.Random, k: int, sd: float, centre_shift: float) -> list[float]:
    """k gaussian draws re-centred to mean centre_shift (deterministic for the rng)."""
    xs = [rng.gauss(0.0, sd) for _ in range(k)]
    m = sum(xs) / k
    return [x - m + centre_shift for x in xs]


def build_history(seed: int = 2026) -> dict:
    rng = random.Random(f"parte:monitor:{seed}")
    rows: list[dict] = []
    signalled: list[dict] = []
    planted = {(r, m, k) for r, m, k, *_ in PLANTED_SIGNALS}
    k = len(PRIOR_PERIODS)
    for rel in sorted(SPEC):
        spec = SPEC[rel]
        n_w1 = _n(rel)
        drop = (rel, "rows", "") in planted
        centre_n = n_w1 / VOLUME_RATIO if drop else n_w1
        ns = [max(1, round(centre_n * (1 + e))) for e in _noise(rng, k, 0.04, 0.005)]
        past = PAST_SIGNAL if PAST_SIGNAL[0] == rel else None
        if past:
            ns[PRIOR_PERIODS.index(past[3])] = round(ns[PRIOR_PERIODS.index(past[3])] * past[4])
            for metric in ["rows"] + [f"amount:{c}" for c in spec.get("amount", [])]:
                signalled.append({"period": past[3], "relation": rel, "metric": metric, "key": "", "rule": "R1"})
        for p, n in zip(PRIOR_PERIODS, ns):
            rows.append({"period": p, "relation": rel, "metric": "rows", "key": "", "value": n, "n": n})
        for col in spec.get("amount", []):
            metric = f"amount:{col}"
            per_row = _obs(rel, metric) / n_w1
            es = _noise(rng, k, 0.02, 0.0)
            for p, n, e in zip(PRIOR_PERIODS, ns, es):
                rows.append({"period": p, "relation": rel, "metric": metric, "key": "",
                             "value": round(n * per_row * (1 + e), 2), "n": n})
        for el in spec.get("null_rate", []):
            metric = f"null_rate:{el}"
            centre = NULL_RATE_HISTORY if (rel, metric, "") in planted else _obs(rel, metric)
            for p, n, e in zip(PRIOR_PERIODS, ns, _noise(rng, k, 0.01, 0.0)):
                nulls = min(n, max(0, round(n * (centre + e))))
                rows.append({"period": p, "relation": rel, "metric": metric, "key": "",
                             "value": round(nulls / n, 6), "n": n})
        for el in spec.get("mix", []):
            metric = f"mix:{el}"
            obs = {kk: v for r, m, kk, v, _ in W1_OBSERVED if r == rel and m == metric}
            base = PROGRAM_MIX_HISTORY if (rel, metric, "") in planted else obs
            for p, n in zip(PRIOR_PERIODS, ns):
                jit = {c: max(0.0, s * (1 + rng.gauss(0.0, 0.03))) for c, s in sorted(base.items())}
                tot = sum(jit.values())
                counts = _largest_remainder({c: v / tot for c, v in jit.items()}, n)
                for c in sorted(counts):
                    rows.append({"period": p, "relation": rel, "metric": metric, "key": c,
                                 "value": round(counts[c] / n, 6), "n": n})
    rows.sort(key=lambda r: (r["relation"], r["metric"], r["key"], r["period"]))
    return {"schema": "monitor_history/1", "state": "ZZ", "run_id": "w1", "period_grain": "quarter",
            "current_period": PERIOD, "spec": SPEC, "bindings": BINDINGS, "periods": PRIOR_PERIODS,
            "metrics": rows, "signalled": signalled,
            "note": ("Synthetic aggregate history (no row values). The current period is computed from the data "
                     "plane by the MONITOR seat; signals are planted through this history (see parte_manifest.json).")}


def build_legacy_totals() -> tuple[dict, list[dict]]:
    """(legacy_totals document, expected comparison per row)."""
    rows, expected = [], []

    def add(rel, measure, new, legacy, status, planted=None):
        rows.append({"relation": rel, "measure": measure, "value": legacy})
        expected.append({"relation": rel, "measure": measure, "legacy": legacy, "new": new, "status": status,
                         **({"planted_as": planted} if planted else {})})
    add("bronze.ie_segment", "records", 470, 470, "MATCH")
    add("bronze.enrollment_834", "records", 174, 174 - W1_834_LATE_FILE_ROWS, "BREAK", "TIMING")
    add("bronze.encounter_header", "records", 164, 164, "MATCH")
    add("bronze.encounter_header", "amount:total_charge", 50179.41, 51429.41, "BREAK", "AMOUNT")
    add("bronze.encounter_header", "amount:mco_paid", 36158.36, 36200.00, "WITHIN")
    add("bronze.encounter_line", "records", 311, 311, "MATCH")
    add("bronze.encounter_line", "amount:line_charge", 49864.4, 49864.40, "MATCH")
    add("bronze.encounter_line", "amount:paid_amt", 36158.36, 36158.36, "MATCH")
    doc = {"system": LEGACY_SYSTEM, "period": PERIOD, "as_of": AS_OF,
           "rows": sorted(rows, key=lambda r: (r["relation"], r["measure"]))}
    return doc, sorted(expected, key=lambda r: (r["relation"], r["measure"]))


def build_manifest(history: dict, legacy_expected: list[dict]) -> dict:
    planted = [{"relation": r, "metric": m, "key": k, "rule": rule, "direction": d, "planted_as": why}
               for r, m, k, rule, d, why in PLANTED_SIGNALS]
    pk = {(p["relation"], p["metric"], p["key"]) for p in planted}
    series = sorted({(r["relation"], r["metric"], "" if r["metric"].startswith("mix:") else r["key"])
                     for r in history["metrics"]})
    clean = [{"relation": r, "metric": m, "key": k} for r, m, k in series if (r, m, k) not in pk]
    clean_relations = sorted({c["relation"] for c in clean} - {p["relation"] for p in planted})
    return {"schema": "parte_manifest/1", "run_id": "w1", "period": PERIOD, "as_of": AS_OF,
            "w1_observed": observed_rows(),
            "w1_observed_source": ("dqagent.agent.monitor.query_aggregates over the w1 data plane after "
                                   "dqagent.cli.main.load_synthetic (E0: 420 RELEASED / 291 HELD / 47 PENDING)"),
            "monitor": {"expected_signals": planted, "clean_series": clean,
                        "clean_relations": clean_relations,
                        "excluded_history": history["signalled"]},
            "parallel_run": {"tolerance": TOLERANCE, "expected": legacy_expected}}


def _dump(obj) -> bytes:
    return (json.dumps(obj, indent=2, sort_keys=True) + "\n").encode("ascii")


def verify_dp(dp_path: str) -> None:
    import duckdb

    from dqagent.agent.monitor import query_aggregates
    con = duckdb.connect(dp_path, read_only=True)
    got = []
    for rel in sorted(SPEC):
        got += query_aggregates(con, rel, SPEC[rel], BINDINGS, period=PERIOD)
    key = lambda r: (r["relation"], r["metric"], r["key"])
    want = sorted(observed_rows(), key=key)
    if sorted(got, key=key) != want:
        raise SystemExit("w1 data-plane aggregates differ from W1_OBSERVED; refusing to write")
    n834 = con.execute("SELECT count(*) FROM bronze.enrollment_834 WHERE _dq_batch_id = "
                       "'B-834_ZZBROKER_20260209_2'").fetchall()[0][0]
    if n834 != W1_834_LATE_FILE_ROWS:
        raise SystemExit(f"late 834 file has {n834} rows, expected {W1_834_LATE_FILE_ROWS}")


def write_w1_extras(out_root: Path = OUT, seed: int = 2026) -> dict:
    d = Path(out_root) / "w1"
    if not d.is_dir():
        raise SystemExit(f"{d} does not exist; w1 must be generated first (this command never generates it)")
    history = build_history(seed)
    legacy, expected = build_legacy_totals()
    manifest = build_manifest(history, expected)
    files = {"monitor_history.json": history, "legacy_totals.json": legacy, "parte_manifest.json": manifest}
    for name, obj in files.items():
        (d / name).write_bytes(_dump(obj))
    return {"out_dir": d, "files": sorted(files)}


# ------------------------------------------------------------------------------------------------ w2_earn
EARN = {"run_id": "w2_earn", "seed": 4242, "start": "20260401", "months": 2, "persons": 80}


def _ie_medicaid_persons(path: Path) -> int:
    """Distinct MEDICAID person keys in one IE extract (a count only; unit = person x authority)."""
    keys = set()
    for line in path.read_text(encoding="ascii").splitlines():
        f = line.split("|")
        if f[0] == "DTL" and len(f) > 8 and f[7] in ("MEDICAID", "MCHIP"):
            keys.add(f[1])
    return len(keys)


def write_w2_earn(out_root: Path = OUT, commit_log=None) -> dict:
    from .generate import generate
    r = generate(EARN["run_id"], "realistic", EARN["seed"], EARN["start"], EARN["months"], out_root, commit_log,
                 EARN["persons"], "w1")
    d = r["out_dir"]
    ies = sorted(d.glob("ie_extract_*.txt"))
    counts = [{"file": p.name, "batch_id": "B-" + p.stem, "medicaid_person_units": _ie_medicaid_persons(p)}
              for p in ies]
    # phases over the IE (eligibility) batches, in arrival order:
    #   1 APPROVE  the first batches are human-approved at L2 (track record accrues)
    #   2 PROMOTE  the maker proposes L2 -> L3 with the track record; two State approvers sign
    #   3 VETO     the next batch with >= 2 MEDICAID units opens veto windows: veto one, let one release
    #   4 LOOKBACK a look-back WITHDRAWN on a unit released at L3 demotes again
    approve = [c for c in counts[:-1] if c["medicaid_person_units"] >= 1][:3]
    rest = [c for c in counts if c not in approve and counts.index(c) > counts.index(approve[-1])]
    veto = next(c for c in rest if c["medicaid_person_units"] >= 2)
    manifest = {"schema": "earn_manifest/1", "run_id": EARN["run_id"], "mode": "realistic", **EARN,
                "authority": "AUTH_MEDICAID", "agent": "dq-reconciliation", "action_class": "RELEASE",
                "start_tier": "L2", "promote_to": "L3",
                "ie_batches": counts,
                "phases": [{"phase": "APPROVE", "batches": [c["batch_id"] for c in approve]},
                           {"phase": "PROMOTE", "after": approve[-1]["batch_id"], "approvers_required": 2},
                           {"phase": "VETO", "batches": [veto["batch_id"]],
                            "expect": "one unit vetoed (auto-demote to L2), one released at the window deadline"},
                           {"phase": "LOOKBACK", "on": veto["batch_id"],
                            "expect": "WITHDRAWN on the L3-released unit demotes to L2"}],
                "note": "Aggregate-safe: file names, batch ids and counts only."}
    (d / "earn_manifest.json").write_bytes(_dump(manifest))
    return {"out_dir": d, "manifest": manifest, "ledger_sha256": r["ledger_sha256"]}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m synthetic.parte", description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    a1 = sub.add_parser("w1-extras")
    a1.add_argument("--verify-dp", default=None, help="w1 data-plane DuckDB to re-check W1_OBSERVED against")
    a1.add_argument("--out-root", default=None)
    a2 = sub.add_parser("w2-earn")
    a2.add_argument("--out-root", default=None)
    a2.add_argument("--commit-log", default=None)
    a = ap.parse_args(argv)
    if a.cmd == "w1-extras":
        if a.verify_dp:
            verify_dp(a.verify_dp)
        r = write_w1_extras(Path(a.out_root) if a.out_root else OUT)
        print(f"out_dir={r['out_dir']} files={','.join(r['files'])}")
    else:
        r = write_w2_earn(Path(a.out_root) if a.out_root else OUT, a.commit_log)
        print(f"out_dir={r['out_dir']} ledger_sha256={r['ledger_sha256']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
