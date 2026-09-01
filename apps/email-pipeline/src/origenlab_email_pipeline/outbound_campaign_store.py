"""Durable outbound campaign ledger repository (campaign, recipient, attempt).

DDL lives in ``outbound_campaign_schema``. This module owns read/write behavior:
campaign CRUD, candidate upsert, selection/reservation, append-only attempt
recording with idempotency, and progress stats.

No FK into ``contact_master``/``organization_master``/``lead_master`` — those
are rebuildable projections; recipients carry a copied ``email``/``institution_name``
snapshot plus a ``source_kind``/``source_ref`` provenance pair instead.
"""

from __future__ import annotations

import sqlite3
import uuid as _uuid
from dataclasses import dataclass

from origenlab_email_pipeline.business_mart import emails_in
from origenlab_email_pipeline.outbound_campaign_gate import evaluate_campaign_eligibility
from origenlab_email_pipeline.timeutil import now_iso

RECIPIENT_STATES: tuple[str, ...] = (
    "candidate", "selected", "reserved", "sent", "blocked", "bounced", "replied", "inactive",
)


class CampaignAlreadyExistsError(ValueError):
    pass


class CampaignNotFoundError(ValueError):
    pass


def normalize_campaign_email(email: str) -> str:
    s = (email or "").strip()
    if not s:
        raise ValueError("Correo no válido: use un email claro tipo nombre@dominio.cl")
    lowered = s.lower()
    found = emails_in(lowered)
    if not found or found[0] != lowered:
        raise ValueError("Correo no válido: use un email claro tipo nombre@dominio.cl")
    return lowered


@dataclass(frozen=True)
class CampaignRow:
    campaign_id: str
    name: str
    sender_email: str
    sender_name: str
    subject: str
    target_attempt_count: int
    baseline_attempt_count: int
    status: str
    created_at: str
    updated_at: str


_CAMPAIGN_COLS = (
    "campaign_id", "name", "sender_email", "sender_name", "subject",
    "target_attempt_count", "baseline_attempt_count", "status", "created_at", "updated_at",
)


def create_campaign(
    conn: sqlite3.Connection,
    *,
    campaign_id: str,
    name: str,
    sender_email: str,
    sender_name: str,
    subject: str,
    target_attempt_count: int,
    baseline_attempt_count: int = 0,
    status: str = "active",
    at_iso: str | None = None,
) -> None:
    cid = (campaign_id or "").strip()
    if not cid:
        raise ValueError("campaign_id requerido")
    ts = at_iso or now_iso()
    try:
        conn.execute(
            """
            INSERT INTO outbound_campaign (
              campaign_id, name, sender_email, sender_name, subject,
              target_attempt_count, baseline_attempt_count, status, created_at, updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?)
            """,
            (cid, name, sender_email, sender_name, subject,
             int(target_attempt_count), int(baseline_attempt_count), status, ts, ts),
        )
    except sqlite3.IntegrityError as exc:
        raise CampaignAlreadyExistsError(f"Campaign ya existe: {cid}") from exc


def get_campaign(conn: sqlite3.Connection, campaign_id: str) -> CampaignRow | None:
    row = conn.execute(
        f"SELECT {', '.join(_CAMPAIGN_COLS)} FROM outbound_campaign WHERE campaign_id = ?",
        (campaign_id,),
    ).fetchone()
    if row is None:
        return None
    return CampaignRow(**dict(zip(_CAMPAIGN_COLS, row)))


_RECIPIENT_COLS = (
    "id", "campaign_id", "email", "email_norm", "state", "source_kind", "source_ref",
    "institution_name", "selection_reason", "block_reason", "selected_at",
    "last_attempt_at", "sent_at", "last_gmail_message_id", "bounce_state",
    "created_at", "updated_at",
)


