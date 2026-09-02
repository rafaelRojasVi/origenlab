#!/usr/bin/env python3
"""Outbound campaign ledger operator CLI (durable SQLite campaign state).

Subcommands: init, status, contact-status {set,show}, candidates add, select,
batch show, send, reconcile, export, research-queue build.

``research-queue build`` is strictly read-only (opens SQLite with ``mode=ro``):
it ranks ``lead_master`` organizations that still need fresh public contact
research when the known-contact universe is exhausted, and writes one CSV
artifact. It never mutates campaign, lead, or suppression state -- see
``outbound_campaign_research_queue`` for the exclusion policy.

Canonical campaign state lives in SQLite. No command writes batch artifacts
anywhere by default — recipient/attempt state always comes from SQLite, never
from a file. The only way to get a CSV/JSON file out of this CLI is the
explicit ``export`` subcommand, which always requires an operator-supplied
``--out`` path and writes exactly there (Downloads included, if that's what
the operator asks for — this CLI does not second-guess an explicit export
destination; it only guarantees nothing is written *implicitly*).

Selection (``select``) and the sender's immediate pre-send recheck both use
the **strictest** canonical marketing gate (``outbound_core.gate_context_for_archive_batch``,
i.e. ``strict_contact_graph_noise=True``) by default, regardless of a
recipient's ``source_kind`` — including candidates added manually via
``candidates add``. This is deliberate: campaign sourcing is not guaranteed to
be pre-vetted (it can include contact_master/archive-derived contacts), so
using the weaker lead-only noise profile here would have let obvious
vendor/platform noise (e.g. supplier-domain rows such as Kalstein, Made-in-China,
EasyMailing, FedEx-style logistics senders) through if manually added to the
candidate table. No ad-hoc domain list was added for this — the existing
canonical ``supplier_master``-backed supplier-domain filter and
``marketing_contact_noise`` strict mode already own that responsibility and are
reused as-is.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sqlite3
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
from origenlab_email_pipeline.outbound_campaign_research_queue import (
    DEFAULT_FIT_BUCKETS,
    RESEARCH_QUEUE_FIELDNAMES,
    compute_research_queue,
    research_org_to_row,
)
from origenlab_email_pipeline.outbound_campaign_sender import send_campaign_batch
from origenlab_email_pipeline.outbound_campaign_store import (
    CampaignAlreadyExistsError,
    CampaignNotFoundError,
    RECIPIENT_STATES,
    campaign_progress,
    create_campaign,
    list_campaign_recipients,
    list_reserved_batch,
    reserve_next_batch,
    upsert_recipient_candidate,
)
from origenlab_email_pipeline.outbound_core import (
    gate_context_for_archive_batch,
    resolve_outbound_gmail_user,
    resolve_outbound_sent_folders,
)


def _resolve_db(args: argparse.Namespace) -> Path:
    return args.db or load_settings().resolved_sqlite_path()


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
        # Strict noise profile by default -- see module docstring. Campaign
        # candidates are not guaranteed pre-vetted; this must not weaken to the
        # lead-only profile just because a row's source_kind is "manual".
        gate_ctx = gate_context_for_archive_batch(
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
    db_path = _resolve_db(args)
    conn = connect(db_path)
    try:
        ensure_outbound_campaign_tables(conn)
        ensure_manual_contact_status_table(conn)
        if args.limit is not None and args.limit <= 0:
            print("--limit must be a positive integer.", file=sys.stderr)
            return 2
        rows = list_reserved_batch(conn, args.campaign_id)
        recipients = [(r["id"], r["email"]) for r in rows]
        if args.limit is not None:
            # list_reserved_batch orders by id (FIFO reservation order) -- keep the
            # oldest-reserved N and leave the rest untouched in 'reserved' state.
            recipients = recipients[: args.limit]
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
        # Same strict gate as `select` -- the pre-send recheck must not be weaker
        # than the selection check that already ran on these recipients.
        gate_ctx = gate_context_for_archive_batch(
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


def _cmd_export(args: argparse.Namespace) -> int:
    """Explicit, operator-invoked export only -- never called by any other command.

    Writes exactly to the path the operator supplies via --out (CSV by default,
    JSON if the path ends in .json), including a Downloads-style path if that is
    what the operator asked for. Default campaign operation never calls this.
    """
    db_path = _resolve_db(args)
    conn = connect(db_path)
    try:
        ensure_outbound_campaign_tables(conn)
        rows = list_campaign_recipients(conn, args.campaign_id, state=args.state)
    finally:
        conn.close()

    out_path = Path(args.out).expanduser()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if out_path.suffix.lower() == ".json":
        out_path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    else:
        with out_path.open("w", newline="", encoding="utf-8") as f:
            fieldnames = list(rows[0].keys()) if rows else [
                "id", "campaign_id", "email", "email_norm", "state", "source_kind",
                "source_ref", "institution_name", "selection_reason", "block_reason",
                "selected_at", "last_attempt_at", "sent_at", "last_gmail_message_id",
                "bounce_state", "created_at", "updated_at",
            ]
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
    print(f"Exported {len(rows)} recipient row(s) to {out_path}")
    return 0


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


def _cmd_research_queue_build(args: argparse.Namespace) -> int:
    """Fresh-public organization research queue -- strictly read-only.

    Opens the SQLite file in ``mode=ro`` (not the shared read/write ``connect``
    used by every other subcommand here) so a bug in this command cannot mutate
    campaign, lead, or suppression state even in principle. Writes exactly one
    CSV to ``--out`` (or the campaign-derived default path) -- never Downloads,
    never a numbered batch file.
    """
    db_path = _resolve_db(args)
    if not db_path.is_file():
        print(f"SQLite file not found: {db_path}", file=sys.stderr)
        return 1
    out_path = Path(args.out).expanduser() if args.out else (
        _ROOT / "reports" / "out" / "active" / "current" / f"{args.campaign_id}_fresh_public_research_queue.csv"
    )

    fit_buckets = DEFAULT_FIT_BUCKETS
    if args.include_low_fit:
        fit_buckets = ("high_fit", "medium_fit", "low_fit")

    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        orgs, stats = compute_research_queue(
            conn, fit_buckets=fit_buckets, limit=args.limit,
            include_discarded=args.include_discarded,
        )
    finally:
        conn.close()

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(RESEARCH_QUEUE_FIELDNAMES))
        writer.writeheader()
        for org in orgs:
            writer.writerow(research_org_to_row(org))

    print(json.dumps({
        "out": str(out_path),
        "leads_scanned": stats.leads_scanned,
        "orgs_scanned": stats.orgs_scanned,
        "blocked_too_low_relevance_leads": stats.blocked_too_low_relevance_leads,
        "blocked_already_has_contact": stats.blocked_already_has_contact,
        "blocked_supplier": stats.blocked_supplier,
        "blocked_suppression": stats.blocked_suppression,
        "blocked_noise": stats.blocked_noise,
        "blocked_discarded": stats.blocked_discarded,
        "final_queue_count": stats.final_queue_count,
    }, ensure_ascii=False, indent=2))
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
    p_send.add_argument(
        "--limit", type=int, default=None,
        help="Process at most this many currently-reserved recipients (FIFO by reservation order). "
             "Default: process all reserved recipients. Remaining reserved rows beyond the limit are "
             "left untouched for a later invocation.",
    )
    p_send.add_argument("--no-stop-on-error", action="store_true")
    p_send.add_argument("--open-browser", action="store_true")
    p_send.add_argument("--gmail-user", default=None)
    p_send.add_argument("--sent-folder", action="append", default=None)
    _add_db_arg(p_send)
    p_send.set_defaults(func=_cmd_send)

    p_recon = sub.add_parser("reconcile", help="Reconcile from Gmail Sent/suppression evidence")
    p_recon.add_argument("--campaign-id", required=True)
    p_recon.add_argument("--gmail-user", default=None)
    p_recon.add_argument("--sent-folder", action="append", default=None)
    _add_db_arg(p_recon)
    p_recon.set_defaults(func=_cmd_reconcile)

    p_export = sub.add_parser(
        "export", help="Explicit operator export of recipient rows to CSV/JSON (never automatic)",
    )
    p_export.add_argument("--campaign-id", required=True)
    p_export.add_argument("--out", required=True, help="Destination path (.json for JSON, else CSV)")
    p_export.add_argument("--state", choices=list(RECIPIENT_STATES), default=None, help="Filter by recipient state (default: all)")
    _add_db_arg(p_export)
    p_export.set_defaults(func=_cmd_export)

    p_research = sub.add_parser(
        "research-queue", help="Fresh-public organization/contact research queue (read-only)",
    )
    research_sub = p_research.add_subparsers(dest="research_queue_command", required=True)
    p_research_build = research_sub.add_parser(
        "build",
        help="Rank lead_master organizations needing fresh public contact research and write one CSV",
    )
    p_research_build.add_argument(
        "--campaign-id", required=True,
        help="Used only to label the run and derive the default --out filename; no DB write.",
    )
    p_research_build.add_argument(
        "--out", default=None,
        help="Destination CSV path (default: reports/out/active/current/<campaign-id>_fresh_public_research_queue.csv)",
    )
    p_research_build.add_argument("--limit", type=int, default=200)
    p_research_build.add_argument(
        "--include-low-fit", action="store_true",
        help="Also include low_fit organizations (default: high_fit/medium_fit only).",
    )
    p_research_build.add_argument(
        "--include-discarded", action="store_true",
        help="Also include organizations the operator already marked 'descartado' in lead_contact_research.",
    )
    _add_db_arg(p_research_build)
    p_research_build.set_defaults(func=_cmd_research_queue_build)

    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
