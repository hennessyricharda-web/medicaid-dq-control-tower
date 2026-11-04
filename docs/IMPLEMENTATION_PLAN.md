# Implementation plan — Control Tower, DQ agent, Data Governance agent

**This is the ship-facing implementation plan.** It is kept deliberately separate from the SDLC build-process files (`blackboard.md`, `BACKLOG.md`, `STATUS_BOARD.md`, `.claude/agents/*`, `.claude/commands/*`, `docs/design/packages/*`'s round-by-round red-team records) — those stay in the full working repo as the build history, but they are not part of what ships, and they are not duplicated here. This file is the one a fresh Claude Code session should read to know **what the product is, what's done, and what's next.**

**Ship scope (corrected 2026-10-29):** Control Tower + DQ agent + Data Governance agent, for the client's HHS/Medicaid data-quality use case. **The K-CQ clinical-quality lane (Controlling High Blood Pressure measure, FHIR/DEQM terminology scaffolding) IS in ship scope — it is the core demonstration use case, not a side lane.** An earlier version of this file and the first zip excluded it; that was a misreading of "not clinical pipelines" and has been corrected. See §3.

**Full package specs** (acceptance criteria, file-level contracts, test lenses) live in `docs/rules/PARTF_IMPLEMENTATION_PLAN.md` (the "PFP") and `docs/EXECUTION_PLAN.md` §6 — both are the real build-vs-read material referenced throughout this file; this file is the status summary and the forward plan, not a replacement for either.

---

## 1. Package status — every K-wave, done vs not done

| Package | What it is | In ship scope? | Status |
|---|---|---|---|
| G0 | Gate tool trustworthy on both platforms | Infra, not shipped as a feature | ✅ DONE |
| K0 | Evidence-gated promotion + eval recorder | ✅ Yes | ✅ DONE |
| K1 | Per-DAG agent profile (read-only) | ✅ Yes | ✅ DONE |
| K2 | Pipelines-lite: catalog, ORCH-1, consumer DAG, wiring wizard | ✅ Yes | ✅ DONE (lite scope; full React Flow graph delivered in K4) |
| K3 | Backtest + promote a gate to block | ✅ Yes | ✅ DONE |
| K4 | Pipelines graph (React Flow) | ✅ Yes | ✅ DONE |
| K5 | New source via YAML | ✅ Yes | ✅ DONE |
| K6 | Demo readiness (steps 1-5 of 6) | ✅ Yes | ✅ steps 1-5 DONE; step 6 (one live rehearsal) needs the user's own machine, not gating |
| K7 | Mock/production parity mechanism | ✅ Yes (when built) | ⬜ **NOT BUILT** — no real route exists yet for K8-K12's touched surfaces |
| K8 | Profile editing | ✅ Yes | ✅ BUILT |
| K9 | Ceiling promotion (step b) | ✅ Yes (when built) | ⬜ **NOT BUILT** — backend-only, a track-record-snapshot field |
| K10 | Robustness (advisory lock, SLA fix, webhook, recovery endpoint) | ✅ Yes (when built) | ⬜ **NOT BUILT** — `sla_due` bug is real backend Python; rest has no UI yet |
| K11 | The rest of the explorer (eval history/diff, annotation queue, feedback) | ✅ Yes | ✅ BUILT |
| K12 | Final readiness | ✅ Yes (when built) | ⬜ **NOT BUILT** — process package, waits on the real backend pass |
| K-CL | Clean-up loop (agent notes panel, source-feedback loop, known-issues screen) | ✅ Yes (when built) | ⬜ **NOT SCHEDULED** — 2 of its 3 pieces (F-45, F-47) are Tower-only and ready now; see §4 |
| K-CQ | Clinical-quality lane (Controlling High Blood Pressure) | ✅ **Yes — core demonstration use case** | ✅ CLOSED 2026-10-29, independently verified — see §3. 7 open BACKLOG items (F-61 through F-67), none blocking the demo |
| L-06 / L-06a | Data Governance agent: canonical-element classification completeness | ✅ Yes | ✅ L06a DONE (evidence) — found a real governance gap (F-68), open, needs a two-person classification decision, not a build |
| **L-17** | **Multi-cloud/DAG execution substrate for the Governance agent — AWS, Azure, Snowflake, Databricks; serverless / platform-native-pipeline / DAG-orchestrator execution models, fully pluggable; Unity Catalog / Snowflake's governance equivalent / Iceberg as the platform-native delegation layer** | ✅ Yes — **scheduled, not yet solved** | ⬜ **DESIGN EXISTS, UNSOLVED. See §2 — this is the next package.** |

No K13 exists. K-CL, K-CQ, L-06 and L-17 are all addenda to the original plan, added after it.

---

## 2. L-17 — scheduled as the next package, and why it isn't code yet

L-17 is the piece the user has asked to see **integrated into the plan as a real package**, not held as a footnote. It is now listed in §1 as scheduled. But it needs to be stated plainly, not glossed over: **this is a genuinely unsolved design problem, not a naming or scheduling formality.**

**What it needs to do:** let the Data Governance agent's checks (today: `classification_completeness()`, `dqagent/governance/registry.py:112-121`, and whatever K-CL/L-06 add next) execute against AWS, Azure, Snowflake and Databricks, using whichever of three execution models fits each platform — cloud-native serverless functions, platform-native pipeline functions, or DAG-orchestrator task functions — selected by configuration, not hard-coded per platform. Platform-native catalogs (Unity Catalog on Databricks, Snowflake's own governance/tagging equivalent, Iceberg as a table format with its own catalog layer) are the intended mechanism for delegating governance enforcement to the platform rather than reimplementing it centrally. The medallion architecture (bronze/silver/gold, or this repo's own landing → gated → `_RELEASED` staging model, `DESIGN.md:146-154`) is the anchor point — the substrate attaches at the medallion-layer transition, not inside each vendor's own execution API.

**Why it's unsolved, concretely** (full detail in `docs/design/packages/L17_GOVERNANCE_MULTICLOUD_SUBSTRATE_DESIGN.md`, which stays in the working repo as the design record — read it before starting this package):
1. The four platforms' native execution APIs are not uniform; "fully pluggable across 3 execution models" is a real abstraction-design problem, not a config flag.
2. Governance regimes differ by platform (Unity Catalog's model, Snowflake's, Iceberg's own catalog layer) — they are not drop-in equivalents of each other or of this repo's `CLASSIFICATION` registry.
3. "Delegate to the platform's native classifier" doesn't resolve credentials/service-identity per platform — that's a separate, unanswered design question per platform.
4. No native classifier reviewed is known to be Part 2 (substance-use data) aware — a real gap for this domain specifically, not generic.
5. The medallion-layer anchor is sound in principle (grounded independently of any one vendor's marketing in this repo's own existing landing/gated/`_RELEASED` model) but **relocates** the hard problem to that boundary rather than resolving it.

**What a real attempt needs before any code is written** (from the design doc's §4): a feasibility note per execution model; a named credential/service-identity model per platform; an explicit, scoped answer for what native classifiers can't cover (Part 2, specifically); a verified real client/tenant to scope the residency question against (not a hypothetical); and confirmation that a quarantine-equivalent stage genuinely exists at the point the design wants to anchor to.

**Process for this package, same as every other:** state the frame, map the blast radius, design first, red team before any build (round cap 5, reconsider the approach after 3 rounds on one finding — this already happened once on this exact question and produced the current unsolved state), then build. Do not start writing cloud adapter code before a design round actually closes the 5 points above — that was the finding that spun this out in the first place, and restating the goal doesn't change it.

---

## 3. K-CQ — the core demonstration use case, fully included

K-CQ (the Controlling High Blood Pressure clinical-quality lane) is real, built, and independently verified, and is **in ship scope as the core demonstration use case** — this is the correction noted in the header; an earlier pass wrongly treated it as out of scope and stripped it from the first zip.

**What ships, in full:** `dqagent/engine/clinical_normalize.py`, `dqagent/engine/measure_cbp.py`, `dqagent/terminology/`, `tower/src/app/measures/` (incl. `cbp-impact`), `tower/src/lib/mock/routes/clinical.ts`, `rules/catalog/federal/clinical_cbp.yaml`, `synthetic/clinical.py`, `synthetic/out/w1_clinical/`, the `test_k_cq_*` / `test_k_cq_intake.py` test files, and the 3 API routes embedded in `dqagent/api/app.py` (`measures/cbp/impact`, `normalizations/pending`, `normalizations/{id}/approve`).

**What's still genuinely open on K-CQ** (tracked, non-blocking for the demo, real work for later — detail in `K-CQ_DESIGN.md` §6/§8 and the BACKLOG IDs): F-61 (live end-to-end rehearsal, Postgres-gated, deferred by user decision), F-62 (no server-side authority partitioning on the 3 clinical endpoints — UI-side `PREVIEW_K_CQ` gating is real but not a substitute), F-63 (mock-contract catalog not extended for these 3 routes), F-64 (`clinical_cbp.yaml` not wired into any state profile — cosmetic for a single-state demo), F-65 (the approve endpoint uses bearer-only auth, not the standing step-up pattern — named exception), F-66 (whether Tower's approval surfaces, this one included, should consolidate on `VerdictFlow` — architect assessment, not yet run), F-67 (`kpa_conversion`/`reshape` transforms never create a pending-approval row today).

---

## 4. What's actually ready to build next (Tower-only, no backend change needed)

Full detail and implementation-ready specs: `docs/design/packages/NEXT_NON_BACKEND_BUILD.md` (kept in the working repo; summarized here because it's forward-looking plan content, not SDLC process).

- **F-45** — agent notes panel on the decision card. Backend already returns `agent_board` (`dqagent/api/app.py:551-552`); no Tower component renders it. Storyboard beat A3.
- **F-47** — Known Issues screen. Backend already has the full governed propose/challenge flow (`dqagent/api/app.py:905-944`, reuses the standing step-up pattern); no Tower route exists. Storyboard beat D3.

Both are smaller and lower-risk than L-17, and don't touch the open multi-cloud design question. A fresh session can do either (or both) before or in parallel with starting L-17's design round.

---

## 5. Recommended order for what's next

1. **F-45, then F-47** (§4) — fast, backend-already-exists, demo-visible.
2. **L-17 design round** (§2) — this is the user's standing priority for "integrated into the plan," but it is a design problem first. Do not skip to code.
3. **K7 → K9 → K10 → K12** — the real-backend pass that was deliberately deferred during the mock-only K7-K12 buildout; needed before any of those four can close for real.
4. **K-CL** (F-46's source-feedback loop specifically) — still needs an architect pass before Tower/backend work is meaningful; not scheduled by the user yet.
