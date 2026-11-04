# What else can we build that is not the backend build — and the full to-be-implemented inventory

**Written 2026-10-29 by the orchestrator, for handing off to a fresh Claude Code session.** This file answers two questions the user asked together: (1) what remaining work is genuinely buildable on the **Tower (frontend) side alone**, with no backend change required first, and (2) what is the **complete** remaining to-be-implemented scope, for a session that wants the whole picture, not just the Tower slice.

**Read order before touching anything:** `CLAUDE.md` → `docs/HANDOVER_INVENTORY.md` → `docs/blackboard.md` (SYNC section, then newest entries) → `docs/STATUS_BOARD.md` → `docs/BACKLOG.md` → this file. This file does not repeat `CLAUDE.md`'s process rules; it assumes them. In particular: **state the frame and wait before building** (pre-work gate), **map the blast radius before editing**, **red team before and after each build**, **targeted tests only, under the gate lock**, **the orchestrator is the sole writer of `docs/blackboard.md`** — a subagent returns its entry, it does not write the file itself.

---

## 1. How "non-backend" was decided

Every open item in `docs/BACKLOG.md` plus every deferred `L-*` row was read and sorted into one of four buckets:

- **Tower-only, build now** — a real backend API/DB mechanism already exists; only the Tower surface is missing. No backend file needs to change.
- **Mixed** — needs a backend change (new column, new endpoint, new auth mechanism) before or alongside the Tower work. Not "non-backend."
- **Backend-only** — no UI implied, or the UI already exists and only backend logic is wrong/missing.
- **Process / governance-only** — not a build at all: a decision (two-person classification, an architect assessment, a user scheduling choice).

Only the first bucket answers "what else can we build that is not the backend build." It has exactly two strong candidates and two weak ones.

---

## 2. Tower-only, build now — ranked

### 2.1 F-45 — agent notes panel on the decision card (**strongest candidate**)

- **What's missing:** the card endpoint already returns `agent_board` — `dqagent/api/app.py:551-552` (confirmed again this session, verbatim): a list of `{seq, seat, kind, template, text}` rows, `text` pre-rendered server-side (`_render(b["template"], b["body"])`). No Tower component reads this field anywhere in `tower/src` (two independent searches, this session and the prior one, agree).
- **Why it's real backend work with zero backend change:** the field is already on the wire, already rendered to plain text, already governed (the `agent_board`/`bb_post` mechanism is the same one `known_issues` in §2.2 uses). This is a pure rendering gap.
- **Demo value:** storyboard beat A3, "what the agent thinks is going on" — one of the core diagnosis beats, named explicitly in `docs/EXECUTION_PLAN.md`'s K-CL addendum as a natural K6-adjacent item.
- **Scope for a Tower seat:**
  - Add a panel (suggested: below the dispositions table, above `proposed_actions`) on the unit-card view that renders `agent_board` entries in order of `seq`, labeled by `seat` and `kind`.
  - The response also already carries `known_issues` and `x_known_issues_hidden_for_checker` on the same object (`dqagent/api/app.py:552`) — a natural adjacent add is a small "known issues affecting this unit" sub-list in the same panel, respecting `x_known_issues_hidden_for_checker` (never show a hidden one — this is the same F7 rule that governs the dedicated screen in §2.2, so get it right once and reuse it).
  - Mock route: confirm `lib/mock/*` already returns a shape-equivalent `agent_board`/`known_issues` (if not, add fixture data — same-shape-as-production is a standing invariant, `CLAUDE.md`).
  - No PHI/Part 2 concern: `agent_board.text` is rendered from `b["template"]`/`b["body"]`, not raw row values — confirm this holds for whatever fixture/live content actually appears before shipping (the standing trace-value rule applies to any new surface).
- **Acceptance:** a unit with ≥1 `agent_board` entry shows the panel populated, in `seq` order; a unit with none shows nothing (no empty panel chrome); a hidden known issue never renders regardless of role. Tower smoke extended with one assertion per case.
- **Owner:** Tower seat alone. **Not backend.**

### 2.2 F-47 — Known Issues screen (**second-strongest candidate**)

