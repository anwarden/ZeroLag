---
name: zerolag
description: Diagnose and reduce user-perceived latency and jank in interactive webapps, especially Next.js App Router, React, Vercel, Prisma and Neon/PostgreSQL. Trigger on slow clicks, tab switches, forms, mobile scrolling, large lists, hydration, network waterfalls, slow queries, cold starts, high INP, or requests to optimize full-stack speed. Reproduce and measure, investigate all relevant layers with version-aware official guidance, implement verified high-impact fixes, and present a one-screen performance report.
---

# ZeroLag

Run one **bounded, evidence-led pass**: **scope -> measure -> diagnose -> prioritize -> fix -> verify**. Target: `$ARGUMENTS`. If unspecified, select up to three reproducible slow user journeys in the current app. Make changes safely and keep the user informed with a one-screen report, not a giant dashboard.

Read [measurement protocol](references/measurement-protocol.md) before timing and the [performance playbook](references/performance-playbook.md) for applicable techniques. Consult [report schema](references/report-schema.md) before writing report JSON. The playbook is an investigation catalog: **consider all 14 categories, but never apply every trick automatically**.

## Preflight: understand versions, scope and access

1. Identify the target webapp in a monorepo; inspect `package.json`, lockfile, `next.config`, Vercel configuration, Prisma schema/adapter and environment structure **without printing secrets**. Record exact Next.js, React and Prisma major versions; identify Cache Components, React Compiler, Node/Edge runtime, tenant/auth model and Neon connection mode.
2. Inspect installed tools: Chrome DevTools MCP, `vercel-react-best-practices`, `neon-postgres`, `vercel-optimize`, optional Impeccable. If available, read the Vercel React best-practices **rule index** and only open rules relevant to evidence. Use `vercel-optimize` **only** under its own telemetry, scope, permission and cost gates. If observability is missing, label the gap and use approved local/preview traces. Never invent tool output.
3. Preserve existing work and branch. **Do not push, deploy, enable paid features, change production configuration, run migrations/create indexes, run destructive SQL or change database compute/region without explicit permission.** Use staging/test accounts. Do not publish customer/employee data, credentials, traces containing secrets, or privileged URLs.
4. Preserve role/tenant isolation, Server Action authentication, cache freshness, transaction correctness, keyboard/mobile use and accessibility. No cross-user cache leaks, unreliable fire-and-forget business writes or fabricated wins.

## 1. Measure what users actually wait for

1. For up to three journeys, define **click -> usable result** (rendered data, responsive search, or confirmed durable save). In a production-like build, capture repeated baseline timings, explicit sample counts, mobile/desktop viewport/CPU/network, cold/warm state, request counts and build SHA. Use user/route-segmented field INP when already available.
2. Trace the critical path: **input queue -> JS/React -> network/RSC -> Vercel/auth -> Prisma pool/SQL -> Neon/external I/O -> browser layout/paint**. Separate serialized and overlapping work; do not sum overlapping timings. Distinguish INP (next paint) from network/DB completion and click-to-usable time.
3. In private investigation notes, mark the playbook's **14 categories** as `checked`, `not applicable`, or `not observable`. Inspect browser responsiveness, React, tables/forms, routing, fetching, cache, Prisma, PostgreSQL, Neon, Vercel, delivery, heavy jobs and regression gates. Record only issues with a measured signal or a clearly **testable hypothesis**; do not pad the report with generic advice.

## 2. Publish the baseline brief **before code edits**

1. Record all distinct, in-scope findings (including lower-impact and deferred items). Include evidence or uncertainty, likely location, next action, impact, effort, confidence and risk.
2. Prioritize by **end-to-end user impact**, then evidence, confidence, effort and risk. Prefer reducing waterfalls, unnecessary work, database round trips and long tasks over speculative micro-optimizations. Verify hypotheses before implementing.
3. Create `PERFORMANCE_FINDINGS.json` according to [report schema](references/report-schema.md), set `phase: "triage"`, then generate both reports:

   ```bash
   python3 .claude/skills/zerolag/scripts/render_report.py \
     --input PERFORMANCE_FINDINGS.json \
     --html PERFORMANCE_REPORT.html \
     --markdown PERFORMANCE_REPORT.md
   ```

   When installed globally, use the actual path under `~/.claude/skills/zerolag/scripts/`. Python 3.10+ and standard library only. If no Python, give a properly labeled Markdown fallback.
4. **Show the local HTML path immediately**, the top measurable bottleneck, and the next step. The report is regenerated at checkpoints; it is not live monitoring.

## 3. Fix and verify incrementally

1. Implement at most **three** highest-impact evidence-supported changes in this pass, one at a time. Use [playbook](references/performance-playbook.md) and installed vendor skill rules **only for the exact versions/features in this app**. Avoid refactoring unrelated modules and unrelated apps in the monorepo.
2. Update each finding `identified -> in_progress -> implemented`. Regenerate the report after each meaningful fix. Test auth/tenant isolation, important business behavior, typecheck/build and relevant UI/integration tests.
3. Remeasure the **same user journey with matched conditions** and realistic data; separate cold/warm. Record before/after values, sample count and source. Mark `verified` only with a documented retest; `implemented` if unmeasured. Never claim a percent gain for noncomparable data or add overlapping improvements. Show regressions.
4. If the remaining work needs approval (schema, infra, paid observability), document the concrete proposed change and stop short of mutating production.

## 4. Deliver a one-screen decision brief

- Set `phase: "complete"` at the end of the bounded pass and regenerate `PERFORMANCE_REPORT.html` / `.md`; keep `PERFORMANCE_FINDINGS.json` as detailed evidence.
- The HTML overview must show **three counts**, **one next action**, **two micro-diagrams** (before/after interactions, impact-by-effort map), and **at most three priorities**. Put all other findings and technical details in native, accessible collapsed disclosures. Keep it offline and dependency-free. No KPI cards, filters, giant charts, artificial progress celebration or clutter.
- Tell the user only: **verified result or not measured; top remaining bottleneck; what to do next**. Do not equate one pass with universal app-wide speed.
- When editing the *report template* (not at every checkpoint), optionally apply Impeccable `distill -> quieter -> polish` if installed. Correctness and readability outrank design trends.
