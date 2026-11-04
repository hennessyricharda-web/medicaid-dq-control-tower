---
description: The authoritative gate. Freeze check, then the full suite plus the optional Tower gates through tools/gate.py, with a code hash at start and end and a receipt
argument-hint: <label> [--tower]
---
You are the ORCHESTRATOR running the authoritative gate for: $ARGUMENTS

1. **Freeze check.**
   - Every seat dispatched in this package has replied "idle: no further edits".
   - No seat is running.
   - No other gate is running: `.gate.lock` is not held.
   - If any of these fails, stop and report.
2. **Environment.**
   - Postgres accepts connections on port 55432. On Windows, start portable PostgreSQL the way `run_demo.ps1` does (`pg_ctl` with `.pgdata` on 127.0.0.1:55432). G0 adds the `-DbOnly` switch for this. On Linux, use the command in EXECUTION_PLAN §5.1.
   - On Windows, set `DQ_PG_HOST=127.0.0.1`, `DQ_PG_PORT=55432` and `DQ_PG_USER=dq_gov`.
3. **Run the gate.** Before G0, the freeze check cannot see `tower/` edits: confirm no Tower seat is running.
   - Run `python -m tools.gate --label $ARGUMENTS` with the Bash tool's `run_in_background`. This is mandatory: a full run takes about 30–60 minutes and a foreground call times out at 10 minutes. Wait for the completion notice. Do not poll.
   - If `tools/gate.py` does not exist yet (before G0), run `flock /tmp/dq_pytest.lock python -m tools.receipt --label <label>` on Linux, then compare the code hash before and after yourself. On Windows, stop: G0 builds the gate tool.
4. **Read the receipt** and report these values verbatim:
   - tests, passed, failed, errors, skipped;
   - the code hash and the `gate_tree_sha256`;
   - whether the gate is void;
   - **`suite_complete`** — if `false`, the suite did not fully run (most commonly: Postgres was unreachable and CP tests skipped). A receipt with `suite_complete: false` is NOT evidence of a real green, even when `void` is `false`. Do not report such a receipt as passing.
   - the receipt path.

   Skipped tests are not passes. If the receipt shows CP tests skipped because Postgres was unreachable, the gate is invalid.
5. **Sanity checks.** Confirm `test_w1_outcomes`, `test_generic_naming` and `test_trace_fault_injection` are in the JUnit file and passed.
6. Do not edit any code during or after the gate. Failures go back to the owning seat.
