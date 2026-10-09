# Next.js App Router

Verified against nextjs.org on 2026-10-09 (latest stable 16.4). Read the installed version and `next.config` first; never paste code written for another major or caching model.

## Contents
1. Version gates
2. Rendering, streaming and blocking work
3. Caching: two models, one rule (privacy first)
4. Navigation and prefetching
5. Server Actions and mutations
6. Data access patterns
7. Proxy, runtime and build tooling
8. Diagnostics

## 1. Version gates

| Version | What changes for performance work |
|---|---|
| 15.x | Previous caching model only (`fetch` uncached by default, `unstable_cache`, segment config); `experimental.reactCompiler`; `middleware.ts` |
| 16.0 | `cacheComponents: true` (stable; replaces `experimental.ppr`/`dynamicIO`); `use cache`, `cacheLife`, `cacheTag`, `updateTag`, `refresh()`; `revalidateTag(tag, profile)` needs a profile; Turbopack default; top-level `reactCompiler`; `proxy.ts` replaces `middleware.ts`; layout deduplication and incremental prefetching; `next build` no longer prints bundle sizes; `next/image` `priority` deprecated for `preload` |
| 16.1–16.2 | `next experimental-analyze` (Turbopack bundle analysis); `logging.serverFunctions`; `next start --inspect` |
| 16.3 | `partialPrefetching: true` (requires Cache Components; set it explicitly together with `cacheComponents`); segment `export const prefetch`; `io()` from `next/cache`; prefetch inlining on by default |
| 16.4 | Cache Components recommended for every app (planned default in 17); `next analyze`; `prefetch()`/`navigation()` from `next/cache`; ships React 19.3 |

