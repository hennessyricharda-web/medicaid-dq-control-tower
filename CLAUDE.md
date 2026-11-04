# CLAUDE.md — standing rules for this repository

These rules load in every Claude Code session. They outrank the newest instruction in the conversation, except an explicit decision by the user. Re-read this file, and the SYNC section of `docs/blackboard.md`, after every context compaction **before** acting.

## Read order, every session
1. This file.
2. `docs/blackboard.md`: the SYNC section, then the newest entries.
3. `docs/EXECUTION_PLAN.md`: the package in progress (§2 order, §4 wave, §6 spec).
4. `docs/BACKLOG.md`: the rows that package closes.
5. The design source the package cites: `docs/rules/PARTF_IMPLEMENTATION_PLAN.md` (PFP; r3 plus REVISION 4, which is binding).

## Pre-work gate (always)
- Before writing a file, running a generator or dispatching a subagent, state the frame in one line: what is being changed, the shape of the output, and what done looks like. Then wait for the user.
- When asked to revise an existing artifact, confirm whether the change is additive or a replacement before starting. The default is additive.
- Map the blast radius **before** editing: every dependent, with `file:line`. Do not discover dependents from failing tests afterwards.
- When the request and the plan disagree, or the scope looks wrong, say so directly. Never silently carry a wrong frame forward.
- Fix the underlying problem. Never bypass or work around it.

## Git — the user runs every git command
- Never run `git add`, `git commit`, `git push`, or any other command that stages, commits or pushes changes.
- Your job is to verify and report what would be staged: new and modified files, generated files that must not be staged, and the naming guard result.
- No AI attribution trailers anywhere: no Co-Authored-By lines, no "Generated with" lines.

