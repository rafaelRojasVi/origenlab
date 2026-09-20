#!/usr/bin/env python3
# -----------------------------------------------------------------------------
# SAFETY (read-only): Opens the V1 SQLite source `mode=ro` with a proven
# `PRAGMA query_only=ON` inside one deferred read transaction. Writes nothing to
# any database, mailbox or hosted service, and makes no Gmail network call — the
# Sent evidence is the already-ingested `emails` rows. Never pauses, stops or
# modifies the ingest cron. Writes only into the --output-dir the operator
# names, which must be outside every Git working tree; every artifact is
# owner-only (0700 directories, 0600 files).
# See docs/SCRIPT_MAP.md — "Read-only QA / report scripts".
# -----------------------------------------------------------------------------
"""Extract the Wave 1B September 2026 outbound campaign safety bundle.

Wave 1A (``docs/DATA.md`` §7) is an immutable historical snapshot from
2026-09-05 and is never revised, replaced, regenerated or relabelled by this
script. Wave 1B is a separate, additive bundle.

Usage (read-only; every path is explicit — nothing defaults to production):

    uv run python scripts/migration/extract_wave1b_v1_safety_bundle.py \\
      --sqlite-path <V1 runtime SQLite path> \\
      --output-dir <directory outside every Git repository> \\
      --wave1a-snapshot-at 2026-09-05T04:24:25Z \\
      --operator-manifest reports/out/active/current/manifest.json

Counts are always measured from the database. ``--expect-remaining-candidates``
only raises a warning on mismatch; it is never treated as truth.

The combined prior-contact delta is the deduplicated union of campaign
execution facts, canonical Gmail **Sent**-recipient evidence and operator
outreach state. The Sent evidence is read through the live outbound gate's own
functions against the already-ingested ``emails`` rows; missing, mismatched or
undatable Sent history refuses the run and there is no override.

Unrestricted operator free text — notes, reasons, evidence, raw API error
strings — never leaves the source database: only a presence flag and a one-way
digest of the normalized original travel.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from origenlab_email_pipeline.migration.bundle import OutputPathError  # noqa: E402
from origenlab_email_pipeline.migration.readonly_source import (  # noqa: E402
    ReadOnlyProofError,
)
from origenlab_email_pipeline.migration.wave1b_extract import (  # noqa: E402
    SEPTEMBER_CAMPAIGN_IDS,
    ExtractionRefused,
    ExtractionRequest,
    extract,
)
from origenlab_email_pipeline.timeutil import now_iso  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="extract_wave1b_v1_safety_bundle",
        description=(
            "Read-only Wave 1B extraction of the two September 2026 outbound campaigns "
            "(mode=ro URI plus a verified PRAGMA query_only=ON, one deferred read "
            "transaction). Writes no database, no mailbox and no hosted service."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Read-only proof: the source is opened with a 'mode=ro' URI, "
            "'PRAGMA query_only=ON' is set and read back before any query, and every "
            "read happens inside one BEGIN DEFERRED transaction whose data_version is "
            "compared across the window. The run aborts before reading anything if "
            "query_only cannot be proven.\n\n"
            "Sent history: the combined prior-contact delta reuses the live outbound "
            "gate's own functions over the already-ingested `emails` rows.\n"
            "No Gmail call is made, and missing, mismatched or undatable Sent "
            "evidence refuses the run with no override.\n\n"
            "Free text: operator notes, reasons, evidence and raw API error strings "
            "never leave the source database — only a presence flag and a one-way "
            "digest travel.\n\n"
            "Permissions: every artifact is owner-only (0700 directories, 0600 "
            "files), written atomically so none ever exists at a wider mode."
        ),
    )
    parser.add_argument(
        "--sqlite-path",
        required=True,
        type=Path,
        help="V1 runtime SQLite database to read. Required: nothing defaults to production.",
    )
    parser.add_argument(
        "--output-dir",
        required=True,
        type=Path,
        help=(
            "Directory to write the bundle into. Required, and refused if it lies "
            "inside any Git working tree."
        ),
    )
    parser.add_argument(
        "--wave1a-snapshot-at",
        required=True,
        help="Wave 1A snapshot instant (UTC, YYYY-MM-DDTHH:MM:SSZ) — the delta baseline.",
    )
    parser.add_argument(
        "--campaign-id",
        action="append",
        dest="campaign_ids",
        help=f"Campaign to extract; repeatable. Default: {', '.join(SEPTEMBER_CAMPAIGN_IDS)}",
    )
    parser.add_argument(
        "--gmail-user",
        default=None,
        help=(
            "Mailbox whose canonical Gmail Sent history is read for the combined "
            "prior-contact delta. Defaults to the shared outbound fallback "
            "(outbound_core.DEFAULT_GMAIL_USER_FALLBACK). No Gmail call is made."
        ),
    )
    parser.add_argument(
        "--sent-folder",
        action="append",
        dest="sent_folders",
        help=(
            "Exact IMAP Sent label; repeatable. Defaults to the shared pair in "
            "marketing_export_context.DEFAULT_SENT_FOLDERS. A folder that matches "
            "no ingested row refuses the run."
        ),
    )
    parser.add_argument(
        "--operator-manifest",
        type=Path,
        default=None,
        help=(
            "Documented operator manifest whose before/after freshness is recorded "
            "(integrity facts and status scalars only; never its paths or contents)."
        ),
    )
    parser.add_argument(
        "--run-timestamp",
        default=None,
        help=(
            "Pin the run instant (UTC, YYYY-MM-DDTHH:MM:SSZ) so a rerun is byte-identical. "
            "Defaults to now."
        ),
    )
    parser.add_argument(
        "--bundle-name",
        default=None,
        help="Override the bundle directory name. Defaults to <run timestamp>_wave1b_v1_safety_bundle.",
    )
    parser.add_argument(
        "--expect-remaining-candidates",
        type=int,
        default=None,
        help=(
            "Operator hint only. A mismatch prints a warning; the measured count is "
            "always the fact."
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    request = ExtractionRequest(
        sqlite_path=args.sqlite_path,
        output_dir=args.output_dir,
        wave1a_snapshot_at=args.wave1a_snapshot_at,
        run_timestamp=args.run_timestamp or now_iso(),
        campaign_ids=tuple(args.campaign_ids or SEPTEMBER_CAMPAIGN_IDS),
        operator_manifest=args.operator_manifest,
        expected_remaining_candidates=args.expect_remaining_candidates,
        bundle_name=args.bundle_name,
        gmail_user=args.gmail_user,
        sent_folders=tuple(args.sent_folders) if args.sent_folders else None,
    )
    try:
        result = extract(request)
    except (ExtractionRefused, OutputPathError, ReadOnlyProofError) as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2

    for line in result.console_lines:
        print(line)
    print(f"bundle: {result.bundle_dir}")
    print(f"archive: {result.archive}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
