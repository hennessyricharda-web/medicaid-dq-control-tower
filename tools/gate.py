"""The authoritative gate: a cross-platform exclusive lock, two tree hashes (evidence-identity and
whole-tree freeze check), the full suite, and a receipt that says honestly whether the run is void
and whether the suite actually completed.

usage:
    python -m tools.gate --label <label> [--tower]
    python -m tools.gate --targeted <pytest-path> [<pytest-path> ...]

`--label` runs the full gate (lock, start hashes, suites, end hashes, receipt).
`--targeted` runs only the given pytest paths under the same lock. It writes NO receipt and never
counts as a gate (EXECUTION_PLAN §5.2 step 7); it returns pytest's real exit code.
`--tower` (EXECUTION_PLAN §5.2 step 4, shipped in the K2 gap-fill) additionally runs, inside the
same lock and the same start/end hash window as the Python suite: `npx tsc --noEmit`, `npx eslint
.`, the K4 bundle-size gate (`node scripts/check-bundle.mjs`), `next build` for mock and live
(E16), and `node scripts/smoke.mjs --mode mock` against a
throwaway dev server this tool starts and tears down itself. Any of those steps failing fails the
gate (non-zero exit, receipt carries the failing step and its output tail); none of them is ever
swallowed into a silent pass.

Two hashes, kept separate (EXECUTION_PLAN §5.2):
- `code_sha256` (dqagent.obs.tracer.code_sha256) is the evidence identity: dqagent, rules, config,
  pipelines/w1_dbt/models, tests, and only .py/.yaml/.yml/.sql files. It stamps CP rows; this module
  only reads it, never redefines it.
- `gate_tree_sha256` (this module) is the whole-tree freeze check: a short explicit include-list
  (dqagent, tower/src, tools, synthetic, pipelines, tests, config, rules), every file of every
  extension under those paths, so a Tower edit mid-gate is visible even though it never touches
  `code_sha256`'s narrower scope.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOCK_PATH = ROOT / ".gate.lock"
TOWER_ROOT = ROOT / "tower"

# F-27 (`.claude/agents/tower.md`): `next build`/`next dev` with a non-default NEXT_DIST_DIR
# rewrites these two checked-in files to point their generated-types reference at that dist dir.
# `_run_tower_checks` snapshots both before any step runs and restores them in a `finally`, so a
# Tower build inside the gate never leaves them mutated on disk.
TOWER_RESTORE_FILES = ["tsconfig.json", "next-env.d.ts"]

# The freeze-check include-list (Decision 2, RESOLVED): a short explicit list, not a gitignore
# pattern engine and not a mirror of .gitignore. Walked recursively, every extension, unlike
# code_sha256's narrower .py/.yaml/.yml/.sql scope. Kept in sync with the top-level tree by
# tests/t1/test_gate_tool.py's include-list-drift test.
GATE_TREE_INCLUDE = ["dqagent", "tower/src", "tools", "synthetic", "pipelines", "tests", "config", "rules"]

# Generated/cache dirs that can appear under an included path; never repo content. Matches the
# precedent set in tests/t0/test_generic_naming.py's SKIP_DIRS.
GATE_TREE_SKIP_DIRS = {"node_modules", ".next", "target", "logs", "__pycache__", ".pytest_cache", ".git",
                       ".ruff_cache", ".smoke-shots", ".venv", ".pgdata"}

# Decision 8 (RESOLVED): CRLF normalisation applies ONLY to this declared text-extension set.
# Every other file (images, fonts, other binaries) is hashed as raw bytes, unnormalised.
GATE_TREE_TEXT_EXT = {".py", ".ts", ".tsx", ".ps1", ".yaml", ".yml", ".sql", ".md", ".json", ".toml",
                      ".css", ".mjs", ".cjs"}

SUITE_PATHS = ["tests/t0", "tests/t1", "tests/synthetic", "tests/plugins"]


def _code_hash() -> str:
    """The evidence-identity hash. Delegates to dqagent.obs.tracer.code_sha256, clearing its
    lru_cache(maxsize=1) first — without the clear, a second read in the same process trivially
    equals the first and the void-on-change check becomes a no-op (the single most important
    correctness fact in this tool, per the design)."""
    from dqagent.obs.tracer import code_sha256
    code_sha256.cache_clear()
    return code_sha256()


def _gate_tree_files():
    for top in GATE_TREE_INCLUDE:
        base = ROOT / top
        if not base.exists():
            continue
        for p in sorted(base.rglob("*")):
            if not p.is_file():
                continue
            if set(p.relative_to(ROOT).parts) & GATE_TREE_SKIP_DIRS:
                continue
            yield p


def gate_tree_sha256() -> str:
    """The whole-tree freeze-check hash. Lives only here (not in tracer.py) because tracer.py's
    code_sha256 is explicitly scoped to the evidence-identity tree and stamps CP rows — conflating
    the two would make a Tower-only edit also bump stamped evidence rows, which is wrong.

    Portability (Decision/red-team C2, already fixed upstream in tracer.code_sha256; mirrored
    here): every path is hashed as relative_to(ROOT).as_posix() so Windows and POSIX path
    separators collapse to one representation. Text-extension files (GATE_TREE_TEXT_EXT) are
    CRLF-normalised before hashing; every other file is hashed as raw, unnormalised bytes
    (Decision 8)."""
    h = hashlib.sha256()
    for p in _gate_tree_files():
        rel = p.relative_to(ROOT).as_posix()
        content = p.read_bytes()
        if p.suffix in GATE_TREE_TEXT_EXT:
            content = content.replace(b"\r\n", b"\n")
        h.update(rel.encode() + b"\0" + content + b"\0")
    return h.hexdigest()


class _Lock:
    """Cross-platform exclusive lock on .gate.lock. Decision 4 (RESOLVED): fail-fast, not
    blocking — a second gate while one is held exits immediately with a clear message rather than
    hanging a seat's run for up to an hour."""

    def __init__(self, path: Path):
        self.path = path
        self._fh = None

    def acquire(self) -> None:
        self._fh = open(self.path, "a+b")
        try:
            if sys.platform == "win32":
                import msvcrt
                self._fh.seek(0)
                msvcrt.locking(self._fh.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self._fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self._fh.close()
            self._fh = None
            raise

    def release(self) -> None:
        if self._fh is None:
            return
        try:
            if sys.platform == "win32":
                import msvcrt
                self._fh.seek(0)
                msvcrt.locking(self._fh.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
        finally:
            self._fh.close()
            self._fh = None

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, *exc):
        self.release()
        return False


def _acquire_or_fail() -> _Lock:
    lock = _Lock(LOCK_PATH)
    try:
        lock.acquire()
    except OSError:
        print("gate already running: .gate.lock is held by another process. Not waiting; exit.")
        raise SystemExit(1)
    return lock


def _run_pytest(paths: list[str], junit: Path | None) -> int:
    cmd = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *paths]
    if junit is not None:
        cmd.append(f"--junitxml={junit}")
    return subprocess.call(cmd, cwd=ROOT)


