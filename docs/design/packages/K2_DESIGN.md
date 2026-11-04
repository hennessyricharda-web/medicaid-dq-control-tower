# K2 design — Pipelines-lite and the consumer DAG (three waves: K2a, K2b, K2c)

**Architect (B1).** Code hash cited throughout: `f000d30a34a95df7cc3dd71cf57a4efe9e214e905529e0624c828f9d0d8d2847`
(given by the orchestrator; matches K1's close value exactly, no drift — confirmed, not assumed, by the orchestrator
directly before this brief). Every claim below is a direct Read/Grep of the file at the cited line against the
CURRENT tree. No shell was used (this seat has none). This design does not resolve ambiguities between PFP, the
plan and the code; those are listed under "Decisions needed" per wave, for the orchestrator/user — per this
package's own instruction, guessing wrong here is expensive across three waves and three gates.

**Spec:** PFP P1-3 (`docs/rules/PARTF_IMPLEMENTATION_PLAN.md:734-885`). REVISION 4 (`:1199-1356`) is binding where it
disagrees in substance; **it does not touch P1-3's text at all** (confirmed: R4-1 through R4-7 cover N1/N2/N5/I1
and R4-5's evidence-gated promotion — none reference PIPELINE, TASK_GATE, `gov_item_lower_only`, ORCH-1,
`gated_dataset_introductions`, the dbt runner, or `catalog_routes.py`'s pipelines/nodes/discover/wire endpoints).
The plan's own K2 section is `EXECUTION_PLAN.md:298-334`.

**Web research:** zero fetches used. Confirmed below (§0.3) that K2 introduces no new third-party dependency — the
task brief's own expectation ("likely needs none... React Flow is K4's, not K2's") is verified correct by direct
reading of `tower/src/app/pipelines/page.tsx` (plain HTML `<table>`, no graph library import) and `package.json`
(no `@xyflow/react` or similar present).

---

## 0. Threshold findings that shape every wave

### 0.1 K1 already built real, adjacent infrastructure K2 must reuse, not duplicate

K1 (closed `f000d30a3…`) already shipped, and K2 must build on top of rather than re-derive:
- `dqagent/governance/profile.py`'s `compose_profile`, `pipeline_authorities`, `_authorities_of`,
  `contributors_for`, `_as_of_cutoff` — all confirmed real, in the tree, exercised by `tests/t1/test_profile.py`
  (9 tests). K2's profile additions (`introduced_by`, `agent_tier`, `effective_tier` per dataset — PFP `:839`) are
  **additive fields inside the existing `compose_profile` return**, not a new function.
- `dqagent/api/catalog_routes.py` — one route today (`GET /pipelines/{id}/profile`). K2c adds four more routes to
  this SAME file (`pipelines`, `nodes`, `discover`, `wire`) — confirmed this file is BC-owned already (K1 built it
  as BC), consistent with PFP `:1085` ("`api/catalog_routes.py` | — | — | BC | BC | BC | —").
