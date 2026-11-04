/**
 * MOCK fixtures. Every object below is shaped exactly like docs/W1_SPINE.md §3.
 * Where the spine leaves a shape open (abc_before_after elements, audit rows, proposed_actions.params,
 * pending decision), fields carry an `x_` prefix and are listed as contract ambiguities.
 *
 * All data is synthetic (state ZZ). Sample tokens are opaque HMAC-style tokens; no values exist here.
 */
import type {
  AbcRow,
  AgentStateRow,
  BoardEntry,
  CoverageResponse,
  CrDetail,
  DecisionCard,
  GovArtifact,
  LookbackItem,
  Tenant,
  AgentRow,
  AutonomyRow,
  CdeRow,
  ClassificationRow,
  EvalRun,
  Explanation,
  FeedItem,
  MonitorRow,
  MonitorSeries,
  ParallelRunRow,
  PromotionMinimums,
  SimRunSummary,
  Me,
  MeasureRow,
  MemoryRow,
  Pipelines,
  QueueCluster,
  RuleRow,
} from "../types";

const now = Date.now();
const iso = (minFromNow: number) => new Date(now + minFromNow * 60_000).toISOString();

export const BATCH_ENC = "B-ZZ-20261019-ENC-01";
export const BATCH_ELIG = "B-ZZ-20261019-ELIG-01";

export interface MockUser {
  username: string;
  me: Me;
}

/**
 * Mirrors app.py /me: `duties` are "duty:AUTHORITY" (token claim AND roster); `authorities` is the effective
 * release scope, which never includes AUTH_INTAKE. Decisions check `release:<authority>` per unit authority.
 */
export const ROSTER: MockUser[] = [
  {
    username: "alice.maker",
    me: {
      person_id: "P-ZZ-1001",
      name: "Alice Ortega",
      duties: ["audit:AUTH_AUDIT", "governance:AUTH_RULES", "operations:AUTH_OPERATIONS", "release:AUTH_INTAKE", "release:AUTH_MEDICAID"],
      authorities: ["AUTH_MEDICAID"],
    },
  },
  {
    username: "bob.checker",
    me: {
      person_id: "P-ZZ-1002",
      name: "Bob Nakamura",
      duties: ["governance:AUTH_RULES", "release:AUTH_INTAKE", "release:AUTH_MEDICAID"],
      authorities: ["AUTH_MEDICAID"],
    },
  },
  {
    username: "carol.reviewer",
    me: {
      person_id: "P-ZZ-1003",
      name: "Carol Singh",
      duties: ["governance:AUTH_RULES", "release:AUTH_MEDICAID"],
      authorities: ["AUTH_MEDICAID"],
    },
  },
  {
    // No intake duty: two MEDICAID-only approvals on an intake-failed batch end AWAITING_COVERAGE.
    username: "dave.approver",
    me: {
      person_id: "P-ZZ-1004",
      name: "Dave Okafor",
      duties: ["release:AUTH_MEDICAID"],
      authorities: ["AUTH_MEDICAID"],
    },
  },
  {
    // Audit duty only: scoped endpoints answer 403 (no effective release scope).
    username: "erin.auditor",
    me: {
      person_id: "P-ZZ-1005",
      name: "Erin Walsh",
      duties: ["audit:AUTH_AUDIT"],
      authorities: [],
    },
  },
  {
    // SNAP-only release duty: K1's D7 scope test (a Medicaid-only pipeline's profile answers 403, not a redacted
    // 200). frank.snap holds no AUTH_MEDICAID duty, so he cannot see any other Part E/F screen's MEDICAID data.
    username: "frank.snap",
    me: {
      person_id: "P-ZZ-1006",
      name: "Frank Ibori",
      duties: ["release:AUTH_SNAP"],
      authorities: ["AUTH_SNAP"],
    },
  },
];

export const U1 = "ZZ:MEDICAID:a3f19c:chain=ENC0001842";
export const U2 = "ZZ:MEDICAID:a3f19c:chain=ENC0001907";
export const U3 = "ZZ:MEDICAID:a3f19c:elig=PSK-00418";
export const U4 = "ZZ:MEDICAID:a3f19c:chain=ENC0002210";
export const U5 = "ZZ:INTAKE:7be201:file=837P_ZZMCOB_20261019_1";

const tok = (seed: string, n: number) =>
  Array.from({ length: n }, (_, i) => {
    let h = 2166136261;
    const s = `${seed}:${i}`;
    for (let j = 0; j < s.length; j++) h = Math.imul(h ^ s.charCodeAt(j), 16777619) >>> 0;
    return `tk_e3_${h.toString(16).padStart(8, "0")}${((h * 31) >>> 0).toString(16).padStart(8, "0")}`;
  }).sort(); // §9: samples are ordered by token

export const abcRow = (
  x_phase: "before" | "after",
  hop: string,
  src_in: number,
  tgt_out: number,
  extra: Partial<AbcRow> = {},
) => {
  const r = {
    x_phase,
    hop,
    measure: "record_count",
    program: "MEDICAID",
    submitter_id: "ZZ-MCO-A",
    src_in,
    tgt_out,
    rejected: 0,
    quarantined: 0,
    deduplicated: 0,
    filtered: 0,
    held: 0,
    ...extra,
  };
  const residual = r.src_in - (r.tgt_out + r.rejected + r.quarantined + r.deduplicated + r.filtered + r.held);
  return { ...r, residual, disposition: residual === 0 ? "PASS" : "FAIL" };
};

export function initialCards(): Record<string, DecisionCard> {
  return {
    [U1]: {
      unit_key: U1,
      batch_id: BATCH_ENC,
      pipeline: "P_ENCOUNTER",
      node: "silver.encounter_chain",
      program: "MEDICAID",
      authorities: ["AUTH_MEDICAID"],
      state: "HELD",
      state_version: 7,
      fingerprint: "9c41e0b7d2a85f3e61c07a94be2d13f8a6c5e0927b4d1f38e2a960c7d5b48e1f",
      fp_epoch: 3,
      required_mode: "H-IN-2",
      mode_trace: [
        "policy[AUTH_MEDICAID][FAIL] = H-IN",
        "floor: FAIL never autonomous -> H-IN",
        "floor: ABC failed in batch -> H-IN-2",
      ],
      approvals: { required: 2, have: 0, makers: [] },
      dispositions: [
        {
          rule_id: "R837.CLM02_LINE_SUM",
          rule_hash: "sha256:4be1a9c0d7e2",
          check_class: "transaction_structure",
          priority: "High",
          disposition: "FAIL",
          evaluated: 412,
          failed: 3,
          metric: 3,
          threshold: 0,
          sample_tokens: tok("clm02", 3),
        },
        {
          rule_id: "R837.FREQ7_NO_F8",
          rule_hash: "sha256:77a0c31f9b44",
          check_class: "transaction_structure",
          priority: "High",
          disposition: "FAIL",
          evaluated: 58,
          failed: 1,
          metric: 1,
          threshold: 0,
          sample_tokens: tok("freq7", 1),
        },
        {
          rule_id: "RPRV.NPI_LUHN",
          rule_hash: "sha256:c03d8e112a9f",
          check_class: "validity",
          priority: "High",
          disposition: "PASS",
          evaluated: 412,
          failed: 0,
          metric: 0,
          threshold: 0,
          sample_tokens: [],
        },
        {
          rule_id: "RELG.SVC_OUTSIDE_ELIG",
          rule_hash: "sha256:19fe0b6a2c71",
          check_class: "referential",
          priority: "Medium",
          disposition: "TA_WARN",
          evaluated: 1290,
          failed: 14,
          metric: 0.0109,
          threshold: 0.01,
          sample_tokens: tok("svc", 5),
        },
      ],
      abc_before_after: [
        abcRow("before", "SILVER", 1290, 1290),
        abcRow("after", "SILVER", 1290, 1286, { quarantined: 4 }),
      ],
      dependencies: [
        { domain: "eligibility", policy: "WAIT", state: "RELEASED" },
        { domain: "provider", policy: "AS_OF_CUTOFF", state: "RELEASED" },
      ],
      proposed_actions: [
        {
          verb: "QUARANTINE_ROWS",
          params: { scope: "CHAIN", x_row_seqs: [10441, 10442, 10443, 10519] },
          dry_run: { rows_quarantined: 4, chains: 2 },
        },
        { verb: "NOTIFY_SUBMITTER", params: { submitter_id: "ZZ-MCO-A" }, dry_run: { rows_quarantined: 0, chains: 0 } },
      ],
      llm_hypothesis: null, // app.py sends null (no hypothesis provider is wired); F-30 aligns the mock
      allowed_decisions: ["RELEASE", "REJECT", "HOLD", "QUARANTINE_AND_RELEASE"],
      sla_due: iso(95),
      x_pending_decision: null,
    },
    [U2]: {
      unit_key: U2,
      batch_id: BATCH_ENC,
      pipeline: "P_ENCOUNTER",
      node: "silver.encounter_chain",
      program: "MEDICAID",
      authorities: ["AUTH_MEDICAID"],
      state: "HELD",
      state_version: 12,
      fingerprint: "0d7be5a1c93f4e28b61a07d9c3e5f2840a1b6c8d9e2f3a4b5c6d7e8f90a1b2c3",
      fp_epoch: 3,
      required_mode: "H-IN-2",
      mode_trace: [
        "policy[AUTH_MEDICAID][FAIL] = H-IN",
        "floor: FAIL never autonomous -> H-IN",
        "floor: ABC failed in batch -> H-IN-2",
      ],
      approvals: { required: 2, have: 1, makers: [{ person_id: "P-ZZ-1001", seat: "MAKER" }] },
      dispositions: [
        {
          rule_id: "R837.CLM02_LINE_SUM",
          rule_hash: "sha256:4be1a9c0d7e2",
          check_class: "transaction_structure",
          priority: "High",
          disposition: "FAIL",
          evaluated: 97,
          failed: 2,
          metric: 2,
          threshold: 0,
          sample_tokens: tok("u2clm", 2),
        },
        {
          rule_id: "R837.HL_PARENT_BROKEN",
          rule_hash: "sha256:e2c4f6a81b03",
          check_class: "transaction_structure",
          priority: "High",
          disposition: "PASS",
          evaluated: 97,
          failed: 0,
          metric: 0,
          threshold: 0,
          sample_tokens: [],
        },
      ],
      abc_before_after: [
        abcRow("before", "SILVER", 97, 97),
        abcRow("after", "SILVER", 97, 95, { quarantined: 2 }),
      ],
      dependencies: [{ domain: "eligibility", policy: "WAIT", state: "RELEASED" }],
      proposed_actions: [
        {
          verb: "QUARANTINE_ROWS",
          params: { scope: "ROW", x_row_seqs: [20877, 20878] },
          dry_run: { rows_quarantined: 2, chains: 1 },
        },
      ],
      llm_hypothesis: null,
      allowed_decisions: ["HOLD"],
      sla_due: iso(-12),
      x_pending_decision: {
        decision_id: "D-ZZ-000311",
        decision: "QUARANTINE_AND_RELEASE",
        rationale:
          "Two lines on one chain do not roll up to CLM02. Quarantine those two rows and release the remaining 95; submitter notified separately.",
        maker_person_id: "P-ZZ-1001",
        quarantine_row_seqs: [20877, 20878],
      },
    },
    [U3]: {
      unit_key: U3,
      batch_id: BATCH_ELIG,
      pipeline: "P_ELIGIBILITY",
      node: "silver.eligibility_span",
      program: "MEDICAID",
      authorities: ["AUTH_MEDICAID"],
      state: "HELD",
      state_version: 4,
      fingerprint: "5e2a91f0c7b3d46e8a1f02c9b7d3e5a4f6c8b0d2e4f6a8c0b2d4e6f8a0c2e4f6",
      fp_epoch: 3,
      required_mode: "H-IN",
      mode_trace: [
        "policy[AUTH_MEDICAID][FAIL] = H-IN",
        "floor: FAIL never autonomous -> H-IN",
      ],
      approvals: { required: 1, have: 0, makers: [] },
      dispositions: [
        {
          rule_id: "RELG.DOB_AFTER_ELIG_START",
          rule_hash: "sha256:5a6b7c8d9e0f",
          check_class: "consistency",
          priority: "High",
          disposition: "FAIL",
          evaluated: 1,
          failed: 1,
          metric: 1,
          threshold: 0,
          sample_tokens: tok("dob", 1),
        },
        {
          rule_id: "RELG.ELIG_OVERLAP_SAME_TYPE",
          rule_hash: "sha256:0f1e2d3c4b5a",
          check_class: "consistency",
          priority: "High",
          disposition: "PASS",
          evaluated: 3,
          failed: 0,
          metric: 0,
          threshold: 0,
          sample_tokens: [],
        },
      ],
      abc_before_after: [abcRow("before", "SILVER", 3, 3), abcRow("after", "SILVER", 3, 3)],
      dependencies: [],
      proposed_actions: [
        { verb: "REQUEST_RESEND", params: { partner: "ZZ-IE" }, dry_run: { rows_quarantined: 0, chains: 0 } },
      ],
      llm_hypothesis: null, // app.py sends null (no hypothesis provider is wired); F-30 aligns the mock
      allowed_decisions: ["RELEASE", "REJECT", "HOLD"],
      sla_due: iso(240),
      x_pending_decision: null,
    },
    [U4]: {
      unit_key: U4,
      batch_id: BATCH_ENC,
      pipeline: "P_ENCOUNTER",
      node: "bronze.encounter_header",
      program: "MEDICAID",
      authorities: ["AUTH_MEDICAID"],
      state: "ERROR",
      state_version: 2,
      fingerprint: "c1d2e3f4a5b6c7d8e9f0a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2",
      fp_epoch: 3,
      required_mode: "H-IN-2",
      mode_trace: [
        "policy[AUTH_MEDICAID][ERROR] = H-IN",
        "floor: ERROR never autonomous -> H-IN",
        "floor: ABC failed in batch -> H-IN-2",
      ],
      approvals: { required: 2, have: 0, makers: [] },
      dispositions: [
        {
          rule_id: "RENV.SE_COUNT_MISMATCH",
          rule_hash: "sha256:aa01bb02cc03",
          check_class: "envelope",
          priority: "High",
          disposition: "ERROR",
          evaluated: 1,
          failed: 1,
          metric: null,
          threshold: null,
          sample_tokens: tok("se", 1),
        },
      ],
      abc_before_after: [abcRow("before", "BRONZE", 61, 0, { held: 61 })],
      dependencies: [],
      proposed_actions: [],
      llm_hypothesis: null,
      allowed_decisions: ["REJECT"],
      sla_due: iso(30),
      x_pending_decision: null,
    },
    [U5]: {
      unit_key: U5,
      batch_id: BATCH_ENC,
      pipeline: "P_INTAKE",
      node: "raw.x12_segment",
      program: "MEDICAID",
      authorities: ["AUTH_INTAKE", "AUTH_MEDICAID"],
      state: "HELD",
      state_version: 3,
      fingerprint: "77e8f9a0b1c2d3e4f5a6b7c8d9e0f1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d7e8",
      fp_epoch: 3,
      required_mode: "H-IN-2",
      mode_trace: [
        "no policy for AUTH_INTAKE -> H-IN-2 (fail closed)",
        "policy[AUTH_MEDICAID][UNBALANCEABLE] = H-IN",
        "floor: UNBALANCEABLE never autonomous -> H-IN",
        "floor: ABC failed in batch -> H-IN-2",
      ],
      approvals: { required: 2, have: 0, makers: [] },
      dispositions: [
        {
          rule_id: "RTRN.TRANSLATOR_REJECT",
          rule_hash: "sha256:9d8c7b6a5f4e",
          check_class: "envelope",
          priority: "High",
          disposition: "UNBALANCEABLE",
          evaluated: 4,
          failed: 1,
          metric: 1,
          threshold: 0,
          sample_tokens: tok("trn", 1),
        },
        {
          rule_id: "RENV.DUP_INTERCHANGE",
          rule_hash: "sha256:3e4f5a6b7c8d",
          check_class: "envelope",
          priority: "High",
          disposition: "PASS",
          evaluated: 1,
          failed: 0,
          metric: 0,
          threshold: 0,
          sample_tokens: [],
        },
      ],
      abc_before_after: [
        abcRow("before", "TRANSLATOR", 4, 3, { submitter_id: "ZZ-MCO-B", rejected: 1, program: "MEDICAID" }),
      ],
      dependencies: [],
      proposed_actions: [
        { verb: "REQUEST_RESEND", params: { partner: "ZZ-MCO-B" }, dry_run: { rows_quarantined: 0, chains: 0 } },
      ],
      llm_hypothesis: null,
      allowed_decisions: ["REJECT", "HOLD"],
      sla_due: iso(480),
      x_pending_decision: null,
    },
  };
}

