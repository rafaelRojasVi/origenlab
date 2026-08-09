"""Split #450 evidence chunks into addressable recognition units.

#450 emits chunks at document-structure grain: a whole DOCX table, a whole PDF
page, a block of spreadsheet rows. Feeding those to the recognizer directly
lets a single page assert five unrelated equipment categories at once, because
every product on the page shares one text blob. That destroys line boundaries
and makes provenance useless for review.

Segmentation restores the boundary the source document already had - a table
row, a sheet row, a visually separated PDF block - and keeps a locator for each
one. Nothing is discarded: every chunk yields at least one segment.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable, Sequence
from typing import Any

from .constants import (
    MAX_PAGE_BLOCK_CHARS,
    MIN_SEGMENT_CHARS,
    SEGMENT_EVIDENCE_TIERS,
    SEGMENT_PAGE_BLOCK,
    SEGMENT_SHEET_ROW,
    SEGMENT_TABLE_ROW,
    SEGMENT_WHOLE_CHUNK,
)
from .models import AnexoEvidenceSegment


_ROW_RE = re.compile(r"(?m)^\[r(\d+)\][ ]?(.*)$")
_BLANK_BLOCK_RE = re.compile(r"\n\s*\n")


def _segment_id(chunk_id: str, ordinal: int) -> str:
    payload = f"anexo_segment\0{chunk_id}\0{ordinal}".encode()
    return hashlib.sha256(payload).hexdigest()[:32]


def _row_segments(text: str) -> list[tuple[dict[str, Any], str]]:
    """Split `[rN] cells...` rendering used by DOCX tables and XLSX row blocks."""
    out: list[tuple[dict[str, Any], str]] = []
    for raw_row, body in _ROW_RE.findall(text):
        out.append(({"row": int(raw_row)}, body.strip()))
    return out


def _page_block_segments(text: str) -> list[tuple[dict[str, Any], str]]:
    """Split a PDF page on blank-line boundaries, then split oversized blocks.

    PDF extraction reflows a table cell into several short lines and separates
    logical items with a blank line, so blank-line blocks track item boundaries
    far better than raw lines do. Continuous prose has no blank lines at all,
    though, and would otherwise collapse into a single page-sized segment whose
    locator tells a reviewer nothing; those are split again on line boundaries.
    """
    out: list[tuple[dict[str, Any], str]] = []
    for index, block in enumerate(_BLANK_BLOCK_RE.split(text), start=1):
        cleaned = " ".join(block.split())
        if len(cleaned) <= MAX_PAGE_BLOCK_CHARS:
            out.append(({"block": index}, cleaned))
            continue
        for line_index, line in enumerate(block.splitlines(), start=1):
            collapsed = " ".join(line.split())
            if collapsed:
                out.append(({"block": index, "line": line_index}, collapsed))
    return out


def segment_chunk(
    chunk: dict[str, Any],
    *,
    attachment_index: dict[str, dict[str, Any]],
) -> tuple[AnexoEvidenceSegment, ...]:
    """Split one evidence chunk into addressable segments.

    `attachment_index` maps attachment_id to its manifest row, supplying the
    filename, digest, and role tag every segment must carry.
    """
    locator_type = str(chunk.get("locator_type") or "")
    text = str(chunk.get("text") or "")

    if locator_type == "docx_table":
        kind = SEGMENT_TABLE_ROW
        parts = _row_segments(text)
    elif locator_type == "xlsx_rows":
        kind = SEGMENT_SHEET_ROW
        parts = _row_segments(text)
    elif locator_type == "pdf_page":
        kind = SEGMENT_PAGE_BLOCK
        parts = _page_block_segments(text)
    else:
        kind = SEGMENT_WHOLE_CHUNK
        parts = [({}, text.strip())]

    # A structured chunk whose markers did not parse must still be searched;
    # falling back to the whole chunk keeps evidence from silently vanishing.
    if not parts:
        kind = SEGMENT_WHOLE_CHUNK
        parts = [({}, text.strip())]

    attachment_id = str(chunk.get("attachment_id") or "")
    record = attachment_index.get(attachment_id, {})
    tier = SEGMENT_EVIDENCE_TIERS[kind]

    segments: list[AnexoEvidenceSegment] = []
    ordinal = 0
    for segment_locator, body in parts:
        if len(body) < MIN_SEGMENT_CHARS:
            continue
        ordinal += 1
        segments.append(
            AnexoEvidenceSegment(
                segment_id=_segment_id(str(chunk.get("chunk_id") or ""), ordinal),
                tender_id=str(chunk.get("tender_id") or ""),
                chunk_id=str(chunk.get("chunk_id") or ""),
                attachment_id=attachment_id,
                evidence_attachment_id=str(
                    record.get("evidence_attachment_id") or attachment_id
                ),
                safe_filename=str(record.get("safe_filename") or attachment_id),
                attachment_sha256=str(record.get("sha256") or ""),
                detected_format=str(
                    chunk.get("source_format") or record.get("detected_format") or ""
                ),
                document_role=str(record.get("role_tag") or "unknown"),
                container_attachment_id=chunk.get("container_attachment_id"),
                member_path=chunk.get("member_path"),
                locator_type=locator_type,
                locator=dict(chunk.get("locator") or {}),
                segment_kind=kind,
                segment_locator=dict(segment_locator),
                segment_ordinal=ordinal,
                text_raw=body,
                evidence_tier=tier,
            )
        )
    return tuple(segments)


def segment_chunks(
    chunks: Iterable[dict[str, Any]],
    *,
    attachments: Sequence[dict[str, Any]],
) -> tuple[AnexoEvidenceSegment, ...]:
    """Segment every chunk, in a deterministic order independent of input order."""
    index = {str(a.get("attachment_id")): a for a in attachments}
    ordered = sorted(
        chunks,
        key=lambda c: (
            str(c.get("tender_id") or ""),
            str(c.get("attachment_id") or ""),
            int(c.get("ordinal") or 0),
            str(c.get("chunk_id") or ""),
        ),
    )
    out: list[AnexoEvidenceSegment] = []
    for chunk in ordered:
        out.extend(segment_chunk(chunk, attachment_index=index))
    return tuple(out)