def _junit_counts(junit: Path) -> dict:
    suites = ET.parse(junit).getroot()
    s = suites if suites.tag == "testsuite" else suites.find("testsuite")
    return {k: int(s.get(k, 0)) for k in ("tests", "failures", "errors", "skipped")}


def _run_full_suites(stamp: str, label: str) -> tuple[int, dict, Path, bool]:
    """Runs all three suite paths (tests/t0, tests/t1, tests/synthetic) as ONE combined pytest
    invocation, writing ONE combined JUnit file — same convention as tools/receipt.py:34-40.
    `_junit_counts` reads a single aggregate `<testsuite>` element, so the counts returned here
    (and the receipt's `tests`/`failures`/`errors`/`skipped` fields) are totals across all three
    paths, not attributed per suite: a receipt cannot tell you which of the three paths produced
    a given skip. Per-suite attribution is not implemented in G0."""
    out = ROOT / "docs" / "receipts"
    out.mkdir(parents=True, exist_ok=True)
    junit = out / f"{stamp}_{label}.junit.xml"
    rc = _run_pytest(SUITE_PATHS, junit)
    counts = _junit_counts(junit)
    suite_complete = counts["skipped"] == 0
    return rc, counts, junit, suite_complete


def _write_receipt(out: Path, stamp: str, label: str, receipt: dict) -> Path:
    path = out / f"{stamp}_{label}.json"
    path.write_text(json.dumps(receipt, indent=2))
    return path


