#!/usr/bin/env python3
"""Safe offline SQLite compaction via VACUUM INTO (never in-place VACUUM).

Default is **preflight only** (zero writes). Writes require both
``--confirm-offline-copy`` and ``--apply``.

Source must be a verified offline snapshot with no ``-wal``/``-shm``/``-journal``.
It is opened ``mode=ro&immutable=1``. Destination must not exist and is published
via a unique ``.partial`` + hard-link no-clobber.

Never swaps the candidate into production. Never deletes the source snapshot.
Never treats historical/referenced body rows as deletable.

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

from origenlab_email_pipeline.config import load_settings
from origenlab_email_pipeline.qa.sqlite_offline_compaction import (
    CompactionError,
    CompactionOptions,
    run_offline_compaction,
)
from origenlab_email_pipeline.qa.sqlite_online_backup import (
    sanitize_error_message,
    sanitize_path_for_log,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source",
        type=Path,
        required=True,
        help="Verified offline SQLite snapshot (opened mode=ro&immutable=1)",
    )
    parser.add_argument(
        "--destination",
        type=Path,
        required=True,
        help="New compacted candidate path (must not exist)",
    )
    parser.add_argument(
        "--confirm-offline-copy",
        action="store_true",
        help="Acknowledge --source is a verified frozen offline/backup copy",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Execute VACUUM INTO compaction (default is preflight-only)",
    )
    parser.add_argument(
        "--allow-same-filesystem",
        action="store_true",
        help="Emergency/test override: allow destination on the same filesystem",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print sanitized JSON preflight/manifest to stdout",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    options = CompactionOptions(
        source=args.source.expanduser(),
        destination=args.destination.expanduser(),
        confirm_offline_copy=bool(args.confirm_offline_copy),
        apply=bool(args.apply),
        allow_same_filesystem=bool(args.allow_same_filesystem),
        settings=load_settings(),
        progress_sink=lambda msg: print(msg, file=sys.stderr),
    )
    try:
        report = run_offline_compaction(options)
    except CompactionError as exc:
        print(f"ERROR: {sanitize_error_message(exc)}", file=sys.stderr)
        return 2
    except (PermissionError, FileNotFoundError, OSError) as exc:
        print(f"ERROR: {sanitize_error_message(exc)}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"ERROR: {sanitize_error_message(exc)}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        mode = report.get("mode") or ("apply" if report.get("completed") else "unknown")
        print(f"mode={mode}")
        print(f"completed={report.get('completed')}")
        print(f"source={sanitize_path_for_log(options.source)}")
        print(f"destination={sanitize_path_for_log(options.destination)}")
        if report.get("completed"):
            print(f"destination_size_bytes={report.get('destination_size_bytes')}")
            print(f"estimated_reclaimed_bytes={report.get('estimated_reclaimed_bytes')}")
        else:
            print(f"writes_performed={report.get('writes_performed')}")
            print(f"capacity_required_bytes={report.get('capacity_required_bytes')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
