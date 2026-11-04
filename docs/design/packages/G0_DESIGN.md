# G0 design: a gate that can be trusted on both platforms

**Architect (B1).** Code hash this design is grounded against: `1a6c2b0ee19a63278f04630767bfbb324566d0c302c0ae8aff86c0d27226586f` (Windows tree, 2026-10-24). PFP (`docs/rules/PARTF_IMPLEMENTATION_PLAN.md`, r3+REVISION 4) does not mention a gate tool anywhere — `tools/gate.py` does not exist in PFP's scope at all (confirmed by grep for `gate.py`, `gate_tree`, `.gitignore`, `.gitattributes`, `msvcrt`, `fcntl` across the whole file: zero hits except three unrelated `harness/gate.py` lines, which is a different file and a different problem — the orchestrator-task gate for dbt publishing, not this gate tool). **G0 sits entirely above PFP; PFP is silent, not contradicted.** EXECUTION_PLAN.md §5.2 is the only design source for this package.

---

## 1. Re-grounding: every `file:line` EXECUTION_PLAN cites, checked against the tree at `1a6c2b0e…`

| Plan citation | Status | Current location / correction |
|---|---|---|
| `dqagent/obs/tracer.py:40,70-75` (HASHED list, `code_sha256()`) | **Stale by a few lines, substance correct.** | `HASHED = [...]` is at **line 40** (correct). `code_sha256()` the function is defined at **line 67**, decorated at **line 66**; its body runs **lines 70-75**. So "40,70-75" is accurate for the body but the def line is 66-67, not folded into the cited range — minor, not misleading. Confirmed: `HASHED = ["dqagent", "rules", "config", "pipelines/w1_dbt/models", "tests"]` (line 40); loop at lines 71-74 uses `p.relative_to(ROOT_DIR)` (no `.as_posix()` — confirms C2) and `p.read_bytes()` raw, no CRLF normalisation (confirms C2 fully); filters to `.py/.yaml/.yml/.sql` only and skips `__pycache__` (confirms C1: nothing under `tower/`, `tools/`, `synthetic/`, `pipelines/**` outside `w1_dbt/models`, or any non-listed top-level dir is hashed). |
| `tools/receipt.py` "how it currently computes/uses code_hash and runs the suite" | **Confirmed, no stale lines.** | `code_hash()` at **tools/receipt.py:22-27**: delegates to `tracer.code_sha256()`, clears the `functools.lru_cache` first (line 26) so every receipt hashes the tree as it is *now*. `main()` (lines 30-49) runs `pytest -q tests/t0 tests/t1 tests/synthetic --junitxml=...` (line 39-40) with **no lock at all** — there is no `flock` call anywhere in `receipt.py`; the lock is bolted on externally by the caller (`.claude/agents/*.md:28`, `.claude/commands/gate.md:17`) via a hand-run `flock /tmp/dq_pytest.lock python -m tools.receipt ...` on Linux only. **This is itself a C3/C1-adjacent gap the plan's prose glosses over**: "replaces the hand-run flock sequence" (EXECUTION_PLAN:149) is accurate, but `tools/receipt.py` itself never held a lock — the lock was seat-discipline, not code. `tools/gate.py` is a superset, not a wrapper with an inserted lock: it must *add* the lock, not "replace" one that lived in `receipt.py`. Receipt schema today (`tools/receipt.py:44-47`): `{label, utc, code_sha256, pytest_exit, tests, failures, errors, skipped, junit, junit_sha256, note}` — confirmed against `docs/receipts/20261024T123138Z_partF-T1traces.json` verbatim. |
| `run_demo.ps1` "especially around line 148, the pip install extras" | **Confirmed exact.** | `run_demo.ps1:148`: `& $py -m pip install --quiet -e "$Root[dbt,x12]"`. No `dev` extra. Confirms C3 exactly as cited. |
| `pyproject.toml`, `dev = ["pytest", "ruff"]` | **Confirmed exact.** | `pyproject.toml:12`: `dev = ["pytest", "ruff"]`. Also note `x12` (line 9: `linuxforhealth-x12>=0.57`) and `dbt` (line 10: `dbt-core>=1.10", "dbt-duckdb>=1.10`) extras exist as installed today; `snowflake-t1` (line 11) exists but is not installed by `run_demo.ps1` and is irrelevant to G0. |
| `.gitattributes` / `.gitignore` existence | **`.gitattributes` does not exist** at the repo root (only vendored copies 5 levels deep under `tower/node_modules/*/`. which are irrelevant). **`.gitignore` exists** at repo root (24 lines) and at `tower/.gitignore` (6 lines). Neither currently ignores `.gate.lock` (it doesn't exist yet either). The root `.gitignore` already ignores `.venv/`, `.pgdata/`, `*.duckdb`, `.ruff_cache/`, `.pytest_cache/`, `__pycache__/`, `synthetic/out/`, `logs/`, demo PID/log files, and `_to_delete/` — **this list is exactly what `gate_tree_sha256` must respect** ("every file that is not gitignored"), so `tools/gate.py` needs to either shell out to `git check-ignore`-equivalent logic or hard-code an equivalent exclude set, because **the tree is not a git repository** (confirmed: no `.git` directory found by Glob at repo root) — `git check-ignore` is not available as a real git call here; the tool must read and interpret `.gitignore` patterns itself, or maintain its own exclude list that is kept in sync with `.gitignore` by a test. This is a **design gap the plan's prose does not resolve** — flagged under Decisions needed, §6 below. |
| PFP citations for I-3 (`tests/t1/test_agent_eval.py` observe() at "`:194-227`") | **Stale by a few lines; substance correct.** | `observe()` is at **tests/t1/test_agent_eval.py:194-224** (not 227); line 227 is actually the `digest()` helper function, immediately below it, not part of `observe()`. The PFP line range folds the helper in by one blank-line miscount. Substance holds: `observe()` reads only explicit named columns (`results`, `units`, `traces`, `measures`, `board`, `memory`, `log` — lines 196-224) and **does not read `track_record` or anything from `trace_schema.py`'s evidence views**, confirmed by grep: no `track_record` string appears in `test_agent_eval.py` at all. |

**Stale-reference count: 2 of the 5 citations check (tracer.py line split, test_agent_eval.py line split) are off by 1-3 lines; 3 of 5 (receipt.py, run_demo.ps1, pyproject.toml) are exact.** None of the drifts change which fix lands where. The `.gitattributes` / `.gitignore` interaction is not a stale reference — it's an unstated design gap (see Decisions needed).

---

## 2. Blast radius

### 2.1 Everything that reads or depends on `code_sha256()` / `code_hash()`

The function is defined once (`dqagent/obs/tracer.py:66-75`) and is the single source every other reference delegates to (`tools/receipt.py:22-27` docstring: "the ONE definition"). Its value is read at:

| Consumer | `file:line` | What breaks if the hash value changes (not the function signature) |
|---|---|---|
| `tools/receipt.py:code_hash()` | `tools/receipt.py:22-27` | Nothing breaks structurally; it *is* the pass-through. Every receipt's `code_sha256` field changes once. |
| `Root.__init__` default | `dqagent/obs/tracer.py:158` | `self.code_digest = config_digest or "none", code_digest or code_sha256()` — every new trace root stamps the new hash from the moment the fix lands. Old roots keep their old stamped value (rows are immutable once written); this is fine, it is a version marker, not a live computation. |
| `_root_args()` | `dqagent/runtime.py:548-550` | Same: `"code_digest": tracer.code_sha256()` passed into every root's kwargs at call time. |
| `_opts_for(...)` (CP session vars) | `dqagent/obs/tracer.py:435-444` | `cs = code_sha256()` is pushed as `-c dq.code_sha256=...` into the Postgres session; every row DEFAULTed by `dq_ctl.*` tables' `code_sha256` column (via `dqagent/cp/trace_schema.py:75-76`, `:281-282`) picks up the new value from the moment the fix lands. |
| `dq_ctl.eval_runs`, `dq_ctl.eval_attestations`, CORRELATED tables (`unit_events, results, abc, measure_results, agent_blackboard, audit_log, veto_windows, decisions, attestations, manifest_versions, monitor_metrics` — `trace_schema.py:29-30`) and COUNTED_EXTRA (`deny, lookback_events` — `trace_schema.py:274`) | `dqagent/cp/trace_schema.py:75-76,281-282` | Every row written *after* the fix lands carries the new hash in its `code_sha256` column (a session-level column DEFAULT). Rows written *before* keep the old hash. `track_record_by_version` (`trace_schema.py:419-453`) buckets by `code_sha256`, so **the current-version track record resets to zero the moment the fix lands** — this is the exact mechanism EXECUTION_PLAN:202 means by "the I-3 digests and code_sha256 values change once," and it is the same mechanism K5's red-team finding I5 names for config edits (EXECUTION_PLAN:380). G0 triggers it once, deliberately, as a one-time, recorded, sanctioned reset — see §6. |
| `evidence_complete`, `promotion_minimums`, `promotion_refusal` views | `dqagent/cp/trace_schema.py:277-453` (evidence_ddl) | Same mechanism: these views filter/join on `code_sha256 = current_setting(...)`. Any currently-accumulating minimum-record count (e.g. toward L2→L3, K0's earn story) restarts. **This only matters if K0's earn-story evidence has already been partially accumulated on tree `8c2934ff…` before G0 runs.** Per EXECUTION_PLAN §1, K0 is "PARTIAL and NOT GATED" — no gated evidence exists yet to lose, so this is low-risk today, but it must be done *before* K0's earn-story work, which the package order already guarantees (G0 is first). |
| `eval_attest()` SQL function | `dqagent/cp/trace_schema.py:320-351` | Refuses a mismatched `p_code_sha256` (lines 333, 340-342). No behavior change; just a new accepted value going forward. |
| `test_agent_eval.py` I-3 digests | `tests/t1/test_agent_eval.py:194-224,227-228` | **`observe()` does not read `code_sha256` anywhere** (confirmed: no `code_sha256` string in the whole file). The I-3 pinned digests in `scenarios.yaml`'s `expect` blocks are derived from `results/units/traces/measures/board/memory/log`, none of which embed the code hash. **The I-3 digests must NOT change from the tracer.py fix alone** — this is a stronger, more favorable finding than the plan's own framing. The plan's CLAUDE.md-flagged tension (EXECUTION_PLAN:202, "I-3 digests ... change once") is about the `code_sha256` *value* stored in correlated CP tables, not about the I-3 *test's pinned digest*, which is a separate, Python-level, column-free hash over `obs()`'s own query results. **This distinction must be made explicit and verified, not assumed** — see Decisions needed, §6. |
| `test_evidence_promotion.py` | `tests/t1/test_evidence_promotion.py:181-193,252` | Monkeypatches `tracer.code_sha256` directly (line 181) to simulate "a NEW code version" — this test's behavior is about the mechanism, not the literal value, so it is unaffected by the fix itself. No change needed. |
| `test_trace_shape.py` | `tests/t1/test_trace_shape.py:153-155` | Asserts `SELECT DISTINCT code_sha256 FROM dq_ctl.results` equals `(tracer.code_sha256(),)` — a single value, computed live at assertion time via the same (fixed) function. **No change needed**; this test is hash-value-agnostic by construction. |
| `test_trace_schema.py:30` | asserts columns include `code_sha256` — schema-shape only, value-agnostic. No change needed. |
| `tests/t1/evidence.py:20-29` | test helper that writes `eval_runs`/`eval_attestations` using `code or tracer.code_sha256()` as a default — value-agnostic, no change needed. |
| `app.py` readers (`:1003,1022,1498-1501,1538-1570`) | `dqagent/api/app.py` | All read `code_sha256` as an opaque string column from the DB; no value dependency, no change needed. |
| `governance_schema.py:749,799` | DDL check constraint `code_sha256 ~ '^[0-9a-f]{64}$'` — a shape constraint (64 hex chars), satisfied by any sha256 hex digest including the fixed one. No change. |
| `docs/receipts/*.json` (9 existing receipt files) | historical | Each stamps the `code_sha256` value live at that receipt's time. **These are immutable historical records and must not be rewritten.** The newest (`20261024T123138Z_partF-T1traces.json`) carries `4d17ec88…`, the last gated hash — this is the value EXECUTION_PLAN:15,22 calls "the last gated tree." It predates the C2 portability fix, so it was computed under the **old** (buggy, OS-raw-bytes) hashing. **A new gate run after the fix will not reproduce `4d17ec88…`, by design** — this is the one-time pair the orchestrator must record (old=`4d17ec88…` or the then-current pre-fix hash of the *current* tree, new=the first post-fix `code_sha256`). |

### 2.2 What `tools/receipt.py` does today that `tools/gate.py` must preserve or supersede

| Behavior in `receipt.py` | Preserve, supersede, or extend in `gate.py`? |
|---|---|
| `--label` CLI arg (line 32) | **Preserve** the flag name and semantics exactly; `gate.py` reserves `--label` for the same purpose. |
| Timestamp/path convention `docs/receipts/<UTC>_<label>.junit.xml` / `.json` (lines 34-37, 47) | **Preserve exactly.** No change to where receipts live or how they are named — downstream tooling (`.claude/commands/close.md`, blackboard entries, BACKLOG DONE-row citations) all cite receipt filenames in this shape. |
| `code_hash()` cache-clear-then-read pattern (lines 22-27) | **Preserve the pattern, reuse the function.** `gate.py` must call `tracer.code_sha256.cache_clear()` itself (or call the same `code_hash()` helper) before reading, both at start and end, because `lru_cache(maxsize=1)` on `code_sha256` (tracer.py:66) means a stale cached value would silently defeat the whole void-on-change check. **This is the single most important implementation correctness fact in this design**: if `gate.py` reads `code_sha256()` twice without clearing the cache between reads, the "end" read is always identical to the "start" read, trivially, and the void check becomes a no-op. The gate-tool red team (step 3 of the plan) must test exactly this. |
| Running `pytest -q tests/t0 tests/t1 tests/synthetic --junitxml=...` (lines 39-40) | **Preserve the three suite paths; extend** with `--tower` adding `tsc`, `eslint`, `next build` ×2, smoke. |
| JUnit parse → counts dict (lines 41-43) | **Preserve.** Same XML shape, same four counted fields. |
| Receipt JSON write + print (lines 44-48) | **Extend**: add `gate_tree_sha256`, `void`, and (when `--tower`) tower step results, while keeping every existing field name unchanged so existing consumers (none found reading receipt JSON programmatically — only the orchestrator reads them by eye per `.claude/commands/gate.md:18-22`) keep working. |
| No lock anywhere in `receipt.py` itself | **Add** — this is new in `gate.py`, not a supersession of existing locking code (none exists). |
| `main()` returns `rc` (pytest exit code) (line 49, 53) | **Preserve** the same exit-code contract; `gate.py`'s own process exit code additionally reflects `void` (plan §5.2 step 5: "exit non-zero" on void), which `receipt.py` never did (it exits with the raw pytest code only, so a void-equivalent condition could not previously be represented at all). |

### 2.3 What happens to existing receipts' schema/consumers if the hash changes

- All 9 existing receipt JSON files (`docs/receipts/*.json`) are untouched historical artifacts; they keep their old `code_sha256` values permanently. No migration, no rewrite — confirmed no code reads old receipts back in and compares hash values across time (the only cross-receipt consumer is a human, the orchestrator, reading EXECUTION_PLAN.md's own prose citations of `4d17ec88…` and `8c2934ff…`, which stay as historical facts in that document).
- No schema field is renamed or removed — `gate.py`'s receipt is strictly additive (`gate_tree_sha256`, `void`, tower fields) over `receipt.py`'s schema, so nothing that reads receipts today (nothing does, programmatically) breaks.
- The one live consumer of the *value itself* going forward is any seat or orchestrator comparing "code hash at start" vs "code hash at end" by eye, per `.claude/agents/*.md:38` ("the code hash at your start and end") and `.claude/commands/gate.md:20`. Both continue to work with the new value; they're string-equality checks, not literal-value-pinned checks.

---

## 3. Reserved names

Seats must use exactly these names; nothing here is available for a seat to rename.

| Category | Name | Notes |
|---|---|---|
| Module (new) | `tools/gate.py` | Owner: Backend-LLM-Obs (BB), confirmed against `.claude/agents/backend-llm-obs.md:11` ("tools/gate.py") and PFP §4 has no row for it (G0 predates PFP's table). |
| Test file (new) | `tests/t1/test_gate_tool.py` | Owner: BB. Placed in `t1` (not `t0`) per the brief's explicit instruction, even though the gate tool itself has no Postgres dependency for its *hashing* logic — t1 is used here because the full-suite invocation it tests against does need Postgres. **This placement is worth a seat-level sanity check**: if the hash/lock/void tests can run with no DB, consider whether a `t0`-placed subset is faster for CI-less local iteration; not a blocker, a build-time judgment call for BB. |
| CLI entry point | `python -m tools.gate` | Mirrors `python -m tools.receipt`'s existing invocation style (`tools/receipt.py:1-6` docstring: `python -m tools.receipt`). |
| CLI flag | `--label <str>` | Reused verbatim from `receipt.py:32`. |
| CLI flag | `--tower` | New. Boolean switch, no value. |
| CLI flag | `--targeted <paths...>` | New. Takes one or more pytest path arguments; writes no receipt (plan §5.2 step 7). |
| Function (existing, reused) | `dqagent.obs.tracer.code_sha256()` | **Not renamed.** Fixed in place (BA). `tools/gate.py` imports and calls it the same way `tools/receipt.py:25` does. |
| Field (existing, reused) | `code_sha256` (receipt JSON key) | Unchanged name; unchanged semantics (evidence identity). |
| Field (new) | `gate_tree_sha256` (receipt JSON key) | The whole-tree freeze-check hash. Lives only in `tools/gate.py`'s own hashing function — **not** in `tracer.py`, because `tracer.py`'s `code_sha256()` is explicitly scoped to the evidence-identity tree (`HASHED` list) and stamps CP rows; conflating the two in one function would make every Tower-only edit also bump stamped evidence rows, which is wrong. Reserve the function name `tools.gate.gate_tree_sha256()` for the whole-tree hash, parallel to (but never imported by) `tracer.code_sha256()`. |
| Field (new) | `void` (receipt JSON key, boolean) | `true` only when `code_sha256` or `gate_tree_sha256` differs between start and end. |
| Field (new, nested, `--tower` only) | `tower` (receipt JSON key, object) | Holds `{"tsc": rc, "eslint": rc, "build_mock": rc, "build_live": rc, "smoke_mock": rc}` — reserved sub-keys; BB picks the exact shape during build but must not collide with these reserved names. |
| Lock file path | `.gate.lock` | Repo root, exact name, gitignored. Not `.gate.lock.json` or any variant — the plan names it exactly once (EXECUTION_PLAN:157,164) and that is the reserved spelling. |
| New file | `.gitattributes` | Repo root. Owner: BA (confirmed `.claude/agents/backend-engine.md:11` lists it). Content pinned by the plan: `* text=auto eol=lf`. |
| `.gitignore` addition | `.gate.lock` | One new line in the existing root `.gitignore` (24 lines today). Owner: BA, per plan step 8 ("Also in G0: add `.gate.lock` to `.gitignore`"), which the seat-ownership table in EXECUTION_PLAN §6 does not explicitly assign — see Decisions needed (it is filed under "Backend-LLM-Obs" step 2 textually, but `.gitignore` is not a BB-owned file per `.claude/agents/backend-llm-obs.md:11`, and BA already owns `.gitattributes`, the sibling file; recommend BA takes `.gitignore` too, for one coherent "repo hygiene" commit — flagged as a decision, not resolved here). |
| `run_demo.ps1` switch (new) | `-Dev` | Switch (boolean), no value. Installs `[dbt,x12,dev]` instead of today's `[dbt,x12]` (`run_demo.ps1:148`). |
| `run_demo.ps1` switch (new) | `-DbOnly` | Switch (boolean), no value. Starts portable Postgres only; skips the API/Tower start blocks (`run_demo.ps1` §4-5, lines 151-186). |
| Blackboard hash-pair record | "old hash / new hash" pair | No fixed field name reserved (it's prose on the blackboard, not code), but the orchestrator must record both literal 64-hex values plus the reason string verbatim — content spec in §6 below. |

No table, view, kind, route, span name or `ATTR_VOCAB` value is introduced by G0 — it touches no control-plane schema. Confirmed: `tracer.py`'s fix is to the *hashing of files on disk*, not to any span, root kind, or DB column; `trace_schema.ATTR_VOCAB` (`trace_schema.py:38`) is untouched by this package.

---

## 4. Golden-path worked example

**Scenario:** a developer, on the Windows machine, has just pulled the G0 fix (BA's `.gitattributes` + tracer.py fix landed, BB's `tools/gate.py` landed). They run the first gate on the clean tree, then simulate a mid-gate Tower edit.

**Step 1 — clean run.**
```
python -m tools.gate --label g0-selfcheck
```
Walkthrough against this design:
1. `gate.py` acquires `.gate.lock` exclusively via `msvcrt.locking` (Windows). If another process holds it, `gate.py` fails fast with "gate already running" rather than blocking silently — this must be in BB's build, though the plan text (EXECUTION_PLAN:157) does not say whether the acquire is blocking-with-timeout or fail-fast; **flagged below as a decision**.
2. `gate.py` clears `tracer.code_sha256`'s lru_cache and reads `code_sha256_start = tracer.code_sha256()`. It separately computes `gate_tree_sha256_start` by walking every non-gitignored file (per §3's reserved function) and hashing `relative_to(ROOT).as_posix()` + CRLF-normalized bytes, sorted, same pattern as `tracer.py:71-74` but over the whole tree.
3. `gate.py` shells `pytest -q tests/t0 tests/t1 tests/synthetic --junitxml=...`, exactly as `receipt.py:39-40` does today, writing the same JUnit file naming convention.
4. No `--tower` flag given, so step 4 of the plan (tsc/eslint/next build/smoke) is skipped.
5. `gate.py` clears the cache again and reads `code_sha256_end` and recomputes `gate_tree_sha256_end`. Both equal their `_start` counterparts (nothing changed mid-run).
6. Receipt written: `{"label": "g0-selfcheck", ..., "code_sha256": "<same both ends>", "gate_tree_sha256": "<same both ends>", "void": false, "tests": N, "failures": 0, ...}`. Exit code 0 (assuming green).
7. Lock released.

**Non-void receipt obtained.** This matches the plan's exit criterion language ("gate-tool tests green... and the same gate_tree_sha256 on both [platforms]").

**Step 2 — mid-run Tower edit, simulated.**
The same command is re-run, but this time, between step 3 (pytest running) and step 5 (end-hash), a file under `tower/src/app/page.tsx` is touched (simulating a Tower seat saving an edit mid-gate — the exact scenario red-team finding C1 describes, and the exact scenario the concurrency rule in EXECUTION_PLAN §3 forbids but which the *tool* must still catch as a backstop).
1. Steps 1-4 identical.
2. At step 5, `gate_tree_sha256_end` differs from `gate_tree_sha256_start` (the Tower file's bytes changed, and `tower/**` is inside the whole-tree hash scope, confirmed reserved per §3).
3. `code_sha256_end` is **unchanged** in this particular example, because `tower/**` is not in `tracer.py`'s `HASHED` list (`tracer.py:40`) — this is the precise case EXECUTION_PLAN:153 calls out ("Tower edits during a gate therefore void it") and it demonstrates *why two separate hashes* are needed: the evidence-identity hash alone would have missed this edit entirely and reported a false-green receipt.
4. `gate.py` writes the receipt with `"void": true`, prints "tree changed during gate: freeze and re-run" (plan §5.2 step 5, verbatim wording reserved), and exits non-zero.
5. The orchestrator reads the void flag, does **not** accept the receipt as evidence, re-freezes (confirms the Tower seat is idle), and re-runs.

This worked example demonstrates every structural claim in the plan text and nothing beyond it. It does not demonstrate the `--tower` sub-steps or the cross-platform lock, which belong to the red-team's "attack the gate" pass (plan step 3) rather than this single golden path.

---

## 5. Technology-selection record

**None needed.** Confirmed per the brief's own instruction: `msvcrt` (Windows) and `fcntl` (POSIX) are both standard-library modules bundled with CPython — no PyPI package, no version pin, no license, no CVE surface, no bundle-size or accessibility consideration applies. No other new dependency is introduced anywhere in the G0 steps (tracer.py fix, `.gitattributes`, `run_demo.ps1` switches, `tools/gate.py`, its test file). This was confirmed by reading the full EXECUTION_PLAN §5.2 and §6 G0 text for any package name, and by the brief's own "no new third-party dependency is anticipated" framing, which this design corroborates rather than contradicts. **No web research was performed for this package** — correctly, since there is nothing external to verify.

---

## 6. Decisions — RESOLVED by the orchestrator (2026-10-24, after red team + scope review)

1. **I-3 digest tension.** RESOLVED: no assumption either way. BA must run `test_agent_eval.py` before and after the `tracer.py` fix and report both results verbatim (pass/fail, and whether the pinned `scenarios.yaml` digests changed). The blackboard record at close must carry: literal old `code_sha256`, literal new `code_sha256`, whether the I-3 pinned digests changed (yes/no with evidence), and the reason string. This is a precondition for K0, not optional.
2. **`.gitignore`-equivalence for `gate_tree_sha256`.** RESOLVED: option (b) — BB implements a short, explicit **include-list** for the freeze-check hash (`dqagent`, `tower/src`, `tools`, `synthetic`, `pipelines`, `tests`, `config`, `rules`), not a general gitignore-pattern engine and not a full exclude-list mirror of `.gitignore`. This is the scope reviewer's "cheaper path" finding, adopted: it closes the one known blind spot (C1, Tower edits invisible to the freeze check) without building pattern-matching machinery the demo doesn't need. BB adds a test that fails if a new top-level source directory is added without being added to this list, so the list doesn't silently rot.
3. **`.gitignore` ownership for `.gate.lock`.** RESOLVED: Backend-Engine (BA) owns this edit, as the sibling of `.gitattributes`, which BA already owns.
4. **Lock contention semantics.** RESOLVED: fail-fast, not blocking. A second `gate.py` invocation while `.gate.lock` is held exits immediately with a clear "gate already running" message. Rationale: gates run 30–60 minutes; a blocking acquire could hang a seat's `--targeted` run silently for up to an hour with no feedback, which is worse than a clear, fast failure the orchestrator can act on.
5. **`--tower` scope.** RESOLVED (user, 2026-10-24): **defer to K2.** The user's direction is to complete the demo mock end-to-end first; production/CI hardening (which is what `--tower`'s build-gate machinery is) is explicitly next-phase work. G0 ships `tools/gate.py` with `--label` and `--targeted` fully working; `--tower` is accepted as a recognized flag that raises `NotImplementedError("--tower ships in K2, the first package that changes tower/")` rather than silently no-op'ing. This deviates from EXECUTION_PLAN.md §5.2 step 4's literal text — recorded here and on the blackboard as a sanctioned, user-directed scope cut, not a silent narrowing.
6. **Receipt honesty on skips (red-team Critical 1, confirmed by the orchestrator at `tools/receipt.py:39,49`).** RESOLVED: `tools/gate.py` must not merely print skip counts — it must treat an under-run suite as a build requirement. Concretely: the receipt gains a `suite_complete` boolean, false whenever any of `tests/t0`, `tests/t1`, `tests/synthetic` reports `skipped > 0`. A receipt with `suite_complete: false` is not void (the tree didn't change), but the gate.md "sanity checks" step and the orchestrator's close checklist must treat it as **not evidence of a real green** — this is already gate.md:24's intent ("skipped tests are not passes... the gate is invalid"), now made machine-checkable instead of relying on a human reading the JSON.
7. **TOCTOU window after the final hash (red-team Critical 2, confirmed structurally real and not fully closeable within this package's scope).** RESOLVED: minimize, document, don't pretend to solve. BB writes the end-hash-then-receipt-write sequence as tightly as possible under the same lock hold (no work between the final hash read and the receipt write other than the write itself), and the receipt's own `note` field states the residual window honestly (plain language: the lock guarantees no second gate run interleaves; it does not guarantee no editor save lands in the few milliseconds between the final hash and the receipt write). This is an honest limitation statement, not a fix, and `tests/t1/test_gate_tool.py` does not need to (and cannot) prove the absence of a race it cannot close.
8. **Binary files in the whole-tree hash (red-team Important finding, confirmed: `G0_DESIGN.md` never addressed this).** RESOLVED: CRLF normalization applies only to files with extensions in a declared text-file set (`.py, .ts, .tsx, .ps1, .yaml, .yml, .sql, .md, .json, .toml, .css, .mjs, .cjs`); every other file (images, fonts, lockfile binaries if any) is hashed as raw bytes, unnormalized. BB adds this distinction explicitly in `tools/gate.py` and a test with at least one binary fixture.
9. **Stale-reference nit (non-blocking):** PFP's own `test_agent_eval.py` observe() citation ("`:194-227`") is off by three lines (`:194-224`; `:227-228` is the separate `digest()` helper). Recorded so BB doesn't re-discover it; changes no fix.

---

## 7. Per-seat context manifests for G0

### Backend-Engine (BA)

**Reads:** `dqagent/obs/tracer.py` (lines 39-75 specifically), `run_demo.ps1` (lines 139-149 specifically), `pyproject.toml` (lines 8-12), `tools/receipt.py` (to confirm it still delegates correctly after the fix), this design file, EXECUTION_PLAN §6 G0 step 1.

**Owns (writes) for G0:**
- `dqagent/obs/tracer.py` — the `code_sha256()` body (lines 71-74): add `.relative_to(ROOT_DIR).as_posix()` (it currently does `.relative_to(ROOT_DIR)` without `.as_posix()` — confirmed stringifying a `Path` object directly at line 74 uses OS separators on Windows) and normalize CRLF→LF on `p.read_bytes()` before hashing.
- `.gitattributes` (new file, repo root): `* text=auto eol=lf`.
- `run_demo.ps1`: add `-Dev` and `-DbOnly` params (new `param()` block entries) and branch logic; the `-Dev` branch changes line 148's extras list from `[dbt,x12]` to `[dbt,x12,dev]`; the `-DbOnly` branch short-circuits after §2 (Postgres start, lines 74-137) and skips §3-5 (lines 139-186).
- `pyproject.toml`: only if BA judges a default-extras change is needed for `-Dev` to work (currently `dev = ["pytest", "ruff"]` already exists at line 12, so likely **no edit needed here** — confirm this during build rather than editing speculatively).
- `.gitignore` — pending decision 3 above; do not edit until the orchestrator confirms ownership.

**Must not touch:** `tools/gate.py`, `tests/t1/test_gate_tool.py` (BB's files), anything under `tower/`.

### Backend-LLM-Obs (BB)

**Reads:** `tools/receipt.py` (whole file, to know exactly what to preserve per §2.2 above), `dqagent/obs/tracer.py` (the fixed `code_sha256()`, once BA lands it — **serial dependency**, see below), the existing `docs/receipts/*.json` shape, `.claude/commands/gate.md` (the orchestrator's own gate-invocation contract, which `gate.py` must satisfy), this design file, EXECUTION_PLAN §5.2 and §6 G0 step 2.

**Owns (writes) for G0:**
- `tools/gate.py` (new file).
- `tests/t1/test_gate_tool.py` (new file): the four test behaviors listed in EXECUTION_PLAN:167-170 (void-on-hash-change for both a `tower/` edit and a `dqagent/` edit; CRLF/LF/Windows-path-style tree hashes to one value; lock exclusivity across two processes; receipt field shape).

**Must not touch:** `dqagent/obs/tracer.py`, `.gitattributes`, `run_demo.ps1`, `pyproject.toml` (BA's files).

### Concurrency and ordering

**File sets are disjoint** — BA touches `{tracer.py, .gitattributes, run_demo.ps1, pyproject.toml(maybe)}`; BB touches `{tools/gate.py, tests/t1/test_gate_tool.py}`. No file is claimed by both, so the two seats *could* in principle run concurrently per the letter of the concurrency rule (EXECUTION_PLAN §3: "backend seats run one at a time... unless the package says otherwise").

**Recommendation: run BA then BB, serially, not concurrently.** Reasoning:
- BB's test file must exercise the **fixed** `code_sha256()` to prove the void-on-change and cross-platform-hash-equality behaviors (EXECUTION_PLAN:168: "the same tree hashed with CRLF and LF line endings, and with Windows-style paths, gives one hash" — this is a direct test of BA's fix, not of `gate.py`'s own hashing function alone, since `gate.py`'s `code_sha256` field is `tracer.code_sha256()` reused verbatim per §3 reserved names). Testing against the *old*, unfixed `code_sha256()` would either pass for the wrong reason (if the test doesn't actually exercise cross-platform paths) or fail in a way that looks like a BB bug but is actually BA's unfinished fix — a diagnostic mess the orchestrator would have to untangle.
- This is also consistent with the plan's own explicit ordering for the parallel P1-1 case elsewhere ("Within P1-1 the serial order is BA, then BB, then BC" — PFP:1061), which is the house pattern this repo already uses whenever one seat's correctness is a precondition for another's test.
- The cost of serializing two small, disjoint-file seats is low (G0 is sized S in EXECUTION_PLAN's table); the cost of a false-negative or false-positive in `test_gate_tool.py` due to ordering is a wasted red-team round against the gate tool itself (step 3 of G0, which explicitly attacks "can it report green on a changed tree?" — exactly the thing a premature BB run risks getting backwards).

**Order: BA first, freeze, confirm BA idle, then BB.** The orchestrator should not dispatch BB until BA's report confirms the `code_sha256()` fix is in place and BA has reported idle.
