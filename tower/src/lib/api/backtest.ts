/** Client for the one new K3 route: `GET /api/v1/backtests/{report_id}` (`dqagent/api/app.py:1012-1023`).
 *  Shapes: lib/types/backtest.ts. The additive `evidence[]` field on the existing CR-detail response needs no
 *  new client function -- it rides on `client.crGet` (lib/api.ts), already called by `app/governance/[crId]`. */
import { useMemo } from "react";
import { call } from "../api";
import { useAuth } from "../auth";
import type { BacktestDetailResponse } from "../types/backtest";

export function backtestClient(token: string | null) {
  return {
    /** 404 when the report_id is unknown (app.py's own HTTPException(404, "unknown backtest report")). */
    backtestDetail: (reportId: string) => call<BacktestDetailResponse>(token, "GET", `/backtests/${encodeURIComponent(reportId)}`),
  };
}
export type BacktestClient = ReturnType<typeof backtestClient>;

export function useBacktest(): BacktestClient {
  const { session } = useAuth();
  return useMemo(() => backtestClient(session?.token ?? null), [session?.token]);
}
