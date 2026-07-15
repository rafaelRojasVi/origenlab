#!/usr/bin/env python3
"""Safe SQLite Online Backup API (never plain cp/rsync of a live WAL database).

Creates a consistent destination snapshot via ``sqlite3.Connection.backup()``.
Source is opened URI ``mode=ro``. Destination must not already exist and is
written through a script-owned ``.partial`` file, then atomically renamed.

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
    run_online_backup,
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
        "--pages-per-batch",
        type=int,
        default=DEFAULT_PAGES_PER_BATCH,
        help=f"Positive page batch size for backup.step (default {DEFAULT_PAGES_PER_BATCH}; pages=0 forbidden)",
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
        help="Print sanitized JSON manifest to stdout",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    options = BackupOptions(
        source=args.source.expanduser(),
        destination=args.destination.expanduser(),
        pages_per_batch=int(args.pages_per_batch),
        allow_same_filesystem=bool(args.allow_same_filesystem),
        busy_timeout_ms=int(args.busy_timeout_ms),
    )
    try:
        print(
            f"Starting online backup: src={sanitize_path_for_log(options.source)} "
            f"dst={sanitize_path_for_log(options.destination)} "
            f"pages_per_batch={options.pages_per_batch}",
            file=sys.stderr,
        )
        manifest = run_online_backup(options)
    except BackupError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(manifest, indent=2, sort_keys=True))
    else:
        print(
            f"Backup completed: {sanitize_path_for_log(options.destination)} "
            f"({manifest.get('destination_size_bytes')} bytes, "
            f"{manifest.get('elapsed_seconds')}s)",
            file=sys.stderr,
        )
        print(f"completed={manifest.get('completed')}")
        print(f"source_mutated_by_utility={manifest.get('source_mutated_by_utility')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
