"""Runs the read-only SQL documented in references/data.md against a disposable database.

Set ZEROLAG_TEST_DATABASE_URL to a throwaway PostgreSQL 16+ database (never a shared or production one) and
install psql. Queries that need pg_stat_statements are skipped when the extension is absent.
"""

from __future__ import annotations

import os
from pathlib import Path
import re
import shutil
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / ".claude/skills/zerolag/references/data.md"
URL = os.environ.get("ZEROLAG_TEST_DATABASE_URL")


def sql_blocks() -> list:
    return re.findall(r"```sql\n(.*?)```", DATA.read_text(encoding="utf-8"), re.S)


def statements(block: str) -> list:
    body = "\n".join(line for line in block.splitlines() if not line.lstrip().startswith("--"))
    return [s.strip() for s in re.split(r";\s*\n", body + "\n") if s.strip()]


class Documentation(unittest.TestCase):
    def test_catalog_is_present_and_read_only(self):
        blocks = sql_blocks()
        self.assertEqual(len(blocks), 2)
        self.assertIn("BEGIN TRANSACTION READ ONLY", blocks[0])
        queries = statements(blocks[1])
        self.assertGreaterEqual(len(queries), 9)
        for query in queries:
            self.assertRegex(query, r"^(SELECT|WITH)\b", query[:60])
            self.assertNotRegex(query.upper(), r"\b(INSERT|UPDATE|DELETE|ALTER|DROP|CREATE|TRUNCATE|GRANT)\b")


@unittest.skipUnless(URL and shutil.which("psql"), "set ZEROLAG_TEST_DATABASE_URL (disposable database) and install psql")
class AgainstDatabase(unittest.TestCase):
    def psql(self, sql: str) -> subprocess.CompletedProcess:
        return subprocess.run(["psql", URL, "-X", "-q", "-v", "ON_ERROR_STOP=1", "-c", sql], capture_output=True, text=True, timeout=60)

    def test_every_documented_query_runs_inside_the_read_only_wrapper(self):
        has_statements = "t" in self.psql("SELECT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'pg_stat_statements')").stdout
        for query in statements(sql_blocks()[1]):
            if "pg_stat_statements" in query and not has_statements:
                continue
            with self.subTest(query=query[:60]):
                proc = subprocess.run(["psql", URL, "-X", "-q", "-v", "ON_ERROR_STOP=1"], input=(
                    "BEGIN TRANSACTION READ ONLY;\nSET LOCAL statement_timeout = '5s';\nSET LOCAL lock_timeout = '1s';\n"
                    f"{query};\nROLLBACK;\n"), capture_output=True, text=True, timeout=60)
                self.assertEqual(proc.returncode, 0, proc.stderr)


if __name__ == "__main__":
    unittest.main()
