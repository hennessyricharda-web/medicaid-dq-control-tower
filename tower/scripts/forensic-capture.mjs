// Captures a live Tower page into the {url, protocol, forms, passwordFields, interactive[], textSample,
// sources?} shape forensic-dom.mjs (Agentic SDLC engine v4.2.0) expects. Read-only: navigates and reads the
// DOM; never types credentials or clicks a submit/irreversible control (per the engine's own
// live-audit-policy.mjs invariants). Uses the Tower's own pinned Playwright + already-downloaded Chromium —
// no install.
//
// Usage: node scripts/forensic-capture.mjs <path> <out.json> [--click "button text"]
import { chromium } from "playwright";
import fs from "node:fs";
import path from "node:path";

const base = process.env.CAPTURE_BASE || "http://localhost:3000";
const route = process.argv[2];
const outPath = process.argv[3];
const clickIdx = process.argv.indexOf("--click");
const clickText = clickIdx !== -1 ? process.argv[clickIdx + 1] : null;
if (!route || !outPath) {
  console.error("usage: forensic-capture.mjs <path> <out.json> [--click \"text\"]");
  process.exit(2);
}

const launchOpts = process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH ? { executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH } : {};
const browser = await chromium.launch(launchOpts);
const page = await browser.newPage();
const target = base + route;
await page.goto(target, { waitUntil: "networkidle" });

if (clickText) {
  // Read-only within policy: a nav/disclosure click (e.g. "Decision queue" row, a details toggle), never a
  // submit/send/delete/confirm control — those are the engine's own no-irreversible-action refusal.
  await page.getByText(clickText, { exact: false }).first().click({ timeout: 5000 }).catch(() => {});
  await page.waitForLoadState("networkidle").catch(() => {});
  await page.waitForTimeout(1000);
}

const data = await page.evaluate(() => {
  const forms = [...document.querySelectorAll("form")].map((f) => ({
    action: f.getAttribute("action"),
    method: (f.getAttribute("method") || "get").toLowerCase(),
  }));
  const passwordFields = [...document.querySelectorAll('input[type="password"]')].map((p) => ({
    id: p.id || null,
    autocomplete: p.getAttribute("autocomplete") || null,
  }));
  const accessibleName = (el) => {
    const aria = el.getAttribute("aria-label");
    if (aria) return { name: aria, viaPlaceholderOnly: false };
    const labelledby = el.getAttribute("aria-labelledby");
    if (labelledby) {
      const t = labelledby.split(/\s+/).map((id) => document.getElementById(id)?.textContent?.trim()).filter(Boolean).join(" ");
      if (t) return { name: t, viaPlaceholderOnly: false };
    }
    if (el.id) {
      const lbl = document.querySelector(`label[for="${el.id}"]`);
      if (lbl && lbl.textContent?.trim()) return { name: lbl.textContent.trim(), viaPlaceholderOnly: false };
    }
    const text = el.textContent?.trim();
    if (text) return { name: text, viaPlaceholderOnly: false };
    const placeholder = el.getAttribute("placeholder");
    if (placeholder) return { name: placeholder, viaPlaceholderOnly: true };
    const title = el.getAttribute("title");
    if (title) return { name: title, viaPlaceholderOnly: false };
    return { name: null, viaPlaceholderOnly: false };
  };
  const interactive = [...document.querySelectorAll("button, a[href], input, select, textarea")]
    .filter((el) => el.offsetParent !== null || el === document.activeElement)
    .map((el) => {
      const { name, viaPlaceholderOnly } = accessibleName(el);
      return {
        tag: el.tagName.toLowerCase(),
        type: el.getAttribute("type") || null,
        name,
        hasName: !!name,
        viaPlaceholderOnly,
        id: el.id || undefined,
      };
    });
  return {
    forms,
    passwordFields,
    interactive,
    textSample: document.body.innerText.slice(0, 200000),
    title: document.title,
  };
});

const capture = {
  url: target,
  title: data.title,
  protocol: new URL(target).protocol,
  forms: data.forms,
  passwordFields: data.passwordFields,
  interactive: data.interactive,
  textSample: data.textSample,
};

fs.mkdirSync(path.dirname(outPath), { recursive: true });
fs.writeFileSync(outPath, JSON.stringify(capture, null, 2));
console.log(`captured ${target} -> ${outPath} (${data.interactive.length} interactive, ${data.forms.length} form(s), ${data.passwordFields.length} password field(s))`);

await browser.close();
