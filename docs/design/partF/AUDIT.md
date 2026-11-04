# Command Tower: screen audit and layout specification

Scope: density and layout for all 17 Tower routes, with a concrete layout for each. Behaviour comes from the code and is cited where a layout depends on it.

Evidence:
- Screenshots in `scratchpad/tower_shots/` (11_*, 12_*, 13_*, 14_* and the older 01–10).
- Source in `/home/claude/medicaid-dq-agent`, read only, on 2026-10-23. **Provenance:** that tree is not a git repository, so there is no commit SHA.
- Pipeline Map and ABC Board have no screenshot. Their entries come from `app/pipelines/page.tsx`, `components/PipelineDag.tsx` and `app/abc/page.tsx`.

Rules every screen below follows (from the code, and restated in LAYOUT_SYSTEM.md):
- Explanations carry a **"deterministic template"** chip when their source is the template (`components/Explanation.tsx:28`). They never carry "fact-checked".
- Explanation text states facts only. There is no causal wording ("because", "judged", "due to") and no advice ("proposes release").
- Any count of 1–10 in an explanation reads "fewer than 11". Record and row counts of 1–10 in tables show `<11`.
- Data is shown only for the viewer's authority scope, with SNAP only for SNAP-scoped users (E5). Each list header says which scope it shows.
- Mode facts follow `dqagent/hitl/modes.py` and `policy_w1.yaml`:
  - FAIL is never autonomous (floor → H_IN).
  - ABC failed in a batch → H_IN_2.
  - A missing tier is L1.
  - A veto window opens only for PASS or TA_WARN units at L3, and lasts PT10M.

Wireframe files are in `wireframes/` and named as in the table at the end.

---

## 0. Shell, inherited by every screen

| # | Problem | Evidence | Fix |
|---|---|---|---|
| 1 | Chrome takes about 440 px of a 1000 px viewport | `14_01_topband.png` | A 44 px header and a 28 px strip. Content starts at y=116 |
| 2 | Eleven tall tiles with wrapped values; static settings shown as status; an action inside a tile | `14_01`, `13_01` | 6 fixed strip slots, each linking to the screen that resolves it: Decisions due, Veto window, ABC, DAG drift, Pipeline order, Ruleset. "All 11 signals" opens a popover with every DESIGN §8 top-band item. The simulated clock and Run next night move to the header |
| 3 | 15 flat links; Nightly runs hard to find; "Agent" and "Agents" collide | `Shell.tsx` NAV | A grouped rail: Operate, Observe, Pipelines, Govern, Assure. Nightly runs carries a "Demo" badge, because the simulator answers 404 outside demo mode (PARTE §10b) |

**Layout:** a grid of `212px | 1fr` columns and `44px 28px 1fr` rows. The rail collapses to 52 px.

---

## 1. Decision Queue (`/`), wireframe 01

| # | Problem | Evidence | Fix |
|---|---|---|---|
| 1 | Full unit keys are printed inside each row, so rows are up to 230 px tall | `14_01`, `12_01` | Show a unit count; the keys go to the detail pane |
| 2 | A FAIL row shows the tier chip "L4 Autonomous", which reads as autonomy that cannot happen | `14_01` | Replace the tier column with **Mode** and **Why**, where Why is the first trace step that set the mode: FAIL floor, ABC failed, L2 ceiling, or vetoed |
| 3 | Veto-window units are mixed in with held units | `12_01` | A separate group at the top, "Veto window open", with a countdown out of 10 minutes |

**Layout:**
- Page header, then a toolbar (filter, Mode, Why held, Node, Assigned to me, sort, density).
- Split: list (flex) and detail (440 px).
- List: grouped table, 32 px rows. Columns: SLA, Worst, Mode, Why, Node, Rule, Failure signature (p2), Units, Owner (p3).
- Detail: the chips, a veto callout when a window is open, the explanation, the mode trace, units, and balance with dependencies.
- Sticky action bar: Veto release (only while a window is open), Open decision card, Assign to me.

## 2. Decision Card (`/units/[unitKey]`), wireframe 07

| # | Problem | Evidence | Fix |
|---|---|---|---|
| 1 | The page is 4,650 px; every disposition carries a paragraph, PASS included | `14_03_card_from_nightly.png` | Non-pass first. PASS rules collapse to one line, and explanations open per row |
| 2 | The decide, veto and reveal controls scroll away | `12_02_card_veto.png` | A fixed right decision rail of 330 px |
| 3 | ABC and dependencies sit at the bottom | `12_02` | Put them above the dispositions, side by side with "Why this is held" |

