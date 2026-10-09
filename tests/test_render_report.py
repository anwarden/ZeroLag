"""Renderer tests: validation, honest comparisons, ranking, rendering, determinism and CLI."""

from __future__ import annotations

import copy
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / ".claude/skills/zerolag/scripts/render_report.py"
DEMO = ROOT / "examples/demo-findings.json"
spec = importlib.util.spec_from_file_location("render_report", SCRIPT)
rr = importlib.util.module_from_spec(spec)
sys.modules["render_report"] = rr
spec.loader.exec_module(rr)


def demo() -> dict:
    return json.loads(DEMO.read_text(encoding="utf-8"))


def finding(**overrides) -> dict:
    base = {"id": "F", "title": "Finding", "impact": "medium", "confidence": "medium", "effort": "medium",
            "risk": "low", "status": "identified", "evidence_kind": "hypothesis"}
    base.update(overrides)
    return base


def minimal(**overrides) -> dict:
    data = {"project": "App", "findings": [], "flows": []}
    data.update(overrides)
    return data


def run_cli(*args: str, cwd: Path | None = None, env: dict | None = None) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True, cwd=cwd,
                          env={**os.environ, **(env or {})}, encoding="utf-8")


class GoldenAndDeterminism(unittest.TestCase):
    def test_examples_match_renderer_output(self):
        report = rr.load_report(demo())
        hint = ("Regenerate with: python3 .claude/skills/zerolag/scripts/render_report.py examples/demo-findings.json "
                "--html examples/demo-report.html --markdown examples/demo-report.md")
        self.assertEqual(rr.render_html(report), (ROOT / "examples/demo-report.html").read_text(encoding="utf-8"), hint)
        self.assertEqual(rr.render_markdown(report), (ROOT / "examples/demo-report.md").read_text(encoding="utf-8"), hint)

    def test_rendering_never_reads_the_clock(self):
        class Frozen:
            fromisoformat = staticmethod(rr.datetime.fromisoformat)

            @staticmethod
            def now(*_, **__):
                raise AssertionError("the renderer must not read the clock")

            utcnow = today = now

        with mock.patch.object(rr, "datetime", Frozen):
            report = rr.load_report(demo())
            rr.render_html(report)
            rr.render_markdown(report)

    def test_cli_output_is_byte_identical_across_runs(self):
        with tempfile.TemporaryDirectory() as temp:
            outputs = []
            for name in ("a", "b"):
                out = Path(temp) / name
                proc = run_cli(str(DEMO), "--out-dir", str(out))
                self.assertEqual(proc.returncode, 0, proc.stderr)
                outputs.append(((out / "report.html").read_bytes(), (out / "report.md").read_bytes()))
            self.assertEqual(outputs[0], outputs[1])

    def test_finding_order_in_input_does_not_change_output(self):
        shuffled = demo()
        shuffled["findings"].reverse()
        self.assertEqual(rr.render_html(rr.load_report(demo())), rr.render_html(rr.load_report(shuffled)))


class Statistics(unittest.TestCase):
    def verdict(self, before, after, comparable=True) -> str:
        def sample(value):
            if value is None:
                return None
            return rr.summarize(value) if isinstance(value, list) else rr.Sample(*value)
        return rr.compare(sample(before), sample(after), comparable).verdict

    def test_summarize_uses_median_and_inclusive_quartiles(self):
        sample = rr.summarize([5, 1, 4, 2, 3])
        self.assertEqual((sample.median, sample.n, sample.q1, sample.q3), (3, 5, 2, 4))
        single = rr.summarize([7])
        self.assertEqual((single.median, single.n, single.q1, single.q3), (7, 1, 7, 7))

    def test_separated_spreads_are_reported_as_changes(self):
        self.assertEqual(self.verdict([1000, 1010, 1020, 1030, 1040], [800, 810, 820, 830, 840]), "faster")
        self.assertEqual(self.verdict([800, 810, 820, 830, 840], [1000, 1010, 1020, 1030, 1040]), "slower")

    def test_overlapping_spreads_are_within_noise(self):
        self.assertEqual(self.verdict([1290, 1318, 1340, 1362, 1395], [1262, 1290, 1305, 1330, 1352]), "within_noise")

    def test_tiny_changes_are_unchanged(self):
        self.assertEqual(self.verdict((1340, 5), (1335, 5)), "unchanged")

    def test_too_few_runs_never_produce_a_claim(self):
        self.assertEqual(self.verdict([1000, 1001], [500, 501, 502]), "insufficient_runs")
        self.assertEqual(self.verdict((1000, 0), (500, 7)), "insufficient_runs")

    def test_summary_only_values_still_compare(self):
        self.assertEqual(self.verdict((1820, 7), (960, 7)), "faster")

    def test_missing_or_noncomparable_sides(self):
        self.assertEqual(self.verdict(None, (900, 5)), "no_baseline")
        self.assertEqual(self.verdict((900, 5), None), "awaiting_retest")
        self.assertEqual(self.verdict((900, 5), (400, 5), comparable=False), "not_comparable")
        self.assertEqual(self.verdict((0, 5), (400, 5)), "not_comparable")

    def test_texts(self):
        self.assertEqual(rr.verdict_text(rr.compare(rr.Sample(1000, 5), rr.Sample(530, 5), True)), "47% faster")
        self.assertEqual([rr.fmt_ms(v) for v in (None, 4.25, 960, 1820, 12345)],
                         ["—", "4.2 ms", "960 ms", "1.82 s", "12.3 s"])


