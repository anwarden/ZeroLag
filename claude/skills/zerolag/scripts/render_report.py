#!/usr/bin/env python3
"""Build an offline, dependency-free HTML and Markdown performance report.

The input JSON is authored by the coding agent after gathering evidence. This
renderer does NOT diagnose a project, estimate performance, or invent findings.
"""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
from html import escape
import json
from pathlib import Path
import sys

IMPACT = {"high": 3, "medium": 2, "low": 1}
CONFIDENCE = {"high": 3, "medium": 2, "low": 1}
EFFORT = {"small": 3, "medium": 2, "large": 1}
RISK = {"low": 3, "medium": 2, "high": 1}
STATUSES = {
    "identified": "Identified",
    "in_progress": "In progress",
    "implemented": "Implemented · awaiting verification",
    "verified": "Verified",
    "blocked": "Blocked",
    "deferred": "Deferred",
}
PHASES = ["baseline", "triage", "fixing", "verification", "complete"]
PHASE_LABEL = {
    "baseline": "Measuring",
    "triage": "Prioritizing",
    "fixing": "Optimizing",
    "verification": "Verifying",
    "complete": "Review complete",
}
STATUS_SHORT = {
    "identified": "Open",
    "in_progress": "In progress",
    "implemented": "To verify",
    "verified": "Verified",
    "blocked": "Blocked",
    "deferred": "Deferred",
}
PIPELINE_STEPS = [
    ("Measure", "Baseline the slow interactions"),
    ("Diagnose", "Find where the time goes"),
    ("Optimize", "Apply the highest-value fixes"),
    ("Verify", "Re-test under matched conditions"),
]


def h(value: object) -> str:
    return escape(str(value if value is not None else ""), quote=True)


def require_str(obj: dict, field: str, context: str) -> str:
    value = obj.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{context}.{field} must be a non-empty string")
    return value.strip()


def optional_str(obj: dict, field: str) -> str:
    value = obj.get(field)
    return value.strip() if isinstance(value, str) else ""


def number_or_none(obj: dict, field: str) -> float | None:
    value = obj.get(field)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (float, int)) or not (0 <= value < 1e12):
        raise ValueError(f"{field} must be a nonnegative finite number or null")
    return float(value)


def integer_or_none(obj: dict, field: str) -> int | None:
    value = obj.get(field)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{field} must be a positive integer or null")
    return value


def validate(data: dict) -> dict:
    if not isinstance(data, dict):
        raise ValueError("Input root must be a JSON object")
    require_str(data, "project", "report")
    phase = data.get("phase", "baseline")
    if phase not in PHASES:
        raise ValueError(f"phase must be one of {', '.join(PHASES)}")
    findings = data.get("findings", [])
    flows = data.get("flows", [])
    limits = data.get("limitations", [])
    if not isinstance(findings, list) or not isinstance(flows, list):
        raise ValueError("findings and flows must be arrays")
    if not isinstance(limits, list) or any(not isinstance(v, str) for v in limits):
        raise ValueError("limitations must be a string array")
    seen = set()
    for i, item in enumerate(findings):
        if not isinstance(item, dict):
            raise ValueError(f"findings[{i}] must be an object")
        ident = require_str(item, "id", f"findings[{i}]")
        require_str(item, "title", f"findings[{i}]")
        if ident in seen:
            raise ValueError(f"Duplicate finding id: {ident}")
        seen.add(ident)
        for name, possible in (
            ("impact", IMPACT),
            ("confidence", CONFIDENCE),
            ("effort", EFFORT),
            ("risk", RISK),
            ("status", STATUSES),
        ):
            if item.get(name) not in possible:
                raise ValueError(f"findings[{i}].{name} must be one of {', '.join(possible)}")
        kind = item.get("evidence_kind", "hypothesis")
        if kind not in ("measured", "hypothesis"):
            raise ValueError(f"findings[{i}].evidence_kind must be measured or hypothesis")
        for key in ("before_ms", "after_ms"):
            number_or_none(item, key)
        for key in ("before_samples", "after_samples"):
            integer_or_none(item, key)
        if "comparable" in item and not isinstance(item["comparable"], bool):
            raise ValueError(f"findings[{i}].comparable must be boolean")
        if item["status"] == "verified" and not optional_str(item, "verification"):
            raise ValueError(f"findings[{i}] cannot be 'verified' without verification evidence")
    for i, flow in enumerate(flows):
        if not isinstance(flow, dict):
            raise ValueError(f"flows[{i}] must be an object")
        require_str(flow, "name", f"flows[{i}]")
        for key in ("baseline_ms", "current_ms"):
            number_or_none(flow, key)
        for key in ("before_samples", "after_samples"):
            integer_or_none(flow, key)
        if "comparable" in flow and not isinstance(flow["comparable"], bool):
            raise ValueError(f"flows[{i}].comparable must be boolean")
    return data