def _targeted(paths: list[str]) -> int:
    lock = _acquire_or_fail()
    try:
        return _run_pytest(paths, junit=None)
    finally:
        lock.release()


# ---------------------------------------------------------------------------
# --tower (EXECUTION_PLAN §5.2 step 4): tsc, eslint, next build x2, smoke mock.
# ---------------------------------------------------------------------------

_SMOKE_PORT = 34217  # a high, unlikely-to-collide port -- never the developer's :3000 dev session


def _npx(args: list[str]) -> list[str]:
    """`npx <args>` as a ready-to-Popen command list. On Windows, npx/next/tsc/eslint resolve to
    `.cmd` shims; `subprocess.Popen(["npx", ...])` without `shell=True` fails with WinError 2 (the
    system cannot find the file specified) -- confirmed directly against this repo's
    tower/node_modules. Routing through `cmd /c` resolves the shim the same way a real shell does,
    without needing `shell=True`'s quoting behaviour. On POSIX, npx is a real executable on PATH."""
    if sys.platform == "win32":
        return ["cmd", "/c", "npx", *args]
    return ["npx", *args]


def _run_tower_step(name: str, cmd: list[str], cwd: Path, env: dict, timeout: int) -> tuple[int, str]:
    """Runs one Tower subprocess step, combined stdout+stderr captured. Returns (exit_code,
    output_tail): the tail (last 4000 chars) is what the receipt and any failure message carry, so
    a Tower failure is never swallowed into a silent pass."""
    try:
        r = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout)
        out = (r.stdout or "") + (r.stderr or "")
        return r.returncode, out[-4000:]
    except subprocess.TimeoutExpired as e:
        out = (e.stdout or "") + (e.stderr or "") if (e.stdout or e.stderr) else ""
        return 124, f"TIMEOUT after {timeout}s running {name}\n{out[-4000:]}"
    except FileNotFoundError as e:
        return 127, f"{name}: command not found: {e}"


def _snapshot_tower_restore_files() -> dict[str, bytes]:
    """F-27 (`.claude/agents/tower.md`): `next build`/`next dev` with a non-default NEXT_DIST_DIR
    rewrites tsconfig.json and next-env.d.ts to point their generated-types reference at that dist
    dir -- confirmed directly (building with NEXT_DIST_DIR=.next/gate-check changed both files on
    disk). Snapshot their bytes before any Tower step runs so they can be restored afterward,
    whatever the outcome."""
    return {name: (TOWER_ROOT / name).read_bytes() for name in TOWER_RESTORE_FILES
            if (TOWER_ROOT / name).exists()}


def _restore_tower_files(snapshot: dict[str, bytes]) -> None:
    for name, content in snapshot.items():
        (TOWER_ROOT / name).write_bytes(content)


def _next_build(mode: str) -> tuple[int, str]:
    """`next build` for E16, once for mock and once for live. Each uses its own NEXT_DIST_DIR
    (tower/next.config.mjs reads NEXT_DIST_DIR, gitignored under .next/) so this never collides
    with a live `next dev` session already serving the default `.next` directory on :3000. The
    scratch build output is removed again once the step completes (pass or fail)."""
    env = dict(os.environ)
    env["NEXT_DIST_DIR"] = f".next/gate-{mode}"
    if mode == "mock":
        env["NEXT_PUBLIC_DQ_MOCK"] = "1"
    else:
        env.pop("NEXT_PUBLIC_DQ_MOCK", None)
    cmd = _npx(["next", "build"])
    try:
        return _run_tower_step(f"next build ({mode})", cmd, TOWER_ROOT, env, timeout=900)
    finally:
        shutil.rmtree(TOWER_ROOT / ".next" / f"gate-{mode}", ignore_errors=True)