Sources: [Next.js 16](https://nextjs.org/blog/next-16), [16.4](https://nextjs.org/blog/next-16-4), [upgrade to 16](https://nextjs.org/docs/app/guides/upgrading/version-16).

Enabling Cache Components on an existing app is a migration with build-time errors to fix, not a quick win: propose it as its own finding with `requires_approval`.

## 2. Rendering, streaming and blocking work

- A layout that reads uncached or runtime data (`cookies()`, `headers()`, uncached fetch or DB call) does not fall back to its own segment's `loading.js`: it blocks navigation. Move the read into the page or wrap it in its own `<Suspense>`. With Cache Components this is a build-time "blocking route" error; locate it with `next build --debug-prerender` ([fetching data](https://nextjs.org/docs/app/getting-started/fetching-data), [blocking-route](https://nextjs.org/docs/messages/blocking-route)).
- Place `<Suspense>` boundaries around slow, independent parts so the shell and fast data stream first. Streaming shows content earlier; it does not make slow SQL faster.
- With Cache Components, `connection()` waits for a real request and also blocks prefetching; Next.js ≥ 16.3 recommends `io()` for time or randomness so subtrees stay prefetchable ([io](https://nextjs.org/docs/app/api-reference/functions/io)).
- Keep large serialized props out of Client Components: pass ids and the fields the client renders, not whole records.

## 3. Caching: two models, one rule

**Privacy first.** Every cache entry needs an explicit owner: public, tenant, role or user. Never put per-user or per-tenant data in a shared cache, a module-level `Map`, or a CDN response without a correct key and `Cache-Control: private`. Test logout, role change, tenant switch and failed writes ([data security](https://nextjs.org/docs/app/guides/data-security)).

**Cache Components (`cacheComponents: true`, 16.0+)** ([caching](https://nextjs.org/docs/app/getting-started/caching)):
- `'use cache'` keys on the build, the function and its serialized arguments and closures. It cannot read `cookies()`, `headers()` or `searchParams`, even through helpers. Default storage is in-memory per server instance: on Vercel, entries usually do not persist across requests or deployments ([use cache](https://nextjs.org/docs/app/api-reference/directives/use-cache)).
- `'use cache: private'` may read cookies and headers; results are cached only in the browser, never shared on the server ([use cache: private](https://nextjs.org/docs/app/api-reference/directives/use-cache-private)).
- `'use cache: remote'` stores entries in a cache handler (Vercel Runtime Cache: regional, billed); worth it only for shared data with a high hit rate ([use cache: remote](https://nextjs.org/docs/app/api-reference/directives/use-cache-remote)).
- Always set `cacheLife` explicitly. Presets (stale/revalidate/expire): `seconds` 30s/1s/1m, `minutes` 5m/1m/1h, `hours` 5m/1h/1d, `days` 5m/1d/1w, `weeks` 5m/1w/30d, `max` 5m/30d/1y ([cacheLife](https://nextjs.org/docs/app/api-reference/functions/cacheLife)).
- After a mutation in a Server Action: `updateTag(tag)` for read-your-writes; `refresh()` to re-render the current route without invalidating caches. `revalidateTag(tag, 'max')` is stale-while-revalidate and does not re-render in the same response; the one-argument form is deprecated. Any revalidation from a Server Action clears the whole client router cache ([updateTag](https://nextjs.org/docs/app/api-reference/functions/updateTag), [revalidateTag](https://nextjs.org/docs/app/api-reference/functions/revalidateTag)).

**Previous model (flag off)**: `fetch` is not cached unless `cache: 'force-cache'`; `unstable_cache` for non-fetch reads; segment `revalidate`/`dynamic`; `revalidatePath`/`revalidateTag(tag, 'max')` ([caching without Cache Components](https://nextjs.org/docs/app/guides/caching-without-cache-components)).

Per-request deduplication: React `cache()` dedupes within one request; it is not a cross-user cache.

## 4. Navigation and prefetching

- `<Link prefetch>`: default (`"auto"`) prefetches in production when the link is visible; without Partial Prefetching a dynamic route is prefetched only down to its nearest `loading.js`. `prefetch={true}` prefetches the full route: one server invocation per visible link — limit it to likely next clicks ([Link](https://nextjs.org/docs/app/api-reference/components/link), [prefetching](https://nextjs.org/docs/app/guides/prefetching)).
- 16.0: shared layouts are downloaded once, prefetches cancel when links leave the viewport and re-prefetch after invalidation.
- Partial Prefetching (16.3, Cache Components): one App Shell per route, including session-dependent parts, cached per session on the client. Turn it off for rarely visited heavy segments with `export const prefetch = 'force-disabled'`; 16.4 adds `prefetch()`/`navigation()` to defer subtrees ([optimizing prefetching](https://nextjs.org/docs/app/guides/optimizing-prefetching)).
- Client cache: dynamic pages are not reused by default (`experimental.staleTimes.dynamic` = 0); with Cache Components the client follows `cacheLife` `stale` (minimum 30 s).
- With Cache Components, up to 3 previously visited routes stay mounted but hidden (React `<Activity>`): state and DOM survive, Effects are cleaned up. Reset transient or user-scoped state on logout; E2E tests must use visibility-aware selectors ([preserving UI state](https://nextjs.org/docs/app/guides/preserving-ui-state)).
- Never run side effects during render: prefetches render pages.

## 5. Server Actions and mutations

- Server Actions are dispatched one at a time per client and are designed for mutations. Never use them to fetch data or to parallelize reads; `Promise.all` over actions still runs them in sequence ([Server Actions](https://nextjs.org/docs/app/guides/server-actions), [mutating data](https://nextjs.org/docs/app/getting-started/mutating-data)).
- Return the updated UI in the same response with `updateTag`, `revalidatePath` or `refresh()` instead of a client `router.refresh()` round trip.
- Every action re-checks authentication and authorization; actions are public HTTP endpoints. Default body limit 1 MB (`experimental.serverActions.bodySizeLimit`).
- `after()` runs work after the response within the function's maximum duration, without retries: not for business-critical writes, emails or payments (platform.md) ([after](https://nextjs.org/docs/app/api-reference/functions/after)).

## 6. Data access patterns

- Waterfalls: start independent reads after the auth check and await them together; keep real dependencies sequential. Check child components that await in turn ("nested waterfalls").
- Server Components call the data layer directly; a Server Component fetching its own Route Handler adds a round trip and serialization.
- Fetch on the server what the first render needs; client fetching after hydration adds a round trip and a loading state.
- Avoid duplicate reads: one authoritative read per request (`cache()`), select only rendered fields (data.md).

## 7. Proxy, runtime and build tooling

- `proxy.ts` (16.0+) replaces `middleware.ts` and runs on the Node.js runtime only. It runs on every matched request: keep it thin (no database queries) and narrow its matcher.
- The Edge runtime is deprecated for routes; Cache Components requires Node.js. Do not propose moving routes to Edge ([runtime](https://nextjs.org/docs/app/api-reference/file-conventions/route-segment-config/runtime)).
- Turbopack is the default bundler in 16 (`--webpack` opts out). React Compiler: top-level `reactCompiler: true` plus `babel-plugin-react-compiler`; it increases build time ([reactCompiler](https://nextjs.org/docs/app/api-reference/config/next-config-js/reactCompiler)).

## 8. Diagnostics

- `next build --debug-prerender` locates blocking-route errors; `next build --profile` produces a React profiling build; `next start --inspect` (16.2+) attaches a profiler to the production server.
- `instrumentation.ts` + `@vercel/otel` adds server spans; `instrumentation-client.ts` `onRouterTransitionStart` can time client navigations in the field ([OpenTelemetry](https://nextjs.org/docs/app/guides/open-telemetry), [instrumentation-client](https://nextjs.org/docs/app/api-reference/file-conventions/instrumentation-client)).
- The dev server's MCP endpoint (`/_next/mcp`, 16+) lists routes, errors and Server Actions: useful for discovery, never for timing ([MCP](https://nextjs.org/docs/app/guides/mcp)).
- `logging.fetches` and `logging.serverFunctions` print dev-server timings: diagnostics only.
- Next.js ≥ 16.3: `instant()` from `@next/playwright` guards instant navigations in tests ([instant navigation](https://nextjs.org/docs/app/guides/instant-navigation)).
