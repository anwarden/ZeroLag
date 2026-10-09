"""Skill package checks: frontmatter, budgets, links, consistency, portability and public-repo hygiene."""

from __future__ import annotations

import ast
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
SKILL_DIR = ROOT / ".claude/skills/zerolag"
SKILL = SKILL_DIR / "SKILL.md"
REFERENCES = sorted((SKILL_DIR / "references").glob("*.md"))
SCRIPTS = sorted((SKILL_DIR / "scripts").iterdir())
PORTABLE_KEYS = {"name", "description", "license", "compatibility", "metadata", "allowed-tools"}
spec = importlib.util.spec_from_file_location("render_report_pkg", SKILL_DIR / "scripts/render_report.py")
rr = importlib.util.module_from_spec(spec)
sys.modules["render_report_pkg"] = rr
spec.loader.exec_module(rr)


def frontmatter(text: str) -> tuple:
    if not text.startswith("---\n"):
        raise AssertionError("SKILL.md must start with YAML frontmatter")
    end = text.index("\n---\n", 4)
    data: dict = {}
    parent = None
    for line in text[4:end].splitlines():
        if not line.strip():
            continue
        key, _, value = line.strip().partition(":")
        value = value.strip()
        if line.startswith((" ", "\t")):
            data[parent][key] = value.strip('"')
        elif value:
            data[key] = value
        else:
            data[key], parent = {}, key
    return data, text[end + 5:]


def text_files() -> list:
    skip = {".git", "node_modules", "dist", "__pycache__", ".zerolag"}
    return [p for p in ROOT.rglob("*") if p.is_file() and not skip.intersection(p.relative_to(ROOT).parts)
            and p.suffix.lower() not in (".png", ".jpg", ".zip", ".pyc")]


def contrast(a: str, b: str) -> float:
    def luminance(color: str) -> float:
        color = color.lstrip("#")
        if len(color) == 3:
            color = "".join(c * 2 for c in color)
        channels = [int(color[i:i + 2], 16) / 255 for i in (0, 2, 4)]
        linear = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
        return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]
    high, low = sorted((luminance(a), luminance(b)), reverse=True)
    return (high + 0.05) / (low + 0.05)


class Frontmatter(unittest.TestCase):
    def setUp(self):
        self.meta, self.body = frontmatter(SKILL.read_text(encoding="utf-8"))

    def test_portable_fields_only(self):
        # claude.ai and the Skills API reject keys outside this set; Claude Code ignores unknown keys silently.
        self.assertTrue(set(self.meta) <= PORTABLE_KEYS, set(self.meta) - PORTABLE_KEYS)

    def test_name_and_description(self):
        self.assertEqual(self.meta["name"], SKILL_DIR.name)
        self.assertRegex(self.meta["name"], r"^[a-z0-9]+(-[a-z0-9]+)*$")
        self.assertLessEqual(len(self.meta["name"]), 64)
        description = self.meta["description"]
        self.assertLessEqual(len(description), 1024)
        self.assertNotRegex(description, r"[<>]")
        for trigger in ("Next.js", "Prisma", "Neon", "slow", "INP", "read-only", "audit"):
            self.assertIn(trigger, description)
        self.assertLessEqual(len(self.meta["compatibility"]), 500)

    def test_plain_yaml_scalars_have_no_colon_space(self):
        for key, value in self.meta.items():
            if isinstance(value, str) and not value.startswith(('"', "'")):
                self.assertNotIn(": ", value, f"{key} would break YAML parsing")


class Budgets(unittest.TestCase):
    def test_skill_body_is_small(self):
        text = SKILL.read_text(encoding="utf-8")
        self.assertLessEqual(len(text.splitlines()), 200)
        self.assertLessEqual(len(text.split()), 1600)

    def test_reference_files_have_contents_and_sources(self):
        for path in REFERENCES:
            text = path.read_text(encoding="utf-8")
            with self.subTest(reference=path.name):
                if len(text.splitlines()) > 100:
                    self.assertIn("## Contents", text)
                self.assertLessEqual(len(text.splitlines()), 260)
                if path.name != "report-schema.md":
                    self.assertGreaterEqual(text.count("](https://"), 6)
                    self.assertRegex(text, r"Verified against .* on \d{4}-\d{2}-\d{2}")


