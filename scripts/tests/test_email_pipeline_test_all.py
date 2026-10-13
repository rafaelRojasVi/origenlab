"""apps/email-pipeline/scripts/test_all.sh: the full run must not pass with V2 proofs skipped."""
from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "apps" / "email-pipeline" / "scripts" / "test_all.sh"


class TestAll(unittest.TestCase):
    def run_with(self, skip_line: str) -> subprocess.CompletedProcess:
        tmp = Path(tempfile.mkdtemp())
        # The skip appears only in the full run, never in the transactional-files phase.
        (tmp / "uv").write_text("#!/usr/bin/env bash\n"
                                'case "$*" in *transactional*) echo "1 passed"; exit 0;; esac\n'
                                f"printf '%s\\n' {skip_line!r} '1 passed'\n")
        (tmp / "docker").write_text("#!/usr/bin/env bash\nexit 0\n")
        for name in ("uv", "docker"):
            (tmp / name).chmod(0o755)
        env_file = tmp / "test-db.env"
        env_file.write_text("OL_PIPELINE_PG_TEST_URL=postgresql://x@127.0.0.1:1/p_test\n")
        env = {"PATH": f"{tmp}:{os.environ['PATH']}", "HOME": str(tmp),
               "OL_TEST_DB_ENV": str(env_file)}
        return subprocess.run(["bash", str(SCRIPT)], env=env, capture_output=True, text=True)

    def test_a_skipped_v2_proof_fails_the_run(self):
        r = self.run_with("SKIPPED [9] tests/test_v2_import.py:40: ORIGENLAB_V2_TEST_DSN is not set")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("database tests skipped", r.stderr)

    def test_the_expected_transactional_url_skips_are_fine(self):
        r = self.run_with("SKIPPED [2] tests/test_x.py:5: set ORIGENLAB_POSTGRES_TEST_URL")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
