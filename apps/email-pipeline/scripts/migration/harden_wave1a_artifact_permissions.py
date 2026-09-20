#!/usr/bin/env python3
# -----------------------------------------------------------------------------
# SAFETY (metadata only): Changes the filesystem MODE of one named artifact
# boundary — a bundle directory, its archive and that archive's .sha256 sidecar
# — to 0700/0600. Writes no content, renames nothing, sets no timestamp, and
# never touches the enclosing directory or anything outside the named boundary.
# Verifies every checksum before and after, and refuses if any digest moves.
# Opens no database, mailbox or hosted service. Prints no address.
# See docs/SCRIPT_MAP.md — "Read-only QA / report scripts".
# -----------------------------------------------------------------------------
"""Tighten the Wave 1A evidence artifact to owner-only.

The Wave 1B bundle is written owner-only by construction; the Wave 1A bundle
predates that policy and sits at `0755` / `0644`, readable by every local
account. Nothing in it is group- or world-*writable*, so the evidence is intact
— it is simply not private. This script closes that gap for exactly one named
boundary and refuses anything else.

Usage:

    uv run python scripts/migration/harden_wave1a_artifact_permissions.py \\
      --bundle-dir <extracted Wave 1A bundle directory> \\
      --archive <that bundle's .tar.gz> \\
      --expect-manifest-sha256 <docs/DATA.md §7 value> \\
      --expect-archive-sha256 <docs/DATA.md §7 value>

Add `--dry-run` to verify and report what would change without changing it.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from origenlab_email_pipeline.migration.artifact_permissions import (  # noqa: E402
    HardeningRefused,
    harden_artifact_permissions,
)
from origenlab_email_pipeline.migration.cross_wave_safety import (  # noqa: E402
    WAVE1A_ARCHIVE_SHA256,
    WAVE1A_MANIFEST_SHA256,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="harden_wave1a_artifact_permissions",
        description=(
            "Tighten one named evidence artifact to 0700/0600. Verifies every "
            "checksum before and after; content is never written or renamed."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--bundle-dir",
        required=True,
        type=Path,
        help="The extracted bundle directory. Its tree is the boundary.",
    )
    parser.add_argument(
        "--archive",
        type=Path,
        default=None,
        help="The bundle's .tar.gz, to tighten alongside it.",
    )
    parser.add_argument(
        "--sidecar",
        type=Path,
        default=None,
        help="That archive's .sha256. Defaults to <archive>.sha256.",
    )
    parser.add_argument(
        "--expect-manifest-sha256",
        default=WAVE1A_MANIFEST_SHA256,
        help="The manifest.json hash docs/DATA.md §7 records. Verified before the change.",
    )
    parser.add_argument(
        "--expect-archive-sha256",
        default=WAVE1A_ARCHIVE_SHA256,
        help="The archive hash docs/DATA.md §7 records. Verified before the change.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Verify and report what would change, changing nothing.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = harden_artifact_permissions(
            bundle_dir=args.bundle_dir,
            archive=args.archive,
            sidecar=args.sidecar,
            expected_manifest_sha256=args.expect_manifest_sha256,
            expected_archive_sha256=args.expect_archive_sha256,
            dry_run=args.dry_run,
        )
    except HardeningRefused as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2

    for line in result.console_lines:
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
