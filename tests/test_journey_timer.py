"""journey_timer.mjs tests: pure functions (Node), CLI failure paths, and real browser runs on local fixtures.

Browser tests need ZEROLAG_PLAYWRIGHT_FROM: a folder whose node_modules contains playwright (or @playwright/test)
with its Chromium installed. They are skipped otherwise.
"""

from __future__ import annotations

import functools
import http.server
import importlib.util
import json
import os
from pathlib import Path
import random
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest

ROOT = Path(__file__).resolve().parents[1]
TIMER = ROOT / ".claude/skills/zerolag/scripts/journey_timer.mjs"
FIXTURES = ROOT / "tests/fixtures/timer"
NODE = shutil.which("node")
PLAYWRIGHT_FROM = os.environ.get("ZEROLAG_PLAYWRIGHT_FROM")
spec = importlib.util.spec_from_file_location("render_report_timer", ROOT / ".claude/skills/zerolag/scripts/render_report.py")
rr = importlib.util.module_from_spec(spec)
sys.modules["render_report_timer"] = rr
spec.loader.exec_module(rr)


def node_eval(source: str) -> object:
    proc = subprocess.run([NODE, "--input-type=module", "-e", source], capture_output=True, text=True, timeout=60)
    if proc.returncode != 0:
        raise AssertionError(proc.stderr)
    return json.loads(proc.stdout)


def timer(*args: str, cwd: Path | None = None, timeout: int = 240) -> subprocess.CompletedProcess:
    return subprocess.run([NODE, str(TIMER), *args], capture_output=True, text=True, cwd=cwd, timeout=timeout)


@unittest.skipUnless(NODE, "Node.js not installed")
class PureFunctions(unittest.TestCase):
    def test_statistics_match_the_python_renderer(self):
        rng = random.Random(7)
        samples = [[round(rng.uniform(100, 3000), 1) for _ in range(n)] for n in (1, 2, 3, 5, 7, 12)]
        js = node_eval(f"import {{ summarize }} from {json.dumps(TIMER.as_uri())};"
                       f"console.log(JSON.stringify({json.dumps(samples)}.map(summarize)));")
        for values, result in zip(samples, js):
            expected = rr.summarize(values)
            # Same interpolation; JavaScript rounds .x5 up for display while Python rounds to even.
            self.assertEqual(result["n"], expected.n)
            for key, value in (("median", expected.median), ("p25", expected.q1), ("p75", expected.q3)):
                self.assertLessEqual(abs(result[key] - value), 0.0501, key)

    def test_local_url_guard(self):
        urls = ["http://localhost:3000", "http://127.0.0.1:8080", "http://[::1]:3000", "http://app.localhost",
                "http://web.local", "https://staging.example.com", "https://my-app.vercel.app", "not a url"]
        result = node_eval(f"import {{ isLocalUrl }} from {json.dumps(TIMER.as_uri())};"
                           f"console.log(JSON.stringify({json.dumps(urls)}.map(isLocalUrl)));")
        self.assertEqual(result, [True, True, True, True, True, False, False, False])

    def test_spec_and_argument_validation(self):
        base = {"base_url": "http://localhost:3000"}
        journey = {"id": "tabs", "start": "/", "action": {"click": "#a"}, "ready": {"selector": "#b"}}
        cases = [
            ([], "spec must be a JSON object"),
            ({}, "base_url is required"),
            ({"base_url": "ftp://localhost"}, "http or https"),
            ({"base_url": "https://prod.example.com", "journeys": [journey]}, "refusing non-local"),
            ({**base, "journeys": []}, "non-empty array"),
            ({**base, "journeys": [{**journey, "id": "bad id"}]}, "must match"),
            ({**base, "journeys": [journey, journey]}, "duplicates"),
            ({**base, "journeys": [{**journey, "start": "https://evil.example.com/"}]}, "must stay on"),
            ({**base, "journeys": [{**journey, "action": {"click": "#a", "tap": "#a"}}]}, "exactly one"),
            ({**base, "journeys": [{**journey, "action": {"fill": "#q"}}]}, "string \"value\""),
            ({**base, "journeys": [{**journey, "action": {"goto": "https://evil.example.com"}}]}, "must stay on"),
            ({**base, "journeys": [{**journey, "ready": {}}]}, "ready needs"),
        ]
        js = node_eval(
            f"import {{ validateSpec, parseArgs }} from {json.dumps(TIMER.as_uri())};"
            f"const cases = {json.dumps([c for c, _ in cases])};"
            "const specs = cases.map(c => { try { validateSpec(c); return 'ok'; } catch (e) { return e.message; } });"
            "const remote = (() => { try { validateSpec({base_url: 'https://staging.example.com', journeys: [{id: 'a', start: '/',"
            " action: {click: '#a'}, ready: {selector: '#b'}}]}, {allowRemote: true}); return 'ok'; } catch (e) { return e.message; } })();"
            "const argv = [[], ['--spec'], ['--spec', 's', '--runs', '2'], ['--spec', 's', '--profile', 'tablet'],"
            " ['--spec', 's', '--cache', 'cold', '--cdp-url', 'http://127.0.0.1:9222'], ['--spec', 's', '--nope', 'x'],"
            " ['--spec', 's', '--cpu', '50'], ['--spec', 's', '--runs', '5', '--allow-remote']];"
            "const args = argv.map(a => { try { parseArgs(a); return 'ok'; } catch (e) { return e.message; } });"
            "console.log(JSON.stringify({specs, remote, args}));"
        )
        for (_, message), actual in zip(cases, js["specs"]):
            self.assertIn(message, actual)
        self.assertEqual(js["remote"], "ok")
        self.assertEqual(js["args"], ["--spec is required", "--spec needs a value", "--runs must be an integer >= 3",
                                      "--profile must be mobile or desktop", "--cache cold cannot be used with --cdp-url",
                                      "unknown option --nope", "--cpu must be between 1 and 20", "ok"])


