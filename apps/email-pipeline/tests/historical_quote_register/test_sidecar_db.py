# tests/historical_quote_register/test_sidecar_db.py
from __future__ import annotations

from origenlab_email_pipeline.historical_quote_register.sidecar_db import create_sidecar


def test_create_sidecar_creates_table_in_run_dir(tmp_path):
    conn = create_sidecar(tmp_path)
    conn.execute(
        "INSERT INTO sidecar_attachment_extracts "
        "(attachment_id, extract_status, extract_method, source, created_at) "
        "VALUES (1, 'success', 'pdf_text', 'recovered_extraction', '2026-08-28T00:00:00')"
    )
    conn.commit()
    row = conn.execute(
        "SELECT extract_status FROM sidecar_attachment_extracts WHERE attachment_id = 1"
    ).fetchone()
    assert row == ("success",)
    assert (tmp_path / "enrichment_sidecar.sqlite").exists()
