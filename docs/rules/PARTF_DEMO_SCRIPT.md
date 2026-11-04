# Part F demo script — the orals walkthrough, as built

Orchestrator-written (EXECUTION_PLAN.md step 5), covering §5's steps 1–10 (PARTF_IMPLEMENTATION_PLAN.md §5, with
R4-7's delta applied). **Mechanics only**: the start command, the sign-in identities, the exact clicks, the expected
screen, and a recovery note per step. Click-by-click screenshots and the two live rehearsals wait for a Windows
execution path (BACKLOG F-57) — this doc is what the presenter rehearses against once that path exists, and what
the orchestrator can check against the build right now.

Phase 1 only (`synthetic/out/w1`). Phase-2 steps (9b, 11, and the second CR of step 9b) need `w1_provider` and are
out of scope until P2-6 builds and pins it — not covered here.

---

## 0. Start

On the Windows machine, from a clean checkout (`run_demo.ps1`'s own job):

```
dqagent demo --data synthetic/out/w1 --nightly --earn-story --preload-nights 15
```

This drops and recreates the demo database, applies the full CP schema, binds `deployment: demo` with
`provider: anthropic` / `transport: scripted` (K6 Front 1's `explainer_cfg` wiring — **never** the global
`config/explainer.yaml`, which stays `provider: template` throughout), seeds the RELEASE AUTH_MEDICAID earn story
at L2, and preloads nights 1–15 of `synthetic/out/w1` (recorded with no person, i.e. `sim_run_next(None)`). Then
`run_demo.ps1` starts the API and the Tower. Sign in at the Tower's URL with the username field (T1 dev login —
no password); see §1 below for who signs in when.

**The 15 preloaded nights, by calendar date** (first of the demo's 22-night dataset, date order,
`synthetic_nights()`): 2026‑01‑01, 01‑10, 01‑15, 01‑20, 01‑22, 01‑25, 01‑28, 01‑29, 02‑05, 02‑09, 02‑12, 02‑19,
02‑23, 02‑26, 03‑01. **The first night the presenter runs live, via "Run next night" in step 1, is night 16:
2026‑03‑05.** This matters for step 3 — see below.

---

## 1. Sign-in identities, and why

| Identity (username) | Name shown | Groups / duties | Used for |
|---|---|---|---|
| `maker` | Alex Maker | release-medicaid, governance-rules, **operations** | The presenter's main seat: runs nights (operations duty), proposes CRs, is the first approver everywhere. |
| `checker` | Casey Checker | release-medicaid, release-intake, governance-rules | Second approver for most two-person steps (6, 9a). |
| `checker2` | Morgan Medicaid | release-medicaid, governance-rules | **F-59's second presenter identity.** A second, independent medicaid-release approver — distinct person, same authority as `checker`. Used wherever the script needs a *different* second approver than `checker` in the same segment (e.g., approving both the wiring CR in step 6 and the backtest-evidence approval in step 9a without reusing `checker` twice in a row, which reads as less convincing live). Sign in as `checker2` exactly as any other identity: type `checker2` in the Username field, click Sign in. No setup step, no seeding — this identity already exists in `DEFAULT_USERS` (`runtime.py:61-71`); nothing here needed building (K6 Front 2, F-59). |
| `auditor` | Ari Auditor | **audit** only | Read-only walk of the audit chain / blackboard, if the orals ask "how would someone check this independently". Cannot approve or release. |
| `outsider` | No Roster | release-medicaid (token claim only, **no roster grant**) | Negative-space moment: signs in fine (the IdP issues a token to anyone named in `DEFAULT_USERS`), but every scoped endpoint 403s — "token claim AND roster", not either alone. Use only if the orals ask about authorization, not part of the main 10 steps. |
| `snap`, `snap2` | Sam Snap, Riley Snap | release-**snap** only | Scope-boundary negative check (a SNAP-only reviewer sees no Medicaid data). Not part of the main 10 steps. |

