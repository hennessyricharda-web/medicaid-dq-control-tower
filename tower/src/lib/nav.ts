/**
 * Navigation registry (LAYOUT_SYSTEM §1.3, AUDIT "Information architecture"). The rail, the `g`-letter
 * shortcuts and the command palette all read this one list.
 *
 * Extending: a later package adds its screen by appending an entry (or flipping `built` to true on a planned one)
 * when the route exists. Entries with `built: false` are never rendered, so the rail shows no dead links.
 * Labels name what the route shows today; where a screen is planned to merge (Balancing, Deliveries, Monitors),
 * the current pages keep their own names until that page is built.
 */
import { PREVIEW_K7_K12 } from "@/lib/config";

export type NavGroup = "Operate" | "Observe" | "Pipelines" | "Govern" | "Assure";
export const NAV_GROUPS: NavGroup[] = ["Operate", "Observe", "Pipelines", "Govern", "Assure"];

export type IconName =
  | "queue" | "lookback" | "deliveries" | "balance" | "moon" | "pulse" | "grid" | "trace" | "eval" | "shield"
  | "layers" | "graph" | "user" | "backtest" | "drift" | "plug" | "link" | "branch" | "doc" | "gear" | "flag"
  | "ledger" | "check" | "chart";

/** Badge sources the shell can fill from data it already loads (summary, queue, look-back). */
export type BadgeSource = "decisions_due" | "lookback_open" | "abc_out" | "demo";

export interface NavItem {
  id: string;
  href: string;
  label: string;
  group: NavGroup;
  icon: IconName;
  /** Second key of the `g` chord, e.g. "q" for g q. */
  key?: string;
  badge?: BadgeSource;
  /** false = planned; hidden everywhere until its route is built. */
  built: boolean;
  /** Shown only when the nightly-run simulator is enabled (demo mode; the API answers 404 otherwise). */
  demoOnly?: boolean;
  /** Extra path prefixes that count as this item being current (e.g. /units under the queue). */
  alsoMatches?: string[];
  /** Words the command palette also matches. */
  keywords?: string;
}

