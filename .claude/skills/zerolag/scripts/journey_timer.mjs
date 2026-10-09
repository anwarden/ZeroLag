#!/usr/bin/env node
// Repeatable "input -> usable" timing for real user journeys, using the target project's own Playwright.
//
// For every run: load the start page, wait until it settles, arm an in-page probe, perform one action
// (click, tap, fill, press or goto) and record the time until the first animation frame where the
// "ready" condition holds. Also records the interaction's Event Timing entry (INP-style breakdown),
// long-animation-frame blocking time and the requests the journey triggered (RSC, Server Actions).
// Works across soft (App Router) and hard navigations. Never installs anything, never prints cookies,
// and refuses non-local URLs unless --allow-remote is passed.

import { createRequire } from "node:module";
import { mkdirSync, readFileSync, renameSync, writeFileSync } from "node:fs";
import path from "node:path";
import process from "node:process";
import { pathToFileURL } from "node:url";

export const VERSION = "2.0.0";
const LOCAL_HOSTS = new Set(["localhost", "127.0.0.1", "::1", "[::1]", "0.0.0.0"]);
// Lighthouse "mobile slow 4G" values for DevTools-style throttling (RTT x 3.75, throughput x 0.9).
const NETWORK = {
  none: null,
  "slow-4g": { latency: 562.5, downloadThroughput: (1474.56 * 1024) / 8, uploadThroughput: (675 * 1024) / 8 },
};
const PROFILES = {
  desktop: { viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1, isMobile: false, hasTouch: false, cpu: 1 },
  mobile: { viewport: { width: 390, height: 844 }, deviceScaleFactor: 3, isMobile: true, hasTouch: true, cpu: 4 },
};
const STATE_KEY = "__zerolag_probe";

export class UsageError extends Error {}

const HELP = `Usage: node journey_timer.mjs --spec <journeys.json> [options]

  --spec <file>            journey spec (see references/measurement.md)
  --journey <id>           run one journey (default: all)
  --runs <n>               measured runs per journey (default 7, minimum 3)
  --warmup <n>             unmeasured warm-up runs (default 1)
  --profile <name>         mobile | desktop (default mobile: 390x844, touch, 4x CPU)
  --cpu <rate>             CPU slowdown override (1 = none)
  --network <preset>       none | slow-4g (default none)
  --cache <mode>           warm (reuse one context) | cold (fresh context per run)
  --out <file>             write results JSON (default .zerolag/runs/<journey>-<profile>.json)
  --playwright-from <dir>  folder whose node_modules has playwright or @playwright/test (default: cwd)
  --channel <name>         use an installed browser, e.g. chrome
  --cdp-url <url>          attach to an already running Chrome (warm cache only)
  --storage-state <file>   Playwright storage state of a TEST account (never commit it)
  --allow-remote           allow non-local base URLs (staging or preview only)
  --headed                 show the browser
  --timeout <ms>           per-run timeout (default 30000)`;

export function parseArgs(argv) {
  const options = {
    runs: 7, warmup: 1, profile: "mobile", network: "none", cache: "warm", timeout: 30000,
    allowRemote: false, headed: false,
  };
  const flags = new Set(["allow-remote", "headed", "help"]);
  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i];
    if (!arg.startsWith("--")) throw new UsageError(`unexpected argument ${arg}`);
    const key = arg.slice(2);
    if (flags.has(key)) {
      options[key === "allow-remote" ? "allowRemote" : key] = true;
      continue;
    }
    const value = argv[i + 1];
    if (value === undefined || value.startsWith("--")) throw new UsageError(`${arg} needs a value`);
    i += 1;
    const name = key.replace(/-([a-z])/g, (_, c) => c.toUpperCase());
    if (!["spec", "journey", "runs", "warmup", "profile", "cpu", "network", "cache", "out", "playwrightFrom",
      "channel", "cdpUrl", "storageState", "timeout"].includes(name)) throw new UsageError(`unknown option ${arg}`);
    options[name] = value;
  }
  if (options.help) return options;
  for (const key of ["runs", "warmup", "timeout"]) options[key] = Number(options[key]);
  if (!Number.isInteger(options.runs) || options.runs < 3) throw new UsageError("--runs must be an integer >= 3");
  if (!Number.isInteger(options.warmup) || options.warmup < 0) throw new UsageError("--warmup must be an integer >= 0");
  if (!Number.isFinite(options.timeout) || options.timeout < 1000) throw new UsageError("--timeout must be >= 1000 ms");
  if (!PROFILES[options.profile]) throw new UsageError("--profile must be mobile or desktop");
  if (!(options.network in NETWORK)) throw new UsageError(`--network must be one of ${Object.keys(NETWORK).join(", ")}`);
  if (!["warm", "cold"].includes(options.cache)) throw new UsageError("--cache must be warm or cold");
  if (options.cpu !== undefined) {
    options.cpu = Number(options.cpu);
    if (!Number.isFinite(options.cpu) || options.cpu < 1 || options.cpu > 20) throw new UsageError("--cpu must be between 1 and 20");
  }
  if (options.cdpUrl && options.cache === "cold") throw new UsageError("--cache cold cannot be used with --cdp-url");
  if (!options.spec) throw new UsageError("--spec is required");
  return options;
}

