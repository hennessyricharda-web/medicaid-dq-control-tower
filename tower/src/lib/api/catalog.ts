/** Client for the catalog routes (dqagent/api/catalog_routes.py). Shapes: lib/types/catalog.ts. */
import { useMemo } from "react";
import { call } from "../api";
import { useAuth } from "../auth";
import type {
  CatalogNodesResponse,
  CatalogPipelinesResponse,
  DiscoverBody,
  DiscoverResponse,
  GraphOverlay,
  PipelineGraphResponse,
  PipelineProfile,
  ProfileDraftBody,
  ProfileDraftResponse,
  PromoteGateBody,
  PromoteGateResponse,
  WireBody,
  WireResponse,
} from "../types/catalog";

export function catalogClient(token: string | null) {
  return {
    /** asOf: a trace_id resolved server-side to that trace's config_digest cutoff (D9); omit for "now". */
    pipelineProfile: (id: string, asOf?: string | null) =>
      call<PipelineProfile>(
        token,
        "GET",
        `/catalog/pipelines/${encodeURIComponent(id)}/profile${asOf ? `?as_of=${encodeURIComponent(asOf)}` : ""}`,
      ),
    /** K2c: every pipeline the caller may see (filtered, never redacted), grouped by provenance. */
    catalogPipelines: () => call<CatalogPipelinesResponse>(token, "GET", "/catalog/pipelines"),
    /** K2c: one pipeline's nodes drawer, the Effective tier column. */
    catalogPipelineNodes: (id: string) =>
      call<CatalogNodesResponse>(token, "GET", `/catalog/pipelines/${encodeURIComponent(id)}/nodes`),
    /** K2c: preview a dbt manifest; writes nothing. 422 REFUSED on a path outside pipelines/ or no manifest there. */
    catalogDiscover: (b: DiscoverBody) => call<DiscoverResponse>(token, "POST", "/catalog/discover", b),
    /** K2c: propose a PIPELINE + its TASK_GATE items. Never decides or applies -- the returned cr_id goes through
     *  the existing /governance/change-requests/{cr_id}/challenge + /decide two-person flow next. */
    catalogWire: (b: WireBody) => call<WireResponse>(token, "POST", "/catalog/wire", b),
    /** F-11b fix: promote an advisory TASK_GATE to block. The server reads the gate's current full content
     *  and patches only `mode` -- the caller sends nothing but a rationale (see PromoteGateBody). */
    catalogPromoteGate: (pipelineId: string, taskId: string, b: PromoteGateBody) =>
      call<PromoteGateResponse>(
        token,
        "POST",
        `/catalog/pipelines/${encodeURIComponent(pipelineId)}/task-gates/${encodeURIComponent(taskId)}/promote`,
        b,
      ),
    /** K8 (PARTF_K7_K12_MOCK_CONTRACT.md). PRE-BACKEND: no real route exists for this yet. Proposes a change to
     *  this pipeline's PIPELINE-artifact config, through the SAME propose/CR mechanism `catalogWire` already
     *  uses -- never decides or applies anything; the returned `cr_id` goes through the existing
     *  `/governance/change-requests/{cr_id}/challenge` + `/decide` flow, same as every other CR. */
    catalogProfileDraft: (pipelineId: string, b: ProfileDraftBody) =>
      call<ProfileDraftResponse>(token, "POST", `/catalog/pipelines/${encodeURIComponent(pipelineId)}/profile/draft`, b),
    /** K4: the interactive graph (PFP P2-4). `overlay` is validated server-side against a fixed allow-list
     *  (today only "gates"); any other value is a 400, not a silent passthrough. */
    catalogPipelineGraph: (id: string, overlay: GraphOverlay = "gates") =>
      call<PipelineGraphResponse>(
        token,
        "GET",
        `/catalog/pipelines/${encodeURIComponent(id)}/graph?overlay=${encodeURIComponent(overlay)}`,
      ),
  };
}
export type CatalogClient = ReturnType<typeof catalogClient>;

export function useCatalog(): CatalogClient {
  const { session } = useAuth();
  return useMemo(() => catalogClient(session?.token ?? null), [session?.token]);
}
