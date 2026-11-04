# K0 design — finish evidence-gated promotion and the eval recorder

**Architect (B1).** Re-grounded against the live tree. Code hash cited throughout:
`3f31ece8d5b1e660d13c01b731fa57ba59cb36e3af8495334ae364fd0a692c78` (confirmed current per blackboard
"2026-10-24 ORCHESTRATOR: SYNC update", `docs/blackboard.md:1654-1657`). This design does not resolve PFP/plan
ambiguities; those are listed under "Decisions needed" for the orchestrator/user.

No shell was used; every claim below is a Read/Grep of the file at the cited line, done directly by this architect
seat, not inferred from the plan's prose.

---

## 1. Re-grounding the plan's "State inherited" bullets (EXECUTION_PLAN.md:216-228) against the current tree

The plan's K0 section was written against tree `8c2934ff…`, before G0 ran. G0 touched `dqagent/obs/tracer.py`
(portability fix only) and added `tools/gate.py`, `.gitattributes`, two test files, and a one-line fix to
`tests/t0/test_generic_naming.py`. None of those are K0 files. I re-verified every bullet directly rather than
assuming G0 left them alone.

| Plan claim | Verdict | Evidence (`file:line`, code hash `3f31ece8…`) |
|---|---|---|
| `cp/trace_schema.py` `evidence_ddl()`: `eval_runs`, `eval_run_record`, 5-arg `eval_attest`, views `evidence_complete`/`track_record_by_version`/`track_record`, `promotion_minimums`/`promotion_refusal` | **HOLDS** | `dqagent/cp/trace_schema.py:277-564` (`evidence_ddl()`). `eval_runs` table `:294-300`; `eval_run_record` `:301-311`; `eval_attest(text,text,text,text,text)` (5 args) `:320-353`; `evidence_complete` view `:361-367`; `track_record_by_version` view `:410-411`; `track_record` view `:415-459`; `promotion_minimums(text)` function `:462-465`; `promotion_refusal(text,jsonb)` function `:484-541`. |
| `governance_schema.py`: old definitions removed; refusals in `gov_decide`/`gov_apply`; POLICY rule raising needs one approver, lowering needs two | **HOLDS** | `gov_decide` calls `dq_ctl.promotion_refusal` at approval time: `dqagent/cp/governance_schema.py:184-187`. `gov_apply` re-checks at apply time: `:237-239`. The lower-only/raise-only rule for `promotion_minimums` lives in `gov_item_lower_only`'s POLICY branch, `:632-638`, which delegates to `minimums_lower_only` `:598-618` (stricter-only semantics: at-least counts must stay/rise, at-most bounds must stay/fall). `gov_propose` sets `approvals_required` to 1 when `gov_items_lower_only` is true, 2 otherwise: `:135-150`. |
| `registry.py` | **HOLDS, narrowly** | `track_record_snapshot` reads `dq_ctl.track_record`, `dq_ctl.promotion_minimums`, `dq_ctl.evidence_status`: `dqagent/governance/registry.py:566-582`. It is called from `propose()` at `:546-548`. The plan's bullet is accurate for what exists; it does **not** mean registry.py exposes anything beyond the snapshot used in change-request impact — see §2 blast radius for what registry.py does *not* yet expose (nothing new; this is unchanged from the plan). |
| `app.py`: `/autonomy` versions and the attestation contract | **PARTIAL — the plan overstates this.** `/autonomy` (`dqagent/api/app.py:1486-1534`) does expose `track_record.versions[]` per (agent, action_class, authority) via `flat_track_record` (`:1001-1022`, `tr["versions"] = versions.get(k, [])` at `:1511`). It does **not** expose `evidence` or `minimums` as named top-level fields anywhere in the `/autonomy` response — I grepped the whole file for `"evidence"` / `"minimums"` literal keys and found zero matches outside `track_record_snapshot`'s internal dict, which is only returned inside a change-request's `impact` block (`propose()`), never from `/autonomy`. The plan's step 4 "confirm that `/autonomy` exposes `evidence` and `minimums` for the Tower" is phrased as a confirmation task, but the correct finding is: **it does not, today; this is new work, not a confirmation.** See blast-radius item B7. |
| `config/state/ZZ/policy_w1.yaml`: demo minimums | **HOLDS** | `promotion_minimums:` block, label `demo minimums`, L2/L3/L4 values exactly as PFP :1324 describes: `config/state/ZZ/policy_w1.yaml:38-42`. |
| Rewritten, unverified: `test_parte_cp`, `test_parte_redteam_fixes`, `test_parte_earn`, `test_parte_api` | **Content-verified consistent with R4-5 semantics; execution-unverified (no Postgres on this machine — same G0-D2 limitation, not new).** See §1a below for what each file actually asserts, read directly. |
| `test_agent_eval` not updated; its `promote()` path will be refused | **STALE — this is now WRONG, not merely unverified.** `tests/t1/test_agent_eval.py:138-149`'s `promote()` method calls `self.s.registry.propose(...)` then has checker/checker2 approve via `self.s.registry.decide(...)`. It does **not** create any evidence bundle (no call to `eval_run_record`, `eval_attest`, or the `tests/t1/evidence.py` helpers) before promoting. Given `gov_decide`'s refusal at `governance_schema.py:184-187`, this scenario (`S6-EARN-AUTONOMY`, `tests/agent_eval/scenarios.yaml:181-211`) **will now raise `Denied`/`CheckViolation` at the first `promote: {tier: L3}` step** (scenario line 196) unless Postgres's `promotion_minimums` happen to already be satisfied by a pre-existing confirmed bundle for the running code version — which nothing in the scenario or the harness establishes. I could not execute this (no Postgres on this Windows machine, same limitation as G0-D1/G0-D2), so I flag this as **analysis, not an executed failure**, and it needs a second independent check once Postgres is available. This is squarely "not done" per the plan's own bullet — the plan correctly flagged it as not-yet-fixed; what's stale is only the framing that this is a known, already-scoped gap rather than something the architect should re-confirm empirically before build. |
| No product writer for `eval_runs` | **HOLDS.** The only caller of `eval_run_record` anywhere in the repo is the test helper `tests/t1/evidence.py:15-23` (`record_run`). I grepped `dqagent/**/*.py` for `eval_run_record` and found zero matches outside `trace_schema.py`'s own definition and the test helper. There is no CLI subcommand, no `tests/plugins/` directory (confirmed absent, see §3), and `dqagent/cli/main.py` (`:171-199`) has exactly two subcommands, `harness` and `demo` — no `eval`. |
| No Tower attestation form/types update | **PARTIAL — the form exists but is built against the OLD (pre-R4-5) API contract, not updated.** `tower/src/app/agents/page.tsx:400-479`'s `Attestations()` component posts `{label, code_sha256, tests, failures, junit_sha256}` (`:403,438`) — the **6-argument legacy shape** that `test_old_6arg_eval_attest_signature_gone` (`tests/t1/test_evidence_promotion.py:246-256`) proves the database function no longer accepts. `tower/src/lib/api.ts:260-262`'s `evalAttest` and `tower/src/lib/types.ts:829-840`'s `EvalAttestation` interface carry the same stale shape (`tests`, `failures`, `junit_sha256` as direct inputs, no `det_run_id`/`llm_run_id`). The mock (`tower/src/lib/mock/server.ts:1001-1032`) also implements the old 6-arg contract **and** the old "an attestation resets the record" semantics explicitly in comments (`:1020 "A confirmed attestation resets every track record"`) — this is now actively wrong per DEC-024/R4-5 §5 ("An attestation resets the window" is removed). This is a real, concrete blast-radius item (B8), not merely "not done" — it is built-and-wrong, and will mislead anyone demoing against the mock today. |

