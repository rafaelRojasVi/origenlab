"""Reconciliation: reflect Sent history + contact_email_suppression bounce evidence onto
campaign recipient/attempt rows. Read-only against ``emails`` and
``contact_email_suppression`` — never writes to either. Bounces continue to be owned by
the existing suppression subsystem; this module only mirrors the result onto the ledger.
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
    suppressed = {
        row[0] for row in conn.execute("SELECT lower(trim(email)) FROM contact_email_suppression").fetchall()
        if row[0]
    }

    rows = conn.execute(
        "SELECT id, email_norm FROM outbound_campaign_recipient "
        "WHERE campaign_id = ? AND state IN ('sent', 'reserved')",
        (campaign_id,),
    ).fetchall()

    checked = confirmed = bounced = no_evidence = 0
    for recipient_id, email_norm in rows:
        checked += 1
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
    return ReconcileSummary(checked=checked, confirmed_sent=confirmed, bounced=bounced, no_evidence=no_evidence)