def _start_mock_dev_server(port: int) -> subprocess.Popen:
    """Starts a throwaway Tower dev server in mock mode (tower/scripts/dev-mock.mjs's own pattern:
    NEXT_PUBLIC_DQ_MOCK=1), on `port`, with its own NEXT_DIST_DIR so it never touches a developer's
    already-running `next dev` session on the default port/dist dir. stdout/stderr go to DEVNULL,
    not PIPE: a long-lived dev server keeps writing request logs, and an undrained PIPE fills and
    deadlocks the child (observed directly while building this function)."""
    env = dict(os.environ)
    env["PORT"] = str(port)
    env["NEXT_DIST_DIR"] = ".next/gate-smoke"
    kwargs: dict = {}
    if sys.platform != "win32":
        kwargs["start_new_session"] = True  # so the whole process group can be killed together
    return subprocess.Popen(
        ["node", "scripts/dev-mock.mjs"], cwd=TOWER_ROOT, env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **kwargs,
    )


def _wait_for_server(url: str, timeout: float = 30.0) -> bool:
    import urllib.request
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as resp:
                if resp.status < 500:
                    return True
        except Exception:
            pass
        time.sleep(0.5)
    return False


def _stop_mock_dev_server(proc: subprocess.Popen) -> None:
    """Kills the whole process tree, not just the direct child: `next dev` forks, and the process
    actually listening on the port is a grandchild (observed directly: `taskkill /PID <child>`
    alone left the listener running). `/T` on Windows and a killed process group on POSIX both
    kill the tree in one call."""
    try:
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                            capture_output=True, text=True, timeout=15)
        else:
            import signal
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except Exception:
        pass
    finally:
        try:
            proc.wait(timeout=10)
        except Exception:
            pass
        shutil.rmtree(TOWER_ROOT / ".next" / "gate-smoke", ignore_errors=True)


def _run_smoke_mock(base_url: str) -> tuple[int, str]:
    cmd = ["node", "scripts/smoke.mjs", "--mode", "mock", "--base", base_url]
    return _run_tower_step("smoke.mjs --mode mock", cmd, TOWER_ROOT, dict(os.environ), timeout=600)


def _run_tower_checks() -> tuple[bool, list[dict]]:
    """Runs the Tower gate steps (EXECUTION_PLAN §5.2 step 4) in order: `npx tsc --noEmit`, `npx
    eslint .`, `next build` for mock and live (E16), and the smoke test in mock, against a
    throwaway dev server this function starts and tears down. Stops at the first failing step (so
    later steps' absence from the step list is itself informative) but every step actually
    attempted is recorded. tsconfig.json/next-env.d.ts are restored in a `finally`, whatever the
    outcome (F-27). Returns (all_ok, steps), steps = [{"step", "rc", "tail"}, ...] in run order."""
    steps: list[dict] = []
    snapshot = _snapshot_tower_restore_files()
    server: subprocess.Popen | None = None
    try:
        env = dict(os.environ)
        for name, cmd in [("tsc --noEmit", _npx(["tsc", "--noEmit"])), ("eslint .", _npx(["eslint", "."]))]:
            rc, tail = _run_tower_step(name, cmd, TOWER_ROOT, env, timeout=300)
            steps.append({"step": name, "rc": rc, "tail": tail})
            if rc != 0:
                return False, steps

        # K4 (EXECUTION_PLAN §6 K4 step 2): bundle-size ratchet against bundle-baseline.json. Runs
        # its own disposable `next build`, restoring tsconfig.json/next-env.d.ts itself (F-27) --
        # independent of the mock/live builds below, so a failure here does not need them to have run.
        rc, tail = _run_tower_step("check-bundle.mjs", ["node", "scripts/check-bundle.mjs"], TOWER_ROOT, env,
                                    timeout=300)
        steps.append({"step": "check-bundle.mjs", "rc": rc, "tail": tail})
        if rc != 0:
            return False, steps

        for mode in ("mock", "live"):
            rc, tail = _next_build(mode)
            steps.append({"step": f"next build ({mode})", "rc": rc, "tail": tail})
            if rc != 0:
                return False, steps

        server = _start_mock_dev_server(_SMOKE_PORT)
        base_url = f"http://localhost:{_SMOKE_PORT}"
        if not _wait_for_server(base_url, timeout=40):
            steps.append({"step": "dev-mock server startup", "rc": 1,
                          "tail": f"server did not answer {base_url} within 40s"})
            return False, steps

        rc, tail = _run_smoke_mock(base_url)
        steps.append({"step": "smoke.mjs --mode mock", "rc": rc, "tail": tail})
        if rc != 0:
            return False, steps

        return True, steps
    finally:
        if server is not None:
            _stop_mock_dev_server(server)
        _restore_tower_files(snapshot)


