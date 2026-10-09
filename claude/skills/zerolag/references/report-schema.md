# Report data contract

Create or update `PERFORMANCE_FINDINGS.json` in the application root. The report renderer consumes this file and never audits or mutates the app itself. It produces a minimal impact-ranked list with expandable evidence; do not add presentation-only fields to the data contract.

## Minimal structure

```json
{
  "project": "My Next.js App",
  "scope": "User journeys tested",
  "environment": "Preview · Chrome mobile emulation · test account",
  "phase": "triage",
  "flows": [
    {
      "name": "Switch tabs",
      "baseline_ms": 1850,
      "current_ms": null,
      "before_samples": 5,
      "after_samples": null,
      "comparable": false,
      "statistic": "Median click-to-usable-result",
      "source": "Chrome DevTools trace, preview deployment"
    }
  ],
  "findings": [
    {
      "id": "PERF-001",
      "title": "Remove a confirmed server request waterfall",
      "category": "Next.js",
      "impact": "high",
      "confidence": "high",
      "effort": "small",
      "risk": "low",
      "status": "identified",
      "evidence_kind": "measured",
      "signal": "Two independent calls block each other on tab switch",
      "location": "app/dashboard/actions.ts",
      "proposed_change": "Parallelize independent awaits without bypassing authorization",
      "verification": "",
      "before_ms": null,
      "after_ms": null,
      "before_samples": null,
      "after_samples": null,
      "comparable": false
    }
  ],
  "limitations": ["No access to production route-level metrics"]
}
```

## Field rules

- `phase`: `baseline`, `triage`, `fixing`, `verification`, `complete` (in this order). `complete` means this *pass* ended, not all issues are fixed.
- `findings`: include **every distinct issue discovered**. IDs remain stable between checkpoints. Never cap the report at the three implemented fixes. A finding needs `id`, `title`, `impact`, `confidence`, `effort`, `risk`, `status`; everything else is strongly encouraged.
- `impact`, `confidence`: `high`, `medium`, `low`.
- `effort`: `small`, `medium`, `large`; `risk`: `low`, `medium`, `high`.
- `evidence_kind`: `measured` for an actually recorded signal; `hypothesis` for a suspected cause that still needs verification. A measured signal is **not** the same as a measured gain.
- `status`: `identified` → `in_progress` → `implemented` → `verified`, or `blocked` / `deferred`. Use `verified` only after a real retest; it requires a nonempty `verification` statement. A fix may be verified functionally even if its speed improvement is not isolated; disclose that in the statement.
- `flows`: every tested user interaction with `name`, optional `baseline_ms` / `current_ms`, `before_samples` / `after_samples`, `comparable`, and a clear aggregation `statistic` (for example, median) and instrumentation `source`. Keep raw run data elsewhere if needed and privacy-safe; these fields summarize comparable samples.
- Numeric changes appear **only** when both timings and both positive sample sizes exist **and** `comparable` is `true`. Set it false if build, environment, device, network, or sampling differences prevent honest comparison. Baselines with one run are single observations, not percentiles.
- `limitations`: list missing credentials, observability, instrumentation, conditions and unresolved unknowns. Do not include secrets or sensitive production data.
- `demo: true`: only in sample data, never to present a real audit.

## Render at every milestone

From the **application root**, when the skill is installed in that application:

```bash
python3 .claude/skills/zerolag/scripts/render_report.py \
  --input PERFORMANCE_FINDINGS.json \
  --html PERFORMANCE_REPORT.html \
  --markdown PERFORMANCE_REPORT.md
```

If the skill is in `~/.claude/skills/`, use `python3 ~/.claude/skills/zerolag/scripts/render_report.py` with the same arguments. The renderer prints the generated report locations and the top unresolved issues to the terminal. Share them in conversation at baseline, after each fix, and final handoff.

The renderer is offline and uses only Python 3.10+ standard library. It never fetches telemetry, calculates synthetic savings, or writes back to the input JSON.
