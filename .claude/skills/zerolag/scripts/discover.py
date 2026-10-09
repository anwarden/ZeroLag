#!/usr/bin/env python3
"""Map a repository for a ZeroLag audit without executing project code or printing secrets.

Finds every web app (single app, workspace monorepo, or several nested Git repositories),
exact framework versions, rendering and caching flags, Prisma and database drivers, Vercel
settings, observability and test tooling, agent instruction files and local tools.
Read-only. Python 3.9+ standard library only.

Environment files are read only with --inspect-env, and then only to classify database URLs
(provider, pooled or direct, region, pool parameters). Credentials, hosts and database names
are never printed; apps that share a database are grouped by an opaque label.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from urllib.parse import parse_qs, urlsplit

SKIP_DIRS = {
    "node_modules", "dist", "build", "out", "coverage", "vendor", "tmp", "temp", "target",
    "__pycache__", "storybook-static",
}
MAX_FILE_BYTES = 2_000_000
MAX_SOURCE_FILES = 8000
TRACKED_PACKAGES = (
    "next", "react", "react-dom", "typescript",
    "prisma", "@prisma/client", "@prisma/adapter-pg", "@prisma/adapter-neon", "@prisma/adapter-pg-worker",
    "@prisma/extension-accelerate", "@neondatabase/serverless", "pg", "postgres", "drizzle-orm", "kysely",
    "@vercel/functions", "@vercel/otel", "@vercel/speed-insights", "@vercel/analytics", "@sentry/nextjs",
    "web-vitals", "babel-plugin-react-compiler", "@tanstack/react-query", "swr",
    "@tanstack/react-virtual", "@tanstack/react-table", "react-window", "react-virtuoso",
    "next-auth", "@auth/core", "better-auth", "@clerk/nextjs",
    "@playwright/test", "playwright", "vitest", "jest", "vite", "react-scripts", "react-router",
    "express", "fastify", "hono", "@nestjs/core",
)
GROUPS = {
    "framework": ("next", "react", "react-dom", "vite", "react-router", "react-scripts", "typescript"),
    "data": ("prisma", "@prisma/client", "@prisma/adapter-pg", "@prisma/adapter-neon", "@prisma/adapter-pg-worker",
             "@prisma/extension-accelerate", "@neondatabase/serverless", "pg", "postgres", "drizzle-orm", "kysely"),
    "client data": ("@tanstack/react-query", "swr", "@tanstack/react-table"),
    "virtualization": ("@tanstack/react-virtual", "react-window", "react-virtuoso"),
    "compiler": ("babel-plugin-react-compiler",),
    "observability": ("@vercel/otel", "@vercel/speed-insights", "@vercel/analytics", "@vercel/functions",
                      "@sentry/nextjs", "web-vitals"),
    "auth": ("next-auth", "@auth/core", "better-auth", "@clerk/nextjs"),
    "testing": ("@playwright/test", "playwright", "vitest", "jest"),
    "server": ("express", "fastify", "hono", "@nestjs/core"),
}
INSTRUCTION_FILES = ("AGENTS.md", "CLAUDE.md", ".github/copilot-instructions.md", ".cursorrules", "CONTRIBUTING.md")
ENV_TEMPLATES = (".env.example", ".env.sample", ".env.template")
ENV_LOCAL = (".env", ".env.local", ".env.development", ".env.development.local")
NEXT_CONFIGS = ("next.config.ts", "next.config.mts", "next.config.mjs", "next.config.js", "next.config.cjs")
NEXT_FLAGS = ("cacheComponents", "partialPrefetching", "reactCompiler", "typedRoutes", "ppr", "dynamicIO")
ROUTE_FILES = {"page": "pages", "layout": "layouts", "loading": "loading", "route": "route handlers"}
SOURCE_SUFFIXES = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs")
DB_PARAMS = ("pgbouncer", "connection_limit", "pool_timeout", "connect_timeout", "sslmode", "pool_max_conns",
             "statement_cache_size", "socket_timeout")


# ---------------------------------------------------------------------------
# Small safe readers


def read_text(path: Path) -> str | None:
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_FILE_BYTES:
            return None
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def read_head(path: Path, size: int = 600) -> str:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            return handle.read(size)
    except OSError:
        return ""


def read_json(path: Path, warnings: list) -> dict | None:
    text = read_text(path)
    if text is None:
        return None
    try:
        data = json.loads(text)
    except json.JSONDecodeError as error:
        warnings.append(f"{path.as_posix()}: invalid JSON ({error.msg} at line {error.lineno})")
        return None
    return data if isinstance(data, dict) else None


def rel(path: Path, root: Path) -> str:
    try:
        value = path.relative_to(root).as_posix()
    except ValueError:
        value = path.as_posix()
    return value or "."


def is_git_root(path: Path) -> bool:
    return (path / ".git").exists()


# ---------------------------------------------------------------------------
# Workspace layout


def workspace_patterns(root: Path, warnings: list) -> list:
    patterns: list = []
    manifest = read_json(root / "package.json", warnings) or {}
    declared = manifest.get("workspaces")
    if isinstance(declared, dict):
        declared = declared.get("packages")
    if isinstance(declared, list):
        patterns += [p for p in declared if isinstance(p, str)]
    pnpm = read_text(root / "pnpm-workspace.yaml")
    if pnpm:
        in_packages = False
        for line in pnpm.splitlines():
            if re.match(r"^packages\s*:", line):
                in_packages = True
                continue
            if in_packages:
                item = re.match(r"^\s+-\s*['\"]?([^'\"#]+?)['\"]?\s*(#.*)?$", line)
                if item:
                    patterns.append(item.group(1).strip())
                elif line.strip() and not line.startswith((" ", "\t")):
                    in_packages = False
    return patterns


def monorepo_tools(root: Path) -> list:
    return [name for name in ("turbo.json", "nx.json", "lerna.json", "rush.json", "pnpm-workspace.yaml") if (root / name).exists()]


def package_dirs(root: Path, max_depth: int) -> list:
    found = []
    root_depth = len(root.parts)
    for current, dirs, files in os.walk(root):
        here = Path(current)
        depth = len(here.parts) - root_depth
        dirs[:] = sorted(d for d in dirs if not d.startswith(".") and d not in SKIP_DIRS
                         and not (here / d).is_symlink() and depth < max_depth)
        if "package.json" in files:
            found.append(here)
    return found


# ---------------------------------------------------------------------------
# Versions


def ancestors(app: Path, root: Path) -> list:
    """The app folder and its parents up to the first Git root or the scan root, inclusive."""
    chain = []
    for directory in [app, *app.parents]:
        chain.append(directory)
        if directory == root or is_git_root(directory):
            break
    return chain


def lockfile_for(app: Path, root: Path) -> Path | None:
    for directory in ancestors(app, root):
        for name in ("package-lock.json", "pnpm-lock.yaml", "yarn.lock", "bun.lock", "npm-shrinkwrap.json"):
            if (directory / name).is_file():
                return directory / name
    return None


def lock_versions(lockfile: Path, app: Path, wanted: set, warnings: list) -> dict:
    text = read_text(lockfile)
    if text is None:
        return {}
    name = lockfile.name
    found: dict = {}
    try:
        if name in ("package-lock.json", "npm-shrinkwrap.json"):
            packages = json.loads(text).get("packages", {})
            prefix = rel(app, lockfile.parent)
            prefix = "" if prefix == "." else prefix + "/"
            for pkg in wanted:
                entry = packages.get(f"{prefix}node_modules/{pkg}") or packages.get(f"node_modules/{pkg}")
                if isinstance(entry, dict) and isinstance(entry.get("version"), str):
                    found[pkg] = entry["version"]
        elif name == "pnpm-lock.yaml":
            importer = rel(app, lockfile.parent)
            block = re.search(r"^importers:\n(.*?)(?=^\S)", text + "\nend:\n", re.M | re.S)
            if block:
                section = re.search(rf"^  {re.escape(importer)}:\n(.*?)(?=^  \S|\Z)", block.group(1), re.M | re.S)
                if section:
                    for pkg in wanted:
                        match = re.search(rf"^      '?{re.escape(pkg)}'?:\n(?:        .*\n)*?        version: ([^\s(]+)",
                                          section.group(1) + "\n", re.M)
                        if match:
                            found[pkg] = match.group(1)
        elif name == "yarn.lock":
            for pkg in wanted:
                match = re.search(rf'^"?{re.escape(pkg)}@[^\n]*:\n(?:  .*\n)*?  version:? "?([^"\s]+)"?', text, re.M)
                if match:
                    found[pkg] = match.group(1)
        elif name == "bun.lock":
            for pkg in wanted:
                match = re.search(rf'"{re.escape(pkg)}": \["{re.escape(pkg)}@([^"]+)"', text)
                if match:
                    found[pkg] = match.group(1)
    except (ValueError, AttributeError) as error:
        warnings.append(f"{lockfile.name}: could not read versions ({error})")
    return found


def installed_version(app: Path, root: Path, pkg: str) -> str | None:
    for directory in ancestors(app, root):
        manifest = directory / "node_modules" / pkg / "package.json"
        if manifest.is_file():
            try:
                version = json.loads(manifest.read_text(encoding="utf-8")).get("version")
                return version if isinstance(version, str) else None
            except (OSError, ValueError):
                return None
    return None


def resolve_versions(app: Path, root: Path, declared: dict, warnings: list) -> dict:
    wanted = {pkg for pkg in TRACKED_PACKAGES if pkg in declared}
    lockfile = lockfile_for(app, root)
    locked = lock_versions(lockfile, app, wanted, warnings) if lockfile else {}
    versions = {}
    for pkg in sorted(wanted):
        installed = installed_version(app, root, pkg)
        if installed:
            versions[pkg] = {"version": installed, "source": "installed"}
        elif pkg in locked:
            versions[pkg] = {"version": locked[pkg], "source": "lockfile"}
        else:
            versions[pkg] = {"version": str(declared[pkg]), "source": "declared"}
    return versions


# ---------------------------------------------------------------------------
# App inspection


def classify(declared: dict, has_prisma: bool) -> str | None:
    if "next" in declared:
        return "next"
    if "react" in declared and "vite" in declared:
        return "react-vite"
    if "react-scripts" in declared:
        return "react-cra"
    if "react-router" in declared and "@react-router/dev" in declared:
        return "react-router"
    if any(pkg in declared for pkg in GROUPS["server"]):
        return "node-server"
    if has_prisma or any(pkg in declared for pkg in ("@prisma/client", "prisma", "drizzle-orm")):
        return "data-package"
    return None


def next_config(app: Path) -> dict | None:
    for name in NEXT_CONFIGS:
        text = read_text(app / name)
        if text is None:
            continue
        code = re.sub(r"//[^\n]*|/\*.*?\*/", "", text, flags=re.S)
        flags = {}
        for flag in NEXT_FLAGS:
            match = re.search(rf"\b{flag}\s*:\s*(true|false|\{{|['\"][\w-]+['\"])", code)
            if match:
                value = match.group(1).strip("'\"")
                flags[flag] = "object" if value == "{" else value
        output = re.search(r"\boutput\s*:\s*['\"](\w+)['\"]", code)
        if output:
            flags["output"] = output.group(1)
        for key in ("optimizePackageImports", "serverExternalPackages", "staleTimes", "logging", "images"):
            if re.search(rf"\b{key}\s*:", code):
                flags[key] = "set"
        return {"file": name, "flags": flags}
    return None


def scan_sources(app: Path) -> dict:
    counts = {label: 0 for label in ROUTE_FILES.values()}
    counts.update({"client files": 0, "server action files": 0})
    special = []
    routers = [r for r, base in (("app", "app"), ("app", "src/app"), ("pages", "pages"), ("pages", "src/pages"))
               if (app / base).is_dir()]
    for name in ("proxy", "middleware", "instrumentation", "instrumentation-client"):
        for base in ("", "src/"):
            if any((app / f"{base}{name}{suffix}").is_file() for suffix in SOURCE_SUFFIXES):
                special.append(f"{base}{name}")
    seen = 0
    for base in ("app", "src", "components", "lib", "pages", "hooks", "server", "features"):
        directory = app / base
        if not directory.is_dir() or directory.is_symlink():
            continue
        for current, dirs, files in os.walk(directory):
            dirs[:] = [d for d in dirs if not d.startswith(".") and d not in SKIP_DIRS]
            for file in files:
                if not file.endswith(SOURCE_SUFFIXES) or seen >= MAX_SOURCE_FILES:
                    continue
                seen += 1
                path = Path(current) / file
                stem = file.rsplit(".", 1)[0]
                if rel(path, app).startswith(("app/", "src/app/")) and stem in ROUTE_FILES:
                    counts[ROUTE_FILES[stem]] += 1
                head = read_head(path).lstrip("\ufeff \t\r\n")
                head = re.sub(r"^(?://[^\n]*\n|/\*.*?\*/\s*)*", "", head, flags=re.S).lstrip()
                if re.match(r"""['"]use client['"]""", head):
                    counts["client files"] += 1
                elif re.match(r"""['"]use server['"]""", head):
                    counts["server action files"] += 1
    return {"routers": sorted(set(routers)), "counts": counts, "special": special,
            "truncated": seen >= MAX_SOURCE_FILES}


