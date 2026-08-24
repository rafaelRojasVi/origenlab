"""Read-only parity audit: email body scan vs email_mart_features scan."""

from __future__ import annotations

import argparse
import io
import sqlite3
import time
from contextlib import redirect_stdout
from dataclasses import dataclass

from origenlab_email_pipeline.business_mart import DocAgg, doc_aggregates
from origenlab_email_pipeline.config import load_settings
from origenlab_email_pipeline.core.mart.build_business_mart_cli import (
    normalize_mart_date_slack_days,
)
from origenlab_email_pipeline.core.mart.build_options import MartBuildOptions
from origenlab_email_pipeline.core.mart.internal_domains import (
    resolve_mart_internal_domains,
)
from origenlab_email_pipeline.core.mart.contact_org_builder import (
    EmailMartFeaturesEmptyError,
    build_organization_map,
    scan_email_contacts,
    scan_email_contacts_from_features,
)
from origenlab_email_pipeline.core.mart.opportunity_signal_builder import (
    compute_opportunity_signal_rows,
)
from origenlab_email_pipeline.db import connect
from origenlab_email_pipeline.freshness_dates import MART_DATE_SLACK_DAYS_DEFAULT

SCRIPT_NAME = "scripts/qa/audit_email_mart_feature_scan.py"

_CONTACT_COMPARE_FIELDS = (
    "domain",
    "org_name",
    "org_type",
    "first_seen_at",
    "last_seen_at",
    "total",
    "inbound",
    "outbound",
    "quote_email",
    "invoice_email",
    "purchase_email",
    "business_doc_email",
    "quote_doc",
    "invoice_doc",
)


@dataclass(frozen=True)
class EmailMartFeatureScanParityReport:
    scanned_emails: int
    scanned_features: int
    contacts_old: int
    contacts_feature: int
    contact_count_delta: int
    mismatched_contacts: int
    missing_in_feature: int
    extra_in_feature: int
    organizations_old: int
    organizations_feature: int
    organization_count_delta: int
    opportunity_signals_old: int
    opportunity_signals_feature: int
    opportunity_signal_count_delta: int
    elapsed_old_seconds: float
    elapsed_feature_seconds: float

    @property
    def has_mismatch(self) -> bool:
        return (
            self.contact_count_delta != 0
            or self.mismatched_contacts != 0
            or self.missing_in_feature != 0
            or self.extra_in_feature != 0
            or self.organization_count_delta != 0
            or self.opportunity_signal_count_delta != 0
        )


def require_email_mart_features_table(conn: sqlite3.Connection) -> None:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='email_mart_features'"
    ).fetchone()
    if row is None:
        raise EmailMartFeaturesEmptyError(
            "email_mart_features table is missing; run build-email-mart-features --apply first"
        )


def load_doc_aggs_readonly(conn: sqlite3.Connection) -> DocAgg:
    """Load document aggregates without rebuilding mart tables."""
    return doc_aggregates(
        conn.execute(
            """
            SELECT a.email_id, ae.detected_doc_type,
                   COALESCE(ae.has_quote_terms, 0),
                   COALESCE(ae.has_invoice_terms, 0),
                   COALESCE(ae.has_purchase_terms, 0),
                   COALESCE(ae.has_price_list_terms, 0)
            FROM attachment_extracts ae
            JOIN attachments a ON a.id = ae.attachment_id
            WHERE ae.extract_status = 'success'
            """
        )
    )


def contact_row_snapshot(row: dict) -> dict[str, object]:
    return {
        **{field: row[field] for field in _CONTACT_COMPARE_FIELDS},
        "equip": dict(row["equip"]),
    }


def compare_contact_maps(
    old_contact: dict[str, dict],
    feature_contact: dict[str, dict],
) -> tuple[int, int, int]:
    """Return (mismatched_contacts, missing_in_feature, extra_in_feature)."""
    old_keys = set(old_contact)
    feature_keys = set(feature_contact)
    missing_in_feature = len(old_keys - feature_keys)
    extra_in_feature = len(feature_keys - old_keys)
    mismatched = 0
    for email in old_keys & feature_keys:
        if contact_row_snapshot(old_contact[email]) != contact_row_snapshot(
            feature_contact[email]
        ):
            mismatched += 1
    return mismatched, missing_in_feature, extra_in_feature


def opportunity_signal_keys(
    contact: dict[str, dict], org: dict[str, dict]
) -> set[tuple[str, str, str]]:
    return {
        (row[0], row[1], row[2])
        for row in compute_opportunity_signal_rows(contact, org)  # type: ignore[arg-type]
    }


