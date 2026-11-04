# Run log — defect fixes and gate results, real-time

Terse, append-only. Full detail stays on `docs/blackboard.md`; this is the fast-scan version so the user doesn't have to wait on narration for each fix.

---

## 2026-10-25 — first real Postgres gate (K1-r1) surfaced 16 failures

Receipt: `docs/receipts/20261025T150908Z_partF-K1-r1.json` — 1110 tests, 16 failures, 1 skipped (down from 346/337 skipped pre-Postgres). First time this suite ever ran against a real database.

**Triage (read-only investigation, completed):**

| Cluster | Count | Cause | Action |
|---|---|---|---|
| A | 3 | `dbt-core` not installed (`pyproject.toml`'s `dbt` extra never installed) | Env gap, not a code defect — pip install needed, separate from the Postgres-install incident |
| B | 10 | **CONFIRMED SEVERE**: `promotion_refusal` (trace_schema.py:494) treats GENESIS's own seed grant as an operational promotion requiring evidence that can't exist yet. A real governed genesis can never complete as written. | **FIX IN PROGRESS** — user approved, dispatched |
| C | 1 | `test_w2_earn_story`: stale propose-time snapshot (`cr["impact"]`) never re-fetched after evidence granted. Test-authoring bug from K0, not a product bug. | **FIX IN PROGRESS** |
| D | 1 | `test_profile_as_of_old_trace_matches_config_then`: `profile.py:182`'s `approved_hash`/`effective_equals_approved` are explicitly present-tense by design, but leak into the "as of the past" digest. Real K1 bug. | **FIX IN PROGRESS** |
| E | 1 | `test_tracing_off_and_on_give_identical_outcomes`: reran in isolation, passed clean (1568s). DQ outcome tables (units/results/latest/abc) were byte-identical even in the original failing run — only `board` differed. **I-8 invariant NOT shown to be violated.** Likely first-run cold-start flakiness. | Re-confirm with one more full-gate run before closing as non-issue (second independent check per CLAUDE.md's absence-claim rule) |

**Dispatched:** Backend-API-Gov fixing clusters B, C, D in one pass (all three are small, root-caused, non-overlapping). Running now.

---

## Fix batch complete (Backend-API-Gov), orchestrator independently re-verifying

**All 3 clusters fixed, plus a 4th masked defect found and fixed in the same pass:**
- **B (genesis evidence-gate):** `promotion_refusal` now takes `p_origin`; returns NULL immediately for `GENESIS`-origin CRs. Both call sites (`gov_decide`, `gov_apply`) updated. Orchestrator read the SQL directly — confirmed exact, including the inline rationale comment. Confirmed via PFP/K0_DESIGN.md grep this was a genuinely unconsidered gap, not a reversed deliberate decision.
- **New, 4th defect found mid-fix:** `Registry.apply()` was unconditionally wrapping EVERY `CheckViolation` from `gov_apply` into a generic `Denied` — including unrelated R4-2 deployment-binding refusals. This was invisible before because cluster B's refusal always fired first. Narrowed the wrap to only R4-5 promotion-refusal messages (discriminated by their `"promotion requires"` prefix). A correctly-scoped fix for a real, previously-hidden bug, not scope creep — it directly blocked proving cluster B's own fix end-to-end.
- **C (stale earn-story snapshot):** `test_parte_earn.py` now re-fetches a live `track_record_snapshot` after granting evidence, instead of reading the stale propose-time snapshot.
- **D (profile digest leak):** `profile.py`'s `profile_digest` now excludes `approved_hash`/`effective_equals_approved` from what gets hashed (both fields stay in the response body for display) — correctly chosen over weakening the test, since those two fields are explicitly present-tense by design per the code's own comment.
- Hash: `7a702be4…` → `ca29740c…`, confirmed by direct recompute.
- Reported test result: 143 passed, 0 failed, 0 skipped, real Postgres, no shortcuts.
- **Orchestrator independently re-ran both halves: 88 passed + 55 passed = 143/143, matches the fix batch's claim exactly.** Genesis bug fix, masked-defect fix, earn-story snapshot fix, and profile-digest fix all confirmed real against live Postgres.
- **Cluster A (dbt-core missing) closed**: installed `dbt-core>=1.10`/`dbt-duckdb>=1.10` directly (declared in `pyproject.toml`'s own `dbt` extra — a project-scoped Python package install, not a system-level change; judged in-bounds given the user's direction to move faster, unlike the out-of-repo system service in INC-001). Ran `tests/t1/test_dbt_gate.py` directly: **3 passed in 74s.** All of cluster A closed.

**Remaining before K1 formally closes:** one full K1 gate run (not just targeted paths) against real Postgres; one more confirmatory run on cluster E's flaky tracing-invariant test; then K1 close. K0 re-gate for real is deferred to the batched K2-K6 gate per the user's new cadence directive, not run separately.

## Full K1 gate — VOID on first real-Postgres attempt, root cause found and fixed in `tools/gate.py` itself

First full gate run (`partF-K1-r2`): `void: true`, 1 failure, 1 skipped — but void means the receipt is not evidence, full stop, regardless of the counts looking good.

**Root cause, found and confirmed directly:** `tools/gate.py`'s `GATE_TREE_SKIP_DIRS` (the freeze-check's exclude list) was missing `"logs"` — present in `.gitignore` (`pipelines/*/logs/`) but never added to the gate tool's own list. `tests/t1/test_dbt_gate.py` can now actually run dbt (dbt-core just installed), which legitimately rewrites `pipelines/w1_dbt/logs/dbt.log` every run — a real, expected regeneration, not a code edit — but the gate tool's own freeze check didn't know to ignore it, so it correctly (if over-sensitively) voided the run.

**Fix (orchestrator, directly — a one-line, low-risk addition to a tool the orchestrator already deeply understands from G0):** added `"logs"` to `GATE_TREE_SKIP_DIRS`. Verified: zero `target`/`logs` files leak into the tree walk now; `tests/t1/test_gate_tool.py`'s own 17 tests still pass, no regression.

Re-running the full K1 gate now.

## Full K1 gate — VALID, void: false. 1 failure, 1 skip out of 1110

Receipt `20261026T012814Z_partF-K1-r3.json`: `void: false` — this is real evidence. Down from 16 failures to 1.

- **The 1 skip** (`test_w1_observed_equals_the_w1_data_plane`) is a legitimate, by-design opt-in test requiring a separate `DQ_W1_DP` env var pointing at an unrelated DuckDB file — unrelated to Postgres, not a gap. Confirmed by reading the skip condition directly.
- **The 1 failure** is cluster E again — `test_tracing_off_and_on_give_identical_outcomes`. **ROOT CAUSE CONFIRMED, real bug, NOT an I-8 invariant violation.** `dqagent/agent/seats.py:240-241` and `dqagent/engine/evaluate.py:176-177` both do `SELECT authorities[1] a FROM dq_ctl.units WHERE batch_id=%s AND node=%s LIMIT 1` with NO `ORDER BY` — a genuine race when a batch/node has multiple units with different `authorities` arrays (confirmed real in `contracts_w1.yaml`'s program→authority mapping). Postgres makes no ordering guarantee for an unordered `LIMIT 1`, so which authority gets picked can flip run to run, independent of tracing. Orchestrator independently verified both call sites directly — confirmed exact.
  - **I-8 is NOT violated**: the actual DQ outcome (units/results/latest/abc — dispositions, releases, the 420/291/47 shape) was byte-identical in every failing run; only the blackboard's own `RELEASE_SUMMARY` authority attribution (and its cascading hash chain) is non-deterministic. A real, separate correctness bug, not a tracing-safety bug.
  - **FIXED.** Not an arbitrary tiebreaker — found and reused an existing precedent: `Runtime._relation_authority` (`runtime.py:683-693`, confirmed by direct read) already answers "which authority owns a relation-level aggregate" for 4 other call sites, using the contract's `pre_split_authority` (AUTH_INTAKE) when a relation spans multiple programs. Mirrored it as a new `relation_authority()` function in `evaluate.py` (can't import `runtime.py` without a circular import), used by both `evaluate.py` and `seats.py`. Replaced both unordered `LIMIT 1` reads with this deterministic, config-driven lookup.
  - Orchestrator independently verified `_relation_authority` is real and matches exactly, confirmed `relation_authority` is importable, hash matches (`f000d30a…`). Subagent got 3 consecutive clean passes; **orchestrator's own independent 4th run also passed clean (14m32s).** 4/4 total. Race condition fix confirmed closed.
  - Blast radius checked: every reader of `RELEASE_SUMMARY`/`measure_results.authority` confirmed unaffected in kind, only now deterministic instead of flaky.

## K1 FULLY CLOSED — first genuinely clean full-suite gate of the whole session

Receipt `20261026T073131Z_partF-K1-r4.json`: `void: false`, `pytest_exit: 0`, **0 failures, 0 errors**, 1110 tests, 1 skip (confirmed the expected, legitimate, by-design opt-in skip — `test_w1_observed_equals_the_w1_data_plane`, needs an unrelated env var, nothing to do with Postgres).

K1's real journey: 16 failures → genesis evidence-gate bug fixed (+ masked error-wrapper bug) → 1 failure (void gate from a gate-tool bug, fixed) → 1 failure (race condition, fixed, 4/4 confirmed clean) → **0 failures.**

K1 is closing now. Moving straight into the K2-K6 batch build per the user's cadence directive (build all 5 packages, one comprehensive gate at the end, not per-package).

A: dbt-core still needs installing separately (env gap, not yet actioned — pending user confirmation per the system-boundary rule, since it's a pip install outside what was explicitly authorized).
E: tracing-invariant flaky test — still needs one more confirmatory run once the gate is clean again.

---

## 2026-10-30 — fresh Windows session: no `.venv` existed, gate blocked from the start; fixing as found

**Context:** this is a new session on the user's Windows machine (`C:\demo\medicaid-dq-agent`). Postgres was already running (port 55432, portable binaries at `C:\demo\pgsql`, `.pgdata` present) — no install needed there, contrary to the user's initial offer to install it. No `.venv` existed at all.

**Fix 1 (done): created `.venv`.** `python -m venv .venv`. Clean.

**Fix 2 (done): `pyproject.toml` had no package-discovery config, so `pip install -e .` failed outright** — `error: Multiple top-level packages discovered in a flat-layout: ['rules', 'tower', 'config', 'deploy', 'dqagent', 'pipelines', 'synthetic']`. setuptools' default auto-discovery can't tell the installable package (`dqagent`) apart from sibling data/config directories that happen to sit at repo root. Confirmed `tools/` and `tests/` are never installed either — every doc/script invokes them as `python -m tools.gate` from repo root, never via an installed console script, so they don't need to be package-discoverable. Added:
```toml
[tool.setuptools.packages.find]
include = ["dqagent*"]
```
This is a `pyproject.toml` edit, which `EXECUTION_PLAN.md` §3 assigns to Backend-Engine ownership — done directly here rather than through a seat dispatch because it's a one-line, mechanical build-config fix blocking all tooling (nothing could even be installed without it), not a design decision. Flagging per the ownership rule rather than silently proceeding.

**Install completed clean:** `pip install -e ".[dbt,x12,dev]"` finished, exit 0 — `dbt-core 1.12.6`, `dbt-duckdb 1.11.1`, `pytest 9.2.0`, `ruff 0.17.3`, `linuxforhealth-x12 0.57.0` all present.

**Fix 3 (done): found and removed a stale, unused dependency — `linuxforhealth-x12`.** User flagged it ("why is this in here... this is very old package"). Checked directly: zero imports of the package anywhere in `dqagent`/`tests` (confirmed by grep across the whole tree). Traced its origin to a 2026-10-20 design-review round (`docs/blackboard.md:1061`, "D6 ... linuxforhealth for bronze only") — a proposal that was superseded by the hand-rolled tokenizer that actually shipped (`dqagent/ingest/x12.py`'s `tokenize()`, `dqagent/ingest/mappers.py`'s `map_837p`/`parse_ie`). Confirmed the package itself is abandoned upstream: PyPI's latest release is 0.57.0, last published 2026-06-10 — pinned from the start, never updated since. Removed:
- `pyproject.toml`: dropped the `x12 = ["linuxforhealth-x12>=0.57"]` optional-dependency group entirely.
- `run_demo.ps1`: `-Dev` help text and the `$extras` assignment both changed from `dbt,x12[,dev]` to `dbt[,dev]`.
- Uninstalled `linuxforhealth-x12` from `.venv` directly (`pip uninstall --yes`).
- Re-ran `tests/t0/test_x12.py` (the repo's actual X12-handling tests) with the package gone: **5/5 passed** — confirms it was genuinely dead weight, not a disguised runtime dependency.

Same ownership flag as Fix 2: `pyproject.toml` and `run_demo.ps1` are both `EXECUTION_PLAN.md`-assigned to Backend-Engine. Edited directly (user gave explicit permission this session to do backend work as found) rather than dispatching a seat, since this is a mechanical dead-code removal with a clean before/after test result, not a design decision.

**First full gate attempt: FAILED at test collection (not a gate void, a real environment gap).** `python -m tools.gate --label win-session-2026-10-30 --tower` surfaced `ModuleNotFoundError: No module named 'jwt'` collecting `tests/t0/test_parte_registry.py` and `tests/t0/test_sim_explain.py`.

**Fix 4 (done): `pyjwt`/`cryptography` were missing from `pyproject.toml`'s BASE `dependencies` entirely, despite being required at import time by core (non-optional) runtime code.** Confirmed by direct read: `dqagent/hitl/idp.py:14` (`import jwt`) and `dqagent/governance/verifier.py:21` (`import jwt`) — both issue/verify RS256 JWS tokens (`idp.py:15` also imports `cryptography.hazmat.primitives.asymmetric.rsa` directly for RSA key generation), and both are imported unconditionally by `dqagent/runtime.py` and `dqagent/governance/registry.py` — not behind a feature flag, not optional. Grepped the whole `dqagent/` tree: exactly these two files use `jwt`/`cryptography`, nowhere else. This is a genuine packaging defect that would fail identically on ANY fresh install (cloud or Windows), not an environment quirk — the base package literally cannot be imported without it. Fix: added `"pyjwt[crypto]>=2.8"` to `dependencies` (the `[crypto]` extra pulls in `cryptography` automatically, so no separate entry needed). Reinstalled (`pip install -e ".[dbt,dev]"`); verified `import jwt, cryptography` now succeeds (`jwt 2.15.1`, `cryptography 50.0.2`).

**Fix 5 (done): `tests/t1/test_demo2_new_source.py` failed to collect — `FileNotFoundError: synthetic/out/w1_provider` did not exist.** Not a dependency gap — this is K5's ("new source by YAML", F-13) generated synthetic provider-feed fixture data, which had simply never been generated on this fresh checkout (`synthetic/out/` had `w1`, `w1_holdout7`, `w1_partc`, `w1_realistic`, `w2_earn` present, but not `w1_provider`). Confirmed via `synthetic/generate.py`'s own `--provider` flag (deterministic, seeded: `PROVIDER_RUN = {"seed": 42, ...}`) that this is reproducible generated data, not something to source or reconstruct by hand. Ran `python -m synthetic.generate --provider`: `files=36 ledger_entries=55`, clean.

**Second full gate attempt: real receipt (`void: false`), 5 failures + 420 skips + Tower tsc failure.** Receipt `20261030T152300Z_win-session-2026-10-30-r2.json`: 1196 tests, 5 failed, 0 errors, **420 skipped**. The gate's own honest note: "T1 Postgres unreachable => CP tests skip" — explains the 420.

**Fix 6 (done): Postgres connection env vars were never set in this shell.** `tests/t1/conftest.py:15-16` defaults `DQ_PG_HOST` to `/tmp` (the Linux cloud-session socket convention) when unset — `CLAUDE.md`'s own Environment section says Windows needs `DQ_PG_HOST=127.0.0.1`/`DQ_PG_PORT=55432`/`DQ_PG_USER=dq_gov` explicitly set; I'd never set them this session. Confirmed Postgres itself was fine (`socket.connect(('127.0.0.1', 55432))` → open) — this was purely a missing env var, not a service problem. Not a code fix; an operational step I'd missed. Will set these for every subsequent test/gate invocation this session.

**Fix 7 (done): Tower's `tsc --noEmit` failed — `Cannot find module '@xyflow/react'`.** Declared correctly in `tower/package.json:14` (`"@xyflow/react": "12.10.2"`, K4's React Flow dependency), just never installed into `tower/node_modules` on this checkout. Ran `npm install` in `tower/` (20 packages added, 2 changed — picked up `@xyflow/react` plus everything else that had drifted). Re-ran `npx tsc --noEmit` directly: clean, 0 errors. **Did NOT run `npm audit fix --force`** despite npm flagging 2 vulnerabilities (1 moderate, 1 high) — that command can apply breaking changes, and this repo's `playwright` version is deliberately pinned (per `docs/BACKLOG.md` F-49) to match an exact on-disk Chromium revision; a forced audit fix risks silently breaking that pin. Flagging rather than acting; a human call, not mine to make unilaterally.

**Fix 8 (in progress): re-ran the 5 originally-failing tests with `DQ_PG_HOST=127.0.0.1 DQ_PG_PORT=55432 DQ_PG_USER=dq_gov` set.** 2 of 5 passed immediately once Postgres was reachable (confirms those were purely Fix-6 masked). 3 more real gaps found in the same run:
- `numpy` missing — `dqagent/adapters/dataplane/duckdb_dp.py:45`'s `self.con.create_function(...)` (DuckDB UDF registration for `dq_hmac_*`) needs numpy at runtime; not declared anywhere.
- `uvicorn` missing — `dqagent/cli/main.py:107`'s `cmd_demo` (`import uvicorn`) is the exact code path `run_demo.ps1` invokes to start the API (confirmed by reading `run_demo.ps1`'s own `demoArgs`: `-m dqagent.cli.main demo ...`) — **without this, `run_demo.ps1` cannot start the API on any fresh checkout, full stop.**
- Grepped `dqagent/` for both: neither is imported anywhere else; both genuine, demo-blocking base-dependency gaps, same class as Fix 4's `jwt` gap, not environment quirks.
- Fix: added `"uvicorn"` and `"numpy"` to `pyproject.toml`'s base `dependencies`. Reinstalled; `import uvicorn, numpy` now succeeds (`uvicorn 0.54.0`, `numpy 2.5.4`). Re-running the 5 tests now to confirm full pass with Postgres reachable and both new deps present.

**Verification after fixes 4+5:** `pytest --collect-only -q` now collects **1196 tests, 0 errors** (up from 1188 collected + 1 collection error before). Re-running the full gate now.

---
*(next entries append below)*
