# ZeroLag: webapp performance playbook

**Purpose:** A comprehensive, evidence-led diagnostic catalog for interactive React/Next.js + Vercel + Prisma + Neon/PostgreSQL apps. **Audit all relevant categories; apply only techniques supported by measured symptoms and installed versions.** Nothing here overrides correctness, authorization, accessibility or cost approval. Keep this document out of the short user-facing report.

## Fast routing: identify the bottleneck before choosing a trick

| User complaint | Measure first | Highest-value candidates |
|---|---|---|
| Click has no immediate effect / typing freezes | INP subparts, Performance trace, Long Animation Frames, React Profiler | Main-thread tasks, re-renders, layout/paint, hydration |
| Switching tabs or routes takes seconds | Click-to-usable timing + Network/RSC waterfall | Serial awaits, blocked layouts, prefetch, server/DB calls |
| Form save is slow or stalls | Pending UI vs actual commit timing, action/server traces | Unnecessary refreshes, serial dependencies, duplicated writes |
| First request after idle is slow | Cold vs warm traces, Vercel init, Neon compute/pool metrics | Function boot, DB wake-up, connection establishment |
| Searching or large tables freeze | CPU profile, DOM nodes, SQL and transfer bytes | Too much DOM, slow filtering, excessive payloads, missing indexes |
| Initial mobile load is sluggish | Field p75 CWV, route bundle and main-thread trace | Hydration, JS weight, LCP image, third parties, fonts |
| Works alone but not under load | Concurrent load traces, DB pool/lock and function metrics | Saturated pool/CPU, locks, cascading retries, inefficient queries |

See [the measurement protocol](measurement-protocol.md). A fast spinner is not a finished user action. INP is not equivalent to click-to-usable-result. When an optimization target is unclear, pick the slowest **reproducible** journey, not the most fashionable trick.

## Coverage checklist (record in private investigation notes)

For EACH applicable category below, mark **checked**, **not applicable**, or **not observable**. A category marked not observable is a diagnostic limitation, not a clean bill of health. This table is NOT copied into the one-screen report.

| # | Category | Evidence that justifies investigation |
|---|---|---|
| 1 | Measurement and critical path | Timing decomposition, production vs dev, p50/p75/p95 |
| 2 | Main-thread/INP | Long tasks, LoAF, input and presentation delay |
| 3 | React render and state | Profiler commits, unnecessary subscriptions |
| 4 | Tables, search, forms and scroll | DOM count, input freezes, client data size |
| 5 | Next.js navigation, streaming and prefetch | RSC waterfall, blocked loading boundary |
| 6 | Fetching and network transfer | Serial calls, duplicate fetches, large payloads |
| 7 | Caching, freshness and privacy | Cache hits/misses, invalidation, user/tenant keys |
| 8 | Prisma query shapes | N+1, selects, joins, aggregates, pagination |
| 9 | PostgreSQL planning and transactions | EXPLAIN, buffers, locks, indexes, stats |
| 10 | Neon and connection lifecycle | Pool wait, cold compute, regions, reconnects |
| 11 | Vercel compute and external I/O | Function duration, CPU/memory, upstream timings |
| 12 | Bundles, assets, mobile delivery | JS/CSS bytes, hydration, images, fonts, vendors |
| 13 | Expensive operations and durability | CPU jobs, upload/OCR/exports, retry/idempotency |
| 14 | Regression gates and operations | Repeat tests, security, cost, real-user monitoring |

## 1. Measurement and critical-path decomposition

- Identify a precise user journey (click -> data visible and usable, typing -> updated results, save -> durable success). Measure it end-to-end on **production-like builds**, mobile and desktop separately; do not use `next dev` timings as production evidence.
- Record build/commit, account/role, sample counts, input data size, device/CPU, throttling/network, cache state, cold vs warm starts, and whether auth/session is already loaded.
- Correlate: input queue -> handler/React -> request -> Vercel startup/auth -> Prisma pool + SQL -> response/RSC -> paint. **Parallel spans overlap**; don't sum them as if sequential.
- Use field INP p75 (good is <=200 ms), LCP and CLS for user populations when data exists. For click-to-usable-result, choose an explicit journey-specific target; run repeated laboratory medians and show n. Do not invent p95/p99 or statistical confidence from tiny samples.
- Capture useful positive and negative controls (same journey at baseline, next build, repeat after browser refresh; slow start and warm path); isolate regressions and report missing telemetry. A score improvement without faster actual interactions is not success.