**Never** sign in as the same identity to both approve and author the same CR — the database refuses it (two-person
rule), and the UI's own hint on `/agents` and `/pipelines/[id]/promote` says so plainly if it's tried live.

---

## 2. Steps 1–10, as built

### Step 1 — Run a night
**Identity:** `maker` (operations duty; the "Run next night" header button is disabled — `simBusy || queued===0` —
for anyone else lacking it, and `useShellData`'s `operator` flag gates the control to that duty).
**Click:** Header → **Run next night**.
**Expected screen:** Toast/status message with the night's counts; `Nightly runs` (nav: Operate → Nightly runs,
`/nightly`) shows a new NIGHT-ZZ-20260305-… row (the 16th night, 2026‑03‑05 — the first live one after preload).
**Data:** `runs` gets a new NIGHT root; `run_events` STARTED then COMPLETED; `sim_runs` gets the new row.
**Recovery:** if the button stays disabled, the signed-in identity lacks `dq-duty-operations` — re-sign in as
`maker`. If it 409s "a night is already running", someone else has the advisory lock; wait and retry — it clears
on completion or lock timeout, never needs a restart.

### Step 2 — Open the trace (as the operator)
**Identity:** `maker` (still signed in from step 1).
**Click:** From the Nightly runs row just created → **Trace** link (lands on `/runs/NIGHT-ZZ-20260305-…`).
**Expected screen:** The span tree (waterfall) for that night's root — PUBLISH_CYCLE children changed, `spans`
tree, `release_checks` reasons, linked-row counts.
**Recovery:** a blank or 404 trace page means the trace link was copied from a different night's row — reopen from
Nightly runs, don't type the trace id by hand.

### Step 3 — A validator rejects a SCRIPTED bad output
**This is the moment K6 Front 1's transport:scripted wiring exists for.** It does **not** happen on the night just
run live in step 1 — **empirically confirmed** by running the real preload sequence against live Postgres (not a
hand-derived guess): of the 15 preloaded nights, **six already hit a SCRIPTED REJECT during preload**: 2026‑01‑15,
01‑20, 01‑22, 01‑25, 02‑09, and **02‑12** (the most recent preloaded one, right before the live sequence starts).
The night the presenter just ran live in step 1 (2026‑03‑05) does **not** reject (confirmed through the first 11
preloaded nights by direct run; the remaining preloaded nights 02‑19 through 03‑01 were not yet confirmed by this
same empirical method at the time this doc was written — see the recovery note below before relying on this for a
live rehearsal).
**Identity:** `maker` (read-only from here; any signed-in identity with scope can view).
**Click:** Nightly runs → the **2026‑02‑12** row → Trace → find the `llm.explain` span for the NIGHT's own
`nightly_run` explanation (subject `nightly:2026-02-12`) → open its card.
**Expected screen:** `llm_calls` row: `verdict REJECT`, `source_mode SCRIPTED`, `reason_codes` naming the specific
mutation (one of `STRAY_NUMBER`/`CAUSAL`/`ADVICE`/`NEGATED`/`QUANTIFIER`/`MISBOUND`, per `transports.py`); label
reads "Scripted responses (Anthropic wire format): no model is called"; a `CHECK_REJECTED` guardrail event; the
card itself shows the **template** text with `fact_check: FALLBACK` (never the rejected provider text — the LLM
is never in the decision path).
**Recovery:** if 2026‑02‑12's explanation shows PASS instead of REJECT (e.g., a dataset or `bad_every` value
changes before rehearsal), re-derive the current REJECT list by running `rt.sim_preload(15)` against a fresh demo
database and reading `dq_ctl.llm_calls WHERE subject LIKE 'nightly:%' AND verdict='REJECT'` as `dq_auditor` — the
method used to produce this list — and pick the latest hit. Never hand-wave this step by describing what a reject
"would" look like; find a real one first.