def upsert_recipient_candidate(
    conn: sqlite3.Connection,
    *,
    campaign_id: str,
    email: str,
    source_kind: str,
    source_ref: str | None = None,
    institution_name: str | None = None,
    at_iso: str | None = None,
) -> int:
    ts = at_iso or now_iso()
    norm = normalize_campaign_email(email)
    existing = conn.execute(
        "SELECT id FROM outbound_campaign_recipient WHERE campaign_id = ? AND email_norm = ?",
        (campaign_id, norm),
    ).fetchone()
    if existing is not None:
        rid = int(existing[0])
        conn.execute(
            """
            UPDATE outbound_campaign_recipient
            SET source_kind = ?, source_ref = ?, institution_name = ?, updated_at = ?
            WHERE id = ?
            """,
            (source_kind, source_ref, institution_name, ts, rid),
        )
        return rid
    cur = conn.execute(
        """
        INSERT INTO outbound_campaign_recipient (
          campaign_id, email, email_norm, state, source_kind, source_ref,
          institution_name, created_at, updated_at
        ) VALUES (?,?,?,?,?,?,?,?,?)
        """,
        (campaign_id, email.strip(), norm, "candidate", source_kind, source_ref,
         institution_name, ts, ts),
    )
    return int(cur.lastrowid)


def list_candidates(conn: sqlite3.Connection, campaign_id: str, limit: int | None = None) -> list[dict]:
    sql = (
        f"SELECT {', '.join(_RECIPIENT_COLS)} FROM outbound_campaign_recipient "
        "WHERE campaign_id = ? AND state = 'candidate' ORDER BY id"
    )
    args: list[object] = [campaign_id]
    if limit is not None:
        sql += " LIMIT ?"
        args.append(int(limit))
    rows = conn.execute(sql, args).fetchall()
    return [dict(zip(_RECIPIENT_COLS, row)) for row in rows]


@dataclass(frozen=True)
class BatchSelectionResult:
    batch_id: str
    reserved: list[int]
    blocked: list[tuple[int, str]]


def reserve_next_batch(
    conn: sqlite3.Connection,
    campaign_id: str,
    *,
    gate_ctx,
    manual_status_by_email: dict[str, str],
    n: int,
    at_iso: str | None = None,
) -> BatchSelectionResult:
    ts = at_iso or now_iso()
    batch_id = str(_uuid.uuid4())
    rows = conn.execute(
        "SELECT id, email_norm, institution_name FROM outbound_campaign_recipient "
        "WHERE campaign_id = ? AND state = 'candidate' ORDER BY id",
        (campaign_id,),
    ).fetchall()

    reserved: list[int] = []
    blocked: list[tuple[int, str]] = []
    for rid, email_norm, institution_name in rows:
        if len(reserved) >= n:
            break
        result = evaluate_campaign_eligibility(
            contact_email=email_norm, institution_name=institution_name,
            gate_ctx=gate_ctx, manual_status_by_email=manual_status_by_email,
        )
        if result.eligible:
            conn.execute(
                "UPDATE outbound_campaign_recipient "
                "SET state = 'reserved', selection_reason = 'gate_eligible', selected_at = ?, updated_at = ? "
                "WHERE id = ?",
                (ts, ts, rid),
            )
            reserved.append(int(rid))
        else:
            reason = result.reasons[0] if result.reasons else "ineligible"
            conn.execute(
                "UPDATE outbound_campaign_recipient SET state = 'blocked', block_reason = ?, updated_at = ? WHERE id = ?",
                (reason, ts, rid),
            )
            blocked.append((int(rid), reason))
    return BatchSelectionResult(batch_id=batch_id, reserved=reserved, blocked=blocked)


def list_reserved_batch(conn: sqlite3.Connection, campaign_id: str) -> list[dict]:
    rows = conn.execute(
        f"SELECT {', '.join(_RECIPIENT_COLS)} FROM outbound_campaign_recipient "
        "WHERE campaign_id = ? AND state = 'reserved' ORDER BY id",
        (campaign_id,),
    ).fetchall()
    return [dict(zip(_RECIPIENT_COLS, row)) for row in rows]