Sources: [INP](https://web.dev/articles/inp), [INP diagnostics](https://web.dev/articles/optimize-inp), [Chrome DevTools MCP](https://github.com/ChromeDevTools/chrome-devtools-mcp), [Vercel debugging](https://vercel.com/docs/functions/debug-slow-functions).

## 2. Browser main thread and immediate input responsiveness

- Use Chrome Performance/LoAF (when available) to separate **input delay**, event handler **processing duration**, and **presentation delay**. Inspect which script/layout/paint work blocks the next frame, not just total network time.
- Reduce synchronous parsing, sorting, filtering, JSON work, heavy date formatting, chart calculation and loops inside click/keyup handlers. Avoid repeated computation in every render. For genuinely CPU-heavy processing, test Web Workers; use transferable objects for large worker messages where suitable.
- Break unnecessary long tasks and yield when possible (`scheduler.yield()` only with feature detection/fallback); schedule truly non-urgent work without starving input. Do not yield inside correctness-critical atomic steps or pretend yielding reduces total CPU work.
- Remove forced synchronous layouts (e.g. alternating DOM writes/reads), shrink oversized DOMs, minimize style/layout invalidation and excessive SVG drawing. Prefer transform/opacity animations *when they lower measured frame cost*. Avoid triggering expensive CSS transitions for every row.
- Throttle high-frequency work such as scroll/resize/drag to animation frames where appropriate; use passive touch/wheel/scroll listeners **only when canceling default is unnecessary**. Remove duplicate global listeners and unnecessary observers after unmount. Honor reduced-motion preferences.
- Check hydration and startup CPU: a page can paint before JavaScript becomes responsive. Defer nonessential vendors and background tasks, but don't defer JavaScript necessary for the next critical interaction without measuring tradeoffs.

Sources: [Optimize INP](https://web.dev/articles/optimize-inp), [long tasks](https://web.dev/articles/optimize-long-tasks), [LoAF](https://developer.chrome.com/docs/web-platform/long-animation-frames), [third-party JS](https://web.dev/articles/efficiently-load-third-party-javascript).

## 3. React render, state and scheduling

- Profile the offending interaction using React Profiler; identify which components re-render, how often, and why. Localize state near consumers, split broad contexts and hooks that subscribe to unrelated state, use stable list keys, and avoid components declared inside render.
- Derive values during render instead of extra effect-driven `setState` chains. Use functional state setters and lazy initialization for expensive initial values. Hoist stable constants/default object props when useful. Avoid unnecessary subscriptions to values used only in callbacks.
- `useTransition` / `startTransition` for non-urgent screen updates; **do not put controlled text input state inside a transition**. `useDeferredValue` can keep typing fluid while a slow results list updates, but **does not debounce requests**. Combine with request debouncing/cancellation where network calls are the issue.
- Only apply `memo`, `useMemo` or `useCallback` when Profiler data or prop identity warrants them. Detect **React Compiler** availability and compilation first; it already memoizes many render paths. Do not rewrite all components or remove existing memoization wholesale.
- Optimize real algorithms before micro-tuning JavaScript: avoid repeated O(n^2) array scans, combine duplicate passes where measured, and use Map/Set for repeated lookups. Avoid premature low-level code changes that complicate maintainability.
- Preserve accessibility and functional correctness when deferring updates; pending content must not masquerade as fresh data or claim saved state before persistence.

Sources: [React Compiler](https://react.dev/learn/react-compiler/introduction), [useDeferredValue](https://react.dev/reference/react/useDeferredValue), [useTransition](https://react.dev/reference/react/useTransition), [Vercel React best practices](https://github.com/vercel-labs/agent-skills/tree/main/skills/react-best-practices).

## 4. Large tables, search, scrolling, forms and perceived progress

- If thousands of rows/widgets render, first consider **server-side pagination/filtering/sorting** to reduce SQL, bytes and JS, then virtualize the visible DOM rows when render cost remains high. Test dynamic row heights, sticky headers, selection, keyboard navigation, focus, screen readers and browser find-in-page.
- Debounce expensive **network requests**, not the text the user sees while typing; abort outdated browser requests with AbortController when possible and ignore stale results that arrive out of order. Avoid broad fetch-on-every-keystroke effects. Cache validated queries with permission-scoped keys if using existing SWR/TanStack Query.
- Keep input and button feedback visible on the next available paint; use pending and accessible status indicators. Use optimistic updates only for safe/reversible work with rollback and race handling. **Do not show 'saved' before the server confirms a durable write.** Prevent duplicate submissions and double charging / emailing.
- For high-frequency gestures (drag/drop, touch scrolling, hover effects), eliminate unrelated render work and expensive layout effects; prefer CSS primitives to per-frame JavaScript when verified faster. Avoid animations whose cost obscures performance gains.
- Keep important interaction affordances accessible on low-end phones; compare real-device experience or CPU-throttled emulation, not a powerful desktop alone.

Sources: [React deferred UI](https://react.dev/reference/react/useDeferredValue), [Next.js updating data](https://nextjs.org/docs/app/getting-started/updating-data), [INP presentation delay](https://web.dev/articles/optimize-inp), [Vercel rendering guidance](https://github.com/vercel-labs/agent-skills/tree/main/skills/react-best-practices).

## 5. Next.js App Router: navigation, RSC, prefetch and streaming

- Inspect installed **Next.js version** and `next.config` before using version-specific APIs. In Next.js 16, distinguish routes using **Cache Components** (`cacheComponents: true`) from the previous cache model; never copy a v14/v15 cache snippet into v16 blindly.
- Identify blocking work in `layout.tsx`, `page.tsx`, `loading.tsx`, Server Components, Route Handlers, Server Actions and middleware/proxy. An uncached `cookies()`, `headers()` or fetch in a layout can block the same-segment loading fallback; move slow work behind a suitable nested `<Suspense>` or into the page when allowed.
- Fetch independent resources concurrently; start promises early and await late **only after correct auth/tenant guards**. Preserve true sequential dependencies and error semantics. Streaming and granular Suspense boundaries expose useful content early but do not make slow SQL faster.
- Use `<Link>` navigation and **production** route prefetch; dynamic routes can prefetch a shell when a `loading.js` boundary exists. Avoid blanket prefetch of expensive, rarely used or sensitive routes; do not execute side effects during prefetch render.
- Reduce oversized React Server Components (RSC) payloads and prop serialization into Client Components. Keep large client-only dependencies out of global layouts and avoid `use client` boundaries high in the tree without need.
- Measure switching between tabs, back/forward navigation, route refresh, loading boundary visibility and data freshness after saves. Do not artificially mask a route by rendering stale tenant data.

Sources: [Next.js fetching/streaming](https://nextjs.org/docs/app/getting-started/fetching-data), [prefetching](https://nextjs.org/docs/app/guides/prefetching), [Next.js 16 upgrade](https://nextjs.org/docs/app/guides/upgrading/version-16), [production checklist](https://nextjs.org/docs/app/guides/production-checklist).

## 6. Fetching, network waterfalls and transfer costs

- Trace each interaction's waterfall (RSC, JSON, API, images, external APIs). Eliminate accidental client -> internal API -> same-server round trips **only when directly importing the server data function is safe**; consolidate repeated reads and parallelize independent calls without blowing up concurrency.
- Check duplicate requests from components, remounts, effects, subscriptions, retries, `router.refresh()` after mutations and broad tag/path invalidation. Prefer one authoritative fetch and targeted invalidation. Use `React.cache()` for request-local deduplication, **not cross-user global caching**.
- Limit serialized fields, relation depths, query result rows and response size. Use conditional HTTP/ETag caching and compression/CDN caching only for resources with correct authorization and freshness semantics; inspect actual response headers.
- Protect from stale results with cancellation/sequence checks. Add sensible timeouts, bounded retries with backoff/jitter for transient **idempotent** requests, and concurrency limits under load; exponential fanout or retry storms can make p95 worse.
- Avoid expensive preloads, DNS preconnects and early imports that compete for bandwidth/CPU with interaction-critical requests. Prefetch only demonstrably likely next actions.

Sources: [Vercel React best practices](https://github.com/vercel-labs/agent-skills/tree/main/skills/react-best-practices), [fetch priority and preconnect](https://web.dev/articles/preconnect-and-dns-prefetch), [Next.js prefetching](https://nextjs.org/docs/app/guides/prefetching).

## 7. Caching and invalidation: correctness before speed

- Determine cache ownership and freshness: public/shared, per-user, per-tenant, per-role, session-sensitive, frequently updated or immutable. Cache **only** if keys, authorization boundaries, TTL and invalidation are explicit and tested; never share privileged data across tenants via a module-level `Map` or a global cache.
- Distinguish browser cache, CDN, RSC Router Cache, per-request memoization, application/server cache and database connection reuse. Benchmark cold miss, warm hit and post-mutation behavior; check actual hit rates.
- Under Next.js 16 with Cache Components, consider `use cache`, `cacheLife`, `cacheTag` for eligible data. `updateTag()` in a Server Action supports immediate read-your-writes; `revalidateTag(tag, 'max')` is stale-while-revalidate for content where staleness is acceptable. Under the previous cache model follow **its separate official guide**. Avoid blanket `revalidatePath()` if narrower invalidation is safe.
- Ensure logout, access revocation, role/tenant switching, mutations and failed writes do not leave stale or unauthorized UI. Do not cache authenticated HTML or responses at a shared CDN without tenant-safe vary/key policies; don't cache errors with sensitive content.
- Validate cache busting and consistency before calling a latency reduction a win. Enabling Cache Components or changing global cache policy is a separate architectural decision, not a quick speed trick.

Sources: [Cache Components](https://nextjs.org/docs/app/api-reference/config/next-config-js/cacheComponents), [revalidating](https://nextjs.org/docs/app/getting-started/revalidating), [previous model](https://nextjs.org/docs/app/guides/caching-without-cache-components), [security](https://nextjs.org/docs/app/guides/data-security).

## 8. Prisma query shapes and data access

- Detect **Prisma major version** and driver/adapter configuration. Prisma v6 and v7 have meaningfully different setup conventions; do not introduce v7 code into a v6 monorepo blindly.
- Trace **counts and timings** of parameterized SQL (redact values): identify N+1 loops, redundant and serial reads, oversized `include` relations, wide `findMany`, unneeded count queries and fetch-everything-then-filter-in-JS patterns.
- Prefer purpose-built `select`, bounded `take`, nested reads, `in` batching, appropriate join/`relationLoadStrategy` where supported, and selective aggregates. Benchmark plans and payload sizes: a JOIN is not always faster if it explodes result cardinality.
- Use bulk writes (`createMany`, `updateMany`, etc.) where semantics and supported versions allow; batch thousands of records with bounded batch sizes and transaction length. Preserve validation, constraints, ordering, idempotency and permission checks.
- Use cursor pagination for deep sequential result sets; shallow offsets can be fine for small tables. Avoid doing per-row additional fetching or rebuilding huge data sets on every tab switch.
- Keep one shared Prisma Client per warm process/runtime where appropriate, not one per request. Do not assume Prisma v7 pools behave exactly like v6; confirm actual adapter and runtime.

Sources: [Prisma v7 optimization](https://www.prisma.io/docs/orm/v7/prisma-client/queries/advanced/query-optimization-performance), [Prisma v6 optimization](https://www.prisma.io/docs/orm/v6/prisma-client/queries/query-optimization-performance), [Prisma best practices](https://www.prisma.io/docs/orm/v7/more/best-practices).

## 9. PostgreSQL planning, indexes and transaction health

- On **safe staging/test SELECTs**, use `EXPLAIN (ANALYZE, BUFFERS)` to inspect actual vs planned row counts, join/sort methods, loops, seq/index scans, spills and buffer hits. `EXPLAIN ANALYZE` **executes the statement**; don't run on mutations or production-sensitive statements without explicit permission. Its timing does not include network transfer.
- Match high-frequency filters + joins + `ORDER BY/LIMIT` to appropriate existing indexes; consider targeted composite/partial/covering indexes only when workload and write/storage tradeoffs justify them. Index creation is a schema/production change requiring approval.
- Check statistics and autovacuum, growing tables, selective predicates, data skew, deep offsets, sort memory, lock waits, deadlocks, slow transactions and idle-in-transaction connections. Don't blindly increase memory or VACUUM tables in production.
- Keep transactions short (do not hold a DB transaction open across slow HTTP calls). Maintain uniqueness, isolation requirements and authorization/tenant filtering. Recheck query plans with representative data size; a toy staging table can give misleading improvements.
- Investigate slow p95/p99 due to row locks or pool queueing separately from average query duration.

Sources: [PostgreSQL EXPLAIN](https://www.postgresql.org/docs/current/using-explain.html), [index examination](https://www.postgresql.org/docs/current/indexes-examine.html), [Prisma performance](https://www.prisma.io/docs/orm/v7/prisma-client/queries/advanced/query-optimization-performance).

## 10. Neon connection lifecycle and data geography

- Verify when the app uses the pooled Neon URL (usually hostname contains `-pooler`) vs direct connection. Use direct connections for migrations/maintenance as required by the project's tooling, and check driver/Prisma version compatibility. **Never print connection strings or credentials.**
- Inspect pool wait, timeouts, per-request client construction, connection storms, transaction duration, idle-in-transaction and prepared statement/session-state assumptions. PgBouncer transaction pooling may not support features that rely on persistent session state; check compatibility before changing URLs.
- Compare first-after-idle and warm responses: Neon scale-to-zero activation and cold buffers can dominate an isolated slow first request. Changing suspend/compute size has cost implications; **no automatic keep-warm cron or paid setting changes**.
- Verify Vercel Function runtime region vs Neon compute region and repeated cross-region round trips. Consider legal/traffic geography and external services before proposing a region move. Do not call a database cold start a React problem.

Sources: [Neon pooling](https://neon.com/docs/connect/connection-pooling), [Neon compute lifecycle](https://neon.com/docs/introduction/compute-lifecycle), [Neon Prisma guide](https://neon.com/docs/guides/prisma), [Vercel regions](https://vercel.com/docs/functions/configuring-functions/region).

## 11. Vercel compute, server work and upstream services

- Check route-level duration, errors, logs, existing Observability and browser/server breakdown. Respect `vercel-optimize`'s account scope, Observability Plus requirements, plan access and approval gates; without it, use browser/preview evidence and state the gap.
- Separate cold function initialization from warm CPU work and DB wait. Inspect expensive module imports, ORM initialization, request body parsing, auth and external API calls, Fluid Compute concurrency and resource settings. More timeout does **not** mean faster responses.
- Consider memory/CPU/region configuration **only when measured and approved**; paid resource changes can raise cost. Confirm Node vs Edge runtime compatibility for Prisma and dependencies before suggesting a runtime switch. Avoid a region that is near the user but far from the database on a chatty SQL path.
- Keep remote APIs out of the critical path when not needed; parallelize genuinely independent requests, use timeouts and circuit breakers/fallbacks for optional features, and keep retries bounded. Never fire-and-forget important financial, HR or email operations in an unreliable runtime.
- For slow endpoints inspect serialization and response size as well as execution time; cache only safe/shared data.

Sources: [debugging slow functions](https://vercel.com/docs/functions/debug-slow-functions), [Fluid Compute](https://vercel.com/docs/fluid-compute), [region configuration](https://vercel.com/docs/functions/configuring-functions/region), [Observability](https://vercel.com/docs/observability).

## 12. Bundles, assets, hydration and mobile delivery

- Analyze **the slow route's** JS, CSS and RSC payload, not an unrelated workspace package. Reduce large `use client` boundaries, unnecessary editor/chart/icon dependencies, wildcard barrel imports, duplicated packages and non-critical script startup.
- Use `next/dynamic` / `React.lazy` for rarely used Client Components; avoid lazy-loading functionality needed immediately after the next likely click without prefetch/intent checks. Dynamically import expensive parsing/chart libraries only when needed. React Server Components are not a blanket replacement for interactive client UI.
- Serve appropriately sized responsive images via Next `<Image>` with `sizes`, lazy loading for below-fold assets, and considered LCP `preload`/`fetchPriority`; `priority` is deprecated in Next.js 16. Avoid over-preloading. Use `next/font` for self-hosted subsets where useful and prevent layout shifts.
- Audit third-party scripts, large polyfills, hydration-time work and repeated parsing. Prefer `async`/`defer` or post-hydration loading for nonessential scripts. Use modern compressed assets/CDN when configured; verify headers, compression and transferred bytes rather than assuming defaults.
- Optimize first useful paint (LCP), visual stability (CLS) and interaction responsiveness (INP) as separate goals. Avoid passing Lighthouse by hiding meaningful work until the user clicks, then making the interaction slower.
- Account for low-end CPUs, touch scroll jank, intermittent mobile networks and reduced-motion preferences.

Sources: [Next.js lazy loading](https://nextjs.org/docs/app/guides/lazy-loading), [Next.js Image](https://nextjs.org/docs/app/api-reference/components/image), [Next.js production checklist](https://nextjs.org/docs/app/guides/production-checklist), [third parties](https://web.dev/articles/efficiently-load-third-party-javascript).

## 13. Heavy workflows, uploads and reliable async processing

- For OCR, PDFs, spreadsheets, exports, image processing or large JSON parsing, determine whether CPU work belongs in a **Web Worker**, isolated server job or background queue. Do not block typing/scrolling or the response for work that is genuinely not necessary to return the current screen.
- For large uploads, consider safe browser-to-blob uploads with server-validated signed permissions, resumable/chunked transport if warranted, size/type limits and progress; inspect actual file-transfer time and latency on mobile. Keep authentication and data-retention guarantees.
- Move slow but nonessential tasks off the critical response path only after specifying durability, retry/idempotency and user feedback. Vercel `after()` is useful for some post-response work but **is not a durable job queue**. For important email, signature, invoicing or HR state changes require persistent job/workflow semantics and duplicate protection.
- Use concurrency limits, timeouts, backpressure and bounded retries to prevent expensive bursts from overwhelming Vercel/Neon/external vendors. Avoid retrying non-idempotent mutations without request idempotency keys or an equivalent guarantee.
- Make loading feedback honest: "Processing" for work in progress, "Completed" only after confirmed completion. Test cancellation and recovery behavior.

Sources: [Vercel Fluid Compute](https://vercel.com/docs/fluid-compute), [Next.js updating data](https://nextjs.org/docs/app/getting-started/updating-data), [web.dev long tasks](https://web.dev/articles/optimize-long-tasks).

## 14. Verification, performance budgets and team handoff

- Test each change against the original baseline: same build mode, device/CPU, network, dataset shape and user role; capture multiple runs, separate warm/cold and report both speedups and regressions. Include functional, permission, tenant isolation, keyboard/mobile and cache freshness checks.
- Segment real-user p75 CWV by mobile/desktop, route and cohort when available; monitor app-specific action latency (e.g., tab click to populated view, form submit to confirmed save) with explicit privacy-safe instrumentation. Use p95 only with enough samples; watch errors and slow tails under realistic concurrency.
- Define **project-specific performance budgets** and run stable CI/preview checks for measured critical flows, bundle increases and query counts. Do not enforce arbitrary numbers as universal guarantees; prioritize meaningful UX regressions and reduce lab flakiness.
- Roll out narrow changes gradually when possible. Track owner, evidence, affected files, before/after, count, regression checks, remaining risk and follow-up in the detailed JSON; keep the user-facing HTML one-screen.
- Report `measured`, `hypothesis`, `implemented but unmeasured` and `verified` honestly. Never add overlapping issue-level speedups to claim app-wide savings. Publish no sensitive traces, credentials, employee data or proprietary URLs.

Sources: [web.dev INP](https://web.dev/articles/inp), [Chrome DevTools](https://developer.chrome.com/docs/devtools/performance/), [Next.js production](https://nextjs.org/docs/app/guides/production-checklist), [Vercel Observability](https://vercel.com/docs/observability).

## Techniques to reject unless evidence demands them

**Do not:** memoize every component; enable cross-user caches by default; cache authentication/session data without explicit isolation; force every call into Promise.all; disable hydration or SSR globally to shrink a score; create indexes just because a query is slow; raise function timeout for latency; keep Neon awake at cost without permission; move everything to Edge without compatibility checks; use aggressive prefetch that competes with real user actions; move reliable writes into fire-and-forget tasks; trade data freshness and correctness for a fast-looking spinner.

**Source-of-truth strategy:** Read the installed [`vercel-react-best-practices`](https://github.com/vercel-labs/agent-skills/tree/main/skills/react-best-practices) rule index (70+ rules at the time of review) for detailed React/Next.js micro-techniques and examples **when available**, and use the vendor links above for their latest version-specific APIs. The current playbook is broad, but no universal catalog can guarantee every app-specific optimization or that every recommendation will help a given app.