**Layout:**
- A header band of two lines: unit key; then chips for State, Worst, Mode and Why, the tier ("Agent tier L3, not binding" when a floor wins), authority, node and batch. The SLA sits on the right.
- Main column, in order:
  1. Explanation.
  2. Two columns: mode trace, and ABC with dependencies.
  3. Dispositions table, with expanding rows.
  4. Dry run of a choice ("nothing has been applied").
  5. The collapsed "Hypothesis: not evidence, not a decision".
- Decision rail:
  - required mode and approvals, and whether the viewer's duty covers the authorities;
  - verb choices, reason code, rationale (at least 10 characters) and the decide button;
  - Reveal records, with a purpose of at least 10 characters.
  - For H_IN_2 the rail adds the checker step-up. For an H_ON unit inside its window, the rail shows the countdown and Veto.

## 3. Pipeline Map (`/pipelines`), becomes the Pipelines workspace, wireframes 02, 03, 03b, 04, 10, 12

| # | Problem | Evidence | Fix |
|---|---|---|---|
| 1 | A pipeline-level graph of 5 boxes; it does not show where the gates are | `PipelineDag.tsx` | A node-level layered SVG with medallion lanes and a gate state per node |
| 2 | The same facts are repeated in two tables under the graph | `pipelines/page.tsx` | Becomes the Table view, a toggle beside Graph that can be the default |
| 3 | The order list sits in its own panel | `pipelines/page.tsx` | A one-line order strip in the Catalog |

**Layout:**
- **Catalog:**
  - an order strip;
  - a table grouped by provenance (Harness w1, evaluated / Demo estate, planned / Orchestrator, SYNTHESIZED);
  - a 280 px drawer for the selected pipeline: facts, gate bindings, dependencies, last night.
- **Graph:** the canvas, with a 340 px node panel.
- **Table view:** every node, with its evaluation and gate state.

## 4. ABC Board (`/abc`), part of Operate › Balancing, wireframe 14

| # | Problem | Evidence | Fix |
|---|---|---|---|
| 1 | ABC and parallel-run reconciliation are separate pages answering similar questions, but they have **different force**: ABC can block release, the parallel run never does | `Shell.tsx`; PARTE §3.3; `policy_w1.yaml` `abc_failed_release` | One Balancing page with **two visually separate sections**, never interleaved |
| 2 | 13 numeric columns per hop | `types.ts` AbcRow | p1: hop, src_in, tgt_out, residual, disposition. p2: rejected, quarantined. p3: deduplicated, filtered, held |
| 3 | The ABC status reaches people only through the band tile | `TopBand.tsx` | The strip's ABC slot links to Balancing, filtered to out-of-balance rows |

**Layout:**
- Section A, titled "**ABC: can block release**", carries a bad-tone left rule. It is a table by batch, then hop, with an expanding row for the hop detail. The out-of-balance batches are pinned at the top, and each links to its held units.
- Section B, titled "**Parallel run: cutover evidence, never blocks**", carries a neutral-tone left rule and a "Never blocks" chip in its header. It is the comparison table grouped by period, with explain codes in an expanding row.
- The two sections never share a table, a sort or a filter.

## 5. Change Requests list (`/governance`), wireframe 06

| # | Problem | Evidence | Fix |
|---|---|---|---|
| 1 | The list and the detail are separate pages | `01_governance_list.png` | A three-column split: list (520 px), detail (flex), decision rail (290 px) |
| 2 | Author cells take 3 lines | `01` | One line; the "you wrote this" note moves to the rail |
| 3 | The two-person reason is not visible in the list | `01` | Approvals k/n in the list. The header chip "Two people, set by the database" |

## 6. Change Request detail (`/governance/[crId]`), the detail pane of wireframe 06

| # | Problem | Evidence | Fix |
|---|---|---|---|
| 1 | Impact repeats itself across three boxes | `12_05_cr_autonomy.png` | One key-value Impact block, computed by the server |
| 2 | The decision box is beside the content, not fixed | `12_05` | A fixed decision rail: approvers, eligibility ("You can approve" or "You wrote it"), reason, Approve with step-up, Refuse |
| 3 | "fact-checked" appears on template text | `12_05` | Use the "deterministic template" chip |

**Layout:** header chips (status, approver rule, origin HUMAN or PROPOSER), the explanation, items as a key diff per artifact, Impact, Events.

## 7. Look-back (`/lookback`), wireframe 08

| # | Problem | Evidence | Fix |
|---|---|---|---|
| 1 | The page is 27,836 px; each item has an explanation box and two stacked buttons | `11_04_lookback.png` | 32 px rows grouped by revoked version and CR |
| 2 | The resolution rules sit above the list | `12_14` | Move them into the decision rail |
| 3 | The live-in-manifest state is a long phrase | `12_14` | A chip: "Live" (warn) or "Not live" |

