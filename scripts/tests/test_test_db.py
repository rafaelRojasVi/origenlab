"""scripts/test_db.sh with a fake docker/uv/disposable cluster: names, env file, teardown."""
from __future__ import annotations

import os
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "test_db.sh"


class TestDb(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        self.log = self.tmp / "calls.log"
        for name in ("docker", "uv", "pg_isready"):
            self.fake(name, f'echo "{name} $*" >> {self.log}\n'
                            'case "$*" in *"port "*) echo "127.0.0.1:55432";; esac\n')
        cluster = self.tmp / "cluster.sh"
        cluster.write_text(textwrap.dedent(f"""\
            #!/usr/bin/env bash
            echo "cluster $*" >> {self.log}
            if [ "$1" = up ]; then echo "OL_TEST_CLUSTER=origenlab_apitest_abcd1234" >> "$2"; fi
        """))
        cluster.chmod(0o755)
        self.env = {"PATH": f"{self.bin}:{os.environ['PATH']}", "HOME": str(self.tmp),
                    "OL_DISPOSABLE_CLUSTER_SCRIPT": str(cluster)}

    def fake(self, name: str, body: str) -> None:
        p = self.bin / name
        p.write_text("#!/usr/bin/env bash\n" + body)
        p.chmod(0o755)

    def run_db(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(["bash", str(SCRIPT), *args], env=self.env,
                              capture_output=True, text=True)

    def test_up_v1_writes_both_urls_and_never_prints_password(self):
        env_file = self.tmp / "e.env"
        r = self.run_db("up", str(env_file), "--v1")
        self.assertEqual(r.returncode, 0, r.stderr)
        text = env_file.read_text()
        self.assertRegex(text, r"OL_TEST_V1_CLUSTER=origenlab_v1test_[0-9a-f]{8}\n")
        self.assertIn("/origenlab_v1_api_test\n", text)
        self.assertIn("/origenlab_v1_pipeline_test\n", text)
        self.assertIn("@127.0.0.1:55432/", text)
        password = text.split("postgres:", 1)[1].split("@", 1)[0]
        self.assertNotIn(password, r.stdout + r.stderr)
        self.assertNotIn("cluster up", self.log.read_text())  # --v1 only: no V2 cluster

    def test_readiness_and_createdb_go_over_tcp(self):
        self.run_db("up", str(self.tmp / "e.env"), "--v1")
        calls = self.log.read_text()
        self.assertIn("pg_isready -h 127.0.0.1", calls)
        self.assertIn("createdb -h 127.0.0.1", calls)

    def test_a_failed_up_removes_what_it_started(self):
        self.fake("docker", f'echo "docker $*" >> {self.log}\n'
                            'case "$*" in *createdb*) exit 1;; *"port "*) echo "127.0.0.1:55432";; esac\n')
        env_file = self.tmp / "e.env"
        r = self.run_db("up", str(env_file))
        self.assertNotEqual(r.returncode, 0)
        calls = self.log.read_text()
        self.assertRegex(calls, r"docker rm -f origenlab_v1test_[0-9a-f]{8}")
        self.assertIn("cluster down origenlab_apitest_abcd1234", calls)
        self.assertFalse(env_file.exists())

    def test_container_names_are_unique_per_call(self):
        a, b = self.tmp / "a.env", self.tmp / "b.env"
        self.run_db("up", str(a), "--v1")
        self.run_db("up", str(b), "--v1")
        name = lambda p: [l for l in p.read_text().splitlines() if l.startswith("OL_TEST_V1_CLUSTER=")][0]
        self.assertNotEqual(name(a), name(b))

    def test_up_refuses_an_existing_env_file(self):
        env_file = self.tmp / "e.env"
        env_file.write_text("OL_TEST_CLUSTER=x\n")
        r = self.run_db("up", str(env_file))
        self.assertEqual(r.returncode, 1)
        self.assertIn("make db-down", r.stderr)

    def test_down_removes_both_and_the_file(self):
        env_file = self.tmp / "e.env"
        env_file.write_text("OL_TEST_CLUSTER=origenlab_apitest_abcd1234\n"
                            "OL_TEST_V1_CLUSTER=origenlab_v1test_00000000\n")
        r = self.run_db("down", str(env_file))
        self.assertEqual(r.returncode, 0, r.stderr)
        calls = self.log.read_text()
        self.assertIn("cluster down origenlab_apitest_abcd1234", calls)
        self.assertIn("docker rm -f origenlab_v1test_00000000", calls)
        self.assertFalse(env_file.exists())

    def test_down_never_touches_other_containers(self):
        env_file = self.tmp / "e.env"
        env_file.write_text("OL_TEST_V1_CLUSTER=supabase_db_origenlab\n")
        r = self.run_db("down", str(env_file))
        self.assertEqual(r.returncode, 1)
        self.assertNotIn("supabase_db_origenlab", self.log.read_text() if self.log.exists() else "")


if __name__ == "__main__":
    unittest.main()
