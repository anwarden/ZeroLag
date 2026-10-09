# Changelog

All notable changes to ZeroLag. Versions follow semantic versioning; the version lives in `SKILL.md` (`metadata.version`), `.claude-plugin/plugin.json`, both scripts and this file, and a test keeps them equal.

## 2.1.0 — 2026-10-09

- README reduced to install and use; everything else moved to `docs/guide.md`.
- CI: the SQL job preloads `pg_stat_statements` and runs every documented query; the extension check compares the value exactly.
- Journey timer: a failed or premature ready condition now names each clause and its matches ("present but hidden" for an element masked at that viewport); a journey stops after two identical failures instead of timing out every run, and a journey with no successful run exits 3.
- Diagnose: attribute the time before blaming a cause (ablation, busy vs waiting, data arrival vs display); documents React's ~300 ms Suspense reveal throttle, which dominates local timings of routes with `loading.tsx`. Found while auditing a real admin app: two plausible causes were ruled out by measurement.

## 2.0.0 — 2026-10-09

### Workflow
- Seven explicit steps: Discover → Measure → Diagnose → Rank → Report → Optimize → Verify. The audit is read-only and the report exists before any edit.
- Modes scaled to the request: narrow question, `audit` (stops after the report), `fix` (at most three changes, one at a time), `verify`.
- Host repository rules (AGENTS.md, CLAUDE.md, CONTRIBUTING.md) override the skill; approvals are required for production, schema, index, infrastructure, paid and branch-creating operations.
- Portable script paths through `${CLAUDE_SKILL_DIR}`, with a fallback when the variable is not expanded.

### Diagnostics
- `scripts/discover.py`: maps single apps, workspaces and multi-repo folders; exact versions (installed, lockfile or declared); Next.js flags; router and Server Action counts; Prisma schemas, generators and adapters; Vercel settings; agent instruction files; shared packages; optional `--inspect-env` that classifies database URLs (provider, pooled, region, pool parameters) and groups apps sharing a database without printing secrets.
- `scripts/journey_timer.mjs`: repeatable input → usable timing with the project's own Playwright; soft and hard navigations; interaction latency subparts, long-animation-frame blocking, RSC/Server Action/API request counts; mobile and desktop profiles, CPU and network throttling, warm or cold cache; refuses non-local URLs by default.
- References split by layer (measurement, frontend, Next.js, data, platform) and re-verified against official documentation: Next.js 16.4, React 19.3, React Compiler 1.0, web-vitals 6, Prisma 7.10 and the Prisma 8 release candidate, PostgreSQL 18, Neon, Vercel. Adds a read-only SQL catalog, connection-budget guidance for shared databases and Chrome DevTools MCP privacy flags.

### Report
- Schema version 2, backward compatible with version 1 files.
- Deterministic rendering: no clock reads; the snapshot time comes from `updated_at`.
- Raw run timings with median and IQR; honest verdicts ("within noise", "too few runs", "not comparable").
- Evidence kinds `measured`, `inspected`, `hypothesis`; ranking by expected return (impact × confidence × effort × risk).
- Workflow-aware next action; approval flags; multi-app tags; per-layer coverage.
- HTML: three headline metrics, one next action, two diagrams, top three bottlenecks; semantic table for the priority map; WCAG AA contrast in light and dark themes; Content-Security-Policy; no scripts.
- Markdown summary with collapsed details. Atomic writes, `--check` and `--strict` modes, explicit field-level errors.
- Artifacts default to a self-ignoring `.zerolag/` folder instead of the app root.

### Packaging and maintenance
- Claude Code plugin and marketplace manifests (`/plugin install zerolag --marketplace anwarden/ZeroLag`).
- `tools/package_skill.py`: reproducible zip, idempotent project, user or Codex installs that never overwrite local edits without `--force`.
- Tests for validation, determinism, golden examples, failure paths, discovery fixtures, the journey timer, packaging and browser rendering; GitHub Actions CI.

### Breaking changes
- Default files moved from `PERFORMANCE_FINDINGS.json` / `PERFORMANCE_REPORT.*` at the app root to `.zerolag/findings.json`, `.zerolag/report.html` and `.zerolag/report.md`. `--input`, `--html` and `--markdown` still accept the old paths.
- `performance-playbook.md` and `measurement-protocol.md` were replaced by the per-layer references.

## 1.0.0 — 2026-10-09

- Initial public release: orchestration skill, playbook, measurement protocol and offline report renderer.
