"""scripts/changed_apps.py: the diff → test-target table, and its agreement with CI."""
from __future__ import annotations

import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import changed_apps  # noqa: E402

# Workflow file → the make target it runs.
WORKFLOW_TARGET = {
    "api.yml": "api",
    "worker.yml": "worker",
    "email-pipeline.yml": "email-pipeline",
    "dashboard.yml": "dashboard",
    "dashboard-proxy.yml": "proxy",
    "web.yml": "web",
    "supabase.yml": "supabase",
    "repo-scripts.yml": "repo",
}


def workflow_paths(path: Path) -> list[str]:
    """The quoted entries under every `paths:` key of a workflow file."""
    out, inside = [], False
    for line in path.read_text(encoding="utf-8").splitlines():
        if re.match(r"^\s*paths:\s*$", line):
            inside = True
            continue
        if inside and re.match(r"^\s*#", line):
            continue  # a comment inside the list does not end it
        m = re.match(r'^\s*-\s*"([^"]+)"\s*$', line)
        if inside and m:
            out.append(m.group(1))
        elif inside:
            inside = False
    return out


def sample(glob: str) -> str:
    """A concrete path the CI glob matches."""
    return glob.replace("**", "x/y.py").replace("*", "z")


class TargetsFor(unittest.TestCase):
    def test_api_change_also_runs_worker_which_shares_its_harness(self):
        self.assertEqual(changed_apps.targets_for(["apps/api/src/x.py"]), ["api", "worker"])

    def test_pipeline_change_runs_its_importers(self):
        self.assertEqual(
            changed_apps.targets_for(["apps/email-pipeline/src/a.py"]),
            ["api", "email-pipeline", "worker"],
        )

    def test_migration_runs_supabase_api_and_worker(self):
        self.assertEqual(
            changed_apps.targets_for(["supabase/migrations/20261012_x.sql"]),
            ["api", "supabase", "worker"],
        )

    def test_dashboard_is_not_proxy(self):
        self.assertEqual(changed_apps.targets_for(["apps/dashboard/src/a.tsx"]), ["dashboard"])
        self.assertEqual(changed_apps.targets_for(["apps/dashboard-proxy/src/a.ts"]), ["proxy"])

    def test_makefile_runs_everything(self):
        self.assertEqual(changed_apps.targets_for(["Makefile"]), list(changed_apps.ALL))

    def test_plain_docs_run_nothing(self):
        self.assertEqual(changed_apps.targets_for(["docs/runbooks/X.md", "README.md"]), [])

    def test_every_ci_path_filter_is_covered(self):
        for name, target in WORKFLOW_TARGET.items():
            wf = ROOT / ".github" / "workflows" / name
            for glob in workflow_paths(wf):
                with self.subTest(workflow=name, glob=glob):
                    self.assertIn(target, changed_apps.targets_for([sample(glob)]))


class ChangedPaths(unittest.TestCase):
    def git(self, repo: Path, *args: str) -> str:
        return subprocess.run(["git", "-C", str(repo), *args], check=True,
                              capture_output=True, text=True).stdout

    def make_repo(self) -> Path:
        repo = Path(tempfile.mkdtemp())
        self.git(repo, "init", "-q", "-b", "main")
        self.git(repo, "config", "user.email", "t@example.invalid")
        self.git(repo, "config", "user.name", "t")
        (repo / "README.md").write_text("x\n")
        self.git(repo, "add", ".")
        self.git(repo, "commit", "-qm", "base")
        self.git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
        return repo

    def test_untracked_file_counts(self):
        repo = self.make_repo()
        (repo / "apps" / "api").mkdir(parents=True)
        (repo / "apps" / "api" / "new.py").write_text("x = 1\n")
        self.assertIn("apps/api/new.py", changed_apps.changed_paths(repo))

    def test_on_main_uses_working_tree(self):
        repo = self.make_repo()
        (repo / "README.md").write_text("y\n")
        self.assertEqual(changed_apps.changed_paths(repo), ["README.md"])

    def test_committed_branch_change_counts(self):
        repo = self.make_repo()
        self.git(repo, "checkout", "-qb", "feat")
        (repo / "Makefile").write_text("x:\n")
        self.git(repo, "add", ".")
        self.git(repo, "commit", "-qm", "c")
        self.assertEqual(changed_apps.changed_paths(repo), ["Makefile"])


if __name__ == "__main__":
    unittest.main()
