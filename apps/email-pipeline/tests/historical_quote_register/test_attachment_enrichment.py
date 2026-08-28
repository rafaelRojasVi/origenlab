# tests/historical_quote_register/test_attachment_enrichment.py
from __future__ import annotations

import sys
from pathlib import Path

_TDIR = Path(__file__).resolve().parent.parent
if str(_TDIR) not in sys.path:
    sys.path.insert(0, str(_TDIR))

from origenlab_email_pipeline.db import insert_email
from origenlab_email_pipeline.historical_quote_register.attachment_enrichment import (
    enrich_attachment,
)
from origenlab_email_pipeline.historical_quote_register.db_readonly import open_readonly
from origenlab_email_pipeline.historical_quote_register.sidecar_db import create_sidecar
from historical_quote_register.sqlite_fixture import build_fixture_db, open_writable_fixture


def test_prefers_existing_canonical_attachment_extracts(tmp_path):
    db_path = build_fixture_db(tmp_path)
    conn = open_writable_fixture(db_path)
    insert_email(
        conn, source_file="gmail:contacto@origenlab.cl/[Gmail]/Enviados",
        folder="[Gmail]/Enviados", message_id="<sent-1@origenlab.cl>",
        subject="Cotización equipo X", sender="contacto@origenlab.cl",
        recipients="compras@cliente.cl", date_raw="2026-05-10", date_iso="2026-05-10T10:00:00",
        body="",
    )
    conn.execute(
        "INSERT INTO attachments (id, email_id, part_index, filename) VALUES (1, 1, 0, 'cot.pdf')"
    )
    conn.execute(
        "INSERT INTO attachment_extracts (attachment_id, extract_status, extract_method, "
        "detected_doc_type, has_quote_terms, text_preview, char_count) "
        "VALUES (1, 'success', 'pdf_text', 'quote', 1, 'cotizacion equipo', 17)"
    )
    conn.commit()
    conn.close()

    sidecar = create_sidecar(tmp_path / "run")
    result = enrich_attachment(
        open_readonly(db_path), sidecar,
        attachment_id=1, filename="cot.pdf", content_type="application/pdf",
        recovered_bytes=None,
    )
    assert result.source == "canonical_attachment_extracts"
    assert result.detected_doc_type == "quote"
    assert result.has_quote_terms is True


def test_prefers_document_master_over_attachment_extracts(tmp_path):
    db_path = build_fixture_db(tmp_path)
    conn = open_writable_fixture(db_path)
    insert_email(
        conn, source_file="gmail:contacto@origenlab.cl/[Gmail]/Enviados",
        folder="[Gmail]/Enviados", message_id="<sent-4@origenlab.cl>",
        subject="Cotización equipo W", sender="contacto@origenlab.cl",
        recipients="compras@cliente.cl", date_raw="2026-05-10", date_iso="2026-05-10T10:00:00",
        body="",
    )
    conn.execute(
        "INSERT INTO attachments (id, email_id, part_index, filename) VALUES (4, 1, 0, 'cot.pdf')"
    )
    # Deliberately conflicting attachment_extracts row (doc_type='invoice') to prove
    # document_master genuinely wins priority, not merely that it's the only row present.
    conn.execute(
        "INSERT INTO attachment_extracts (attachment_id, extract_status, extract_method, "
        "detected_doc_type, has_quote_terms, text_preview, char_count) "
        "VALUES (4, 'success', 'pdf_text', 'invoice', 0, 'factura antigua', 15)"
    )
    conn.execute(
        "INSERT INTO document_master (attachment_id, email_id, filename, extension, sender_email, "
        "sender_domain, recipient_domain, sent_at, doc_type, extracted_preview_raw, "
        "extracted_preview_clean, preview_quality_score, has_quote_terms, has_invoice_terms, "
        "has_purchase_terms, has_price_list_terms, equipment_tags) VALUES "
        "(4, 1, 'cot.pdf', 'pdf', 'contacto@origenlab.cl', 'origenlab.cl', 'cliente.cl', "
        "'2026-05-10T10:00:00', 'quote', 'Cotización equipo W', 'cotizacion equipo w', 0.9, "
        "1, 0, 0, 0, NULL)"
    )
    conn.commit()
    conn.close()

    sidecar = create_sidecar(tmp_path / "run")
    result = enrich_attachment(
        open_readonly(db_path), sidecar,
        attachment_id=4, filename="cot.pdf", content_type="application/pdf",
        recovered_bytes=None,
    )
    assert result.source == "canonical_document_master"
    assert result.detected_doc_type == "quote"
    assert result.has_quote_terms is True
    assert result.text_preview == "cotizacion equipo w"


def test_falls_back_to_recovered_bytes_extraction_when_missing(tmp_path):
    db_path = build_fixture_db(tmp_path)
    conn = open_writable_fixture(db_path)
    insert_email(
        conn, source_file="gmail:contacto@origenlab.cl/[Gmail]/Enviados",
        folder="[Gmail]/Enviados", message_id="<sent-2@origenlab.cl>",
        subject="Cotización equipo Y", sender="contacto@origenlab.cl",
        recipients="compras@cliente.cl", date_raw="2026-05-10", date_iso="2026-05-10T10:00:00",
        body="",
    )
    conn.execute(
        "INSERT INTO attachments (id, email_id, part_index, filename) VALUES (2, 1, 0, 'cot.pdf')"
    )
    conn.commit()
    conn.close()

    sidecar = create_sidecar(tmp_path / "run")
    pdf_bytes = b"%PDF-1.4\ncotizacion presupuesto\n%%EOF"
    result = enrich_attachment(
        open_readonly(db_path), sidecar,
        attachment_id=2, filename="cot.pdf", content_type="application/pdf",
        recovered_bytes=pdf_bytes,
    )
    assert result.source == "recovered_extraction"

    persisted = sidecar.execute(
        "SELECT source FROM sidecar_attachment_extracts WHERE attachment_id = 2"
    ).fetchone()
    assert persisted == ("recovered_extraction",)


def test_unavailable_when_no_canonical_row_and_no_bytes(tmp_path):
    db_path = build_fixture_db(tmp_path)
    conn = open_writable_fixture(db_path)
    insert_email(
        conn, source_file="gmail:contacto@origenlab.cl/[Gmail]/Enviados",
        folder="[Gmail]/Enviados", message_id="<sent-3@origenlab.cl>",
        subject="Cotización equipo Z", sender="contacto@origenlab.cl",
        recipients="compras@cliente.cl", date_raw="2026-05-10", date_iso="2026-05-10T10:00:00",
        body="",
    )
    conn.execute(
        "INSERT INTO attachments (id, email_id, part_index, filename) VALUES (3, 1, 0, 'cot.pdf')"
    )
    conn.commit()
    conn.close()

    sidecar = create_sidecar(tmp_path / "run")
    result = enrich_attachment(
        open_readonly(db_path), sidecar,
        attachment_id=3, filename="cot.pdf", content_type="application/pdf",
        recovered_bytes=None,
    )
    assert result.source == "unavailable"
