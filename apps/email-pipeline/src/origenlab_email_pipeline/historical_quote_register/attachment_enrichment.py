"""Fills the doc-type/quote-terms gap for attachments the canonical
document_master/attachment_extracts pipeline has never processed (design §2:
zero document_master rows have sender_domain='origenlab.cl' today).

Read-only against the canonical DB; any new extraction result is persisted
only to the per-run sidecar (design §3.1) — never to attachment_extracts or
document_master.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import sqlite3
from typing import Literal

from origenlab_email_pipeline.attachment_extract import extract_bytes

EnrichmentSource = Literal[
    "canonical_document_master", "canonical_attachment_extracts", "recovered_extraction", "unavailable"
]


@dataclass(frozen=True)
class EnrichmentResult:
    attachment_id: int
    detected_doc_type: str | None
    has_quote_terms: bool | None
    text_preview: str | None
    source: EnrichmentSource


def _from_document_master(conn: sqlite3.Connection, attachment_id: int) -> EnrichmentResult | None:
    row = conn.execute(
        "SELECT doc_type, has_quote_terms, extracted_preview_clean "
        "FROM document_master WHERE attachment_id = ?",
        (attachment_id,),
    ).fetchone()
    if row is None:
        return None
    return EnrichmentResult(
        attachment_id=attachment_id,
        detected_doc_type=row[0],
        has_quote_terms=bool(row[1]) if row[1] is not None else None,
        text_preview=row[2],
        source="canonical_document_master",
    )


def _from_attachment_extracts(conn: sqlite3.Connection, attachment_id: int) -> EnrichmentResult | None:
    row = conn.execute(
        "SELECT detected_doc_type, has_quote_terms, text_preview "
        "FROM attachment_extracts WHERE attachment_id = ?",
        (attachment_id,),
    ).fetchone()
    if row is None:
        return None
    return EnrichmentResult(
        attachment_id=attachment_id,
        detected_doc_type=row[0],
        has_quote_terms=bool(row[1]) if row[1] is not None else None,
        text_preview=row[2],
        source="canonical_attachment_extracts",
    )


def enrich_attachment(
    canonical_conn: sqlite3.Connection,
    sidecar_conn: sqlite3.Connection,
    *,
    attachment_id: int,
    filename: str | None,
    content_type: str | None,
    recovered_bytes: bytes | None,
) -> EnrichmentResult:
    existing = _from_document_master(canonical_conn, attachment_id) or _from_attachment_extracts(
        canonical_conn, attachment_id
    )
    if existing is not None:
        return existing

    cached = sidecar_conn.execute(
        "SELECT detected_doc_type, has_quote_terms, text_preview "
        "FROM sidecar_attachment_extracts WHERE attachment_id = ?",
        (attachment_id,),
    ).fetchone()
    if cached is not None:
        return EnrichmentResult(
            attachment_id=attachment_id,
            detected_doc_type=cached[0],
            has_quote_terms=bool(cached[1]) if cached[1] is not None else None,
            text_preview=cached[2],
            source="recovered_extraction",
        )

    if recovered_bytes is None:
        return EnrichmentResult(
            attachment_id=attachment_id,
            detected_doc_type=None,
            has_quote_terms=None,
            text_preview=None,
            source="unavailable",
        )

    result = extract_bytes(recovered_bytes, content_type=content_type, filename=filename)
    sidecar_conn.execute(
        "INSERT OR REPLACE INTO sidecar_attachment_extracts "
        "(attachment_id, extract_status, extract_method, detected_doc_type, has_quote_terms, "
        " text_preview, char_count, source, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            attachment_id, result.status, result.method, result.detected_doc_type,
            int(bool(result.has_quote_terms)) if result.has_quote_terms is not None else None,
            result.text_preview, result.char_count, "recovered_extraction",
            datetime.now(timezone.utc).isoformat(),
        ),
    )
    sidecar_conn.commit()
    return EnrichmentResult(
        attachment_id=attachment_id,
        detected_doc_type=result.detected_doc_type,
        has_quote_terms=result.has_quote_terms,
        text_preview=result.text_preview,
        source="recovered_extraction",
    )