/**
 * Batches whose intake verdict failed (U5's translator reject sits in BATCH_ENC). As in DecisionService,
 * units in these batches need AUTH_INTAKE coverage from some approver before a decision applies.
 */
export const INTAKE_FAILED_BATCHES = new Set([BATCH_ENC]);

/** Units whose first DECIDE is answered with HTTP 409 to exercise the stale path in mock mode. */
export const STALE_ON_FIRST_DECIDE = new Set([U3]);

export const CLUSTERS: Omit<QueueCluster, "unit_count">[] = [
  {
    cluster_id: "CL-ENC-CLM02",
    authority: "AUTH_MEDICAID",
    rule_ids: ["R837.CLM02_LINE_SUM", "R837.FREQ7_NO_F8"],
    node: "silver.encounter_chain",
    failure_signature: "CLM02 != sum(SV102) | freq 7 without REF*F8",
    worst_disposition: "FAIL",
    required_mode: "H-IN-2",
    sla_due: iso(-12),
    units: [U1, U2],
  },
  {
    cluster_id: "CL-ELIG-DOB",
    authority: "AUTH_MEDICAID",
    rule_ids: ["RELG.DOB_AFTER_ELIG_START"],
    node: "silver.eligibility_span",
    failure_signature: "dob > eff_start",
    worst_disposition: "FAIL",
    required_mode: "H-IN",
    sla_due: iso(240),
    units: [U3],
  },
  {
    cluster_id: "CL-ENV-SE",
    authority: "AUTH_MEDICAID",
    rule_ids: ["RENV.SE_COUNT_MISMATCH"],
    node: "bronze.encounter_header",
    failure_signature: "SE01 != segment count",
    worst_disposition: "ERROR",
    required_mode: "H-IN-2",
    sla_due: iso(30),
    units: [U4],
  },
  {
    cluster_id: "CL-INTAKE-TRN",
    authority: "AUTH_INTAKE",
    rule_ids: ["RTRN.TRANSLATOR_REJECT"],
    node: "raw.x12_segment",
    failure_signature: "999 IK5=R for ST 0003",
    worst_disposition: "UNBALANCEABLE",
    required_mode: "H-IN-2",
    sla_due: iso(480),
    units: [U5],
  },
];

export const PIPELINES: Pipelines = {
  pipelines: [
    {
      id: "P_INTAKE",
      platform: "duckdb",
      orchestrator: "local",
      domains: ["intake"],
      gated_nodes: [
        { node: "raw.x12_segment", state: "HELD" },
        { node: "raw.flat_record", state: "RELEASED" },
      ],
    },
    {
      id: "P_ELIGIBILITY",
      platform: "duckdb",
      orchestrator: "local",
      domains: ["eligibility"],
      gated_nodes: [
        { node: "bronze.ie_segment", state: "RELEASED" },
        { node: "silver.eligibility_span", state: "HELD" },
      ],
    },
    {
      id: "P_ENROLLMENT",
      platform: "duckdb",
      orchestrator: "local",
      domains: ["enrollment"],
      gated_nodes: [{ node: "bronze.enrollment_834", state: "RELEASED" }],
    },
    {
      id: "P_PROVIDER",
      platform: "duckdb",
      orchestrator: "local",
      domains: ["provider"],
      gated_nodes: [{ node: "silver.provider", state: "RELEASED" }],
    },
    {
      id: "P_ENCOUNTER",
      platform: "duckdb",
      orchestrator: "local",
      domains: ["encounter"],
      gated_nodes: [
        { node: "bronze.encounter_header", state: "ERROR" },
        { node: "silver.encounter_chain", state: "HELD" },
        { node: "gold.tmsis_cot_subset", state: "PENDING_DEPENDENCY" },
      ],
    },
  ],
  order: ["P_INTAKE", "P_ELIGIBILITY", "P_ENROLLMENT", "P_PROVIDER", "P_ENCOUNTER"],
  dependencies: [
    { domain: "eligibility", depends_on: "intake", policy: "WAIT", temporal_binding: "batch" },
    { domain: "enrollment", depends_on: "eligibility", policy: "WAIT", temporal_binding: "coverage_start..coverage_end" },
    { domain: "encounter", depends_on: "eligibility", policy: "WAIT", temporal_binding: "svc_date in eff_start..eff_end" },
    { domain: "encounter", depends_on: "provider", policy: "AS_OF_CUTOFF", temporal_binding: "calendar cut-off" },
    { domain: "encounter", depends_on: "intake", policy: "WAIT", temporal_binding: "batch" },
  ],
};

const abc = (
  hop: string,
  src_in: number,
  tgt_out: number,
  p: Partial<AbcRow> & { x_batch_id?: string } = {},
): AbcRow & { x_batch_id: string } => {
  const r = {
    hop,
    measure: "record_count",
    program: "MEDICAID",
    submitter_id: "ZZ-MCO-A",
    src_in,
    tgt_out,
    rejected: 0,
    quarantined: 0,
    deduplicated: 0,
    filtered: 0,
    held: 0,
    x_batch_id: BATCH_ENC,
    ...p,
  };
  const residual = r.src_in - (r.tgt_out + r.rejected + r.quarantined + r.deduplicated + r.filtered + r.held);
  return { ...r, residual, disposition: residual === 0 ? "PASS" : "FAIL" };
};