class Ranking(unittest.TestCase):
    def test_demo_top_three(self):
        report = rr.load_report(demo())
        self.assertEqual([f.id for f in rr.open_findings(report)[:3]], ["PERF-02", "PERF-03", "PERF-04"])

    def test_expected_return_beats_raw_impact_when_evidence_is_weak(self):
        report = rr.load_report(minimal(findings=[
            finding(id="risky", impact="high", confidence="low", effort="large", risk="high"),
            finding(id="quick", impact="medium", confidence="high", effort="small", risk="low",
                    evidence_kind="measured", signal="trace"),
        ]))
        self.assertEqual([f.id for f in report.findings], ["quick", "risky"])

    def test_evidence_breaks_ties(self):
        report = rr.load_report(minimal(findings=[
            finding(id="a"), finding(id="b", evidence_kind="measured", signal="trace"),
            finding(id="c", evidence_kind="inspected", signal="code"),
        ]))
        self.assertEqual([f.id for f in report.findings], ["b", "c", "a"])

    def test_blocked_findings_stay_visible(self):
        report = rr.load_report(minimal(findings=[finding(id="b", status="blocked", impact="high")]))
        self.assertEqual([f.id for f in rr.open_findings(report)], ["b"])


class NextStep(unittest.TestCase):
    def step(self, *items, **root) -> tuple:
        return rr.next_step(rr.load_report(minimal(findings=list(items), **root)))

    def test_each_workflow_state(self):
        measured = dict(evidence_kind="measured", signal="trace")
        self.assertEqual(self.step(finding(id="a", status="in_progress"), finding(id="b", **measured))[0], "Working on")
        self.assertEqual(self.step(finding(status="implemented", **measured))[0], "Verify next")
        self.assertEqual(self.step(finding(**measured))[0], "Fix next")
        self.assertEqual(self.step(finding(evidence_kind="inspected", signal="code"))[0], "Measure next")
        self.assertEqual(self.step(finding())[0], "Validate next")
        self.assertEqual(self.step(finding(requires_approval="index on a shared table", **measured))[0], "Needs approval")
        self.assertEqual(self.step(finding(status="blocked"))[0], "Unblock")
        self.assertEqual(self.step(finding(status="verified", verification="retested")),
                         ("Next", "No open bottleneck in this scope."))
        self.assertEqual(self.step(finding(), next_action="Measure cold starts on preview"),
                         ("Next", "Measure cold starts on preview"))


