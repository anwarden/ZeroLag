"""Tests for the one-screen performance brief, preserving audit accuracy."""

import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / ".claude/skills/zerolag/scripts/render_report.py"
SAMPLE = ROOT / "examples/demo-findings.json"
spec = importlib.util.spec_from_file_location("render_report", SCRIPT)
report = importlib.util.module_from_spec(spec)
spec.loader.exec_module(report)


class ReportTests(unittest.TestCase):
    def setUp(self):
        self.data = json.loads(SAMPLE.read_text(encoding="utf-8"))

    def test_ranking_and_all_evidence_preserved(self):
        data = report.validate(self.data)
        ordered = report.rank(data["findings"])
        self.assertEqual(len(ordered), 7)
        self.assertEqual([x["impact"] for x in ordered], ["high"] * 3 + ["medium"] * 2 + ["low"] * 2)
        html = report.render_html(data)
        self.assertEqual(html.count('<details class="finding"'), 7)
        self.assertLess(html.index('Eliminate sequential data fetching'), html.index('Reduce nonessential JavaScript'))
        self.assertIn("Six identical select operations", html)

    def test_concise_initial_view_with_two_visuals(self):
        html = report.render_html(self.data)
        self.assertIn('role="progressbar"', html)
        self.assertIn('class="stage"', html)
        self.assertIn('class="heatmap"', html)
        self.assertIn('class="flow-bars"', html)
        self.assertIn('id="all-findings"', html)
        self.assertEqual(html.count('<div class="priority">'), 3)
        self.assertEqual(html.count('class="heat-cell'), 9)
        self.assertNotIn('class="visual-grid"', html)
        self.assertNotIn('data-filter=', html)
        self.assertNotIn('<script', html)
        self.assertIn('@media(max-width:680px)', html)

    def test_live_priority_and_progress_from_data(self):
        html = report.render_html(self.data)
        self.assertIn('2</b> high-impact open', html)
        self.assertIn('aria-valuenow="29"', html)
        self.assertIn('Working on', html)
        self.assertIn('Remove repeated Prisma lookups from the list API', html)
        self.assertEqual(report.count_summary(self.data)["high_open"], 2)

    def test_matrix_excludes_verified_and_deferred(self):
        html = report.matrix_html(report.rank(self.data["findings"]))
        self.assertIn('High impact, Small effort: 1 open findings', html)
        self.assertIn('High impact, Medium effort: 1 open findings', html)
        self.assertIn('Low impact, Small effort: 0 open findings', html)

    def test_small_changes_not_overclaimed(self):
        delta, tone = report.short_flow_change(self.data["flows"][2])
        self.assertEqual(delta, '≈ unchanged (0.4%)')
        self.assertEqual(tone, 'neutral')

    def test_noncomparable_current_hidden(self):
        flow = self.data["flows"][1]
        self.assertIsNone(report.comparable_pair(flow, "baseline_ms", "current_ms"))
        flow["current_ms"] = 123
        fragment = report.flow_row(flow, 2500)
        self.assertIn('Not comparable', fragment)
        self.assertIn('class="pending"', fragment)
        self.assertNotIn('123 ms', fragment)
        flow["comparable"] = True
        self.assertIsNone(report.comparable_pair(flow, "baseline_ms", "current_ms"))
        flow["after_samples"] = 1
        self.assertIsNotNone(report.comparable_pair(flow, "baseline_ms", "current_ms"))

    def test_all_flows_accessible_not_overloading_hero(self):
        self.data["flows"] += [dict(self.data["flows"][0]) for _ in range(5)]
        html = report.render_html(self.data)
        self.assertIn('5 more interaction(s)', html)
        self.assertEqual(html.count('<div class="flow">'), 8)

    def test_html_escaping(self):
        self.data["project"] = '<script>alert("x")</script>'
        self.data["findings"][0]["title"] = '<img src=x onerror=alert(1)>'
        self.data["findings"][0]["location"] = '</span><script>bad()</script>'
        self.data["flows"][0]["name"] = '<svg onload=alert(1)>'
        html = report.render_html(self.data)
        self.assertNotIn('<script', html)
        self.assertNotIn('<img src=x', html)
        self.assertNotIn('<svg onload', html)
        self.assertIn('&lt;img src=x onerror=alert(1)&gt;', html)

    def test_verified_status_needs_evidence(self):
        self.data["findings"][0]["verification"] = ""
        with self.assertRaisesRegex(ValueError, "cannot be 'verified'"):
            report.validate(self.data)

    def test_empty_report(self):
        data = {"project": "Empty", "phase": "baseline", "flows": [], "findings": [], "limitations": []}
        report.validate(data)
        self.assertIn('No open actionable issues', report.render_html(data))
        self.assertIn('No findings', report.render_markdown(data))

    def test_markdown_is_brief(self):
        md = report.render_markdown(self.data)
        self.assertLessEqual(len(md.splitlines()), 32)
        self.assertIn('## Opportunities', md)
        self.assertEqual(md.count('| High |'), 3)
        self.assertNotIn('### PERF-', md)

    def test_cli_creates_reports(self):
        with tempfile.TemporaryDirectory() as temp:
            html = Path(temp) / 'report.html'
            md = Path(temp) / 'report.md'
            proc = subprocess.run([sys.executable, str(SCRIPT), '--input', str(SAMPLE), '--html', str(html), '--markdown', str(md)], capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn('2 high-impact outstanding', proc.stdout)
            self.assertIn('Priority map', html.read_text(encoding='utf-8'))
            self.assertIn('## Opportunities', md.read_text(encoding='utf-8'))


if __name__ == '__main__':
    unittest.main()
