"""P2A strict local evidence-bundle boundary contracts."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent / "fixtures"))

from chilecompra_anexo_evidence.synthetic_documents import make_csv, make_zip  # noqa: E402

from origenlab_email_pipeline.chilecompra_anexo_evidence import (  # noqa: E402
    LocalAttachmentSource,
    build_tender_bundle,
    write_evidence_outputs,
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
from origenlab_email_pipeline.chilecompra_anexo_evidence.planner import build_summary
from origenlab_email_pipeline.chilecompra_anexo_evidence.redaction import (
    redact_portal_tokens,
)
from origenlab_email_pipeline.commercial_procurement_anexo_integration import (
    AnexoBundleValidationError,
    load_verified_anexo_evidence,
)


def _dump(path: Path, payload: object) -> None:
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=True) + "\n",
        encoding="utf-8",
    )


def _write_bundles(root: Path, bundles: tuple[TenderAttachmentBundle, ...]) -> None:
    root.mkdir()
    _dump(root / "summary.json", build_summary(bundles))
    _dump(
        root / "attachment_manifest.json",
        {
            "tenders": [
                {
                    "tender_id": bundle.tender_id,
                    "attachments": [
                        attachment.to_dict() for attachment in bundle.attachments
                    ],
                }
                for bundle in bundles
            ]
        },
    )
    _dump(
        root / "extraction_manifest.json",
        {
            "tenders": [
                {
                    "tender_id": bundle.tender_id,
                    "semantic_digest": bundle.semantic_digest,
                    "bundle_complete": bundle.bundle_complete,
                    "download_complete": bundle.download_complete,
                    "extraction_complete": bundle.extraction_complete,
                    "incomplete_reason_codes": list(
                        bundle.incomplete_reason_codes
                    ),
                    "extractions": [
                        {
                            "attachment_id": attachment.attachment_id,
                            "safe_filename": attachment.safe_filename,
                            "outcome": attachment.outcome,
                            "extraction": (
                                attachment.extraction.to_dict()
                                if attachment.extraction
                                else None
                            ),
                            "archive_members": [
                                member.to_dict()
                                for member in attachment.archive_members
                            ],
                        }
                        for attachment in bundle.attachments
                    ],
                }
                for bundle in bundles
            ]
        },
    )
    lines = [
        json.dumps(chunk.to_dict(), sort_keys=True, ensure_ascii=True)
        for bundle in bundles
        for chunk in bundle.chunks
    ]
    (root / "evidence_chunks.jsonl").write_text(
        "\n".join(lines) + ("\n" if lines else ""),
        encoding="utf-8",
    )


def _make_bundle(
    tender_id: str,
    *,
    outcome: str = "extraction_success",
    text: str | None = "adquisición de centrífuga de laboratorio",
) -> TenderAttachmentBundle:
    attachment_id = build_attachment_id(tender_id, 0, 0)
    chunks: tuple[EvidenceChunk, ...] = ()
    if text is not None:
        locator = {"paragraph": 1, "section": "body"}
        locator_key = json.dumps(locator, sort_keys=True, separators=(",", ":"))
        chunks = (
            EvidenceChunk(
                chunk_id=build_chunk_id(attachment_id, "", locator_key, 1),
                attachment_id=attachment_id,
                tender_id=tender_id,
                source_format="docx",
                locator_type="docx_paragraph",
                locator=locator,
                text=text,
                text_sha256=sha256_text(text),
                char_count=len(text),
                ordinal=1,
            ),
        )
    extraction = AttachmentExtraction(
        outcome=outcome,
        detected_format="docx",
        chunk_count=len(chunks),
        char_count=sum(chunk.char_count for chunk in chunks),
        extractor="ooxml_docx",
    )
    attachment = AttachmentRecord(
        attachment_id=attachment_id,
        tender_id=tender_id,
        listing_ordinal=0,
        row_ordinal=0,
        portal_filename="bases.docx",
        safe_filename="bases.docx",
        tipo="Anexo Técnico",
        descripcion="Bases técnicas",
        fecha_adjunto="01-01-2026 10:00:00",
        content_type="application/octet-stream",
        detected_format="docx",
        extension="docx",
        byte_count=100,
        sha256=hashlib.sha256(tender_id.encode()).hexdigest(),
        download_status="downloaded",
        outcome=outcome,
        role_tag="technical_spec",
        evidence_attachment_id=attachment_id,
        extraction=extraction,
    )
    incomplete = () if outcome in {"extraction_success", "extracted_empty"} else (outcome,)
    digest = bundle_semantic_digest(
        tender_id=tender_id,
        attachments=(attachment,),
        chunks=chunks,
        incomplete_reason_codes=incomplete,
    )
    return TenderAttachmentBundle(
        tender_id=tender_id,
        source_kind="mercadopublico_portal",
        listing_page_count=1,
        attachments_discovered=1,
        attachments_downloaded=1,
        attachments=(attachment,),
        chunks=chunks,
        bytes_downloaded=100,
        incomplete_reason_codes=incomplete,
        semantic_digest=digest,
        outcome_counts={outcome: 1},
    )


def _write_valid_bundle(root: Path) -> TenderAttachmentBundle:
    root.mkdir()
    tender_id = "1234-1-LE26"
    attachment_id = build_attachment_id(tender_id, 0, 0)
    text = "adquisición de centrífuga de laboratorio"
    locator = {"paragraph": 1, "section": "body"}
    locator_key = json.dumps(locator, sort_keys=True, separators=(",", ":"))
    chunk = EvidenceChunk(
        chunk_id=build_chunk_id(attachment_id, "", locator_key, 1),
        attachment_id=attachment_id,
        tender_id=tender_id,
        source_format="docx",
        locator_type="docx_paragraph",
        locator=locator,
        text=text,
        text_sha256=sha256_text(text),
        char_count=len(text),
        ordinal=1,
    )
    extraction = AttachmentExtraction(
        outcome="extraction_success",
        detected_format="docx",
        chunk_count=1,
        char_count=len(text),
        extractor="ooxml_docx",
    )
    attachment = AttachmentRecord(
        attachment_id=attachment_id,
        tender_id=tender_id,
        listing_ordinal=0,
        row_ordinal=0,
        portal_filename="bases.docx",
        safe_filename="bases.docx",
        tipo="Anexo Técnico",
        descripcion="Bases técnicas",
        fecha_adjunto="01-01-2026 10:00:00",
        content_type="application/octet-stream",
        detected_format="docx",
        extension="docx",
        byte_count=100,
        sha256="a" * 64,
        download_status="downloaded",
        outcome="extraction_success",
        role_tag="technical_spec",
        evidence_attachment_id=attachment_id,
        extraction=extraction,
    )
    digest = bundle_semantic_digest(
        tender_id=tender_id,
        attachments=(attachment,),
        chunks=(chunk,),
        incomplete_reason_codes=(),
    )
    bundle = TenderAttachmentBundle(
        tender_id=tender_id,
        source_kind="mercadopublico_portal",
        listing_page_count=1,
        attachments_discovered=1,
        attachments_downloaded=1,
        attachments=(attachment,),
        chunks=(chunk,),
        bytes_downloaded=100,
        semantic_digest=digest,
        outcome_counts={"extraction_success": 1},
    )
    _dump(root / "summary.json", build_summary((bundle,)))
    _dump(
        root / "attachment_manifest.json",
        {
            "tenders": [
                {
                    "tender_id": tender_id,
                    "attachments": [attachment.to_dict()],
                }
            ]
        },
    )
    _dump(
        root / "extraction_manifest.json",
        {
            "tenders": [
                {
                    "tender_id": tender_id,
                    "semantic_digest": digest,
                    "bundle_complete": True,
                    "download_complete": True,
                    "extraction_complete": True,
                    "incomplete_reason_codes": [],
                    "extractions": [
                        {
                            "attachment_id": attachment_id,
                            "safe_filename": attachment.safe_filename,
                            "outcome": attachment.outcome,
                            "extraction": extraction.to_dict(),
                            "archive_members": [],
                        }
                    ],
                }
            ]
        },
    )
    (root / "evidence_chunks.jsonl").write_text(
        json.dumps(chunk.to_dict(), sort_keys=True, ensure_ascii=True) + "\n",
        encoding="utf-8",
    )
    return bundle


def test_verified_bundle_loads_and_has_deterministic_aggregate_digest(
    tmp_path: Path,
) -> None:
    bundle = _write_valid_bundle(tmp_path / "evidence")

    first = load_verified_anexo_evidence(tmp_path / "evidence")
    second = load_verified_anexo_evidence(tmp_path / "evidence")

    assert first.semantic_digest == second.semantic_digest
    assert len(first.semantic_digest) == 64
    assert first.tender_semantic_digests == {bundle.tender_id: bundle.semantic_digest}
    assert first.tender_ids == (bundle.tender_id,)


@pytest.mark.parametrize(
    "filename",
    [
        "summary.json",
        "attachment_manifest.json",
        "extraction_manifest.json",
        "evidence_chunks.jsonl",
    ],
)
def test_verified_bundle_requires_every_e1_artifact(
    tmp_path: Path,
    filename: str,
) -> None:
    root = tmp_path / "evidence"
    _write_valid_bundle(root)
    (root / filename).unlink()

    with pytest.raises(AnexoBundleValidationError, match="missing required"):
        load_verified_anexo_evidence(root)


def test_verified_bundle_rejects_empty_or_malformed_artifacts(tmp_path: Path) -> None:
    root = tmp_path / "evidence"
    _write_valid_bundle(root)
    (root / "summary.json").write_text("{}{}", encoding="utf-8")

    with pytest.raises(AnexoBundleValidationError, match="malformed JSON"):
        load_verified_anexo_evidence(root)


def test_verified_bundle_rejects_chunk_text_hash_or_count_tampering(
    tmp_path: Path,
) -> None:
    root = tmp_path / "evidence"
    _write_valid_bundle(root)
    chunk_path = root / "evidence_chunks.jsonl"
    chunk = json.loads(chunk_path.read_text(encoding="utf-8"))
    chunk["text"] += " alterado"
    chunk_path.write_text(json.dumps(chunk) + "\n", encoding="utf-8")

    with pytest.raises(AnexoBundleValidationError, match="text_sha256"):
        load_verified_anexo_evidence(root)


def test_verified_bundle_rejects_cross_tender_chunk_binding(tmp_path: Path) -> None:
    root = tmp_path / "evidence"
    _write_valid_bundle(root)
    chunk_path = root / "evidence_chunks.jsonl"
    chunk = json.loads(chunk_path.read_text(encoding="utf-8"))
    chunk["tender_id"] = "9999-9-LE26"
    chunk_path.write_text(json.dumps(chunk) + "\n", encoding="utf-8")

    with pytest.raises(AnexoBundleValidationError, match="unknown tender"):
        load_verified_anexo_evidence(root)


def test_verified_bundle_rejects_cross_file_extraction_mismatch(
    tmp_path: Path,
) -> None:
    root = tmp_path / "evidence"
    _write_valid_bundle(root)
    path = root / "extraction_manifest.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["tenders"][0]["extractions"][0]["outcome"] = "needs_ocr"
    _dump(path, payload)

    with pytest.raises(AnexoBundleValidationError, match="attachment_manifest"):
        load_verified_anexo_evidence(root)


def test_verified_bundle_rejects_summary_digest_or_version_tampering(
    tmp_path: Path,
) -> None:
    root = tmp_path / "evidence"
    _write_valid_bundle(root)
    path = root / "summary.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["builder_version"] = "unsupported"
    _dump(path, payload)

    with pytest.raises(AnexoBundleValidationError, match="builder_version"):
        load_verified_anexo_evidence(root)


def test_verified_bundle_rejects_duplicate_json_keys(tmp_path: Path) -> None:
    root = tmp_path / "evidence"
    _write_valid_bundle(root)
    (root / "summary.json").write_text(
        '{"builder_version":"a","builder_version":"b"}\n',
        encoding="utf-8",
    )

    with pytest.raises(AnexoBundleValidationError, match="duplicate JSON key"):
        load_verified_anexo_evidence(root)


def test_verified_bundle_rejects_unredacted_portal_tokens(tmp_path: Path) -> None:
    root = tmp_path / "evidence"
    _write_valid_bundle(root)
    path = root / "evidence_chunks.jsonl"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["text"] += " https://portal.invalid/anexo?ticket=opaque-secret"
    path.write_text(json.dumps(payload) + "\n", encoding="utf-8")

    with pytest.raises(AnexoBundleValidationError, match="portal token"):
        load_verified_anexo_evidence(root)


@pytest.mark.parametrize("outcome", ["extracted_empty", "needs_ocr"])
def test_verified_bundle_accepts_accounted_zero_chunk_e1_bundle(
    tmp_path: Path,
    outcome: str,
) -> None:
    root = tmp_path / "evidence"
    bundle = _make_bundle("1234-2-LE26", outcome=outcome, text=None)
    _write_bundles(root, (bundle,))

    verified = load_verified_anexo_evidence(root)

    assert verified.tender_ids == (bundle.tender_id,)
    assert verified.bundle(bundle.tender_id).chunks == ()


@pytest.mark.parametrize(
    ("outcome", "text"),
    [
        ("extracted_empty", "adquisición de centrífuga"),
        ("extraction_success", None),
    ],
)
def test_verified_bundle_rejects_outcome_content_contradictions(
    tmp_path: Path,
    outcome: str,
    text: str | None,
) -> None:
    root = tmp_path / "evidence"
    _write_bundles(
        root,
        (_make_bundle("1234-3-LE26", outcome=outcome, text=text),),
    )

    with pytest.raises(AnexoBundleValidationError, match="claims|cannot carry"):
        load_verified_anexo_evidence(root)


@pytest.mark.parametrize(
    "outcome",
    [
        "needs_converter",
        "unsupported_format",
        "encrypted",
        "corrupt",
    ],
)
def test_verified_bundle_rejects_positive_chunks_for_zero_chunk_outcomes(
    tmp_path: Path,
    outcome: str,
) -> None:
    root = tmp_path / "evidence"
    _write_bundles(root, (_make_bundle("1234-30-LE26", outcome=outcome),))

    with pytest.raises(AnexoBundleValidationError, match="cannot carry"):
        load_verified_anexo_evidence(root)


def test_verified_bundle_rejects_download_failed_attachment_with_chunk(
    tmp_path: Path,
) -> None:
    root = tmp_path / "evidence"
    positive = _make_bundle("1234-31-LE26")
    original = positive.attachments[0]
    failed = AttachmentRecord(
        attachment_id=original.attachment_id,
        tender_id=original.tender_id,
        listing_ordinal=original.listing_ordinal,
        row_ordinal=original.row_ordinal,
        portal_filename=original.portal_filename,
        safe_filename=original.safe_filename,
        tipo=original.tipo,
        descripcion=original.descripcion,
        fecha_adjunto=original.fecha_adjunto,
        content_type=original.content_type,
        detected_format=original.detected_format,
        extension=original.extension,
        byte_count=0,
        sha256="",
        download_status="not_downloaded",
        outcome="download_failed",
        role_tag=original.role_tag,
        evidence_attachment_id=None,
        extraction=None,
        error_message="fixture download failure",
    )
    failed_bundle = TenderAttachmentBundle(
        tender_id=positive.tender_id,
        source_kind=positive.source_kind,
        listing_page_count=positive.listing_page_count,
        attachments_discovered=1,
        attachments_downloaded=0,
        attachments=(failed,),
        chunks=positive.chunks,
        bytes_downloaded=0,
        incomplete_reason_codes=("download_failed",),
        semantic_digest=bundle_semantic_digest(
            tender_id=positive.tender_id,
            attachments=(failed,),
            chunks=positive.chunks,
            incomplete_reason_codes=("download_failed",),
        ),
        outcome_counts={"download_failed": 1},
    )
    _write_bundles(root, (failed_bundle,))

    with pytest.raises(AnexoBundleValidationError, match="cannot carry"):
        load_verified_anexo_evidence(root)


def test_verified_bundle_rejects_casefold_colliding_tender_ids(
    tmp_path: Path,
) -> None:
    root = tmp_path / "evidence"
    _write_bundles(
        root,
        (
            _make_bundle("CASE-1-LE26"),
            _make_bundle("case-1-le26"),
        ),
    )

    with pytest.raises(
        AnexoBundleValidationError,
        match="case-insensitive duplicate tender identities",
    ):
        load_verified_anexo_evidence(root)


def test_verified_bundle_rejects_archive_members_on_non_archive_attachment(
    tmp_path: Path,
) -> None:
    root = tmp_path / "evidence"
    _write_valid_bundle(root)
    path = root / "attachment_manifest.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    attachment = payload["tenders"][0]["attachments"][0]
    member_path = "inside.txt"
    attachment["archive_members"] = [
        ArchiveMemberRecord(
            member_id=hashlib.sha256(
                f"{attachment['attachment_id']}|{member_path}".encode()
            ).hexdigest()[:24],
            container_attachment_id=attachment["attachment_id"],
            tender_id=attachment["tender_id"],
            member_path=member_path,
            member_sha256="b" * 64,
            byte_count=10,
            depth=1,
            detected_format="txt",
            outcome="extracted_empty",
        ).to_dict()
    ]
    _dump(path, payload)

    with pytest.raises(AnexoBundleValidationError, match="non-archive attachment"):
        load_verified_anexo_evidence(root)


def test_verified_bundle_rejects_direct_chunk_on_archive_container(
    tmp_path: Path,
) -> None:
    root = tmp_path / "evidence"
    _write_valid_bundle(root)
    attachment_path = root / "attachment_manifest.json"
    attachment_payload = json.loads(attachment_path.read_text(encoding="utf-8"))
    attachment = attachment_payload["tenders"][0]["attachments"][0]
    member_path = "inside.txt"
    member = ArchiveMemberRecord(
        member_id=hashlib.sha256(
            f"{attachment['attachment_id']}|{member_path}".encode()
        ).hexdigest()[:24],
        container_attachment_id=attachment["attachment_id"],
        tender_id=attachment["tender_id"],
        member_path=member_path,
        member_sha256="b" * 64,
        byte_count=10,
        depth=1,
        detected_format="txt",
        outcome="extraction_success",
    ).to_dict()
    attachment["detected_format"] = "zip"
    attachment["extension"] = "zip"
    attachment["extraction"]["detected_format"] = "zip"
    attachment["extraction"]["extractor"] = "archive_expand"
    attachment["archive_members"] = [member]
    _dump(attachment_path, attachment_payload)
    extraction_path = root / "extraction_manifest.json"
    extraction_payload = json.loads(extraction_path.read_text(encoding="utf-8"))
    extraction = extraction_payload["tenders"][0]["extractions"][0]
    extraction["extraction"] = attachment["extraction"]
    extraction["archive_members"] = [member]
    _dump(extraction_path, extraction_payload)

    with pytest.raises(AnexoBundleValidationError, match="carries direct chunk"):
        load_verified_anexo_evidence(root)


@pytest.mark.parametrize(
    "unsafe_token",
    [
        "ticket=<redacted>still-secret",
        "ticket=<redacted><redacted>",
        "ticket=<redacted><secret",
        "ticket=<redacted>\\SECRET",
    ],
)
def test_verified_bundle_rejects_redaction_marker_with_secret_suffix(
    tmp_path: Path,
    unsafe_token: str,
) -> None:
    root = tmp_path / "evidence"
    _write_valid_bundle(root)
    path = root / "evidence_chunks.jsonl"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["text"] += f" {unsafe_token}"
    path.write_text(json.dumps(payload) + "\n", encoding="utf-8")

    with pytest.raises(AnexoBundleValidationError, match="portal token"):
        load_verified_anexo_evidence(root)


def test_portal_token_redaction_is_idempotent_and_exact() -> None:
    raw = "https://portal.invalid/a?ticket=opaque&enc=second"
    redacted = redact_portal_tokens(raw)

    assert redacted == (
        "https://portal.invalid/a?ticket=<redacted>&enc=<redacted>"
    )
    assert redact_portal_tokens(redacted) == redacted


def test_verified_bundle_rejects_non_finite_json_numbers(tmp_path: Path) -> None:
    root = tmp_path / "evidence"
    _write_valid_bundle(root)
    path = root / "summary.json"
    payload = path.read_text(encoding="utf-8").replace(
        '"tenders": 1', '"tenders": NaN'
    )
    path.write_text(payload, encoding="utf-8")

    with pytest.raises(AnexoBundleValidationError, match="non-finite JSON number"):
        load_verified_anexo_evidence(root)


@pytest.mark.parametrize("artifact", ["attachment_manifest", "evidence_chunks"])
def test_verified_bundle_rejects_unsigned_unknown_fields(
    tmp_path: Path,
    artifact: str,
) -> None:
    root = tmp_path / "evidence"
    _write_valid_bundle(root)
    if artifact == "attachment_manifest":
        path = root / "attachment_manifest.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["tenders"][0]["attachments"][0][
            "classification_override"
        ] = "strong_equipment_class"
        _dump(path, payload)
    else:
        path = root / "evidence_chunks.jsonl"
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["classification_override"] = "strong_equipment_class"
        path.write_text(json.dumps(payload) + "\n", encoding="utf-8")

    with pytest.raises(AnexoBundleValidationError, match="unknown keys"):
        load_verified_anexo_evidence(root)


@pytest.mark.parametrize(
    "items",
    [
        [("empty.txt", "text/plain", b"")],
        [
            ("one.csv", "text/csv", make_csv(3)),
            ("two.txt", "text/plain", make_csv(3)),
        ],
        [
            (
                "archive.zip",
                "application/zip",
                make_zip([("literal::name.txt", b"centrifuga de laboratorio")]),
            )
        ],
        [
            (
                "ambiguous-prefixes.zip",
                "application/zip",
                make_zip(
                    [
                        ("a.zip", make_zip([])),
                        (
                            "a.zip::b.zip",
                            make_zip([("c.txt", b"centrifuga de laboratorio")]),
                        ),
                    ]
                ),
            )
        ],
        [
            (
                "literal-prefix.zip",
                "application/zip",
                make_zip(
                    [
                        ("plain", b"centrifuga de laboratorio"),
                        (
                            "plain::b.zip",
                            make_zip([("c.txt", b"centrifuga de laboratorio")]),
                        ),
                    ]
                ),
            )
        ],
    ],
)
def test_verified_loader_accepts_canonical_e1_writer_round_trip(
    tmp_path: Path,
    items: list[tuple[str, str, bytes]],
) -> None:
    email_root = tmp_path / "email-pipeline"
    (email_root / "reports" / "out").mkdir(parents=True)
    bundle = build_tender_bundle(
        "1234-4-LE26",
        LocalAttachmentSource(items=items),
    )
    out_dir = email_root / "reports" / "out" / "evidence"
    write_evidence_outputs(
        [bundle],
        out_dir,
        repo_email_pipeline_root=email_root,
        require_git_ignored=False,
    )

    verified = load_verified_anexo_evidence(out_dir)

    assert verified.tender_semantic_digests == {
        bundle.tender_id: bundle.semantic_digest
    }
