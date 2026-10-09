#!/usr/bin/env python3
"""Render ZeroLag findings into a one-screen offline HTML brief and a short Markdown summary.

The agent authors the findings JSON from real evidence. This renderer validates it, ranks
findings deterministically and renders them. It never audits code, estimates savings, reads
the clock or touches the network: the same input always produces byte-identical output.
Python 3.9+ standard library only.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
from html import escape
import json
import math
import os
import re
from pathlib import Path
import statistics
import sys

RENDERER_VERSION = "2.1.0"
SCHEMA_VERSION = 2
DEFAULT_INPUT = Path(".zerolag/findings.json")

IMPACT = ("high", "medium", "low")
CONFIDENCE = ("high", "medium", "low")
EFFORT = ("small", "medium", "large")
RISK = ("low", "medium", "high")
EVIDENCE = ("measured", "inspected", "hypothesis")
STATUSES = ("identified", "in_progress", "implemented", "verified", "blocked", "deferred")
OPEN_STATUSES = ("identified", "in_progress", "implemented", "blocked")
PHASES = ("audit", "optimize", "verify", "complete")
LEGACY_PHASES = {"baseline": "audit", "triage": "audit", "fixing": "optimize", "verification": "verify"}
LAYERS = ("interaction", "react", "nextjs", "network", "server", "orm", "database", "delivery")
COVERAGE_STATUSES = ("checked", "not_applicable", "not_observable", "skipped")
COMPARED_VERDICTS = ("faster", "slower", "within_noise", "unchanged")

# Expected return = impact x confidence x ease x safety. Integer weights keep the order exact.
IMPACT_WEIGHT = {"high": 9, "medium": 3, "low": 1}
CONFIDENCE_WEIGHT = {"high": 10, "medium": 7, "low": 4}
EASE_WEIGHT = {"small": 10, "medium": 7, "large": 4}
SAFETY_WEIGHT = {"low": 10, "medium": 8, "high": 6}
EVIDENCE_ORDER = {"measured": 0, "inspected": 1, "hypothesis": 2}
MIN_RUNS_FOR_VERDICT = 3

STATUS_LABEL = {
    "identified": "Open",
    "in_progress": "In progress",
    "implemented": "To verify",
    "verified": "Verified",
    "blocked": "Blocked",
    "deferred": "Deferred",
}
EVIDENCE_LABEL = {"measured": "Measured", "inspected": "Seen in code", "hypothesis": "Hypothesis"}
PHASE_LABEL = {"audit": "Audit", "optimize": "Optimize", "verify": "Verify", "complete": "Complete"}
LAYER_LABEL = {
    "interaction": "Interaction & main thread",
    "react": "React rendering",
    "nextjs": "Next.js routing & caching",
    "network": "Network & payloads",
    "server": "Server, functions & I/O",
    "orm": "Prisma / ORM",
    "database": "PostgreSQL / Neon",
    "delivery": "Bundles, assets & hydration",
}
LAYER_SHORT = {
    "interaction": "Interaction", "react": "React", "nextjs": "Next.js", "network": "Network",
    "server": "Server", "orm": "ORM", "database": "Database", "delivery": "Delivery",
}
COVERAGE_LABEL = {
    "checked": "Checked",
    "not_applicable": "Not applicable",
    "not_observable": "Not observable",
    "skipped": "Skipped",
}

ROOT_KEYS = {
    "schema_version", "project", "scope", "environment", "updated_at", "phase", "demo",
    "next_action", "flows", "findings", "coverage", "limitations",
}
FLOW_KEYS = {
    "id", "name", "app", "statistic", "source", "conditions", "comparable", "baseline_ms",
    "current_ms", "before_samples", "after_samples", "baseline_runs_ms", "current_runs_ms",
}
FINDING_KEYS = {
    "id", "title", "app", "layer", "category", "impact", "confidence", "effort", "risk",
    "status", "evidence_kind", "signal", "location", "proposed_change", "verification",
    "notes", "requires_approval", "journeys", "comparable", "before_ms", "after_ms",
    "before_samples", "after_samples", "before_runs_ms", "after_runs_ms",
}
COVERAGE_KEYS = {"layer", "status", "note"}


class ReportError(ValueError):
    """The findings data is invalid or contradictory."""


@dataclass(frozen=True)
class Sample:
    median: float
    n: int  # 0 = sample count unknown
    q1: float | None = None
    q3: float | None = None

    @property
    def has_spread(self) -> bool:
        return self.q1 is not None and self.q3 is not None and self.n >= MIN_RUNS_FOR_VERDICT


@dataclass(frozen=True)
class Comparison:
    verdict: str
    before: Sample | None
    after: Sample | None
    pct: float | None = None  # signed change; negative means faster

    @property
    def compared(self) -> bool:
        return self.verdict in COMPARED_VERDICTS


@dataclass(frozen=True)
class Flow:
    key: str
    name: str
    app: str
    statistic: str
    source: str
    conditions: str
    comparison: Comparison


@dataclass(frozen=True)
class Finding:
    id: str
    title: str
    app: str
    layer: str
    category: str
    impact: str
    confidence: str
    effort: str
    risk: str
    status: str
    evidence: str
    signal: str
    location: str
    proposed_change: str
    verification: str
    notes: str
    approval: str
    journeys: tuple
    comparison: Comparison | None
    score: int


@dataclass(frozen=True)
class CoverageItem:
    layer: str
    status: str
    note: str


@dataclass(frozen=True)
class Report:
    project: str
    scope: str
    environment: str
    updated: str
    phase: str
    demo: bool
    next_action: str
    flows: tuple
    findings: tuple  # ranked, highest expected return first
    coverage: tuple
    limitations: tuple
    apps: tuple
    warnings: tuple


# ---------------------------------------------------------------------------
# Validation and normalisation


def _fail(path: str, message: str) -> None:
    raise ReportError(f"{path} {message}")


def _object(value: object, path: str) -> dict:
    if not isinstance(value, dict):
        _fail(path, "must be a JSON object")
    return value


def _text(obj: dict, key: str, path: str, required: bool = False) -> str:
    value = obj.get(key)
    if value is None:
        if required:
            _fail(f"{path}.{key}", "is required")
        return ""
    if not isinstance(value, str):
        _fail(f"{path}.{key}", "must be a string")
    value = " ".join(value.split())
    if required and not value:
        _fail(f"{path}.{key}", "must not be empty")
    return value


def _choice(obj: dict, key: str, path: str, allowed: tuple, default: str | None = None) -> str:
    value = obj.get(key, default)
    if value not in allowed:
        _fail(f"{path}.{key}", f"must be one of {', '.join(allowed)} (got {value!r})")
    return value


def _flag(obj: dict, key: str, path: str) -> bool:
    value = obj.get(key, False)
    if not isinstance(value, bool):
        _fail(f"{path}.{key}", "must be true or false")
    return value


def _milliseconds(value: object, path: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value < 1e9:
        _fail(path, "must be a non-negative number of milliseconds")
    return float(value)


def _optional_ms(obj: dict, key: str, path: str) -> float | None:
    value = obj.get(key)
    return None if value is None else _milliseconds(value, f"{path}.{key}")


def _optional_count(obj: dict, key: str, path: str) -> int | None:
    value = obj.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        _fail(f"{path}.{key}", "must be a positive integer or null")
    return value


def _runs(obj: dict, key: str, path: str) -> list | None:
    value = obj.get(key)
    if value is None:
        return None
    if not isinstance(value, list) or not value:
        _fail(f"{path}.{key}", "must be a non-empty array of milliseconds")
    return [_milliseconds(item, f"{path}.{key}[{index}]") for index, item in enumerate(value)]


def summarize(runs: list) -> Sample:
    """Median and interquartile range (inclusive method) of raw run timings."""
    data = sorted(runs)
    if len(data) >= 2:
        q1, _, q3 = statistics.quantiles(data, n=4, method="inclusive")
    else:
        q1 = q3 = data[0]
    return Sample(median=statistics.median(data), n=len(data), q1=q1, q3=q3)


def _sample(obj: dict, path: str, median_key: str, count_key: str, runs_key: str) -> Sample | None:
    runs = _runs(obj, runs_key, path)
    median = _optional_ms(obj, median_key, path)
    count = _optional_count(obj, count_key, path)
    if runs is not None:
        sample = summarize(runs)
        if median is not None and abs(median - sample.median) > 0.5:
            _fail(f"{path}.{median_key}", f"({median:g}) contradicts the median of {runs_key} ({sample.median:g})")
        if count is not None and count != sample.n:
            _fail(f"{path}.{count_key}", f"({count}) contradicts the {sample.n} values in {runs_key}")
        return sample
    if median is None:
        if count is not None:
            _fail(f"{path}.{count_key}", f"is set but {median_key} is missing")
        return None
    return Sample(median=median, n=count or 0)


def compare(before: Sample | None, after: Sample | None, comparable: bool) -> Comparison:
    """Classify a before/after pair without ever overstating it."""
    if before is None:
        return Comparison("no_baseline", before, after)
    if after is None:
        return Comparison("awaiting_retest", before, after)
    if not comparable or before.median <= 0:
        return Comparison("not_comparable", before, after)
    pct = (after.median - before.median) / before.median * 100
    if min(before.n, after.n) < MIN_RUNS_FOR_VERDICT:
        return Comparison("insufficient_runs", before, after, pct)
    if abs(pct) < 1:
        verdict = "unchanged"
    elif before.has_spread and after.has_spread and before.q1 <= after.q3 and after.q1 <= before.q3:
        verdict = "within_noise"
    else:
        verdict = "faster" if pct < 0 else "slower"
    return Comparison(verdict, before, after, pct)


def _format_timestamp(value: str, path: str) -> str:
    raw = value[:-1] + "+00:00" if value.endswith(("Z", "z")) else value
    try:
        stamp = datetime.fromisoformat(raw)
    except ValueError:
        _fail(path, f"must be an ISO 8601 date or date-time (got {value!r})")
    if len(raw) == 10:
        return stamp.strftime("%d %b %Y")
    if stamp.tzinfo is None:
        _fail(path, "must include a timezone, for example 2026-10-09T14:05:00Z")
    return stamp.astimezone(timezone.utc).strftime("%d %b %Y · %H:%M UTC")


def _unknown(obj: dict, known: set, path: str, warnings: list) -> None:
    for key in sorted(obj):
        if key not in known and not key.startswith("x_"):
            warnings.append(f"{path}.{key} is not a known field (prefix custom fields with x_)")


def _approval(obj: dict, path: str) -> str:
    value = obj.get("requires_approval", False)
    if value is True:
        return "Required"
    if value is False or value is None:
        return ""
    if isinstance(value, str) and value.strip():
        return " ".join(value.split())
    _fail(f"{path}.requires_approval", "must be true, false or a short reason")
    return ""


def load_report(data: object) -> Report:
    """Validate raw JSON data and return a normalised, ranked report."""
    root = _object(data, "report")
    warnings: list = []
    version = root.get("schema_version", 1)
    if version not in (1, SCHEMA_VERSION):
        _fail("report.schema_version", f"must be 1 or {SCHEMA_VERSION} (got {version!r})")
    _unknown(root, ROOT_KEYS, "report", warnings)
    project = _text(root, "project", "report", required=True)
    phase = root.get("phase", "audit")
    phase = LEGACY_PHASES.get(phase, phase)
    if phase not in PHASES:
        _fail("report.phase", f"must be one of {', '.join(PHASES)} (got {root.get('phase')!r})")
    updated = _text(root, "updated_at", "report")
    updated = _format_timestamp(updated, "report.updated_at") if updated else ""

    for key in ("flows", "findings", "coverage", "limitations"):
        if not isinstance(root.get(key, []), list):
            _fail(f"report.{key}", "must be an array")

    flows = []
    flow_names: dict = {}  # id or name -> display name
    for index, raw in enumerate(root.get("flows", [])):
        path = f"flows[{index}]"
        item = _object(raw, path)
        _unknown(item, FLOW_KEYS, path, warnings)
        name = _text(item, "name", path, required=True)
        key = _text(item, "id", path) or name
        if key in flow_names:
            _fail(path, f"duplicates journey {key!r}; give each journey a unique id or name")
        flow_names[key] = name
        flow_names.setdefault(name, name)
        before = _sample(item, path, "baseline_ms", "before_samples", "baseline_runs_ms")
        after = _sample(item, path, "current_ms", "after_samples", "current_runs_ms")
        comparable = _flag(item, "comparable", path)
        if comparable and after is not None and (before is None or not before.n or not after.n):
            _fail(f"{path}.comparable", "is true but sample counts or runs are missing on one side")
        flows.append(Flow(
            key=key, name=name, app=_text(item, "app", path),
            statistic=_text(item, "statistic", path) or "Median", source=_text(item, "source", path),
            conditions=_text(item, "conditions", path), comparison=compare(before, after, comparable),
        ))

    findings = []
    seen: set = set()
    for index, raw in enumerate(root.get("findings", [])):
        path = f"findings[{index}]"
        item = _object(raw, path)
        _unknown(item, FINDING_KEYS, path, warnings)
        ident = _text(item, "id", path, required=True)
        if ident in seen:
            _fail(f"{path}.id", f"duplicates {ident!r}")
        seen.add(ident)
        impact = _choice(item, "impact", path, IMPACT)
        confidence = _choice(item, "confidence", path, CONFIDENCE)
        effort = _choice(item, "effort", path, EFFORT)
        risk = _choice(item, "risk", path, RISK)
        status = _choice(item, "status", path, STATUSES)
        evidence = _choice(item, "evidence_kind", path, EVIDENCE, default="hypothesis")
        layer = item.get("layer")
        if layer is not None and layer not in LAYERS:
            _fail(f"{path}.layer", f"must be one of {', '.join(LAYERS)} (got {layer!r})")
        signal = _text(item, "signal", path)
        verification = _text(item, "verification", path)
        if status == "verified" and not verification:
            _fail(path, "cannot be 'verified' without a verification statement")
        if evidence == "measured" and not signal:
            _fail(f"{path}.signal", "must describe the measured signal when evidence_kind is 'measured'")
        if status in ("implemented", "verified") and evidence == "hypothesis":
            warnings.append(f"{path} is {status} but its evidence is still a hypothesis")
        journeys = item.get("journeys", [])
        if not isinstance(journeys, list) or any(not isinstance(j, str) or not j.strip() for j in journeys):
            _fail(f"{path}.journeys", "must be an array of journey ids or names")
        for journey in journeys:
            if journey not in flow_names:
                warnings.append(f"{path}.journeys refers to unknown journey {journey!r}")
        before = _sample(item, path, "before_ms", "before_samples", "before_runs_ms")
        after = _sample(item, path, "after_ms", "after_samples", "after_runs_ms")
        comparable = _flag(item, "comparable", path)
        if comparable and after is not None and (before is None or not before.n or not after.n):
            _fail(f"{path}.comparable", "is true but sample counts or runs are missing on one side")
        findings.append(Finding(
            id=ident, title=_text(item, "title", path, required=True), app=_text(item, "app", path),
            layer=layer or "", category=_text(item, "category", path), impact=impact,
            confidence=confidence, effort=effort, risk=risk, status=status, evidence=evidence,
            signal=signal, location=_text(item, "location", path),
            proposed_change=_text(item, "proposed_change", path), verification=verification,
            notes=_text(item, "notes", path), approval=_approval(item, path),
            journeys=tuple(flow_names.get(j, " ".join(j.split())) for j in journeys),
            comparison=compare(before, after, comparable) if before or after else None,
            score=IMPACT_WEIGHT[impact] * CONFIDENCE_WEIGHT[confidence] * EASE_WEIGHT[effort] * SAFETY_WEIGHT[risk],
        ))

    coverage = []
    covered: set = set()
    for index, raw in enumerate(root.get("coverage", [])):
        path = f"coverage[{index}]"
        item = _object(raw, path)
        _unknown(item, COVERAGE_KEYS, path, warnings)
        layer = _choice(item, "layer", path, LAYERS)
        if layer in covered:
            _fail(f"{path}.layer", f"duplicates {layer!r}")
        covered.add(layer)
        coverage.append(CoverageItem(layer, _choice(item, "status", path, COVERAGE_STATUSES), _text(item, "note", path)))
    coverage.sort(key=lambda c: LAYERS.index(c.layer))

    limitations = []
    for index, note in enumerate(root.get("limitations", [])):
        if not isinstance(note, str) or not note.strip():
            _fail(f"limitations[{index}]", "must be a non-empty string")
        limitations.append(" ".join(note.split()))

    if phase == "complete" and any(f.status == "in_progress" for f in findings):
        warnings.append("report.phase is 'complete' but some findings are still in_progress")

    apps = []
    for name in [f.app for f in flows] + [f.app for f in findings]:
        if name and name not in apps:
            apps.append(name)

    return Report(
        project=project, scope=_text(root, "scope", "report"), environment=_text(root, "environment", "report"),
        updated=updated, phase=phase, demo=_flag(root, "demo", "report"),
        next_action=_text(root, "next_action", "report"), flows=tuple(flows),
        findings=tuple(sorted(findings, key=rank_key)), coverage=tuple(coverage),
        limitations=tuple(limitations), apps=tuple(apps), warnings=tuple(warnings),
    )


# ---------------------------------------------------------------------------
# Ranking and summary


def rank_key(finding: Finding) -> tuple:
    return (-finding.score, -IMPACT_WEIGHT[finding.impact], EVIDENCE_ORDER[finding.evidence], finding.id)


def open_findings(report: Report) -> list:
    return [f for f in report.findings if f.status in OPEN_STATUSES]


def next_action(report: Report) -> tuple:
    """(label, text, finding or None) for the single recommended next action."""
    if report.next_action:
        return ("Next", report.next_action, None)
    pending = open_findings(report)
    for status, label in (("in_progress", "Working on"), ("implemented", "Verify next")):
        match = next((f for f in pending if f.status == status), None)
        if match:
            return (label, match.title, match)
    match = next((f for f in pending if f.status == "identified"), None)
    if match:
        if match.approval:
            return ("Needs approval", match.title, match)
        label = {"measured": "Fix next", "inspected": "Measure next", "hypothesis": "Validate next"}[match.evidence]
        return (label, match.title, match)
    match = next((f for f in pending if f.status == "blocked"), None)
    if match:
        return ("Unblock", match.title, match)
    return ("Next", "No open bottleneck in this scope.", None)


def next_step(report: Report) -> tuple:
    """(label, text) for the single recommended next action."""
    return next_action(report)[:2]


def summary(report: Report) -> dict:
    compared = [f for f in report.flows if f.comparison.compared]
    return {
        "total": len(report.findings),
        "verified": sum(f.status == "verified" for f in report.findings),
        "high_open": sum(f.impact == "high" and f.status in OPEN_STATUSES for f in report.findings),
        "compared": len(compared),
        "faster": sum(f.comparison.verdict == "faster" for f in compared),
        "baselined": sum(f.comparison.before is not None for f in report.flows),
    }


def headline(stats: dict) -> list:
    """The three headline metrics as (value, label, tone); tone is colour only when it is earned."""
    if stats["compared"]:
        journeys = (f'{stats["faster"]}/{stats["compared"]}', "retested journeys faster",
                    "good" if stats["faster"] == stats["compared"] else "")
    else:
        journeys = (str(stats["baselined"]), "journey baselined" if stats["baselined"] == 1 else "journeys baselined", "")
    return [
        (str(stats["high_open"]), "high-impact open", "high" if stats["high_open"] else ""),
        (f'{stats["verified"]}/{stats["total"]}', "findings verified",
         "good" if stats["total"] and stats["verified"] == stats["total"] else ""),
        journeys,
    ]


# ---------------------------------------------------------------------------
# Formatting


def h(value: object) -> str:
    return escape(str(value), quote=True)


def fmt_ms(value: float | None) -> str:
    if value is None:
        return "—"
    if value >= 10000:
        return f"{value / 1000:.1f} s"
    if value >= 1000:
        return f"{value / 1000:.2f} s"
    if value >= 10:
        return f"{value:.0f} ms"
    return f"{value:.1f} ms"


def hms(value: float | None) -> str:
    """Escaped duration that never wraps between number and unit."""
    return h(fmt_ms(value)).replace(" ", "&nbsp;")


def delta_text(comparison: Comparison) -> str:
    if comparison.before is None or comparison.after is None:
        return ""
    difference = comparison.after.median - comparison.before.median
    return f"{'−' if difference < 0 else '+'}{fmt_ms(abs(difference))}"


def verdict_text(comparison: Comparison, with_delta: bool = False) -> str:
    verdict = comparison.verdict
    if verdict in ("faster", "slower"):
        text = f"{abs(comparison.pct):.0f}% {verdict}"
        return f"{text} ({delta_text(comparison)})" if with_delta else text
    return {
        "unchanged": "≈ unchanged",
        "within_noise": "Within noise",
        "insufficient_runs": "Too few runs",
        "not_comparable": "Not comparable",
        "awaiting_retest": "Awaiting retest",
        "no_baseline": "No baseline",
    }[verdict]


def sample_text(sample: Sample | None) -> str:
    if sample is None:
        return "not measured"
    parts = [fmt_ms(sample.median), f"n={sample.n}" if sample.n else "n unknown"]
    if sample.has_spread:
        parts.append(f"IQR {fmt_ms(sample.q1)}–{fmt_ms(sample.q3)}")
    return " · ".join(parts)


def comparison_text(comparison: Comparison) -> str:
    return (f"Before {sample_text(comparison.before)}. After {sample_text(comparison.after)}. "
            f"{verdict_text(comparison, with_delta=True)}.")


def plural(count: int, word: str) -> str:
    return f"{count} {word}{'' if count == 1 else 's'}"


def journeys_label(names: tuple) -> str:
    if not names:
        return ""
    return names[0] if len(names) == 1 else f"{names[0]} +{len(names) - 1}"


# ---------------------------------------------------------------------------
# HTML


STYLE = """
:root{color-scheme:light dark;--bg:#fff;--surface:#f3f6f4;--ink:#1c2a28;--muted:#56645f;--line:#dbe3df;
--accent:#1f5f5a;--good:#256b4f;--warn:#8a4a26;--bad:#a13d34;--track:#e6ece9;--before:#788a83;--select:#cfe5df;
--hh:#f4e5da;--hh-ink:#7a4222}
@media (prefers-color-scheme:dark){:root{--bg:#111816;--surface:#18211f;--ink:#e2eae7;--muted:#9fb0aa;
--line:#2a3532;--accent:#86cbc3;--good:#7fcca5;--warn:#eaa982;--bad:#f2948a;--track:#24302c;--before:#6b7d75;--select:#2b4a44;
--hh:#3b2a1f;--hh-ink:#f1c3a2}}
*{box-sizing:border-box}
::selection{background:var(--select);color:var(--ink)}
body{margin:0;background:var(--bg);color:var(--ink);font:.875rem/1.5 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;-webkit-text-size-adjust:100%}
main{max-width:58rem;margin:0 auto;padding:2rem 1.75rem 2.5rem}
h1,h2,h3,p{margin:0}h1{font-size:1.5rem;line-height:1.2;font-weight:700;letter-spacing:-.02em;overflow-wrap:anywhere}
h2{font-size:1rem;font-weight:600}h3{font-size:.875rem;font-weight:600}
code{font:.75rem/1.4 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;overflow-wrap:anywhere}
.sr-only{position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;clip:rect(0,0,0,0);white-space:nowrap;border:0}
.head{display:flex;justify-content:space-between;align-items:baseline;gap:1rem}
.scope,.env,.meta,.hint,.note,.foot{color:var(--muted)}.scope{margin-top:.25rem;overflow-wrap:anywhere}
.env{margin-top:.125rem;font-size:.75rem;overflow-wrap:anywhere}.meta{font-size:.75rem;text-align:right;flex-shrink:0}
.demo{margin-top:.5rem;font-size:.75rem;color:var(--warn)}
.metrics{display:flex;flex-wrap:wrap;gap:.375rem 1.75rem;margin:1.25rem 0 0;padding:.875rem 0;border-block:1px solid var(--line)}
.metric{display:flex;align-items:baseline;gap:.4375rem}
.metric dd{order:-1;margin:0;font-size:1.375rem;font-weight:700;font-variant-numeric:tabular-nums;letter-spacing:-.02em}
.metric dt{color:var(--muted)}.metric.high dd{color:var(--warn)}.metric.good dd{color:var(--good)}
.next{margin-top:.875rem;padding:.75rem .875rem;border:1px solid var(--line);border-radius:.5rem;background:var(--surface);display:flex;gap:.75rem;align-items:baseline}
.next h2{flex-shrink:0;font-size:.75rem;font-weight:700;letter-spacing:.04em;text-transform:uppercase;color:var(--accent)}
.next p{overflow-wrap:anywhere}.next strong{font-weight:600}
section.priorities{margin-top:1.5rem}
.block-head{display:flex;justify-content:space-between;align-items:baseline;gap:.625rem;margin-bottom:.75rem}.hint{font-size:.75rem}
summary{cursor:pointer;list-style:none}summary::-webkit-details-marker{display:none}
summary::before{content:"";display:inline-block;flex-shrink:0;width:0;height:0;margin-right:.5rem;vertical-align:.0625rem;
border-left:.3125rem solid var(--muted);border-block:.25rem solid transparent;transition:transform .15s ease-out}
details[open]>summary::before{transform:rotate(90deg)}
summary:focus-visible{outline:2px solid var(--accent);outline-offset:2px;border-radius:3px}
.top{list-style:none;margin:0;padding:0;border-top:1px solid var(--line)}.top>li{border-bottom:1px solid var(--line)}
.top summary{display:grid;grid-template-columns:.875rem 1.75rem minmax(0,1fr) auto;gap:.125rem .5rem;align-items:baseline;padding:.6875rem 0;min-height:2.75rem}
.top summary::before{margin:0;align-self:center}
.rank{color:var(--muted);font-variant-numeric:tabular-nums}.title{font-weight:600;overflow-wrap:anywhere}
.status{font-size:.75rem;color:var(--muted);white-space:nowrap}.status.in_progress,.status.implemented{color:var(--accent);font-weight:600}.status.blocked{color:var(--bad);font-weight:600}
.tags{grid-column:3/-1;display:flex;flex-wrap:wrap;gap:.25rem .75rem;font-size:.75rem;color:var(--muted)}
.tags .high{color:var(--warn);font-weight:600}.tags .approval{color:var(--warn)}.tags code{color:var(--muted)}
.top .facts{margin-left:3.625rem}
.visuals{display:grid;grid-template-columns:minmax(0,1.35fr) minmax(0,1fr);gap:1.75rem;margin-top:1.75rem}
.journeys{display:grid;gap:.875rem}
.journey-head{display:flex;flex-wrap:wrap;justify-content:space-between;align-items:baseline;gap:.125rem .625rem}
.journey-name{overflow-wrap:anywhere}.journey-name code{margin-left:.375rem}
.verdict{font-size:.75rem;font-weight:600;white-space:nowrap}.verdict.good{color:var(--good)}.verdict.bad{color:var(--bad)}
.bars{display:grid;grid-template-columns:3rem minmax(0,1fr) 4rem;gap:.25rem .625rem;align-items:center;margin-top:.3125rem;font-size:.75rem}
.bars span{color:var(--muted)}.bars b{font-weight:600;text-align:right;font-variant-numeric:tabular-nums}
.track{position:relative;height:.375rem;border-radius:.375rem;background:var(--track)}
.track i{position:absolute;top:0;bottom:0;left:0;border-radius:.375rem;background:var(--before)}.track i.after{background:var(--accent)}
.journey.within_noise .track i.after,.journey.unchanged .track i.after{background:var(--before)}
.track i.iqr{left:auto;top:-.1875rem;bottom:-.1875rem;border-radius:0;background:linear-gradient(var(--ink),var(--ink)) center/100% 1px no-repeat;border-inline:1px solid var(--ink);opacity:.55}
.matrix{width:100%;border-collapse:separate;border-spacing:.25rem;font-size:.75rem;table-layout:fixed}
.matrix th,.matrix .axis{font-weight:400;color:var(--muted)}.matrix thead th{padding-bottom:.125rem}
.matrix .axis{text-align:left}.matrix tbody th{text-align:left;width:4.25rem}
.matrix td{height:2rem;text-align:center;border-radius:.3125rem;background:var(--surface);color:var(--ink);font-weight:600;font-variant-numeric:tabular-nums}
.matrix thead td{background:none;height:auto}.matrix tr.high td.n{background:var(--hh);color:var(--hh-ink)}.matrix td.blank{background:none;border:1px solid var(--line)}
.note{margin-top:.375rem;font-size:.75rem}
details.more{border-bottom:1px solid var(--line)}details.more:first-of-type{margin-top:.5rem}
details.more>summary{padding:.75rem 0;min-height:2.75rem;color:var(--accent);font-weight:600}
details.more details.finding{border-top:1px solid var(--line)}
details.more details.finding>summary{display:flex;align-items:baseline;gap:.5rem;padding:.75rem 0;min-height:2.75rem}
.ftitle{flex:1;min-width:0;overflow-wrap:anywhere;font-weight:600}.ftitle .rank{margin-right:.375rem;font-weight:400}
.fmeta{flex-shrink:0;font-size:.75rem;color:var(--muted)}
.facts{margin:0 0 .875rem;padding:.75rem .875rem;border-radius:.5rem;background:var(--surface);display:grid;grid-template-columns:1fr 1fr;gap:.625rem 1.375rem}
.facts dt{font-size:.75rem;color:var(--muted)}.facts dd{margin:.125rem 0 0;overflow-wrap:anywhere;max-width:75ch}.facts .wide{grid-column:1/-1}
.kind{font-weight:600;margin-right:.375rem}
.group{margin:1rem 0 .25rem;font-size:.75rem;font-weight:700;color:var(--muted);text-transform:uppercase;letter-spacing:.05em}
.journey-detail h3{margin:.875rem 0 .375rem}
.plain{margin:.25rem 0 .875rem;padding-left:1.25rem;max-width:75ch}.plain li{margin:.25rem 0}.label{font-weight:600}.blind{color:var(--warn);font-weight:600}
.foot{margin-top:1.5rem;font-size:.75rem;max-width:75ch}
@media (max-width:680px){main{padding:1.375rem 1rem 2rem}.head{display:block}.meta{text-align:left;margin-top:.375rem}
.metrics{gap:.375rem 1.125rem}.visuals{grid-template-columns:1fr;gap:1.5rem}.next{display:block}.next h2{margin-bottom:.25rem}
.facts{grid-template-columns:1fr}.top .facts{margin-left:0}
.top summary{grid-template-columns:.875rem 1.5rem minmax(0,1fr)}.top .status{grid-column:3}}
@media (prefers-reduced-motion:reduce){summary::before{transition:none}}
@media print{main{max-width:none;padding:0}details::details-content{content-visibility:visible}summary::before{display:none}
.next,.facts{break-inside:avoid}}
"""

CSP = "default-src 'none'; style-src 'unsafe-inline'; img-src data:; base-uri 'none'; form-action 'none'"


def anchors_for(report: Report) -> dict:
    """Stable, unique fragment ids derived from finding ids (report.html#f-PERF-02)."""
    used: set = set()
    anchors = {}
    for finding in sorted(report.findings, key=lambda f: f.id):
        base = "f-" + (re.sub(r"[^A-Za-z0-9_.-]+", "-", finding.id).strip("-") or "finding")
        anchor, suffix = base, 2
        while anchor in used:
            anchor, suffix = f"{base}-{suffix}", suffix + 1
        used.add(anchor)
        anchors[finding.id] = anchor
    return anchors


def _location_html(location: str) -> str:
    return f"<code>{h(location)}</code>" if location and not re.search(r"\s", location) else h(location)


def _facts_html(finding: Finding, show_app: bool) -> str:
    rows = [("Evidence", f'<span class="kind">{h(EVIDENCE_LABEL[finding.evidence])}</span>{h(finding.signal or "not recorded")}', "wide")]
    if finding.comparison is not None:
        rows.append(("Observed", h(comparison_text(finding.comparison)), "wide"))
    if finding.verification:
        rows.append(("Verification", h(finding.verification), "wide"))
    if finding.location:
        rows.append(("Location", _location_html(finding.location), ""))
    change = "Change" if finding.status in ("implemented", "verified") else "Next step"
    rows.append((change, h(finding.proposed_change or "Investigate"), ""))
    rows.append(("Assessment", h(f"{finding.impact.capitalize()} impact · {finding.confidence.capitalize()} confidence · "
                                 f"{finding.effort.capitalize()} effort · {finding.risk.capitalize()} risk"), ""))
    area = " · ".join(x for x in ((finding.app if show_app else ""), LAYER_LABEL.get(finding.layer, "") or finding.category) if x)
    if area:
        rows.append(("Area", h(area), ""))
    if finding.approval:
        rows.append(("Approval", h(finding.approval), ""))
    if finding.journeys:
        rows.append(("Journeys", h(", ".join(finding.journeys)), ""))
    if finding.notes:
        rows.append(("Notes", h(finding.notes), "wide"))
    parts = []
    for key, value, cls in rows:
        attr = f' class="{cls}"' if cls else ""
        parts.append(f"<div{attr}><dt>{h(key)}</dt><dd>{value}</dd></div>")
    return f'<dl class="facts">{"".join(parts)}</dl>'


def _top_html(report: Report, ranks: dict, anchors: dict, show_app: bool) -> str:
    top = open_findings(report)[:3]
    if not top:
        return '<p class="note">Nothing open in this scope.</p>'
    items = []
    for finding in top:
        status = (f'<span class="status {h(finding.status)}">{h(STATUS_LABEL[finding.status])}</span>'
                  if finding.status != "identified" else "")
        tags = [f'<span>{h(EVIDENCE_LABEL[finding.evidence])}</span>',
                f'<span class="{h(finding.impact)}">{h(finding.impact.capitalize())} impact</span>',
                f'<code>{h(finding.id)}</code>']
        if finding.approval:
            tags.append('<span class="approval">Needs approval</span>')
        if show_app and finding.app:
            tags.append(f'<code>{h(finding.app)}</code>')
        if finding.journeys:
            tags.append(f'<span>Slows {h(journeys_label(finding.journeys))}</span>')
        items.append(
            f'<li><details class="finding" id="{anchors[finding.id]}"><summary>'
            f'<span class="rank">{ranks[finding.id]:02d}</span><span class="title">{h(finding.title)}</span>{status}'
            f'<span class="tags">{"".join(tags)}</span></summary>{_facts_html(finding, show_app)}</details></li>'
        )
    return f'<ol class="top" role="list">{"".join(items)}</ol>'


def _journeys_html(report: Report, show_app: bool) -> str:
    if not report.flows:
        return '<p class="note">No journey measured yet.</p>'
    visible = report.flows[:3]

    def shown(flow: Flow) -> tuple:
        c = flow.comparison
        return (c.before, c.after if c.after is not None and c.compared else None)

    values = [s.q3 if s.q3 is not None else s.median for flow in visible for s in shown(flow) if s is not None]
    peak = max(values + [1.0])

    def pct(value: float) -> str:
        return f"{max(0.0, min(100.0, 100 * value / peak)):.1f}%"

    def bar(sample: Sample | None, cls: str) -> str:
        if sample is None:
            return '<span class="track" aria-hidden="true"></span>'
        fill = f'<i class="{cls}" style="width:{max(3.0, min(100.0, 100 * sample.median / peak)):.1f}%"></i>'
        whisker = ""
        if sample.has_spread and sample.q3 > sample.q1:
            whisker = f'<i class="iqr" style="left:{pct(sample.q1)};width:{pct(sample.q3 - sample.q1)}"></i>'
        return f'<span class="track" aria-hidden="true">{fill}{whisker}</span>'

    rows = []
    for flow in visible:
        before, after = shown(flow)
        verdict = flow.comparison.verdict
        tone = {"faster": "good", "slower": "bad"}.get(verdict, "")
        app = f"<code>{h(flow.app)}</code>" if show_app and flow.app else ""
        # The after bar appears only for a valid comparison; the verdict explains every other case.
        before_row = (f'<span>Before</span>{bar(before, "")}<b>{hms(before.median)}</b>' if before is not None else
                      '<span>Before</span><span class="track" aria-hidden="true"></span>'
                      '<b><span aria-hidden="true">—</span><span class="sr-only">not measured</span></b>')
        after_row = f'<span>After</span>{bar(after, "after")}<b>{hms(after.median)}</b>' if after is not None else ""
        rows.append(
            f'<div class="journey {verdict}"><div class="journey-head"><span class="journey-name">{h(flow.name)}{app}</span>'
            f'<span class="verdict {tone}">{h(verdict_text(flow.comparison, with_delta=True))}</span></div>'
            f'<div class="bars">{before_row}{after_row}</div></div>'
        )
    extra = len(report.flows) - len(visible)
    more = f'<p class="note">+{plural(extra, "more journey")} in the details below.</p>' if extra else ""
    return f'<div class="journeys">{"".join(rows)}</div>{more}'


def _journeys_hint(report: Report) -> str:
    statistics_used = {flow.statistic for flow in report.flows}
    return statistics_used.pop() if len(statistics_used) == 1 else "Medians · see journey details"


def _matrix_html(report: Report, ranks: dict) -> str:
    pending = open_findings(report)
    if not pending:
        return '<p class="note">No open findings.</p>'
    head = ('<thead><tr><td></td><th scope="colgroup" colspan="3">Effort</th></tr>'
            '<tr><td class="axis">Impact</td><th scope="col">Small</th><th scope="col">Medium</th><th scope="col">Large</th></tr></thead>')
    rows = []
    for impact in IMPACT:
        cells = []
        for effort in EFFORT:
            numbers = [f"{ranks[f.id]:02d}" for f in pending if f.impact == impact and f.effort == effort]
            if numbers:
                text = " ".join(numbers[:3]) + (f" +{len(numbers) - 3}" if len(numbers) > 3 else "")
                cells.append(f'<td class="n">{text}</td>')
            else:
                cells.append('<td class="blank"><span class="sr-only">none</span></td>')
        rows.append(f'<tr class="{impact}"><th scope="row">{impact.capitalize()}</th>{"".join(cells)}</tr>')
    return ('<table class="matrix"><caption class="sr-only">Open findings by impact (rows) and effort (columns); '
            f'numbers are ranks</caption>{head}<tbody>{"".join(rows)}</tbody></table>')


def _other_findings_html(report: Report, ranks: dict, anchors: dict, show_app: bool) -> tuple:
    groups = (
        ("Open", open_findings(report)[3:]),
        ("Verified", [f for f in report.findings if f.status == "verified"]),
        ("Deferred", [f for f in report.findings if f.status == "deferred"]),
    )
    out, count = [], 0
    for name, items in groups:
        if not items:
            continue
        count += len(items)
        out.append(f'<h3 class="group">{h(name)} · {len(items)}</h3>')
        for finding in items:
            rank = f'<span class="rank">{ranks[finding.id]:02d}</span>' if finding.id in ranks else ""
            out.append(
                f'<details class="finding" id="{anchors[finding.id]}"><summary><span class="ftitle">{rank}{h(finding.title)}</span>'
                f'<span class="fmeta"><code>{h(finding.id)}</code> · {h(finding.impact.capitalize())} impact · '
                f'{h(STATUS_LABEL[finding.status])}</span></summary>{_facts_html(finding, show_app)}</details>'
            )
    return count, "".join(out)


def _journey_details_html(report: Report) -> str:
    blocks = []
    for flow in report.flows:
        c = flow.comparison
        rows = [("Before", sample_text(c.before)), ("After", sample_text(c.after)), ("Result", verdict_text(c, with_delta=True)),
                ("Statistic", flow.statistic)]
        if flow.conditions:
            rows.append(("Conditions", flow.conditions))
        if flow.source:
            rows.append(("Source", flow.source))
        app = f" ({flow.app})" if flow.app else ""
        body = "".join(f"<div><dt>{h(key)}</dt><dd>{h(value)}</dd></div>" for key, value in rows)
        blocks.append(f'<div class="journey-detail"><h3>{h(flow.name)}{h(app)}</h3><dl class="facts">{body}</dl></div>')
    return "".join(blocks)


def _notes(report: Report) -> tuple:
    """(summary label, HTML) for coverage and limitations; blind spots first."""
    blind = [c for c in report.coverage if c.status in ("not_observable", "skipped")]
    checked = [c for c in report.coverage if c.status == "checked"]
    skipped_layers = [c for c in report.coverage if c.status == "not_applicable"]
    out = []
    if blind:
        out.append('<h3 class="group">Blind spots</h3><ul class="plain">' + "".join(
            f'<li><span class="blind">{h(LAYER_LABEL[c.layer])}: {h(COVERAGE_LABEL[c.status].lower())}</span>'
            f'{(". " + h(c.note)) if c.note else ""}</li>' for c in blind) + "</ul>")
    if checked or skipped_layers:
        items = []
        if checked:
            items.append(f'<li><span class="label">Checked:</span> {h(", ".join(LAYER_LABEL[c.layer] for c in checked))}</li>')
        if skipped_layers:
            items.append(f'<li><span class="label">Not applicable:</span> {h(", ".join(LAYER_LABEL[c.layer] for c in skipped_layers))}</li>')
        items += [f'<li><span class="label">{h(LAYER_LABEL[c.layer])}:</span> {h(c.note)}</li>' for c in checked + skipped_layers if c.note]
        out.append('<h3 class="group">Covered</h3><ul class="plain">' + "".join(items) + "</ul>")
    if report.limitations:
        out.append('<h3 class="group">Limitations</h3><ul class="plain">' + "".join(f"<li>{h(n)}</li>" for n in report.limitations) + "</ul>")
    parts = []
    if len(blind) == 1:
        parts.append(f"{LAYER_SHORT[blind[0].layer]} {COVERAGE_LABEL[blind[0].status].lower()}")
    elif blind:
        parts.append(plural(len(blind), "blind spot"))
    if report.limitations:
        parts.append(plural(len(report.limitations), "note"))
    return "Coverage & limitations" + (f" · {' · '.join(parts)}" if parts else ""), "".join(out)


def render_html(report: Report) -> str:
    stats = summary(report)
    show_app = len(report.apps) > 1
    ranks = {f.id: i for i, f in enumerate(open_findings(report), 1)}
    anchors = anchors_for(report)
    label, action, target = next_action(report)
    hint = ""
    if target is not None and label == "Needs approval" and target.approval != "Required":
        hint = f' <span class="hint">{h(target.approval)}</span>'
    elif target is not None and target.journeys:
        hint = f' <span class="hint">Then retest {h(", ".join(target.journeys))}.</span>'
    metric_html = "".join(f'<div class="metric {cls}"><dt>{h(text)}</dt><dd>{h(value)}</dd></div>'
                          for value, text, cls in headline(stats))
    meta = f"Performance report · {PHASE_LABEL[report.phase]} phase · {report.updated or 'undated snapshot'}"
    scope = f'<p class="scope">{h(report.scope)}</p>' if report.scope else ""
    env = f'<p class="env">{h(report.environment)}</p>' if report.environment else ""
    demo = '<p class="demo">Illustrative demo · synthetic data, no application was audited</p>' if report.demo else ""
    other_count, other_html = _other_findings_html(report, ranks, anchors, show_app)
    others = (f'<details class="more" id="other-findings"><summary>Other findings · {other_count}</summary>{other_html}</details>'
              if other_count else "")
    journey_details = (f'<details class="more"><summary>Journey details · {len(report.flows)}</summary>'
                       f'{_journey_details_html(report)}</details>' if report.flows else "")
    notes_label, notes_html = _notes(report)
    notes = f'<details class="more"><summary>{h(notes_label)}</summary>{notes_html}</details>' if notes_html else ""
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="{CSP}">
<meta name="referrer" content="no-referrer">
<meta name="color-scheme" content="light dark">
<meta name="generator" content="ZeroLag render_report {RENDERER_VERSION}">
<title>Performance · {h(report.project)}</title>
<style>{STYLE}</style>
</head>
<body>
<main>
<header class="head"><div><h1>{h(report.project)}</h1>{scope}{env}{demo}</div><p class="meta">{h(meta)}</p></header>
<section aria-labelledby="next-title">
<dl class="metrics">{metric_html}</dl>
<div class="next"><h2 id="next-title">{h(label)}</h2><p><strong>{h(action)}</strong>{hint}</p></div>
</section>
<section class="priorities" aria-labelledby="top-title"><div class="block-head"><h2 id="top-title">Top bottlenecks</h2><span class="hint">Highest expected return first</span></div>{_top_html(report, ranks, anchors, show_app)}</section>
<div class="visuals">
<section aria-labelledby="journeys-title"><div class="block-head"><h2 id="journeys-title">Journeys</h2><span class="hint">{h(_journeys_hint(report))}</span></div>{_journeys_html(report, show_app)}</section>
<section aria-labelledby="map-title"><div class="block-head"><h2 id="map-title">Priority map</h2><span class="hint">Open findings by rank</span></div>{_matrix_html(report, ranks)}</section>
</div>
{others}
{journey_details}
{notes}
<p class="foot">Snapshot, not live monitoring. Ranked by impact × confidence × ease × safety. Gains are never added across findings.</p>
</main>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# Markdown


def md(value: object) -> str:
    text = " ".join(str(value).split())
    return text.replace("\\", "\\\\").replace("|", "\\|").replace("<", "&lt;").replace(">", "&gt;")


def render_markdown(report: Report) -> str:
    stats = summary(report)
    label, action, target = next_action(report)
    show_app = len(report.apps) > 1
    metrics = " · ".join(f"{value} {text}" for value, text, _ in headline(stats))
    lines = [f"# Performance · {md(report.project)}", ""]
    if report.demo:
        lines += ["> **Demo only.** Synthetic findings and timings; no application was audited.", ""]
    context = [f"**{PHASE_LABEL[report.phase]} phase**", report.updated or "undated snapshot"]
    if report.scope:
        context.append(md(report.scope))
    lines += [" · ".join(context), ""]
    if report.environment:
        lines += [f"Measured on: {md(report.environment)}", ""]
    retest = f" Then retest {md(', '.join(target.journeys))}." if target is not None and target.journeys else ""
    sentence = md(action).rstrip(".") + "."
    lines += [f"**{metrics}**", "", f"**{label}:** {sentence}{retest}", "", "## Top bottlenecks", ""]
    ranks = {f.id: i for i, f in enumerate(open_findings(report), 1)}
    top = open_findings(report)[:3]
    for finding in top:
        extra = [EVIDENCE_LABEL[finding.evidence], f"{finding.impact.capitalize()} impact", STATUS_LABEL[finding.status], f"`{md(finding.id)}`"]
        if show_app and finding.app:
            extra.append(f"`{md(finding.app)}`")
        if finding.approval:
            extra.append("needs approval")
        if finding.journeys:
            extra.append(f"slows {md(journeys_label(finding.journeys))}")
        if finding.location:
            extra.append(f"`{md(finding.location)}`")
        lines.append(f"{ranks[finding.id]}. **{md(finding.title)}** · {' · '.join(extra)}")
    if not top:
        lines.append("Nothing open in this scope.")
    lines += ["", "## Journeys", "", "| Journey | Before | After | Result |", "|---|---:|---:|---|"]
    for flow in report.flows:
        c = flow.comparison
        after = fmt_ms(c.after.median) if c.after is not None and c.compared else "—"
        before = fmt_ms(c.before.median) if c.before is not None else "—"
        name = f"{flow.name} ({flow.app})" if show_app and flow.app else flow.name
        lines.append(f"| {md(name)} | {before} | {after} | {verdict_text(c, with_delta=True)} |")
    if not report.flows:
        lines.append("| No journey measured yet | — | — | — |")
    if report.flows:
        statistics_used = sorted({flow.statistic for flow in report.flows})
        lines += ["", f"Statistic: {md('; '.join(statistics_used))}."]
    others = ([f for f in open_findings(report)[3:]] + [f for f in report.findings if f.status == "verified"]
              + [f for f in report.findings if f.status == "deferred"])
    if others:
        lines += ["", f"<details><summary>Other findings ({len(others)})</summary>", "",
                  "| Rank | Id | Impact | Evidence | Status | Finding |", "|---:|---|---|---|---|---|"]
        for finding in others:
            rank = str(ranks[finding.id]) if finding.id in ranks else ""
            lines.append(f"| {rank} | `{md(finding.id)}` | {finding.impact.capitalize()} | {EVIDENCE_LABEL[finding.evidence]} | "
                         f"{STATUS_LABEL[finding.status]} | {md(finding.title)} |")
        lines += ["", "</details>"]
    notes = [f"{LAYER_LABEL[c.layer]}: {COVERAGE_LABEL[c.status].lower()}" + (f". {c.note}" if c.note else "")
             for c in report.coverage if c.status in ("not_observable", "skipped")] + list(report.limitations)
    if notes:
        lines += ["", f"<details><summary>Blind spots and limitations ({len(notes)})</summary>", ""]
        lines += [f"- {md(note)}" for note in notes]
        lines += ["", "</details>"]
    lines += ["", "Gains are never added across findings. Full evidence: the source JSON and the HTML report.", ""]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI


def write_atomic(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with open(temporary, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def protect_output_dir(directory: Path) -> None:
    """Keep the default .zerolag/ artifact folder out of Git without touching the app's .gitignore."""
    ignore = directory / ".gitignore"
    if directory.name == ".zerolag" and not ignore.exists():
        write_atomic(ignore, "# Created by ZeroLag: audit artifacts stay local.\n*\n")


def main(argv: list | None = None) -> int:
    parser = argparse.ArgumentParser(description="Render a ZeroLag findings JSON into HTML and Markdown reports.")
    parser.add_argument("input", nargs="?", type=Path, help=f"findings JSON (default: {DEFAULT_INPUT})")
    parser.add_argument("--input", dest="input_flag", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--out-dir", type=Path, help="output folder for report.html and report.md (default: the input's folder)")
    parser.add_argument("--html", type=Path, help="explicit HTML output path")
    parser.add_argument("--markdown", type=Path, help="explicit Markdown output path")
    parser.add_argument("--check", action="store_true", help="validate only; write nothing")
    parser.add_argument("--strict", action="store_true", help="treat warnings as errors")
    args = parser.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    source = args.input_flag or args.input or DEFAULT_INPUT
    try:
        report = load_report(json.loads(source.read_text(encoding="utf-8")))
    except FileNotFoundError:
        print(f"error: {source} not found. Create it first (see references/report-schema.md).", file=sys.stderr)
        return 1
    except json.JSONDecodeError as error:
        print(f"error: {source} is not valid JSON: {error}", file=sys.stderr)
        return 1
    except (OSError, ReportError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    for warning in report.warnings:
        print(f"warning: {warning}", file=sys.stderr)
    if args.strict and report.warnings:
        print(f"error: {plural(len(report.warnings), 'warning')} with --strict", file=sys.stderr)
        return 1
    stats = summary(report)
    if args.check:
        print(f"OK: {source} is valid ({plural(stats['total'], 'finding')}, {plural(len(report.flows), 'journey')}).")
        return 0
    out_dir = args.out_dir or source.parent
    html_path = args.html or out_dir / "report.html"
    markdown_path = args.markdown or out_dir / "report.md"
    try:
        write_atomic(html_path, render_html(report))
        write_atomic(markdown_path, render_markdown(report))
        for directory in {html_path.parent, markdown_path.parent}:
            protect_output_dir(directory)
    except OSError as error:
        print(f"error: cannot write report: {error}", file=sys.stderr)
        return 1
    label, action = next_step(report)
    print(f"ZeroLag · {PHASE_LABEL[report.phase]} phase · " + " · ".join(f"{v} {t}" for v, t, _ in headline(stats)))
    print(f"HTML:     {html_path.resolve()}")
    print(f"Markdown: {markdown_path.resolve()}")
    print(f"{label}: {action}")
    for position, finding in enumerate(open_findings(report)[:3], 1):
        print(f"  {position}. {finding.impact.upper()} · {finding.id} · {finding.title} "
              f"({STATUS_LABEL[finding.status].lower()}, {EVIDENCE_LABEL[finding.evidence].lower()})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
