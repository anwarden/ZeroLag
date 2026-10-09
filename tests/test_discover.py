"""discover.py tests on generated repositories: layouts, versions, flags, shared infrastructure, secrets."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / ".claude/skills/zerolag/scripts/discover.py"
SECRET = "fixture-password"


def write(base: Path, files: dict) -> None:
    for name, content in files.items():
        path = base / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(content, indent=1) if isinstance(content, (dict, list)) else content, encoding="utf-8")


def run(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(SCRIPT), str(root), "--no-tools", *args],
                          capture_output=True, text=True, encoding="utf-8")


def discover_json(root: Path, *args: str) -> dict:
    proc = run(root, "--json", *args)
    if proc.returncode != 0:
        raise AssertionError(proc.stderr)
    return json.loads(proc.stdout)


def neon_url(user: str, endpoint: str, region: str, pooled: bool, query: str = "") -> str:
    host = f"ep-{endpoint}{'-pooler' if pooled else ''}.{region}.aws.neon.tech"
    return f"postgresql://{user}:{SECRET}@{host}/appdb{query}"


def single_app(base: Path) -> None:
    write(base, {
        "package.json": {"name": "web", "scripts": {"dev": "next dev", "build": "next build", "start": "next start"},
                         "engines": {"node": ">=20"},
                         "dependencies": {"next": "^16.4.0", "react": "19.3.0", "react-dom": "19.3.0", "@prisma/client": "^7.10.0",
                                          "@prisma/adapter-pg": "^7.10.0", "pg": "^8.23.0"},
                         "devDependencies": {"prisma": "^7.10.0", "@playwright/test": "^1.64.0", "babel-plugin-react-compiler": "1.0.0"}},
        "package-lock.json": {"lockfileVersion": 3, "packages": {
            "": {"name": "web"}, "node_modules/next": {"version": "16.4.0"}, "node_modules/react": {"version": "19.3.0"},
            "node_modules/@prisma/client": {"version": "7.10.0"}, "node_modules/prisma": {"version": "7.10.0"}}},
        "node_modules/next/package.json": {"name": "next", "version": "16.4.1"},
        "node_modules/fake/package.json": {"name": "fake", "dependencies": {"next": "1.0.0"}},
        "next.config.ts": "// reactCompiler: false is only a comment\nconst config = {\n  cacheComponents: true,\n  reactCompiler: true,\n"
                          "  output: 'standalone',\n  experimental: { staleTimes: { dynamic: 30 } },\n};\nexport default config;\n",
        "app/layout.tsx": "export default function Layout({ children }) { return children }\n",
        "app/page.tsx": "export default function Page() { return null }\n",
        "app/dashboard/page.tsx": "export default async function Dashboard() { return null }\n",
        "app/dashboard/loading.tsx": "export default function Loading() { return null }\n",
        "app/dashboard/tabs.tsx": "// Tabs for the dashboard\n'use client';\nexport function Tabs() { return null }\n",
        "app/actions.ts": "\"use server\";\nexport async function save() {}\n",
        "app/api/health/route.ts": "export function GET() { return new Response('ok') }\n",
        "proxy.ts": "export function proxy() {}\n",
        "instrumentation.ts": "export function register() {}\n",
        "prisma/schema.prisma": 'datasource db {\n  provider = "postgresql"\n}\n\ngenerator client {\n  provider = "prisma-client"\n'
                                '  output = "../src/generated"\n  previewFeatures = ["relationJoins"]\n}\n\nmodel User {\n  id Int @id\n}\n\n'
                                'model Post {\n  id Int @id\n}\n',
        "prisma.config.ts": "export default {}\n",
        "vercel.json": {"regions": ["fra1"], "functions": {"app/api/**": {"maxDuration": 30, "memory": 1024}},
                        "crons": [{"path": "/api/cron", "schedule": "0 * * * *"}]},
        ".vercel/project.json": {"projectId": "prj_fixture_id", "orgId": "team_fixture_id"},
        ".env.example": "DATABASE_URL=\nDIRECT_URL=\nPAYMENT_PROVIDER_KEY=\n",
        ".env.local": f'DATABASE_URL="{neon_url("app", "fixture-000000", "eu-central-1", True, "?sslmode=require&connection_limit=5")}"\n'
                      f"DIRECT_URL={neon_url('app', 'fixture-000000', 'eu-central-1', False)}\nOTHER=1\n",
        "AGENTS.md": "# Rules\n",
    })


class SingleApp(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp.name) / "web"
        single_app(cls.root)
        cls.result = discover_json(cls.root)
        cls.app = cls.result["apps"][0]

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_layout_and_instructions(self):
        self.assertEqual(self.result["layout"], "single-app")
        self.assertEqual(len(self.result["apps"]), 1)
        self.assertEqual(self.result["instructions"], ["AGENTS.md"])
        self.assertEqual(self.app["kind"], "next")

    def test_version_sources_prefer_installed_then_lockfile(self):
        versions = self.app["versions"]
        self.assertEqual(versions["next"], {"version": "16.4.1", "source": "installed"})
        self.assertEqual(versions["react"], {"version": "19.3.0", "source": "lockfile"})
        self.assertEqual(versions["@prisma/client"]["version"], "7.10.0")
        self.assertEqual(versions["@playwright/test"], {"version": "^1.64.0", "source": "declared"})

    def test_next_flags_ignore_comments(self):
        flags = self.app["next_config"]["flags"]
        self.assertEqual(flags["cacheComponents"], "true")
        self.assertEqual(flags["reactCompiler"], "true")
        self.assertEqual(flags["output"], "standalone")
        self.assertEqual(flags["staleTimes"], "set")

    def test_source_counts(self):
        sources = self.app["sources"]
        self.assertEqual(sources["routers"], ["app"])
        self.assertEqual(sources["counts"], {"pages": 2, "layouts": 1, "loading": 1, "route handlers": 1,
                                             "client files": 1, "server action files": 1})
        self.assertEqual(sources["special"], ["proxy", "instrumentation"])

    def test_prisma_and_vercel(self):
        schema = self.app["prisma"][0]
        self.assertEqual((schema["provider"], schema["generators"], schema["preview_features"], schema["models"]),
                         ("postgresql", ["prisma-client"], ["relationJoins"], 2))
        self.assertEqual(schema["config"], "prisma.config.ts")
        vercel = self.app["vercel"]
        self.assertTrue(vercel["linked"])
        self.assertEqual(vercel["regions"], ["fra1"])
        self.assertEqual(vercel["functions"], {"app/api/**": {"maxDuration": 30, "memory": 1024}})
        self.assertEqual(vercel["crons"], 1)

    def test_no_secret_or_identifier_without_inspect_env(self):
        raw = run(self.root, "--json").stdout + run(self.root).stdout
        self.assertNotIn("database_urls", self.app)
        for needle in (SECRET, "ep-fixture", "prj_fixture_id", "team_fixture_id"):
            self.assertNotIn(needle, raw)

    def test_inspect_env_classifies_without_leaking(self):
        result = discover_json(self.root, "--inspect-env")
        urls = {u["var"]: u for u in result["apps"][0]["database_urls"]}
        self.assertEqual(urls["DATABASE_URL"]["provider"], "neon")
        self.assertTrue(urls["DATABASE_URL"]["pooled"])
        self.assertEqual(urls["DATABASE_URL"]["region"], "aws eu-central-1")
        self.assertEqual(urls["DATABASE_URL"]["params"], {"connection_limit": "5", "sslmode": "require"})
        self.assertFalse(urls["DIRECT_URL"]["pooled"])
        self.assertNotIn("OTHER", urls)
        raw = run(self.root, "--json", "--inspect-env").stdout + run(self.root, "--inspect-env").stdout
        for needle in (SECRET, "ep-fixture", "appdb", "app:", "_fingerprint"):
            self.assertNotIn(needle, raw)

    def test_text_summary_is_compact(self):
        text = run(self.root).stdout
        self.assertLessEqual(len(text.splitlines()), 25)
        self.assertIn("next.config.ts: cacheComponents=true reactCompiler=true", text)
        self.assertIn("env names (templates): DATABASE_URL, DIRECT_URL (+1 other)", text)
        self.assertIn("Read first (host rules): AGENTS.md", text)


class Monorepo(unittest.TestCase):
    def test_pnpm_workspace_with_shared_database_package(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            write(root, {
                "package.json": {"name": "mono", "private": True, "devDependencies": {"turbo": "^2"}},
                "pnpm-workspace.yaml": "packages:\n  - 'apps/*'\n  - \"packages/*\"\n",
                "turbo.json": {},
                "pnpm-lock.yaml": "lockfileVersion: '9.0'\n\nimporters:\n\n  .:\n    devDependencies:\n      turbo:\n        specifier: ^2\n"
                                  "        version: 2.5.0\n\n  apps/admin:\n    dependencies:\n      '@acme/db':\n        specifier: workspace:*\n"
                                  "        version: link:../../packages/db\n      next:\n        specifier: ^15.5.0\n"
                                  "        version: 15.5.4(react@19.1.0)\n\n  apps/web:\n    dependencies:\n      next:\n"
                                  "        specifier: ^16.4.0\n        version: 16.4.0(react@19.3.0)\n      react:\n"
                                  "        specifier: ^19.3.0\n        version: 19.3.0\n\npackages:\n\n  next@16.4.0:\n    resolution: {}\n",
                "apps/web/package.json": {"name": "web", "dependencies": {"next": "^16.4.0", "react": "^19.3.0", "@acme/db": "workspace:*"}},
                "apps/admin/package.json": {"name": "admin", "dependencies": {"next": "^15.5.0", "react": "^19.1.0", "@acme/db": "workspace:*"}},
                "packages/db/package.json": {"name": "@acme/db", "dependencies": {"@prisma/client": "^6.19.0"}, "devDependencies": {"prisma": "^6.19.0"}},
                "packages/db/prisma/schema.prisma": 'generator client {\n  provider = "prisma-client-js"\n}\n',
                "packages/ui/package.json": {"name": "@acme/ui", "dependencies": {"react": "^19.3.0"}},
            })
            result = discover_json(root)
            self.assertEqual(result["layout"], "monorepo")
            self.assertEqual(result["workspace_patterns"], ["apps/*", "packages/*"])
            self.assertEqual(result["monorepo_tools"], ["turbo.json", "pnpm-workspace.yaml"])
            apps = {a["path"]: a for a in result["apps"]}
            self.assertEqual(sorted(apps), ["apps/admin", "apps/web", "packages/db"])
            self.assertEqual(apps["apps/web"]["versions"]["next"], {"version": "16.4.0", "source": "lockfile"})
            self.assertEqual(apps["apps/admin"]["versions"]["next"], {"version": "15.5.4", "source": "lockfile"})
            self.assertEqual(apps["packages/db"]["kind"], "data-package")
            self.assertEqual(result["shared_packages"], [{"package": "packages/db", "kind": "data-package",
                                                          "used_by": ["apps/admin", "apps/web"]}])
            self.assertEqual(result["lockfiles"], ["pnpm-lock.yaml"])


class MultiRepo(unittest.TestCase):
    def test_separate_git_repos_share_a_database_without_printing_it(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            write(root, {
                "package.json": {"name": "control", "private": True},
                "package-lock.json": {"lockfileVersion": 3, "packages": {"node_modules/next": {"version": "99.0.0"}}},
                "shop/.git/HEAD": "ref: refs/heads/main\n",
                "shop/package.json": {"name": "shop", "dependencies": {"next": "16.4.0", "react": "19.3.0"}},
                "shop/.env.local": f"DATABASE_URL={neon_url('shop', 'shared-111111', 'eu-west-2', True)}\n",
                "backoffice/.git": "gitdir: ../.git/worktrees/backoffice\n",
                "backoffice/package.json": {"name": "backoffice", "dependencies": {"next": "^16.2.0"}},
                "backoffice/package-lock.json": {"lockfileVersion": 3, "packages": {"node_modules/next": {"version": "16.2.6"}}},
                "backoffice/.env.local": f"DATABASE_URL={neon_url('bo', 'shared-111111', 'eu-west-2', False)}\n",
            })
            result = discover_json(root, "--inspect-env")
            self.assertEqual(result["layout"], "multi-repo")
            apps = {a["path"]: a for a in result["apps"]}
            self.assertTrue(apps["shop"]["own_git_repo"] and apps["backoffice"]["own_git_repo"])
            self.assertEqual(apps["shop"]["versions"]["next"], {"version": "16.4.0", "source": "declared"})
            self.assertEqual(apps["backoffice"]["versions"]["next"], {"version": "16.2.6", "source": "lockfile"})
            self.assertEqual(result["shared_databases"], [{"label": "db-1", "used_by": [
                "backoffice:DATABASE_URL (direct)", "shop:DATABASE_URL (pooled)"]}])
            self.assertNotIn(SECRET, json.dumps(result))
            self.assertIn("Shared databases: db-1 used by backoffice:DATABASE_URL (direct), shop:DATABASE_URL (pooled)",
                          run(root, "--inspect-env").stdout)


class Robustness(unittest.TestCase):
    def test_bad_inputs_degrade_to_warnings(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "repo"
            outside = Path(temp) / "outside"
            write(outside, {"package.json": {"name": "outside", "dependencies": {"next": "16.4.0"}}})
            write(root, {
                "apps/broken/package.json": "{ not json",
                "apps/ok/package.json": {"name": "ok", "dependencies": {"next": "16.4.0"}},
                "a/b/c/d/e/package.json": {"name": "deep", "dependencies": {"next": "16.4.0"}},
            })
            (root / "apps/link").symlink_to(outside, target_is_directory=True)
            result = discover_json(root)
            self.assertEqual([a["name"] for a in result["apps"]], ["ok"])
            self.assertTrue(any("invalid JSON" in w for w in result["warnings"]))
            deep = discover_json(root, "--max-depth", "6")
            self.assertIn("deep", [a["name"] for a in deep["apps"]])

    def test_usage_errors(self):
        missing = run(Path("/nonexistent-zerolag-root"))
        self.assertEqual(missing.returncode, 1)
        self.assertIn("is not a directory", missing.stderr)
        with tempfile.TemporaryDirectory() as temp:
            self.assertEqual(run(Path(temp), "--max-depth", "0").returncode, 2)
            empty = discover_json(Path(temp))
            self.assertEqual(empty["layout"], "no-web-app-found")

    def test_tool_detection_runs(self):
        with tempfile.TemporaryDirectory() as temp:
            proc = subprocess.run([sys.executable, str(SCRIPT), temp, "--json"], capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            tools = json.loads(proc.stdout)["tools"]
            self.assertEqual(tools["python"], sys.version.split()[0])
            self.assertIn("node", tools)


if __name__ == "__main__":
    unittest.main()
