# K7-K12 mock-only buildout — deferred-to-production catalog

Additive, running log. Every entry here is a decision to defer real implementation of something to the later backend-build pass (run separately, in Claude Code, by the user). Nothing in this file is ever removed or overwritten — new entries get appended as the mock-only buildout proceeds through K7-K12. This is the handoff contract the architect's assessment (2026-10-29) recommended, so the backend pass implements TO a contract instead of reverse-engineering one from this session's history.

Companion to `docs/FRAMEWORK_FEEDBACK.md` (process-level learning) and `docs/BACKLOG.md` (demo defects) — this file is neither; it is scope-deferral bookkeeping only.

**Convention used throughout:** any screen built in this pass carries a persistent, structural visual marker — a fixed banner, not a small badge — reading **"PRE-BACKEND — NOT WIRED TO A REAL SYSTEM"**, driven by a single shared flag/component, distinct from every K0-K6 screen (which mirrors a real, tested backend). No screen built under this contract may claim a capability (promotion, enforcement, causal outcome) the backend does not actually have.

---

## DEFERRED — K7 (Mock/production parity mechanism)

**Decision:** full defer. Not attempted in any form this pass.

**Why:** K7's entire mechanism (`seams.yaml` manifest + recorder + parity tests + generated contract types) exists to certify that a mock route matches a REAL route's recorded response. There is no real route for anything K8-K12 touches yet. Building "K7" against invented routes would produce a parity test that passes by construction — proving nothing, and actively misrepresenting what K7 is for.

**What the backend pass needs to do instead:** once K8/K10/K11's real backend routes exist, run K7 as originally scoped (PFP P1-M:370, P2-2:967-982) against them. The `seams.yaml` manifest should get placeholder entries now (disposition `planned`) for every mock route this pass invents — see each package's entry below for the exact route names/shapes to carry over.

---

## DEFERRED — K9 (Ceiling promotion, step b)

**Decision:** full defer. No Tower work attempted.

