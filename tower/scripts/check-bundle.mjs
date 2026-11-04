#!/usr/bin/env node
// Bundle gate (PFP P2-4, EXECUTION_PLAN K4 exit criterion "the bundle gate is green"). Runs its own `next build`
// in a disposable dist dir, parses the route table `next build` itself prints ("Route (app) / Size / First Load
// JS"), and compares every route's First Load JS against `tower/bundle-baseline.json`, which was captured BEFORE
// `@xyflow/react` was installed (K4 step 2).
//
//   node scripts/check-bundle.mjs [--base bundle-baseline.json] [--keep-dist]
//
// Rules (PFP P2-4):
//   - any route OTHER than /pipelines/graph that grows by more than 2 KB (gzip-equivalent First Load JS, as
//     `next build` reports it) over its baseline value fails the gate -- the new dependency must not leak into
//     routes that never import it;
//   - /pipelines/graph has no baseline (it did not exist before this package): the FIRST clean run of this
//     script records its measured First Load JS into bundle-baseline.json as "the budget measured at install"
//     (PFP's own phrase) and passes; every later run must stay at or under that recorded budget.
//
// Uses a separate dist dir (NEXT_DIST_DIR) so it never collides with a build another gate step has running, and
// restores tsconfig.json / next-env.d.ts afterward (F-27) -- `next build` edits both to add the dist dir's own
// generated-types glob, and that edit must not linger as an uncommitted diff after the gate step is done.
import { execFileSync } from "node:child_process";
import { readFileSync, writeFileSync, rmSync, existsSync } from "node:fs";
import { createRequire } from "node:module";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const require = createRequire(import.meta.url);
const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const args = Object.fromEntries(
  process.argv.slice(2).reduce((acc, a, i, all) => {
    if (a.startsWith("--")) acc.push([a.slice(2), all[i + 1] && !all[i + 1].startsWith("--") ? all[i + 1] : "1"]);
    return acc;
  }, []),
);
const BASE_PATH = resolve(ROOT, args.base ?? "bundle-baseline.json");
const DIST_DIR = ".next/gate-bundle";
const GRAPH_ROUTE = "/pipelines/graph";
const GROWTH_BUDGET_KB = 2;

function fail(msg) {
  console.error(`FAIL check-bundle: ${msg}`);
  process.exitCode = 1;
}

function restoreGuardedFiles(snapshot) {
  for (const [path, content] of Object.entries(snapshot)) {
    if (content === null) continue; // did not exist before the build; next build's own writes under .next/ are fine
    writeFileSync(join(ROOT, path), content);
  }
}

function snapshotGuardedFiles() {
  const out = {};
  for (const path of ["tsconfig.json", "next-env.d.ts"]) {
    const p = join(ROOT, path);
    out[path] = existsSync(p) ? readFileSync(p, "utf8") : null;
  }
  return out;
}

/**
 * Parses `next build`'s own stdout table into {route: firstLoadKb}. The table's rows look like:
 *   ├ ○ /pipelines/catalog                   8.64 kB         156 kB
 *   ├ ƒ /pipelines/[id]/profile              7.04 kB         155 kB
 * Only the LAST number on a route line (First Load JS, always in kB at this scale) is kept; the route's own
 * page-size column is not part of the budget this script enforces (PFP only names First Load JS).
 */