def prisma_schemas(app: Path) -> list:
    schemas = []
    candidates = [app / "prisma" / "schema.prisma", app / "schema.prisma", app / "prisma" / "schema"]
    candidates += sorted((app / "prisma").glob("*.prisma")) if (app / "prisma").is_dir() else []
    seen = set()
    for path in candidates:
        files = sorted(path.glob("*.prisma")) if path.is_dir() else ([path] if path.is_file() else [])
        if not files or path in seen:
            continue
        seen.update(files)
        seen.add(path)
        text = "\n".join(read_text(f) or "" for f in files)
        provider = re.search(r"datasource\s+\w+\s*\{[^}]*?provider\s*=\s*\"(\w+)\"", text, re.S)
        generators = re.findall(r"generator\s+\w+\s*\{[^}]*?provider\s*=\s*\"([\w-]+)\"", text, re.S)
        preview = re.findall(r"previewFeatures\s*=\s*\[([^\]]*)\]", text)
        schemas.append({
            "path": rel(path, app),
            "provider": provider.group(1) if provider else None,
            "generators": sorted(set(generators)),
            "preview_features": sorted({p.strip().strip('"') for block in preview for p in block.split(",") if p.strip()}),
            "env": sorted(set(re.findall(r"env\(\s*\"(\w+)\"\s*\)", text))),
            "models": len(re.findall(r"^\s*model\s+\w+\s*\{", text, re.M)),
            "relation_mode": (re.search(r"relationMode\s*=\s*\"(\w+)\"", text) or [None, None])[1],
        })
    config = next((name for name in ("prisma.config.ts", "prisma.config.mts", "prisma.config.js") if (app / name).is_file()), None)
    if config and schemas:
        schemas[0]["config"] = config
    elif config:
        schemas.append({"path": None, "config": config})
    return schemas


