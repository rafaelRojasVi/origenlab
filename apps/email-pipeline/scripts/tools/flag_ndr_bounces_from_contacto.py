#!/usr/bin/env python3
# -----------------------------------------------------------------------------
# SAFETY (break-glass): --apply writes contact_email_suppression. Default is print-only.
# See docs/SCRIPT_MAP.md — "Break-glass scripts".
# -----------------------------------------------------------------------------
"""Scan contacto@origenlab Gmail-ingested mail for NDR / Mailer-Daemon bounces.

Reads ``emails`` rows whose ``source_file`` is the workspace ingest prefix, keeps rows
classified as ``bounce_ndr``, extracts failed recipient(s) from the DSN body, and optionally
writes ``contact_email_suppression`` with ``bounce_no_such_user`` / ``bounce_access_denied`` /
``bounce_other``.

Default is print-only; use ``--apply`` after reviewing matches.

Targeted apply (operator allowlist)::

  uv run python scripts/tools/flag_ndr_bounces_from_contacto.py \\
    --since-days 1 --emails-file reports/in/manual_reviews/allowlist.txt \\
    --only-code bounce_no_such_user --apply

Allowlist emails must appear in the current NDR scan evidence; otherwise apply is refused.

Optional inbound human-reported mode (legacy ``flag_reported_non_delivery_from_contacto.py`` behavior)::

  uv run python scripts/tools/flag_ndr_bounces_from_contacto.py --include-reported-non-delivery --since-days 30

Example::

  uv run python scripts/tools/flag_ndr_bounces_from_contacto.py --since-days 30 --limit 20000
  uv run python scripts/tools/flag_ndr_bounces_from_contacto.py --apply
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_ROOT / "src"))

from origenlab_email_pipeline.config import load_settings
from origenlab_email_pipeline.contact_email_suppression import (
    ensure_contact_email_suppression_table,
    fetch_contact_email_suppression_row,
    upsert_contact_email_suppression,
    validate_contact_email_suppression_payload,
)
from origenlab_email_pipeline.db import connect
from origenlab_email_pipeline.ndr_contacto_scan import (
    PlannedEntry,
    scan_ndr_planned_recipients,
)
from origenlab_email_pipeline.reported_non_delivery_contacto_scan import (
    ReportedNonDeliveryEntry,
    scan_reported_non_delivery_senders,
)

def load_emails_allowlist(path: Path) -> list[str]:
    """Load one email per line; ignore blanks and ``#`` comments."""
    emails: list[str] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        emails.append(line.lower())
    return emails


def select_planned_for_apply(
    planned: dict[str, PlannedEntry],
    *,
    emails_allowlist: list[str] | None,
    only_code: str | None,
) -> tuple[dict[str, PlannedEntry], list[str], list[str]]:
    """Subset ``planned`` for targeted apply.

    Returns ``(selected, refused_not_in_evidence, refused_wrong_code)``.
    """
    refused_not_in_evidence: list[str] = []
    refused_wrong_code: list[str] = []

    if emails_allowlist is not None:
        selected: dict[str, PlannedEntry] = {}
        for email in emails_allowlist:
            entry = planned.get(email)
            if entry is None:
                refused_not_in_evidence.append(email)
                continue
            code = entry[0]
            if only_code is not None and code != only_code:
                refused_wrong_code.append(email)
                continue
            selected[email] = entry
        return selected, refused_not_in_evidence, refused_wrong_code

    if only_code is not None:
        return (
            {e: v for e, v in planned.items() if v[0] == only_code},
            [],
            [],
        )

    return dict(planned), [], []


def _print_planned_subset(planned: dict[str, PlannedEntry], *, label: str) -> None:
    print(f"{label}: {len(planned)} address(es)")
    for email in sorted(planned.keys()):
        code, d_iso, eid, subj_snip = planned[email]
        extra = f"  email_id={eid}  date_iso={d_iso or '—'}"
        if subj_snip:
            extra += f"\n      subject: {subj_snip}"
        print(f"  - {email}  → {code}{extra}")