class SkillContent(unittest.TestCase):
    def setUp(self):
        self.text = SKILL.read_text(encoding="utf-8")

    def test_every_reference_and_script_is_reachable_from_skill(self):
        for path in REFERENCES:
            self.assertIn(f"](references/{path.name})", self.text, path.name)
        for path in SCRIPTS:
            if path.suffix in (".py", ".mjs"):
                self.assertIn(path.name, self.text + (SKILL_DIR / "references/measurement.md").read_text(encoding="utf-8"))

    def test_portable_script_paths(self):
        self.assertIn("${CLAUDE_SKILL_DIR}/scripts/render_report.py", self.text)
        self.assertNotIn(".claude/skills/zerolag/scripts", self.text)
        self.assertIn("folder that contains this SKILL.md", self.text)

    def test_workflow_and_guardrails(self):
        headings = re.findall(r"^## (\d)\. (\w+)", self.text, re.M)
        self.assertEqual([name for _, name in headings], ["Discover", "Measure", "Diagnose", "Rank", "Report", "Optimize", "Verify"])
        for phrase in ("Host rules win", "Read-only until the audit report exists", "explicit approval",
                       "Install nothing", "per-user data", "Never invent numbers", "In audit mode, stop here",
                       "one change at a time", "comparable: true"):
            self.assertIn(phrase, self.text)


class Links(unittest.TestCase):
    def test_relative_links_resolve(self):
        for path in [SKILL, *REFERENCES, ROOT / "README.md", ROOT / "CHANGELOG.md"]:
            text = path.read_text(encoding="utf-8")
            targets = re.findall(r"\]\(([^)\s]+)\)", text) + re.findall(r'<img[^>]+src="([^"]+)"', text)
            for target in targets:
                if re.match(r"^(https?:|mailto:|#)", target):
                    continue
                with self.subTest(file=path.name, target=target):
                    self.assertTrue((path.parent / target.split("#")[0]).exists())


class Consistency(unittest.TestCase):
    def test_one_version_everywhere(self):
        meta, _ = frontmatter(SKILL.read_text(encoding="utf-8"))
        version = meta["metadata"]["version"]
        plugin = json.loads((ROOT / ".claude-plugin/plugin.json").read_text(encoding="utf-8"))
        timer = re.search(r'export const VERSION = "([^"]+)"', (SKILL_DIR / "scripts/journey_timer.mjs").read_text(encoding="utf-8"))
        changelog = re.search(r"^## (\d+\.\d+\.\d+)", (ROOT / "CHANGELOG.md").read_text(encoding="utf-8"), re.M)
        self.assertRegex(version, r"^\d+\.\d+\.\d+$")
        self.assertEqual({version}, {plugin["version"], rr.RENDERER_VERSION, timer.group(1), changelog.group(1)})

    def test_schema_doc_covers_every_value_and_field(self):
        doc = (SKILL_DIR / "references/report-schema.md").read_text(encoding="utf-8")
        values = [*rr.IMPACT, *rr.CONFIDENCE, *rr.EFFORT, *rr.RISK, *rr.EVIDENCE, *rr.STATUSES, *rr.PHASES,
                  *rr.LAYERS, *rr.COVERAGE_STATUSES, *rr.ROOT_KEYS, *rr.FLOW_KEYS, *rr.FINDING_KEYS, *rr.COVERAGE_KEYS]
        missing = sorted({v for v in values if f"`{v}`" not in doc})
        self.assertEqual(missing, [])

    def test_schema_doc_example_is_valid(self):
        doc = (SKILL_DIR / "references/report-schema.md").read_text(encoding="utf-8")
        example = json.loads(re.search(r"```json\n(.*?)```", doc, re.S).group(1))
        report = rr.load_report(example)
        self.assertEqual(report.warnings, ())

    def test_plugin_and_marketplace_manifests(self):
        plugin = json.loads((ROOT / ".claude-plugin/plugin.json").read_text(encoding="utf-8"))
        market = json.loads((ROOT / ".claude-plugin/marketplace.json").read_text(encoding="utf-8"))
        self.assertEqual(plugin["name"], "zerolag")
        for path in plugin["skills"]:
            self.assertTrue((ROOT / path / "SKILL.md").is_file(), path)
        self.assertEqual([p["name"] for p in market["plugins"]], [plugin["name"]])
        self.assertEqual(market["plugins"][0]["source"], ".")
        self.assertIn("name", market["owner"])

    def test_codex_metadata(self):
        text = (SKILL_DIR / "agents/openai.yaml").read_text(encoding="utf-8")
        for key in ("display_name:", "short_description:", "default_prompt:", "allow_implicit_invocation:"):
            self.assertIn(key, text)

    @unittest.skipUnless(shutil.which("claude"), "Claude Code CLI not installed")
    def test_claude_plugin_validate(self):
        proc = subprocess.run(["claude", "plugin", "validate", str(ROOT), "--strict"], capture_output=True, text=True, timeout=120)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)


