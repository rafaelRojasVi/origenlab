"""Refactored campaign sender: dry-run by default, records every attempt.

Calls ``gmail_send.py`` directly as a library (no shelling out to
``scripts/qa/send_inline_html_email_via_gmail_api.py``). Recipient state comes
entirely from SQLite — no CSV of any kind is written by this module. (An
earlier revision wrote a temporary CSV "for crash-debuggability"; it was
removed because the library-call refactor never needed one — the existing
Gmail sender library does not require a recipient CSV, so per the original
design brief there is nothing to write. Explicit CSV/JSON export is a
separate, operator-invoked concern — see the CLI's ``export`` subcommand.)

Safety: re-checks hard eligibility (manual sidecar + canonical gate)
immediately before each send; refuses to re-send if a prior attempt for the
recipient was already ``accepted``. A live send is two-phase — see
``outbound_campaign_store.begin_live_attempt`` / ``finish_live_attempt``:
the ``in_flight`` row is committed *before* the Gmail API call, so if the
process dies between Gmail accepting the message and the terminal result
being persisted, the row is left ``in_flight`` and a retry refuses to call
Gmail again for that recipient (fail-closed; requires reconciliation or
explicit operator recovery — see ``outbound_campaign_reconcile.py``). Each
attempt commits immediately so ``stop_on_error`` never rolls back a prior
accepted send.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from origenlab_email_pipeline.gmail_send import build_gmail_message_with_inline_images, gmail_api_send_message
from origenlab_email_pipeline.manual_contact_status import load_manual_status_map
from origenlab_email_pipeline.outbound_campaign_gate import evaluate_campaign_eligibility
from origenlab_email_pipeline.outbound_campaign_store import (
    begin_live_attempt,
    finish_live_attempt,
    get_campaign,
    has_accepted_attempt,
    latest_attempt_status,
    record_attempt,
)

REASON_ALREADY_SENT = "already_sent"
REASON_AMBIGUOUS_IN_FLIGHT = "ambiguous_in_flight_requires_reconciliation"


@dataclass(frozen=True)
class SendOutcome:
    recipient_id: int
    email: str
    mode: str
    result: str
    gmail_message_id: str | None
    error: str | None


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
    for recipient_id, email in recipients:
        existing = has_accepted_attempt(conn, campaign_id, recipient_id)
        if existing is not None:
            outcomes.append(SendOutcome(
                recipient_id=recipient_id, email=email, mode=mode,
                result="skipped", gmail_message_id=existing[1], error=REASON_ALREADY_SENT,
            ))
            continue

        latest = latest_attempt_status(conn, campaign_id, recipient_id)
        if latest is not None and latest[0] == "in_flight":
            # A previous live attempt may or may not have reached Gmail before the
            # process died. We cannot know, so we refuse to call Gmail again --
            # this recipient needs reconciliation or explicit operator recovery.
            outcomes.append(SendOutcome(
                recipient_id=recipient_id, email=email, mode=mode,
                result="skipped", gmail_message_id=None, error=REASON_AMBIGUOUS_IN_FLIGHT,
            ))
            if stop_on_error:
                break
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
            # True dry-run: eligibility was rechecked and the complete Gmail
            # message was successfully built above, but campaign state must
            # remain untouched. In particular, do not create an accepted
            # attempt: accepted attempts represent actual Gmail acceptance and
            # transition the recipient to sent.
            outcomes.append(SendOutcome(
                recipient_id=recipient_id, email=email, mode="dry_run",
                result="accepted", gmail_message_id=None, error=None,
            ))
            continue

        # Phase 1: durably record intent BEFORE calling Gmail, and commit it. If the
        # process dies anywhere after this commit, the row is left in_flight and the
        # check above (latest_attempt_status) protects the next run.
        attempt_id = begin_live_attempt(
            conn, campaign_id=campaign_id, recipient_id=recipient_id,
            email_norm=email, batch_id=batch_id,
        )
        conn.commit()

        try:
            api_result = gmail_api_send_message(access_token=access_token, raw_message_bytes=msg.as_bytes())
            message_id = api_result.get("id")
        except Exception as exc:
            # Phase 2 (failure path): Gmail did not accept it -- safe to resolve to
            # 'failed' so a future run can retry this recipient.
            finish_live_attempt(
                conn, attempt_id=attempt_id, recipient_id=recipient_id,
                result="failed", error_detail=str(exc),
            )
            conn.commit()
            outcomes.append(SendOutcome(
                recipient_id=recipient_id, email=email, mode="live",
                result="failed", gmail_message_id=None, error=str(exc),
            ))
            if stop_on_error:
                break
            continue

        # Phase 2 (success path): Gmail accepted it -- resolve immediately.
        finish_live_attempt(
            conn, attempt_id=attempt_id, recipient_id=recipient_id,
            result="accepted", gmail_message_id=message_id,
        )
        conn.commit()
        outcomes.append(SendOutcome(
            recipient_id=recipient_id, email=email, mode="live",
            result="accepted", gmail_message_id=message_id, error=None,
        ))

    return outcomes