export const NAV_ITEMS: NavItem[] = [
  // Operate
  { id: "queue", href: "/", label: "Decision queue", group: "Operate", icon: "queue", key: "q", badge: "decisions_due", built: true, alsoMatches: ["/units"], keywords: "held units clusters" },
  { id: "lookback", href: "/lookback", label: "Look-back", group: "Operate", icon: "lookback", key: "l", badge: "lookback_open", built: true, keywords: "revoked rule version re-review" },
  { id: "deliveries", href: "/deliveries", label: "Deliveries", group: "Operate", icon: "deliveries", key: "d", built: false },
  { id: "abc", href: "/abc", label: "ABC board", group: "Operate", icon: "balance", key: "b", badge: "abc_out", built: true, keywords: "balancing control totals" },
  { id: "reconciliation", href: "/reconciliation", label: "Reconciliation", group: "Operate", icon: "deliveries", key: "o", built: true, keywords: "parallel run feed items deliveries" },
  { id: "nightly", href: "/nightly", label: "Nightly runs", group: "Operate", icon: "moon", key: "n", badge: "demo", built: true, demoOnly: true, keywords: "simulator business date" },
  // Observe
  { id: "monitoring", href: "/monitoring", label: "Monitors", group: "Observe", icon: "pulse", key: "m", built: true, keywords: "spc xmr psi monitoring" },
  { id: "measures", href: "/measures", label: "Measures", group: "Observe", icon: "chart", key: "s", built: true, keywords: "cms measures" },
  { id: "coverage", href: "/coverage", label: "Coverage", group: "Observe", icon: "grid", key: "v", built: true, keywords: "floor matrix cde sensitivity" },
  { id: "traces", href: "/runs", label: "Traces", group: "Observe", icon: "trace", key: "t", built: true, keywords: "runs spans waterfall release checks run diff otlp" },
  // K8-K12 preview gate (PARTF_K7_K12_MOCK_CONTRACT.md): OFF by default; run_demo.ps1 never sets
  // NEXT_PUBLIC_DQ_PREVIEW_K7_K12, so this item is invisible in the live demo, exactly as before this build.
  { id: "evaluations", href: "/evaluations", label: "Evaluations", group: "Observe", icon: "eval", built: PREVIEW_K7_K12, keywords: "eval runs attestation history diff compare" },
  { id: "guardrails", href: "/guardrails", label: "Guardrails", group: "Observe", icon: "shield", key: "x", built: true, keywords: "masked suppressed fallback check rejected scope denied" },
  { id: "llm-usage", href: "/llm-usage", label: "LLM usage", group: "Observe", icon: "chart", key: "y", built: true, keywords: "prompt versions source mode template scripted recorded live tokens" },
  // Planned with the P2-1 backend (eval runs, annotation queue, explanation feedback): hidden until then.
  // K8-K12 preview gate: OFF by default; run_demo.ps1 never sets NEXT_PUBLIC_DQ_PREVIEW_K7_K12, so this item
  // is invisible in the live demo, exactly as before this build.
  { id: "annotations", href: "/annotations", label: "Annotation queue", group: "Observe", icon: "flag", built: PREVIEW_K7_K12, keywords: "feedback explanation queue pre-backend" },
  // Pipelines
  { id: "pipelines", href: "/pipelines", label: "Pipeline map", group: "Pipelines", icon: "layers", key: "p", built: true, keywords: "dag order dependencies" },
  // K2c: the new, richer provenance-grouped table (PFP :834); coexists with Pipeline map above (K2c-1, RESOLVED
  // toward coexistence, not replacement -- K2_DESIGN.md §9).
  { id: "catalog", href: "/pipelines/catalog", label: "Catalog", group: "Pipelines", icon: "grid", built: true, keywords: "harness wired task gate effective tier provenance" },
  { id: "graph", href: "/pipelines/graph", label: "Graph", group: "Pipelines", icon: "graph", key: "g", built: false },
  { id: "profile", href: "/pipelines/profile", label: "Agent profile", group: "Pipelines", icon: "user", built: false },
  { id: "backtests", href: "/pipelines/backtests", label: "Backtests", group: "Pipelines", icon: "backtest", built: false },
  { id: "drift", href: "/pipelines/drift", label: "DAG drift", group: "Pipelines", icon: "drift", key: "f", built: false },
  // K2c: the wiring wizard (PFP :844), proposing through the EXISTING two-person change-request mechanism.
  { id: "wire", href: "/pipelines/wire", label: "Wire a pipeline", group: "Pipelines", icon: "plug", built: true, keywords: "dbt manifest discover task gate propose" },
  { id: "connections", href: "/pipelines/connections", label: "Connections", group: "Pipelines", icon: "link", built: false },
  // Govern
  { id: "governance", href: "/governance", label: "Change requests", group: "Govern", icon: "branch", key: "c", built: true, keywords: "cr approve two-person" },
  { id: "rules", href: "/rules", label: "Rules", group: "Govern", icon: "doc", key: "r", built: true, keywords: "rule versions revoke" },
  { id: "agents", href: "/agents", label: "Agents", group: "Govern", icon: "gear", key: "a", built: true, keywords: "autonomy tiers entitlements promotion" },
  { id: "known-issues", href: "/known-issues", label: "Known issues", group: "Govern", icon: "flag", key: "k", built: false },
  // Assure
  { id: "blackboard", href: "/agent", label: "Blackboard", group: "Assure", icon: "ledger", key: "e", built: true, keywords: "ledger memory known issues batch state" },
  { id: "audit", href: "/audit", label: "Audit chain", group: "Assure", icon: "check", key: "u", built: true, keywords: "verify chain" },
];

export interface NavContext {
  /** The simulator is enabled (summary.sim.enabled). */
  demo: boolean;
}

export function visibleNav(ctx: NavContext): NavItem[] {
  return NAV_ITEMS.filter((n) => n.built && (!n.demoOnly || ctx.demo));
}

export function isCurrent(item: NavItem, path: string): boolean {
  const prefixes = [item.href, ...(item.alsoMatches ?? [])];
  if (item.href === "/") return path === "/" || (item.alsoMatches ?? []).some((p) => path.startsWith(p));
  return prefixes.some((p) => path === p || path.startsWith(`${p}/`));
}

export function navByKey(key: string, ctx: NavContext): NavItem | undefined {
  return visibleNav(ctx).find((n) => n.key === key);
}