/** `x_batch_id` lets the mock filter by ?batch_id=; the spine row has no batch column (ambiguity A5). */
export const ABC_ROWS = [
  abc("TRANSLATOR", 4, 3, { submitter_id: "ZZ-MCO-B", rejected: 1 }),
  abc("TRANSLATOR", 6, 6),
  abc("FILE", 6, 6),
  abc("RAW", 2214, 2214),
  abc("BRONZE", 1351, 1290, { held: 61 }),
  abc("SILVER", 1290, 1286, { quarantined: 4 }),
  abc("GOLD", 1286, 1279, { deduplicated: 5 }),
  abc("FILE", 1, 1, { submitter_id: "ZZ-IE", x_batch_id: BATCH_ELIG }),
  abc("RAW", 5120, 5120, { submitter_id: "ZZ-IE", x_batch_id: BATCH_ELIG }),
  abc("BRONZE", 5120, 4977, { submitter_id: "ZZ-IE", filtered: 143, x_batch_id: BATCH_ELIG }),
  abc("SILVER", 4977, 4975, { submitter_id: "ZZ-IE", held: 1, x_batch_id: BATCH_ELIG }),
  abc("BRONZE", 143, 143, { program: "SNAP", submitter_id: "ZZ-IE", x_batch_id: BATCH_ELIG }),
];

// =====================================================================================================
// Part D (PARTD_DESIGN §5): governance, rules, coverage, agent and measures. Synthetic state ZZ only;
// rule ids are taken from the repository catalog. Shapes follow dqagent/api/app.py.
// =====================================================================================================

export const MOCK_STATE = "ZZ";

/** 64-hex FNV-based digest for fixture hashes. MOCK ONLY — not sha256. */
export function fx(seed: string, len = 64): string {
  let out = "";
  for (let lane = 0; out.length < len; lane++) {
    let h = 2166136261 ^ lane;
    for (let i = 0; i < seed.length; i++) h = Math.imul(h ^ seed.charCodeAt(i), 16777619) >>> 0;
    out += h.toString(16).padStart(8, "0");
  }
  return out.slice(0, len);
}

const day = (d: number) => new Date(now + d * 86_400_000).toISOString().slice(0, 10);

type RuleSeed = [id: string, pack: string, entity: string, kind: string, status: string, reason: string, relation: string | null, title: string, dimension: string | null, gating: string, req: string[]];
const RULE_SEEDS: RuleSeed[] = [
  ["ENV-ISA-WIDTH", "federal/x12_envelope", "ENCOUNTER_HEADER", "file", "ACTIVE", "", "bronze.encounter_header", "ISA is the fixed-width interchange header", "validity", "gate", ["X12-ENV-001"]],
  ["R-ENC-001", "federal_pack/w1", "ENCOUNTER_HEADER", "row", "ACTIVE", "", "bronze.encounter_header", "Header total equals the sum of line charges", "consistency", "gate", ["TMSIS-CLM-010"]],
  ["R-ENC-002", "federal_pack/w1", "ENCOUNTER_HEADER", "row", "ACTIVE", "", "bronze.encounter_header", "Member id resolves to an eligible member", "referential", "gate", ["TMSIS-CLM-011"]],
  ["R-ENC-003", "federal_pack/w1", "ENCOUNTER_LINE", "row", "ACTIVE", "", "bronze.encounter_line", "Service date not after the paid date", "validity", "gate", ["TMSIS-CLM-012"]],
  ["R-ENC-004", "federal_pack/w1", "ENCOUNTER_LINE", "row", "ACTIVE", "", "bronze.encounter_line", "Procedure code present on every line", "completeness", "gate", ["TMSIS-CLM-013"]],
  ["R-ENC-007", "federal_pack/w1", "ENCOUNTER_HEADER", "row", "NOT_APPLICABLE", "element CLM.ADJ_IND not delivered on " + day(0), "bronze.encounter_header", "Adjustment indicator is a valid code", "validity", "gate", ["TMSIS-CLM-020"]],
  ["R-ELG-001", "federal_pack/w1", "ELIGIBILITY", "row", "ACTIVE", "", "bronze.ie_segment", "Eligibility span start precedes end", "validity", "gate", ["TMSIS-ELG-001"]],
  ["R-ELG-002", "federal_pack/w1", "ELIGIBILITY", "row", "ACTIVE", "", "bronze.ie_segment", "One open span per member and program", "uniqueness", "gate", ["TMSIS-ELG-002"]],
  ["R-ELG-003", "federal_pack/w1", "ELIGIBILITY", "row", "NOT_BOUND", "element ELG.RACE_CD is not mapped by the state", null, "Race code is a valid code", "validity", "gate", ["TMSIS-ELG-003"]],
  ["R-834-001", "federal_pack/w1", "ENROLLMENT_834", "row", "ACTIVE", "", "bronze.enrollment_834", "INS03 maintenance code is valid", "validity", "gate", ["X12-834-001"]],
  ["SA-834-INS03", "state/ZZ", "ENROLLMENT_834", "row", "ACTIVE", "", "bronze.enrollment_834", "State subset of INS03 codes", "validity", "advisory", ["ZZ-834-001"]],
  ["SA-ENC-PLAN-PAID-PRESENT", "state/ZZ", "ENCOUNTER_LINE", "row", "STATE_WAIVED", "waived: plan-paid amount deferred to phase 2 (SCR-12)", "bronze.encounter_line", "Plan-paid amount present", "completeness", "gate", ["ZZ-ENC-004"]],
  ["B-X-DUP-ACROSS-BATCH", "basic/cross_domain", "ENCOUNTER_HEADER", "row", "ACTIVE", "", "bronze.encounter_header", "No claim id repeats across batches", "uniqueness", "advisory", []],
  ["B-X-SVC-AFTER-DOB", "basic/cross_domain", "ENCOUNTER_LINE", "row", "ACTIVE", "", "bronze.encounter_line", "Service date on or after date of birth", "consistency", "gate", []],
  ["B-X-ADJ-ORIG-KNOWN", "basic/cross_domain", "ENCOUNTER_HEADER", "row", "NOT_APPLICABLE", "element CLM.ORIG_ID not delivered on " + day(0), "bronze.encounter_header", "Adjustment names a known original", "referential", "advisory", []],
  ["M-RULE-7907", "federal/tmsis_measures_preload", "ENCOUNTER_LINE", "measure", "ACTIVE", "", "bronze.encounter_line", "Share of lines with an invalid procedure code", null, "gate", ["TMSIS-DQ-7907"]],
  ["M-RULE-7245", "federal/tmsis_measures_preload", "ELIGIBILITY", "measure", "ACTIVE", "", "bronze.ie_segment", "Share of members with overlapping spans", null, "gate", ["TMSIS-DQ-7245"]],
  ["M-EXP-27-001-1", "federal/tmsis_measures_preload", "ENCOUNTER_HEADER", "measure", "ACTIVE", "", "bronze.encounter_header", "Expenditure: share of zero-paid headers", null, "advisory", ["TMSIS-EXP-27"]],
];

export function initialRules(): RuleRow[] {
  return RULE_SEEDS.map(([id, pack, entity, kind, status, reason, relation, title, dimension, gating, req]) => ({
    id,
    pack,
    entity,
    kind,
    status,
    reason,
    effective_hash: fx(`${id}@${entity}`, 16),
    relation,
    title,
    priority: kind === "measure" ? "Medium" : "High",
    stage: kind === "file" ? "FILE" : "BRONZE",
    dimension,
    req_ids: req,
    analog_of: null,
    gating,
    overlay:
      id === "SA-ENC-PLAN-PAID-PRESENT"
        ? { verb: "waive", status: "ACTIVE", expires: day(45), approved_ref: "SCR-12" }
        : id === "R-834-001"
          ? { verb: "tighten", status: "EXPIRED", expires: day(-3), approved_ref: "SCR-9" }
          : null,
    origin: pack.startsWith("state") ? "STATE" : pack.startsWith("basic") ? "BASIC" : "FEDERAL",
    approved_by_cr: id === "SA-ENC-PLAN-PAID-PRESENT" ? "CR-7c41e09a2b" : "TEST-GENESIS",
  }));
}

export const RULE_ENTITY_DOMAIN: Record<string, string> = {
  ENCOUNTER_HEADER: "CLAIMS_PROFESSIONAL",
  ENCOUNTER_LINE: "CLAIMS_PROFESSIONAL",
  ELIGIBILITY: "ELIGIBILITY",
  ENROLLMENT_834: "ENROLLMENT",
};

export function initialArtifacts(): GovArtifact[] {
  const a = (artifact_id: string, kind: string, version: number, cr_id: string, approved_by: string | null): GovArtifact => ({
    artifact_id,
    kind,
    version,
    op: "PUT",
    content_hash: fx(`${artifact_id}#${version}`),
    cr_id,
    at: iso(-60 * 24 * 3),
    approved_by,
  });
  return [
    a("ZZ:catalog", "CATALOG", 1, "TEST-GENESIS", null),
    a("ZZ:elements", "BINDINGS", 1, "TEST-GENESIS", null),
    a("ZZ:feeds", "FEEDS", 1, "TEST-GENESIS", null),
    a("ZZ:overlay:SA-ENC-PLAN-PAID-PRESENT", "OVERLAY", 1, "CR-7c41e09a2b", "P-ZZ-1002,P-ZZ-1003"),
    a("ZZ:policy", "POLICY", 1, "TEST-GENESIS", null),
    a("ZZ:profile", "PROFILE", 1, "TEST-GENESIS", null),
  ];
}

export const OVERLAY_CURRENT: Record<string, unknown> = {
  "ZZ:overlay:SA-ENC-PLAN-PAID-PRESENT": { verb: "waive", reason: "plan-paid amount deferred to phase 2", approved_ref: "SCR-12", expires: day(45) },
};

