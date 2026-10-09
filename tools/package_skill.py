#!/usr/bin/env python3
"""Build a reproducible ZeroLag archive, or install the skill where Claude Code or Codex loads it.

  python3 tools/package_skill.py build [--out dist/zerolag-<version>.zip]
  python3 tools/package_skill.py install --project <app>  [--agent claude|codex] [--dry-run] [--force]
  python3 tools/package_skill.py install --user           [--agent claude|codex] [--dry-run] [--force]
  python3 tools/package_skill.py install --dest <skills folder>

An install records the version and file hashes in .zerolag-install.json. Re-running it is safe:
unchanged installs are left alone, upgrades replace shipped files, and files edited locally are never
overwritten without --force (which saves the previous folder as zerolag.previous.zip). Python 3.9+.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile
import zipfile

REPO = Path(__file__).resolve().parents[1]
SKILL = REPO / ".claude" / "skills" / "zerolag"
MANIFEST = ".zerolag-install.json"
LEGACY = ("fullstack-latency-optimizer", "rocketspeed")
IGNORED_NAMES = {".DS_Store", "__pycache__", MANIFEST}
EPOCH = (1980, 1, 1, 0, 0, 0)


def version() -> str:
    match = re.search(r'^\s+version:\s*"([^"]+)"', (SKILL / "SKILL.md").read_text(encoding="utf-8"), re.M)
    if not match:
        raise SystemExit("error: metadata.version missing from SKILL.md")
    return match.group(1)


def skill_files() -> list:
    files = []
    for path in sorted(SKILL.rglob("*")):
        relative = path.relative_to(SKILL)
        if path.is_file() and not path.is_symlink() and not any(part in IGNORED_NAMES for part in relative.parts) \
                and path.suffix not in (".pyc", ".pyo"):
            files.append(relative)
    return files


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def is_executable(path: Path) -> bool:
    return path.suffix in (".py", ".mjs") and path.read_bytes().startswith(b"#!")


def build(out: Path) -> int:
    out.parent.mkdir(parents=True, exist_ok=True)
    temporary = out.with_name(out.name + ".tmp")
    with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for relative in skill_files():
            source = SKILL / relative
            info = zipfile.ZipInfo(f"zerolag/{relative.as_posix()}", date_time=EPOCH)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = ((0o755 if is_executable(source) else 0o644) | 0o100000) << 16
            archive.writestr(info, source.read_bytes())
    os.replace(temporary, out)
    print(f"Built {out} ({len(skill_files())} files, version {version()}, sha256 {digest(out)[:16]})")
    return 0


def destination(args) -> Path:
    folder = ".agents" if args.agent == "codex" else ".claude"
    if args.dest:
        return Path(args.dest).expanduser().resolve() / "zerolag"
    if args.user:
        return Path.home() / folder / "skills" / "zerolag"
    project = Path(args.project).expanduser().resolve()
    if not project.is_dir():
        raise SystemExit(f"error: project folder {project} does not exist")
    return project / folder / "skills" / "zerolag"


def install(args) -> int:
    target = destination(args)
    if target.resolve() == SKILL.resolve() or SKILL.resolve() in target.resolve().parents:
        print("error: refusing to install over the ZeroLag source folder", file=sys.stderr)
        return 1
    if target.is_symlink():
        print(f"error: {target} is a symbolic link; update the folder it points to instead", file=sys.stderr)
        return 1
    files = skill_files()
    shipped = {relative.as_posix(): digest(SKILL / relative) for relative in files}
    manifest_path = target / MANIFEST
    previous: dict = {}
    if manifest_path.is_file():
        try:
            previous = json.loads(manifest_path.read_text(encoding="utf-8")).get("files", {})
        except (ValueError, AttributeError):
            previous = {}
    conflicts, changes, removals = [], [], []
    for name, new_hash in shipped.items():
        current = target / name
        if not current.exists():
            changes.append(f"add {name}")
            continue
        current_hash = digest(current)
        if current_hash == new_hash:
            continue
        if previous.get(name) == current_hash:
            changes.append(f"update {name}")
        else:
            conflicts.append(name)
    for name, old_hash in previous.items():
        current = target / name
        if name not in shipped and current.exists():
            (removals if digest(current) == old_hash else conflicts).append(name)
    if target.is_dir():
        for path in sorted(target.rglob("*")):
            relative = path.relative_to(target)
            name = relative.as_posix()
            if path.is_file() and name not in shipped and name not in previous \
                    and not any(part in IGNORED_NAMES for part in relative.parts) and path.suffix not in (".pyc", ".pyo"):
                conflicts.append(name)  # a file this tool never installed: keep it unless --force

    print(f"ZeroLag {version()} → {target}", flush=True)
    if not changes and not removals and not conflicts and previous:
        print("Already up to date.")
        return 0
    if conflicts and not args.force:
        print("error: these files differ from both the shipped and the previously installed version "
              "(local edits or an untracked install):", file=sys.stderr)
        for name in conflicts:
            print(f"  {name}", file=sys.stderr)
        print("Review them, then re-run with --force (the current folder is saved as zerolag.previous.zip).", file=sys.stderr)
        return 1
    for line in changes + [f"remove {name}" for name in removals]:
        print(f"  {line}")
    if args.dry_run:
        print("Dry run: nothing written.")
        return 0
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".zerolag-install-", dir=target.parent))
    try:
        for relative in files:
            (staging / relative).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(SKILL / relative, staging / relative)
        (staging / MANIFEST).write_text(json.dumps({"version": version(), "files": shipped}, indent=1, sort_keys=True) + "\n",
                                        encoding="utf-8")
        if target.exists():
            if args.force and conflicts:
                # A zip, not a folder: a second SKILL.md under skills/ would load as a duplicate skill.
                backup = shutil.make_archive(str(target.with_name(target.name + ".previous")), "zip", target)
                print(f"  previous version saved as {backup}")
            shutil.rmtree(target)
        staging.rename(target)
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    for legacy in LEGACY:
        if (target.parent / legacy).exists():
            print(f"note: an older copy exists at {target.parent / legacy}; remove it after checking it has no local changes.")
    invoke = "$zerolag" if args.agent == "codex" else "/zerolag"
    print(f"Installed. Start a new session (or keep the current one: skills reload live) and run {invoke}.")
    return 0


def main(argv: list | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build or install the ZeroLag skill.")
    commands = parser.add_subparsers(dest="command", required=True)
    build_cmd = commands.add_parser("build", help="write a reproducible zip of the skill folder")
    build_cmd.add_argument("--out", type=Path, help="archive path (default: dist/zerolag-<version>.zip)")
    install_cmd = commands.add_parser("install", help="copy the skill where an agent loads it")
    where = install_cmd.add_mutually_exclusive_group(required=True)
    where.add_argument("--project", help="app or repository root: installs into <root>/.claude/skills (or .agents/skills)")
    where.add_argument("--user", action="store_true", help="personal install: ~/.claude/skills (or ~/.agents/skills)")
    where.add_argument("--dest", help="explicit skills folder; zerolag/ is created inside")
    install_cmd.add_argument("--agent", choices=("claude", "codex"), default="claude")
    install_cmd.add_argument("--force", action="store_true", help="replace locally modified files (saves zerolag.previous.zip first)")
    install_cmd.add_argument("--dry-run", action="store_true", help="show what would change")
    args = parser.parse_args(argv)
    if args.command == "build":
        return build(args.out or REPO / "dist" / f"zerolag-{version()}.zip")
    return install(args)


if __name__ == "__main__":
    raise SystemExit(main())
