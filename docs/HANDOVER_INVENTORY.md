# Handover inventory — G0 through K-CQ, for a fresh Claude Code session

**Written 2026-10-29 by the orchestrator, for handing this build off to a new Claude Code session.** This is an index and a status snapshot, not the record of record — every claim below cites the document that actually proves it. Read in this order:

1. `CLAUDE.md` — binding process rules. Read this first, always.
2. This file — where every package stands, right now.
3. `docs/blackboard.md` SYNC section, then the newest entries — the attributed, chronological record.
4. `docs/STATUS_BOARD.md` — the live per-package table this file summarizes.
5. `docs/BACKLOG.md` — every open item, with an ID you can cite.
6. `docs/EXECUTION_PLAN.md` — the package specs themselves (§6), and the addenda for K-CL and K-CQ.

## Environment state, right now (check before doing anything)

- **Postgres is NOT running in this cloud session** (confirmed `pg_isready`/`psql` refused, 2026-10-29 17:5x CT). Restart per `CLAUDE.md`'s Environment section / `EXECUTION_PLAN.md` §5.1 before running anything Postgres-dependent (any real gate, K-CQ's deferred intake tests, the K-CQ governed-CR onboarding step). Starting it is a **System-boundary action** — `CLAUDE.md` requires escalating to the user first; this excerpt's own Postgres round (see K-CQ below) is the precedent for how that conversation goes.
- Tree is **not a git repo**. There is no commit SHA; the gate's own `code_sha256`/`gate_tree_sha256` stand in for one. Don't assert a code-tree claim without citing one of those or a direct `file:line` read.
- The single most important unresolved process debt, stated plainly: **no non-void, full `tools.gate --tower` run has completed since K3** (receipt `20261028T141354Z_partF-K3.json`, 1167/1168, 1 legitimate skip). Every package from K4 onward — K4, K5, K6, K7-K12's built pieces, and K-CQ — has closed on the orchestrator's own independent spot-verification (direct code reads plus targeted re-runs of the specific tests/smoke suites touched), per the user's own 2026-10-28 deferred-gating cadence decision, not on a fresh combined gate receipt. The one gate attempt since (`20261029T025220Z_partF-K4.json`) came back **void** (a concurrent-build hash mismatch, the orchestrator's own disclosed mistake) and was deliberately not re-run, consistent with that cadence decision. **A real, full, non-void `tools.gate --tower` run against a running Postgres, covering everything built since K3, has never happened.** This is accepted risk, not an oversight — but whoever picks this up next should not assume gate-level coverage exists where it doesn't.

## Package-by-package status