def has_accepted_attempt(
    conn: sqlite3.Connection, campaign_id: str, recipient_id: int
) -> tuple[int, str | None] | None:
    row = conn.execute(
        "SELECT id, gmail_message_id FROM outbound_send_attempt "
        "WHERE campaign_id = ? AND recipient_id = ? AND result = 'accepted' "
        "ORDER BY attempt_seq DESC LIMIT 1",
        (campaign_id, recipient_id),
    ).fetchone()
    return (int(row[0]), row[1]) if row else None


def next_attempt_seq(conn: sqlite3.Connection, campaign_id: str, recipient_id: int) -> int:
    row = conn.execute(
        "SELECT COALESCE(MAX(attempt_seq), 0) FROM outbound_send_attempt WHERE campaign_id = ? AND recipient_id = ?",
        (campaign_id, recipient_id),
    ).fetchone()
    return int(row[0]) + 1


def record_attempt(
    conn: sqlite3.Connection,
    *,
    campaign_id: str,
    recipient_id: int,
    email_norm: str,
    batch_id: str,
    mode: str,
    result: str,
    gmail_message_id: str | None = None,
    error_code: str | None = None,
    error_detail: str | None = None,
    at_iso: str | None = None,
) -> int:
    ts = at_iso or now_iso()
    seq = next_attempt_seq(conn, campaign_id, recipient_id)
    cur = conn.execute(
        """
        INSERT INTO outbound_send_attempt (
          campaign_id, recipient_id, email_norm, batch_id, attempt_seq, attempted_at,
          mode, result, gmail_message_id, error_code, error_detail, reconciliation_status, created_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?, 'unreconciled', ?)
        """,
        (campaign_id, recipient_id, email_norm, batch_id, seq, ts,
         mode, result, gmail_message_id, error_code, error_detail, ts),
    )
    conn.execute(
        "UPDATE outbound_campaign_recipient SET last_attempt_at = ?, updated_at = ? WHERE id = ?",
        (ts, ts, recipient_id),
    )
    if result == "accepted":
        conn.execute(
            "UPDATE outbound_campaign_recipient "
            "SET state = 'sent', sent_at = ?, last_gmail_message_id = ?, updated_at = ? WHERE id = ?",
            (ts, gmail_message_id, ts, recipient_id),
        )
    return int(cur.lastrowid)


@dataclass(frozen=True)
class CampaignProgress:
    target: int
    baseline: int
    ledger_attempts: int
    total_accepted: int
    remaining: int
    candidates: int
    selected_reserved: int
    sent: int
    blocked: int
    bounced: int


def campaign_progress(conn: sqlite3.Connection, campaign_id: str) -> CampaignProgress:
    campaign = get_campaign(conn, campaign_id)
    if campaign is None:
        raise CampaignNotFoundError(campaign_id)
    ledger_attempts = int(
        conn.execute(
            "SELECT COUNT(*) FROM outbound_send_attempt WHERE campaign_id = ? AND result = 'accepted'",
            (campaign_id,),
        ).fetchone()[0]
    )
    counts = {"candidate": 0, "reserved": 0, "sent": 0, "blocked": 0, "bounced": 0}
    for state, count in conn.execute(
        "SELECT state, COUNT(*) FROM outbound_campaign_recipient WHERE campaign_id = ? GROUP BY state",
        (campaign_id,),
    ).fetchall():
        counts[state] = count
    total_accepted = campaign.baseline_attempt_count + ledger_attempts
    remaining = max(0, campaign.target_attempt_count - total_accepted)
    return CampaignProgress(
        target=campaign.target_attempt_count,
        baseline=campaign.baseline_attempt_count,
        ledger_attempts=ledger_attempts,
        total_accepted=total_accepted,
        remaining=remaining,
        candidates=counts["candidate"],
        selected_reserved=counts["reserved"],
        sent=counts["sent"],
        blocked=counts["blocked"],
        bounced=counts["bounced"],
    )
