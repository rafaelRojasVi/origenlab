#!/usr/bin/env python3
"""Read-only commercial truth audit (PR1).

Requires explicit --sqlite-path and --output-dir. Never falls back to production DB.
Does not send email, mutate Gmail, write SQLite/Postgres, or change classifications.

Example::

  uv run python scripts/qa/audit_commercial_truth.py \\
    --sqlite-path /explicit/path/to/emails.sqlite \\
    --output-dir reports/out/active/current/commercial_truth_audit
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_ROOT / "src"))

from origenlab_email_pipeline.qa.commercial_truth_audit import (  # noqa: E402
    connect_sqlite_readonly,
    require_explicit_paths,
    run_commercial_truth_audit,
)
from origenlab_email_pipeline.qa.commercial_truth_audit.readonly import (  # noqa: E402
    CommercialTruthAuditPathError,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--sqlite-path",
        "--db",
        type=Path,
        dest="sqlite_path",
        required=True,
        help="Explicit SQLite path (required; no settings fallback).",
    )
    parser.add_argument(
        "--output-dir",
        "--out-dir",
        type=Path,
        dest="output_dir",
        required=True,
        help="Explicit output directory (required).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        sqlite_path, output_dir = require_explicit_paths(
            sqlite_path=args.sqlite_path,
            output_dir=args.output_dir,
        )
    except CommercialTruthAuditPathError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    conn = connect_sqlite_readonly(sqlite_path)
    try:
        result = run_commercial_truth_audit(
            conn,
            sqlite_path=sqlite_path,
            output_dir=output_dir,
        )
    finally:
        conn.close()

    metrics = result.summary.get("metrics") or {}
    print(f"commercial_truth_audit: wrote {result.output_dir}")
    print(
        "headline: "
        f"prospects={metrics.get('prospect_rows', 0)} "
        f"already_contacted={metrics.get('already_contacted_count', 0)} "
        f"campaign_only_pct={metrics.get('already_contacted_campaign_recipient_only_pct', 0)} "
        f"hidden_active={metrics.get('active_cases_hidden_in_generic_buckets_count', 0)} "
        f"suppressed_leakage={metrics.get('suppressed_recipient_leakage', 0)}"
    )
    return result.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
