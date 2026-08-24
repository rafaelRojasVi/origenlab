"""CLI orchestration for business mart build (break-glass rebuild path)."""

from __future__ import annotations

import argparse

from origenlab_email_pipeline.config import load_settings
from origenlab_email_pipeline.core.mart.build_options import MartBuildOptions
from origenlab_email_pipeline.core.mart.internal_domains import (
    resolve_mart_internal_domains,
)
from origenlab_email_pipeline.core.mart.build_runner import (
    ensure_fast_indexes,
    run_business_mart_build,
)
from origenlab_email_pipeline.db import connect
from origenlab_email_pipeline.freshness_dates import MART_DATE_SLACK_DAYS_DEFAULT
from origenlab_email_pipeline.pipeline_run_recorder import start_run
from origenlab_email_pipeline.sqlite_migrate import SchemaLayer, migrate_sqlite_schema

SCRIPT_NAME = "scripts/mart/build_business_mart.py"


def normalize_mart_date_slack_days(mart_slack: int) -> int:
    """Clamp invalid ``--mart-date-slack-days`` values to the default."""
    if mart_slack < 0 or mart_slack > 3660:
        return MART_DATE_SLACK_DAYS_DEFAULT
    return mart_slack


def _build_arg_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--internal-domain",
        action="append",
        default=[],
        help="repeatable; add operator-approved domains to canonical defaults",
    )
    ap.add_argument(
        "--limit-emails", type=int, default=None, help="debug: limit emails scanned"
    )
    ap.add_argument(
        "--rebuild", action="store_true", help="truncate and rebuild mart tables"
    )
    ap.add_argument(
        "--dashboard-fast",
        action="store_true",
        help="Fast daily mode (canonical/recent rows, skip heavy stages when safe).",
    )
    ap.add_argument(
        "--canonical-only",
        action="store_true",
        help="Process only canonical contacto Gmail source rows.",
    )
    ap.add_argument(
        "--since-days",
        type=int,
        default=None,
        help="Restrict email scan to date_iso within N days (best effort).",
    )
    ap.add_argument(
        "--skip-document-master-if-unchanged",
        action="store_true",
        help="Skip rebuilding document_master when attachment/extract signature is unchanged.",
    )
    ap.add_argument(
        "--mart-date-slack-days",
        type=int,
        default=MART_DATE_SLACK_DAYS_DEFAULT,
        help=(
            "Exclude email date_iso from mart first/last_seen (and document sent_at) when "
            "parsed calendar date is more than this many days after local today (default: "
            f"{MART_DATE_SLACK_DAYS_DEFAULT}). Raw emails table is never modified."
        ),
    )
    ap.add_argument(
        "--use-email-mart-features",
        action="store_true",
        help=(
            "Scan precomputed email_mart_features instead of emails bodies "
            "(requires build-email-mart-features --apply first)"
        ),
    )
    return ap


def run_build_business_mart_from_argv(argv: list[str] | None = None) -> int:
    """Run mart build from CLI args; returns 0 on success."""
    args = _build_arg_parser().parse_args(argv)

    settings = load_settings()
    db_path = settings.resolved_sqlite_path()
    conn = connect(db_path)
    migrate_sqlite_schema(conn, layers={SchemaLayer.ARCHIVE_AND_MART})
    ensure_fast_indexes(conn)

    run_id = start_run(
        conn,
        script_name=SCRIPT_NAME,
        notes="business mart build",
    )

    internal_domains = resolve_mart_internal_domains(
        args.internal_domain,
    )

    print(f"DB: {db_path}")
    print(f"Internal domains: {sorted(internal_domains)[:10]}")
    if args.dashboard_fast:
        print("[mode] dashboard-fast enabled")
    if args.canonical_only:
        print("[mode] canonical-only enabled")
    if args.since_days is not None:
        print(f"[mode] since-days={args.since_days}")
    if args.use_email_mart_features:
        print("[mode] use-email-mart-features enabled")
    mart_slack = normalize_mart_date_slack_days(int(args.mart_date_slack_days))
    print(f"Mart date slack days (plausible timeline): {mart_slack}")

    if args.rebuild:
        conn.executescript(
            """
            DELETE FROM opportunity_signals;
            DELETE FROM document_master;
            DELETE FROM contact_master;
            DELETE FROM organization_master;
            """
        )
        conn.commit()

    options = MartBuildOptions(
        internal_domains=frozenset(internal_domains),
        limit_emails=args.limit_emails,
        dashboard_fast=bool(args.dashboard_fast),
        canonical_only=bool(args.canonical_only),
        since_days=args.since_days,
        skip_document_master_if_unchanged=bool(args.skip_document_master_if_unchanged),
        mart_date_slack_days=mart_slack,
        use_email_mart_features=bool(args.use_email_mart_features),
    )
    built_at = run_business_mart_build(conn, run_id, options)
    conn.close()
    print("Done.")
    print(f"created_at|{built_at}")
    return 0


def main() -> None:
    raise SystemExit(run_build_business_mart_from_argv())


if __name__ == "__main__":
    main()
