"""Offline invariants for the production release; never contacts hosted systems."""
from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parents[1]
ROOT = SCRIPT_DIR.parents[1]
sys.path.insert(0, str(SCRIPT_DIR))

def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    instance = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(instance)
    return instance

migrations = load(SCRIPT_DIR / "hosted_migrations.py", "hosted_migrations")
render = load(ROOT / "scripts/deploy/render_release.py", "render_release")


class ProductionGates(unittest.TestCase):
    def test_ledger_is_exact_prefix(self):
        with tempfile.TemporaryDirectory() as directory:
            p = Path(directory)
            rows = [("20261007160000", "first", p / "one.sql"),
                    ("20261008120000", "second", p / "two.sql")]
            self.assertEqual(migrations.pending_files(rows, ["20261007160000"]), rows[1:])
            self.assertEqual(migrations.pending_files(rows, []), rows)
            self.assertEqual(migrations.pending_files(rows, [row[0] for row in rows]), [])
            for unexpected in (["20261008120000"], ["20261007160000", "unexpected"]):
                with self.assertRaises(Exception):
                    migrations.pending_files(rows, unexpected)

    def test_migration_requires_explicit_owner_role(self):
        with tempfile.TemporaryDirectory() as directory:
            p = Path(directory) / "supabase/migrations"
            p.mkdir(parents=True)
            f = p / "20261007160000_test.sql"
            f.write_text("select 1;")
            with self.assertRaises(Exception):
                migrations.chain(Path(directory))
            f.write_text("set role origenlab_owner;\nselect 1;\nreset role;\n")
            self.assertEqual(len(migrations.chain(Path(directory))), 1)

    def test_render_autodeploy_gate(self):
        name, service_id = render.SERVICES[0]
        obj = dict(id=service_id, ownerId=render.OWNER,
                   repo=render.REPOSITORY, branch="main", autoDeployTrigger="off")
        render.verify_service(obj, service_id)
        for key, value in (("autoDeployTrigger", "commit"), ("branch", "other"),
                           ("ownerId", "other")):
            with self.assertRaises(Exception):
                render.verify_service(dict(obj, **{key: value}), service_id)

    def test_all_four_services(self):
        self.assertEqual(len({r for _, r in render.SERVICES}), 4)


if __name__ == "__main__":
    unittest.main()
