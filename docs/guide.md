# ZeroLag guide

Everything beyond the quick start in the [README](../README.md): how it works, what it checks, installation options, optional tools, outputs, maintenance and limits.

## The one-screen report

**[Open the offline demo](../examples/demo-report.html)** (synthetic data, not a real audit).

Readable in ten seconds:
- **Three numbers**: open high-impact bottlenecks, verified findings, and retested journeys that got faster. Colour is used only when it is earned.
- **One next action**, with the journey to retest or the approval it needs.
- **Top three bottlenecks**, ranked by expected return. Each is marked measured, seen in code, or hypothesis, carries a stable id (`PERF-02`) and the journey it slows, and opens in place to show its evidence.
- **Two small diagrams**:
  - before/after journey timings: medians of raw runs, with interquartile whiskers; "within noise" when the spreads overlap;
  - an impact × effort map of the open findings by rank.

Everything else (all findings, evidence, journey conditions, coverage and limitations) sits in collapsed sections. The HTML is static and offline: no scripts, no network, light and dark themes, phone-friendly. A Markdown summary is generated next to it.

## How it works

1. **Discover**: `discover.py` maps the repository. It handles single apps, workspaces and folders of separate Git repos, and records exact Next.js, React and Prisma versions, flags, adapters, regions, the tooling in place and the host repo's agent rules. Secrets are never printed.
2. **Measure**: `journey_timer.mjs` times up to three real journeys on a production build, with fixed conditions (mobile or desktop, CPU, network, cache) and at least five runs. It uses the project's own Playwright and installs nothing.
3. **Diagnose**: Claude follows the critical path (input → React → Next.js → Server Action or API → Vercel → Prisma → Neon/PostgreSQL → response → paint) using per-layer references verified against official documentation.
4. **Rank**: impact × confidence × effort × risk.
5. **Report**: `.zerolag/report.html` and `.zerolag/report.md` are written before any code edit. An audit stops here.
6. **Optimize**: when asked to fix, at most three changes, one at a time, following the host repository's rules.
7. **Verify**: the same journey under the same conditions. A fix is marked verified only after a matched retest.

**Safety model.** Nothing below happens without explicit approval:
- Production: deployments, pushes, writes to production data or settings.
- Database: migrations, index creation, data-modifying SQL, `EXPLAIN ANALYZE` on writes, database branches.
- Infrastructure: region, compute or plan changes, paid telemetry.

Other guarantees:
- Authorization, tenant isolation and cache privacy are checked with every change.
- Reports hold aggregate timings and code locations, never secrets or personal data.
- `.zerolag/` ignores itself in Git.

## What it checks

| Layer | Examples |
|---|---|
| Interaction & React | Interaction latency subparts, long animation frames, re-render cascades, transitions, React Compiler, `<Activity>` tabs, forms with honest pending states |
| Tables & delivery | Server pagination before virtualization (accessible), `content-visibility`, hydration cost, route bundles (`next analyze`), images, third parties |
| Next.js | Blocking layouts, Suspense placement, Cache Components vs the previous model, private vs shared caches, prefetch cost, Server Actions run one at a time, `proxy.ts` |
| Data | N+1 and over-fetching, pagination, transactions, Prisma 6/7/8 pool differences, Neon pooler limits, plans, indexes, locks, statistics resets, read-only SQL |
| Platform | Fluid compute, function vs database region, cold starts, observability limits per plan, durable background work, regression gates |
| Multi-app | Shared database connection budget, per-app roles, migration locks, Prisma version drift, region drift |

## Install

**Claude Code plugin** (recommended for teams; updates through the marketplace):

```text
/plugin install zerolag --marketplace anwarden/ZeroLag
```

On Claude Code older than 2.1.275: `/plugin marketplace add anwarden/ZeroLag`, then `/plugin install zerolag@zerolag`. Plugin skills are namespaced: invoke `/zerolag:zerolag`. Update with `claude plugin marketplace update zerolag`, then `claude plugin update zerolag@zerolag` and `/reload-plugins`.

**Copy into a project or your user folder** (no plugin system needed):

```bash
git clone https://github.com/anwarden/ZeroLag.git && cd ZeroLag
python3 tools/package_skill.py install --user                          # ~/.claude/skills/zerolag
python3 tools/package_skill.py install --project /path/to/app          # app/.claude/skills/zerolag
python3 tools/package_skill.py install --project /path/to/app --agent codex   # app/.agents/skills/zerolag
```

- Invoke `/zerolag` in Claude Code, or `$zerolag` in Codex.
- Re-running the command upgrades the install. Files you edited are never overwritten without `--force`, which first saves `zerolag.previous.zip`. `--dry-run` shows the plan.
- A project install can be committed so teammates get it with `git pull`. If your repository forbids committing agent tooling, use `--user` instead.
- `python3 tools/package_skill.py build` writes a reproducible `dist/zerolag-<version>.zip` (a `zerolag/` folder) for offline distribution.

