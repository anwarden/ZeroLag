"""Structural and guardrail checks for the ZeroLag skill package."""

import re
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / ".claude/skills/zerolag/SKILL.md"
PLAYBOOK = ROOT / ".claude/skills/zerolag/references/performance-playbook.md"
PROTOCOL = ROOT / ".claude/skills/zerolag/references/measurement-protocol.md"
SCHEMA = ROOT / ".claude/skills/zerolag/references/report-schema.md"
README = ROOT / "README.md"


class SkillCoverageTests(unittest.TestCase):
    def test_entrypoint_and_install_paths(self):
        skill = SKILL.read_text(encoding="utf-8")
        readme = README.read_text(encoding="utf-8")
        self.assertTrue(skill.startswith("---\nname: zerolag\n"))
        self.assertIn("/zerolag", readme)
        self.assertIn(".claude/skills/zerolag/scripts/render_report.py", skill)
        self.assertTrue((ROOT / ".claude/skills/zerolag/scripts/render_report.py").is_file())

    def test_all_research_categories_present(self):
        playbook = PLAYBOOK.read_text(encoding="utf-8")
        headings = re.findall(r"^## (\d+)\. ", playbook, flags=re.MULTILINE)
        self.assertEqual(headings, [str(i) for i in range(1, 15)])
        for expected in (
            "useDeferredValue", "useTransition", "React Compiler",
            "Server Actions", "streaming", "cacheComponents",
            "N+1", "EXPLAIN", "Neon", "-pooler", "LCP", "INP",
            "Vercel", "Prisma", "tenant", "pagination",
            "Web Workers", "Abort", "cursor", "React Profiler",
            "idempotency", "scale-to-zero", "prefetch", "read-your-writes",
            "LoAF", "cold", "cache", "streaming",
        ):
            self.assertIn(expected, playbook)
        self.assertGreaterEqual(playbook.count("https://"), 20)

    def test_safety_and_evidence_contract(self):
        skill = SKILL.read_text(encoding="utf-8")
        protocol = PROTOCOL.read_text(encoding="utf-8")
        schema = SCHEMA.read_text(encoding="utf-8")
        self.assertIn("Do not push, deploy", skill)
        self.assertIn("Regressions are shown", protocol)
        self.assertIn("comparable: true", protocol)
        self.assertIn("evidence_kind", schema)
        self.assertIn("PERFORMANCE_FINDINGS.json", skill)
        self.assertIn("one-screen", skill)
        self.assertIn("14 categories", skill)
        self.assertIn("measured", skill)
        self.assertIn("cross-user", skill)
        self.assertIn("/zerolag", README.read_text(encoding="utf8"))


    def test_renamed_skill_has_no_legacy_invocation(self):
        skill = SKILL.read_text(encoding="utf8").lower()
        self.assertNotIn("rocketspeed", skill)
        self.assertNotIn("fullstack-latency-optimizer", skill)
        self.assertIn("name: zerolag", skill)
        self.assertIn("/zerolag", README.read_text(encoding="utf8"))

    def test_version_safe_and_end_to_end_guidance(self):
        playbook = PLAYBOOK.read_text(encoding="utf8")
        for marker in (
            "Next.js 16", "Prisma v6", "Prisma v7", "Cache Components",
            "scheduler.yield()", "React Compiler", "AbortController",
            "PostgreSQL", "connection", "tenant", "budget", "Worker",
            "EXPLAIN (ANALYZE, BUFFERS)", "Vercel", "Neon",
            "idempotency", "INP", "durable", "latency", "mobile",
        ):
            self.assertIn(marker, playbook)
        self.assertGreater(playbook.count("Sources:"), 12)


if __name__ == "__main__":
    unittest.main()
