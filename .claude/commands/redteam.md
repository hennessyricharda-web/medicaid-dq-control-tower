---
description: Dispatch the red team, the scope reviewer (before only) and the cross-examiner (after only) in parallel for a package
argument-hint: K<n> before|after
---
You are the ORCHESTRATOR. Arguments: $ARGUMENTS (package, then phase).

**Before the build:** dispatch in parallel:
- `red-team`, with the R1 seat for this package (EXECUTION_PLAN §4.5 table);
- `scope-reviewer`.

Give each one the package spec and the architect's design file. Phrase the brief as a question to attack, for example "Will this design deliver the demo moment and keep the invariants? Find where it fails". Do not phrase it as "review this good design".

**After the build:** dispatch in parallel:
- `red-team`, aimed at the built diff, with:
  - the list of changed files;
  - the gate receipt and code hash;
  - the PFP section;
  - specific attack questions drawn from the package's risks (scope leaks, PHI in traces, mock/production label honesty, fail-closed paths, concurrency, the invariants in `CLAUDE.md`).
- `cross-examiner`, with every BACKLOG row and fix this package marks DONE and the receipt it cites.

When they return:
1. Record each output verbatim on the blackboard, attributed to its dispatch, under Findings.
2. Verify every Critical and Important finding at its `file:line` before routing it. Drop false alarms, with a one-line reason.
3. Write a Decisions entry: fix now (and which seat), defer (to which backlog id), or user decision needed.
4. DEMOTE verdicts send the BACKLOG row back to its true status.

Report to the user in plain language: what was found, what is being fixed, and what needs their decision.
