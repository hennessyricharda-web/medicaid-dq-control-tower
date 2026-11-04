# K1 design — per-DAG agent profile (read-only)

**Architect (B1).** Code hash cited throughout: `77a7dfa5ae01b1ff65d5fb7fb4b26f9f23edf9ab9a126c7592cd5157d821f885`
(given by the orchestrator; matches K0's close value exactly, no drift). Every claim below is a direct Read/Grep of
the file at the cited line. No shell was used. This design does not resolve ambiguities between the plan and the
code; those are listed under "Decisions needed" for the orchestrator/user.

**Answers:** "where is the agent configuration as we add new DAGs?"
**Spec:** PFP P1-2 (`docs/rules/PARTF_IMPLEMENTATION_PLAN.md:697-731`), unchanged by REVISION 4. Runs on
`harness.pipelines[]` until K2 introduces PIPELINE items.

---

## 0. Scope attack: is this the right package, as scoped?

The plan's K1 section (`EXECUTION_PLAN.md:278-294`) is terse relative to PFP's own text. Before building anything,
two scope questions:

## Decisions — RESOLVED by the orchestrator (2026-10-25, after independently verifying both load-bearing claims)

Orchestrator independently re-confirmed before resolving anything: `profile_digests` plumbing genuinely exists end-to-end (`trace_schema.py:111` column, `tracer.py:153,161,213,218,345,357` constructor/serialization) — this is real, built-ahead plumbing, not a misread. `PIPELINE_RUN`/`BACKTEST` genuinely have zero call sites anywhere in `runtime.py` (grepped directly, zero matches) — confirming K1's real scope for the "three root kinds" line is NIGHT only.

1. **D1 (fixture mechanism: fresh `tools/record_fixtures.py` now vs. defer to K7).** RESOLVED: defer to K7. basis: K7's own EXECUTION_PLAN.md scope is explicitly "the recorder for every route" (§6 K7) — building a one-off recorded-transcript mechanism in K1 that K7 would then have to generalize or replace is exactly the kind of rework the architect flagged as a risk. K1's Tower mock instead follows the ALREADY-BUILT live-derivation pattern (`routes/obs.ts`'s style), consistent with every other K-package's mock so far. wrong-if: K7 turns out to need K1's profile route recorded in a way the live-derivation mock can't retrofit — low risk, profile data is static-shaped (a digest + pipeline metadata), not a live trace.
2. **D2 (runtime.py/tracer.py ownership for P1-2).** RESOLVED: Backend-Engine, per the architect's own recommendation and EXECUTION_PLAN.md §3's seat-file default (Backend-Engine owns `runtime.py` unless a package's PFP table says otherwise, which P1-2's table does not). Recorded here, once, as the formal assignment the plan's own text says is owed.
3. **D3 (three-root-kinds scope).** RESOLVED: NIGHT only, now. basis: PIPELINE_RUN and BACKTEST have no call sites to stamp (confirmed, not inferred) — stamping something that doesn't exist is not possible, let alone in scope. No forward stub is needed since the existing `tracer.py` constructor already accepts `profile_digests` as an optional kwarg (defaults handle the absence cleanly); K2/K3 will pass real values at their own new call sites when those roots are built.
4. **D4 (server-asserted vs. client-side-computed "ran under profile X") — REVERSED after scope review, 2026-10-25.** Originally resolved as server-asserted; a scope-review pass caught that this overrode the architect's own explicit recommendation without adequate reason, and the orchestrator independently confirmed two things the original resolution missed: (a) `contracts/obs.py`'s `Run` model has `ConfigDict(extra="forbid")` (confirmed directly, `obs.py:11` via the shared `_M` base) — widening it is a locked-contract change, not a free additive field; (b) `obs_routes.py`/`contracts/obs.py` are explicitly listed under K1's OWN "must not touch" file list in this same design doc, and are P1-1/BB territory per PFP's table, not P1-2/BC's — the original resolution bypassed the project's own file-ownership guardrail by fiat instead of recording a proper reassignment. **RESOLVED (final): option (b), client-side-only.** Tower's existing `app/runs/[traceId]/page.tsx` calls the new profile endpoint and compares digests in the browser; `obs_routes.py`/`contracts/obs.py` are not touched at all. The presenter sees the identical equality either way — zero loss to the demo narrative, and this removes a cross-seat touch, an unassigned ownership exception, and a contract-widening change that the demo does not actually require (this is exactly the CLAUDE.md demo-vs-hardening line: server-side provenance assertion on a locked contract is a pilot-grade integrity property, not something a read-only demo screen needs).
5. **D5 (naming collision, `"profile_digest"` already used for an unrelated onboarding concept at `app.py:1176,1183`).** RESOLVED: no action, confirmed low-stakes as the architect flagged. Noted here so the after-build red team doesn't waste a round rediscovering this as a false "collision."

**Scope note:** none of these 5 decisions change K1's scope, contradict PFP, or require a user WAIT — all are either file-ownership/sequencing calls within the orchestrator's mandate, or narrowing calls justified by a confirmed absence in the code (not an assumption).

## Decisions — ROUND 2, after-red-team (2026-10-25), both CRITICAL findings confirmed and resolved

Orchestrator independently verified both before resolving: grepped `runtime.py` directly and confirmed `_root_args()` (`:548-550`) genuinely feeds FOUR root kinds — APPLY_CHANGE (`:420`), PUBLISH_CYCLE (`:561`), RECOVER (`:1233`), NIGHT (`:1552-1553`) — not just NIGHT. Also confirmed `contracts_w1.yaml`'s `authorities_by_program` genuinely exists only at the per-relation level (lines 13,27,37), with no pipeline-level authority field, and confirmed `compose_profile`'s reserved signature (§3) genuinely takes no `scope`/`principal` argument.

6. **D6 (CRITICAL — `profile_digests` must NOT be added to the shared `_root_args()` return).** RESOLVED: gate by root kind, explicitly, in code — not left as "the seat's call." Backend-Engine adds `profile_digests=` as an EXPLICIT keyword argument ONLY at the `tracer.root("NIGHT", ...)` call site (`runtime.py:1552-1553`), never inside `_root_args()` itself. `APPLY_CHANGE`, `PUBLISH_CYCLE`, and `RECOVER` roots get no `profile_digests` value (the constructor's existing default, confirmed in K1_DESIGN.md's own finding, handles the absence cleanly — `tracer.py` already treats it as optional). basis: stamping a "pipelines touched tonight" map onto a PUBLISH_CYCLE or RECOVER root is meaningless and misleading — those roots aren't about a specific night's pipeline run. wrong-if: a future package's PUBLISH_CYCLE/RECOVER work actually wants a profile stamp for its own reason — that's a new, separate decision for that package, not inherited from K1's NIGHT-only stamp.
7. **D7 (CRITICAL — the SNAP-scope enforcement mechanism was named by test only, not specified).** RESOLVED: the profile route (`GET /api/v1/catalog/pipelines/{id}/profile`) takes the caller's principal and computes the pipeline's authority set the SAME way `contracts_w1.yaml`'s `authorities_by_program` already determines it for every gated relation the pipeline touches (union across the pipeline's `gated_nodes`/relations, per the harness entry). If the caller's `scope(p)` (the existing primitive, `app.py:131-134`) does not intersect that authority set, the route returns **403**, not a redacted profile — basis: this matches the row-level-deny pattern every other authority-scoped route in this codebase already uses (`as_role("dq_api", scope(p))`, RLS-backed), and a redacted-profile response would be a NEW response shape this package doesn't otherwise need, where a flat 403 reuses the existing pattern exactly. Backend-API-Gov implements this as part of `profile.py`'s route handler, not deferred, not left to a seat's guess.
8. **D8 (IMPORTANT, closing the red team's D4-vs-§4-step-2 tension).** Confirmed consistent with the D4 reversal already made: the "server-asserted" language in the ORIGINAL (now-reversed) D4 only ever applied to the underlying `dq_ctl.runs.profile_digests` column (which D6 above makes real and NIGHT-scoped). The Tower TRACE SCREEN's rendering of "ran under profile X" is, and is accepted to be, a client-side comparison against two independently-fetched endpoints (the reversed D4's explicit choice) — this is a known, accepted limitation for a demo-scoped package, not a silent gap. If a network hiccup or stale mock produced a wrong on-screen comparison, that would be a Tower bug to catch in QA/live-audit, not a missing server assertion — explicitly out of scope for K1's own server-side guarantees.
9. **D9 (IMPORTANT, `as_of` resolution algorithm).** RESOLVED: `as_of` resolves to a point-in-time cutoff by comparing against `config_artifacts.at` (the artifact's own approval timestamp), using the trace's `config_digest` (NOT `started_at`) as the anchor — `compose_profile(..., as_of=None)` defaults to "now" (the latest approved artifacts); `as_of=<trace_id>` resolves by looking up that trace's stamped `config_digest` and finding the `config_artifacts` row whose hash matches, then using ITS `at` timestamp as the cutoff for selecting which OTHER artifacts (rules, bindings) were current at that same moment. basis: `config_digest` is the actual point-in-time identity already stamped on every root (confirmed existing, `tracer.py:153` constructor param) — reusing it avoids inventing a second notion of "when" a run happened. wrong-if: multiple config_artifacts share an identical digest at different times (collision) — Backend-API-Gov should assert digest uniqueness or take the most recent match, flagged for that seat to confirm against the real schema.
10. **D10 (Nit, accepted as a cheap mitigation, not a scope change).** The red team's point that "where is the config" naturally leads to "how do I change it" (deferred to K8) gets a one-line UI mitigation: the Tower profile page shows a disabled "Edit" affordance with a tooltip pointing to K8, rather than silence. Small, additive, Tower-owned; not a new capability.

**Scope note (round 2):** D6 and D7 are build-correctness requirements the red team correctly found underspecified in prose — resolving them precisely is squarely the orchestrator's job before dispatch, not a scope change. No WAIT triggered.

---

1. **Is `compose_profile` genuinely new-build, or does equivalent logic already exist and need only exposing?**
   Confirmed by exhaustive grep (two independent passes, both empty): `compose_profile` and `profile_digest` as a
   *function* do not exist anywhere in `dqagent/`. `profile_digest` as a *string key* exists only in
   `dqagent/api/app.py:1176,1183` — the **onboarding proposer's** digest (`hashlib.sha256` over relation profiles,
   `profile_relation()`), a completely different concept (metadata-profiling of a *relation* for element-binding
   proposals, Part D). This is a naming collision worth flagging (§5) but not a blocker: PFP's `compose_profile`
   is a *governance configuration snapshot of a pipeline*, unrelated to the onboarding proposer's per-relation
   profile. **Verdict: pure new-build**, confirmed by a second independent grep of `tests/t1/test_governance_api.py`
   (the only other file matching `compose_profile|profile_digest`, which only asserts the onboarding proposer's
   `profile_digest` key, `:68` — not PFP's).

2. **Does `profile_digests` already have a landing place on the trace roots?** Yes — confirmed load-bearing finding:
   `Root.__init__` (`dqagent/obs/tracer.py:152-162`) already accepts and stores a `profile_digests` kwarg (dict,
   defaults to `{}`), `_runs_row` (`:211-214`) already serializes it as `jsonb` into the `_RUNS_INSERT` statement
   (`:217-219`), and `dq_ctl.runs.profile_digests jsonb NOT NULL DEFAULT '{}'::jsonb` is **already a column** in the
   schema (`dqagent/cp/trace_schema.py:111`). This column and plumbing exist already, pre-K1 — almost certainly laid
   down during T1-traces as forward-compatible plumbing for P1-2. **This changes the step-2 framing materially**:
   the work is not "add a `profile_digests` field to the trace roots," it is "populate the dict that `root(...)`
   already accepts, at each of the three call sites, with real digests computed by `compose_profile`." See §2 and
   §6 step 2.

3. **Scope reviewer's likely challenge, pre-empted here**: is read-only profile a worthwhile package on its own,
   given K2 will replace `harness.pipelines[]` with PIPELINE items almost immediately after? Yes — the plan's own
   ordering rationale (`EXECUTION_PLAN.md:28`, "demo story first... each one adds a demo moment that works on its
   own") makes this intentional: K1's demo moment ("this night ran under this exact configuration") does not
   require PIPELINE items, only `harness.pipelines[]`, and K2's P1-3 is explicitly built as K1's "Prerequisite"
   dependent (PFP line 738, "Prerequisites: P1-2"), not the reverse. The read/write split (K1 read, K8 edit) is
   also PFP's own structure (`P2-3`, `:984-991`), not this plan's invention. No scope change recommended.

---

## 1. Re-grounding every file:line the plan and PFP cite, against the current tree

| Citation | Claim | Verdict | Evidence (code hash `77a7dfa5…`) |
|---|---|---|---|
| PFP `:700` "Owners: BC (backend); TP (screen)" | ownership | **HOLDS**, consistent with `EXECUTION_PLAN.md` step 1 | No conflicting ownership found |
| PFP `:706` `compose_profile(registry, state, pipeline_id, as_of: str \| None) -> dict` | exact signature | **Confirmed absent, new-build** (two independent greps, see §0.1) | n/a — reserved below, §3 |
| PFP `:709` "`as_of` is a trace_id or cr_id, resolved to a `config_artifacts.at` cutoff (primary key `(artifact_id, version)`, `governance_schema.py:25-29`)" | PK location | **STALE LINE NUMBER, content holds elsewhere.** `dqagent/cp/governance_schema.py:25-30` is `config_artifacts` with `PRIMARY KEY (artifact_id, version)` at line 30, not 25-29 (off by ~1-5 lines — the table's `CREATE TABLE` opens at `:25`, the PK clause is at `:30`). Minor drift, not a material error; cite `:25-30` going forward. | `dqagent/cp/governance_schema.py:25-30` |
| PFP `:710` "the pipeline (from `harness.pipelines[]` until P1-3...)" | current data source | **HOLDS.** `dqagent/runtime.py:617` (`pipelines_view`) iterates `self.harness["pipelines"]`; `self.harness` is loaded from `config/harness_w1.yaml` via the registry's `HARNESS` artifact (`dqagent/governance/registry.py:160-161`, `:182`) and set on the runtime at `dqagent/runtime.py:348-349`. Confirmed shape: `config/harness_w1.yaml:1-30` — two pipelines (`p4_eligibility_ie`, `p6_claims`), each with `id, platform, orchestrator, dag_source, contracts, gated_nodes, domains`, plus a top-level `dependencies[]` list (`domain, depends_on, key_map, temporal_binding, policy, max_wait, escalation`). | `config/harness_w1.yaml:1-30`; `dqagent/runtime.py:348-349,617` |
| PFP `:712` "rules per relation... `effective_hash` against the approved hash and overlays" | approved-hash source | **HOLDS.** `Registry.approved()` (`dqagent/governance/registry.py:467-474`) returns `{artifact_id: {hash, ...}}` from `registry_events` where `state='APPROVED'`; `ruleset()` (`:186-190`) computes each rule's live `effective_hash`. `compose_profile` must diff these two, exactly as `_impact()` (`:439-446`) already does for change-request impact — that function is the house pattern to reuse, not duplicate. | `dqagent/governance/registry.py:186-190,439-446,467-474` |
| PFP `:714` "P1-2 shows the 4-argument `autonomy_tier`" | function arity | **HOLDS.** `dq_ctl.autonomy_tier(p_state, p_agent, p_action_class, p_authority)` — exactly 4 args, `dqagent/cp/governance_schema.py:450-455`. Callable from Python via `Registry.tier()` (`dqagent/governance/registry.py:739-743`) or `tier_of()` in `app.py:258-264`. | `dqagent/cp/governance_schema.py:450-455`; `dqagent/governance/registry.py:739-743` |
| PFP `:715` "monitors and pinned baselines, with `provenance: FILE` (L-10)" | monitor source | **HOLDS.** `MONITOR_DEFAULTS` (`dqagent/governance/registry.py:42-43`) is a Python literal seeded once at genesis as the `MONITOR` artifact (`:170`) — there is no separate "monitor spec file" beyond this seed; BACKLOG L-10 (`docs/BACKLOG.md:120`) confirms: "Monitor spec and bindings are sourced from files, not governed... shown in the profile as 'not governed'." The profile's "not governed" chip is therefore a literal label on the MONITOR artifact's content, not a comparison against anything external. | `dqagent/governance/registry.py:42-43,170`; `docs/BACKLOG.md:120` |
| PFP `:717` "routing (governed after P1-0)" | FEEDS governance | **HOLDS.** `Routing.of(cfg.get("feeds"))` validated at propose-time (`dqagent/governance/registry.py:524-526`); `FEEDS` is a `MANDATORY` kind (`:210`) — always governed, never "not governed." | `dqagent/governance/registry.py:524-526` |
| PFP `:718` "`contributors[{artifact_id,version,content_hash,cr_id,approvers}]`" | approvers source | **New assembly required — no single existing function returns this exact shape.** `config_artifacts` rows (via `Registry.artifacts()`, `:454-462`) give `artifact_id, version, content_hash, cr_id` directly. `approvers` (plural, per artifact version) is **not** stored as a column; it must be derived from `config_cr_events` where `event='APPROVED'`, filtered to the `cr_id` on that artifact version — the same join `gov_apply`'s SQL already performs server-side (`dqagent/cp/governance_schema.py:213-217`, counting distinct `person_id` for `event='APPROVED' AND person_id <> author`). `compose_profile` needs an equivalent read-only query (no existing Python helper returns a list of approver person_ids per `cr_id`; this is new, see §3). | `dqagent/cp/governance_schema.py:213-217`; `dqagent/governance/registry.py:454-462` |
| PFP `:721` "registered at `app.py:1207`" | registration line | **STALE.** `app.py:1207` today is inside `measure_series` (`dqagent/api/app.py:1196-1217`, specifically mid-function at `:1207`, the suppression-logic `for r in rows:` loop) — not a registration point at all. The file has grown past where PFP's line numbers point (K0 added the onboarding-proposer routes and others ahead of this point). The nearest sensible registration point for a new router module, by house pattern, is where `obs_routes(app, services, principal)` and `ops_routes(app, services, principal)` are called, both inside `create_app`, at `dqagent/api/app.py:1342-1345`. | `dqagent/api/app.py:1207` (stale), `:1342-1345` (correct pattern to follow) |
| PFP `:722` "Tower: `app/pipelines/[id]/profile/page.tsx`... Mock serves the recorded `p1_profile.json`" | file path, recorder | **Confirmed absent, new-build.** `tower/src/app/pipelines/` contains only `page.tsx` (the existing Pipeline map / global order screen, `tower/src/app/pipelines/page.tsx:1-118` — unrelated to a per-DAG profile; it is PFP's later Catalog work, K2/K4 territory, already built ahead of plan as a flat map view). No `[id]/` subdirectory exists. `tools/record_fixtures.py` **does not exist anywhere in the repo** (confirmed: `tools/*.py` lists only `__init__.py, receipt.py, eval_detections.py, pair_matrix.py, gate.py`; grepped `record_fixtures` across `dqagent/`, `tools/`, found 0 hits — only 3 *doc* references, in `EXECUTION_PLAN.md`, PFP itself, and `OBSERVABILITY_PARITY_ARCHITECTURE.md`). `tower/src/lib/mock/recorded/` **does not exist** (confirmed: `Glob` for `tower/src/lib/mock/recorded/*` returns nothing). The mock machinery that DOES exist today is `tower/src/lib/mock/routes/obs.ts`, which derives everything from in-memory mock state at request time (`routes/obs.ts:1-16`), not from a recorded JSON fixture — this is a **different mocking pattern** from what PFP/K7 envisions (a static recorded-transcript fixture file). K1 is the first package to need the recorded-fixture pattern; K7 is titled "mock/production parity (seams manifest, assert-no-mock, **recorded transcripts**)" and is scheduled *after* K1-K6. **This is the single largest ambiguity in this package — see Decisions needed #1.** | Confirmed absent by two independent passes: directory listing + content grep |
| PFP `:724-728` four test names in `tests/t1/test_profile.py` | test file location | **Confirmed absent** (only `tests/t0/test_onboarding_proposer.py` matches `test_profile` substring, and only incidentally via `test_profile_relation`-style names — not PFP's file). New file, names reserved verbatim below. | `tests/t1/` directory listing |
| EXECUTION_PLAN `:284` "PFP §4 lists no P1-2 owner for these files (`:1067, :1074`)" | PFP ownership table gap | **HOLDS.** PFP's file-ownership table (`docs/rules/PARTF_IMPLEMENTATION_PLAN.md:1065-1103`) has a P1-2 column; `runtime.py`'s P1-2 cell (`:1067`) and `obs/tracer.py`'s P1-2 cell (`:1074`) are both literally `—` (not edited in P1-2, per that table). This is a genuine gap the plan correctly flagged. The orchestrator must formally record an ownership assignment for these two files in K1 — this design does **not** resolve it (see Decisions needed #2), only confirms the gap is real. | `docs/rules/PARTF_IMPLEMENTATION_PLAN.md:1065-1103` |
| EXECUTION_PLAN `:286` duplicate "3." numbering (Backend-LLM-Obs fixture recording AND Tower both numbered "3.") | plan text bug | **CONFIRMED, cosmetic.** `EXECUTION_PLAN.md:285` ("3. **Backend-LLM-Obs:**...") and `:286` ("3. **Tower:**...") are both "3." — a markdown list-numbering slip, not a substantive ambiguity (the plan's own prose makes the intended order clear: fixtures before the Tower consumes them). Resolved into a clean step order for seats in §6 below; not silently renumbered in the plan itself (left for the orchestrator to fix, or not, in `EXECUTION_PLAN.md`). | `docs/EXECUTION_PLAN.md:285-286` |

---

## 2. Blast radius

Every object K1 changes, its dependents (`file:line`), and the test that covers each.

| # | Object K1 touches | Dependent(s) (`file:line`) | Test coverage |
|---|---|---|---|
| B1 | New module `dqagent/governance/profile.py` (`compose_profile`) | Called by new route in `dqagent/api/catalog_routes.py` (new); called by Backend-Engine at the three `tracer.root(...)` call sites to populate `profile_digests` (B3-B5) | New: `tests/t1/test_profile.py` (4 PFP tests + 1 scope test, §4) |
| B2 | New file `dqagent/api/catalog_routes.py`, registered in `create_app` | `app.py:1342-1345` (registration pattern to follow); Tower's `lib/api/catalog.ts` (new) calls it | `tests/t1/test_profile.py::test_profile_as_of_old_trace_matches_config_then` (exercises the route through `TestClient`, house pattern per `test_trace_shape.py`) |
| B3 | `tracer.root("NIGHT", ...)` call, `dqagent/runtime.py:1552-1553` | `_root_args()` (`:548-550`) is the natural place to add `profile_digests=...`, since every root already funnels through it | New assertion in `test_profile_digest_on_every_run_root` |
| B4 | `tracer.root("PIPELINE_RUN", ...)` — **does not exist yet**; confirmed absent (grep of `dqagent/` for `PIPELINE_RUN` finds it only declared in `RUN_KINDS` at `dqagent/cp/trace_schema.py:18`, never instantiated by a `tracer.root(...)` call anywhere in `runtime.py` or elsewhere) | n/a today — PIPELINE_RUN roots are introduced by **K2** (`EXECUTION_PLAN.md:325`, "The PIPELINE_RUN span after the drain"), not K1 | **Decisions needed #3**: PFP's P1-2 text (`:720`) says "stamp `runs.profile_digests` on NIGHT, PIPELINE_RUN and BACKTEST roots" as if all three exist now; two of the three (PIPELINE_RUN, BACKTEST) do not exist as root kinds instantiated anywhere in the current runtime. This is not a stale line number — it is PFP describing three call sites where only one (NIGHT) currently has a call site to edit. |
| B5 | `tracer.root("BACKTEST", ...)` — **does not exist yet**; confirmed absent by the same method (declared in `RUN_KINDS` at `trace_schema.py:18`, zero `tracer.root("BACKTEST"` call sites anywhere) | `backtest.py` **does not exist in the tree** (confirmed: `Glob dqagent/backtest.py` → no files found) — it is K3's deliverable (`EXECUTION_PLAN.md:337-346`, PFP P1-4) | Same as B4 — see Decisions needed #3 |
| B6 | `Root.__init__`'s existing `profile_digests` kwarg and `_runs_row`'s serialization (`dqagent/obs/tracer.py:152-162,211-219`) | Already-built plumbing (§0.2); no code change needed here, only call-site population | Existing trace-shape tests (`tests/t1/test_trace_shape.py`) must keep passing unmodified — a regression guard, not new coverage |
| B7 | `dq_ctl.runs.profile_digests` column (`dqagent/cp/trace_schema.py:111`) | Any reader of `dq_ctl.runs.*` that enumerates columns explicitly. Checked `_run_detail` (`dqagent/api/obs_routes.py:229-232`) — it selects a **named tuple of columns** (`"trace_id", "kind", "parent_trace_id", "state", "pipeline_id", "night_date", "attempt", "person_id", "config_digest", "code_digest", "deployment", "source_mode", "started_at"`) that explicitly **excludes** `profile_digests` today. `contracts/obs.py`'s `Run` model (`:38-51`) likewise has no `profile_digests` field and forbids extra keys (`ConfigDict(extra="forbid")`, `:11`). **K1 does not need to expose `profile_digests` through `/runs/{trace_id}` for its own demo moment** (the demo moment reads the *profile endpoint's* digest and compares it against the *trace's* digest by other means, per the demo script row 5 — see §4 golden path); whether to also surface it on `/runs/{trace_id}` is a product nicety, not required by PFP's P1-2 text, and is listed as Decisions needed #4 so a seat doesn't silently widen scope by editing a `extra="forbid"` contract mid-package. | None yet; `obs_routes.py`/`contracts/obs.py` are **not** BC/P1-2-owned files per PFP's table (owned by BB in P1-1, `:1083`, not reassigned for P1-2) |
| B8 | `tower/src/app/pipelines/page.tsx` (existing flat Pipeline-map page) | Will coexist with the new `app/pipelines/[id]/profile/page.tsx` route; Next.js route segments do not collide (`pipelines/page.tsx` vs `pipelines/[id]/profile/page.tsx` are distinct routes) — confirmed no naming conflict, but the existing page's "Pipeline map" title and the new page's "profile" concept should be visibly distinct in the Tower nav so a presenter doesn't confuse them (a Tower UX note, not a blast-radius risk) | `tower/scripts/smoke.mjs` (existing) must keep passing; new smoke shots added for the profile page |
| B9 | `tower/src/lib/types.ts`'s `Pipelines` interface (`:267-277`) | Read-only; K1 does not touch this type (it introduces a **new** type, `PipelineProfile`, in a **new** file `lib/types/catalog.ts` per PFP's file-ownership table `:1101` — "`lib/types/{obs,catalog}.ts`... TP (catalog)" — never in `types.ts`, which PFP reserves to TO/P1-1) | n/a — confirms no edit needed to the existing file |
| B10 | `ATTR_VOCAB` closed vocabulary (`dqagent/cp/trace_schema.py:38-44`) | K1 adds no new span **attribute** values (profile digests are stamped on the **run root**, a `jsonb` column, not a span `attr()` call subject to `_check_value`'s closed vocabulary, `tracer.py:88-103`) — confirmed no `ATTR_VOCAB` edit needed. K1 **does** add a new span-**ref** usage if the profile page links back via a `pipeline:` ref, but `"pipeline"` is already a registered `REFS_PREFIXES` entry (`trace_schema.py:31`) — no new prefix needed. | None — confirms no change |
| B11 | The onboarding proposer's existing `profile_digest` key (`app.py:1176,1183`) | Naming collision risk only (§0.1) — no functional dependency. Flagged so no seat conflates the two or "refactors" one into the other mid-package. | n/a |

**Count: 11 blast-radius items (B1-B11), two of which (B4, B5) resolve to "the dependent doesn't exist yet" rather than a concrete file:line — itself a material finding, not a null result.**

---

## 3. Reserved names

Seats must claim names from this list, not invent alternatives.

**Backend (Backend-API-Gov):**
- Module: `dqagent/governance/profile.py`
- Function: `compose_profile(registry, state: str, pipeline_id: str, as_of: str | None = None) -> dict`
  — matches PFP `:706` exactly. `registry` is a `Registry` instance (not `self.cp` directly — follow the house
  pattern of every other Part-D/E function in `registry.py`, which takes `self` as the registry already).
  **Correction to PFP's literal signature**: since this lives as a *method* on `Registry` or a free function taking
  a `Registry` instance, the first positional argument should be `registry: Registry`, consistent with PFP's own
  text ("`compose_profile(registry, state, pipeline_id, as_of)`" — PFP already writes `registry` first, confirmed
  correct, no correction needed).
- New read helper (no existing equivalent, per B1/§1 `:718` finding): `Registry.contributors_for(self, state, artifact_ids: list[str]) -> dict` — returns `{artifact_id: [{version, content_hash, cr_id, approvers: [person_id]}]}` by joining `config_artifacts` with `config_cr_events` (`event='APPROVED'`), the same join shape as `gov_apply`'s SQL (`governance_schema.py:213-217`), exposed read-only through `dq_registry_svc`.
- Route: `GET /api/v1/catalog/pipelines/{id}/profile?as_of=` — exact path from PFP `:721`; file `dqagent/api/catalog_routes.py`, new module, registered in `create_app` immediately after the `ops_routes`/`obs_routes` registrations (`app.py:1342-1345` pattern), i.e. add a `from dqagent.api.catalog_routes import catalog_routes` + `catalog_routes(app, services, principal, scope)` call there. **Do not use the stale `app.py:1207` line as a literal insertion point** (§1 finding).
- Response field names inside the profile dict, taken verbatim from PFP `:710-719`: `pipeline`, `contracts`, `rules`, `policy`, `tiers`, `monitors`, `dependencies`, `order`, `routing`, `contributors`, `profile_digest`.

**Backend (Backend-Engine, re-assigned file per Decisions needed #2):**
- No new function name needed in `runtime.py` — populate the existing `_root_args()` (`:548-550`) with a
  `profile_digests={...}` entry computed via `compose_profile`, at the one call site that currently exists
  (`NIGHT`, `:1552-1553`). Reserve the dict's key name as `pipeline_id` → `profile_digest` string (i.e.
  `profile_digests = {pid: compose_profile(...)["profile_digest"] for pid in <pipelines touched this night>}`),
  consistent with the column's existing type (`jsonb`, a map) and `Root.__init__`'s parameter name (plural,
  `tracer.py:153`).

**Backend (Backend-LLM-Obs):**
- Tool: `tools/record_fixtures.py` — **confirmed net-new** (§1, PFP `:722` row). Reserve its one fixture name for
  K1: `p1_profile.json`, written to `tower/src/lib/mock/recorded/p1_profile.json` (new directory, per PFP's file
  table `:1100`, "TP" owns `lib/mock/recorded/**" for P1-2" — correction: the table says `lib/mock/recorded/**`
  under the **TO** column for P1-1 and leaves P1-2's cell blank for that row; the fixture itself is BB's to
  *record*, TP's to *serve* — both seats touch adjacent files, never the same one. See Decisions needed #1 for
  whether this tool is built fresh in K1 or deferred.

**Tower:**
- Page: `tower/src/app/pipelines/[id]/profile/page.tsx` — exact path from PFP `:722`.
- Type file: `tower/src/lib/types/catalog.ts` (new), interface `PipelineProfile` mirroring the backend's field
  names above.
- API client file: `tower/src/lib/api/catalog.ts` (new), function `pipelineProfile(token, id, asOf?) => call<PipelineProfile>(...)`.
- Mock route file: `tower/src/lib/mock/routes/catalog.ts` (new; PFP's file table `:1100-1101` reserves a dedicated
  per-package file, never shared — "one file per package (`obs.ts` for TO; `catalog.ts`, `profile.ts`, `backtest.ts`
  for TP)"). **Correction/clarification**: PFP's table literally lists three separate Tower mock-route files —
  `catalog.ts`, `profile.ts`, `backtest.ts` — one per later package (K2/K1/K3 respectively, by content). K1's own
  mock route file should therefore be named **`profile.ts`**, not `catalog.ts` (which PFP reserves for K2's
  Catalog-table work). Reserved: `tower/src/lib/mock/routes/profile.ts`.

**Tests:**
- File: `tests/t1/test_profile.py` (new), exact name from PFP `:724`.
- Test ids, verbatim from PFP `:725-728`:
  - `test_profile_as_of_old_trace_matches_config_then`
  - `test_profile_digest_on_every_run_root`
  - `test_monitor_marked_not_governed`
  - `test_profile_scoped`
- Scope test (added by the plan, `EXECUTION_PLAN.md:292`, not separately named by PFP): reserve
  `test_profile_scoped_snap_reviewer_cannot_see_medicaid_rules` as a second case **inside**
  `test_profile.py::test_profile_scoped` (parametrized), or as a sibling test in the same file — the plan's own
  wording ("plus a scope test") suggests it extends, not duplicates, PFP's existing `test_profile_scoped`. Seat
  discretion on parametrize-vs-sibling; name is reserved either way as
  `test_profile_scoped_snap_reviewer_cannot_see_medicaid_rules`.

---

## 4. Golden-path worked example (against the ACTUAL current data shape)

Walking the demo moment ("This night ran under this exact configuration. Here is who approved each part, and what
is not governed") against the real tree, flagging every step that lacks underlying data today.

1. **A night runs.** `Runtime.run_night(...)` opens a `tracer.root("NIGHT", tid, ...)` (`runtime.py:1552-1553`),
   via `_root_args()` (`:548-550`). **Today** `profile_digests` defaults to `{}` (no caller passes it) — this is
   exactly what K1 step 2 must fix: `_root_args()` gains a `profile_digests` entry.
   - **Underlying data check**: for the demo's `p6_claims` pipeline, `compose_profile(registry, "ZZ", "p6_claims", as_of=None)`
     must be callable with the night's current effective configuration. `Registry.effective("ZZ")` (`:464-465`)
     and `Registry.artifacts("ZZ")` (`:454-462`) both work today against any control plane that has run genesis
     (confirmed: these are the same calls `governance_ctx()` already makes successfully every night,
     `runtime.py:380-391`). **No gap** — the data this function needs already flows nightly.
2. **A presenter opens Nightly runs › Trace** (`tower/src/app/runs/[traceId]/page.tsx`, existing, P1-1-built) and
   sees the NIGHT root. **Gap, confirmed by §2/B7**: the trace page today does not render `profile_digests` at all
   (the contract excludes it). For the demo's literal script row 5 ("Pipelines › p6_claims › Profile," PFP's demo
   script `:1137`, "`profile_digest` equals the night root's") to be shown **on the trace screen itself** (not just
   the profile screen), either (a) `obs_routes.py`'s `_run_detail` and `contracts/obs.py`'s `Run` model need a
   `profile_digests` field added — which is **not** a P1-2/BC-owned file per PFP's table (it's P1-1/BB territory)
   — or (b) the Tower computes the "ran under profile X" link purely from data already in `Run.pipeline_id` plus a
   client-side call to the new profile endpoint, never touching `obs_routes.py` at all. **(b) is the design this
   architect recommends** (lowest blast radius, no cross-seat file edit needed) — flagged as Decisions needed #4,
   not resolved here.
3. **The presenter clicks through to `/pipelines/p6_claims/profile`.** The new route calls
   `GET /api/v1/catalog/pipelines/p6_claims/profile`, which calls `compose_profile(registry, "ZZ", "p6_claims", as_of=None)`.
   - Returns `pipeline` (from `harness.pipelines[]`, confirmed real data, §1), `contracts` (from
     `config/state/ZZ/contracts_w1.yaml`, confirmed real file, read in §1's investigation), `rules` (from
     `ruleset()`, confirmed real), `tiers` (from `autonomy_tier`, confirmed real, 4-arg), `monitors` (from the
     `MONITOR` artifact, labeled "not governed" per L-10, confirmed real), `contributors` (from the new
     `contributors_for()` helper, §3 — real data once built, since `config_artifacts`/`config_cr_events` are both
     populated by every `propose`/`decide` call the demo already makes), `profile_digest` (a new
     `hashlib.sha256` over the whole dict, same pattern as `Registry._h()`, `registry.py:51-52`).
4. **The profile page's own digest must equal the night root's `profile_digests["p6_claims"]`.** This holds
   **only if** step 1's write and step 3's read compute the digest identically (same function, called at both
   sites, over the same effective configuration) — this is the central correctness property `test_profile_digest_on_every_run_root`
   must assert, and the central reason `compose_profile` must be the single definition both call sites use,
   never duplicated logic.
5. **"Here is who approved each part"**: `contributors[*].approvers` — confirmed buildable from existing tables
   (§3), but genuinely new code (no existing function returns this shape, per B1 finding in §1's `:718` row).
6. **"what is not governed"**: the monitors chip — confirmed real and simple (a literal label on the MONITOR
   artifact, §1's `:715` row) — **no gap**, the easiest part of this package.

**Net assessment**: the golden path is fully supported by existing data (nothing requires data that doesn't exist
in a running demo), but items 2 (trace-screen integration) and 5 (approvers assembly) are genuinely new code with
no existing equivalent to reuse, and item 2 specifically has a file-ownership question that this design does not
resolve (Decisions needed #4).

---

## 5. Technology-selection records

**No new third-party dependency is introduced by K1** — confirmed: the backend work is pure Python/SQL against
tables that already exist; the Tower work (`[id]/profile/page.tsx`) uses only existing Next.js/React patterns
already present in `tower/src/app/pipelines/page.tsx` and `tower/src/app/runs/[traceId]/page.tsx`, both already
built and in the tree. No `package.json` change is required. Per CLAUDE.md's web-research cap (12 fetches) and the
task's own instruction ("likely not needed... confirm this rather than assume") — **confirmed by code inspection
alone; zero web fetches used.** No technology-selection record is produced because there is no capability gap and
no new candidate set to evaluate.

---

## 6. Per-seat context manifests (resolved step order)

The plan's own text has a duplicate "3." (EXECUTION_PLAN.md:285-286, §1 finding). Resolved into one clean serial
order below — Backend seats run one at a time per CLAUDE.md's concurrency rule; Tower may start once its
dependency (the fixture) exists, consistent with PFP's serial order "BA, then BB, then BC" generalized to this
package's actual dependency chain (API before fixture before Tower).

### Step 1 — Backend-API-Gov
**Reads:** `dqagent/governance/registry.py` (whole file, to reuse `_impact`, `approved`, `artifacts`, `ruleset`,
`_h` patterns); `config/harness_w1.yaml`; `config/state/ZZ/contracts_w1.yaml`; `dqagent/cp/governance_schema.py`
(`:25-30` `config_artifacts`; `:213-217` the approver-counting join to mirror); `dqagent/api/app.py` (`:1342-1345`
registration pattern; `:131-134` `scope()`; `:258-264` `tier_of()`).
**Owns/writes:** new `dqagent/governance/profile.py`; new `dqagent/api/catalog_routes.py`; one registration line
added to `dqagent/api/app.py` near `:1342-1345` (the file is BC-owned for P1-2 registration per PFP `:1082`,
"BC (register)").
**Must not touch:** `dqagent/obs/tracer.py`, `dqagent/runtime.py` (reserved for step 2, pending the orchestrator's
ownership ruling, Decisions needed #2); `dqagent/api/obs_routes.py`, `dqagent/api/contracts/obs.py` (BB-owned,
not reassigned for P1-2, per PFP `:1083`; see Decisions needed #4 before any seat is tempted to add
`profile_digests` there).

### Step 2 — Backend-Engine (file ownership formally assigned by the orchestrator, per EXECUTION_PLAN.md:284 and
PFP's own acknowledged gap at `:1067,:1074`)
**Reads:** `dqagent/obs/tracer.py:151-220` (`Root.__init__`, `_runs_row`, already-built `profile_digests` plumbing,
§0.2 — nothing to change here); `dqagent/runtime.py:548-550` (`_root_args`), `:1552-1553` (the NIGHT root call
site); the new `dqagent/governance/profile.py` from step 1.
**Owns/writes:** `dqagent/runtime.py` only — add a `profile_digests=` entry to `_root_args()`'s return (or to the
one caller that needs it; seat's call on the cleanest implementation, since `_root_args()` is shared by
APPLY_CHANGE, PUBLISH_CYCLE, RECOVER and NIGHT roots alike, `runtime.py:420,561,1233,1553`, and only NIGHT needs a
per-pipeline digest map — adding it unconditionally to `_root_args()` would stamp a meaningless empty/irrelevant
map on the other three root kinds, which may be harmless (`{}` default) or may be worth gating by `kind`).
**Must not touch:** `dqagent/obs/tracer.py` (no change needed — confirmed in §0.2 the plumbing is already there);
anything in `dqagent/governance/` or `dqagent/api/`.
**Flag from EXECUTION_PLAN.md step 2 of K1 (`:284`):** PFP's text ("stamp `runs.profile_digests` on NIGHT,
PIPELINE_RUN and BACKTEST roots") describes three call sites; only one (NIGHT) exists today (B4/B5, §2). This
seat's actual K1 scope is **one call site, not three** — the other two are out of scope until K2/K3 build their
root kinds. Do not pre-emptively add dead code for PIPELINE_RUN/BACKTEST call sites that don't exist yet.

### Step 3 — Backend-LLM-Obs
**Reads:** the finished route from step 1 (to record a live response); `docs/rules/PARTF_IMPLEMENTATION_PLAN.md:722`
("Mock serves the recorded `p1_profile.json`"); existing mock pattern `tower/src/lib/mock/routes/obs.ts` (for
contrast — note it does NOT use a static recorded-fixture file, it derives live from mock state, §1 finding under
PFP `:722`).
**Owns/writes:** new `tools/record_fixtures.py` (confirmed net-new, §1/§3) and the one fixture it writes,
`tower/src/lib/mock/recorded/p1_profile.json`. Per PFP's file table (`:1090`), this is BB's file in every package
from P1-2 onward.
**Must not touch:** `tower/src/lib/mock/routes/*.ts` (TP-owned); the backend route itself (BC-owned, step 1 is
closed by the time this step starts).
**Decisions needed #1 governs this step's scope** — see below; this manifest assumes the tool is built fresh in
K1 (the plan's literal instruction), not deferred to K7.

### Step 4 — Tower
**Reads:** `tower/src/app/runs/[traceId]/page.tsx` (existing pattern to follow for a detail page); `tower/src/app/pipelines/page.tsx`
(existing sibling route, for nav consistency, §2/B8); the recorded fixture from step 3; `tower/src/lib/api.ts:130-145`
(`call<T>` pattern, the one function every API client wraps, per PFP's file table `:1098,1110`, "TP uses
`lib/api/catalog.ts`, which imports the exported `call`").
**Owns/writes:** `tower/src/app/pipelines/[id]/profile/page.tsx` (new); `tower/src/lib/types/catalog.ts` (new);
`tower/src/lib/api/catalog.ts` (new); `tower/src/lib/mock/routes/profile.ts` (new, per the corrected name in §3).
**Must not touch:** `tower/src/lib/types.ts`, `tower/src/lib/api.ts` (PFP reserves both to TO/P1-1, never TP, per
the table's explicit note `:1109-1110`); `tower/src/app/pipelines/page.tsx` itself (read-only reference, not an
edit target — K1 adds a sibling route, not a change to the existing map page).

### Step 5 — Tests (whichever seat's work they cover; per CLAUDE.md, tests ship with the capability, so steps 1-2
each ship their own `tests/t1/test_profile.py` cases incrementally, not as a separate step)
Reserved test ids per §3. Scope test exercises the house pattern already proven in `w1_harness.py:19`
(`H(rt, "snap")`, scoping a token to `AUTH_SNAP` only) and `test_sim_nightly.py:236-238` (`_visible_units` /
`AUTH_SNAP` vs `AUTH_MEDICAID` contrast) — reuse this fixture pattern rather than inventing a new one.

---

## 7. Decisions needed (NOT resolved by this architect — for the orchestrator/user)

1. **The recorded-fixture mechanism (`tools/record_fixtures.py`, `p1_profile.json`) does not exist anywhere in the
   tree, and the mocking pattern actually built so far (`routes/obs.ts`) is architecturally different (live
   derivation from mock state, not a static recorded transcript).** PFP's own K7 package title is "mock/production
   parity... recorded transcripts" — K7 runs *after* K1-K6. Does K1 build the recorder tool from scratch (as PFP's
   P1-2 text literally instructs), knowing K7 will likely rework/generalize it for every other route — or does K1
   follow the already-built `routes/obs.ts` pattern (derive the mock profile live from the same mock pipeline
   fixtures already in `fixtures.ts:511-538`) and defer the recorded-transcript mechanism to K7 as originally
   scoped there? This is a real fork with cost either way: building the recorder now risks throwaway work if K7
   redesigns it; deferring it means K1's mock and K1's live route diverge in mechanism from day one, which is
   exactly the parity gap K7 exists to close. **Needs a user/orchestrator call before Backend-LLM-Obs's step 3
   starts.**
2. **File ownership of `dqagent/runtime.py` and `dqagent/obs/tracer.py` for P1-2.** PFP's own table leaves both
   blank for the P1-2 column (`:1067,:1074`), and the plan correctly flags this as needing the orchestrator's
   formal re-assignment (`EXECUTION_PLAN.md:284`). This design's §6 step 2 assumes Backend-Engine (the seat that
   owns `runtime.py` by default per the seat-file table, `EXECUTION_PLAN.md:60`) — but this is a recommendation,
   not a resolution; the orchestrator must record it on the blackboard before step 2 starts, per CLAUDE.md's
   ownership rule.
3. **PFP `:720` instructs stamping `profile_digests` on NIGHT, PIPELINE_RUN and BACKTEST roots, but only NIGHT
   exists as an instantiated root kind today** (B4/B5, §2). Is K1's scope for this line item correctly narrowed
   to "NIGHT only, now; PIPELINE_RUN and BACKTEST get their own stamp when K2/K3 build those root kinds" — or does
   the orchestrator want a forward-compatible stub added now (e.g. a documented TODO at the exact future call
   sites)? This design recommends the narrow reading (do only what has a real call site) but does not resolve it,
   since it is a scope-sizing call, not a code-correctness one.
4. **Does the night-trace screen need to show "ran under profile `<digest>`" literally on the trace page** (PFP's
   demo-script row 5 implies a two-way link: profile → trace AND trace → profile), **which would require editing
   `dqagent/api/obs_routes.py` / `contracts/obs.py` (BB-owned, not reassigned to BC for P1-2)** — or is a
   client-side-only link (Tower computes/fetches the comparison without a backend contract change) sufficient for
   K1's demo moment? §4 step 2 recommends the client-side-only approach to avoid a cross-seat file edit mid-package,
   but this changes what "ran under profile X" visually means (a Tower-computed label vs. a server-asserted field)
   and should be confirmed before Tower's step 4 starts.
5. **Naming collision, low-stakes but worth a one-line acknowledgment**: the onboarding proposer already uses the
   string key `"profile_digest"` for an unrelated concept (`app.py:1176,1183`). Recommend no action (different
   namespaces — one is an HTTP response field on `/onboarding/propose-bindings`, the other on
   `/catalog/pipelines/{id}/profile` — no actual collision), but flagging so no later red-team round mistakes one
   for the other.

**No WAIT is triggered by this design on its own** — items 1-4 are genuine open scope/sizing questions the
architect is required to surface, not resolve (per this seat's DoD); none of them contradict PFP's design intent,
but all materially affect which seat does what work and how much of it. The orchestrator should resolve at least
#1 and #2 before dispatching the before-build red team, since both change what the red team would even be
attacking.