export function isLocalUrl(value) {
  let url;
  try { url = new URL(value); } catch { return false; }
  const host = url.hostname.toLowerCase();
  return LOCAL_HOSTS.has(host) || host.endsWith(".localhost") || host.endsWith(".local");
}

const ACTIONS = ["click", "tap", "fill", "press", "goto"];

export function validateSpec(spec, { allowRemote = false } = {}) {
  if (!spec || typeof spec !== "object" || Array.isArray(spec)) throw new UsageError("spec must be a JSON object");
  if (typeof spec.base_url !== "string") throw new UsageError("spec.base_url is required (e.g. http://localhost:3000)");
  let base;
  try { base = new URL(spec.base_url); } catch { throw new UsageError("spec.base_url is not a valid URL"); }
  if (!["http:", "https:"].includes(base.protocol)) throw new UsageError("spec.base_url must use http or https");
  if (!allowRemote && !isLocalUrl(spec.base_url)) {
    throw new UsageError(`refusing non-local base_url ${base.host}; use a local production build, or pass --allow-remote for a staging/preview URL you are allowed to load-test`);
  }
  if (!Array.isArray(spec.journeys) || spec.journeys.length === 0) throw new UsageError("spec.journeys must be a non-empty array");
  const ids = new Set();
  spec.journeys.forEach((journey, index) => {
    const where = `journeys[${index}]`;
    if (!journey || typeof journey !== "object") throw new UsageError(`${where} must be an object`);
    if (typeof journey.id !== "string" || !/^[\w.-]{1,64}$/.test(journey.id)) throw new UsageError(`${where}.id must match [A-Za-z0-9_.-]{1,64}`);
    if (ids.has(journey.id)) throw new UsageError(`${where}.id duplicates ${journey.id}`);
    ids.add(journey.id);
    if (typeof journey.start !== "string") throw new UsageError(`${where}.start is required (path or URL of the start page)`);
    const start = new URL(journey.start, base);
    if (start.origin !== base.origin) throw new UsageError(`${where}.start must stay on ${base.origin}`);
    const action = journey.action;
    const kinds = action && typeof action === "object" ? ACTIONS.filter((k) => k in action) : [];
    if (kinds.length !== 1) throw new UsageError(`${where}.action needs exactly one of ${ACTIONS.join(", ")}`);
    if (kinds[0] === "fill" && typeof action.value !== "string") throw new UsageError(`${where}.action.fill needs a string "value"`);
    if (kinds[0] === "press" && typeof action.on !== "string") throw new UsageError(`${where}.action.press needs an "on" selector`);
    if (kinds[0] === "goto" && new URL(action.goto, base).origin !== base.origin) throw new UsageError(`${where}.action.goto must stay on ${base.origin}`);
    const ready = journey.ready;
    if (!ready || typeof ready !== "object" || !["selector", "text", "gone", "url"].some((k) => typeof ready[k] === "string")) {
      throw new UsageError(`${where}.ready needs at least one of selector, text, gone, url (CSS selectors only)`);
    }
  });
  return spec;
}

