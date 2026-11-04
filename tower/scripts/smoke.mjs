#!/usr/bin/env node
// Browser smoke for the Command Tower (Part E behaviour + Part F shell).
//
//   node scripts/smoke.mjs --mode mock [--base http://localhost:3000] [--shots DIR] [--prefix 16_] [--preview off|on]
//   node scripts/smoke.mjs --mode live [--base ...] [--api http://localhost:8080/api/v1] [--idp http://localhost:8081/token]
//
// --preview (mock only, default "off"): which K7-K12 preview-flag state the server at --base was started in.
//   "off" (the live-demo default; run_demo.ps1 never sets NEXT_PUBLIC_DQ_PREVIEW_K7_K12) checks that K8-K12's
//   additions are hidden/disabled/absent. "on" checks the pre-backend preview itself still works, against a
//   SEPARATE server started with that env var set, e.g.:
//     NEXT_PUBLIC_DQ_PREVIEW_K7_K12=1 PORT=3001 npm run dev:mock   (in one shell)
//     node scripts/smoke.mjs --mode mock --preview on --base http://localhost:3001 --prefix 16b_
//
// mock: start the Tower with `npm run dev:mock`. Mock state lives in the browser tab, so each suite uses its own
//       browser context and navigates by clicks only (a reload resets the fixtures and signs you out).
// live: needs the control-plane API, the dev IdP and the Tower started against them (run_demo). Its screenshots
//       contain the real tenant name from the private profile: internal only.
//
// Playwright is not a project dependency. The script imports `playwright` normally; if that fails it uses the
// module directory in PLAYWRIGHT_MODULE (e.g. a global install). Screenshots default to tower/.smoke-shots/
// (git-ignored). Exit code 0 only when every check passes.
import { createRequire } from "node:module";
import { mkdirSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const args = Object.fromEntries(
  process.argv.slice(2).reduce((acc, a, i, all) => {
    if (a.startsWith("--")) acc.push([a.slice(2), all[i + 1] && !all[i + 1].startsWith("--") ? all[i + 1] : "1"]);
    return acc;
  }, []),
);
const MODE = args.mode ?? "mock";
if (!["mock", "live"].includes(MODE)) {
  console.error("--mode must be mock or live");
  process.exit(2);
}
const BASE = (args.base ?? process.env.TOWER_URL ?? "http://localhost:3000").replace(/\/+$/, "");
const API = (args.api ?? "http://localhost:8080/api/v1").replace(/\/+$/, "");
const IDP = args.idp ?? "http://localhost:8081/token";
const SHOTS = resolve(args.shots ?? join(HERE, "..", ".smoke-shots"));
const PREFIX = args.prefix ?? "16_";
// K8-K12 preview gate (PARTF_K7_K12_MOCK_CONTRACT.md): which state the server THIS SCRIPT is pointed at (--base)
// was started in. "off" (default) is the live-demo default (run_demo.ps1 never sets
// NEXT_PUBLIC_DQ_PREVIEW_K7_K12) and runs mockK8K11PreviewOffSuite, which asserts everything K8-K12 added is
// hidden/disabled/absent. "on" runs mockK8K11PreviewOnSuite (the pre-existing K8/K11 checks) against a server
// started with that env var set, e.g. `NEXT_PUBLIC_DQ_PREVIEW_K7_K12=1 PORT=3001 npm run dev:mock` and
// `--base http://localhost:3001`. Both states must be run to cover this suite; neither alone is sufficient.
const PREVIEW = (args.preview ?? "off").toLowerCase();
if (!["on", "off"].includes(PREVIEW)) {
  console.error("--preview must be on or off");
  process.exit(2);
}
const VIEW = { width: 1440, height: 900 };
mkdirSync(SHOTS, { recursive: true });

async function loadPlaywright() {
  try {
    return await import("playwright");
  } catch {
    const dir = process.env.PLAYWRIGHT_MODULE;
    if (!dir) {
      console.error("Playwright not found. Install it (npm i -D playwright) or set PLAYWRIGHT_MODULE to its module directory.");
      process.exit(2);
    }
    return createRequire(join(resolve(dir), "package.json"))(resolve(dir));
  }
}

// ---------------------------------------------------------------- harness
const results = [];
function check(name, ok, detail = "") {
  results.push([name, !!ok]);
  console.log(`${ok ? "PASS" : "FAIL"} ${name}${detail ? ` :: ${detail}` : ""}`);
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
async function until(fn, timeout = 15000, what = "condition") {
  const end = Date.now() + timeout;
  let last;
  while (Date.now() < end) {
    try {
      if (await fn()) return true;
    } catch (e) {
      last = e;
    }
    await sleep(150);
  }
  throw new Error(`timed out waiting for ${what}${last ? `: ${last.message}` : ""}`);
}
const visible = (loc, timeout = 15000) => loc.first().waitFor({ state: "visible", timeout });
async function hasText(loc, text, timeout = 15000) {
  await until(async () => (await loc.first().innerText()).includes(text), timeout, `"${text}"`);
}
async function step(name, fn) {
  try {
    await fn();
  } catch (e) {
    check(name, false, String(e.message ?? e).split("\n")[0].slice(0, 200));
  }
}
let shotN = 0;
async function shot(page, name) {
  shotN += 1;
  await page.addStyleTag({ content: "nextjs-portal{display:none!important}" }).catch(() => {});
  await page.screenshot({ path: join(SHOTS, `${PREFIX}${String(shotN).padStart(2, "0")}_${name}.png`) });
}

// ---------------------------------------------------------------- shell helpers
const rail = (page) => page.getByRole("navigation", { name: "Primary" });
async function nav(page, label) {
  const link = rail(page).getByRole("link", { name: label, exact: true });
  await link.click();
  const href = await link.getAttribute("href");
  // Wait for the route itself (dev mode compiles a route on first visit), not just for the click.
  await until(async () => new URL(page.url()).pathname === href && (await link.getAttribute("aria-current")) === "page", 60000, `route ${href}`);
}
async function signInRoster(page, user) {
  await page.getByRole("button", { name: user, exact: true }).click();
  await visible(page.locator(".whobtn"));
}
async function signInLive(page, user) {
  await page.fill("#username", user);
  await page.click("button[type=submit]:has-text('Sign in')");
  await visible(page.locator(".whobtn"), 20000);
}
async function signOut(page) {
  await page.locator(".whobtn").click();
  await page.getByRole("menuitem", { name: "Sign out" }).click();
}
async function openSignals(page) {
  await page.getByRole("button", { name: "All 11 signals" }).click();
  const pop = page.getByRole("dialog", { name: "All 11 signals" });
  await visible(pop);
  return pop;
}
async function closeSignals(page) {
  await page.keyboard.press("Escape");
}
const clock = (page) => page.locator(".simclock");
const signalRow = (pop, k) => pop.locator("tr", { has: pop.page().locator(`th:text-is("${k}")`) });

/**
 * Small-cell rule (LAYOUT_SYSTEM §7b, dqagent/llm/facts.py): an explanation never prints a bare count from 1 to 10
 * next to a count noun; it reads "fewer than 11". Collects offenders across every screen a suite visits.
 */
const SMALL_COUNT = /(?<![\d.,:\-])\b(10|[1-9])\b(?:\s+of\s+[\d,]+)?\s+(?:row|unit|claim|member|record)s?(?:\(s\))?\b/i;
const smallOffenders = [];
async function scanExplanations(page, where) {
  for (const t of await page.locator(".explanation-text").allInnerTexts()) {
    const m = SMALL_COUNT.exec(t);
    if (m) smallOffenders.push(`${where}: "${m[0]}" in "${t.slice(0, 90)}"`);
  }
}
/** Rows of the queue's "Veto window open" group: [worst disposition text, row text]. */
async function vetoGroupRows(page) {
  return page.evaluate(() => {
    const out = [];
    let inVeto = false;
    for (const tr of document.querySelectorAll(".dt tbody tr")) {
      if (tr.classList.contains("grp")) inVeto = tr.textContent.includes("Veto window open");
      else if (inVeto && tr.classList.contains("r")) out.push([tr.children[1]?.textContent ?? "", tr.textContent ?? ""]);
    }
    return out;
  });
}

/** New Part F layout checks, run on any screen that has a page header. */
async function contentStart(page, where) {
  const ph = await page.locator(".ph").first().boundingBox();
  const bottom = ph ? ph.y + ph.height : 9999;
  check(`layout: content starts at y <= 120 (${where})`, bottom <= 120, `page header ends at y=${Math.round(bottom)}`);
}

// ---------------------------------------------------------------- mock suites
async function mockPartE(browser) {
  const ctx = await browser.newContext({ viewport: VIEW });
  const page = await ctx.newPage();
  const errors = [];
  page.on("pageerror", (e) => errors.push(String(e)));
  page.on("console", (m) => m.type() === "error" && errors.push(m.text()));
  await page.goto(`${BASE}/`);
  await signInRoster(page, "alice.maker");

  // 1 Header, status strip and queue
  await step("strip: signals", async () => {
    await visible(page.locator(".dt tbody tr.r"), 30000);
    const pop = await openSignals(page);
    await hasText(signalRow(pop, "Explainer"), "template; decisions deterministic");
    check("topband: explainer tile", true);
    check("topband: genesis alerts when not RATIFIED", (await page.locator(".hdr .env-test").count()) === 1 && (await signalRow(pop, "Genesis").locator(".chip.t-bad").count()) === 1);
    check(
      "topband: held age and SLA remaining are separate tiles",
      (await signalRow(pop, "Oldest held").count()) === 1 && (await signalRow(pop, "SLA remaining").count()) === 1,
    );
    check("strip: popover lists all 11 signals", (await pop.locator("tbody tr").count()) === 11);
    await shot(page, "signals_popover");
    await closeSignals(page);
  });
  await step("queue: tier badge", async () => {
    await visible(page.locator(".detail .chip", { hasText: "L3 Supervised" }));
    check("queue: tier badge", true);
    check("queue: veto countdown", (await page.locator(".chip", { hasText: "veto" }).count()) >= 1);
    check("queue: veto group sits first", (await page.locator(".dt tr.grp").first().innerText()).includes("Veto window open"));
    const veto = await vetoGroupRows(page);
    const bad = veto.filter(([w]) => /FAIL|ERROR/.test(w));
    check("mock honesty: no unit in a veto window has a FAIL or ERROR worst disposition", veto.length > 0 && bad.length === 0, `${veto.length} veto row(s); offending: ${bad.map(([w]) => w).join(", ") || "none"}`);
    await scanExplanations(page, "queue detail");
    await contentStart(page, "queue");
    await shot(page, "queue");
  });

  // Part F shell checks
  await step("shell: rail collapses", async () => {
    await page.evaluate(() => (document.activeElement instanceof HTMLElement ? document.activeElement.blur() : undefined));
    await page.keyboard.press("[");
    await until(async () => (await page.locator(".app.rail-min").count()) === 1, 5000, "rail-min");
    const w = (await rail(page).boundingBox())?.width ?? 0;
    check("shell: rail collapses with [ to 52 px", Math.round(w) === 52, `${w}px`);
    await shot(page, "rail_collapsed");
    await rail(page).locator("[data-rail-toggle]").click();
    await until(async () => (await page.locator(".app.rail-min").count()) === 0, 5000, "rail expanded");
    const w2 = (await rail(page).boundingBox())?.width ?? 0;
    check("shell: rail expands again from its toggle", Math.round(w2) === 212, `${w2}px`);
  });
  await step("shell: Ctrl K opens", async () => {
    await page.keyboard.press("Control+k");
    const pal = page.getByRole("dialog", { name: "Command palette" });
    await visible(pal, 5000);
    check("shell: Ctrl K opens the command palette", true);
    check("shell: palette offers Run next night to an operator", (await pal.getByRole("option", { name: /Run next night/ }).count()) === 1);
    await page.keyboard.type("ENC0002377");
    check("shell: palette finds a unit in scope", (await pal.getByRole("option", { name: /ENC0002377/ }).count()) >= 1);
    await shot(page, "command_palette");
    await page.keyboard.press("Escape");
    await until(async () => (await pal.count()) === 0, 5000, "palette closed");
  });
  await step("shell: split pane resizes from the keyboard", async () => {
    const div = page.locator('[data-split="queue"]');
    const pane = page.locator(".pane.fixed").first();
    const w0 = (await pane.boundingBox()).width;
    await div.focus();
    await page.keyboard.press("ArrowLeft");
    await page.keyboard.press("ArrowLeft");
    await page.keyboard.press("ArrowLeft");
    const w1 = (await pane.boundingBox()).width;
    await page.keyboard.press("ArrowRight");
    await page.keyboard.press("ArrowRight");
    await page.keyboard.press("ArrowRight");
    const w2 = (await pane.boundingBox()).width;
    check("shell: split pane resizes from the keyboard", Math.round(w1 - w0) === 48 && Math.round(w2) === Math.round(w0), `${w0} -> ${w1} -> ${w2}`);
    check("shell: divider is a focusable separator", (await div.getAttribute("role")) === "separator");
  });
  await step("shell: j/k and ?sel=", async () => {
    await page.evaluate(() => (document.activeElement instanceof HTMLElement ? document.activeElement.blur() : undefined));
    const before = new URL(page.url()).searchParams.get("sel");
    await page.keyboard.press("j");
    await until(async () => new URL(page.url()).searchParams.get("sel") !== before, 5000, "sel to change");
    check("shell: j moves the selection and ?sel= follows it", true, `${before} -> ${new URL(page.url()).searchParams.get("sel")}`);
    await page.keyboard.press("k");
    await until(async () => new URL(page.url()).searchParams.get("sel") === before, 5000, "sel back");
  });

  // 2 L3 card with veto window
  await step("card: open", async () => {
    await page.locator(".dt tr.r", { hasText: "encounter_line" }).click();
    await page.getByRole("link", { name: "ZZ:MEDICAID:a3f19c:chain=ENC0002377" }).click();
    await visible(page.locator("#expl-h"));
    const ex = page.locator(".explanation").first();
    check("card: explanation shown with its deterministic-template label (fact_check TEMPLATE)", (await ex.locator(".chip", { hasText: "deterministic template" }).count()) === 1);
    const chips = await page.locator(".explanation .explanation-meta").evaluateAll((els) => els.map((e) => [...e.querySelectorAll(".chip")].map((c) => c.textContent.replace(/^\W+/, "").trim())));
    check(
      "explanation: TEMPLATE shows one chip, deterministic template",
      chips.length > 0 && chips.every((c) => c.length === 1 && c[0] === "deterministic template"),
      JSON.stringify(chips.slice(0, 3)),
    );
    check(
      "card: per-disposition explanations",
      (await page.locator("#disp-h, table").locator(".explanation").count()) >= 1 || (await page.locator(".explanation.compact").count()) >= 1,
    );
    await visible(page.locator("#veto-h"));
    const worst = await page.locator(".cardhead .chip").nth(1).innerText();
    check("mock honesty: the veto-window card's worst disposition is PASS or TA_WARN", /PASS|TA_WARN/.test(worst) && !/FAIL|ERROR/.test(worst), worst);
    const trace = await page.locator("#why-held-h ~ ol").innerText();
    check("mock honesty: the veto-window card's mode trace reaches H_ON through the L3 ceiling", /L3 ceiling .*H_ON/.test(trace) && !/FAIL/.test(trace), trace.replace(/\n/g, " | "));
    await scanExplanations(page, "veto card");
    const rail0 = await page.locator(".drail").boundingBox();
    check("card: decision rail is 330 px and fixed beside the evidence", rail0 && Math.round(rail0.width) === 330);
    await shot(page, "card_veto");
    await page.selectOption("#veto-reason", "DATA_SUSPECT");
    await page.getByRole("button", { name: "Veto", exact: true }).click();
    await visible(page.getByText("Veto recorded"));
    check("card: veto holds the unit and reports the demotion", (await page.getByText("drops to L2").count()) >= 1);
  });

  // 3 Agents
  await step("agents", async () => {
    await nav(page, "Agents");
    await visible(page.locator("#reg-h"));
    await contentStart(page, "agents");
    check("agents: dq-reconciliation ACTIVE", (await page.locator("tr", { hasText: "dq-reconciliation" }).locator(".chip", { hasText: "ACTIVE" }).count()) >= 1);
    check("agents: governance agent REGISTERED / next phase", (await page.getByText("next phase").count()) === 1);
    check(
      "agents: entitlements live with expiry",
      (await page.locator("#ent-h ~ div .chip", { hasText: "live" }).count()) >= 3 && (await page.getByText("day(s) left").count()) >= 3,
    );
    check("agents: tier ladder rendered", (await page.locator(".tier-step").count()) >= 5);
    const med = page.locator("tr", { hasText: "AUTH_MEDICAID" }).filter({ hasText: "RELEASE" });
    check("agents: veto demoted RELEASE AUTH_MEDICAID to L2", (await med.locator(".chip", { hasText: "L2" }).count()) === 1);
    await page.getByRole("button", { name: "Show track record for dq-reconciliation RELEASE AUTH_MEDICAID" }).click();
    await hasText(page.locator("#tr-h"), "AUTH_MEDICAID");
    check("agents: track record + demotion explanation", (await page.locator("#tr-h ~ ol .explanation").count()) >= 1);
    await page.selectOption("#pt-to", "L3");
    await visible(page.getByText("needs two State agency approvers"));
    check("agents: promotion says needs two State approvers", true);
    await page.fill("#pt-why", "Clean record since the veto; reviewer agreed to re-test supervised mode.");
    await page.click("button:has-text('Propose promotion')");
    const link = page.locator("[role=status] a", { hasText: "Open CR-" });
    await visible(link);
    const t = await link.locator("xpath=..").innerText();
    check("agents: AUTONOMY CR drafted", t.includes("needs 2 approval"), t.slice(0, 120));
    await link.click();
    await visible(page.locator("#dec-h"));
    check("cr: track-record evidence shown", (await page.getByText("Track-record evidence signed with this request").count()) === 1);
    check("cr: explanation shown", (await page.locator(".explanation").count()) >= 2);
  });

  // 4 Monitors
  await step("monitoring", async () => {
    await nav(page, "Monitors");
    await visible(page.locator("#mon-h"));
    await visible(page.locator("tbody .chip", { hasText: "BASELINE_BUILDING" }));
    for (const st of ["SIGNAL", "SHIFT", "BASELINE_BUILDING", "OK"]) check(`monitoring: status ${st} listed`, (await page.locator("tbody .chip", { hasText: st }).count()) >= 1);
    check("monitoring: FALLBACK shown honestly", (await page.locator(".chip", { hasText: "FALLBACK" }).count()) >= 1);
    await scanExplanations(page, "monitors");
    await page.locator("button.linklike", { hasText: "rows" }).first().click();
    await visible(page.locator("svg.chart[role=img]"));
    check("monitoring: XmR limits + signal marker", (await page.locator("svg.chart .limit").count()) >= 2 && (await page.locator("svg.chart .signal").count()) === 1);
    await page.locator("button.linklike", { hasText: "null_rate" }).click();
    await visible(page.locator("svg.chart[role=img]"));
    await sleep(500);
    check("monitoring: p-chart per-period limits", (await page.getByText("UCL / LCL per period").count()) === 1);
    await page.locator("button.linklike", { hasText: "mix" }).click();
    await visible(page.getByText("Population stability index"));
    check("monitoring: PSI mix panel", (await page.locator("table", { hasText: "Category mix" }).count()) === 1);
    await page.locator("tr", { hasText: "bronze.enrollment_834" }).locator("button.linklike").click();
    await visible(page.locator("[role=status]", { hasText: "BASELINE_BUILDING" }));
    check("monitoring: BASELINE_BUILDING state", true);
  });

  // 5 Reconciliation
  await step("reconciliation", async () => {
    await nav(page, "Reconciliation");
    await visible(page.locator("#pr-h"));
    await visible(page.locator("tbody .chip", { hasText: "BREAK" }));
    await visible(page.getByRole("button", { name: "Acknowledge feed item 1" }));
    let all3 = true;
    for (const s of ["MATCH", "WITHIN", "BREAK"]) all3 = all3 && (await page.locator("tbody .chip", { hasText: s }).count()) >= 1;
    check("recon: MATCH / WITHIN / BREAK", all3);
    const brk = page.locator("button[aria-label^='Explain break']");
    const n0 = await brk.count();
    await brk.first().click();
    await page.locator("select[id$='-code']").first().selectOption("TIMING");
    await page.click("button:has-text('Record TIMING')");
    await until(async () => (await brk.count()) === n0 - 1, 15000, "TIMING recorded");
    check("recon: TIMING recorded without step-up", true);
    await brk.first().click();
    await page.locator("select[id$='-code']").first().selectOption("LEGACY_DEFECT");
    await page.click("button:has-text('Request challenge')");
    await page.locator("input[id$='-stepup-user']").fill("alice.maker");
    await page.click("button:has-text('Step up')");
    await page.click("button:has-text('Record LEGACY_DEFECT with step-up')");
    await until(async () => (await brk.count()) === n0 - 2, 15000, "LEGACY_DEFECT recorded");
    check("recon: LEGACY_DEFECT recorded with step-up", true);
    await page.getByRole("button", { name: "Acknowledge feed item 1" }).click();
    await visible(page.locator("tr", { hasText: "837P" }).first().locator(".chip", { hasText: "ACKED" }));
    check("recon: feed item acknowledged with reason code", true);
    check("recon: break + feed explanations", (await page.locator(".explanation").count()) >= 4);
    await scanExplanations(page, "reconciliation");
    // K6 Front 3: UNROUTED_FILE / RECOVERY_DATA_LOST kind-specific rendering (fixtures.ts items 4/5), the
    // deployment/idp chips, and each kind's own (narrower) ack reason-code list.
    check("recon: UNROUTED_FILE row with an honest, kind-specific label", (await page.locator("tbody tr", { hasText: "UNROUTED_FILE" }).locator(".hint", { hasText: "matched no feed route" }).count()) === 1);
    check("recon: RECOVERY_DATA_LOST row with an honest, kind-specific label", (await page.locator("tbody tr", { hasText: "RECOVERY_DATA_LOST" }).locator(".hint", { hasText: "frozen load" }).count()) === 1);
    check("recon: deployment and idp chips shown near the feed items", (await page.locator("#fi-h ~ p .chip", { hasText: "deployment:" }).count()) === 1 && (await page.locator("#fi-h ~ p .chip", { hasText: "idp:" }).count()) === 1);
    await page.locator("tbody tr", { hasText: "UNROUTED_FILE" }).locator("select").first().selectOption({ label: "ROUTE_PROPOSED" });
    check("recon: UNROUTED_FILE offers its own ack codes (ROUTE_PROPOSED), not the delivery-kind codes", true);
    check("recon: UNROUTED_FILE never offers a delivery-only ack code", (await page.locator("tbody tr", { hasText: "UNROUTED_FILE" }).locator("select option", { hasText: "HOLIDAY" }).count()) === 0);
    await page.locator("tbody tr", { hasText: "UNROUTED_FILE" }).getByRole("button", { name: /^Acknowledge feed item/ }).click();
    await visible(page.locator("tbody tr", { hasText: "UNROUTED_FILE" }).locator(".chip", { hasText: "ACKED" }));
    check("recon: UNROUTED_FILE acknowledged with its own reason code", true);
    check("recon: RECOVERY_DATA_LOST offers only its own two ack codes", (await page.locator("tbody tr", { hasText: "RECOVERY_DATA_LOST" }).locator("select option").allInnerTexts()).join(",") === "REDELIVERY_REQUESTED,RESUBMIT_REQUESTED");
    await shot(page, "reconciliation_feed_kinds");
  });

  // 6 Coverage + Rules classification
  await step("coverage + rules", async () => {
    await nav(page, "Coverage");
    await visible(page.locator("#cde-h ~ div .chip", { hasText: "NOT_LOADED" }));
    let ok = true;
    for (const s of ["MEETS", "BELOW", "NOT_EXERCISED", "NOT_LOADED"]) ok = ok && (await page.locator("#cde-h ~ div .chip", { hasText: s }).count()) >= 1;
    check("coverage: CDE statuses", ok);
    await page.getByText("Sensitivity classification").click();
    check("coverage: part2 badged", (await page.locator(".chip.t-crit", { hasText: "part2" }).count()) >= 1);
    await nav(page, "Rules");
    await hasText(page.locator("#rules-h"), "shown");
    check("rules: classification column with part2 badge", (await page.locator("tbody .chip.t-crit", { hasText: "part2" }).count()) >= 1);
  });

  // 7 Audit verify
  await step("audit", async () => {
    await nav(page, "Audit chain");
    await page.click("button:has-text('Verify chain')");
    await visible(page.getByText("Audit chain: intact"));
    check("audit: both chains verified", (await page.getByText("Blackboard chain: intact").count()) === 1);
    check("audit: readable labels", (await page.locator("th", { hasText: "label" }).count()) === 1 && (await page.getByText("Release vetoed during its window").count()) >= 1);
  });

  // 8 Look-back: live now + at open, an explanation per item (in the detail pane)
  await step("lookback", async () => {
    await nav(page, "Look-back");
    await hasText(page.locator("#lb-h"), "shown");
    await visible(page.getByText("live now:"));
    check("lookback: live now + at open", (await page.getByText("live now:").count()) >= 1 && (await page.getByText("at open:").count()) >= 1);
    await contentStart(page, "look-back");
    const texts = new Set();
    const rows = page.locator(".dt tbody tr.r");
    const n = Math.min(3, await rows.count());
    for (let i = 0; i < n; i++) {
      await rows.nth(i).click();
      await until(async () => (await rows.nth(i).getAttribute("aria-selected")) === "true", 5000, "row selected");
      texts.add(await page.locator(".detail .explanation .explanation-text").first().innerText());
      await scanExplanations(page, `look-back item ${i + 1}`);
    }
    check("lookback: explanations", texts.size >= 3, `${texts.size} distinct explanation(s) over ${n} item(s)`);
    check("lookback: decision rail beside the detail", (await page.locator(".drail", { hasText: "Decide" }).count()) === 1);
    await shot(page, "lookback");
  });

  // 9 CR approval with explanation_ref (checker, STATE)
  await step("cr approval", async () => {
    await signOut(page);
    await signInRoster(page, "bob.checker");
    await nav(page, "Change requests");
    await page.getByRole("link", { name: "CR-3f9a12c7d0" }).click();
    await visible(page.locator("#dec-h"));
    await page.getByLabel("I reviewed every item diff").check();
    await page.click("button:has-text('Request challenge')");
    await page.locator("input[id$='-stepup-user']").fill("bob.checker");
    await page.click("button:has-text('Step up')");
    await page.locator("button:has-text('Approve change request CR-3f9a12c7d0')").click();
    await visible(page.getByText("approved and applied"));
    check("cr: approval with explanation shown applies", true);
  });

  check("no page or console errors (Part E suite)", errors.length === 0, errors.slice(0, 3).join("; "));
  await ctx.close();
}

async function mockSim(browser) {
  const ctx = await browser.newContext({ viewport: VIEW });
  const page = await ctx.newPage();
  const errors = [];
  page.on("pageerror", (e) => errors.push(String(e)));
  page.on("console", (m) => m.type() === "error" && errors.push(m.text()));
  await page.goto(`${BASE}/`);
  await signInRoster(page, "alice.maker");
  await step("sim: header clock", async () => {
    await hasText(clock(page), "2026-03-01");
    const t = await clock(page).innerText();
    check("topband: simulated date tile labelled SIMULATED", /Simulated/i.test(t) && t.includes("7 queued"), t.replace(/\n/g, " | "));
    check("topband: operator sees Run next night", (await clock(page).getByRole("button", { name: "Run next night" }).count()) === 1);
  });
  await step("sim: nightly", async () => {
    await nav(page, "Nightly runs");
    await hasText(page.locator("#nights-h"), "15 run · 7 queued");
    check("nightly: 22 nights listed, 15 run, 7 queued", (await page.locator("#nights-h ~ div tbody tr").count()) === 22);
    await hasText(page.locator("#run-h"), "2026-03-01");
    check("nightly: latest run summary with explanation", (await page.locator("#run-h ~ .explanation").count()) === 1);
    await contentStart(page, "nightly");
  });
  await step("sim: run next", async () => {
    await clock(page).getByRole("button", { name: "Run next night" }).click();
    const st = page.locator(".simmsg[role=status]");
    await hasText(st, "Ran 2026-03-05");
    await hasText(clock(page), "6 queued");
    check("run-next: date advances to 2026-03-05", (await clock(page).locator("b").innerText()).includes("2026-03-05"));
    await st.getByRole("link", { name: "View run" }).click();
    await hasText(page.locator("#run-h"), "2026-03-05");
    const counts = page.locator(".countgrid .count");
    check("nightly: ten counts shown", (await counts.count()) === 10);
    const held = counts.filter({ hasText: "Held for a person" });
    check("nightly: held count links to units", (await held.locator("a[href^='/units/']").count()) === 2);
    check("nightly: signals link to Monitoring", (await counts.filter({ hasText: "Monitor signals" }).getByRole("link", { name: "Open Monitoring" }).count()) === 1);
    check("nightly: mode reasons on held", (await held.innerText()).includes("floor H_IN"));
    check("nightly: run explanation", (await page.locator("#run-h ~ .explanation").count()) === 1);
    await scanExplanations(page, "nightly run 2026-03-05");
    await held.locator("a[href^='/units/']").first().click();
    await visible(page.locator("#unit-h"));
    check("nightly: held link opens the unit card", true);
  });
  await step("sim: run all", async () => {
    for (let i = 0; i < 6; i++) {
      const btn = clock(page).getByRole("button", { name: "Run next night" });
      await until(async () => await btn.isEnabled(), 15000, "Run enabled");
      await btn.click();
      await hasText(clock(page), `${5 - i} queued`);
    }
    const t = await clock(page).innerText();
    check("run-next: ends at 2026-04-01 with 0 queued", t.includes("2026-04-01") && t.includes("0 queued"));
    check("run-next: button disabled when nothing queued", await clock(page).getByRole("button", { name: "Run next night" }).isDisabled());
  });
  await step("sim: non-operator", async () => {
    await signOut(page);
    await signInRoster(page, "bob.checker");
    await visible(clock(page));
    check("topband: non-operator sees no Run button", (await clock(page).getByRole("button", { name: "Run next night" }).count()) === 0);
  });
  check("no page or console errors (simulator suite)", errors.length === 0, errors.slice(0, 3).join("; "));
  await ctx.close();
}

/** Every screen at 1440x900 in a fresh tab (fixtures as shipped), plus the content-start check on each. */
async function mockScreens(browser) {
  const ctx = await browser.newContext({ viewport: VIEW });
  const page = await ctx.newPage();
  const errors = [];
  page.on("pageerror", (e) => errors.push(String(e)));
  await page.goto(`${BASE}/`);
  await signInRoster(page, "alice.maker");
  await visible(page.locator(".dt tbody tr.r"), 30000);
  await sleep(600);
  await shot(page, "screen_decision_queue");
  const screens = [
    ["Look-back", ".dt tbody tr.r"],
    ["ABC board", ".pagebody .panel, .pagebody table"],
    ["Reconciliation", "#pr-h"],
    ["Nightly runs", "#run-h"],
    ["Monitors", "#mon-h"],
    ["Measures", ".pagebody h2, .pagebody table"],
    ["Coverage", "#cde-h, .pagebody h2"],
    ["Pipeline map", ".pagebody svg, .pagebody table"],
    ["Change requests", ".pagebody table:visible"],
    ["Rules", "#rules-h"],
    ["Agents", "#reg-h"],
    ["Blackboard", ".pagebody h2"],
    ["Audit chain", ".pagebody table, .pagebody button"],
  ];
  let allStart = true;
  for (const [label, sel] of screens) {
    await step(`screen: ${label}`, async () => {
      await nav(page, label);
      await visible(page.locator(sel), 20000);
      await sleep(700);
      const ph = await page.locator(".ph").first().boundingBox();
      if (!ph || ph.y + ph.height > 120) allStart = false;
      await shot(page, `screen_${label.toLowerCase().replace(/[^a-z]+/g, "_")}`);
      await scanExplanations(page, label);
    });
  }
  check("layout: content starts at y <= 120 on every screen with a page header", allStart);
  await step("screen: decision card", async () => {
    await nav(page, "Decision queue");
    await page.locator(".dt tr.r", { hasText: "encounter_chain" }).click();
    await page.locator(".detail a[href^='/units/']").first().click();
    await visible(page.locator("#expl-h"));
    await sleep(600);
    const ev = await page.locator("#expl-h").boundingBox();
    check("layout: card evidence starts by y=200 under its header band", ev && ev.y <= 200, `evidence at y=${ev && Math.round(ev.y)}`);
    await shot(page, "screen_decision_card_held");
    await page.getByRole("button", { name: "All", exact: true }).click();
    const n = await page.locator("#disp-h ~ div tbody tr.r, .sec:has(#disp-h) tbody tr.r").count();
    for (let i = 0; i < n; i++) {
      const row = page.locator(".sec:has(#disp-h) tbody tr.r").nth(i);
      if ((await row.getAttribute("aria-expanded")) !== "true") await row.click();
      await scanExplanations(page, `held card row ${i + 1}`);
    }
  });
  await step("screen: CR detail", async () => {
    await nav(page, "Change requests");
    await page.locator(".pagebody a[href^='/governance/CR-']:visible").first().click();
    await visible(page.locator("#dec-h"));
    await shot(page, "screen_change_request");
    await scanExplanations(page, "change request");
  });
  await step("screen: shortcut sheet", async () => {
    await page.evaluate(() => (document.activeElement instanceof HTMLElement ? document.activeElement.blur() : undefined));
    await page.keyboard.press("?");
    await visible(page.getByRole("dialog", { name: "Keyboard shortcuts" }), 5000);
    check("shell: ? opens the shortcut sheet", true);
    await shot(page, "shortcut_sheet");
    await page.keyboard.press("Escape");
  });
  await step("screen: dark theme", async () => {
    await nav(page, "Decision queue");
    await page.locator(".whobtn").click();
    await page.getByRole("menuitem", { name: /Theme: system/ }).click(); // system -> light (the menu stays open)
    await page.getByRole("menuitem", { name: /Theme: light/ }).click(); // light -> dark
    await page.keyboard.press("Escape");
    await until(async () => (await page.locator("html[data-theme=dark]").count()) === 1, 5000, "dark theme");
    await sleep(400);
    await shot(page, "queue_dark");
  });
  check("no page errors (screens suite)", errors.length === 0, errors.slice(0, 3).join("; "));
  await ctx.close();
}


/** Part F T1 explorer: traces, trace detail, run diff, guardrails, LLM usage, card citations, scope (mock). */
const CAUSAL = /\b(because|due to|caused|causes|led to|as a result|therefore|so that|resulted in)\b/i;
async function mockObsSuite(browser) {
  const ctx = await browser.newContext({ viewport: VIEW, acceptDownloads: true });
  const page = await ctx.newPage();
  const errors = [];
  page.on("pageerror", (e) => errors.push(String(e)));
  page.on("console", (m) => m.type() === "error" && !/404/.test(m.text()) && errors.push(m.text()));
  await page.goto(`${BASE}/`);
  await signInRoster(page, "alice.maker");
  await visible(page.locator(".dt tbody tr.r"), 30000);

  await step("traces: list", async () => {
    await nav(page, "Traces");
    await visible(page.locator(".dt tbody tr.r"), 30000);
    const kinds = await page.locator(".dt tbody tr.r td:nth-child(3)").allInnerTexts();
    check("traces: list shows NIGHT and EXPLAIN runs", kinds.includes("NIGHT") && kinds.includes("EXPLAIN"), [...new Set(kinds)].join(","));
    await contentStart(page, "traces");
    check("traces: no LIVE source in mock", (await page.locator(".src.LIVE").count()) === 0);
    await shot(page, "traces");
    await page.selectOption("#tr-kind", "NIGHT");
    await until(async () => { const k = await page.locator(".dt tbody tr.r td:nth-child(3)").allInnerTexts(); return k.length > 0 && k.every((x) => x === "NIGHT"); }, 15000, "NIGHT filter");
    check("traces: kind filter", (await page.locator(".dt tbody tr.r").count()) >= 2);
  });

  await step("trace: detail", async () => {
    await page.locator(".dt tbody tr.r a").first().click();
    await visible(page.locator(".ph h1 .mono", { hasText: "NIGHT-" }), 30000);
    await visible(page.locator(".dt tbody tr.r"), 30000);
    check("trace: table view is the default", (await page.getByRole("button", { name: "Table", exact: true }).getAttribute("aria-pressed")) === "true");
    check("trace: span tree indents child steps", (await page.locator(".tree-name[aria-level='2']").count()) > 0);
    await page.locator(".dt tbody tr.r", { hasText: "release_check" }).first().click();
    await visible(page.locator("#sd-rc"));
    check("trace: release_checks panel on a release_check step", (await page.locator("#sd-rc ~ table tbody tr").count()) >= 1);
    await shot(page, "trace_detail_table");
    await page.getByRole("button", { name: "Waterfall", exact: true }).click();
    await visible(page.locator(".wf i").first());
    check("trace: waterfall view draws timing bars", (await page.locator(".wf i").count()) > 5);
    await shot(page, "trace_detail_waterfall");
    await page.getByRole("button", { name: "Table", exact: true }).click();
    const [dl] = await Promise.all([page.waitForEvent("download", { timeout: 15000 }), page.getByRole("button", { name: "Export OTLP" }).click()]);
    const fs = await import("node:fs");
    const otlp = JSON.parse(fs.readFileSync(await dl.path(), "utf8"));
    check("trace: OTLP export has spans", Array.isArray(otlp.resourceSpans) && otlp.resourceSpans[0].scopeSpans[0].spans.length > 0);
  });

  await step("trace: explain run", async () => {
    await nav(page, "Traces");
    await page.selectOption("#tr-kind", "EXPLAIN");
    await until(async () => { const k = await page.locator(".dt tbody tr.r td:nth-child(3)").allInnerTexts(); return k.length > 0 && k.every((x) => x === "EXPLAIN"); }, 15000, "EXPLAIN filter");
    await page.locator(".dt tbody tr.r", { hasText: "SCRIPTED" }).locator("a").click();
    await visible(page.locator(".dt tbody tr.r", { hasText: "llm.explain" }), 30000);
    await page.locator(".dt tbody tr.r", { hasText: "llm.explain" }).click();
    await visible(page.locator("#sd-llm"));
    const t = await page.locator("#sd-llm").locator("xpath=..").innerText();
    check("trace: LLM call panel shows SCRIPTED, REJECT and no model called", /SCRIPTED/.test(t) && /check REJECT/.test(t) && /No model was called/.test(t), t.slice(0, 120).replace(/\n/g, " | "));
    await page.locator(".dt tbody tr.r", { hasText: "llm.validate" }).click();
    await visible(page.locator("#sd-gr"));
    check("trace: guardrail chips on the validate step", (await page.locator("#sd-gr ~ ul .chip", { hasText: "CHECK_REJECTED" }).count()) === 1);
    await shot(page, "trace_explain_scripted");
  });

  await step("run diff", async () => {
    // K6 Front 3 (F-35 Fix 4b): the page now calls GET /api/v1/runs/diff (obs_routes.py) instead of re-deriving
    // the diff client-side from two raw run fetches.
    await nav(page, "Traces");
    await page.selectOption("#tr-kind", "NIGHT");
    await until(async () => { const k = await page.locator(".dt tbody tr.r td:nth-child(3)").allInnerTexts(); return k.length > 0 && k.every((x) => x === "NIGHT"); }, 15000, "NIGHT filter");
    const boxes = page.locator(".dt tbody tr.r input[type=checkbox]");
    await boxes.nth(0).check();
    await boxes.nth(1).check();
    await page.getByRole("button", { name: "Compare 2 runs" }).click();
    await visible(page.locator("#d-rel"), 30000);
    check("diff: no in-browser-aggregation artifacts remain (old 'Step counts and values' / per-step 'In A only' lists)", (await page.locator("text=Step counts and values").count()) === 0 && (await page.locator("h3", { hasText: "In A only" }).count()) === 0);
    const body = await page.locator(".pagebody").innerText();
    check("diff: release results, step outcomes and guardrails sections", (await page.locator("#d-rel, #d-span, #d-gr").count()) === 3);
    check("diff: shows step-outcome differences between the two nights, or states there are none", /Step outcomes/.test(body));
    check("diff: no causal wording", !CAUSAL.test(body), (CAUSAL.exec(body) ?? [""])[0]);
    check("diff: states counts only for steps found in one run only, no fabricated names", /step\(s\) in A only/.test(body) && /step\(s\) in B only/.test(body));
    await shot(page, "run_diff");
  });

  await step("unit trace", async () => {
    await nav(page, "Traces");
    await page.fill("#tr-unit", "ZZ:MEDICAID:a3f19c:chain=ENC0001842");
    await page.press("#tr-unit", "Enter");
    await visible(page.locator(".dt tbody tr.r", { hasText: "llm.explain" }), 30000);
    check("traces: unit filter lists the unit's steps", (await page.locator(".dt tbody tr.r").count()) >= 2);
    await shot(page, "unit_trace");
  });

  await step("guardrails", async () => {
    await nav(page, "Guardrails");
    await visible(page.locator(".dt tbody tr.r"), 30000);
    const codes = await page.locator(".dt tbody tr.r td:nth-child(2)").allInnerTexts();
    check("guardrails: SUPPRESSED, CHECK_REJECTED and FALLBACK listed", ["SUPPRESSED", "CHECK_REJECTED", "FALLBACK"].every((c) => codes.some((x) => x.includes(c))), [...new Set(codes)].join(","));
    check("guardrails: fail mode shown (closed for CHECK_REJECTED)", (await page.locator(".dt tbody tr.r", { hasText: "CHECK_REJECTED" }).first().innerText()).includes("closed"));
    check("guardrails: read-only (no action buttons in rows)", (await page.locator(".dt tbody tr.r button").count()) === 0);
    await contentStart(page, "guardrails");
    await shot(page, "guardrails");
  });

  await step("llm usage", async () => {
    // K6 Front 3 (F-35 Fix 4a): the page now calls GET /api/v1/llm/usage (obs_routes.py) instead of aggregating
    // EXPLAIN-run calls in the browser; the stale "no usage endpoint yet" doc comment is gone with it.
    await nav(page, "LLM usage");
    await visible(page.locator("#lu-use ~ table tbody tr"), 30000);
    check("llm usage: rows carry a Day column (the server's own per-day grouping, GET /api/v1/llm/usage)", (await page.locator("#lu-use ~ table thead th", { hasText: "Day" }).count()) === 1);
    check("llm usage: configured explainer shows TEMPLATE", (await page.locator("#lu-cfg ~ dl .src.TEMPLATE").count()) === 1);
    check("llm usage: TEMPLATE and SCRIPTED sources, never LIVE in mock", (await page.locator("#lu-use ~ table .src.TEMPLATE").count()) >= 1 && (await page.locator("#lu-use ~ table .src.SCRIPTED").count()) >= 1 && (await page.locator(".src.LIVE").count()) === 0);
    check("llm usage: says the server computed it, over GET /api/v1/llm/usage", (await page.getByText("GET /api/v1/llm/usage").count()) === 1 && (await page.getByText("Computed in this browser").count()) === 0);
    await shot(page, "llm_usage");
  });

  await step("card citations", async () => {
    await nav(page, "Decision queue");
    await page.locator(".dt tr.r", { hasText: "encounter_chain" }).click();
    await page.getByRole("link", { name: "ZZ:MEDICAID:a3f19c:chain=ENC0001842" }).click();
    await visible(page.locator(".citations .cite"), 30000);
    check("card: citations per claim, linking evidence refs", (await page.locator(".citations .cite").count()) >= 3 && (await page.locator(".citations .cite a[href^='/units/']").count()) >= 1);
    check("card: citations labelled generated from facts with source TEMPLATE", (await page.locator(".citations .chip", { hasText: "generated from facts" }).count()) === 1 && (await page.locator(".citations .src.TEMPLATE").count()) === 1);
    check("card: fact-check status shown", (await page.locator(".citations [data-fact-check=TEMPLATE]").innerText()).includes("not fact-checked"));
    check("F-26: a HELD unit reads 'Why this is held'", (await page.locator("#why-held-h").innerText()) === "Why this is held");
    check("card: trace link in the rail", (await page.locator("#trace-h ~ a[href^='/runs?unit=']").count()) === 1);
    await shot(page, "card_citations");
    await nav(page, "Decision queue");
    await page.locator(".dt tr.r", { hasText: "encounter_line" }).click();
    await page.getByRole("link", { name: "ZZ:MEDICAID:a3f19c:chain=ENC0002377" }).click();
    await visible(page.locator("#why-held-h"), 30000);
    check("F-26: the EVALUATING veto-window unit does not read 'held'", (await page.locator("#why-held-h").innerText()) === "Why this mode applies");
    check("F-30: a null SLA is stated with its reason", (await page.locator(".cardhead").innerText()).includes("No SLA: unit never held"));
    check("F-30: a null dry run is stated with its reason", (await page.locator("#decide-h ~ dl").innerText()).includes("no dry run (no proposal)"));
  });

  await step("nightly trace link", async () => {
    await nav(page, "Nightly runs");
    await visible(page.locator("#run-h a[href^='/runs/NIGHT-']"), 30000);
    check("nightly: the run links to its NIGHT trace", true);
  });

  await step("scope: non-elevated reviewer", async () => {
    await signOut(page);
    await signInRoster(page, "bob.checker");
    await nav(page, "Traces");
    await visible(page.locator(".dt tbody tr.r"), 30000);
    const kinds = await page.locator(".dt tbody tr.r td:nth-child(3)").allInnerTexts();
    check("scope: a reviewer without operations or audit sees no engine-wide NIGHT runs", !kinds.includes("NIGHT") && kinds.length > 0, [...new Set(kinds)].join(","));
    await nav(page, "Guardrails");
    await visible(page.locator("#gr-code"));
    check("scope: SCOPE_DENIED is not offered to a non-audit reviewer", (await page.locator("#gr-code option[value=SCOPE_DENIED]").count()) === 0);
    await shot(page, "traces_scoped_reviewer");
  });

  check("no page or console errors (explorer suite)", errors.length === 0, errors.slice(0, 3).join("; "));
  await ctx.close();
}

// ---------------------------------------------------------------- K1: per-DAG agent profile
// Mock sessions live in React memory only (lib/auth.tsx): a `page.goto()` after sign-in reloads the SPA and signs
// the tab out, exactly as a real reload would. Every step below reaches the profile page by a client-side nav
// (deep-linking first, signing in on that same page, same as a presenter opening a bookmarked URL) rather than by
// `goto`-ing away from a session already open.
async function mockProfileSuite(browser) {
  const ctx = await browser.newContext({ viewport: VIEW });
  const page = await ctx.newPage();
  const errors = [];
  page.on("pageerror", (e) => errors.push(String(e)));
  page.on("console", (m) => m.type() === "error" && !/404/.test(m.text()) && errors.push(m.text()));

  await step("profile: contracts, rules, tiers, monitors, routing, contributors", async () => {
    await page.goto(`${BASE}/pipelines/P_ENCOUNTER/profile`);
    await signInRoster(page, "alice.maker");
    await visible(page.locator(".ph h1 .mono", { hasText: "P_ENCOUNTER" }), 30000);
    await visible(page.locator("#pp-contracts ~ div tbody tr"), 30000); // the profile call is async: wait for data, not just the header
    check("profile: contracts table lists gated relations", (await page.locator("#pp-contracts ~ div tbody tr").count()) > 0);
    check("profile: rules table lists at least one rule", (await page.locator("#pp-rules ~ div tbody tr").count()) > 0);
    check("profile: tiers table lists AUTH_MEDICAID", (await page.locator("#pp-tiers ~ div tbody tr", { hasText: "AUTH_MEDICAID" }).count()) === 1);
    check("profile: monitors chip reads not governed (L-10)", (await page.locator("#pp-monitors ~ p .chip", { hasText: "not governed" }).count()) === 1);
    // D10's disabled Edit affordance is superseded by K8 (PARTF_K7_K12_MOCK_CONTRACT.md) only when the K7-K12
    // preview flag is on; the SAFETY FIX gates K8's drawer behind NEXT_PUBLIC_DQ_PREVIEW_K7_K12 (off by default,
    // the live-demo state) so D10's ORIGINAL disabled affordance is what this default-state suite must see. The
    // drawer itself, enabled only under --preview on, is covered by the dedicated K8/K11 preview-ON suite below.
    check(
      `profile: Edit is present, ${PREVIEW === "on" ? "enabled (K8, preview on)" : "disabled (D10's original state, preview off)"}`,
      PREVIEW === "on" ? await page.getByRole("button", { name: "Edit" }).isEnabled() : await page.getByRole("button", { name: "Edit" }).isDisabled(),
    );
    check("profile: contributors table lists the governed artifacts, approvers shown or honestly 'none recorded'", (await page.locator("#pp-contributors ~ div tbody tr").count()) > 0);
    check("profile: digest shown", (await page.locator(".ph .sub code").first().innerText()).length > 0);
    await shot(page, "profile_p_encounter");
  });

  await step("profile: as-of selector fed by night traces (D9)", async () => {
    const sel = page.locator("#pp-asof");
    const optCount = await sel.locator("option").count();
    check("profile: as-of offers at least one past night", optCount > 1, `${optCount} option(s)`);
    if (optCount > 1) {
      await sel.selectOption({ index: 1 });
      await until(async () => new URL(page.url()).searchParams.has("as_of"), 15000, "as_of param set");
      await visible(page.locator(".ph .sub", { hasText: "as of" }), 15000);
      check("profile: picking a night sets as_of and shows it in the header", true);
    }
  });

  await step("K1 reversed-D4: trace page links to the pipeline's profile", async () => {
    await nav(page, "Traces");
    await page.selectOption("#tr-kind", "NIGHT");
    await until(async () => { const k = await page.locator(".dt tbody tr.r td:nth-child(3)").allInnerTexts(); return k.length > 0 && k.every((x) => x === "NIGHT"); }, 15000, "NIGHT filter");
    await page.locator(".dt tbody tr.r a").first().click();
    await visible(page.locator(".ph h1 .mono", { hasText: "NIGHT-" }), 30000);
    const note = page.locator("p.hint[role=note]", { hasText: "profile as of this trace" });
    await visible(note, 15000);
    check("trace: links to a pipeline's profile as of this trace (client-side only, D4 reversed)", (await note.locator("a[href*='/profile']").count()) > 0);
  });

  // Mock sessions are React state only; sign-out / sign-in-as-a-different-user is a client-side control (the
  // avatar menu, then the inline sign-in), never a reload. The deep-linked pipeline profile is reached the same
  // way a presenter would: via the Pipeline map's own "Agent profile" link, added for this package.
  await step("D7: SNAP-only reviewer on a Medicaid-only pipeline", async () => {
    await signOut(page);
    await signInRoster(page, "frank.snap");
    await nav(page, "Pipeline map");
    await visible(page.locator("#gated-h"), 30000);
    await page.locator("tbody tr", { hasText: "P_ENCOUNTER" }).getByRole("link", { name: "Agent profile" }).click();
    await visible(page.locator("[role=alert].banner.t-warn"), 30000);
    check("profile: SNAP-only reviewer gets an honest access message, not a blank page", (await page.getByText("You do not have access").count()) === 1);
    await shot(page, "profile_scoped_403");
    await page.goBack();
    await visible(page.locator("#gated-h"), 30000);
    await page.locator("tbody tr", { hasText: "P_ELIGIBILITY" }).getByRole("link", { name: "Agent profile" }).click();
    await visible(page.locator(".ph h1 .mono", { hasText: "P_ELIGIBILITY" }), 30000);
    await visible(page.locator("#pp-tiers ~ div tbody tr"), 30000);
    check("profile: the SAME reviewer sees a pipeline whose authorities include SNAP (control)", (await page.locator("#pp-tiers ~ div tbody tr", { hasText: "AUTH_SNAP" }).count()) === 1);
    await signOut(page);
    await signInRoster(page, "alice.maker");
  });

  check("no page or console errors (profile suite)", errors.length === 0, errors.slice(0, 3).join("; "));
  await ctx.close();
}

// ---------------------------------------------------------------- live suite (Part E live flow on the new shell)
async function http(method, path, body, token, url) {
  const h = { "Content-Type": "application/json" };
  if (token) h.Authorization = `Bearer ${token}`;
  const r = await fetch(url ?? `${API}${path}`, { method, headers: h, body: body === undefined ? undefined : JSON.stringify(body) });
  const text = await r.text();
  return [r.status, text ? JSON.parse(text) : null];
}
async function tok(user, challenge) {
  const b = challenge ? { username: user, step_up: true, challenge } : { username: user };
  return (await http("POST", "", b, null, IDP))[1].access_token;
}

async function live(browser) {
  const NIGHT = 180_000;
  const missing = [];
  const challenges = [];
  const ctx = await browser.newContext({ viewport: VIEW });
  const page = await ctx.newPage();
  const errors = [];
  page.on("pageerror", (e) => errors.push(String(e)));
  page.on("request", (r) => r.method() === "POST" && r.url().endsWith("/challenge") && challenges.push([r.url(), r.postData()]));
  const audit = async (where) => {
    const n = await page.getByText("none was supplied by the server").count();
    if (n) missing.push(`${where}: ${n}`);
  };
  const runNight = async () => {
    const btn = clock(page).getByRole("button", { name: "Run next night" });
    await until(async () => await btn.isEnabled(), 30000, "Run enabled");
    await btn.click();
    const st = page.locator(".simmsg");
    await visible(st, NIGHT);
    return st.innerText();
  };
  const approveCr = async (user, cr) => {
    await nav(page, "Change requests");
    await page.getByRole("link", { name: cr }).first().click();
    await visible(page.locator("#dec-h"), 30000);
    await audit(`CR ${cr}`);
    await page.getByLabel("I reviewed every item diff").check();
    await page.click("button:has-text('Request challenge')");
    await page.locator("input[id$='-stepup-user']").fill(user);
    await page.click("button:has-text('Step up')");
    await page.locator(`button:has-text('Approve change request ${cr}')`).click();
    const out = page.locator("[role=status] h2, p.err[role=alert]").first();
    await visible(out, 120000);
    return out.innerText();
  };
  const proposeTier = async (to) => {
    await nav(page, "Agents");
    await visible(page.locator("#tier-h"), 30000);
    await page.getByRole("button", { name: "Show track record for dq-reconciliation RELEASE AUTH_MEDICAID" }).click();
    await page.selectOption("#pt-to", to);
    await page.fill("#pt-why", `Smoke: set RELEASE AUTH_MEDICAID to ${to} with the track record shown.`);
    await page.locator("#pt-h ~ form button[type=submit]").click();
    const link = page.locator("[role=status] a", { hasText: "Open CR-" });
    await visible(link, 60000);
    return [/CR-[0-9a-f]+/.exec(await link.innerText())[0], await link.locator("xpath=..").innerText()];
  };

  const tenant = (await http("GET", "/tenant"))[1];
  await page.goto(`${BASE}/`);
  await signInLive(page, "maker");

  await step("live: header and strip", async () => {
    await hasText(clock(page), "Simulated", 30000);
    const s = (await http("GET", "/summary", undefined, await tok("maker")))[1];
    const pop = await openSignals(page);
    check("topband: genesis alert follows genesis_state", ((await page.locator(".hdr .env-test").count()) === 1) === (s.genesis_state !== "RATIFIED"), s.genesis_state);
    const slaAlarm = (await signalRow(pop, "SLA remaining").locator(".chip.t-bad").count()) === 1;
    check("topband: SLA remaining alert iff negative", slaAlarm === (s.oldest_sla_remaining_min !== null && s.oldest_sla_remaining_min < 0), `${s.oldest_sla_remaining_min} min`);
    check("topband: explainer tile", (await signalRow(pop, "Explainer").innerText()).includes("template; decisions deterministic"));
    await closeSignals(page);
    check(
      "topband: simulated date + operator button",
      (await clock(page).innerText()).includes(s.sim.current_date) && (await clock(page).getByRole("button", { name: "Run next night" }).count()) === 1,
      s.sim.current_date,
    );
    check("header: tenant from API", (await page.locator(".tenant-name").innerText()) === tenant.display_name);
    await contentStart(page, "queue (live)");
    await shot(page, "live_queue");
  });

  await step("live: two nights", async () => {
    const t1 = await runNight();
    await page.locator(".simmsg .x").click();
    const t2 = await runNight();
    check("sim: two nights run", t1.includes("Ran ") && t2.includes("Ran ") && t1 !== t2, `${t1.slice(0, 40)} | ${t2.slice(0, 40)}`);
    await page.locator(".simmsg[role=status]").getByRole("link", { name: "View run" }).click();
    await visible(page.locator("#run-h"), 30000);
    check("sim: run summary + explanation", (await page.locator("#run-h ~ .explanation").count()) === 1 && (await page.locator(".countgrid .count").count()) === 10);
    await audit("nightly run");
    const links = page.locator(".countgrid .count a[href^='/units/']");
    check("sim: counts link to units", (await links.count()) > 0, `${await links.count()} unit links`);
    if (await links.count()) {
      await links.first().click();
      await visible(page.locator("#expl-h"), 30000);
      check("sim: unit link opens a card with an explanation", (await page.locator("#expl-h ~ .explanation").count()) === 1);
      await audit("card from nightly");
      await shot(page, "live_card");
    }
  });

  await step("live: tiers", async () => {
    await nav(page, "Agents");
    await visible(page.locator("#reg-h"), 30000);
    await visible(page.locator("#tr-h"), 60000);
    check("agents: registry ACTIVE + REGISTERED next phase", (await page.getByText("next phase").count()) === 1);
    check("agents: entitlements live", (await page.locator("#ent-h ~ div .chip", { hasText: "live" }).count()) >= 1);
    check("agents: tier ladder + track record", (await page.locator(".tier-step").count()) >= 1 && (await page.locator("#tr-h").count()) === 1);
    await audit("agents");
    let [crDem, txt] = await proposeTier("L2");
    check("agents: demotion L4->L2 drafted (1 approval)", txt.includes("needs 1 approval"), txt.slice(0, 100));
    await signOut(page);
    await signInLive(page, "checker");
    let out = await approveCr("checker", crDem);
    check("cr: demotion applied by one STATE approver", out.includes("approved and applied"), out.slice(0, 120));
    await signOut(page);
    await signInLive(page, "maker");
    const [crPro, txt2] = await proposeTier("L3");
    check("agents: promotion L2->L3 says needs two State approvers", (await page.getByText("needs two").count()) === 1 && txt2.includes("needs 2 approval"), txt2.slice(0, 100));
    await signOut(page);
    await signInLive(page, "checker");
    out = await approveCr("checker", crPro);
    check("cr: first approval does not apply", !out.includes("approved and applied"), out.slice(0, 120));
    await signOut(page);
    await signInLive(page, "checker2");
    out = await approveCr("checker2", crPro);
    check("cr: second STATE approver applies promotion", out.includes("approved and applied"), out.slice(0, 120));
    const cr = challenges.filter(([u, d]) => u.includes("/governance/change-requests/") && d).map(([, d]) => JSON.parse(d));
    check("cr: every CR challenge carried explanation_ref", cr.length > 0 && cr.every((c) => "explanation_ref" in c), `${cr.length} challenge(s)`);
    const tiers = Object.fromEntries((await http("GET", "/autonomy", undefined, await tok("maker")))[1].map((r) => [`${r.action_class}|${r.authority}`, r.tier]));
    check("api: RELEASE AUTH_MEDICAID is now L3", tiers["RELEASE|AUTH_MEDICAID"] === "L3", JSON.stringify(tiers));
  });

  await step("live: veto", async () => {
    await signOut(page);
    await signInLive(page, "maker");
    const t3 = await runNight();
    await page.locator(".simmsg[role=status]").getByRole("link", { name: "View run" }).click();
    await visible(page.locator("#run-h"), 30000);
    const vw = page.locator(".countgrid .count", { hasText: "Veto windows opened" });
    const nveto = Number(await vw.locator(".n").innerText());
    check("sim: a veto window opened at L3", nveto > 0, `${t3.slice(0, 60)}; veto windows ${nveto}`);
    if (nveto) {
      await vw.locator("a[href^='/units/']").first().click();
      await visible(page.locator("#veto-h"), 30000);
      check("card: veto countdown shown", (await page.locator("#veto-h .chip").count()) === 1);
      await audit("veto card");
      await page.selectOption("#veto-reason", "DATA_SUSPECT");
      await page.getByRole("button", { name: "Veto", exact: true }).click();
      const res = page.locator("[role=status] h2", { hasText: "Veto" });
      await visible(res, 60000);
      check("card: veto recorded", (await res.innerText()).includes("Veto recorded"));
      await nav(page, "Agents");
      await visible(page.locator("#tier-h"), 30000);
      const rows = (await http("GET", "/autonomy", undefined, await tok("maker")))[1];
      const rel = rows.find((r) => r.action_class === "RELEASE" && r.authority === "AUTH_MEDICAID") ?? {};
      check(
        "agents: veto demoted RELEASE AUTH_MEDICAID to L2",
        rel.tier === "L2" && (rel.demotions ?? []).some((d) => d.reason_code === "VETO"),
        `${rel.tier} ${(rel.demotions ?? []).map((d) => d.reason_code)}`,
      );
      await page.getByRole("button", { name: "Show track record for dq-reconciliation RELEASE AUTH_MEDICAID" }).click();
      check("agents: demotion shown with explanation", (await page.locator("#tr-h ~ ol .explanation").count()) >= 1);
    }
  });

  await step("live: remaining nights + monitors", async () => {
    for (;;) {
      const q = (await http("GET", "/summary", undefined, await tok("maker")))[1].sim.queued;
      if (q === 0) break;
      await page.locator(".simmsg .x").click().catch(() => {});
      await runNight();
      await sleep(500);
    }
    check("sim: all nights run", (await http("GET", "/sim/nights", undefined, await tok("maker")))[1].current_date === "2026-04-01");
    await nav(page, "Monitors");
    await visible(page.locator("#mon-h tbody, tbody .chip"), 30000);
    const rows = (await http("GET", "/monitor", undefined, await tok("maker")))[1];
    const sig = rows.filter((r) => ["SIGNAL", "SHIFT", "WATCH"].includes(r.status));
    check("monitor: planted signals present", sig.length > 0, sig.map((r) => `${r.relation} ${r.metric}`).join(", "));
    check("monitor: a clean relation stays OK", rows.some((r) => r.status === "OK"));
    await audit("monitoring");
    await page.locator("tbody tr", { hasText: "SIGNAL" }).first().locator("button.linklike").click();
    await visible(page.locator("svg.chart[role=img]"), 30000);
    check("monitor: control limits drawn", (await page.locator("svg.chart .limit").count()) >= 1);
  });

  await step("live: reconciliation", async () => {
    await nav(page, "Reconciliation");
    await visible(page.locator("#pr-h"), 30000);
    const pr = (await http("GET", "/parallel-run", undefined, await tok("maker")))[1];
    const stats = new Set(pr.map((r) => r.status));
    check("recon: MATCH / WITHIN / BREAK present", ["MATCH", "WITHIN", "BREAK"].every((s) => stats.has(s)), [...stats].join(","));
    const brk = page.locator("button[aria-label^='Explain break'], button[aria-label^='Re-explain break']");
    if (await brk.count()) {
      await brk.first().click();
      await page.locator("select[id$='-code']").first().selectOption("LEGACY_DEFECT");
      await page.click("button:has-text('Request challenge')");
      await page.locator("input[id$='-stepup-user']").fill("maker");
      await page.click("button:has-text('Step up')");
      await page.click("button:has-text('Record LEGACY_DEFECT with step-up')");
      await sleep(3000);
      const after = (await http("GET", "/parallel-run", undefined, await tok("maker")))[1];
      check("recon: LEGACY_DEFECT recorded with step-up", after.some((r) => r.explain_code === "LEGACY_DEFECT"));
    }
    const fi = (await http("GET", "/feeds/items", undefined, await tok("checker")))[1];
    check("recon: feed items visible to a release user (to ack one)", fi.length > 0, `${fi.length} visible`);
    if (fi.length) {
      await page.getByRole("button", { name: /^Acknowledge feed item/ }).first().click();
      await sleep(2000);
    }
    await audit("reconciliation");
  });

  await step("live: coverage, rules, look-back, audit", async () => {
    await nav(page, "Coverage");
    await visible(page.locator("#cde-h"), 30000);
    check("coverage: CDE view", (await page.locator("#cde-h ~ div tbody tr").count()) > 0);
    await audit("coverage");
    await nav(page, "Rules");
    await hasText(page.locator("#rules-h"), "shown", 60000);
    check("rules: classification column rendered", (await page.locator("th", { hasText: "Classification" }).count()) === 1);
    const p2 = await page.locator("tbody .chip.t-crit", { hasText: "part2" }).count();
    check("rules: part2 badge present", p2 > 0, `${p2} part2 chips`);
    const mt = await tok("maker");
    const rule = (await http("GET", "/rules", undefined, mt))[1].rules.find((r) => `${r.id}@${r.entity}` === "R-ELG-001@ELIG_SEGMENT");
    const h = rule.effective_hash;
    const [st, cr] = await http(
      "POST",
      "/governance/change-requests",
      {
        rationale: "Smoke: revoke and replace R-ELG-001 for look-back",
        items: [
          { artifact_id: `ZZ:revocation:R-ELG-001@ELIG_SEGMENT:${h.slice(0, 12)}`, kind: "REVOCATION", op: "PUT", content: { rule: "ZZ:R-ELG-001@ELIG_SEGMENT", hash: h, reason: "smoke look-back revocation" } },
          { artifact_id: "ZZ:overlay:R-ELG-001", kind: "OVERLAY", op: "PUT", content: { verb: "raise_priority", priority: "Critical", reason: "smoke replacement", approved_ref: "SCR-LB" } },
        ],
      },
      mt,
    );
    if (st === 200) {
      const ct = await tok("checker");
      const ch = (await http("POST", `/governance/change-requests/${cr.cr_id}/challenge`, { verdict: "APPROVED" }, ct))[1].challenge;
      const [st2, r2] = await http("POST", `/governance/change-requests/${cr.cr_id}/decide`, { verdict: "APPROVED", step_up_token: await tok("checker", ch) }, ct);
      check("lookback: revocation applied (API setup)", st2 === 200, JSON.stringify(r2).slice(0, 160));
    } else {
      check("lookback: revocation proposed (API setup)", false, JSON.stringify(cr).slice(0, 200));
    }
    await nav(page, "Look-back");
    await hasText(page.locator("#lb-h"), "shown", 60000);
    check("lookback: live now + at open", (await page.getByText("live now:").count()) >= 1 && (await page.getByText("at open:").count()) >= 1);
    await audit("look-back");
    await shot(page, "live_lookback");
    await signOut(page);
    await signInLive(page, "auditor");
    await nav(page, "Audit chain");
    await page.click("button:has-text('Verify chain')");
    await visible(page.getByText("Audit chain: intact"), 60000);
    check("audit: both chains verified", (await page.getByText("Blackboard chain: intact").count()) === 1);
    check("audit: readable label column", (await page.locator("th", { hasText: "label" }).count()) === 1);
  });

  check("explanation contract: no screen lacked an explanation", missing.length === 0, missing.join("; "));
  check("no uncaught page errors", errors.length === 0, errors.slice(0, 3).join("; "));
  await ctx.close();
}

// ---------------------------------------------------------------- K2c: Catalog, nodes drawer, wiring wizard
async function mockCatalogSuite(browser) {
  const ctx = await browser.newContext({ viewport: VIEW });
  const page = await ctx.newPage();
  const errors = [];
  page.on("pageerror", (e) => errors.push(String(e)));
  page.on("console", (m) => m.type() === "error" && !/404/.test(m.text()) && errors.push(m.text()));
  await page.goto(`${BASE}/`);
  await signInRoster(page, "alice.maker");

  await step("catalog: table first, grouped by provenance", async () => {
    await nav(page, "Catalog");
    await visible(page.locator(".dt tbody tr.r"), 30000);
    await contentStart(page, "catalog");
    check("catalog: HARNESS group listed", (await page.locator(".dt tbody tr.grp", { hasText: "HARNESS" }).count()) >= 1);
    check("catalog: dependency-order strip shown", (await page.locator("ol li.mono").count()) >= 1);
    await shot(page, "catalog_table");
  });

  await step("catalog: nodes drawer, Effective tier column", async () => {
    await page.locator(".dt tbody tr.r", { hasText: "P_ENCOUNTER" }).click();
    await visible(page.locator("#cat-nodes-h"), 15000);
    await visible(page.locator("#cat-nodes-h ~ div tbody tr"), 15000);
    check("catalog: task gates table lists at least one gate", (await page.locator("#cat-gates-h ~ div tbody tr").count()) >= 1);
    check("catalog: nodes drawer shows an Effective tier value for a TASK_GATE row", (await page.locator("#cat-nodes-h ~ div tbody tr .chip", { hasText: /L[1-4]/ }).count()) >= 1);
    await shot(page, "catalog_nodes_drawer");
  });

  await step("wire: discover preview, honest refusal on a bad manifest path", async () => {
    await nav(page, "Wire a pipeline");
    await visible(page.locator("#w-id"), 15000);
    await page.fill("#w-manifest", "pipelines/does_not_exist/target/manifest.json");
    await page.click("button:has-text('Preview (discover)')");
    await visible(page.locator(".banner.t-warn", { hasText: "Refused" }), 15000);
    check("wire: discover REFUSED on an unknown manifest path, honestly labelled", true);
    await page.fill("#w-manifest", "pipelines/tmsis_outbound/target/manifest.json");
    await page.click("button:has-text('Preview (discover)')");
    await visible(page.locator("table", { hasText: "outbound_eligibility_extract" }), 15000);
    check("wire: discover preview lists the real manifest's nodes, never a fabricated link", (await page.locator("td.mono", { hasText: "outbound_eligibility_extract" }).count()) >= 1);
    check("wire: an UNLAYERED-equivalent or declared layer is shown honestly (never inferred from folder names)", (await page.getByText("OUTBOUND").count()) >= 1);
    await shot(page, "wire_discover");
  });

  let crId = "";
  await step("wire: propose a PIPELINE + TASK_GATE items (one already-gated, one new)", async () => {
    await page.fill("#w-id", "tmsis_outbound");
    await page.fill("#w-platform", "duckdb");
    await page.fill("#w-domains", "CLAIMS, ELIGIBILITY");
    await page.fill("#w-task-0", "outbound_eligibility_extract");
    await page.fill("#w-ds-0", "silver.eligibility_span"); // already a HARNESS gated_node (P_ELIGIBILITY)
    await page.click("button:has-text('Add another gate')");
    await page.fill("#w-task-1", "outbound_summary");
    await page.fill("#w-ds-1", "gold.tmsis_outbound_subset"); // a genuinely new dataset
    await page.fill("#w-why", "Wire tmsis_outbound in advisory mode for the smoke suite's demo moment.");
    await page.click("button:has-text('Propose wiring')");
    await visible(page.locator("#wire-impact-h"), 15000);
    const link = page.locator("#wire-impact-h a[href^='/governance/']");
    await visible(link);
    crId = (await link.innerText()).trim();
    check("wire: CR drafted with a cr_id", /^CR-/.test(crId), crId);
    check("wire: needs 2 approvals (PIPELINE/TASK_GATE are never lower-only)", (await page.locator("[role=status]", { hasText: "Needs" }).innerText()).includes("2"));
    check("wire: a single approver is called out as refused", (await page.getByText("A single approver is refused").count()) === 1);
    check("wire: already-gated dataset shown as unchanged, reopens nothing (ORCH-1)", (await page.locator("tr", { hasText: "silver.eligibility_span" }).locator(".chip", { hasText: "unchanged (already gated)" }).count()) === 1);
    check("wire: the newly introduced dataset is named", (await page.getByText("gold.tmsis_outbound_subset").count()) >= 1);
    await shot(page, "wire_impact");
  });

  // Mock sessions live in React memory only (lib/auth.tsx): switching users is sign-out / sign-in-as-a-different-
  // user (the avatar menu, then the inline roster), never page.goto -- a goto reloads the SPA and resets the
  // mock's module-level state, which would silently discard the CR this step needs (profile suite's own pattern).
  await step("wire: a single approver does not apply it; a second, different approver does", async () => {
    await signOut(page);
    await signInRoster(page, "bob.checker");
    await nav(page, "Change requests");
    await page.getByRole("link", { name: crId }).first().click();
    await visible(page.locator("#dec-h"), 15000);
    check("cr: approvals required is 2", (await page.locator(".mono", { hasText: "0/2" }).count()) >= 1);
    await page.getByLabel("I reviewed every item diff").check();
    await page.click("button:has-text('Request challenge')");
    await page.locator("input[id$='-stepup-user']").fill("bob.checker");
    await page.click("button:has-text('Step up')");
    await page.locator(`button:has-text('Approve change request ${crId}')`).click();
    await visible(page.locator("[role=status]"), 15000);
    check("wire: the database refuses a single approver (status stays short of applied)", !(await page.getByText("approved and applied").count()));
    await signOut(page);
    await signInRoster(page, "carol.reviewer");
    await nav(page, "Change requests");
    await page.getByRole("link", { name: crId }).first().click();
    await visible(page.locator("#dec-h"), 15000);
    await page.getByLabel("I reviewed every item diff").check();
    await page.click("button:has-text('Request challenge')");
    await page.locator("input[id$='-stepup-user']").fill("carol.reviewer");
    await page.click("button:has-text('Step up')");
    await page.locator(`button:has-text('Approve change request ${crId}')`).click();
    await visible(page.getByText("approved and applied"), 15000);
    check("wire: a second, different approver applies it", true);
  });

  await step("catalog: the wired pipeline now appears under WIRED", async () => {
    await signOut(page);
    await signInRoster(page, "alice.maker");
    await nav(page, "Catalog");
    await visible(page.locator(".dt tbody tr.r"), 30000);
    check("catalog: WIRED group appears after apply", (await page.locator(".dt tbody tr.grp", { hasText: "WIRED" }).count()) === 1);
    check("catalog: tmsis_outbound listed as WIRED", (await page.locator(".dt tbody tr.r", { hasText: "tmsis_outbound" }).count()) === 1);
    await shot(page, "catalog_wired");
  });

  check("no page or console errors (K2c suite)", errors.length === 0, errors.slice(0, 3).join("; "));
  await ctx.close();
}

// ---------------------------------------------------------------- K3: backtest mechanics and promote-to-block
// Verbatim substring of dqagent/backtest.py's own MODE_ONLY_DISCLOSURE constant (the exact opening clause), used
// below to assert the Backtest panel renders the disclosure VERBATIM, not a paraphrase (K3_DESIGN.md §6
// Decision 1, the single most important check this seat's brief names).
const DELTA_NOTE_VERBATIM =
  "This change does not alter release/hold/pending outcomes in this backtest; `block` mode is not yet " +
  "behaviorally enforced";

async function mockBacktestSuite(browser) {
  const ctx = await browser.newContext({ viewport: VIEW });
  const page = await ctx.newPage();
  const errors = [];
  page.on("pageerror", (e) => errors.push(String(e)));
  page.on("console", (m) => m.type() === "error" && !/404/.test(m.text()) && errors.push(m.text()));

  let crId = "";
  await step("promote: page renders an advisory gate, picks it, proposes a mode-change CR", async () => {
    await page.goto(`${BASE}/pipelines/tmsis_outbound/promote`);
    await signInRoster(page, "alice.maker");
    await visible(page.locator("#promote-pipe-h"), 30000);
    check("promote: lists the pipeline's TASK_GATE items", (await page.locator("td.mono", { hasText: "outbound_eligibility_extract" }).count()) >= 1);
    await page.getByRole("radio", { name: /Select gate outbound_eligibility_extract/ }).check();
    check("promote: proposed mode shown as block", (await page.locator("#promote-mode-h ~ * .chip", { hasText: "block" }).count()) >= 1);
    check(
      "promote: states plainly this is a label, not a behavior change (scope boundary, never softened)",
      (await page.getByText("This promotes a label, not a behavior.").count()) === 1,
    );
    check(
      "promote: scope-boundary banner names the real mechanism honestly",
      (await page.locator(".banner.t-warn", { hasText: "This promotes a label" }).innerText()).includes("read only by the"),
    );
    await page.fill("#pr-why", "Smoke: promote outbound_eligibility_extract to block (mechanics only, K3).");
    await page.click("button:has-text('Propose promotion')");
    await visible(page.locator("#promote-result-h ~ p a[href^='/governance/']"), 15000);
    const link = page.locator("#promote-result-h ~ p a[href^='/governance/']");
    crId = (await link.innerText()).trim();
    check(
      "promote: CR drafted with a cr_id, needs 2 approvals",
      /^CR-/.test(crId) && (await page.locator("#promote-result-h ~ p", { hasText: "Needs" }).innerText()).includes("2"),
    );
    check(
      "promote: tells the operator where the real backtest trigger lives (the CLI, not this page)",
      (await page.locator("#pr-why").locator("xpath=../..").innerText()).includes("dqagent backtest"),
    );
    await shot(page, "promote_page");
  });

  await step("cr: a fresh mode-change CR with no evidence shows the honest refusal disclosure", async () => {
    await nav(page, "Change requests");
    await page.getByRole("link", { name: crId }).first().click();
    await visible(page.locator("#bt-h"), 15000);
    check("cr: Backtest panel present on a CR with no evidence", (await page.locator("#bt-h").innerText()).includes("0 report(s) linked"));
    check(
      "cr: refusal-without-evidence is surfaced honestly (not pre-emptively hidden)",
      (await page.getByText("promotion requires a linked backtest report").count()) >= 1,
    );
    await shot(page, "cr_backtest_no_evidence");
  });

  await step("cr: the server itself refuses an approve attempt on a CR with no linked evidence", async () => {
    await signOut(page);
    await signInRoster(page, "bob.checker");
    await nav(page, "Change requests");
    await page.getByRole("link", { name: crId }).first().click();
    await visible(page.locator("#dec-h"), 15000);
    await page.getByLabel("I reviewed every item diff").check();
    await page.click("button:has-text('Request challenge')");
    await page.locator("input[id$='-stepup-user']").fill("bob.checker");
    await page.click("button:has-text('Step up')");
    await page.locator(`button:has-text('Approve change request ${crId}')`).click();
    await visible(page.locator("p.err[role=alert]"), 15000);
    check(
      "cr: the database's own refusal reaches the approver (FAIL is never shown as autonomous; this is a real, server-side refusal)",
      (await page.locator("p.err[role=alert]").innerText()).includes("promotion requires a linked backtest report"),
    );
    await signOut(page);
    await signInRoster(page, "alice.maker");
  });

  await step("cr: a seeded mode-only CR's linked report renders delta_note VERBATIM", async () => {
    await nav(page, "Change requests");
    await page.locator(".pagebody tr", { hasText: "Backtest ran clean over 22 synthetic nights" }).getByRole("link", { name: /^CR-/ }).click();
    await visible(page.locator("#bt-h"), 15000);
    check("cr: evidence linked (1 report)", (await page.locator("#bt-h").innerText()).includes("1 report(s) linked"));
    await page.locator("#bt-h ~ * button", { hasText: /^View report/ }).first().click();
    await visible(page.locator(".banner.t-warn", { hasText: "No causal block, by design." }), 15000);
    const noteText = await page.locator(".banner.t-warn", { hasText: "No causal block, by design." }).innerText();
    check("cr: delta_note renders VERBATIM (the exact disclosure sentence, not a paraphrase)", noteText.includes(DELTA_NOTE_VERBATIM), noteText.slice(0, 160));
    check("cr: label shown honestly as SYNTHESIZED, never LIVE", (await page.locator(".chip", { hasText: "SYNTHESIZED" }).count()) >= 1 && (await page.locator(".chip", { hasText: "LIVE" }).count()) === 0);
    await shot(page, "cr_backtest_mode_only");
  });

  await step("cr: a seeded non-mode-only CR's report has delta_note null and shows a real delta", async () => {
    await nav(page, "Change requests");
    await page.locator(".pagebody tr", { hasText: "Backtest shows the tightened release ceiling" }).getByRole("link", { name: /^CR-/ }).click();
    await visible(page.locator("#bt-h"), 15000);
    await page.locator("#bt-h ~ * button", { hasText: /^View report/ }).first().click();
    await visible(page.locator(".tablewrap table caption", { hasText: "Backtest relation deltas" }), 15000);
    check(
      "cr: no delta_note banner when the change is not mode-only",
      (await page.locator(".tablewrap table caption", { hasText: "Backtest relation deltas" }).locator("xpath=../..").locator(".banner.t-warn", { hasText: "No causal block" }).count()) === 0,
    );
    check("cr: a real, non-zero relation delta is shown", (await page.locator("td.mono", { hasText: "silver.eligibility_span" }).locator("xpath=../..").innerText()).match(/[+-][1-9]/) !== null);
    await shot(page, "cr_backtest_real_delta");
  });

  check("no page or console errors (K3 suite)", errors.length === 0, errors.slice(0, 3).join("; "));
  await ctx.close();
}

// ---------------------------------------------------------------- K4: pipelines graph (React Flow) and its
// Table/Outline accessible default. `tmsis_outbound` is seeded WIRED from the start (server.ts's own fixture),
// with a real manifest behind it (6 discovered dbt nodes) plus its one TASK_GATE-governed dataset -- a genuine,
// multi-node graph with real edges, not a single isolated node, so the keyboard-only traversal check below has
// something to traverse.
async function mockGraphSuite(browser) {
  const ctx = await browser.newContext({ viewport: VIEW });
  const page = await ctx.newPage();
  const errors = [];
  page.on("pageerror", (e) => errors.push(String(e)));
  page.on("console", (m) => m.type() === "error" && !/404/.test(m.text()) && errors.push(m.text()));
  await page.goto(`${BASE}/`);
  await signInRoster(page, "alice.maker");

  await step("graph: reachable from the Catalog, same node set as the nodes drawer", async () => {
    await nav(page, "Catalog");
    await visible(page.locator(".dt tbody tr.r"), 30000);
    const row = page.locator(".dt tbody tr.r", { hasText: "tmsis_outbound" });
    await visible(row, 15000);
    const nodesInDrawer = await (async () => {
      await row.click();
      await visible(page.locator("#cat-nodes-h ~ div tbody tr"), 15000);
      return page.locator("#cat-nodes-h ~ div tbody tr").count();
    })();
    await row.getByRole("link", { name: /Open the pipelines graph for/ }).click();
    await until(async () => new URL(page.url()).pathname === "/pipelines/graph", 30000, "the graph page");
    check("graph: catalog row links straight to /pipelines/graph?id=... (reachable, not just asserted)", true);
    await visible(page.locator("button", { hasText: "Table view (accessible)" }), 15000);
    check(
      "graph: the FIRST focusable element on the page is 'Table view (accessible)' (DEC-020)",
      await page.evaluate(() => {
        const first = document.querySelector("button, a, input, select, textarea, [tabindex]");
        return !!first && first.textContent?.includes("Table view (accessible)");
      }),
    );
    check("graph: opens in the Table/Outline view by default (accessible default, before any interaction)", (await page.locator("table caption", { hasText: "The same node set" }).count()) === 1);
    const tableRows = await page.locator("table caption", { hasText: "The same node set" }).locator("xpath=../..").locator("tbody tr").count();
    check("graph and table show the same node set (EXECUTION_PLAN K4 exit criterion)", tableRows === nodesInDrawer, `table=${tableRows} drawer=${nodesInDrawer}`);
    await shot(page, "graph_table_default");
  });

  await step("graph: switching to Graph view renders React Flow with the SAME node set", async () => {
    const tableRows = await page.locator("table tbody tr").count();
    await page.locator("button", { hasText: "Graph view (React Flow)" }).click();
    await visible(page.locator(".react-flow"), 15000);
    const rfNodes = await page.locator(".react-flow__node").count();
    check("graph: React Flow renders the same node count as the table", rfNodes === tableRows, `react-flow=${rfNodes} table=${tableRows}`);
    check("graph: at least one edge is drawn (a real, non-trivial dependency graph, not a single isolated node)", (await page.locator(".react-flow__edge").count()) >= 1);
    await shot(page, "graph_reactflow_view");
  });

  await step("graph: keyboard-only traversal follows an edge (arrow keys move between connected nodes)", async () => {
    await page.locator(".react-flow__renderer, .react-flow__pane").first().click();
    await page.keyboard.press("Tab");
    const firstFocused = await page.evaluate(() => document.activeElement?.className ?? "");
    check("graph: Tab focuses onto a React Flow node (keyboard-reachable, not mouse-only)", /react-flow__node/.test(firstFocused), firstFocused);
    await page.keyboard.press("ArrowRight");
    await page.keyboard.press("ArrowRight");
    const afterArrow = await page.evaluate(() => document.activeElement?.className ?? "");
    check(
      "graph: arrow keys move focus (keyboard-only traversal; React Flow's own built-in behavior, not disabled)",
      /react-flow__node/.test(afterArrow),
      afterArrow,
    );
    await shot(page, "graph_keyboard_traversal");
  });

  await step("graph: the saved preference forces Table view back on", async () => {
    const cb = page.locator("label", { hasText: "Always open in table view" }).locator("input[type=checkbox]");
    await cb.check();
    await page.goto(`${BASE}/pipelines/graph?id=tmsis_outbound`);
    await signInRoster(page, "alice.maker");
    await visible(page.locator("table caption", { hasText: "The same node set" }), 15000);
    check("graph: 'Graph view: Table' saved preference forces the accessible default back on reload", true);
    await shot(page, "graph_saved_pref_table");
  });

  check("no page or console errors (K4 suite)", errors.length === 0, errors.slice(0, 3).join("; "));
  await ctx.close();
}

// ---------------------------------------------------------------- K8 + K11: PRE-BACKEND profile-edit drawer,
// evaluations history/diff, annotation queue and the decision-card feedback control (PARTF_K7_K12_MOCK_CONTRACT.md).
// Every screen/control built under this contract must show the structural PRE-BACKEND banner -- except the
// evaluations HISTORY list, which calls the REAL, already-wired GET /evals (same call /agents' attestation
// picker already makes) and therefore carries no such banner; this suite checks that split explicitly.
const PRE_BACKEND_TEXT = "PRE-BACKEND — NOT WIRED TO A REAL SYSTEM";
const NOT_IN_DEMO_TEXT = "This screen isn’t part of the current demo.";

// Flag-OFF pass (the live-demo default; run_demo.ps1 never sets NEXT_PUBLIC_DQ_PREVIEW_K7_K12): asserts the
// Tower, pointed at a server started WITHOUT the flag, is byte-for-byte the pre-K8 shape -- nav hides both new
// items, a direct hit on any of the three new routes shows the calm "not part of the demo" message and never
// the real screen or the red PreBackendBanner, the profile Edit button is visibly disabled (not hidden) and its
// drawer never opens, and the unit page's feedback control is fully absent, not disabled-and-visible.
async function mockK8K11PreviewOffSuite(browser) {
  const ctx = await browser.newContext({ viewport: VIEW });
  const page = await ctx.newPage();
  const errors = [];
  page.on("pageerror", (e) => errors.push(String(e)));
  page.on("console", (m) => m.type() === "error" && !/404/.test(m.text()) && errors.push(m.text()));

  await step("preview OFF: nav rail hides Evaluations and Annotation queue", async () => {
    await page.goto(`${BASE}/`);
    await signInRoster(page, "alice.maker");
    check("preview OFF: 'Evaluations' link not in the rail", (await rail(page).getByRole("link", { name: "Evaluations" }).count()) === 0);
    check("preview OFF: 'Annotation queue' link not in the rail", (await rail(page).getByRole("link", { name: "Annotation queue" }).count()) === 0);
    await shot(page, "preview_off_nav_rail");
  });

  for (const path of ["/evaluations", "/evaluations/diff", "/annotations"]) {
    await step(`preview OFF: ${path} self-guards (direct hit, no sign-in on the tab)`, async () => {
      const ctx2 = await browser.newContext({ viewport: VIEW });
      const page2 = await ctx2.newPage();
      await page2.goto(`${BASE}${path}`);
      await visible(page2.getByText(NOT_IN_DEMO_TEXT));
      check(`preview OFF: ${path} shows the calm not-in-demo message`, true);
      check(`preview OFF: ${path} never shows the PRE-BACKEND banner`, (await page2.getByText(PRE_BACKEND_TEXT).count()) === 0);
      check(`preview OFF: ${path} renders no real content beneath the message`, (await page2.locator("table").count()) === 0);
      await shot(page2, `preview_off${path.replace(/\//g, "_")}`);
      await ctx2.close();
    });
  }

  await step("preview OFF: profile Edit is visibly disabled, drawer never opens", async () => {
    await page.goto(`${BASE}/pipelines/P_ENCOUNTER/profile`);
    await signInRoster(page, "alice.maker");
    await visible(page.locator("#pp-contracts ~ div tbody tr"), 30000);
    const btn = page.getByRole("button", { name: "Edit" });
    check("preview OFF: Edit is visibly present but disabled (D10's original state)", await btn.isVisible());
    check("preview OFF: Edit is disabled", await btn.isDisabled());
    await btn.click({ force: true }).catch(() => {});
    check("preview OFF: the drawer never opens", (await page.getByRole("dialog", { name: "Propose a profile change" }).count()) === 0);
    await shot(page, "preview_off_profile_edit_disabled");
  });

  const U1_KEY = "ZZ:MEDICAID:a3f19c:chain=ENC0001842";
  await step("preview OFF: unit page has no feedback control at all", async () => {
    await page.goto(`${BASE}/units/${encodeURIComponent(U1_KEY)}`);
    await signInRoster(page, "alice.maker");
    await visible(page.locator("#expl-h"), 15000);
    check(
      "preview OFF: 'Feedback on this explanation' is fully absent, not disabled-and-visible",
      (await page.getByRole("button", { name: "Feedback on this explanation" }).count()) === 0,
    );
    await shot(page, "preview_off_unit_no_feedback");
  });

  check("no page or console errors (preview-OFF K8/K11 suite)", errors.length === 0, errors.slice(0, 3).join("; "));
  await ctx.close();
}

// Flag-ON pass: the original K8/K11 checks, run against a server started WITH NEXT_PUBLIC_DQ_PREVIEW_K7_K12=1,
// confirming the preview still works for its own private-review purpose.
async function mockK8K11PreviewOnSuite(browser) {
  const ctx = await browser.newContext({ viewport: VIEW });
  const page = await ctx.newPage();
  const errors = [];
  page.on("pageerror", (e) => errors.push(String(e)));
  page.on("console", (m) => m.type() === "error" && !/404/.test(m.text()) && errors.push(m.text()));
  // Mock sessions live in React memory only (lib/auth.tsx): a page.goto() after sign-in reloads the SPA and signs
  // the tab out (mockProfileSuite's own established pattern, above). This suite goes to the profile page FIRST
  // (a deep link, before any sign-in), signs in on that landed page, then uses client-side nav/link clicks only
  // for every screen after that -- never goto-ing away from a session already open.
  await page.goto(`${BASE}/pipelines/P_ENCOUNTER/profile`);
  await signInRoster(page, "alice.maker");

  let draftCrId = "";
  await step("K8: profile Edit opens a drawer, carries the PRE-BACKEND banner, proposes a CR", async () => {
    await visible(page.locator("#pp-contracts ~ div tbody tr"), 30000);
    check("K8: Edit is enabled (no longer D10's disabled affordance)", await page.getByRole("button", { name: "Edit" }).isEnabled());
    await page.getByRole("button", { name: "Edit" }).click();
    const dlg = page.getByRole("dialog", { name: "Propose a profile change" });
    await visible(dlg);
    check("K8: drawer shows the PRE-BACKEND banner", (await dlg.getByText(PRE_BACKEND_TEXT).count()) === 1);
    await dlg.locator("#ed-why").fill("Smoke test: realign domains for the encounter pipeline.");
    await dlg.getByRole("button", { name: "Propose change" }).click();
    await visible(dlg.locator("[role=status]", { hasText: "Proposed" }), 15000);
    const link = dlg.locator("[role=status] a[href^='/governance/']");
    draftCrId = (await link.innerText()).trim();
    check("K8: a CR is drafted with a cr_id", /^CR-/.test(draftCrId), draftCrId);
    check("K8: needs 2 approvals (PIPELINE is never lower-only)", (await dlg.locator("[role=status]").innerText()).includes("2"));
    await shot(page, "k8_profile_edit_drawer");
    await dlg.getByRole("button", { name: "Open change request" }).click();
    await until(async () => new URL(page.url()).pathname === `/governance/${draftCrId}`, 15000, "governance detail route");
    check("K8: submitting routes to the EXISTING /governance/[crId] review screen, not a new review UI", (await page.locator("h1").innerText()).includes(draftCrId));
    await shot(page, "k8_cr_in_governance");
  });

  await step("K11a: Evaluations history lists recorded runs via the REAL GET /evals, no PRE-BACKEND banner", async () => {
    await nav(page, "Evaluations");
    await visible(page.locator("#ev-h ~ div tbody tr"), 15000);
    const rowCount = await page.locator("#ev-h ~ div tbody tr").count();
    check("K11a: lists at least 4 recorded eval runs (3 real + 1 synthetic)", rowCount >= 4, `${rowCount} row(s)`);
    check("K11a: the synthetic row is named as synthetic, never implied real", (await page.getByText("run-agent_eval-0003-synthetic").count()) === 1);
    check("K11a: this real-data screen carries NO PRE-BACKEND banner (honesty: it is not invented)", (await page.getByText(PRE_BACKEND_TEXT).count()) === 0);
    await shot(page, "k11a_evaluations_history");
    // Pick the real run-agent_eval-0002 and the synthetic run-agent_eval-0003-synthetic, then follow the
    // in-page "Compare" link (a client-side router.push, never a goto) onto the separate eval-diff screen.
    await page.locator("tr", { hasText: "run-agent_eval-0002" }).locator("input[type=checkbox]").check();
    await page.locator("tr", { hasText: "run-agent_eval-0003-synthetic" }).locator("input[type=checkbox]").check();
    await page.getByRole("button", { name: /^Compare run-agent_eval-0002 vs run-agent_eval-0003-synthetic$/ }).click();
  });

  await step("K11b: Evaluations diff is a SEPARATE screen from the real Traces run diff, states differences only", async () => {
    await visible(page.locator("#ed-fields"), 15000);
    check("K11b: drawer/page carries the PRE-BACKEND banner (an invented route)", (await page.getByText(PRE_BACKEND_TEXT).count()) === 1);
    const body = await page.locator(".pagebody").innerText();
    check("K11b: no causal wording in the diff", !CAUSAL.test(body), (CAUSAL.exec(body) ?? [""])[0]);
    check("K11b: at least one field differs between the real run and the synthetic one (passed/code)", (await page.locator("#ed-fields-table tbody tr").count()) >= 1);
    check("K11b: this is not the real nightly run-diff screen (no #d-rel release-results section here)", (await page.locator("#d-rel").count()) === 0);
    await shot(page, "k11b_eval_diff");
  });

  const U1_KEY = "ZZ:MEDICAID:a3f19c:chain=ENC0001842";
  await step("K11c: Annotation queue lists the real unit by its real key, and opens its real card", async () => {
    await nav(page, "Annotation queue");
    await visible(page.locator("#aq-h ~ div tbody tr"), 15000);
    check("K11c: annotation queue carries the PRE-BACKEND banner", (await page.getByText(PRE_BACKEND_TEXT).count()) === 1);
    const row = page.locator("#aq-h ~ div tbody tr", { hasText: U1_KEY });
    await visible(row);
    check("K11c: the unit's subject is its REAL unit_key, never a fabricated id", true);
    check("K11c: not yet recorded for this unit", (await row.locator(".chip", { hasText: "open" }).count()) === 1);
    await shot(page, "k11_annotation_queue");
    await row.getByRole("link", { name: "Open card" }).click(); // client-side Link to the real /units/[unitKey] card
    await visible(page.locator("#expl-h"), 15000);
    check("K11c: lands on the real decision-card screen for this unit", page.url().includes(encodeURIComponent(U1_KEY)) || page.url().includes(U1_KEY));
  });

  await step("K11c (bis): decision-card feedback control records feedback only, claims nothing else", async () => {
    await page.getByRole("button", { name: "Feedback on this explanation" }).click();
    check("K11c: the feedback form carries the PRE-BACKEND banner and its no-side-effect disclosure", (await page.getByText(PRE_BACKEND_TEXT).count()) === 1 && (await page.getByText("never creates an evaluation candidate").count()) === 1);
    await page.getByRole("radio", { name: "Helpful", exact: true }).click();
    await page.getByRole("button", { name: "Submit feedback" }).click();
    await hasText(page.locator("#expl-h ~ p[role=status]"), "Feedback recorded.");
    check("K11c: success message is EXACTLY 'Feedback recorded.' -- no eval-candidate or promotion claim", (await page.locator("#expl-h ~ p[role=status]").innerText()).trim() === "Feedback recorded.");
    await shot(page, "k11c_feedback_recorded");
    // Back to the queue (client-side nav) to confirm the flip is live-derived, not a static fixture.
    await nav(page, "Annotation queue");
    await visible(page.locator("#aq-h ~ div tbody tr"), 15000);
    const row = page.locator("#aq-h ~ div tbody tr", { hasText: U1_KEY });
    check("K11c: this unit now shows feedback recorded (live-derived, not a static fixture)", (await row.locator(".chip", { hasText: "feedback recorded" }).count()) === 1);
    await shot(page, "k11_annotation_queue_after_feedback");
  });

  check("no page or console errors (preview-ON K8/K11 suite)", errors.length === 0, errors.slice(0, 3).join("; "));
  await ctx.close();
}

// ---------------------------------------------------------------- main
const { chromium } = await loadPlaywright();
const launchOpts = process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH ? { executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH } : {};
const browser = await chromium.launch(launchOpts);
try {
  if (MODE === "mock") {
    await mockPartE(browser);
    await mockSim(browser);
    await mockScreens(browser);
    await mockObsSuite(browser);
    await mockProfileSuite(browser);
    await mockCatalogSuite(browser);
    await mockBacktestSuite(browser);
    await mockGraphSuite(browser);
    if (PREVIEW === "on") {
      await mockK8K11PreviewOnSuite(browser);
    } else {
      await mockK8K11PreviewOffSuite(browser);
    }
    check("small cells: no explanation shows a bare count of 1-10 next to rows/units/claims/members", smallOffenders.length === 0, smallOffenders.slice(0, 4).join("; "));
  } else {
    await live(browser);
  }
} finally {
  await browser.close();
}
const fails = results.filter(([, ok]) => !ok);
console.log(`\n${results.length - fails.length}/${results.length} checks passed (${MODE}); screenshots in ${SHOTS}`);
process.exit(fails.length ? 1 : 0);