def rank(findings: list[dict]) -> list[dict]:
    return sorted(
        findings,
        key=lambda f: (
            -IMPACT[f["impact"]],
            -(1 if f.get("evidence_kind") == "measured" else 0),
            -CONFIDENCE[f["confidence"]],
            -EFFORT[f["effort"]],
            -RISK[f["risk"]],
            f["id"],
        ),
    )


def comparable_pair(item: dict, before: str, after: str) -> tuple[float, float] | None:
    a = number_or_none(item, before)
    b = number_or_none(item, after)
    if a is None or b is None or a <= 0 or not item.get("comparable", False):
        return None
    if integer_or_none(item, "before_samples") is None or integer_or_none(item, "after_samples") is None:
        return None
    return a, b


def format_ms(ms: float | None) -> str:
    if ms is None:
        return "—"
    if ms >= 1000:
        return f"{ms / 1000:,.2f} s".rstrip("0").rstrip(".") if ms % 1000 else f"{ms / 1000:,.0f} s"
    return f"{ms:,.0f} ms" if ms >= 1 else f"{ms:,.2f} ms"


def change_label(item: dict, before: str, after: str) -> str:
    pair = comparable_pair(item, before, after)
    if not pair:
        return "Not yet comparable"
    a, b = pair
    difference = a - b
    verb = "faster" if difference > 0 else ("slower" if difference < 0 else "unchanged")
    if verb == "unchanged":
        return "No observed change"
    return f"{format_ms(abs(difference))} {verb} ({abs(difference / a) * 100:.0f}%)"


def label(value: str) -> str:
    return value.replace("_", " ").capitalize()


def count_summary(data: dict) -> dict:
    findings = data.get("findings", [])
    status = Counter(f["status"] for f in findings)
    high_open = sum(f["impact"] == "high" and f["status"] not in ("verified", "deferred") for f in findings)
    measured_flows = sum(comparable_pair(flow, "baseline_ms", "current_ms") is not None for flow in data.get("flows", []))
    return {
        "total": len(findings),
        "verified": status["verified"],
        "implemented": status["implemented"],
        "high_open": high_open,
        "measured_flows": measured_flows,
    }


