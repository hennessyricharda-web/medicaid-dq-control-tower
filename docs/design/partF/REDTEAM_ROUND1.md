The pack is not ready to show a state Medicaid agency. The wireframes show the agent doing things its own code forbids. The Part F architecture lowers the approval bar for wiring pipelines. Most of the screens the user called too dense still have no wireframe. Provenance: `/home/claude/medicaid-dq-agent` is not a git repository, so there is no commit SHA; I read it on 2026-10-23.

## Critical

**C1. The decision queue wireframe contradicts the agent's own rules.** Line numbers below are in `ux/_build/pages/01-decision-queue.html`.
- Lines 23–25 label FAIL rows "L4 Autonomous". In the code, FAIL is never autonomous (`dqagent/hitl/modes.py:51-53`).
- The selected FAIL row shows "Release in 7 min 57 s unless vetoed". That can't happen: the FAIL floor stops a veto window from opening.
- Veto countdowns of 1:12:00 and 6:40:00 contradict the design's 10-minute window (`veto_window: PT10M`, PARTE_DESIGN §1.4).
- "No agent" is wrong: a missing tier means L1 (`modes.py:66`).
- Line 51 reads "the agent judged the gap as rounding … proposes release". That is advice to release plus an attributed judgement, which the explainer bans (§7).
- `03-dag-viewer.html:41` says "because one rule failed", which is causal wording with no hypothesis behind it.
- A "fact-checked" chip sits on template text in five places: `01:52`, `02:72`, `05:33`, `06:41` and `PIPELINES_UX.md:94`. This undoes E2, which the built `tower/src/components/Explanation.tsx:28` already fixed.
- Explanation text contains counts of 1–10 ("3 of 412 rows", "holds 3 units"), which the facts builder must suppress.
- The arithmetic in `05-nightly-runs` doesn't add up: 6 units created, but 5 released plus 3 held.

**C2. The wireframes show an estate that doesn't exist.**
- `config/harness_w1.yaml:1-16` has 2 pipelines and 4 gated nodes, all on DuckDB.
- `02-pipelines-catalog` shows 8 pipelines, 412 nodes, "Approved v12", 14-night run histories and "11 downstream nodes did not run".
- `03-dag-viewer` gives every node a ✓ Succeeded. No legend has a "discovered, not evaluated" state, although PARTF_ARCH §5 requires one.
- An evaluator will ask to see their own Airflow. The UX says yes, while PARTF_ARCH §7 says wiring is display-only.

**C3. Splitting HARNESS lowers the approval bar.**
- HARNESS is always loosening today (`dqagent/governance/registry.py:176`). PARTF_ARCH §3 makes "adding a pipeline or binding" a one-approver change.
- Once intake is driven by bindings (wave C), a new binding brings new datasets under the existing L4 RELEASE tier for AUTH_MEDICAID. That widens what the agent releases on its own.
- The classification lives only in Python. The database's requires-two check covers only AUTONOMY, AGENT, ENTITLEMENT and CLASSIFICATION (`dqagent/cp/governance_schema.py:437,484`), which breaks the C4 rule that the database decides.
- `PIPELINES_UX.md` §3 step 7 says wiring always needs two people, so the two documents disagree.
- **Fix:** compute requires-two in the database for PIPELINE, CONNECTION and GATE_BINDING. Newly bound datasets start at L1.

**C4. The drift change request can't be created.** PARTF_ARCH §3 has the agent propose it with origin `AGENT`. `gov_propose` raises an error for any origin other than HUMAN, PROPOSER or GENESIS (`governance_schema.py:140-141`), and §1.1 has no PROPOSE_CR action class. **Fix:** the agent drafts a metadata-only proposal, and a named person submits it with origin PROPOSER.

## Important

**I1. The density problem is solved on 6 of 17 screens.** There are no wireframes for:
- Look-back (27,836 px tall, AUDIT §6);
- Measures (5,771 px);
- the Decision Card (4,650 px), which is where decisions are made;
- Coverage (3,656 px);
- Rules, Agents, Monitors, Balancing and Audit.

The user said the problem is on "nearly all screens".

**I2. The proposed merges lose things.**
- **Blackboard:**
  - `tower/src/app/agent/page.tsx:75-97` checks each entry's link to the one before it and flags a CHAIN BREAK.
  - Split into per-agent tabs, that check can't run, because the previous entry often belongs to another agent.
  - AUDIT §15 also shrinks the chain to one status line.
  - **Fix:** keep a global blackboard ledger under Assure, and keep known-issue posts visible only to their proposer (`page.tsx:124`).
- **Balancing:** ABC can block release. The parallel run never does; it is cutover evidence only (§3.3). Putting them side by side invites a reader to take a BREAK as blocking.
- **Expected deliveries:** moving them under Nightly runs means they vanish outside demo mode, because the simulator API returns 404 there (§10b).

