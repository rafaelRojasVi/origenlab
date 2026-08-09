"""Human-readable rendering of an anexo evidence bundle (no portal tokens)."""

from __future__ import annotations

from typing import Iterable

from origenlab_email_pipeline.chilecompra_anexo_evidence.models import (
    TenderAttachmentBundle,
)


def _status_icon(complete: bool) -> str:
    return "complete" if complete else "INCOMPLETE"


def render_walkthrough(bundles: Iterable[TenderAttachmentBundle]) -> str:
    """Tender → listing pages → attachments → archive children → completeness."""
    items = list(bundles)
    lines: list[str] = [
        "# ChileCompra anexo evidence walkthrough",
        "",
        "Read-only extraction review. No classification, persistence, or outreach.",
        "",
        f"Tenders: {len(items)}",
        "",
    ]

    for bundle in items:
        lines.append(f"## Tender {bundle.tender_id}")
        lines.append("")
        lines.append(f"- source: {bundle.source_kind}")
        lines.append(f"- listing pages: {bundle.listing_page_count}")
        lines.append(f"- attachments discovered: {bundle.attachments_discovered}")
        lines.append(f"- attachments downloaded: {bundle.attachments_downloaded}")
        lines.append(f"- bytes downloaded: {bundle.bytes_downloaded}")
        lines.append(f"- chunks: {len(bundle.chunks)}")
        lines.append(f"- download: {_status_icon(bundle.download_complete)}")
        lines.append(f"- extraction: {_status_icon(bundle.extraction_complete)}")
        lines.append(f"- bundle: {_status_icon(bundle.bundle_complete)}")
        if bundle.incomplete_reason_codes:
            lines.append(
                "- incomplete reasons: " + ", ".join(bundle.incomplete_reason_codes)
            )
        lines.append(f"- semantic digest: {bundle.semantic_digest}")
        lines.append("")

        if not bundle.attachments:
            lines.append("_No attachments published for this tender._")
            lines.append("")
            continue

        lines.append("### Attachments")
        lines.append("")
        for record in bundle.attachments:
            chunk_count = (
                record.extraction.chunk_count if record.extraction else 0
            )
            lines.append(
                f"- [{record.listing_ordinal}.{record.row_ordinal}] "
                f"`{record.safe_filename}` — {record.detected_format} — "
                f"{record.outcome} — chunks: {chunk_count} — role: {record.role_tag}"
            )
            if record.duplicate_of_attachment_id:
                lines.append(
                    f"    - duplicate ({record.duplicate_kind}) of "
                    f"{record.duplicate_of_attachment_id}"
                )
            for warning in record.warnings:
                lines.append(f"    - warning: {warning}")
            for member in record.archive_members:
                lines.append(
                    f"    - member `{member.member_path}` (depth {member.depth}) — "
                    f"{member.detected_format} — {member.outcome}"
                )
        lines.append("")

    lines.append("## Completeness contract")
    lines.append("")
    lines.append(
        "`extraction_complete=true` requires every discovered attachment and archive "
        "member to end in `extraction_success` or `extracted_empty`. Anything else "
        "means positive findings may still exist but the corpus was not fully read."
    )
    lines.append("")
    return "\n".join(lines)