class Validation(unittest.TestCase):
    def test_rejected_inputs(self):
        flow = {"name": "Tabs", "baseline_ms": 900, "before_samples": 5}
        cases = [
            ([], "must be a JSON object"),
            ({"findings": []}, "project is required"),
            (minimal(project="  "), "must not be empty"),
            (minimal(phase="shipping"), "phase must be one of"),
            (minimal(schema_version=3), "schema_version"),
            (minimal(demo="yes"), "demo must be true or false"),
            (minimal(findings={}), "findings must be an array"),
            (minimal(findings=[finding(impact="huge")]), "impact must be one of"),
            (minimal(findings=[finding(status="done")]), "status must be one of"),
            (minimal(findings=[finding(evidence_kind="guess")]), "evidence_kind must be one of"),
            (minimal(findings=[finding(layer="cdn")]), "layer must be one of"),
            (minimal(findings=[finding(), finding()]), "duplicates 'F'"),
            (minimal(findings=[finding(status="verified")]), "without a verification statement"),
            (minimal(findings=[finding(evidence_kind="measured")]), "must describe the measured signal"),
            (minimal(findings=[finding(before_ms=-1)]), "non-negative number"),
            (minimal(findings=[finding(before_ms=float("nan"))]), "non-negative number"),
            (minimal(findings=[finding(before_ms=float("inf"))]), "non-negative number"),
            (minimal(findings=[finding(before_ms=True)]), "non-negative number"),
            (minimal(findings=[finding(before_ms=10, before_samples=2.5)]), "positive integer"),
            (minimal(findings=[finding(before_samples=5)]), "before_ms is missing"),
            (minimal(findings=[finding(before_runs_ms=[])]), "non-empty array"),
            (minimal(findings=[finding(before_runs_ms=[1, "2"])]), r"before_runs_ms\[1\]"),
            (minimal(findings=[finding(before_runs_ms=[1, 2, 3], before_ms=9)]), "contradicts the median"),
            (minimal(findings=[finding(before_runs_ms=[1, 2, 3], before_samples=4)]), "contradicts the 3 values"),
            (minimal(findings=[finding(before_ms=900, after_ms=400, after_samples=5, comparable=True)]), "missing on one side"),
            (minimal(findings=[finding(journeys="tabs")]), "journeys must be an array"),
            (minimal(findings=[finding(requires_approval=3)]), "requires_approval"),
            (minimal(flows=[flow, dict(flow)]), "duplicates journey"),
            (minimal(flows=[{"baseline_ms": 1}]), r"flows\[0\]\.name is required"),
            (minimal(flows=[{**flow, "current_ms": 500, "comparable": True}]), "missing on one side"),
            (minimal(coverage=[{"layer": "gpu", "status": "checked"}]), "layer must be one of"),
            (minimal(coverage=[{"layer": "react", "status": "maybe"}]), "status must be one of"),
            (minimal(coverage=[{"layer": "react", "status": "checked"}] * 2), "duplicates 'react'"),
            (minimal(limitations=["ok", 4]), r"limitations\[1\]"),
            (minimal(updated_at="yesterday"), "ISO 8601"),
            (minimal(updated_at="2026-10-09T10:00:00"), "must include a timezone"),
        ]
        for data, message in cases:
            with self.subTest(message=message), self.assertRaisesRegex(rr.ReportError, message):
                rr.load_report(data)

    def test_warnings_flag_typos_and_contradictions(self):
        report = rr.load_report(minimal(
            phase="complete", staus="typo", x_owner="team-a",
            findings=[finding(id="a", status="in_progress", journeys=["missing"], severity="high"),
                      finding(id="b", status="implemented")],
        ))
        text = "\n".join(report.warnings)
        self.assertIn("report.staus is not a known field", text)
        self.assertNotIn("x_owner", text)
        self.assertIn("findings[0].severity", text)
        self.assertIn("unknown journey 'missing'", text)
        self.assertIn("findings[1] is implemented but its evidence is still a hypothesis", text)
        self.assertIn("'complete' but some findings are still in_progress", text)

    def test_legacy_v1_files_still_render(self):
        legacy = {
            "project": "Legacy", "phase": "fixing",
            "flows": [{"name": "Tabs", "baseline_ms": 1820, "current_ms": 960, "before_samples": 7,
                       "after_samples": 7, "comparable": True, "statistic": "Median", "source": "trace"}],
            "findings": [{"id": "PERF-01", "title": "T", "category": "Next.js", "impact": "high", "confidence": "high",
                          "effort": "small", "risk": "low", "status": "identified", "evidence_kind": "measured",
                          "signal": "s", "before_ms": None, "after_ms": None, "before_samples": None,
                          "after_samples": None, "comparable": False}],
            "limitations": [],
        }
        report = rr.load_report(legacy)
        self.assertEqual(report.phase, "optimize")
        self.assertEqual(report.flows[0].comparison.verdict, "faster")
        self.assertEqual(report.warnings, ())
        for old, new in rr.LEGACY_PHASES.items():
            self.assertEqual(rr.load_report(minimal(phase=old)).phase, new)

    def test_timestamps(self):
        self.assertEqual(rr.load_report(minimal(updated_at="2026-10-09T16:05:00+02:00")).updated, "09 Oct 2026 · 14:05 UTC")
        self.assertEqual(rr.load_report(minimal(updated_at="2026-10-09T14:05:00.123Z")).updated, "09 Oct 2026 · 14:05 UTC")
        self.assertEqual(rr.load_report(minimal(updated_at="2026-10-09")).updated, "09 Oct 2026")


