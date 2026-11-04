# Command Tower layout system

The design goal is a console a reviewer can work in for six hours. It should read in one sweep, show alarms where they live, and never spend a pixel of the first viewport on decoration. The reference points are operations desks: Datadog and Grafana for signal density, Linear for list and detail flow, and Airflow's grid for run history. It is not a dashboard of cards.

The tokens are additive to `tower/src/app/globals.css`. Every existing tone token (`--ok-*`, `--info-*`, `--warn-*`, `--bad-*`, `--crit-*`, `--neutral-*`) is kept at its current value. New tokens add type, spacing, density, shell, medallion layers and graph edges. The reference implementation is `ux/_build/shared.css`, which the wireframes use.

## Principles

1. **Exception first.** Healthy things are quiet and one line tall. Alarms tint in place, so position stays stable and the eye learns where to look.
2. **List and detail, not page jumps.** Every queue-like screen is a split pane. The full-page view exists only where a decision is made.
3. **One line per row.** Explanations, unit keys and long text move to the detail pane, an expanding row or a tooltip. They never sit inline in a list.
4. **Words do the work.** Every status chip has a text label and a glyph. Colour is the third channel, never the only one.
5. **The medallion is the house vocabulary.** Raw, bronze, silver and gold always appear the same way: a 3 px coloured tick plus the word. This is the one distinctive visual device in the system, and it is used consistently rather than decoratively.

## 1. App shell

```
┌──────────────────────────────────────────────────────────────────────────────┐ 44 px header
│▣ Command Tower │State A      │ Search…  Ctrl K │ Test genesis T1│Mock│ Business date 2026-03-12 [Run next night] │AO│
├──────────────────────────────────────────────────────────────────────────────┤ 28 px strip
│! Decisions due 148, 6 past SLA │! Veto window 1, 7:57 │! ABC 2 out │! DAG drift 1 │✓ Pipeline order │✓ Ruleset d45d1cbc │ … Updated 8 s ago │ All 11 signals │
├────────────┬─────────────────────────────────────────────────────────────────┤
│ Operate    │ Page title  sub-line                            [actions]      │ 44 px page header
│  Decision… │ Tabs (optional, 36 px)                                         │
│  Look-back │ Toolbar: filters · sort · density (38 px)                      │
│  Nightly…  ├──────────────────────────────┬──────────────────────────────────┤
│  Balancing │ list (dense table)           │ detail pane          │ decision │
│ Observe    │                              │                      │ rail     │
│ Pipelines  │                              │                      │ (governed│
│ Govern     │                              │                      │  screens)│
│ Assure     │                              ├──────────────────────┤          │
│ [? ] [<]   │ status footer: selection, keys│ sticky action bar    │          │
└────────────┴──────────────────────────────┴──────────────────────┴──────────┘
  212 px rail (52 px collapsed)
```

At 1440×900 content starts at y=116, against about y=440 today.

### 1.1 Header (44 px)

