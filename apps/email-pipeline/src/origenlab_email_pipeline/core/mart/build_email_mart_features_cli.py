"""CLI orchestration for email_mart_features backfill (dry-run default)."""

from __future__ import annotations

import argparse
import sqlite3
import time
from dataclasses import dataclass

from origenlab_email_pipeline.config import load_settings
from origenlab_email_pipeline.core.mart.build_business_mart_cli import (
    normalize_mart_date_slack_days,
)
from origenlab_email_pipeline.core.mart.internal_domains import (
    resolve_mart_internal_domains,
)
from origenlab_email_pipeline.core.mart.email_mart_features import (
    EmailMartFeature,
    compute_email_mart_feature,
)
from origenlab_email_pipeline.db import connect
from origenlab_email_pipeline.freshness_dates import MART_DATE_SLACK_DAYS_DEFAULT
from origenlab_email_pipeline.sqlite_migrate import SchemaLayer, migrate_sqlite_schema

SCRIPT_NAME = "scripts/mart/build_email_mart_features.py"

_UPSERT_SQL = """
INSERT INTO email_mart_features (
  email_id,
  message_id,
  source_file,
  folder,
  sender_email,
  sender_domain,
  recipient_emails_json,
  external_targets_json,
  direction,
  is_noise,
  is_quote_email,
  is_invoice_email,
  is_purchase_email,
  equipment_tags_json,
  mart_date_iso,
  body_len,
  feature_source_hash,
  computed_at
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(email_id) DO UPDATE SET
  message_id=excluded.message_id,
  source_file=excluded.source_file,
  folder=excluded.folder,
  sender_email=excluded.sender_email,
  sender_domain=excluded.sender_domain,
  recipient_emails_json=excluded.recipient_emails_json,
  external_targets_json=excluded.external_targets_json,
  direction=excluded.direction,
  is_noise=excluded.is_noise,
  is_quote_email=excluded.is_quote_email,
  is_invoice_email=excluded.is_invoice_email,
  is_purchase_email=excluded.is_purchase_email,
  equipment_tags_json=excluded.equipment_tags_json,
  mart_date_iso=excluded.mart_date_iso,
  body_len=excluded.body_len,
  feature_source_hash=excluded.feature_source_hash,
  computed_at=excluded.computed_at
"""

