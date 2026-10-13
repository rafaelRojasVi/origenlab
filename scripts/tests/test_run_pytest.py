"""scripts/run_pytest.sh against a fake `uv` and `docker` on PATH."""
from __future__ import annotations

import os
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "run_pytest.sh"


class RunPytest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        self.env_file = self.tmp / "test-db.env"
        self.pytest_out = "1 passed in 0.1s"
        self.docker_alive = True

    def fake(self, name: str, body: str) -> None:
        p = self.bin / name
        p.write_text("#!/usr/bin/env bash\n" + textwrap.dedent(body))
        p.chmod(0o755)

    def run_script(self, *args: str, **extra: str) -> subprocess.CompletedProcess:
        self.fake("uv", f"""
            echo "VIRTUAL_ENV=${{VIRTUAL_ENV:-}}" >&2
            printf '%s\\n' {self.pytest_out!r}
        """)
        self.fake("docker", "exit 0\n" if self.docker_alive else "exit 1\n")
        env = {"PATH": f"{self.bin}:{os.environ['PATH']}", "HOME": str(self.tmp),
               "OL_TEST_DB_ENV": str(self.env_file), **extra}
        return subprocess.run(["bash", str(SCRIPT), "worker", *args], env=env,
                              capture_output=True, text=True)

    def write_env(self) -> None:
        self.env_file.write_text("OL_TEST_CLUSTER=origenlab_apitest_0000\n"
                                 "ORIGENLAB_V2_TEST_DSN=postgresql://x@127.0.0.1:1/postgres\n")

    def test_refuses_without_env_file(self):
        r = self.run_script()
        self.assertEqual(r.returncode, 1)
        self.assertIn("make db-up", r.stderr)

    def test_skip_db_runs_without_env_file(self):
        r = self.run_script(SKIP_DB="1")
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_refuses_when_cluster_container_is_gone(self):
        self.write_env()
        self.docker_alive = False
        r = self.run_script()
        self.assertEqual(r.returncode, 1)
        self.assertIn("make db-down && make db-up", r.stderr)

    def test_fails_on_a_database_skip(self):
        self.write_env()
        self.pytest_out = ("SKIPPED [3] tests/test_x.py:9: ORIGENLAB_V2_TEST_DSN and "
                           "ORIGENLAB_V2_API_TEST_DSN are both required")
        r = self.run_script()
        self.assertEqual(r.returncode, 1)
        self.assertIn("database tests skipped", r.stderr)

    def test_unrelated_skip_is_fine(self):
        self.write_env()
        self.pytest_out = "SKIPPED [1] tests/test_y.py:3: needs ripgrep"
        self.assertEqual(self.run_script().returncode, 0)

    def test_a_skip_that_also_needs_a_sqlite_archive_is_fine(self):
        self.write_env()
        self.pytest_out = ("SKIPPED [3] tests/m.py:133: Set ORIGENLAB_TEST_POSTGRES_URL and "
                           "ORIGENLAB_TEST_SQLITE_PATH for ARCH-2B real-backend parity.")
        self.assertEqual(self.run_script().returncode, 0)

    def test_a_v1_postgres_skip_fails(self):
        self.write_env()
        self.pytest_out = ("SKIPPED [7] tests/q.py:52: Set ORIGENLAB_TEST_POSTGRES_URL to a "
                           "disposable Postgres migrated to Alembic head.")
        self.assertEqual(self.run_script().returncode, 1)

    def test_unsets_virtual_env(self):
        self.write_env()
        r = self.run_script(VIRTUAL_ENV="/home/someone/.venv")
        self.assertIn("VIRTUAL_ENV=\n", r.stderr)

    def calls(self, *args: str, uv_body: str) -> tuple[subprocess.CompletedProcess, list[str]]:
        self.write_env()
        log = self.tmp / "uv.log"
        self.fake("uv", f'echo "$*" >> {log}\n' + uv_body)
        self.fake("docker", "exit 0\n")
        env = {"PATH": f"{self.bin}:{os.environ['PATH']}", "HOME": str(self.tmp),
               "OL_TEST_DB_ENV": str(self.env_file)}
        r = subprocess.run(["bash", str(SCRIPT), "worker", *args], env=env,
                           capture_output=True, text=True)
        return r, log.read_text().splitlines()

    def test_runs_in_parallel_then_serial_db_tests_alone(self):
        r, calls = self.calls("tests/test_a.py", uv_body="echo '1 passed'\n")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(len(calls), 2, calls)
        parallel, alone = calls
        for flag in ("-n auto", "--dist loadfile", "-rfEs", "--durations=10", "-p pg_per_worker",
                     "not serial_db", "tests/test_a.py"):
            self.assertIn(flag, parallel)
        self.assertIn("-n 0", alone)
        self.assertIn("-m serial_db", alone)
        self.assertIn("tests/test_a.py", alone)

    def test_no_serial_db_tests_selected_is_fine(self):
        body = 'case "$*" in *"-m serial_db"*) exit 5;; esac\necho "1 passed"\n'
        r, _ = self.calls(uv_body=body)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_a_failure_in_either_phase_fails_the_run(self):
        body = 'case "$*" in *"-m serial_db"*) exit 1;; esac\necho "1 passed"\n'
        r, _ = self.calls(uv_body=body)
        self.assertEqual(r.returncode, 1)


if __name__ == "__main__":
    unittest.main()
