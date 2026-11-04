# Part D design: two-person governance of rules and configuration, onboarding proposer, Tower screens

Status: BUILT 2026-10-23 (user go 2026-10-22: all five Tower screens; binding proposer included in Part D). Both red teams' adjudications are in docs/blackboard.md; where this text and the blackboard differ, the blackboard (as built) wins.
Naming is generic to the DQ estate. The exemplar state is STATE_A.

## 1. Problem

- Today every compiled rule is approved by `TEST-GENESIS` (`runtime._test_genesis`), and the configuration in effect is whatever the files say: overlays, profile params and packs, feeds, element bindings, policy.
- A one-line edit to `overlays.yaml` therefore changes what gates a Medicaid release, and no second person ever sees it.
- `can_release` clause 2 checks that every rule which *wrote* a result is approved. It does not check that every *required* rule ran (RED-3).

## 2. Model

### 2.1 Configuration artifacts (control plane)

`dq_ctl.config_artifacts(artifact_id, version, kind, content jsonb, content_hash, cr_id, at)` is append-only.

| kind | artifact_id | content |
|---|---|---|
| OVERLAY | `<ST>:overlay:<RULE_ID>` | the overlay (verb, thresholds / applies_when / priority, reason, expires) |
| PROFILE | `<ST>:profile` | packs + params |
| FEEDS | `<ST>:feeds` | feed manifest |
| BINDINGS | `<ST>:elements` | entity → relation and element → column map |
| POLICY | `<ST>:policy` | mode policy (validated: TA_WARN maps like PASS) |
| RULESET | `<ST>:ruleset` | the list of compiled `rule@entity` and their effective hashes (the *impact* of a change) |

The **effective configuration** of a state is the latest APPROVED version of each artifact. The runtime compiles rules ONLY from it: `compile_rules(state, cfg=effective_cfg)`. The files under `config/state/<ST>/` are no longer authority. They are the *seed* proposed once at genesis.

### 2.2 Change requests

- **Tables.** `dq_ctl.config_change_requests(cr_id, state, author, origin HUMAN|PROPOSER|GENESIS, rationale, items jsonb, impact jsonb, base_versions jsonb, at)` is append-only. So is `dq_ctl.config_cr_events(cr_id, event PROPOSED|APPROVED|REJECTED|WITHDRAWN|APPLIED|STALE, person_id, jti UNIQUE, at)`.
- **Items.** Each `items[]` entry is `{artifact_id, kind, op: PUT|DELETE, content}`.
- **Impact.** `impact` is computed by the server when the request is proposed, by compiling the state *with* the change:
  - the rules whose effective hash changes (old → new);
  - rules that become or stop being ACTIVE / NOT_APPLICABLE / STATE_WAIVED / gating;
  - a no-loosening verdict (any loosening is refused at propose time, as overlays already are).
- **Approval.**
  - The approver must be a **different person** from the author (the database enforces this).
  - The approver holds the governance duty (`dq-duty-governance-rules` token group AND a roster grant).
  - The approval is a **step-up** bound to `sha256(cr_id, items hash, impact hash, base_versions)`. The approver therefore signs exactly the diff and impact they saw.
  - The step-up jti is single-use.
- **Staleness.** If any `base_versions` entry is no longer the current version when the request is approved, the request becomes STALE (optimistic concurrency) and must be re-proposed.
- **Apply (the registry service, `dq_registry_svc`, a new role).** Applying writes the new artifact versions plus `registry_events` APPROVED for every new rule hash in the impact, with `decision_ref = cr_id`. The approver approved those hashes explicitly, because the impact was in the signed challenge. The runtime then reloads the configuration.
- **PROPOSER-origin requests** (the onboarding proposer) are authored by the proposing *person* who ran it. The proposer is a tool that drafts and is never an approver.

### 2.3 Genesis

- On first start of a state, the seed files are proposed as one GENESIS request by a named person.
- A different person ratifies it with a step-up.
- Until then the state has **no effective configuration**: no rule compiles, and every unit holds (fail closed).
- Tests keep `test_genesis=True`, which writes `TEST-GENESIS` artifacts. It is labelled and test-only. The Tower already shows "TEST_GENESIS (T1)" versus "RATIFIED".

### 2.4 Release fails closed (clause 2b)

- For a unit, the **required** set is every ACTIVE, gating (not advisory) row or file rule of the effective ruleset whose relation equals the unit's node.
- If any required rule has no result at the unit's latest `eval_version`, `can_release` returns `2b: approved rule X wrote no result`.
- NOT_IN_SCOPE counts as a result. NOT_APPLICABLE / STATE_WAIVED rules are not required.

## 3. Part B open gaps (D2)