**Requirements**:
- Python 3.9+ (standard library only; 3.11+ recommended, and macOS's system Python works).
- Optional:
  - Node.js 20+ with the app's own Playwright, for journey timing;
  - Chrome DevTools MCP;
  - read access to Vercel and Neon.

When a tool is missing, ZeroLag records the gap and uses a documented fallback. It never pretends.

### Optional companions

Install only what your security policy allows; pin versions where implicit downloads are not allowed.

| Tool | Adds | Setup |
|---|---|---|
| Chrome DevTools MCP | Traces, interaction breakdown, network waterfalls | `claude mcp add chrome-devtools --scope user -- npx chrome-devtools-mcp@1.10.1 --isolated --no-usage-statistics --no-performance-crux --redact-network-headers` |
| Neon MCP (read-only) | Query statistics and plans | `claude mcp add --transport http neon "https://mcp.neon.tech/mcp?readonly=true"`, then authenticate with `/mcp`. Neon recommends development and test databases only |
| Vercel MCP | Deployments, runtime logs | `claude mcp add --transport http vercel https://mcp.vercel.com`, then `/mcp` |
| Vercel React best practices | 70 code-level React/Next.js rules | `npx skills@1.7.1 add vercel-labs/agent-skills --skill vercel-react-best-practices --agent claude-code -y` (Node 22.20+) |

The DevTools flags above keep the browser profile isolated and stop usage statistics and trace URLs from being sent to Google. They also redact cookies from network results.

## Use

```text
/zerolag audit                                   read-only audit of the current app, report, stop
/zerolag audit apps/admin: tab switching on mobile
/zerolag fix                                     audit, then up to three verified fixes
/zerolag verify                                  re-measure and refresh the report
```

| Output (`.zerolag/` at the app or workspace root) | Purpose |
|---|---|
| `findings.json` | Source of truth ([schema](../.claude/skills/zerolag/references/report-schema.md)) |
| `report.html`, `report.md` | One-screen brief and short summary, regenerated at each checkpoint |
| `journeys.json`, `runs/` | Journey spec and raw timings, reusable as a regression check |

The report is a snapshot, not live monitoring. Open it in a browser and refresh after each checkpoint.

## Try the scripts without Claude

```bash
python3 .claude/skills/zerolag/scripts/render_report.py examples/demo-findings.json --out-dir /tmp/zerolag-demo
python3 .claude/skills/zerolag/scripts/discover.py /path/to/app
node .claude/skills/zerolag/scripts/journey_timer.mjs --help
```

## Maintain

- **Tests**:
  - Run `python3 -m unittest discover -s tests -v` on Python 3.9+.
  - Browser and journey-timer tests run when `ZEROLAG_PLAYWRIGHT_FROM` points to a folder whose `node_modules` contains Playwright with its Chromium installed; otherwise they are skipped.
  - `ZEROLAG_TEST_DATABASE_URL` (a disposable PostgreSQL 16+ database) runs the documented SQL.
  - CI runs everything on every push.
- **Golden examples**: after changing the renderer, regenerate them:
  ```bash
  python3 .claude/skills/zerolag/scripts/render_report.py examples/demo-findings.json --html examples/demo-report.html --markdown examples/demo-report.md
  ZEROLAG_PLAYWRIGHT_FROM=/path/with/playwright node tests/browser_check.mjs examples/demo-report.html --screenshots examples
  ```
- **Releases**:
  - Bump the version in `SKILL.md` (`metadata.version`), `.claude-plugin/plugin.json`, `render_report.py`, `journey_timer.mjs` and `CHANGELOG.md`; a test keeps them equal.
  - Plugin users only receive a new version when this number changes.
- **Version-sensitive facts**:
  - Each reference states when it was verified. Re-check it when Next.js, React, Prisma, Neon or Vercel ship a major version or change pricing tiers.
  - Run `claude plugin validate . --strict` before tagging.
- **Public hygiene**: to make sure no internal names leak, run `ZEROLAG_DENYLIST=/path/to/private-terms.txt python3 -m unittest tests.test_skill_package`. Keep that file outside the repository.

## Limits

- Lab timings are not field data, and CPU throttling is relative to the host machine. Confirm critical results on real devices or with existing real-user monitoring.
- Without browser automation, database statistics or platform telemetry, findings stay "seen in code" or "hypothesis", and the report says so.
- One pass fixes at most three bottlenecks. It never guarantees app-wide speed.

## Primary sources

[Claude Code skills](https://code.claude.com/docs/en/skills) · [Plugins](https://code.claude.com/docs/en/plugins) · [Next.js](https://nextjs.org/docs/app) · [React](https://react.dev/reference/react) · [web.dev INP](https://web.dev/articles/inp) · [Chrome DevTools MCP](https://github.com/ChromeDevTools/chrome-devtools-mcp) · [Prisma ORM](https://www.prisma.io/docs/orm) · [Neon](https://neon.com/docs) · [PostgreSQL](https://www.postgresql.org/docs/current/) · [Vercel Functions](https://vercel.com/docs/functions)
