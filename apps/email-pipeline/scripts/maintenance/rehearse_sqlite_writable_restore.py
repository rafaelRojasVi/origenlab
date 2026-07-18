#!/usr/bin/env python3
"""Synthetic writable-restore rehearsal CLI (throwaway fixtures only).

Default is preflight (zero writes). ``--apply`` requires ``--confirm-throwaway``.
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
    EXIT_APPLY,
    EXIT_OK,
    EXIT_PREFLIGHT,
    EXIT_SAFETY,
    EXIT_VERIFY,
    RehearsalError,
    RehearsalOptions,
    build_synthetic_source,
    run_rehearsal,
)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=(
            "Synthetic writable-restore rehearsal. Use throwaway/synthetic paths only."
        )
    )
    p.add_argument(
        "--source",
        type=Path,
        help="Existing synthetic source DB (or use --build-synthetic-source)",
    )
    p.add_argument(
        "--restore-target",
        type=Path,
        required=True,
        help="New no-clobber throwaway restore target (basename must include throwaway|rehearsal|synthetic)",
    )
    p.add_argument(
        "--build-synthetic-source",
        type=Path,
        default=None,
        help="Create a tiny synthetic source at this path (no-clobber) then rehearse",
    )
    p.add_argument(
        "--confirm-throwaway",
        action="store_true",
        help="Acknowledge paths are synthetic/throwaway only (not the real compact candidate)",
    )
    p.add_argument(
        "--apply",
        action="store_true",
        help="Perform copy + writable transaction + rollback verification",
    )
    p.add_argument("--json", action="store_true", help="Print sanitized JSON report")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        source = args.source
        if args.build_synthetic_source is not None:
            build_synthetic_source(args.build_synthetic_source.expanduser())
            source = args.build_synthetic_source.expanduser()
        if source is None:
            raise RehearsalError("provide --source or --build-synthetic-source")
        opts = RehearsalOptions(
            source=source.expanduser(),
            restore_target=args.restore_target.expanduser(),
            confirm_throwaway=bool(args.confirm_throwaway),
            apply=bool(args.apply),
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
        low = msg.lower()
        if "production" in low or "basename" in low or "symlink" in low:
            return EXIT_SAFETY
        if "fingerprint" in low or "rollback" in low or "read-back" in low:
            return EXIT_VERIFY
        if args.apply:
            return EXIT_APPLY
        return EXIT_PREFLIGHT
    except Exception as exc:  # noqa: BLE001 — CLI boundary
        print(
            f"error: unexpected {type(exc).__name__}",
            file=sys.stderr,
        )
        return EXIT_APPLY


if __name__ == "__main__":
    raise SystemExit(main())