function parseRouteTable(stdout) {
  const routes = {};
  let sharedKb = null;
  for (const raw of stdout.split("\n")) {
    const line = raw.replace(/\x1B\[[0-9;]*m/g, ""); // strip ANSI colour codes
    const shared = line.match(/First Load JS shared by all\s+([\d.]+)\s*kB/);
    if (shared) sharedKb = Number(shared[1]);
    const m = line.match(/^[│├└]?\s*[○ƒ]\s+(\/\S*)\s+([\d.]+)\s*(B|kB)\s+([\d.]+)\s*(B|kB)\s*$/);
    if (!m) continue;
    const [, route, , , kb, unit] = m;
    routes[route] = unit === "kB" ? Number(kb) : Number(kb) / 1000;
  }
  return { routes, sharedKb };
}

function runBuild() {
  const guard = snapshotGuardedFiles();
  let stdout;
  try {
    stdout = execFileSync(process.execPath, [require.resolve("next/dist/bin/next"), "build"], {
      cwd: ROOT,
      env: { ...process.env, NEXT_DIST_DIR: DIST_DIR },
      encoding: "utf8",
      maxBuffer: 32 * 1024 * 1024,
    });
  } finally {
    restoreGuardedFiles(guard);
    if (!args["keep-dist"]) rmSync(join(ROOT, DIST_DIR), { recursive: true, force: true });
  }
  return stdout;
}

function main() {
  if (!existsSync(BASE_PATH)) {
    fail(`no baseline at ${BASE_PATH} -- capture bundle-baseline.json BEFORE installing a new dependency, per PFP P2-4.`);
    return;
  }
  const baseline = JSON.parse(readFileSync(BASE_PATH, "utf8"));
  const stdout = runBuild();
  const { routes, sharedKb } = parseRouteTable(stdout);
  if (!Object.keys(routes).length) {
    fail("could not parse any route from `next build`'s output -- its table format may have changed; update parseRouteTable().");
    console.error(stdout);
    return;
  }

  let changed = false;
  for (const [route, kb] of Object.entries(routes)) {
    if (route === GRAPH_ROUTE) continue;
    const before = baseline.routes?.[route];
    if (before === undefined) {
      console.log(`NOTE check-bundle: ${route} is new (no baseline entry) -- not subject to the +${GROWTH_BUDGET_KB} KB rule, only /pipelines/graph is exempt from a baseline by design.`);
      continue;
    }
    const delta = kb - before;
    if (delta > GROWTH_BUDGET_KB) {
      fail(`${route}: First Load JS grew by ${delta.toFixed(1)} kB (baseline ${before} kB -> ${kb} kB), over the +${GROWTH_BUDGET_KB} kB budget.`);
    } else {
      console.log(`PASS check-bundle: ${route} (${before} kB -> ${kb} kB, ${delta >= 0 ? "+" : ""}${delta.toFixed(1)} kB)`);
    }
  }

  const graphKb = routes[GRAPH_ROUTE];
  if (graphKb === undefined) {
    fail(`${GRAPH_ROUTE} did not appear in the build output at all -- the page did not build, or its route path differs from what this script expects.`);
  } else if (baseline.graph_budget_kb === undefined) {
    baseline.graph_budget_kb = graphKb;
    baseline.graph_budget_captured_at = new Date().toISOString();
    changed = true;
    console.log(`PASS check-bundle: ${GRAPH_ROUTE} measured at ${graphKb} kB First Load JS -- recorded as its budget (first clean run).`);
  } else if (graphKb > baseline.graph_budget_kb) {
    fail(`${GRAPH_ROUTE}: First Load JS is ${graphKb} kB, over its recorded budget of ${baseline.graph_budget_kb} kB.`);
  } else {
    console.log(`PASS check-bundle: ${GRAPH_ROUTE} at ${graphKb} kB, within its ${baseline.graph_budget_kb} kB budget.`);
  }

  if (sharedKb !== undefined) console.log(`INFO check-bundle: shared First Load JS ${sharedKb} kB (baseline ${baseline.shared_first_load_kb ?? "n/a"} kB).`);

  if (changed && process.exitCode !== 1) {
    writeFileSync(BASE_PATH, JSON.stringify(baseline, null, 2) + "\n");
    console.log(`INFO check-bundle: wrote the measured /pipelines/graph budget back into ${BASE_PATH}.`);
  }

  if (process.exitCode !== 1) console.log("PASS check-bundle: bundle gate green.");
}

main();