export function quantile(sorted, q) {
  if (!sorted.length) return null;
  const position = (sorted.length - 1) * q;
  const lower = Math.floor(position);
  const upper = Math.ceil(position);
  return sorted[lower] + (sorted[upper] - sorted[lower]) * (position - lower);
}

export function summarize(values) {
  const data = values.filter((v) => Number.isFinite(v)).sort((a, b) => a - b);
  if (!data.length) return null;
  const round = (v) => Math.round(v * 10) / 10;
  return {
    n: data.length, median: round(quantile(data, 0.5)), p25: round(quantile(data, 0.25)), p75: round(quantile(data, 0.75)),
    min: round(data[0]), max: round(data[data.length - 1]),
  };
}

export function loadPlaywright(fromDir) {
  const require = createRequire(path.join(path.resolve(fromDir), "package.json"));
  for (const name of ["playwright", "@playwright/test", "playwright-core"]) {
    let mod;
    try { mod = require(name); } catch { continue; }
    if (!mod?.chromium) continue;
    let version = "unknown";
    try { version = require(`${name}/package.json`).version; } catch { /* exports may hide package.json */ }
    return { chromium: mod.chromium, name, version };
  }
  throw new Error(`Playwright was not found from ${path.resolve(fromDir)}. Use the project's installed Playwright `
    + "(--playwright-from <app folder>) or another measurement method; ZeroLag never installs packages.");
}

// Runs inside every document before page scripts (addInitScript). Keeps a tiny state in sessionStorage
// so a journey can be timed across a hard navigation; all times are absolute (timeOrigin + now).
function probe(key) {
  if (window.__zerolagProbe) return;
  const local = (window.__zerolagProbe = { events: [], loafs: [] });
  const load = () => { try { return JSON.parse(sessionStorage.getItem(key) || "null"); } catch { return null; } };
  let state = load();
  const save = () => { try { sessionStorage.setItem(key, JSON.stringify(state)); } catch { /* storage blocked */ } };
  const abs = (t) => performance.timeOrigin + t;
  const visible = (el) => !!el && el.getClientRects().length > 0 && getComputedStyle(el).visibility !== "hidden";
  const met = (ready) => {
    if (ready.url && !location.href.includes(ready.url)) return false;
    if (ready.gone && [...document.querySelectorAll(ready.gone)].some(visible)) return false;
    let scope = document.body;
    if (ready.selector) {
      scope = [...document.querySelectorAll(ready.selector)].find(visible);
      if (!scope) return false;
    }
    return !ready.text || (scope?.textContent || "").includes(ready.text);
  };
  local.met = (ready) => { try { return met(ready); } catch { return false; } };
  // One clause per condition, so a failed or premature ready condition names the selector at fault.
  local.explain = (ready) => {
    const parts = [];
    try {
      if (ready.url) parts.push(location.href.includes(ready.url) ? "url ok" : `url "${ready.url}" not in ${location.pathname}`);
      if (ready.gone) {
        const left = [...document.querySelectorAll(ready.gone)].filter(visible).length;
        parts.push(left ? `gone "${ready.gone}": ${left} still visible` : "gone ok");
      }
      let scope = document.body;
      if (ready.selector) {
        const all = [...document.querySelectorAll(ready.selector)];
        const shown = all.filter(visible);
        parts.push(`selector "${ready.selector}": ${all.length} match(es), ${shown.length} visible`
          + (all.length && !shown.length ? " (present but hidden; check this profile's viewport)" : ""));
        scope = shown[0];
      }
      if (ready.text) parts.push(scope && (scope.textContent || "").includes(ready.text) ? "text ok" : `text "${ready.text}" not found`);
    } catch (error) { parts.push(`invalid selector: ${error.message}`); }
    return parts.join("; ");
  };
  const tick = () => {
    if (!state || !state.armed || state.t1 !== null) return;
    if (state.t0 !== null && local.met(state.ready)) {
      state.t1 = abs(performance.now());
      save();
      return;
    }
    requestAnimationFrame(tick);
  };
  local.arm = (ready, mode) => {
    state = { armed: true, mode, ready, t0: null, t1: null };
    save();
    if (mode === "input") requestAnimationFrame(tick);
  };
  local.read = () => load();
  const onInput = (event) => {
    if (state && state.armed && state.mode === "input" && state.t0 === null) {
      state.t0 = abs(event.timeStamp);
      save();
      requestAnimationFrame(tick);
    }
  };
  for (const type of ["pointerdown", "touchstart", "mousedown", "keydown", "input"]) addEventListener(type, onInput, { capture: true });
  if (state && state.armed && state.t1 === null) {
    if (state.mode === "navigation" && state.t0 === null) state.t0 = performance.timeOrigin;
    save();
    requestAnimationFrame(tick);
  }
  try {
    new PerformanceObserver((list) => {
      for (const e of list.getEntries()) {
        local.events.push({ start: abs(e.startTime), duration: e.duration, processingStart: abs(e.processingStart),
          processingEnd: abs(e.processingEnd), interactionId: e.interactionId || 0, name: e.name });
      }
    }).observe({ type: "event", durationThreshold: 16, buffered: true });
  } catch { /* Event Timing unsupported */ }
  try {
    new PerformanceObserver((list) => {
      for (const e of list.getEntries()) local.loafs.push({ start: abs(e.startTime), duration: e.duration, blocking: e.blockingDuration || 0 });
    }).observe({ type: "long-animation-frame", buffered: true });
  } catch { /* LoAF unsupported */ }
}

