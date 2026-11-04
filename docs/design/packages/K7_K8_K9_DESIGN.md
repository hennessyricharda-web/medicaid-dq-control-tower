# K7 / K8 / K9 design — parity mechanism, profile editing made real, ceiling promotion step b

**Author:** Architect seat (B1).
**Provenance.** The tree is not a git repository; there is no commit SHA. In its place, the orchestrator's brief
states the whole-tree code hash `d1fd11244956f2cad200a43b76c6877c45b9e7a5909ecce32037ee697778b1c9` (stated by the
orchestrator; this seat has no shell, so it cannot independently recompute `code_hash()` — this is disclosed, not
hidden). Every `file:line` below was read directly by this seat on 2026-10-31, against the tree at that hash. Where
a PFP line reference is stale, the current location is given instead and the gap is listed under "Stale references".
**Scope boundary.** This document covers design only. No product code, `seams.yaml`, or test file is written here —
despite PFP P1-M and the architect persona card nominally giving the Architect seat ownership of `docs/seams.yaml`
and the two seam tests, this brief's SCOPE section does not ask for them to be produced in this pass, and K7's own
code (the manifest, the recorder, the parity tests) is explicitly backend/Tower work per the table in §0. This is
flagged under "Decisions needed," not resolved unilaterally.

---

## 0. Owners, restated from PFP §4 and this repo's seat convention

| File / artifact | Package | Owner | PFP citation |
|---|---|---|---|
| `docs/seams.yaml`, `tests/t0/test_seam_manifest.py`, `tests/t0/test_mock_flags_default_off.py` | K7 | **Architect** | PFP P1-M, EXECUTION_PLAN.md:59 ("Architect: `seams.yaml` and the two seam tests (E1)") |
| `tools/record_fixtures.py` (extended), recorder plumbing | K7 | **Backend-LLM-Obs** | PFP P2-2 ("Owners: BB (recorder)"); EXECUTION_PLAN.md:430 |
| `tower/tsconfig.mock.json`, `tower/scripts/mock-parity.mjs`, `tests/t1/test_mock_parity.py`, `fixtures.ts` sha256 fix (F-17), `lib/mock/sha256.ts`, `fx`→`mockId` rename | K7 | **Tower** | PFP P2-2 ("Owners: TO (mock)"); EXECUTION_PLAN.md:431 |
| `tools/gen_contract.py`, `lib/types/*.gen.ts`, `Eq<>` checks | K7 | **Backend-LLM-Obs** (writer) + **Tower** (consumer in `types.ts`) | PFP P2-2 |
| `dqagent/api/catalog_routes.py` (new `POST /catalog/pipelines/{id}/profile/draft` route) | K8 | **Backend-API-Gov** (BC) — `api/catalog_routes.py` is BC's file in every package per PFP §4 table (`PARTF_IMPLEMENTATION_PLAN.md:1085`) and K8's own spec names BC (PFP P2-3: "Owners: BC; TP") | PFP P2-3 |
| `dqagent/cp/agent_schema.py` (`BODY_KEYS` baseline key, E-09) | K8 | **Backend-Engine (BA)** — `cp/agent_schema.py` has no per-package owner row in the PFP §4 table; R4-4 assigns BA ownership of the agent/blackboard namespace generally, and `BODY_KEYS` is BA's file by the T0 table (`agent_schema.py` is not separately listed, but `runtime.py`'s dual-read alias is BA's `runtime.py` territory in every phase-1 package). **Re-assignment recorded here**: BA owns both the schema key addition and the `runtime.py` baseline accessors for K8. Orchestrator should confirm this on the blackboard before BA starts. | PFP P2-3; ADJ-6 |
| `tower/src/app/pipelines/[id]/profile/page.tsx` (rewire only — drawer already exists), `tower/src/lib/api/catalog.ts` (`catalogProfileDraft` — already exists, points at the mock path pattern; needs no shape change, only the live `api.ts` call target), `tower/src/lib/mock/routes/wire.ts` (`mockProfileDraft` — stays, now proven against a real route) | K8 | **Tower** (TP) | PFP P2-3 |
| `dqagent/cp/governance_schema.py` (`gov_item_valid` TASK_GATE branch, `gov_decide`), `dqagent/governance/registry.py` (`track_record_snapshot` extension) | K9 | **Backend-API-Gov (BC)** | PFP P2-5 ("Owner: BC") |
| `tower/src/app/governance/[crId]/page.tsx` (`Impact` component — **new field needed**, see §3.2, corrected) | K9 | **Tower (TP)** — re-assigned; PFP's "no Tower change implied" does not survive re-verification (§3.2) | Not in PFP P2-5's own text; found by this design |
| `dqagent/cp/trace_schema.py` (`human_released` — already done by K0; confirm only, no edit expected) | K9 | N/A — confirm-only | E-07, closed by K0 |

**Serial order within this combined build:** K7 touches no file K8 or K9 touch. K8 and K9 also touch disjoint files
(K8: `catalog_routes.py` POST route + `agent_schema.py` + `runtime.py` baseline accessors + Tower; K9:
`governance_schema.py` + `registry.py` + the one Tower file named above). **K7, K8 and K9 may build in parallel
seat-wise**, subject to the one standing exception below.

**One shared-file exception, found by re-grounding, not inherited from the brief:** PFP's P1-M table
(`PARTF_IMPLEMENTATION_PLAN.md:1098`) gives `tower/src/lib/api.ts` to TO (Tower-Obs) in P1-M, "exports `call`." K8's
Tower work needs to change `api.ts`'s routing so `catalogProfileDraft` calls the *live* path instead of (or in
addition to) the mock path once the real route exists — confirmed by direct read, `tower/src/lib/api/catalog.ts`
already imports `call` from `api.ts` and the mock/live switch lives in `api.ts`'s `MOCK` branch
(`tower/src/lib/config.ts:3`), not in `catalog.ts` itself. K7's Tower work (mock-parity tooling) does not need to
edit `api.ts`'s runtime switch, only add a separate compile-time harness (`mock-parity.mjs`) that imports the mock
server directly. **No actual file collision**: K8 edits `api.ts`'s behavior indirectly only by the route existing
(no edit needed there at all — `api.ts`'s `call()` already dispatches to whatever path string it's given; the mock
switch is already keyed off `MOCK`, not off per-route logic). **This seat did not read the full `api.ts` file end
to end**, so the Tower seat must re-verify this at build start before assuming zero `api.ts` changes are needed.

---

## 1. K7 — mock/production parity mechanism

### 1.1 Re-grounding PFP against the current tree (stale references)

