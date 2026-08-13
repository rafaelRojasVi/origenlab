"""Strict, fail-closed loader for an existing local E1 evidence bundle.

The E2 research loader is deliberately backward-compatible and permissive. A
production planning boundary cannot inherit that tolerance: every required
artifact, identity join, count, extraction outcome, and E1 semantic digest is
verified here before any annex text is exposed to PR5D.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from types import MappingProxyType
from typing import Any, NoReturn

from origenlab_email_pipeline.chilecompra_anexo_evidence.constants import (
    ALL_OUTCOMES,
    ANEXO_EVIDENCE_BUILDER_VERSION,
    ANEXO_EVIDENCE_EXTRACTOR_VERSION,
    COMPLETE_OUTCOMES,
    FORMAT_CSV,
    FORMAT_DOC,
    FORMAT_DOCX,
    FORMAT_IMAGE,
    FORMAT_LEGACY_OLE,
    FORMAT_PDF,
    FORMAT_PPTX,
    FORMAT_RAR,
    FORMAT_SEVENZIP,
    FORMAT_TXT,
    FORMAT_UNKNOWN,
    FORMAT_XLS,
    FORMAT_XLSX,
    FORMAT_XML,
    FORMAT_ZIP,
    OUTCOME_DOWNLOAD_FAILED,
    OUTCOME_CORRUPT,
    OUTCOME_ENCRYPTED,
    OUTCOME_EXTRACTED_EMPTY,
    OUTCOME_EXTRACTION_FAILED,
    OUTCOME_EXTRACTION_SUCCESS,
    OUTCOME_NEEDS_CONVERTER,
    OUTCOME_NEEDS_OCR,
    OUTCOME_PARTIAL_DUE_TO_SAFETY_LIMIT,
    OUTCOME_UNSUPPORTED_FORMAT,
    REASON_ATTACHMENT_COUNT_BUDGET_EXCEEDED,
    REASON_TOTAL_BYTES_BUDGET_EXCEEDED,
    ROLE_ADMINISTRATIVE_BASIS,
    ROLE_ECONOMIC_FORM,
    ROLE_TECHNICAL_SPEC,
    ROLE_TEMPLATE,
    ROLE_UNKNOWN,
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
    sha256_text,
)
from origenlab_email_pipeline.chilecompra_anexo_evidence.redaction import (
    ArtifactRedactionError,
    assert_no_portal_tokens,
)
from origenlab_email_pipeline.commercial_procurement_acquisition.canonical_json import (
    canonical_json_digest,
)
from origenlab_email_pipeline.commercial_procurement_anexo_tender_terms import (
    resolve_evidence_chunks,
)

from .constants import (
    ANEXO_INTEGRATION_CONTRACT_VERSION,
    REQUIRED_EVIDENCE_FILENAMES,
    VERIFIED_ANEXO_BUNDLE_DIGEST_ALGORITHM,
)
from .models import VerifiedAnexoEvidence


_HEX24_RE = re.compile(r"^[0-9a-f]{24}$")
_HEX64_RE = re.compile(r"^[0-9a-f]{64}$")
_NONCOMPLETE_OUTCOMES = frozenset(ALL_OUTCOMES) - frozenset(COMPLETE_OUTCOMES)
_BUDGET_REASON_CODES = frozenset(
    {
        REASON_ATTACHMENT_COUNT_BUDGET_EXCEEDED,
        REASON_TOTAL_BYTES_BUDGET_EXCEEDED,
    }
)
_ALLOWED_INCOMPLETE_REASON_CODES = frozenset(
    {
        *_NONCOMPLETE_OUTCOMES,
        *_BUDGET_REASON_CODES,
        *(f"archive_member_{outcome}" for outcome in _NONCOMPLETE_OUTCOMES),
    }
)
_KNOWN_FORMATS = frozenset(
    {
        FORMAT_PDF,
        FORMAT_DOCX,
        FORMAT_XLSX,
        FORMAT_PPTX,
        FORMAT_ZIP,
        FORMAT_LEGACY_OLE,
        FORMAT_XLS,
        FORMAT_DOC,
        FORMAT_CSV,
        FORMAT_TXT,
        FORMAT_XML,
        FORMAT_IMAGE,
        FORMAT_RAR,
        FORMAT_SEVENZIP,
        FORMAT_UNKNOWN,
    }
)
_KNOWN_ROLE_TAGS = frozenset(
    {
        ROLE_TECHNICAL_SPEC,
        ROLE_ADMINISTRATIVE_BASIS,
        ROLE_ECONOMIC_FORM,
        ROLE_TEMPLATE,
        ROLE_UNKNOWN,
    }
)
_ZERO_CHUNK_OUTCOMES = frozenset(
    {
        OUTCOME_DOWNLOAD_FAILED,
        OUTCOME_NEEDS_CONVERTER,
        OUTCOME_UNSUPPORTED_FORMAT,
        OUTCOME_ENCRYPTED,
        OUTCOME_CORRUPT,
    }
)
_PARTIAL_CHUNK_OUTCOMES = frozenset(
    {
        OUTCOME_NEEDS_OCR,
        OUTCOME_EXTRACTION_FAILED,
        OUTCOME_PARTIAL_DUE_TO_SAFETY_LIMIT,
    }
)


class AnexoBundleValidationError(ValueError):
    """The local evidence directory does not satisfy the verified E1 contract."""


class _DuplicateJsonKey(ValueError):
    pass


class _NonFiniteJsonNumber(ValueError):
    pass


def _fail(message: str) -> NoReturn:
    raise AnexoBundleValidationError(message)


def _assert_token_safe(payload: Any, where: str) -> None:
    try:
        assert_no_portal_tokens(
            json.dumps(payload, ensure_ascii=False, sort_keys=True), where=where
        )
    except ArtifactRedactionError as exc:
        _fail(str(exc))


def _object_without_duplicate_keys(
    pairs: list[tuple[str, Any]],
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateJsonKey(key)
        result[key] = value
    return result


def _reject_non_finite_json_number(value: str) -> NoReturn:
    raise _NonFiniteJsonNumber(value)


def _load_json_object(path: Path) -> dict[str, Any]:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        _fail(f"cannot read required evidence file {path.name}: {exc}")
    if not raw.strip():
        _fail(f"required evidence file is empty: {path.name}")
    try:
        payload = json.loads(
            raw,
            object_pairs_hook=_object_without_duplicate_keys,
            parse_constant=_reject_non_finite_json_number,
        )
    except _DuplicateJsonKey as exc:
        _fail(f"duplicate JSON key in {path.name}: {exc}")
    except _NonFiniteJsonNumber as exc:
        _fail(f"non-finite JSON number in {path.name}: {exc}")
    except json.JSONDecodeError as exc:
        _fail(f"malformed JSON in {path.name}: {exc}")
    if not isinstance(payload, dict):
        _fail(f"{path.name} must contain a JSON object")
    return payload


def _load_jsonl_objects(
    path: Path, *, allow_empty: bool = False
) -> list[dict[str, Any]]:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        _fail(f"cannot read required evidence file {path.name}: {exc}")
    if not raw.strip() and not allow_empty:
        _fail(f"required evidence file is empty: {path.name}")
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(raw.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(
                line,
                object_pairs_hook=_object_without_duplicate_keys,
                parse_constant=_reject_non_finite_json_number,
            )
        except _DuplicateJsonKey as exc:
            _fail(f"duplicate JSON key in {path.name}:{line_number}: {exc}")
        except _NonFiniteJsonNumber as exc:
            _fail(f"non-finite JSON number in {path.name}:{line_number}: {exc}")
        except json.JSONDecodeError as exc:
            _fail(f"malformed JSON in {path.name}:{line_number}: {exc}")
        if not isinstance(row, dict):
            _fail(f"{path.name}:{line_number} must contain a JSON object")
        rows.append(row)
    if not rows and not allow_empty:
        _fail(f"required evidence file has no JSONL records: {path.name}")
    return rows


def _dict(value: Any, where: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        _fail(f"{where} must be an object")
    return value


def _exact_keys(row: dict[str, Any], expected: set[str], where: str) -> None:
    actual = set(row)
    if actual != expected:
        unknown = sorted(actual - expected)
        missing = sorted(expected - actual)
        _fail(f"{where} has unknown keys {unknown} or missing keys {missing}")


def _list(value: Any, where: str) -> list[Any]:
    if not isinstance(value, list):
        _fail(f"{where} must be a list")
    return value


def _str(value: Any, where: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or (not allow_empty and not value):
        _fail(f"{where} must be a{' non-empty' if not allow_empty else ''} string")
    return value


def _optional_str(value: Any, where: str) -> str | None:
    if value is None:
        return None
    return _str(value, where, allow_empty=True)


def _int(value: Any, where: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        _fail(f"{where} must be an integer >= {minimum}")
    return value


def _optional_int(value: Any, where: str) -> int | None:
    if value is None:
        return None
    return _int(value, where)


def _bool(value: Any, where: str) -> bool:
    if not isinstance(value, bool):
        _fail(f"{where} must be a boolean")
    return value


def _strings(value: Any, where: str) -> tuple[str, ...]:
    values = _list(value, where)
    result = tuple(_str(item, f"{where}[]") for item in values)
    if len(result) != len(set(result)):
        _fail(f"{where} contains duplicate values")
    return result


def _hex(value: Any, where: str, pattern: re.Pattern[str]) -> str:
    result = _str(value, where)
    if pattern.fullmatch(result) is None:
        _fail(f"{where} is not a canonical lowercase digest/id")
    return result


def _unique_rows_by_tender(
    payload: dict[str, Any],
    *,
    where: str,
) -> dict[str, dict[str, Any]]:
    rows = _list(payload.get("tenders"), f"{where}.tenders")
    if not rows:
        _fail(f"{where}.tenders must not be empty")
    by_id: dict[str, dict[str, Any]] = {}
    for index, raw in enumerate(rows):
        row = _dict(raw, f"{where}.tenders[{index}]")
        tender_id = _str(row.get("tender_id"), f"{where}.tenders[{index}].tender_id")
        if tender_id in by_id:
            _fail(f"duplicate tender_id in {where}: {tender_id}")
        by_id[tender_id] = row
    return by_id


def _archive_member(raw: Any, where: str, *, tender_id: str) -> ArchiveMemberRecord:
    row = _dict(raw, where)
    member = ArchiveMemberRecord(
        member_id=_hex(row.get("member_id"), f"{where}.member_id", _HEX24_RE),
        container_attachment_id=_hex(
            row.get("container_attachment_id"),
            f"{where}.container_attachment_id",
            _HEX24_RE,
        ),
        tender_id=_str(row.get("tender_id"), f"{where}.tender_id"),
        member_path=_str(row.get("member_path"), f"{where}.member_path"),
        member_sha256=_str(
            row.get("member_sha256"),
            f"{where}.member_sha256",
            allow_empty=True,
        ),
        byte_count=_int(row.get("byte_count"), f"{where}.byte_count"),
        depth=_int(row.get("depth"), f"{where}.depth", minimum=1),
        detected_format=_str(row.get("detected_format"), f"{where}.detected_format"),
        outcome=_str(row.get("outcome"), f"{where}.outcome"),
        warnings=_strings(row.get("warnings"), f"{where}.warnings"),
        duplicate_of_sha256=_optional_str(
            row.get("duplicate_of_sha256"),
            f"{where}.duplicate_of_sha256",
        ),
    )
    _exact_keys(row, set(member.to_dict()), where)
    if member.tender_id != tender_id:
        _fail(f"{where}.tender_id does not match enclosing tender")
    if member.outcome not in ALL_OUTCOMES:
        _fail(f"{where}.outcome is not an E1 outcome: {member.outcome}")
    if member.detected_format not in _KNOWN_FORMATS:
        _fail(f"{where}.detected_format is not an E1 format")
    expected_id = hashlib.sha256(
        f"{member.container_attachment_id}|{member.member_path}".encode("utf-8")
    ).hexdigest()[:24]
    if member.member_id != expected_id:
        _fail(f"{where}.member_id is not recomputable from container/path")
    if member.member_sha256:
        _hex(member.member_sha256, f"{where}.member_sha256", _HEX64_RE)
    elif member.byte_count:
        _fail(f"{where} has bytes but no member_sha256")
    if member.duplicate_of_sha256 is not None:
        _fail(f"{where}.duplicate_of_sha256 is unsupported by E1 v1")
    return member


def _attachment_extraction(raw: Any, where: str) -> AttachmentExtraction | None:
    if raw is None:
        return None
    row = _dict(raw, where)
    extraction = AttachmentExtraction(
        outcome=_str(row.get("outcome"), f"{where}.outcome"),
        detected_format=_str(row.get("detected_format"), f"{where}.detected_format"),
        chunk_count=_int(row.get("chunk_count"), f"{where}.chunk_count"),
        char_count=_int(row.get("char_count"), f"{where}.char_count"),
        warnings=_strings(row.get("warnings"), f"{where}.warnings"),
        page_count=_optional_int(row.get("page_count"), f"{where}.page_count"),
        sheet_count=_optional_int(row.get("sheet_count"), f"{where}.sheet_count"),
        row_count=_optional_int(row.get("row_count"), f"{where}.row_count"),
        extractor=_optional_str(row.get("extractor"), f"{where}.extractor"),
        error_message=_optional_str(
            row.get("error_message"),
            f"{where}.error_message",
        ),
    )
    _exact_keys(row, set(extraction.to_dict()), where)
    if extraction.outcome not in ALL_OUTCOMES:
        _fail(f"{where}.outcome is not an E1 outcome: {extraction.outcome}")
    return extraction


def _attachment(raw: Any, where: str, *, tender_id: str) -> AttachmentRecord:
    row = _dict(raw, where)
    members = tuple(
        _archive_member(
            member, f"{where}.archive_members[{index}]", tender_id=tender_id
        )
        for index, member in enumerate(
            _list(row.get("archive_members"), f"{where}.archive_members")
        )
    )
    record = AttachmentRecord(
        attachment_id=_hex(
            row.get("attachment_id"), f"{where}.attachment_id", _HEX24_RE
        ),
        tender_id=_str(row.get("tender_id"), f"{where}.tender_id"),
        listing_ordinal=_int(row.get("listing_ordinal"), f"{where}.listing_ordinal"),
        row_ordinal=_int(row.get("row_ordinal"), f"{where}.row_ordinal"),
        portal_filename=_str(
            row.get("portal_filename"),
            f"{where}.portal_filename",
            allow_empty=True,
        ),
        safe_filename=_str(row.get("safe_filename"), f"{where}.safe_filename"),
        tipo=_str(row.get("tipo"), f"{where}.tipo", allow_empty=True),
        descripcion=_str(
            row.get("descripcion"), f"{where}.descripcion", allow_empty=True
        ),
        fecha_adjunto=_str(
            row.get("fecha_adjunto"), f"{where}.fecha_adjunto", allow_empty=True
        ),
        content_type=_str(
            row.get("content_type"), f"{where}.content_type", allow_empty=True
        ),
        detected_format=_str(row.get("detected_format"), f"{where}.detected_format"),
        extension=_str(row.get("extension"), f"{where}.extension", allow_empty=True),
        byte_count=_int(row.get("byte_count"), f"{where}.byte_count"),
        sha256=_str(row.get("sha256"), f"{where}.sha256", allow_empty=True),
        download_status=_str(row.get("download_status"), f"{where}.download_status"),
        outcome=_str(row.get("outcome"), f"{where}.outcome"),
        role_tag=_str(row.get("role_tag"), f"{where}.role_tag"),
        warnings=_strings(row.get("warnings"), f"{where}.warnings"),
        duplicate_of_attachment_id=_optional_str(
            row.get("duplicate_of_attachment_id"),
            f"{where}.duplicate_of_attachment_id",
        ),
        duplicate_kind=_optional_str(
            row.get("duplicate_kind"), f"{where}.duplicate_kind"
        ),
        extraction_reused_from_attachment_id=_optional_str(
            row.get("extraction_reused_from_attachment_id"),
            f"{where}.extraction_reused_from_attachment_id",
        ),
        evidence_attachment_id=_optional_str(
            row.get("evidence_attachment_id"),
            f"{where}.evidence_attachment_id",
        ),
        extraction=_attachment_extraction(row.get("extraction"), f"{where}.extraction"),
        archive_members=members,
        error_message=_optional_str(row.get("error_message"), f"{where}.error_message"),
    )
    _exact_keys(row, set(record.to_dict()), where)
    if record.tender_id != tender_id:
        _fail(f"{where}.tender_id does not match enclosing tender")
    expected_id = build_attachment_id(
        tender_id, record.listing_ordinal, record.row_ordinal
    )
    if record.attachment_id != expected_id:
        _fail(f"{where}.attachment_id is not recomputable from tender/position")
    if record.outcome not in ALL_OUTCOMES:
        _fail(f"{where}.outcome is not an E1 outcome: {record.outcome}")
    if record.detected_format not in _KNOWN_FORMATS:
        _fail(f"{where}.detected_format is not an E1 format")
    if record.role_tag not in _KNOWN_ROLE_TAGS:
        _fail(f"{where}.role_tag is not an E1 role")
    if record.download_status not in {"downloaded", "not_downloaded"}:
        _fail(f"{where}.download_status is invalid")
    if record.download_status == "downloaded":
        _hex(record.sha256, f"{where}.sha256", _HEX64_RE)
        if record.extraction is None:
            _fail(f"{where} is downloaded but has no extraction record")
    elif record.sha256 or record.byte_count or record.extraction is not None:
        _fail(f"{where} not_downloaded record carries downloaded content")
    if record.outcome == OUTCOME_DOWNLOAD_FAILED:
        if record.download_status != "not_downloaded":
            _fail(f"{where} download_failed row must be not_downloaded")
    elif record.download_status != "downloaded":
        _fail(f"{where} non-download-failure row must be downloaded")
    if record.extraction is not None:
        if record.extraction.outcome != record.outcome:
            _fail(f"{where} attachment/extraction outcomes disagree")
        if (
            record.duplicate_of_attachment_id is None
            and record.extraction.detected_format != record.detected_format
        ):
            _fail(f"{where} attachment/extraction formats disagree")
    if members and record.detected_format != FORMAT_ZIP:
        _fail(f"{where} non-archive attachment carries archive members")
    for member in members:
        if member.container_attachment_id != record.attachment_id:
            _fail(f"{where} archive member is bound to another container")
    return record


def _chunk(raw: Any, where: str) -> EvidenceChunk:
    row = _dict(raw, where)
    locator = _dict(row.get("locator"), f"{where}.locator")
    text = _str(row.get("text"), f"{where}.text")
    chunk = EvidenceChunk(
        chunk_id=_hex(row.get("chunk_id"), f"{where}.chunk_id", _HEX24_RE),
        attachment_id=_hex(
            row.get("attachment_id"), f"{where}.attachment_id", _HEX24_RE
        ),
        tender_id=_str(row.get("tender_id"), f"{where}.tender_id"),
        source_format=_str(row.get("source_format"), f"{where}.source_format"),
        locator_type=_str(row.get("locator_type"), f"{where}.locator_type"),
        locator=locator,
        text=text,
        text_sha256=_hex(row.get("text_sha256"), f"{where}.text_sha256", _HEX64_RE),
        char_count=_int(row.get("char_count"), f"{where}.char_count", minimum=1),
        ordinal=_int(row.get("ordinal"), f"{where}.ordinal", minimum=1),
        container_attachment_id=_optional_str(
            row.get("container_attachment_id"),
            f"{where}.container_attachment_id",
        ),
        member_path=_optional_str(row.get("member_path"), f"{where}.member_path"),
        warnings=_strings(row.get("warnings"), f"{where}.warnings"),
    )
    _exact_keys(row, set(chunk.to_dict()), where)
    if chunk.container_attachment_id is not None:
        _hex(
            chunk.container_attachment_id,
            f"{where}.container_attachment_id",
            _HEX24_RE,
        )
    if chunk.source_format not in _KNOWN_FORMATS:
        _fail(f"{where}.source_format is not an E1 format")
    if chunk.text_sha256 != sha256_text(chunk.text):
        _fail(f"{where}.text_sha256 does not match text")
    if chunk.char_count != len(chunk.text):
        _fail(f"{where}.char_count does not match text")
    locator_key = json.dumps(chunk.locator, sort_keys=True, separators=(",", ":"))
    expected_id = build_chunk_id(
        chunk.attachment_id,
        chunk.member_path or "",
        locator_key,
        chunk.ordinal,
    )
    if chunk.chunk_id != expected_id:
        _fail(f"{where}.chunk_id is not recomputable from provenance")
    return chunk


def _assert_unique(values: list[str], where: str) -> None:
    duplicates = sorted(value for value, count in Counter(values).items() if count > 1)
    if duplicates:
        _fail(f"duplicate identities in {where}: {duplicates[:5]}")


def _validate_summary_row_types(row: dict[str, Any], *, tender_id: str) -> None:
    where = f"summary[{tender_id}]"
    for key in (
        "listing_page_count",
        "attachments_discovered",
        "attachments_downloaded",
        "attachments_extraction_success",
        "attachments_empty",
        "attachments_needs_ocr",
        "attachments_needs_converter",
        "attachments_unsupported",
        "attachments_failed",
        "attachments_partial",
        "archive_children",
        "chunk_count",
        "bytes_downloaded",
    ):
        _int(row.get(key), f"{where}.{key}")
    for key in ("download_complete", "extraction_complete", "bundle_complete"):
        _bool(row.get(key), f"{where}.{key}")
    _str(row.get("source_kind"), f"{where}.source_kind")
    _hex(row.get("semantic_digest"), f"{where}.semantic_digest", _HEX64_RE)
    outcomes = _dict(row.get("outcome_counts"), f"{where}.outcome_counts")
    for outcome, count in outcomes.items():
        if outcome not in ALL_OUTCOMES:
            _fail(f"{where}.outcome_counts contains unknown outcome {outcome!r}")
        _int(count, f"{where}.outcome_counts.{outcome}")


def _validate_summary_total_types(summary: dict[str, Any]) -> None:
    for key in (
        "tenders",
        "attachments_discovered",
        "attachments_downloaded",
        "archive_children",
        "chunk_count",
        "bytes_downloaded",
        "duplicate_content_groups",
        "bundles_complete",
        "bundles_incomplete",
    ):
        _int(summary.get(key), f"summary.{key}")
    outcomes = _dict(summary.get("outcome_totals"), "summary.outcome_totals")
    for outcome, count in outcomes.items():
        if outcome not in ALL_OUTCOMES:
            _fail(f"summary.outcome_totals contains unknown outcome {outcome!r}")
        _int(count, f"summary.outcome_totals.{outcome}")


def _validate_attachment_relationships(
    records: tuple[AttachmentRecord, ...],
    *,
    where: str,
) -> None:
    by_id = {record.attachment_id: record for record in records}
    positions = [(record.listing_ordinal, record.row_ordinal) for record in records]
    if len(positions) != len(set(positions)):
        _fail(f"duplicate listing/row positions in {where}")
    for record in records:
        evidence_id = record.evidence_attachment_id
        if record.download_status == "not_downloaded":
            if evidence_id is not None:
                _fail(f"{where}.{record.attachment_id} failed download claims evidence")
            if record.duplicate_of_attachment_id is not None:
                _fail(
                    f"{where}.{record.attachment_id} failed download claims duplicate"
                )
            if record.duplicate_kind is not None:
                _fail(f"{where}.{record.attachment_id} has orphan duplicate_kind")
            if record.extraction_reused_from_attachment_id is not None:
                _fail(f"{where}.{record.attachment_id} has orphan extraction reuse")
            continue
        if not evidence_id:
            _fail(f"{where}.{record.attachment_id} lacks evidence_attachment_id")
        if evidence_id not in by_id:
            _fail(f"{where}.{record.attachment_id} has unbound evidence attachment")
        duplicate_id = record.duplicate_of_attachment_id
        if duplicate_id is None:
            if record.duplicate_kind is not None:
                _fail(f"{where}.{record.attachment_id} has orphan duplicate_kind")
            if record.extraction_reused_from_attachment_id is not None:
                _fail(f"{where}.{record.attachment_id} has orphan extraction reuse")
            if evidence_id != record.attachment_id:
                _fail(f"{where}.{record.attachment_id} points at unrelated evidence")
            continue
        if duplicate_id == record.attachment_id or duplicate_id not in by_id:
            _fail(f"{where}.{record.attachment_id} has invalid duplicate target")
        original = by_id[duplicate_id]
        if original.duplicate_of_attachment_id is not None:
            _fail(f"{where}.{record.attachment_id} duplicate target is not canonical")
        if record.duplicate_kind not in {
            "same_name_same_bytes",
            "different_name_same_bytes",
        }:
            _fail(f"{where}.{record.attachment_id} has invalid duplicate_kind")
        expected_kind = (
            "same_name_same_bytes"
            if record.safe_filename == original.safe_filename
            else "different_name_same_bytes"
        )
        if record.duplicate_kind != expected_kind:
            _fail(f"{where}.{record.attachment_id} duplicate_kind disagrees with name")
        if record.sha256 != original.sha256:
            _fail(f"{where}.{record.attachment_id} duplicate digest disagrees")
        if record.byte_count != original.byte_count:
            _fail(f"{where}.{record.attachment_id} duplicate byte_count disagrees")
        if record.outcome != original.outcome:
            _fail(f"{where}.{record.attachment_id} duplicate outcome disagrees")
        if record.extraction_reused_from_attachment_id != duplicate_id:
            _fail(f"{where}.{record.attachment_id} extraction reuse is unbound")
        if evidence_id != duplicate_id:
            _fail(f"{where}.{record.attachment_id} evidence target is unbound")
        if record.archive_members:
            _fail(f"{where}.{record.attachment_id} duplicate repeats archive members")
        assert record.extraction is not None
        assert original.extraction is not None
        if (
            record.extraction.detected_format
            != original.extraction.detected_format
        ):
            _fail(
                f"{where}.{record.attachment_id} duplicate extraction format disagrees"
            )
        if record.extraction.chunk_count or record.extraction.char_count:
            _fail(f"{where}.{record.attachment_id} duplicate repeats extracted content")
        for field in ("page_count", "sheet_count", "row_count", "extractor"):
            if getattr(record.extraction, field) != getattr(original.extraction, field):
                _fail(
                    f"{where}.{record.attachment_id} duplicate extraction {field} disagrees"
                )
        expected_warnings = (
            *original.extraction.warnings,
            f"extraction_reused_from:{duplicate_id}",
        )
        if record.extraction.warnings != expected_warnings:
            _fail(
                f"{where}.{record.attachment_id} duplicate extraction warnings disagree"
            )
        if record.extraction.error_message is not None or record.error_message is not None:
            _fail(f"{where}.{record.attachment_id} duplicate carries error message")


def _validate_outcome_content(
    *, outcome: str, chunk_count: int, where: str
) -> None:
    if outcome == OUTCOME_EXTRACTED_EMPTY and chunk_count != 0:
        _fail(f"{where} claims extracted_empty while carrying chunks")
    if outcome in _ZERO_CHUNK_OUTCOMES and chunk_count != 0:
        _fail(f"{where} outcome {outcome} cannot carry extracted chunks")
    if outcome not in {
        OUTCOME_EXTRACTION_SUCCESS,
        OUTCOME_EXTRACTED_EMPTY,
        *_ZERO_CHUNK_OUTCOMES,
        *_PARTIAL_CHUNK_OUTCOMES,
    }:
        _fail(f"{where} has unknown outcome/content semantics: {outcome}")
    # Only needs_ocr, extraction_failed, and partial_due_to_safety_limit may
    # retain bounded partial chunks.  This preserves positive evidence while
    # never treating incomplete extraction as negative proof.


def _validate_chunk_joins(
    *,
    tender_id: str,
    records: tuple[AttachmentRecord, ...],
    chunks: tuple[EvidenceChunk, ...],
) -> None:
    attachment_by_id = {record.attachment_id: record for record in records}
    member_by_id: dict[str, ArchiveMemberRecord] = {}
    for record in records:
        for member in record.archive_members:
            if member.member_id in member_by_id or member.member_id in attachment_by_id:
                _fail(f"duplicate/colliding archive member id: {member.member_id}")
            member_by_id[member.member_id] = member

    ordinals: dict[tuple[str, str | None], list[int]] = defaultdict(list)
    direct_by_attachment: Counter[str] = Counter()
    archive_by_container: Counter[str] = Counter()
    chars_by_attachment: Counter[str] = Counter()
    chars_by_container: Counter[str] = Counter()
    chunks_by_member: Counter[str] = Counter()
    for chunk in chunks:
        if chunk.tender_id != tender_id:
            _fail(f"chunk {chunk.chunk_id} crosses tender boundary")
        ordinals[(chunk.attachment_id, chunk.member_path)].append(chunk.ordinal)
        if chunk.attachment_id in attachment_by_id:
            record = attachment_by_id[chunk.attachment_id]
            if record.detected_format == FORMAT_ZIP or record.archive_members:
                _fail(f"archive container {record.attachment_id} carries direct chunk")
            if (
                chunk.container_attachment_id is not None
                or chunk.member_path is not None
            ):
                _fail(f"direct chunk {chunk.chunk_id} has archive provenance")
            if chunk.source_format != record.detected_format:
                _fail(f"direct chunk {chunk.chunk_id} format does not match attachment")
            direct_by_attachment[record.attachment_id] += 1
            chars_by_attachment[record.attachment_id] += chunk.char_count
        elif chunk.attachment_id in member_by_id:
            member = member_by_id[chunk.attachment_id]
            if chunk.container_attachment_id != member.container_attachment_id:
                _fail(f"archive chunk {chunk.chunk_id} has wrong container")
            if chunk.member_path != member.member_path:
                _fail(f"archive chunk {chunk.chunk_id} has wrong member path")
            if chunk.source_format != member.detected_format:
                _fail(f"archive chunk {chunk.chunk_id} format does not match member")
            archive_by_container[member.container_attachment_id] += 1
            chars_by_container[member.container_attachment_id] += chunk.char_count
            chunks_by_member[member.member_id] += 1
        else:
            _fail(f"chunk {chunk.chunk_id} references unknown attachment/member")

    for key, values in ordinals.items():
        expected = list(range(1, len(values) + 1))
        if sorted(values) != expected:
            _fail(f"chunk ordinals are not contiguous for source {key}")

    for record in records:
        extraction = record.extraction
        if record.archive_members:
            expected_chunks = archive_by_container[record.attachment_id]
            expected_chars = chars_by_container[record.attachment_id]
        else:
            expected_chunks = direct_by_attachment[record.attachment_id]
            expected_chars = chars_by_attachment[record.attachment_id]
        _validate_outcome_content(
            outcome=record.outcome,
            chunk_count=expected_chunks,
            where=f"attachment {record.attachment_id}",
        )
        if (
            record.outcome == OUTCOME_EXTRACTION_SUCCESS
            and expected_chunks == 0
            and record.duplicate_of_attachment_id is None
        ):
            _fail(
                f"attachment {record.attachment_id} claims extraction_success "
                "without extracted chunks"
            )
        if extraction is None:
            continue
        if extraction.chunk_count != expected_chunks:
            _fail(f"attachment {record.attachment_id} extraction chunk_count disagrees")
        if extraction.char_count != expected_chars:
            _fail(f"attachment {record.attachment_id} extraction char_count disagrees")
        if record.duplicate_of_attachment_id is not None:
            continue
        if record.detected_format == FORMAT_ZIP:
            if record.outcome not in {
                OUTCOME_EXTRACTION_SUCCESS,
                OUTCOME_EXTRACTED_EMPTY,
                OUTCOME_PARTIAL_DUE_TO_SAFETY_LIMIT,
            }:
                _fail(f"archive attachment {record.attachment_id} has invalid outcome")
            if (
                record.outcome == OUTCOME_EXTRACTION_SUCCESS
                and (
                    not record.archive_members
                    or extraction.warnings
                    or any(
                        member.outcome not in COMPLETE_OUTCOMES
                        for member in record.archive_members
                    )
                )
            ):
                _fail(
                    f"archive attachment {record.attachment_id} success disagrees with members"
                )
            if record.outcome == OUTCOME_EXTRACTED_EMPTY and (
                extraction.warnings
                or any(
                    member.outcome not in COMPLETE_OUTCOMES
                    for member in record.archive_members
                )
            ):
                _fail(
                    f"archive attachment {record.attachment_id} is empty with incomplete member"
                )
            if record.outcome == OUTCOME_PARTIAL_DUE_TO_SAFETY_LIMIT and not (
                extraction.warnings
                or any(
                    member.outcome not in COMPLETE_OUTCOMES
                    for member in record.archive_members
                )
            ):
                _fail(
                    f"archive attachment {record.attachment_id} is partial without debt"
                )

    members = tuple(member_by_id.values())
    ambiguous_parent_ids: set[str] = set()
    resolved_parent_by_child: dict[str, str] = {}
    for child in members:
        if child.depth <= 1:
            continue
        candidates = tuple(
            candidate
            for candidate in members
            if candidate.detected_format == FORMAT_ZIP
            and candidate.depth == child.depth - 1
            and child.member_path.startswith(f"{candidate.member_path}::")
        )
        if not candidates:
            _fail(f"archive member {child.member_id} has no ZIP parent")
        if len(candidates) == 1:
            resolved_parent_by_child[child.member_id] = candidates[0].member_id
        else:
            # E1 v1 has no parent_member_id and permits literal ``::`` inside a
            # member name, so this hierarchy is not uniquely attributable.  Do
            # not invent a parent; exact container/member/chunk joins below stay
            # authoritative and all possible parents skip descendant inference.
            ambiguous_parent_ids.update(candidate.member_id for candidate in candidates)

    def _has_resolved_ancestor(child: ArchiveMemberRecord, ancestor_id: str) -> bool:
        current_id = child.member_id
        seen: set[str] = set()
        while current_id in resolved_parent_by_child:
            if current_id in seen:
                _fail(f"archive member hierarchy contains a cycle at {current_id}")
            seen.add(current_id)
            current_id = resolved_parent_by_child[current_id]
            if current_id == ancestor_id:
                return True
        return False

    for member in members:
        descendants = tuple(
            candidate
            for candidate in members
            if _has_resolved_ancestor(candidate, member.member_id)
        )
        direct_chunk_count = chunks_by_member[member.member_id]
        _validate_outcome_content(
            outcome=member.outcome,
            chunk_count=direct_chunk_count,
            where=f"archive member {member.member_id}",
        )
        if member.detected_format == FORMAT_ZIP:
            if direct_chunk_count:
                _fail(f"nested archive member {member.member_id} carries direct chunks")
            if member.outcome not in {
                OUTCOME_EXTRACTION_SUCCESS,
                OUTCOME_EXTRACTED_EMPTY,
                OUTCOME_PARTIAL_DUE_TO_SAFETY_LIMIT,
            }:
                _fail(f"nested archive member {member.member_id} has invalid outcome")
            if (
                member.member_id not in ambiguous_parent_ids
                and member.outcome == OUTCOME_EXTRACTION_SUCCESS
                and (
                not descendants
                or any(
                    descendant.outcome not in COMPLETE_OUTCOMES
                    for descendant in descendants
                )
                )
            ):
                _fail(
                    f"nested archive member {member.member_id} success disagrees with children"
                )
            if (
                member.member_id not in ambiguous_parent_ids
                and member.outcome == OUTCOME_EXTRACTED_EMPTY
                and descendants
            ):
                _fail(
                    f"nested archive member {member.member_id} empty outcome has children"
                )
        else:
            if descendants:
                _fail(f"non-archive member {member.member_id} has nested children")
            if (
                member.outcome == OUTCOME_EXTRACTION_SUCCESS
                and direct_chunk_count == 0
            ):
                _fail(
                    f"archive member {member.member_id} claims extraction_success "
                    "without extracted chunks"
                )


def _validate_extraction_manifest(
    *,
    tender_id: str,
    row: dict[str, Any],
    raw_attachments: list[dict[str, Any]],
    summary: dict[str, Any],
) -> None:
    where = f"extraction_manifest[{tender_id}]"
    _exact_keys(
        row,
        {
            "tender_id",
            "semantic_digest",
            "bundle_complete",
            "download_complete",
            "extraction_complete",
            "incomplete_reason_codes",
            "extractions",
        },
        where,
    )
    if (
        _str(row.get("semantic_digest"), f"{where}.semantic_digest")
        != summary["semantic_digest"]
    ):
        _fail(f"{where} semantic digest disagrees with summary")
    for key in ("bundle_complete", "download_complete", "extraction_complete"):
        if _bool(row.get(key), f"{where}.{key}") != summary[key]:
            _fail(f"{where}.{key} disagrees with summary")
    reasons = list(_strings(row.get("incomplete_reason_codes"), f"{where}.reasons"))
    if reasons != summary["incomplete_reason_codes"]:
        _fail(f"{where}.incomplete_reason_codes disagrees with summary")

    extraction_rows = _list(row.get("extractions"), f"{where}.extractions")
    by_id: dict[str, dict[str, Any]] = {}
    for index, raw in enumerate(extraction_rows):
        extraction = _dict(raw, f"{where}.extractions[{index}]")
        _exact_keys(
            extraction,
            {
                "attachment_id",
                "safe_filename",
                "outcome",
                "extraction",
                "archive_members",
            },
            f"{where}.extractions[{index}]",
        )
        attachment_id = _str(
            extraction.get("attachment_id"),
            f"{where}.extractions[{index}].attachment_id",
        )
        if attachment_id in by_id:
            _fail(f"duplicate attachment_id in {where}: {attachment_id}")
        by_id[attachment_id] = extraction

    expected_by_id = {str(row["attachment_id"]): row for row in raw_attachments}
    if set(by_id) != set(expected_by_id):
        _fail(f"{where} attachment set disagrees with attachment_manifest")
    for attachment_id, raw_attachment in expected_by_id.items():
        extraction = by_id[attachment_id]
        for key in ("safe_filename", "outcome", "extraction", "archive_members"):
            if extraction.get(key) != raw_attachment.get(key):
                _fail(
                    f"{where}.{attachment_id}.{key} disagrees with attachment_manifest"
                )


def load_verified_anexo_evidence(evidence_dir: Path) -> VerifiedAnexoEvidence:
    """Load and fully verify a local E1 bundle, or raise without partial output."""
    root = Path(evidence_dir)
    if not root.is_dir():
        _fail(f"annex evidence directory does not exist: {root}")
    for filename in REQUIRED_EVIDENCE_FILENAMES:
        path = root / filename
        if not path.is_file():
            _fail(f"missing required evidence file: {filename}")

    summary = _load_json_object(root / "summary.json")
    attachment_manifest = _load_json_object(root / "attachment_manifest.json")
    extraction_manifest = _load_json_object(root / "extraction_manifest.json")
    _exact_keys(attachment_manifest, {"tenders"}, "attachment_manifest")
    _exact_keys(extraction_manifest, {"tenders"}, "extraction_manifest")
    # A fully accounted bundle may legitimately contain only extracted-empty or
    # unread/needs-OCR attachments.  E1 publishes that state as an existing,
    # zero-byte JSONL file; absence of the file remains an error above.
    raw_chunks = _load_jsonl_objects(
        root / "evidence_chunks.jsonl", allow_empty=True
    )
    _assert_token_safe(summary, "verified annex summary")
    _assert_token_safe(attachment_manifest, "verified annex attachment manifest")
    _assert_token_safe(extraction_manifest, "verified annex extraction manifest")
    _assert_token_safe(raw_chunks, "verified annex evidence chunks")

    if summary.get("builder_version") != ANEXO_EVIDENCE_BUILDER_VERSION:
        _fail("summary.json builder_version is not the supported E1 version")
    if summary.get("extractor_version") != ANEXO_EVIDENCE_EXTRACTOR_VERSION:
        _fail("summary.json extractor_version is not the supported E1 version")
    _validate_summary_total_types(summary)

    summary_rows = _list(summary.get("tender_summaries"), "summary.tender_summaries")
    if not summary_rows:
        _fail("summary.tender_summaries must not be empty")
    summary_by_id: dict[str, dict[str, Any]] = {}
    for index, raw in enumerate(summary_rows):
        row = _dict(raw, f"summary.tender_summaries[{index}]")
        tender_id = _str(
            row.get("tender_id"), f"summary.tender_summaries[{index}].tender_id"
        )
        if tender_id in summary_by_id:
            _fail(f"duplicate tender_id in summary: {tender_id}")
        _validate_summary_row_types(row, tender_id=tender_id)
        summary_by_id[tender_id] = row

    attachments_by_tender_raw = _unique_rows_by_tender(
        attachment_manifest,
        where="attachment_manifest",
    )
    extractions_by_tender = _unique_rows_by_tender(
        extraction_manifest,
        where="extraction_manifest",
    )
    tender_ids = set(summary_by_id)
    normalized_tender_ids: dict[str, str] = {}
    for tender_id in sorted(tender_ids):
        normalized = tender_id.casefold()
        prior = normalized_tender_ids.get(normalized)
        if prior is not None:
            _fail(
                "case-insensitive duplicate tender identities in annex bundle: "
                f"{prior!r}, {tender_id!r}"
            )
        normalized_tender_ids[normalized] = tender_id
    if tender_ids != set(attachments_by_tender_raw):
        _fail("summary and attachment_manifest tender sets disagree")
    if tender_ids != set(extractions_by_tender):
        _fail("summary and extraction_manifest tender sets disagree")
    for tender_id, row in attachments_by_tender_raw.items():
        _exact_keys(
            row,
            {"tender_id", "attachments"},
            f"attachment_manifest[{tender_id}]",
        )

    chunks_by_tender_objects: dict[str, list[EvidenceChunk]] = defaultdict(list)
    all_chunk_ids: list[str] = []
    for index, raw in enumerate(raw_chunks):
        chunk = _chunk(raw, f"evidence_chunks.jsonl[{index}]")
        if chunk.tender_id not in tender_ids:
            _fail(f"chunk {chunk.chunk_id} references unknown tender {chunk.tender_id}")
        all_chunk_ids.append(chunk.chunk_id)
        chunks_by_tender_objects[chunk.tender_id].append(chunk)
    _assert_unique(all_chunk_ids, "evidence_chunks.jsonl.chunk_id")

    records_by_tender: dict[str, tuple[AttachmentRecord, ...]] = {}
    bundles_by_tender: dict[str, TenderAttachmentBundle] = {}
    all_attachment_ids: list[str] = []
    all_member_ids: list[str] = []
    tender_digests: dict[str, str] = {}

    for tender_id in sorted(tender_ids):
        attachment_container = attachments_by_tender_raw[tender_id]
        raw_attachments_any = _list(
            attachment_container.get("attachments"),
            f"attachment_manifest[{tender_id}].attachments",
        )
        raw_attachments = [
            _dict(raw, f"attachment_manifest[{tender_id}].attachments[{index}]")
            for index, raw in enumerate(raw_attachments_any)
        ]
        records = tuple(
            _attachment(
                raw,
                f"attachment_manifest[{tender_id}].attachments[{index}]",
                tender_id=tender_id,
            )
            for index, raw in enumerate(raw_attachments)
        )
        _assert_unique(
            [record.attachment_id for record in records],
            f"attachment_manifest[{tender_id}].attachment_id",
        )
        _validate_attachment_relationships(
            records,
            where=f"attachment_manifest[{tender_id}]",
        )
        records_by_tender[tender_id] = records
        all_attachment_ids.extend(record.attachment_id for record in records)
        for record in records:
            all_member_ids.extend(member.member_id for member in record.archive_members)

        chunks = tuple(chunks_by_tender_objects.get(tender_id, ()))
        _validate_chunk_joins(tender_id=tender_id, records=records, chunks=chunks)

        summary_row = summary_by_id[tender_id]
        incomplete_reasons = _strings(
            summary_row.get("incomplete_reason_codes"),
            f"summary[{tender_id}].incomplete_reason_codes",
        )
        if tuple(sorted(incomplete_reasons)) != incomplete_reasons:
            _fail(f"summary[{tender_id}].incomplete_reason_codes is not sorted")
        unknown_reasons = set(incomplete_reasons) - _ALLOWED_INCOMPLETE_REASON_CODES
        if unknown_reasons:
            _fail(f"summary[{tender_id}] has unknown incomplete reason codes")
        required_reasons = {
            record.outcome
            for record in records
            if record.outcome not in COMPLETE_OUTCOMES
        }
        required_reasons.update(
            f"archive_member_{member.outcome}"
            for record in records
            for member in record.archive_members
            if member.outcome not in COMPLETE_OUTCOMES
        )
        if not required_reasons <= set(incomplete_reasons):
            _fail(f"summary[{tender_id}] omits extraction coverage debt")

        digest = bundle_semantic_digest(
            tender_id=tender_id,
            attachments=records,
            chunks=chunks,
            incomplete_reason_codes=incomplete_reasons,
        )
        tender_digests[tender_id] = digest
        bundle = TenderAttachmentBundle(
            tender_id=tender_id,
            source_kind=_str(
                summary_row.get("source_kind"), f"summary[{tender_id}].source_kind"
            ),
            listing_page_count=_int(
                summary_row.get("listing_page_count"),
                f"summary[{tender_id}].listing_page_count",
            ),
            attachments_discovered=len(records),
            attachments_downloaded=sum(
                record.download_status == "downloaded" for record in records
            ),
            attachments=records,
            chunks=chunks,
            bytes_downloaded=sum(
                record.byte_count
                for record in records
                if record.download_status == "downloaded"
            ),
            incomplete_reason_codes=incomplete_reasons,
            semantic_digest=digest,
            outcome_counts=dict(Counter(record.outcome for record in records)),
        )
        # P2A-v2 keeps the canonical E1 object intact. T1-B is the authority
        # for chunk-to-attachment/archive-member provenance, including
        # locator/source-format validation.
        try:
            resolve_evidence_chunks(bundle)
        except ValueError as exc:
            _fail(
                "canonical tender evidence validation failed for "
                f"{tender_id}: {exc}"
            )

        bundles_by_tender[tender_id] = bundle

        expected_summary = bundle.to_summary_dict()
        if summary_row != expected_summary:
            _fail(f"summary[{tender_id}] is inconsistent with manifests/chunks")
        _validate_extraction_manifest(
            tender_id=tender_id,
            row=extractions_by_tender[tender_id],
            raw_attachments=raw_attachments,
            summary=summary_row,
        )

    _assert_unique(all_attachment_ids, "attachment_manifest attachment IDs")
    _assert_unique(all_member_ids, "attachment_manifest archive member IDs")
    collisions = (
        (set(all_attachment_ids) & set(all_member_ids))
        | (set(all_attachment_ids) & set(all_chunk_ids))
        | (set(all_member_ids) & set(all_chunk_ids))
    )
    if collisions:
        _fail(f"cross-kind evidence identity collision: {sorted(collisions)[:5]}")

    expected_summary_totals = {
        "builder_version": ANEXO_EVIDENCE_BUILDER_VERSION,
        "extractor_version": ANEXO_EVIDENCE_EXTRACTOR_VERSION,
        "tenders": len(tender_ids),
        "attachments_discovered": sum(
            row["attachments_discovered"] for row in summary_by_id.values()
        ),
        "attachments_downloaded": sum(
            row["attachments_downloaded"] for row in summary_by_id.values()
        ),
        "archive_children": sum(
            row["archive_children"] for row in summary_by_id.values()
        ),
        "chunk_count": len(raw_chunks),
        "bytes_downloaded": sum(
            row["bytes_downloaded"] for row in summary_by_id.values()
        ),
        "duplicate_content_groups": sum(
            1
            for records in records_by_tender.values()
            for record in records
            if record.duplicate_of_attachment_id is not None
        ),
        "outcome_totals": dict(
            sorted(
                Counter(
                    record.outcome
                    for records in records_by_tender.values()
                    for record in records
                ).items()
            )
        ),
        "bundles_complete": sum(
            bool(row["bundle_complete"]) for row in summary_by_id.values()
        ),
        "bundles_incomplete": sum(
            not bool(row["bundle_complete"]) for row in summary_by_id.values()
        ),
        "tender_summaries": summary_rows,
    }
    if summary != expected_summary_totals:
        _fail("summary.json aggregate totals are inconsistent")

    semantic_digest = canonical_json_digest(
        {
            "algorithm": VERIFIED_ANEXO_BUNDLE_DIGEST_ALGORITHM,
            "integration_contract_version": ANEXO_INTEGRATION_CONTRACT_VERSION,
            "builder_version": ANEXO_EVIDENCE_BUILDER_VERSION,
            "extractor_version": ANEXO_EVIDENCE_EXTRACTOR_VERSION,
            "tender_semantic_digests": dict(sorted(tender_digests.items())),
        }
    )
    return VerifiedAnexoEvidence(
        bundles=MappingProxyType(dict(sorted(bundles_by_tender.items()))),
        semantic_digest=semantic_digest,
        tender_semantic_digests=MappingProxyType(
            dict(sorted(tender_digests.items()))
        ),
        summary=summary,
        attachment_manifest=attachment_manifest,
        extraction_manifest=extraction_manifest,
    )


__all__ = [
    "AnexoBundleValidationError",
    "load_verified_anexo_evidence",
]