**Why — corrected finding, important:** the core promotion/demotion capability this package's name suggests is **already real**, built in K0, already part of K6's live demo ("Propose promotion"/"Propose demotion" buttons on `/agents`, the veto-window panel, the evidence-gated refusal-before-bundle moment — all confirmed by direct code read during K6's demo-script authoring, not mock). K9 "step b" itself is narrower than its name implies: PFP's own spec says *"E-07 is already closed by K0. K9 adds the gate evidence to the snapshot."* That's a backend data-precision refinement to what's recorded in the track-record snapshot, with no new UI surface at all — nothing to build mock-only, and nothing missing from the demo by deferring it.

**What the backend pass needs to do:** implement the snapshot's gate-evidence field per PFP P2-5:1010-1024. No Tower change implied; confirm this against the real `/agents` screen once done, don't add a new control.

---

## DEFERRED — K10 (Robustness) — corrected to full defer

**Original plan (wrong, corrected before any build):** this file first proposed fixing `sla_due` now as a "mock-only, real bug fix." That was wrong. Checked the actual code before building: the bug lives in real backend Python, `dqagent/api/app.py:424` — `"sla_due": cl["created"].isoformat()` (cluster creation time, not a real SLA computation) — not in the Tower mock layer at all. The correct arithmetic pattern already exists nearby in the same file (`app.py:500`, "F-07 SLA due: the SAME arithmetic as /summary") — so the real fix is a small, well-understood backend change, but it is still a **backend** change, and this pass is Tower/mock-only by the user's own scoping (backend work goes to the separate Claude Code pass). The Tower mock fixtures already carry independent, demo-appropriate `sla_due` values unaffected by this bug, so nothing is broken in what the mock-mode screens would show either way.

**Decision:** full defer, no exception. Nothing built for K10 this pass. The advisory lock and `WebhookSink` behavior (PFP P2-7:1044-1051) remain deferred as originally stated, and `sla_due`'s one-line fix (`app.py:424`, copy the pattern at `:500`) joins them — flagged here so the backend pass picks it up without needing to rediscover it.

---

## DEFERRED — K12 (Final readiness)

**Decision:** full defer. Not process work this pass — step 9b (needs K9's real mechanics, already deferred above), the two rehearsals (downgraded to one, needs the user's machine, separate decision already on `docs/blackboard.md`), the retroactive R5 pass across BACKLOG, and the final receipt all wait for the end of the real backend-build pass, not this mock-only buildout.

---

## BUILT 2026-10-29 — Tower seat, independently re-verified by the orchestrator (3 screenshots viewed directly, not just the report trusted)

**K8 — profile edit drawer.** Wired the pre-existing disabled "Edit" affordance on `tower/src/app/pipelines/[id]/profile/page.tsx` (its own D10 comment already named this as K8's job) to a new modal (`ProfileEditDrawer`, same `.overlay`/`.dialog` pattern `ShortcutSheet` uses — no new drawer component). Proposes through the SAME `propose`-a-CR mechanism `/pipelines/wire` uses for real, producing a CR in the same `CrDetail` shape; review routes to the EXISTING `/governance/[crId]` screen — no new approval UI built. Invented mock route: `POST /catalog/pipelines/{id}/profile/draft` (`tower/src/lib/mock/routes/wire.ts`'s `mockProfileDraft`, reuses `ctx.propose`). Carries the PRE-BACKEND banner. Does not touch E-09's baseline-key rename (BACKLOG:26, still open, gated by ADJ-6) — any baseline field shown is inert/display-only.

**K11a — Evaluations history** (`tower/src/app/evaluations/page.tsx`). **No PRE-BACKEND banner** — deliberate and correct: it calls the real `GET /api/v1/evals` (`dqagent/api/app.py:1572`), the same real endpoint `/agents`' eval-attestation picker already uses. Confirmed real by the orchestrator via direct grep before accepting, not just the build report's claim. Lists `initialEvalRuns()` (`fixtures.ts:1296`) verbatim, no new fields invented.

**K11b — Eval-run diff** (`tower/src/app/evaluations/diff/page.tsx`, new route — does NOT touch or modify the pre-existing, already-real `tower/src/app/runs/diff/page.tsx`, which is a different screen: nightly pipeline-run outcome diff). Invented mock route `GET /eval-runs/diff`. Built against exactly two explicitly-synthetic runs — one newly added, clearly-labeled fixture row, `run-agent_eval-0003-synthetic` (`fixtures.ts:1305`). Follows the same honesty pattern as the real `runs/diff` screen's own file-header comment: "states what differs; it never says why." Carries the PRE-BACKEND banner.

**K11c — Annotation queue** (`tower/src/app/annotations/page.tsx`) + feedback control (`components/ExplanationFeedback.tsx`, added to the unit page, not `Evidence.tsx` since that component's own contract is state-free). Queue reuses real decision-unit and feed-item keys from existing fixtures (confirmed by the orchestrator in the screenshot: real feed-item kinds `UNROUTED_FILE`, `RECOVERY_DATA_LOST`, `837P EMPTY_DELIVERY`, `IE MISSING_DELIVERY`, not invented entities). Feedback control's success response says "Feedback recorded." only — confirmed by the orchestrator via direct code read that it never claims an evaluation candidate was created or any promotion/track-record state changed (`test_feedback_creates_candidate_and_hides_comment`, which this codebase does not have, is explicitly not simulated). Invented mock routes: `GET /annotation-queue`, `POST /explanation-feedback`. Both carry the PRE-BACKEND banner.

**Gates:** `tsc --noEmit` clean, `eslint` clean, `smoke.mjs --mode mock` 199/202 (3 failures are the pre-existing K4 React-Flow/headless-Chromium flake family already on record, F-50/F-51 — not new, not touched by this build).

**API gaps for the backend pass** (none of these have a real route yet):
- `POST /catalog/pipelines/{id}/profile/draft` (`tower/src/lib/api/catalog.ts` `catalogProfileDraft`)
- `GET /eval-runs/diff` (`tower/src/lib/api.ts` `evalRunDiff`)
- `GET /annotation-queue` (`tower/src/lib/api.ts` `annotationQueue`)
- `POST /explanation-feedback` (`tower/src/lib/api/obs.ts` `explanationFeedback`) — real counterpart is `test_feedback_creates_candidate_and_hides_comment`, which doesn't exist anywhere in this codebase yet.

**BACKLOG status: unchanged.** This build closes no BACKLOG row — F-05, F-22 (K11's own exit criteria need an R5 UPHOLD against real backend logic), E-07 (closed earlier by K0, unrelated), E-13, E-09 all remain exactly as they were.

**Installed or changed anything outside the repo tree:** No (per the Tower seat's own disclosure line, and the orchestrator's independent check — one generated artifact, `tower/tsconfig.tsbuildinfo`, was removed by the seat before finishing).

---

## CRITICAL FIX 2026-10-29: gated the entire K8/K11 build behind a preview flag, off by default

**What was wrong.** The build above was correct in isolation but shipped a real exposure risk: `PreBackendBanner`'s "PRE-BACKEND — NOT WIRED TO A REAL SYSTEM" text, and every screen/control carrying it, was reachable in the SAME codebase `run_demo.ps1` runs for the actual live demo. A presenter clicking the profile Edit button, or the Evaluations/Annotation queue nav items, during the real demo would show that red banner to the audience. **User caught this directly** ("that is time of death") — not caught by any gate, any red team, or the orchestrator's own re-verification above, which checked the content was honest but never checked whether it was reachable at the wrong moment.

**Fix (Tower seat, independently re-verified by the orchestrator — code read + 3 screenshots viewed directly, not just the report trusted):**
- New flag `NEXT_PUBLIC_DQ_PREVIEW_K7_K12` (`tower/src/lib/config.ts`, `PREVIEW_K7_K12`), same pattern as the existing `NEXT_PUBLIC_DQ_MOCK`/`MOCK`. **Off by default. `run_demo.ps1`/`stop_demo.ps1` never set it** (confirmed by grep, by both the seat and the orchestrator independently).
- `nav.ts`: `evaluations`/`annotations` `built` field now reads the flag — invisible in the rail when off. **Verified by screenshot**: the Observe group with the flag off shows exactly Monitors/Measures/Coverage/Traces/Guardrails/LLM usage, nothing added.
- Defense in depth, not just a hidden nav link: `evaluations`, `evaluations/diff`, `annotations` pages each self-guard — a direct URL hit with the flag off shows a new, calm `NotInDemo` component ("This screen isn't part of the current demo.", a link back to `/`) — **never** the red banner. **Verified by screenshot** on `/annotations`.
- Profile page's Edit button: `disabled={!PREVIEW_K7_K12}`, onClick no-ops when off, and the drawer's own render is gated (`editOpen && PREVIEW_K7_K12`) so even a direct state-manipulation attempt can't open it. Reverts to exactly D10's original pre-K8 state. **Verified by code read** (`profile/page.tsx:292-297`) and by screenshot (Edit visible, disabled).
- `ExplanationFeedback` returns `null` entirely when the flag is off — fully absent from the unit page, not disabled-and-visible.
- Gates: `tsc`/`eslint` clean; `smoke.mjs --mode mock --preview off` 196/198 (new flag-OFF assertions all pass: hidden nav, calm message with no banner on all 3 routes, disabled Edit, absent feedback control); `smoke.mjs --mode mock --preview on` 200/202 (original K8/K11 preview behavior unchanged) — same 2 pre-existing K4 flakes (F-50/F-51) in both runs, nothing new.

**Lesson for `docs/FRAMEWORK_FEEDBACK.md` (not yet written there as of this entry — add on next pass):** the after-build red team / orchestrator re-verification checked content-honesty (does the banner say the truth) but never checked reachability-at-the-wrong-time (can this truth be shown to the wrong audience at the wrong moment). These are different failure modes and need a separate explicit check whenever a build adds anything to a codebase that doubles as the live-demo artifact.