# Compact, evidence-first presentation; no external scripts, fonts, or assets.
STYLE = r"""
:root{color-scheme:light;--ink:#20312f;--muted:#677673;--line:#e1e8e4;--accent:#2a6461;--good:#2f785b;--warm:#985834;--soft:#f5f8f6}
*{box-sizing:border-box}body{margin:0;background:#fff;color:var(--ink);font:13px/1.45 system-ui,-apple-system,"Segoe UI",sans-serif;-webkit-font-smoothing:antialiased}
main{max-width:900px;margin:0 auto;padding:34px 28px 44px}h1,h2,h3,p{margin:0}h1{font-size:24px;font-weight:650;letter-spacing:-.035em}h2{font-size:14px;font-weight:650}small,.muted{color:var(--muted)}
.topline{display:flex;justify-content:space-between;align-items:baseline;gap:16px}.meta{color:var(--muted);font-size:11px;text-align:right;white-space:nowrap}.scope{color:var(--muted);font-size:12px;margin-top:3px;overflow-wrap:anywhere}.demo{color:#87562f;font-size:11px;margin-top:8px}
.metrics{display:flex;align-items:center;flex-wrap:wrap;gap:8px 20px;border-top:1px solid var(--line);border-bottom:1px solid var(--line);padding:14px 0;margin-top:21px}.metrics b{font-size:19px;letter-spacing:-.03em;font-weight:650;font-variant-numeric:tabular-nums}.metrics .high b{color:var(--warm)}.metrics .verified b{color:var(--good)}.meter{margin-left:auto;width:96px;height:4px;border-radius:9px;background:var(--line);overflow:hidden}.meter i{display:block;height:100%;background:var(--good)}
.stage{display:flex;align-items:center;flex-wrap:wrap;gap:8px;margin:12px 0 13px;font-size:11px;color:var(--muted)}.stage-step{white-space:nowrap}.stage-step::before{content:'○';margin-right:5px;color:#8b9993}.stage-step.done::before{content:'✓';color:var(--good)}.stage-step.current{color:var(--accent);font-weight:650}.stage-step.current::before{content:'●';font-size:8px;color:var(--accent)}.stage-sep{color:#b2bfba}
.next{padding:11px 13px;background:var(--soft);border:1px solid var(--line);border-radius:7px;display:flex;align-items:baseline;gap:12px}.next small{flex-shrink:0;font-size:10px;text-transform:uppercase;letter-spacing:.065em;font-weight:700}.next strong{font-weight:600;font-size:12px;overflow-wrap:anywhere}
.visuals{display:grid;grid-template-columns:minmax(0,1.3fr) minmax(0,.85fr);gap:23px;margin:21px 0 0}.block-heading{display:flex;justify-content:space-between;align-items:baseline;gap:8px;margin-bottom:11px}.block-heading small{font-size:11px}.flows{display:grid;gap:12px}.flow{display:grid;grid-template-columns:minmax(0,1fr) 116px;column-gap:12px;align-items:center}.flow-name{font-size:12px;overflow-wrap:anywhere}.flow-outcome{font-size:11px;color:var(--muted);text-align:right;white-space:nowrap}.flow-outcome.good{color:var(--good)}.flow-outcome.bad{color:#ae4c43}.flow-bars{grid-column:1 / -1;display:grid;grid-template-columns:44px minmax(0,1fr) 56px;gap:5px 9px;margin-top:4px;align-items:center}.flow-bars span{color:var(--muted);font-size:10px}.flow-bars strong{font-size:10px;text-align:right;font-weight:550;font-variant-numeric:tabular-nums}.track{background:#eef2ef;height:5px;border-radius:10px;overflow:hidden}.track i{display:block;height:100%;background:#bac8c2;border-radius:10px}.track i.after{background:var(--accent)}.track i.pending{background:transparent}
.heatmap{display:grid;grid-template-columns:40px repeat(3,minmax(0,1fr));gap:4px}.heat-label{text-align:center;font-size:10px;color:var(--muted);padding-bottom:3px}.heat-label.side{text-align:left;align-self:center;padding:0}.heat-cell{min-height:26px;display:flex;align-items:center;justify-content:center;background:#f2f5f3;border-radius:3px;font-size:11px;font-weight:600;color:var(--muted);font-variant-numeric:tabular-nums}.heat-cell.busy.high{background:#f2e8df;color:#86502f}.heat-cell.busy.medium{background:#e2f0ea;color:#24594b}.heat-cell.busy.low{background:#e7edeb;color:#516963}.footnote{color:var(--muted);font-size:10px;margin-top:5px}
.priorities{margin-top:24px}.priority-list{border-top:1px solid var(--line)}.priority{min-height:43px;display:grid;grid-template-columns:14px minmax(0,1fr) 70px 92px;align-items:center;gap:12px;border-bottom:1px solid var(--line)}.priority .number{color:#92a19b;font-size:11px;font-variant-numeric:tabular-nums}.priority-title{font-size:12px;font-weight:560;overflow-wrap:anywhere}.priority .impact,.priority .status{font-size:11px;white-space:nowrap;color:var(--muted)}.priority .impact.high{color:var(--warm)}.priority .status.verified{color:var(--good)}.priority .status.in_progress{color:var(--accent)}.priority .status.blocked{color:var(--warm)}
.details{margin-top:10px;border-bottom:1px solid var(--line)}summary{cursor:pointer;list-style:none;user-select:none}summary::-webkit-details-marker{display:none}summary::marker{content:''}summary:focus-visible{outline:2px solid var(--accent);outline-offset:3px;border-radius:2px}.details>summary{padding:9px 0;color:var(--accent);font-size:12px;font-weight:550}.details>summary::after{content:'⌄';margin-left:7px}.details[open]>summary::after{content:'⌃'}
.finding{border-top:1px solid var(--line)}.finding>summary{display:flex;align-items:center;justify-content:space-between;gap:10px;padding:10px 0;font-size:12px}.finding-label{flex:1;min-width:0;overflow-wrap:anywhere}.finding-label small{font-size:10px}.finding-status{color:var(--muted);font-size:10px;white-space:nowrap}.technical{background:var(--soft);padding:11px 14px;display:grid;grid-template-columns:1fr 1fr;gap:8px 20px}.technical dt{font-size:10px;color:var(--muted)}.technical dd{font-size:11px;margin:2px 0 0;overflow-wrap:anywhere}.notes{margin-top:11px}.notes>summary{color:var(--muted)}.notes ul{margin:6px 0 11px;padding-left:19px;color:var(--muted);font-size:11px}footer{margin-top:22px;font-size:10px;color:var(--muted)}
@media(max-width:680px){main{padding:24px 16px 38px}.topline{display:block}.meta{text-align:left;margin-top:4px}h1{font-size:22px}.metrics{gap:8px 14px}.meter{width:100%;margin:0}.visuals{grid-template-columns:1fr;gap:20px}.stage{gap:6px 5px}.priority{grid-template-columns:14px minmax(0,1fr) 63px;gap:8px;min-height:51px}.priority .impact{display:none}.technical{grid-template-columns:1fr}.next{display:block}.next small{display:block;margin-bottom:4px}}
@media(prefers-reduced-motion:reduce){*{scroll-behavior:auto!important}}@media print{main{max-width:none;padding:0}.details{break-inside:avoid}}
"""


