# Part E design: earned autonomy, agent registry, reconciliation, monitoring, explanations, and hooks for later agents

**Status:** APPROVED for build on 2026-10-23.
- User decision: build tweaks A, hooks B, and three additions:
  - a switchable LLM whose default is a built-in offline TEMPLATE explainer;
  - mandatory plain-language explanations;
  - distribution and statistical-control monitoring as a seat inside the DQ agent.
- The governance agent itself and the knowledge graph are the next phase.
- Snowflake execution and break-glass are out of scope.

**Source of this text:** the architect's draft, adjudicated against the pre-build red team (C1–C4, I1–I7, N1–N2 in `docs/blackboard.md` "PART E PRE-BUILD").

**Naming and numbering:**
- Naming is generic to the DQ estate.
- Keep the name "Command Tower".
- Provider keys (`anthropic`, `watsonx`, `azure_openai`, `cortex`) are technology identifiers in config, not organisation names.
- The code-set registry, previously called Part E, is now Part F.

**Gate:** `tests/t1/test_w1_outcomes.py` (E0) pins w1 at 420 RELEASED / 291 HELD / 47 PENDING_DEPENDENCY. Every Part E change must leave it green. Any intended change must be justified on the blackboard first.

## 1. Earned-autonomy ladder (A2)

### 1.1 Tiers are ceilings
A tier is set per `(agent, action_class, authority)`. The effective mode is `max(policy mode, floors, tier ceiling)`, so floors can still raise any tier to H_IN_2.

| Tier | Name | Ceiling | Card |
|---|---|---|---|
| L4 | Autonomous | A | — |
| L3 | Supervised | H_ON (act after a veto window) | veto countdown |
| L2 | Recommend | H_IN | shows the agent's recommended action |
| L1 | Assist | H_IN | evidence only; the agent's recommendation is suppressed |

- **Missing entry:** a missing AUTONOMY entry means **L1**, never L4.
- **Action classes:** `RELEASE`, `POST_BOARD`, `PIN_MEMORY`. There is no `PROPOSE_CR`, because `gov_propose` only accepts calls from `dq_api` with a named person.
- **Artifacts:**
  - AUTONOMY `<ST>:autonomy:<agent>:<action_class>`, content `{authority: tier}`.
  - The seed keeps w1 invariant. `dq-reconciliation` is set to:
    - RELEASE: AUTH_MEDICAID L4, AUTH_SNAP L2;
    - POST_BOARD and PIN_MEMORY: L4.

### 1.2 Promotion and demotion
- **Promotion** (raising any tier) is a two-person change request. **The database enforces it (C4):** `gov_propose` computes `requires_two` itself for AUTONOMY, ENTITLEMENT and AGENT items. Single approval is allowed only when SQL proves the change is lower-only:
  - the new tier rank is at most the old rank for every authority;
  - not_after is earlier or equal;
  - fewer action classes or authorities;
  - status goes from ACTIVE to SUSPENDED or REGISTERED.
  - Any other change, including a brand-new AUTONOMY or ENTITLEMENT, needs two.
  - The Python `classify()` result is recorded for display only, and the database may only make it stricter.
- **Evidence:** the change request's `impact.track_record` holds a snapshot of the track record and its hash, so approvers sign the evidence they saw.
- **No automatic promotion, ever.** Negative control: N clean releases do not change the tier.
- **Demotion is automatic and lower-only** (`dq_ctl.autonomy_demote(agent, action_class, authority, to_tier, reason_code, ref)`, SECURITY DEFINER, callable only by the registry service). It fires when:
  - a look-back item is decided WITHDRAWN for a unit that this agent released at L3 or L4 → that authority drops to L2;
  - a VETO is recorded → that authority drops to L2.
  - Each demotion writes an AUTONOMY artifact version with origin `DEMOTION`, a blackboard post (`AUTONOMY_DEMOTED`) and an explanation.

### 1.3 Track record
`dq_ctl.track_record` is a view per `(agent, action_class, authority)` counting:
- autonomous releases;
- releases after a veto window;
- vetoes;
- human overrides (human release over FAIL);
- DENY on released units;
- look-back WITHDRAWN.

It resets at the latest of the current tier's change request, a ruleset version change, and an eval-attestation change (I7).

**Eval attestation:** `dq_ctl.eval_attestations(label, code_sha256, tests, failures, junit_sha256, at, person_id)` is append-only and inserted through `dq_api` by a named person. It is not read from files under `docs/` (I7).

