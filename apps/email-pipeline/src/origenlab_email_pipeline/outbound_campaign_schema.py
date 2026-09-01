"""Schema owner: durable outbound campaign ledger (SQLite operator sidecar family).

Owns four tables, all additive and lazily created via ``ensure_outbound_campaign_tables``
(not part of the default ``sqlite_migrate.migrate_sqlite_schema`` stack — same pattern as
``outreach_contact_state.py``). No table here has a ``REFERENCES`` into a rebuildable
projection (``contact_master`` / ``organization_master`` / ``lead_master``); those are
joined by normalized email/domain string only.

- ``outbound_campaign``: one row per campaign, stable ``campaign_id`` key.
- ``outbound_campaign_recipient``: one row per (campaign, email); durable lifecycle state.
- ``outbound_send_attempt``: append-only attempt ledger; idempotency via
  ``UNIQUE(campaign_id, recipient_id, attempt_seq)`` plus an application-level
  "already accepted" guard in ``outbound_campaign_store``.
- ``manual_contact_status``: operator-owned contact lifecycle sidecar (active/inactive/hold).
  Does not mutate ``contact_master``. inactive/hold is a hard exact-email campaign block.
  "active" is informational only — it is NOT marketing consent and does not bypass any
  other gate check.
"""

from __future__ import annotations

import sqlite3

OUTBOUND_CAMPAIGN_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS outbound_campaign (
  campaign_id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  sender_email TEXT NOT NULL,
  sender_name TEXT NOT NULL,
  subject TEXT NOT NULL,
  target_attempt_count INTEGER NOT NULL,
  baseline_attempt_count INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'active'
    CHECK (status IN ('active','paused','completed','archived')),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS outbound_campaign_recipient (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  campaign_id TEXT NOT NULL,
  email TEXT NOT NULL,
  email_norm TEXT NOT NULL,
  state TEXT NOT NULL DEFAULT 'candidate'
    CHECK (state IN ('candidate','selected','reserved','sent','blocked','bounced','replied','inactive')),
  source_kind TEXT,
  source_ref TEXT,
  institution_name TEXT,
  selection_reason TEXT,
  block_reason TEXT,
  selected_at TEXT,
  last_attempt_at TEXT,
  sent_at TEXT,
  last_gmail_message_id TEXT,
  bounce_state TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(campaign_id, email_norm)
);
CREATE INDEX IF NOT EXISTS idx_outbound_campaign_recipient_campaign_state
  ON outbound_campaign_recipient(campaign_id, state);

CREATE TABLE IF NOT EXISTS outbound_send_attempt (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  campaign_id TEXT NOT NULL,
  recipient_id INTEGER NOT NULL,
  email_norm TEXT NOT NULL,
  batch_id TEXT NOT NULL,
  attempt_seq INTEGER NOT NULL,
  attempted_at TEXT NOT NULL,
  mode TEXT NOT NULL CHECK (mode IN ('dry_run','live')),
  result TEXT NOT NULL CHECK (result IN ('accepted','failed','skipped')),
  gmail_message_id TEXT,
  error_code TEXT,
  error_detail TEXT,
  reconciliation_status TEXT NOT NULL DEFAULT 'unreconciled'
    CHECK (reconciliation_status IN ('unreconciled','confirmed_sent','bounced','no_evidence')),
  created_at TEXT NOT NULL,
  UNIQUE(campaign_id, recipient_id, attempt_seq)
);
CREATE INDEX IF NOT EXISTS idx_outbound_send_attempt_recipient
  ON outbound_send_attempt(campaign_id, recipient_id);
CREATE INDEX IF NOT EXISTS idx_outbound_send_attempt_result
  ON outbound_send_attempt(campaign_id, result);

CREATE TABLE IF NOT EXISTS manual_contact_status (
  email_norm TEXT PRIMARY KEY,
  status TEXT NOT NULL CHECK (status IN ('active','inactive','hold')),
  organization_domain TEXT,
  organization_name TEXT,
  role_label TEXT,
  reason TEXT,
  evidence TEXT,
  effective_at TEXT,
  updated_at TEXT NOT NULL,
  updated_by TEXT
);
CREATE INDEX IF NOT EXISTS idx_manual_contact_status_status
  ON manual_contact_status(status);
"""

_TABLES = (
    "outbound_campaign",
    "outbound_campaign_recipient",
    "outbound_send_attempt",
    "manual_contact_status",
)


def ensure_outbound_campaign_tables(conn: sqlite3.Connection) -> None:
    conn.executescript(OUTBOUND_CAMPAIGN_SCHEMA_SQL)


def outbound_campaign_tables_exist(conn: sqlite3.Connection) -> bool:
    for table in _TABLES:
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()
        if not row:
            return False
    return True