_INSERT_SQL = """
INSERT INTO email_mart_features (
  email_id,
  message_id,
  source_file,
  folder,
  sender_email,
  sender_domain,
  recipient_emails_json,
  external_targets_json,
  direction,
  is_noise,
  is_quote_email,
  is_invoice_email,
  is_purchase_email,
  equipment_tags_json,
  mart_date_iso,
  body_len,
  feature_source_hash,
  computed_at
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""

_EMAIL_SELECT_COLS = (
    "id, message_id, source_file, folder, sender, recipients, subject, "
    "COALESCE(top_reply_clean,''), COALESCE(full_body_clean,''), date_iso"
)

_EMAIL_SELECT_COLS_E = (
    "e.id, e.message_id, e.source_file, e.folder, e.sender, e.recipients, e.subject, "
    "COALESCE(e.top_reply_clean,''), COALESCE(e.full_body_clean,''), e.date_iso"
)


@dataclass(frozen=True)
class EmailMartFeaturesBackfillReport:
    dry_run: bool
    mode: str
    scanned_emails: int
    existing_features: int
    missing_features: int
    stale_features: int
    current_features: int
    inserted_features: int
    updated_features: int
    elapsed_seconds: float
    body_total_chars: int


def email_mart_feature_row_values(feature: EmailMartFeature) -> tuple[object, ...]:
    return (
        feature.email_id,
        feature.message_id,
        feature.source_file,
        feature.folder,
        feature.sender_email,
        feature.sender_domain,
        feature.recipient_emails_json,
        feature.external_targets_json,
        feature.direction,
        feature.is_noise,
        feature.is_quote_email,
        feature.is_invoice_email,
        feature.is_purchase_email,
        feature.equipment_tags_json,
        feature.mart_date_iso,
        feature.body_len,
        feature.feature_source_hash,
        feature.computed_at,
    )


def print_email_mart_features_backfill_report(
    report: EmailMartFeaturesBackfillReport,
) -> None:
    dry = "true" if report.dry_run else "false"
    print(f"email_mart_features dry_run={dry}")
    print(f"mode={report.mode}")
    print(f"scanned_emails={report.scanned_emails}")
    print(f"existing_features={report.existing_features}")
    print(f"missing_features={report.missing_features}")
    print(f"stale_features={report.stale_features}")
    print(f"current_features={report.current_features}")
    print(f"inserted_features={report.inserted_features}")
    print(f"updated_features={report.updated_features}")
    print(f"elapsed_seconds={report.elapsed_seconds:.2f}")
    print(f"body_total_chars={report.body_total_chars}")


def _missing_only_email_sql(limit: int | None) -> tuple[str, list[object]]:
    sql = (
        f"SELECT {_EMAIL_SELECT_COLS_E} "
        "FROM emails e "
        "LEFT JOIN email_mart_features f ON f.email_id = e.id "
        "WHERE f.email_id IS NULL "
        "ORDER BY e.id"
    )
    params: list[object] = []
    if limit is not None:
        sql += " LIMIT ?"
        params.append(max(1, int(limit)))
    return sql, params


def _full_email_sql(limit: int | None) -> tuple[str, list[object]]:
    sql = f"SELECT {_EMAIL_SELECT_COLS} FROM emails ORDER BY id"
    params: list[object] = []
    if limit is not None:
        sql += " LIMIT ?"
        params.append(max(1, int(limit)))
    return sql, params


def _compute_feature_from_row(
    row: tuple[object, ...],
    *,
    internal_domains: frozenset[str],
    mart_date_slack_days: int,
    computed_at: str | None,
) -> EmailMartFeature:
    (
        email_id,
        message_id,
        source_file,
        folder,
        sender,
        recipients,
        subject,
        top_reply_clean,
        full_body_clean,
        date_iso,
    ) = row
    return compute_email_mart_feature(
        email_id=int(email_id),
        message_id=message_id,
        source_file=source_file,
        folder=folder,
        sender=sender,
        recipients=recipients,
        subject=subject,
        top_reply_clean=top_reply_clean,
        full_body_clean=full_body_clean,
        date_iso=date_iso,
        internal_domains=internal_domains,
        mart_date_slack_days=mart_date_slack_days,
        computed_at=computed_at,
    )


def run_email_mart_features_backfill(
    conn: sqlite3.Connection,
    *,
    dry_run: bool,
    missing_only: bool = False,
    limit: int | None = None,
    batch_size: int = 5000,
    internal_domains: frozenset[str],
    mart_date_slack_days: int,
    computed_at: str | None = None,
) -> EmailMartFeaturesBackfillReport:
    mode = "missing_only" if missing_only else "full"

    if missing_only:
        sql, params = _missing_only_email_sql(limit)
        cur = conn.execute(sql, params)
        scanned = 0
        inserted = 0
        body_total_chars = 0
        to_write: list[EmailMartFeature] = []

        while True:
            batch = cur.fetchmany(max(1, int(batch_size)))
            if not batch:
                break
            for row in batch:
                scanned += 1
                feature = _compute_feature_from_row(
                    row,
                    internal_domains=internal_domains,
                    mart_date_slack_days=mart_date_slack_days,
                    computed_at=computed_at,
                )
                body_total_chars += feature.body_len
                if not dry_run:
                    to_write.append(feature)
                    inserted += 1

        if not dry_run and to_write:
            conn.executemany(
                _INSERT_SQL, [email_mart_feature_row_values(f) for f in to_write]
            )
            conn.commit()

        return EmailMartFeaturesBackfillReport(
            dry_run=dry_run,
            mode=mode,
            scanned_emails=scanned,
            existing_features=0,
            missing_features=scanned,
            stale_features=0,
            current_features=0,
            inserted_features=inserted,
            updated_features=0,
            elapsed_seconds=0.0,
            body_total_chars=body_total_chars,
        )

    existing_hashes = {
        int(row[0]): str(row[1])
        for row in conn.execute(
            "SELECT email_id, feature_source_hash FROM email_mart_features"
        ).fetchall()
    }

    sql, params = _full_email_sql(limit)
    cur = conn.execute(sql, params)

    scanned = 0
    missing = 0
    stale = 0
    current = 0
    inserted = 0
    updated = 0
    body_total_chars = 0
    to_write: list[EmailMartFeature] = []

    while True:
        batch = cur.fetchmany(max(1, int(batch_size)))
        if not batch:
            break
        for row in batch:
            scanned += 1
            feature = _compute_feature_from_row(
                row,
                internal_domains=internal_domains,
                mart_date_slack_days=mart_date_slack_days,
                computed_at=computed_at,
            )
            body_total_chars += feature.body_len

            prior_hash = existing_hashes.get(feature.email_id)
            if prior_hash is None:
                missing += 1
                if not dry_run:
                    to_write.append(feature)
                    inserted += 1
            elif prior_hash != feature.feature_source_hash:
                stale += 1
                if not dry_run:
                    to_write.append(feature)
                    updated += 1
            else:
                current += 1

    if not dry_run and to_write:
        conn.executemany(
            _UPSERT_SQL, [email_mart_feature_row_values(f) for f in to_write]
        )
        conn.commit()

    existing_features = stale + current
    return EmailMartFeaturesBackfillReport(
        dry_run=dry_run,
        mode=mode,
        scanned_emails=scanned,
        existing_features=existing_features,
        missing_features=missing,
        stale_features=stale,
        current_features=current,
        inserted_features=inserted,
        updated_features=updated,
        elapsed_seconds=0.0,
        body_total_chars=body_total_chars,
    )


def _build_arg_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser()
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument(
        "--dry-run",
        action="store_true",
        help="profile only; do not write email_mart_features (default)",
    )
    mode.add_argument(
        "--apply",
        action="store_true",
        help="write missing/stale email_mart_features rows",
    )
    ap.add_argument(
        "--limit", type=int, default=None, help="debug: limit emails scanned"
    )
    ap.add_argument("--batch-size", type=int, default=5000, help="fetchmany batch size")
    ap.add_argument(
        "--internal-domain",
        action="append",
        default=[],
        help="repeatable; add operator-approved domains to canonical defaults",
    )
    ap.add_argument(
        "--mart-date-slack-days",
        type=int,
        default=MART_DATE_SLACK_DAYS_DEFAULT,
        help="Mart timeline slack days passed to feature extraction",
    )
    ap.add_argument(
        "--missing-only",
        action="store_true",
        help="scan/insert only emails without email_mart_features rows (no stale updates)",
    )
    return ap


def run_build_email_mart_features_from_argv(argv: list[str] | None = None) -> int:
    args = _build_arg_parser().parse_args(argv)
    dry_run = not bool(args.apply)

    settings = load_settings()
    db_path = settings.resolved_sqlite_path()
    conn = connect(db_path)
    migrate_sqlite_schema(conn, layers={SchemaLayer.ARCHIVE_AND_MART})

    internal_domains = resolve_mart_internal_domains(
        args.internal_domain,
    )

    mart_slack = normalize_mart_date_slack_days(int(args.mart_date_slack_days))
    batch_size = max(1, int(args.batch_size))

    print(f"DB: {db_path}")
    print(f"Internal domains: {sorted(internal_domains)[:10]}")
    print(f"Mart date slack days: {mart_slack}")
    if args.missing_only:
        print("[mode] missing-only enabled")

    t0 = time.monotonic()
    report = run_email_mart_features_backfill(
        conn,
        dry_run=dry_run,
        missing_only=bool(args.missing_only),
        limit=args.limit,
        batch_size=batch_size,
        internal_domains=frozenset(internal_domains),
        mart_date_slack_days=mart_slack,
    )
    elapsed = time.monotonic() - t0
    conn.close()

    final_report = EmailMartFeaturesBackfillReport(
        dry_run=report.dry_run,
        mode=report.mode,
        scanned_emails=report.scanned_emails,
        existing_features=report.existing_features,
        missing_features=report.missing_features,
        stale_features=report.stale_features,
        current_features=report.current_features,
        inserted_features=report.inserted_features,
        updated_features=report.updated_features,
        elapsed_seconds=elapsed,
        body_total_chars=report.body_total_chars,
    )
    print_email_mart_features_backfill_report(final_report)
    return 0


def main() -> None:
    raise SystemExit(run_build_email_mart_features_from_argv())