### 1.4 Veto window (L3/H_ON) (C2, C3, N1)
- **Policy:** `veto_window: PT10M` (ISO duration) and `approvals.H_ON: {persons: 0}`. H_ON now means act + notify + veto.
- **Opening the window:** the evaluator writes `dq_ctl.veto_windows(unit_key, eval_version, opened_at default now(), window_s, policy_hash)` (append-only) and the unit stays EVALUATING.
- **Clock:** the deadline is computed by the database clock.
- **Dependency escalation:**
  - It is `_max(m, 'H_IN')`. Never replace an H_ON with itself (C2: the old line `evaluate.py:305` is wrong).
  - The escalation flag is persisted on the eval row, and the publisher's recompute applies it.
- **Publisher clause 4 (rewritten):** allows mode A, or H_ON when `now() >= opened_at + window_s` and there is no VETO and the tier is still ≥ L3 and nothing has been escalated.
- **Lifecycle edges:**
  - `(EVALUATING, HELD, VETO)` for dq_api;
  - `(EVALUATING, HELD, CEILING)` for the publisher, used when the recomputed mode exceeds H_ON, so a unit never sits silently in EVALUATING (C3).
- **AUTONOMY change applied:** reopen through the existing RULESET edge.
- **Veto API:** accepts a veto while the unit is EVALUATING with an open window, by the database clock. It returns 409 only once the unit has released.

### 1.5 Earning scenario w2_earn (C1)
- **Data:** a deterministic synthetic run `synthetic/out/w2_earn`, generated by a script added to the generator, aggregate-safe.
- **Starting state:** a test state where `dq-reconciliation` RELEASE for one authority starts at **L2**.
- **The story:**
  1. N batches are human-approved, and the track record accrues.
  2. The maker proposes L2 → L3 with the track-record evidence, and two State approvers sign.
  3. The next batch opens veto windows. If no one vetoes, units release at their deadline (by the database clock). A veto holds that unit and auto-demotes the authority to L2. Every other window still open for that authority then holds at the L2 ceiling, and a person decides those units. This is fail-safe by design (E-14, 2026-10-23): a veto withdraws trust from the whole tier, not from a single unit. To show a release at the deadline, let at least one window expire before vetoing.
  4. A look-back WITHDRAWN on an L3-released unit also demotes.
- **Tests:** a t1 test plus an agent-eval scenario `S6`.

## 2. Agent registry and entitlements (A3, I7)

- **Artifacts:**
  - AGENT `<ST>:agent:<id>`, content `{name, version, status, seats[]}`;
  - ENTITLEMENT `<ST>:entitlement:<id>:<action_class>`, content `{authorities[], not_before, not_after}`.
- **Seed** from `config/agents.yaml`:
  - `dq-reconciliation` is ACTIVE, entitled for 365 days from genesis;
  - `data-governance-classification` is REGISTERED with no entitlements. It is shown in the Tower as "next phase".
- **View:** `dq_ctl.entitlements_current`.
- **Expiry lowers the ceiling to H_IN, and never stops human-approved releases (I7).** An agent without POST_BOARD or PIN_MEMORY entitlement is refused inside `bb_post` / `mem_pin_baseline`, which fail closed with a clear error.
- **Seat binding:** seats belong to agents. A session role whose agent is not ACTIVE is refused.

## 3. Reconciliation (A4, I1, I2, I3)

### 3.1 One definition of a gating ABC failure
- **Schema:** `abc.gating boolean NOT NULL DEFAULT true`.
- **View:** `dq_ctl.abc_gating_failures(batch_id)`. Every caller reads it:
  - `runtime.py` (both sites);
  - `publish.py` clause 4 and clause 7;
  - `seats.after_intake`.
- **Effect:** non-gating hops never raise the batch floor.

### 3.2 Empty expected delivery (feed-level)
- **Cadence:** a feed may declare `cadence: {expected: true, every: P1D|P1W|P1M}` in the feeds manifest. Defaults:
  - IE and 837 feeds: expected;
  - 834 deltas: `empty_ok: true`.
- **When a delivered file of an expected feed has zero detail rows**, the system:
  - records a non-gating ABC row `FILE->RAW expect_nonzero` with status FAIL;
  - opens a `dq_ctl.feed_items` item (`kind=EMPTY_DELIVERY`), append-only, with events.
- **Resolution:** a human acknowledges it with a reason code: NO_ACTIVITY, HOLIDAY, SUBMITTER_INACTIVE or RESUBMIT_REQUESTED. It never triggers the batch floor.
- **Missing expected deliveries** (none arrived inside the cadence) open `MISSING_DELIVERY` the same way.
- **Feed with `empty_ok`:** no item.
- **Tests:** an expected empty feed opens an item; an `empty_ok` feed does not; w1 is unchanged.