@unittest.skipUnless(NODE, "Node.js not installed")
class CliFailures(unittest.TestCase):
    def test_usage_and_spec_errors_exit_2(self):
        self.assertEqual(timer("--help").returncode, 0)
        self.assertEqual(timer().returncode, 2)
        with tempfile.TemporaryDirectory() as temp:
            spec_path = Path(temp) / "spec.json"
            spec_path.write_text("{bad", encoding="utf-8")
            self.assertIn("cannot read", timer("--spec", str(spec_path)).stderr)
            spec_path.write_text(json.dumps({"base_url": "https://prod.example.com", "journeys": [
                {"id": "a", "start": "/", "action": {"click": "#a"}, "ready": {"selector": "#b"}}]}), encoding="utf-8")
            proc = timer("--spec", str(spec_path))
            self.assertEqual(proc.returncode, 2)
            self.assertIn("refusing non-local base_url prod.example.com", proc.stderr)

    def test_missing_playwright_is_explained_and_nothing_is_installed(self):
        with tempfile.TemporaryDirectory() as temp:
            spec_path = Path(temp) / "spec.json"
            spec_path.write_text(json.dumps({"base_url": "http://localhost:9", "journeys": [
                {"id": "a", "start": "/", "action": {"click": "#a"}, "ready": {"selector": "#b"}}]}), encoding="utf-8")
            env_free = {k: v for k, v in os.environ.items() if k != "ZEROLAG_PLAYWRIGHT_FROM"}
            proc = subprocess.run([NODE, str(TIMER), "--spec", str(spec_path), "--playwright-from", temp],
                                  capture_output=True, text=True, env=env_free, timeout=60)
            self.assertEqual(proc.returncode, 1)
            self.assertIn("never installs packages", proc.stderr)
            self.assertFalse((Path(temp) / "node_modules").exists())


class QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *_):
        pass


