"""Production-publication contract tests for ANEXO-T1 tender terms.

All source bundles are local in-memory evidence. These tests perform no
ChileCompra or Mercado Público network acquisition and no database writes.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from origenlab_email_pipeline.chilecompra_anexo_evidence.models import (
    AttachmentExtraction,
    AttachmentRecord,
    EvidenceChunk,
    TenderAttachmentBundle,
    sha256_text,
)
from origenlab_email_pipeline.commercial_procurement_anexo_tender_terms import (
    TENDER_TERMS_PUBLICATION_CONTRACT_VERSION,
    TenderTermsPublicationError,
    load_published_tender_terms,
    publish_tender_terms,
)
from origenlab_email_pipeline.commercial_procurement_anexo_tender_terms import (
    production_publish as publication,
)


_AS_OF = "2026-08-15T17:30:14Z"

_BUDGET_TEXT = (
    "Artículo 5°: Datos de la licitación. "
    "Presupuesto Disponible $ 28.419.400.- CLP impuestos incluidos"
)


def _bundle(
    tender_id: str,
    *texts: str,
    incomplete_reason_codes: tuple[str, ...] = (),
) -> TenderAttachmentBundle:
    """Build the same real T1 evidence shape used by the extractor tests."""

    attachment_id = f"att-{tender_id}"

    chunks = tuple(
        EvidenceChunk(
            chunk_id=f"chunk-{tender_id}-{index:02d}",
            attachment_id=attachment_id,
            tender_id=tender_id,
            source_format="pdf",
            locator_type="pdf_page",
            locator={
                "page": index,
                "page_count": max(len(texts), 1),
                "part": 1,
            },
            text=text,
            text_sha256=sha256_text(text),
            char_count=len(text),
            ordinal=index,
        )
        for index, text in enumerate(texts, start=1)
    )

    record = AttachmentRecord(
        attachment_id=attachment_id,
        tender_id=tender_id,
        listing_ordinal=1,
        row_ordinal=1,
        portal_filename="bases.pdf",
        safe_filename="bases.pdf",
        tipo="Bases",
        descripcion="Bases administrativas y técnicas",
        fecha_adjunto="2026-08-15",
        content_type="application/pdf",
        detected_format="pdf",
        extension="pdf",
        byte_count=10_000,
        sha256="a" * 64,
        download_status="downloaded",
        outcome="extraction_success",
        role_tag="administrative_basis",
        evidence_attachment_id=attachment_id,
        extraction=AttachmentExtraction(
            outcome="extraction_success",
            detected_format="pdf",
            chunk_count=len(chunks),
            char_count=sum(len(text) for text in texts),
            page_count=max(len(texts), 1),
            extractor="test",
        ),
    )

    return TenderAttachmentBundle(
        tender_id=tender_id,
        source_kind="test",
        listing_page_count=1,
        attachments_discovered=1,
        attachments_downloaded=1,
        attachments=(record,),
        chunks=chunks,
        bytes_downloaded=10_000,
        incomplete_reason_codes=incomplete_reason_codes,
        semantic_digest=f"source-digest-{tender_id}",
        outcome_counts={"extraction_success": 1},
    )


def _publication_dir(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    name: str = "tender_terms",
) -> Path:
    """Give output_safety a synthetic reports/out root with no Git dependency."""

    email_pipeline_root = tmp_path / "email-pipeline"
    reports_out = email_pipeline_root / "reports" / "out"
    reports_out.mkdir(parents=True)

    monkeypatch.setattr(
        publication,
        "_email_pipeline_root",
        lambda: email_pipeline_root,
    )
    return reports_out / name


def _tree_bytes(path: Path) -> dict[str, bytes]:
    return {
        str(child.relative_to(path)): child.read_bytes()
        for child in sorted(path.rglob("*"))
        if child.is_file()
    }


def _copy_publication(source: Path, target: Path) -> None:
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(source, target)


def _rewrite_summary(path: Path, mutator) -> None:
    summary_path = path / publication.SUMMARY_FILENAME
    data = json.loads(summary_path.read_text(encoding="utf-8"))
    mutator(data)
    summary_path.write_text(
        json.dumps(data, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _rewrite_first_row(path: Path, mutator) -> None:
    terms_path = path / publication.TENDER_TERMS_FILENAME
    lines = terms_path.read_text(encoding="utf-8").splitlines()
    assert lines

    row = json.loads(lines[0])
    mutator(row)
    lines[0] = json.dumps(
        row,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
    terms_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_publish_is_deterministic_and_sorted_independent_of_input_order(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = _bundle("2000-2-LE26", _BUDGET_TEXT)
    second = _bundle("1000-1-LE26", _BUDGET_TEXT)

    out_a = _publication_dir(tmp_path, monkeypatch, "tender_terms_a")
    out_b = out_a.parent / "tender_terms_b"

    result_a = publish_tender_terms(
        bundles=[first, second],
        as_of_utc=_AS_OF,
        out_dir=out_a,
        require_git_ignored=False,
    )
    result_b = publish_tender_terms(
        bundles=[second, first],
        as_of_utc=_AS_OF,
        out_dir=out_b,
        require_git_ignored=False,
    )

    assert result_a.result == "applied"
    assert result_b.result == "applied"
    assert result_a.publication_digest == result_b.publication_digest
    assert _tree_bytes(out_a) == _tree_bytes(out_b)

    loaded = load_published_tender_terms(out_a)
    assert [row["tender_id"] for row in loaded["tender_terms"]] == [
        "1000-1-LE26",
        "2000-2-LE26",
    ]


def test_real_t1_fact_and_provenance_survive_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    out_dir = _publication_dir(tmp_path, monkeypatch)
    source = _bundle("1057890-1-LE26", _BUDGET_TEXT)

    result = publish_tender_terms(
        bundles=[source],
        as_of_utc=_AS_OF,
        out_dir=out_dir,
        require_git_ignored=False,
    )

    assert result.result == "applied"

    loaded = load_published_tender_terms(out_dir)
    row = loaded["tender_terms"][0]

    budget = next(
        fact for fact in row["tender_facts"] if fact["field_name"] == "total_budget"
    )

    assert budget["state"] == "explicit"
    assert budget["value"] == 28_419_400
    assert budget["currency"] == "CLP"
    assert budget["tax_basis"] == "taxes_included"

    evidence = budget["evidence"][0]
    assert evidence["attachment_id"] == "att-1057890-1-LE26"
    assert evidence["safe_filename"] == "bases.pdf"
    assert evidence["attachment_sha256"] == "a" * 64
    assert evidence["chunk_id"] == "chunk-1057890-1-LE26-01"
    assert evidence["locator_type"] == "pdf_page"
    assert evidence["locator"]["page"] == 1
    assert evidence["locator_display"]
    assert "Presupuesto Disponible" in evidence["evidence_excerpt"]
    assert evidence["evidence_text_sha256"]

    assert row["contact_authorization"] is False
    assert row["outreach_authorization"] is False
    assert row["production_queue_mutated"] is False
    assert row["persisted"] is False


def test_complete_and_incomplete_coverage_counts_reconcile(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    out_dir = _publication_dir(tmp_path, monkeypatch)

    complete = _bundle("1000-1-LE26", _BUDGET_TEXT)
    incomplete = _bundle(
        "2000-2-LE26",
        "Documento parcial sin términos reconocibles.",
        incomplete_reason_codes=("archive_member_needs_ocr",),
    )

    result = publish_tender_terms(
        bundles=[complete, incomplete],
        as_of_utc=_AS_OF,
        out_dir=out_dir,
        require_git_ignored=False,
    )

    assert result.result == "applied"
    assert result.tender_count == 2
    assert result.complete_coverage_count == 1
    assert result.incomplete_coverage_count == 1

    loaded = load_published_tender_terms(out_dir)
    summary = loaded["summary"]
    assert summary["tender_count"] == 2
    assert summary["complete_coverage_count"] == 1
    assert summary["incomplete_coverage_count"] == 1


def test_every_serialized_tender_remains_non_authorizing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    out_dir = _publication_dir(tmp_path, monkeypatch)

    result = publish_tender_terms(
        bundles=[
            _bundle("1000-1-LE26", _BUDGET_TEXT),
            _bundle("2000-2-LE26", _BUDGET_TEXT),
        ],
        as_of_utc=_AS_OF,
        out_dir=out_dir,
        require_git_ignored=False,
    )
    assert result.result == "applied"

    loaded = load_published_tender_terms(out_dir)

    assert loaded["summary"]["contact_authorization"] is False
    assert loaded["summary"]["outreach_authorization"] is False
    assert loaded["summary"]["production_queue_mutated"] is False
    assert loaded["summary"]["persisted"] is False

    for row in loaded["tender_terms"]:
        assert row["contact_authorization"] is False
        assert row["outreach_authorization"] is False
        assert row["production_queue_mutated"] is False
        assert row["persisted"] is False


def test_duplicate_source_tender_id_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    out_dir = _publication_dir(tmp_path, monkeypatch)
    source = _bundle("1000-1-LE26", _BUDGET_TEXT)

    result = publish_tender_terms(
        bundles=[source, source],
        as_of_utc=_AS_OF,
        out_dir=out_dir,
        require_git_ignored=False,
    )

    assert result.result == "failed"
    assert result.error is not None
    assert "duplicate tender_id" in result.error
    assert not out_dir.exists()


def test_loader_rejects_malformed_jsonl(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    out_dir = _publication_dir(tmp_path, monkeypatch)

    assert (
        publish_tender_terms(
            bundles=[_bundle("1000-1-LE26", _BUDGET_TEXT)],
            as_of_utc=_AS_OF,
            out_dir=out_dir,
            require_git_ignored=False,
        ).result
        == "applied"
    )

    (out_dir / publication.TENDER_TERMS_FILENAME).write_text(
        "{not-json\n",
        encoding="utf-8",
    )

    with pytest.raises(
        TenderTermsPublicationError,
        match="malformed JSON",
    ):
        load_published_tender_terms(out_dir)


def test_loader_rejects_unsupported_publication_contract(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    out_dir = _publication_dir(tmp_path, monkeypatch)

    assert (
        publish_tender_terms(
            bundles=[_bundle("1000-1-LE26", _BUDGET_TEXT)],
            as_of_utc=_AS_OF,
            out_dir=out_dir,
            require_git_ignored=False,
        ).result
        == "applied"
    )

    _rewrite_summary(
        out_dir,
        lambda summary: summary.__setitem__(
            "contract_version",
            "tender_terms_publication_v999",
        ),
    )

    with pytest.raises(
        TenderTermsPublicationError,
        match="unsupported publication contract",
    ):
        load_published_tender_terms(out_dir)


@pytest.mark.parametrize(
    "missing_filename",
    [
        publication.SUMMARY_FILENAME,
        publication.TENDER_TERMS_FILENAME,
    ],
)
def test_loader_rejects_missing_required_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    missing_filename: str,
) -> None:
    out_dir = _publication_dir(tmp_path, monkeypatch)

    assert (
        publish_tender_terms(
            bundles=[_bundle("1000-1-LE26", _BUDGET_TEXT)],
            as_of_utc=_AS_OF,
            out_dir=out_dir,
            require_git_ignored=False,
        ).result
        == "applied"
    )

    (out_dir / missing_filename).unlink()

    with pytest.raises(
        TenderTermsPublicationError,
        match="missing",
    ):
        load_published_tender_terms(out_dir)


def test_loader_rejects_tender_semantic_digest_tampering(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    out_dir = _publication_dir(tmp_path, monkeypatch)

    assert (
        publish_tender_terms(
            bundles=[_bundle("1000-1-LE26", _BUDGET_TEXT)],
            as_of_utc=_AS_OF,
            out_dir=out_dir,
            require_git_ignored=False,
        ).result
        == "applied"
    )

    _rewrite_first_row(
        out_dir,
        lambda row: row.__setitem__("semantic_digest", "0" * 64),
    )

    with pytest.raises(
        TenderTermsPublicationError,
        match="semantic digest mismatch",
    ):
        load_published_tender_terms(out_dir)


def test_failed_staged_validation_preserves_prior_canonical_bundle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    out_dir = _publication_dir(tmp_path, monkeypatch)

    seed = publish_tender_terms(
        bundles=[_bundle("1000-1-LE26", _BUDGET_TEXT)],
        as_of_utc=_AS_OF,
        out_dir=out_dir,
        require_git_ignored=False,
    )
    assert seed.result == "applied"

    before = _tree_bytes(out_dir)
    original_writer = publication._write_publication_files

    def _write_then_corrupt(
        staged: Path,
        *,
        bundles,
        as_of_utc: str,
    ):
        written = original_writer(
            staged,
            bundles=bundles,
            as_of_utc=as_of_utc,
        )
        (staged / publication.TENDER_TERMS_FILENAME).write_text(
            "{not-json\n",
            encoding="utf-8",
        )
        return written

    monkeypatch.setattr(
        publication,
        "_write_publication_files",
        _write_then_corrupt,
    )

    failed = publish_tender_terms(
        bundles=[_bundle("2000-2-LE26", _BUDGET_TEXT)],
        as_of_utc=_AS_OF,
        out_dir=out_dir,
        require_git_ignored=False,
    )

    assert failed.result == "failed"
    assert _tree_bytes(out_dir) == before

    loaded = load_published_tender_terms(out_dir)
    assert [row["tender_id"] for row in loaded["tender_terms"]] == ["1000-1-LE26"]


def test_successful_publish_replaces_prior_canonical_bundle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    out_dir = _publication_dir(tmp_path, monkeypatch)

    first = publish_tender_terms(
        bundles=[_bundle("1000-1-LE26", _BUDGET_TEXT)],
        as_of_utc=_AS_OF,
        out_dir=out_dir,
        require_git_ignored=False,
    )
    assert first.result == "applied"
    before = _tree_bytes(out_dir)

    second = publish_tender_terms(
        bundles=[_bundle("2000-2-LE26", _BUDGET_TEXT)],
        as_of_utc=_AS_OF,
        out_dir=out_dir,
        require_git_ignored=False,
    )

    assert second.result == "applied"
    assert _tree_bytes(out_dir) != before

    loaded = load_published_tender_terms(out_dir)
    assert [row["tender_id"] for row in loaded["tender_terms"]] == ["2000-2-LE26"]


def test_empty_publication_is_valid_empty(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    out_dir = _publication_dir(tmp_path, monkeypatch)

    result = publish_tender_terms(
        bundles=[],
        as_of_utc=_AS_OF,
        out_dir=out_dir,
        require_git_ignored=False,
    )

    assert result.result == "applied"
    assert result.tender_count == 0
    assert result.complete_coverage_count == 0
    assert result.incomplete_coverage_count == 0

    loaded = load_published_tender_terms(out_dir)
    assert loaded["tender_terms"] == []
    assert loaded["summary"]["tender_count"] == 0
    assert (out_dir / publication.TENDER_TERMS_FILENAME).read_bytes() == b""


def test_portal_token_in_serialized_evidence_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    out_dir = _publication_dir(tmp_path, monkeypatch)

    source = _bundle(
        "1000-1-LE26",
        (
            "Presupuesto Disponible "
            "qs=opaque-secret-token "
            "$ 28.419.400.- CLP impuestos incluidos"
        ),
    )

    result = publish_tender_terms(
        bundles=[source],
        as_of_utc=_AS_OF,
        out_dir=out_dir,
        require_git_ignored=False,
    )

    assert result.result == "failed"
    assert not out_dir.exists()
    assert result.error is not None
    assert "opaque-secret-token" not in result.error


def test_publication_contract_version_is_the_expected_v1_token() -> None:
    assert TENDER_TERMS_PUBLICATION_CONTRACT_VERSION == "tender_terms_publication_v1"
