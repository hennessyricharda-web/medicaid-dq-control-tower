# Execution plan: finishing the DQ agent and Command Tower with Claude Code

**Status:** PLAN. Nothing in this document has been built yet.
**Date:** 2026-10-24
**Runs on:** the user's Windows machine (`C:\demo\medicaid-dq-agent`) or the Linux cloud session, chosen per package.
**Relationship to the existing plan:** additive. The design source stays `docs/rules/PARTF_IMPLEMENTATION_PLAN.md` (r3 + REVISION 4, abbreviated PFP below). This document sequences what is left, says who builds it, and says how each package is proved and closed. Where the two disagree on design, PFP wins. Where they disagree on order, this document wins (user decision 2026-10-24: demo story first).
**Method:** the agentic build framework (coalition-build-framework). The waves, seats, red-team panel, gates E1–E18 and the Definition of Done are mapped onto this repo in §3 to §5.

---

## 1. Where the build stands (baseline for this plan)

| Item | State | Evidence |
|---|---|---|
| Parts A–E, Part F T0, T1-traces backend | DONE | Receipt `docs/receipts/20261024T123138Z_partF-T1traces.json`: 1053 tests, 1052 passed, 1 skipped, 0 failed. Code hash `4d17ec88…`, the same at start and end. |
| Tower shell, dense layouts, Part E screens | DONE (mock + live build) | `tower/scripts/smoke.mjs`, 116/116 in mock |
| Trace explorer screens (traces, run detail/waterfall, run diff, guardrails, LLM usage, card citations) | DONE in mock only | Smoke shots `17_25`–`17_34`. **Never run against the live backend.** |
| R4-5 evidence-gated promotion (DEC-024) | **PARTIAL and NOT GATED**. Synced to Windows 2026-10-24 at the user's request. | Tree `8c2934ff…`. Schema and the 9 R4-5 tests are in. Four dependent test files were rewritten but not verified; `test_agent_eval` is not updated; there is no `eval_runs` writer; the Tower attestation form is not updated. Details in K0. |
| Pipelines, per-DAG profile, add-a-DAG demos, backtest | NOT STARTED | BACKLOG F-09, F-10, F-11, F-12, F-13 |
| Windows end-to-end run | NOT DONE | BACKLOG E-03 |

**First action on either platform:** confirm the tree you are on. Both copies are at `8c2934ff…` (R4-5 partial, synced ungated 2026-10-24). The last gated tree is `4d17ec88…`. G0 and then K0 start from `8c2934ff…` on either platform.

---

## 2. Package order (demo story first)

The orals story is: *run a night → trace the agent → see the DAG's agent configuration → wire a new DAG → test it → promote it → add a new source.* The packages are ordered so each one adds a demo moment that works on its own. Hardening comes after the story works end to end.

| # | Package | Closes | Design source | Demo moment it adds | Size |
|---|---|---|---|---|---|
| G0 | A gate that can be trusted on both platforms: whole-tree gate hash, portable code hash, test tooling on Windows | Red team 2026-10-24 C1–C3 | This plan §5.2 | None (it makes every later green trustworthy) | S |
| K0 | Finish evidence-gated promotion and the eval recorder | R4-5, E-13, E-07, part of F-05 | PFP R4-5 (:1264-1346), R4-7 (:1351), P2-1 eval recorder (:950) | "The database refuses a promotion without evidence; two people approve it with evidence" (§5 step 10) | M |
| K1 | Per-DAG agent profile (read-only) | F-09 read half | PFP P1-2 (:697-731) | "This night ran under this exact configuration; here is who approved each part" | M |
| K2 | Pipelines-lite: catalog, ORCH-1 introductions, consumer DAG, wiring wizard | F-10 lite, F-12 | PFP P1-3 (:734-885) | "See every DAG. Wire a consumer DAG in advisory mode; two people; the tier does not move" | XL (three waves) |
| K3 | Backtest, plus promoting a gate from advisory to block | F-11 (a) | PFP P1-4 (:888-922) | "Backtest the new gate, then promote it to block with two signatures" | L |
| K4 | Pipelines graph (React Flow) and the demo estate | F-10 full | PFP P2-4 (:993-1008); DEC-020 | "The whole estate as a graph, with gates overlaid; a table for accessibility" | L |
| K5 | New source added by YAML (Demo 2) | F-13 | PFP P2-6 (:1026-1042) | "A new provider feed onboarded by configuration only; no code changes" | L |
| K6 | Demo readiness (story complete) | E-03, E-11, N-T0-3, F-35 Tower half; Phase 1 demo script | PFP P1-R (:925-932), §5 (:1123-1144) | The full story rehearsed twice on the user's Windows machine | M |
| K7 | Mock/production parity (seams manifest, assert-no-mock, recorded transcripts) | F-03, F-04, F-17 | PFP P1-M (:370), P2-2 (:967-982) | "The mock cannot show something production would not" | L |
| K8 | Editing the profile | F-09 edit, E-09 | PFP P2-3 (:984-991) | "Edit a DAG's agent configuration through a two-person change request" | M |
| K9 | Ceiling promotion (step b) | F-11 (b) | PFP P2-5 (:1010-1024) | §5 step 9b | M |
| K10 | Robustness | F-14 | PFP P2-7 (:1044-1051) | Pipeline lock, per-DAG SLA, alert sink | M |
| K11 | The rest of the explorer: evaluations, feedback, annotations | F-05 (close), F-22 | PFP P2-1 (:940-965) | §5 step 11 (folded into step 10 by R4-7) | L |
| K12 | Final readiness and whole-build cross-examination | P2-R; R5 retroactive | PFP P2-R (:1053-1055) | The final demo script, rehearsed | M |

**Stop points.** After K6 the demo story is complete and rehearsed. The user decides whether to continue to K7–K12 before the orals. Each package also ends with the user committing (§6), so stopping after any package leaves a coherent tree.

**Not in this plan** (unchanged roadmap; BACKLOG L-01 to L-16b): real warehouse data planes, real model keys (L-14), the Data Governance agent (L-06), the knowledge graph (L-07), break-glass access, production IdP (F-19), and PHI redaction and retention for the trace tables (L-16b).

---

## 3. Seats (Claude Code subagents)

The orchestrator is the main Claude Code session. It never builds; it sequences, freezes, gates and writes the blackboard. The seats are defined in `.claude/agents/`. Each one is composed from the framework persona cards (B1/B2/R1–R5) using the six-block prompt standard: ROLE, INHERITS, CONTEXT MANIFEST, SCOPE, DoD, OUTPUT.