## Naming and client data
- Use no client, agency or organisation names, and no client specifics, anywhere in the repo. `tests/t0/test_generic_naming.py` enforces this. The exemplar state is `STATE_A` / `ZZ`.
- The tenant profile lives only outside the repo, in `C:\demo\_private_state_a_sources\`. Never copy its contents into the repo. Screenshots that show the tenant name stay out of `docs/`.
- Technology names (Snowflake, Databricks, BigQuery, Airflow, dbt, ADF) are allowed.

## Security
- **Never** read, copy, stage or quote anything under `C:\demo\DQ`. That folder holds client certificates, an internal database and a hard-coded credential. Donor code there is a source of patterns only, and was assessed earlier; do not reopen it.
- Never write secrets into config. Literal secret values in governed items are refused by design.
- Patient data (PHI) and substance-use data (Part 2): trace tables, explanations, guardrail details and screenshots carry only counts and references, never record values.

## System boundary (added after INC-001, amended after INC-002 — `docs/INCIDENTS.md`)
- No seat or subagent may install software, start or stop a system service, write outside the repository's working tree, or make a network download, without first stopping and escalating to the orchestrator for a user decision. This applies even when the action would make a test genuinely pass instead of honestly skip, and even when the install instructions are already documented in-repo (e.g. `run_demo.ps1`'s own printed URL, or this file's own Environment section) — documented does not mean pre-approved.
- **The same binds the orchestrator.** When the orchestrator itself needs to install software, start or stop a system service, or make a network download — including to stand up the already-documented Linux cloud environment below — it stops and escalates to the **user** first, the same way a seat escalates to the orchestrator. A status update narrating the action after the fact is not escalation.
- "Stop if something is awkward" is not sufficient instruction for this boundary. Every build brief must state it explicitly.
- Every subagent's final report must include an explicit disclosure line: whether it installed anything or changed anything outside the repo tree, yes/no, with detail if yes. The orchestrator checks this line before accepting any report as closed.
- **Bind the action, not the actor (added after INC-002; combined-lessons review, 2026-11-04).** INC-002 happened because the rule above, read literally, named "seat or subagent" escalating "to the orchestrator" — leaving unstated that the orchestrator must escalate to the *user* for the same actions. Any boundary rule that names a specific actor has a built-in literal-reading gap for every actor it didn't name. When writing or editing a boundary rule in this file or a seat definition, state the action that triggers escalation and who it escalates to at each level (seat → orchestrator → user), not just the first hop. Apply this check retroactively whenever this section is next amended.
- **Exit criteria that cross this boundary are flagged at design time, not execution time (FB-1, `docs/FRAMEWORK_FEEDBACK.md`).** Before an exit criterion that requires running real infrastructure (booting a service, a cold-start install) is written into `EXECUTION_PLAN.md` as mandatory, the architect and scope-reviewer check: (a) does the live demonstration itself re-enact this moment, or only the build/verification process; (b) does satisfying it require a System-boundary action? If yes to (b), it is flagged for user escalation in the design step, before the plan is in motion — not discovered later once a seat or the orchestrator is already executing toward it.

## Verification (combined-lessons review, 2026-11-04 — generalizes INC-001/INC-002/FB-2 into one standing rule)
- Every claim — the orchestrator's own, a seat's, a doc's, a screenshot's — is a hypothesis until independently re-checked against the current live tree or gate output. Self-report is never accepted as "done" on its own.
- A claim that a capability "works" or "persists" must name the specific before/after check performed (a restart, a re-run, a re-query against durable state) — a passing test suite or a render check is not, by itself, that check. A gate passing is not the same thing as a behavior observed.
- A rule in this file, or a gate or exit criterion derived from it, can test the wrong moment: process plumbing the live demo never re-enacts, rather than what the audience actually sees (FB-1, FB-3). Before accepting a gate or exit criterion as sufficient, confirm what moment it actually exercises, not just that it passed.
- Content truth and reachability are separate risks (FB-4). A statement that is true is not automatically safe to ship: also check who can reach it, and when, in the artifact that will actually be presented. This applies to any package that adds UI or output to a codebase also used for a live or client-facing demonstration.

## How work runs (coalition-build-framework)
- The main session is the **orchestrator**. It sequences the work, freezes edits, runs the gate, and is the **sole writer of `docs/blackboard.md`**. Subagents return their entries and the orchestrator records them verbatim with attribution. The orchestrator does not build.
- Seats are defined in `.claude/agents/`. One seat owns each file per package (PFP §4, plus re-assignments recorded on the blackboard). A seat that needs a file it doesn't own asks the orchestrator.
- Backend seats run **one at a time**. Tower may run in parallel only on disjoint files.
- Seats run **targeted tests only**, under the gate lock, and **never the full suite**.
- **Only the orchestrator runs the full suite**, through `python -m tools.gate --label <pkg> [--tower]`, after every seat has confirmed it is idle. A gate whose code hash changed between start and end is void.
- A seat's own "green" is never accepted as done. Only a non-void gate receipt counts.
- Red team runs before and after each build, with a scope reviewer and a cross-examiner (R5). Blocking findings must name a failing input or a `file:line`, and are tagged Important, Nit or Pre-existing. **The round cap is 5** build↔panel rounds per package; after 3 rounds on one finding, reconsider the approach.
- Subagent briefs present the brief as a question to attack, not a premise. At least one reviewer challenges the scope.
- Claims about the code cite `file:line` and the code hash. The tree is not a git repo, so the code hash stands in for a commit SHA. A claim that something is **absent** needs a second, independent confirmation.
- Autonomy tier: **T0**. There is no CI. Nothing runs unattended, and nothing is merged by an agent.

## Invariants every gate must keep
- w1 outcomes 420 RELEASED / 291 HELD / 47 PENDING_DEPENDENCY (`tests/t1/test_w1_outcomes.py`).
- I-3 agent-eval digests unchanged, unless the package records why.
- The nightly earn story works with `--preload-nights 15` and with `--preload-nights 0`.
- Tracing never changes an outcome, and never blocks a release, decision or explanation (`tests/t1/test_trace_fault_injection.py`).
- Every capability works in **mock** and in a **production-like** mode. Each new endpoint ships a same-shape mock route. Labels come from stored rows: never show LIVE, "fact-checked", or causal wording that the data does not support.
- FAIL is never autonomous. A missing tier means L1. Promotion needs two people, evidence and a minimum record. Demotion is automatic and lowers only.

## Environment
- **Windows (user machine):** no Docker. Portable PostgreSQL via `run_demo.ps1` on `127.0.0.1:55432`. For tests, set `DQ_PG_HOST=127.0.0.1`, `DQ_PG_PORT=55432`, `DQ_PG_USER=dq_gov`.
- **Linux (cloud):** Postgres on port 55432 with its socket in `/tmp`, user `dq_gov`. Restart it after a container restart (command in `docs/EXECUTION_PLAN.md` §5.1).
- Deleting files on the user's machine through the bridge is not permitted. Archives go to `C:\demo\_to_delete\`, and the user deletes them.
- Report progress step by step while multi-agent work runs.

## Commands
`/package K<n>` · `/gate <label> [--tower]` · `/redteam K<n> before|after` · `/close K<n>`. Definitions are in `.claude/commands/`.