class HtmlContract(unittest.TestCase):
    def setUp(self):
        self.html = rr.render_html(rr.load_report(demo()))

    def test_one_screen_budget(self):
        self.assertEqual(self.html.count('<div class="metric'), 3)
        self.assertEqual(self.html.count('class="journeys"') + self.html.count('class="matrix"'), 2)
        top = re.search(r'<ol class="top" role="list">(.*?)</ol>', self.html, re.S).group(1)
        self.assertEqual(top.count("<li>"), 3)
        self.assertEqual(self.html.count('<div class="next">'), 1)
        self.assertEqual(self.html.count("<h1>"), 1)
        self.assertLess(self.html.index('id="top-title"'), self.html.index('id="journeys-title"'))  # bottlenecks before diagrams

    def test_offline_safe_and_accessible(self):
        self.assertNotIn("<script", self.html.lower())
        self.assertIsNone(re.search(r'(?:src|href)="(?:https?:)?//', self.html))
        self.assertIn("default-src 'none'", self.html)
        self.assertNotIn('role="text"', self.html)
        self.assertNotIn('role="progressbar"', self.html)
        self.assertIn('<caption class="sr-only">', self.html)
        self.assertEqual(self.html.count('scope="row"'), 3)
        self.assertIn('<h2 id="next-title">Working on</h2>', self.html)
        self.assertIn("prefers-color-scheme:dark", self.html)
        self.assertIn("@media (max-width:680px)", self.html)
        self.assertIn("details::details-content{content-visibility:visible}", self.html)  # evidence prints expanded
        for glyph in ("▸", "▾", "○", "●", "✓"):
            self.assertNotIn(glyph, self.html)  # disclosure markers are drawn in CSS, not Unicode glyphs
        self.assertNotIn(" px", self.html)

    def test_honest_journey_display(self):
        self.assertIn("47% faster (−860 ms)", self.html)
        self.assertIn('<div class="journey within_noise">', self.html)  # neutral after bar, same weight as wins
        self.assertIn("Awaiting retest", self.html)
        self.assertIn("<dd>1/2</dd>", self.html)  # one of two retested journeys is faster
        self.assertIn("<dt>retested journeys faster</dt>", self.html)
        self.assertIn('<div class="metric "><dt>findings verified</dt><dd>2/7</dd>', self.html)  # no green until complete
        awaiting = re.search(r'<div class="journey awaiting_retest">(.*?)</div></div>', self.html, re.S).group(1)
        self.assertNotIn("After", awaiting)  # a single baseline bar before any retest
        self.assertIn('Then retest Open employee details.', self.html)

    def test_all_evidence_is_kept_in_disclosures(self):
        self.assertEqual(len(re.findall(r'<details class="finding" id="f-PERF-0\d">', self.html)), 7)
        self.assertIn("six identical SELECTs", self.html)
        self.assertIn("Open employee details, Submit administrative form", self.html)
        self.assertIn('<span class="blind">PostgreSQL / Neon: not observable</span>', self.html)
        self.assertIn("Coverage &amp; limitations · Database not observable · 3 notes", self.html)
        self.assertIn("Needs approval", self.html)
        self.assertIn("<summary>Other findings · 4</summary>", self.html)
        verified = re.search(r'id="f-PERF-01">(.*?)</details>', self.html, re.S).group(1)
        self.assertIn("<dt>Change</dt>", verified)
        self.assertNotIn("<dt>Next step</dt>", verified)
        self.assertIn("<dt>Next step</dt>", re.search(r'id="f-PERF-04">(.*?)</details>', self.html, re.S).group(1))

    def test_noncomparable_after_value_is_hidden(self):
        data = demo()
        data["flows"][1]["current_runs_ms"] = [100, 101, 102, 103, 104]
        html = rr.render_html(rr.load_report(data))
        self.assertIn("Not comparable", html)
        self.assertNotIn("102&nbsp;ms</b>", html)

    def test_extra_journeys_move_to_details(self):
        data = demo()
        data["flows"] += [{"name": f"Journey {i}", "baseline_ms": 500 + i, "before_samples": 5} for i in range(4)]
        html = rr.render_html(rr.load_report(data))
        self.assertEqual(html.count('<div class="journey '), 3)
        self.assertIn("+4 more journeys", html)
        self.assertIn("Journey details · 7", html)

    def test_app_tags_only_for_multi_app_reports(self):
        single = demo()
        for item in single["findings"]:
            item["app"] = "web"
        self.assertNotIn("<code>web</code>", rr.render_html(rr.load_report(single)))
        multi = demo()
        multi["findings"][1]["app"] = "admin"
        multi["findings"][2]["app"] = "shared"
        html = rr.render_html(rr.load_report(multi))
        self.assertIn("<code>admin</code>", html)
        self.assertIn("<code>shared</code>", html)

    def test_escaping_every_text_field(self):
        attack = '"><script>alert(1)</script><img src=x onerror=alert(2)>'
        data = demo()
        data.update(project=attack, scope=attack, environment=attack, next_action=attack, limitations=[attack])
        data["flows"][0].update(name=attack, app=attack, source=attack, conditions=attack, statistic=attack)
        data["findings"][0].update(title=attack, location=attack, signal=attack, proposed_change=attack,
                                   verification=attack, notes=attack, app=attack, requires_approval=attack)
        data["coverage"][0]["note"] = attack
        report = rr.load_report(data)
        html = rr.render_html(report)
        markdown = rr.render_markdown(report)
        for needle in ("<script", "<img", '"><script', "onerror=alert(2)>"):
            self.assertFalse(needle in html, f"unescaped {needle!r} in HTML")
            self.assertFalse(needle in markdown, f"unescaped {needle!r} in Markdown")
        self.assertIn("&quot;&gt;&lt;script&gt;", html)

    def test_empty_report(self):
        report = rr.load_report(minimal())
        html = rr.render_html(report)
        self.assertIn("No open bottleneck in this scope.", html)
        self.assertIn("No journey measured yet.", html)
        self.assertIn("No open findings.", html)
        self.assertIn("undated snapshot", html)
        self.assertNotIn("Other findings", html)
        self.assertNotIn("<details", html)
        self.assertIn("No open bottleneck", rr.render_markdown(report))

    def test_stable_unique_anchors(self):
        report = rr.load_report(minimal(findings=[finding(id="A B"), finding(id="A-B"), finding(id="<x>")]))
        anchors = rr.anchors_for(report)
        self.assertEqual(sorted(anchors.values()), ["f-A-B", "f-A-B-2", "f-x"])
        self.assertEqual(len(set(anchors.values())), 3)

    def test_earned_colour_and_approval_hint(self):
        done = minimal(findings=[finding(status="verified", verification="retested")])
        self.assertIn('<div class="metric good"><dt>findings verified</dt><dd>1/1</dd>', rr.render_html(rr.load_report(done)))
        approval = minimal(findings=[finding(evidence_kind="measured", signal="trace", requires_approval="Index on a shared table")])
        html = rr.render_html(rr.load_report(approval))
        self.assertIn('<h2 id="next-title">Needs approval</h2>', html)
        self.assertIn('<span class="hint">Index on a shared table</span>', html)