async function settle(page, inflight, timeout) {
  const deadline = Date.now() + Math.min(timeout, 10000);
  let quietSince = Date.now();
  while (Date.now() < deadline) {
    if (inflight.size > 0) quietSince = Date.now();
    if (Date.now() - quietSince >= 500) break;
    await page.waitForTimeout(50);
  }
  await page.evaluate(() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))));
}

async function act(page, action, base, hasTouch, timeout) {
  if ("goto" in action) return page.goto(new URL(action.goto, base).href, { waitUntil: "commit", timeout });
  if ("fill" in action) return page.locator(action.fill).first().fill(action.value, { timeout });
  if ("press" in action) return page.locator(action.on).first().press(action.press, { timeout });
  const target = page.locator(action.click ?? action.tap).first();
  return ("tap" in action || hasTouch) ? target.tap({ timeout }) : target.click({ timeout });
}

async function measureRun(page, journey, base, profile, timeout, traffic) {
  await page.goto(new URL(journey.start, base).href, { waitUntil: "load", timeout });
  const explain = (ready) => page.evaluate((r) => window.__zerolagProbe?.explain(r) ?? "probe not installed", ready)
    .catch(() => "page unavailable");
  if (journey.start_ready) {
    try {
      await page.locator(journey.start_ready).first().waitFor({ state: "visible", timeout });
    } catch {
      throw new Error(`start_ready not visible within ${timeout} ms: ${await explain({ selector: journey.start_ready })}`);
    }
  }
  await settle(page, traffic.inflight, timeout);
  const mode = "goto" in journey.action ? "navigation" : "input";
  const already = await page.evaluate(([ready, armMode]) => {
    const probeApi = window.__zerolagProbe;
    if (!probeApi) return "missing";
    if (armMode === "input" && probeApi.met(ready)) return "met";
    probeApi.arm(ready, armMode);
    return "armed";
  }, [journey.ready, mode]);
  if (already === "missing") throw new Error("probe not installed (is the page blocking scripts?)");
  if (already === "met") {
    throw new UsageError(`journey ${journey.id}: the ready condition is already true before the action `
      + `(${await explain(journey.ready)}); make it specific to the new state or add "gone"`);
  }
  traffic.reset();
  await act(page, journey.action, base, profile.hasTouch, timeout);
  try {
    await page.waitForFunction((key) => {
      try { return JSON.parse(sessionStorage.getItem(key) || "null")?.t1 != null; } catch { return false; }
    }, STATE_KEY, { timeout, polling: 50 });
  } catch {
    throw new Error(`ready condition not met within ${timeout} ms: ${await explain(journey.ready)}`);
  }
  const result = await page.evaluate((key) => {
    const state = JSON.parse(sessionStorage.getItem(key));
    const local = window.__zerolagProbe;
    sessionStorage.removeItem(key);
    return { state, events: local.events, loafs: local.loafs };
  }, STATE_KEY);
  const { t0, t1 } = result.state;
  const interaction = result.events.filter((e) => e.interactionId > 0 && e.start >= t0 - 2 && e.start <= t1)
    .sort((a, b) => b.duration - a.duration)[0];
  const loafs = result.loafs.filter((f) => f.start + f.duration >= t0 && f.start <= t1);
  const window_ = traffic.snapshot();
  return {
    ms: Math.round((t1 - t0) * 10) / 10,
    interaction_ms: interaction ? interaction.duration : null,
    input_delay_ms: interaction ? Math.round(interaction.processingStart - interaction.start) : null,
    processing_ms: interaction ? Math.round(interaction.processingEnd - interaction.processingStart) : null,
    presentation_ms: interaction ? Math.round(interaction.start + interaction.duration - interaction.processingEnd) : null,
    long_frames: loafs.length,
    blocking_ms: Math.round(loafs.reduce((sum, f) => sum + f.blocking, 0)),
    ...window_,
  };
}

