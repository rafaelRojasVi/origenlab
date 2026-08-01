"""Safe gitignored reports/out containment for planner outputs."""

from __future__ import annotations

from pathlib import Path


class ReportOutputError(ValueError):
    """Requested output path is outside the ignored reports/out tree."""


def require_reports_out_dir(out_dir: Path, *, repo_email_pipeline_root: Path) -> Path:
    """Resolve and require out_dir under apps/email-pipeline/reports/out/.

    Rejects path traversal and symlink escape before any files are created.
    """
    root = repo_email_pipeline_root.resolve()
    reports_out = (root / "reports" / "out").resolve()
    if not reports_out.is_dir():
        raise ReportOutputError("reports/out directory is missing")

    # Do not create out_dir yet — only resolve parents that already exist.
    requested = Path(out_dir)
    # Expand user, then resolve carefully: if path does not exist, resolve parent.
    candidate = requested.expanduser()
    if candidate.exists():
        resolved = candidate.resolve()
        if candidate.is_symlink() and not str(resolved).startswith(str(reports_out)):
            raise ReportOutputError("symlink escape outside reports/out")
    else:
        parent = candidate.parent
        if not parent.exists():
            # Walk up to first existing ancestor under reports/out.
            walk = parent
            while not walk.exists() and walk != walk.parent:
                walk = walk.parent
            if not walk.exists():
                raise ReportOutputError("cannot resolve output path parent")
            resolved_parent = walk.resolve()
            if not str(resolved_parent).startswith(str(reports_out)):
                raise ReportOutputError("output path escapes reports/out")
            # Reconstruct logical resolved path under the verified parent.
            try:
                rel = candidate.relative_to(walk)
            except ValueError as exc:
                raise ReportOutputError("output path escapes reports/out") from exc
            resolved = (resolved_parent / rel).resolve()
        else:
            resolved = (parent.resolve() / candidate.name).resolve()

    if not str(resolved).startswith(str(reports_out) + "/") and resolved != reports_out:
        # Allow exact reports/out, but planner should use a subdirectory.
        if resolved != reports_out:
            raise ReportOutputError("output path must be inside reports/out")

    # Symlink escape: any existing symlink component outside reports/out.
    cur = Path(out_dir).expanduser()
    parts = []
    while cur != cur.parent:
        parts.append(cur.name)
        cur = cur.parent
        if cur.exists() and cur.is_symlink():
            target = cur.resolve()
            if not str(target).startswith(str(reports_out)):
                raise ReportOutputError("symlink escape outside reports/out")

    # Prove destination is under ignored reports/out (gitignore contract).
    # We require the path prefix; CI hygiene already ignores reports/out.
    if reports_out.name != "out" or reports_out.parent.name != "reports":
        raise ReportOutputError("reports/out contract broken")

    return resolved