| Seat | Agent file | Framework persona | Owns (PFP §4, :1059-1119, is binding) | Tools |
|---|---|---|---|---|
| Architect | `architect.md` | B1 Spine | Package design refresh, the reserved namespace, the golden-path example, technology-selection records, `docs/seams.yaml` and `tests/t0/test_seams*.py` (K7) | Read, Grep, Glob, WebSearch, WebFetch; Write limited to `docs/design/packages/`, `docs/seams.yaml`, `tests/t0/test_seams*.py` |
| Backend-Engine (BA) | `backend-engine.md` | B2 | Default: `runtime.py`, `engine/*`, `intervention/*`, `obs/tracer.py`, `cp/schema.py`, `cp/agent_schema.py`, `backtest.py`, `cli/main.py`, the simulator, `synthetic/**`, `tests/plugins/**`, `run_demo.ps1`, `stop_demo.ps1`, `pyproject.toml`. **Per-package ownership follows PFP §4 (:1065-1119), which moves `runtime.py`, `evaluate.py:318` and `modes.py` to BC in P1-3 (K2).** | Full |
| Backend-LLM-Obs (BB) | `backend-llm-obs.md` | B2 | `llm/*`, `obs/explain_service.py`, `cp/trace_schema.py` (from R4-5 on), `api/obs_routes.py`, `api/contracts/obs.py`, `tools/gate.py`, `tools/record_fixtures.py`, `tests/parity/**`, and the recorded mock transcripts `tower/src/lib/mock/recorded/**` (PFP §4 :1090) | Full |
| Backend-API-Gov (BC) | `backend-api-gov.md` | B2 | `api/app.py`, `api/catalog_routes.py`, `cp/governance_schema.py`, `governance/*` (including `profile.py`), `harness/*` (including `dbt_runner.py`, `gate.py`), `adapters/dag/*`, `hitl/modes.py`, `pipelines/**`; plus whatever PFP §4 gives BC per package | Full |
| Tower | `tower.md` | B2 (TS + TO + TP merged) | `tower/**` | Full |
| Red team | `red-team.md` | R1–R4, R1 rotating per package (§4.4) | — (read-only) | Read, Grep, Glob |
| Cross-examiner | `cross-examiner.md` | R5 | — (read-only) | Read, Grep, Glob |
| Scope reviewer | `scope-reviewer.md` | Scope challenge (user rule) | — (read-only) | Read, Grep, Glob |

**Ownership rule.** One seat owns each file in a package. **The per-package column of PFP §4 (:1065-1119) decides ownership; the seat files list defaults only.** Every seat brief names its files for that package, with the PFP line that assigns them. A seat that needs a change in a file it does not own asks the orchestrator, which routes the request to the owner or re-assigns the file *for that package only* and records the re-assignment on the blackboard. This is the rule the 2026-10-24 R4-5 mis-brief broke (blackboard entry "R4-5 brief correction").

**Concurrency rule (learned 2026-10-24).** All seats work in one working tree, so:
1. Within a package, backend seats run **one at a time** in the PFP serial order (BA → BB → BC unless the package says otherwise). The Tower seat may run in parallel with a backend seat only when the package's file lists are disjoint.
2. Seats run **targeted** tests only: their own files plus the blast-radius list. They take the test lock (§5.2). They **never** run the full suite.
3. The full suite runs only once, at the gate, by the orchestrator, after every seat has confirmed it is idle (the freeze). A gate whose code hash differs between start and end is void (blackboard 2026-10-24).

**Autonomy tier (E18), stated honestly:** **T0.** There is no CI and no independent merge control. The user runs every git command. Agent-authored red-team passes and smoke tests are evidence, not verifiers. Nothing ships unattended.

---

## 4. The wave: how every package runs

Run a package with `/package K<n>` (`.claude/commands/package.md`). The command walks these steps and stops where marked **WAIT**.

### 4.1 Pre-flight (orchestrator) — WAIT
1. Re-read, in this order: `CLAUDE.md`, the SYNC section of `docs/blackboard.md`, this plan's row and section for the package, the PFP section it cites, and the BACKLOG rows it closes.
2. Establish provenance: platform, code hash (`python -c "from tools.receipt import code_hash; print(code_hash())"`), latest receipt, and whether Postgres is up.
3. **Map the blast radius before any edit.** For every object the package changes (table, view, function, endpoint, type, file), list its dependents with `file:line`. The PFP blast-radius lists are a starting point only: re-grep, because the code has moved since PFP was written.
4. State the frame in one line: what changes, what shape the output takes, what done looks like. Confirm the package is additive. **WAIT for the user's go.** The user's go at this point covers dispatching the architect, the red team and the build seats for this package. Steps 4.2, 4.5 and 4.6 add their own WAITs.

### 4.2 Design refresh and red team before the build
1. The **Architect** re-grounds the PFP section against the current code. It corrects stale `file:line` references, reserves names (tables, kinds, routes, types, test ids), writes the golden-path example, and adds a technology-selection record for any new dependency (K4: `@xyflow/react`). Output: a blackboard **Design** entry.
2. The **Red team (before)** and the **Scope reviewer** run in parallel against the refreshed design. They receive the design as a *question to attack*, not a premise. The scope reviewer must challenge whether the package is the right scope for the demo. Cap: one round, at most 35 tool calls each.
3. The orchestrator records their output verbatim on the blackboard (**Findings**) and decides (**Decisions**, with `basis · wrong-if · NOT-verified`). If a finding changes scope or contradicts PFP, **WAIT**: the user decides.

### 4.3 Build (seats, serial per §3)
Each seat receives a composed prompt (`.claude/commands/package.md` builds it from the seat file, this plan, the Design entry and the Decisions). Every seat must:
- build to the PFP section and the Decisions, and match the house patterns already in the code;
- ship tests with every capability, written with the adversarial test lenses (fail-closed guards, degenerate inputs, order independence, no silent degradation);
- keep **mock and production paths both working** (user rule): every new endpoint gets a mock route in the same shape, and every seam declares its mock and production behaviour;
- run targeted tests under the lock and report: files changed, dependents mapped, targeted counts, and the code hash at its start and end;
- stop and report instead of guessing when the brief and PFP disagree (Backend-C did this correctly on 2026-10-24).

### 4.4 Gate (orchestrator) — the only green that counts
1. **Freeze.** Every seat confirms it is idle with no unsaved edits.
2. Run `python -m tools.gate --label <package>` (§5.2). It hashes the tree, runs the full suite under the lock, runs the Tower gates, hashes the tree again, and writes the receipt. If the hashes differ, the gate is void; re-freeze and re-run.
3. Live smoke for any package that changes a screen: `node tower/scripts/smoke.mjs` against the **live** API as well as the mock. The orchestrator looks at the screenshots itself.