def vercel_settings(app: Path, warnings: list) -> dict | None:
    config = read_json(app / "vercel.json", warnings) if (app / "vercel.json").is_file() else None
    linked = (app / ".vercel" / "project.json").is_file()
    has_ts = any((app / name).is_file() for name in ("vercel.ts", "vercel.mts"))
    if not config and not linked and not has_ts:
        return None
    info: dict = {"linked": linked}
    if has_ts:
        info["config"] = "vercel.ts (not evaluated)"
    if config:
        info["config"] = "vercel.json"
        if isinstance(config.get("regions"), list):
            info["regions"] = [r for r in config["regions"] if isinstance(r, str)]
        if isinstance(config.get("functions"), dict):
            info["functions"] = {pattern: {k: v for k, v in settings.items() if k in ("memory", "maxDuration", "regions")}
                                 for pattern, settings in config["functions"].items() if isinstance(settings, dict)}
        if isinstance(config.get("crons"), list):
            info["crons"] = len(config["crons"])
    return info


RELEVANT_ENV = re.compile(r"DATABASE|POSTGRES|^PG|PRISMA|NEON|DIRECT_URL|SHADOW|REDIS|^KV_|UPSTASH|CACHE|QUEUE|QSTASH|"
                          r"VERCEL|EDGE_CONFIG|OTEL|SENTRY|NEXT_PUBLIC_APP|_REGION$")