@unittest.skipUnless(NODE and PLAYWRIGHT_FROM, "set ZEROLAG_PLAYWRIGHT_FROM to run browser timing tests")
class BrowserRuns(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        handler = functools.partial(QuietHandler, directory=str(FIXTURES))
        cls.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.temp = tempfile.TemporaryDirectory()
        spec = json.loads((FIXTURES / "journeys.json").read_text(encoding="utf-8"))
        spec["base_url"] = f"http://127.0.0.1:{cls.server.server_address[1]}"
        cls.spec = Path(cls.temp.name) / "journeys.json"
        cls.spec.write_text(json.dumps(spec), encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.temp.cleanup()

    def measure(self, journey: str, *extra: str) -> dict:
        out = Path(self.temp.name) / f"{journey}-{len(extra)}.json"
        proc = timer("--spec", str(self.spec), "--journey", journey, "--runs", "3", "--warmup", "1",
                     "--playwright-from", PLAYWRIGHT_FROM, "--out", str(out), *extra, cwd=Path(self.temp.name))
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        return json.loads(out.read_text(encoding="utf-8"))

    def test_async_render_is_timed_from_input_to_ready(self):
        result = self.measure("tabs", "--profile", "desktop")
        self.assertEqual(len(result["runs_ms"]), 3)
        self.assertGreaterEqual(result["summary"]["median"], 290)
        self.assertLess(result["summary"]["median"], 1500)
        self.assertEqual(result["conditions"]["profile"], "desktop")
        self.assertFalse(result["conditions"]["authenticated"])
        self.assertEqual(result["failures"], [])

    def test_blocking_handler_shows_in_interaction_and_long_frames(self):
        result = self.measure("busy", "--profile", "mobile")
        self.assertGreaterEqual(result["interaction"]["median"], 100)
        self.assertGreater(result["blocking"]["median"], 0)
        self.assertEqual(result["conditions"]["cpu_slowdown"], 4)

    def test_hard_navigation_and_goto(self):
        clicked = self.measure("nav", "--profile", "desktop")
        self.assertGreaterEqual(clicked["summary"]["median"], 190)
        self.assertGreaterEqual(clicked["runs"][0]["requests"], 1)
        direct = self.measure("goto", "--profile", "desktop", "--cache", "cold")
        self.assertGreaterEqual(direct["summary"]["median"], 190)
        self.assertEqual(direct["conditions"]["cache"], "cold")

    def test_timer_output_feeds_the_report(self):
        before = self.measure("tabs", "--profile", "desktop")
        after = self.measure("busy", "--profile", "desktop")
        findings = {
            "schema_version": 2, "project": "Fixture", "phase": "verify", "updated_at": "2026-10-09T10:00:00Z",
            "flows": [{"id": "tabs", "name": before["journey"]["name"], "source": before["tool"],
                       "conditions": json.dumps(before["conditions"]), "baseline_runs_ms": before["runs_ms"],
                       "current_runs_ms": after["runs_ms"], "comparable": True}],
            "findings": [{"id": "F-1", "title": "Fixture finding", "impact": "high", "confidence": "high", "effort": "small",
                          "risk": "low", "status": "verified", "evidence_kind": "measured", "signal": "Timer runs",
                          "verification": "Same spec and profile, 3 runs each", "journeys": ["tabs"]}],
        }
        source = Path(self.temp.name) / "findings.json"
        source.write_text(json.dumps(findings), encoding="utf-8")
        proc = subprocess.run([sys.executable, str(ROOT / ".claude/skills/zerolag/scripts/render_report.py"), str(source), "--strict"],
                              capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        html = (Path(self.temp.name) / "report.html").read_text(encoding="utf-8")
        self.assertIn("faster", html)  # ~300 ms async render vs ~150 ms busy handler, separated spreads
        self.assertIn("n=3", html)

    def test_condition_already_true_is_refused(self):
        proc = timer("--spec", str(self.spec), "--journey", "already", "--runs", "3", "--playwright-from", PLAYWRIGHT_FROM,
                     cwd=Path(self.temp.name))
        self.assertEqual(proc.returncode, 2)
        self.assertIn("already true before the action", proc.stderr)
        self.assertIn('selector "#result": 1 match(es), 1 visible', proc.stderr)

    def test_hidden_ready_element_is_named_and_stops_early(self):
        out = Path(self.temp.name) / "hidden.json"
        proc = timer("--spec", str(self.spec), "--journey", "hidden", "--runs", "7", "--timeout", "1000",
                     "--playwright-from", PLAYWRIGHT_FROM, "--out", str(out), cwd=Path(self.temp.name))
        self.assertEqual(proc.returncode, 3, proc.stdout + proc.stderr)
        result = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(result["runs_ms"], [])
        self.assertIn("present but hidden", result["failures"][0])
        self.assertLessEqual(len(result["failures"]), 2)  # stopped, not 7 timeouts
        self.assertIn("identical failures", " ".join(result["notes"]))


if __name__ == "__main__":
    unittest.main()