def _print_reported_non_delivery_subset(
    planned: dict[str, ReportedNonDeliveryEntry],
    *,
    label: str,
) -> None:
    print(f"{label}: {len(planned)} address(es)")
    for email in sorted(planned.keys()):
        d_iso, eid, subj_snip = planned[email]
        extra = f"  email_id={eid}  date_iso={d_iso or '—'}"
        if subj_snip:
            extra += f"\n      subject: {subj_snip}"
        print(f"  - {email}  → human_reported_non_delivery{extra}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", type=Path, default=None, help="SQLite path (default: from config)")
    ap.add_argument(
        "--apply",
        action="store_true",
        help="Upsert contact_email_suppression for selected recipients.",
    )
    ap.add_argument("--limit", type=int, default=50_000, help="Max contacto Gmail rows to scan")
    ap.add_argument(
        "--since-days",
        type=int,
        default=None,
        help="Only rows with date_iso >= now - N days (SQLite date string compare is lexical ISO).",
    )
    ap.add_argument(
        "--since-date-utc",
        default=None,
        metavar="YYYY-MM-DD",
        help=(
            "Absolute UTC calendar lower bound for date_iso (YYYY-MM-DD). "
            "Mutually exclusive with --since-days; preferred for reviewed-queue apply."
        ),
    )
    ap.add_argument(
        "--emails-file",
        type=Path,
        default=None,
        help="Apply only these exact emails (must match NDR scan evidence).",
    )
    ap.add_argument(
        "--only-code",
        default=None,
        metavar="CODE",
        help="Only recipients with this suppression code (e.g. bounce_no_such_user).",
    )
    ap.add_argument(
        "--include-reported-non-delivery",
        action="store_true",
        help=(
            "Also scan inbound (non-Sent) contacto rows for human-reported non-delivery "
            "(e.g. «no recibimos su correo»). Default scan remains NDR/bounce_ndr only."
        ),
    )
    args = ap.parse_args()

    if args.since_days is not None and args.since_date_utc is not None:
        print("--since-days and --since-date-utc are mutually exclusive", file=sys.stderr)
        return 1

    emails_allowlist: list[str] | None = None
    if args.emails_file is not None:
        if not args.emails_file.is_file():
            print(f"emails-file not found: {args.emails_file}", file=sys.stderr)
            return 1
        emails_allowlist = load_emails_allowlist(args.emails_file)
        if not emails_allowlist:
            print(f"emails-file is empty: {args.emails_file}", file=sys.stderr)
            return 1

    db_path = args.db or load_settings().resolved_sqlite_path()
    conn = connect(db_path)
    try:
        planned, scanned, skipped_no_rcpt = scan_ndr_planned_recipients(
            conn,
            since_days=args.since_days,
            since_date_utc=args.since_date_utc,
            limit=args.limit,
        )
        print(
            f"Scanned {scanned} recent contacto row(s); "
            f"bounce_ndr with extracted recipient: {len(planned)} distinct address(es); "
            f"bounce_ndr but no address parsed: {skipped_no_rcpt}."
        )

        selected, refused_missing, refused_wrong_code = select_planned_for_apply(
            planned,
            emails_allowlist=emails_allowlist,
            only_code=args.only_code,
        )

        if args.emails_file is not None or args.only_code is not None:
            print(
                f"Targeted filter: allowlist={len(emails_allowlist) if emails_allowlist else '—'} "
                f"only_code={args.only_code or '—'} "
                f"→ matched={len(selected)} "
                f"refused_not_in_ndr_evidence={len(refused_missing)} "
                f"refused_wrong_code={len(refused_wrong_code)}"
            )
            if refused_missing:
                for email in refused_missing:
                    print(f"  REFUSE (not in NDR evidence): {email}", file=sys.stderr)
            if refused_wrong_code:
                for email in refused_wrong_code:
                    code = planned[email][0]
                    print(
                        f"  REFUSE (code {code}, wanted {args.only_code}): {email}",
                        file=sys.stderr,
                    )
            if refused_missing or refused_wrong_code:
                print("Refusing apply: fix allowlist or widen scan window.", file=sys.stderr)
                return 1

        if emails_allowlist is not None or args.only_code is not None:
            _print_planned_subset(selected, label="Selected for apply")
        else:
            _print_planned_subset(planned, label="All NDR recipients")

        reported_planned: dict[str, ReportedNonDeliveryEntry] = {}
        reported_scanned = 0
        if args.include_reported_non_delivery:
            reported_planned, reported_scanned = scan_reported_non_delivery_senders(
                conn,
                since_days=args.since_days,
                limit=args.limit,
            )
            print(
                f"\nHuman-reported non-delivery scan: {reported_scanned} recent non-sent contacto row(s); "
                f"{len(reported_planned)} distinct sender(s) matched."
            )
            _print_reported_non_delivery_subset(
                reported_planned,
                label="All human_reported_non_delivery recipients",
            )

        if not args.apply:
            print("\nDry run only. Re-run with --apply to write suppressions.")
            return 0

        if args.include_reported_non_delivery and args.only_code is not None:
            if args.only_code != "reported_non_delivery":
                print(
                    "Refusing apply: --only-code with human-reported mode applies only to "
                    "reported_non_delivery (NDR codes use bounce_*).",
                    file=sys.stderr,
                )
                return 1

        if not selected and not (args.include_reported_non_delivery and reported_planned):
            print("Nothing selected to apply.", file=sys.stderr)
            return 1

        ensure_contact_email_suppression_table(conn)
        n = 0
        skipped_manual = 0
        for email, (code, d_iso, eid, _subj) in sorted(selected.items()):
            existing = fetch_contact_email_suppression_row(conn, email)
            if existing and str(existing.get("suppression_reason_code") or "") == "manual_do_not_contact":
                print(f"  skip (manual_do_not_contact): {email}", file=sys.stderr)
                skipped_manual += 1
                continue
            payload = validate_contact_email_suppression_payload(
                email=email,
                suppression_reason_code=code,
                suppression_reason_text=f"NDR from contacto Gmail ingest (source email id {eid})",
                suppression_source="flag_ndr_bounces_from_contacto.py",
                last_bounced_at=d_iso,
                updated_by="flag_ndr_bounces_from_contacto.py",
            )
            upsert_contact_email_suppression(conn, payload=payload)
            n += 1

        reported_n = 0
        skipped_reported_existing = 0
        if args.include_reported_non_delivery and reported_planned:
            for email, (d_iso, eid, _subj) in sorted(reported_planned.items()):
                existing = fetch_contact_email_suppression_row(conn, email)
                if existing and str(existing.get("suppression_reason_code") or "") not in (
                    "",
                    "reported_non_delivery",
                ):
                    print(
                        f"  skip (already suppressed as {existing.get('suppression_reason_code')}): {email}",
                        file=sys.stderr,
                    )
                    skipped_reported_existing += 1
                    continue
                if existing and str(existing.get("suppression_reason_code") or "") == "manual_do_not_contact":
                    print(f"  skip (manual_do_not_contact): {email}", file=sys.stderr)
                    skipped_manual += 1
                    continue
                payload = validate_contact_email_suppression_payload(
                    email=email,
                    suppression_reason_code="reported_non_delivery",
                    suppression_reason_text=(
                        f"Human-reported non-delivery inbound from contacto Gmail (email id {eid})"
                    ),
                    suppression_source="flag_ndr_bounces_from_contacto.py",
                    last_bounced_at=None,
                    updated_by="flag_ndr_bounces_from_contacto.py",
                )
                upsert_contact_email_suppression(conn, payload=payload)
                reported_n += 1

        conn.commit()
        if n:
            print(f"Upserted bounce suppressions: {n} row(s); skipped_manual: {skipped_manual}")
        if reported_n:
            print(
                f"Upserted human_reported_non_delivery: {reported_n} row(s); "
                f"skipped_existing_other_reason: {skipped_reported_existing}"
            )
        elif args.include_reported_non_delivery and not reported_planned:
            print("No human_reported_non_delivery matches to apply.")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
