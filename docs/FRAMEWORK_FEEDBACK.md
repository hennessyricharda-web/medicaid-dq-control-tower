# Framework feedback — the coalition-build-framework's own learning loop

Maintained by the orchestrator, same discipline as `docs/INCIDENTS.md`. This file is NOT about this demo's defects (those go in `docs/BACKLOG.md`) — it's about gaps in the **process itself**: the gates, seat rules, and EXECUTION_PLAN conventions this build runs under, which this build is also being used to pressure-test. The user asked explicitly, 2026-10-29, to capture these as they're found, to exercise the framework's own feedback/learning loop, not just ship around them quietly.

Each entry: what happened, why the existing gates/rules didn't catch it, root cause, disposition, and the fix-at-source change proposed for the framework (`CLAUDE.md`, the seat files, EXECUTION_PLAN's template conventions, or the `coalition-build-framework` skill itself) — distinct from any fix applied to this project's own docs.

---

## FB-1: a gate was built for a non-demonstrable moment, and would have needed its own system-boundary escalation (2026-10-29)

**What happened.** BACKLOG F-57 scoped K6's exit criteria to require a "Windows gate": actually running `run_demo.ps1` from a clean checkout — which boots a local Postgres from cold (`initdb`/`pg_ctl start`) — as a mandatory, scripted, sign-off-blocking step (`EXECUTION_PLAN.md:417-418`, "the Windows gate is green... mandatory before sign-off"). The orchestrator began executing toward this via computer-use on the user's real desktop, under the user's explicit approval to drive it that way. Only then did the user stop the plan, on two grounds that were both valid and both missed earlier: (1) the live demo itself never re-enacts cold stack bring-up — the audience only sees "Run next night" against an already-running stack, so gating on this tested deployment plumbing, not anything the demonstration shows; (2) starting a system service (even as part of a "gate") is exactly the action `CLAUDE.md`'s System boundary requires escalating to the user *before* taking — folding it into an already-approved "drive via computer-use" plan was the same category of mistake INC-002 already named, just one step removed.

**Why the existing rules didn't catch it.** Nothing in the before-build red team, the package design step, or `EXECUTION_PLAN.md`'s own authoring asks "does this exit criterion exercise a moment the actual demonstration will show, or does it test infrastructure the audience never sees?" The System-boundary rule is written as a *per-action* checkpoint (stop before this specific install/service-start happens) — it was never checked at *gate-design* time, when the step requiring a service-start was first written into the plan as mandatory. By the time the boundary rule would have fired, the plan was already in motion and framed as "the user already approved computer-use for this," which is a different, broader approval than the specific action needed.

**Root cause.** Two gaps, not one: (a) no step in the before-build red team or package design asks whether a proposed exit criterion is demonstration-fidelity-relevant before it's written as mandatory; (b) the System-boundary rule has no check at *plan-authoring* time — only at *execution* time — so a gate that will require a boundary-crossing action can be designed, written into EXECUTION_PLAN, and carried for days before anyone notices the conflict.

**Disposition.** F-57 downgraded by user decision: one-time, off-camera, user-run manual check, not gated, not required before K6 closes. See `docs/BACKLOG.md` F-57 and `docs/blackboard.md`, 2026-10-29 entries.

