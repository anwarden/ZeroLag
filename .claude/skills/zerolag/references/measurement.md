# Measurement

Verified against official docs on 2026-10-09. Re-check version-gated items when upgrading.

## Contents
1. Choose the right metric
2. Baseline protocol
3. Journey timer (`scripts/journey_timer.mjs`)
4. Tool ladder and fallbacks
5. Chrome DevTools MCP: safe setup and trace recipe
6. Server and database timing
7. Statistics rules
8. Privacy

## 1. Choose the right metric

| Metric | Answers | Do not confuse with |
|---|---|---|
| Input → usable (journey) | How long until the requested view is usable: data visible and interactive, results updated, or save confirmed by the server | INP, TTFB, spinner appearance |
| Interaction latency (lab) | One interaction's input delay + processing + presentation delay until the next paint | INP; network or DB completion |
| INP (field) | Near-worst interaction latency of a visit (one highest ignored per 50 interactions); clicks, taps and key presses only | A lab median; hover or scroll |
| TTFB / server duration | Time until the response starts / function execution | Rendering, hydration, total journey |
| LCP / CLS | Initial-load content paint / visual stability | Tab, form or table responsiveness |
| SQL execution time | Server-side execution (`pg_stat_statements`, EXPLAIN ANALYZE) | Pool wait + network + ORM time (client-observed) |

Field thresholds (p75, mobile and desktop separately): INP good ≤ 200 ms, poor > 500 ms; LCP ≤ 2.5 s / > 4 s; CLS ≤ 0.1 / > 0.25. A local median is never "p75 INP". Journeys have no universal threshold: set a target per journey from the baseline and the business need.

