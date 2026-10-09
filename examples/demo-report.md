**DEMO ONLY — synthetic findings and timings.**

# Performance — Demo Operations Portal

**2 high-impact open · 7 found · 2 verified** · Optimizing

**Next:** Remove repeated Prisma lookups from the list API

## Response time

| Interaction | Before | After | Change |
|---|---:|---:|---|
| Switch between dashboard tabs | 1.82 s | 960 ms | 47% faster |
| Open employee details | 2.40 s | — | Awaiting comparable retest |
| Submit administrative form | 1.34 s | 1.33 s | ≈ unchanged (0.4%) |

## Opportunities · highest impact first

| Impact | Status | Finding |
|---|---|---|
| High | Verified | Eliminate sequential data fetching in tab navigation |
| High | In progress | Remove repeated Prisma lookups from the list API |
| High | Open | Check Vercel-to-Neon region alignment |
| Medium | Open | Virtualize a long client-side table |
| Medium | Open | Reduce nonessential JavaScript loaded with the admin shell |
| Low | Verified | Remove an obsolete duplicate status request |
| Low | Deferred | Avoid redundant style recalculation on hover |

**Caveats:** 3 (see source JSON).

Full evidence: `PERFORMANCE_FINDINGS.json` · Visual: `PERFORMANCE_REPORT.html`.
