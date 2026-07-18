#!/usr/bin/env python3
"""Synthetic writable-restore rehearsal CLI (throwaway fixtures only).

Operations (explicit):

* Default / ``--operation preflight`` — **zero-write** validation only
  (no mkdir, locks, fixtures, or sidecars).
* ``--operation build-fixture --apply --confirm-throwaway`` — create a tiny
  marked synthetic source under the scratch root (a write).
* ``--operation rehearse --apply --confirm-throwaway`` — stream-copy restore,
  writable transaction, two-phase rollback verification.

Fixture creation and rehearsal are **separate** apply operations.
Never opens the real ~61 GiB compact candidate or production SQLite.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_ROOT / "src"))

from origenlab_email_pipeline.qa.sqlite_online_backup import sanitize_error_message
from origenlab_email_pipeline.qa.sqlite_writable_restore_rehearsal import (
    COPY_CHUNK_BYTES,
    DEFAULT_SCRATCH_ROOT,
    EXIT_APPLY,
    EXIT_OK,
    MAX_SOURCE_BYTES,
    RehearsalError,
    RehearsalFailureCategory,
    RehearsalOptions,
    build_synthetic_source,
    run_rehearsal,
)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=(
            "Synthetic writable-restore rehearsal. Paths must live under the "
            "rehearsal scratch root and use throwaway|rehearsal|synthetic basenames. "
            f"Hard source size cap: {MAX_SOURCE_BYTES} bytes; "
            f"stream copy chunk: {COPY_CHUNK_BYTES} bytes."
        )
    )
    p.add_argument(
        "--operation",
        choices=("preflight", "build-fixture", "rehearse"),
        default="preflight",
        help=(
            "preflight=zero-write checks; build-fixture=create marked synthetic DB "
            "(requires --apply); rehearse=restore rehearsal (requires --apply)"
        ),
    )
    p.add_argument(
        "--source",
        type=Path,
        help="Synthetic source DB path (under scratch root; required for preflight/rehearse)",
    )
    p.add_argument(
        "--restore-target",
        type=Path,
        help="New no-clobber throwaway restore target (required for preflight/rehearse)",
    )
    p.add_argument(
        "--scratch-root",
        type=Path,
        default=DEFAULT_SCRATCH_ROOT,
        help=f"Rehearsal scratch root (default: {DEFAULT_SCRATCH_ROOT})",
    )
    p.add_argument(
        "--confirm-throwaway",
        action="store_true",
        help="Acknowledge paths are synthetic/throwaway only (not the real compact candidate)",
    )
    p.add_argument(
        "--apply",
        action="store_true",
        help="Perform the selected write operation (build-fixture or rehearse)",
    )
    p.add_argument("--json", action="store_true", help="Print sanitized JSON report")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.operation == "build-fixture":
            if args.source is None:
                raise RehearsalError(
                    "build-fixture requires --source as the fixture output path",
                    category=RehearsalFailureCategory.PREFLIGHT,
                )
            if args.restore_target is not None:
                raise RehearsalError(
                    "build-fixture does not take --restore-target "
                    "(fixture creation and rehearsal are separate operations)",
                    category=RehearsalFailureCategory.PREFLIGHT,
                )
            report = build_synthetic_source(
                args.source.expanduser(),
                scratch_root=args.scratch_root.expanduser(),
                confirm_throwaway=bool(args.confirm_throwaway),
                apply=bool(args.apply),
            )
        else:
            if args.source is None or args.restore_target is None:
                raise RehearsalError(
                    "preflight/rehearse require --source and --restore-target",
                    category=RehearsalFailureCategory.PREFLIGHT,
                )
            if args.operation == "rehearse" and not args.apply:
                raise RehearsalError(
                    "operation=rehearse requires --apply "
                    "(use operation=preflight for zero-write checks)",
                    category=RehearsalFailureCategory.PREFLIGHT,
                )
            if args.operation == "preflight" and args.apply:
                raise RehearsalError(
                    "operation=preflight is zero-write; use operation=rehearse --apply",
                    category=RehearsalFailureCategory.PREFLIGHT,
                )
            opts = RehearsalOptions(
                source=args.source.expanduser(),
                restore_target=args.restore_target.expanduser(),
                scratch_root=args.scratch_root.expanduser(),
                confirm_throwaway=bool(args.confirm_throwaway),
                apply=bool(args.apply) and args.operation == "rehearse",
            )
            report = run_rehearsal(opts)

        if args.json:
            print(json.dumps(report, indent=2, sort_keys=True))
        else:
            print(
                f"ok completed={report.get('completed')} mode={report.get('mode')} "
                f"apply={report.get('apply')}"
            )
        return EXIT_OK
    except RehearsalError as exc:
        msg = sanitize_error_message(str(exc))
        print(f"error: {msg}", file=sys.stderr)
        if exc.recovery:
            print(f"recovery: {sanitize_error_message(exc.recovery)}", file=sys.stderr)
        return int(exc.exit_code)
    except Exception:  # noqa: BLE001 — CLI boundary
        print("error: unexpected failure", file=sys.stderr)
        return EXIT_APPLY


if __name__ == "__main__":
    raise SystemExit(main())
