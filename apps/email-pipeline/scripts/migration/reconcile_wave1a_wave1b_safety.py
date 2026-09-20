#!/usr/bin/env python3
# -----------------------------------------------------------------------------
# SAFETY (read-only): Reads the immutable Wave 1A bundle, its RFC 2047 addendum
# and the Wave 1B bundle, and never writes into any of them. Opens no database,
# mailbox or hosted service. Writes only the reconciliation report and its
# SHA-256 sidecar into the --output-dir the operator names, which must be
# outside every Git working tree. No address is ever printed.
# See docs/SCRIPT_MAP.md — "Read-only QA / report scripts".
# -----------------------------------------------------------------------------
"""Measure the cross-wave V1 outbound-safety baseline.

``docs/DATA.md`` §7 and §7.5.1 record each wave's own raw counts. Neither
records how many distinct addresses the two waves protect together, because
they overlap by an unknown amount::

    wave1a_safety     = deduplicate(wave1a_contacted_union | wave1a_rfc2047_addendum)
    cross_wave_safety = deduplicate(wave1a_safety | wave1b_combined_prior_contact)

This script reconstructs both sets from the artifacts themselves — never from a
reported total — verifies every available checksum first, measures the exact
intersection and union, and writes an owner-only report carrying counts only.

Usage:

    uv run python scripts/migration/reconcile_wave1a_wave1b_safety.py \\
      --wave1a-dir <extracted Wave 1A bundle directory> \\
      --wave1a-addendum <Wave 1A RFC 2047 addendum .jsonl> \\
      --wave1b-dir <extracted Wave 1B bundle directory> \\
      --output-dir <directory outside every Git repository>
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from origenlab_email_pipeline.migration.bundle import OutputPathError  # noqa: E402
from origenlab_email_pipeline.migration.cross_wave_safety import (  # noqa: E402
    ReconciliationRefused,
    reconcile_cross_wave_safety,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="reconcile_wave1a_wave1b_safety",
        description=(
            "Reconcile the Wave 1A and Wave 1B outbound-safety address sets from the "
            "immutable artifacts. Both bundles are read-only and are never modified."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--wave1a-dir",
        required=True,
        type=Path,
        help="Extracted Wave 1A bundle directory (read-only; never modified).",
    )
    parser.add_argument(
        "--wave1a-addendum",
        required=True,
        type=Path,
        help="The separately hashed Wave 1A RFC 2047 addendum .jsonl (docs/DATA.md §7.4).",
    )
    parser.add_argument(
        "--wave1b-dir",
        required=True,
        type=Path,
        help="Extracted Wave 1B bundle directory (read-only; never modified).",
    )
    parser.add_argument(
        "--output-dir",
        required=True,
        type=Path,
        help="Where to write the report. Refused if inside any Git working tree.",
    )
    parser.add_argument(
        "--wave1a-archive",
        type=Path,
        default=None,
        help="The Wave 1A .tar.gz. Defaults to <wave1a-dir>.tar.gz.",
    )
    parser.add_argument(
        "--wave1b-archive",
        type=Path,
        default=None,
        help="The Wave 1B .tar.gz. Defaults to <wave1b-dir>.tar.gz.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = reconcile_cross_wave_safety(
            wave1a_dir=args.wave1a_dir,
            wave1a_addendum_path=args.wave1a_addendum,
            wave1b_dir=args.wave1b_dir,
            output_dir=args.output_dir,
            wave1a_archive=args.wave1a_archive,
            wave1b_archive=args.wave1b_archive,
        )
    except (ReconciliationRefused, OutputPathError) as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2

    for line in result.console_lines:
        print(line)
    print(f"report: {result.path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
