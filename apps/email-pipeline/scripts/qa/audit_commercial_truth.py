#!/usr/bin/env python3
"""Read-only commercial truth audit (PR1).

Requires explicit --sqlite-path and --output-dir. Never falls back to production DB.
By default --output-dir must resolve under gitignored apps/email-pipeline/reports/out/.
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
        help="Explicit output directory under reports/out/ (required).",
    )
    parser.add_argument(
        "--allow-output-outside-report-root",
        action="store_true",
        help="Permit --output-dir outside gitignored reports/out/ (tests/tmp only).",
    )
    parser.add_argument(
        "--skip-sent-campaign-quality",
        action="store_true",
        help="Skip Sent-folder campaign quality scan (prospect cohort audit only).",
    )
    parser.add_argument(
        "--sent-campaign-since-days",
        type=int,
        default=365,
        help="Lookback window for sent_campaign_quality.csv (default 365).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        sqlite_path, output_dir = require_explicit_paths(
            sqlite_path=args.sqlite_path,
            output_dir=args.output_dir,
            allow_output_outside_report_root=bool(args.allow_output_outside_report_root),
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
            include_sent_campaign_quality=not args.skip_sent_campaign_quality,
            sent_campaign_since_days=int(args.sent_campaign_since_days),
        )
    finally:
        conn.close()

    metrics = result.summary.get("metrics") or {}
    flat = metrics.get("_flat") if isinstance(metrics, dict) else {}
    print(f"commercial_truth_audit: wrote {result.output_dir}")
    print(
        "headline: "
        f"prospects={flat.get('prospect_rows', 0)} "
        f"already_contacted={flat.get('already_contacted_count', 0)} "
        f"current_fulfillment_pct={flat.get('already_contacted_current_fulfillment_or_post_sale_pct', 0)} "
        f"history_only_pct={flat.get('already_contacted_customer_or_commercial_history_pct', 0)} "
        f"cohort_dup_rate_valid={flat.get('prospect_cohort_duplicate_rate_of_valid_email_rows', 0)} "
        f"hidden_active={flat.get('active_cases_hidden_in_generic_buckets_count', 0)} "
        f"cohort_suppressed={flat.get('prospect_cohort_current_suppressed_count', 0)}"
    )
    return result.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