def _full_gate(label: str, tower: bool) -> int:
    lock = _acquire_or_fail()
    try:
        code_start = _code_hash()
        tree_start = gate_tree_sha256()

        stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        rc, counts, junit, suite_complete = _run_full_suites(stamp, label)

        # Step 4 (EXECUTION_PLAN §5.2): with --tower, also run tsc/eslint/next build x2/smoke,
        # inside the SAME lock hold and the SAME start/end hash window as the Python suite above,
        # so a Tower edit mid-gate is caught by the void check exactly like a dqagent edit is.
        tower_ok = True
        tower_steps: list[dict] = []
        if tower:
            tower_ok, tower_steps = _run_tower_checks()
            if not tower_ok:
                failing = next((s for s in tower_steps if s["rc"] != 0), tower_steps[-1] if tower_steps else None)
                if failing is not None:
                    print(f"TOWER STEP FAILED: {failing['step']} (exit {failing['rc']})")
                    print(failing["tail"])

        # Computed before the final hash reads below, precisely so nothing but the hash reads
        # and the write itself sit in the window Decision 7 is about.
        junit_sha256 = hashlib.sha256(junit.read_bytes()).hexdigest()

        # Decision 7 (RESOLVED): minimize the window between the final hash read and the receipt
        # write. No other file I/O happens between these two lines and the write below (the
        # junit hash above is computed before this point, not after), under the same lock hold.
        code_end = _code_hash()
        tree_end = gate_tree_sha256()

        out = ROOT / "docs" / "receipts"
        void = (code_end != code_start) or (tree_end != tree_start)
        receipt = {
            "label": label,
            "utc": stamp,
            "code_sha256": code_end,
            "gate_tree_sha256": tree_end,
            "void": void,
            "suite_complete": suite_complete,
            "pytest_exit": rc,
            **counts,
            "junit": junit.name,
            "junit_sha256": junit_sha256,
            "tower": tower,
            "tower_ok": tower_ok,
            "tower_steps": tower_steps,
            "note": ("skipped tests are NOT passes (T1 Postgres unreachable => CP tests skip); "
                     "the lock guarantees no second gate run interleaves; it does not guarantee "
                     "no file write lands in the few milliseconds between the final hash read "
                     "and this receipt's write."),
        }
        _write_receipt(out, stamp, label, receipt)
        print(json.dumps(receipt, indent=2))

        if void:
            print("tree changed during gate: freeze and re-run")
            return 1
        if tower and not tower_ok:
            return 1
        return rc
    finally:
        lock.release()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", default=None)
    ap.add_argument("--tower", action="store_true")
    ap.add_argument("--targeted", nargs="+", default=None, metavar="PATH")
    a = ap.parse_args(argv)

    if a.targeted is not None:
        return _targeted(a.targeted)

    label = a.label or "run"
    return _full_gate(label, a.tower)


if __name__ == "__main__":
    raise SystemExit(main())
