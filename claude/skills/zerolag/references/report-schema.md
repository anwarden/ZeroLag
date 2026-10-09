# Report data contract (schema version 2)

`render_report.py` validates `.zerolag/findings.json`, ranks findings and writes `report.html` and `report.md` next to it. It never audits the app, estimates savings, reads the clock or touches the network: the same JSON always produces the same bytes. Version 1 files (phases `baseline`/`triage`/`fixing`/`verification`, `PERFORMANCE_FINDINGS.json`) still render.

## Contents
1. Files
2. Minimal example
3. Root fields
4. Journeys (`flows`)
5. Findings
6. Rubrics: impact, confidence, effort, risk
7. Coverage and limitations
8. How the renderer decides
9. Command line

## 1. Files

| Path (app or workspace root) | Content |
|---|---|
| `.zerolag/findings.json` | Source of truth, written by the agent |
| `.zerolag/report.html` | One-screen offline brief (no scripts, no network, light and dark) |
| `.zerolag/report.md` | Short summary with collapsed details |
| `.zerolag/journeys.json`, `.zerolag/runs/` | Journey spec and raw timer output |
| `.zerolag/.gitignore` | Created by the renderer (`*`): the folder never reaches Git |

## 2. Minimal example

```json
{
  "schema_version": 2,
  "project": "Admin portal",
  "scope": "Employee list and detail panel, mobile",
  "environment": "Local production build · mobile profile (390×844, 4× CPU) · fixture data",
  "updated_at": "2026-10-09T14:05:00Z",
  "phase": "audit",
  "flows": [
    {"id": "details", "name": "Open employee details", "statistic": "Median click → usable",
     "conditions": "Production build · mobile · warm", "source": "journey_timer.mjs",
     "baseline_runs_ms": [2310, 2365, 2400, 2420, 2490], "comparable": false}
  ],
  "findings": [
    {"id": "PERF-01", "title": "Remove repeated lookups from the details panel", "layer": "orm",
     "impact": "high", "confidence": "high", "effort": "medium", "risk": "medium", "status": "identified",
     "evidence_kind": "measured", "signal": "Six identical SELECTs per panel open (query log, 5 of 5 runs)",
     "location": "lib/data/people.ts", "proposed_change": "One query selecting only rendered fields",
     "journeys": ["details"]}
  ],
  "coverage": [{"layer": "database", "status": "not_observable", "note": "No statistics access"}],
  "limitations": ["No production telemetry"]
}
```

## 3. Root fields

| Field | Rule |
|---|---|
| `schema_version` | `2` (or `1` for legacy files) |
| `project` | Required, non-empty |
| `scope`, `environment` | Short text; no internal URLs |
| `updated_at` | ISO 8601 date or date-time with timezone (`date -u +%Y-%m-%dT%H:%M:%SZ`); shown as the snapshot time |
| `phase` | `audit` → `optimize` → `verify` → `complete` (complete = this pass ended, not "everything fixed") |
| `next_action` | Optional sentence that overrides the derived next action |
| `demo` | `true` only for synthetic examples |
| `flows`, `findings`, `coverage`, `limitations` | Arrays (see below) |
| `x_*` | Any custom field prefixed with `x_` is kept and ignored; other unknown fields raise warnings |

## 4. Journeys (`flows`)

| Field | Rule |
|---|---|
| `name` | Required; `id` optional but unique (findings reference either) |
| `app` | App path or name in multi-app reports |
| `baseline_runs_ms` / `current_runs_ms` | Preferred: raw run timings (ms); the renderer computes median and IQR |
| `baseline_ms` + `before_samples`, `current_ms` + `after_samples` | Accepted summaries (median and run count) when raw runs are unavailable; must agree with raw runs if both are given |
| `comparable` | `true` only when journey definition, build, profile, network, cache state, account and data shape match |
| `statistic`, `conditions`, `source` | What was computed, under which conditions, with which tool |

Only the first three journeys appear in the chart; all appear in the details.

## 5. Findings