**Fix-at-source proposed (not yet applied to the framework itself — this project's docs are fixed; the framework is not):** add a question to the before-build red-team checklist (or the design step itself) for any exit criterion that implies running real infrastructure: "does the live demonstration itself re-enact this, or only the build/verification process?" — and a second question: "does satisfying this criterion require any System-boundary action (install/service-start/stop/network-download)? If yes, flag it for user escalation at *design* time, not discovered later at execution time."

---

## FB-2: a standing required step (the live-API forensic screen audit) was silently skipped, and no gate caught it

**What happened.** `EXECUTION_PLAN.md:105` states a standing requirement: "Live smoke for any package that changes a screen: `node tower/scripts/smoke.mjs` against the **live** API as well as the mock. The orchestrator looks at the screenshots itself." This was genuinely done for G0, K0, K2, and K3 (real Playwright sessions, real screenshots, reviewed by eye — the blackboard calls this "forensically verified" throughout). For K6 Front 3 (Tower changes: feed-kind rendering, chips, the deployment-field fix), the orchestrator ran `smoke.mjs --mode mock` only, never the live-API pass, and never visually reviewed a real screenshot — then reported the package's after-build red team as closed. This was caught only because the user separately recalled, from memory of the process, that a "forensics component" auditing screens was supposed to exist, and asked about it directly.

**Why the existing rules didn't catch it.** `tools/gate.py`'s automated gate does not check for or require a live-mode smoke run — the live-API pass is prose instruction to the orchestrator (`EXECUTION_PLAN.md:105`), not a mechanical check. Nothing in the red-team-after checklist, the package close checklist, or the orchestrator's own report format asks "was the live-mode pass actually run for this package, distinct from the mock-mode pass?" A mock-mode-only smoke result (180-182/182 passing) reads, on its face, exactly like success, so there was no natural prompt to notice the live pass was missing.

**Root cause.** The live-API audit is a documented convention the orchestrator is trusted to remember and self-apply every package, with no mechanical enforcement and no explicit line item in the package-close report that would show its absence. This is the same shape of gap INC-001/INC-002 already found for system-boundary actions (a real rule, stated in prose, with no check forcing it to be either done or explicitly skipped-with-reason) — just recurring in a different rule.

**Root risk this reveals in the mock/production-mode split now being planned (K7-K12 mock-only buildout, 2026-10-29):** if the live-API audit can be silently skipped once already, the same gap applies directly to whatever packages get built mock-only next — there needs to be an explicit, checkable marker that a package's screens were NEVER live-audited (by design, not by oversight), distinct from a package where the live audit was simply forgotten.

**Disposition.** Not retroactively re-run for K6 as of this entry — deferred, per the user's own decision on the rehearsal-count question, to be covered either by the single live rehearsal or folded into the later backend-build pass's own live-mode audit. See `docs/blackboard.md`, 2026-10-29 entries ("two rehearsals" → one, and the mock/production-mode discussion).

**Fix-at-source proposed:** every package-close report template gets an explicit, mandatory line (same pattern as INC-001's "did you install anything outside the repo tree? yes/no" fix): **"Live-API smoke/screenshot pass run for this package's changed screens? yes/no + evidence, or explicit reason it's deferred."** A missing or unanswered line should read as a non-closeable package, the same way a missing idle-confirmation or a void gate hash already does.

---

## FB-3: an exit criterion (two rehearsals) was more process than the actual residual risk justified, and no one noticed until asked

**What happened.** `EXECUTION_PLAN.md:417` set K6's exit criteria at "two clean rehearsals." When the user asked directly whether this was proportionate, the honest accounting was: the automated mock-mode smoke suite already covers DOM-wiring regressions on every build; the thing a rehearsal uniquely buys is real-backend + real-screen integration, exercised live — which is one specific gap (see FB-2), not two independent risks needing two independent live passes. Once that was named, one rehearsal covering that gap, not two, was the defensible number — but the framework's own process (package design, before-build red team) never asked "what specific risk does rehearsal #2 cover that #1 doesn't?" before writing "two" into the plan as a requirement.

**Why the existing rules didn't catch it.** The round-cap and red-team mechanisms in `CLAUDE.md`/the framework are built to catch missing coverage (a finding the red team raises when something is absent), not excess coverage (a requirement that duplicates a check already satisfied elsewhere). There is no symmetric "is this requirement double-counting a risk another control already closes?" pass.

**Disposition.** Downgraded to one rehearsal, user decision 2026-10-29. See `docs/STATUS_BOARD.md` K6 step 6 and `docs/blackboard.md`.

**Fix-at-source proposed:** add an explicit prompt to the package-design step (not just the red team, which looks for gaps): for every repeated/numbered verification requirement ("twice," "two independent passes," "N rounds"), name the DISTINCT risk each repetition is meant to catch that the others don't. If two repetitions are proposed to cover the same risk, that's a signal to question the count, not just accept the larger number as safer.

---

## FB-4: a honesty marker was content-correct but reachable at the wrong moment — red team checked the words, not when they'd be seen (2026-10-29)

**What happened.** The K8/K11 mock-only build gave every invented-capability screen a "PRE-BACKEND — NOT WIRED TO A REAL SYSTEM" banner — the right content, matching this project's own honesty invariants. It still shipped into the exact codebase `run_demo.ps1` runs for the real, in-front-of-an-audience demo, fully reachable (a nav click, or a direct URL) during that live run. The orchestrator's own after-build re-verification pass (code reads + viewing actual screenshots) checked that the banner said something true, and missed entirely that saying something true, to the wrong audience, at the wrong moment, is its own failure mode — arguably worse than the thing the banner was built to prevent. The user caught it ("that is time of death"), not any gate or red-team lens.

**Why the existing rules didn't catch it.** CLAUDE.md's honesty invariants ("never show LIVE... or causal wording the data does not support") are about CONTENT accuracy — is the claim true. Nothing in the red-team panel or the after-build checklist asks the orthogonal question: WHO can reach this content, and WHEN. A true statement shown to the wrong audience at the wrong time is a distinct risk from a false statement shown to the right audience, and this project's gates only ever checked for the second kind.

**Root cause.** The after-build verification discipline this session has been applying (confirmed throughout K0-K6 and this K8/K11 round) is built around "is the claim on screen true" — a content-correctness lens. It has no companion "is this screen reachable in the artifact that will actually be presented, in the state it will actually be presented in" lens — a reachability/blast-radius-of-visibility check, distinct from the file-level blast-radius check CLAUDE.md's pre-work gate already requires for code edits.

**Disposition.** Fixed same-day: a preview flag, off by default, never set by `run_demo.ps1`, gates every K8/K11 addition out of the default (live-demo) path, with self-guards on each page as defense in depth beyond just hiding the nav link. See `docs/rules/PARTF_K7_K12_MOCK_CONTRACT.md`, "CRITICAL FIX 2026-10-29."

**Fix-at-source proposed:** add a standing question to any package whose build target is the SAME codebase/artifact used for a live, in-person, or client-facing demonstration (not every build has this — a pure backend package doesn't): **"If this screen/control were clicked right now, by the actual presenter, in the actual demo run, what would the audience see — and is that acceptable?"** This is a reachability-and-timing check, separate from and in addition to the existing content-honesty check, and should run explicitly whenever new UI surface is added to a live-demo-serving codebase, not folded silently into the honesty check and assumed covered.

---

## Action items

| # | Action | Status |
|---|---|---|
| 1 | Add a "does this exit criterion match a moment the live demo actually shows?" question to before-build red team / design, for any criterion implying real infrastructure | **APPLIED** 2026-11-04 — `.claude/agents/scope-reviewer.md` item 6, `.claude/agents/architect.md` item 7 |
| 2 | Add a System-boundary conflict check at plan-authoring time (design/red-team-before), not only at execution time | **APPLIED** 2026-11-04 — `CLAUDE.md` System boundary section ("Exit criteria that cross this boundary..."), `.claude/agents/architect.md` item 7 |
| 3 | Add a mandatory "live-API smoke pass run? yes/no + evidence/reason" line to every package-close report template | **APPLIED** 2026-11-04 — `.claude/commands/close.md` step 1 |
| 4 | Add an explicit "which distinct risk does repetition #2 close that #1 doesn't?" question wherever a plan asks for N>1 repeated verification passes | **APPLIED** 2026-11-04 — `.claude/agents/scope-reviewer.md` item 7 |
| 5 | Add a reachability/timing check ("what would the live-demo audience see if this were clicked right now") for any build that adds UI to a live-demo-serving codebase, distinct from and in addition to content-honesty checks | **APPLIED** 2026-11-04 — `.claude/agents/red-team.md` ("Reachability and timing") |

Also folded into `CLAUDE.md` this session, from the cross-project (core platform + medicaid-dq-agent) combined-lessons review, 2026-11-04:
- A new **Verification** section generalizing INC-001/INC-002/FB-2 into one standing rule: no claim of "works"/"persists" is accepted without naming the specific before/after check, and self-report is never accepted as done on its own.
- A **bind-the-action-not-the-actor** principle added to the System-boundary section, directly addressing how INC-002's literal-reading gap arose, so the same shape of gap is checked for whenever a boundary rule is next amended.

These are now live in this project's own `CLAUDE.md` and seat files, not just proposed. The same combined-lessons review also produced equivalent additive edits to the whole-person care (`Finalrhtpdemo`) platform's own governance files, done separately in that repo.
