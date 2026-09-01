"""Refactored campaign sender: dry-run by default, records every attempt.

Calls ``gmail_send.py`` directly as a library (no shelling out to
``scripts/qa/send_inline_html_email_via_gmail_api.py``). A per-run temporary
CSV snapshot of the batch is written under ``tempfile`` purely for
crash-debuggability and is always removed in a ``finally`` block — it is
never treated as durable state; canonical state is the SQLite ledger.

Safety: re-checks hard eligibility (manual sidecar + canonical gate)
immediately before each send; refuses to re-send if a prior attempt for the
recipient was already ``accepted`` (idempotent no-op); each attempt commits
immediately so ``stop_on_error`` never rolls back a prior accepted send.
"""

from __future__ import annotations

import csv
import tempfile
from dataclasses import dataclass
from pathlib import Path

from origenlab_email_pipeline.gmail_send import build_gmail_message_with_inline_images, gmail_api_send_message
from origenlab_email_pipeline.manual_contact_status import load_manual_status_map
from origenlab_email_pipeline.outbound_campaign_gate import evaluate_campaign_eligibility
from origenlab_email_pipeline.outbound_campaign_store import get_campaign, has_accepted_attempt, record_attempt


@dataclass(frozen=True)
class SendOutcome:
    recipient_id: int
    email: str
    mode: str
    result: str
    gmail_message_id: str | None
    error: str | None


def _write_batch_snapshot(recipients: list[tuple[int, str]]) -> Path:
    fd, path = tempfile.mkstemp(prefix="outbound_campaign_batch_", suffix=".csv")
    tmp_path = Path(path)
    with open(fd, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["recipient_id", "email"])
        for rid, email in recipients:
            writer.writerow([rid, email])
    return tmp_path


def send_campaign_batch(
    conn,
    *,
    campaign_id: str,
    recipients: list[tuple[int, str]],
    html: str,
    html_dir: Path,
    live: bool,
    access_token: str | None,
    gate_ctx,
    batch_id: str,
    stop_on_error: bool = True,
) -> list[SendOutcome]:
    if live and not access_token:
        raise ValueError("access_token required for live sends")
    campaign = get_campaign(conn, campaign_id)
    if campaign is None:
        raise ValueError(f"Unknown campaign: {campaign_id}")
    sender_header = f"{campaign.sender_name} <{campaign.sender_email}>"
    manual_status = load_manual_status_map(conn)
    mode = "live" if live else "dry_run"

    outcomes: list[SendOutcome] = []
    snapshot_path = _write_batch_snapshot(recipients)
    try:
        for recipient_id, email in recipients:
            existing = has_accepted_attempt(conn, campaign_id, recipient_id)
            if existing is not None:
                outcomes.append(SendOutcome(
                    recipient_id=recipient_id, email=email, mode=mode,
                    result="skipped", gmail_message_id=existing[1], error="already_sent",
                ))
                continue

            recheck = evaluate_campaign_eligibility(
                contact_email=email, institution_name=None,
                gate_ctx=gate_ctx, manual_status_by_email=manual_status,
            )
            if not recheck.eligible:
                reason = recheck.reasons[0] if recheck.reasons else "ineligible"
                record_attempt(
                    conn, campaign_id=campaign_id, recipient_id=recipient_id, email_norm=email,
                    batch_id=batch_id, mode=mode, result="skipped", error_code=reason,
                )
                conn.commit()
                outcomes.append(SendOutcome(
                    recipient_id=recipient_id, email=email, mode=mode,
                    result="skipped", gmail_message_id=None, error=reason,
                ))
                if stop_on_error:
                    break
                continue

            msg, _inline_images = build_gmail_message_with_inline_images(
                sender_email=sender_header, to_emails=email, subject=campaign.subject,
                html=html, html_dir=html_dir,
            )

            if not live:
                record_attempt(
                    conn, campaign_id=campaign_id, recipient_id=recipient_id, email_norm=email,
                    batch_id=batch_id, mode="dry_run", result="accepted",
                )
                conn.commit()
                outcomes.append(SendOutcome(
                    recipient_id=recipient_id, email=email, mode="dry_run",
                    result="accepted", gmail_message_id=None, error=None,
                ))
                continue

            try:
                api_result = gmail_api_send_message(access_token=access_token, raw_message_bytes=msg.as_bytes())
                message_id = api_result.get("id")
                record_attempt(
                    conn, campaign_id=campaign_id, recipient_id=recipient_id, email_norm=email,
                    batch_id=batch_id, mode="live", result="accepted", gmail_message_id=message_id,
                )
                conn.commit()
                outcomes.append(SendOutcome(
                    recipient_id=recipient_id, email=email, mode="live",
                    result="accepted", gmail_message_id=message_id, error=None,
                ))
            except Exception as exc:
                record_attempt(
                    conn, campaign_id=campaign_id, recipient_id=recipient_id, email_norm=email,
                    batch_id=batch_id, mode="live", result="failed", error_detail=str(exc),
                )
                conn.commit()
                outcomes.append(SendOutcome(
                    recipient_id=recipient_id, email=email, mode="live",
                    result="failed", gmail_message_id=None, error=str(exc),
                ))
                if stop_on_error:
                    break
    finally:
        snapshot_path.unlink(missing_ok=True)

    return outcomes
