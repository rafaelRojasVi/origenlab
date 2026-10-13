"""scripts/check.sh with a fake `make`: every target in order, stop at the first failure."""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import changed_apps  # noqa: E402


class Check(unittest.TestCase):
    def run_check(self, failing: str = "") -> tuple[subprocess.CompletedProcess, list[str]]:
        tmp = Path(tempfile.mkdtemp())
        log = tmp / "make.log"
        fake = tmp / "make"
        fake.write_text(f'#!/usr/bin/env bash\necho "$*" >> {log}\n'
                        f'[ "${{@: -1}}" = "test-{failing}" ] && exit 2\nexit 0\n')
        fake.chmod(0o755)
        env = {**os.environ, "PATH": f"{tmp}:{os.environ['PATH']}"}
        r = subprocess.run(["bash", str(ROOT / "scripts" / "check.sh"), "--all"], env=env,
                           capture_output=True, text=True)
        return r, (log.read_text().splitlines() if log.exists() else [])

    def test_all_runs_every_target(self):
        r, calls = self.run_check()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual([c.split()[-1] for c in calls], [f"test-{t}" for t in changed_apps.ALL])
        self.assertIn("== passed ==", r.stdout)

    def test_stops_at_the_first_failure(self):
        r, calls = self.run_check(failing="email-pipeline")
        self.assertEqual(r.returncode, 1)
        self.assertEqual(calls[-1].split()[-1], "test-email-pipeline")
        self.assertIn("FAILED: email-pipeline", r.stderr)


if __name__ == "__main__":
    unittest.main()