### 1a. What the four "rewritten" test files actually assert (read directly, not inferred)

- **`tests/t1/test_parte_cp.py`**: `test_track_record_counts_releases_and_never_promotes` (`:197-241`) asserts the
  R4-5 continuous-record semantics directly: `tr["AUTH_MEDICAID"]["current_version"]["autonomous"]` present
  (`:210`), clean releases never auto-promote (`:211-213`), one-person propose then two-person-refusal-to-self-confirm
  (`:220-229`), and that confirming is evidence that "nothing resets" (`:234`, comment "R4-5: nothing resets").
  `test_every_new_part_e_table_is_append_only` (`:358-384`) uses `attest(s, record_run(s), label="suite")` — the
  NEW 5-argument-shaped helper, confirming this file is written against the current (not legacy) `eval_attest`.
  **Consistent with R4-5.**

---

## 6. Decisions — RESOLVED by the orchestrator (2026-10-24, after independent verification of D4 and D7 against the code)

Orchestrator independently re-confirmed two load-bearing claims before resolving anything: (D4) `trace_schema.py:544-550` does carry the append-only trigger + `REVOKE ALL ... FROM PUBLIC` block for `eval_runs`, exactly as the architect found — confirmed real. (D7) `tests/t1/test_agent_eval.py`'s `promote()` method (`:138-149`) and the whole file contain zero references to `eval_run_record`, `eval_attest`, `eval-attestations`, or `det_run_id` (confirmed by direct grep) — the architect's highest-leverage flag is real, not speculative, though it remains execution-unverified pending Postgres.