class MarkdownContract(unittest.TestCase):
    def test_short_summary_with_progressive_disclosure(self):
        markdown = rr.render_markdown(rr.load_report(demo()))
        visible = markdown.split("<details>")[0]
        self.assertLessEqual(len(visible.strip().splitlines()), 32)
        self.assertIn("**Working on:** Remove repeated Prisma lookups from the details panel. Then retest Open employee details.", visible)
        self.assertEqual(len(re.findall(r"^\d\. \*\*", visible, re.M)), 3)
        self.assertIn("`PERF-02`", visible)
        self.assertIn("**2 high-impact open · 2/7 findings verified · 1/2 retested journeys faster**", visible)
        self.assertIn("<details><summary>Other findings (4)</summary>", markdown)
        self.assertIn("> **Demo only.**", markdown)
        self.assertNotIn("—", visible.replace("| — |", ""))  # no em-dash separators outside empty cells

    def test_table_cells_are_escaped(self):
        data = minimal(flows=[{"name": "A | B", "baseline_ms": 10, "before_samples": 3}])
        self.assertIn("A \\| B", rr.render_markdown(rr.load_report(data)))


class Cli(unittest.TestCase):
    def test_default_paths_and_self_ignoring_folder(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp) / ".zerolag"
            folder.mkdir()
            (folder / "findings.json").write_text(DEMO.read_text(encoding="utf-8"), encoding="utf-8")
            proc = run_cli(cwd=Path(temp))
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("Working on: Remove repeated Prisma lookups", proc.stdout)
            self.assertEqual(sorted(p.name for p in folder.iterdir()),
                             [".gitignore", "findings.json", "report.html", "report.md"])
            self.assertEqual((folder / ".gitignore").read_text(encoding="utf-8").splitlines()[-1], "*")
            (folder / ".gitignore").write_text("custom\n", encoding="utf-8")
            self.assertEqual(run_cli(cwd=Path(temp)).returncode, 0)
            self.assertEqual((folder / ".gitignore").read_text(encoding="utf-8"), "custom\n")

    def test_explicit_outputs_do_not_create_gitignore(self):
        with tempfile.TemporaryDirectory() as temp:
            html, markdown = Path(temp) / "out/a.html", Path(temp) / "out/a.md"
            proc = run_cli("--input", str(DEMO), "--html", str(html), "--markdown", str(markdown))
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertTrue(html.is_file() and markdown.is_file())
            self.assertFalse((Path(temp) / "out/.gitignore").exists())

    def test_check_mode_writes_nothing(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "findings.json"
            source.write_text(DEMO.read_text(encoding="utf-8"), encoding="utf-8")
            proc = run_cli(str(source), "--check")
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("is valid (7 findings, 3 journeys)", proc.stdout)
            self.assertEqual([p.name for p in Path(temp).iterdir()], ["findings.json"])

    def test_failures_are_explicit_and_leave_no_partial_files(self):
        with tempfile.TemporaryDirectory() as temp:
            missing = run_cli(cwd=Path(temp))
            self.assertEqual(missing.returncode, 1)
            self.assertIn("findings.json not found", missing.stderr)
            broken = Path(temp) / "broken.json"
            broken.write_text("{nope", encoding="utf-8")
            self.assertIn("is not valid JSON", run_cli(str(broken)).stderr)
            invalid = Path(temp) / "invalid.json"
            invalid.write_text(json.dumps(minimal(findings=[finding(impact="huge")])), encoding="utf-8")
            proc = run_cli(str(invalid))
            self.assertEqual(proc.returncode, 1)
            self.assertIn("findings[0].impact must be one of", proc.stderr)
            self.assertFalse((Path(temp) / "report.html").exists())
            blocked = Path(temp) / "dir.html"
            blocked.mkdir()
            proc = run_cli(str(DEMO), "--html", str(blocked), "--markdown", str(Path(temp) / "x.md"))
            self.assertEqual(proc.returncode, 1)
            self.assertIn("cannot write report", proc.stderr)
            self.assertEqual(list(Path(temp).glob(".*.tmp")), [])
            self.assertEqual(run_cli("--unknown-flag").returncode, 2)

    def test_strict_mode_turns_warnings_into_errors(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "findings.json"
            source.write_text(json.dumps(minimal(staus="typo")), encoding="utf-8")
            self.assertEqual(run_cli(str(source), "--check").returncode, 0)
            proc = run_cli(str(source), "--check", "--strict")
            self.assertEqual(proc.returncode, 1)
            self.assertIn("warning: report.staus", proc.stderr)

    def test_non_utf8_terminal_does_not_crash(self):
        with tempfile.TemporaryDirectory() as temp:
            proc = subprocess.run([sys.executable, str(SCRIPT), str(DEMO), "--out-dir", temp], capture_output=True,
                                  env={**os.environ, "PYTHONIOENCODING": "ascii"})
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn(b"HTML:", proc.stdout)


if __name__ == "__main__":
    unittest.main()