### 3.3 Parallel run (legacy vs new)
- **Input:** a generic `legacy_totals` JSON file:
  `{system, period, as_of, rows:[{relation, measure, value}]}`.
  Measures: `records`, `amount:<name>`, `rule_failed:<rule_id>`.
- **Comparison:**
  - New-side values come from gated relations and results for the same `period` and `as_of`.
  - Matching is per (period, relation, measure).
  - Tolerance per measure from PROFILE `parallel_run.tolerance` (e.g. `records: 0`, `amount: 0.005` relative).
- **Storage:** results go to `dq_ctl.parallel_run_results`, append-only:
  `(run_id, period, as_of, relation, measure, legacy, new, delta, tolerance, status MATCH|WITHIN|BREAK, explain_code)`.
- **Explaining a break:** a break needs an explain code before go/no-go evidence counts it. The codes are TIMING, LEGACY_DEFECT, NEW_DEFECT, DEFINITION and FIXED. The code is set by a named person through the API, with a step-up only for LEGACY_DEFECT and DEFINITION.
- **Non-gating:** it is go/no-go evidence for cutover and never holds units.
- **Data:** the synthetic generator emits `legacy_totals` for w1 with a deliberate timing break and a $ break.

## 4. Critical data elements and minimum viable DQ (A5)
- **Effective artifact:** CDE `<ST>:cde`, content `{element: {mvdq: {completeness_min, validity_min}, steward_domain}}`. It is seeded from a new `rules/cde_defaults.yaml`. `elements.yaml` is not edited for CDEs, so the catalog digest doesn't move.
- **Computation:** `dqagent/rules/cde.py` computes the latest pass rates from B-CMP, B-VAL and code-set rules bound to the element. The status is one of:
  - MEETS;
  - BELOW;
  - NOT_EXERCISED (the domain isn't delivered);
  - NOT_LOADED (the code set is absent).
- **Advisory only.**

## 5. Sensitivity classification (B2, N2)
- **Effective artifact:** CLASSIFICATION `<ST>:classification`, content `{element: class}`, where class is identity, clinical, payment, program, coverage, part2 or none. It is seeded from `rules/classification_defaults.yaml` merged with the state contract `control_tags`.
- **Governance:** downgrading a class is two-person, enforced in the database by the same lower-only rule as §1.2.
- **Masking:** reveal masking reads the effective classification. Masked classes are identity, clinical and part2.
- **Record-level Part 2 (N2):** units from a relation or program declared `part2_program: true` in the feeds manifest carry `part2=true`. Explanations, exports and the blackboard never include values from these units. Exports apply small-cell suppression (1–10) to counts.

## 6. Monitoring seat (distribution and statistical process control) (I4, I5)
- **Seat:** MONITOR, role `dq_monitor_svc`, inside agent `dq-reconciliation`. It is advisory only (no gating in Part E) and runs after seal, before evaluation.
- **Metrics:** `dq_ctl.monitor_metrics(batch_id, period, relation, metric, key, value, n)`, append-only. Values are aggregates only, computed in the data plane:
  - `rows`;
  - `amount:<payment column>` (sum);
  - `null_rate:<CDE element>`;
  - `mix:<CDE element>` (category shares; rare categories, below 5 expected, folded into OTHER, which is kept, never dropped).
- **Charts:**
  - XmR (individuals and moving range) for `rows` and `amount`;
  - p-chart with per-period limits `p̄ ± 3√(p̄(1−p̄)/n)` for `null_rate`;
  - PSI for `mix`, computed only when every bin has at least 5 expected: 0.10–0.25 is WATCH, ≥ 0.25 is SHIFT.
- **Run rules:** R1 (beyond 3σ) is on by default. R2 (8 points on one side) is available, off by default, and configurable through MONITOR config.
- **Baselines:**
  - Memory kind SPC_BASELINE, at least 8 eligible periods, frozen once pinned.
  - Periods with an open signal are excluded (no poisoning).
  - Re-baselining is a MONITOR change request.
  - With fewer periods, nothing posts and the status is BASELINE_BUILDING.
- **History:** the synthetic generator emits 12 prior periods of aggregate monitor history for w1 relations (`synthetic/out/w1/monitor_history.json`), loaded as baseline periods. A seeded volume drop, null-rate jump and mix shift are planted in the w1 period.
- **Blackboard templates:** `SPC_SIGNAL`, `MIX_SHIFT`, `BASELINE_BUILDING`.

## 7. Explainer and mandatory explanations (NEW1, NEW2, I6)
- **Package** `dqagent/llm/`:
  - `facts.py`: builds facts from control-plane rows and aggregates only. It drops identity, clinical and part2 classes and Part 2 units, and suppresses small cells.
  - `template.py`: the default, deterministic and offline. Its output is byte-stable.
  - `providers/{anthropic,watsonx,azure_openai,cortex}.py`: config `config/explainer.yaml`, default `provider: template`. Credentials come from the environment only. Each provider is contract-tested against a stub HTTP server and labelled "not exercised live".
  - `check.py`, the structured check. The provider must return JSON `{text, claims:[{fact_key, value}]}`, and the result is rejected (falling back to the template) unless all of these hold:
    - every number in the text (digits, dates, percentages, and spelled-out cardinals to at least 9,999) is bound to a claim, and every identifier equals a fact token (whole token, case-insensitive, never a substring);
    - every claim's value equals the fact at `fact_key`;
    - every disposition word (and synonyms: published, blocked, withheld, ...) is bound to a claim in its clause, and its polarity matches: negation is read per clause;
    - no causal wording ("because", "since", "caused by", "due to", "as a result", "owing to", "led to", "root cause", ...) unless a HYPOTHESIS fact exists;
    - no release advice ("safe to release", "you may release", "can proceed", "ok to release", "should approve", ...).
    (Hardened after the post-build red team, E1.)
- **Every explanation object:** `{text, source, fact_check: TEMPLATE|PASS|FALLBACK, facts_hash, text_hash}`. TEMPLATE is the deterministic template generated only from facts; it is not fact-checked and is never labelled PASS (E2). PASS is a provider text that passed the check; FALLBACK is the template shown after a provider failed.
- **Binding to decisions:** a decision (maker, checker, veto, look-back decide, change-request decide) stores the `facts_hash` and `text_hash` of the explanation shown, through the challenge payload. It goes in an `explanation_ref` column on the decision event tables.
- **Mandatory:** every card disposition, unit decision card, change request and its impact, look-back item, monitor signal, parallel-run break, feed item and demotion carries `explanation`. A contract test walks every endpoint and fails on any missing or empty explanation.
- **The LLM is never in the decision path.**

## 8. Hooks for the next agents (B1, B3)
- **B1 agent namespace:**
  - the blackboard and memory gain `agent`, which goes into `bb_hash`;
  - SEATS maps role → (agent, seat);
  - refs become typed text: `bb:<seq>`, `mem:<id>`, `rule:<id>@<entity>`, `unit:<key>`, `cr:<id>`, `lookback:<id>`, `feed:<id>`, `pr:<id>`;
  - `/agent/*` takes `?agent=`.
- **B3 export (vendor-neutral, open standards):**
  - `GET /api/v1/export/lineage` returns OpenLineage-style dataset/job facets plus a typed-edge list (binds, depends_on, checks, result_of, decided_by, released_in). This is the input the future knowledge graph would ingest.
  - `GET /api/v1/export/dq-assertions` returns rows of `{asset, element, rule, dimension, disposition, evaluated, failed (suppressed 1–10), batch, ruleset_version, effective_hash, cr_id}`.
  - Output is deterministic and sorted, and fields come from an allow-list.

## 9. Command Tower (A1, A6, A7 and the new screens)
- **A1 top band:**
  - `genesis_state` is an enum, and the Tower alerts whenever it isn't RATIFIED;
  - `oldest_held_age_min` plus `oldest_sla_remaining_min` (negative means overdue), with SLA durations from policy `sla`;
  - `decisions_due` = HELD units still short of the required approvals;
  - tile: "Explainer: template · decisions deterministic".
- **A6:** `GET /api/v1/audit/verify` returns the result for the audit chain and the blackboard chain; audit rows gain `label`.
- **A7:** look-back `in_live_manifest` is computed live, and `in_live_manifest_at_open` is kept.
- **New screens:** Agents (registry, entitlements, tiers, track record, a promote-or-demote change-request form), Monitoring (control charts with limits and signals), Reconciliation (parallel run plus feed items).
- **Changed screens:**
  - Coverage gains the CDE/MVDQ view.
  - Rules and Coverage show classification.
  - Queue cards show the veto countdown and the tier badge.
- **Everywhere:** an `<Explanation>` component wherever there's an explanation.
- **Mock server:** mirrors every endpoint.

## 10. API contract
The architect's contract applies, as amended here (blackboard "PART E PRE-BUILD"):
- `GET /api/v1/summary`
- `GET /api/v1/agents`
- `GET /api/v1/autonomy`
- `POST /api/v1/units/{key}/veto` with `{reason_code}`
- `GET /api/v1/feeds/items`
- `POST /api/v1/feeds/items/{id}/ack` with `{reason_code}`
- `GET /api/v1/parallel-run?period=`
- `POST /api/v1/parallel-run/{row_id}/explain` with `{explain_code, step_up_token?}`
- `GET /api/v1/coverage?cde=true`
- `GET /api/v1/classification`
- `GET /api/v1/monitor`
- `GET /api/v1/monitor/series?relation=&metric=&key=`
- `GET /api/v1/audit/verify`
- `GET /api/v1/export/{lineage,dq-assertions}`
- `GET /api/v1/explainer` returns `{provider, configured, fallback_count}`
- `POST /api/v1/eval-attestations`
- The governance change-request endpoints accept the kinds AUTONOMY, AGENT, ENTITLEMENT, CLASSIFICATION, CDE and MONITOR.

## 10b. Nightly-run simulator (DEC-018, added 2026-10-23)
**Purpose.** Show the agent working night by night, instead of loading every synthetic delivery at startup. This is a demo-mode feature only, and the Tower labels it SIMULATED.

**Simulated business date**
- Stored in `dq_ctl.sim_runs`, append-only, with columns `(run_id, night_date, started_at, finished_at, person_id, files jsonb, summary jsonb)`.
- The current simulated date is the latest `night_date`.
- The runtime refuses the simulator unless it was started in demo mode (`--nightly`). Outside demo mode the API returns 404.

**Nights**
- A night is one delivery date of the synthetic run. w1 has 22 nights, 2026-01-01 to 2026-04-01.
- Its files are ingested in the existing `arrival_order`.

**Demo start**
- `dqagent demo --nightly --preload-nights K` loads nights 1..K at startup. The default K is 15. The rest stay queued.

**What a night run does, in order**
1. Ingest and seal each file that arrived that night.
2. Record monitor metrics.
3. Evaluate and run the publish cycle, including autonomous releases and opening veto windows.
4. `check_missing_deliveries(now=night_date)`.
5. Close the monitor period when the night crosses its period boundary, or is the last night.
6. Re-evaluate units waiting on a dependency.
7. Build the run summary.

**Run summary**
- Counts: files arrived; ABC breaks (gating and advisory); units created; released autonomously; veto windows opened; held for a person, with the mode reasons; pending dependency; feed items opened; monitor signals; demotions.
- Each count carries the ids of the objects it counts.
- The summary carries a mandatory plain-language `explanation` from the explainer (kind `nightly_run`).

**API**
- `GET /api/v1/sim/nights` returns `[{night_date, files[], status QUEUED|RUN, run_id}]` plus `current_date`.
- `POST /api/v1/sim/run-next` requires the operations duty. The token group is `dq-duty-operations` and the roster must also grant it. It returns the run summary. It returns 409 when no night is queued, or while a run is already in progress (advisory lock).
- `GET /api/v1/sim/runs` and `GET /api/v1/sim/runs/{run_id}` return the summaries.

**Tower**
- The top band shows "Simulated date: <date>" and a "Run next night" button for operators.
- A new **Nightly runs** screen shows queued and completed nights, each run summary with its explanation, and links to the held units and signals.

**Invariant**
- Running all remaining nights from any K must end exactly at w1's 420 / 291 / 47.
- K=0 is the same as today's full load.
- A test proves this for K=15 and for K=0.

## 11. Done means
- E0 is green: w1 is 420 / 291 / 47.
- A t1 test and an agent-eval S6 cover w2_earn: promotion is two-person, the veto window releases exactly at its deadline, a veto demotes, and a look-back WITHDRAWN demotes.
- Every C/I fix from the pre-build red team has a test.
- The explanation contract test passes, and template output is byte-stable.
- The provider stub tests pass, including fallback on an invented number, a negated disposition and causal wording.
- The monitor fires on the planted volume drop, null-rate jump and mix shift, and stays silent on the clean relations (negative control).
- The parallel run shows MATCH, WITHIN and BREAK, each break with an explain code.
- The full suite, agent evals and naming guard pass.
- The Tower passes typecheck, lint and build, and a live smoke with screenshots.
- A post-build red team is run and adjudicated.
- The receipt is taken and the sync is done.
