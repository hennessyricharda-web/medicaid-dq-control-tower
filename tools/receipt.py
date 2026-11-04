"""Write a test receipt: run the T0+T1 suites, keep the JUnit XML, and bind it to a hash of the
code under test (the tree is not a git repository, so the hash stands in for a commit SHA).

usage: python -m tools.receipt [--label w1-exit]
output: docs/receipts/<UTC timestamp>_<label>.junit.xml and .json
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# the hashed tree is defined once, in dqagent.obs.tracer (HASHED, code_sha256)


def code_hash() -> str:
    """The code-tree hash. One definition (Part F P1-1): dqagent.obs.tracer.code_sha256, which also stamps every
    correlated control-plane row, so a receipt and the rows it covers name the same version."""
    from dqagent.obs.tracer import code_sha256
    code_sha256.cache_clear()                    # a receipt always hashes the tree as it is NOW
    return code_sha256()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", default="run")
    a = ap.parse_args(argv)
    out = ROOT / "docs" / "receipts"
    out.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    junit = out / f"{stamp}_{a.label}.junit.xml"
    code = code_hash()
    rc = subprocess.call([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests/t0", "tests/t1",
                          "tests/synthetic", f"--junitxml={junit}"], cwd=ROOT)
    suites = ET.parse(junit).getroot()
    s = suites if suites.tag == "testsuite" else suites.find("testsuite")
    counts = {k: int(s.get(k, 0)) for k in ("tests", "failures", "errors", "skipped")}
    receipt = {"label": a.label, "utc": stamp, "code_sha256": code, "pytest_exit": rc, **counts,
               "junit": junit.name, "junit_sha256": hashlib.sha256(junit.read_bytes()).hexdigest(),
               "note": "skipped tests are NOT passes (T1 Postgres unreachable => CP tests skip)"}
    (out / f"{stamp}_{a.label}.json").write_text(json.dumps(receipt, indent=2))
    print(json.dumps(receipt, indent=2))
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
