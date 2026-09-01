"""Operator-owned manual contact lifecycle sidecar (active/inactive/hold).

Additive only — never mutates ``contact_master`` or ``organization_master``.
An inactive/hold row is a hard, exact-email block on campaign eligibility
(see ``outbound_campaign_gate.evaluate_campaign_eligibility``). An "active"
row is informational only: it is NOT marketing consent and must never bypass
any other gate check (suppression, domain suppression, Sent-history, outreach
state, supplier domain, noise filters).

Table DDL lives in ``outbound_campaign_schema`` (single schema owner module);
this module owns validation, upsert, and read helpers, mirroring the shape of
``outreach_contact_state.py`` / ``contact_email_suppression.py``.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from origenlab_email_pipeline.business_mart import emails_in
from origenlab_email_pipeline.outbound_campaign_schema import ensure_outbound_campaign_tables
from origenlab_email_pipeline.timeutil import now_iso

MANUAL_CONTACT_STATUSES: tuple[str, ...] = ("active", "inactive", "hold")
HARD_BLOCK_STATUSES: frozenset[str] = frozenset({"inactive", "hold"})

_MAX_EMAIL = 320
_MAX_TEXT = 4000
_MAX_LABEL = 200
_MAX_UPDATED_BY = 160


@dataclass(frozen=True)
class ManualContactStatusPayload:
    email_norm: str
    status: str
    organization_domain: str | None
    organization_name: str | None
    role_label: str | None
    reason: str | None
    evidence: str | None
    effective_at: str | None
    updated_by: str | None


def ensure_manual_contact_status_table(conn: sqlite3.Connection) -> None:
    """The table lives in the shared outbound campaign DDL; ensure the whole family."""
    ensure_outbound_campaign_tables(conn)


def _trim(value: str | None, max_len: int) -> str | None:
    if value is None:
        return None
    s = str(value).strip()
    return s[:max_len] if s else None


def normalize_manual_contact_email(email: str) -> str:
    s = _trim(email, _MAX_EMAIL)
    if not s:
        raise ValueError("Correo no válido: use un email claro tipo nombre@dominio.cl")
    lowered = s.lower()
    found = emails_in(lowered)
    if not found or found[0] != lowered:
        raise ValueError("Correo no válido: use un email claro tipo nombre@dominio.cl")
    return lowered


def validate_manual_contact_status_payload(
    *,
    email: str,
    status: str,
    organization_domain: str | None = None,
    organization_name: str | None = None,
    role_label: str | None = None,
    reason: str | None = None,
    evidence: str | None = None,
    effective_at: str | None = None,
    updated_by: str | None = None,
) -> ManualContactStatusPayload:
    norm = normalize_manual_contact_email(email)
    st = (status or "").strip().lower()
    if st not in MANUAL_CONTACT_STATUSES:
        raise ValueError(
            f"Estado no válido: {status!r}. Use uno de: {', '.join(MANUAL_CONTACT_STATUSES)}."
        )
    return ManualContactStatusPayload(
        email_norm=norm,
        status=st,
        organization_domain=_trim(organization_domain, _MAX_LABEL),
        organization_name=_trim(organization_name, _MAX_LABEL),
        role_label=_trim(role_label, _MAX_LABEL),
        reason=_trim(reason, _MAX_TEXT),
        evidence=_trim(evidence, _MAX_TEXT),
        effective_at=_trim(effective_at, 64),
        updated_by=_trim(updated_by, _MAX_UPDATED_BY),
    )


def upsert_manual_contact_status(
    conn: sqlite3.Connection, *, payload: ManualContactStatusPayload, at_iso: str | None = None
) -> None:
    ts = at_iso or now_iso()
    conn.execute(
        """
        INSERT INTO manual_contact_status (
          email_norm, status, organization_domain, organization_name, role_label,
          reason, evidence, effective_at, updated_at, updated_by
        ) VALUES (?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(email_norm) DO UPDATE SET
          status = excluded.status,
          organization_domain = excluded.organization_domain,
          organization_name = excluded.organization_name,
          role_label = excluded.role_label,
          reason = excluded.reason,
          evidence = excluded.evidence,
          effective_at = excluded.effective_at,
          updated_at = excluded.updated_at,
          updated_by = excluded.updated_by
        """,
        (
            payload.email_norm, payload.status, payload.organization_domain,
            payload.organization_name, payload.role_label, payload.reason,
            payload.evidence, payload.effective_at, ts, payload.updated_by,
        ),
    )


_COLS = (
    "email_norm", "status", "organization_domain", "organization_name", "role_label",
    "reason", "evidence", "effective_at", "updated_at", "updated_by",
)


def fetch_manual_contact_status(conn: sqlite3.Connection, email: str) -> dict[str, object] | None:
    try:
        key = normalize_manual_contact_email(email)
    except ValueError:
        return None
    row = conn.execute(
        f"SELECT {', '.join(_COLS)} FROM manual_contact_status WHERE email_norm = ?", (key,)
    ).fetchone()
    if row is None:
        return None
    return dict(zip(_COLS, row))


def load_manual_status_map(conn: sqlite3.Connection) -> dict[str, str]:
    rows = conn.execute("SELECT email_norm, status FROM manual_contact_status").fetchall()
    return {r[0]: r[1] for r in rows}


def load_hard_block_norms(conn: sqlite3.Connection) -> frozenset[str]:
    rows = conn.execute(
        "SELECT email_norm FROM manual_contact_status WHERE status IN ('inactive','hold')"
    ).fetchall()
    return frozenset(r[0] for r in rows)