### Step 4 — Grounded citations
**Identity:** unchanged.
**Click:** On any card's explanation (the one from step 3 works, or any PASS/TEMPLATE card) → the citation chips
under the explanation text.
**Expected screen:** `/explanations/{subject}/citations` resolves each chip to a real reference — `results:<unit>@
<version>` or `abc:<batch>` — and a template-sourced explanation's chips read "generated from facts", never
implying a model checked them.
**Recovery:** a chip with no link (label-only) means that fact has no citable row yet — this is a known, honest
gap (never silently papered over by a fabricated link); say so if asked.

### Step 5 — Per-DAG profile
**Identity:** `maker`, or `checker`/`checker2` to show the same page from a different authority's view.
**Click:** nav Pipelines → `p6_claims` row → **Profile** (`/pipelines/p6_claims/profile`).
**Expected screen:** Contributors table with approvers; `profile_digest` equal to the night root's own digest
(confirms the profile shown is the one that actually ran); monitors chip reads "not governed" (L-10, honest, not
hidden).
**Recovery:** a digest mismatch between the profile and the night root is a real defect, not a display glitch —
stop and investigate rather than narrating past it live.

### Step 6 — Add the consumer DAG, advisory
**Identity:** propose as `maker`; approve as `checker` (two different people; the UI refuses a self-approval).
**Click:** nav Pipelines → **Wire a pipeline** (`/pipelines/wire`) → fill in the `tmsis_outbound` manifest path →
**Propose wiring**.
**Expected screen:** `ImpactView` shows the proposed CR's link, `datasets_already_gated` (by the genesis gates,
ceiling **inert** on them), and states the effective tier is unchanged (`effective_tier_before` =
`effective_tier_after`, from `autonomy_tier_for`) — the consumer introduces nothing new to govern. Open the CR
link (`/governance/{cr_id}`) signed in as `checker` → **Approve**. A single approver is refused (needs 2); approve
once more as a third identity if the demo wants to show the refusal live, or trust the on-screen refusal message.
**Recovery:** if "Propose wiring" 422s on the manifest path, the path is wrong or the manifest isn't the declared
layer — the page shows this honestly (never infers a layer from folder names); fix the path, don't retype guesses.