**Layout:**
- List (flex), detail (flex), decision rail (300 px).
- Detail: the explanation, release facts (released by, mode, the tier at the time, the revoked and current versions), the revoking CR, and a Reassess action.
- Rail: Accept or Withdraw, with rationale and step-up. It states that a withdrawal of an L3/L4 release lowers the agent to L2.

## 8. Rules (`/rules`), wireframe 09, view "rules"

| # | Problem | Evidence | Fix |
|---|---|---|---|
| 1 | The description wraps under the ID, so rows are 4–5 lines | `12_12_rules_classification.png` | Separate ID and description columns; the description truncates |
| 2 | 18 "Revoke version" buttons, one per row | `12_12` | A row menu (⋯) plus the detail pane |
| 3 | 7 equal selects | `12_12` | Filter chips plus "More filters" |

**Layout:**
- List: compact table with Rule, Relation, Description, Gating, Status, Priority (p2), and a menu.
- Detail (420 px): description, relation, version, any revoked versions with their look-back count, whether the rule applies (by feed delivery), source, and actions that open a change request.

## 9. Coverage (`/coverage`), wireframe 09, view "coverage"

| # | Problem | Evidence | Fix |
|---|---|---|---|
| 1 | Four tables on one 3,656 px page | `12_11_coverage_cde.png` | Tabs: Floor matrix (default), Critical data elements, Sensitivity, Requirement inventory |
| 2 | Explanations inside cells | `12_11` | A drawer on cell select |
| 3 | Matrix cells mix chip, text and count | `12_11` | Heat cells of word plus count. "Not delivered" is dashed neutral and never reads as pass or fail |

## 10. Agents (`/agents`), wireframe 15

| # | Problem | Evidence | Fix |
|---|---|---|---|
| 1 | Registry, entitlements, tiers, track record and the form all on one page | `12_04_agents.png` | An agent list, then agent detail with tabs: Overview, Entitlements, Autonomy, Track record |
| 2 | Track-record buttons on every row | `12_04` | Row selection drives the detail |
| 3 | The promotion form is half width and below the fold | `14_05` | A drawer opened from the decision rail. Promotion is always two-person (PARTE §1.2) |

The **Blackboard is not a tab here**: see §11.

## 11. Blackboard (`/agent`), becomes Assure › Blackboard ledger (wireframe 13), with batch state moving to Nightly runs

| # | Problem | Evidence | Fix |
|---|---|---|---|
| 1 | One page mixes per-batch state, the blackboard, memory, known issues and a form | `04_agent.png` | Split by job, but keep the **global ledger whole** |
| 2 | Splitting the blackboard per agent would break the chain check, which compares each entry with the one before it, often written by another seat | `app/agent/page.tsx:75-97` | A **global Blackboard ledger** under Assure. Every seat is in one table. The Link column shows linked, link n/a or **CHAIN BREAK** (crit), and a status bar counts the breaks in view |
| 3 | Known-issue posts are visible to their proposer only | `app/agent/page.tsx:124` | Kept. Other viewers see a placeholder row, "A known-issue post, shown to its proposer only", so the sequence gap is explained |

**Layout:**
- Ledger: a chain status bar, filters (batch, seat, kind), and a table of Seq, At, Seat, Kind, Batch, Post and Link.
- Per-batch agent state goes to **Nightly runs › night detail** (demo) and **Deliveries** (all modes).
- Baselines memory becomes an Agents tab.
- Known issues becomes **Govern › Known issues**, with the proposer-only visibility rule.

## 12. Monitoring (`/monitoring`), becomes Observe › Monitors (wireframe 09, default view)

| # | Problem | Evidence | Fix |
|---|---|---|---|
| 1 | The full explanation under every row | `14_12_monitoring.png` | Compact rows with a sparkline column |
| 2 | The chart sits below the table | `12_06`–`12_09` | The chart moves to the 420 px detail pane |
| 3 | Monitors and Measures duplicate a pattern | `05`, `14_12` | One list with a Kind filter: XmR, p-chart, PSI, CMS measure. The header chip reads "Advisory: never changes a unit's mode" |

## 13. Reconciliation (`/reconciliation`), parallel run moves to Balancing, section B; deliveries move to Operate › Deliveries (wireframe 11)

| # | Problem | Evidence | Fix |
|---|---|---|---|
| 1 | An explanation box after every break row | `12_10_reconciliation.png` | Expanding rows |
| 2 | Expected deliveries share a page with the parallel run | `12_10` | Move them to **Operate › Deliveries**. That page works in every mode, so they are never lost when the simulator is off (PARTE §10b). Nightly runs links to it |
| 3 | One table per period, with repeated headers | `12_10` | One table grouped by period |

**Deliveries layout** (wireframe 11), one page with three sections:
1. Open feed items, with an inline acknowledge control.
2. Expected cadence per feed. This is governed: a change is a CR, and making a feed less frequent is loosening.
3. Pipeline schedules, read from the orchestrator and read-only, with provenance (LIVE or SYNTHESIZED).