def short_flow_change(item: dict) -> tuple[str, str]:
    pair = comparable_pair(item, "baseline_ms", "current_ms")
    if not pair:
        return ("Awaiting retest" if number_or_none(item, "current_ms") is None else "Not comparable", "neutral")
    before, after = pair
    pct = abs((after - before) / before) * 100
    if pct < 1:
        return (f"≈ unchanged ({pct:.1f}%)", "neutral")
    return (f"{pct:.0f}% faster" if after < before else f"{pct:.0f}% slower", "good" if after < before else "bad")


def pipeline_html(phase: str) -> str:
    step = {"baseline": 0, "triage": 1, "fixing": 2, "verification": 3, "complete": 4}.get(phase, 0)
    names = ("Measure", "Diagnose", "Optimize", "Verify")
    out = []
    for index, name in enumerate(names):
        cls = "done" if index < step else ("current" if index == step else "")
        out.append(f'<span class="stage-step {cls}">{name}</span>')
        if index < 3:
            out.append('<span class="stage-sep" aria-hidden="true">→</span>')
    return "".join(out)


def matrix_html(findings: list[dict]) -> str:
    """Tiny matrix: genuine counts only; the full evidence lives below."""
    remaining = [f for f in findings if f["status"] not in ("verified", "deferred")]
    parts = ['<div class="heatmap" role="group" aria-label="Open issues by impact and effort">',
             '<span aria-hidden="true"></span>',
             '<span class="heat-label">Small</span><span class="heat-label">Med.</span><span class="heat-label">Large</span>']
    for impact in ("high", "medium", "low"):
        parts.append(f'<span class="heat-label side">{label(impact)}</span>')
        for effort in ("small", "medium", "large"):
            n = sum(f["impact"] == impact and f["effort"] == effort for f in remaining)
            parts.append(f'<span class="heat-cell {impact} {"busy" if n else ""}" role="text" aria-label="{label(impact)} impact, {label(effort)} effort: {n} open findings">{n if n else "·"}</span>')
    return "".join(parts) + "</div>"


