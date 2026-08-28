"""Per-run sidecar SQLite — the only place attachment enrichment is ever
written for this feature. Never the canonical archive (design §3.1)."""

from __future__ import annotations

from pathlib import Path
import sqlite3

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS sidecar_attachment_extracts (
    attachment_id INTEGER PRIMARY KEY,
    extract_status TEXT NOT NULL,
    extract_method TEXT NOT NULL,
    detected_doc_type TEXT,
    has_quote_terms INTEGER,
    text_preview TEXT,
    char_count INTEGER,
    source TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""


def create_sidecar(run_dir: Path) -> sqlite3.Connection:
    run_dir.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(run_dir / "enrichment_sidecar.sqlite"))
    conn.executescript(_SCHEMA_SQL)
    conn.commit()
    return conn
