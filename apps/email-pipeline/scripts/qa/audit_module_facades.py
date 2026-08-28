#!/usr/bin/env python3
"""Read-only audit: duplicate module basenames and root vs core facade pairs.

Uses ``git ls-files`` only. Does not import pipeline modules, mutate SQLite/Postgres/Gmail,
or write output files by default.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
_PACKAGE_PREFIX = "apps/email-pipeline/src/origenlab_email_pipeline/"
_MAX_FACADE_NONEMPTY_LINES = 20
_STAR_IMPORT_RE = re.compile(r"from\s+[\.\w]+\s+import\s+\*")
_IMPL_LIVES_MARKERS = (
    "Implementation currently lives in",
    "implementation currently lives in",
    "Re-export only",
    "re-export only",
)


def _monorepo_root() -> Path:
    proc = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        cwd=_REPO,
        capture_output=True,
        text=True,
        check=True,
    )
    return Path(proc.stdout.strip())


def _git_tracked_package_py_paths() -> list[str]:
    """Package-relative paths (no ``__init__.py``)."""
    root = _monorepo_root()
    proc = subprocess.run(
        ["git", "ls-files", "--", "apps/email-pipeline/src/origenlab_email_pipeline"],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
    )
    out: list[str] = []
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line.endswith(".py") or line.endswith("__init__.py"):
            continue
        if not line.startswith(_PACKAGE_PREFIX):
            continue
        rel = line[len(_PACKAGE_PREFIX) :]
        out.append(rel)
    return sorted(out)


def _nonempty_noncomment_lines(content: str) -> list[str]:
    lines: list[str] = []
    for raw in content.splitlines():
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        lines.append(stripped)
    return lines


def is_facade_wrapper(content: str) -> bool:
    nonempty = _nonempty_noncomment_lines(content)
    if len(nonempty) > _MAX_FACADE_NONEMPTY_LINES:
        return False
    if not _STAR_IMPORT_RE.search(content):
        return False
    return any(marker in content for marker in _IMPL_LIVES_MARKERS)


# Same-basename groups that are verified distinct domains despite mixing a
# root implementation with subpackage implementations. Reviewed 2026-08-28
# (commercial platform reset): root db.py is the SQLite connection layer;
# postgres_dashboard_api/db.py is the Postgres mirror connection helper.
_REVIEWED_DISTINCT_GROUPS: dict[str, frozenset[str]] = {
    "db.py": frozenset({"db.py", "postgres_dashboard_api/db.py"}),
}


def classify_duplicate_group(entries: list[dict[str, object]]) -> str:
    root_entries = [e for e in entries if e["is_root"]]
    sub_entries = [e for e in entries if not e["is_root"]]

    paths = {str(e["path"]) for e in entries}
    for basename, reviewed_paths in _REVIEWED_DISTINCT_GROUPS.items():
        if paths == reviewed_paths:
            return "same_basename_distinct_domains"

    if len(root_entries) == 1 and sub_entries:
        root_facade = bool(root_entries[0]["is_facade"])
        sub_facades = [bool(s["is_facade"]) for s in sub_entries]
        if not root_facade and any(sub_facades):
            return "root_implementation_with_subpackage_facade"
        if root_facade and any(not f for f in sub_facades):
            return "root_facade_to_subpackage_implementation"

    if not root_entries and len(sub_entries) >= 2:
        top_dirs = {str(s["path"]).split("/")[0] for s in sub_entries}
        if len(top_dirs) >= 2:
            return "same_basename_distinct_domains"

    return "needs_manual_review_true_duplicate_or_special_case"


def build_report() -> dict[str, object]:
    root = _monorepo_root()
    rel_paths = _git_tracked_package_py_paths()
    by_basename: dict[str, list[dict[str, object]]] = defaultdict(list)

    for rel in rel_paths:
        abs_path = root / _PACKAGE_PREFIX / rel
        try:
            content = abs_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            content = ""
        by_basename[Path(rel).name].append(
            {
                "path": rel,
                "is_root": "/" not in rel,
                "is_facade": is_facade_wrapper(content),
            }
        )

    pairs: list[dict[str, object]] = []
    summary: dict[str, int] = defaultdict(int)

    for basename, entries in sorted(by_basename.items()):
        if len(entries) < 2:
            continue
        classification = classify_duplicate_group(entries)
        summary[classification] += 1
        pairs.append(
            {
                "basename": basename,
                "classification": classification,
                "paths": entries,
            }
        )

    return {
        "scanned_files": len(rel_paths),
        "duplicate_basenames": len(pairs),
        "summary": dict(sorted(summary.items())),
        "pairs": pairs,
    }


def format_human_report(report: dict[str, object]) -> str:
    lines = [
        "Module facade audit (read-only, git ls-files)",
        f"scanned_files={report['scanned_files']}",
        f"duplicate_basenames={report['duplicate_basenames']}",
        "",
        "classification_summary:",
    ]
    summary = report.get("summary") or {}
    if not summary:
        lines.append("  (no duplicate basenames)")
    else:
        for key, count in sorted(summary.items()):
            lines.append(f"  {key}: {count}")

    lines.append("")
    lines.append("pairs:")
    for pair in report.get("pairs") or []:
        basename = pair["basename"]
        classification = pair["classification"]
        path_bits: list[str] = []
        for entry in pair["paths"]:
            role = "facade" if entry["is_facade"] else "impl"
            root_tag = "root" if entry["is_root"] else "sub"
            path_bits.append(f"{entry['path']} ({root_tag},{role})")
        lines.append(f"  {basename}: {classification}")
        lines.append(f"    {' + '.join(path_bits)}")
    return "\n".join(lines) + "\n"


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--json", action="store_true", help="Print machine-readable JSON to stdout")
    p.add_argument(
        "--fail-on-manual-review",
        action="store_true",
        help="Exit 1 when any pair needs manual review",
    )
    return p


def main() -> int:
    args = build_parser().parse_args()
    report = build_report()

    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        print(format_human_report(report), end="")

    if args.fail_on_manual_review:
        manual = int((report.get("summary") or {}).get("needs_manual_review_true_duplicate_or_special_case", 0))
        if manual > 0:
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
