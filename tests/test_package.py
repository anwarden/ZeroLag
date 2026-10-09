"""Packaging and installation tests: reproducible archive, safe idempotent installs, installed scripts run."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools/package_skill.py"
SKILL = ROOT / ".claude/skills/zerolag"
DEMO = ROOT / "examples/demo-findings.json"


def tool(*args: str, home: Path | None = None) -> subprocess.CompletedProcess:
    env = {**os.environ, **({"HOME": str(home), "USERPROFILE": str(home)} if home else {})}
    return subprocess.run([sys.executable, str(TOOL), *args], capture_output=True, text=True, env=env, encoding="utf-8")


def shipped() -> list:
    return sorted(p.relative_to(SKILL).as_posix() for p in SKILL.rglob("*")
                  if p.is_file() and "__pycache__" not in p.parts and p.name != ".DS_Store")


class Archive(unittest.TestCase):
    def test_build_is_reproducible_and_clean(self):
        with tempfile.TemporaryDirectory() as temp:
            first, second = Path(temp) / "a.zip", Path(temp) / "b.zip"
            self.assertEqual(tool("build", "--out", str(first)).returncode, 0)
            self.assertEqual(tool("build", "--out", str(second)).returncode, 0)
            self.assertEqual(hashlib.sha256(first.read_bytes()).digest(), hashlib.sha256(second.read_bytes()).digest())
            with zipfile.ZipFile(first) as archive:
                names = archive.namelist()
                self.assertEqual(names, sorted(names))
                self.assertEqual([n[len("zerolag/"):] for n in names], shipped())
                modes = {info.filename: (info.external_attr >> 16) & 0o777 for info in archive.infolist()}
                self.assertEqual(modes["zerolag/scripts/render_report.py"], 0o755)
                self.assertEqual(modes["zerolag/SKILL.md"], 0o644)
                archive.extractall(Path(temp) / "unzipped")
            proc = subprocess.run([sys.executable, str(Path(temp) / "unzipped/zerolag/scripts/render_report.py"), str(DEMO),
                                   "--out-dir", str(Path(temp) / "out")], capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0, proc.stderr)


class Install(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.app = self.base / "app"
        self.app.mkdir()
        self.target = self.app / ".claude/skills/zerolag"

    def tearDown(self):
        self.temp.cleanup()

    def test_project_install_is_idempotent_and_runnable(self):
        first = tool("install", "--project", str(self.app))
        self.assertEqual(first.returncode, 0, first.stderr)
        installed = sorted(p.relative_to(self.target).as_posix() for p in self.target.rglob("*") if p.is_file())
        self.assertEqual(installed, sorted(shipped() + [".zerolag-install.json"]))
        manifest = json.loads((self.target / ".zerolag-install.json").read_text(encoding="utf-8"))
        self.assertEqual(sorted(manifest["files"]), shipped())
        second = tool("install", "--project", str(self.app))
        self.assertEqual(second.returncode, 0)
        self.assertIn("Already up to date.", second.stdout)
        workdir = self.base / "elsewhere"
        workdir.mkdir()
        render = subprocess.run([sys.executable, str(self.target / "scripts/render_report.py"), str(DEMO), "--out-dir", str(workdir)],
                                capture_output=True, text=True, cwd=workdir)
        self.assertEqual(render.returncode, 0, render.stderr)
        discover = subprocess.run([sys.executable, str(self.target / "scripts/discover.py"), str(self.app), "--no-tools"],
                                  capture_output=True, text=True, cwd=workdir)
        self.assertEqual(discover.returncode, 0, discover.stderr)

    def test_local_edits_are_protected_until_force(self):
        self.assertEqual(tool("install", "--project", str(self.app)).returncode, 0)
        edited = self.target / "references/data.md"
        edited.write_text(edited.read_text(encoding="utf-8") + "\nlocal note\n", encoding="utf-8")
        (self.target / "references/mine.md").write_text("custom\n", encoding="utf-8")
        blocked = tool("install", "--project", str(self.app))
        self.assertEqual(blocked.returncode, 1)
        self.assertIn("references/data.md", blocked.stderr)
        self.assertIn("references/mine.md", blocked.stderr)
        self.assertIn("local note", edited.read_text(encoding="utf-8"))
        forced = tool("install", "--project", str(self.app), "--force")
        self.assertEqual(forced.returncode, 0, forced.stderr)
        self.assertNotIn("local note", edited.read_text(encoding="utf-8"))
        backup = self.target.parent / "zerolag.previous.zip"
        with zipfile.ZipFile(backup) as archive:
            self.assertIn("references/mine.md", archive.namelist())
        self.assertEqual([p.name for p in self.target.parent.iterdir() if p.is_dir()], ["zerolag"])

    def test_obsolete_unmodified_files_are_removed_on_upgrade(self):
        self.assertEqual(tool("install", "--project", str(self.app)).returncode, 0)
        old = self.target / "references/old.md"
        old.write_text("from an older version\n", encoding="utf-8")
        manifest_path = self.target / ".zerolag-install.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["files"]["references/old.md"] = hashlib.sha256(old.read_bytes()).hexdigest()
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        upgrade = tool("install", "--project", str(self.app))
        self.assertEqual(upgrade.returncode, 0, upgrade.stderr)
        self.assertIn("remove references/old.md", upgrade.stdout)
        self.assertFalse(old.exists())

    def test_user_codex_dry_run_and_guards(self):
        home = self.base / "home"
        home.mkdir()
        dry = tool("install", "--user", "--dry-run", home=home)
        self.assertEqual(dry.returncode, 0)
        self.assertIn("Dry run: nothing written.", dry.stdout)
        self.assertFalse((home / ".claude").exists())
        (home / ".agents/skills/fullstack-latency-optimizer").mkdir(parents=True)
        codex = tool("install", "--user", "--agent", "codex", home=home)
        self.assertEqual(codex.returncode, 0, codex.stderr)
        self.assertTrue((home / ".agents/skills/zerolag/SKILL.md").is_file())
        self.assertIn("older copy exists", codex.stdout)
        self.assertIn("$zerolag", codex.stdout)
        self.assertEqual(tool("install", "--project", str(ROOT)).returncode, 1)
        self.assertEqual(tool("install", "--project", str(self.base / "missing")).returncode, 1)
        self.assertEqual(tool("install").returncode, 2)

    @unittest.skipIf(os.name == "nt", "symlinks need privileges on Windows")
    def test_symlinked_install_is_left_alone(self):
        (self.app / ".claude/skills").mkdir(parents=True)
        self.target.symlink_to(SKILL, target_is_directory=True)
        proc = tool("install", "--project", str(self.app))
        self.assertEqual(proc.returncode, 1)
        self.assertTrue(self.target.is_symlink())


if __name__ == "__main__":
    unittest.main()