/** Change requests as the detail endpoint returns them (list rows are derived from these). */
export function initialChangeRequests(rules: RuleRow[]): CrDetail[] {
  const facts = (id: string, over: Partial<{ hash: string; status: string; gating: string | null }> = {}) => {
    const r = rules.find((x) => x.id === id)!;
    return { hash: r.effective_hash, status: r.status, gating: r.gating, relation: r.relation, kind: r.kind, ...over };
  };
  const rsv = fx("ruleset:v1");
  const base = (id: string, author: string, origin: string, at: number, extra: Partial<CrDetail>): CrDetail => ({
    cr_id: id,
    state: MOCK_STATE,
    author,
    origin,
    loosening: false,
    approvals: 0,
    approvals_required: 1,
    status: "AWAITING_APPROVAL",
    at: iso(at),
    rationale: "",
    items: [],
    impact: { rules: [], loosening_reasons: [], ruleset_before: rsv, ruleset_after: rsv, summary: { changed: 0, rules_total: rules.length } },
    base: { versions: { "ZZ:profile": 1, "ZZ:feeds": 1, "ZZ:elements": 1 }, ruleset: rsv },
    events: [{ event: "PROPOSED", person_id: author, detail: null, at: iso(at) }],
    x_you_are_author: false,
    ...extra,
  });
  return [
    base("CR-3f9a12c7d0", "P-ZZ-1001", "HUMAN", -95, {
      rationale: "Raise the header balancing rule to Critical: the state finance office treats an unbalanced header as a submission defect.",
      items: [
        {
          artifact_id: "ZZ:overlay:R-ENC-001",
          kind: "OVERLAY",
          op: "PUT",
          content: { verb: "raise_priority", priority: "Critical", reason: "state finance priority", approved_ref: "SCR-31" },
          content_hash: fx("CR-3f9a12c7d0:item0"),
          current: null,
        },
      ],
      impact: {
        rules: [{ rule: "ZZ:R-ENC-001@ENCOUNTER_HEADER", before: facts("R-ENC-001"), after: facts("R-ENC-001", { hash: fx("R-ENC-001:crit", 16) }) }],
        loosening_reasons: [],
        ruleset_before: rsv,
        ruleset_after: fx("ruleset:v2a"),
        summary: { changed: 1, rules_total: rules.length },
      },
    }),
    base("CR-a81d04be55", "P-ZZ-1002", "HUMAN", -40, {
      rationale: "Waive the open-span uniqueness rule until the eligibility vendor fixes the overlap defect (ticket ZZ-ELG-118).",
      loosening: true,
      approvals_required: 2,
      items: [
        {
          artifact_id: "ZZ:overlay:R-ELG-002",
          kind: "OVERLAY",
          op: "PUT",
          content: { verb: "waive", reason: "vendor overlap defect ZZ-ELG-118", approved_ref: "SCR-33", expires: day(30) },
          content_hash: fx("CR-a81d04be55:item0"),
          current: null,
        },
      ],
      impact: {
        rules: [
          {
            rule: "ZZ:R-ELG-002@ELIGIBILITY",
            before: facts("R-ELG-002"),
            after: facts("R-ELG-002", { status: "STATE_WAIVED", hash: fx("R-ELG-002:waived", 16) }),
          },
        ],
        loosening_reasons: ["ZZ:overlay:R-ELG-002: verb waive reduces what is checked", "ZZ:R-ELG-002@ELIGIBILITY leaves ACTIVE (STATE_WAIVED)"],
        ruleset_before: rsv,
        ruleset_after: fx("ruleset:v2b"),
        summary: { changed: 1, rules_total: rules.length },
      },
    }),
    base("CR-5b20e7f913", "P-ZZ-1003", "PROPOSER", -25, {
      rationale: "Onboarding proposer draft: metadata-only element bindings for the eligibility extract.",
      items: [
        {
          artifact_id: "ZZ:elements",
          kind: "BINDINGS",
          op: "PUT",
          content: {
            entities: { ELIGIBILITY: "bronze.ie_segment", ENROLLMENT_834: "bronze.enrollment_834" },
            columns: { "ELG.MEMBER_ID": "msis_id", "ELG.SPAN_START": "elig_start_dt", "ELG.SPAN_END": "elig_end_dt", "ELG.RACE_CD": "race_cd" },
          },
          content_hash: fx("CR-5b20e7f913:item0"),
          current: {
            entities: { ELIGIBILITY: "bronze.ie_segment", ENROLLMENT_834: "bronze.enrollment_834" },
            columns: { "ELG.MEMBER_ID": "msis_id", "ELG.SPAN_START": "elig_start_dt", "ELG.SPAN_END": "elig_end_date" },
          },
        },
      ],
      impact: {
        rules: [
          { rule: "ZZ:R-ELG-003@ELIGIBILITY", before: facts("R-ELG-003"), after: facts("R-ELG-003", { status: "ACTIVE", hash: fx("R-ELG-003:bound", 16) }) },
          { rule: "ZZ:R-ELG-001@ELIGIBILITY", before: facts("R-ELG-001"), after: facts("R-ELG-001", { hash: fx("R-ELG-001:rebound", 16) }) },
        ],
        loosening_reasons: [],
        ruleset_before: rsv,
        ruleset_after: fx("ruleset:v2c"),
        summary: { changed: 2, rules_total: rules.length },
      },
    }),
    base("CR-7c41e09a2b", "P-ZZ-1001", "HUMAN", -60 * 24 * 2, {
      rationale: "Waive plan-paid presence until phase 2 delivers the field.",
      loosening: true,
      approvals: 2,
      approvals_required: 2,
      status: "APPLIED",
      items: [
        {
          artifact_id: "ZZ:overlay:SA-ENC-PLAN-PAID-PRESENT",
          kind: "OVERLAY",
          op: "PUT",
          content: OVERLAY_CURRENT["ZZ:overlay:SA-ENC-PLAN-PAID-PRESENT"],
          content_hash: fx("CR-7c41e09a2b:item0"),
          current: OVERLAY_CURRENT["ZZ:overlay:SA-ENC-PLAN-PAID-PRESENT"],
        },
      ],
      impact: {
        rules: [
          {
            rule: "ZZ:SA-ENC-PLAN-PAID-PRESENT@ENCOUNTER_LINE",
            before: facts("SA-ENC-PLAN-PAID-PRESENT", { status: "ACTIVE", hash: fx("SA-PLAN:before", 16) }),
            after: facts("SA-ENC-PLAN-PAID-PRESENT"),
          },
        ],
        loosening_reasons: ["ZZ:overlay:SA-ENC-PLAN-PAID-PRESENT: verb waive reduces what is checked"],
        ruleset_before: fx("ruleset:v0"),
        ruleset_after: rsv,
        summary: { changed: 1, rules_total: rules.length },
      },
      events: [
        { event: "PROPOSED", person_id: "P-ZZ-1001", detail: null, at: iso(-60 * 24 * 2) },
        { event: "APPROVED", person_id: "P-ZZ-1002", detail: null, at: iso(-60 * 24 * 2 + 30) },
        { event: "APPROVED", person_id: "P-ZZ-1003", detail: null, at: iso(-60 * 24 * 2 + 50) },
        { event: "APPLIED", person_id: null, detail: { ruleset_version: rsv }, at: iso(-60 * 24 * 2 + 50) },
      ],
    }),
    base("CR-e09c33a1f4", "P-ZZ-1003", "HUMAN", -60 * 24, {
      rationale: "Tighten the invalid procedure code measure to 0.5%.",
      status: "STALE",
      items: [
        {
          artifact_id: "ZZ:overlay:M-RULE-7907",
          kind: "OVERLAY",
          op: "PUT",
          content: { verb: "tighten", thresholds: { max: 0.005 }, reason: "state target", approved_ref: "SCR-29" },
          content_hash: fx("CR-e09c33a1f4:item0"),
          current: null,
        },
      ],
      events: [
        { event: "PROPOSED", person_id: "P-ZZ-1003", detail: null, at: iso(-60 * 24) },
        { event: "STALE", person_id: null, detail: "base version of ZZ:feeds moved (1 -> 2)", at: iso(-60 * 23) },
      ],
    }),
  ];
}

export function initialCoverage(rules: RuleRow[]): CoverageResponse {
  const DIMS = ["completeness", "validity", "uniqueness", "referential", "consistency", "timeliness"];
  const doms = ["CLAIMS_DENTAL", "CLAIMS_INSTITUTIONAL", "CLAIMS_PROFESSIONAL", "ELIGIBILITY", "ENROLLMENT", "PHARMACY", "PRIOR_AUTH", "PROVIDER", "TPL"];
  const matrix: CoverageResponse["basic"]["matrix"] = {};
  for (const d of doms) {
    matrix[d] = {};
    for (const dim of DIMS) {
      const rs = rules.filter((r) => r.kind !== "measure" && RULE_ENTITY_DOMAIN[r.entity] === d && (r.dimension ?? "validity") === dim);
      const active = rs.filter((r) => r.status === "ACTIVE").length;
      const na = rs.filter((r) => r.status === "NOT_APPLICABLE").length;
      const declared = ["CLAIMS_DENTAL", "CLAIMS_INSTITUTIONAL", "PHARMACY", "PRIOR_AUTH"].includes(d) && ["completeness", "validity"].includes(dim);
      matrix[d]![dim] = rs.length
        ? {
            status: active ? "EXERCISED" : na ? "DECLARED_NOT_EXERCISED" : "NOT_ACTIVE",
            active,
            not_applicable: na,
            other: rs.length - active - na,
            rules: rs.map((r) => `${r.id}@${r.entity}:${r.status}`),
          }
        : declared
          ? { status: "DECLARED_NOT_EXERCISED", active: 0, not_applicable: 2, other: 0, rules: [`B-${d.slice(0, 6)}-${dim.toUpperCase().slice(0, 4)}@${d}:NOT_APPLICABLE`] }
          : { status: "EMPTY" };
    }
  }
  const cells = Object.values(matrix).flatMap((r) => Object.values(r));
  const requirements = rules
    .filter((r) => r.req_ids?.length)
    .map((r) => ({
      req_id: r.req_ids![0]!,
      domain: r.kind === "measure" ? "MEASURES" : r.entity.startsWith("ENC") ? "CLAIMS" : r.entity === "ELIGIBILITY" ? "ELIGIBILITY" : "ENROLLMENT",
      stage: r.stage ?? "BRONZE",
      dimension: r.dimension ?? "validity",
      priority: r.priority ?? "",
      source: r.pack.startsWith("state") ? "ZZ-STATE-GUIDE" : "CMS-TMSIS-DQ",
      requirement_status: "UNVERIFIED",
      coverage: r.status,
      rules: [`${r.id}@${r.entity}`],
    }));
  requirements.push(
    { req_id: "TMSIS-PRV-004", domain: "PROVIDER", stage: "BRONZE", dimension: "validity", priority: "High", source: "CMS-TMSIS-DQ", requirement_status: "UNVERIFIED", coverage: "GAP", rules: [] },
    { req_id: "TMSIS-TPL-002", domain: "TPL", stage: "SILVER", dimension: "referential", priority: "Medium", source: "CMS-TMSIS-DQ", requirement_status: "UNVERIFIED", coverage: "GAP", rules: [] },
    { req_id: "TMSIS-CLM-031", domain: "CLAIMS", stage: "GOLD", dimension: "consistency", priority: "High", source: "CMS-TMSIS-DQ", requirement_status: "UNVERIFIED", coverage: "ANALOG_ACTIVE", rules: ["B-X-SVC-AFTER-DOB@ENCOUNTER_LINE"] },
  );
  const summary: CoverageResponse["summary"] = {};
  for (const q of requirements) {
    const k = `${q.domain}|${q.stage}`;
    summary[k] = summary[k] ?? {};
    summary[k]![q.coverage] = (summary[k]![q.coverage] ?? 0) + 1;
  }
  return {
    requirements,
    summary,
    basic: {
      matrix,
      summary: {
        domains: doms.length,
        exercised_cells: cells.filter((c) => c.status === "EXERCISED").length,
        declared_not_exercised_cells: cells.filter((c) => c.status === "DECLARED_NOT_EXERCISED").length,
        codesets_declared_for_part_e: 7,
      },
    },
  };
}

export const MEASURE_BATCHES = [
  "B-837P_ZZMCOA_20260125_1",
  "B-837P_ZZMCOB_20260201_1",
  "B-837P_ZZMCOA_20260215_2",
  "B-837P_ZZMCOB_20260301_2",
  "B-837P_ZZMCOA_20260315_3",
  "B-837P_ZZMCOB_20260329_3",
  "B-837P_ZZMCOA_20260405_4",
];