def flow_row(item: dict, maximum: float) -> str:
    baseline = number_or_none(item, "baseline_ms")
    current = number_or_none(item, "current_ms")
    delta, tone = short_flow_change(item)
    # A measured current is drawn only if the before/after conditions are comparable.
    pair = comparable_pair(item, "baseline_ms", "current_ms")
    current_display = current if pair else None
    delta_display = delta if pair else ("Awaiting retest" if current is None else "Not comparable")
    def width(value: float | None) -> str:
        return f"{max(4, min(100, 100 * value / maximum)):.1f}%" if value is not None else "0%"
    return f'''<div class="flow">
      <span class="flow-name">{h(item['name'])}</span><span class="flow-outcome {tone if pair else 'neutral'}">{h(delta_display)}</span>
      <div class="flow-bars" aria-label="Before {h(format_ms(baseline))}, after {h(format_ms(current_display))}">
        <span>Before</span><div class="track"><i style="width:{width(baseline)}"></i></div><strong>{h(format_ms(baseline))}</strong>
        <span>After</span><div class="track"><i class="{'after' if pair else 'pending'}" style="width:{width(current_display)}"></i></div><strong>{h(format_ms(current_display))}</strong>
      </div>
    </div>'''


def priority_row(item: dict, position: int) -> str:
    return f'''<div class="priority"><span class="number">{position:02d}</span>
      <span class="priority-title">{h(item['title'])}</span><span class="impact {h(item['impact'])}">{h(label(item['impact']))}</span>
      <span class="status {h(item['status'])}">{h(STATUS_SHORT[item['status']])}</span></div>'''


def finding_row(item: dict, position: int) -> str:
    evidence = "Measured" if item.get("evidence_kind") == "measured" else "Needs validation"
    info = [
        ("Evidence", optional_str(item, "signal") or "Not recorded"),
        ("Next step", optional_str(item, "proposed_change") or "Investigate"),
        ("Source", optional_str(item, "location") or "Not identified"),
        ("Assessment", f"{label(item['confidence'])} confidence · {label(item['effort'])} effort · {label(item['risk'])} risk"),
    ]
    if optional_str(item, "verification"):
        info.append(("Verified by", item["verification"]))
    if comparable_pair(item, "before_ms", "after_ms"):
        info.append(("Observed", f"{format_ms(item['before_ms'])} → {format_ms(item['after_ms'])} · {change_label(item, 'before_ms', 'after_ms')}"))
    rows = "".join(f'<div><dt>{h(k)}</dt><dd>{h(v)}</dd></div>' for k, v in info)
    return f'''<details class="finding" data-impact="{h(item['impact'])}"><summary>
      <span class="finding-label">{position:02d} · {h(item['title'])} <small>({h(evidence)})</small></span>
      <span class="finding-status">{h(label(item['impact']))} · {h(STATUS_SHORT[item['status']])}</span>
      </summary><dl class="technical">{rows}</dl></details>'''