- `dqagent/runtime.py:1517-1618` — `_night_profile_digests` and `_record_profile_digest_degradation`, built in K1
  with an explicit, hard-won decision (K1's D6) that `profile_digests` is passed as an **explicit kwarg only at the
  NIGHT call site**, never inside the shared `_root_args()` (`:548-550`, confirmed still shared by APPLY_CHANGE
  `:420`, PUBLISH_CYCLE `:561`, RECOVER `:1233`, NIGHT `:1592-1593`). **K2b's PIPELINE_RUN root is a brand-new,
  FIFTH call site** (confirmed: zero `tracer.root("PIPELINE_RUN"` call sites exist anywhere in the tree today,
  verified by direct grep of `dqagent/runtime.py` — `PIPELINE_RUN` appears only in `RUN_KINDS`,
  `trace_schema.py:18`). K1's D6 precedent (gate stamping by root kind explicitly, never through the shared helper)
  is the house pattern K2b's new root should follow for whatever K2b itself needs to stamp.
- `tower/src/app/pipelines/[id]/profile/page.tsx`, `lib/types/catalog.ts`, `lib/api/catalog.ts`,
  `lib/mock/routes/profile.ts` — all real, built in K1. K2c's `catalog.ts`/`wire.ts` mock files are **new,
  sibling** files (PFP's own file table reserves `catalog.ts`, `profile.ts`, `backtest.ts` as three separate files,
  `:1111` — `profile.ts` is K1's, confirmed already in the tree at `tower/src/lib/mock/routes/profile.ts`).
- `tower/src/app/pipelines/page.tsx` — **today a flat "Pipeline map"** (plain `<table>` + `PipelineDag` component,
  confirmed by direct read), fed by `GET /api/v1/pipelines` (`app.py:725-727`, confirmed unchanged, calls
  `services.pipelines_view(scope(p))`, `runtime.py:608-630`, confirmed unchanged). **This is PFP's "the old
  endpoint" — distinct from the NEW `GET /catalog/pipelines` the plan's K2c section names.** PFP's own text
  (`:834`) describes a different response shape (`{groups:[{provenance, pipelines:[...]}], order[]}`) from the
  existing `/api/v1/pipelines`'s shape (`{pipelines:[...], order, dependencies}`, confirmed at `runtime.py:627`).
  **K2c replaces `app/pipelines/page.tsx`'s content with the new Catalog TABLE fed by the new
  `GET /catalog/pipelines`, OR adds it as a new route and leaves the old one as-is — this is Decision K2c-1, not
  resolved here** (§4).

### 0.2 K1's genesis-evidence-gate class of bug: does K2's genesis split risk the same defect?

The task brief explicitly asks this to be traced, not assumed clean. K1's closing fix (`governance_schema.py`'s
`gov_decide`/`gov_apply`, confirmed read in full above, `:186-189,239-242`) made `promotion_refusal` take the
change request's own stored `origin` and exempt `GENESIS` **only** — confirmed: `promotion_refusal(p_state,
p_items, p_origin)` returns `NULL` immediately when `p_origin = 'GENESIS'` (`trace_schema.py:495`), and this
exemption applies **only to AUTONOMY items whose tier rank rises** (the function's only loop body, `:496-546`).

PFP's K2a genesis split (`:805-810`) adds **PIPELINE** and **TASK_GATE** items to `seed_from_files`'s genesis list,
all with `origin: GENESIS` (confirmed: `seed_from_files`, `registry.py:147-171`, is the one function
`propose_genesis` calls, `registry.py:757-758`, with `origin="GENESIS"` hard-coded there). Tracing whether this
risks the SAME class of bug K1 fixed:

- K1's bug was specifically about `promotion_refusal`, which only fires for **AUTONOMY** items (confirmed:
  `promotion_refusal`'s `FOR it IN ... CONTINUE WHEN it->>'kind' <> 'AUTONOMY'`, `trace_schema.py:497`). PIPELINE
  and TASK_GATE are **different kinds entirely** — they never enter this function's loop, so this exact code path
  cannot refuse a PIPELINE/TASK_GATE genesis item for missing evidence. **No collision with K1's fix by
  construction.**
- The SEPARATE two-person rule (`gov_item_lower_only`, PFP `:751-755`) governs PIPELINE/TASK_GATE instead.
  PFP's text says PIPELINE always returns FALSE (never lower-only, always needs two) and TASK_GATE returns TRUE
  only under five conjunctive conditions for an *existing* gate (never for a brand-new one). **For genesis itself,
  this is irrelevant**: `gov_propose`'s SQL (`governance_schema.py:132-153`) computes `lower_only` and sets
  `approvals_required` from it, but `write_test_genesis`'s path (`registry.py:760-783`) **bypasses `gov_propose`
  and `gov_decide` entirely** — it INSERTs directly into `config_artifacts` and `registry_events` as the admin
  connection, confirmed by direct read (`registry.py:772-782`, no call to `dq_ctl.gov_propose` or
  `dq_ctl.gov_decide` anywhere in that function). **This is the real, confirmed risk, independent of K1's fix**:
  `write_test_genesis` is the TEST-ONLY path (labelled `TEST-GENESIS` everywhere, confirmed by the module
  docstring, `registry.py:760-762`), explicitly exempt from the two-person rule by design (`_write_test_genesis`'s
  own docstring: "writes the seed as effective and approves its ruleset WITHOUT two people"). The GOVERNED genesis
  path (`propose_genesis` → `gov_propose` → `gov_decide` → `gov_apply`) DOES go through the normal two-person /
  `gov_item_lower_only` machinery, so PIPELINE/TASK_GATE genesis items under a real (non-test) genesis are
  correctly gated by the ordinary mechanism, not a special-cased bypass.
- **The actual new risk K2a introduces, confirmed by direct trace, not present in K1's fixed bug class**: PFP
  `:779-782` requires `_introduce_datasets` to be called from BOTH genesis paths with "the same items" (its own
  stated correctness property, C1, tested by `test_introductions_identical_under_test_and_governed_genesis`). The
  GOVERNED path calls it from inside `gov_apply` (PFP `:780`, "after the artifact loop,
  `governance_schema.py:227-231`" — **this exact line range is now STALE**, see §1a below: the artifact loop in
  the CURRENT `gov_apply` is at `:243-247`, not `:227-231`, because K0/K1's R4-5 evidence-gate code was inserted
  earlier in the function, `:239-242`). The TEST path calls it from `write_test_genesis` on the **admin
  connection** (PFP `:781`, `registry.py:631-636` — also stale, current `_write_test_genesis` body is
  `:768-783`). **Both call sites must pass items in the SAME relative order** (PFP `:774`, "DELETEs before PUTs,"
  processed "in item order") for the C1 identity test to hold — this is a real, non-trivial invariant a seat could
  break by reordering `seed_from_files`'s TASK_GATE items differently from how `_introduce_datasets` iterates them.
  **Flagged as a build-correctness requirement for K2a's seat, not a scope ambiguity**: `_introduce_datasets` must
  be the ONE function both paths call with the IDENTICAL item list `seed_from_files` already produces (no
  re-ordering, no re-filtering at either call site).

**Verdict for the brief's question**: K2's genesis split does **not** hit K1's exact fixed bug (different kind,
different code path — `promotion_refusal` never sees PIPELINE/TASK_GATE). It DOES introduce its own new
correctness-sensitive genesis invariant (C1, the test/governed identity property), which is PFP's own explicitly
named risk (not a surprise this architect is discovering) — the K2a build must get `_introduce_datasets`'s
item-ordering identical across both call sites, and the test PFP already names
(`test_introductions_identical_under_test_and_governed_genesis`) is the correct, sufficient guard for it. No
additional test is recommended beyond PFP's own list.

### 0.3 Technology-selection: confirmed, no new dependency

- Backend: PIPELINE/TASK_GATE are new governed *kinds* (strings + JSON shapes), validated in SQL
  (`gov_item_valid`, already present and extensible, `governance_schema.py:487-525`) and Python
  (`_check_parte_item`, `registry.py:391-430`) — both existing extension points, no new library.
  `dqagent/harness/dbt_runner.py` (confirmed read above) already imports `dbt.cli.main.dbtRunner` — dbt-core is
  **already a project dependency** (confirmed real and installed per the blackboard's K1-close entry: "a real
  Postgres instance... dbt-core not installed... installed directly, closed" — this refers to a different gap,
  the environment's dbt-core install, not a new Python package dependency declared by this codebase; `dbt_runner.py`
  itself was written in a prior package and already assumes dbt is present). K2b's `pipelines/tmsis_outbound/`
  dbt project is new SQL/YAML content under an existing dbt project convention (`pipelines/w1_dbt/` is the
  existing sibling, confirmed via Glob above), not a new dependency.
- Tower: the Catalog stays a plain accessible `<table>` per DEC-020 (confirmed: `tower/src/app/pipelines/page.tsx`
  today already uses a plain `<table>` for its "Gated nodes" section, confirmed by direct read above — the house
  pattern is already in the tree, zero new rendering technology needed). The wiring wizard is a multi-step form,
  the same pattern K1's profile page and other Tower pages already use (`Gate`, `useLoad`, `useAuth`, confirmed
  imports at `pipelines/page.tsx:1-9`). **No `package.json` change required for K2.**

**No technology-selection record is produced** — no capability gap exists.

---

## 1. Re-grounding every PFP file:line citation against the current tree (per wave)

### Wave K2a citations

| PFP cite | Claim | Verdict | Evidence |
|---|---|---|---|
| `:742` "Kind CHECK (`governance_schema.py:27-28`)" | kind CHECK location | **STALE by a few lines, content holds.** Current `kind CHECK` is at `governance_schema.py:27` exactly (`kind text NOT NULL CHECK (kind IN ('OVERLAY','PROFILE',...,'MONITOR'))`), confirmed by direct read — PFP's own `:27-28` straddles a line wrap that today is one logical line at `:27`. Adding `'PIPELINE','TASK_GATE'` to this CHECK list (and the matching `DROP/ADD CONSTRAINT` migration PFP names) is correct and still needed. | `governance_schema.py:27` |
| `:743` "`registry.KINDS` (`registry.py:35-36`)" | KINDS tuple location | **HOLDS exactly.** `KINDS = ("OVERLAY", "PROFILE", ..., "MONITOR")` is at `registry.py:35-36` verbatim. PIPELINE and TASK_GATE must be added here. | `registry.py:35-36` |
| `:744` "`_expected_kind` (`:158-172`)" | function location | **STALE.** Current `_expected_kind` is `registry.py:193-207`, not `:158-172` (the file grew between PFP's authoring and now — K0/K1 added code ahead of this point). Content/shape holds: it is a `rest = artifact_id[len(state)+1:]` dispatch returning a kind string by pattern-matching the artifact-id suffix, exactly as PFP describes. Two new `elif`/regex branches are needed: `pipeline:<id>` → `"PIPELINE"`, `taskgate:<pipeline>:<task>` → `"TASK_GATE"` — these must be added to the SAME function, following the existing `autonomy:`/`entitlement:` regex-branch pattern at `:199-204`. | `registry.py:193-207` |
| `:745` "`BINDINGS` (`elements`) is untouched" | scope boundary | **HOLDS, confirmed by omission**: no PFP text anywhere in P1-3 names `BINDINGS`/`elements` as a kind K2 touches. Nothing in the current `BINDINGS` handling (`_expected_kind`'s `"elements": "BINDINGS"` mapping, `:206`) needs any K2 edit. | `registry.py:206` |
| `:746-750` "`gov_item_valid` (`:432-478`)" | shape-validation function location | **STALE.** Current `gov_item_valid` is `governance_schema.py:487-525`, not `:432-478`. Content holds: it is a per-kind `IF k = ...` dispatch (today: AUTONOMY/AGENT/ENTITLEMENT/CLASSIFICATION/CDE/MONITOR, `:492-521`) that must gain PIPELINE and TASK_GATE branches validating PFP's named shapes (`{adapter, scope{manifest_path}, platform, domains[], layer_map{}, run_on}` and `{task_id, datasets[], mode, correlate, max_wait, on_timeout, release_ceiling, reintroduce[]}` respectively), plus the literal-secret refusal (`token\|password\|secret\|key` key-name regex check — no existing helper does this; new, small, added inline in this same function per the existing per-kind `ok := ok AND ...` pattern, e.g. `:495-499`'s AUTONOMY branch). | `governance_schema.py:487-525` |
| `:750` "`INHERIT` is accepted only when origin is GENESIS, checked in `gov_propose` (`:140`)" | origin check location | **STALE line, correct function.** Current `gov_propose`'s origin check is `governance_schema.py:140-142` (`IF p_origin NOT IN ('HUMAN','PROPOSER','GENESIS')...`) — this is actually the SAME line number PFP cites, confirmed exact. The new work is a NEW check inside this same function (or inside `gov_item_valid`, called from `gov_propose` at `:145`): a TASK_GATE item with `content.release_ceiling = 'INHERIT'` must be refused unless `p_origin = 'GENESIS'`. **Decision needed K2a-1** (§4): where exactly this check lives (inside `gov_item_valid`, which does not currently receive `p_origin` as an argument, or as a new standalone check in `gov_propose` after the existing origin validation) — not resolved here, a real signature-shape choice. | `governance_schema.py:132-153` |
| `:751-755` "`gov_item_lower_only` (`:480-513`)" | lower-only function location | **STALE.** Current `dq_ctl.gov_item_lower_only` is `governance_schema.py:623-672`, not `:480-513`. Content/shape holds exactly: a per-kind `IF k = ...` dispatch (today: FEEDS, POLICY, then AUTONOMY/ENTITLEMENT/AGENT/CLASSIFICATION via a shared "else" fallthrough, `:627-671`) with a final `RETURN true` for every other kind (`:643`, confirmed — this is WHY PIPELINE/TASK_GATE currently fall through to `true`, i.e. always lower-only / one approver, which is the OPPOSITE of PFP's required PIPELINE-always-FALSE and TASK_GATE's five-condition check. **This is the actual code-correctness gap K2a must close**: without this edit, a PIPELINE item would incorrectly need only one approver today.) New branches must be added BEFORE the final `IF k NOT IN (...) THEN RETURN true` fallthrough at `:643`, analogous to the existing FEEDS/POLICY special-cases at `:627-642`. | `governance_schema.py:623-672`, confirmed fallthrough at `:643` |
| `:751` "HARNESS returns FALSE" | — | **HOLDS as a requirement, confirmed currently WRONG in the running code**: HARNESS is not named anywhere in the current `gov_item_lower_only` (confirmed by grep of the function body — no `k = 'HARNESS'` branch), so it currently falls through to `RETURN true` at `:643`, same gap as PIPELINE. PFP's `:752` ("HARNESS returns FALSE") is a REQUIREMENT this package must ALSO implement for HARNESS (not previously enforced), confirmed not a stale citation but a real, currently-unmet requirement predating K2's own PIPELINE/TASK_GATE work. **Flagged for K2a's build**, since HARNESS already carries real content (the two w1 pipelines, `config/harness_w1.yaml`) and a change to it today would incorrectly need only one approver. | `governance_schema.py:623-672` (absence confirmed) |
| `:757-782` ORCH-1: `gated_dataset_introductions`, `_introduce_datasets`, genesis callers | new table/function | **Confirmed absent, pure new-build** (two independent greps of `dqagent/` for `gated_dataset_introductions` and `_introduce_datasets`: zero hits in both `.py` and no occurrence in `governance_schema.py`'s current DDL, confirmed by full read of the file above, lines 1-1341). PFP's own cited call-site lines are stale (`governance_schema.py:227-231` for the `gov_apply` artifact loop — current artifact loop is `:243-247`, confirmed by direct read above; `registry.py:631-636` for `write_test_genesis` — current function body is `registry.py:760-783`, confirmed above). | Confirmed absent; correct current call-site line ranges given |
| `:783-792` `task_gate_ceiling` | new function | **Confirmed absent** (grep: zero hits for `task_gate_ceiling` anywhere in `dqagent/`). New-build. | Confirmed absent |
| `:793-804` `autonomy_tier_for`, `modes.autonomy_for(relation=)`, `evaluate.py:318` | new function + 2 signature changes | **CORRECTED 2026-10-26, post-build: this architect's own design had an internal self-contradiction.** This row originally said "detailed in Wave K2b's table below," but `EXECUTION_PLAN.md:304` explicitly assigns `autonomy_tier_for` (the FUNCTION only, not `modes.autonomy_for`/`evaluate.py`'s signature changes, which stay K2b's — they live in `modes.py`/`evaluate.py`, files K2b owns per the reassignment) to **Wave K2a**. The build seat correctly followed `EXECUTION_PLAN.md` (the authoritative plan this design inherits from) over this document's own unflagged internal disagreement, and built `autonomy_tier_for` in K2a. §3's "Wave K2a" reserved-names section is corrected to include it; §3's "Wave K2b" section (line ~246) is corrected to note the function itself is NOT K2b's to build (already built), only the `modes.py`/`evaluate.py` call-site changes remain K2b's. **Process note**: this architect should have flagged the plan-vs-own-design disagreement explicitly rather than silently splitting one PFP-cited line range (`:793-804`) across two waves without cross-checking `EXECUTION_PLAN.md`'s own wave assignment for the same function. | K2a (function), K2b (call sites) |
| `:805-810` genesis split | `seed_from_files` (`registry.py:112-136`) | **STALE line range, correct function.** Current `seed_from_files` is `registry.py:147-171`, not `:112-136` (confirmed by direct read above — matches PFP's described shape exactly: builds a list of genesis items from `config/state/<ST>/*.yaml` plus `agent_seed`, `cde_defaults.yaml`, `classification_seed`, `MONITOR_DEFAULTS`). PFP's "2 PIPELINE items; 6 TASK_GATE items... HARNESS holding dependencies only" is new content to ADD to this list — **confirmed buildable**: `config/harness_w1.yaml` already has exactly 2 pipelines (`p4_eligibility_ie`, `p6_claims`, confirmed by direct read above) and `dqagent/harness/dbt_runner.py`'s `HOLD_UNTIL_TERMINAL` dict already has exactly 2 entries (`gold_tmsis_elg_subset`, `gold_tmsis_cot_subset`, confirmed by direct read above) — PFP's "two `hold_until_terminal` gates from `dbt_runner.py:15-18`" is **confirmed real, current, and exactly 2** (not stale: `dbt_runner.py:15-18` is exactly where `HOLD_UNTIL_TERMINAL` is defined today). The "four `block` gates for the w1 relations" (PFP `:807`) have **no existing analog in the current harness/governance config** — confirmed by grep: no TASK_GATE-shaped block-mode gate exists anywhere pre-K2 (TASK_GATE doesn't exist as a kind yet). These 4 block gates are genuinely new content this package must author (their `datasets[]` are presumably the 4 w1 bronze relations gated today via `contracts_w1.yaml`'s `gated_nodes`, i.e. `bronze.ie_segment`, `bronze.enrollment_834`, `bronze.encounter_header`, `bronze.encounter_line` — confirmed these 4 are exactly `harness_w1.yaml`'s 4 `gated_nodes` entries across its 2 pipelines). **Decision needed K2a-2** (§4): the exact `datasets[]`/`task_id` naming for these 4 new block gates is not specified anywhere in PFP's prose beyond "the four w1 relations" — a seat needs a concrete naming convention before writing code. | `registry.py:147-171`; `config/harness_w1.yaml:1-16`; `dqagent/harness/dbt_runner.py:15-18` |
| `:811-815` scoped reopen (`runtime.py:310-327`) | structural tuple location | **STALE.** No "structural tuple at `:318`" exists at that exact location today in the sense PFP describes (a 5-tuple of kind names). The actual mechanism is `_apply_change`'s `structural = any(it["kind"] in ("POLICY", "CONTRACTS", "HARNESS", "BINDINGS", "PROFILE") for it in cr["items"])` at `runtime.py:438`, confirmed by direct read above — this IS the "structural tuple" PFP refers to, just not a literal tuple object at a fixed line, and the set (`POLICY, CONTRACTS, HARNESS, BINDINGS, PROFILE`) exactly matches PFP's named 5 kinds. **PFP's instruction holds exactly**: PIPELINE and TASK_GATE must NOT be added to this set (they are correctly left out already, since they don't exist as kinds yet — no code change needed to "leave them out," only to NOT add them when PIPELINE/TASK_GATE are introduced). The NEW `_affected_relations(cr_id)` and scoped `_reopen_ceiling`/`_reopen` calls PFP describes are genuinely new logic: current `_apply_change` (`runtime.py:423-447`) computes `affected` as `{(ch.get("after") or ch.get("before") or {}).get("relation") for ch in cr["impact"]["rules"]}` (`:439`) — this is a RULE-impact-based affected-set, not a dataset-introduction-based one. PFP's `_affected_relations` (datasets whose introduction rows carry this `cr_id`, plus datasets whose introducing gate's ceiling/mode changed) is additional, new logic for TASK_GATE-kind change requests specifically — it does not replace the existing rule-impact affected-set, it is needed IN ADDITION, for the dataset-introduction-table-driven case PFP names at `:815` ("A consumer of datasets that are already gated reopens nothing"). | `runtime.py:423-447` (current location of the mechanism PFP describes) |
| `:816-820` dbt runner / verdicts | `dbt_runner.held_models`, `harness/gate.py`, `gate_checks` | Detailed in Wave K2b's table below. | See K2b table |
| `:821-824` discovery | `adapters/dag/dbt_files.py`, `dag_snapshots` | Detailed in Wave K2b's table below. | See K2b table |
| `:825-828` consumer DAG | `pipelines/tmsis_outbound/`, `_run_night` PIPELINE_RUN span | Detailed in Wave K2b's table below. | See K2b table |
| `:830-845` API + Tower | `catalog_routes.py` additions, Tower Catalog table/wizard | Detailed in Wave K2c's table below. | See K2c table |

### Wave K2b citations

| PFP cite | Claim | Verdict | Evidence |
|---|---|---|---|
| `:793-799` `autonomy_tier_for(p_state, p_agent, p_action_class, p_authority, p_relation)` | new function | **Confirmed absent** (grep: zero hits for `autonomy_tier_for` anywhere). New-build, SQL function, returns `LEAST`-equivalent of `autonomy_tier(...)` (existing, confirmed unchanged at `governance_schema.py:453-458`, exactly 4 args as PFP's `:799` itself notes — "the 4-argument `autonomy_tier`... is NOT changed") and the new `task_gate_ceiling(state, relation)` (also new-build, per PFP `:783-792`), taking the tier-rank-lower of the two per PFP's text. The tier-rank comparison should reuse `dq_ctl.tier_rank` (existing, confirmed `governance_schema.py:415-417`, immutable, already the house pattern for exactly this kind of comparison — e.g. `gov_item_lower_only`'s AUTONOMY branch, `:646-651`). | Confirmed absent; `tier_rank` reuse point identified |
| `:800` "`modes.autonomy_for` (`modes.py:109-117`) gains `relation=None`" | signature change | **STALE line range by exactly the amount expected, content/shape holds.** Current `autonomy_for` is `dqagent/hitl/modes.py:109-117` — **confirmed this is the EXACT line range PFP cites, no drift at all** (one of very few P1-3 citations that is still byte-accurate). Current signature: `def autonomy_for(c, state: str | None, authorities: list[str], action_class: str = "RELEASE") -> dict:`. Adding `relation: str | None = None` and switching its internal SQL call from `dq_ctl.autonomy_tier(%s,%s,%s,%s)` (current, `:114`) to conditionally call `dq_ctl.autonomy_tier_for(%s,%s,%s,%s,%s)` when `relation` is given is the exact, small, confirmed-correct edit PFP describes. | `dqagent/hitl/modes.py:109-117` (EXACT, no drift) |
| `:801` "`effective_mode` (`:144-160`) passes `unit["node"]`" | call-site change | **STALE line range.** Current `effective_mode` is `dqagent/hitl/modes.py:150-166`, not `:144-160` (off by exactly 6 lines — a small, uniform drift, confirmed by direct read: the function signature and its one call to `autonomy_for(c, state, unit["authorities"])` is at `:161` today). The edit is exactly as PFP describes: pass `relation=unit["node"]` as a new keyword at this one call site. | `dqagent/hitl/modes.py:150-166`, call at `:161` |
| `:802` "In `evaluate.py:318`, `auto_cache[ak] = autonomy_for(c, state, list(ak), relation=rel)`" | call-site change | **STALE line, and the surrounding context PFP describes has DRIFTED MORE THAN A LINE NUMBER — this is the exact file the task brief warned about ("evaluate.py changed in T1-traces... AND again in K1").** Current line: `dqagent/engine/evaluate.py:372`, `auto_cache[ak] = autonomy_for(c, state, list(ak))` (confirmed by direct read above, inside `Evaluator.evaluate`'s per-unit loop). **Important nuance PFP's text misses, confirmed by direct read**: `rel` (the relation this evaluation call is FOR) is a stable per-call value — `evaluate(self, batch_id: str, rel: str, ...)` is the enclosing method's own `rel` parameter (`evaluate.py:235`), confirmed unchanged throughout the function body — so PFP's instruction to add `relation=rel` is correct in substance, it is simply a different variable name reference (`rel`, the method parameter, not a new local) and a different current line (`:372`, not `:318`). `auto_cache` itself is confirmed still per-call-scoped (`auto_cache: dict = {}` at `:266`, inside `evaluate`, matching PFP's `:231`-era description of "per call"). | `dqagent/engine/evaluate.py:235,266,372` |
| `:803` "`:327` (`m0`...) is unchanged" | no-change confirmation | **HOLDS as a requirement, STALE line.** Current `m0` computation (the un-ceilinged mode, used only for `ceiling_label`) is at `dqagent/engine/evaluate.py:381-382` (`m0, _ = compute_mode(self.policy, u["authorities"], disps, ..., escalated=escalated, vetoed=was_vetoed(c, uk))` — confirmed this call passes NO `autonomy=` argument at all, so it is unaffected by whatever `auto_cache[ak]` becomes). `ceiling_label` (confirmed unchanged, `dqagent/hitl/modes.py:82-89`) still parses the trace string `"tier: {tier} ceiling for {auth} -> {ceil}"` produced inside `mode()` (`modes.py:68`) — confirmed this string format is UNCHANGED by K2b's edit (the new `relation=` argument changes WHICH ceiling value `autonomy_tier`/`autonomy_tier_for` returns, not the trace string's format), so `ceiling_label`'s parsing continues to work. PFP's `:804` ("The trace line reads `'tier: Lx ceiling for AUTH (task gate <id> introduced <rel>) -> …'`") describes an EXTENDED trace string with new parenthetical content — **this is a proposed CHANGE to `mode()`'s trace-string format** (`modes.py:68`), which `ceiling_label`'s regex-free parsing (`trace.split()` word-position based, `modes.py:87`) must be re-verified not to break. **Decision needed K2b-1** (§4): does K2b actually need to change `mode()`'s trace string format for the demo moment, or is reporting the plain tier/ceiling (without the "(task gate X introduced Y)" parenthetical) sufficient? PFP's text presents this as settled; this architect finds it as an open, non-trivial trace-format change that `ceiling_label`'s word-position parsing must be checked against, not a trivial line-number drift. | `dqagent/engine/evaluate.py:381-382`; `dqagent/hitl/modes.py:68,82-89` |
| `:816-818` `dbt_runner.held_models(cp_dsn, gates)`, `harness/gate.py` `gate_verdict`, `gate_checks` | new/changed function signatures | **`held_models` signature STALE — this is a real behavior gap, not a line-number drift.** Current signature (confirmed by direct read above, `dqagent/harness/dbt_runner.py:21`): `def held_models(cp_dsn: dict) -> dict[str, list[str]]:` — **takes only `cp_dsn`, no `gates` parameter at all**, and its logic reads a hard-coded Python dict `HOLD_UNTIL_TERMINAL` (`:15-18`) rather than any TASK_GATE row from the database. PFP's `:816` signature (`held_models(cp_dsn, gates)`) describes the POST-K2 function, which must additionally accept the TASK_GATE rows (or query them itself) and union the hard-coded `HOLD_UNTIL_TERMINAL` set with whatever `block`/`hold_until_terminal` TASK_GATE rows exist — PFP's own text says this ("reads `block` and `hold_until_terminal` gates, and gives the SAME held set for w1" — i.e. backward-compatible, the w1 behavior must not change, confirmed achievable since `test_dbt_gate.py`'s existing 3 tests, confirmed read in full above, exercise exactly the CURRENT `HOLD_UNTIL_TERMINAL`-only behavior and must keep passing unmodified). `dqagent/harness/gate.py` (`gate_verdict`) is **confirmed completely absent** — no file `dqagent/harness/gate.py` exists (confirmed: `Glob dqagent/harness/*.py` returns only `__init__.py, plan.py, dbt_runner.py`). New-build. `dq_ctl.gate_checks`/`gate_check_transitions` tables are confirmed absent (grep: zero hits for `gate_checks` anywhere in `dqagent/`). | `dqagent/harness/dbt_runner.py:15-26` (current, confirmed real gap vs PFP's target signature); `dqagent/harness/gate.py` confirmed absent |
| `:819` "Writer: new role `dq_discovery_svc`" | new DB role | **Confirmed absent.** `dq_discovery_svc` is not in `SERVICE_ROLES` (`dqagent/cp/schema.py:28-30`, confirmed by direct read — the list has exactly 10 roles, none named `dq_discovery_svc`). This role must be ADDED to `SERVICE_ROLES` (the one list `roles_ddl()` iterates to `CREATE ROLE`, confirmed `:38-48`) — this is a `cp/schema.py` edit, confirmed **not listed in PFP's P1-3 file-ownership row** (`cp/schema.py` has no column at all in PFP's file table, `:1065-1118`, confirmed by direct read of that table — every row names a specific `.py` file, and `cp/schema.py` does not appear as a row). **Decision needed K2a-3** (§4): formal ownership assignment for this one-line `SERVICE_ROLES` edit, exactly the class of gap K1's own Decision #2 resolved for `runtime.py`/`tracer.py` in P1-2 (PFP leaves no owner; the orchestrator must formally assign one, per `EXECUTION_PLAN.md:68`'s own rule). This architect recommends Backend-API-Gov (BC), the seat that owns every other P1-3 file touching governance roles/grants — but this is a recommendation, not a resolution. | `dqagent/cp/schema.py:28-30` (confirmed role list, confirmed `dq_discovery_svc` absent) |
| `:821-824` `adapters/dag/dbt_files.py` `discover`, `dag_snapshots` | new adapter + table | **Confirmed absent, by TWO independent checks.** (1) `dqagent/adapters/dag/__init__.py` exists but is confirmed EMPTY (read directly, "the file exists but the contents are empty" — the module is a bare package marker with zero content). (2) No `dqagent/adapters/dag/dbt_files.py` file exists at all (confirmed by the `Glob dqagent/**/*.py` listing captured above, which shows `dqagent\adapters\dag\__init__.py` as the ONLY file under that directory). `dq_ctl.dag_snapshots` table confirmed absent by grep (zero hits). Pure new-build, for both. The `@seam("dag")` decorator PFP names (`:822`) is confirmed to be a REAL, existing mechanism this new file must use — `dqagent/deploy.py:39-44`'s `seam()` function (confirmed read in full above) is the one real seam-registration decorator in the codebase (currently used by exactly 2 existing call sites: `dqagent/secrets.py:16` and `dqagent/llm/transports.py:93`, confirmed by grep). This is the SAME mechanism K7's `docs/seams.yaml` eventually inventories (per `dqagent/deploy.py:31`'s own comment, "E1 seam manifest") — K2b's `dbt_files.py` becomes the THIRD seam, under a NEW seam id `"dag"` not used by either existing call site. | `dqagent/adapters/dag/__init__.py` confirmed empty; `dqagent/deploy.py:31,39-44` (the real `@seam` mechanism) |
| `:825-828` consumer DAG, `pipelines/tmsis_outbound/`, PIPELINE_RUN span | new dbt project + new root kind | **`pipelines/tmsis_outbound/` confirmed completely absent** (Glob returns zero files; the ONLY existing dbt project in `pipelines/` is `pipelines/w1_dbt/`, confirmed by the full Glob listing captured above — 25 files, all under `w1_dbt/`). Pure new-build: ~6 dbt models over `bronze.ie_segment`/`bronze.encounter_header`'s RELEASED views. **Confirmed buildable against real released-view sources**: `pipelines/w1_dbt/models/sources.yml` (confirmed read in full above) already defines exactly the `dq_rel.ie_segment_released` and `dq_rel.encounter_header_released` sources PFP's consumer DAG would read from — these are REAL, existing release views the new project can declare as `sources:` entries (a NEW `sources.yml` under `tmsis_outbound/`, pointing at the SAME `dq_rel` schema, confirmed to already carry both needed tables). The PIPELINE_RUN span itself (`_run_night`'s "after the drain, each PIPELINE with `run_on: night_end` runs under a PIPELINE_RUN span," PFP `:827`) is new logic in `dqagent/runtime.py`'s `_run_night` (confirmed current drain-pass loop at `runtime.py:1650-1659`, confirmed by direct read above — `drain_passes` loop with per-pass `tracer.span(f"drain[{i}]")`, followed by a `parallel_run` span; the new PIPELINE_RUN root must be added AFTER this loop, as PFP's text says, "after the drain"). This is a NEW `tracer.root("PIPELINE_RUN", ...)` call site — confirmed genuinely new (§0.1, zero existing call sites) — and per K1's D6 precedent (§0.1), it should take whatever stamping it needs as an EXPLICIT kwarg at this one new call site, never by widening `_root_args()`. | `pipelines/w1_dbt/models/sources.yml:1-11`; `dqagent/runtime.py:1650-1659` |

### Wave K2c citations

| PFP cite | Claim | Verdict | Evidence |
|---|---|---|---|
| `:830-837` `catalog_routes.py` table (pipelines, nodes, discover, wire) | new routes | **Confirmed the file exists with exactly ONE route today** (`GET /pipelines/{id}/profile`, confirmed by direct full read above, `catalog_routes.py:1-38`). The four new routes PFP names are confirmed new-build. **One load-bearing finding for K2c, confirmed by direct read**: the existing route's own `catalog_routes(app, services, principal, scope)` function signature (`:22`) already takes `scope` as a parameter — the new routes can reuse this EXACT same signature and the SAME `caller_scope = set(scope(p))` / 403 pattern already proven in the existing route (`:26,31-33`) for any route that needs authority-scoped access (`nodes`, and `wire`'s impact preview). `discover` is explicitly a preview-only, path-confined operation (PFP `:836`, "nothing is written; paths are confined to `pipelines/`") — this needs a NEW, separate check (a path-prefix guard against `manifest_path`, confirmed no existing helper does this anywhere in the codebase; new, small, inline). | `dqagent/api/catalog_routes.py:1-38` (current, one route) |
| `:834` `GET /catalog/pipelines` response shape | new shape | **Confirmed genuinely NEW and DISTINCT from the existing `/api/v1/pipelines`** (§0.1, this finding is cross-referenced here because it is load-bearing for K2c's route design): `services.pipelines_view(scope)` (`runtime.py:608-630`, confirmed unchanged) returns `{pipelines, order, dependencies}`, grouped by nothing. PFP's new shape groups by `provenance: HARNESS \| WIRED` and nests `task_gates`/`evaluation`/`last_night`/`governance` per pipeline — genuinely new aggregation logic, not a wrapper around the existing view. **Decision needed K2c-1** (§4, carried from §0.1): is this a NEW endpoint path (e.g. `GET /catalog/pipelines`, distinct from `GET /api/v1/pipelines`) serving a NEW Tower page, or does it REPLACE the existing `/api/v1/pipelines` + `app/pipelines/page.tsx`? PFP's own route table (`:834`) writes the path as `GET /catalog/pipelines` (no `/api/v1` prefix shown, but every other route in this same table, e.g. `:836-837`, is written the same abbreviated way, and the EXISTING route registration pattern `catalog_routes` uses is `@app.get("/api/v1/catalog/pipelines/{pipeline_id}/profile")`, confirmed with the full `/api/v1` prefix, `catalog_routes.py:24`) — so the new route is almost certainly `GET /api/v1/catalog/pipelines`, a DIFFERENT path from `/api/v1/pipelines`, meaning BOTH endpoints and BOTH Tower pages can coexist without collision. This narrows the decision to: does the Tower NAVIGATION point the "Pipelines" nav item at the OLD page or the NEW Catalog page, and does the old page stay reachable? Not resolved here — a product/UX call, not a technical blocker. | `dqagent/runtime.py:608-630` (existing, confirmed distinct); `dqagent/api/catalog_routes.py:24` (confirmed full-path convention) |
| `:838-839` `effective_tier` via `autonomy_tier_for` | cross-wave dependency | **Confirmed real cross-wave dependency, not an ambiguity**: K2c's `nodes` route and the profile's new `effective_tier` field both depend on K2b's `autonomy_tier_for` SQL function existing first. This is PFP's own stated serial order (K2a → K2b → K2c, confirmed by the plan's own three-wave structure, `EXECUTION_PLAN.md:301-325`) — flagged here only to confirm the dependency direction is correctly one-way (K2c depends on K2b/K2a; K2b/K2a do not depend on K2c). | Cross-reference only; no code gap |
| `:841-845` Tower Catalog table, nodes drawer, wiring wizard, mock | new UI | **Confirmed new-build**, consistent with §0.1's finding that `app/pipelines/[id]/profile/page.tsx` (K1's) stays untouched and K2c's work is additive siblings (`app/pipelines/wire/page.tsx`, confirmed absent today by the Glob listing above — `tower/src/app/pipelines/` has exactly `page.tsx` and the K1-built `[id]/profile/page.tsx`, no `wire/` directory). The "nodes drawer" is new UI inside (or linked from) the Catalog table page, not the existing profile page. | `tower/src/app/pipelines/` (confirmed current contents: `page.tsx`, `[id]/profile/page.tsx` only) |

---

## 2. Blast radius

Per the brief: PFP's list at `:875-882`, plus the T1-traces additions (trace roots, `run_scopes`, `ATTR_VOCAB`).
Every item re-grepped against the current tree; none trusted from PFP's list without independent confirmation.

| # | Object | Dependent(s) (`file:line`) | Test coverage | Verdict |
|---|---|---|---|---|
| B1 | `governance_schema.py`'s kind CHECK, `KINDS` | `registry.py:35-36,193-207`; every `gov_item_valid` caller (`gov_propose`, `:145`) | New PFP tests, `test_pipelines_db.py` | Confirmed, both locations re-grounded (§1) |
| B2 | `gov_item_lower_only` | `gov_propose`'s `lower_only := dq_ctl.gov_items_lower_only(...)` (`governance_schema.py:146`); `classify()`/`lower_only()` Python mirror (`registry.py:226-265`, confirmed this Python function is a DISPLAY-ONLY mirror per its own docstring, `:227`, "the database decides" — it must ALSO gain PIPELINE/TASK_GATE/HARNESS branches to keep the mirror honest, or the Tower's impact-preview display will disagree with what the database actually decides) | PFP's 11 named tests in `test_pipelines_db.py` | **New finding**: `registry.py:226-265`'s Python mirror is an undocumented-by-PFP dependent that must be updated in lockstep or the UI will show a wrong approver-count preview |
| B3 | `_introduce_datasets`, `gated_dataset_introductions` | `gov_apply` (`governance_schema.py:201-275`, confirmed current artifact-loop location `:243-247`); `write_test_genesis`/`_write_test_genesis` (`registry.py:760-783`) | `test_introductions_*` (4 PFP-named tests) | Confirmed, both call sites re-grounded (§0.2, §1) |
| B4 | `task_gate_ceiling`, `autonomy_tier_for` | `modes.autonomy_for` (`modes.py:109-117`); `effective_mode` (`modes.py:150-166`); `Evaluator.evaluate` (`evaluate.py:372`) | `test_pipelines_runtime.py::test_consumer_gate_of_gated_datasets_changes_no_autonomy` (I-10, the package's own named negative control) | Confirmed, all 3 call sites re-grounded (§1, K2b) |
| B5 | `_apply_change`'s structural tuple / affected-relations | `runtime.py:423-447` (confirmed current location); new `_affected_relations(cr_id)` | `test_wiring_consumer_reopens_nothing` (PFP-named) | Confirmed, re-grounded (§1) |
| B6 | `dbt_runner.held_models` | `test_dbt_gate.py` (3 existing tests, confirmed read in full — must keep passing byte-for-byte on the unchanged `HOLD_UNTIL_TERMINAL` w1 behavior); the night-runner's own call to `dbt_runner.run` (confirmed this is a TEST-ONLY helper today — grep shows `dbt_runner.run` called only from `tests/t1/test_dbt_gate.py:33,47,55`, never from `dqagent/runtime.py` or any production code path — **this is itself a finding**: K2's "advisory gate" demo moment needs a REAL production call site invoking the dbt build with the gate applied, which does not exist today; flagged as Decision K2b-2, §4) | `test_dbt_gate.py` (existing + new `::test_advisory_gate_records_would_hold_but_builds`) | Confirmed `dbt_runner.run` has zero production call sites today — new finding, not in PFP's blast-radius list |
| B7 | `pipelines_view` / `/api/v1/pipelines` | `tower/src/app/pipelines/page.tsx` (confirmed unchanged unless K2c-1 is resolved to replace it); `test_api_contract.py::test_pipelines_and_abc_endpoints` (`app.py:725-727`, confirmed asserts `pl["order"] == ["p4_eligibility_ie", "p6_claims"]` — **this exact assertion must stay true after K2**, since K2 adds new PIPELINE items to governance but does not, by design, change `harness_w1.yaml`'s 2 pipelines) | `test_api_contract.py:110-115` (confirmed read, exact assertion content captured above) | Confirmed, assertion content directly verified (PFP's blast-radius list only names the test file, not what it asserts — this architect confirms the specific invariant it pins) |
| B8 | `test_parte_registry.py` | `tests/t0/test_parte_registry.py` (confirmed exists at this path, not `tests/t1/`) | — | **Location correction**: PFP's blast-radius list (`:876`) cites `test_parte_registry.py:36-39` without a directory; confirmed this file is under `tests/t0/`, not `tests/t1/` |
| B9 | `test_parte_export.py` | `tests/t0/test_parte_export.py` (confirmed) | — | Confirmed location, `tests/t0/` |
| B10 | `test_governance*.py` | `tests/t1/test_governance_api.py`, `tests/t1/test_governance.py` (confirmed, both under `t1/`) | — | Confirmed, two files match this glob, both under `t1/` |
| B11 | `test_parte_earn.py` (4-arg tier unchanged) | `tests/t1/test_parte_earn.py` (confirmed exists; K1's close entry already modified this file for the GENESIS-exemption fix, confirmed by the blackboard's K1-CLOSED entry's modified-files list — K2 must not regress that fix) | — | Confirmed via blackboard cross-reference |
| B12 | `test_api_contract.py::test_pipelines_and_abc_endpoints` | See B7 | — | Folded into B7 |
| B13 | `test_cp_invariants` | File confirmed to exist per K0-era blackboard references (not independently re-read in full this pass; PFP's own citation is directory-free, same class of gap as B8/B9) | — | Not independently re-verified this pass — **flagged, not confirmed**, since this architect's read budget prioritized the higher-risk genesis/kind-matrix files; the build seat should re-confirm this file's exact path before relying on it |
| B14 | agent_eval (I-3) | The digest invariant (`observe()` does not read `track_record`, confirmed unchanged by K1's close) | — | Confirmed by K1's own close entry; K2 touches no code `observe()` reads |
| B15 | T1-traces: trace roots | `dqagent/cp/trace_schema.py:18`'s `RUN_KINDS` tuple — **`PIPELINE_RUN` and `BACKTEST` are ALREADY present in this tuple** (confirmed by direct read, `RUN_KINDS = ("NIGHT", "PIPELINE_RUN", "REEVALUATE", "PUBLISH_CYCLE", "DECISION", "APPLY_CHANGE", "BACKTEST", "EXPLAIN", "RECOVER")`) — **no schema edit needed for K2b's new root kind**, confirming K1's own earlier finding (K1_DESIGN.md §2/B4) that the DDL-level support for PIPELINE_RUN was already laid down ahead of need. Only the Python call site (`runtime.py`, after the drain) is new. | — | Confirmed, zero DDL change needed |
| B16 | T1-traces: `run_scopes` | `dqagent/cp/trace_schema.py:25-27`, confirmed unchanged, generic over any root kind (table, not kind-specific) | — | Confirmed, no K2-specific change needed |
| B17 | T1-traces: `ATTR_VOCAB` | `dqagent/cp/trace_schema.py:38-44`, confirmed current 5-key vocabulary (`ingest_state`, `source_mode`, `transport`, `verdict`, `fact_check`) — **K2 needs at least ONE new entry if the new PIPELINE_RUN span or the advisory "would HOLD" check attaches a string attribute naming the verdict** (e.g. a `gate_verdict` attribute with values `PASS`/`HOLD`/`ESCALATE`, mirroring PFP's named `on_timeout` enum `HOLD\|ESCALATE\|PASS_ADVISORY`). **Decision needed K2b-3** (§4): the exact attribute key/value set for the PIPELINE_RUN span's advisory check reporting is not specified by PFP's prose at all (PFP says only "Advisory gates build their task and record would-HOLD," `:820`) — a seat must not invent a free-text string attribute (I5's closed-vocabulary rule, confirmed enforced at the database level by `span_attrs_ok`, `trace_schema.py:88-94`, which REJECTS any string value not in `ATTR_VALUES`) without this architect or the orchestrator first reserving the exact key and value set. | `dqagent/cp/trace_schema.py:38-45` | Confirmed current vocabulary; confirmed this IS a real, enforced requirement (not advisory prose) — `span_attrs_ok`'s SQL CHECK constraint is a hard database-level refusal, confirmed by direct read |
| B18 | `REFS_PREFIXES` | `dqagent/cp/trace_schema.py:31-34` — **`"pipeline"` and `"taskgate"` are ALREADY present** (confirmed: `REFS_PREFIXES = ("batch", "rel", "rule", "unit", "file", "cr", "dec", "pipeline", "taskgate", "abc", ...)`) | — | Confirmed, zero change needed — this was laid down ahead of need, same pattern as B15 |

**Count: 18 blast-radius items (B1-B18).** Three resolve to "confirmed laid down ahead of need, zero change required"
(B15, B16, B18) — themselves material findings, not null results, since they close three potential gaps the brief
explicitly asked this architect to verify rather than assume. Two (B6, B17) surface NEW gaps not named in PFP's own
blast-radius list at all.

---

## 3. Reserved names

Seats must claim names from this list, not invent alternatives. Organized per wave since different seats build each.

### Wave K2a (Backend-API-Gov)

- Kind strings (exact, verbatim from PFP, added to `governance_schema.py`'s kind CHECK and `registry.KINDS`):
  `PIPELINE`, `TASK_GATE`.
- Artifact-id patterns (added to `_expected_kind`, `registry.py:193-207`): `pipeline:<id>` → `PIPELINE`;
  `taskgate:<pipeline>:<task>` → `TASK_GATE` (both confirmed to already be registered `REFS_PREFIXES` values at
  `trace_schema.py:31`, B18 — the artifact-id vocabulary and the trace-ref vocabulary intentionally share the same
  prefixes).
- New table: `dq_ctl.gated_dataset_introductions` — exact columns per PFP `:762-765` (verbatim SQL already given
  in PFP's text; no re-derivation needed).
- New function: `dq_ctl._introduce_datasets(p_state text, p_items jsonb, p_cr text) RETURNS void` — exact signature
  per PFP `:771`.
- New function: `dq_ctl.task_gate_ceiling(p_state text, p_relation text) RETURNS text` — exact signature per PFP
  `:786`.
- New role: `dq_discovery_svc` (added to `SERVICE_ROLES`, `dqagent/cp/schema.py:28-30` — pending Decision K2a-3 on
  ownership of this edit).
- Test file: `tests/t1/test_pipelines_db.py` (new). Test ids, verbatim from PFP `:849-860`:
  `test_lower_only_matrix_task_gate`, `test_harness_two_person_without_python_flag`, `test_pipeline_always_two_person`,
  `test_mode_change_always_two_person`, `test_literal_secret_refused`, `test_inherit_only_at_genesis`,
  `test_introductions_written_only_by_helper`, `test_introductions_append_only`,
  `test_introductions_identical_under_test_and_governed_genesis`, `test_reintroduction_after_introducer_delete`,
  `test_explicit_reintroduce_needs_two`. **This is PFP's full named list of 11 tests** (confirmed by direct count
  of the bullet list at `:849-860`), matching the plan's own "all 11 PFP tests" phrasing (`EXECUTION_PLAN.md:306`).

### Wave K2b (Backend-API-Gov, per PFP §4's reassignment — see §4 Decisions for confirmation this is still accurate)

- **`dq_ctl.autonomy_tier_for` itself is K2a's, not K2b's — corrected 2026-10-26, see the K2a table above.**
  Already built in K2a (confirmed in the running database, exact signature `(p_state text, p_agent text,
  p_action_class text, p_authority text, p_relation text) RETURNS text`, matches PFP `:796`). K2b's job is only
  to CALL it from the two new call sites below; do not re-build the function itself.
- `modes.autonomy_for`'s new keyword: `relation: str | None = None` (added at `dqagent/hitl/modes.py:109`).
- `effective_mode`'s new call-site keyword: `relation=unit["node"]` (added at `dqagent/hitl/modes.py:161`, the
  current — not PFP's stale `:160` — call site).
- `evaluate.py`'s new call-site keyword: `relation=rel` (added at `dqagent/engine/evaluate.py:372`, the current —
  not PFP's stale `:318` — call site).
- New file: `dqagent/harness/gate.py` — function `gate_verdict(dp, gate, run_key)`, exact signature per PFP `:818`.
- New table: `dq_ctl.gate_checks(pipeline_id, task_id, run_key, gate_version, verdict, first_at, last_at, polls)`
  with `UNIQUE` on the first four columns — exact shape per PFP `:819`.
- New table: `dq_ctl.gate_check_transitions` (named, not detailed, by PFP `:819` — shape reserved for the seat to
  derive from the append-only event-log pattern already used throughout this codebase, e.g.
  `dq_ctl.config_cr_events`/`dq_ctl.feed_item_events`'s shape, confirmed the house convention).
  **Decision needed K2b-4** (§4): PFP names this table but gives no column shape at all — a real gap, not a
  stale-line issue; the seat needs a concrete shape before writing DDL.
- New file: `dqagent/adapters/dag/dbt_files.py` — function `discover(manifest_path)`, decorated `@seam("dag")`
  (the seam id `"dag"` is PFP's own literal text, `:822`, reserved verbatim).
- New table: `dq_ctl.dag_snapshots` (named by PFP `:824`, no shape given — same class of gap as
  `gate_check_transitions`; **Decision needed K2b-5**, §4).
- New dbt project: `pipelines/tmsis_outbound/` — ~6 models, `meta.dq_layer: OUTBOUND` (PFP `:826`'s exact literal
  value, reserved verbatim), reading from `pipelines/w1_dbt/models/sources.yml`'s existing
  `dq_rel.ie_segment_released` / `dq_rel.encounter_header_released` sources (confirmed real, §1).
- Test file: `tests/t1/test_pipelines_runtime.py` (new). Test ids, verbatim from PFP `:862-869`:
  `test_consumer_gate_of_gated_datasets_changes_no_autonomy`, `test_composed_harness_equals_yaml`,
  `test_wiring_consumer_reopens_nothing`, `test_genesis_still_needs_two`.
- Additional test, verbatim from PFP `:872`: `tests/t1/test_dbt_gate.py::test_advisory_gate_records_would_hold_but_builds`.
- Additional test, verbatim from PFP `:873`: `tests/t0/test_dbt_files_adapter.py::test_layers_never_inferred_from_folders`
  (new file, confirmed absent today by Glob).
- **ATTR_VOCAB reservation (pending Decision K2b-3, §4, not finalized by this architect alone)**: this architect's
  recommendation, offered as a starting point for the orchestrator's decision, not a unilateral reservation: a new
  key `gate_verdict` with values `("PASS", "HOLD", "ESCALATE")`, mirroring PFP's `on_timeout` enum
  (`HOLD|ESCALATE|PASS_ADVISORY`) minus `PASS_ADVISORY` (which is a TIMEOUT POLICY name, not a verdict value) —
  the actual verdict set a gate reports should instead be `PASS`/`HOLD` (from `gate_verdict`'s own two outcomes per
  PFP `:818`, "correlation LATEST_SEALED, otherwise HOLD") plus however an advisory "would-HOLD" is distinguished
  from a real HOLD. **This needs the orchestrator's decision before K2b's seat writes `gate.py`**, since the
  database-level `span_attrs_ok` CHECK (B17) will hard-refuse ANY value not pre-registered here — getting this
  wrong is not a soft failure, it is a thrown exception from a trace-write path that I-8 requires to never block
  the night.

### Wave K2c (Backend-API-Gov, then Tower)

- Routes (exact paths per PFP's table `:834-837`, following the existing route's full-path convention confirmed
  at `catalog_routes.py:24`): `GET /api/v1/catalog/pipelines`, `GET /api/v1/catalog/pipelines/{id}/nodes`,
  `POST /api/v1/catalog/discover`, `POST /api/v1/catalog/wire`.
- Fixture names (Backend-LLM-Obs, following K1's precedent of recorded fixtures per pipeline-area route, pending
  Decision K2c-2, §4, on whether the recorder mechanism is built or deferred): `p1_catalog.json`, `p1_wire.json`
  (PFP `:845`, reserved verbatim).
- Tower mock route files (per PFP's own file table, `:1111`, "`catalog.ts`... for TP"): `tower/src/lib/mock/routes/catalog.ts`,
  `tower/src/lib/mock/routes/wire.ts` — **note**: PFP's `EXECUTION_PLAN.md:324` phrasing names "mock routes
  `catalog.ts` and `wire.ts`" explicitly (two files, not one), confirmed consistent with PFP's own file table's
  "one file per package, never shared" convention (`:1111`) extended naturally to one file per major route group
  within K2c.
- Tower page: `tower/src/app/pipelines/wire/page.tsx` (new; confirmed absent today, §1).
- Tower type/API files: `tower/src/lib/types/catalog.ts` (already exists from K1 — **additive** edits only, new
  interfaces alongside the existing `PipelineProfile` et al., never a replacement of the file); a NEW file
  `tower/src/lib/api/catalog.ts` is **already named and owned by TP per PFP's table** — confirmed this file
  ALREADY EXISTS from K1 (`tower/src/lib/api/catalog.ts`, built in K1 step 4) — K2c's new route-calling functions
  are ADDITIVE to this existing file, not a new file.

---

## 4. Decisions needed (NOT resolved by this architect — for the orchestrator/user), by wave

### Wave K2a

1. **K2a-1 (signature/call-site for the INHERIT-at-GENESIS-only check).** PFP `:750` says "`INHERIT` is accepted
   only when origin is GENESIS, checked in `gov_propose` (`:140`)." `gov_item_valid` (the function that validates
   shapes, confirmed at `governance_schema.py:487-525`) currently receives `(p_state, p_item)` only — it has no
   `p_origin` parameter. Does this new check live (a) inside `gov_item_valid` with a new third parameter
   (`p_origin`) threaded through from `gov_propose`'s existing call site (`:145`, `PERFORM
   dq_ctl.gov_item_valid(p_state, x) FROM jsonb_array_elements(p_items) x`), or (b) as a separate, new inline check
   directly inside `gov_propose` itself, after its existing origin check (`:140-142`), iterating `p_items` a second
   time? Both are correct in substance; (a) keeps shape validation in one place but widens a function signature
   many future kinds might also need; (b) keeps `gov_item_valid` kind-shape-only but duplicates the
   `jsonb_array_elements` iteration. Not resolved here.
2. **K2a-2 (naming convention for the 4 new w1 block gates).** PFP `:807` says the genesis split needs "four
   `block` gates for the w1 relations first." The 4 w1 relations are confirmed (`bronze.ie_segment`,
   `bronze.enrollment_834`, `bronze.encounter_header`, `bronze.encounter_line`, §1), but no `task_id`/artifact-id
   naming convention is given anywhere in PFP's text for these 4 genesis-seeded gates. A seat cannot write
   `seed_from_files`'s new TASK_GATE items without a concrete `taskgate:<pipeline>:<task>` naming scheme. Not
   resolved here — needs either the orchestrator's decision or a cheap default (e.g. `taskgate:<pipeline_id>:w1_<relation_short_name>`).
3. **K2a-3 (ownership of the one-line `SERVICE_ROLES` edit for `dq_discovery_svc`).** `dqagent/cp/schema.py` has
   no row at all in PFP's P1-3 file-ownership table (confirmed, §1/B-item under K2b's `dq_discovery_svc` finding).
   This architect recommends Backend-API-Gov (the seat owning every other governance-role-adjacent file in P1-3),
   but per `EXECUTION_PLAN.md:68`'s own rule this must be the orchestrator's formal, recorded assignment, not an
   architect's unilateral call — exactly the class of gap K1's Decision #2 resolved for `runtime.py`/`tracer.py`
   in P1-2.

### Wave K2b

4. **K2b-1 (does the trace-string format for `mode()`'s ceiling line actually need to change?).** PFP `:804`
   proposes extending the trace string to `"tier: Lx ceiling for AUTH (task gate <id> introduced <rel>) -> …"`.
   This is a change to `modes.py:68`'s f-string, read by `ceiling_label`'s word-position parsing (`modes.py:87`,
   confirmed `trace.split()`-based, not regex — a parenthetical insertion mid-string could shift word positions and
   silently break `ceiling_label`'s `t.split()[1]` tier extraction if not done carefully). Is this extended trace
   string actually required for K2's demo moment (showing "would HOLD" advisory checks and the effective-tier
   invariant), or is the PLAIN unchanged trace string sufficient, with the task-gate/introduction detail surfaced
   instead through the new `nodes` route's `introduced_by` field (K2c) rather than through the trace string
   itself? This architect recommends NOT changing the trace string (lower blast radius, `ceiling_label` stays
   provably unaffected) but does not resolve it, since PFP's prose presents the extended string as settled design.
5. **K2b-2 (does K2 need a REAL production call site for `dbt_runner.run`, beyond the existing test-only calls?).**
   Confirmed by direct grep (B6): `dbt_runner.run` is called ONLY from `tests/t1/test_dbt_gate.py` today, never
   from any production code path in `dqagent/runtime.py`. K2's own demo moment (§5, "the next night... trace shows
   the PIPELINE_RUN span with would-HOLD checks, nothing excluded") implies the dbt build actually runs as part of
   a real night, gated by the new `harness/gate.py::gate_verdict` logic — but PFP's P1-3 text never explicitly
   says `_run_night` calls `dbt_runner.run` (or an updated `harness/gate.py`-aware equivalent) in production. Is
   wiring a real production call site for the dbt build (vs. continuing to exercise it only through dedicated
   tests, with the PIPELINE_RUN span wrapping some OTHER, lighter-weight verdict-check-only operation) in scope for
   K2b? Not resolved here — this materially changes what the PIPELINE_RUN span's body actually does.
6. **K2b-3 (the exact `ATTR_VOCAB` key/value reservation for the advisory gate-verdict span attribute).** See §3's
   recommendation (`gate_verdict`, values `PASS`/`HOLD`, possibly a third value distinguishing "would-HOLD/advisory"
   from a real block-mode HOLD). This is a hard database-level CHECK constraint (B17) — getting it wrong throws an
   exception from inside a trace-write path I-8 requires to never block the night. Needs the orchestrator's
   decision (or explicit delegation back to this architect, or to the build seat with this architect's
   recommendation as the default) before `harness/gate.py` is written, not after.
7. **K2b-4 (shape of `dq_ctl.gate_check_transitions`).** PFP names this table (`:819`) but gives no column shape.
   This architect's recommended default (the append-only event-log house pattern, e.g. mirroring
   `config_cr_events`'s `(id, fk_to_parent, event, person_id, jti, detail, at)` shape, minus the person/jti columns
   since gate checks are system-written, not person-decided) is offered but not imposed.
8. **K2b-5 (shape of `dq_ctl.dag_snapshots`).** PFP names this table (`:824`) but gives no column shape at all —
   not even a sketch. The seat needs, at minimum, confirmation of what a "snapshot" captures (a manifest digest? a
   discovered-node list? both?) before writing DDL. Not resolved here.

### Wave K2c

9. **K2c-1 (does the new Catalog table REPLACE `app/pipelines/page.tsx`, or coexist as a new page)?** Confirmed
   (§0.1, §1): the new `GET /api/v1/catalog/pipelines` is a genuinely distinct endpoint from the existing
   `GET /api/v1/pipelines`, so there is no technical collision either way. This is a product/navigation decision:
   does the Tower's "Pipelines" nav item point at the new Catalog table (making the old flat map page either
   removed or demoted to a secondary view), or do both coexist as separate nav items? Not resolved here — flagged
   explicitly so the Tower seat does not silently delete K0/K1-era working UI (the existing page's "Global order"
   and "Dependency graph" sections, confirmed real and working) while adding the new one.
10. **K2c-2 (recorded-fixture mechanism: build `tools/record_fixtures.py` now, or keep following K1's
    live-derivation mock pattern?).** This is the SAME fork K1's Decision #1 faced and deferred to K7. Per the
    task brief's own instruction to check whether K1's reasoning still applies rather than copy it blindly: K1
    deferred because "the mocking pattern actually built so far (`routes/obs.ts`) is architecturally different
    (live derivation from mock state, not a static recorded transcript)" and K7's own scope is explicitly "the
    recorder for every route." **This reasoning still applies unchanged to K2c**: `tower/src/lib/mock/routes/profile.ts`
    (K1's own mock file, confirmed existing) still uses live derivation, not a recorded transcript, and
    `tools/record_fixtures.py` still does not exist anywhere in the tree (confirmed, re-grepped this pass,
    zero hits). PFP's own K2c text (`EXECUTION_PLAN.md:318`) names "Backend-LLM-Obs: record `p1_catalog.json` and
    `p1_wire.json`" as an explicit step, same as it did for K1's `p1_profile.json` — and K1 deferred that exact
    instruction. This architect recommends the SAME deferral for K2c, for the SAME reason, with NO new finding
    that would distinguish K2's situation from K1's — but per the brief's explicit instruction not to just copy
    K1's decision, this is surfaced as a decision, not silently carried forward.

**No WAIT is triggered by this design on its own.** Per this package's scope (K2a/K2b/K2c decisions above are all
either genuine prose gaps in PFP's own text (naming conventions, table shapes never specified) or file-ownership
formalities the orchestrator's own process already has a precedent for resolving (K1's Decision #2) — none
contradict PFP's design intent, and none are scope changes. The orchestrator should resolve at minimum K2a-2,
K2a-3, K2b-3, K2b-4, K2b-5 and K2c-2 before dispatching each wave's build seat, since all six leave a seat with no
safe default to guess from.

## 9. Decisions — RESOLVED by the orchestrator (2026-10-26)

Orchestrator independently verified two load-bearing claims before resolving anything: `lower_only()`'s fallthrough
(`registry.py:265`, `return True`) genuinely defaults any unhandled kind — including HARNESS — to lower-only, the
OPPOSITE of PFP's required "HARNESS never counts as lower-only" rule. This is a real, pre-existing gap, not
something K2 introduces, but K2a must close it since K2a is the package actually touching this function next.
Also confirmed `dbt_runner.run` (`harness/dbt_runner.py:29`) genuinely has zero call sites in `dqagent/runtime.py`
— only test files call it today.

1. **K2a-1 (INHERIT-at-GENESIS check location).** RESOLVED: option (a) — add `p_origin` as a new parameter to
   `gov_item_valid`, threaded from `gov_propose`'s existing call site. basis: one shape-validation function is
   easier to keep correct over time than two call-ordered checks across two functions, and the signature widening
   is small (one text parameter) relative to the cost of a future kind needing the same origin-conditional shape
   rule and having to choose between two existing patterns. wrong-if: a future kind needs an origin check that
   ISN'T shape-validation-shaped (e.g. needs to see sibling items in the same batch) — cross that bridge then.
2. **K2a-2 (the 4 genesis-seeded TASK_GATE naming convention) — the highest-leverage decision, resolved precisely.**
   RESOLVED: `taskgate:<pipeline_id>:<relation_short_name>`, using the architect's own proposed default, with
   `relation_short_name` being the part of the relation after its LAST dot (e.g. `bronze.ie_segment` →
   `taskgate:p4_eligibility_ie:ie_segment`). basis: matches this project's existing `<kind>:<scope>:<leaf>` naming
   convention used elsewhere (e.g. `overlay:<rule_id>`, confirmed pattern at `registry.py:162-163`), and the leaf
   name is stable and human-readable for the demo's own "wire a consumer DAG, see it introduced" narrative.
   wrong-if: two different pipelines share an identical relation short name after their last dot — checked directly
   against the real w1 harness entries (`p4_eligibility_ie`: `bronze.ie_segment`, `bronze.enrollment_834`;
   `p6_claims`: `bronze.encounter_header`, `bronze.encounter_line`) — no collision exists today. NOT-verified against
   a future pipeline this package doesn't yet know about; the build seat should assert no collision at seed time,
   not just trust the naming scheme blindly.
3. **K2a-3 (`dq_discovery_svc`/`SERVICE_ROLES` ownership).** RESOLVED: Backend-API-Gov, per the architect's own
   recommendation and consistent with K1's Decision #2 precedent (the seat owning every other governance-role-
   adjacent file in this package's PFP section gets the one-line role-list edit too, rather than splitting a
   one-line change across a seat boundary).
4. **K2a-4, NEW (not in the architect's list, found by the orchestrator while verifying): the `lower_only()`
   HARNESS fallthrough gap.** RESOLVED: Backend-API-Gov adds an explicit `if kind == "HARNESS": return False`
   branch to `registry.py:226-265`'s `lower_only()`, matching the database's own `gov_item_lower_only`'s required
   behavior (PFP's stated rule) — confirmed this is also missing on the database side in `governance_schema.py`'s
   `gov_item_lower_only` function itself (not just the Python mirror); fix BOTH, since the Python function is
   explicitly documented as a *display-only mirror* of the database's real decision (`registry.py:227`'s own
   docstring) — fixing only the mirror would make the Tower's impact preview lie about what the database will
   actually do.
5. **K2b-1 (trace-string format change for `mode()`'s ceiling line).** RESOLVED: do NOT change the trace string.
   basis: the architect's own analysis is correct — `ceiling_label`'s word-position parsing (`modes.py:87`) is a
   real, working mechanism that a parenthetical insertion risks silently breaking, and the task-gate/introduction
   detail the extended string would carry is already available through the new `nodes` route's `introduced_by`
   field (K2c), a lower-blast-radius path to the same information. wrong-if: the demo script specifically needs
   this detail to appear IN the trace explorer's existing text rendering, not just in a separate API field — if so,
   surface it as a NEW, separate span attribute (not a trace-string edit), never resolved by changing a
   word-position-parsed string.
6. **K2b-2 (real production call site for `dbt_runner.run`) — REVERSED after scope review, 2026-10-26.**
   Originally resolved YES (wire a real call site). A scope-review pass, independently verified by the orchestrator
   against the actual mechanism, found the original reasoning was theater, not substance: advisory mode's "nothing
   excluded" claim is true BY CONSTRUCTION, regardless of whether a real dbt build ever runs — confirmed directly
   in `dbt_runner.py:21,31-34` (`held_models` only populates excludes from `block`/`hold_until_terminal` gates;
   advisory gates never enter that list). The design's own golden-path trace (§5 step 7) already admitted the real
   tension: without a real build, "there is nothing for 'nothing is excluded' to observably DEMONSTRATE" — but the
   LOGICAL claim the demo makes was never contingent on a real build executing; only the theatrical "watch it
   happen live" value was. That theatrical value is not worth the real cost: CLAUDE.md's standing invariant
   requires the nightly earn story to work at `--preload-nights 15`, meaning a real dbt build wired into every
   night run would execute 15 real builds on every K6 rehearsal, forever — production-grade execution cost for a
   demo package, directly against CLAUDE.md's "do not gold-plate a demo" rule.
   **RESOLVED (final): do NOT wire a real production call site in K2.** Compute `gate_verdict` from the committed
   dbt manifest/fixture state instead — consistent with the SCRIPTED/RECORDED pattern this codebase already
   establishes elsewhere (DEC-023/R4-3) for exactly this "show the UI working without the full backend" purpose.
   Still write a REAL `gate_checks` row with a real `WOULD_HOLD`/`PASS` verdict, still populate a real PIPELINE_RUN
   span — only the underlying dbt EXECUTION is deferred, not the observable check/trace/UI. Defer the real
   `dbt_runner.run` production wiring to K3, which already handles promoting advisory→block and is the point where
   a real build outcome starts to matter for something the demo actually gates on.
   **Two red-team findings against the original (now-superseded) resolution are recorded here for K3, since
   they'll matter once a real call site is eventually wired**: (a) `dbt_runner.run`'s invocation (`dbt_runner.py:36`,
   `dbtRunner().invoke(args)`) is NOT wrapped by `tracer.root()`'s own fail-safe — that mechanism only protects the
   trace-WRITE path, not code running inside the `with` block (confirmed, `tracer.py:360-372`: an exception from
   the block body propagates, it is not swallowed) — so a real call site must catch dbt-process exceptions
   explicitly and degrade to a recorded verdict, never let them propagate into `_run_night` and crash the whole
   night. (b) `dbt_runner.py:35`'s `os.environ["DQ_DUCKDB_PATH"] = duckdb_path` is process-global mutable state;
   a second pipeline's build running concurrently (or even sequentially without resetting it) is a latent
   concurrency hazard once more than one real pipeline shares this call path. Neither applies to K2 itself (no
   real call site in K2), but both are real, load-bearing findings for whoever wires the real call eventually.
7. **K2b-3 (`ATTR_VOCAB` reservation for the gate-verdict span attribute).** RESOLVED: key `gate_verdict`, values
   `("PASS", "WOULD_HOLD")` — two values, not three. basis: K2 is advisory-only (block-mode promotion is explicitly
   K3's scope, confirmed in EXECUTION_PLAN.md's own K3 description "promoting a gate from advisory to block"), so
   a real `HOLD` value does not exist yet in K2 and reserving it now would let K2-era code accidentally claim a
   capability it doesn't have. `WOULD_HOLD` is the honest, K2-scoped label; `HOLD` gets reserved when K3 actually
   builds block-mode. This must be added to `trace_schema.py:38`'s `ATTR_VOCAB` dict BEFORE `harness/gate.py` is
   written.
   **Correction, after red team, confirmed by the orchestrator directly (`trace_schema.py:88-94`):** the original
   framing ("a wrong value throws from inside an I-8-governed trace-write path") OVERSTATED what the database
   actually enforces. `span_attrs_ok`'s CHECK is `(e.value #>> '{}') = ANY (ARRAY[ATTR_VALUES])` against
   `ATTR_VALUES` — a single FLATTENED UNION across every key's values (`:45`), never checked per-key, despite the
   module's own comment (`:36`, "a CLOSED vocabulary per key") claiming otherwise. This is a genuine, pre-existing
   gap between the documented intent and the actual mechanism — NOT introduced by K2, but K2 is the first design
   to lean on it as a safety property for a new attribute. Practical consequence for this decision: adding
   `gate_verdict: ("PASS", "WOULD_HOLD")` is still SAFE (no value collision with any existing key's values), but a
   future typo like `gate_verdict: "REJECT"` would NOT be caught by the database — `REJECT` is already a valid
   global value via the `verdict` key, so that write would silently succeed. Recorded here as a known, accepted
   limitation; fixing `span_attrs_ok` to be genuinely per-key is real, separate hardening work, correctly NOT
   bundled into K2 (out of scope — this is Pre-existing, affects every ATTR_VOCAB key, not just K2's, and belongs
   with whichever package next touches trace-schema correctness, e.g. K7's parity work).
8. **K2b-4 (`dq_ctl.gate_check_transitions` shape).** RESOLVED: the architect's own recommended default — mirror
   `config_cr_events`'s append-only event-log shape, minus `person_id`/`jti` (system-written, not person-decided):
   `(id, unit_key or batch_id, gate_id, verdict, detail jsonb, at)`. basis: reusing an already-proven, already-
   tested house pattern is lower-risk than inventing a new shape for what is structurally the same kind of object
   (an immutable, timestamped event record).
9. **K2b-5 (`dq_ctl.dag_snapshots` shape) — REVERSED after scope review, 2026-10-26.** Originally resolved to
   persist both a digest and a full node list. A scope-review pass correctly applied K1's own already-established
   precedent (deferring recorded fixtures because the demo's "see it now" moment doesn't need persisted history,
   only live derivation) to this exact situation: K2's "see every DAG" demo moment is POINT-IN-TIME — it needs
   `discover()`'s live preview (already built per §3, reads the committed manifest fresh each time), not a
   persisted snapshot table. **RESOLVED (final): `dag_snapshots` captures ONLY a `manifest_digest` for drift
   detection** (has the DAG changed since it was last discovered — a genuinely forward-looking need, e.g. for K8's
   profile-editing or a future "DAG drifted" alert), with no node list persisted. The Tower's catalog table
   renders nodes live from `discover()` every time it's viewed, exactly as K1's mock-parity precedent already
   established for comparable "render what's true right now" screens.
10. **K2c-1 (Catalog table vs. existing `app/pipelines/page.tsx`).** RESOLVED: coexist, do not replace or remove
    the existing page. basis: the existing page's "Global order" and "Dependency graph" sections are confirmed
    real, working UI (per the architect's own check) — removing working, tested capability to make room for new
    capability is exactly the kind of avoidable regression this project's standing rules warn against. The Tower's
    "Pipelines" nav gets a second entry (e.g. "Catalog") alongside the existing one, not a replacement. K4 (React
    Flow) may later consolidate these; that is K4's decision to make, not K2c's.
11. **K2c-2 (recorded-fixture mechanism).** RESOLVED: same deferral as K1's Decision #1, for the same reason,
    re-confirmed rather than blindly copied — the architect checked and found no new fact that distinguishes K2c's
    situation from K1's (the mock pattern in the tree is still live-derivation; K7's scope is still "the recorder
    for every route"; `tools/record_fixtures.py` still does not exist). Defer to K7.

**Scope note:** none of these 11 decisions change K2's scope, contradict PFP, or require a user WAIT. K2a-4 (the
HARNESS fallthrough fix) is a genuine pre-existing defect this package closes, not new scope invention — the same
category as K1's genesis-evidence-gate fix: found by careful verification, not assumed away.

**Note on §5's golden-path text below (written before the K2b-2/K2b-5 reversals above):** §5 step 7's prose still
describes the demo moment as contingent on "a production call site for `dbt_runner.run`" existing. That
contingency is now resolved differently (fixture/manifest-driven `gate_verdict`, real `gate_checks` row, real
PIPELINE_RUN span, no real dbt execution in K2) — the OBSERVABLE demo moment (trace shows would-HOLD, nothing
excluded) is unchanged and still achievable; only the underlying mechanism producing it changed. Build seats should
read §9's reversed decisions as authoritative over §5's original prose where the two disagree.

---

## 5. Golden-path worked example (the package's own demo moment, both halves)

Walking `EXECUTION_PLAN.md:329-331`'s two demo moments against the actual current tree, flagging every step that
lacks underlying data or a real call site today (not merely restating the plan's prose).

**Demo moment 1: "Wire `tmsis_outbound` in advisory mode. The database refuses a single approver. After two
approvers, the effective tier on the datasets it reads does not move."**

1. A presenter opens the new wiring wizard (`app/pipelines/wire/page.tsx`, confirmed new-build, §1) and enters the
   manifest path for the new `pipelines/tmsis_outbound/` dbt project (confirmed new-build, §1, and confirmed its
   sources — `bronze.ie_segment`'s and `bronze.encounter_header`'s RELEASED views — already exist and are real,
   §1's `sources.yml` finding). `POST /api/v1/catalog/discover` returns a preview (confirmed new-build, §3) —
   **no gap**, the manifest this call reads is produced by a plain `dbt parse` against new, self-contained SQL
   files this package authors; nothing here depends on data that doesn't exist.
2. The wizard's Task gates step defaults to `mode: advisory`, `release_ceiling: L1` (PFP `:844`, confirmed
   consistent with `task_gate_ceiling`'s own fail-closed default, PFP `:792`, "`'L1'` if it is not effective").
3. `POST /api/v1/catalog/wire` with ONE approver's rationale creates a change request whose items include a new
   PIPELINE item and TASK_GATE items. Per K2a's `gov_item_lower_only` matrix (PFP `:751-755`, confirmed as the
   package's own correctness requirement, §1/B2), PIPELINE is NEVER lower-only — `gov_propose`'s existing
   `approvals_required` computation (`governance_schema.py:149`, confirmed unchanged: `CASE WHEN p_loosening OR NOT
   lower_only THEN 2 ELSE 1 END`) therefore stores `2` for this change request automatically, with NO new code
   needed in `gov_propose` itself (only the new `gov_item_lower_only` branch returning FALSE for PIPELINE, §1/B2,
   is the actual new code). A SECOND approval attempt by the SAME single approver is refused by the EXISTING
   `gov_decide` check (`governance_schema.py:166-168`, confirmed unchanged: "the author cannot approve or reject
   their own change") — **this existing mechanism is sufficient for "the database refuses a single approver,"
   confirmed by direct read, no new refusal logic needed for this specific claim**. The demo's "refuses a single
   approver" is really "a SECOND approval from a DIFFERENT person is required before `gov_apply` will apply it" —
   `gov_apply`'s existing approval-count check (`:215-223`, confirmed unchanged) is the actual enforcement point.
4. A second, different person approves. `apply_change` runs (`runtime.py:415-421`, confirmed unchanged mechanism,
   now also calling the new `_introduce_datasets` via `gov_apply`'s artifact loop, per §0.2/§1). The two datasets
   (`bronze.ie_segment`, `bronze.encounter_header`) each get a NEW `gated_dataset_introductions` row — but **only
   if they have no existing row** (PFP `:775`, "if the dataset has no row, insert version 1 with reason `FIRST`").
   **Confirmed critical check, directly relevant to this exact demo moment**: both of these 2 relations are
   ALREADY gated today, by the existing w1 pipelines (`p4_eligibility_ie` gates `bronze.ie_segment`;
   `p6_claims` gates `bronze.encounter_header`, both confirmed in `harness_w1.yaml:1-16`). Per K2a-2's genesis
   split (§1, §4), these SAME 2 relations should ALREADY have a `gated_dataset_introductions` row from the 4
   genesis block-gates (pending K2a-2's naming decision) — **so wiring `tmsis_outbound`'s TASK_GATE items for
   these same 2 relations should hit PFP's "already introduced" case, NOT the "FIRST" case** — confirmed this is
   EXACTLY the scenario PFP's `:815` describes ("A consumer of datasets that are already gated reopens nothing")
   and exactly what `test_consumer_gate_of_gated_datasets_changes_no_autonomy` (I-10) is designed to prove. **This
   demo moment's entire premise depends on K2a-2's genesis-seeded block gates existing and being correctly ordered
   BEFORE the demo wires the new consumer** — if K2a-2 is resolved with the WRONG 4 relations, or if the genesis
   item ORDER puts the 4 block gates after some other TASK_GATE item, this demo moment silently breaks (the new
   consumer would incorrectly become the "FIRST" introducer and WOULD move the effective tier, the opposite of
   what the demo claims). **This is the single highest-leverage correctness dependency in the whole package**,
   confirmed by tracing the actual mechanism, not assumed from PFP's prose.
5. `autonomy_tier_for(state, agent, 'RELEASE', authority, relation)` for `bronze.ie_segment`/`bronze.encounter_header`
   returns the SAME value before and after wiring (since `task_gate_ceiling` for these two relations is unchanged —
   their introducing gate is still one of the 4 original block gates, not the new advisory one). **Confirmed
   logically sound, given step 4's dependency holds.**

**Demo moment 2: "Run the next night. The trace shows the PIPELINE_RUN span with would-HOLD checks, and nothing is
excluded."**

6. `run_night` runs its existing drain-pass loop (`runtime.py:1650-1659`, confirmed unchanged), then (per K2b's new
   logic, §1) opens a NEW `tracer.root("PIPELINE_RUN", ...)` for each PIPELINE with `run_on: night_end` — confirmed
   `tmsis_outbound`'s PIPELINE item would need `run_on: night_end` (PFP `:747`'s named content shape) for this to
   fire at all; the wizard (step 2 above) must set this, or default it, for the demo to show anything.
7. Inside this span, the advisory TASK_GATE's `gate_verdict` is computed (new, `harness/gate.py`, §1/§3) and
   recorded as a `gate_checks` row with `verdict` reflecting "would HOLD" (pending K2b-3's ATTR_VOCAB decision,
   §4, for how this is attached to the SPAN itself, vs. only the `gate_checks` TABLE row — PFP's `:820` says
   "Advisory gates build their task and record would-HOLD," which this architect reads as: the dbt build
   proceeds UNEXCLUDED (advisory never excludes, only `block`/`hold_until_terminal` gates exclude via
   `held_models`, confirmed §1/B6) while the gate_checks row records what WOULD have happened under `block` mode.
   **Confirmed consistent with "nothing excluded"**: advisory mode's entire point, per PFP's own TASK_GATE content
   shape (`:748`, `mode: block|hold_until_terminal|advisory`), is that `held_models`'s exclusion logic (§1/B6) only
   needs to look at `block`/`hold_until_terminal` gates — an advisory gate, by construction, never enters the
   `--exclude` list `dbt_runner.run` builds (`dbt_runner.py:33-34`, confirmed unchanged mechanism). **This demo
   moment depends on K2b-2's resolution** (§4): if no production call site for `dbt_runner.run`/the dbt build
   exists inside `run_night`, there is nothing for "nothing is excluded" to observably demonstrate on a real
   night run — the gate_checks row would exist, but the dbt build itself wouldn't actually run as part of the
   night, undermining the demo's visual/narrative claim ("run the next night... see the trace").

**Net assessment**: Demo moment 1 is achievable, confirmed by direct mechanism trace, contingent on K2a-2's
genesis-ordering decision being made correctly (flagged as the package's highest-leverage dependency). Demo moment
2 is achievable in its DATA/TRACE sense (the gate_checks row, the PIPELINE_RUN span) but its NARRATIVE sense ("see
it run on a real night, nothing excluded") depends on K2b-2's resolution of whether the dbt build actually runs
inside `run_night` — without that, the demo would need to show the gate_checks row and the dbt-build-skip
separately, rather than as one continuous night-run trace.

---

## 6. Technology-selection records

**None required.** Confirmed (§0.3) by direct code inspection: K2 introduces no new third-party dependency in
either the backend (dbt-core already present and used; no new Python package) or Tower (plain `<table>`, existing
multi-step-form pattern, no new npm package). DEC-020's "Catalog as a table, not a graph" framing is confirmed
still current and unsuperseded — K4 (React Flow) is explicitly the FIRST package to add a graph rendering
dependency (`EXECUTION_PLAN.md:349-369`), confirmed not pulled forward into K2 by anything in this package's scope.

---

## 7. Per-seat context manifests

Per PFP §4 (`:1065-1118`) plus this package's K2a/K2b/K2c structure. **Confirmed PFP §4's P1-3 column assignments
are internally consistent** (every P1-3 cell names BC except Tower-owned files, which name TP) — the task brief's
flag that PFP §4 "moves `runtime.py`/`evaluate.py:318`/`modes.py` to BC specifically for K2... unusual" is
confirmed accurate as a textual fact (PFP's table literally shows BC in the P1-3 column for all three files,
`:1067,1069,1071`), but this architect finds it **not actually anomalous once traced**: K0's blackboard-recorded
decision (`"PFP §4 has no row for any K-series package — EXECUTION_PLAN.md §3's seat defaults govern K-package
file ownership, PFP is cited for design content only"`, confirmed verbatim from the K0-CLOSED blackboard entry)
already settled this exact question for every K-package, including K2 — **PFP's BA/BB/BC column letters are a
DIFFERENT seat-naming scheme from this run's actual seats (Backend-Engine/Backend-LLM-Obs/Backend-API-Gov)**,
and the K0 decision says PFP's columns are design-content citations only, not binding ownership for K-packages.
The CURRENT, binding ownership is `EXECUTION_PLAN.md §3`'s own table: Backend-API-Gov (BC) owns `harness/*`
(including `dbt_runner.py`, `gate.py`) and `adapters/dag/*` by DEFAULT already (`EXECUTION_PLAN.md:62`, confirmed
read above), so PFP's P1-3-column "BC" notation for `runtime.py`/`evaluate.py`/`modes.py` is consistent with this
package's OWN specification (`EXECUTION_PLAN.md:308`, "Backend-API-Gov, which owns `runtime.py`, `evaluate.py:318`,
`modes.py` and `harness/*` in P1-3") — **confirmed accurate, not stale, not an error to flag further**; this is a
deliberate, already-adjudicated per-package reassignment (Backend-Engine's usual default ownership of `runtime.py`
is reassigned to Backend-API-Gov FOR K2b ONLY, exactly the class of exception `EXECUTION_PLAN.md §3`'s own
"Ownership rule" paragraph anticipates and the orchestrator must still formally record on the blackboard before
K2b's seat starts, per that same rule's text).

### Wave K2a — Backend-API-Gov

**Reads:** `dqagent/cp/governance_schema.py` (whole file, now read in full by this architect above — the kind
CHECK `:27`, `gov_item_valid` `:487-525`, `gov_item_lower_only` `:623-672`, `gov_propose` `:132-153`, `gov_apply`
`:201-275`); `dqagent/governance/registry.py` (`KINDS` `:35-36`, `_expected_kind` `:193-207`, `seed_from_files`
`:147-171`, `lower_only`/`classify` `:226-290`, `write_test_genesis`/`_write_test_genesis` `:760-783`);
`config/harness_w1.yaml`; `dqagent/harness/dbt_runner.py:15-18` (the 2 existing `HOLD_UNTIL_TERMINAL` entries).

**Owns/writes:** `dqagent/cp/governance_schema.py`, `dqagent/governance/registry.py` (the genesis/kind/lower-only
additions only — NOT `profile.py`, which stays K1's shape plus K2c's additive fields, handled in wave K2c below).

**Must not touch:** `dqagent/runtime.py`, `dqagent/engine/evaluate.py`, `dqagent/hitl/modes.py` (K2b's files, even
though this is the SAME seat across waves — per the package's own three-gate structure, K2a's gate runs before
K2b's seat work starts); `tower/**`.

### Wave K2b — Backend-API-Gov (per the confirmed-accurate reassignment above)

**Reads:** `dqagent/hitl/modes.py` (whole file, `:109-117` `autonomy_for`, `:150-166` `effective_mode`, `:82-89`
`ceiling_label`); `dqagent/engine/evaluate.py:225-400` (the whole `evaluate` method, confirmed read in full above);
`dqagent/runtime.py:415-447` (`_apply_change`), `:548-550` (`_root_args`), `:1517-1659` (K1's `_night_profile_digests`
precedent and the drain-pass loop); `dqagent/harness/dbt_runner.py` (whole file); `dqagent/adapters/dag/__init__.py`
(confirmed empty); `dqagent/deploy.py:39-44` (`@seam`); `pipelines/w1_dbt/models/sources.yml`.

**Owns/writes:** `dqagent/hitl/modes.py`, `dqagent/engine/evaluate.py` (the one call site at `:372`, plus whatever
K2b-1 resolves for the trace string), `dqagent/runtime.py` (the new PIPELINE_RUN call site and `_affected_relations`
logic only — NOT the NIGHT-root `profile_digests` code K1 already built, which stays untouched), `dqagent/harness/gate.py`
(new), `dqagent/harness/dbt_runner.py` (the `held_models` signature change), `dqagent/adapters/dag/dbt_files.py`
(new), `pipelines/tmsis_outbound/**` (new dbt project).

**Must not touch:** `dqagent/cp/governance_schema.py`, `dqagent/governance/registry.py` (K2a's files — K2a's gate
must close first); `dqagent/api/catalog_routes.py` (K2c's); `tower/**`.

**Cross-wave dependency, confirmed one-way (§1)**: K2b's build cannot start until K2a's gate is green (K2b's
`autonomy_tier_for`/`task_gate_ceiling` calls depend on K2a's new table and function existing).

### Wave K2c — Backend-API-Gov, then Tower

**Reads:** `dqagent/api/catalog_routes.py` (whole file, K1's existing route); `dqagent/governance/profile.py`
(whole file, K1's `compose_profile`); `dqagent/runtime.py:608-630` (`pipelines_view`, the OLD endpoint, for
contrast — read-only, never edited by K2c); K2a's new table/functions, K2b's new table/functions (both read-only
dependencies, confirmed one-way).

**Owns/writes (Backend-API-Gov half):** `dqagent/api/catalog_routes.py` (the 4 new routes, additive to K1's
existing file); `dqagent/governance/profile.py` (the additive `introduced_by`/`agent_tier`/`effective_tier` fields
inside the existing `compose_profile` return, per PFP `:839` — NOT a new function).

**Owns/writes (Tower half):** `tower/src/app/pipelines/wire/page.tsx` (new); `tower/src/lib/mock/routes/catalog.ts`,
`tower/src/lib/mock/routes/wire.ts` (new); `tower/src/lib/types/catalog.ts` (additive edit to K1's existing file);
`tower/src/lib/api/catalog.ts` (additive edit to K1's existing file); `tower/src/app/pipelines/page.tsx` (ONLY if
K2c-1 is resolved toward replacing/augmenting it — otherwise untouched, left exactly as K1/earlier packages built
it).

**Must not touch:** `tower/src/lib/types.ts`, `tower/src/lib/api.ts` (reserved to TO/P1-1 per PFP's table `:1109-1110`,
confirmed still the binding convention — no K2-specific reason to violate it); `dqagent/cp/governance_schema.py`,
`dqagent/governance/registry.py`, `dqagent/hitl/modes.py`, `dqagent/engine/evaluate.py` (K2a's/K2b's files,
whose gates must both be closed first).

**Cross-wave dependency, confirmed one-way (§1)**: K2c's `nodes` route and `effective_tier` field depend on K2a's
and K2b's gates both being green first — this is the plan's own stated serial order (`EXECUTION_PLAN.md:301-325`),
confirmed by this architect's trace, not merely restated.

---

## 8. Summary for the orchestrator

- **Stale-reference count by wave**: K2a — 8 stale/re-grounded citations (`:742,744,746,751,783,805,811`, plus the
  confirmed-still-accurate `:750`); K2b — 5 stale/re-grounded citations (`:800`[confirmed NOT stale],`:801,802,803,816`),
  with `:802` (evaluate.py) being the highest-risk drift the task brief specifically warned about, confirmed via
  direct trace rather than assumed; K2c — 2 re-grounded citations (`:834,838`), both confirming genuine new-build
  rather than line-number drift. Total: **15 citations re-grounded**, of which 1 (`:800`) is confirmed exactly
  accurate with zero drift, and the rest range from small (1-6 line) drift to full "describes something that does
  not exist yet, correctly, as new-build."
- **Blast-radius count**: **18 items (B1-B18)**, 3 of which resolve to "confirmed already laid down, zero change
  needed" (B15, B16, B18 — DDL/vocabulary support that predates K2), and 2 of which (B6, B17) are NEW gaps this
  architect found that are not named anywhere in PFP's own blast-radius list.
- **Decisions needed**: 10 total — 3 affecting K2a (K2a-1, K2a-2, K2a-3), 5 affecting K2b (K2b-1 through K2b-5),
  2 affecting K2c (K2c-1, K2c-2). None trigger a WAIT on their own; six (K2a-2, K2a-3, K2b-3, K2b-4, K2b-5, K2c-2)
  leave a build seat with no safe default and should be resolved before that wave's seat is dispatched.
- **Highest-leverage finding, worth restating plainly**: the package's own first demo moment (wiring a consumer in
  advisory mode without moving the effective tier) is correctness-dependent on K2a-2's genesis-seeded block-gate
  decision being resolved BEFORE K2a's build, not after — getting the wrong 4 relations, or the wrong item order,
  would make the demo's OWN central claim ("the effective tier does not move") false in exactly the case the demo
  is built to show working.