/** Rows as /measures returns them (newest first), plus x_long for the series endpoint. */
export function initialMeasures(): (MeasureRow & { x_long: string | null })[] {
  const specs: { rule: string; node: string; th: Record<string, number>; vals: (number | null)[]; dens: number[] }[] = [
    { rule: "M-RULE-7907", node: "bronze.encounter_line", th: { min: 0, max: 0.01 }, vals: [0.004, 0.006, 0.005, null, 0.007, 0.013, 0.009], dens: [412, 388, 455, 7, 430, 401, 397] },
    { rule: "M-EXP-27-001-1", node: "bronze.encounter_header", th: { min: 0, max: 0.3, longitudinal: 0.1 }, vals: [0.12, 0.14, 0.11, 0.13, 0.27, 0.15, 0.31], dens: [23, 22, 27, 20, 17, 25, 21] },
    { rule: "M-RULE-7245", node: "bronze.ie_segment", th: { max: 0.002 }, vals: [0.0, 0.001, null, 0.0, 0.0, 0.001, 0.0], dens: [222, 140, 9, 180, 210, 205, 199] },
  ];
  const disp = (v: number | null, th: Record<string, number>) =>
    v === null ? "NOT_EVALUATED" : (th.max !== undefined && v > th.max) || (th.min !== undefined && v < th.min) ? "FAIL" : v > (th.max ?? 1) * 0.8 ? "TA_WARN" : "PASS";
  const rows: (MeasureRow & { x_long: string | null })[] = [];
  MEASURE_BATCHES.forEach((b, i) => {
    for (const s of specs) {
      const v = s.vals[i] ?? null;
      const den = s.dens[i] ?? 0;
      const prev = i > 0 ? (s.vals[i - 1] ?? null) : null;
      const long = i === 0 || v === null || prev === null ? null : Math.abs(v - prev) > (s.th.longitudinal ?? (s.th.max ?? 1) * 0.5) ? "TA_WARN" : "PASS";
      rows.push({
        batch_id: b,
        node: s.node,
        rule_id: s.rule,
        numerator: v === null ? (den < 11 ? 1 : null) : Math.round(v * den),
        denominator: den,
        value: den < 11 ? (v ?? 0.111) : v,
        thresholds: s.th,
        disposition: den < 11 ? "NOT_EVALUATED" : disp(v, s.th),
        at: iso(-60 * 24 * (MEASURE_BATCHES.length - i)),
        x_long: long,
      });
    }
  });
  return rows.reverse();
}

export function initialAgentState(): AgentStateRow[] {
  return [
    { batch_id: BATCH_ENC, node: "bronze.encounter_header", units: 61, released: 38, held: 23, pending: 0, in_flight: 0, board_entries: 0, hypotheses: 0 },
    { batch_id: BATCH_ENC, node: "bronze.encounter_line", units: 61, released: 57, held: 4, pending: 0, in_flight: 0, board_entries: 0, hypotheses: 0 },
    { batch_id: BATCH_ELIG, node: "bronze.ie_segment", units: 222, released: 221, held: 1, pending: 0, in_flight: 0, board_entries: 0, hypotheses: 0 },
    { batch_id: "B-834_ZZBROKER_20261019_1", node: "bronze.enrollment_834", units: 140, released: 140, held: 0, pending: 0, in_flight: 0, board_entries: 0, hypotheses: 0 },
  ];
}

type Post = Omit<BoardEntry, "seq" | "prev_hash" | "entry_hash" | "at" | "refs" | "text"> & { text: string; refs?: number[] };
const POSTS: Post[] = [
  { batch_id: BATCH_ENC, authority: "AUTH_MEDICAID", seat: "EVALUATOR", person_id: null, kind: "FINDING", subject_type: "RULE", subject_id: "R-ENC-001", template: "RULE_NON_PASS", body: { rule_id: "R-ENC-001", disposition: "FAIL", unit_count: 23, node: "bronze.encounter_header" }, text: "Rule R-ENC-001 is FAIL on 23 unit(s) of bronze.encounter_header." },
  { batch_id: BATCH_ENC, authority: "AUTH_MEDICAID", seat: "TRIAGE", person_id: null, kind: "HYPOTHESIS", subject_type: "SUBMITTER", subject_id: "ZZ-MCO-B", template: "FAILURES_ONE_SUBMITTER", body: { count: 23, rule_id: "R-ENC-001", submitter_id: "ZZ-MCO-B" }, text: "All 23 failing row(s) of R-ENC-001 come from submitter ZZ-MCO-B; a submitter-side defect is likely." },
  { batch_id: BATCH_ENC, authority: "AUTH_MEDICAID", seat: "TRIAGE", person_id: null, kind: "FINDING", subject_type: "MEASURE", subject_id: "M-EXP-27-001-1", template: "MEASURE_OFF_BASELINE", body: { measure_id: "M-EXP-27-001-1", value: 0.31, median: 0.13 }, text: "Measure M-EXP-27-001-1 = 0.31 differs from its baseline median 0.13 (advisory)." },
  { batch_id: BATCH_ELIG, authority: "AUTH_MEDICAID", seat: "EVALUATOR", person_id: null, kind: "FINDING", subject_type: "RULE", subject_id: "R-ELG-002", template: "RULE_NON_PASS", body: { rule_id: "R-ELG-002", disposition: "FAIL", unit_count: 1, node: "bronze.ie_segment" }, text: "Rule R-ELG-002 is FAIL on 1 unit(s) of bronze.ie_segment." },
  { batch_id: BATCH_ENC, authority: "AUTH_MEDICAID", seat: "STEWARD", person_id: "P-ZZ-1001", kind: "DECISION_REF", subject_type: "UNIT", subject_id: U2, template: "RELEASE_SUMMARY", body: { batch_id: BATCH_ENC, node: "bronze.encounter_header", released: 0, held: 1, pending: 0 }, text: `Batch ${BATCH_ENC} / bronze.encounter_header: 0 released, 1 held, 0 pending.` },
  { batch_id: BATCH_ELIG, authority: "AUTH_MEDICAID", seat: "PUBLISHER", person_id: null, kind: "NOTE", subject_type: "BATCH", subject_id: BATCH_ELIG, template: "RELEASE_SUMMARY", body: { batch_id: BATCH_ELIG, node: "bronze.ie_segment", released: 221, held: 1, pending: 0 }, text: `Batch ${BATCH_ELIG} / bronze.ie_segment: 221 released, 1 held, 0 pending.` },
];

/** Hash-chained like dq_ctl.bb_post (prev_hash = previous entry_hash); FNV stand-in, not sha256. */
export function chainPost(prev: BoardEntry | undefined, p: Post, at: string): BoardEntry {
  const seq = (prev?.seq ?? 0) + 1;
  const prev_hash = prev?.entry_hash ?? "0".repeat(64);
  const entry_hash = fx(`${prev_hash}|${seq}|${p.batch_id}|${p.seat}|${p.kind}|${p.template}|${JSON.stringify(p.body)}`);
  return { refs: [], ...p, seq, prev_hash, entry_hash, at };
}

export function initialBlackboard(): BoardEntry[] {
  const out: BoardEntry[] = [];
  POSTS.forEach((p, i) => out.push(chainPost(out[out.length - 1], p, iso(-180 + i * 20))));
  return out;
}

export function initialMemory(): MemoryRow[] {
  const bl = (memory_id: number, rule: string, node: string, median: number, n: number, status: string): MemoryRow => ({
    memory_id,
    kind: "BASELINE",
    scope_type: "MEASURE",
    scope_id: `${rule}@${node}`,
    authority: "AUTH_MEDICAID",
    content: { measure_id: rule, node, median, n_periods: n, periods: fx(`${rule}:${n}`, 16) },
    content_hash: fx(`baseline:${rule}:${n}`),
    created_seat: "TRIAGE",
    proposed_by: null,
    approved_by: null,
    expires_at: null,
    status,
    at: iso(-60 * 24 + memory_id),
  });
  return [
    bl(1, "M-RULE-7907", "bronze.encounter_line", 0.005, 3, "SUPERSEDED"),
    bl(2, "M-RULE-7907", "bronze.encounter_line", 0.006, 5, "PINNED"),
    bl(3, "M-EXP-27-001-1", "bronze.encounter_header", 0.13, 5, "PINNED"),
    bl(4, "M-RULE-7245", "bronze.ie_segment", 0.0, 5, "PINNED"),
    {
      memory_id: 5,
      kind: "KNOWN_ISSUE",
      scope_type: "RULE",
      scope_id: "R-ENC-001",
      authority: "AUTH_MEDICAID",
      content: { rule_id: "R-ENC-001", reason_code: "UPSTREAM_DEFECT_TICKETED", authority: "AUTH_MEDICAID", expires: day(21) },
      content_hash: fx("ki:5"),
      created_seat: "STEWARD",
      proposed_by: "P-ZZ-1003",
      approved_by: null,
      expires_at: `${day(21)}T00:00:00Z`,
      status: "PROPOSED",
      at: iso(-120),
    },
  ];
}

// ---- tenant (generic values only; a real tenant name arrives at runtime from a profile outside the repo) ----
export const TENANT: Tenant = {
  display_name: "State A",
  agency: "State Medicaid agency",
  division: "Program division",
  organisations: { STATE: "State agency", VENDOR: "Implementation partner" },
  approver_org: "STATE",
  source: "mock",
};
/** person_id → organisation code (the profile's `people` map). Alice and Dave are VENDOR: they cannot approve configuration. */
export const PEOPLE_ORG: Record<string, string> = {
  "P-ZZ-1001": "VENDOR",
  "P-ZZ-1002": "STATE",
  "P-ZZ-1003": "STATE",
  "P-ZZ-1004": "VENDOR",
  "P-ZZ-1005": "STATE",
};

/** Look-back items opened by an applied revocation of R-ELG-002 (fixture CR-6d0e5a7b31). */
export function initialLookback(): LookbackItem[] {
  const mk = (item_id: number, batch: string, unit: string, live: boolean, mins: number): LookbackItem => ({
    item_id,
    cr_id: "CR-6d0e5a7b31",
    unit_key: unit,
    batch_id: batch,
    node: "bronze.ie_segment",
    authority: "AUTH_MEDICAID",
    rule_artifact: "ZZ:R-ELG-002@ELIGIBILITY",
    rule_hash: fx("R-ELG-002@ELIGIBILITY", 16),
    released_at: iso(mins),
    in_live_manifest: live,
    at: iso(-30),
    resolution: null,
    reassessment: null,
    status: "OPEN",
  });
  return [
    mk(1, BATCH_ELIG, "ZZ:MEDICAID:a3f19c:elig=PSK-00401", true, -60 * 30),
    mk(2, BATCH_ELIG, "ZZ:MEDICAID:a3f19c:elig=PSK-00402", true, -60 * 30),
    mk(3, "B-ZZ-20261012-ELIG-01", "ZZ:MEDICAID:a3f19c:elig=PSK-00377", false, -60 * 24 * 7),
  ];
}

