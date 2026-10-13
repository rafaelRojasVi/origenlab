"""scripts/lint.sh: runs pre-commit on every file, and says how to install it when missing."""
from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "lint.sh"


class Lint(unittest.TestCase):
    def run_lint(self, with_pre_commit: bool) -> subprocess.CompletedProcess:
        tmp = Path(tempfile.mkdtemp())
        if with_pre_commit:
            fake = tmp / "pre-commit"
            fake.write_text('#!/usr/bin/env bash\necho "pre-commit $*"\n')
            fake.chmod(0o755)
        # Only the fake (if any) plus the basics bash needs; never the developer's real pre-commit.
        env = {"PATH": f"{tmp}:/usr/bin:/bin", "HOME": str(tmp)}
        return subprocess.run(["bash", str(SCRIPT)], env=env, capture_output=True, text=True)

    def test_missing_pre_commit_names_make_setup(self):
        r = self.run_lint(with_pre_commit=False)
        self.assertEqual(r.returncode, 1)
        self.assertIn("make setup", r.stderr)

    def test_runs_every_hook_on_every_file(self):
        r = self.run_lint(with_pre_commit=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("run --all-files", r.stdout)


if __name__ == "__main__":
    unittest.main()
