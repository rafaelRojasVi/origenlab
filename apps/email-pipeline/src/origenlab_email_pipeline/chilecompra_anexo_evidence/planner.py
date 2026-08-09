"""Assemble a tender's anexo evidence bundle and publish it atomically.

Every discovered row lands in the manifest with exactly one outcome, so a
downstream reader can always tell "no equipment found" apart from "we did not
finish reading this tender".
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from origenlab_email_pipeline.chilecompra_anexo_evidence.acquire import (
    AcquiredAttachment,
    AttachmentBudgetError,
    AttachmentSource,
    AttachmentStub,
    ContentAddressedCache,
)
from origenlab_email_pipeline.chilecompra_anexo_evidence.archive import (
    ArchiveLimits,
    ArchiveSafetyError,
    iter_archive_members,
    preflight_archive,
)
from origenlab_email_pipeline.chilecompra_anexo_evidence.constants import (
    ANEXO_EVIDENCE_BUILDER_VERSION,
    ANEXO_EVIDENCE_EXTRACTOR_VERSION,
    COMPLETE_OUTCOMES,
    FORMAT_ZIP,
    OUTCOME_DOWNLOAD_FAILED,
    OUTCOME_EXTRACTED_EMPTY,
    OUTCOME_EXTRACTION_SUCCESS,
    OUTCOME_PARTIAL_DUE_TO_SAFETY_LIMIT,
    REASON_ARCHIVE_DEPTH_LIMIT,
)
from origenlab_email_pipeline.chilecompra_anexo_evidence.detect import (
    detect_format,
    role_tag_for,
)
from origenlab_email_pipeline.chilecompra_anexo_evidence.extract import (
    ChunkDraft,
    ExtractionLimits,
    ExtractionOutput,
    extract_payload,
)
from origenlab_email_pipeline.chilecompra_anexo_evidence.fingerprint import (
    bundle_semantic_digest,
)
from origenlab_email_pipeline.chilecompra_anexo_evidence.models import (
    ArchiveMemberRecord,
    AttachmentExtraction,
    AttachmentRecord,
    EvidenceChunk,
    TenderAttachmentBundle,
    build_attachment_id,
    build_chunk_id,
    sha256_bytes,
    sha256_text,
)
from origenlab_email_pipeline.chilecompra_anexo_evidence.redaction import (
    assert_no_portal_tokens,
    redact_portal_tokens,
)
from origenlab_email_pipeline.chilecompra_anexo_evidence.walkthrough import (
    render_walkthrough,
)
from origenlab_email_pipeline.commercial_procurement_candidate_planner.output_safety import (
    write_atomically,
)


@dataclass(frozen=True)
class EvidenceBuildConfig:
    extraction_limits: ExtractionLimits = ExtractionLimits()
    archive_limits: ArchiveLimits = ArchiveLimits()
    cache: ContentAddressedCache | None = None


def _member_id(attachment_id: str, member_path: str) -> str:
    seed = f"{attachment_id}|{member_path}".encode("utf-8")
    return hashlib.sha256(seed).hexdigest()[:24]


def _chunks_from_output(
    output: ExtractionOutput,
    *,
    attachment_id: str,
    tender_id: str,
    source_format: str,
    container_attachment_id: str | None = None,
    member_path: str | None = None,
) -> list[EvidenceChunk]:
    chunks: list[EvidenceChunk] = []
    for ordinal, draft in enumerate(output.chunks, start=1):
        locator_key = json.dumps(draft.locator, sort_keys=True, separators=(",", ":"))
        chunks.append(
            EvidenceChunk(
                chunk_id=build_chunk_id(
                    attachment_id, member_path or "", locator_key, ordinal
                ),
                attachment_id=attachment_id,
                tender_id=tender_id,
                source_format=source_format,
                locator_type=draft.locator_type,
                locator=dict(draft.locator),
                text=draft.text,
                text_sha256=sha256_text(draft.text),
                char_count=len(draft.text),
                ordinal=ordinal,
                container_attachment_id=container_attachment_id,
                member_path=member_path,
                warnings=tuple(output.warnings),
            )
        )
    return chunks


def _expand_archive(
    payload: bytes,
    *,
    attachment_id: str,
    tender_id: str,
    prefix: str,
    depth: int,
    config: EvidenceBuildConfig,
) -> tuple[list[ArchiveMemberRecord], list[EvidenceChunk], list[str]]:
    """Recursively account for archive members without ever writing to disk."""
    members: list[ArchiveMemberRecord] = []
    chunks: list[EvidenceChunk] = []
    warnings: list[str] = []

    if depth > config.archive_limits.max_depth:
        warnings.append(REASON_ARCHIVE_DEPTH_LIMIT)
        return members, chunks, warnings

    try:
        preflight = preflight_archive(payload, limits=config.archive_limits)
    except ArchiveSafetyError as exc:
        warnings.append(f"archive_unreadable:{exc}"[:120])
        return members, chunks, warnings

    warnings.extend(preflight.warnings)
    if preflight.rejected:
        warnings.extend(preflight.reason_codes)
        return members, chunks, warnings

    try:
        payloads = iter_archive_members(payload, limits=config.archive_limits)
    except ArchiveSafetyError as exc:
        warnings.append(f"archive_unreadable:{exc}"[:120])
        return members, chunks, warnings

    for member in payloads:
        member_path = f"{prefix}{member.member_path}" if prefix else member.member_path
        member_ident = _member_id(attachment_id, member_path)
        member_warnings = list(member.warnings)
        if member.encrypted:
            members.append(
                ArchiveMemberRecord(
                    member_id=member_ident,
                    container_attachment_id=attachment_id,
                    tender_id=tender_id,
                    member_path=member_path,
                    member_sha256="",
                    byte_count=0,
                    depth=depth,
                    detected_format="unknown",
                    outcome="encrypted",
                    warnings=tuple(member_warnings),
                )
            )
            continue
        if not member.payload:
            members.append(
                ArchiveMemberRecord(
                    member_id=member_ident,
                    container_attachment_id=attachment_id,
                    tender_id=tender_id,
                    member_path=member_path,
                    member_sha256="",
                    byte_count=0,
                    depth=depth,
                    detected_format="unknown",
                    outcome=OUTCOME_PARTIAL_DUE_TO_SAFETY_LIMIT,
                    warnings=tuple(member_warnings or ["member_not_read"]),
                )
            )
            continue

        detection = detect_format(member.payload, filename=member.member_path)
        member_warnings.extend(detection.warnings)
        member_sha = sha256_bytes(member.payload)

        if detection.detected_format == FORMAT_ZIP:
            nested_members, nested_chunks, nested_warnings = _expand_archive(
                member.payload,
                attachment_id=attachment_id,
                tender_id=tender_id,
                prefix=f"{member_path}::",
                depth=depth + 1,
                config=config,
            )
            member_warnings.extend(nested_warnings)
            nested_complete = all(
                m.outcome in COMPLETE_OUTCOMES for m in nested_members
            )
            outcome = (
                OUTCOME_EXTRACTION_SUCCESS
                if nested_members and nested_complete and not nested_warnings
                else OUTCOME_PARTIAL_DUE_TO_SAFETY_LIMIT
            )
            if not nested_members and not nested_warnings:
                outcome = OUTCOME_EXTRACTED_EMPTY
            members.append(
                ArchiveMemberRecord(
                    member_id=member_ident,
                    container_attachment_id=attachment_id,
                    tender_id=tender_id,
                    member_path=member_path,
                    member_sha256=member_sha,
                    byte_count=len(member.payload),
                    depth=depth,
                    detected_format=FORMAT_ZIP,
                    outcome=outcome,
                    warnings=tuple(member_warnings),
                )
            )
            members.extend(nested_members)
            chunks.extend(nested_chunks)
            continue

        output = extract_payload(
            member.payload,
            detected_format=detection.detected_format,
            limits=config.extraction_limits,
        )
        member_warnings.extend(output.warnings)
        if member.truncated and output.outcome in COMPLETE_OUTCOMES:
            output.outcome = OUTCOME_PARTIAL_DUE_TO_SAFETY_LIMIT
        chunks.extend(
            _chunks_from_output(
                output,
                attachment_id=member_ident,
                tender_id=tender_id,
                source_format=detection.detected_format,
                container_attachment_id=attachment_id,
                member_path=member_path,
            )
        )
        members.append(
            ArchiveMemberRecord(
                member_id=member_ident,
                container_attachment_id=attachment_id,
                tender_id=tender_id,
                member_path=member_path,
                member_sha256=member_sha,
                byte_count=len(member.payload),
                depth=depth,
                detected_format=detection.detected_format,
                outcome=output.outcome,
                warnings=tuple(member_warnings),
            )
        )
    return members, chunks, warnings


def _record_for_failed_download(
    stub: AttachmentStub,
    *,
    tender_id: str,
    reason: str,
) -> AttachmentRecord:
    return AttachmentRecord(
        attachment_id=build_attachment_id(
            tender_id, stub.listing_ordinal, stub.row_ordinal
        ),
        tender_id=tender_id,
        listing_ordinal=stub.listing_ordinal,
        row_ordinal=stub.row_ordinal,
        portal_filename=stub.portal_filename,
        safe_filename=stub.safe_filename,
        tipo=stub.tipo,
        descripcion=stub.descripcion,
        fecha_adjunto=stub.fecha_adjunto,
        content_type="",
        detected_format="unknown",
        extension="",
        byte_count=0,
        sha256="",
        download_status="not_downloaded",
        outcome=OUTCOME_DOWNLOAD_FAILED,
        role_tag=role_tag_for(stub.portal_filename, stub.tipo, stub.descripcion),
        warnings=(reason,),
        error_message=redact_portal_tokens(reason)[:300],
    )


def build_tender_bundle(
    tender_id: str,
    source: AttachmentSource,
    *,
    config: EvidenceBuildConfig | None = None,
) -> TenderAttachmentBundle:
    """Inventory first, then stream one body at a time into records and chunks."""
    active = config or EvidenceBuildConfig()
    inventory = source.inventory()
    stubs = list(inventory.stubs)
    records: list[AttachmentRecord] = []
    chunks: list[EvidenceChunk] = []
    incomplete: list[str] = []
    bytes_downloaded = 0
    downloaded = 0

    by_content: dict[str, tuple[str, AttachmentExtraction, str]] = {}
    names_by_content: dict[str, str] = {}
    processed_rows: set[tuple[int, int]] = set()

    def _finish_remaining(reason: str) -> None:
        for stub in stubs:
            if (stub.listing_ordinal, stub.row_ordinal) in processed_rows:
                continue
            records.append(
                _record_for_failed_download(stub, tender_id=tender_id, reason=reason)
            )
            processed_rows.add((stub.listing_ordinal, stub.row_ordinal))

    stream: Iterable[AcquiredAttachment]
    try:
        stream = source.iter_payloads(inventory)
        for acquired in stream:
            stub = acquired.stub
            processed_rows.add((stub.listing_ordinal, stub.row_ordinal))
            attachment_id = build_attachment_id(
                tender_id, stub.listing_ordinal, stub.row_ordinal
            )
            role = role_tag_for(stub.portal_filename, stub.tipo, stub.descripcion)

            if acquired.error is not None:
                records.append(
                    _record_for_failed_download(
                        stub, tender_id=tender_id, reason=acquired.error
                    )
                )
                continue

            payload = acquired.payload
            downloaded += 1
            bytes_downloaded += len(payload)
            digest = sha256_bytes(payload)
            if active.cache is not None:
                active.cache.put(payload)

            detection = detect_format(
                payload,
                content_type=acquired.content_type,
                filename=stub.portal_filename,
            )
            warnings = list(detection.warnings)

            previous = by_content.get(digest)
            if previous is not None:
                previous_id, previous_extraction, previous_name = previous
                duplicate_kind = (
                    "same_name_same_bytes"
                    if previous_name == stub.safe_filename
                    else "different_name_same_bytes"
                )
                reused = AttachmentExtraction(
                    outcome=previous_extraction.outcome,
                    detected_format=previous_extraction.detected_format,
                    chunk_count=0,
                    char_count=0,
                    warnings=tuple(
                        [*previous_extraction.warnings, f"extraction_reused_from:{previous_id}"]
                    ),
                    page_count=previous_extraction.page_count,
                    sheet_count=previous_extraction.sheet_count,
                    row_count=previous_extraction.row_count,
                    extractor=previous_extraction.extractor,
                )
                records.append(
                    AttachmentRecord(
                        attachment_id=attachment_id,
                        tender_id=tender_id,
                        listing_ordinal=stub.listing_ordinal,
                        row_ordinal=stub.row_ordinal,
                        portal_filename=stub.portal_filename,
                        safe_filename=stub.safe_filename,
                        tipo=stub.tipo,
                        descripcion=stub.descripcion,
                        fecha_adjunto=stub.fecha_adjunto,
                        content_type=acquired.content_type,
                        detected_format=detection.detected_format,
                        extension=detection.extension,
                        byte_count=len(payload),
                        sha256=digest,
                        download_status="downloaded",
                        outcome=previous_extraction.outcome,
                        role_tag=role,
                        warnings=tuple(warnings),
                        duplicate_of_attachment_id=previous_id,
                        duplicate_kind=duplicate_kind,
                        extraction=reused,
                    )
                )
                continue

            existing_name_digest = names_by_content.get(stub.safe_filename)
            if existing_name_digest is not None and existing_name_digest != digest:
                warnings.append("same_name_different_content")
            names_by_content.setdefault(stub.safe_filename, digest)

            members: tuple[ArchiveMemberRecord, ...] = ()
            if detection.detected_format == FORMAT_ZIP:
                member_records, member_chunks, archive_warnings = _expand_archive(
                    payload,
                    attachment_id=attachment_id,
                    tender_id=tender_id,
                    prefix="",
                    depth=1,
                    config=active,
                )
                warnings.extend(archive_warnings)
                members = tuple(member_records)
                chunks.extend(member_chunks)
                all_complete = bool(member_records) and all(
                    m.outcome in COMPLETE_OUTCOMES for m in member_records
                )
                if not member_records:
                    outcome = (
                        OUTCOME_PARTIAL_DUE_TO_SAFETY_LIMIT
                        if archive_warnings
                        else OUTCOME_EXTRACTED_EMPTY
                    )
                elif all_complete and not archive_warnings:
                    outcome = (
                        OUTCOME_EXTRACTION_SUCCESS
                        if member_chunks
                        else OUTCOME_EXTRACTED_EMPTY
                    )
                else:
                    outcome = OUTCOME_PARTIAL_DUE_TO_SAFETY_LIMIT
                extraction = AttachmentExtraction(
                    outcome=outcome,
                    detected_format=FORMAT_ZIP,
                    chunk_count=len(member_chunks),
                    char_count=sum(c.char_count for c in member_chunks),
                    warnings=tuple(archive_warnings),
                    extractor="archive_expand",
                )
            else:
                output = extract_payload(
                    payload,
                    detected_format=detection.detected_format,
                    limits=active.extraction_limits,
                )
                warnings.extend(output.warnings)
                new_chunks = _chunks_from_output(
                    output,
                    attachment_id=attachment_id,
                    tender_id=tender_id,
                    source_format=detection.detected_format,
                )
                chunks.extend(new_chunks)
                outcome = output.outcome
                extraction = AttachmentExtraction(
                    outcome=outcome,
                    detected_format=detection.detected_format,
                    chunk_count=len(new_chunks),
                    char_count=output.char_count,
                    warnings=tuple(output.warnings),
                    page_count=output.page_count,
                    sheet_count=output.sheet_count,
                    row_count=output.row_count,
                    extractor=output.extractor,
                    error_message=redact_portal_tokens(output.error_message or "")[:300]
                    or None,
                )

            by_content[digest] = (attachment_id, extraction, stub.safe_filename)
            records.append(
                AttachmentRecord(
                    attachment_id=attachment_id,
                    tender_id=tender_id,
                    listing_ordinal=stub.listing_ordinal,
                    row_ordinal=stub.row_ordinal,
                    portal_filename=stub.portal_filename,
                    safe_filename=stub.safe_filename,
                    tipo=stub.tipo,
                    descripcion=stub.descripcion,
                    fecha_adjunto=stub.fecha_adjunto,
                    content_type=acquired.content_type,
                    detected_format=detection.detected_format,
                    extension=detection.extension,
                    byte_count=len(payload),
                    sha256=digest,
                    download_status="downloaded",
                    outcome=outcome,
                    role_tag=role,
                    warnings=tuple(warnings),
                    extraction=extraction,
                    archive_members=members,
                    error_message=extraction.error_message,
                )
            )
    except AttachmentBudgetError as exc:
        incomplete.append(exc.reason_code)
        _finish_remaining(f"{exc.reason_code}: {exc}"[:300])

    # Any row the source never yielded still needs an explicit outcome.
    _finish_remaining("attachment_row_not_yielded_by_source")

    records.sort(key=lambda r: (r.listing_ordinal, r.row_ordinal))
    chunks.sort(key=lambda c: (c.attachment_id, c.member_path or "", c.ordinal))

    outcome_counts: dict[str, int] = {}
    for record in records:
        outcome_counts[record.outcome] = outcome_counts.get(record.outcome, 0) + 1
        if record.outcome not in COMPLETE_OUTCOMES:
            incomplete.append(record.outcome)
        for member in record.archive_members:
            if member.outcome not in COMPLETE_OUTCOMES:
                incomplete.append(f"archive_member_{member.outcome}")

    digest = bundle_semantic_digest(
        tender_id=tender_id,
        attachments=records,
        chunks=chunks,
        incomplete_reason_codes=incomplete,
    )

    return TenderAttachmentBundle(
        tender_id=tender_id,
        source_kind=getattr(source, "source_kind", "unknown"),
        listing_page_count=inventory.listing_page_count,
        attachments_discovered=len(stubs),
        attachments_downloaded=downloaded,
        attachments=tuple(records),
        chunks=tuple(chunks),
        bytes_downloaded=bytes_downloaded,
        incomplete_reason_codes=tuple(sorted(set(incomplete))),
        semantic_digest=digest,
        outcome_counts=outcome_counts,
    )


def _dump_json(path: Path, payload: Any) -> str:
    text = json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=True) + "\n"
    assert_no_portal_tokens(text, where=path.name)
    path.write_text(text, encoding="utf-8")
    return str(path)


def build_summary(bundles: Iterable[TenderAttachmentBundle]) -> dict[str, Any]:
    items = list(bundles)
    totals: dict[str, int] = {}
    for bundle in items:
        for outcome, count in bundle.outcome_counts.items():
            totals[outcome] = totals.get(outcome, 0) + count
    return {
        "builder_version": ANEXO_EVIDENCE_BUILDER_VERSION,
        "extractor_version": ANEXO_EVIDENCE_EXTRACTOR_VERSION,
        "tenders": len(items),
        "attachments_discovered": sum(b.attachments_discovered for b in items),
        "attachments_downloaded": sum(b.attachments_downloaded for b in items),
        "archive_children": sum(
            len(a.archive_members) for b in items for a in b.attachments
        ),
        "chunk_count": sum(len(b.chunks) for b in items),
        "bytes_downloaded": sum(b.bytes_downloaded for b in items),
        "duplicate_content_groups": sum(
            1
            for b in items
            for a in b.attachments
            if a.duplicate_of_attachment_id is not None
        ),
        "outcome_totals": dict(sorted(totals.items())),
        "bundles_complete": sum(1 for b in items if b.bundle_complete),
        "bundles_incomplete": sum(1 for b in items if not b.bundle_complete),
        "tender_summaries": [b.to_summary_dict() for b in items],
    }


def write_evidence_outputs(
    bundles: Iterable[TenderAttachmentBundle],
    out_dir: Path,
    *,
    repo_email_pipeline_root: Path,
    require_git_ignored: bool = True,
    include_chunk_text: bool = True,
) -> dict[str, str]:
    """Publish the review bundle as one atomic, gitignored directory."""
    items = list(bundles)

    def _write(staged: Path) -> dict[str, str]:
        written: dict[str, str] = {}
        written["summary.json"] = _dump_json(staged / "summary.json", build_summary(items))
        written["attachment_manifest.json"] = _dump_json(
            staged / "attachment_manifest.json",
            {
                "tenders": [
                    {
                        "tender_id": b.tender_id,
                        "attachments": [a.to_dict() for a in b.attachments],
                    }
                    for b in items
                ]
            },
        )
        written["extraction_manifest.json"] = _dump_json(
            staged / "extraction_manifest.json",
            {
                "tenders": [
                    {
                        "tender_id": b.tender_id,
                        "semantic_digest": b.semantic_digest,
                        "bundle_complete": b.bundle_complete,
                        "download_complete": b.download_complete,
                        "extraction_complete": b.extraction_complete,
                        "incomplete_reason_codes": list(b.incomplete_reason_codes),
                        "extractions": [
                            {
                                "attachment_id": a.attachment_id,
                                "safe_filename": a.safe_filename,
                                "outcome": a.outcome,
                                "extraction": a.extraction.to_dict()
                                if a.extraction
                                else None,
                                "archive_members": [
                                    m.to_dict() for m in a.archive_members
                                ],
                            }
                            for a in b.attachments
                        ],
                    }
                    for b in items
                ]
            },
        )

        chunk_path = staged / "evidence_chunks.jsonl"
        lines: list[str] = []
        for bundle in items:
            for chunk in bundle.chunks:
                payload = chunk.to_dict()
                if not include_chunk_text:
                    payload.pop("text", None)
                else:
                    payload["text"] = redact_portal_tokens(payload["text"])
                lines.append(json.dumps(payload, sort_keys=True, ensure_ascii=True))
        chunk_text = "\n".join(lines) + ("\n" if lines else "")
        assert_no_portal_tokens(chunk_text, where="evidence_chunks.jsonl")
        chunk_path.write_text(chunk_text, encoding="utf-8")
        written["evidence_chunks.jsonl"] = str(chunk_path)

        walkthrough = render_walkthrough(items)
        assert_no_portal_tokens(walkthrough, where="walkthrough.md")
        (staged / "walkthrough.md").write_text(walkthrough, encoding="utf-8")
        written["walkthrough.md"] = str(staged / "walkthrough.md")
        return written

    return write_atomically(
        out_dir,
        repo_email_pipeline_root=repo_email_pipeline_root,
        writer=_write,
        require_git_ignored=require_git_ignored,
    )


__all__ = [
    "ChunkDraft",
    "EvidenceBuildConfig",
    "build_summary",
    "build_tender_bundle",
    "write_evidence_outputs",
]
