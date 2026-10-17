"""Every new migration must pass the production release's own SQL parser.

`supabase/scripts/hosted_migrations.py apply` refuses (and the whole release stops) on a
migration it cannot split safely — e.g. a backslash in a top-level SQL literal (2026-10-16:
the model-alias migration stopped a release this way). This runs the same parser offline, so a
pull request finds out instead of the release. Migrations applied before this test existed by
the manual route are listed and skipped.
"""
from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
MIGRATIONS = ROOT / "supabase" / "migrations"
#: Applied to production by the reviewed manual route before the release parser existed.
APPLIED_BY_HAND = {"20261012120000_slice7_case_documents.sql"}
#: The automatic release applies everything after this version.
FIRST_RELEASED = "20261013"


def _release_parser():
    scripts = str(ROOT / "supabase" / "scripts")
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    spec = importlib.util.spec_from_file_location("hosted_migrations", ROOT / "supabase/scripts/hosted_migrations.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class MigrationsPassTheReleaseParser(unittest.TestCase):
    def test_every_release_migration_splits_cleanly(self) -> None:
        parser = _release_parser()
        checked = 0
        for path in sorted(MIGRATIONS.glob("*.sql")):
            if path.name < FIRST_RELEASED or path.name in APPLIED_BY_HAND:
                continue
            with self.subTest(migration=path.name):
                parser.sql_statements(path.read_text())
                parser.require_reviewed_owner_transition(path)
            checked += 1
        self.assertGreater(checked, 0)


if __name__ == "__main__":
    unittest.main()
