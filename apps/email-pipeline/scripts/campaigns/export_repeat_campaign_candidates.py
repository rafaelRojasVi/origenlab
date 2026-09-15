#!/usr/bin/env python3
"""Export ranked lead candidates for a durable repeat marketing campaign.

Unlike the legacy one-shot lead exporter, previous Gmail Sent delivery and
historical ``contacted``/``replied`` outreach state are not permanent blockers.
The shared gate still blocks suppression, domain suppression, snoozed state,
active commercial engagements, internal/supplier/noise contacts, and invalid
addresses.

The command is read-only on SQLite and sends no email. It fails closed until
``refresh_active_commercial_holds.py --apply`` has produced a successful CRM
hold snapshot.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from origenlab_email_pipeline.config import load_settings
from origenlab_email_pipeline.db import connect
from origenlab_email_pipeline.leads_schema import ensure_leads_tables
from origenlab_email_pipeline.marketing_export_context import (
    active_commercial_hold_projection_ready,
    load_active_commercial_hold_norms,
)
from origenlab_email_pipeline.next_marketing_queue import compute_next_marketing_recipients
from origenlab_email_pipeline.outbound_core import (
    resolve_outbound_gmail_user,
    resolve_outbound_sent_folders,
)
from origenlab_email_pipeline.outbound_sent_preflight import (
    evaluate_sent_history_preflight,
    print_sent_preflight_failure_to_stderr,
    probe_sent_history,
    sent_preflight_summary_dict,
)

_FIELDS = [
    "case_id",
    "id_lead",
    "contact_email",
    "email_source",
    "recipient_name",
    "institution_name",
    "sector",
    "fit_bucket",
    "priority_score",
    "already_in_archive_flag",
    "source_name",
    "website",
    "evidence_summary",
    "variant_type",
]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--db", type=Path, default=None)
    ap.add_argument("--limit", type=int, default=200)
    ap.add_argument("--fetch-cap", type=int, default=20000)
    ap.add_argument("--include-low-fit", action="store_true")
    ap.add_argument("--min-priority", type=float, default=None)
    ap.add_argument("--gmail-user", default=None)
    ap.add_argument("--sent-folder", action="append", default=[])
    ap.add_argument("--allow-empty-sent-history", action="store_true")
    ap.add_argument("--summary-out", type=Path, default=None)
    args = ap.parse_args(argv)

    if args.limit <= 0 or args.fetch_cap <= 0:
        print("--limit and --fetch-cap must be positive integers", file=sys.stderr)
        return 2

    settings = load_settings()
    db_path = (args.db or settings.resolved_sqlite_path()).expanduser().resolve()
    gmail_user = resolve_outbound_gmail_user(settings, explicit=args.gmail_user)
    sent_folders = resolve_outbound_sent_folders(args.sent_folder)

    conn = connect(db_path)
    try:
        ensure_leads_tables(conn)

        probe = probe_sent_history(conn, gmail_user=gmail_user, sent_folders=sent_folders)
        preflight = evaluate_sent_history_preflight(
            probe,
            allow_empty=bool(args.allow_empty_sent_history),
        )
        if not preflight.ok:
            print_sent_preflight_failure_to_stderr(preflight)
            return 3

        if not active_commercial_hold_projection_ready(conn):
            print(
                "Repeat-campaign export blocked: CRM commercial-hold snapshot is missing. "
                "Run scripts/campaigns/refresh_active_commercial_holds.py --apply first.",
                file=sys.stderr,
            )
            return 4

        held = load_active_commercial_hold_norms(conn)
        rows, stats = compute_next_marketing_recipients(
            conn,
            gmail_user=gmail_user,
            sent_folders=sent_folders,
            limit=int(args.limit),
            fetch_cap=int(args.fetch_cap),
            include_low_fit=bool(args.include_low_fit),
            min_priority=args.min_priority,
            allow_prior_outreach_history=True,
        )
    finally:
        conn.close()

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=_FIELDS, extrasaction="ignore", lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)

    summary_path = args.summary_out or args.out.with_name(args.out.stem + "_summary.json")
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": "1",
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "mode": "repeat_campaign",
        "sqlite_path": str(db_path),
        "gmail_user": gmail_user,
        "sent_folders": list(sent_folders),
        "policy": {
            "prior_gmail_sent_blocks": False,
            "historical_contacted_blocks": False,
            "historical_replied_blocks": False,
            "snoozed_blocks": True,
            "email_and_domain_suppression_blocks": True,
            "active_commercial_engagement_blocks": True,
            "supplier_internal_noise_blocks": True,
        },
        "counts": {
            "exported": len(rows),
            "scanned": stats.n_scanned,
            "known_sent_history": stats.n_sent_folder_recipients,
            "known_suppressions": stats.n_suppressed,
            "known_outreach_state": stats.n_outreach_state,
            "active_commercial_hold_emails": len(held),
        },
        "sent_preflight": sent_preflight_summary_dict(preflight),
        "out": str(args.out.resolve()),
    }
    summary_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(json.dumps(payload, ensure_ascii=False, indent=2))
    if len(rows) < int(args.limit):
        print(
            f"Warning: only {len(rows)} eligible unique addresses found for requested {args.limit}.",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