| Field | Rule |
|---|---|
| `id`, `title` | Required; ids stay stable across checkpoints |
| `impact`, `confidence` | `high`, `medium`, `low` |
| `effort` | `small`, `medium`, `large` |
| `risk` | `low`, `medium`, `high` |
| `status` | `identified` → `in_progress` → `implemented` → `verified`, or `blocked`, `deferred` |
| `evidence_kind` | `measured` (recorded timing, trace or count; `signal` required), `inspected` (confirmed in code, config or plan; cost not measured), `hypothesis` (plausible, unconfirmed; default) |
| `layer` | `interaction`, `react`, `nextjs`, `network`, `server`, `orm`, `database`, `delivery`; `category` (free text) is the legacy alternative |
| `app` | App path or name; `shared` for cross-app infrastructure |
| `signal`, `location`, `proposed_change` | Evidence, code or config location, smallest change that removes the root cause |
| `verification` | Required for `verified`: how it was retested (runs, conditions, tests) |
| `requires_approval` | `true` or a short reason for schema, index, infrastructure, paid, production or cross-team changes |
| `journeys` | Journey ids or names this finding slows down |
| `before_runs_ms` / `after_runs_ms` (or `before_ms`/`before_samples`, `after_ms`/`after_samples`) + `comparable` | Optional per-finding measurement, same rules as journeys |
| `notes` | Optional short context |

## 6. Rubrics

- **Impact**: `high` = on the critical path of a frequent or core journey and ≥ 20% of its time, ≥ 200 ms of interaction latency, or causing errors, timeouts or data risk; `medium` = noticeable (≥ 100 ms or 10–20%) on a common journey, or high on a rare one; `low` = minor or rare.
- **Confidence**: `high` = measured and reproduced (≥ 5 runs or two independent sources); `medium` = measured once, or inspected with a clear mechanism; `low` = hypothesis.
- **Effort**: `small` ≤ half a day and a few files; `medium` 1–3 days or several modules; `large` multi-day, schema, infrastructure or architecture work.
- **Risk**: `low` = local change covered by tests; `medium` = shared code, caching or auth-adjacent paths; `high` = authorization, tenant or cache boundaries, data writes, schema or infrastructure.

## 7. Coverage and limitations

- `coverage`: one entry per layer — `{"layer": "react", "status": "checked"}` with `status` in `checked`, `not_applicable`, `not_observable`, `skipped` and an optional `note`. "Not observable" is a blind spot, not a clean bill of health.
- `limitations`: missing access, telemetry or tools, unrepresentative data, and anything that weakens conclusions. No secrets or personal data.

## 8. How the renderer decides

- **Ranking**: expected return = impact (9/3/1) × confidence (10/7/4) × ease (small 10, medium 7, large 4) × safety (low risk 10, medium 8, high 6); ties go to higher impact, then stronger evidence (measured, inspected, hypothesis), then id. Top bottlenecks = the three best open findings (`identified`, `in_progress`, `implemented`, `blocked`).
- **Next action**: `next_action` if set; else the in-progress finding ("Working on"), else one awaiting verification ("Verify next"), else the best identified one ("Fix next" if measured, "Measure next" if inspected, "Validate next" if a hypothesis, "Needs approval" if `requires_approval`), else a blocked one ("Unblock").
- **Comparisons**: no baseline → "No baseline"; no current value → "Awaiting retest"; `comparable` false → "Not comparable" (the after value is hidden); fewer than 3 runs on a side → "Too few runs"; change under 1% → "≈ unchanged"; overlapping IQRs (raw runs on both sides) → "Within noise"; otherwise "N% faster" or "N% slower".
- **Headline metrics**: high-impact open findings; verified/total findings (green only when all are verified); retested journeys faster/compared (green only when all are faster), or journeys baselined before any retest.
- **Layout**: metrics and next action, then the top three bottlenecks (expandable, with stable ids and anchors such as `report.html#f-PERF-02`), then the two diagrams; other findings, journey details, coverage and limitations stay collapsed and print expanded.

## 9. Command line

```bash
python3 "${CLAUDE_SKILL_DIR}/scripts/render_report.py" .zerolag/findings.json        # writes report.html + report.md beside it
python3 "${CLAUDE_SKILL_DIR}/scripts/render_report.py" .zerolag/findings.json --check  # validate only
```

Options: `--out-dir DIR`, `--html PATH`, `--markdown PATH`, `--strict` (warnings fail). Exit codes: 0 success, 1 invalid input or write error, 2 usage error. Errors name the exact field (for example `findings[2].impact must be one of high, medium, low (got 'huge')`). Writes are atomic. Python 3.9+, standard library only.
