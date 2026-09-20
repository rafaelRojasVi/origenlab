#!/usr/bin/env python3
# -----------------------------------------------------------------------------
# SAFETY (read-only): Reads the immutable Wave 1A bundle and never writes into
# it. Opens no database, mailbox or hosted service. Writes only the addendum and
# its SHA-256 sidecar into the --output-dir the operator names, which must be
# outside every Git working tree.
# See docs/SCRIPT_MAP.md — "Read-only QA / report scripts".
# -----------------------------------------------------------------------------
"""Re-derive the separate, independently hashed Wave 1A RFC 2047 addendum.

``docs/DATA.md`` §7.1 records three RFC 2047 decoded addresses absent from the
Wave 1A contacted union, to load with ``source = wave1a_rfc2047_addendum``, and
states that they are a separate loader input hashed independently — the
immutable bundle is **never** edited to include them.

That file was never materialized. The addresses are preserved inside the bundle
at ``reports/parse_failure_summary.json``
(``rfc2047_diagnostic.recovered_addresses_not_in_contacted_union``). This script
reconstitutes the loader input from that recorded evidence: it decodes nothing,
infers nothing and invents nothing, verifies every file it reads against the
bundle's own ``SHA256SUMS``, re-checks each address against the bundle's
recipient ledger, and never prints an address.

Usage:

    uv run python scripts/migration/derive_wave1a_rfc2047_addendum.py \\
      --bundle-dir <extracted Wave 1A bundle directory> \\
      --output-dir <directory outside every Git repository>
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from origenlab_email_pipeline.migration.bundle import OutputPathError  # noqa: E402
from origenlab_email_pipeline.migration.rfc2047_addendum import (  # noqa: E402
    AddendumRefused,
    derive_wave1a_rfc2047_addendum,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="derive_wave1a_rfc2047_addendum",
        description=(
            "Re-derive the Wave 1A RFC 2047 addendum from the immutable bundle. The "
            "bundle is read-only and is never edited, regenerated or relabelled."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--bundle-dir",
        required=True,
        type=Path,
        help="Extracted Wave 1A bundle directory (read-only; never modified).",
    )
    parser.add_argument(
        "--output-dir",
        required=True,
        type=Path,
        help="Where to write the addendum. Refused if inside any Git working tree.",
    )
    parser.add_argument(
        "--expect-count",
        type=int,
        default=None,
        help="Operator hint only. A mismatch prints a warning; the derived count is the fact.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = derive_wave1a_rfc2047_addendum(
            bundle_dir=args.bundle_dir,
            output_dir=args.output_dir,
            expect_count=args.expect_count,
        )
    except (AddendumRefused, OutputPathError) as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2

    for line in result.console_lines:
        print(line)
    print(f"addendum: {result.path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
