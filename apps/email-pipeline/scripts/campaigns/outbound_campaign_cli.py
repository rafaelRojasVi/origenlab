#!/usr/bin/env python3
"""Outbound campaign ledger operator CLI (durable SQLite campaign state).

Subcommands: init, status, contact-status {set,show}, candidates add, select,
batch show, send, reconcile.

Canonical campaign state lives in SQLite. This CLI never writes batch
artifacts to a Windows Downloads path by default; optional CSV/JSON export
only happens when the operator explicitly passes --out to a command that
supports it, and --out is refused if it points at a Downloads-style path.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from origenlab_email_pipeline.config import load_settings
from origenlab_email_pipeline.db import connect
from origenlab_email_pipeline.manual_contact_status import (
    MANUAL_CONTACT_STATUSES,
    ensure_manual_contact_status_table,
    fetch_manual_contact_status,
    load_manual_status_map,
    upsert_manual_contact_status,
    validate_manual_contact_status_payload,
)
from origenlab_email_pipeline.outbound_campaign_reconcile import reconcile_campaign
from origenlab_email_pipeline.outbound_campaign_schema import ensure_outbound_campaign_tables
from origenlab_email_pipeline.outbound_campaign_sender import send_campaign_batch
from origenlab_email_pipeline.outbound_campaign_store import (
    CampaignAlreadyExistsError,
    CampaignNotFoundError,
    campaign_progress,
    create_campaign,
    list_reserved_batch,
    reserve_next_batch,
    upsert_recipient_candidate,
)
from origenlab_email_pipeline.outbound_core import (
    gate_context_for_lead_master_export,
    resolve_outbound_gmail_user,
    resolve_outbound_sent_folders,
)

_DOWNLOADS_MARKER = "Downloads"


def _resolve_db(args: argparse.Namespace) -> Path:
    return args.db or load_settings().resolved_sqlite_path()


def _guard_out_path(path: str | None) -> None:
    if path and _DOWNLOADS_MARKER in path:
        raise SystemExit(
            "Refusing to write to a Downloads-style path via campaign commands. "
            "Use --out with an explicit reports/out/... path if you need an export."
        )


def _cmd_init(args: argparse.Namespace) -> int:
    db_path = _resolve_db(args)
    conn = connect(db_path)
    try:
        ensure_outbound_campaign_tables(conn)
        try:
            create_campaign(
                conn, campaign_id=args.campaign_id, name=args.name,
                sender_email=args.sender_email, sender_name=args.sender_name,
                subject=args.subject, target_attempt_count=args.target,
                baseline_attempt_count=args.baseline,
            )
        except CampaignAlreadyExistsError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        conn.commit()
    finally:
        conn.close()
    print(f"Initialized campaign {args.campaign_id!r} in {db_path}")
    return 0


def _cmd_status(args: argparse.Namespace) -> int:
    db_path = _resolve_db(args)
    conn = connect(db_path)
    try:
        ensure_outbound_campaign_tables(conn)
        try:
            progress = campaign_progress(conn, args.campaign_id)
        except CampaignNotFoundError:
            print(f"Unknown campaign: {args.campaign_id}", file=sys.stderr)
            return 1
    finally:
        conn.close()
    print(json.dumps(progress.__dict__, ensure_ascii=False, indent=2))
    return 0


def _cmd_contact_status_set(args: argparse.Namespace) -> int:
    db_path = _resolve_db(args)
    conn = connect(db_path)
    try:
        ensure_manual_contact_status_table(conn)
        payload = validate_manual_contact_status_payload(
            email=args.email, status=args.status, organization_domain=args.org_domain,
            organization_name=args.org_name, role_label=args.role, reason=args.reason,
            evidence=args.evidence, effective_at=args.effective_at, updated_by=args.updated_by,
        )
        upsert_manual_contact_status(conn, payload=payload)
        conn.commit()
    finally:
        conn.close()
    print(f"Set manual status for {args.email}: {args.status}")
    return 0


def _cmd_contact_status_show(args: argparse.Namespace) -> int:
    db_path = _resolve_db(args)
    conn = connect(db_path)
    try:
        ensure_manual_contact_status_table(conn)
        row = fetch_manual_contact_status(conn, args.email)
    finally:
        conn.close()
    print(json.dumps(row, ensure_ascii=False, indent=2) if row else "null")
    return 0


def _cmd_candidates_add(args: argparse.Namespace) -> int:
    db_path = _resolve_db(args)
    conn = connect(db_path)
    try:
        ensure_outbound_campaign_tables(conn)
        added = []
        for email in args.email:
            rid = upsert_recipient_candidate(
                conn, campaign_id=args.campaign_id, email=email,
                source_kind=args.source_kind, source_ref=args.source_ref,
                institution_name=args.institution,
            )
            added.append(rid)
        conn.commit()
    finally:
        conn.close()
    print(f"Upserted {len(added)} candidate(s) for {args.campaign_id}")
    return 0


def _cmd_select(args: argparse.Namespace) -> int:
    db_path = _resolve_db(args)
    conn = connect(db_path)
    try:
        ensure_outbound_campaign_tables(conn)
        ensure_manual_contact_status_table(conn)

        settings = load_settings()
        gmail_user = resolve_outbound_gmail_user(settings, explicit=args.gmail_user)
        sent_folders = resolve_outbound_sent_folders(args.sent_folder)
        gate_ctx = gate_context_for_lead_master_export(
            conn, gmail_user=gmail_user, sent_folders=sent_folders,
        )
        manual_status = load_manual_status_map(conn)
        result = reserve_next_batch(
            conn, args.campaign_id, gate_ctx=gate_ctx, manual_status_by_email=manual_status,
            n=args.n,
        )
        conn.commit()
    finally:
        conn.close()
    print(json.dumps({
        "batch_id": result.batch_id,
        "reserved": len(result.reserved),
        "blocked": len(result.blocked),
    }, ensure_ascii=False, indent=2))
    return 0


def _cmd_batch_show(args: argparse.Namespace) -> int:
    db_path = _resolve_db(args)
    conn = connect(db_path)
    try:
        ensure_outbound_campaign_tables(conn)
        rows = list_reserved_batch(conn, args.campaign_id)
    finally:
        conn.close()
    print(json.dumps(rows, ensure_ascii=False, indent=2))
    return 0


def _cmd_send(args: argparse.Namespace) -> int:
    _guard_out_path(str(args.out) if args.out else None)
    db_path = _resolve_db(args)
    conn = connect(db_path)
    try:
        ensure_outbound_campaign_tables(conn)
        ensure_manual_contact_status_table(conn)
        rows = list_reserved_batch(conn, args.campaign_id)
        recipients = [(r["id"], r["email"]) for r in rows]
        if not recipients:
            print(json.dumps({
                "mode": "live" if args.live else "dry_run",
                "accepted": 0, "failed": 0, "skipped": 0, "note": "no reserved recipients",
            }, ensure_ascii=False, indent=2))
            return 0
        html = Path(args.html).expanduser().resolve().read_text(encoding="utf-8")
        html_dir = Path(args.html).expanduser().resolve().parent

        settings = load_settings()
        gmail_user = resolve_outbound_gmail_user(settings, explicit=args.gmail_user)
        sent_folders = resolve_outbound_sent_folders(args.sent_folder)
        gate_ctx = gate_context_for_lead_master_export(
            conn, gmail_user=gmail_user, sent_folders=sent_folders,
        )

        access_token = None
        if args.live:
            from origenlab_email_pipeline.gmail_workspace_oauth import load_credentials_for_gmail_imap
            client_json = os.environ.get("ORIGENLAB_GMAIL_OAUTH_CLIENT_JSON")
            if not client_json:
                print("Missing ORIGENLAB_GMAIL_OAUTH_CLIENT_JSON env var.", file=sys.stderr)
                return 2
            token_json = Path(os.environ.get(
                "ORIGENLAB_GMAIL_TOKEN_JSON",
                str(Path.home() / ".origenlab" / "secrets" / "gmail_workspace_token.json"),
            ))
            creds = load_credentials_for_gmail_imap(
                client_secrets_json=Path(client_json).expanduser().resolve(),
                token_json=token_json.expanduser().resolve(),
                open_browser=bool(args.open_browser),
            )
            access_token = creds.token

        batch_id = str(uuid.uuid4())
        outcomes = send_campaign_batch(
            conn, campaign_id=args.campaign_id, recipients=recipients, html=html,
            html_dir=html_dir, live=bool(args.live), access_token=access_token,
            gate_ctx=gate_ctx, batch_id=batch_id, stop_on_error=not args.no_stop_on_error,
        )
    finally:
        conn.close()
    accepted = sum(1 for o in outcomes if o.result == "accepted")
    failed = sum(1 for o in outcomes if o.result == "failed")
    skipped = sum(1 for o in outcomes if o.result == "skipped")
    print(json.dumps({
        "mode": "live" if args.live else "dry_run", "accepted": accepted,
        "failed": failed, "skipped": skipped,
    }, ensure_ascii=False, indent=2))
    return 0 if failed == 0 else 2


def _cmd_reconcile(args: argparse.Namespace) -> int:
    db_path = _resolve_db(args)
    conn = connect(db_path)
    try:
        ensure_outbound_campaign_tables(conn)
        settings = load_settings()
        gmail_user = resolve_outbound_gmail_user(settings, explicit=args.gmail_user)
        sent_folders = resolve_outbound_sent_folders(args.sent_folder)
        summary = reconcile_campaign(
            conn, args.campaign_id, gmail_user=gmail_user, sent_folders=sent_folders,
        )
    finally:
        conn.close()
    print(json.dumps(summary.__dict__, ensure_ascii=False, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="command", required=True)

    def _add_db_arg(p: argparse.ArgumentParser) -> None:
        p.add_argument("--db", type=Path, default=None, help="SQLite path (default: from config)")

    p_init = sub.add_parser("init", help="Create a new campaign")
    p_init.add_argument("--campaign-id", required=True)
    p_init.add_argument("--name", required=True)
    p_init.add_argument("--sender-email", required=True)
    p_init.add_argument("--sender-name", required=True)
    p_init.add_argument("--subject", required=True)
    p_init.add_argument("--target", type=int, required=True)
    p_init.add_argument("--baseline", type=int, default=0)
    _add_db_arg(p_init)
    p_init.set_defaults(func=_cmd_init)

    p_status = sub.add_parser("status", help="Show campaign status/progress")
    p_status.add_argument("--campaign-id", required=True)
    _add_db_arg(p_status)
    p_status.set_defaults(func=_cmd_status)

    p_cs = sub.add_parser("contact-status", help="Manual contact lifecycle sidecar")
    cs_sub = p_cs.add_subparsers(dest="contact_status_command", required=True)

    p_cs_set = cs_sub.add_parser("set", help="Register/update a manual contact status")
    p_cs_set.add_argument("--email", required=True)
    p_cs_set.add_argument("--status", required=True, choices=list(MANUAL_CONTACT_STATUSES))
    p_cs_set.add_argument("--org-domain", default=None)
    p_cs_set.add_argument("--org-name", default=None)
    p_cs_set.add_argument("--role", default=None)
    p_cs_set.add_argument("--reason", default=None)
    p_cs_set.add_argument("--evidence", default=None)
    p_cs_set.add_argument("--effective-at", default=None)
    p_cs_set.add_argument("--updated-by", default="outbound_campaign_cli.py")
    _add_db_arg(p_cs_set)
    p_cs_set.set_defaults(func=_cmd_contact_status_set)

    p_cs_show = cs_sub.add_parser("show", help="Show a contact's manual status")
    p_cs_show.add_argument("--email", required=True)
    _add_db_arg(p_cs_show)
    p_cs_show.set_defaults(func=_cmd_contact_status_show)

    p_cand = sub.add_parser("candidates", help="Manage campaign candidates")
    cand_sub = p_cand.add_subparsers(dest="candidates_command", required=True)
    p_cand_add = cand_sub.add_parser("add", help="Add candidate emails to a campaign")
    p_cand_add.add_argument("--campaign-id", required=True)
    p_cand_add.add_argument("--email", action="append", required=True)
    p_cand_add.add_argument("--institution", default=None)
    p_cand_add.add_argument("--source-kind", default="manual")
    p_cand_add.add_argument("--source-ref", default=None)
    _add_db_arg(p_cand_add)
    p_cand_add.set_defaults(func=_cmd_candidates_add)

    p_select = sub.add_parser("select", help="Select/reserve next N eligible candidates")
    p_select.add_argument("--campaign-id", required=True)
    p_select.add_argument("--n", type=int, required=True)
    p_select.add_argument("--gmail-user", default=None)
    p_select.add_argument("--sent-folder", action="append", default=None)
    _add_db_arg(p_select)
    p_select.set_defaults(func=_cmd_select)

    p_batch = sub.add_parser("batch", help="Inspect the reserved batch")
    batch_sub = p_batch.add_subparsers(dest="batch_command", required=True)
    p_batch_show = batch_sub.add_parser("show", help="List currently reserved recipients")
    p_batch_show.add_argument("--campaign-id", required=True)
    _add_db_arg(p_batch_show)
    p_batch_show.set_defaults(func=_cmd_batch_show)

    p_send = sub.add_parser("send", help="Send the reserved batch (dry-run by default)")
    p_send.add_argument("--campaign-id", required=True)
    p_send.add_argument("--html", required=True, type=Path)
    p_send.add_argument("--live", action="store_true", help="Actually call the Gmail API (default: dry-run)")
    p_send.add_argument("--no-stop-on-error", action="store_true")
    p_send.add_argument("--open-browser", action="store_true")
    p_send.add_argument("--gmail-user", default=None)
    p_send.add_argument("--sent-folder", action="append", default=None)
    p_send.add_argument("--out", default=None, help="Optional explicit export path (never Downloads)")
    _add_db_arg(p_send)
    p_send.set_defaults(func=_cmd_send)

    p_recon = sub.add_parser("reconcile", help="Reconcile from Gmail Sent/suppression evidence")
    p_recon.add_argument("--campaign-id", required=True)
    p_recon.add_argument("--gmail-user", default=None)
    p_recon.add_argument("--sent-folder", action="append", default=None)
    _add_db_arg(p_recon)
    p_recon.set_defaults(func=_cmd_reconcile)

    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
