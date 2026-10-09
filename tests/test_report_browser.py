"""Browser rendering of the report at desktop and mobile sizes, light and dark (needs ZEROLAG_PLAYWRIGHT_FROM)."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
RENDERER = ROOT / ".claude/skills/zerolag/scripts/render_report.py"
CHECK = ROOT / "tests/browser_check.mjs"
PLAYWRIGHT_FROM = os.environ.get("ZEROLAG_PLAYWRIGHT_FROM")


@unittest.skipUnless(shutil.which("node") and PLAYWRIGHT_FROM, "set ZEROLAG_PLAYWRIGHT_FROM to run browser checks")
class ReportInBrowser(unittest.TestCase):
    def check(self, findings: dict) -> list:
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "findings.json"
            source.write_text(json.dumps(findings), encoding="utf-8")
            render = subprocess.run([sys.executable, str(RENDERER), str(source)], capture_output=True, text=True)
            self.assertEqual(render.returncode, 0, render.stderr)
            proc = subprocess.run(["node", str(CHECK), str(Path(temp) / "report.html")], capture_output=True, text=True,
                                  timeout=180, env={**os.environ, "ZEROLAG_PLAYWRIGHT_FROM": PLAYWRIGHT_FROM})
            results = json.loads(proc.stdout) if proc.stdout.strip().startswith("[") else []
            self.assertEqual(proc.returncode, 0, proc.stdout[-3000:] + proc.stderr[-2000:])
            return results

    def test_demo_report(self):
        results = self.check(json.loads((ROOT / "examples/demo-findings.json").read_text(encoding="utf-8")))
        self.assertEqual([r["view"] for r in results], ["desktop", "desktop-dark", "mobile", "mobile-dark"])

    def test_long_multi_app_content_still_fits(self):
        long_title = "Remove the sequential database reads that block the payroll export when many employees are selected " * 2
        findings = [{"id": f"P-{i}", "title": long_title, "app": "apps/admin" if i % 2 else "apps/web",
                     "impact": ("high", "medium", "low")[i % 3], "confidence": "medium", "effort": "small", "risk": "low",
                     "status": "identified", "evidence_kind": "inspected", "signal": "x" * 300,
                     "location": "apps/admin/src/features/payroll/export/very/long/path/to/the/module/that/does/the/work.ts"}
                    for i in range(12)]
        flows = [{"name": long_title, "app": "apps/web", "baseline_runs_ms": [12000, 12500, 13000, 13100, 14000],
                  "current_runs_ms": [900, 950, 990, 1000, 1100], "comparable": True}] * 1
        self.check({"project": "Workspace", "scope": "All apps · " + long_title, "flows": flows, "findings": findings,
                    "updated_at": "2026-10-09T10:00:00Z", "limitations": [long_title]})


if __name__ == "__main__":
    unittest.main()
