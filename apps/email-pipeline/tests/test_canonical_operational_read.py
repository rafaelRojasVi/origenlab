"""Canonical operational mart counts (SQLite read-only)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from origenlab_email_pipeline.contacto_gmail_source import CONTACTO_GMAIL_SOURCE_PREFIX
from origenlab_email_pipeline.canonical_operational_sql import (
    count_archive_mart_table,
    count_canonical_operational_contacts,
    count_canonical_operational_opportunity_signals,
    canonical_emails_where,
)


def _seed_db(path: Path) -> None:
    conn = sqlite3.connect(str(path))
    conn.executescript(
        f"""
        CREATE TABLE emails (
          id INTEGER PRIMARY KEY,
          date_iso TEXT,
          subject TEXT,
          sender TEXT,
          recipients TEXT,
          source_file TEXT
        );
        CREATE TABLE contact_master (
          email TEXT PRIMARY KEY,
          domain TEXT,
          last_seen_at TEXT
        );
        CREATE TABLE organization_master (
          domain TEXT PRIMARY KEY,
          last_seen_at TEXT
        );
        CREATE TABLE opportunity_signals (
          id INTEGER PRIMARY KEY,
          signal_type TEXT,
          entity_kind TEXT,
          entity_key TEXT,
          email_id INTEGER,
          score REAL,
          created_at TEXT
        );
        INSERT INTO emails (id, date_iso, sender, recipients, source_file) VALUES
          (1, '2024-06-01T00:00:00Z', 'good@lab.cl', '', '{CONTACTO_GMAIL_SOURCE_PREFIX}INBOX'),
          (2, '2024-06-02T00:00:00Z', 'mailer-daemon@google.com', '', '{CONTACTO_GMAIL_SOURCE_PREFIX}INBOX'),
          (3, '2024-06-03T00:00:00Z', 'legacy@x.cl', '', 'mbox:old/pst');
        INSERT INTO contact_master (email, domain, last_seen_at) VALUES
          ('good@lab.cl', 'lab.cl', '2024-06-01T00:00:00Z'),
          ('mailer-daemon@google.com', 'google.com', '2024-06-02T00:00:00Z'),
          ('legacy@x.cl', 'x.cl', '2024-01-01T00:00:00Z');
        INSERT INTO opportunity_signals VALUES
          (1, 'dormant_contact', 'contact', 'good@lab.cl', 1, 0.9, '2024-06-01'),
          (2, 'dormant_contact', 'contact', 'mailer-daemon@google.com', 2, 0.8, '2024-06-02'),
          (3, 'dormant_contact', 'contact', 'legacy@x.cl', 3, 0.7, '2024-01-01');
        """
    )
    conn.commit()
    conn.close()


def test_canonical_contact_count_excludes_noise_and_legacy(tmp_path: Path) -> None:
    db = tmp_path / "op.sqlite"
    _seed_db(db)
    conn = sqlite3.connect(str(db))
    try:
        assert count_archive_mart_table(conn, "contact_master") == 3
        assert count_canonical_operational_contacts(conn) == 1
        assert CONTACTO_GMAIL_SOURCE_PREFIX.replace("/", "") in canonical_emails_where()
    finally:
        conn.close()


def test_canonical_opportunity_signal_count_excludes_noise(tmp_path: Path) -> None:
    db = tmp_path / "today.sqlite"
    _seed_db(db)
    conn = sqlite3.connect(str(db))
    try:
        assert count_canonical_operational_opportunity_signals(conn) == 1
    finally:
        conn.close()
