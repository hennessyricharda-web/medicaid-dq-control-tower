// Like forensic-capture.mjs, but signs in once (mock roster button click — no credential typed, no real
// auth) and then captures a LIST of routes in the same browser context, so authenticated screens render.
// Still read-only / no-irreversible-action, per the engine's own live-audit-policy.mjs.
//
// Usage: node scripts/forensic-capture-session.mjs <user> <outDir> <route1> [route2 ...]
import { chromium } from "playwright";
import fs from "node:fs";
import path from "node:path";

const base = process.env.CAPTURE_BASE || "http://localhost:3000";
const [, , user, outDir, ...routes] = process.argv;
if (!user || !outDir || !routes.length) {
  console.error('usage: forensic-capture-session.mjs <user> <outDir> <route1> [route2 ...]');
  process.exit(2);
}

const launchOpts = process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH ? { executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH } : {};
const browser = await chromium.launch(launchOpts);
const page = await browser.newPage();
await page.goto(base + "/", { waitUntil: "networkidle" });
await page.getByText(user, { exact: false }).first().click({ timeout: 5000 });
await page.waitForLoadState("networkidle").catch(() => {});
await page.waitForTimeout(1000);

const extract = () => page.evaluate(() => {
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
      return { tag: el.tagName.toLowerCase(), type: el.getAttribute("type") || null, name, hasName: !!name, viaPlaceholderOnly, id: el.id || undefined };
    });
  return { forms, passwordFields, interactive, textSample: document.body.innerText.slice(0, 200000), title: document.title };
});

fs.mkdirSync(outDir, { recursive: true });
for (const route of routes) {
  const target = base + route;
  // Tokens live in memory for the tab only (Tower's own SignIn.tsx note) — a full page.goto() reload would
  // sign the session out, so navigate via an in-app link click (client-side router) instead.
  const navigated = await page.evaluate((path) => {
    const a = [...document.querySelectorAll("a[href]")].find((el) => new URL(el.href).pathname === path);
    if (a) { a.click(); return true; }
    return false;
  }, route).catch(() => false);
  if (!navigated) await page.goto(target, { waitUntil: "networkidle" }).catch(() => {});
  await page.waitForLoadState("networkidle").catch(() => {});
  await page.waitForTimeout(600);
  const data = await extract();
  const capture = { url: target, title: data.title, protocol: new URL(target).protocol, forms: data.forms, passwordFields: data.passwordFields, interactive: data.interactive, textSample: data.textSample };
  const name = (route === "/" ? "home" : route.replace(/^\//, "").replace(/\//g, "_")) + ".json";
  fs.writeFileSync(path.join(outDir, name), JSON.stringify(capture, null, 2));
  console.log(`captured ${target} -> ${path.join(outDir, name)} (${data.interactive.length} interactive, ${data.forms.length} form(s), ${data.passwordFields.length} password field(s))`);
}

await browser.close();