class Scripts(unittest.TestCase):
    def test_python_scripts_are_stdlib_only(self):
        # Scripts always run through `python3` or `node`: no reliance on the executable bit, which
        # browser uploads and Windows checkouts drop.
        stdlib = getattr(sys, "stdlib_module_names", None)
        for path in [p for p in SCRIPTS if p.suffix == ".py"] + [ROOT / "tools/package_skill.py"]:
            with self.subTest(script=path.name):
                source = path.read_text(encoding="utf-8")
                self.assertTrue(source.startswith("#!/usr/bin/env python3"))
                tree = ast.parse(source)
                if stdlib:
                    for node in ast.walk(tree):
                        names = [a.name for a in node.names] if isinstance(node, ast.Import) else (
                            [node.module] if isinstance(node, ast.ImportFrom) and node.module else [])
                        for name in names:
                            self.assertIn(name.split(".")[0], stdlib | {"__future__"}, name)

    def test_node_script_uses_builtins_only(self):
        path = SKILL_DIR / "scripts/journey_timer.mjs"
        source = path.read_text(encoding="utf-8")
        self.assertTrue(source.startswith("#!/usr/bin/env node"))
        for module in re.findall(r'from "([^"]+)"', source):
            self.assertTrue(module.startswith("node:"), module)
        if shutil.which("node"):
            proc = subprocess.run(["node", "--check", str(path)], capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0, proc.stderr)


class Hygiene(unittest.TestCase):
    def test_no_secrets_local_paths_or_private_data(self):
        patterns = {
            "credential URL": r"postgres(?:ql)?://[^\s:@/'\"]+:(?!fixture-password|\{)[^\s@/'\"]+@",
            "local path": r"(?:/Users/|/home/)[a-z][\w.-]+/|[A-Z]:\\\\Users\\\\",
            "token": r"\b(?:ghp_[A-Za-z0-9]{20,}|github_pat_\w{20,}|sk-[A-Za-z0-9]{20,}|xox[baprs]-[\w-]{10,}|AKIA[0-9A-Z]{16}|npm_[A-Za-z0-9]{30,})",
            "private key": r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
            "email": r"\b[\w.+-]+@(?!example\.(?:com|org)\b)(?!users\.noreply\.github\.com\b)(?:[\w-]+\.)+[A-Za-z]{2,}\b",
        }
        denylist = []
        if os.environ.get("ZEROLAG_DENYLIST"):
            denylist = [t.strip().lower() for t in Path(os.environ["ZEROLAG_DENYLIST"]).read_text(encoding="utf-8").splitlines() if t.strip()]
        for path in text_files():
            text = path.read_text(encoding="utf-8", errors="replace")
            for label, pattern in patterns.items():
                with self.subTest(file=str(path.relative_to(ROOT)), check=label):
                    self.assertIsNone(re.search(pattern, text), f"{label} in {path.relative_to(ROOT)}")
            lowered = text.lower()
            for term in denylist:  # whole words: a brand must not match a longer common word
                match = re.search(rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])", lowered)
                self.assertIsNone(match, f"denylisted term in {path.relative_to(ROOT)}")

    def test_artifacts_are_ignored(self):
        ignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
        for entry in (".zerolag/", "__pycache__/", "dist/", ".env", "PERFORMANCE_REPORT.html"):
            self.assertIn(entry, ignore)
        self.assertTrue(json.loads((ROOT / "examples/demo-findings.json").read_text(encoding="utf-8"))["demo"])


class ReportColors(unittest.TestCase):
    def test_text_contrast_meets_wcag_aa_in_both_themes(self):
        light = dict(re.findall(r"--([\w-]+):(#[0-9a-fA-F]{3,6})", rr.STYLE.split("@media (prefers-color-scheme:dark)")[0]))
        dark = {**light, **dict(re.findall(r"--([\w-]+):(#[0-9a-fA-F]{3,6})", rr.STYLE.split("@media (prefers-color-scheme:dark)")[1].split("}}")[0]))}
        text_pairs = [("ink", "bg"), ("muted", "bg"), ("muted", "surface"), ("accent", "bg"), ("accent", "surface"),
                      ("good", "bg"), ("warn", "bg"), ("bad", "bg"), ("hh-ink", "hh"), ("ink", "select")]
        graphic_pairs = [("before", "track"), ("accent", "track")]  # WCAG 1.4.11 non-text contrast
        for name, theme in (("light", light), ("dark", dark)):
            for (fg, bg), minimum in [(pair, 4.5) for pair in text_pairs] + [(pair, 3.0) for pair in graphic_pairs]:
                with self.subTest(theme=name, pair=(fg, bg)):
                    self.assertGreaterEqual(contrast(theme[fg], theme[bg]), minimum)


if __name__ == "__main__":
    unittest.main()