- **Expiry for every overlay verb.** An expired overlay is ignored and reported as `EXPIRED` in `describe()` and on the Rules screen. `waive` already has this; `narrow`, `tighten`, `promote` and `raise_priority` gain it.
- **Unknown rule id.** An overlay keyed to a rule id that is not in the enabled catalog is refused at compile time and at propose time.
- **One disposition per compiled rule per unit.** `compile_rules` asserts that no two ACTIVE compiled rules share `(id, relation)`. Results stay keyed by `rule_id`, which is unique per node by that assertion.

## 4. Onboarding binding proposer (D3, reference-codebase pattern 4, clean-room)

- **Input.** A **schema profile**: per relation, per column, name, SQL type, null fraction, distinct count, min/max length, and *character-class shape*, e.g. `9N`, `10N`, `A9N`, `DATE`. It is computed in the data plane by `profile_relation()`. No value leaves; only aggregates and shape classes.
- **Output.** A PROPOSER-origin change request with a BINDINGS item (and a FEEDS item marking relations found):
  - one proposed column per canonical element, with a score and the reasons;
  - unmatched elements left out, so they surface as NOT_BOUND or NOT_APPLICABLE and never as a guess.
- **Scoring.** Deterministic. Name similarity to the element id, its X12 / NCPDP locator words and a synonym list; type compatibility; and shape compatibility with the element contract (e.g. a 10-digit shape for an NPI contract).
- **LLM.** None in the decision path. The provider stays NullProvider, and any future LLM suggestion would arrive as one more scored reason.
- **Approval.** A person must approve the request (two-person, as above). Nothing takes effect until then.

## 5. API contract (for the Tower seat)

**Change requests**
- `GET  /api/v1/governance/state` returns the effective config summary per artifact (version, content_hash, cr_id, approved_by, at) and `genesis_status`.
- `GET  /api/v1/governance/change-requests?status=` returns a list of `{cr_id, state, author, origin, rationale, status, at, items:[{artifact_id, kind, op}], impact_summary}`.
- `GET  /api/v1/governance/change-requests/{cr_id}` returns the full items (content and diff against the current version), the impact (rule changes table) and the events.
- `POST /api/v1/governance/change-requests` takes `{state, rationale, items:[...]}` and returns `{cr_id, impact}`. Loosening returns 422.
- `POST /api/v1/governance/change-requests/{cr_id}/challenge` takes `{verdict}` and returns `{challenge, step_up_url}`.
- `POST /api/v1/governance/change-requests/{cr_id}/decide` takes `{verdict: APPROVED|REJECTED, step_up_token}`. It returns 403 when the approver is the author or lacks the duty, 409 when the request is STALE or a replay, and 428 when the step-up is missing.

**Onboarding**
- `POST /api/v1/onboarding/profile` takes `{relations:[...]}` and returns the schema profile (aggregates only).
- `POST /api/v1/onboarding/propose-bindings` takes `{state, schema_profile, rationale}` and returns `{cr_id, proposals:[{element, column, relation, score, reasons}], unmatched:[element]}`.

**Rules, coverage, agent, measures**
- `GET  /api/v1/rules` (exists) gains `gating`, `overlay_status`, `expires`, `approved_by`, `cr_id`.
- `GET  /api/v1/coverage` (exists) includes `basic`.
- The agent endpoints exist: `/api/v1/agent/{state,blackboard,memory}` and `known-issues`.
- `GET  /api/v1/measures/series?rule_id=&node=` returns, for one measure, the latest value per period in period order. It carries thresholds, the pinned baseline median and the cell count. Small cells (denominator < 11) carry `value: null, suppressed: true`.

## 6. Tower screens (D4, Next.js; the existing mock server is extended)

1. **Change requests.** A list and a detail view. The detail shows the diff per item, the impact table (rule, old → new hash, status change) and the events. Approve and reject go through StepUp. The author's own request shows "awaiting a second person".
2. **Rules.** The compiled catalog with status, reason, gating (advisory / gate), overlay and expiry, the effective hash and the approving request. It can be filtered by domain, dimension and status.
3. **Coverage.** The basic-DQ matrix (domain × dimension, with EXERCISED / DECLARED_NOT_EXERCISED / EMPTY) plus the requirement inventory with its coverage states.
4. **Agent.** Agent state per batch; the blackboard timeline by seat (rendered text, template, hash-chain status); memory (baselines, known issues with propose / approve).
5. **Measures.** One row per measure (latest value, threshold, disposition, longitudinal) and a dependency-free SVG trend chart with the [min, max] band and baseline median. Suppressed cells are shown as gaps.

## 7. Done means

- A change to an overlay goes PROPOSED → (refused for the author) → APPROVED by a second person with step-up → APPLIED. The rule's effective hash changes and the runtime compiles the new version. Releases then use it.
- An unapproved edit to a file changes nothing.
- Clause 2b holds a unit whose required rule did not run.
- The proposer onboards a differently-named data plane (the XX fixture shape) to a request whose approved bindings compile.
- The five screens pass build, lint, typecheck and a live smoke test.
- The full test suite and the agent evals pass, unit outcomes on w1 are unchanged, both red teams are run and adjudicated, and the receipt and sync are done.