**I3. The gate API has four holes.**
- PARTF_ARCH §2 computes PASS from `can_release` reasons, while §0.2 says the gate reads the released manifest. Answering PASS before the release happens is a race. **Fix:** PASS only when the manifest says released.
- Each GET writes a `gate_checks` row, so sensor polling grows the table without limit. **Fix:** de-duplicate per (run, binding version).
- A `gate:read:<pipeline>` token gets unit states for every authority. That is the same cross-scope leak as E5 (`docs/blackboard.md:1373`).
- An OpenLineage COMPLETE event triggers seal, which changes state. **Fix:** require a separate `lineage:write` scope, and still require complete ABC before sealing.

**I4. The wizard contradicts the binding rules.**
- Step 2 offers "dbt pre-hook", which `docs/DESIGN.md:373` forbids.
- Step 3 says the layer is "inferred (editable)", while PARTF_ARCH §1 says the layer is "never guessed".
- Step 1 accepts an Airflow DAG *file*. Parsing a Python DAG file means running its code. **Fix:** accept only API or serialised JSON.
- The wizard toggles gates per task node and loses the datasets-vs-tasks model. That model (§0.1) is correct.

**I5. "Configure" and "updates" are missing.** The user asked for both explicitly.
- The UX pack has no Connections screen, no schedule management and no screen for credential references.
- It has no DAG drift view. A grep of every `.md` and `.html` file found only ruleset drift.

**I6. Graph accessibility.**
- React Flow's `onlyRenderVisibleElements` takes off-screen nodes out of the page. Arrow-key traversal can then land on a node that isn't rendered.
- **Fix:** make the Outline/table view a toggle that can be the default, and have it carry gate state.
- At about 64 real nodes there is no need for viewport culling, so React Flow isn't needed. Use the zero-dependency SVG option for the demo.

**I7. One PARTF_ARCH citation is wrong.** PARTF_ARCH §0.3 says intake is hard-coded at `runtime.py:850-856`. Those lines parse parallel-run measures. The hard-coding is at `dqagent/runtime.py:909`.

## Cross-examination
- **UPHOLD:** HARNESS kind and the kind CHECK constraint (`governance_schema.py:27-28`).
- **UPHOLD:** DuckDB is the only data plane (`dqagent/adapters/dataplane/`).
- **UPHOLD:** the empty `adapters/dag` and `adapters/orchestrator` packages.
- **UPHOLD:** the rejected onboarding endpoint that bypassed two-person approval (`blackboard.md:1139`).
- **DEMOTE:** "no runtime change needed" (§3). It depends on the new kinds having database-enforced classification, which they don't.

## Scope challenge
Part F is not the right next build. The Part E post-build red team sent E1–E8 to a fix seat (`blackboard.md:1366-1376`). Apart from E2 in the Tower, I found no record that any were closed. E5, counts leaking across scope, shows up again in the nightly wireframe, which shows SNAP units to every role.

Recommended order:
1. Close E1–E8.
2. The shell, status strip and nav rail.
3. Split-pane Queue, Card and Look-back.
4. A Graph and Catalog of the 2 real pipelines, plus one Airflow fixture labelled SYNTHESIZED and "discovered, not evaluated".

"Discovered, not evaluated" is not enough on its own. Every run and gate glyph on those nodes must be empty, not ✓.

## Required before the user sees the pack
1. Rebuild the wireframe data from w1 and the real tier, floor and veto rules; fix everything in C1.
2. Use the TEMPLATE chip, rewrite explanations without causal or advisory wording, and suppress counts of 1–10.
3. Wireframe the Decision Card and Look-back at minimum.
4. Settle the approver count for wiring (two people, enforced in the database) in both documents.
5. Add a Connections/schedule screen and a drift view, or state that they are deferred.
6. Keep a global blackboard ledger.
7. Fix the pre-hook, inferred-layer and DAG-file contradictions in the wizard.

## Cut or defer
- Wave D adapters (ADF, Databricks, Snowflake Tasks, dbt Cloud).
- The OpenLineage receiver and the orchestrator gate shims.
- The 412-node estate.
- Signal pinning, the minimap and React Flow.
- The "Run all queued" button: no such endpoint exists in §10b.

## Worth saving to memory
- The UX pack regressed the E2 "fact-checked" fix.
- HARNESS is always loosening, and the new kinds must keep that in the database.
- `gov_propose` rejects agent-origin change requests.

Files:
- `/tmp/claude-0/-home-claude/b53dc761-3a9d-5f69-b18c-009e2c2abafd/scratchpad/PARTF_ARCH.md`
- `/tmp/claude-0/-home-claude/b53dc761-3a9d-5f69-b18c-009e2c2abafd/scratchpad/ux/_build/pages/01-decision-queue.html`
- `/home/claude/medicaid-dq-agent/dqagent/hitl/modes.py`
- `/home/claude/medicaid-dq-agent/dqagent/cp/governance_schema.py`
- `/home/claude/medicaid-dq-agent/dqagent/governance/registry.py`