| Package | What it is | Status | Real gate evidence? | Open items (BACKLOG ID) |
|---|---|---|---|---|
| G0 | Gate tool trustworthy on both platforms | ✅ CLOSED | Yes — part of the chain leading to K3's receipt | — |
| K0 | Evidence-gated promotion + eval recorder | ✅ CLOSED | Yes (`20261025T015805Z_partF-K0.json` + r1) | E-07 (track record doesn't count L2 releases — OPEN, "likely K9"); E-13 (no pending-attestations endpoint — deferred to K11, not yet built) |
| K1 | Per-DAG agent profile (read-only) | ✅ CLOSED | Yes (`partF-K1` + r1-r4 receipts) | — |
| K2 | Pipelines-lite: catalog, ORCH-1, consumer DAG, wiring wizard | ✅ CLOSED (lite scope) | Yes (`20261027T094637Z_partF-K2.json`, 1149 passed) | Full React Flow graph was always K4's scope, not a K2 gap — delivered there |
| K3 | Backtest + promote a gate to block | ✅ CLOSED | Yes (`20261028T141354Z_partF-K3.json`, 1167/1168) | F-11c (NULL-swallowing in `gov_item_valid`, pre-existing, OPEN); F-11d (`_is_mode_only_cr` is CR-wide not per-item, OPEN) |
| K4 | Pipelines graph (React Flow) | ✅ CLOSED on independent re-verification, **no non-void gate receipt** | **No** — only attempt is void (`20261029T025220Z_partF-K4.json`) | F-49 (resolved); F-50, F-51 (test-timing issues, non-blocking, OPEN) |
| K5 | New source via YAML (Demo 2) | ✅ CLOSED | **No** — deferred to K6's combined gate, which never ran | F-52 (demo-script sequencing note); F-53 (must confirm `test_w1_outcomes.py` green — it was, independently, this session); F-54, F-55, F-56 (demo-scope-accepted, non-blocking, OPEN) |
| K6 | Demo readiness — steps 1-5 of 6 | ✅ steps 1-5 DONE; step 6 (one live rehearsal) **NEEDS the user's own machine, not gating close** | **No** — the promised combined gate never ran | F-57 (Windows cold-bringup gate, downgraded to an optional one-time off-camera check, non-blocking); F-58 (recovery-reason field never persisted, needs its own package); F-59 (resolved — documentation only); F-60 (dormant subject-format gap, demo path unaffected, OPEN non-blocking) |
| K7 | Mock/production parity mechanism | ⬜ **DEFERRED, full** | — | No real route exists yet for anything K8-K12 touches; building a parity test against invented routes would prove nothing |
| K8 | Profile editing | ✅ BUILT, independently re-verified | Targeted tests only, no combined gate | — |
| K9 | Ceiling promotion (step b) | ⬜ **DEFERRED, full** | — | Core capability already real (K0, live in K6); K9 itself is only a backend track-record-snapshot field |
| K10 | Robustness (advisory lock, SLA, webhook, recovery endpoint) | ⬜ **DEFERRED, full** | — | `sla_due` bug is real backend Python, not Tower-fixable in a mock-only pass; no UI exists for the rest |
| K11 | The rest of the explorer (eval history/diff, annotation queue, feedback) | ✅ BUILT, independently re-verified | Targeted tests only | Does not close F-05/F-22 (need a real-backend R5 pass) |
| K12 | Final readiness | ⬜ **DEFERRED, full** | — | Process-only package: step 9b, rehearsal, retroactive R5, final receipt all wait for the real backend pass |
| K-CL | Clean-up loop (addendum) | ⬜ **NOT SCHEDULED** | — | User hasn't decided whether/when to run this; see `EXECUTION_PLAN.md` addendum 2026-10-26 |
| K-CQ | Clinical-quality lane (Controlling High Blood Pressure measure) | ✅ CLOSED 2026-10-29 | **No** — Postgres-dependent coverage explicitly deferred (user decision); everything else independently re-verified by direct read + targeted re-runs | F-61 (live rehearsal, Postgres-gated); F-62 (no authority partitioning on 3 new endpoints); F-63 (mock-contract catalog not extended); F-64 (rules-catalog not wired to a state profile); F-65 (approve endpoint has no step-up, named exception); F-66 (Architect consolidation assessment of Tower's approval surfaces, not yet run); F-67 (`kpa_conversion`/`reshape` pending-row path has nothing that creates a row yet) |
| L-06a | Data governance: canonical-element classification-completeness check (`classification_completeness()`, `dqagent/governance/registry.py:112-121`) | ✅ DONE (evidence), 2026-10-29 | **No** — targeted tests only (19/20 pass, independently re-run, exact match) | F-68 (the 1 failing test is a real governance gap, not a bug — 68/119 canonical elements unclassified, most notably `CQP.MEMBER_ID`; needs a two-person CLASSIFICATION decision, not a build) |
| L-17 | Multi-cloud/DAG execution substrate for L-06's checks (Azure/AWS/Snowflake/Databricks) | ⬜ **SPUN OUT, UNSOLVED, not scheduled** | — | No BACKLOG row beyond the L-17 line itself; see `docs/design/packages/L17_GOVERNANCE_MULTICLOUD_SUBSTRATE_DESIGN.md` — 3 red-team rounds found no convergent credential model, held at the user's explicit instruction ("hold L17") |

No K13 exists — the plan's package list ends at K12. K-CL, K-CQ, L-06 and L-17 are all addenda, added after the original plan.

**2026-10-29, later the same day — see `docs/design/packages/NEXT_NON_BACKEND_BUILD.md`** for a classification of every item below by layer (backend / Tower / mixed / process-only) and a ranked, implementation-ready list of what a fresh Claude Code session can build on the **Tower side alone**, with no backend change needed first — the two strongest candidates (F-45, F-47) already have a real, governed backend API sitting unused.

## What a fresh session most needs to know

1. **The demo is clickable end to end for everything marked BUILT above**, in mock mode, without Postgres. `run_demo.ps1`/`stop_demo.ps1` boot it; every preview flag (`PREVIEW_K7_K12`, `PREVIEW_K_CQ`) defaults off, so the live-demo path is unchanged by any of this session's work unless a flag is explicitly set.
2. **Nothing built since K3 has a real combined gate receipt.** If the next session's job is to harden toward production, or if the user ever asks "did this actually all pass together," the honest answer is: not yet, by design, per the user's own cadence decision — and running one (with Postgres actually up) is the single highest-value next step before trusting this tree further.
3. **K7, K9, K10, K12, and K-CL are fully unbuilt**, not partially built — don't assume mock scaffolding exists for them.
4. **Every deferred item has a BACKLOG ID** (F-52 through F-67 cover K5 through K-CQ). Don't re-discover these from scratch; read `docs/BACKLOG.md` first.
5. **The orchestrator's own discipline this session caught real defects that seat reports and even red-team passes got wrong on first pass** (a nonexistent test-file citation, a false premise about a refusal mechanism, a misdiagnosed non-bug, an actual UI bug hiding a server-side refusal from ever being exercised). Don't accept a seat's "green" report, or a red-team verdict, without independently re-running or re-reading what it claims — that discipline is what kept this tree honest, and dropping it is the most likely way a fresh session regresses the quality bar here.