/** The applied REVOCATION that opened the fixture look-back items. */
export function revocationFixture(rules: RuleRow[]): CrDetail {
  const r = rules.find((x) => x.id === "R-ELG-002")!;
  const hash = fx("R-ELG-002@ELIGIBILITY", 16);
  const content = { rule: "ZZ:R-ELG-002@ELIGIBILITY", hash, reason: "span overlap logic found defective after release" };
  return {
    cr_id: "CR-6d0e5a7b31",
    state: MOCK_STATE,
    author: "P-ZZ-1001",
    origin: "HUMAN",
    loosening: false,
    approvals: 1,
    approvals_required: 1,
    status: "APPLIED",
    at: iso(-90),
    rationale: `Revoke R-ELG-002@ELIGIBILITY version ${hash.slice(0, 12)}: ${content.reason}`,
    items: [{ artifact_id: `ZZ:revocation:R-ELG-002@ELIGIBILITY:${hash.slice(0, 12)}`, kind: "REVOCATION", op: "PUT", content, content_hash: fx("CR-6d0e5a7b31:item0"), current: null }],
    impact: {
      rules: [{ rule: content.rule, before: { hash, status: "ACTIVE", gating: r.gating, relation: r.relation, kind: r.kind }, after: { hash, status: "REVOKED", gating: r.gating, relation: r.relation, kind: r.kind } }],
      loosening_reasons: [],
      summary: { changed: 1, rules_total: rules.length },
    },
    base: {},
    events: [
      { event: "PROPOSED", person_id: "P-ZZ-1001", detail: null, at: iso(-90) },
      { event: "APPROVED", person_id: "P-ZZ-1002", detail: null, at: iso(-60) },
      { event: "APPLIED", person_id: null, detail: null, at: iso(-60) },
    ],
    x_you_are_author: false,
  };
}

// =====================================================================================================
// Part E (PARTE_DESIGN §10). Generic values only. The mock tells the w2_earn story *after* a promotion:
// RELEASE for AUTH_MEDICAID was raised to L3 by a two-person change request, AUTH_SNAP was demoted to L2.
// =====================================================================================================

/**
 * Small-cell suppression, mirroring dqagent/llm/facts.py: a count key (COUNT_KEYS, COUNT_SUFFIXES) whose value is
 * an integer from 1 to 10 becomes "<11", and the template prints "<11" as "fewer than 11" (llm/template.py fmt).
 * Approval counts, file counts and similar operational counts are not record counts and are not suppressed.
 */
export const SMALL_CELL_MAX = 10;
const COUNT_KEYS = new Set(["count", "failed", "evaluated", "rows", "records", "n", "units", "unit_count", "rows_affected", "held_units", "released_units", "failed_units", "nulls", "detail_rows"]);
const COUNT_SUFFIXES = ["_count", "_rows", "_units", "_records", "_failed", "_evaluated"];
export function isCountKey(k: string): boolean {
  return COUNT_KEYS.has(k) || COUNT_SUFFIXES.some((s) => k.endsWith(s));
}
function suppressFacts(v: unknown, key = ""): unknown {
  if (Array.isArray(v)) return v.map((x) => suppressFacts(x, key));
  if (v && typeof v === "object") return Object.fromEntries(Object.entries(v as Record<string, unknown>).map(([k, x]) => [k, suppressFacts(x, k)]));
  if (isCountKey(key) && typeof v === "number" && Number.isInteger(v) && v >= 1 && v <= SMALL_CELL_MAX) return "<11";
  return v;
}
/** A record count as the template prints it: 1–10 reads "fewer than 11"; 0 and 11+ print as numbers. */
export function cnt(n: number): string {
  return Number.isInteger(n) && n >= 1 && n <= SMALL_CELL_MAX ? "fewer than 11" : n.toLocaleString("en-US");
}

/** Every explanation the mock issued, by "<facts_hash>:<text_hash>": the (suppressed) facts behind it, for citations. */
export const ISSUED = new Map<string, { facts: Record<string, unknown>; suppressed: number; fallback: boolean }>();
function countSuppressed(v: unknown): number {
  if (v === "<11") return 1;
  if (Array.isArray(v)) return v.reduce((n: number, x) => n + countSuppressed(x), 0);
  if (v && typeof v === "object") return Object.values(v as Record<string, unknown>).reduce((n: number, x) => n + countSuppressed(x), 0);
  return 0;
}

/** Template explainer stand-in: deterministic text over suppressed facts; hashes are FNV stand-ins, not sha256 (F-17). */
export function explain(text: string, facts: Record<string, unknown>, fallback = false): Explanation {
  const f = suppressFacts(facts) as Record<string, unknown>;
  const e: Explanation = { text, source: "template", fact_check: fallback ? "FALLBACK" : "TEMPLATE", facts_hash: fx(JSON.stringify(f)), text_hash: fx(text) };
  ISSUED.set(`${e.facts_hash}:${e.text_hash}`, { facts: f, suppressed: countSuppressed(f), fallback });
  return e;
}

const inDays = (d: number) => new Date(now + d * 86_400_000).toISOString();

export function initialAgents(): AgentRow[] {
  const ent = (action_class: string, authorities: string[]) => ({ action_class, authorities, not_before: inDays(-65), not_after: inDays(300), live: true });
  return [
    {
      agent_id: "dq-reconciliation",
      name: "DQ reconciliation agent",
      version: "1.4.0",
      status: "ACTIVE",
      seats: ["INTAKE", "EVALUATOR", "TRIAGE", "MONITOR", "PUBLISHER", "VERIFIER"],
      entitlements: [ent("RELEASE", ["AUTH_MEDICAID", "AUTH_SNAP"]), ent("POST_BOARD", ["AUTH_MEDICAID", "AUTH_SNAP"]), ent("PIN_MEMORY", ["AUTH_MEDICAID"])],
      cr_id: "TEST-GENESIS",
    },
    {
      agent_id: "data-governance-classification",
      name: "Data governance classification agent",
      version: "0.1.0",
      status: "REGISTERED",
      seats: [],
      entitlements: [],
      cr_id: "TEST-GENESIS",
    },
  ];
}

// K0 / B7, B8, B9: this mock's own "running code version" (the mock has no real tracer; this stands in for
// tracer.code_sha256() consistently across eval runs, attestations and evidence_status()).
export const MOCK_CODE_SHA256 = fx("mock-code-version");
/** K0: demo minimums, matching config/state/ZZ/policy_w1.yaml's real values exactly (one definition, same shape
 *  the real dq_ctl.promotion_minimums() returns). */
export const MOCK_MINIMUMS: PromotionMinimums = {
  label: "demo minimums",
  L2: { human_decisions: 3, override_rate_max: 0.1, denies: 0, lookback_withdrawn: 0 },
  L3: { human_releases: 5, overrides: 0, denies: 0, lookback_withdrawn: 0 },
  L4: { released_after_window: 5, vetoes: 0, denies: 0, lookback_withdrawn: 0 },
};

const tr = (autonomous: number, h_on: number, vetoes: number, overrides: number, denies: number, lw: number, humanReleased = 0, humanDecisions = 0) => {
  const current_version = {
    code_sha256: MOCK_CODE_SHA256,
    autonomous,
    released_after_window: h_on,
    human_released: humanReleased,
    human_decisions: humanDecisions,
    vetoes,
    overrides,
    denies,
    lookback_withdrawn: lw,
  };
  return {
    autonomous,
    h_on_released: h_on,
    vetoes,
    overrides,
    denies,
    lookback_withdrawn: lw,
    human_released: humanReleased,
    human_decisions: humanDecisions,
    since: inDays(-12),
    eval_attestation: {
      label: "w2-partE-eval",
      code_sha256: MOCK_CODE_SHA256,
      tests: 214,
      failures: 0,
      at: inDays(-12),
      proposed_by: "P-ZZ-1002",
      confirmed_by: "P-ZZ-1003",
      halves: { deterministic: true, llm: false },
    },
    current_version,
    versions: [current_version],
    prior: { autonomous: autonomous + 31, h_on_released: 0, vetoes: 0, overrides: 2, denies: 0, lookback_withdrawn: 0, since: inDays(-40), until: inDays(-12) },
  };
};

export function initialAutonomy(): AutonomyRow[] {
  // K0 / B7: evidence/minimums are state-wide on the real /autonomy -- the mock mirrors that by sharing one
  // mutable object every row points at (so confirming an attestation, mock-side, flips it on every row at once).
  const evidence = { code_sha256: MOCK_CODE_SHA256, prompt_version: "p-mock", deployment: "demo", deterministic: true, llm: false, llm_required: false, complete: true };
  const minimums = MOCK_MINIMUMS;
  const row = (action_class: string, authority: string, tier: AutonomyRow["tier"], ceiling: string, cr: string, t: AutonomyRow["track_record"]): AutonomyRow => ({
    agent_id: "dq-reconciliation",
    action_class,
    authority,
    tier,
    ceiling_mode: ceiling,
    cr_id: cr,
    track_record: t,
    track_record_hash: fx(JSON.stringify([action_class, authority, t])),
    evidence,
    minimums,
  });
  return [
    row("RELEASE", "AUTH_MEDICAID", "L3", "H_ON", "CR-1f5c0a9e72", tr(0, 14, 0, 0, 0, 0, 5, 5)),
    {
      ...row("RELEASE", "AUTH_SNAP", "L2", "H_IN", "TEST-GENESIS", tr(0, 3, 1, 0, 0, 0, 2, 3)),
      demotions: [
        {
          at: inDays(-2),
          from_tier: "L3",
          to_tier: "L2",
          reason_code: "VETO",
          ref: "unit:ZZ:SNAP:4c11d2:elig=PSK-00912",
          explanation: explain("A reviewer vetoed a release by dq-reconciliation for AUTH_SNAP during its veto window, so its RELEASE tier for AUTH_SNAP was lowered from L3 to L2. A person now decides each release.", { kind: "demotion", authority: "AUTH_SNAP", from: "L3", to: "L2", reason: "VETO" }),
        },
      ],
    },
    row("POST_BOARD", "AUTH_MEDICAID", "L4", "A", "TEST-GENESIS", tr(212, 0, 0, 0, 0, 0)),
    row("POST_BOARD", "AUTH_SNAP", "L4", "A", "TEST-GENESIS", tr(40, 0, 0, 0, 0, 0)),
    row("PIN_MEMORY", "AUTH_MEDICAID", "L4", "A", "TEST-GENESIS", tr(9, 0, 0, 0, 0, 0)),
  ];
}

/** K0 / B9: fixture `dq_ctl.eval_runs` rows for the Tower's attestation run picker, newest first. */
export function initialEvalRuns(): EvalRun[] {
  return [
    { run_id: "run-agent_eval-0001", suite: "agent_eval", source_mode: "DETERMINISTIC", cases: 214, passed: 214, code_sha256: MOCK_CODE_SHA256, prompt_version: null, junit_sha256: fx("junit:agent_eval:0001"), at: inDays(-12) },
    { run_id: "run-agent_eval-0002", suite: "agent_eval", source_mode: "DETERMINISTIC", cases: 214, passed: 214, code_sha256: MOCK_CODE_SHA256, prompt_version: null, junit_sha256: fx("junit:agent_eval:0002"), at: inDays(-1) },
    { run_id: "run-explainer-0001", suite: "explainer", source_mode: "LIVE", cases: 40, passed: 40, code_sha256: MOCK_CODE_SHA256, prompt_version: "p-mock", junit_sha256: fx("junit:explainer:0001"), at: inDays(-1) },
    // K11 (PARTF_K7_K12_MOCK_CONTRACT.md): SYNTHETIC -- added only to give the new eval-diff screen a clean pair
    // to compare against run-agent_eval-0002. Never a recorded historical run; its run_id says so, and the
    // eval-diff screen never implies otherwise.
    {
      run_id: "run-agent_eval-0003-synthetic",
      suite: "agent_eval",
      source_mode: "DETERMINISTIC",
      cases: 214,
      passed: 211,
      code_sha256: fx("code:synthetic-0003", 16),
      prompt_version: null,
      junit_sha256: fx("junit:agent_eval:0003-synthetic"),
      at: inDays(0),
    },
  ];
}