- **What's missing:** the full backend mechanism already exists and is already governed:
  - `GET /api/v1/agent/memory` (`dqagent/api/app.py:905-916`) — lists `KNOWN_ISSUE` (and other) memory rows, with the F7 visibility rule already enforced server-side: a `PROPOSED` row is visible to a second reviewer; an `APPROVED` row is visible only to its own proposer and approver (never to a generic checker).
  - `POST /api/v1/agent/memory/known-issues` (`:925-934`) — propose a known issue (`rule_id`, `authority`, `reason_code` from `agent.memory.REASON_CODES`, `expires`).
  - `POST /api/v1/agent/memory/{memory_id}/challenge` (`:936-944`) — two-person APPROVED/REJECTED verdict, returning a `step_up_url` — **this is the standing challenge-bound step-up pattern** (`CLAUDE.md` "Decision authentication"), so this screen can reuse `StepUp.tsx` + one of the existing decision-flow components directly rather than inventing a new auth path.
  - No Tower route exists for any of this (confirmed, `api/app.py:905` cited as the gap in `BACKLOG.md` F-47 and `HANDOVER_INVENTORY.md`).
- **Demo value:** storyboard beat D3.
- **Scope for a Tower seat:**
  - New route (suggested `governance/known-issues` or `agent/memory`, matching the existing `governance/[crId]` naming convention already in the app).
  - List view from `GET /api/v1/agent/memory`, filtered/sectioned by `kind === "KNOWN_ISSUE"` and `status` (`PROPOSED` vs the visibility-restricted `APPROVED`).
  - Propose action → `POST .../known-issues` (needs `rule_id`, `authority` in `scope(p)`, `reason_code`, `expires`).
  - Decide action → the standing step-up flow against `POST .../{memory_id}/challenge`. **First check whether this maps cleanly onto the existing `VerdictFlow`/`DecisionPanel`/`CheckerFlow` components** (same question F-66 raises generally — don't build a fourth bespoke decision widget if one of the three existing ones fits with a thin adapter).
  - Mock route for both endpoints, same-shape as production.
- **Acceptance:** propose → appears as `PROPOSED` to a second reviewer, not yet visible to a generic checker as approved; challenge APPROVED → visible only to proposer+approver afterward (F7, server-enforced, but the UI must not contradict it by rendering something the API didn't return); challenge REJECTED → disappears from the active list. Mock + live-shaped smoke.
- **Owner:** Tower seat alone for the UI. **Not backend** — but do the `VerdictFlow` reuse check (a Tower-side investigation, not a backend change) before writing a new decision widget.

### 2.3 Smaller, lower-value Tower-only items

- **F-50** (`tower/src/.../smoke.mjs` test-only) — the K4 graph page's "first focusable element" smoke assertion doesn't account for the app's pre-existing global skip-link. Fix: narrow the assertion to "first focusable element inside `<main>`," or drop it as redundant. One file, test-only, no product code touched. `docs/BACKLOG.md` F-50.
- **F-51** (same file) — the graph/table row-count smoke assertions race the mock server's fixed 150ms latency; wait for the real row count (or absence of a loading state) before asserting, here and anywhere this pattern recurs. Test-only. `docs/BACKLOG.md` F-51.
- Both are safe, bounded, same-seat-can-do-both-in-one-pass items. Worth batching together; neither is demo-visible, so they're correctly lower priority than §2.1/§2.2.

---

## 3. Flagged, but not a pure "build" — do not treat these as ready Tower work

- **F-66** — whether Tower's approval/decision surfaces (`governance/[crId]`, `pipelines/[id]/promote`, the K-CQ clinical panel, and any future one — now including whatever §2.2 builds) should consolidate onto the `VerdictFlow` pattern via a dedicated Architect assessment. This is real, valuable, and genuinely non-backend — but it is an **assessment**, not code, and `docs/BACKLOG.md` already names its owner as "Architect (future)." A fresh session should run this as an architect pass (read `tower/src/components/{DecisionPanel,CheckerFlow,VerdictFlow,StepUp}.tsx` plus every screen that uses one, produce a design-doc verdict) **before** building a fourth bespoke decision widget anywhere else in this backlog — which is exactly why §2.2 above points back here.
- **F-46** — the source-feedback loop (K-CL's centerpiece). Explicitly scoped in `docs/EXECUTION_PLAN.md`'s K-CL addendum as "Architect, then Backend + Tower" — a real design needs to exist (what a feedback package contains, how a corrected replacement is recognized against the quarantined original) before any Tower work is meaningful. Not non-backend. Not scheduled — the user has not yet decided whether/when K-CL runs at all (`docs/HANDOVER_INVENTORY.md` K-CL row).

---

## 4. Everything else — the full to-be-implemented inventory, for completeness

Everything below needs a backend change, is backend-only, or is a decision rather than a build. Grouped by disposition. Every ID below is already tracked in `docs/BACKLOG.md` — this is an index, not a new record of record.

### 4.1 Backend-gated (Tower work exists or is trivial, but can't be meaningfully built without the backend piece first)
| ID | What | Why it's backend-gated |
|---|---|---|
| K7 | Mock/production parity mechanism | No real route exists yet for K8-K12's touched surfaces; a parity test against invented routes proves nothing |
| K9 | Ceiling promotion step (b) | Backend-only: a track-record-snapshot field, no UI implied |
| K10 | Robustness (advisory lock, SLA fix, webhook sink, recovery endpoint) | `sla_due`'s bug is real backend Python (`dqagent/api/app.py:424`); the rest has no UI at all |
| K12 | Final readiness | Process-only: step 9b, rehearsal, retroactive R5, final receipt — all wait on the real backend pass |
| F-58 | Persist the `RECOVERY_DATA_LOST` reason (new `feed_items` column + write site) | Needs its own schema-change package + blast-radius map |
| F-60 | `explain_for`'s default-subject allow-list gap (9 of 12 `KINDS`) | Backend-only, demo-path unaffected, needs its own blast-radius map before touching `REFS_PREFIXES` |
| F-62 | No server-side authority partitioning on the 3 K-CQ clinical endpoints | Backend auth logic, not a UI gap |
| F-63 | Mock-contract catalog not extended for the 3 new clinical routes | Documentation of backend routes |
| F-64 | `clinical_cbp.yaml` not wired into any state profile | Config/backend, cosmetic for single-state demo |
| F-65 | K-CQ approve endpoint has no step-up (named exception) | Needs a new `/challenge` endpoint before Tower can reuse `VerdictFlow` here |
| F-67 | `kpa_conversion`/`reshape` never create a pending-approval row | Backend default (`clinical_normalize.py:233`) |
| F-11c | `gov_item_valid`'s NULL-swallowing boolean accumulator | Pure SQL/backend, pre-existing |
| F-11d | `_is_mode_only_cr` is CR-wide not per-item | Pure backend (`dqagent/backtest.py:138-155`) |
| F-61 | K-CQ live end-to-end rehearsal | Needs Postgres + a registered governed CR; deferred by user decision |

### 4.2 Process / governance / decision-only (not a build at either layer)
| ID | What | Who decides |
|---|---|---|
| F-68 | 68/119 canonical elements unclassified (most notably `CQP.MEMBER_ID`) | A two-person CLASSIFICATION governance decision — a build seat deciding this alone would bypass the governance it's supposed to honor |
| K-CL (whole) | Whether/when the clean-up loop runs at all | User |
| L-17 | Multi-cloud/DAG execution substrate for L-06 | Held at the user's explicit instruction; unsolved (no convergent credential model found across 3 red-team rounds) |

### 4.3 Deferred roadmap (`L-01` through `L-16b`, excluding `L-06`/`L-17` above) — unchanged, listed for completeness only
These were already deferred before this session and nothing here changes their disposition. See `docs/BACKLOG.md`'s "Later / deferred (decided)" table for the current one-line description and reason on each: `L-01` Connections/DAG-drift screen, `L-02` HTTP gate + workload-identity tokens, `L-03` ADF/Databricks/Snowflake Tasks/dbt Cloud adapter interfaces, `L-04` real data-plane adapters, `L-05` code-set registry (Part G), `L-07` knowledge graph, `L-08` break-glass access, `L-09` look-back/two-person-ACCEPT/roster governance, `L-10` monitor-spec governance, `L-11` raw-file retention policy, `L-12` run↔batch correlation, `L-13` downstream-consumer record, `L-14` real model run (blocked on user API keys), `L-15` Snowflake execution (no environment), `L-16b` PHI trace redaction/retention/OTLP egress control. None of these are Tower-only; most are backend/infrastructure or explicitly blocked on something outside this repo.

---

## 5. Recommended order for a fresh session

1. **F-45** (§2.1) — smallest, highest demo value, zero backend risk. Good first package for a session re-establishing context.
2. **F-47** (§2.2) — larger, same backend-already-exists property. Do the `VerdictFlow`-reuse check (ties into F-66) before writing new decision-widget code.
3. **F-66** (§3) — run as an Architect assessment once F-47 exists as a second real data point (alongside `governance/[crId]`, `pipelines/[id]/promote`, and K-CQ's panel) for what to consolidate.
4. **F-50 + F-51** (§2.3) — batch together, low priority, safe filler between the above.
5. Everything in §4 waits on a backend pass or a user decision — do not start it from "what else can we build that is not backend."

Each of the above still goes through this repo's standing discipline: state the frame and wait (pre-work gate), map the blast radius, red team before and after, targeted tests only under the gate lock, independent re-verification before calling anything closed, and the orchestrator transcribes the entry to `docs/blackboard.md` — no seat writes it directly.