1. **D1 (recorder module location).** RESOLVED: `dqagent/eval/recorder.py`, not `tests/plugins/eval_recorder.py`. basis: the plan's own framing says "the recorder becomes the product path" (EXECUTION_PLAN.md K0 step 1) — a privileged CLI-invoked DB writer belongs under the product package, not `tests/plugins/`, which every other seat file and PFP's own package layout (`dqagent/{model,adapters,...}`) reserves for product code. wrong-if: some other test-harness convention specifically expects `tests/plugins/` for CLI-registered pytest plugins (not the case here — this is a `dqagent eval run` CLI subcommand, not a pytest plugin). NOT-verified: whether any existing import path assumes the PFP-literal location; Backend-Engine must grep for `eval_recorder` before creating the file and report if anything else expects the old path.
2. **D2 (route ownership collision: `GET /evals` / `GET /eval-attestations?status=PROPOSED`).** RESOLVED: `app.py` (Backend-API-Gov), not `obs_routes.py`. basis: K0's own existing POST/confirm attestation endpoints already live in `app.py` (confirmed by the architect's own read of `app.py:1486-1534` and the existing attestation contract) — splitting GET from POST/confirm across two files for the same resource is the worse default. PFP P2-1's eventual full ownership by BB is a LATER package's concern (K11, "the rest of the explorer" per EXECUTION_PLAN.md); K0 stays consistent with its own existing pattern. wrong-if: K11 finds this creates a bigger migration than keeping it in `obs_routes.py` now — acceptable, revisit then, not now.
3. **D3 (K0 has no PFP §4 ownership row at all).** RESOLVED: confirmed and accepted as a standing fact for every K-package, not just K0 — EXECUTION_PLAN.md:62 already says this explicitly ("per-package ownership follows PFP §4... plus any re-assignments recorded on the blackboard" for packages PFP defines; K0-K12 are this plan's OWN packages, layered on top of and sequenced differently from PFP's P-numbered packages per EXECUTION_PLAN.md:6). Recording once, here, so it isn't re-litigated every package: K-series packages use EXECUTION_PLAN.md §3's seat-file defaults as the baseline, with PFP cited only where a specific P-numbered PFP section is in scope for that K-package (e.g. K0 cites PFP R4-5/R4-7/P2-1 for DESIGN content, not for FILE OWNERSHIP).
4. **D4 (append-only enforcement on `eval_runs`).** CONFIRMED by the orchestrator directly (see above) — not a gap. No action needed.
5. **D5 (no seat owns `tests/t1/evidence.py`).** RESOLVED: Backend-LLM-Obs, bundled with its existing step-3 ownership of `test_parte_cp`/`test_parte_redteam_fixes`/`test_parte_earn`/`test_parte_api` (all of which depend on this helper's exact signatures, per the architect's own finding). basis: one seat should own the helper and its direct consumers together to avoid a cross-seat signature-drift risk mid-package.
6. **D6 (who fixes `test_agent_eval`'s missing evidence-bundle creation before promote).** RESOLVED: Backend-LLM-Obs, per the plan's own literal text (EXECUTION_PLAN.md K0 step 3: "fix `test_agent_eval`: the promotion step creates a deterministic evidence bundle through the step-1 recorder") — this reads as a TEST fix (calling the already-built step-1 recorder from within the test harness's `promote()` method), not a runtime/engine change, so it stays with the seat already fixing the other four test files in the same step, not with Backend-Engine's earn-story work in step 2. wrong-if: the fix turns out to require a `runtime.py`-level change (e.g. the simulator's own earn-story path, not just this specific test harness) — if Backend-LLM-Obs finds that, per its own seat rules it must stop and report rather than edit a file it doesn't own.
7. **D7 (is `S6-EARN-AUTONOMY` actually broken today?).** RESOLVED as the explicit FIRST build step, ahead of everything else in K0, exactly as the architect recommended. Backend-Engine's step-1 work (the recorder) and Backend-LLM-Obs's step-3 fix to `test_agent_eval` are sequenced so that `test_agent_eval` is run (to the extent possible without Postgres — it will skip, not fail, which is itself informative per G0's `suite_complete` discipline) immediately after the recorder lands, BEFORE any Tower/API work proceeds, so this question is answered with real evidence as early as the no-Postgres environment allows. If/when the user installs PostgreSQL (BACKLOG G0-D2), this check must be re-run as the first thing against a live CP, not assumed fixed by the other changes landing.

**Scope note:** none of these 7 decisions change K0's scope, contradict PFP, or contradict a DEC — all are either file-ownership/location calls within the orchestrator's mandate, or sequencing calls that make an existing plan step (step 3's `test_agent_eval` fix) run first instead of third, for a stated risk-reduction reason. No WAIT triggered.
- **`tests/t1/test_parte_redteam_fixes.py`**: `test_eval_attestation_needs_a_second_person_and_never_resets_the_record`
  (`:62-94`) explicitly posts the OLD contract (`{label, code_sha256, tests, failures, junit_sha256}`) and asserts it
  is **refused with 422** (`:76-77`, "old contract"), then posts the correct new contract
  (`{label, code_sha256, det_run_id}`, `:78`) and asserts it succeeds. This file is a **positive regression test
  for exactly the Tower-side staleness found in B8** — it proves the backend correctly rejects what the Tower
  currently sends. **Consistent with R4-5; also directly evidences the Tower gap.**
- **`tests/t1/test_parte_earn.py`**: `test_w2_earn_story` (`:71-152`, truncated read) calls `grant_evidence(s)`
  (`:96`) before `_promote(s)` (`:97`), and asserts `tr["evidence"]["complete"]` and
  `tr["minimums"]["label"] == "demo minimums"` (`:101`) and that the human-released count meets the L3 minimum
  (`:102-103`). This is the Part-E-successor earn story, fully updated to R4-5. **Consistent with R4-5.** This is a
  **different file from `tests/agent_eval` `S6-EARN-AUTONOMY`** (the latter is `test_agent_eval.py`'s `Run.promote()`,
  unrelated to this file despite similar names) — the plan's bullet about `test_parte_earn.py` needing to "still
  pass" is about THIS file, not `test_agent_eval.py`. Do not conflate the two when briefing Backend-LLM-Obs.
- **`tests/t1/test_parte_api.py`**: `test_single_approver_leaves_a_promotion_awaiting_and_it_never_applies` (`:81-100`)
  calls `allow_promotions(s)` (`:83`, from `tests/t1/evidence.py:59-64`) first — the test's *subject* is the
  two-person rule, not evidence, so it deliberately grants evidence/zero-minimums up front via the product's own
  governed paths. **Consistent with R4-5.**

All four files import from `.evidence` (the rewritten helper at `tests/t1/evidence.py`), confirming they were
updated together as a set, consistent with the plan's framing. None of the four import or reference the legacy
6-argument `eval_attest` signature as something they expect to succeed — only `test_parte_redteam_fixes.py` references
it, and only to assert it is refused. **I cannot execute any of this (Postgres unreachable on this Windows machine,
per CLAUDE.md environment note and BACKLOG G0-D2/G0-D1); this is a content read, not a passing-test confirmation. A
second independent check (ideally an actual run once Postgres is available) is still owed before closing K0.**

---

## 2. Blast radius

Every object K0 changes, its dependents (`file:line`), and the test that covers each. Count: **23 distinct blast-radius
items** (B1–B23), beyond the plan's own list at EXECUTION_PLAN.md:255-262 (which named 7: `tier_of`,
`track_record_snapshot`, `test_parte_*`, `test_sim_nightly`, I-3 digests, the Tower Agents screen and its mock — all
re-verified below as B1-B6, B22-B23). I re-grepped rather than trusting the plan's list, per PFP/plan instruction.

| # | Object K0 touches | Dependent(s) (`file:line`) | Test coverage |
|---|---|---|---|
| B1 | `tier_of()` | `dqagent/api/app.py:257-263` (reads `dq_ctl.autonomy_tier`, not `track_record` — **no actual dependency on anything K0 changes**; the plan's listing of it as blast radius is defensive, not load-bearing) | none needed; confirmed unaffected by direct read |
| B2 | `track_record_snapshot()` | `dqagent/governance/registry.py:566-582`, called from `propose()` at `:546-548`, consumed by `app.py`'s CR impact formatting (`flat_track_record`, `:1001-1022`) and by `tower/src/app/governance/[crId]/page.tsx` and `tower/src/app/agents/page.tsx` (both grep-confirmed at top of this doc's investigation) | `test_parte_cp.py`, `test_parte_api.py`, `test_parte_earn.py` (all exercise `propose()`'s impact block) |
| B3 | `tests/t1/test_parte_cp.py` | depends on `evidence.py` helpers | content-verified §1a |
| B4 | `tests/t1/test_parte_redteam_fixes.py` | depends on `evidence.py` helpers; depends on `/eval-attestations` contract in `app.py:1536-1556` | content-verified §1a; **also the regression anchor for B8** |
| B5 | `tests/t1/test_parte_earn.py` | depends on `evidence.py` helpers, `synthetic/out/w2_earn/earn_manifest.json` | content-verified §1a |
| B6 | I-3 agent-eval digests | `tests/t1/test_agent_eval.py::digest()` (`:227-228`) hashes `observe()`'s output; `observe()` (`:194-224`) reads `results`, `units`, `unit_events`, `measure_results`, `agent_blackboard`, `agent_memory` — **confirmed by direct read: it does NOT query `track_record`, `eval_runs`, `eval_attestations`, or any R4-5 table.** PFP R4-5 §5 (:1333) claim "observe() does not read track_record" **HOLDS** by direct grep, not just by the plan's assertion. | `test_agent_scenario_matches_recipe_and_is_deterministic` (`:317-330`) |
| B7 | `/api/v1/autonomy` response shape | `app.py:1486-1534` — **new work needed**: no `evidence`/`minimums` fields today. Tower `agents/page.tsx` does not currently read such fields either (not grep-confirmed as a consumer yet, because they don't exist) | none yet — new test needed if this is built in K0 |
| B8 | Tower `Attestations()` component, `api.ts` `evalAttest`/`evalConfirm`, `types.ts` `EvalAttestation`, mock `server.ts` eval-attestation handlers | all four built against the stale 6-arg contract and stale "resets the record" semantics (`tower/src/app/agents/page.tsx:400-479`, `tower/src/lib/api.ts:260-262`, `tower/src/lib/types.ts:829-840`, `tower/src/lib/mock/server.ts:1001-1032`) | **none** — nothing in the Tower's own test/smoke suite currently exercises eval-attestation POST against the live API (F-08's smoke predates R4-5); this is a live-smoke gap, flagged, not closed, by this design |
| B9 | `GET /evals` (new route, does not exist) | would be consumed by the new Tower attestation form (plan step 5) and by a future `GET /eval-attestations?status=PROPOSED` picker | new; no test exists |
| B10 | `GET /eval-attestations?status=PROPOSED` (new route, does not exist; closes E-13) | consumed by the Tower pending-attestations list (plan step 5); E-13's BACKLOG row (`docs/BACKLOG.md:16`) names exactly this absence | new; `tests/t1/test_obs_api.py::test_pending_attestations_listed` is listed in PFP P2-1 (:962) for a LATER package (K11), not K0 — **flagged as a plan-vs-PFP sequencing question, see Decisions needed D1** |
| B11 | `dqagent eval run --suite agent_eval\|explainer` CLI subcommand (does not exist) | `dqagent/cli/main.py:171-199` has no `eval` subcommand; adding it does not collide with `harness`/`demo` | new |
| B12 | `tests/plugins/eval_recorder.py` (does not exist; confirmed by Glob, zero results) | see §3 reserved-names discussion — **this is the one item flagged back to the orchestrator as a location decision, not resolved here** | new |
| B13 | `tests/t1/evidence.py` (the test helper) | **plan says it "is changed to call" the new recorder.** Currently it calls `dq_ctl.eval_run_record` directly via `s.cp.as_role("dq_eval_svc")` (`tests/t1/evidence.py:15-23`). If the recorder becomes the product path, this helper's `record_run()` should delegate to it instead of calling the DB function directly — but every one of the 9 R4-5 tests plus the 4 rewritten files plus `test_parte_earn.py` imports and depends on `record_run`'s exact signature (`suite=, source_mode=, cases=, passed=, code=, prompt_version=, run_id=`). Any seat touching this file must preserve that signature exactly, or re-verify all callers. | all of B3-B5, `test_evidence_promotion.py` (9 tests), `test_parte_cp.py` |
| B14 | `tests/t1/test_agent_eval.py::Run.promote()` | **as found in §1, this will raise once `gov_decide`'s refusal fires** — `promote()` (`:138-149`) has no evidence-bundle creation step. The scenario `S6-EARN-AUTONOMY` (`scenarios.yaml:181-211`) exercises it. | `test_agent_scenario_matches_recipe_and_is_deterministic[S6-EARN-AUTONOMY]` — **this test's current status is UNKNOWN to me (cannot execute); treat as a build-blocking finding, not a closed one** |
| B15 | `test_sim_nightly.py` | grep for `earn_story`/`promote`/`AUTH_MEDICAID` in this file returned **zero matches** — contrary to what one might assume from the plan's blast-radius list naming it. The earn-story logic the plan is actually worried about lives in `dqagent/runtime.py`'s `earn_story_seed()` (`:1728-1736`) and `sim_preload()` (`:1738-1743`), and the CLI's `--earn-story` flag (`cli/main.py:92-93,138-139`). **The plan's blast-radius list naming `test_sim_nightly` appears to be stale or refers to a test file that does not exist under that name with that content; re-verify before assuming it needs changes.** |
| B16 | `--earn-story` CLI path | `runtime.py:1728-1736` demotes `AUTH_MEDICAID` to L2 via the ordinary lower-only path; it does **not** itself attempt any promotion or evidence creation — the promotion is a human action taken live during the demo (plan step 10/demo moment). No evidence gap here; confirmed by direct read. |
| B17 | `tests/agent_eval/scenarios.yaml` | `S6-EARN-AUTONOMY` (`:181-211`) is the recipe that drives B14; its `expect.log` (`:206-210`) hard-codes `[promote, 2, [AWAITING_APPROVAL, APPLIED], L3]` twice — if `promote()` is fixed to create evidence first, this expected log shape is unaffected (the log only records the promotion call's own output), but the **scenario will need either a zero-minimums setup or enough prior human-approved releases**, and the digest (B6) will change if any new DB rows are written during the scenario that `observe()` happens to pick up indirectically (it does not read `track_record` directly, but it DOES read `agent_blackboard`, and if evidence-bundle creation posts any blackboard entries, the digest changes). **This needs direct verification once Postgres is available — flagged, not resolved.** |
| B18 | `GUARDRAIL_CODES`, `ATTR_VOCAB`, span names | K0 adds no new guardrail codes or span attribute values by the plan's own text (no new transport, no new source_mode value — `EVAL_SOURCE_MODES` in `governance_schema.py:259` already has DETERMINISTIC/LIVE/SCRIPTED/RECORDED/TEMPLATE, unchanged). **Confirmed: K0 needs no `ATTR_VOCAB` edits.** | n/a |
| B19 | `dqagent/cp/governance_schema.py` POLICY validation (`validate_policy`) | `dqagent/governance/registry.py:514-516` calls `validate_policy(new_cfg.get("policy") or {})` inside `propose()` when any POLICY item is present — this is the path `test_raising_minimums_one_person_lowering_two` exercises indirectly via `set_minimums()`. Unaffected by K0's new work (CLI/API/Tower), but any seat editing `promotion_minimums` validation must go through this path, not a new one. | `test_raising_minimums_one_person_lowering_two` (`tests/t1/test_evidence_promotion.py:218-243`) |
| B20 | `dqagent/api/app.py` import of `services.cp.as_role("dq_eval_svc")` for a new `/evals` route | no existing route in `app.py` uses the `dq_eval_svc` role for reads (`eval_run_record` requires `session_user = 'dq_eval_svc'` at write time per `governance_schema.py:305-307`, but reads of `eval_runs` are granted to `dq_api` too: `GRANT SELECT ON dq_ctl.eval_runs TO dq_api, ...` at `trace_schema.py:552`) — so `GET /evals` can read as `dq_api`, no new grant needed. Confirmed by direct read of the grant list. | new |
| B21 | Tower `scripts/smoke.mjs` | not grep-confirmed to touch eval-attestations at all (F-08's original 81/81 mock smoke predates R4-5) — if K0 updates the Tower attestation form, smoke coverage for it is new, not a regression risk against existing shots | new, or explicitly deferred |
| B22 | `tests/t1/test_sim_nightly.py` | see B15 — **plan's claim re-verified as likely stale**; flagged, not fixed | n/a pending re-verification |
| B23 | Tower Agents screen's mock (`tower/src/lib/mock/fixtures.ts:1202`) | the one `eval_attestation` mock fixture uses the OLD shape (`label, code_sha256, tests, failures, at, proposed_by, confirmed_by` — no `det_run_id`/`llm_run_id`/`halves`) | consistent with B8; same fix |

**Not in the plan's list but found by re-grep, with no action needed:** `dqagent/llm/__init__.py` and
`dqagent/obs/tracer.py` both reference `PROMPT_VERSION`/`code_sha256` consumption paths that R4-5 already wired
through (`_prompt_setting()` in `registry.py:112-116`); no further change needed there for K0.

---

## 3. Reserved names

Parallel seats must claim names from this list, not invent new ones.

### 3.1 CLI
- Subcommand: `dqagent eval run --suite {agent_eval,explainer}` (matches `EVAL_SUITES` in
  `governance_schema.py:258`, exactly — do not introduce a third suite name without a POLICY/schema change).
- No existing CLI subparser named `eval`; adding `sub.add_parser("eval")` in `cli/main.py` after line 177's `demo`
  parser is the natural slot, with its own `sub2 = e.add_subparsers(dest="eval_cmd")` for `run` (room for `list`
  later, K11).

### 3.2 Recorder module location — flagged, not decided
PFP (plan :950, line 950 of `PARTF_IMPLEMENTATION_PLAN.md`) names `tests/plugins/eval_recorder.py`. I confirmed this
path **does not exist today** (`Glob tests/plugins/*` → no files found). The plan's own framing for K0 (brief's
CONTEXT MANIFEST) calls this "the product path" — but `tests/plugins/` reads as a test-only location to any future
maintainer, and `pyproject.toml` has no `pytest11`/plugin entry-point block that would make "plugins" a meaningful
pytest-plugin directory (I checked; no matches). Putting product code that the CLI (`dqagent eval run`) imports and
that writes to `eval_runs` via the only-`dq_eval_svc`-can-write path (`governance_schema.py:305-307`) under
`tests/plugins/` would mean the product's control-plane writer lives inside the `tests/` tree, which is unusual and
risks being excluded from `code_sha256`'s hashed scope if `tests/` is ever narrowed, or — more immediately — risks
being misread as test-only scaffolding by a reviewer skimming file paths. **I flag two alternatives for the
orchestrator/user, and do not pick one:**
- **Option A (follow PFP literally):** `tests/plugins/eval_recorder.py`, imported by both `dqagent/cli/main.py` and
  by `pytest` fixtures/tests that want to invoke a recorded run. Pro: zero deviation from the binding plan text.
  Con: product code (a CLI-invoked, privilege-bearing DB writer) lives under `tests/`.
- **Option B:** `dqagent/eval/recorder.py` (new package), with `tests/plugins/eval_recorder.py` reduced to a thin
  re-export for any pytest-side callers, if those exist. Pro: product code lives under `dqagent/`, consistent with
  every other seam (`llm/`, `obs/`, `governance/`). Con: deviates from PFP's literal path, which CLAUDE.md says is
  binding on substance but the *plan* wins on *order* only — path naming is arguably neither, so this needs a
  decision, not an architect guess.

This is listed under "Decisions needed" (D1) and is NOT resolved here.

### 3.3 API routes (new)
- `GET /api/v1/evals` — list `eval_runs` (filterable; shape TBD by the owning seat, following the `RunList`/
  `GuardrailList` pagination pattern already established in `obs_routes.py:1-18`'s docstring convention: cursor +
  limit, `MAX_LIMIT = 200` reused).
- `GET /api/v1/eval-attestations?status=PROPOSED` — closes E-13 (`BACKLOG.md:16`). Must reuse the existing
  `eval_attestations_confirmed` view's sibling (unconfirmed rows come straight from `eval_attestations` filtered by
  absence in `eval_attestation_confirmations` — the table referenced at `governance_schema.py` region near
  `eval_attest_confirm`, not re-derived here since this architect did not open that function's body; the owning
  seat must locate it before building).
- Both routes live in `dqagent/api/app.py` per PFP §4's P1-1 ownership column (`BC only` for `api/app.py`,
  `PARTF_IMPLEMENTATION_PLAN.md:1082`), **not** `obs_routes.py` — see Decisions needed D2, because the existing
  `/eval-attestations` POST/confirm routes are already in `app.py` (confirmed at `:1536,1558`), so symmetry argues
  for keeping the new GETs there too, but PFP P2-1 (:951-953) lists `/evals` among the **obs_routes.py-style**
  endpoints owned by BB in that later package. K0 is building a subset of P2-1 early (per EXECUTION_PLAN's own
  admission that K0 "closes... part of F-05" and folds in E-13 from P2-1). **This is a real file-ownership
  collision between BC (owns `app.py`) and BB (owns `obs_routes.py`/P2-1), not resolved here.**

### 3.4 Mock routes
- No existing `tower/src/lib/mock/routes/evals.ts` or similar — only `obs.ts` exists (Glob confirmed). The new
  `/evals` and `/eval-attestations?status=PROPOSED` mocks should extend `tower/src/lib/mock/server.ts`'s existing
  inline handler style (consistent with how `/eval-attestations` POST/confirm are already implemented in-line at
  `server.ts:1001-1032`), **not** a new routes file, unless the owning seat judges the file is getting too large —
  flagged as a style choice, not a name reservation, since PFP §4's P1-1 row for `lib/mock/routes/*.ts` shows one
  file per package with no K0-specific entry (K0 isn't in the P1-1/P1-2/P1-3 table at all — see Decisions needed D3).

### 3.5 Test ids (new, reserve exactly these; do not rename)
- `tests/t1/test_evidence_promotion.py` — **already exists with all 9 PFP-named tests**, confirmed verbatim against
  PFP §6 (:1336-1344): `test_promotion_refused_without_evidence_bundle`, `test_promotion_refused_below_minimum_record`,
  `test_demo_deterministic_bundle_suffices`, `test_production_needs_det_and_live_llm`,
  `test_scripted_or_template_llm_run_rejected_by_eval_attest`, `test_track_record_never_resets_and_segments_by_version`,
  `test_demotion_needs_no_evidence`, `test_raising_minimums_one_person_lowering_two`,
  `test_old_6arg_eval_attest_signature_gone`. **No new names needed here — these 9 are done, content-verified,
  execution-unverified.**
- New for K0's remaining scope: `test_cli_eval_run_writes_eval_runs_row` (or similar; CLI-level, step 1 of the
  plan); `test_scripted_explainer_run_refused_by_eval_attest_via_cli` (the CLI-level sibling of
  `test_scripted_or_template_llm_run_rejected_by_eval_attest`, since that test attacks the SQL function directly —
  plan step 1's second bullet explicitly wants a CLI-level negative control, which is a DIFFERENT test from the
  existing SQL-level one, not a duplicate); `test_eval_runs_append_only_and_role_restricted` (I9, red-team evidence
  integrity: `eval_runs` already has `REVOKE ALL ... FROM PUBLIC` and the no-mutation trigger pattern is used
  elsewhere (`trace_schema.py:166-178`'s `DO $$ ... reject_mutation()` loop) but I did **not** find an equivalent
  trigger applied to `eval_runs` specifically in `evidence_ddl()` — re-check this; it may be a genuine gap, not
  just an untested one. See Decisions needed D4.); `test_get_evals_endpoint_shape`;
  `test_pending_attestations_endpoint_lists_proposed_only` (E-13's actual closing test, distinct from the one PFP
  lists under P2-1 at :962 — see D1 on sequencing).

---

## 4. Golden-path worked example (plan's demo moment, R4-7, walked against the ACTUAL current schema/API)

Plan text (EXECUTION_PLAN.md:264-268 / PARTF_IMPLEMENTATION_PLAN.md:1352-1353):

1. **"Run `dqagent eval run --suite agent_eval`."**
   **Does not work today.** `cli/main.py:171-199` has no `eval` subcommand (confirmed, B11). This step is pure
   future-tense until step 1 of the plan's own build order lands.
2. **"Propose the attestation; a second person confirms it."**
   **Partially works today**, but only via the OLD/soon-to-be-replaced contract if done through the Tower
   (B8), or correctly via direct API call if the caller knows the new 5-field shape
   (`{label, code_sha256, det_run_id, llm_run_id?}`, confirmed live at `app.py:1536-1556`). **The `GET
   /eval-attestations?status=PROPOSED` pending list the demo script implies ("propose... confirm") does not exist**
   (B10) — today the confirmer must already know the attestation id (this is exactly what E-13's BACKLOG row
   says, `BACKLOG.md:16`, and what the Tower's own code comment admits: `agents/page.tsx:397-398`, "The API lists
   no pending attestations, so the confirmer enters the id the proposer reports").
3. **"The promotion change request shows 'evidence ✓ (deterministic, demo)' and 'record 5/5 ✓'."**
   **The underlying mechanics work today** — `propose()`'s `track_record_snapshot()` (`registry.py:566-582`)
   returns `evidence` (from `dq_ctl.evidence_status()`) and `minimums` (from `dq_ctl.promotion_minimums()`) inside
   the CR's `impact` block, and `test_demo_deterministic_bundle_suffices` (`test_evidence_promotion.py:100-110`)
   proves exactly this shape (`ev["complete"]`, `cr["impact"]["track_record"]["minimums"]["label"] == "demo
   minimums"`). **But the literal display string "evidence ✓ (deterministic, demo)" is not found anywhere in
   `app.py` or the Tower** — I grepped for it and found no match. This is a **presentation-layer gap**: the data
   exists, the formatted string the demo script promises does not. The error-message format strings DO exist
   (`promotion_refusal`'s `RETURN format(...)` calls, `governance_schema.py:497-501,517-536` produce
   "deterministic ✓ / live LLM ✗" style text) — so the *refusal* message format exists, but the *success* display
   string the demo wants to show on an approved/pending CR does not yet have an equivalent on the Tower side.
4. **"Before the bundle exists, the database refuses the promotion. Show that refusal live."**
   **Works today, fully, and is well-tested.** `test_promotion_refused_without_evidence_bundle`
   (`test_evidence_promotion.py:63-78`) is exactly this scenario end to end, including the exact refusal text
   match (`r"promotion requires complete evidence for code [0-9a-f]{12}: deterministic ✗"`, `:70`). This is the
   one step of the four I am most confident already works, modulo actually running it against live Postgres
   (still unverified on this machine, same G0-D2 limitation).

**Honest summary for the orchestrator:** step 4 is solid; step 3's data layer is solid but its *display* layer is
not; steps 1 and 2 need real build work (CLI subcommand; pending-attestations list). This matches the plan's own
"Not done" bullets almost exactly — the one correction is that step 3's gap is narrower (display string only) than
it might appear, and step 2's gap is more concrete (E-13, with a code comment admitting the exact absence) than the
plan's phrasing suggests.

---

## 5. New third-party dependency check

**Confirmed: none needed.** K0 adds a CLI subcommand, two API routes, DB objects that already exist, and Tower
form/type updates — all using libraries already in `pyproject.toml` and `tower/package.json` (FastAPI, psycopg,
Next.js/React, already present per every file read above). No technology-selection record is required for K0.
This mirrors G0's own "no new dependency" finding (`docs/blackboard.md:1536`) and PFP's own text, which names no
new package for R4-5/P2-1's eval-recorder scope. **Confirmed by inspection, not merely assumed.**

---

## 6. Per-seat context manifests for K0's six steps

Ownership follows PFP §4 (:1059-1119) **as the binding rule per CLAUDE.md and EXECUTION_PLAN.md §3**, with gaps
flagged rather than filled, per the lesson of the 2026-10-24 R4-5 mis-brief (`docs/blackboard.md:1492-1499`) — that
mis-brief happened precisely because an orchestrator description of ownership overrode PFP's actual table. I re-
located the correction's claims below (§6.0) before building the seat manifests, since the brief specifically asked
for this.

### 6.0 Re-verification of the 2026-10-24 R4-5 brief correction's claims

| Correction's claim | Still true? | Evidence |
|---|---|---|
| Owner is Backend-B (BB), in `trace_ddl`, after `eval_runs` | **TRUE** | `trace_schema.py:251` ends `trace_ddl()` with `""" + evidence_ddl()`, i.e. `evidence_ddl()`'s content (which defines `eval_runs` first, then everything else) is appended directly after the main `trace_ddl()` body — confirmed by direct read, "after `eval_runs`" holds structurally since `eval_runs` is the first table `evidence_ddl()` creates (`:294`). PFP §4's P1-1 row for `cp/trace_schema.py` names BA (`PARTF_IMPLEMENTATION_PLAN.md:1078`), **not BB** — this is for P1-1. K0 is not one of the lettered P1-x/P2-x package columns in that table at all (confirmed: the table's header row lists P1-0 through P1-R and P2-1 through P2-7; "K0" does not appear as a column — K0 is an EXECUTION_PLAN.md invention, not a PFP package). **The blackboard correction's "Backend-B" attribution is for the ORIGINAL R4-5 build (which happened inside the P1-1/T1-explorer wave, before K-numbering existed)**, and per EXECUTION_PLAN.md's own seat table (§3), the file `cp/trace_schema.py` defaults to Backend-LLM-Obs (BB) ownership (`EXECUTION_PLAN.md:61`, "`cp/trace_schema.py` (from R4-5 on)"). **Net: still true for K0's purposes — BB owns `trace_schema.py` now and for K0 — but the reasoning chain is "EXECUTION_PLAN.md §3's default", not "PFP §4's P1-1 column", since K0 sits outside PFP's package table entirely.** This distinction matters for anyone trying to re-derive ownership from PFP alone, as the brief instructed me to attempt. |
| `evidence_complete(code_sha256, prompt_version, complete, attestation_id)` is a view | **TRUE** | `trace_schema.py:361-367`: `CREATE OR REPLACE VIEW dq_ctl.evidence_complete AS ... SELECT a.code_sha256, a.prompt_version, (...) AS complete, a.id AS attestation_id FROM ...` — four columns in that exact order, confirmed. |
| Minimums use the per-tier table at PFP :1318-1322 | **TRUE** | `DEFAULT_PROMOTION_MINIMUMS` dict (`governance_schema.py:262-266`) matches PFP's table exactly: L2 `{human_decisions:10, override_rate_max:0.10, denies:0, lookback_withdrawn:0}`, L3 `{human_releases:20, overrides:0, denies:0, lookback_withdrawn:0}`, L4 `{released_after_window:30, vetoes:0, denies:0, lookback_withdrawn:0}`. |
| Raising needs one approver, lowering needs two | **TRUE** | §1 table row above; `minimums_lower_only()` + `gov_item_lower_only`'s POLICY branch, `governance_schema.py:598-638`. |
| The nine tests are at PFP :1335-1344 | **TRUE, but relocated.** | They now live in `tests/t1/test_evidence_promotion.py` (a dedicated file), not inline at whatever line PFP's own numbering implied for a draft state. All 9 confirmed present by name, §3.5 above. PFP's line numbers point at the *plan document's own prose describing* these tests, not at a code file — so "relocated" here means "the plan's description became a real file, correctly," not "the file moved." No actual drift found. |

### 6.1 Step 1 — Backend-Engine (recorder, CLI, I9 integrity test)

**Reads:** `dqagent/cli/main.py` (owns, per EXECUTION_PLAN.md §3 default for BA — confirmed: BA owns `cli/main.py`
except where PFP §4 reassigns it per-package, and K0 is outside PFP's table, so the EXECUTION_PLAN.md §3 default
stands); `dqagent/cp/trace_schema.py` and `governance_schema.py` (read-only — **BA does not own these for K0**,
per the ownership correction above; BA must not edit `eval_run_record`'s signature without asking BB); `tests/t1/
evidence.py` (shared helper — **flagged**: PFP §4 does not list an owner for `tests/t1/evidence.py` in any package
column; by the EXECUTION_PLAN.md §3 default, `tests/plugins/**` belongs to BA, but `tests/t1/evidence.py` is not
under `tests/plugins/` — it is a T1 test helper. **This file's ownership is UNRESOLVED; flagged as Decision D5,
not assigned here.**).

**Owns (for this step):** the new `tests/plugins/eval_recorder.py` (or `dqagent/eval/recorder.py` — per D1, not
decided by this architect); `dqagent/cli/main.py`'s new `eval` subparser; the new CLI-level negative-control test.

**Must not touch:** `cp/trace_schema.py`, `cp/governance_schema.py` DDL (BB's file); `api/app.py` (BC's file);
anything under `tower/`.

### 6.2 Step 2 — Backend-Engine (simulator / earn story)

**Reads:** `dqagent/runtime.py` (`earn_story_seed()` :1728-1736, `sim_preload()` :1738-1743 — owns, per
EXECUTION_PLAN.md §3's `runtime.py` default row, which is BA for everything except P1-3's BC reassignment —
irrelevant here since K0 ≠ P1-3); `tests/t1/test_agent_eval.py` (read-only investigation first — **flagged: this
file's fix (B14) is NOT clearly BA's alone.** `test_agent_eval.py` is not listed under any seat's "owns" column in
EXECUTION_PLAN.md §3's table at all (it is a T1 test, and the table lists `tests/plugins/**` for BA and
`tests/parity/**` for BB, but not `tests/t1/test_agent_eval.py` specifically). **Flagged as Decision D6.**)

**Owns:** whatever change makes the `--earn-story` CLI path (and, if B14 is confirmed a real failure, the
`S6-EARN-AUTONOMY` scenario / `Run.promote()`) consistent with evidence-gated promotion, pending the Decision D6
resolution on who fixes `test_agent_eval.py` itself.

**Must not touch:** the same exclusions as 6.1.

### 6.3 Step 3 — Backend-LLM-Obs (fix the four rewritten test files; fix `test_agent_eval` if assigned here instead of step 2)

**Reads:** `tests/t1/test_parte_cp.py`, `test_parte_redteam_fixes.py`, `test_parte_earn.py`, `test_parte_api.py`
(content already verified consistent with R4-5, §1a — this step may be closer to "confirm and run" than "fix",
pending actual Postgres execution); `tests/t1/test_agent_eval.py` (if D6 assigns the fix here — PFP's own plan text
for the original R4-5 package explicitly names this as a BB task: "fix `test_agent_eval`: the promotion step
creates a deterministic evidence bundle through the step-1 recorder", EXECUTION_PLAN.md:241 — **this is the
stronger reading; I lean toward BB owning this fix, but do not resolve it, since it depends on D1/recorder location
decided first**).

**Owns:** these five test files' fixes (if needed after execution); re-verification that I-3 digests are unchanged
(B6 — already confirmed by static read that `observe()` doesn't touch the R4-5 tables, but the EMPIRICAL before/
after digest comparison is still owed, and per the G0 record (`BACKLOG.md:36`) a similar empirical check was
previously DEMOTED on cross-examination as "shallow green" when Postgres was down — **BB must not repeat that
mistake: a digest check that only "ran" under skipped/no-Postgres conditions is not evidence**).

**Must not touch:** `app.py` (BC), Tower files, `cli/main.py` (BA, per 6.1).

### 6.4 Step 4 — Backend-API-Gov (new routes, `/autonomy` evidence/minimums fields)

**Reads/owns:** `dqagent/api/app.py` — confirmed sole BC ownership for this file in every PFP-cited package column
I found ("BC only" explicitly at `PARTF_IMPLEMENTATION_PLAN.md:1082` for P1-1; BC for P1-2/P1-4 registration too).
This step's two new GET routes (`/evals`, `/eval-attestations?status=PROPOSED`) **should live in `app.py` per this
ownership**, creating the D2 collision noted in §3.3 against PFP P2-1's later BB/obs_routes.py ownership of the
*eventual* `/evals` family. **Flagged, not resolved** — my recommendation (not a decision) is that since K0 is
building a narrow subset early and PFP's own P1-1 precedent already put the sibling POST routes in `app.py`, the
GET routes should follow them there for consistency within K0, with a note on the blackboard that K11 (P2-1 proper)
may consolidate everything into `obs_routes.py` later. The orchestrator should decide, not assume my recommendation.

**Must not touch:** `cp/trace_schema.py`, `cp/governance_schema.py` (BB), `cli/main.py` (BA), `tower/` (Tower seat).

### 6.5 Step 5 — Tower

**Reads/owns:** `tower/src/app/agents/page.tsx`, `tower/src/lib/api.ts`, `tower/src/lib/types.ts`,
`tower/src/lib/mock/server.ts` (or a new `lib/mock/routes/evals.ts`, per the flagged style choice §3.4) —
all confirmed `tower/**` is wholly Tower-owned per EXECUTION_PLAN.md §3's table (`Owns: tower/**`). **This step
must fix B8/B23 (the stale 6-arg contract and stale "resets" language) in addition to building the new pending-
list UI** — both are real defects in the current tree, not merely missing features, and should be framed to the
Tower seat as "fix the contract mismatch" (additive correction) rather than "build the attestation form" (which
already exists, incorrectly).

**Must not touch:** anything under `dqagent/`.

### 6.6 Step 6 — Gate (orchestrator only)

`python -m tools.gate --label partF-K0 --tower`. Per the environment note in this brief, Postgres is not installed
on this Windows machine (BACKLOG G0-D2, BLOCKED) — **the gate will again report `suite_complete: false`** for any
CP-dependent T1 test, exactly as G0's own closing receipt did (`docs/blackboard.md:1641`, 752/1081 passed, 326
skipped). This is expected, not a new K0 defect, and must be reported honestly rather than as a clean green.

---

## 7. SQLite-shim classification of K0's CP-touching tests

Per the brief's instruction, classifying each new/changed test by whether it is the kind of logic a future SQLite
shim could meaningfully exercise (pure decision logic) versus the kind that is fundamentally Postgres-only (roles,
GRANTs, RLS, real CAS/row-locking, PL/pgSQL). I do not block on the shim; I classify only.

| Test | Classification | Why |
|---|---|---|
| `test_promotion_refused_without_evidence_bundle` | **Shim-compatible (logic)** | The refusal is a pure predicate over rows (`promotion_refusal()`'s logic: does a complete evidence row exist for this code version). The CHECK is expressible without PL/pgSQL-specific features — a SQLite port of the predicate (in Python, or a simplified SQL view) could exercise the same decision logic. |
| `test_promotion_refused_below_minimum_record` | **Shim-compatible (logic)** | Same: counting rows against a JSON-configured minimum is portable logic, not a Postgres-specific guarantee. |
| `test_demo_deterministic_bundle_suffices` | **Shim-compatible (logic)** | Deployment-conditional completeness check; portable. |
| `test_production_needs_det_and_live_llm` | **Mixed — mostly Postgres-only.** | Uses `_raw_plane()` which directly inserts into `dq_ctl.deployment_binding` and `dq_ctl.roster` as the admin connection, bypassing the app layer (`test_evidence_promotion.py:113-122`) — this is testing the SECURITY DEFINER function's SQL logic directly via `psycopg.connect`, which depends on PL/pgSQL (`LANGUAGE plpgsql SECURITY DEFINER`, `governance_schema.py`-style functions). **Postgres-only** for the full test; the underlying *business rule* (production needs both halves) is logic-portable in principle, but this specific test exercises it through real PL/pgSQL functions. |
| `test_scripted_or_template_llm_run_rejected_by_eval_attest` | **Postgres-only** | Directly calls the PL/pgSQL `eval_attest` function and asserts `psycopg.errors.CheckViolation` with specific Postgres error matching (`match="not evidence"` etc., `:161-169`) — this is testing real Postgres CHECK constraint / exception behavior, not portable. |
| `test_track_record_never_resets_and_segments_by_version` | **Postgres-only** | Depends on `dq_ctl.track_record_by_version`, a real view over real session-scoped `current_setting('dq.code_sha256')` defaults set by `CP.connect_as` — session-local GUC variables (`SET ... true`) are a Postgres-specific mechanism with no SQLite equivalent. |
| `test_demotion_needs_no_evidence` | **Mixed.** | The *rule* ("demotion needs no evidence check") is portable logic, but the test calls `s.registry.demote(...)` which goes through the real `gov_apply`/registry service paths — exercising it meaningfully requires the real CP. |
| `test_raising_minimums_one_person_lowering_two` | **Postgres-only for the forged-flag sub-case; shim-compatible for the one/two-approver counting logic.** | The sub-test `with s.cp.as_role("dq_api") ... gov_propose(...)` with a forged `false` loosening flag (`:237-242`) specifically proves the DATABASE recomputes `approvals_required` server-side rather than trusting a client-supplied flag — this is a trust-boundary proof that only matters if the shim also enforces server-side recomputation, which a simplified shim likely would not. The core "raise=1, lower=2" counting rule is portable logic. |
| `test_old_6arg_eval_attest_signature_gone` | **Postgres-only** | Directly queries `pg_proc`/`pg_namespace` system catalogs (`:248-251`) to assert the old function signature doesn't exist — this is inherently a real-Postgres-catalog test with no SQLite analogue. |
| New: CLI-level `eval run` writer test (B11/step 1) | **Shim-compatible (logic), partially.** | Whether `source_mode` is *derived* by the recorder rather than accepted from the caller (the I9 red-team requirement in the plan, EXECUTION_PLAN.md:236) is pure Python logic in the recorder module, fully shim- (or even no-DB-) testable. The actual row write still needs a real `eval_runs` table with its role-restricted INSERT grant — **the write-permission half is Postgres-only** (GRANT/REVOKE is a Postgres role concept), but the derivation-logic half is shim-compatible. |
| New: `eval_runs` append-only / role-restriction test (D4/I9) | **Postgres-only** | GRANTs, REVOKE ALL FROM PUBLIC, and the no-mutation trigger pattern are all Postgres role/trigger mechanisms with no SQLite equivalent. |
| New: `GET /evals`, `GET /eval-attestations?status=PROPOSED` shape tests | **Shim-compatible (logic), if the shim can seed rows at all.** | These test API response SHAPE against seeded rows — portable once rows exist, regardless of how they got there. |

**Summary: of the 9 existing R4-5 tests, 3 are clearly shim-compatible (pure logic), 5 are Postgres-only (PL/pgSQL
functions, session GUCs, system catalogs, or role/GRANT mechanics), and 1 is mixed. Of the ~5 new tests K0 still
needs, roughly half are shim-compatible (API shape, CLI-level derivation logic) and half are Postgres-only (GRANT/
append-only enforcement).** This matters for the build seats: expect SOME real local signal on the logic-heavy new
work once a shim lands, but zero local signal on anything proving a role grant, a PL/pgSQL CHECK violation message,
or a system-catalog fact, on this Windows machine until Postgres itself is installed (G0-D2).

---

## 8. Decisions needed (NOT resolved by this architect; for the orchestrator/user)

- **D1 — recorder module location.** PFP says `tests/plugins/eval_recorder.py`; this architect flags that path as
  an unusual place for a privileged, CLI-invoked DB writer, and offers Option A (follow PFP literally) vs Option B
  (`dqagent/eval/recorder.py` with a thin re-export). Not decided here. (§3.2)
- **D2 — new `/evals` and `/eval-attestations?status=PROPOSED` route ownership/location.** PFP's P1-1 precedent
  (and K0's own narrower scope) argues for `app.py` (BC); PFP's P2-1 (the eventual full package) argues for
  `obs_routes.py` (BB). A collision the architect does not resolve. (§3.3, §6.4)
- **D3 — K0 is not a column in PFP §4's ownership table at all.** PFP §4 (`PARTF_IMPLEMENTATION_PLAN.md:1065-1119`)
  has columns P1-0 through P1-R and P2-1 through P2-7; "K0" (an EXECUTION_PLAN.md invention layering the PFP
  content into a different package order) has no corresponding column. Every per-file ownership claim in this
  design for K0 is therefore derived from EXECUTION_PLAN.md §3's *default* seat-file list, with PFP's P1-1/P2-1
  precedents cited only as analogy, not as literal per-package authority. The orchestrator should confirm whether
  this analogy-based approach is acceptable or whether K0 needs its own explicit ownership row added to PFP (or a
  blackboard DECISION recording the mapping once, for every future K-package, not just K0).
- **D4 — is `eval_runs` actually append-only-enforced today?** `evidence_ddl()` (`trace_schema.py:277-564`) does
  NOT contain a visible `DO $$ ... reject_mutation() ... LOOP` block for `eval_runs` of the kind
  `trace_ddl()` applies to `TRACE_TABLES` (`:166-178`) — I found a scattered, inline trigger-creation block for
  `eval_runs` specifically at `:544-549` ("`DO $$BEGIN ... eval_runs_no_mut ... eval_runs_no_trunc ... END$$;`"),
  which DOES exist and DOES look correct on inspection — but I want a second independent confirmation before
  calling this closed, because my own read nearly missed it (it's a one-off block, not part of the generic
  `TRACE_TABLES` loop), and CLAUDE.md requires two independent confirmations for an absence claim — this is a
  presence claim found on a second pass, so I flag it explicitly rather than quietly relying on my own single read.
  **Re-verify `:544-549` directly before assuming I9 (append-only) is unaddressed or addressed.**
- **D5 — who owns `tests/t1/evidence.py`?** Not listed under any seat in EXECUTION_PLAN.md §3's table, and not a
  PFP §4 column item either (PFP §4 lists `tests/plugins/**` and `tests/parity/**` only). Every R4-5-related test
  file depends on it. (§6.1)
- **D6 — who fixes `test_agent_eval.py`'s `promote()` (B14)?** EXECUTION_PLAN.md's K0 step 3 (Backend-LLM-Obs)
  text explicitly says BB "fix[es] `test_agent_eval`", but step 2 (Backend-Engine, simulator) is the step most
  naturally adjacent to `runtime.py`'s earn-story mechanics that `test_agent_eval.py`'s `Run.promote()` exercises.
  I lean toward BB (per the plan's own explicit text) but do not resolve it, since the dependency on D1 (where the
  recorder lives) affects what "through the step-1 recorder" even means for this fix.
- **D7 (new, found during this re-grounding, not previously on the blackboard) — is `test_agent_eval.py`'s
  `S6-EARN-AUTONOMY` scenario actually broken today, or does something in the harness already grant evidence that
  I didn't find?** I searched `Run` class and found no evidence-bundle creation anywhere in its ~230 lines, and no
  import of `tests/t1/evidence.py`'s helpers. This is a strong signal the scenario is broken, but I could not
  execute it (no Postgres). **This needs to be the FIRST thing checked once Postgres is available — before any
  other K0 work — because if confirmed, it means the "I-3 agent-eval digests unchanged" invariant (CLAUDE.md:48)
  cannot even be checked today: the test that produces those digests may not currently pass at all.** This is
  the single highest-leverage unresolved question in this design, and I surface it as a decision rather than a
  blast-radius footnote because its answer changes K0's step ordering (the digest-preservation check in step 3
  presupposes a passing baseline to diff against).