### 4.5 Red team after the build and cross-examination (capped)
1. The **Red team (after)** attacks the built diff, not the plan. It uses R2 (what is absent), R3 (stub legitimacy: could the mock or demo mislead?), R4 (security, scope, concurrency, PHI) and the rotating R1 seat:

   | Package | R1 domain seat |
   |---|---|
   | K0, K8, K9 | AI governance / model-risk (earned autonomy, adverse-action HITL) |
   | K1, K2, K3, K4 | Data platform / orchestration (dbt, Airflow, lineage, gating semantics) |
   | K5 | T-MSIS / Medicaid data operations (provider enrollment feed realism) |
   | K6, K12 | Demo-integrity / honesty (does every screen tell the truth?) |
   | K7 | Stub legitimacy (R3 doubled) |
   | K10 | Operations / SRE |
   | K11 | Evaluation science (eval design, drift, feedback loops) |

   Every blocking finding must name a concrete failing input or a `file:line`. Findings are tagged Important, Nit or Pre-existing.
2. The **Cross-examiner (R5)** returns UPHOLD or DEMOTE for each BACKLOG item the package marks DONE. For each one it asks: would the cited test fail if the feature were deleted?
3. Fixes go back to the owning seat. Then re-freeze and re-gate. **Round cap: 5 build↔panel rounds for this build** (user decision). After 3 rounds on the same finding, the orchestrator reconsiders the approach instead of patching line by line.
4. A **Critical** finding closes only after: two independent refutation attempts fail, a regression test is shown to fail on the unfixed code, and the red team re-attacks the fixed code and cannot reproduce it.

### 4.6 Close (orchestrator) — WAIT
1. Update BACKLOG: DONE rows cite the receipt and test ids. Demoted rows return to their true status.
2. Update the blackboard: the package closure entry and the next "Active now" row.
3. **Report what would be staged**: the list of new and modified files, the untracked generated files that must *not* be staged, and confirmation that the naming guard passed. **The user runs git.** Never stage, commit or push.
4. If the package ran in the cloud: sync to `C:\demo\medicaid-dq-agent` (§5.3) and verify the receipt is there.
5. **WAIT:** the user commits and says go for the next package.

---

## 5. Gates, receipts and sync (both platforms)

### 5.1 Environment

