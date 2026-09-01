"""Reconciliation: reflect Sent history + contact_email_suppression bounce evidence onto
campaign recipient/attempt rows. Read-only against ``emails`` and
``contact_email_suppression`` — never writes to either. Bounces continue to be owned by
the existing suppression subsystem; this module only mirrors the result onto the ledger.

Matching strategy (bounded, address-level — not message-level):
    Gmail API's ``users.messages.send`` response ``id`` field (stored as
    ``outbound_send_attempt.gmail_message_id``) is an **internal Gmail resource id**.
    It is not the RFC 822 ``Message-ID`` header. IMAP ingest
    (``ingest/gmail_imap.py``) populates ``emails.message_id`` from
    ``msg.get("Message-ID")`` — the raw MIME header — a different identifier space
    entirely. There is no code path in this repo that captures the RFC Message-ID at
    send time, so **exact message-id reconciliation is not available** and must not
    be assumed.

    Given that, reconciliation matches by **normalized recipient email address**
    within the configured Gmail Sent folders — the same evidence source
    (``marketing_export_context.load_sent_recipient_norms``) the existing
    archive/lead outbound lanes already use for Sent-history blocking. This is
    coarse (it confirms "we sent *something* to this address in this scan window",
    not "this specific attempt's message"), so it fails closed: absence of evidence
    is reported as ``no_evidence`` (never auto-treated as failure, never
    auto-retried), and an ``in_flight`` attempt is only auto-resolved when *positive*
    evidence exists (Sent-folder match or bounce-suppression match) — a genuinely
    ambiguous attempt (no evidence either way) is left ``in_flight`` for explicit
    operator review.

    Precedence when both bounce-suppression and Sent-folder evidence exist for the
    same address: bounce wins. A bounce is evidence the message *was* sent and then
    rejected downstream, so it is a superset of "sent" — resolving to
    ``bounced`` (not ``confirmed_sent``) surfaces the actionable fact (this address
    needs suppression follow-up) rather than the weaker one.
"""

from __future__ import annotations

from dataclasses import dataclass

from origenlab_email_pipeline.marketing_export_context import load_sent_recipient_norms
from origenlab_email_pipeline.timeutil import now_iso


@dataclass(frozen=True)
class ReconcileSummary:
    checked: int
    confirmed_sent: int
    bounced: int
    no_evidence: int
    resolved_in_flight: int
    still_in_flight: int


def _table_exists(conn, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1", (name,)
    ).fetchone()
    return bool(row)


def _load_suppressed_norms(conn) -> set[str]:
    if not _table_exists(conn, "contact_email_suppression"):
        return set()
    rows = conn.execute("SELECT lower(trim(email)) FROM contact_email_suppression").fetchall()
    return {row[0] for row in rows if row[0]}


def _latest_attempt(conn, campaign_id: str, recipient_id: int) -> tuple[int, str] | None:
    row = conn.execute(
        "SELECT id, result FROM outbound_send_attempt "
        "WHERE campaign_id = ? AND recipient_id = ? ORDER BY attempt_seq DESC LIMIT 1",
        (campaign_id, recipient_id),
    ).fetchone()
    return (int(row[0]), row[1]) if row else None


def reconcile_campaign(
    conn,
    campaign_id: str,
    *,
    gmail_user: str,
    sent_folders: tuple[str, ...],
    at_iso: str | None = None,
) -> ReconcileSummary:
    ts = at_iso or now_iso()
    sent_norms = load_sent_recipient_norms(conn, gmail_user=gmail_user, sent_folders=sent_folders)
    suppressed = _load_suppressed_norms(conn)

    rows = conn.execute(
        "SELECT id, email_norm FROM outbound_campaign_recipient "
        "WHERE campaign_id = ? AND state IN ('sent', 'reserved')",
        (campaign_id,),
    ).fetchall()

    checked = confirmed = bounced = no_evidence = resolved_in_flight = still_in_flight = 0
    for recipient_id, email_norm in rows:
        checked += 1
        latest = _latest_attempt(conn, campaign_id, recipient_id)

        if latest is not None and latest[1] == "in_flight":
            attempt_id = latest[0]
            if email_norm in suppressed:
                # Bounce evidence proves the message was accepted by Gmail and later
                # rejected -- safe to resolve the ambiguous window as accepted+bounced.
                conn.execute(
                    "UPDATE outbound_send_attempt SET result = 'accepted', "
                    "reconciliation_status = 'bounced', resolved_at = ? WHERE id = ?",
                    (ts, attempt_id),
                )
                conn.execute(
                    "UPDATE outbound_campaign_recipient "
                    "SET state = 'bounced', bounce_state = 'bounced', updated_at = ? WHERE id = ?",
                    (ts, recipient_id),
                )
                resolved_in_flight += 1
                bounced += 1
            elif email_norm in sent_norms:
                conn.execute(
                    "UPDATE outbound_send_attempt SET result = 'accepted', "
                    "reconciliation_status = 'confirmed_sent', resolved_at = ? WHERE id = ?",
                    (ts, attempt_id),
                )
                conn.execute(
                    "UPDATE outbound_campaign_recipient SET state = 'sent', sent_at = ?, updated_at = ? WHERE id = ?",
                    (ts, ts, recipient_id),
                )
                resolved_in_flight += 1
                confirmed += 1
            else:
                # No positive evidence either way -- stays in_flight. Do NOT guess.
                still_in_flight += 1
            continue

        if email_norm in suppressed:
            bounced += 1
            conn.execute(
                "UPDATE outbound_campaign_recipient SET state = 'bounced', bounce_state = 'bounced', updated_at = ? WHERE id = ?",
                (ts, recipient_id),
            )
            conn.execute(
                "UPDATE outbound_send_attempt SET reconciliation_status = 'bounced' "
                "WHERE campaign_id = ? AND recipient_id = ? AND result = 'accepted'",
                (campaign_id, recipient_id),
            )
        elif email_norm in sent_norms:
            confirmed += 1
            conn.execute(
                "UPDATE outbound_send_attempt SET reconciliation_status = 'confirmed_sent' "
                "WHERE campaign_id = ? AND recipient_id = ? AND result = 'accepted'",
                (campaign_id, recipient_id),
            )
        else:
            no_evidence += 1
            conn.execute(
                "UPDATE outbound_send_attempt SET reconciliation_status = 'no_evidence' "
                "WHERE campaign_id = ? AND recipient_id = ? AND result = 'accepted'",
                (campaign_id, recipient_id),
            )
    conn.commit()
    return ReconcileSummary(
        checked=checked, confirmed_sent=confirmed, bounced=bounced, no_evidence=no_evidence,
        resolved_in_flight=resolved_in_flight, still_in_flight=still_in_flight,
    )