Sources: [INP](https://web.dev/articles/inp), [thresholds](https://web.dev/articles/defining-core-web-vitals-thresholds), [optimize INP](https://web.dev/articles/optimize-inp).

## 2. Baseline protocol

1. Pick at most three journeys per app: frequent or business-critical (tab switch, list → detail, search, form save). Define start state, one action and the "usable" condition. A save is usable only when the server confirmed it.
2. Measure a production build: `next build && next start` on a free local port, or an authorized preview. Never time `next dev` (dev compiles on demand). Check the app is already serving through `next start` before reusing a server; never kill processes you did not start.
3. Fix and record the conditions: build/commit, profile (mobile 390×844 touch + CPU slowdown, or desktop), network, cache state (warm or cold), server state (warm or first request after idle), account/role (test account), data volume (fixture or authorized copy).
4. One warm-up run, then at least 5 measured runs (7 preferred). Store raw values (`runs_ms`), not just a median.
5. Capture one trace per journey for diagnosis (section 5). Timing and tracing are separate: tracing adds overhead.
6. Separate first-visit/cold behaviour from warm behaviour; never mix them in one sample.
7. Attribute the time before ranking a cause. A plausible mechanism found in code is not yet the bottleneck:
   - **Ablate**: rerun the journey with the suspected factor removed (small vs realistic data in an isolated fixture copy; requests blocked with Playwright `page.route`). Equal timings rule the factor out.
   - **Busy or waiting**: take a CPU profile of the journey (CDP `Profiler`). A mostly idle main thread means the time goes to network, server or a timer, not JavaScript.
   - **Timeline**: compare when the data finished arriving with when the content appeared. Content about 300 ms after a skeleton, with the data long complete, is React's Suspense reveal throttle ([frontend](frontend.md) §3); fast local servers make it dominate.

CPU slowdown is relative to the host machine. Chrome DevTools (134+) can calibrate "mid-tier" and "low-tier mobile" presets; DevTools cannot truly simulate mobile CPUs, so confirm critical results on a real mid-tier Android when possible ([source](https://developer.chrome.com/blog/devtools-grounded-real-world)).

## 3. Journey timer

Uses the project's own Playwright (`playwright` or `@playwright/test`); it never installs anything. Without Playwright, use the tool ladder.

```bash
node "${CLAUDE_SKILL_DIR}/scripts/journey_timer.mjs" --spec .zerolag/journeys.json --profile mobile --runs 7
```

Spec (CSS selectors for `ready`; any Playwright selector for actions):

```json
{
  "base_url": "http://localhost:3000",
  "storage_state": ".zerolag/auth.json",
  "journeys": [
    {
      "id": "tabs",
      "name": "Switch to the payroll tab",
      "start": "/dashboard",
      "start_ready": "[data-testid=dashboard]",
      "action": {"click": "role=tab[name=\"Payroll\"]"},
      "ready": {"selector": "[data-testid=payroll] tbody tr", "gone": "[aria-busy=true]"}
    }
  ]
}
```

- Actions: `click`, `tap`, `fill` (+ `value`), `press` (+ `on`), `goto` (hard navigation). Mobile profile taps.
- `ready`: `selector` (visible), `text` (inside selector or body), `gone` (no visible match), `url` (substring). It must become true only after the action; the timer refuses a condition already true before acting.
- Options: `--runs` (≥ 3), `--warmup`, `--profile mobile|desktop`, `--cpu`, `--network none|slow-4g`, `--cache warm|cold`, `--storage-state`, `--cdp-url` (attach to a running Chrome with a test session), `--channel chrome`, `--playwright-from <app>`, `--allow-remote` (non-local URLs are refused by default).
- Output: `.zerolag/runs/<id>-<profile>-<cache>.json` with `runs_ms`, median/IQR, interaction latency and subparts (Event Timing, 8 ms granularity), long-animation-frame blocking time (Chromium), request counts (RSC requests, Server Actions, API calls) and errors. Copy `runs_ms` into `baseline_runs_ms` / `current_runs_ms` in findings.json.
- Timing: from the input event's timestamp (or navigation start for `goto`) to the first animation frame where `ready` holds. Works across App Router soft navigations and hard navigations on the same origin.
- Exit codes: 0 ok, 1 runtime problem (Playwright or browser missing), 2 invalid spec or arguments, 3 more than 20% of runs failed or none succeeded.
- A failed run says which ready clause did not hold and how many elements matched; two identical failures in a row stop the journey.
- Mutation journeys repeat the mutation on every run: use fixture data only.

## 4. Tool ladder and fallbacks

Use the first available option; record what was unavailable in `limitations` and lower the evidence kind accordingly.

| Need | Preferred | Fallback | Last resort |
|---|---|---|---|
| Journey timing | journey_timer + project Playwright | Chrome DevTools MCP: repeat the action n times and read `performance.now()` marks with `evaluate_script` | User records in DevTools and shares numbers; otherwise findings stay `inspected`/`hypothesis` |
| Interaction breakdown | DevTools MCP trace + `performance_analyze_insight` | DevTools Performance panel: Interactions track, "INP by phase", "Forced reflow" | `web-vitals/attribution` `onINP` in a local build |
| React rendering | React DevTools Profiler, React Performance Tracks (React ≥ 19.2, dev or profiling build: `next build --profile`) | Temporary `<Profiler>` in a disposable branch | Code inspection |
| Server time | Server-Timing headers or logs on a local production build | `curl -w` loop (section 6); on authorized Vercel previews: `vercel httpstat`, `vercel curl --trace` | Function logs |
| Database | Read-only SQL on a fixture or authorized database (data.md) | Prisma query events in a disposable branch (redacted) | `EXPLAIN (GENERIC_PLAN)` on a schema-only fixture; code inspection |
| Field data | Existing RUM (web-vitals beacons, Speed Insights) | CrUX (public, indexable pages only; SPA routes count as the landing page) | Label "no field data" |
| Vercel telemetry | Vercel MCP or CLI read commands on the linked project | Observability dashboard (plan-dependent) | Local production build only |

Never enable paid telemetry, create database branches or install tools to fill a gap without approval.

## 5. Chrome DevTools MCP: safe setup and trace recipe

Run it isolated and without sending data to Google: `--isolated --no-usage-statistics --no-performance-crux --redact-network-headers`. Pin the version instead of `@latest` where your rules forbid implicit downloads. Avoid `--autoConnect` to a personal browser profile. The server exposes everything in its browser to the agent ([configuration](https://github.com/ChromeDevTools/chrome-devtools-mcp/blob/main/docs/configuration.md)).

Interaction trace: `navigate_page` → `emulate` (`cpuThrottlingRate`, `networkConditions`, mobile `viewport`) → `performance_start_trace` (`reload: false`, `autoStop: false`) → `click` / `fill` / `press_key` → `performance_stop_trace` → `performance_analyze_insight` with an insight name listed in the trace summary. Use `list_network_requests` for waterfalls; `take_snapshot` (accessibility tree) beats screenshots for state checks ([tool reference](https://github.com/ChromeDevTools/chrome-devtools-mcp/blob/main/docs/tool-reference.md)).

## 6. Server and database timing

```bash
# 10 sequential requests to a local production build; prints connect, TTFB, total (seconds)
for i in $(seq 10); do curl -s -o /dev/null -w '%{time_connect} %{time_starttransfer} %{time_total}\n' "http://localhost:3000/api/health"; done
```

- Authenticated routes: use a test account cookie from a file you delete afterwards; never paste cookies into chat, logs or reports.
- Next.js `logging.fetches` / `logging.serverFunctions` are dev-server diagnostics, not timing evidence.
- Prisma query events (`log: [{ emit: 'event', level: 'query' }]`) expose raw parameter values: log only query shape, count and duration, in a disposable branch that is never committed.
- `pg_stat_statements` execution time excludes pool wait, network and ORM work; Prisma's `prisma:engine:connection` OpenTelemetry span shows pool wait ([Prisma tracing](https://www.prisma.io/docs/orm/v7/prisma-client/observability-and-logging/opentelemetry-tracing)).
- Concurrency: single-user timings hide pool exhaustion, lock waits and CPU saturation. When the complaint is "slow when everyone uses it", replay the endpoint or journey with a load tool the project already has, against a local production build or authorized staging only, ramping concurrency in steps. Watch p95 latency, error rate, pool wait and lock waits (data.md queries 4–6) at each step. Never load-test production or shared databases without explicit approval.

## 7. Statistics rules

- Report the median and the interquartile range (IQR) of raw runs. Use p95/p99 only with ≥ 20 runs per side or field data.
- A before/after comparison needs the same journey definition, build mode, profile, network, cache state, account and data shape (`comparable: true`), and ≥ 3 runs per side (≥ 5 recommended).
- The renderer reports "within noise" when the before and after IQRs overlap and "≈ unchanged" under 1%; never claim those as gains.
- Show regressions. Never add gains from overlapping findings; report journeys, not sums.
- After a fix without a matched retest, the status is `implemented`, not `verified`. A functional verification can be `verified` if the statement says the latency effect was not isolated.

## 8. Privacy

- Test accounts and fixture data only; production access needs explicit approval for the named operation.
- `.zerolag/` holds journeys, runs, findings and reports; it ignores itself in Git. Storage-state files contain session cookies: keep them there, delete them after the audit.
- Reports carry aggregate timings and code locations only: no customer or employee data, credentials, connection strings, raw SQL parameters, internal URLs or screenshots of personal data.