export function initialFeedItems(): FeedItem[] {
  return [
    {
      item_id: 1,
      feed: "837P",
      submitter_id: "ZZ-MCO-B",
      kind: "EMPTY_DELIVERY",
      period: "2026-10-19",
      expected_by: null,
      status: "OPEN",
      reason_code: null,
      at: inDays(-1),
      explanation: explain("Feed 837P from submitter ZZ-MCO-B arrived for 2026-10-19 with 0 detail rows. The feed is expected to carry activity every day. This does not hold the batch; a person records why it was empty.", { feed: "837P", submitter: "ZZ-MCO-B", rows: 0, period: "2026-10-19" }),
    },
    {
      item_id: 2,
      feed: "IE",
      submitter_id: null,
      kind: "MISSING_DELIVERY",
      period: "2026-W42",
      expected_by: inDays(-0.5),
      status: "OPEN",
      reason_code: null,
      at: inDays(-0.4),
      explanation: explain("No IE eligibility extract arrived for period 2026-W42 by its expected time. The feed is expected weekly. This does not hold any batch.", { feed: "IE", period: "2026-W42", delivered: false }),
    },
    {
      item_id: 3,
      feed: "837P",
      submitter_id: "ZZ-MCO-A",
      kind: "EMPTY_DELIVERY",
      period: "2026-10-12",
      expected_by: null,
      status: "ACKED",
      reason_code: "HOLIDAY",
      acked_by: "P-ZZ-1002",
      at: inDays(-8),
      explanation: explain("Feed 837P from submitter ZZ-MCO-A arrived for 2026-10-12 with 0 detail rows.", { feed: "837P", submitter: "ZZ-MCO-A", rows: 0 }),
    },
    {
      // governance_schema.py FEED_ITEM_KINDS / runtime.py:1175 feed_item_open(..., 'UNROUTED_FILE', ...): an inbound
      // file no FEEDS route matched. Never ingested; carries no cadence (period/expected_by are null, not "—").
      item_id: 4,
      feed: "UNROUTED",
      submitter_id: null,
      kind: "UNROUTED_FILE",
      period: null,
      expected_by: null,
      status: "OPEN",
      reason_code: null,
      at: inDays(-2),
      explanation: explain(
        "The inbound file 837X_ZZUNKNOWN_20261027_1.dat matched no route in the FEEDS routing table and was never ingested. It opens an item here for the intake duty to resolve: propose a route, reject the file, or ask the submitter to resend.",
        { feed: "UNROUTED", file: "837X_ZZUNKNOWN_20261027_1.dat", routed: false },
      ),
    },
    {
      // ops_routes.py:71-72 / runtime.py:1297 RecoveryDataLost (R4-1): an ABC recovery found the batch's frozen
      // loads gone from the data plane. The batch stays held; this is a routing/recovery event, not a cadence miss.
      item_id: 5,
      feed: "RECOVERY",
      submitter_id: null,
      kind: "RECOVERY_DATA_LOST",
      period: null,
      expected_by: null,
      status: "OPEN",
      reason_code: null,
      at: inDays(-1.2),
      explanation: explain(
        "Batch B-ZZ-20261020-01 was sealed but the frozen load its ABC recovery needs is no longer in the data plane. Recovery was refused (409 RECOVERY_DATA_LOST); the batch stays held. This does not state why the load is gone.",
        { feed: "RECOVERY", batch_id: "B-ZZ-20261020-01", abc_rows: null },
      ),
    },
  ];
}

export function initialParallelRun(): ParallelRunRow[] {
  let id = 0;
  const r = (period: string, as_of: string, relation: string, measure: string, legacy: number, nw: number, tol: number, explain_code: string | null = null): ParallelRunRow => {
    const delta = Math.round((nw - legacy) * 100) / 100;
    const rel = legacy === 0 ? (delta === 0 ? 0 : 1) : Math.abs(delta / legacy);
    const status = delta === 0 ? "MATCH" : rel <= tol ? "WITHIN" : "BREAK";
    id += 1;
    return {
      row_id: id,
      run_id: `PR-${period}`,
      period,
      as_of,
      relation,
      measure,
      legacy,
      new: nw,
      delta,
      tolerance: tol,
      status,
      explain_code,
      explanation: explain(
        status === "BREAK"
          ? `For ${period} as of ${as_of}, ${relation} ${measure} is ${legacy} in the legacy system and ${nw} in the new pipeline, a difference of ${delta}, beyond the tolerance of ${tol}. It needs an explain code before it counts as go/no-go evidence.`
          : `For ${period}, ${relation} ${measure} is ${legacy} in legacy and ${nw} in the new pipeline (${status}).`,
        { period, relation, measure, legacy, new: nw, delta, tolerance: tol, status },
      ),
    };
  };
  return [
    r("2026-09", "2026-10-05", "gold.encounter_header", "records", 1279, 1279, 0),
    r("2026-09", "2026-10-05", "gold.encounter_line", "amount:paid", 1843211.42, 1843640.1, 0.005),
    r("2026-09", "2026-10-05", "gold.encounter_header", "rule_failed:R-ENC-001", 23, 23, 0),
    r("2026-10", "2026-10-19", "gold.encounter_header", "records", 1301, 1286, 0),
    r("2026-10", "2026-10-19", "gold.encounter_line", "amount:paid", 1902118.0, 1897118.0, 0.001),
    r("2026-10", "2026-10-19", "gold.ie_segment", "records", 4975, 4975, 0),
    r("2026-09", "2026-10-05", "gold.ie_segment", "records", 4890, 4884, 0, "TIMING"),
  ];
}

export function initialCde(): CdeRow[] {
  const c = (element: string, domain: string, completeness: number | null, validity: number | null, status: string, text: string): CdeRow => ({
    element,
    domain,
    completeness,
    validity,
    thresholds: { completeness_min: 0.98, validity_min: 0.95 },
    status,
    steward_domain: domain,
    explanation: explain(text, { element, completeness, validity, status }),
  });
  return [
    c("ELG.MEMBER_ID", "ELIGIBILITY", 1, 0.998, "MEETS", "ELG.MEMBER_ID is 100% complete and 99.8% valid, above its minimums of 98% and 95%."),
    c("ELG.SPAN_START", "ELIGIBILITY", 0.999, 0.97, "MEETS", "ELG.SPAN_START is 99.9% complete and 97% valid, above its minimums."),
    c("CLM.PAID_AMT", "CLAIMS_PROFESSIONAL", 0.962, 0.99, "BELOW", "CLM.PAID_AMT is 96.2% complete, below its completeness minimum of 98%."),
    c("CLM.PROC_CD", "CLAIMS_PROFESSIONAL", 0.995, 0.941, "BELOW", "CLM.PROC_CD is 94.1% valid, below its validity minimum of 95%."),
    c("RX.NDC", "PHARMACY", null, null, "NOT_EXERCISED", "RX.NDC is not exercised: the pharmacy domain is not delivered, so it is not counted as meeting."),
    c("ELG.RACE_CD", "ELIGIBILITY", 0.91, null, "NOT_LOADED", "ELG.RACE_CD validity cannot be computed: its code set is not loaded."),
  ];
}

export function initialClassification(): ClassificationRow[] {
  const r = (element: string, cls: string, source = "DEFAULT", cr_id: string | null = null): ClassificationRow => ({ element, class: cls, source, cr_id });
  return [
    r("ELG.MEMBER_ID", "identity"),
    r("ELG.SPAN_START", "coverage"),
    r("ELG.RACE_CD", "identity"),
    r("CLM.PAID_AMT", "payment"),
    r("CLM.PROC_CD", "clinical"),
    r("CLM.DIAG_CD", "clinical"),
    r("CLM.SUD_PROGRAM_FLAG", "part2", "CR", "CR-0b7d3e5521"),
    r("RX.NDC", "clinical"),
    r("ENR.PLAN_ID", "program"),
  ];
}

/** rule id → classes of the elements it binds (rules carry this in Part E). */
export const RULE_CLASSES: Record<string, string[]> = {
  "R-ENC-001": ["payment"],
  "R-ENC-002": ["identity"],
  "R-ENC-003": ["clinical"],
  "R-ENC-004": ["clinical"],
  "R-ELG-001": ["coverage"],
  "R-ELG-002": ["identity", "coverage"],
  "R-ELG-003": ["identity"],
  "SA-ENC-PLAN-PAID-PRESENT": ["payment"],
  "B-X-SVC-AFTER-DOB": ["identity", "clinical"],
  "M-RULE-7907": ["clinical"],
  "B-X-DUP-ACROSS-BATCH": ["part2"],
};

// ---- monitoring ----------------------------------------------------------------------------------
/** 13 monthly periods, 2025-10 through 2026-10. */
const PERIODS = Array.from({ length: 13 }, (_, i) => `${2025 + Math.floor((i + 9) / 12)}-${String(((i + 9) % 12) + 1).padStart(2, "0")}`);

function xmr(values: number[]) {
  const base = values.slice(0, -1);
  const cl = base.reduce((a, b) => a + b, 0) / base.length;
  const mr = base.slice(1).map((v, i) => Math.abs(v - base[i]!));
  const mrBar = mr.reduce((a, b) => a + b, 0) / mr.length;
  const sigma = mrBar / 1.128;
  return { cl, ucl: cl + 2.66 * mrBar, lcl: Math.max(0, cl - 2.66 * mrBar), sigma2: [cl - 2 * sigma, cl + 2 * sigma] as [number, number] };
}

function xmrSeries(relation: string, metric: string, values: number[], fallback = false): MonitorSeries {
  const l = xmr(values);
  const last = values.length - 1;
  const signal = values[last]! > l.ucl || values[last]! < l.lcl;
  const batch = (i: number) => `B-ZZ-${PERIODS[i]}`;
  return {
    relation,
    metric,
    key: null,
    chart: "XmR",
    status: signal ? "SIGNAL" : "OK",
    points: values.map((v, i) => ({ batch_id: batch(i), period: PERIODS[i] ?? null, value: v, n: null })),
    cl: l.cl,
    ucl: l.ucl,
    lcl: l.lcl,
    sigma2: l.sigma2,
    signals: signal
      ? [
          {
            batch_id: batch(last),
            rule: "R1",
            explanation: explain(
              `${relation} ${metric} for ${PERIODS[last]} is ${values[last]}, ${values[last]! < l.lcl ? "below the lower" : "above the upper"} control limit of ${Math.round(values[last]! < l.lcl ? l.lcl : l.ucl)} (rule R1). This is advisory and does not change any unit's mode.`,
              { relation, metric, value: values[last], ucl: l.ucl, lcl: l.lcl, rule: "R1" },
              fallback,
            ),
          },
        ]
      : [],
    baseline_ref: fx(`spc:${relation}:${metric}`),
    mix: null,
  };
}

