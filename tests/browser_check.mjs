#!/usr/bin/env node
// Renders a ZeroLag report in Chromium at desktop and mobile sizes, light and dark, and checks the
// one-screen contract: no console errors, no horizontal scroll, three metrics, next action and top
// bottlenecks above the fold on desktop, readable text sizes, keyboard-operable disclosures.
// Usage: ZEROLAG_PLAYWRIGHT_FROM=<folder with node_modules/playwright> node tests/browser_check.mjs <report.html> [--screenshots <dir>]

import { createRequire } from "node:module";
import path from "node:path";
import process from "node:process";
import { pathToFileURL } from "node:url";

const [file, flag, shotDir] = process.argv.slice(2);
if (!file || (flag && flag !== "--screenshots")) {
  console.error("usage: node tests/browser_check.mjs <report.html> [--screenshots <dir>]");
  process.exit(2);
}
const require = createRequire(path.join(path.resolve(process.env.ZEROLAG_PLAYWRIGHT_FROM ?? "."), "package.json"));
let chromium;
for (const name of ["playwright", "@playwright/test", "playwright-core"]) {
  try { ({ chromium } = require(name)); break; } catch { /* try next */ }
}
if (!chromium) {
  console.error("Playwright not found; set ZEROLAG_PLAYWRIGHT_FROM");
  process.exit(1);
}

const views = [
  { name: "desktop", viewport: { width: 1366, height: 900 }, scheme: "light", foldItems: [".metrics", ".next", ".top"] },
  { name: "desktop-dark", viewport: { width: 1366, height: 900 }, scheme: "dark", foldItems: [".metrics", ".next", ".top"] },
  // Phones: headline and next action fit; the top list must at least start on the first screen.
  { name: "mobile", viewport: { width: 390, height: 844 }, scheme: "light", foldItems: [".metrics", ".next"], startItems: [".top"], mobile: true },
  { name: "mobile-dark", viewport: { width: 390, height: 844 }, scheme: "dark", foldItems: [".metrics", ".next"], startItems: [".top"], mobile: true },
];
const browser = await chromium.launch();
const results = [];
let failed = false;
try {
  for (const view of views) {
    const context = await browser.newContext({ viewport: view.viewport, colorScheme: view.scheme, isMobile: Boolean(view.mobile),
      hasTouch: Boolean(view.mobile), deviceScaleFactor: 1 });
    const page = await context.newPage();
    const problems = [];
    page.on("console", (m) => { if (["error", "warning"].includes(m.type())) problems.push(`console ${m.type()}: ${m.text()}`); });
    page.on("pageerror", (e) => problems.push(`page error: ${e.message}`));
    page.on("requestfailed", (r) => problems.push(`request failed: ${r.url()}`));
    await page.goto(pathToFileURL(path.resolve(file)).href);
    if (shotDir && view.scheme === "light") {
      // Untouched page: no focus ring, no disclosure animation in the published preview.
      await page.screenshot({ path: path.join(shotDir, `preview-${view.name}.png`), fullPage: true });
    }
    const facts = await page.evaluate(([foldItems, startItems]) => {
      const fold = innerHeight;
      const bottom = (selector) => document.querySelector(selector)?.getBoundingClientRect().bottom ?? Infinity;
      const top = (selector) => document.querySelector(selector)?.getBoundingClientRect().top ?? Infinity;
      const texts = [...document.querySelectorAll("body *")].filter((el) => el.childElementCount === 0 && el.textContent.trim()
        && el.getClientRects().length && !el.closest(".sr-only") && getComputedStyle(el).visibility !== "hidden");
      return {
        overflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
        metrics: [...document.querySelectorAll(".metric")].filter((m) => m.getClientRects().length).length,
        aboveFold: Object.fromEntries([...foldItems.map((s) => [s, bottom(s) <= fold]),
          ...startItems.map((s) => [`${s} starts`, top(s) < fold - 48])]),
        minFont: Math.min(...texts.map((el) => parseFloat(getComputedStyle(el).fontSize))),
        background: getComputedStyle(document.body).backgroundColor,
        diagrams: document.querySelectorAll(".journeys, .matrix").length,
      };
    }, [view.foldItems, view.startItems ?? []]);
    // Keyboard: the first top bottleneck and the first section disclosure open with Enter.
    let opened = true;
    for (const selector of [".top details > summary", "details.more > summary"]) {
      if (!(await page.$(selector))) continue;
      await page.focus(selector);
      await page.keyboard.press("Enter");
      opened = opened && await page.evaluate((s) => document.querySelector(s).parentElement.open, selector);
    }
    const findingExpanded = opened;
    // Printing or saving as PDF must keep the evidence that collapsed disclosures hide on screen.
    await page.evaluate(() => document.querySelectorAll("details").forEach((d) => { d.open = false; }));
    await page.emulateMedia({ media: "print" });
    const printsEvidence = await page.evaluate(() => {
      const fact = document.querySelector("details .facts dd");
      return !fact || fact.getClientRects().length > 0;
    });
    await page.emulateMedia({ media: "screen" });
    const expandedOverflow = await page.evaluate(() => {
      document.querySelectorAll("details").forEach((d) => { d.open = true; });
      return document.documentElement.scrollWidth - document.documentElement.clientWidth;
    });
    const checks = {
      "no console or page errors": problems.length === 0,
      "no horizontal scroll": facts.overflow <= 0 && expandedOverflow <= 0,
      "three metrics": facts.metrics === 3,
      "two diagrams": facts.diagrams === 2,
      "key content above the fold": Object.values(facts.aboveFold).every(Boolean),
      "text at least 12px": facts.minFont >= 12,
      "keyboard opens disclosures": findingExpanded,
      "print shows collapsed evidence": printsEvidence,
      "theme applied": view.scheme === "dark" ? facts.background !== "rgb(255, 255, 255)" : facts.background === "rgb(255, 255, 255)",
    };
    const failures = Object.entries(checks).filter(([, ok]) => !ok).map(([name]) => name);
    if (failures.length) failed = true;
    results.push({ view: view.name, failures, problems, ...facts });
    await context.close();
  }
} finally {
  await browser.close();
}
console.log(JSON.stringify(results, null, 1));
process.exit(failed ? 1 : 0);