function trafficRecorder(page) {
  const inflight = new Set();
  let requests = [];
  let errors = 0;
  page.on("request", (request) => {
    inflight.add(request);
    const headers = request.headers();
    requests.push({
      rsc: headers.rsc === "1" || /[?&]_rsc=/.test(request.url()),
      action: Boolean(headers["next-action"]),
      type: request.resourceType(),
      request,
    });
  });
  const done = (request) => inflight.delete(request);
  page.on("requestfinished", done);
  page.on("requestfailed", (request) => { done(request); errors += 1; });
  page.on("pageerror", () => { errors += 1; });
  page.on("console", (message) => { if (message.type() === "error") errors += 1; });
  return {
    inflight,
    reset() { requests = []; errors = 0; },
    snapshot() {
      return {
        requests: requests.length,
        rsc_requests: requests.filter((r) => r.rsc).length,
        server_actions: requests.filter((r) => r.action).length,
        api_requests: requests.filter((r) => ["fetch", "xhr"].includes(r.type) && !r.rsc && !r.action).length,
        errors,
      };
    },
  };
}

function readJson(file) {
  try { return JSON.parse(readFileSync(file, "utf8")); } catch (error) { throw new UsageError(`cannot read ${file}: ${error.message}`); }
}

function writeAtomic(file, data) {
  mkdirSync(path.dirname(file), { recursive: true });
  const temporary = `${file}.${process.pid}.tmp`;
  writeFileSync(temporary, `${JSON.stringify(data, null, 2)}\n`);
  renameSync(temporary, file);
}

const fmt = (ms) => (ms === null || ms === undefined ? "—" : ms >= 1000 ? `${(ms / 1000).toFixed(2)} s` : `${Math.round(ms)} ms`);

