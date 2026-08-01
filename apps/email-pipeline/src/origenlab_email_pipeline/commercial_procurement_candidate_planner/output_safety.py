"""Safe gitignored reports/out containment for planner outputs."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Callable


class ReportOutputError(ValueError):
    """Requested output path is outside the ignored reports/out tree."""


def _git_check_ignore(path: Path, *, git_cwd: Path) -> bool:
    """Return True when `git check-ignore -q` reports the path as ignored."""
    try:
        proc = subprocess.run(
            ["git", "check-ignore", "-q", "--", str(path)],
            cwd=str(git_cwd),
            check=False,
            capture_output=True,
        )
    except OSError as exc:
        raise ReportOutputError(f"git check-ignore unavailable: {exc}") from exc
    return proc.returncode == 0


def require_reports_out_dir(
    out_dir: Path,
    *,
    repo_email_pipeline_root: Path,
    require_git_ignored: bool = True,
) -> Path:
    """Resolve and require out_dir under apps/email-pipeline/reports/out/.

    Rejects path traversal, symlink escape, and (when enabled) destinations that
    Git does not report as ignored. Does not create the destination.
    """
    root = repo_email_pipeline_root.resolve()
    reports_out = (root / "reports" / "out").resolve()
    if not reports_out.is_dir():
        raise ReportOutputError("reports/out directory is missing")

    requested = Path(out_dir)
    candidate = requested.expanduser()
    if candidate.exists():
        resolved = candidate.resolve()
        if candidate.is_symlink() and not str(resolved).startswith(str(reports_out)):
            raise ReportOutputError("symlink escape outside reports/out")
    else:
        parent = candidate.parent
        if not parent.exists():
            walk = parent
            while not walk.exists() and walk != walk.parent:
                walk = walk.parent
            if not walk.exists():
                raise ReportOutputError("cannot resolve output path parent")
            resolved_parent = walk.resolve()
            if not str(resolved_parent).startswith(str(reports_out)):
                raise ReportOutputError("output path escapes reports/out")
            try:
                rel = candidate.relative_to(walk)
            except ValueError as exc:
                raise ReportOutputError("output path escapes reports/out") from exc
            resolved = (resolved_parent / rel).resolve()
        else:
            resolved = (parent.resolve() / candidate.name).resolve()

    if not str(resolved).startswith(str(reports_out) + "/") and resolved != reports_out:
        raise ReportOutputError("output path must be inside reports/out")

    cur = Path(out_dir).expanduser()
    while cur != cur.parent:
        if cur.exists() and cur.is_symlink():
            target = cur.resolve()
            if not str(target).startswith(str(reports_out)):
                raise ReportOutputError("symlink escape outside reports/out")
        cur = cur.parent

    if reports_out.name != "out" or reports_out.parent.name != "reports":
        raise ReportOutputError("reports/out contract broken")

    if require_git_ignored:
        # Prefer checking from monorepo root when present; else email-pipeline root.
        git_cwd = root
        if not (root / ".git").exists() and (root.parent.parent / ".git").exists():
            git_cwd = root.parent.parent
        # Path relative to git_cwd for check-ignore when possible.
        check_target = resolved
        try:
            check_target = resolved.relative_to(git_cwd.resolve())
        except ValueError:
            check_target = resolved
        if not _git_check_ignore(check_target, git_cwd=git_cwd):
            # Also try absolute path form.
            if not _git_check_ignore(resolved, git_cwd=git_cwd):
                raise ReportOutputError(
                    "destination is not git-ignored (git check-ignore failed)"
                )

    return resolved


def write_atomically(
    out_dir: Path,
    *,
    repo_email_pipeline_root: Path,
    writer: Callable[[Path], dict[str, str]],
    require_git_ignored: bool = True,
) -> dict[str, str]:
    """Validate destination, write complete bundle to a temp sibling, then rename.

    On failure after temp creation, temp is removed so no partial plan remains
    at the destination.
    """
    safe = require_reports_out_dir(
        out_dir,
        repo_email_pipeline_root=repo_email_pipeline_root,
        require_git_ignored=require_git_ignored,
    )
    parent = safe.parent
    parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(
        tempfile.mkdtemp(
            prefix=f".{safe.name}.tmp.",
            dir=str(parent),
        )
    )
    try:
        written = writer(tmp)
        if safe.exists():
            if safe.is_dir():
                shutil.rmtree(safe)
            else:
                safe.unlink()
        os.replace(tmp, safe)
        # Rewrite paths to final destination.
        return {k: str(safe / Path(v).name) for k, v in written.items()}
    except Exception:
        if tmp.exists():
            shutil.rmtree(tmp, ignore_errors=True)
        raise
