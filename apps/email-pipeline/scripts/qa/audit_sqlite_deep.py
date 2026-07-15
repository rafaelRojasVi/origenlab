#!/usr/bin/env python3
"""Read-only SQLite deep forensic audit (offline copy only for heavy phases).

Opens SQLite with URI ``mode=ro`` and ``PRAGMA query_only=ON``. Never runs VACUUM,
ANALYZE, REINDEX, wal_checkpoint, or any DML/DDL.

Heavy phases (dbstat, column-byte profiling, duplicate/usefulness analysis, full
``integrity_check``) require ``--confirm-offline-copy`` and refuse the configured
production SQLite path even when confirmed.

Synthetic / verified offline copies only for heavy work. See
``docs/SQLITE_STORAGE_MAINTENANCE.md``.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_ROOT / "src"))

from origenlab_email_pipeline.config import load_settings
from origenlab_email_pipeline.qa.sqlite_deep_audit import (
    ALL_PHASE_NAMES,
    HEAVY_PHASE_NAMES,
    LIGHT_PHASE_NAMES,
    AuditOptions,
    is_configured_production_db,
    render_markdown_summary,
    run_audit,
    validate_heavy_access,
    write_outputs,
)


def _parse_phases(raw: str | None) -> frozenset[str]:
    if not raw:
        return frozenset(ALL_PHASE_NAMES)
    names = {p.strip() for p in raw.split(",") if p.strip()}
    unknown = names - ALL_PHASE_NAMES
    if unknown:
        raise ValueError(f"unknown phases: {sorted(unknown)}")
    return frozenset(names)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--db",
        type=Path,
        default=None,
        help="SQLite path (default: settings resolved_sqlite_path; heavy phases refuse production)",
    )
    parser.add_argument(
        "--confirm-offline-copy",
        action="store_true",
        help="Acknowledge --db is a verified offline/backup copy (required for heavy phases)",
    )
    parser.add_argument(
        "--phases",
        default=None,
        help=f"Comma-separated phases (default: all). Choices: {', '.join(sorted(ALL_PHASE_NAMES))}",
    )
    parser.add_argument(
        "--light-only",
        action="store_true",
        help=f"Run only light phases: {', '.join(sorted(LIGHT_PHASE_NAMES))}",
    )
    parser.add_argument(
        "--full-integrity-check",
        action="store_true",
        help="Opt-in PRAGMA integrity_check (can take hours on large DBs)",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Resume from checkpoint JSON in --output-dir",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Directory for JSON/Markdown/checkpoint outputs",
    )
    parser.add_argument("--json", action="store_true", help="Print JSON report to stdout")
    parser.add_argument(
        "--markdown",
        action="store_true",
        help="Print sanitized Markdown summary to stdout",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    settings = load_settings()
    db = (args.db or settings.resolved_sqlite_path()).resolve()

    try:
        phases = _parse_phases(args.phases)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    if args.light_only:
        phases = frozenset(LIGHT_PHASE_NAMES)

    options = AuditOptions(
        db=db,
        confirm_offline_copy=bool(args.confirm_offline_copy),
        phases=phases,
        full_integrity_check=bool(args.full_integrity_check),
        resume=bool(args.resume),
        output_dir=args.output_dir,
        settings=settings,
    )

    err = validate_heavy_access(options)
    if err:
        print(f"ERROR: {err}", file=sys.stderr)
        if is_configured_production_db(db, settings):
            print(
                "Hint: copy the database to separate storage and pass --db <copy> "
                "--confirm-offline-copy.",
                file=sys.stderr,
            )
        return 2

    if options.full_integrity_check:
        print(
            "WARNING: --full-integrity-check may take hours on multi-GiB databases.",
            file=sys.stderr,
        )

    try:
        report = run_audit(options)
    except (PermissionError, FileNotFoundError, RuntimeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    out_dir = options.output_dir or (db.parent / "sqlite_deep_audit_out")
    json_path, md_path = write_outputs(report, out_dir)

    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    elif args.markdown:
        print(render_markdown_summary(report))
    else:
        print(f"Wrote {json_path}")
        print(f"Wrote {md_path}")
        print(f"Database: {report.get('database_basename')}")
        print(f"Phases: {', '.join(sorted(report.get('phases', {}).keys()))}")
        conclusions = report.get("conclusions") or {}
        print(f"Structural corruption: {conclusions.get('structural_corruption')}")
        print(f"Duplication present: {conclusions.get('duplication_present')}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
