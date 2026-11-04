---
name: package
description: >-
  Run one package of docs/EXECUTION_PLAN.md through the full wave (pre-flight,
  design, red team, build, gate, red team, close), stopping at each WAIT point
metadata:
  user-invocable: true
  disable-model-invocation: true
  argument-hint: K<n>
---

You are the ORCHESTRATOR for package $ARGUMENTS. Follow `docs/EXECUTION_PLAN.md` §4 exactly. Do not build anything yourself; seats build and you gate. You are the only writer of `docs/blackboard.md`.

Give the user visible step-by-step progress throughout. Use the task list, one item per step below.

**Step 1: Pre-flight (§4.1). WAIT at the end.**
- Read `CLAUDE.md`, the blackboard SYNC section, EXECUTION_PLAN §2 and the §6 spec for $ARGUMENTS, the PFP section it cites, and the BACKLOG rows it closes.
- Report:
  - the platform;
  - the code hash;
  - the latest receipt, and whether it is void;
  - whether Postgres is up;
  - whether any seat is still running.
- Check the prerequisites in the §2 table (G0 before everything). If one isn't met, stop and say so.
- State the frame in one line (what changes, the output shape, what done means), and confirm the package is additive.
- Wait for the user's go. That go covers dispatching the architect, the red team and the build seats for this package. Steps 3, 6 and 7 still stop where they say WAIT.

**Step 2: Design.**
- Dispatch the `architect` subagent with a composed brief: ROLE (from its file), INHERITS, CONTEXT MANIFEST, SCOPE (the $ARGUMENTS spec), DoD, OUTPUT.
- Record its output verbatim on the blackboard, with attribution, as a Design entry.

**Step 3: Red team before the build.**
- Run `/redteam $ARGUMENTS before`.
- Record the Findings and your Decisions, each with `basis · wrong-if · NOT-verified`.
- If any finding changes scope or contradicts PFP or a DEC, WAIT for the user.

**Step 4: Build.**
- Dispatch the seats in the serial order the spec gives. Run only one backend seat at a time. Run Tower in parallel only on disjoint files.
- Each brief is composed from the seat file, the spec steps for that seat, the design file, and the Decisions.
- When a seat reports a conflict, resolve it from PFP: cite the line, or ask the user. Never override PFP from memory.
- Record each seat's report on the blackboard.

**Step 5: Gate.**
- Confirm that every seat said "idle: no further edits".
- Run `/gate partF-$ARGUMENTS`, adding `--tower` if `tower/` changed.
- If the gate is void or red, route the failures back to the owning seat, re-freeze, and gate again.
- For packages that touch screens, run the live smoke against the real API and look at the screenshots yourself.

**Step 6: Red team after the build.**
- Run `/redteam $ARGUMENTS after`.
- Send the Important and Critical findings to their owning seats.
- After the fixes, run the gate again.
- Round cap: 5. After 3 rounds on the same finding, reconsider the approach instead of patching it.
- A Critical finding is closed only by the protocol in §4.5.4.

**Step 7: Close. WAIT at the end.**
- Run `/close $ARGUMENTS`.
- Wait for the user to commit and to say go before starting the next package.
