#!/usr/bin/env python3
"""Ingest Gmail All Mail + Trash so archived/trashed NDRs reach canonical SQLite."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
_INGEST_SCRIPT = _REPO / "scripts" / "ingest" / "05_workspace_gmail_imap_to_sqlite.py"

NDR_GMAIL_FOLDERS: tuple[str, ...] = (
    "[Gmail]/Todos",
    "[Gmail]/Papelera",
)


def build_commands(since_days: int) -> list[list[str]]:
    """Build bounded safe ingest commands for NDR-bearing non-Inbox folders."""
    if isinstance(since_days, bool) or not isinstance(since_days, int) or since_days < 0:
        raise ValueError(f"since_days must be a nonnegative integer, got {since_days!r}")

    return [
        [
            sys.executable,
            str(_INGEST_SCRIPT),
            "--folder",
            folder,
            "--skip-duplicate-message-id",
            "--since-days",
            str(since_days),
        ]
        for folder in NDR_GMAIL_FOLDERS
    ]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--since-days",
        type=int,
        default=2,
        help="Bound Gmail All Mail + Trash ingest to the last N days (default: 2)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    try:
        commands = build_commands(args.since_days)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    for folder, cmd in zip(NDR_GMAIL_FOLDERS, commands, strict=True):
        proc = subprocess.run(
            cmd,
            cwd=str(_REPO),
            check=False,
        )
        rc = int(proc.returncode)

        if rc != 0:
            print(
                f"[gmail-ingest-ndr] {folder} -> FAILED rc={rc}",
                file=sys.stderr,
            )
            return rc

        print(f"[gmail-ingest-ndr] {folder} -> OK rc=0")

    print(
        "NDR mailbox catch-up complete: "
        "[Gmail]/Todos (includes archived) + [Gmail]/Papelera."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
