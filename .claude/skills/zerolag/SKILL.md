---
name: zerolag
description: Evidence-led latency audit and optimization for web apps built with React, Next.js (App Router, Server Actions), Vercel, Prisma and Neon/PostgreSQL, including monorepos and multi-app workspaces. Use when clicks, tab switches, navigation, search, forms, tables, mobile views, API routes, queries or cold starts feel slow, when INP, LCP or TTFB is poor, or when asked to audit, diagnose or optimize performance. Starts with a read-only audit and a one-screen report, then applies targeted fixes one at a time with before/after measurements.
compatibility: Python 3.9+ standard library for the scripts. Optional - Node.js 20+ with the project's own Playwright, Chrome DevTools MCP, Vercel and Neon read access. No network access or package installs required.
metadata:
  version: "2.0.0"
---

# ZeroLag

Make real user journeys faster without trading away correctness, security or accessibility. Every claim needs evidence, and every change needs a matched before/after measurement.

Request: $ARGUMENTS

## Contract

- **Host rules win.** Read the instruction files `discover.py` lists (AGENTS.md, CLAUDE.md, CONTRIBUTING.md). Follow their branch, worktree, lock, test and environment rules over anything here.
- **Read-only until the audit report exists.** Then change code only when the request includes fixing ("fix", "optimize", "make it faster"), one change at a time.
- **Never do these without explicit approval of the named operation:**
  - deploy, push, commit or open a PR (unless asked);
  - change production data or settings, run migrations or DDL, or create or drop indexes;
  - run data-modifying SQL or `EXPLAIN ANALYZE` on writes;
  - create database branches, change compute, region or plan, or enable paid telemetry.
- **Use only safe targets and protect secrets.** Work on fixtures, test accounts, local production builds or authorized staging. Never print or store secrets, cookies, connection strings, raw SQL parameters or personal data.
- **Install nothing.** Never add packages or run unpinned `npx <tool>`. Use the project's installed tools; `npx prisma` without a version now fetches an incompatible major.
- **Preserve behavior.** Keep authentication, authorization, tenant and user isolation, cache privacy, data consistency, accessibility and business behavior intact. Never put per-user data in a shared cache.
- **Report honestly.** Never invent numbers, add up overlapping gains, or call a change verified without a matched retest. Show regressions.

## Scale to the request

| Request | Do |
|---|---|
| One narrow question ("why is this query slow?") | Answer it with the relevant reference; skip the full workflow |
| `audit`, "diagnose", or unclear | Steps 1–5, then stop and recommend the next action |
| `fix`, "optimize", "make it faster" | Steps 1–7; the audit report still comes first; at most 3 changes per pass |
| `verify` | Re-measure existing findings (step 7) and refresh the report |

Scripts live in `${CLAUDE_SKILL_DIR}/scripts/`. If that variable appears unexpanded, use the folder that contains this SKILL.md. Write all artifacts to `.zerolag/` at the app root, or at the workspace root for cross-app audits. The renderer makes that folder ignore itself in Git.

## 1. Discover (read-only)

```bash
python3 "${CLAUDE_SKILL_DIR}/scripts/discover.py" .            # add --inspect-env only if host rules allow reading local .env files
```

- Choose the app(s) in scope. In a monorepo or multi-repo workspace, audit each app separately, then review the infrastructure they share: database and pool budget, shared packages, regions.
- Note:
  - exact versions: Next.js, React, Prisma major (6, 7 or the 8 RC) and Node;
  - flags: `cacheComponents`, `partialPrefetching` and `reactCompiler`;
  - the Prisma adapter and who owns the pool, and Neon pooled vs direct URLs;
  - the Vercel region, the runtime, existing observability and the auth model.
- List the available tools: Chrome DevTools MCP, the project's Playwright, Vercel MCP or CLI, Neon MCP (read-only), vendor skills. Record each missing one in `limitations` and take the fallback from [measurement](references/measurement.md).

## 2. Measure the baseline

- Pick at most three journeys that matter: frequent or business-critical. Define the start, the action and what "usable" means. A save counts as usable only once the server has confirmed it.
- Time a production build (`next build && next start` on a free port, or an authorized preview), never `next dev`.
- For each journey, run 1 warm-up plus at least 5 measured runs (7 preferred) under fixed conditions:
  ```bash
  node "${CLAUDE_SKILL_DIR}/scripts/journey_timer.mjs" --spec .zerolag/journeys.json --profile mobile --runs 7
  ```
  Keep the raw runs. Measure cold and warm separately, and mobile and desktop separately.
- Capture one trace per journey for diagnosis, using the Chrome DevTools MCP recipe in [measurement](references/measurement.md).
- No browser automation available: follow the fallback ladder and label the evidence honestly.

## 3. Diagnose

Follow the critical path: input → main thread/React → Next.js routing/RSC/Server Action → network → function/auth/external I/O → Prisma pool and SQL → Neon/PostgreSQL → response → render and paint. Open only the references that match the evidence:

- [frontend](references/frontend.md): interaction latency, long frames, React rendering, tabs, search, forms, tables, hydration, bundles, images, mobile.
- [nextjs](references/nextjs.md): version gates, streaming, both caching models, prefetching, Server Actions, proxy, diagnostics.
- [data](references/data.md): Prisma 6/7/8, query shapes, pools, Neon, PostgreSQL plans, indexes and locks, shared databases, read-only SQL.
- [platform](references/platform.md): Vercel Fluid compute, regions, cold starts, observability, external I/O, background work, regression gates.

For every finding:
- Record the root cause, not the symptom. A faster spinner is not a fix.
- Set the evidence kind: `measured` (recorded timing, trace or count), `inspected` (confirmed in code, config or plan; cost unknown) or `hypothesis`.
- Set coverage per layer: `checked`, `not_applicable`, `not_observable` or `skipped`.

## 4. Rank

Score impact, confidence, effort and risk with the rubrics in [report schema](references/report-schema.md); the renderer orders findings by expected return.

- Favor removing waterfalls, round trips, unnecessary work and long tasks over micro-optimizations.
- Flag anything that needs an owner's approval with `requires_approval` and the reason.

## 5. Report before any edit

Write `.zerolag/findings.json` following [report schema](references/report-schema.md), with `phase: "audit"` and `updated_at`. Then run:

```bash
python3 "${CLAUDE_SKILL_DIR}/scripts/render_report.py" .zerolag/findings.json
```

Tell the user, in five lines at most:
- the report path;
- the top bottleneck and its evidence;
- the recommended next action;
- the main limitation.

In audit mode, stop here.

## 6. Optimize, one change at a time

- **Before the first edit:** follow the host rules for branch, worktree or session. Check `git status` and leave other people's changes alone.
- **The change:**
  - Take the highest-ranked finding that needs no approval and set it to `in_progress`.
  - Make the smallest change that removes the root cause.
  - No unrelated refactors, no new dependencies, no global config changes.
- **Checks:** run the typecheck, lint and tests that cover the change, plus checks for authorization and tenant isolation, cache freshness after mutations, and keyboard and mobile behavior for UI changes.

## 7. Verify

- Re-run the same spec, profile, data, build mode and cache state. Store the result as `current_runs_ms` (or `after_runs_ms`) with `comparable: true` only if the conditions really match.
- Status:
  - `verified` only with a documented retest in `verification`;
  - `implemented` when the change is not yet measured;
  - report "within noise" and regressions as they are.
- Re-render after each change with `phase: "optimize"`, then `phase: "complete"` when the pass ends.

## Final message

Verified result (or "not measured"), top remaining bottleneck, recommended next action, main limitations, and the report path. Report per journey, never as a total.
