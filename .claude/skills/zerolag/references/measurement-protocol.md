# ZeroLag measurement protocol

## Decide which metric is relevant

| Metric | Answers | Don't confuse it with |
|---|---|---|
| Click-to-usable-result | How long until the requested view/action is *actually usable* | INP, TTFB, spinner appearance |
| INP | How promptly an interaction produces the next paint, including input/processing/presentation delay | Network request completion or database commit |
| TTFB / server duration | How long until bytes are returned / backend executes | React rendering, hydration, total journey latency |
| LCP | When main page content loads on initial navigation | Whether tabs, typing and forms feel fast |
| CLS | Visual instability as elements move unexpectedly | Application data fetch latency |
| SQL query time | Time inside PostgreSQL for a query | Network+pool+Prisma serialization+client time |

`INP <= 200ms` is considered good **at the field p75** for mobile/desktop separately; this is a Web Vital, not a promised click-to-usable result. Do not call a local five-sample median "p75 field INP".

## Baseline procedure

1. Choose up to 3 exact user journeys: initial render (if relevant), tab/list/detail open, and important mutation. Define "usable" per journey. Note errors, pending states and whether data is correct.
2. Pin app/commit/build, environment, user role, synthetic/test data shape, device viewport, CPU throttle, network conditions, navigation state, CDN cache state and authentication context.
3. Run several replicates (aim >= 5 before and >= 5 after for basic medians; more samples and field data needed for tail statistics); separate cold and warm starts. Don't invent precision or significance.
4. Record browser performance trace + network waterfall; use Server Timing/logs for function phases, Prisma/query telemetry and database EXPLAIN only when safe. Trace only aggregate values. Prefer RUM/Speed Insights for field distributions if already authorized/enabled.
5. If backend telemetry unavailable, state the blind spot, instrument local/preview or use correlation IDs in a privacy-safe way. Avoid invasive production logging or authentication bypasses.

## Critical-path decomposition (not additive without evidence)

```
User click
  |-- input queue / long tasks
  |-- event handler + React update
  |-- browser request / round trips
  |     |-- function startup + auth
  |     |-- Prisma query / pool wait / SQL
  |     `-- external I/O
  `-- RSC payload / state commit / layout + paint -> usable result
```

Parallel activities overlap: observed waterfall lengths and click-to-usable spans are better than summing subsystem durations. Distinguish *critical path* from unrelated background work.

## Before/after contract

- `comparable: true` requires matched environment/account/device/network/action definitions, baseline/current timestamps, both n values > 0, and a documented measurement source.
- Regressions are shown, never dropped from the report. If result is >0 but <1% change with small n, describe as `~ unchanged` rather than a breakthrough.
- After a code fix without a real retest, status is `implemented`, not `verified`; a purely functional verification can be `verified` if the statement discloses that speed impact was not isolated.
- Prefer p50 for repeatable laboratory user flows. Show p95/p99 only if appropriately sampled; statistical noise, warm-up and jitter are real. User-perceived speed also includes error handling, visual stability and genuine persistence.
- For real users, inspect segmented field p75 INP, LCP and CLS where the provider supports it. Never suggest setting up paid instrumentation without user permission.

Official sources: [INP](https://web.dev/articles/inp), [Chrome LoAF](https://developer.chrome.com/docs/web-platform/long-animation-frames), [web.dev optimize INP](https://web.dev/articles/optimize-inp), [Next.js fetching/streaming](https://nextjs.org/docs/app/getting-started/fetching-data), [Vercel slow functions](https://vercel.com/docs/functions/debug-slow-functions).

## Expanded verification for real-world performance

- Benchmark **the app and route under test**, not a dev-mode monorepo. Include at least one low-end/mobile CPU profile and, where relevant, intermittent network conditions and a large realistic data set.
- Repeatedly test navigation **during hydration**, tab switching after previous data loads, back/forward, rapid successive clicks, keyboard input during pending fetches, slow/failed requests, and duplicate form submission prevention.
- Separate latency to first visual feedback from latency to correct, usable content and latency to durable completion. Optimistic UI can improve the first but is not proof of the last.
- For high traffic or multi-user incidents, collect representative **concurrency and tail latency**; observe function capacity, DB pool queueing, lock waits, rate limits and retry storms. Do not infer load resilience from a single warm local request.
- Explicitly annotate cold vs warm database/function behavior; identify user-specific cached data and verify no cross-tenant/cross-role access after cache changes.
- For read-only diagnostics, never expose auth cookies or raw customer data through report files, screenshots, traces or external MCP tools. Browser automation of privileged sessions requires a suitable test account.
- Maintain regression budgets for critical user journeys in CI/preview when practical, calibrated to measured baseline variability; do not fail CI on a flaky single run.

Sources: [INP](https://web.dev/articles/inp), [Chrome DevTools MCP privacy](https://github.com/ChromeDevTools/chrome-devtools-mcp), [Next.js routing](https://nextjs.org/docs/app/guides/prefetching), [Vercel slow functions](https://vercel.com/docs/functions/debug-slow-functions).