def run_email_mart_feature_scan_parity(
    conn: sqlite3.Connection,
    *,
    options: MartBuildOptions,
) -> EmailMartFeatureScanParityReport:
    require_email_mart_features_table(conn)
    feature_count = int(
        conn.execute("SELECT COUNT(*) FROM email_mart_features").fetchone()[0]
    )
    if feature_count == 0:
        raise EmailMartFeaturesEmptyError(
            "email_mart_features is empty; run build-email-mart-features --apply first"
        )

    doc_aggs = load_doc_aggs_readonly(conn)

    with redirect_stdout(io.StringIO()):
        t0 = time.monotonic()
        old_contact, scanned_emails = scan_email_contacts(
            conn, options=options, doc_aggs=doc_aggs
        )
        elapsed_old = time.monotonic() - t0

        t1 = time.monotonic()
        feature_contact, scanned_features = scan_email_contacts_from_features(
            conn,
            options=options,
            doc_aggs=doc_aggs,
        )
        elapsed_feature = time.monotonic() - t1

    mismatched, missing_in_feature, extra_in_feature = compare_contact_maps(
        old_contact,
        feature_contact,
    )
    contacts_old = len(old_contact)
    contacts_feature = len(feature_contact)

    old_org = build_organization_map(old_contact)  # type: ignore[arg-type]
    feature_org = build_organization_map(feature_contact)  # type: ignore[arg-type]
    organizations_old = len(old_org)
    organizations_feature = len(feature_org)

    opportunity_signals_old = len(opportunity_signal_keys(old_contact, old_org))
    opportunity_signals_feature = len(
        opportunity_signal_keys(feature_contact, feature_org)
    )

    return EmailMartFeatureScanParityReport(
        scanned_emails=scanned_emails,
        scanned_features=scanned_features,
        contacts_old=contacts_old,
        contacts_feature=contacts_feature,
        contact_count_delta=contacts_feature - contacts_old,
        mismatched_contacts=mismatched,
        missing_in_feature=missing_in_feature,
        extra_in_feature=extra_in_feature,
        organizations_old=organizations_old,
        organizations_feature=organizations_feature,
        organization_count_delta=organizations_feature - organizations_old,
        opportunity_signals_old=opportunity_signals_old,
        opportunity_signals_feature=opportunity_signals_feature,
        opportunity_signal_count_delta=opportunity_signals_feature
        - opportunity_signals_old,
        elapsed_old_seconds=elapsed_old,
        elapsed_feature_seconds=elapsed_feature,
    )


def print_email_mart_feature_scan_parity_report(
    report: EmailMartFeatureScanParityReport,
) -> None:
    print("email_mart_feature_scan_parity")
    print(f"scanned_emails={report.scanned_emails}")
    print(f"scanned_features={report.scanned_features}")
    print(f"contacts_old={report.contacts_old}")
    print(f"contacts_feature={report.contacts_feature}")
    print(f"contact_count_delta={report.contact_count_delta}")
    print(f"mismatched_contacts={report.mismatched_contacts}")
    print(f"missing_in_feature={report.missing_in_feature}")
    print(f"extra_in_feature={report.extra_in_feature}")
    print(f"organizations_old={report.organizations_old}")
    print(f"organizations_feature={report.organizations_feature}")
    print(f"organization_count_delta={report.organization_count_delta}")
    print(f"opportunity_signals_old={report.opportunity_signals_old}")
    print(f"opportunity_signals_feature={report.opportunity_signals_feature}")
    print(f"opportunity_signal_count_delta={report.opportunity_signal_count_delta}")
    print(f"elapsed_old_seconds={report.elapsed_old_seconds:.2f}")
    print(f"elapsed_feature_seconds={report.elapsed_feature_seconds:.2f}")


def _build_arg_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description="Read-only parity audit: email scan vs email_mart_features scan.",
    )
    ap.add_argument(
        "--limit", type=int, default=None, help="limit emails/features scanned"
    )
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
        help="Mart timeline slack days passed to scans",
    )
    ap.add_argument(
        "--allow-mismatch",
        action="store_true",
        help="exit 0 even when parity mismatches are found",
    )
    ap.add_argument(
        "--canonical-only",
        action="store_true",
        help="Process only canonical contacto Gmail source rows",
    )
    ap.add_argument(
        "--since-days",
        type=int,
        default=None,
        help="Restrict scan to date_iso within N days (best effort)",
    )
    return ap


def run_audit_email_mart_feature_scan_from_argv(argv: list[str] | None = None) -> int:
    args = _build_arg_parser().parse_args(argv)

    settings = load_settings()
    db_path = settings.resolved_sqlite_path()
    conn = connect(db_path)

    internal_domains = resolve_mart_internal_domains(
        args.internal_domain,
    )

    mart_slack = normalize_mart_date_slack_days(int(args.mart_date_slack_days))
    limit = args.limit
    if limit is not None:
        limit = max(1, int(limit))

    print(f"DB: {db_path}")
    print(f"Internal domains: {sorted(internal_domains)[:10]}")
    print(f"Mart date slack days: {mart_slack}")
    if limit is not None:
        print(f"[mode] limit={limit}")

    options = MartBuildOptions(
        internal_domains=frozenset(internal_domains),
        limit_emails=limit,
        dashboard_fast=False,
        canonical_only=bool(args.canonical_only),
        since_days=args.since_days,
        skip_document_master_if_unchanged=False,
        mart_date_slack_days=mart_slack,
        use_email_mart_features=False,
    )

    report = run_email_mart_feature_scan_parity(conn, options=options)
    conn.close()

    print_email_mart_feature_scan_parity_report(report)
    if report.has_mismatch and not args.allow_mismatch:
        return 1
    return 0


def main() -> None:
    raise SystemExit(run_audit_email_mart_feature_scan_from_argv())
