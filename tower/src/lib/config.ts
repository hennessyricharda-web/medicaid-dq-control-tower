export const API_BASE = (process.env.NEXT_PUBLIC_DQ_API || "http://localhost:8080/api/v1").replace(/\/+$/, "");
export const IDP_BASE = (process.env.NEXT_PUBLIC_DQ_IDP || "http://localhost:8081").replace(/\/+$/, "");
export const MOCK = process.env.NEXT_PUBLIC_DQ_MOCK === "1";
/**
 * K8/K11 pre-backend preview gate (PARTF_K7_K12_MOCK_CONTRACT.md; see docs/rules for the incident this closes).
 * OFF by default; run_demo.ps1 never sets this; set it yourself (e.g. `$env:NEXT_PUBLIC_DQ_PREVIEW_K7_K12="1"`
 * before `npm run dev`) to preview this pre-backend work privately. Everything K8-K12 added this pass (the
 * profile-edit drawer, the Evaluations/Annotation nav items and their screens, and the decision-card feedback
 * control) is gated behind this one flag so the live-demo path is byte-for-byte what it was before this build.
 */
export const PREVIEW_K7_K12 = process.env.NEXT_PUBLIC_DQ_PREVIEW_K7_K12 === "1";
export const RATIONALE_MIN = 20;
/** How often an open card re-reads itself to detect that it went stale under the reviewer. */
export const CARD_POLL_MS = 20_000;
export const SUMMARY_POLL_MS = 30_000;