function pSeries(relation: string, metric: string, key: string, rates: number[], ns: number[]): MonitorSeries {
  const base = rates.slice(0, -1);
  const pbar = base.reduce((a, b, i) => a + b * ns[i]!, 0) / ns.slice(0, -1).reduce((a, b) => a + b, 0);
  const lim = (n: number) => 3 * Math.sqrt((pbar * (1 - pbar)) / n);
  const last = rates.length - 1;
  const pts = rates.map((v, i) => ({ batch_id: `B-ZZ-${PERIODS[i]}`, period: PERIODS[i] ?? null, value: v, n: ns[i]!, ucl: pbar + lim(ns[i]!), lcl: Math.max(0, pbar - lim(ns[i]!)) }));
  const signal = rates[last]! > pts[last]!.ucl;
  return {
    relation,
    metric,
    key: "",
    chart: "p",
    status: signal ? "SIGNAL" : "OK",
    points: pts,
    cl: pbar,
    ucl: null,
    lcl: null,
    sigma2: null,
    signals: signal
      ? [
          {
            batch_id: pts[last]!.batch_id,
            rule: "R1",
            explanation: explain(
              `The null rate of ${key} in ${relation} is ${(rates[last]! * 100).toFixed(1)}% for ${PERIODS[last]}, above its upper limit of ${(pts[last]!.ucl * 100).toFixed(1)}% for ${cnt(ns[last]!)} rows (rule R1). Advisory only.`,
              { relation, key, rate: rates[last], ucl: pts[last]!.ucl, n: ns[last], rule: "R1" },
            ),
          },
        ]
      : [],
    baseline_ref: fx(`spc:${relation}:${metric}:${key}`),
    mix: null,
  };
}

export function initialMonitorSeries(): MonitorSeries[] {
  const rows = [1290, 1312, 1276, 1301, 1288, 1325, 1297, 1306, 1284, 1311, 1299, 1293, 1061];
  const paid = [1.81, 1.86, 1.83, 1.88, 1.84, 1.87, 1.85, 1.82, 1.86, 1.84, 1.89, 1.85, 1.9].map((v) => Math.round(v * 1e6));
  const ns = [4890, 4902, 4911, 4930, 4925, 4941, 4950, 4962, 4955, 4970, 4968, 4973, 4975];
  const nulls = [0.021, 0.019, 0.022, 0.02, 0.023, 0.018, 0.021, 0.02, 0.022, 0.019, 0.021, 0.02, 0.047];
  const bins = [
    { category: "MEDICAID_EXPANSION", expected: 0.41, actual: 0.22 },
    { category: "MEDICAID_TRADITIONAL", expected: 0.37, actual: 0.37 },
    { category: "CHIP", expected: 0.14, actual: 0.33 },
    { category: "OTHER", expected: 0.08, actual: 0.08 },
  ];
  const psi = bins.reduce((a, b) => a + (b.actual - b.expected) * Math.log(Math.max(b.actual, 1e-4) / Math.max(b.expected, 1e-4)), 0);
  const mixStatus = psi >= 0.25 ? "SHIFT" : psi >= 0.1 ? "WATCH" : "OK";
  return [
    xmrSeries("bronze.encounter_header", "rows", rows, true),
    xmrSeries("bronze.encounter_line", "amount:paid", paid),
    pSeries("bronze.ie_segment", "null_rate:ELG.RACE_CD", "ELG.RACE_CD", nulls, ns),
    {
      relation: "bronze.ie_segment",
      metric: "mix:ELG.PROGRAM",
      key: "",
      chart: "PSI",
      status: mixStatus,
      points: [],
      cl: null,
      ucl: null,
      lcl: null,
      sigma2: null,
      signals:
        mixStatus === "OK"
          ? []
          : [
              {
                batch_id: "B-ZZ-2026-10",
                rule: mixStatus,
                explanation: explain(
                  `The program mix of ELG.PROGRAM in bronze.ie_segment moved: PSI is ${psi.toFixed(3)} (${mixStatus}). CHIP rose from 14% to 33% of rows. Advisory only.`,
                  { relation: "bronze.ie_segment", key: "ELG.PROGRAM", psi, status: mixStatus },
                ),
              },
            ],
      baseline_ref: fx("spc:mix:ELG.PROGRAM"),
      mix: { psi, status: mixStatus, bins },
    },
    {
      // Part E fix: an open period is PROVISIONAL (no signals until it closes) with a received-share note.
      ...xmrSeries("bronze.ie_segment", "rows", [4890, 4902, 4911, 4930, 4925, 4941, 4950, 4962, 4955, 4970, 4968, 4973, 2110]),
      status: "PROVISIONAL",
      signals: [],
      note: "Period 2026-10 is open: 42% of the expected deliveries have arrived; no signal is raised until it closes.",
      received_pct: 42,
    } as MonitorSeries & { note: string; received_pct: number },
    {
      relation: "bronze.enrollment_834",
      metric: "rows",
      key: null,
      chart: "XmR",
      status: "BASELINE_BUILDING",
      points: [140, 152, 133, 147, 140].map((v, i) => ({ batch_id: `B-ZZ-834-${i + 1}`, period: PERIODS[i + 8] ?? null, value: v, n: null })),
      cl: null,
      ucl: null,
      lcl: null,
      sigma2: null,
      signals: [],
      baseline_ref: null,
      mix: null,
    },
  ];
}

export function monitorRows(series: (MonitorSeries & { note?: string; received_pct?: number })[]): MonitorRow[] {
  return series.map((s) => ({
    note: s.note ?? null,
    received_pct: s.received_pct ?? null,
    relation: s.relation,
    metric: s.metric,
    key: s.key,
    status: s.status,
    n_periods: s.chart === "PSI" ? 13 : s.points.length,
    last_signal: s.signals.length ? s.signals[s.signals.length - 1]! : null,
  }));
}

// ---- §10b nightly-run simulator: the 22 delivery dates of the synthetic w1 run, 15 preloaded -----------------
export const SIM_NIGHTS: { night_date: string; files: string[] }[] = [
  ["2026-01-01", ["ie_extract_20260101.txt", "834_ZZBROKER_20260101_1.x12"]],
  ["2026-01-10", ["ie_extract_20260110.txt"]],
  ["2026-01-15", ["ie_extract_20260115.txt"]],
  ["2026-01-20", ["837P_ZZMCOB_20260120_1.x12"]],
  ["2026-01-22", ["ie_extract_20260122.txt"]],
  ["2026-01-25", ["837P_ZZMCOA_20260125_1.x12"]],
  ["2026-01-28", ["837P_ZZMCOA_20260128_2.x12"]],
  ["2026-01-29", ["ie_extract_20260129.txt"]],
  ["2026-02-05", ["ie_extract_20260205.txt"]],
  ["2026-02-09", ["834_ZZBROKER_20260209_2.x12"]],
  ["2026-02-12", ["ie_extract_20260212.txt"]],
  ["2026-02-19", ["ie_extract_20260219.txt"]],
  ["2026-02-23", ["837P_ZZMCOB_20260223_2.x12"]],
  ["2026-02-26", ["ie_extract_20260226.txt"]],
  ["2026-03-01", ["837P_ZZMCOA_20260301_3.x12"]],
  ["2026-03-05", ["ie_extract_20260305.txt"]],
  ["2026-03-12", ["ie_extract_20260312.txt"]],
  ["2026-03-15", ["837P_ZZMCOB_20260315_3.x12"]],
  ["2026-03-19", ["ie_extract_20260319.txt"]],
  ["2026-03-25", ["837P_ZZMCOA_20260325_4.x12"]],
  ["2026-03-29", ["ie_extract_20260329.txt"]],
  ["2026-04-01", ["ie_extract_20260401.txt"]],
].map(([night_date, files]) => ({ night_date: night_date as string, files: files as string[] }));
export const SIM_PRELOAD = 15;

/** Deterministic run summary for night i (0-based). Held ids on the first queued night point at real mock cards. */
export function simSummary(i: number, heldKeys: string[], person: string | null): SimRunSummary {
  const n = SIM_NIGHTS[i]!;
  const date = n.night_date.replace(/-/g, "");
  const claims = n.files.some((f) => f.startsWith("837P"));
  const units = claims ? 20 + (i % 5) : 6 + (i % 7);
  const held = heldKeys.length ? heldKeys : claims ? Array.from({ length: 3 + (i % 3) }, (_, k) => `ZZ:MEDICAID:a3f19c:chain=ENC${date}${k}`) : [];
  const pending = claims && i % 2 ? [`ZZ:MEDICAID:a3f19c:chain=ENC${date}P`] : [];
  const veto = claims && i >= SIM_PRELOAD ? [`ZZ:MEDICAID:a3f19c:chain=ENC${date}V`] : [];
  const released = Array.from({ length: Math.max(0, units - held.length - pending.length - veto.length) }, (_, k) => `unit:ZZ:MEDICAID:a3f19c:${date}-${k}`);
  const feeds = i === 9 ? ["feed:2"] : i === 16 ? ["feed:1"] : [];
  const signals = i === SIM_PRELOAD ? ["bb:61", "bb:62"] : [];
  const demotions = i === 13 ? ["bb:48"] : [];
  const abc = i % 6 === 3 ? ["abc:FILE->RAW expect_nonzero"] : [];
  const reasons: Record<string, number> = held.length ? { "floor H_IN (FAIL disposition)": Math.max(1, held.length - 1), "tier L2 ceiling H_IN": held.length > 1 ? 1 : 0 } : {};
  for (const k of Object.keys(reasons)) if (!reasons[k]) delete reasons[k];
  const c = (ids: string[]) => ({ n: ids.length, ids });
  const t0 = Date.parse(`${n.night_date}T02:00:00Z`);
  const text = `On the night of ${n.night_date} (simulated), ${n.files.length} file(s) arrived and ${cnt(units)} unit(s) were created. ${cnt(released.length)} unit(s) were released autonomously, ${cnt(held.length)} unit(s) were held for a person${pending.length ? `, ${cnt(pending.length)} unit(s) waited on a dependency` : ""}${veto.length ? `, and ${veto.length} veto window(s) opened` : ""}. ${signals.length ? `${signals.length} monitor signal(s) were posted. ` : ""}${feeds.length ? `${feeds.length} feed item(s) opened. ` : ""}${demotions.length ? `${demotions.length} automatic demotion(s) occurred.` : ""}`.trim();
  return {
    run_id: `SIM-${date}`,
    night_date: n.night_date,
    started_at: new Date(t0).toISOString(),
    finished_at: new Date(t0 + 95_000).toISOString(),
    person_id: person,
    files: n.files,
    counts: {
      files: c(n.files.map((f) => `file:${f}`)),
      abc_breaks: { ...c(abc), gating: 0, advisory: abc.length },
      units_created: c([...released, ...held.map((k) => `unit:${k}`), ...pending.map((k) => `unit:${k}`), ...veto.map((k) => `unit:${k}`)]),
      released_autonomously: c(released),
      veto_windows_opened: c(veto.map((k) => `unit:${k}`)),
      held: { ...c(held.map((k) => `unit:${k}`)), mode_reasons: reasons },
      pending_dependency: c(pending.map((k) => `unit:${k}`)),
      feed_items: c(feeds),
      monitor_signals: c(signals),
      demotions: c(demotions),
    },
    explanation: explain(text, { night_date: n.night_date, files: n.files.length, created_units: units, released_units: released.length, held_units: held.length, pending_units: pending.length, veto_windows: veto.length }),
  };
}
