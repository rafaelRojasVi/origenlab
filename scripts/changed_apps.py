#!/usr/bin/env python3
"""Which test targets a change needs; `make check` runs exactly these.

Paths are the commits since the merge base with origin/main plus staged, unstaged and untracked
files, so a branch, a dirty tree on main and a brand-new file all count. RULES must cover every
`paths:` filter in .github/workflows; scripts/tests/test_changed_apps.py fails if they drift.
"""
from __future__ import annotations

import argparse
import fnmatch
import subprocess
import sys
from pathlib import Path

ALL = ("api", "dashboard", "email-pipeline", "proxy", "repo", "supabase", "web", "worker")

# Every matching rule contributes its targets. `*` crosses `/` in fnmatch, so "apps/api/*"
# covers the whole tree and "apps/dashboard/*" never matches "apps/dashboard-proxy/...".
RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    # The worker imports the api's case archive and shares its database test harness.
    ("apps/api/*", ("api", "worker")),
    ("render.yaml", ("worker",)),
    # api and worker import email-pipeline modules.
    ("apps/email-pipeline/*", ("email-pipeline", "api", "worker")),
    ("apps/worker/*", ("worker",)),
    # Both apps' database tests build their schema from these files.
    ("supabase/migrations/*", ("supabase", "api", "worker")),
    ("supabase/roles.sql", ("supabase", "api", "worker")),
    ("supabase/*", ("supabase",)),
    ("docs/ARCHITECTURE.md", ("supabase",)),
    ("docs/DOMAIN.md", ("supabase",)),
    ("docs/MIGRATION.md", ("supabase",)),
    ("docs/OPERATIONS.md", ("supabase",)),
    ("docs/README.md", ("supabase",)),
    ("scripts/security/*", ("supabase", "repo")),
    ("apps/dashboard/*", ("dashboard",)),
    ("apps/dashboard-proxy/*", ("proxy",)),
    ("apps/web/*", ("web",)),
    ("scripts/*", ("repo",)),
    ("Makefile", ALL),
    (".github/workflows/*", ALL),
)


def targets_for(paths: list[str]) -> list[str]:
    found: set[str] = set()
    for path in paths:
        for pattern, targets in RULES:
            if fnmatch.fnmatch(path, pattern):
                found.update(targets)
    return sorted(found)


def _git(root: Path, *args: str) -> list[str]:
    out = subprocess.run(["git", "-C", str(root), *args], check=True,
                         capture_output=True, text=True).stdout
    return [line for line in out.splitlines() if line]


def changed_paths(root: Path, base: str = "origin/main") -> list[str]:
    merge_base = _git(root, "merge-base", base, "HEAD")[0]
    paths = set(_git(root, "diff", "--name-only", merge_base))  # commits + working tree
    paths.update(_git(root, "ls-files", "--others", "--exclude-standard"))
    return sorted(paths)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--all", action="store_true", help="every target")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    targets = list(ALL) if args.all else targets_for(changed_paths(root))
    print("\n".join(targets))
    return 0


if __name__ == "__main__":
    sys.exit(main())
