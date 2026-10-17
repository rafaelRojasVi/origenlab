#!/usr/bin/env python3
"""Decide what a release must do, from the paths changed since what each service runs.

Pure decisions plus read-only git. The Render reads live in render_release.py; this module never
contacts a provider. Every rule leans towards doing more: an unknown path deploys every service,
an unknown or non-ancestor base deploys that service, a pending migration deploys everything and
requires the supabase workflow.
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SERVICE_NAMES = ("worker", "api", "dashboard")
ALL = frozenset(SERVICE_NAMES)
SHA = re.compile(r"[0-9a-f]{40}")

# Prefix -> services whose build or runtime reads it. The worker installs apps/api and
# apps/email-pipeline as editable path dependencies (apps/worker/pyproject.toml) and the API
# image copies apps/email-pipeline (apps/api/Dockerfile), so those deploy both Python services.
# Migrations change what both Python services run against; deploy both with them.
SERVICE_PREFIXES: tuple[tuple[str, frozenset[str]], ...] = (
    ("apps/dashboard/", frozenset({"dashboard"})),
    ("apps/worker/", frozenset({"worker"})),
    ("apps/api/", frozenset({"worker", "api"})),
    ("apps/email-pipeline/", frozenset({"worker", "api"})),
    ("supabase/migrations/", frozenset({"worker", "api"})),
    ("supabase/roles.sql", frozenset({"worker", "api"})),
    # Deployed by their own workflows (web-deploy, dashboard-proxy-deploy), never by Render here.
    ("apps/web/", frozenset()),
    ("apps/dashboard-proxy/", frozenset()),
    ("docs/", frozenset()),
    ("supabase/tests/", frozenset()),
)
# Root files no build reads. Anything else at the root (render.yaml, Makefile, ruff.toml, ...)
# is unknown and deploys every service.
NO_SERVICE_FILES = frozenset({"README.md", "AGENTS.md", "CLAUDE.md", "CONTRIBUTING.md",
                              "SECURITY.md", "LICENSE"})

# What the supabase workflow tests: exactly its pull_request paths (.github/workflows/supabase.yml;
# a test keeps the two equal). A change to any of these since what is live requires that
# workflow's success at the release SHA.
SUPABASE_CI_PREFIXES = ("supabase/",)
SUPABASE_CI_FILES = frozenset({
    "docs/ARCHITECTURE.md", "docs/DOMAIN.md", "docs/MIGRATION.md", "docs/OPERATIONS.md",
    "docs/README.md", "scripts/security/check-public-repo-hygiene.sh", ".github/workflows/supabase.yml",
})


class PlanRefused(RuntimeError):
    pass


def services_for_path(path: str) -> frozenset[str]:
    """The services a changed path deploys; an unrecognised path deploys all of them."""
    if path in NO_SERVICE_FILES:
        return frozenset()
    for prefix, services in SERVICE_PREFIXES:
        if path == prefix or (prefix.endswith("/") and path.startswith(prefix)):
            return services
    return ALL


def needs_supabase_ci(path: str) -> bool:
    return path in SUPABASE_CI_FILES or any(path.startswith(p) for p in SUPABASE_CI_PREFIXES)


def _git(*args: str, root: Path = ROOT) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True,
                          check=False, timeout=60)


def changed_paths(base: str | None, sha: str, *, root: Path = ROOT) -> list[str] | None:
    """Paths changed from base to sha, both sides of a rename; None when it cannot be proved.

    None (deploy everything) when base is missing, not a commit here, or not an ancestor of sha:
    a rollback or a manual deploy of another branch must never shrink a release.
    """
    if not base or not SHA.fullmatch(base) or not SHA.fullmatch(sha):
        return None
    if _git("cat-file", "-e", f"{base}^{{commit}}", root=root).returncode != 0:
        return None
    if _git("merge-base", "--is-ancestor", base, sha, root=root).returncode != 0:
        return None
    out = _git("diff", "--name-only", "--no-renames", "-z", base, sha, root=root)
    if out.returncode != 0:
        return None
    return sorted(p for p in out.stdout.split("\0") if p)


def build_plan(sha: str, bases: dict[str, str | None], pending_migrations: int | None,
               diff=None) -> dict:
    """The release plan: per service its live base and whether to deploy; whether supabase CI binds.

    bases: the commit each service runs now (its latest Render deploy, only when live), or None.
    pending_migrations: None when unknown, which is treated like a pending migration.
    """
    if not SHA.fullmatch(sha) or set(bases) != ALL:
        raise PlanRefused("Release plan needs the exact SHA and the three service bases")
    diff = diff or changed_paths
    migration_due = pending_migrations is None or pending_migrations > 0
    services: dict[str, dict] = {}
    union: set[str] = set()
    any_unknown = False
    for name in SERVICE_NAMES:
        base = bases[name]
        paths = diff(base, sha)
        if paths is None:
            any_unknown = True
            deploy, reason = True, "base unknown or not an ancestor"
        else:
            union.update(paths)
            hit = sorted(p for p in paths if name in services_for_path(p))
            if migration_due:
                deploy, reason = True, "pending migration"
            elif base == sha:
                deploy, reason = False, "already live at this SHA"
            elif hit:
                deploy, reason = True, f"{len(hit)} changed path(s), first {hit[0]}"
            else:
                deploy, reason = False, "no change it builds from"
        services[name] = {"base": base if paths is not None else None, "deploy": deploy, "reason": reason}
    require_supabase = migration_due or any_unknown or any(needs_supabase_ci(p) for p in union)
    return {"sha": sha, "pending_migrations": pending_migrations,
            "require_supabase": require_supabase, "services": services,
            "changed_paths": len(union)}


def parse_plan(text: str | None, sha: str) -> dict | None:
    """The plan handed between jobs. None when absent (callers then do the full release);
    anything present but malformed refuses."""
    if text is None or not text.strip():
        return None
    try:
        plan = json.loads(text)
    except ValueError as exc:
        raise PlanRefused("Release plan is not JSON") from exc
    if not isinstance(plan, dict) or plan.get("sha") != sha:
        raise PlanRefused("Release plan does not name this SHA")
    services = plan.get("services")
    if not isinstance(services, dict) or set(services) != ALL:
        raise PlanRefused("Release plan does not list exactly the three services")
    for name, row in services.items():
        if not isinstance(row, dict) or not isinstance(row.get("deploy"), bool):
            raise PlanRefused(f"Release plan entry for {name} is malformed")
        base = row.get("base")
        if not row["deploy"] and (not isinstance(base, str) or not SHA.fullmatch(base)):
            raise PlanRefused(f"Release plan keeps {name} without its exact live commit")
    if not isinstance(plan.get("require_supabase"), bool):
        raise PlanRefused("Release plan does not say whether supabase CI binds")
    return plan


def expected_commits(plan: dict | None, sha: str) -> dict[str, str]:
    """The commit each service must be live at once the release is done."""
    if plan is None:
        return {name: sha for name in SERVICE_NAMES}
    return {name: sha if row["deploy"] else row["base"] for name, row in plan["services"].items()}


def summary_lines(plan: dict | None, sha: str) -> list[str]:
    """How to tell what is live: one line per service, deployed or kept."""
    if plan is None:
        return [f"{name}: deploy {sha[:12]} (no plan: full release)" for name in SERVICE_NAMES]
    lines = []
    for name in SERVICE_NAMES:
        row = plan["services"][name]
        if row["deploy"]:
            lines.append(f"{name}: deploy {sha[:12]} ({row['reason']})")
        else:
            lines.append(f"{name}: keep {row['base'][:12]} ({row['reason']})")
    return lines