def render_html(data: dict) -> str:
    stats = count_summary(data)
    ordered = rank(data.get("findings", []))
    actionable = [f for f in ordered if f["status"] not in ("verified", "deferred", "blocked")]
    top = actionable[:3]
    phase = data.get("phase", "baseline")
    verified_pct = round(100 * stats["verified"] / stats["total"]) if stats["total"] else 0
    stamp = datetime.now(timezone.utc).strftime("%d %b %Y · %H:%M UTC")
    project = h(data["project"])
    scope = optional_str(data, "scope")
    scope_html = f'<p class="scope">{project}{(" · " + h(scope)) if scope else ""}</p>'
    demo_html = '<p class="demo">Illustrative demo · No real application was audited</p>' if data.get("demo") else ""
    if actionable:
        next_one = actionable[0]
        action = "Validate next" if next_one.get("evidence_kind") != "measured" else ("Working on" if next_one["status"] == "in_progress" else "Fix next")
        next_html = f'<div class="next"><small>{h(action)}</small><strong>{h(next_one["title"])}</strong></div>'
    else:
        next_html = '<div class="next"><small>Next</small><strong>No actionable finding in this audit scope.</strong></div>'
    priority_html = "".join(priority_row(f, i + 1) for i, f in enumerate(top)) or '<p class="muted">No open actionable issues.</p>'
    # Show at most 3 journeys in the default view, keeping the remainder accessible.
    flows = data.get("flows", [])
    visible_flows = flows[:3]
    maximum = max([number_or_none(f, k) or 0 for f in visible_flows for k in ("baseline_ms", "current_ms")] + [1])
    flow_html = "".join(flow_row(f, maximum) for f in visible_flows) or '<p class="muted">Awaiting baseline measurements.</p>'
    remaining_flows = flows[3:]
    extra_flows = (
        f'<details class="details"><summary>{len(remaining_flows)} more interaction(s)</summary>' +
        '<div class="flows">' + "".join(flow_row(f, maximum) for f in remaining_flows) + '</div></details>'
    ) if remaining_flows else ""
    all_findings = "".join(finding_row(f, i + 1) for i, f in enumerate(ordered)) or '<p class="muted">No findings recorded.</p>'
    caveats = list(data.get("limitations", []))
    if optional_str(data, "environment"):
        caveats.append("Environment: " + data["environment"])
    notes_html = (
        f'<details class="details notes"><summary>Measurement notes ({len(caveats)})</summary><ul>' +
        "".join(f'<li>{h(note)}</li>' for note in caveats) + '</ul></details>'
    ) if caveats else ""
    return f'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light"><title>{project} · Performance</title><style>{STYLE}</style></head>
<body><main><header class="topline"><div><h1>Performance</h1>{scope_html}{demo_html}</div>
<div class="meta">{h(PHASE_LABEL[phase])} · {h(stamp)}</div></header>
<div class="metrics" aria-label="Audit summary">
  <span class="high"><b>{stats['high_open']}</b> high-impact open</span>
  <span><b>{stats['total']}</b> found</span>
  <span class="verified"><b>{stats['verified']}</b> verified</span>
  <div class="meter" role="progressbar" aria-label="Verified fixes" aria-valuemin="0" aria-valuemax="100" aria-valuenow="{verified_pct}"><i style="width:{verified_pct}%"></i></div>
</div>
<div class="stage" aria-label="Optimization stage">{pipeline_html(phase)}</div>
{next_html}
<div class="visuals"><section aria-labelledby="flows-heading"><div class="block-heading"><h2 id="flows-heading">Response time</h2><small>Before / after</small></div>
 <div class="flows">{flow_html}</div>{extra_flows}</section>
<section aria-labelledby="map-heading"><div class="block-heading"><h2 id="map-heading">Priority map</h2><small>Impact × effort</small></div>
 {matrix_html(ordered)}<p class="footnote">Open items only · numbers indicate findings</p></section></div>