## 14. Nightly runs (`/nightly`), demo only, wireframe 05

| # | Problem | Evidence | Fix |
|---|---|---|---|
| 1 | Counter cards holding lists of unit keys | `14_02_nightly_run.png` | A stat grid plus an explicit sum line: created = released + veto window + held + pending |
| 2 | A flat list of nights | `14_02` | A grid of harness pipelines by nights (22 nights, from SIM_NIGHTS), with the selected night's detail pane |
| 3 | Counts show SNAP to every role (E5) | `14_02`; `blackboard.md:1373` | Counts cover the viewer's scope, and say so |

**Layout:**
- Page header: a "Demo mode only" chip and a single **Run next night** primary button. There is no bulk-run action, because no such endpoint exists.
- Split: grid and queued nights (flex), night detail (420 px).

## 15. Measures (`/measures`), merged into Monitors (§12)

| # | Problem | Evidence | Fix |
|---|---|---|---|
| 1 | 5,771 px, with the chart at the bottom | `05_measures_chart.png` | Measures appear as rows in Monitors, kind "CMS measure" |
| 2 | Binding detail inside every row | `06_measures_table.png` | Shown in the detail pane |
| 3 | A drift banner pushes the page down | `05` | The strip's Ruleset slot tints. The banner appears only on Govern screens |

## 16. Audit (`/audit`), Assure › Audit chain (no wireframe; spec only)

| # | Problem | Evidence | Fix |
|---|---|---|---|
| 1 | Raw field names in headers | `12_13_audit_verify.png` | Seq, Time, Actor, Action, Unit, Detail |
| 2 | Timestamps wrap | `12_13` | One line, with the ISO time in a tooltip |
| 3 | A 160 px panel for chain status | `12_13` | Keep **both** chain results visible: the audit chain and the blackboard chain, each with "verified through seq N", head hash and first break. They sit in a 2-column status band of 56 px, not one line, so that a break is never hidden. The Verify button stays |

## 17. Login (`/login`)

| # | Problem | Evidence | Fix |
|---|---|---|---|
| 1 | It inherits no shell, so it is the first impression in a demo | `app/login/page.tsx` | A centred 400 px panel: product mark, tenant name, sign-in, and the environment chips (Test genesis, Demo mode) so the evaluator sees the mode before signing in |
| 2 | Step-up and sign-in must allow password managers | WCAG 3.3.8 | No cognitive tests; paste allowed |
| 3 | An error state is needed | none | Inline: "Sign-in failed: the identity provider refused the request. Try again or contact your administrator." |

---

## Information architecture

| Today (15 links) | Proposed | Note |
|---|---|---|
| Decision Queue | Operate › Decision queue | |
| Look-back | Operate › Look-back | |
| (part of Reconciliation) | **Operate › Deliveries** | Works in every mode; Nightly runs links here |
| ABC Board + Reconciliation (parallel run) | Operate › Balancing | Two separate sections: ABC can block; the parallel run never blocks |
| Nightly runs | Operate › Nightly runs, with a Demo badge | 404 outside demo mode |
| Monitoring + Measures | Observe › Monitors | Advisory only |
| Coverage | Observe › Coverage | Tabs |
| Pipeline Map | Pipelines › Catalog, Graph, DAG drift, Wire a pipeline, Connections | |
| Change Requests | Govern › Change requests | |
| Rules | Govern › Rules | |
| Agents | Govern › Agents | Without the blackboard |
| (inside Blackboard) Known issues | Govern › Known issues | Proposer-only posts |
| Blackboard ("Agent") | **Assure › Blackboard ledger** | Global, with the chain check intact |
| Audit | Assure › Audit chain | |

## Wireframe index

| File | Screen |
|---|---|
| 01-decision-queue | Decision queue, plus the signals popover render |
| 02-pipelines-catalog | Pipelines catalog |
| 03-dag-viewer / 03b-dag-table | Graph, and its Table view |
| 04-wire-gate-bindings | Wizard step 4, gate bindings |
| 05-nightly-runs | Nightly runs (demo) |
| 06-change-requests | Change requests list, detail and rail |
| 07-decision-card | Decision card |
| 08-look-back | Look-back |
| 09-observe-density | Monitors (default), Rules (`?view=rules`), Coverage (`?view=coverage`) |
| 10-connections | Connections |
| 11-deliveries-schedules | Deliveries: feed items, cadence and schedules |
| 12-dag-drift | DAG drift |
| 13-blackboard-ledger | Blackboard ledger |
| 14-balancing | Balancing: ABC (can block) and parallel run (never blocks) |
| 15-agents | Agents: registry, entitlements, ladder, tier history, track record, promotion |

No wireframe exists for Audit or Login; each has a layout spec above.
