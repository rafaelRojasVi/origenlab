"""scripts/pytest_plugins/pg_per_worker.py: clone naming and the guards that keep it on test databases."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pytest_plugins"))

import pg_per_worker  # noqa: E402


class CloneUrl(unittest.TestCase):
    def test_clone_keeps_the_test_suffix_and_names_the_worker(self):
        self.assertEqual(
            pg_per_worker.clone_url("postgresql://u:p@127.0.0.1:5/origenlab_v1_api_test", "gw3"),
            "postgresql://u:p@127.0.0.1:5/origenlab_v1_api_gw3_test",
        )

    def test_maintenance_url_points_at_postgres(self):
        self.assertEqual(
            pg_per_worker.maintenance_url("postgresql://u:p@localhost:5/origenlab_v1_api_test"),
            "postgresql://u:p@localhost:5/postgres",
        )

    def test_refuses_a_database_not_named_test(self):
        with self.assertRaises(ValueError):
            pg_per_worker.clone_url("postgresql://u:p@127.0.0.1:5/origenlab_clean", "gw0")

    def test_refuses_a_remote_host(self):
        with self.assertRaises(ValueError):
            pg_per_worker.clone_url("postgresql://u:p@db.example.com:5/x_test", "gw0")

    def test_refuses_a_strange_worker_id(self):
        with self.assertRaises(ValueError):
            pg_per_worker.clone_url("postgresql://u:p@127.0.0.1:5/x_test", "gw0; drop")


if __name__ == "__main__":
    unittest.main()
