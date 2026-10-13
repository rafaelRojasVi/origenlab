#!/usr/bin/env python3
"""Canonical archive outbound batch (alternate lane — not daily outbound).

Default: audit CSV + JSON only. Pass ``--build-batch`` for shortlist → gate snapshot →
commercial precheck → send_ready / review CSVs. See RUNBOOK / OUTBOUND_SOURCE_OF_TRUTH."""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from origenlab_email_pipeline.archive_outreach_queue import ARCHIVE_CANDIDATE_SORT_COMPANY_INTRO
from origenlab_email_pipeline.archive_send_batch_builder import (
    SEND_READY_CSV_NAME,
    BUILD_SUMMARY_JSON_NAME,
    build_archive_send_batch,
    refresh_sent_mailbox,
)
from origenlab_email_pipeline.cli_modes import (
    add_audit_only_build_batch_flags,
    resolve_audit_only_build_batch_mode,
)
from origenlab_email_pipeline.config import load_settings
from origenlab_email_pipeline.db import connect
from origenlab_email_pipeline.outbound_core import (
    resolve_outbound_gmail_user,
    resolve_outbound_sent_folders,
    sent_folder_defaults_were_used,
)
from origenlab_email_pipeline.outbound_sent_preflight import (
    SentHistoryPreflightFailed,
    print_sent_preflight_failure_to_stderr,
)
from origenlab_email_pipeline.postgres_outbound_audit import (
    OutboundAuditError,
    build_outbound_batch_payload,
    build_outbound_recipient_payloads,
    maybe_write_postgres_outbound_audit,
)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--gmail-user",
        type=str,
        default=None,
        help="Mailbox for Sent-history gate (default: ORIGENLAB_GMAIL_WORKSPACE_USER or contacto@origenlab.cl).",
    )
    ap.add_argument(
        "--out-dir",
        type=Path,
        default=Path("reports/out/active/archive_send_batch"),
        help="Output directory for stable archive batch artifacts.",
    )
    ap.add_argument("--db", type=Path, default=None, help="SQLite path (default: from config)")
    ap.add_argument("--fetch-cap", type=int, default=20000, help="Archive source scan cap.")
    ap.add_argument("--audit-limit", type=int, default=500, help="Max audited archive rows.")
    ap.add_argument(
        "--shortlist-limit",
        type=int,
        default=25,
        help="Max archive shortlist rows (e.g. 100 or 200 for a larger company-intro pool).",
    )
    ap.add_argument(
        "--refresh-sent",
        action="store_true",
        help="Refresh mailbox Sent folder before running audit/build.",
    )
    ap.add_argument(
        "--sent-folder",
        action="append",
        default=[],
        help="Sent folder label for gate and optional refresh (repeatable).",
    )
    ap.add_argument(
        "--since-days",
        type=int,
        default=30,
        help="If --refresh-sent, refresh window in days.",
    )
    ap.add_argument(
        "--strict-contact-graph-noise",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Use strict contact graph noise filtering (default: true).",
    )
    ap.add_argument(
        "--allow-weak-warmth",
        action="store_true",
        help="Include weak-warmth archive candidates in shortlist (review-required downstream).",
    )
    ap.add_argument(
        "--skip-commercial-precheck",
        action="store_true",
        help="Debug-only bypass for commercial precheck; defaults to running precheck.",
    )
    ap.add_argument(
        "--route-personal-domain-with-client-signals-to-review",
        action="store_true",
        help=(
            "If enabled, free-personal-domain contacts with invoice/purchase signals are "
            "forced to review_required (never auto-send)."
        ),
    )
    add_audit_only_build_batch_flags(
        ap,
        audit_help=(
            "Same as default: write only archive_outreach_audit.csv and "
            "archive_outreach_audit_summary.json (plus a small build summary). "
            "Kept for compatibility; do not combine with --build-batch."
        ),
        build_help=(
            "Generate full archive batch artifacts (shortlist, precheck, send_ready / review CSVs). "
            "Default is audit-only."
        ),
    )
    ap.add_argument(
        "--strict-commercial-drop",
        action=argparse.BooleanOptionalAction,
        default=False,
        help=(
            "When commercial precheck recommends drop (intel suppressed/rejected), treat as final "
            "drop and omit from send_ready/review CSVs. Default false: advisory — those rows go "
            "to review_required (shared export gate remains the only hard blocker for gate-eligible "
            "addresses)."
        ),
    )
    ap.add_argument(
        "--exclude-domain",
        action="append",
        default=[],
        help="Extra blocked domains for gate context (repeatable).",
    )
    ap.add_argument(
        "--suppress-email",
        action="append",
        default=[],
        help="Hard exclude contact email from send_ready/review CSVs (repeatable).",
    )
    ap.add_argument(
        "--suppress-domain",
        action="append",
        default=[],
        help="Hard exclude domain (and subdomains) from send_ready/review CSVs (repeatable).",
    )
    ap.add_argument(
        "--archive-candidate-sort",
        choices=("company_intro", "company_intro_fresh_last_seen", "legacy"),
        default=ARCHIVE_CANDIDATE_SORT_COMPANY_INTRO,
        help=(
            "How archive candidates are ordered before gate audit: "
            "'company_intro' (default) prefers org/business domains, procurement context, "
            "and non-suppressed commercial contact rows over free-personal Gmail; "
            "'company_intro_fresh_last_seen' is the same buckets with newest "
            "contact_last_seen_at first within ties; "
            "'legacy' uses warmth-only ordering."
        ),
    )
    ap.add_argument(
        "--shortlist-one-per-domain",
        action="store_true",
        help=(
            "After sorting, keep at most one eligible contact per email domain in the shortlist "
            "(spread sends across organizations; still capped by --shortlist-limit)."
        ),
    )
    ap.add_argument(
        "--allow-empty-sent-history",
        action="store_true",
        help=(
            "Allow batch build when no Gmail Sent rows match, or Sent rows have no parseable "
            "recipients. Dangerous: Sent-based already-contacted blocking may be ineffective."
        ),
    )
    ap.add_argument(
        "--write-postgres-audit",
        action="store_true",
        help="Optionally write outbound batch + recipients into Postgres outbound audit tables.",
    )
    ap.add_argument(
        "--postgres-url",
        type=str,
        default=None,
        help="Optional Postgres URL override for outbound audit write.",
    )
    ap.add_argument(
        "--audit-created-by",
        type=str,
        default=None,
        help="Optional actor string written to outbound.outbound_batch.created_by.",
    )
    args = ap.parse_args()

    mode = resolve_audit_only_build_batch_mode(ap, args)
    audit_only = mode.audit_only

    if audit_only and args.write_postgres_audit:
        ap.error("--write-postgres-audit requires --build-batch")

    settings = load_settings()
    db_path = args.db or settings.resolved_sqlite_path()
    gmail_user = resolve_outbound_gmail_user(settings, explicit=args.gmail_user)
    sent_folder_defaults_used = sent_folder_defaults_were_used(args.sent_folder)
    sent_folders = resolve_outbound_sent_folders(args.sent_folder)
    extra_exclude_domains = tuple(args.exclude_domain) if args.exclude_domain else ()
    manual_suppress_emails = tuple(args.suppress_email) if args.suppress_email else ()
    manual_suppress_domains = tuple(args.suppress_domain) if args.suppress_domain else ()

    if args.refresh_sent:
        refresh_sent_mailbox(
            project_root=_ROOT,
            db_path=db_path,
            sent_folder=sent_folders[0],
            since_days=int(args.since_days),
        )

    conn = connect(db_path)
    try:
        try:
            result = build_archive_send_batch(
                conn=conn,
                db_path=db_path,
                out_dir=args.out_dir,
                gmail_user=gmail_user,
                fetch_cap=int(args.fetch_cap),
                audit_limit=int(args.audit_limit),
                shortlist_limit=int(args.shortlist_limit),
                sent_folders=sent_folders,
                strict_contact_graph_noise=bool(args.strict_contact_graph_noise),
                allow_weak_warmth=bool(args.allow_weak_warmth),
                skip_commercial_precheck=bool(args.skip_commercial_precheck),
                route_personal_domain_with_client_signals_to_review=bool(
                    args.route_personal_domain_with_client_signals_to_review
                ),
                audit_only=audit_only,
                strict_commercial_drop=bool(args.strict_commercial_drop),
                extra_exclude_domains=extra_exclude_domains,
                manual_suppress_emails=manual_suppress_emails,
                manual_suppress_domains=manual_suppress_domains,
                sent_folder_defaults_used=sent_folder_defaults_used,
                archive_candidate_sort=str(args.archive_candidate_sort),
                shortlist_one_per_domain=bool(args.shortlist_one_per_domain),
                allow_empty_sent_history=bool(args.allow_empty_sent_history),
            )
        except SentHistoryPreflightFailed as exc:
            print_sent_preflight_failure_to_stderr(
                exc.outcome,
                headline="error: outbound Sent-history preflight failed — batch aborted.",
            )
            return 3
    finally:
        conn.close()

    sp = result.summary.get("sent_preflight") or {}
    if isinstance(sp, dict) and sp.get("override_used"):
        for w in sp.get("warnings") or []:
            print(f"warning: {w}", file=sys.stderr)

    if audit_only:
        print("Audit only: pass --build-batch to generate send-ready/review CSVs.")
        print(f"Wrote archive audit-only artifacts to {result.out_dir}")
    else:
        print(f"Wrote archive send batch to {result.out_dir}")
    print(f"Summary: {result.out_dir / BUILD_SUMMARY_JSON_NAME}")

    if args.write_postgres_audit:
        sent_preflight = result.summary.get("sent_preflight") or {}
        batch_payload = build_outbound_batch_payload(
            lane="archive",
            created_by=args.audit_created_by,
            gmail_user=gmail_user,
            sent_folders=list(sent_folders),
            sent_preflight_json=sent_preflight if isinstance(sent_preflight, dict) else {},
            gate_version="candidate_export_gate",
            output_artifact_path=str((result.out_dir / SEND_READY_CSV_NAME).resolve()),
            notes="archive build_archive_send_batch",
        )

        recipient_rows: list[dict[str, object]] = []
        send_ready_csv = result.out_dir / SEND_READY_CSV_NAME
        if send_ready_csv.is_file():
            with send_ready_csv.open("r", encoding="utf-8", newline="") as f:
                for r in csv.DictReader(f):
                    recipient_rows.append(
                        {
                            "email_norm": r.get("contact_email"),
                            "lead_id": None,
                            "source_kind": "archive",
                            "source_key": r.get("contact_email"),
                            "organization_name": r.get("organization_name"),
                            "organization_domain": r.get("domain"),
                            "eligibility_result": "eligible",
                            "exclusion_reason": None,
                            "metadata_json": {
                                "decision_path": r.get("final_decision_path"),
                                "candidate_tier": r.get("candidate_tier"),
                                "contact_name": r.get("contact_name"),
                            },
                        }
                    )

        recipients_payload = build_outbound_recipient_payloads(recipient_rows)
        try:
            batch_id = maybe_write_postgres_outbound_audit(
                write_requested=True,
                explicit_postgres_url=args.postgres_url,
                batch=batch_payload,
                recipients=recipients_payload,
            )
        except OutboundAuditError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        print(
            f"Postgres outbound audit written: batch_id={batch_id} recipients={len(recipients_payload)}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

