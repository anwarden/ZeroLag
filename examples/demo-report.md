# Performance · Demo Operations Portal

> **Demo only.** Synthetic findings and timings; no application was audited.

**Optimize phase** · 09 Oct 2026 · 09:30 UTC · Mobile dashboard navigation and form submission

Measured on: Synthetic example · production build on localhost · Chrome mobile profile (390×844, 4× CPU)

**2 high-impact open · 2/7 findings verified · 1/2 retested journeys faster**

**Working on:** Remove repeated Prisma lookups from the details panel. Then retest Open employee details.

## Top bottlenecks

1. **Remove repeated Prisma lookups from the details panel** · Measured · High impact · In progress · `PERF-02` · slows Open employee details · `lib/data/people.ts`
2. **Check Vercel function region against the Neon region** · Hypothesis · High impact · Open · `PERF-03` · needs approval · slows Open employee details +1 · `Vercel project settings · Neon project settings`
3. **Virtualize the long client-side employee table** · Measured · Medium impact · Open · `PERF-04` · `components/data-grid.tsx`

## Journeys

| Journey | Before | After | Result |
|---|---:|---:|---|
| Switch between dashboard tabs | 1.82 s | 960 ms | 47% faster (−860 ms) |
| Open employee details | 2.40 s | — | Awaiting retest |
| Submit administrative form | 1.34 s | 1.30 s | Within noise |

Statistic: Median click → confirmed save; Median click → usable.

<details><summary>Other findings (4)</summary>

| Rank | Id | Impact | Evidence | Status | Finding |
|---:|---|---|---|---|---|
| 4 | `PERF-05` | Medium | Seen in code | Open | Move the chart library out of the shared admin layout |
|  | `PERF-01` | High | Measured | Verified | Eliminate sequential data fetching in tab navigation |
|  | `PERF-07` | Low | Measured | Verified | Remove a duplicate status request on back navigation |
|  | `PERF-06` | Low | Hypothesis | Deferred | Avoid style recalculation on navigation hover |

</details>

<details><summary>Blind spots and limitations (4)</summary>

- PostgreSQL / Neon: not observable. No read access to the shared database statistics.
- Synthetic example: no repository, credentials, telemetry or customer data was used.
- Findings can overlap; their gains must never be added together.
- Region and bundle findings stay unverified until measured.

</details>

Gains are never added across findings. Full evidence: the source JSON and the HTML report.
