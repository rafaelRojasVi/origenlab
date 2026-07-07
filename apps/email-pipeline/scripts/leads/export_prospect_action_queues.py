#!/usr/bin/env python3
"""Export Prospectos commercial action queue CSVs from the lead research SQLite mirror.

Read-only: writes CSV/JSON exports only; does not send email, mutate Gmail, or import leads.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from origenlab_email_pipeline.config import load_settings
from origenlab_email_pipeline.db import connect
from origenlab_email_pipeline.lead_research.prospect_action_queue_export import (
    DEFAULT_OUTPUT_DIR,
    export_prospect_action_queues,
)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--out-dir",
        type=Path,
        default=_ROOT / DEFAULT_OUTPUT_DIR,
        help=f"Output directory (default: {DEFAULT_OUTPUT_DIR}).",
    )
    ap.add_argument("--db", type=Path, default=None, help="SQLite path (default: from config).")
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="Compute queue counts without writing CSV/JSON files.",
    )
    args = ap.parse_args()

    settings = load_settings()
    db_path = args.db or settings.resolved_sqlite_path()
    if not db_path.is_file():
        print(f"SQLite file not found: {db_path}", file=sys.stderr)
        return 1

    with connect(db_path) as conn:
        summary = export_prospect_action_queues(conn, output_dir=args.out_dir, dry_run=args.dry_run)

    mode = "dry-run" if args.dry_run else "written"
    print(f"prospect action queues ({mode})")
    print(f"  total_active_prospects: {summary['total_active_prospects']}")
    for filename, count in summary["exports"].items():
        print(f"  {filename}: {count}")
    if not args.dry_run:
        print(f"  summary: {args.out_dir / 'prospect_action_queues_summary.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
