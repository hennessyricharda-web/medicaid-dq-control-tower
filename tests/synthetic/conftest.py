import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
for p in (str(ROOT), str(HERE)):
    if p not in sys.path:
        sys.path.insert(0, p)

from synthetic.generate import generate  # noqa: E402

ARGS = dict(seed=42, start="20260101", months=3)


@pytest.fixture(scope="session")
def commit_log(tmp_path_factory):
    return tmp_path_factory.mktemp("commit") / "LEDGER_COMMITMENTS.log"


@pytest.fixture(scope="session")
def realistic_run(tmp_path_factory, commit_log):
    return generate("w1r", "realistic", out_root=tmp_path_factory.mktemp("out"), commit_log=commit_log, **ARGS)


@pytest.fixture(scope="session")
def seeded_run(tmp_path_factory, commit_log):
    return generate("w1", "seeded", out_root=tmp_path_factory.mktemp("out"), commit_log=commit_log, **ARGS)