export async function main(argv = process.argv.slice(2)) {
  const options = parseArgs(argv);
  if (options.help) {
    console.log(HELP);
    return 0;
  }
  const spec = validateSpec(readJson(options.spec), { allowRemote: options.allowRemote });
  const journeys = options.journey ? spec.journeys.filter((j) => j.id === options.journey) : spec.journeys;
  if (!journeys.length) throw new UsageError(`no journey with id ${options.journey}`);
  const storageState = options.storageState ?? spec.storage_state;
  const profile = PROFILES[options.profile];
  const cpu = options.cpu ?? profile.cpu;
  const playwright = loadPlaywright(options.playwrightFrom ?? process.env.ZEROLAG_PLAYWRIGHT_FROM ?? process.cwd());
  const browser = options.cdpUrl
    ? await playwright.chromium.connectOverCDP(options.cdpUrl)
    : await playwright.chromium.launch({ headless: !options.headed, ...(options.channel ? { channel: options.channel } : {}) });
  let worst = 0;
  try {
    for (const journey of journeys) {
      const contextOptions = {
        viewport: profile.viewport, deviceScaleFactor: profile.deviceScaleFactor, isMobile: profile.isMobile,
        hasTouch: profile.hasTouch, ...(storageState ? { storageState } : {}),
      };
      const notes = [];
      const open = async () => {
        const context = options.cdpUrl ? browser.contexts()[0] ?? await browser.newContext() : await browser.newContext(contextOptions);
        await context.addInitScript(probe, STATE_KEY);
        const page = await context.newPage();
        const traffic = trafficRecorder(page);
        const cdp = await context.newCDPSession(page);
        if (cpu > 1) await cdp.send("Emulation.setCPUThrottlingRate", { rate: cpu });
        if (NETWORK[options.network]) {
          try {
            await cdp.send("Network.enable");
            await cdp.send("Network.emulateNetworkConditions", { offline: false, ...NETWORK[options.network] });
          } catch (error) {
            notes.push(`network throttling unavailable: ${error.message}`);
          }
        }
        return { context, page, traffic };
      };
      let session = await open();
      const runs = [];
      const failures = [];
      let previousError = null;
      for (let index = 0; index < options.warmup + options.runs; index += 1) {
        if (options.cache === "cold" && index > 0) {
          await session.context.close();
          session = await open();
        }
        try {
          const run = await measureRun(session.page, journey, spec.base_url, profile, options.timeout, session.traffic);
          if (index >= options.warmup) runs.push(run);
          previousError = null;
        } catch (error) {
          if (error instanceof UsageError) throw error;
          const message = error.message.split("\n")[0];
          if (index >= options.warmup) failures.push(message);
          // The same failure twice in a row before any success is a broken spec, not noise: stop instead of
          // waiting for every remaining run to time out.
          if (runs.length === 0 && message === previousError) {
            notes.push(`stopped after ${index + 1} identical failures; fix the spec for this profile`);
            break;
          }
          previousError = message;
        }
      }
      if (!options.cdpUrl) await session.context.close();
      else await session.page.close();
      const summary = {
        journey: { id: journey.id, name: journey.name ?? journey.id },
        conditions: {
          profile: options.profile, viewport: `${profile.viewport.width}x${profile.viewport.height}`, cpu_slowdown: cpu,
          network: options.network, cache: options.cache, browser: options.cdpUrl ? "existing Chrome over CDP" : `chromium ${browser.version()}`,
          playwright: `${playwright.name} ${playwright.version}`, origin: new URL(spec.base_url).host, authenticated: Boolean(storageState || options.cdpUrl),
        },
        runs_ms: runs.map((r) => r.ms),
        summary: summarize(runs.map((r) => r.ms)),
        interaction: summarize(runs.map((r) => r.interaction_ms).filter((v) => v !== null)),
        blocking: summarize(runs.map((r) => r.blocking_ms)),
        requests_median: summarize(runs.map((r) => r.requests))?.median ?? null,
        runs,
        failures,
        notes,
        tool: `zerolag journey_timer ${VERSION}`,
      };
      const out = options.out && journeys.length === 1 ? options.out : path.join(".zerolag", "runs", `${journey.id}-${options.profile}-${options.cache}.json`);
      writeAtomic(out, summary);
      const s = summary.summary;
      const last = runs.at(-1);
      console.log(`${journey.id} · ${options.profile} · ${options.cache} · n=${s?.n ?? 0}`
        + (s ? ` · median ${fmt(s.median)} (IQR ${fmt(s.p25)}–${fmt(s.p75)})` : "")
        + (summary.interaction ? ` · interaction p50 ${fmt(summary.interaction.median)}` : "")
        + (summary.blocking ? ` · blocking p50 ${fmt(summary.blocking.median)}` : "")
        + (last ? ` · requests ${last.requests} (RSC ${last.rsc_requests}, actions ${last.server_actions})` : "")
        + (failures.length ? ` · ${failures.length} failed run(s): ${failures[0]}` : "")
        + ` → ${out}`);
      for (const note of notes) console.log(`  note: ${note}`);
      if (runs.length === 0 || failures.length > Math.floor(options.runs * 0.2)) worst = Math.max(worst, 3);
    }
  } finally {
    await browser.close();
  }
  return worst;
}

if (process.argv[1] && import.meta.url === pathToFileURL(path.resolve(process.argv[1])).href) {
  main().then((code) => { process.exitCode = code; }, (error) => {
    console.error(`error: ${error.message}`);
    if (error instanceof UsageError) console.error("Run with --help for usage.");
    process.exitCode = error instanceof UsageError ? 2 : 1;
  });
}
