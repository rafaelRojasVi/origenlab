#!/usr/bin/env python3
"""Stage Gmail/Drive evidence into V2 as review candidates. Never as contacts.

**Dry-run by default.** Without `--apply` no database connection is opened at all: the
manifest is read, fully validated and reported, and nothing is written.

**This tool makes no network call.** It imports no Google client and holds no credential.
Its only input is a local JSON manifest an operator produced out-of-band, so everything
that can reach the database is a file a human can read, diff and refuse first. That is the
boundary, not a placeholder until credentials arrive.

What an apply writes: `evidence.source_record` (`review_status = 'pending'`) and
`evidence.assertion` (`resolution = 'unresolved'`). Nothing else. `crm.*` is counted before
and after inside the same transaction and any change rolls the whole pass back — so no
staged observation can become a person, an organization, an opportunity or a permission to
send. Deciding what staged evidence actually establishes is
`promote_evidence_into_crm.py`, a separate tool, run by a human who looked at the queue.

Manifest shape (`manifest_version: 1`):

    {
      "manifest_version": 1,
      "provider": "gmail",                       // or "drive"
      "note": "September inbox sweep, read-only export",
      "records": [
        {
          "external_id": "18f0c1a2b3",           // Gmail message id / Drive file id
          "source_uri": "gmail://msg/18f0c1a2b3",
          "acquired_at": "2026-09-21T10:00:00Z",
          "payload": {"subject": "...", "from": "..."},
          "observations": [
            {"kind": "contact_address",   "value": "compras@uni.example"},
            {"kind": "organization_name", "value": "Universidad Ejemplo"}
          ]
        }
      ]
    }

A `gmail` record may assert `contact_address` and `organization_name`. A `drive` record may
assert `document_reference` and `organization_name`. Nothing else is accepted: an
affiliation is a relationship no message header records, and `contacted_address` is a claim
about our own outbound history that only the send ledger may make.

Examples:
    # Dry run — reads the file, opens no database.
    uv run python scripts/migration/stage_gmail_drive_evidence.py \
        --manifest ~/data/origenlab-v2-local/evidence/gmail-2026-09.json

    # Apply, against a loopback database carrying migration 20260921120000.
    uv run python scripts/migration/stage_gmail_drive_evidence.py \
        --manifest ~/data/origenlab-v2-local/evidence/gmail-2026-09.json \
        --database-url postgresql://postgres:postgres@127.0.0.1:54332/origenlab_dev --apply
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_SRC = Path(__file__).resolve().parents[2] / "src"
if str(REPO_SRC) not in sys.path:  # pragma: no cover - script bootstrap
    sys.path.insert(0, str(REPO_SRC))

from origenlab_email_pipeline.migration.v2_evidence_stage.apply import (  # noqa: E402
    ApplyRefused,
    apply_staging,
)
from origenlab_email_pipeline.migration.v2_evidence_stage.manifest import (  # noqa: E402
    ManifestRefused,
    load_manifest,
)
from origenlab_email_pipeline.migration.v2_evidence_stage.report import (  # noqa: E402
    build_report,
    render_console,
)
from origenlab_email_pipeline.migration.v2_import.target import (  # noqa: E402
    TargetRefused,
    assert_local_target,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--manifest",
        required=True,
        help="path to a local JSON staging manifest (manifest_version 1)",
    )
    parser.add_argument(
        "--database-url",
        help="a loopback PostgreSQL DSN carrying the Slice 0 schema. Required with --apply; "
        "ignored without it, because a dry run opens no connection.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="write the staged records and assertions. Without this flag nothing is written "
        "and no database connection is opened.",
    )
    parser.add_argument(
        "--json", action="store_true", help="print the report as JSON instead of text."
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    try:
        manifest = load_manifest(args.manifest)
    except ManifestRefused as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2

    applied = None
    target_label = "none — dry run opens no connection"
    if args.apply:
        if not args.database_url:
            print("refused: --apply needs --database-url", file=sys.stderr)
            return 2
        try:
            target = assert_local_target(args.database_url)
        except TargetRefused as exc:
            print(f"refused: {exc}", file=sys.stderr)
            return 3
        target_label = target.redacted()
        try:
            applied = apply_staging(manifest, target)
        except ApplyRefused as exc:
            print(f"refused: {exc}", file=sys.stderr)
            return 5

    report = build_report(
        manifest,
        mode="apply" if args.apply else "dry-run",
        target=target_label,
        applied=applied,
    )
    print(json.dumps(report, indent=2, sort_keys=True) if args.json else render_console(report))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