| Zone | Content | Rule |
|---|---|---|
| Brand cell (rail width) | Mark and "Command Tower" | The cell collapses with the rail to the mark only |
| Tenant | Display name, with agency and division on a second 11 px line | From `GET /tenant`, as today; never hard-coded |
| Search | "Search units, rules, nodes, change requests", Ctrl K | Opens a command palette: navigation, entity search, and actions such as "Run next night" |
| Environment chips | `Test genesis T1` (amber), `Mock data` (slate) | Shown only when true. These replace the Genesis tile and the MOCK DATA flag |
| Scope | The user block names the authority scope ("State agency, scope AUTH_MEDICAID") | Every list states the scope it shows. SNAP data appears only for SNAP-scoped users (E5) |
| Simulated clock | "Business date 2026-03-12", a Simulated chip and **Run next night** | Only in demo mode. The button shows only for the operator role (today's `13_06_non_operator` rule) |
| User | Avatar, name, organisation label | Its menu holds Theme (System / Light / Dark), Shortcuts and Sign out. Theme and sign-out no longer take a row |

### 1.2 Status strip (28 px, one line, never wraps)

Which signals belong in the strip? Only those that are **cross-cutting** and **can change during a session**. Static configuration does not belong there. The slots are fixed; there is no per-user pinning (cut in red-team round 1).

| Fixed slot | Why it earns a slot |
|---|---|
| Decisions due, with the count past SLA | The primary work signal. It merges three of today's tiles: Held units, Decisions due and SLA remaining |
| Veto window (open count, time left) | Time-critical: an L3 unit releases when its PT10M window closes unless a person vetoes |
| ABC (out-of-balance count) | An ABC failure in a batch raises its units to H_IN_2 |
| DAG drift (unratified count) | A drifted gated task holds with the reason DRIFT_UNRATIFIED |
| Pipeline order | It rarely fails, but when it does, every run is suspect |
| Ruleset (short hash; tints on drift or when missing) | Drift is the governance alarm |

| In the "All 11 signals" popover | Why it is not a slot |
|---|---|
| DENY lag | A liveness objective; it gets a strip slot only while breaching its 900 s objective |
| Oldest held | It duplicates the past-SLA information |
| Assigned to me | It is personal: a badge and filter on the queue |
| Genesis | Static; shown as a header chip when not ratified |
| Explainer | Static configuration |

Behaviour:
- **Fixed slot order.** A healthy slot is a glyph plus a label (✓ Pipeline order). An alarmed slot shows its value, a bad or warn tint and a 2 px underline. Slots never reorder.
- **Scope.** Every count in the strip and the popover covers the viewer's authority scope only, and the popover says so (E5).
- **Each slot is a link** to the screen that resolves it, already filtered.
- **Live.** "Updated 8 s ago" sits at the right. If the summary is older than twice the poll interval, the text turns warn and reads "Stale, last updated 2 min ago".
- **Status messages (WCAG 4.1.3).** The strip is `role="status"`, but it announces only *transitions into or out of alarm*, never each poll. A hidden live region carries the transition text.

### 1.3 Navigation rail

```
Operate     Decision queue   148      g q
            Look-back          3      g l
            Deliveries         1      g d    (every mode)
            Balancing          2      g b    (ABC can block | parallel run never blocks)
            Nightly runs     Demo     g n    (demo mode only)
Observe     Monitors           1      g m
            Coverage                  g v
Pipelines   Catalog                   g p
            Graph                     g g
            DAG drift          1      g f
            Wire a pipeline
            Connections
Govern      Change requests    2      g c
            Rules                     g r
            Agents                    g a
            Known issues              g k
Assure      Blackboard ledger         g e
            Audit chain               g u
```

- The group labels are sentence case and in muted 11 px semibold. They are not all-caps eyebrows.
- Badges show work that is waiting, in a tone: bad for overdue, warn for open look-back items, neutral for counts.
- The rail collapses from 212 to 52 px with `[`, and the state persists. Collapsed, each item shows its icon with a tooltip, and a bad badge becomes a corner dot with its number. The DAG viewer opens with the rail collapsed by default, because the canvas needs the width.
- **The home for Nightly runs** is Operate, with `g n` and a "Demo" badge, because the simulator answers 404 outside demo mode (PARTE §10b). Outside demo mode the item is hidden. Its one action, Run next night, appears in the page header, on the header clock and in the command palette. It is shown only to people with the operations duty. There is no bulk "run all" action.
- **Expected deliveries** live under Operate › Deliveries, which works in every mode; Nightly runs links to it.

### 1.4 Keyboard

| Keys | Action |
|---|---|
| Ctrl K | Command palette: go to, search, run actions |
| `/` | Focus the page filter |
| `g` then a letter | Go to a screen (see the rail) |
| `j` / `k`, or ↓ / ↑ | Move the selection in a list |
| Enter | Open the selection (detail, or full card) |
| `x` | Toggle row selection |
| `[` | Collapse or expand the rail |
| `?` | Shortcut sheet |
| Esc | Close the drawer, popover or panel |
| Graph: + / − / 0 | Zoom in, zoom out, fit |
| Graph: arrow keys | Move between connected nodes (← upstream, → downstream, ↑/↓ siblings) |

All single-character shortcuts are active only while focus is not in a text field. A Shortcuts preference turns them off or remaps them, as WCAG 2.1.4 requires.

## 2. Type and spacing

**Faces.** All faces are self-hosted with `next/font/local`, with no external fetch, so the Tower works on an isolated network.
- **Public Sans** (SIL OFL) for all UI text. It was drawn for public-sector interfaces, which fits the people who use this console. It is neutral and legible at 12–13 px, and its tabular figures line up in tables.
- **JetBrains Mono** (SIL OFL) only for machine identifiers: unit keys, rule IDs, node names, hashes and file names. It is never used for labels or prose.
- `font-variant-numeric: tabular-nums` is set globally.

| Token | px / line-height | Weight | Use |
|---|---|---|---|
| `--t-xs` | 11 / 1.35 | 500–600 | Chips, rail group labels, legends, secondary meta |
| `--t-sm` | 12 / 1.4 | 400–600 | Table headers, toolbar fields, strip, sub-lines |
| `--t-base` | 13 / 1.45 | 400 | **Default body and table cells** |
| `--t-md` | 14 / 1.5 | 400–600 | Plain-language explanations, section titles |
| `--t-lg` | 16 / 1.3 | 600 | Page title (h1), stat values |
| `--t-xl` | 20 / 1.25 | 600 | Rare: an empty-state heading |

Today's body is 15 px; the new default is 13 px. Plain-language explanations stay at 14 px, with a measure of at most 68 characters, because those must be read, not scanned.

**Spacing.** The grid is 4 px: `--s1`…`--s6` = 4, 8, 12, 16, 20, 24. The page gutter is 16 px, the gaps between detail sections are 16 px, and the gaps inside a control are 6–8 px.

**Radii.** 3 px for chips and 5 px for controls and panels. Nothing is fully rounded except avatars and toggles. Hierarchy comes from borders and surface steps (`--surface`, `--surface-2`, `--surface-3`), not shadows. Only floating layers (popover, drawer, graph overlays) cast a shadow.

## 3. Tables

| Rule | Spec |
|---|---|
| Density modes | Compact 28 px, **Default 32 px**, Comfortable 40 px. A segmented control sits in the toolbar and persists per user per table |
| Header | 30 px, sticky, `--surface-2`, 12 px semibold, sentence case. Today's tracked all-caps headers are dropped |
| Sticky first column | On tables wider than their pane, the identifier column sticks |
| Column priority | Each column is tagged p1, p2 or p3. **p1 always shows. p2 hides below 1600 px viewport width, p3 below 1800 px.** A "Columns" menu restores hidden columns per user. Hidden values stay available in the detail pane |
| Alignment | Numbers align right with tabular figures. Identifiers use mono, left aligned. Chips align left |
| Truncation | Use one line and an ellipsis. The full value is in a tooltip on hover or focus and in the detail pane, with a copy button on identifiers. A long unit key truncates in the middle (`ZZ:MEDICAID:a3f19c…0002377`) |
| Grouping | Group rows are 30 px, `--surface-2`, and show the label and a count, for example "AUTH_MEDICAID 144 units in 9 clusters". They can be collapsed |
| Expanding rows | Explanations and evidence open under the row on Enter or click, one at a time by default |
| Row actions | Selecting a row reveals its actions in the detail pane. Destructive actions (Revoke, Withdraw) never sit as a column of buttons |
| Selection and bulk | A checkbox column. "N selected" with bulk actions appears in the status footer |
| Large lists | Virtualise above 200 rows. Sticky group headers keep working |
| Empty cell | An en dash in `--faint`, never a blank |

## 4. Cards versus tables

Use a **table** when items share attributes and people compare or scan them. This covers every list in the Tower.

Use a **stat grid** (bordered cells and numbers, no shadows) only for 4–8 counters that belong to one object, such as a night's summary. Never put a list inside a stat cell.

Use a **card** only for a self-contained object shown alone: an empty state, or a promotion form in a drawer.

The current pattern of a panel per section, each with its own border and heading, stacked down the page, is replaced by tabs (for parallel content) or detail sections separated by a 1 px rule (for one object).

## 5. Split panes

| Screen | List | Detail | Third column |
|---|---|---|---|
| Decision queue | Clusters (flex) | Cluster summary, results, units, actions (452 px) | none |
| Change requests | Requests (540 px) | Plain language, diff, impact, events (flex) | Decision rail (290 px) |
| Look-back | Items grouped by revoked version (flex) | Explanation, release facts (420 px) | Decision rail |
| Rules, Agents, Monitors, Balancing, Known issues | Same pattern | | |
| Nightly runs | Run grid (flex) | Selected night (420 px) | none |
| Graph | Canvas (flex), with a Table view toggle | Node panel (340 px) | none |
| Decision card | Evidence (flex) | none | Decision rail (330 px) |
| DAG drift | Drift list (380 px) | Diff, rename candidates, affected bindings (flex) | Draft CR rail (380 px) |
| Connections | Connections (flex) | Fields, test result, last discovery (440 px) | none |

- The divider can be dragged, with limits of 320 px minimum and 60% maximum, and it remembers its position. A keyboard alternative sits on the divider: focus it and press ←/→ (WCAG 2.5.7).
- The selection lives in the URL (`?sel=CR-ca572f0705`), so a link opens the same view and Back works.
- Below 1280 px the detail becomes a right drawer over the list, and Esc closes it.
- **Decision rail.** Every governed object (unit, CR, look-back item, known issue) has the same right rail. It shows the approvals needed, who can act and why the current user can or cannot, the action buttons, and the step-up prompt. It never scrolls away.

## 6. Page anatomy

1. **Page header (44 px).** It holds the h1, a one-line sub-line with the key count, and at most two buttons on the right: one primary and one secondary. Breadcrumbs only appear inside a workspace (Pipelines / Graph).
2. **Tabs (36 px, optional)** for parallel views of the same subject, with counts.
3. **Toolbar (38 px).** It holds the filter field (`/`), filter chips that show their value ("Authority **All**"), then on the right the sort and density controls. Secondary filters fold into "More filters N".
4. **Content.** The page body does not scroll; panes scroll on their own.
5. **Status footer (32 px, list panes).** It shows the selection count and keyboard hints.

## 7. Empty, loading, error and stale states

| State | Treatment | Copy pattern |
|---|---|---|
| Loading under 300 ms | Nothing | none |
| Loading over 300 ms | Skeleton rows at the current density, with the header and toolbar already real. No spinners in tables | none |
| Empty, nothing to do | A centred 20 px heading, one line, one action | "No decisions due. The next night runs at 02:00, or run it now." with [Run next night] |
| Empty, filtered | Inline in the table | "No clusters match these filters." with [Clear filters] |
| Error, fetch failed | An inline callout in the pane (`t-bad`) that keeps the last good data visible underneath, dimmed | "Could not load clusters: the API returned 503. Showing data from 17:52. [Retry]" |
| Error 401 | A full-pane sign-in prompt | "Your session ended. Sign in to continue." |
| Error 403 | Inline `t-info` | "Your role cannot see AUTH_SNAP units. Ask an administrator for the scope." |
| Stale | The strip's "Updated" text turns warn, plus a dimmed timestamp in the pane | "Stale: last updated 2 min ago." |
| Fail-closed configuration | The strip's Ruleset slot turns crit | "No effective ruleset. Every unit holds." |

The voice is plain, active and specific. Errors state what happened and what to do. They do not apologise.

## 7b. Explanations, counts and scope

| Rule | Spec | Source |
|---|---|---|
| Provenance chip | Template text carries `template` plus **`deterministic template`**. "fact-checked" appears only on provider text that passed the check. FALLBACK is a warn chip with one sentence of explanation | `components/Explanation.tsx:20-31` |
| Wording | Facts only: what happened, what state it is in, which rule and which count. No causal links ("because", "due to", "so"), no judgements ("the agent judged"), no advice ("proposes release") | PARTE §7; red team C1 |
| Small counts | In explanation text, every count of 1–10 reads "fewer than 11". In tables, record and row counts of 1–10 show `<11`. Operational counts (units, rules, approvals) stay numeric in table chrome | `presentation/suppress`, overlay threshold 11 |
| Scope | Lists, counts, the strip and run summaries cover only the viewer's authority scope, and say which scope | E5, `blackboard.md:1373` |
| Mode facts | FAIL is never autonomous (floor → H_IN). ABC failed → H_IN_2. A missing tier is L1. A veto window opens only for PASS or TA_WARN at L3 and lasts PT10M. The Tower shows **Mode** and **Why** (the first trace step that set the mode); it never shows a tier as if it were the outcome | `hitl/modes.py:51-66`, `policy_w1.yaml` |
| Provenance of discovered data | Anything from a planned estate or a SYNTHESIZED fixture is labelled "discovered, not evaluated" or "SYNTHESIZED". It never gets ✓ run or gate glyphs | PARTF_ARCH §0.4, §5 |

## 8. Alert colour system

| Tone | Token family | Glyph | Means | Example |
|---|---|---|---|---|
| ok | `--ok-*` | ✓ | Passed, released, balanced, healthy | PASS, RELEASED, Approved v9 |
| info | `--info-*` | i or none | In progress, informational, waiting by design | EVALUATING, Awaiting approval, WAIT edge |
| warn | `--warn-*` | ! | Needs attention soon, or loosening | TA_WARN, HELD, veto window open, Loosening |
| bad | `--bad-*` | ✕ | Failed, overdue, out of balance | FAIL, past SLA, ABC out |
| crit | `--crit-*` | ✕ (inverse) | Safety stop: fail closed, broken audit chain | No ruleset, chain break |
| neutral | `--neutral-*` | none | Facts with no judgement | Mode H_IN, template |

Rules:
- A chip is always text, optionally with a glyph, on a tone background with a tone border. Colour never stands alone (WCAG 1.4.1).
- Tint has three placements only: the **strip slot**, the **chip** and the **row's left edge** (3 px, for the selected or alarmed row). Rows never get a full-width tinted background, except the expanded-error row in Balancing.
- Severity ordering for sort and roll-up: crit > bad > warn > info > ok > neutral.
- **Medallion layers** are a separate, earthy hue family (raw clay-grey, bronze, slate silver, ochre gold, and a deep teal for outbound). They never use the tone colours, so a gold node cannot be misread as a warning.
- **Graph edges.** Within a pipeline: solid neutral. Cross-pipeline WAIT: dashed blue. Cross-pipeline AS OF: dotted amber. Edges are told apart by dash pattern as well as hue.

## 9. Themes

- Light and dark are both first-class. **The default is System, and demos run Light**, because projectors wash out dark themes.
- The header and rail chrome are dark in both themes, so the frame stays the same and the content area carries the theme.
- `:root[data-theme]` overrides plus the `prefers-color-scheme` guard keep their current mechanics.

Measured WCAG contrast ratios (computed in this session):

| Pair | Light | Dark |
|---|---|---|
| Text on background | 16.2:1 | 16.4:1 |
| Muted text on surface | 7.9:1 | 9.2:1 |
| Faint text on surface-2 | 5.0:1 (`--faint` darkened to #5f6771; the old #6b737d gave 4.2) | 5.9:1 |
| Every tone chip, foreground on background | 6.9–9.1:1 | 9.0–9.8:1 |
| Layer ticks and labels on surface | 5.1–5.8:1 (gold darkened to #7a5d08) | 7.5–9.7:1 |
| Graph edge on medallion lanes (non-text) | 3.4:1 (`--edge` #7b838d; the first draft gave 2.3) | 3.4:1 (#647080) |
| Control border (`--border-strong`) on surface | 3.2:1 | 3.7:1 |
| Focus ring on surface | 6.4:1 | 8.4:1 |

## 10. Accessibility: WCAG 2.2 AA

| Criterion | How the system meets it |
|---|---|
| 1.3.1 Info and relationships | Real `<table>`s with `scope`. Group rows use `<tr>` with the `rowgroup` role. The split panes are landmark `<section aria-label>`s |
| 1.4.1 Use of colour | Every status pairs text and a glyph with colour. Edges also differ by dash. Layers carry their word |
| 1.4.3 / 1.4.11 Contrast | See §9. Every text pair is at least 4.5:1, and every UI or graphic element is at least 3:1 |
| 1.4.10 Reflow | The shell reflows to a drawer layout down to 1024 px. Data tables and the graph are a two-dimensional exception, but scroll inside their pane, never the page |
| 1.4.12 Text spacing | No fixed-height text containers, except single-line chips that truncate with a tooltip |
| 1.4.13 Content on hover or focus | Tooltips and the signals popover can be dismissed with Esc, can be hovered, and persist until dismissed |
| 2.1.1 Keyboard | Every action has a key path. Graph nodes are focusable, and arrow keys follow edges |
| 2.1.4 Character key shortcuts | Single-key shortcuts can be turned off or remapped, and are inactive inside inputs |
| 2.4.3 Focus order | Header, strip, rail, page header, toolbar, list, detail, decision rail |
| 2.4.7 / 2.4.11 Focus visible and not obscured | A 2 px focus ring with a 2 px offset. Sticky headers and action bars set `scroll-padding` so a focused row is never hidden behind them |
| 2.5.7 Dragging movements | Graph pan and zoom has buttons and keys. Split dividers respond to the arrow keys |
| 2.5.8 Target size | Every interactive target is at least 24×24 px: rail items 30 px, buttons 24–28 px, toggles 30×17 inside a 24 px row hit area. Chips are not targets |
| 3.2.6 Consistent help | The `?` shortcut sheet and help sit in the same place, the rail footer, on every screen |
| 3.3.7 Redundant entry | The wire-a-pipeline wizard carries every earlier answer forward and never asks twice |
| 3.3.8 Accessible authentication | Step-up sign-in allows paste and password managers, and has no cognitive test |
| 4.1.2 Name, role, value | Toggles are `role="switch"` with `aria-checked`. Tabs use the `tablist` pattern. The rail has `aria-current="page"` |
| 4.1.3 Status messages | The strip announces only alarm transitions. Toasts for completed actions are `role="status"`, and their wording matches the action ("Vetoed" after "Veto release") |
| Graph text equivalent | An Outline tab lists pipelines, then layers, then nodes as a tree. The node panel's Lineage tab lists edges in words. The SVG has a `<title>` and `<desc>` |
