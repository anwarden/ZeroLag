# Platform: Vercel functions, external I/O, background work, regression gates

Verified against vercel.com/docs on 2026-10-09. Read settings and telemetry; never change project settings, regions, plans or paid features without explicit approval.

## Contents
1. Functions and Fluid compute
2. Regions
3. Cold starts
4. Observability and what each plan shows
5. External services
6. Background work and durability
7. CDN and shared caches
8. Regression gates

## 1. Functions and Fluid compute

- Fluid compute is the default for projects created since 2025-04-23: one instance serves concurrent requests. Module scope is shared between users: no per-request or per-user state at module level ([Fluid compute](https://vercel.com/docs/fluid-compute)).
- Active CPU is billed while code runs and pauses during I/O; provisioned memory keeps billing during database waits, so slow queries cost money as well as time ([pricing](https://vercel.com/docs/functions/usage-and-pricing)).
- Database pools under Fluid: `attachDatabasePool(pool)` from `@vercel/functions` releases idle clients before an instance suspends (data.md) ([functions package](https://vercel.com/docs/functions/functions-api-reference/vercel-functions-package)).
- A longer `maxDuration` never makes a response faster. Bigger memory or CPU is a paid change: propose it only with measured CPU-bound evidence.
- Memory: with concurrent requests sharing an instance, large payloads, buffering whole files or exports in memory and unbounded module-level caches raise memory and can crash or slow every request on that instance. Stream large responses and bound in-process caches.

## 2. Regions

- New projects run functions in `iad1` (Washington, D.C.) unless configured. Configure in project settings, `regions` in `vercel.json`, or per function. Place functions next to the database: a chatty request pays every round trip twice ([function regions](https://vercel.com/docs/functions/configuring-functions/region)).
- Check every project in a multi-app workspace: one app left in `iad1` while the database is in Europe pays a transatlantic round trip on every query. Quantify it with a single-query endpoint timed from the function (Server-Timing) rather than estimating.
- Region pairs with Neon: data.md section 4. Multi-region functions only help with replicated data.

## 3. Cold starts

- Separate the first request after a deployment or idle period from warm requests. Production deployments are pre-warmed; bytecode caching applies to production on Node 20+, so preview cold starts overstate production ([Fluid compute](https://vercel.com/docs/fluid-compute)).
- Common causes: heavy imports at module scope, large bundled dependencies, client or ORM initialization, database compute activation (Neon scale-to-zero, data.md).
- Do not add keep-warm crons or always-on database computes without approval: they trade latency for cost.

## 4. Observability and what each plan shows

| Source | Shows | Notes |
|---|---|---|
| Observability (all plans) | Function invocations, errors, durations | Without Observability Plus: no p75 latency, no per-path breakdown, short retention ([Observability Plus](https://vercel.com/docs/observability/observability-plus)) |
| Observability Plus | p75 latency per route, longer retention | Paid; never enable it yourself |
| Speed Insights | Real-user metrics | Free tier shows the Real Experience Score only; per-metric INP/LCP/CLS needs paid Speed Insights Plus ([limits](https://vercel.com/docs/speed-insights/limits-and-pricing)) |
| Tracing | Request traces (beta) | `@vercel/otel` spans from the app ([tracing](https://vercel.com/docs/tracing)) |
| CLI (read) | `vercel logs`, `vercel inspect`, `vercel httpstat`, `vercel curl --trace`, `vercel traces` | Linked project and authorization required; `vercel metrics` needs Observability Plus |

- The Vercel MCP server (`https://mcp.vercel.com`) exposes read tools such as runtime logs and deployment details with your account's access; use read operations only.
- The `vercel-optimize` skill needs Vercel CLI ≥ 53, a linked project and Observability Plus for route-level results, and asks before scanner-only mode: respect its gates.
- Debugging guide: [slow functions](https://vercel.com/docs/functions/debug-slow-functions).

## 5. External services

- Put timeouts on every outbound call; retry only idempotent requests, with a bound and jitter; skip optional calls behind a circuit breaker when the provider is slow.
- Run independent calls concurrently (with a concurrency limit); keep providers that the screen does not need off the critical path.
- Record provider latency separately (Server-Timing or spans) so a slow third party is not blamed on the database or React.

## 6. Background work and durability

- `after()` (Next.js; Vercel `waitUntil` underneath) runs after the response within the function's maximum duration, without retries ([after](https://nextjs.org/docs/app/api-reference/functions/after)).
- Emails, signatures, invoices, payroll or HR state changes need durable execution: an existing job queue or workflow system with retries and idempotency keys ([Vercel Workflow](https://vercel.com/docs/workflows)). Moving such work to fire-and-forget is a correctness regression, not an optimization.
- UI states stay honest: "Processing" until the job confirms completion; "Done" only after it did.

## 7. CDN and shared caches

- Never cache authenticated HTML or API responses in a shared cache without a tenant- and user-safe key; prefer `Cache-Control: private, no-store` for personal data.
- Vercel Runtime Cache (used by `'use cache: remote'`) is regional and billed: shared, non-personal data with a measured hit rate only.
- Check real response headers (`curl -sI`) instead of assuming defaults.

## 8. Regression gates

- Keep the journey spec (`.zerolag/journeys.json`) and re-run it before and after each change with identical options; compare medians and IQRs, never single runs.
- CI or preview gates: journey timings with ≥ 5 runs and a tolerance calibrated on measured variance; route bundle snapshots (`next analyze --output`); query-count assertions in integration tests for critical endpoints; Next.js ≥ 16.3 `instant()` navigation tests on test builds.
- Gate on meaningful regressions, not on noise: a flaky gate gets disabled and protects nothing.
- Before shipping: typecheck, tests, authorization and tenant-isolation checks, cache-freshness checks after mutations, mobile and keyboard checks for UI changes.
