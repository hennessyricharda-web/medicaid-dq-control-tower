/**
 * K3 (PFP P1-4, `docs/design/packages/K3_DESIGN.md`): backtest report shapes, and the additive `evidence[]` field
 * on the existing CR-detail response. Field-for-field mirrors of the backend's own dicts; kept in step with:
 *   - `dqagent/backtest.py` (`_run_once`/`compute_backtest`, lines ~303-308, 327-334, 355-356) for `BacktestReport`;
 *   - `dqagent/api/app.py`'s `backtest_detail` route (`:1012-1023`) for `BacktestDetailResponse`;
 *   - `dqagent/api/app.py`'s `flat_cr_evidence` (`:983-987`) for `CrEvidenceRow`, mirroring `dq_ctl.cr_evidence`'s
 *     own columns (`cr_id, report_id, items_digest, linked_by, at`) 1:1.
 *
 * `CrDetail` itself (`lib/types.ts`) is NOT edited here (reserved elsewhere, per the standing per-package
 * convention): a caller widens the already-fetched object locally, e.g.
 *   `const dd = d as CrDetail & { evidence?: CrEvidenceRow[] };`
 * The runtime object already carries the field (the server returns it additively); this file only names its shape.
 */

export interface BacktestRelationCounts {
  RELEASED?: number;
  HELD?: number;
  PENDING_DEPENDENCY?: number;
}

export interface BacktestRuleHitRate {
  evaluated: number;
  failed: number;
  hit_rate: number;
}

/** One sandbox's own measured side (`base` or `cand`): relation counts, rule hit rates, the held-reason
 *  histogram, and the advisory `gate_checks` would-HOLD count (PFP :902). */
export interface BacktestSideReport {
  relations: Record<string, BacktestRelationCounts>;
  rule_hit_rates: Record<string, BacktestRuleHitRate>;
  held_reasons: Record<string, number>;
  would_hold: number;
}

export interface BacktestDeltas {
  relations: Record<string, Record<string, number>>;
  would_hold: number;
}

/**
 * The `report` jsonb `dqagent/backtest.py` builds and `dq_ctl.backtest_put` stores verbatim. `delta_note` is
 * either `null` (not a mode-only change) or the EXACT `MODE_ONLY_DISCLOSURE` string (`backtest.py:65-71`) --
 * rendered as-is wherever it is shown, never re-authored, never softened, never omitted when non-null (K3_DESIGN.md
 * §6 item 1, the orchestrator's mandatory disclosure requirement).
 */
export interface BacktestReport {
  cr_id: string;
  pipeline_id: string;
  state: string;
  nights: number;
  base: BacktestSideReport;
  cand: BacktestSideReport;
  deltas: BacktestDeltas;
  trace_shape_digest_base: string;
  trace_shape_digest_cand: string;
  inputs_digest: string;
  delta_note: string | null;
  digest: string;
  /** Always the literal string "SYNTHESIZED" (`dq_ctl.backtest_reports` CHECK constraint); never implied LIVE. */
  label: "SYNTHESIZED" | string;
}

/** `GET /api/v1/backtests/{report_id}` (`dqagent/api/app.py::backtest_detail`). */
export interface BacktestDetailResponse {
  report_id: string;
  cr_id: string;
  label: "SYNTHESIZED" | string;
  digest: string;
  at: string;
  report: BacktestReport;
}

/** One `dq_ctl.cr_evidence` row, flattened (`app.py::flat_cr_evidence`). Additive on
 *  `GET /governance/change-requests/{cr_id}` as `evidence[]`; empty array means no evidence linked yet. */
export interface CrEvidenceRow {
  report_id: string;
  items_digest: string;
  linked_by: string;
  at: string;
}