def env_names(app: Path) -> list:
    names = set()
    for name in ENV_TEMPLATES:
        for line in (read_text(app / name) or "").splitlines():
            match = re.match(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=", line)
            if match:
                names.add(match.group(1))
    return sorted(names)


def classify_database_url(value: str) -> dict | None:
    value = value.strip().strip("'\"")
    if not re.match(r"^(postgres(?:ql)?|prisma(?:\+postgres)?)://", value):
        return None
    try:
        parts = urlsplit(value)
        host = (parts.hostname or "").lower()
        port = parts.port
    except ValueError:
        return {"provider": "unparsable"}
    params = parse_qs(parts.query)
    info: dict = {"scheme": parts.scheme.split("+")[0]}
    if parts.scheme.startswith("prisma"):
        info["provider"] = "prisma-accelerate-or-postgres"
    elif host in ("localhost", "127.0.0.1", "::1", "host.docker.internal") or host.endswith(".local"):
        info["provider"] = "local"
    elif host.endswith(".neon.tech"):
        info["provider"] = "neon"
        info["pooled"] = "-pooler." in host
        region = re.search(r"\.([a-z]{2}-[a-z]+-\d)\.(aws|azure|gcp)\.neon\.tech$", host)
        if region:
            info["region"] = f"{region.group(2)} {region.group(1)}"
    elif host.endswith((".supabase.co", ".supabase.com")):
        info["provider"] = "supabase"
        info["pooled"] = "pooler" in host or port == 6543
    elif host.endswith(".rds.amazonaws.com"):
        info["provider"] = "aws-rds"
    else:
        info["provider"] = "other"
    selected = {key: params[key][0] for key in DB_PARAMS if key in params}
    if selected:
        info["params"] = selected
    # Pooled and direct Neon hosts reach the same database: fingerprint them together.
    same_database = host.replace("-pooler.", ".") if info.get("provider") == "neon" else host
    digest = hashlib.sha256(f"{same_database}:{port if info.get('provider') != 'neon' else ''}/{parts.path}".encode()).hexdigest()
    info["_fingerprint"] = digest[:12]
    return info


def inspect_env(app: Path) -> list:
    urls = []
    for name in ENV_LOCAL:
        for line in (read_text(app / name) or "").splitlines():
            match = re.match(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$", line)
            if not match:
                continue
            info = classify_database_url(match.group(2))
            if info:
                urls.append({"file": name, "var": match.group(1), **info})
    return urls


def instructions(directory: Path) -> list:
    return [name for name in INSTRUCTION_FILES if (directory / name).is_file()]


def inspect_app(app: Path, root: Path, manifest: dict, kind: str, args, warnings: list) -> dict:
    declared = {}
    for field in ("dependencies", "devDependencies", "optionalDependencies"):
        if isinstance(manifest.get(field), dict):
            declared.update(manifest[field])
    scripts = manifest.get("scripts") if isinstance(manifest.get("scripts"), dict) else {}
    info: dict = {
        "path": rel(app, root),
        "name": manifest.get("name") if isinstance(manifest.get("name"), str) else app.name,
        "kind": kind,
        "own_git_repo": is_git_root(app) and app != root,
        "versions": resolve_versions(app, root, declared, warnings),
        "scripts": {k: v for k, v in scripts.items() if k in ("dev", "build", "start", "test", "lint", "typecheck", "e2e")
                    and isinstance(v, str)},
        "workspace_deps": sorted(k for k, v in declared.items() if isinstance(v, str) and v.startswith("workspace:")),
        "instructions": instructions(app) if app != root else [],
        "_declared": sorted(declared),
    }
    engines = manifest.get("engines")
    if isinstance(engines, dict) and isinstance(engines.get("node"), str):
        info["node_engine"] = engines["node"]
    if kind == "next":
        info["next_config"] = next_config(app)
    if kind in ("next", "react-vite", "react-cra", "react-router", "node-server"):
        info["sources"] = scan_sources(app)
    schemas = prisma_schemas(app)
    if schemas:
        info["prisma"] = schemas
    vercel = vercel_settings(app, warnings)
    if vercel:
        info["vercel"] = vercel
    names = env_names(app)
    if names:
        info["env_names"] = names
    if args.inspect_env:
        urls = inspect_env(app)
        if urls:
            info["database_urls"] = urls
    return info


# ---------------------------------------------------------------------------
# Tools


def tool_versions() -> dict:
    tools = {"python": sys.version.split()[0]}
    for name, command in (("node", ["node", "--version"]), ("git", ["git", "--version"]), ("psql", ["psql", "--version"])):
        if shutil.which(command[0]) is None:
            tools[name] = None
            continue
        try:
            out = subprocess.run(command, capture_output=True, text=True, timeout=5).stdout.strip()
            tools[name] = (re.search(r"\d+(?:\.\d+)+", out) or [out])[0] if out else "present"
        except (OSError, subprocess.SubprocessError):
            tools[name] = "present"
    tools["vercel"] = "present" if shutil.which("vercel") else None
    browsers = [Path("/Applications/Google Chrome.app"), Path.home() / "Library/Caches/ms-playwright",
                Path.home() / ".cache/ms-playwright"]
    if os.environ.get("LOCALAPPDATA"):
        browsers.append(Path(os.environ["LOCALAPPDATA"]) / "ms-playwright")
    chrome_cli = any(shutil.which(name) for name in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser"))
    tools["browser"] = "present" if chrome_cli or any(path.exists() for path in browsers) else None
    return tools


# ---------------------------------------------------------------------------
# Main discovery


def discover(root: Path, args) -> dict:
    warnings: list = []
    patterns = workspace_patterns(root, warnings)
    apps = []
    for directory in package_dirs(root, args.max_depth):
        manifest = read_json(directory / "package.json", warnings)
        if manifest is None:
            continue
        declared = {}
        for field in ("dependencies", "devDependencies"):
            if isinstance(manifest.get(field), dict):
                declared.update(manifest[field])
        has_prisma = (directory / "prisma").is_dir() or (directory / "schema.prisma").is_file()
        kind = classify(declared, has_prisma)
        if kind is None:
            continue
        if directory == root and kind == "data-package" and patterns:
            continue
        apps.append(inspect_app(directory, root, manifest, kind, args, warnings))

    own_repos = [a for a in apps if a["own_git_repo"]]
    web_apps = [a for a in apps if a["kind"] != "data-package"]
    if own_repos:
        layout = "multi-repo"
    elif patterns or len(web_apps) > 1 or monorepo_tools(root):
        layout = "monorepo"
    elif web_apps:
        layout = "single-app"
    else:
        layout = "no-web-app-found"

    shared = []
    for package in apps:
        users = sorted(a["path"] for a in apps if a is not package and package["name"] in a["_declared"])
        if users:
            shared.append({"package": package["path"], "kind": package["kind"], "used_by": users})
    databases: dict = {}
    for app in apps:
        for url in app.get("database_urls", []):
            fingerprint = url.pop("_fingerprint", None)
            if fingerprint:
                mode = " (pooled)" if url.get("pooled") else (" (direct)" if "pooled" in url else "")
                databases.setdefault(fingerprint, []).append((app["path"], f"{app['path']}:{url['var']}{mode}"))
    groups = [entries for _, entries in sorted(databases.items()) if len({path for path, _ in entries}) > 1]
    shared_dbs = [{"label": f"db-{index}", "used_by": sorted({label for _, label in entries})}
                  for index, entries in enumerate(groups, 1)]
    for app in apps:
        app.pop("_declared", None)

    managers = sorted({lock.name for lock in (lockfile_for(root / a["path"], root) for a in apps) if lock})
    return {
        "zerolag_discover": 1,
        "root": root.name or str(root),
        "layout": layout,
        "workspace_patterns": patterns,
        "monorepo_tools": monorepo_tools(root),
        "lockfiles": managers,
        "instructions": instructions(root),
        "apps": apps,
        "shared_packages": shared,
        "shared_databases": shared_dbs,
        "tools": tool_versions() if not args.no_tools else {},
        "warnings": warnings,
    }


def format_versions(versions: dict, group: tuple) -> str:
    return ", ".join(f"{pkg} {v['version']}" + ("" if v["source"] == "installed" else f" ({v['source']})")
                     for pkg, v in versions.items() if pkg in group)


def to_text(result: dict) -> str:
    lines = [f"ZeroLag discovery · {result['root']} · layout: {result['layout']} · "
             f"{len(result['apps'])} package(s) of interest"]
    if result["instructions"]:
        lines.append(f"Read first (host rules): {', '.join(result['instructions'])}")
    meta = [f"lockfiles: {', '.join(result['lockfiles']) or 'none'}"]
    if result["workspace_patterns"]:
        meta.append(f"workspaces: {', '.join(result['workspace_patterns'])}")
    if result["monorepo_tools"]:
        meta.append(f"tools: {', '.join(result['monorepo_tools'])}")
    lines.append(" · ".join(meta))
    if result["tools"]:
        lines.append("Local tools: " + " · ".join(f"{k} {v or 'missing'}" for k, v in result["tools"].items()))
    for app in result["apps"]:
        head = f"\n[{app['path']}] {app['name']} · {app['kind']}" + (" · own git repo" if app["own_git_repo"] else "")
        lines.append(head)
        if app["instructions"]:
            lines.append(f"  read first: {', '.join(app['instructions'])}")
        for label, group in GROUPS.items():
            text = format_versions(app["versions"], group)
            if text:
                lines.append(f"  {label}: {text}")
        if app.get("node_engine"):
            lines.append(f"  node engine: {app['node_engine']}")
        config = app.get("next_config")
        if app["kind"] == "next":
            if config:
                flags = " ".join(f"{k}={v}" for k, v in config["flags"].items()) or "no performance flags set"
                lines.append(f"  {config['file']}: {flags}")
            else:
                lines.append("  next.config: not found")
        sources = app.get("sources")
        if sources:
            counts = ", ".join(f"{k} {v}" for k, v in sources["counts"].items() if v)
            lines.append(f"  routers: {', '.join(sources['routers']) or 'none'}" + (f" · {counts}" if counts else "")
                         + (" · scan truncated" if sources["truncated"] else ""))
            if sources["special"]:
                lines.append(f"  files: {', '.join(sources['special'])}")
        for schema in app.get("prisma", []):
            parts = [f"provider {schema.get('provider')}"] if schema.get("provider") else []
            if schema.get("generators"):
                parts.append(f"generator {'/'.join(schema['generators'])}")
            if schema.get("models"):
                parts.append(f"{schema['models']} models")
            if schema.get("preview_features"):
                parts.append(f"preview {', '.join(schema['preview_features'])}")
            if schema.get("env"):
                parts.append(f"env {', '.join(schema['env'])}")
            if schema.get("relation_mode"):
                parts.append(f"relationMode {schema['relation_mode']}")
            if schema.get("config"):
                parts.append(schema["config"])
            lines.append(f"  prisma {schema.get('path') or ''}: {' · '.join(parts)}".rstrip())
        vercel = app.get("vercel")
        if vercel:
            bits = ["linked" if vercel["linked"] else "not linked"]
            if vercel.get("config"):
                bits.append(vercel["config"])
            if vercel.get("regions"):
                bits.append(f"regions {', '.join(vercel['regions'])}")
            if vercel.get("functions"):
                bits.append(f"function overrides {len(vercel['functions'])}")
            if vercel.get("crons"):
                bits.append(f"crons {vercel['crons']}")
            lines.append(f"  vercel: {' · '.join(bits)}")
        if app["scripts"]:
            lines.append("  scripts: " + " · ".join(f"{k}={v}" for k, v in app["scripts"].items()))
        if app["workspace_deps"]:
            lines.append(f"  workspace deps: {', '.join(app['workspace_deps'])}")
        if app.get("env_names"):
            relevant = [n for n in app["env_names"] if RELEVANT_ENV.search(n)]
            others = len(app["env_names"]) - len(relevant)
            lines.append(f"  env names (templates): {', '.join(relevant) or 'none performance-related'}"
                         + (f" (+{others} other)" if others else ""))
        for url in app.get("database_urls", []):
            desc = [url["provider"]]
            if "pooled" in url:
                desc.append("pooled" if url["pooled"] else "direct")
            if url.get("region"):
                desc.append(url["region"])
            if url.get("params"):
                desc.append(" ".join(f"{k}={v}" for k, v in url["params"].items()))
            lines.append(f"  db url {url['var']} ({url['file']}): {' · '.join(desc)}")
    if result["shared_packages"]:
        lines.append("\nShared packages: " + "; ".join(f"{s['package']} ({s['kind']}) used by {', '.join(s['used_by'])}"
                                                      for s in result["shared_packages"]))
    if result["shared_databases"]:
        lines.append("Shared databases: " + "; ".join(f"{s['label']} used by {', '.join(s['used_by'])}"
                                                       for s in result["shared_databases"]))
    if result["warnings"]:
        lines.append("\nWarnings:\n" + "\n".join(f"  - {w}" for w in result["warnings"]))
    return "\n".join(lines) + "\n"


def main(argv: list | None = None) -> int:
    parser = argparse.ArgumentParser(description="Map a repository for a ZeroLag audit (read-only, no secrets).")
    parser.add_argument("root", nargs="?", default=".", type=Path, help="repository or workspace root (default: .)")
    parser.add_argument("--json", action="store_true", help="print JSON instead of the text summary")
    parser.add_argument("--max-depth", type=int, default=4, help="folder depth to search for apps (default: 4)")
    parser.add_argument("--inspect-env", action="store_true",
                        help="classify database URLs in local .env files without printing them")
    parser.add_argument("--no-tools", action="store_true", help="skip local tool detection")
    args = parser.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    root = args.root.resolve()
    if not root.is_dir():
        print(f"error: {args.root} is not a directory", file=sys.stderr)
        return 1
    if args.max_depth < 1:
        print("error: --max-depth must be at least 1", file=sys.stderr)
        return 2
    result = discover(root, args)
    print(json.dumps(result, indent=1, sort_keys=True) if args.json else to_text(result), end="" if not args.json else "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