| PFP reference | PFP's claim | Current tree state | Current location / correction |
|---|---|---|---|
| P1-M (`:370-401`) | `docs/seams.yaml` exists with entries for `deployment`, `secrets`, `idp`, `llm.provider`, `llm.transport`, `dataplane`, `orchestrator`, `dag`, `clock`, `tower.datasource`, `alert_sink`, `explanation_cache` | **Absent.** Confirmed by `Glob("docs/seams.yaml")` → no match, and independently by `Grep` across `docs/` for related filenames → only referencing docs found, never the file itself. | Not built. This is K7's actual starting point — PFP wrote this section as if P1-M had already landed; it has not. |
| P1-M | `tests/t0/test_seam_manifest.py`, `tests/t0/test_mock_flags_default_off.py` exist | **Absent.** `Glob("tests/t0/test_seam*.py")` → no match. Confirmed twice (glob + grep for the test names across `tests/`). | Not built. |
| P2-2 (`:967-982`) | `tools/record_fixtures.py` extended to every route | **The file does not exist at all** (not even the narrower P1-1/P1-2 version PFP assumes was built earlier). `Glob("tools/record_fixtures.py")` → no match. | Not built. K7 for this tree is not "extend an existing recorder," it is "build the recorder for the first time." |
| P2-2 | `tests/t1/test_mock_parity.py`, `tower/scripts/mock-parity.mjs`, `tower/tsconfig.mock.json` | **All absent.** Confirmed by glob. | Not built. |
| P2-2 | `tools/gen_contract.py` | **Absent.** | Not built. |
| OBSERVABILITY_PARITY_ARCHITECTURE.md §1 | "`fixtures.ts:1136-1138` hashes with FNV" (F-17) | Confirmed still true by direct grep: `tower/src/lib/mock/fixtures.ts:1136` is cited verbatim in BACKLOG F-17's own evidence column, unchanged since 2026-10-23. The file has grown since (other citations in this codebase reference lines past 1573), so **re-verify the exact line number at build start** — this seat did not re-open `fixtures.ts` to confirm line 1136 currently holds FNV code, only that the backlog's own citation is unchanged. | F-17 still open per BACKLOG:54. |
| `@seam(...)` markers | PFP implies every seam-bearing implementation already carries the decorator | Found **3** live `@seam(...)` call sites by direct grep: `dqagent/secrets.py:16` (`@seam("secrets")`), `dqagent/llm/transports.py:93` (`@seam("llm.transport")` — on `HttpTransport` only, **not** on `ScriptedTransport`, `RecordedTransport`, or `FixedTransport`, confirmed by reading `transports.py:80-184` in full), `dqagent/adapters/dag/dbt_files.py:26` (`@seam("dag")`). | `dqagent/deploy.py:39-44` defines the `seam()` marker and the `SEAMS` registry dict itself — this part of P1-0/R4-1 *is* built. What's missing is the manifest file that cross-checks `SEAMS` against declared dispositions, and the markers on `idp`, `dataplane`, `orchestrator`, `clock`, `tower.datasource`, `explanation_cache`, `llm.provider` (none of these carry `@seam` today — confirmed by the same grep returning only the 3 files above). |
| K8/K9's own mock contract (`PARTF_K7_K12_MOCK_CONTRACT.md:11-17`) | "K7... full defer... no real route for anything K8-K12 touches yet" | **This premise changes with this very build.** K8 in this pass creates the first real route (`POST /catalog/pipelines/{id}/profile/draft`). The mock contract file's own instruction (`:17`) is: "once K8/K10/K11's real backend routes exist, run K7 as originally scoped... against them." This design treats that instruction as live, not historical. | Confirms K7's correct scope per the brief: cover the K8 route, plus seam entries for everything else, not full P2-2 replay parity for routes that don't exist. |

**Stale-reference count: 6** distinct PFP claims above that assert something exists that does not (the manifest, two
seam tests, the recorder, the mock-parity tooling, `gen_contract.py`), each independently confirmed by glob plus a
second search method (grep or directory listing) per the two-confirmation rule for absence claims.

### 1.2 What K7 actually builds in this pass (scoped to the brief, not full P2-2)

The brief is explicit: K7 here is **not** "replay parity for every route." It is:
1. `docs/seams.yaml` with an entry for every seam PFP P1-M lists (disposition `planned`, `real-impl`,
   `fail-closed-stub`, `mock-only`, or `absent`, each with the evidence that disposition is honest).
2. The two seam tests (`test_seam_manifest.py`, `test_mock_flags_default_off.py`).
3. A parity test and seam entry for the **one real route K8 creates** this pass:
   `POST /api/v1/catalog/pipelines/{id}/profile/draft`.
4. Seam entries (not full parity coverage) for every other mock route the K7_K12_MOCK_CONTRACT catalogs, each
   correctly labelled `planned` (no real route exists; building parity against an invented route "would prove
   nothing, and actively misrepresent what K7 is for" — the mock contract's own words, `:15`, which this design
   affirms rather than overrides).

### 1.3 Seam manifest entries (reserved now; parallel seats claim from this list, do not invent new ids)