<section class="priorities" aria-labelledby="priority-heading"><div class="block-heading"><h2 id="priority-heading">Top priorities</h2><small>Highest impact first</small></div>
 <div class="priority-list">{priority_html}</div>
 <details class="details" id="all-findings"><summary>All {stats['total']} findings &amp; evidence</summary>{all_findings}</details>
</section>
{notes_html}
<footer>Snapshot · Regenerate after each checkpoint. Measurements are not cumulative.</footer>
</main></body></html>'''


def md_cell(value: object) -> str:
    return str(value if value is not None else "—").replace("|", "\\|").replace("\n", " ").strip()


def render_markdown(data: dict) -> str:
    """A quick handoff, not a second full-length technical report."""
    stats = count_summary(data)
    ordered = rank(data.get("findings", []))
    actionable = [f for f in ordered if f["status"] not in ("verified", "deferred", "blocked")]
    phase = data.get("phase", "baseline")
    lines = [f"# Performance — {data['project']}", "",
             f"**{stats['high_open']} high-impact open · {stats['total']} found · {stats['verified']} verified** · {PHASE_LABEL[phase]}", "",
             f"**Next:** {actionable[0]['title'] if actionable else 'No actionable findings'}", "",
             "## Response time", "",
             "| Interaction | Before | After | Change |", "|---|---:|---:|---|"]
    for flow in data.get("flows", []):
        pair = comparable_pair(flow, "baseline_ms", "current_ms")
        current = format_ms(pair[1]) if pair else "—"
        outcome, _ = short_flow_change(flow)
        lines.append(f"| {md_cell(flow['name'])} | {format_ms(flow.get('baseline_ms'))} | {current} | {outcome if pair else 'Awaiting comparable retest'} |")
    if not data.get("flows"):
        lines.append("| No measurements yet | — | — | — |")
    lines.extend(["", "## Opportunities · highest impact first", "",
                  "| Impact | Status | Finding |", "|---|---|---|"])
    for item in ordered:
        lines.append(f"| {label(item['impact'])} | {STATUS_SHORT[item['status']]} | {md_cell(item['title'])} |")
    if not ordered:
        lines.append("| — | — | No findings |")
    if data.get("limitations"):
        lines.extend(["", f"**Caveats:** {len(data['limitations'])} (see source JSON)."])
    if data.get("demo"):
        lines.insert(0, "**DEMO ONLY — synthetic findings and timings.**\n")
    lines.append("\nFull evidence: `PERFORMANCE_FINDINGS.json` · Visual: `PERFORMANCE_REPORT.html`.\n")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path, help="Input audit findings JSON")
    parser.add_argument("--html", default=Path("PERFORMANCE_REPORT.html"), type=Path, help="Offline HTML dashboard output")
    parser.add_argument("--markdown", default=Path("PERFORMANCE_REPORT.md"), type=Path, help="Markdown report output")
    args = parser.parse_args()
    try:
        data = validate(json.loads(args.input.read_text(encoding="utf-8")))
        args.html.parent.mkdir(parents=True, exist_ok=True)
        args.markdown.parent.mkdir(parents=True, exist_ok=True)
        args.html.write_text(render_html(data), encoding="utf-8")
        args.markdown.write_text(render_markdown(data), encoding="utf-8")
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"Report generation failed: {error}", file=sys.stderr)
        return 1
    stats = count_summary(data)
    print(f"Dashboard: {args.html.resolve()}")
    print(f"Markdown:  {args.markdown.resolve()}")
    print(f"Found {stats['total']} opportunities; {stats['high_open']} high-impact outstanding; {stats['verified']} verified.")
    for item in [f for f in rank(data.get('findings', [])) if f['status'] not in ('verified', 'deferred')][:3]:
        print(f"  {item['impact'].upper()} · {item['id']} · {item['title']} ({item['status']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