### Step 7 — Night with the new DAG
**Identity:** `maker`.
**Click:** Header → **Run next night** again, then its Trace.
**Expected screen:** A new `PIPELINE_RUN` span for `tmsis_outbound`; `gate_checks` show **WOULD_HOLD** (advisory —
K2 is advisory-only; a true `HOLD` value doesn't exist yet and the UI never claims one); nothing is excluded from
the run; no unit's mode changes.

### Step 8 — Backtest
**This step is a CLI step, not a Tower click — the page says so explicitly, twice, so narrate it as such rather
than hunting for a backtest button.**
**Identity:** terminal, same machine (`maker`'s CR id from step 9a's wiring, once drafted — see below for the
actual order this needs).
**Click/command:** from a terminal, `dqagent backtest --cr &lt;cr_id&gt;` (the CR id from the promotion CR you are
about to draft in step 9a — draft that CR first, copy its id, then run the backtest over it, then return to the CR
page to show the evidence land).
**Expected screen:** back on `/governance/{cr_id}`, the **Backtest panel** now shows a linked report
(`backtest_reports`, label SYNTHESIZED, never LIVE — no model credentials exist, L-14) with a `delta_note` that
reads **verbatim**, not paraphrased: for a mode-only change like step 9a's, it states plainly that the delta is
legitimately zero because block mode isn't yet behaviorally enforced — never implying a real behavior check ran.
**Recovery:** if the CR's Backtest panel still says "no linked backtest report" after running the CLI command,
confirm the `--cr` value matches the CR actually open on screen — the backtest attaches evidence to one specific
CR id, never retroactively to "the most recent one".

### Step 9a — Promote the consumer to block
**Identity:** propose as `maker`; approve as `checker2` (F-59's second identity — avoids reusing `checker` right
after step 6, which read as the same two people rubber-stamping everything).
**Click:** `/pipelines/tmsis_outbound/promote` → pick the advisory TASK_GATE → **Propose promotion**. The page's
own banner states plainly: this is a label change, not a behavior change — block mode has no effect anywhere in
this build's evaluation or publish path. Run step 8's backtest CLI against the resulting CR id, then open
`/governance/{cr_id}` signed in as `checker2` → Approve.
**Expected screen:** on a bare approve attempt with no linked evidence, the server refuses with a message starting
"promotion requires a linked backtest report" (`dq_ctl.mode_promotion_refusal`) — this refusal is real, not staged;
only after step 8's CLI backtest lands does the second approval actually go through.
**Recovery:** if the second approval still refuses after the backtest ran, re-check the CR id the backtest was run
against matches the CR open on screen (same failure mode as step 8).

### Step 9b (P2) — Ceiling promotion of a new dataset
**Out of scope for this script** — needs `synthetic/out/w1_provider`, which doesn't exist until P2-6. Do not
attempt this step in Phase 1; skip straight to step 10.

### Step 10 — Veto and demotion (E-14 fail-safe)

**10a — promote AUTONOMY L2→L3 for AUTH_MEDICAID, open the veto window, let one expire.**
**Identity:** run the eval first as `maker` from a terminal: `dqagent eval run --suite agent_eval` (prints a
`{run_id, suite, source_mode, cases, passed, code_sha256}` row — `source_mode` reads DETERMINISTIC). Then on
`/agents`, still as `maker`: select the RELEASE / AUTH_MEDICAID row → **Track record** → confirm the evidence chip
and record chip both read ✓ (R4-7: demo minimums, e.g. "5/5" — read the actual number on screen, don't assume it
matches the production minimums table) → fill the rationale (cites the backtest report if relevant) → **Propose
promotion**. A second person — `checker` — opens the resulting CR and **Approve**s (promotion needs two approvers
from the approver organisation; demotion later in 10b needs only one, since it's lower-only).
Once applied: Header → **Run next night** → Queue (nav: Operate → Decision queue, `/`) shows veto-window clusters
at the top, on **both** `bronze.ie_segment` **and** `encounter_header` units — this is the live demonstration that
the Phase 1 consumer's L1 gate (step 6/9a) is inert (ORCH-1): wiring a new consumer never lowers an existing
dataset's real governance.

**Veto-window pacing — this is the single real-time constraint in the whole script.** The demo's actual governed
veto-window duration is `PT10M` (`config/state/ZZ/policy_w1.yaml`, pinned by `tests/t0/test_parte_registry.py:95`
as the real policy value — not a demo stand-in, so it is **not** shortened for the walkthrough: see
`docs/blackboard.md`'s "K6 Front 1 veto-window control — scrapped, resolved as demo choreography" entry for why a
clock-advance control was scrapped as unbuildable, and a shortened policy value was rejected as misrepresenting
the real policy). **Plan ~10 real minutes between opening the window and showing it close.** The Queue page's
veto-window chip (`VetoCountdown`, backed by `veto_window_status.remaining_s`, computed live against the real
Postgres wall clock) gives a literal live countdown — narrate against it, don't guess at timing. Use the gap to
cover steps the script doesn't otherwise sequence tightly against this one (re-show step 3's reject, the
citations in step 4, or field questions) and circle back once the countdown reaches zero to show the release at
the deadline, exactly as §5 step 10 calls for ("let one window expire to show a release at the deadline").

**10b — veto.** Pick the **other** window (the one not left to expire) before it closes.
**Identity:** any approver-scope identity other than the one that approved the L2→L3 promotion reads better live
(e.g. `checker` or `checker2`, whichever wasn't used for 10a's approval).
**Click:** Queue → that cluster's **Veto release…** link (lands on `/units/{unitKey}#veto-h`) → pick a **Reason**
from the dropdown → **Veto**.
**Expected screen:** "Vetoed: the unit is now HELD. The agent's tier for this authority drops to L2 automatically."
The trace shows a DECISION root and CEILING_APPLIED events. **Every other open window for that authority also
holds** — this is the fail-safe-by-design point (E-14): one veto demotes the authority immediately, and every
window still open for it holds rather than racing to release before the demotion takes effect.
**Recovery:** a 409 on Veto means the window already closed and the unit released — the UI's own hint explains
this plainly ("the window has passed and the unit released"); this is expected if 10a's pacing ran long. Rerun
10a's promotion on a fresh demo database if a clean veto is needed for the rehearsal, rather than trying to veto
a unit that already released.

### Step 11 (P2) — Evaluation, explorer, and attestation
**Scope:** Evaluation suite inspection, diffing, and pending attestations (K11).
- **Evaluations History:** Navigate to `/evaluations` to view recorded deterministic and LLM evaluation runs.
- **Evaluation Run Diff:** Navigate to `/evaluations/diff` to compare suite regressions and outcome deltas between runs.
- **Attestation Confirmation:** Navigate to `/agents` or call `GET /api/v1/eval-attestations?status=PROPOSED` (E-13) to review pending two-person evidence attestations for promotion.
- **Feedback & Annotations:** Explanation cards provide feedback capture logging to `/api/v1/explanations/feedback`, surfacing candidate reviews at `/annotations`.

---

## 3. Risks, stated plainly (from PFP §6, carried forward — read this to presenters before the orals, not just once)

- **No model credentials (L-14).** Every provider is tested only against a stub and the scripted transport. The
  demo never says LIVE, anywhere, for any explanation.
- **No warehouse data planes (L-04, L-15).** Real-estate DAGs beyond the synthetic set are "discovered, not
  evaluated".
- **No production IdP (L-16).** `LocalIdP` is this entire script's IdP; a `pilot` deployment would show a red
  "local IdP — not production" chip, and `production` refuses to start without OIDC. Never say or imply this demo
  is running real identity.
- **Mock parity is shape-only** for the routes this package touched (F-35's two endpoints) — Tower's mock proves
  the same response shape, not real computed values; say so if the orals ask whether the mock and the real backend
  agree on numbers.
- **Trace loss inside a deferred cycle** loses that cycle's spans; affected rows carry a labelled dangling
  `trace_id`. The label is informational, never a security control.
- **F-57 (Windows execution gap, open):** this script has never been run end-to-end on the user's Windows machine
  from this session — no Windows shell driver is connected here. Everything above is grounded in direct reads of
  the real route/component code plus, for step 3 specifically, an actual run of the real preload sequence against
  live Postgres in this cloud environment (not Windows, but the same code, same Postgres version target, same
  `dqagent` package) — not a guess. The two live rehearsals (EXECUTION_PLAN step 6) and the first Windows gate
  (EXECUTION_PLAN step 3) still need to happen on the user's machine before this script is signed off.

---

## 4. Open items this script does not resolve (tracked elsewhere, not silently dropped)

- **BACKLOG F-57**: the Windows gate and the two live rehearsals need a Windows execution path this cloud session
  doesn't have. This doc is ready for that rehearsal; it hasn't been one yet.
- **BACKLOG F-58**: a reason field on the recovery UI (descoped from K6 Front 3) — not referenced by this script
  because the feed-items page never claims to show one.
- **BACKLOG F-60**: `explain_for`'s default subject format — doesn't affect this script; the one real call site
  (`_run_night`'s `nightly_run` explanation, which step 3 relies on) already passes a compliant subject.
- The exact demo-minimums number shown on `/agents`' record chip in step 10a (this doc says "e.g. 5/5" — read the
  actual number live; it comes from `config/state/ZZ/policy_w1.yaml`'s `promotion_minimums` key, not hand-copied
  here).