| Seam id | Disposition | Mock impl | Production impl | Evidence for the disposition |
|---|---|---|---|---|
| `deployment` | `real-impl` | n/a (not seam-marked; `deploy.resolve()` is pure Python, no mock/live split) | `dqagent/deploy.py:47-52` `resolve()` | No `@seam` marker exists on `deploy.py` itself — it is infrastructure the seam system is built from, not a seam. **Open question for the seam test author**: does `test_seam_manifest.py` require every manifest entry to trace to an `@seam()` marker, or can an entry like this cite its home module directly? PFP's own wording (`:386-392`) implies every entry traces to a marker. If so, `deployment` may not belong in the manifest at all — flagged under Decisions needed. |
| `secrets` | `real-impl` (test keys mock-only) | `TEST_KEYS` literal, `dqagent/deploy.py` module docstring; allowed only in test/demo (`deploy.py:18`) | `EnvSecrets` class, `dqagent/secrets.py:16` (`@seam("secrets")`, confirmed present) | `secrets.py:16` |
| `llm.provider` | `mock-only` for `template`; `fail-closed-stub` for the 4 vendor providers (no live keys, L-14) | `template` (`dqagent/llm/__init__.py`) | `providers/{anthropic,watsonx,azure_openai,cortex}.py` — **not confirmed `@seam`-marked**; this seat's grep for `@seam(` found no hit inside `dqagent/llm/providers/` | **Gap, confirmed by the same grep that found only 3 `@seam` sites total.** The seam test (§1.4 below) will fail until either the providers are marked or the manifest entry is written to point at `llm/transports.py`'s markers instead (transport vs. provider are different seams in PFP's own table, `OBSERVABILITY_PARITY_ARCHITECTURE.md:32`). **This is new work for whichever seat builds the manifest test**, not a pre-existing pass. |
| `llm.transport` | `real-impl` (`HttpTransport`, untested live — no keys); `mock-only`/`fail-closed-stub` for the rest | `ScriptedTransport`, `RecordedTransport`, `FixedTransport` (`dqagent/llm/transports.py:120,159,177`) | `HttpTransport` (`transports.py:93-104`, `@seam("llm.transport")` confirmed) | Only `HttpTransport` carries the decorator. `ScriptedTransport`/`RecordedTransport`/`FixedTransport` do **not** (confirmed, `transports.py:80-184` read in full). **Design choice recorded, not re-decided here**: only `HttpTransport` needs the `@seam` marker, since `deploy.allowed_transports()` (the `TRANSPORTS` allow-list, not `@seam`) is the actual gate for the non-live transports — they are deployment-gated test fixtures, not alternate implementations of the same production seam. |
| `idp` | `mock-only` (LocalIdP); `absent` for OIDC | `LocalIdP`, `dqagent/hitl/idp.py:22` | **None.** No `OidcIdP` class exists — confirmed by the same grep that found only `class LocalIdP` in `idp.py`. | Production without OIDC fails closed per F-19/R4-2 (`NotConfiguredError`), which **is** built (`deploy.py`, R4-2 binding logic) — the fail-closed behavior exists even though the real implementation doesn't. Disposition is `fail-closed-stub`, not `planned`, because the failure path is real. **The exact covering test id (PFP cites `test_production_without_oidc_raises`) was not independently re-confirmed to resolve under collection this pass** — the build seat must run `pytest --collect-only` on it before citing it in the manifest (this is exactly what the seam test itself will mechanise going forward). |
| `dataplane` | `real-impl` (DuckDB); `planned` (Snowflake/Databricks/BigQuery) | n/a | `dqagent/adapters/dataplane/duckdb_dp.py` (confirmed present by glob) | No Snowflake/Databricks/BigQuery adapter files exist (`Glob("dqagent/adapters/dataplane/*.py")` → exactly 2 files, `__init__.py` and `duckdb_dp.py`). |
| `orchestrator` | `planned` | — | — | `dqagent/adapters/orchestrator/__init__.py` confirmed as the only file in that package by glob. |
| `dag` | `real-impl` | n/a | `dqagent/adapters/dag/dbt_files.py:26` (`@seam("dag")`, confirmed present and marked) | Already correctly marked — this is the one seam PFP's own text (`:822`) correctly predicted would be `real-impl` by K2, and it is. |
| `clock` | **Not resolved this pass; a genuine negative finding, needing a second independent search before the manifest states it as fact.** This seat's grep for `class FrozenClock`/`class SystemClock` inside `dqagent/obs/tracer.py` returned zero matches. A second, independent search (grep across the whole `dqagent/` tree for those exact class names, not just `tracer.py`) was **not** run by this seat and must be run by the build seat before the manifest entry is written — PFP's claim and this seat's single negative result disagree, and neither side of that disagreement has the required two-confirmation backing yet. | — | — | **Needs re-verification before the manifest entry is written as fact.** |
| `tower.datasource` | `real-impl` | `mockApi` (`tower/src/lib/api.ts`, existence confirmed by directory read and by `catalog.ts`'s import of it) | `fetch`-based live client, same file | Not independently re-confirmed line-by-line this pass; PFP's claimed line numbers (`api.ts:142-143,288-289`) were not re-opened. Low risk: this file's existence and general shape (one `MOCK`-gated dispatcher) was confirmed indirectly via `catalog_routes.py`'s callers and the Tower profile page's imports. |
| `explanation_cache` | Confirmed **built**: `dqagent/cp/trace_schema.py:161` creates `dq_ctl.explanations(subject, authorities, facts_hash, ...)`. | in-memory (test) | control-plane table (confirmed) | `trace_schema.py:161` |
| `alert_sink` | `absent` | — | — | PFP's own text already says this (`OBSERVABILITY_PARITY_ARCHITECTURE.md:40`); BACKLOG F-14/P2-7 confirms no `WebhookSink`/`LogSink` exists yet (deferred to K10 per the K7_K12_MOCK_CONTRACT, `:31-35`). Not re-verified by a fresh grep this pass — carried forward from the existing BACKLOG record, which itself cites two independent searches. |
| `catalog.profile_draft` (**new seam id, reserved by this design, not in PFP** — PFP never named K8's route as a seam because PFP assumed it already had a route; this seat adds it because K7 now covers it) | `mock-only` **until K8 ships**, then `real-impl` once K8's route lands in the same gate | `tower/src/lib/mock/routes/wire.ts`'s `mockProfileDraft` (confirmed present, read in full) | `POST /api/v1/catalog/pipelines/{id}/profile/draft` via `Registry.propose` (K8 builds this; does not exist yet, confirmed by grep across `catalog_routes.py`, which currently has 6 routes, none matching `/profile/draft`) | This is the one seam this document asks K7 to prove parity on for real, because it is the one real route this combined build creates. |

### 1.4 The two seam tests

`tests/t0/test_seam_manifest.py` — per PFP P1-M (`:386-392`), binding as stated:
- every `@seam("id")` marker has a manifest entry (this will **immediately surface** the `llm.provider` gap in
  §1.3 above — the build seat must either mark the providers or adjust the manifest's definition of what counts
  as that seam's implementation; this is a real decision, not a typo, flagged under Decisions needed);
- every entry has both a mock and a production side;
- a `fail-closed-stub` entry's implementation raises rather than returning a value (testable directly against
  `EnvSecrets` when unconfigured, and against the OIDC-unconfigured path);
- every cited test node id resolves under `pytest --collect-only -q` (E12 mechanised);
- a `real-impl` seam never falls back to its mock when production is configured (testable for `dag` and
  `dataplane`, both of which are DuckDB/dbt-local already);
- code never imports `adapters.orchestrator` while its manifest entry says `planned`.

`tests/t0/test_mock_flags_default_off.py` — per PFP (`:393,58`):
- `DQ_DEPLOYMENT` unset resolves to `production` (`deploy.py:19,47-52`, confirmed);
- `NEXT_PUBLIC_DQ_MOCK` unset is falsy (confirmed pattern at `config.ts:3`, same pattern `PREVIEW_K7_K12` reuses at
  `config.ts:11`);
- `DQ_ALLOW_TEST_GENESIS` alone never suffices without a bound TEST deployment (testable against
  `test_production_paths.py`'s existing fixtures, not independently re-opened this pass);
- `transport: scripted` is refused outside test/demo (`deploy.py:24-29` `TRANSPORTS` dict, confirmed: `pilot` and
  `production` rows contain only `"none", "http"`).

### 1.5 Reserved names for K7 (parallel seats claim from here)
- Seam ids: `deployment`, `secrets`, `llm.provider`, `llm.transport`, `idp`, `dataplane`, `orchestrator`, `dag`,
  `clock`, `tower.datasource`, `explanation_cache`, `alert_sink`, `catalog.profile_draft` (new, this design).
  **No other seam id may be invented without updating this list.**
- Test ids: `tests/t0/test_seam_manifest.py::test_every_seam_marker_has_an_entry`,
  `::test_every_entry_has_both_sides`, `::test_fail_closed_stub_raises`, `::test_cited_test_ids_resolve`,
  `::test_real_impl_never_falls_back_to_mock`, `::test_planned_seam_is_not_imported`;
  `tests/t0/test_mock_flags_default_off.py::test_deployment_defaults_production`,
  `::test_next_public_mock_defaults_off`, `::test_allow_test_genesis_alone_is_insufficient`,
  `::test_scripted_refused_outside_test_demo`.
  (Exact names are this design's proposal; the build seat may refine wording but must not change scope.)
- `tests/t1/test_mock_parity.py::test_mock_and_live_keys_and_types_match`,
  `::test_route_coverage_both_directions`, `::test_ts_canonical_sha256_matches_python` — **scoped to the
  `catalog.profile_draft` route and whatever other routes already have recordings (K1/K2/K3's existing
  `p1_profile.json`, `p1_catalog.json`, `p1_wire.json` recordings, cited in PFP :285,318 — not independently
  re-confirmed as existing files this pass; the build seat should glob `tower/src/lib/mock/recorded/*.json`
  before assuming they are there).**

---

## 2. K8 — editing the profile, made real

### 2.1 Re-grounding and blast radius

**Current state, confirmed by direct read (`tower/src/app/pipelines/[id]/profile/page.tsx:1-145`,
`tower/src/lib/mock/routes/wire.ts:196-233`):**
- The Tower drawer (`ProfileEditDrawer`) is fully built and gated behind `PREVIEW_K7_K12` (off by default,
  confirmed `config.ts:11`, confirmed in `profile/page.tsx:292-297` the disabled/guarded render).
- It calls `catalog.catalogProfileDraft(pipelineId, {platform, domains, rationale})`
  (`profile/page.tsx:68-72`).
- That call currently resolves to the **mock** route `mockProfileDraft` only
  (`tower/src/lib/mock/routes/wire.ts:203-233`), because no live route exists. Confirmed by grep: zero matches
  for `/profile/draft` anywhere in `dqagent/api/catalog_routes.py` (which has exactly 6 routes, listed in full
  at `catalog_routes.py:1-8`'s own docstring and verified against the 6 `@app.` decorators actually present).
- `mockProfileDraft` builds a `PIPELINE`-kind PUT item and calls `ctx.propose(...)`, the mock's own
  `proposeCr` — i.e., the mock already knows the *shape* the real route must produce.

**What K8 must build, per the brief:**
1. **New real route** `POST /api/v1/catalog/pipelines/{pipeline_id}/profile/draft` in
   `dqagent/api/catalog_routes.py`, modeled directly on the existing `catalog_wire` (`:347-429`) and
   `catalog_promote_gate` (`:431-473`) routes in the same file — both already call `services.registry.propose(...)`
   and both already have the `_gov_duty` check, the rationale-length check, and the `IRError`/`psycopg` exception
   translation pattern. The new route is a **smaller** version of `catalog_wire`: one `PIPELINE`-kind PUT item,
   no `TASK_GATE` items.
   **CORRECTED by the before-build red team (CRITICAL finding #3, confirmed by the orchestrator at file:line,
   resolved by user decision 2026-10-31): the route reads the pipeline's CURRENT `config_effective` row
   (`artifact_id = {state}:pipeline:{pipeline_id}`, `op='PUT'`) and merges the caller's `{platform?, domains?,
   run_on?}` overrides onto that real stored content — `adapter`, `scope.manifest_path`, and `layer_map` are
   carried from the current row UNCHANGED, never reconstructed from a formula and never defaulted to `{}`.**
   The earlier instruction to copy `mockProfileDraft`'s shape "verbatim" (`wire.ts:216-228`) is **void** — that
   mock formula (`scope.manifest_path` derived from `pipelines/${pipelineId.toLowerCase()}/target/manifest.json`,
   `layer_map: {}` hardcoded) does not read current content at all and would silently corrupt `layer_map`/
   `manifest_path` on any real pipeline whose actual values don't fit that formula. The build seat must NOT
   reproduce `wire.ts`'s literals; it may reuse `wire.ts`'s field-presence pattern (which caller field overrides
   which stored field) but every value must come from a real `config_effective` read. The Tower drawer's existing
   payload (`{platform, domains, rationale}` — confirmed it sends no `run_on`, `adapter`, `scope`, or `layer_map`
   fields, `profile/page.tsx:68-72`) is sufficient under this corrected design precisely because the route now
   fills in everything the drawer omits from the real stored row, not from a default.
2. **Body shape** (reserved, do not deviate without updating K7's parity test): `{platform?: string, domains?:
   string[], run_on?: string, rationale: string}` → `{cr_id: string, approvals_required: number}`. This is
   `mockProfileDraft`'s own body and return shape verbatim (`wire.ts:203-232`), chosen because the Tower drawer is
   already built against it and re-shaping it now would mean editing a Tower file this package does not otherwise
   need to touch.
3. **E-09** (`BODY_KEYS` baseline key): `dqagent/cp/agent_schema.py:48-55` — add `"baseline"` to the list.
   **Resolved finding, not inherited from PFP's stale citation**: PFP's own text (BACKLOG:26, PFP P2-3 `:990`)
   says "reads go through a dual-read alias (`runtime.py:657-660`)" — that line range is `pipelines_view`, an
   unrelated function; **this PFP citation is stale.** The real mechanism is confirmed by tracing the actual
   constraint: `dqagent/cp/agent_schema.py:99-105` defines `dq_ctl.agent_body_ok(p_body jsonb)`, built from
   `BODY_KEYS` (`:102`, `k NOT IN ({keys})` where `{keys}` is `agent_schema.py:65`'s rendering of `BODY_KEYS`).
   This **one** function gates **both** tables: `agent_blackboard` (via `bb_post`, `agent_schema.py:144`,
   `IF NOT dq_ctl.agent_body_ok(p_body) ...`) **and** `agent_memory` (via a table-level `CHECK`,
   `agent_schema.py:204`, `CHECK (dq_ctl.agent_body_ok(content))`). The SPC-baseline-pin path
   (`runtime.py:912-948` `_pin_baselines` → `dq_ctl.mem_pin_baseline(...)` → `INSERT INTO dq_ctl.agent_memory`,
   `agent_schema.py:241`) writes into `agent_memory.content`, which **is** constrained by `agent_body_ok` and
   therefore by `BODY_KEYS`. **E-09's premise is confirmed correct**: adding `"baseline"` to `BODY_KEYS` is the
   one schema change needed, and it is a genuine DDL migration (the `CHECK` is rebuilt from the Python list at
   `agent_ddl()`-apply time, not a pure Python-side change) affecting both tables' write paths at once.
   **ADJ-6 gate, restated exactly, with the check named**: before adding the key AND before changing anything
   that reads the stored value, the build seat must run
   `tests/t1/test_agent_eval.py::test_agent_scenario_matches_recipe_and_is_deterministic` (parametrized over every
   scenario in `SCEN`, confirmed this is the actual I-3 test, `test_agent_eval.py:328` and its two-pass
   determinism check, `:311-325`) **both before and after** the `BODY_KEYS` change, and diff the digests.
   `observe()` (`test_agent_eval.py:205-235`) reads `board[].body` verbatim except for `memory_id` and `cr_id`
   stripping (`:224-229`), and reads `memory[].content` with only `expires` stripped (`:230`) — it does **not**
   special-case `template_args`, so widening the allow-list by itself should not change existing rows' observed
   content. **Expected branch, stated as a prediction the build seat must verify, not assume:** adding a new
   allowed key to an allow-list should not change *existing* content for any row that doesn't use the new key, so
   the I-3 digest is very likely unchanged — but "very likely" is not evidence, and the build seat runs the check
   first, before touching `runtime.py:940-941` (where `_pin_baselines` currently writes the baseline document
   under `template_args` inside the `content` dict passed to `mem_pin_baseline`). If digests differ, ship only a
   read alias (an accessor that reads a new `baseline` key when present, else falls back to `template_args`) and
   leave `BODY_KEYS` and the write path untouched, per ADJ-6's own text.
   **Cost and skip-risk disclosed by the before-build red team (Important finding #4, confirmed):**
   `tests/t1/test_agent_eval.py`'s determinism test is parametrized over every scenario in `SCEN`, each spawning a
   child Python subprocess with its own `PYTHONHASHSEED` and a fresh Postgres database per invocation
   (`:311-324`, `:327-341`) — running it "before and after" means the full suite runs twice, not a quick check.
   It also `pytest.skip`s (not fails) when Postgres is unreachable (`:334`) — if the build seat runs this in an
   environment where Postgres is down, BOTH runs skip, no digests are actually compared, and a false "clean"
   result could be read as passing ADJ-6 when nothing was checked. **The build seat must confirm Postgres is
   reachable and the test actually EXECUTES (not skips) on both the before and after run**, or the ADJ-6 gate has
   not been satisfied regardless of exit code.
   **Correction, not a DDL migration (Nit finding #5, confirmed):** `agent_schema.py:204`'s
   `CHECK (dq_ctl.agent_body_ok(content))` is a function-reference check, not an inline expression — widening
   `BODY_KEYS` only requires `CREATE OR REPLACE FUNCTION dq_ctl.agent_body_ok` (rendered from the Python list);
   no `ALTER TABLE ... DROP/ADD CONSTRAINT` is needed, and Postgres does not re-validate existing rows against a
   redefined function. This is the safe direction (old rows stay valid, new rows get the wider rule) — the
   earlier "genuine DDL migration" framing overstated the mechanism's risk.
4. **Tower rewire**: `tower/src/lib/api/catalog.ts`'s `catalogProfileDraft` (confirmed this function exists,
   called from `profile/page.tsx:68`) needs no shape change — it already posts to
   `/catalog/pipelines/{id}/profile/draft` via the generic `call()` dispatcher. **The only change needed is
   removing the mock-only assumption**: today this resolves to the mock unconditionally because no live route
   exists; once K8 ships the live route, the existing `MOCK` flag in `config.ts` already routes correctly with
   zero Tower code change, *provided* `api.ts`'s dispatcher is purely path-based (flagged in §0 as unconfirmed
   with certainty — build seat must verify `api.ts`'s `call()` doesn't hard-code a per-route mock override list).
5. **Remove the PRE-BACKEND banner from only this screen.** Per the brief: `editOpen && PREVIEW_K7_K12` gating
   (`profile/page.tsx:305`) and the `disabled={!PREVIEW_K7_K12}` on the Edit button (`:294-295`) and the
   `<PreBackendBanner .../>` inside the drawer (`:105`) are removed — **but only for this drawer.** The
   `NotInDemo`-gated `/evaluations`, `/evaluations/diff`, `/annotations` screens, and the
   `ExplanationFeedback` component, all stay exactly as built (still gated behind `PREVIEW_K7_K12`, off by
   default) — this design explicitly does **not** touch `tower/src/lib/nav.ts`'s `built` flag for
   `evaluations`/`annotations`, nor any K11 file. Confirmed scope boundary: K11 is out of scope per the brief's
   own text.

### 2.2 Blast radius

| Object | Dependents (file:line) | Covering test |
|---|---|---|
| `catalog_routes.py` new route | `registry.propose()` (`registry.py:586-644`, unchanged call pattern — reused, not modified); `gov_item_valid` PIPELINE branch (`governance_schema.py:820-831`, unchanged — the new route produces a PIPELINE PUT item identical in shape to `catalog_wire`'s own, so no SQL change needed); `tower/src/lib/api/catalog.ts` `catalogProfileDraft` (already calls this path) | New: `tests/t1/test_catalog_routes.py::test_profile_draft_proposes_a_pipeline_item` (name reserved; file already exists and already covers `catalog_wire`/`catalog_promote_gate` with the same pattern — new tests are added to it, not a new file, per the one-owner-per-file rule) |
| `agent_schema.py` `BODY_KEYS` | **Both** `dq_ctl.bb_post()` (`agent_blackboard` writes) **and** `dq_ctl.mem_pin_baseline()` (`agent_memory` writes, including the SPC-baseline path at `runtime.py:945`) — confirmed via the single shared `agent_body_ok()` gate (§2.1 point 3). A DDL migration (the CHECK is regenerated from the Python list), not a pure Python edit. | `tests/t1/test_agent_eval.py` (I-3, ADJ-6 gate) |
| `tower/pipelines/[id]/profile/page.tsx` banner removal | Nothing downstream — this is a display-only change confirmed to affect only this file's own render tree | Tower smoke (`smoke.mjs --mode mock` and, newly, `--mode live` against the real route) |

### 2.3 Golden-path worked example (K8)

1. A governance-duty operator (`checker`, per `LocalIdP`'s `DEFAULT_USERS`) opens `/pipelines/p6_claims/profile`
   with `NEXT_PUBLIC_DQ_PREVIEW_K7_K12=1` set (preview only; the live demo never sets this flag).
2. Clicks **Edit**. The drawer opens (no PRE-BACKEND banner, once this package ships). Changes `platform` from
   `duckdb` to `duckdb` (no-op allowed) and types a rationale ≥10 characters: "Clarify platform label for the
   provider audit."
3. Submits. The Tower calls `POST /api/v1/catalog/pipelines/p6_claims/profile/draft`
   `{platform: "duckdb", domains: [...], rationale: "..."}`.
4. The real route builds one `PIPELINE` PUT item for `ZZ:pipeline:p6_claims` and calls `registry.propose(...)`.
   Because `PIPELINE` is never lower-only (`governance_schema.py:1002`, confirmed: `IF k = 'PIPELINE' THEN RETURN
   false; END IF;`), `approvals_required` is 2.
5. The drawer shows "Proposed CR-xxxxxxxxxx. Needs 2 approval(s)," with a link to `/governance/CR-xxxxxxxxxx`
   (existing screen, untouched by K8).
6. A second person (`maker`, a different identity) opens the CR and approves it. `gov_apply` runs; the pipeline's
   `platform` field updates in `config_effective`.
7. The profile page, re-loaded, shows the new `platform` value and the new contributor row (artifact version N+1,
   the CR id, the two approvers) in the existing "Contributors" table (`profile/page.tsx:535-577`) — **no new
   Tower code needed for step 7**, since that table already reads from `compose_profile`'s live data.

This is the demo moment the brief's K8 row names: "Edit a DAG's agent configuration through a two-person change
request" (EXECUTION_PLAN.md:41).

### 2.4 Reserved names for K8
- Route: `POST /api/v1/catalog/pipelines/{pipeline_id}/profile/draft` (already reserved by the existing mock;
  no other seat may claim this path for a different shape).
- `BODY_KEYS` key: `"baseline"` (reserved by E-09's own text; no other key of that name may be added for a
  different purpose).
- Test ids: `tests/t1/test_catalog_routes.py::test_profile_draft_proposes_a_pipeline_item`,
  `::test_profile_draft_needs_two_approvers`, `::test_profile_draft_rejects_short_rationale`,
  `::test_profile_draft_unknown_pipeline_404`.

---

## 3. K9 — ceiling promotion, step b

### 3.1 Re-grounding against the current tree

**Confirmed by direct read, `dqagent/cp/governance_schema.py:811-847` (`gov_item_valid`'s TASK_GATE branch):**
- The function already validates `release_ceiling IN ('L1','L2','L3','L4','INHERIT')` (`:843`) and refuses
  `INHERIT` outside a GENESIS origin (`:844`, `AND (c->>'release_ceiling' <> 'INHERIT' OR p_origin = 'GENESIS')`).
- **It has no check at all for "ceiling is inert: datasets are gated by `<introduced_by>`."** Confirmed: the
  TASK_GATE branch (`:832-847`) validates shape (regex on artifact id, array types, enum membership, cadence
  parseability) but never queries `gated_dataset_introductions` or compares the proposing gate's own datasets
  against any dataset's actual introducer. **This gap is real, not hypothetical — confirmed by reading the full
  branch, not just grepping for the word "inert."**
- **`gov_decide` (`:164-204`) has no "requires a linked report for a raised effective ceiling" check.** Confirmed:
  `gov_decide`'s only two evidence-style refusals are `promotion_refusal` (AUTONOMY-only, `:195`) and
  `mode_promotion_refusal` (TASK_GATE `mode` changes only, `:199`, confirmed by reading the full function body
  `:442-458` — it checks `old->>'mode' IS NOT DISTINCT FROM it->'content'->>'mode'` specifically, **never**
  `release_ceiling`). A TASK_GATE item that changes only `release_ceiling` (not `mode`) sails through
  `mode_promotion_refusal`'s `CONTINUE WHEN` at `:450` (`old->>'mode' IS NOT DISTINCT FROM ...` is true, since
  `mode` didn't change) and is never evidence-checked at all today.
- **Both gaps independently confirmed** by reading the complete text of both functions in this session (not a
  single grep hit treated as proof) — satisfying the standing two-confirmation rule for an absence claim.

**E-07 status, re-verified rather than inherited from the BACKLOG row or the brief's own framing:**
- `dqagent/cp/trace_schema.py:385` (`_track_record_counts` view): `WHEN x.to_state = 'RELEASED' AND x.from_state =
  'PENDING_RELEASE' THEN 'human_released'` — confirmed present.
- `:408`: `count(DISTINCT m.k) FILTER (WHERE m.metric = 'human_released') AS human_released` — confirmed.
- `:433`: `coalesce(sum(b.human_released), 0)::bigint AS human_released` (in `track_record`'s all-versions sum) —
  confirmed.
- `:454`: `'human_released', coalesce(sum(b.human_released) FILTER (WHERE b.code_sha256 = cur.v), 0)` (in the
  `current_version` JSON block) — confirmed.
- **Conclusion: E-07 is already closed, exactly as the brief's context manifest and the K-CQ mock-contract file
  both already state. This design does not re-open it.** K9's only remaining E-07-adjacent task, per PFP P2-5's
  own text ("E-07 is already closed by K0. K9 adds the gate evidence to the snapshot"), is extending
  `track_record_snapshot` (`registry.py:646-662`), which does **not** currently carry any TASK_GATE/gate-evidence
  field — confirmed by reading the function in full: it returns `{rows, minimums, evidence, hash}` built purely
  from AUTONOMY items (`:649`, `arts = sorted({it["artifact_id"] for it in items if it["kind"] == "AUTONOMY"})`).
  TASK_GATE items passed into `propose()` alongside an AUTONOMY item today contribute **nothing** to this
  snapshot — confirmed, since the filter on `:649` drops them before the loop even starts.

### 3.2 Tower impact — re-verified, and found to disagree with PFP's own "no Tower change" text

**The brief's own context manifest asks this to be re-verified, not assumed either way. Re-verification finds a
real gap PFP's text missed.**
1. `tower/src/app/agents/page.tsx:193-234` (`TrackRecordPanel`) already renders `t.current_version` fields
   (`autonomous`, `h_on_released`, `vetoes`, `overrides`, `denies`, `lookback_withdrawn`) plus `r.evidence` and
   `r.minimums`. **It does not render anything named `gate_evidence`, `backtest`, or similar** — confirmed by
   grep across `tower/src` for `gate_evidence`/`human_released`, which found only `types.ts` (type declarations,
   no gate-evidence field) and the `agents/page.tsx` fields already named. **This screen is unaffected by K9**,
   because it reads `track_record`/`track_record_by_version` live via `/autonomy` (`app.py:1512`), a different
   data path from `track_record_snapshot` (point 4 below) — this part of PFP's "no Tower change" claim holds.
2. **But `tower/src/app/governance/[crId]/page.tsx:126-154` (the `Impact` component) does render
   `d.impact.track_record` today, by explicit named field** — confirmed by direct read: `imp.track_record.hash`,
   `.autonomous`, `.h_on_released`, `.vetoes`, `.overrides`, `.denies`, `.lookback_withdrawn` are each destructured
   and printed individually (`:144-152`). **This is not a generic/spread render of the impact object; it is a
   fixed set of named fields.** Any new key K9 adds to the snapshot (a `gate_evidence` block, per §3.3 point 3)
   will be present in the JSON the server returns and the approver's step-up signs, but **invisible on this
   screen** unless this component is also extended to read and display it.
3. **Corrected conclusion, replacing PFP's "No Tower change implied" for this one surface:** PFP P2-5's own text
   is wrong on this point, or was written before this screen existed in its current form. A build that stops at
   "SQL + Python only" per PFP's literal words would ship gate evidence that is signed (cryptographically bound
   into the approver's step-up) but never shown to the approver before they sign it — the opposite of the
   evidence-gated-promotion story's own stated purpose (K0's "two people approve it with evidence" demo moment,
   EXECUTION_PLAN.md:33). **This design re-assigns one Tower file to K9** (`governance/[crId]/page.tsx`, §0 table)
   to add the gate-evidence fields to the existing `Impact` component, following the same pattern already used
   for the AUTONOMY track-record fields immediately above it in the same component.
4. The `/agents` screen's own `track_record` object (`AutonomyRow.track_record`, `types.ts:715`) is unaffected,
   confirmed: `track_record_snapshot` is called only from `Registry.propose()` (`registry.py:626`), at
   CR-creation time, to produce the hash approvers sign — a different code path from `/autonomy`'s live reads.
   **Scope stays narrow**: only the one CR-detail screen needs a change, not the `/agents` screen.

### 3.3 What K9 builds

1. **`gov_item_valid`'s TASK_GATE branch** (`governance_schema.py:832-847`): add a check that a `release_ceiling`
   **change** (not every TASK_GATE edit) on a gate that is not the dataset's `introduced_by` gate is refused,
   with message "ceiling is inert: datasets are gated by `<introduced_by>`" (PFP's own exact wording, `:1015`).
   **Resolved by the before-build red team (CRITICAL finding #1, confirmed by the orchestrator at file:line) and
   by user decision 2026-10-31: per-gate framing confirmed (refuse only if the gate introduced NONE of its own
   datasets), WITH a mandatory genesis exemption.** `gated_dataset_introductions` (`governance_schema.py:320-323`)
   is populated only by `_introduce_datasets`, which runs inside `gov_apply` (`:267`) — strictly AFTER two-person
   approval. `gov_item_valid` runs inside `gov_propose` (`:153`) — BEFORE approval. For a brand-new dataset never
   previously gated, `gated_dataset_introductions` has ZERO rows at propose time — this is not "introduced by
   someone else," it is genuinely absent. A literal "introduced none of its datasets" reading would refuse this
   case and break ordinary, already-working `/catalog/wire` calls that introduce a new gate with a non-default
   `release_ceiling`. **The check must exempt "no row exists yet for this dataset at all"** (treat as not-yet-
   decided, not as evidence of a non-introducing gate) — symmetric to the existing precedent in
   `mode_promotion_refusal`, proven by `tests/t1/test_promotion.py::test_mode_promotion_refusal_ignores_a_brand_new_gate`
   (confirmed present and passing). The existing `task_gate_ceiling` SQL already has this exact NULL-safe pattern
   (`WHEN i.introduced_by IS NULL THEN NULL`, `:738`) — reuse it rather than writing a new one. A new test,
   `test_ceiling_check_ignores_a_brand_new_gate`, is added to the reserved list below, mirroring the sibling test.
   For the multi-dataset case where the gate introduces SOME but not ALL of its datasets (both covered by at
   least one prior row, i.e. not the genesis case): per-gate refusal still applies (refuse the whole change only
   if the gate introduced none of ITS OWN already-introduced datasets), but the red team's Important finding #2
   stands as a non-blocking follow-up — record per-dataset applicability in the response/evidence so an approver
   signing a ceiling change on a partially-introducing gate isn't shown a value that silently doesn't apply to
   every listed dataset. Not required before build start; logged to BACKLOG for this package's after-build pass.
2. **`gov_decide`**: add a refusal requiring a linked report (same `dq_ctl.cr_evidence` mechanism
   `mode_promotion_refusal` already uses, `:451-455`) for **any TASK_GATE item whose `release_ceiling` rises**
   (not just mode changes). This is naturally a sibling function to `mode_promotion_refusal`
   (`ceiling_promotion_refusal`, reserved name) called alongside it at `:200`, following the exact same
   `_items_digest`/`cr_evidence` pattern.
3. **`track_record_snapshot`** (`registry.py:646-662`): extend to also collect, for every TASK_GATE item in the
   proposal, whether a `cr_evidence` row already exists for it (same digest check), so approvers see the gate
   evidence alongside the AUTONOMY evidence in one signed snapshot. **Open design question, not resolved here**:
   does this need its own `rows` key (`{autonomy: {...}, task_gate: {...}}`) or does it fold into the existing
   `evidence` key? The brief says "adds the gate evidence to the snapshot," which this design reads as additive
   (new key), consistent with the standing additive-by-default rule. **Whatever shape is chosen here is the exact
   shape the Tower's `Impact` component (point 4) must read** — these two must be designed together at build
   time, by the same owner (BC writes the shape; TP consumes it — both now K9 owners per §0).
4. **Tower change, corrected from PFP's "no Tower change implied" (§3.2):** extend
   `tower/src/app/governance/[crId]/page.tsx`'s `Impact` component to render the new gate-evidence field(s)
   alongside the existing track-record fields, so an approver sees what they are signing.

### 3.4 Honest semantics, restated (PFP's own text, unchanged by this design)
- L1→L2 changes no unit's mode — only the trace line text changes. This is **not inert** in the governance sense
  (a database write happens, the audit chain records it), only inert in the unit-outcome sense. Important for the
  demo script's honesty, not for this design's blast radius.
- L2→L3 is the step that actually opens veto windows, and only when the authority's agent tier is at least L3.

### 3.5 Blast radius

| Object | Dependents (file:line) | Covering test |
|---|---|---|
| `gov_item_valid` TASK_GATE branch | `catalog_wire`, `catalog_promote_gate`, K8's new `profile/draft` route — all three call `registry.propose()`, which calls `gov_propose` → (eventually) `gov_item_valid` per item (`governance_schema.py:153`, confirmed: `PERFORM dq_ctl.gov_item_valid(p_state, x, p_origin) FROM jsonb_array_elements(p_items) x;`). **A bug in the new ceiling-inert check could break K8's own route if K8 ever proposes a TASK_GATE item — confirmed it does not: K8's route only ever builds a PIPELINE item, never TASK_GATE.** No collision. | `tests/t1/test_promotion.py` (existing file, 4 tests confirmed: `test_mode_promotion_refused_without_report`, `test_mode_promotion_two_person_with_report`, `test_mode_promotion_refusal_is_specific_to_mode_changing_items`, `test_mode_promotion_refusal_ignores_a_brand_new_gate` — new K9 tests join this file, same owner (BC), same one-file-one-owner rule) |
| `gov_decide` | Every caller of `/governance/change-requests/{cr_id}/decide` (`app.py:1133`) — i.e., every CR in the system, including K8's new CR type. **Re-confirmed no regression risk**: the new ceiling-evidence refusal only fires when a TASK_GATE item's `release_ceiling` changes, which K8's PIPELINE-only CR never triggers. | New tests in `test_promotion.py`, names reserved below |
| `track_record_snapshot` | `Registry.propose()` (`registry.py:626-628`) — every CR that includes an AUTONOMY item already calls this; extending its return shape is additive (a new key), so no existing caller breaks, confirmed by reading `propose()`'s use of the return value (`if tr is not None: impact["track_record"] = tr` — a dict merge, tolerant of new keys) | `tests/t1/test_promotion.py::test_track_record_counts_human_releases` (already named by PFP, reserved; **note this test name tests something already built (E-07) — the build seat should confirm this test is about the snapshot carrying the count, not re-litigate E-07 itself**) |
| `governance/[crId]/page.tsx` `Impact` component | Nothing downstream (display-only); the new field(s) must round-trip through both mock (`wire.ts`'s `proposeCr`/the K7 parity test) and live, so **K9's Tower change and K7's parity coverage should be built and gated together, not as two unrelated diffs** | Tower smoke; a new assertion in the existing CR-detail smoke flow that the gate-evidence field renders when present |

### 3.6 Reserved names for K9
- SQL function: `dq_ctl.ceiling_promotion_refusal(p_cr text, p_items jsonb) RETURNS text` (new, sibling to
  `mode_promotion_refusal`).
- Error message (verbatim, from PFP): `"ceiling is inert: datasets are gated by <introduced_by>"`.
- Test ids (from PFP P2-5, confirmed none exist yet by grep):
  `tests/t1/test_promotion.py::test_ceiling_change_refused_on_consumer_gate`,
  `::test_ceiling_L1_to_L2_changes_no_mode`, `::test_ceiling_L2_to_L3_opens_veto_windows_when_authority_L3`,
  `::test_track_record_counts_human_releases`, `::test_ceiling_check_ignores_a_brand_new_gate` (new, added by the
  before-build red team's CRITICAL finding #1 fix — mirrors `test_mode_promotion_refusal_ignores_a_brand_new_gate`).

---

## 4. Golden-path example, end to end across all three packages

This is the one worked example the brief asks for, chained across K7→K8→K9 to show the parity mechanism actually
proving something real, not a second isolated example per package:

1. **K8 ships the real route.** An operator edits `p6_claims`'s profile through the drawer (§2.3). The CR is
   approved by two people.
2. **K7's parity test runs against this exact route.** `tools/record_fixtures.py` runs a scripted HTTP transcript:
   `POST /catalog/pipelines/p6_claims/profile/draft` with the same body the drawer sends, recording the real
   response. `tests/t1/test_mock_parity.py::test_mock_and_live_keys_and_types_match` replays the same body through
   `mockProfileDraft` (via `mock-parity.mjs`, compiled without `tsx`) and asserts the key set and types match the
   recorded live response. **This is the first time this mock route is checked against anything real** — before
   K8, it was PRE-BACKEND by definition and could not be parity-tested at all (the K7_K12_MOCK_CONTRACT's own
   stated reason for deferring K7 full-stop).
3. **K9's governance check is exercised in the same CR flow, negatively.** The same operator later tries to
   change `p6_claims`'s TASK_GATE `release_ceiling` directly (a different CR, TASK_GATE kind) without being the
   introducing gate. `gov_item_valid`'s new check refuses it with "ceiling is inert: datasets are gated by
   `ZZ:taskgate:w1_dbt:...`" — demonstrating K9's refusal live, chained from the same profile the audience just
   watched get edited for real in step 1. The CR-detail screen (§3.2/§3.3) now also shows, on a *successful*
   ceiling promotion elsewhere in the demo, the gate evidence the approver is about to sign.

This single chained walkthrough satisfies "one golden-path worked example that runs end to end through the
package's demo moment" for all three packages, because K8's demo moment (profile editing) is the substrate K7's
demo moment (parity) proves against, and K9's demo moment (a correctly-refused ceiling change, and an honestly
evidenced one) is shown on the same pipeline the audience already has open.

---

## 5. Technology-selection records

**None needed.** No new external dependency is introduced by K7, K8, or K9: K7 reuses the existing TypeScript
5.9.3 compiler (per the already-adjudicated F-20 decision, "no `tsx`," `PARTF_IMPLEMENTATION_PLAN.md:289`) and
Python's standard `hashlib`/`pydantic` (already a dependency per `OBSERVABILITY_PARITY_ARCHITECTURE.md:63`, not
independently re-opened this pass). K8 and K9 add no library at all — pure extensions of existing SQL functions,
FastAPI routes, and one existing Tower component. Per the brief's own instruction, "a capability with no suitable
candidate fails closed and is recorded as a gap" — not applicable here, since no capability in this trio requires
an external tool in the first place.

---

## 6. Decisions needed (not resolved here; the orchestrator and red team decide)

1. **Does `llm.provider`'s seam entry require the 4 vendor provider classes to carry `@seam("llm.provider")`
   markers, or can the manifest test accept an entry whose "implementation" is cited by module path rather than
   decorator?** PFP's own wording implies the former; building that marker onto 4 files not otherwise touched by
   this package is additional scope the brief didn't explicitly ask for. **Recommend**: mark them (small, safe,
   closes a real gap in the seam system's own self-description) — but this is the architect's recommendation,
   not a decision this design makes unilaterally.
2. **Multi-dataset TASK_GATE ceiling-inert check (§3.3 point 1)**: refuse per-gate (any non-introduced dataset
   blocks the whole ceiling change) or per-dataset (only the non-introduced datasets' ceiling is refused, others
   proceed)? PFP's wording supports the per-gate reading, but this is this seat's inference, not a quoted PFP
   sentence resolving it explicitly.
3. **The exact shape of the gate-evidence addition to `track_record_snapshot`** (§3.3 point 3): a new top-level
   key vs. folding into `evidence`. Needed before BC (backend shape) and TP (Tower render) can build in parallel
   with a stable contract between them.
4. **Architect's own scope boundary (§0's framing).** This design assumes `docs/seams.yaml` and the two seam
   tests are written by the Architect seat in a *separate* dispatch from this one (consistent with PFP §4's table
   and the agent persona card), since this brief's SCOPE section asks for design output only. **Flagged so the
   orchestrator confirms the next dispatch, not silently inherited.**
5. **The `api.ts` dispatcher collision question (§0)** — needs the Tower seat to open `tower/src/lib/api.ts` in
   full before K8 build starts, to confirm the mock/live switch is genuinely route-agnostic. This seat did not
   open that file end to end.
6. **Whether K7's manifest entry format should include the new `catalog.profile_draft` id this design invented**,
   or whether the orchestrator prefers a different naming convention consistent with other recently-added seams.
   Not a blocking decision, but a naming convention the orchestrator should ratify once, not leave to the build
   seat's own judgment, since PFP never named this seam (it didn't exist when PFP was written).
7. **RESOLVED by the before-build red team's second independent search** (recursive grep across the whole
   `dqagent/` tree, not limited to `tracer.py`): zero matches for `FrozenClock`/`SystemClock`/`class.*Clock`
   anywhere. Combined with this design's own single negative, the two-confirmation rule is satisfied: **no such
   classes exist.** PFP's claim is stale. The manifest records `clock` as `absent`/`planned`, not `real-impl`.

---

## 7. GO/NO-GO recommendation for the before-build red team — SUPERSEDED, see §8

(Original recommendation below is superseded by the actual red-team pass; kept for the record, not deleted.)

**GO.** The blast radius for all three packages is narrow and well-isolated (K7 touches test/tooling
infrastructure only; K8 touches one new route plus a pure rewire of an already-built, already-frozen Tower
drawer; K9 touches two SQL functions, one Python dict-return extension, and — corrected from PFP's own text —
one existing Tower component's render). Both of this design's own initially-open questions about E-09's actual
mechanism and the CR-detail screen's rendering behavior were resolved during this same design pass by direct
code read (§2.1 point 3, §3.2), not left as guesses: `BODY_KEYS` does govern the baseline-pin path (through the
shared `agent_body_ok()` gate), and the CR-detail `Impact` component does need a new field for K9's gate evidence
to be visible to an approver before they sign. The one genuinely unresolved technical question with real
build-shape consequences is **Decision #2** (per-gate vs. per-dataset ceiling-inert refusal), because it decides
the SQL's join/`EXISTS` structure; recommend the red team spend its first pass confirming which reading
`gated_dataset_introductions`' actual row shape supports before the build seat writes that function. Recommend a
second pass on **Decision #3** (the gate-evidence shape), since BC and TP need a stable contract before building
in parallel. The `clock` seam ambiguity (Decision #7) and the `api.ts` dispatcher question (Decision #5) are both
cheap to resolve with one more targeted read each and should not block dispatch, only be assigned as the first
five minutes of the relevant seat's own work.

## 8. Before-build red team result (2026-10-31): NO-GO as originally written, fixed in place, GO after fixes

The before-build red team found **two CRITICAL defects** in this design, both independently re-verified by the
orchestrator at file:line before being accepted (not taken on the red team's word alone), both now fixed in
§2.1 point 1 (K8, merge-from-current-content) and §3.3 point 1 (K9, genesis exemption) above:

1. **K9's ceiling-inert check, as originally specified, would have refused ordinary, already-working
   `/catalog/wire` calls** introducing a brand-new gate — because `gated_dataset_introductions` has zero rows
   for a never-before-gated dataset at PROPOSE time (the table is populated only at APPLY time, after approval).
   Fixed: an explicit genesis exemption, symmetric to `mode_promotion_refusal`'s existing precedent
   (`tests/t1/test_promotion.py::test_mode_promotion_refusal_ignores_a_brand_new_gate`, confirmed present).
2. **K8's own design gave the build seat two incompatible instructions**: merge from the pipeline's current
   stored content, vs. copy `mockProfileDraft`'s shape verbatim — and the mock's shape does NOT read current
   content (hardcodes `layer_map: {}`, derives `manifest_path` from a naming formula). Following the literal
   "copy verbatim" instruction would have silently corrupted real pipelines' `layer_map`/`manifest_path` on every
   edit. Fixed: the route merges from the real `config_effective` row; the "copy verbatim" instruction is void.

One Important, non-blocking, logged to BACKLOG rather than fixed now: K9's per-gate framing, even with the
genesis exemption, doesn't flag the partially-introducing multi-dataset case (a gate that introduced SOME but
not ALL of its datasets) with per-dataset applicability — an approver could sign a ceiling value that silently
doesn't apply to every dataset listed. Two Nits resolved in the design text (ADJ-6 cost/skip-risk disclosed;
"DDL migration" framing corrected to a function redefinition). The `clock` seam ambiguity (Decision #7) is
CLOSED — a second independent search confirms no `FrozenClock`/`SystemClock` classes exist anywhere in
`dqagent/`; manifest records `clock` as `absent`/`planned`.

**User decisions on the scope reviewer's parallel findings (2026-10-31):**
- **K9**: build now, with the Critical fix above (user overrode the scope reviewer's "defer again" recommendation).
- **K7**: build in full as designed (user overrode the scope reviewer's "cut to one route" recommendation).
- **K8's merge question**: merge from current content, not copy-mock-verbatim (this is also the only
  correctness-safe choice — the red team's finding and the user's scope choice agree).

**GO for build**, with both Critical fixes now in the design text above. The scope reviewer's rehearsal/
reachability-check recommendation for K8's newly-un-gated live Edit button is carried forward as a required
build-brief step (§2.3's golden path), not optional — this is the same failure class as the 2026-10-29
PRE-BACKEND banner incident ("time of death").
