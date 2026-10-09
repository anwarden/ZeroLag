# ZeroLag

Find and fix the latency your users actually feel in React and Next.js apps on Vercel, Prisma and Neon/PostgreSQL.

ZeroLag audits first (read-only), shows a one-screen report, then fixes the top bottlenecks one at a time and proves each fix with before/after timings.

<img src="examples/preview-desktop.png" alt="ZeroLag report: three headline numbers, the next action, the top three bottlenecks, before/after journey timings and an impact × effort map" width="900" />

## Install

In Claude Code:

```text
/plugin install zerolag --marketplace anwarden/ZeroLag
```

On Claude Code older than 2.1.275, run `/plugin marketplace add anwarden/ZeroLag`, then `/plugin install zerolag@zerolag`.

## Use

Open Claude Code in your app's folder and run:

```text
/zerolag:zerolag audit     read-only audit and report
/zerolag:zerolag fix       audit, then up to three verified fixes
```

You can also describe the problem instead, for example "the employees tab is slow on mobile".

Then open `.zerolag/report.html` in your browser. [See a demo report](examples/demo-report.html).

## Good to know

- **Requirements**:
  - Needs Python 3, included with the macOS developer tools and most Linux systems.
  - Your app's Playwright gives precise timings, and Chrome DevTools MCP adds traces.
  - Without them, ZeroLag still runs and lists what it could not measure.
- **Safe by default**:
  - Never deploys, pushes or commits unless you ask.
  - Never touches production data or settings, the database schema or paid features without your approval.
  - Report files stay out of Git.

More: [guide](docs/guide.md) (other install options, optional tools, maintenance) · [changelog](CHANGELOG.md)