| | Windows (user machine) | Linux (cloud session) |
|---|---|---|
| Postgres | Portable PostgreSQL 16 via `run_demo.ps1` (no Docker), `127.0.0.1:55432`, `.pgdata` in the repo | `su pgrun -c ".../pg_ctl -D /home/pgrun/pgdata -o '-p 55432 -k /tmp' -l /home/pgrun/pg.log start"`, socket `/tmp` |
| Test env | `DQ_PG_HOST=127.0.0.1`, `DQ_PG_PORT=55432`, `DQ_PG_USER=dq_gov` | defaults |
| Python | `.venv` from `run_demo.ps1 -Dev`, which installs `[dbt,x12,dev]` so pytest is present (G0). Today the script installs `[dbt,x12]` only (`run_demo.ps1:148`), so there is **no pytest** on Windows until G0. | system Python |
| Node | Node 20+, `tower/node_modules` | same |
| Test lock | `tools/gate.py` file lock (no `flock` on Windows) | the same lock (it replaces `flock /tmp/dq_pytest.lock`) |
| Deletes | Not permitted from the bridge; archives go to `_to_delete\` | normal |
| Long commands | The gate takes 30–60 minutes. Claude Code must start it with `run_in_background` (the Bash tool times out at 10 minutes) | same |

### 5.2 The gate tool (built in G0 by Backend-LLM-Obs; red-teamed before K0)
`tools/gate.py` replaces the hand-run `flock … tools.receipt` sequence. It is one command that behaves identically on both platforms (framework hardening: one canonical gate).

**Two hashes, kept separate:**
- `code_sha256` (`dqagent/obs/tracer.py:40,70-75`) is the **evidence identity**. It covers `dqagent`, `rules`, `config`, `pipelines/w1_dbt/models` and `tests`, and only `.py`, `.yaml`, `.yml` and `.sql` files. It stamps rows and evidence, so it stays as it is, apart from the portability fix below.
- `gate_tree_sha256` (new, in `tools/gate.py`) is the **freeze check**. It covers every file that is not gitignored, including `tower/**`, `tools/**`, `synthetic/**` and `pipelines/**` of every type. Tower edits during a gate therefore void it (red team C1).
- **Portability (red team C2):** both hashes use `relative_to(...).as_posix()` and normalise CRLF to LF before hashing. A `.gitattributes` file pins `* text=auto eol=lf`. Without this, a Windows checkout hashes differently from the cloud.

Steps:
1. Take a cross-platform exclusive lock: `msvcrt.locking` on Windows, `fcntl.flock` on POSIX. The lock file is `.gate.lock` in the repo, and it is gitignored.
2. Record `code_hash()` and `gate_tree_sha256` at the start.
3. Run `tools.receipt` (the suites `tests/t0`, `tests/t1`, `tests/synthetic` plus JUnit).
4. With `--tower`: `npx tsc --noEmit`, `npx eslint`, `next build` for mock and live (E16), and the smoke test in mock.
5. Record both hashes at the end. If either differs from the start, write the receipt with `"void": true`, exit non-zero and print "tree changed during gate: freeze and re-run".
6. Print the receipt path, the counts and the hash. Skipped tests are never counted as passes.
7. `--targeted <paths>` runs only the given test paths under the same lock, for seats. It writes no receipt and never counts as a gate.
8. Also in G0: add `.gate.lock` to `.gitignore`. Add `-Dev` and `-DbOnly` switches to `run_demo.ps1` (Backend-Engine): `-Dev` installs `[dbt,x12,dev]`; `-DbOnly` starts portable Postgres without the API or Tower.

`tests/t1/test_gate_tool.py` covers it:
- a void-on-hash-change negative control, run separately for an edit under `tower/` and an edit under `dqagent/`;
- the same tree hashed with CRLF and LF line endings, and with Windows-style paths, gives one hash;
- the lock being exclusive across two processes;
- the receipt fields.

### 5.3 Sync (cloud → Windows), only when a package ran in the cloud
1. After the user's go at §4.6, package the repo excluding `node_modules`, `.next`, caches, `.pgdata`, `.venv`, `*.duckdb` and `.smoke-shots`. Include a **deletion manifest**: files present in the last synced tree but removed in the cloud.
2. Write it through the bridge to `C:\demo\_to_delete\`, then extract it **in the bridge's Linux shell** with `tar xzf --overwrite`. Native Windows `tar` (bsdtar) does not accept `--overwrite`; if extracting natively, use `tar -xzf` and accept the overwrite. Show the deletion manifest to the user, who removes those files, because the bridge cannot delete.
3. Verify that the receipt and two changed files exist on the device, and that the device's `gate_tree_sha256` equals the cloud gate's (possible only after G0).
4. The user deletes `_to_delete\` themselves.
5. Sync only gated trees, unless the user explicitly asks for an ungated sync. In that case, label the sync UNGATED on the blackboard and in the report.

### 5.4 Standing gates in every package's Definition of Done
- **E5:** the full suite is green, run by the orchestrator, with the same hash at start and end.
- **E16:** `next build` passes for mock and live whenever `tower/` changed.
- **The naming guard** `tests/t0/test_generic_naming.py` is green. No client or agency names appear anywhere in the repo; the tenant profile stays outside it.
- **Pinned invariants:**
  - w1 outcomes stay at 420 released / 291 held / 47 pending dependency (`tests/t1/test_w1_outcomes.py`);
  - the I-3 agent-eval digests are unchanged unless the package says why;
  - the nightly earn story still works with K=15 and with K=0.
- **E14 wired path:** every new capability is reachable from a real entry point (CLI, API or night run), not only from tests.
- **E15 parity:** every new route has a mock in the same shape (mechanised from K7 onwards).
- **E12:** every DONE row in BACKLOG cites a passing test.

---

## 6. Package specifications

Each spec gives what PFP does not, or what has changed since PFP was written: the current state, the steps in build order, the seats, the tests, the blast radius, the demo moment and the exit criteria. Unless a step says otherwise, PFP's text applies.

### G0. A gate that can be trusted on both platforms
**Why first:** the red team showed that the current freeze check cannot see Tower edits (C1). It also showed that Windows and Linux hash the same tree differently (C2), and that the Windows venv has no pytest (C3). Every later receipt depends on these being fixed.

**Steps:**
1. **Backend-Engine:**
   - `tracer.py` code hash: `as_posix()` and CRLF normalisation. The I-3 digests and `code_sha256` values change once; record the old and new hash pair on the blackboard.
   - Add `.gitattributes`.
   - Add the `run_demo.ps1` switches `-Dev` and `-DbOnly`.
2. **Backend-LLM-Obs:** `tools/gate.py` and `tests/t1/test_gate_tool.py`, per §5.2.
3. **Red team, before first use:** attack the gate itself. Can it report green on a changed tree? Can it count skips as passes? Is the lock really exclusive on Windows?
4. **The first gate with the new tool**, on the current tree. Expect the four unverified R4-5 test files (K0) to fail. G0 is accepted if the gate tool and hash tests pass and every failure is one of the known K0 items.
5. **Windows:** the user runs `run_demo.ps1 -Dev -DbOnly`, then `python -m tools.gate --targeted tests/t1/test_gate_tool.py tests/t0`, so that portability is proven on the real machine early.

**Exit:** gate-tool tests green on both platforms, and the same `gate_tree_sha256` on both for the same tree.

### K0. Finish evidence-gated promotion and the eval recorder
**Why first:** the cloud tree is half-migrated (§1). Nothing else can be gated on top of it.
**Platform:** either. The partial tree is on both.

**State inherited (Backend-LLM-Obs report, 2026-10-24, tree `8c2934ff…`):**
- **Built, with its 9 tests passing on their own (11 with parametrisation):**
  - `cp/trace_schema.py` `evidence_ddl()`: `eval_runs`, `eval_run_record`, the 5-argument `eval_attest`, the views `evidence_complete`, `track_record_by_version` and `track_record`, and `promotion_minimums` / `promotion_refusal`;
  - `governance_schema.py`: the old definitions removed, refusals in `gov_decide`/`gov_apply`, and the POLICY rule where raising the minimums needs one approver and lowering needs two;
  - `registry.py`;
  - `app.py`: `/autonomy` versions and the attestation contract;
  - `config/state/ZZ/policy_w1.yaml`: the demo minimums.
- **Rewritten, unverified:** `test_parte_cp`, `test_parte_redteam_fixes`, `test_parte_earn`, `test_parte_api`.
- **Not done:**
  - `test_agent_eval` (its `promote()` path will now be refused);
  - no product writer for `eval_runs`;
  - the Tower attestation form and types;
  - the earn story needs 5 human-approved releases before promotion.

**Steps:**
Steps run in this order: 1 (recorder), 2 (simulator), 3 (tests), 4 (API), 5 (Tower), 6 (gate). Each step depends on the one before it.

1. **Backend-Engine:**
   - add `dqagent eval run --suite agent_eval|explainer` (PFP P2-1 :950, `tests/plugins/eval_recorder.py`). It runs the suite, then writes `eval_runs` through `eval_run_record` with `source_mode` (DETERMINISTIC, or LIVE only when a real transport is configured), `code_sha256`, `prompt_version` and the JUnit hash.
   - Add a test: a SCRIPTED or TEMPLATE explainer run is refused by `eval_attest` (R4-5 test 5 already covers the SQL; this one covers the CLI).
   - **Evidence integrity (red team I9):** `eval_runs` is append-only. `source_mode` is derived by the recorder from the transport that actually ran, never accepted from the caller. Only `dq_eval_svc` can write. Test: a direct INSERT or UPDATE by any other role is refused, and a SCRIPTED run can never be recorded as LIVE.
   - Today only the test helper `tests/t1/evidence.py:20` writes `eval_runs`. The recorder becomes the product path, and the helper is changed to call it.
2. **Backend-Engine (simulator):** the earn story (`--earn-story`) performs the 5 human-approved releases at L2 and an `eval run` before the L2→L3 promotion. The story must still pass with K=15 and with K=0.
3. **Backend-LLM-Obs:**
   - run `test_parte_cp`, `test_parte_redteam_fixes`, `test_parte_earn` and `test_parte_api` under the lock, and fix them to R4-5 semantics; `test_parte_earn` asserts that the refusal happens before the evidence exists;
   - fix `test_agent_eval`: the promotion step creates a deterministic evidence bundle through the step-1 recorder.
   - **The I-3 agent-eval digests MUST be unchanged.** PFP R4-5 §5 (:1333) says `observe()` does not read `track_record`. A changed digest is a finding to fix, not a note.
4. **Backend-API-Gov:**
   - `GET /evals` and `GET /eval-attestations?status=PROPOSED` (E-13);
   - the mock routes for both;
   - confirm that `/autonomy` exposes `evidence` and `minimums` for the Tower.
5. **Tower:**
   - The attestation form picks runs from `GET /evals` and shows the deterministic and LLM halves.
   - The pending-attestations list (E-13).
   - The Agents screen shows the track record per code version, the minimums ("demo minimums" label) and the evidence status.
   - It shows the refusal message verbatim when promotion is refused.
   - Update the mock in the same shapes.
6. **Gate:** `python -m tools.gate --label partF-K0 --tower`, plus a live smoke of the Agents screen.

**Blast radius to re-verify:**
- `tier_of`;
- `track_record_snapshot` (`registry.py`);
- every reader of the old `track_record` columns (keep the "prior" columns, R4-5 §3);
- `test_parte_*`;
- `test_sim_nightly`;
- the I-3 digests;
- the Tower Agents screen and its mock.

**Demo moment (R4-7):**
1. Run `dqagent eval run --suite agent_eval`.
2. Propose the attestation; a second person confirms it.
3. The promotion change request shows "evidence ✓ (deterministic, demo)" and "record 5/5 ✓".
4. Before the bundle exists, the database refuses the promotion. Show that refusal live.

**Exit:**
- the gate is green and not void;
- the R5 verdicts are UPHOLD on R4-5, E-13 and E-07;
- the backlog is updated;
- the tree is synced to Windows after the user commits.

---

### K1. Per-DAG agent profile (read-only)
**Answers the user's question:** "where is the agent configuration as we add new DAGs?"
**Spec:** PFP P1-2 :697-731, unchanged. It runs on the harness pipelines (`harness.pipelines[]`) until K2 introduces PIPELINE items.

**Steps:**
1. **Backend-API-Gov** (PFP P1-2 "Owners: BC", :700): `governance/profile.py` `compose_profile(...)` with `as_of`; `api/catalog_routes.py` `GET /catalog/pipelines/{id}/profile?as_of=`.
2. **Backend-Engine:** stamp `runs.profile_digests` on NIGHT, PIPELINE_RUN and BACKTEST roots in `tracer.py` and `runtime.py`. PFP §4 lists no P1-2 owner for these files (:1067, :1074), so the orchestrator records this assignment on the blackboard.
3. **Backend-LLM-Obs:** record `p1_profile.json` from the live API with `tools/record_fixtures.py` (PFP :722, §4 :1090). The Tower mock serves the recording, not a hand-written fixture.
3. **Tower:**
   - `app/pipelines/[id]/profile/page.tsx`: dense two-column layout;
   - "not governed" chips on monitors (L-10);
   - an "as of" selector fed by night traces;
   - a link from the night trace header ("ran under profile <digest>");
   - a mock route that serves the recording.
4. **Tests:** the four PFP tests, plus a scope test showing a SNAP-only reviewer cannot see a Medicaid pipeline's rules.

**Exit:** the gate is green. The live smoke shows the profile digest on the night trace equal to the profile page's digest.

---

### K2. Pipelines-lite and the consumer DAG (three waves, one gate per wave)
**Spec:** PFP P1-3 :734-885. Its highest-risk design (ORCH-1) is already adjudicated. The user chose React Flow for K4, so here the Catalog is a **table** (accessible default, DEC-020).

**Wave K2a: database and governance (Backend-API-Gov)**
- PIPELINE and TASK_GATE kinds, shape validation, and refusal of literal secrets.
- The `gov_item_lower_only` matrix (:740-755).
- `gated_dataset_introductions`, `_introduce_datasets`, `task_gate_ceiling` and `autonomy_tier_for` (:757-799).
- The genesis split (:805-810) and the scoped reopen (:811-815).
- Tests: `tests/t1/test_pipelines_db.py`, all 11 PFP tests.

**Wave K2b: runtime and the consumer DAG (Backend-API-Gov, which owns `runtime.py`, `evaluate.py:318`, `modes.py` and `harness/*` in P1-3 per PFP §4 :1067-1071, :1086)**
- The `modes.autonomy_for(relation=)` and `evaluate.py` changes. **Re-locate the PFP line numbers first**: `evaluate.py` changed in T1-traces.
- `dbt_runner.held_models`, `harness/gate.py`, `gate_checks`, the `dq_discovery_svc` role.
- `adapters/dag/dbt_files.py`, `dag_snapshots`.
- `pipelines/tmsis_outbound/` (about 6 dbt models; the manifest is committed, so the demo needs no dbt install).
- The PIPELINE_RUN span after the drain.
- Tests: `tests/t1/test_pipelines_runtime.py` (including the I-10 test with its negative control), `test_dbt_gate` advisory, and `tests/t0/test_dbt_files_adapter.py`.

**Wave K2c: API and Tower (Backend-API-Gov, then Tower)**
- `catalog_routes.py`: pipelines, nodes, discover (paths confined to `pipelines/`) and wire (:830-839).
- Backend-LLM-Obs: record `p1_catalog.json` and `p1_wire.json` (PFP :845).
- Tower:
  - Catalog table grouped by provenance, with a dependency-order strip;
  - a nodes drawer with the Effective tier column;
  - the wiring wizard (:844);
  - a link from the profile;
  - mock routes `catalog.ts` and `wire.ts` that serve the recordings.
- The night trace shows the PIPELINE_RUN span and the advisory "would HOLD" checks.

**Blast radius:** the PFP list at :875-882, plus the T1-traces additions: trace roots, `run_scopes`, and the attribute vocabulary for the new span names (`ATTR_VOCAB` in `trace_schema.py`; every new string attribute value must be added there).

**Demo moments:** §5 steps 6 and 7.
- Wire `tmsis_outbound` in advisory mode. The database refuses a single approver. After two approvers, the effective tier on the datasets it reads does not move.
- Run the next night. The trace shows the PIPELINE_RUN span with would-HOLD checks, and nothing is excluded.

**Exit:** three gates (K2a, K2b, K2c), one closing red team on the whole of K2, R5 on F-10 (lite) and F-12.

---

### K3. Backtest and promoting a gate to block
**Spec:** PFP P1-4 :888-922.
- Backend-Engine: `backtest.py` and the CLI subcommand.
- Backend-API-Gov: SQL, `cr_evidence`, and the `gov_decide` refusal.
- Tower: the promote page and the Backtest block on the change request.
- The backtest runs in sandbox control planes (`dqbt_<cr>_base` and `_cand`). It **must drop them** and must never write to the live control plane except through `backtest_put`.
- On Windows, the sandboxes must work against portable Postgres. The test that proves the backtest never touches the live control plane also runs on Windows in K6.

**Demo moments:** §5 steps 8 and 9a.

---

### K4. Pipelines graph (React Flow) and the demo estate
**Spec:** PFP P2-4 :993-1008.

**Steps:**
1. **Architect:** the technology-selection record for `@xyflow/react`:
   - a recall scan (any newer dominant option?);
   - liveness: version, licence from the package LICENSE file, maintenance, and CVEs, each with its source URL;
   - fit: client-only rendering, bundle size, accessibility.

   The version is pinned exactly. **WAIT** for the user if the record raises any concern.
2. **Tower:**
   - capture `bundle-baseline.json` **before** installing;
   - install; build the graph page (`next/dynamic`, `ssr:false`; a deterministic layout in `lib/graph/layout.ts`; arrow keys follow edges);
   - the table view is the default under reduced-motion or forced-colours, when saved as a preference, or when the first interaction is from the keyboard;
   - add `scripts/check-bundle.mjs` to `tools.gate --tower`.
3. **Backend-API-Gov:** `GET /catalog/pipelines/{id}/graph?overlay=gates`; `pipelines/demo_estate/` (about 64 models, all "discovered, not evaluated").

**Exit:**
- the bundle gate is green;
- the graph and the table show the same node set (a test);
- the live smoke includes a keyboard-only traversal.

---

### K5. New source added by YAML (Demo 2)
**Spec:** PFP P2-6 :1026-1042.
- **Backend-Engine:** add `Intake.delimited_file`; remove the three hard-coded pieces (`X12_RELATIONS`, the TRIAGE literal, the `_intersecting_elig_units` stub). **Re-locate each `file:line` first.**
- **Synthetic data:** add `synthetic/generate.py --provider`.
- **Onboarding:** add the change requests, under `config/` only.
- **Pin `w1_provider` on its first green run**, with the reason on the blackboard. w1 stays at 420/291/47.
- **Key test:** `test_new_feed_by_yaml_only` compares a dedicated hash of the tree **excluding** `config/` and `synthetic/` before and after onboarding. It cannot reuse `code_sha256`, which includes `config/`.
- **Track-record consequence (red team I5):** onboarding edits `config/`, which changes `code_sha256`, so the current-version record (`track_record_by_version`) starts again from zero. Before step 9b's L2→L3 promotion, the demo must re-earn the minimum record on the new version. Alternatively, the Architect proposes, and the user decides, whether governed config should be excluded from the evidence identity. That changes R4-5 semantics, so it needs a user decision before K5 starts.

**Demo moment:**
1. The provider files appear as UNROUTED_FILE items.
2. Onboard the source by configuration.
3. The files are ingested and held at H_IN under "task gate ceiling L1".

---

### K6. Demo readiness (the story is complete)

**Steps:**
1. **Backend-Engine:** E-11. `load_legacy_totals` takes `as_of` from the simulated night (PFP :930), with the test `test_parallel_run_as_of_matches_sim_clock`.
2. **Tower:**
   - N-T0-3: the UNROUTED_FILE and RECOVERY_DATA_LOST feed types, the deployment and idp chips, and a reason field on the recovery UI;
   - F-35 Tower half: switch LLM usage, run diff and unit trace to the new endpoints, and delete the in-browser aggregation;
   - the mock night trace names feed steps by feed key, never by file name.
3. **Orchestrator, on the user's Windows machine (E-03):**
   1. Run `run_demo.ps1` from a clean checkout: portable Postgres, `--nightly --earn-story --preload-nights 15`, then the API and the Tower.
   2. Run `python -m tools.gate --label partF-K6-windows --tower`. **This is the first gate on Windows.**
   3. Run `stop_demo.ps1`.
   4. Remove the label "untested on Windows" from both scripts only after this run passes.
4. **Demo-script gaps that no other package builds (red team I10):**
   - **Backend-Engine:**
     - a sim-clock "advance past the veto deadline" control, for §5 step 10a "let one window expire";
     - confirm that the demo binding sets `transport: scripted`, for step 3.
   - **Backend-API-Gov:**
     - the second presenter identity on `LocalIdP`, documented with its sign-in;
     - update BACKLOG E-03, which still says Docker.
5. **Orchestrator:** write `docs/rules/PARTF_DEMO_SCRIPT.md`. It holds the §5 rows for steps 1–10 as built (with R4-7 applied), the exact clicks, the sign-in identities per step, the expected screen, and a recovery note for each step that can go wrong.
6. Rehearse twice live on Windows, with screenshots of each step stored in `docs/receipts/demo_<date>/`.
   - The tenant name is shown only when the private tenant profile is loaded.
   - **Screenshots that show the tenant name stay outside the repo**, in the private folder.
   - Only generic-tenant screenshots go into `docs/`.
7. **Red team:** the demo-integrity lens. On every screen in the script, does anything claim more than the data supports? Look especially for: LIVE labels, "fact-checked", causal wording in run diff, and FAIL units shown as autonomous.

**Exit:**
- two clean rehearsals;
- the Windows gate is green;
- the user signs off the script.

**After K6 the user decides whether to continue to K7–K12.**

---

### K7. Mock/production parity
**Spec:** PFP P1-M :370 (`docs/seams.yaml`, assert-no-mock) and P2-2 :967-982 (recorder for every route, mock compile without `tsx`, the three parity tests, F-17 real sha256 hashes in the mock, and generated contract types).

**Seats:**
- **Architect:** `seams.yaml` and the two seam tests (E1).
- **Backend-LLM-Obs:** the recorder.
- **Tower:** the mock side and the generated types.

**Exit:** E15 is mechanised. A mock route whose keys or types drift from the live recording fails the gate. (K1 to K3 already serve recordings for their routes; K7 extends recording to every route.)

### K8. Editing the profile
**Spec:** PFP P2-3 :984-991. An edit drawer proposes changes through `Registry.propose`, which needs two people.
- E-09 baseline key: apply the rename **only if the `test_agent_eval` digests are unchanged** (ADJ-6). Otherwise ship only the read alias.

### K9. Ceiling promotion (step b)
**Spec:** PFP P2-5 :1010-1024, with its honest semantics:
- L1→L2 changes no unit's mode, only the trace line.
- L2→L3 opens veto windows, and only when the authority's tier is at least L3.
- E-07 is already closed by K0. K9 adds the gate evidence to the snapshot.

### K10. Robustness
**Spec:** PFP P2-7 :1044-1051.
- A per-pipeline advisory lock.
- A PIPELINE `sla` field feeding the card's `sla_due`. This also fixes the red-team nit that `sla_due` is currently the cluster's creation time.
- `WebhookSink` raises in production when no URL is set; `LogSink` is used in the demo.

### K11. The rest of the explorer
**Spec:** PFP P2-1 :940-965, minus what T1-traces and K0 already built.
- Backend: `eval_cases`, `explanation_feedback`, `eval_candidates` and `/evals/diff`.
- Screens: Evaluations (history and diff), the feedback control on the card, and the Annotation queue.
- Fold in from F-34: rule spans carry authorities.
- **Exit:** F-05 and F-22 are DONE, with an R5 UPHOLD.

### K12. Final readiness
1. Extend the demo script with step 9b and rehearse it twice.
2. Run **R5 retroactively across every DONE row in BACKLOG**: re-check the claim against its test, and confirm the test would fail if the feature were deleted.
3. Promote open residual risks to the pilot backlog with owners: F-33 forgeable correlation, L-16b, N-T0-5 and F-27.
4. Produce a final receipt and sync.

---

## 7. Risk register for this plan

| Risk | Effect | Control |
|---|---|---|
| Seats editing during a gate | Void gates, wasted hours (happened 2026-10-24) | §3 concurrency rule. From G0, `gate_tree_sha256` covers the whole tree, including `tower/`. Before G0, the freeze check cannot see Tower edits, so no Tower seat may run during a gate |
| PFP `file:line` references are stale | Seats edit the wrong lines | Architect re-grounding in §4.2 |
| A brief contradicts PFP | Wrong build (the R4-5 mis-brief) | Seats stop and report; PFP wins on design; the orchestrator cites PFP lines in every brief |
| Windows-only failures (paths, line endings, the lock, portable Postgres, no pytest in the venv) | The demo breaks on the user's machine | `tools/gate.py` is cross-platform, and the K6 Windows gate is mandatory before sign-off |
| Cloud and Windows trees diverge | Confusion over what was proved | Every report states its platform and code hash; sync only gated trees (§5.3) |
| Scope creep from red-team findings | The package never closes | Round cap of 5; Important vs Nit tagging; scope changes go to the user |
| Demo overclaims (LIVE, fact-checked, causal) | Credibility in the orals | The K6 demo-integrity lens; I6 is fixed; labels come from the stored rows |
| Tenant name leaks into the repo | Breaks the user's naming rule | The naming guard in every gate; tenant screenshots stay in the private folder |
| `C:\demo\DQ` donor secrets | Exposes the client's credential | `CLAUDE.md` forbids reading, copying or staging anything under `C:\demo\DQ` |

---

## 8. Kit contents (this plan's companion files)

| File | Purpose |
|---|---|
| `CLAUDE.md` | Standing rules Claude Code loads every session: read order, git, naming, security, gates, ownership, round caps |
| `.claude/agents/*.md` | The eight seat definitions in §3 |
| `.claude/commands/package.md` | `/package K<n>`: runs §4 with its WAIT points |
| `.claude/commands/gate.md` | `/gate <label> [--tower]`: the freeze check, then `tools.gate` |
| `.claude/commands/redteam.md` | `/redteam K<n> before|after`: dispatches the red team, the scope reviewer and the cross-examiner in parallel |
| `.claude/commands/close.md` | `/close K<n>`: the backlog and blackboard updates and the list of files to stage |

`tools/gate.py` does not exist yet. G0 builds it.


---

## Addendum 2026-10-26: candidate package K-CL, the clean-up loop (not yet scheduled; needs the user's decision)

The demo storyboard (`C:\demo\DQ_Command_Tower_Storyboard.html`) puts clean-up at the centre of the data-quality story. What exists and what does not:

- **Built:** TRIAGE hypotheses (`agent/seats.py:171-221`); quarantine and release with signed and verified row sets (`hitl/decisions.py:254-271`, `intervention/publish.py:247-251`); automatic re-evaluation when a dependency is released (`runtime.py:401-411`); known-issue memory (`agent/memory.py`).
- **Missing:** the agent-notes panel on the card (F-45); the source-feedback loop (F-46); the Known issues screen (F-47).

**Proposed placement:** F-45 and F-47 are small Tower items and can join K6 (demo readiness). F-46 is a design-first package (architect, red team, then backend and Tower), proposed after K6 and before K7. **The user decides whether and where it enters the order.**


---

## Addendum 2026-10-28: package K-CQ, a clinical-quality lane for Controlling High Blood Pressure (added at the user's request; placement not yet decided)

**What this is.** One focused clinical-quality lane, built around the Controlling High Blood Pressure measure (CBP). It is **not** a general FHIR platform. Blood pressure is the right example because a code or unit problem silently lowers the State's quality score. That makes the value of data quality visible to a business audience in a way claims data rarely does.

**Additive.** This addendum changes nothing above it. G0–K12, K-CL and every invariant in §5.4 stand as written.

### 1. The agent has the right pattern but not the clinical pieces
Re-verified 2026-10-28 on the user's machine at code hash `d0e266ffef74…`, the tree of the K3 closing gate (receipt `20261028T141354Z_partF-K3`).
- **What exists:** versioned code sets for billing codes (ICD-10-CM, HCPCS, POS, NUCC taxonomy, NPPES deactivations, State-local), each load visible to rules only after two-person approval in the registry (`dqagent/codesets/loaders.py:1-14`). CPT has no loader by design (licensed). Rules, the Control Tower and the autonomy ladder all carry over unchanged.
- **What is missing, confirmed by two independent searches** (a content search across `dqagent`, `config`, `rules`, `tests`, `pipelines` and `tower/src`; then a file-name search plus a scan of every Python import in `dqagent`): FHIR, LOINC, UCUM, value sets, CQL and DEQM. `dqagent/terminology/` holds only an empty `__init__.py`.
- **So:** this is a new lane on the existing agent, not a rebuild.

### 2. The measure fixes exactly what a valid blood pressure looks like
- **The reading itself:** the FHIR blood pressure profile requires panel code LOINC `85354-9`, with systolic `8480-6` and diastolic `8462-4` as components. Units must be UCUM `mm[Hg]`, with category `vital-signs`.
- **The measure logic:** the FHIR version of the measure, CMS165FHIR, uses the most recent reading in the measurement year. It is controlled if it is below 140/90. With several readings on the same day it takes the lowest of each. Readings taken during inpatient stays or ED visits do not count, and no usable reading counts as "not controlled".
- **Why that matters:** a reading the measure cannot recognise does not drop out quietly. It counts against the State.

### 3. "Converted" covers three different kinds of change, each on its own rung of the autonomy ladder

| What arrives from the plan or provider | What the agent does | Autonomy |
|---|---|---|
| Unit sent as `mmHg` instead of `mm[Hg]` (UCUM codes are case- and bracket-exact) | Normalises the syntax; same unit, same number | Can earn "on its own" |
| Unit in kPa | Converts at 1 kPa = 7.50062 mm[Hg] and keeps the original value | Recommends, 1 person approves until it has a track record |
| Systolic and diastolic sent as two separate observations, or "120/80" as text | Reshapes them into one panel with two components | Recommends, 1 person approves |
| Local code such as `BP_SYS` instead of LOINC | Proposes a mapping to `8480-6` | Recommends, **2 people approve**, because the mapping changes measure results for every future file |
| Reading taken during an ED or inpatient visit | Flags it as excluded from the measure; not an error | On its own, explained in plain English |

**Design rule: the agent never overwrites what was sent.** It keeps the original, writes a normalised copy, and records where the copy came from (source record, transform and its version, approver). That keeps "the agent never edits anyone's data" true, and it gives auditors the full trail.

These rungs use the existing machinery only: floors, tiers, two-person approval and earned promotion (K0, K3). FAIL is never autonomous. A missing tier means L1.

### 4. "All the value sets" is bigger than it sounds, and it contains a licensing trap
- **Size:** the draft FHIR version of this one measure uses about 30 value sets. They cover essential hypertension (OID `2.16.840.1.113883.3.464.1003.104.12.1011`), the qualifying visits, and exclusions such as hospice, ESRD, pregnancy, frailty and palliative care.
- **Source:** they come from VSAC, which needs a UMLS account.
- **The trap:** visit value sets such as Office Visit include CPT codes, which are licensed (AMA). The agent deliberately holds no CPT data today ("zero rows by design"). The demo therefore uses fixture expansions labelled `FIXTURE_SYNTHETIC`, and production needs a CPT licence. A screen must never present a fixture expansion as the official one.
- **Versioning:** expansions change every year and must be pinned to the measurement period. The existing two-person code-set approval and semantic hashing extend to value-set expansions naturally.

### 5. DEQM is the exchange format, not the validation
- **What it is:** DEQM (currently STU5, v5.0.0) defines how measure data and results move between parties: submitting and collecting data, individual and summary MeasureReports, and gaps in care.
- **What it is not:** the measure page itself does not reference DEQM. The measure logic lives in CQL, and DEQM is the envelope.
- **In this package:** accept a DEQM "Submit Data"-shaped bundle as the input format, so the agent checks the data before it reaches measure evaluation. Show a summary MeasureReport-shaped result with the before-and-after control rate. Gaps-in-care exchange stays on the roadmap.

### 6. Scope and size
Large (XL), so it runs in three waves with one gate per wave unless the user compresses the cadence again (as for K2).
- **Wrap, don't rebuild.** A FHIR profile validator, a terminology service and a CQL engine are each sizeable. The Architect selects existing tools through a technology-selection record. Measure evaluation for the demo may be a deterministic implementation of the CMS165FHIR logic in §2, cited line by line, and labelled honestly as demo-scope evaluation, not a certified measure engine.
- **Keep the demo narrow:** one measure, a handful of seeded defects (the §3 table), and a before-and-after rate.
- **Get the governance right:** the code mapping and the unit conversion each need their own approval path in the Control Tower.

### Waves and seats (owners per PFP §4 conventions; the Architect re-grounds every `file:line` at design time)

**K-CQa: terminology and rules (Architect, then Backend-Engine and Backend-API-Gov)**
1. **Architect:** `docs/design/packages/K-CQ_DESIGN.md`, covering:
   - the clinical relation contract (synthetic FHIR R4 Observation, Encounter, Condition and Patient);
   - the technology-selection record (validator, terminology source, measure evaluation): recall scan, liveness, licence, fit;
   - the blast radius;
   - the decisions list.

   Then the red team and scope reviewer run before the build.
2. **Backend-Engine:** `dqagent/terminology/`:
   - a LOINC subset for blood pressure;
   - a UCUM unit table;
   - a value-set expansion loader pinned to the measurement period.

   Each load is a governed registry artifact with two-person approval, reusing the `codeset` pattern. CPT-bearing expansions load as `FIXTURE_SYNTHETIC` only.
3. **Backend-Engine:** clinical intake into bronze, with ABC rows for resources received against those loaded. This depends on K5 removing the hard-coded relation set (`X12_RELATIONS` and related), so that a new relation is added by configuration.
4. **Rules** (a new catalog file under `rules/catalog/`):
   - the profile rules: panel code, components, UCUM unit, category;
   - value-set membership;
   - the encounter-context exclusion (inpatient and ED).

**K-CQb: normalisation and measure (Backend-Engine, Backend-API-Gov)**
5. **Normalised copy plus provenance.** Original rows are never updated. The four transform kinds in §3 are governed artifacts, each with its autonomy floor:
   - syntax fix: eligible to earn A;
   - kPa conversion: H_IN until it has a track record;
   - reshape: H_IN;
   - local-code mapping: H_IN_2.
6. **Measure computation:** the CBP denominator and numerator per §2, computed before and after normalisation. Counts follow the existing small-cell rule: no bare 1–10 counts.

**K-CQc: API, Tower and demo (Backend-API-Gov, then Tower)**
7. **API:**
   - the clinical lane on decision cards;
   - a measure-impact endpoint;
   - the same-shape mock route (DEC-022).
8. **Tower:** a measure-impact panel showing the rate before and after, and per defect: what arrived, what the agent did, its autonomy rung, and who approved.
9. **Synthetic data:** a clinical generator with the §3 defects seeded, plus controls. **w1 stays at 420 / 291 / 47.**

### Tests (minimum)
- one test per transform kind;
- **never-overwrite:** the original bytes are unchanged and a provenance row exists;
- value-set version pinning;
- fixture labelling: a CPT-bearing fixture is never shown as official;
- the measure logic: most recent reading, the lowest of each on the same day, ED and inpatient excluded, no reading means not controlled;
- the standing invariants: w1 outcomes, the I-3 digests, tracing never changes an outcome, mock/production parity.

**Gate time:** the full gate already takes about 6 hours (K3). The new tests must be fast, and any slow test is named in the design with its expected runtime.

### Demo moment
"Three data-quality defects were silently lowering the Controlling High Blood Pressure rate. The agent fixed the safe one on its own, one person approved the unit conversion, and two people approved the code mapping. The rate moved from X% to Y%, with every fix approved and traceable."

X and Y come from the synthetic run; they are never hand-written.

### Not in K-CQ
- a general FHIR platform;
- other measures;
- a live VSAC connection in the demo;
- a certified CQL engine;
- gaps-in-care exchange;
- any real patient data (synthetic only; trace tables carry counts and references, never values).

### Decisions for the user (before K-CQ starts)
1. **Placement.** The proposal is after K5, because clinical intake needs configuration-driven relations. It could go before K6 if the clinical story is to be rehearsed for the orals, or after K6 as an extension.
2. **Runtime.** If the selected validator needs a Java runtime on the user's machine, installing it is a system change under SDLC-1 / INC-001 and needs the user's decision. The alternative is Python-only validation for the demo.
3. **DEQM scope.** Input bundle and summary MeasureReport only (proposed), or nothing in the demo.
4. **The measurement year** to pin value-set expansions to.

**Exit:**
- a green, non-void gate per wave (or one compressed gate, if the user so decides);
- R5 UPHOLD on the never-overwrite rule and on the honest labelling of fixture value sets and demo-scope measure evaluation;
- the demo moment rehearsed with the numbers taken from the run.

**Sources (primary):**
- [FHIR R4 blood pressure profile](https://hl7.org/fhir/r4/bp.html)
- [US Core Blood Pressure profile](https://www.hl7.org/fhir/us/core/Observation-blood-pressure.ttl.html)
- [CMS165FHIR, draft dQM content IG](https://build.fhir.org/ig/cqframework/dqm-content-cms-2025/Measure-CMS165FHIRControllingHighBP.html)
- [CMS165v14 on the eCQI Resource Center](https://ecqi.healthit.gov/ecqm/ec/2026/cms0165v14)
- [DEQM IG, STU5](https://www.hl7.org/fhir/us/davinci-deqm/toc.html)
- [2024 Medicaid Adult Core Set](https://www.medicaid.gov/sites/default/files/2024-02/2024-adult-core-set.pdf)
