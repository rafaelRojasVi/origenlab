#!/usr/bin/env python3
"""Safe SQLite Online Backup API (never plain cp/rsync of a live WAL database).

Default is **preflight only** (zero writes). Pass ``--apply`` to create lock,
``.partial``, backup, and completed manifest.

Source is opened URI ``mode=ro``. Destination must not already exist and is
written through a script-owned ``.partial`` file. A backup is **completed**
only when the final DB and completed final manifest both exist.

Planned operator destination (do not assume it exists yet)::

  /mnt/d/origenlab-sqlite-offline/

Does not run VACUUM, integrity_check, dbstat, or the deep audit.
Does not mutate Gmail/Postgres/cron/systemd.

See ``docs/SQLITE_STORAGE_MAINTENANCE.md``.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_ROOT / "src"))

from origenlab_email_pipeline.qa.sqlite_online_backup import (
    DEFAULT_PAGES_PER_BATCH,
    BackupError,
    BackupOptions,
    build_safe_cli_json_report,
    run_online_backup,
    sanitize_error_message,
    sanitize_path_for_log,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source",
        type=Path,
        required=True,
        help="Source SQLite path (opened read-only via URI mode=ro)",
    )
    parser.add_argument(
        "--destination",
        type=Path,
        required=True,
        help="Destination SQLite path (must not exist; written via .partial then rename)",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Execute backup (default is preflight-only with zero writes)",
    )
    parser.add_argument(
        "--pages-per-batch",
        type=int,
        default=DEFAULT_PAGES_PER_BATCH,
        help=(
            f"Positive page batch size for Connection.backup "
            f"(default {DEFAULT_PAGES_PER_BATCH}; pages=0 forbidden)"
        ),
    )
    parser.add_argument(
        "--allow-same-filesystem",
        action="store_true",
        help="Emergency/test override: allow destination on the same filesystem as source",
    )
    parser.add_argument(
        "--busy-timeout-ms",
        type=int,
        default=30_000,
        help="SQLite busy timeout in milliseconds",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print allowlisted CLI JSON report to stdout (closed projection)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    options = BackupOptions(
        source=args.source.expanduser(),
        destination=args.destination.expanduser(),
        apply=bool(args.apply),
        pages_per_batch=int(args.pages_per_batch),
        allow_same_filesystem=bool(args.allow_same_filesystem),
        busy_timeout_ms=int(args.busy_timeout_ms),
    )
    try:
        if options.apply:
            print(
                f"Starting online backup (--apply): src={sanitize_path_for_log(options.source)} "
                f"dst={sanitize_path_for_log(options.destination)} "
                f"pages_per_batch={options.pages_per_batch}",
                file=sys.stderr,
            )
        else:
            print(
                f"Preflight only (no writes): src={sanitize_path_for_log(options.source)} "
                f"dst={sanitize_path_for_log(options.destination)}. "
                "Re-run with --apply to execute.",
                file=sys.stderr,
            )
        result = run_online_backup(options)
        if args.json:
            report = build_safe_cli_json_report(result)
            print(json.dumps(report, indent=2, sort_keys=True))
            return 0
    except BackupError as exc:
        print(f"ERROR: {sanitize_error_message(exc)}", file=sys.stderr)
        detail = exc.detail if isinstance(exc.detail, dict) else None
        if detail:
            op = detail.get("operational_error")
            if isinstance(op, dict):
                print(
                    "operational_error "
                    f"category={op.get('category')} "
                    f"phase={op.get('phase')} "
                    f"sqlite_errorcode={op.get('sqlite_errorcode')} "
                    f"sqlite_errorname={op.get('sqlite_errorname')} "
                    f"retryable={op.get('retryable')} "
                    f"recovery={op.get('recovery')}",
                    file=sys.stderr,
                )
            fd = detail.get("fd_observation")
            if isinstance(fd, dict):
                print(
                    "fd_observation "
                    f"verdict={fd.get('verdict')} "
                    f"blocker_count={fd.get('blocker_count')} "
                    f"ambiguous_count={fd.get('ambiguous_count')}",
                    file=sys.stderr,
                )
        return 2
    except Exception as exc:
        print(f"ERROR: {sanitize_error_message(exc)}", file=sys.stderr)
        return 1

    if result.get("mode") == "preflight":
        print(f"mode=preflight writes_performed={result.get('writes_performed')}")
        print(f"source_basename={result.get('source_basename')}")
        print(f"destination_basename={result.get('destination_basename')}")
        print(f"source_size_bytes={result.get('source_size_bytes')}")
        print(f"destination_free_bytes={result.get('destination_free_bytes')}")
        print(f"capacity_required_bytes={result.get('capacity_required_bytes')}")
        print(f"filesystem_separated={result.get('filesystem_separated')}")
        print(f"pages_per_batch={result.get('pages_per_batch')}")
    else:
        print(
            f"Backup completed: {sanitize_path_for_log(options.destination)} "
            f"({result.get('destination_size_bytes')} bytes, "
            f"{result.get('elapsed_seconds')}s)",
            file=sys.stderr,
        )
        print(f"completed={result.get('completed')}")
        print(f"source_mutated_by_utility={result.get('source_mutated_by_utility')}")
        print(f"directory_fsync_supported={result.get('directory_fsync_supported')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
