"""Actual-expansion safety for OOXML containers.

``preflight_archive`` can only trust the ZIP central directory. A member that
understates its size passes that check, and the parsers then issue their own
unbounded reads — measured at ~161 MB (XLSX) and ~134 MB (DOCX) peak from an
~80 KB container before this layer existed. These tests pin the real
decompressed bounds instead of the declared ones.
"""

from __future__ import annotations

import gc
import io
import sys
import tracemalloc
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent / "fixtures"))

from chilecompra_anexo_evidence.synthetic_documents import (  # noqa: E402
    forge_member_header,
    make_docx,
    make_understated_ooxml,
    make_xlsx,
    make_zip,
    replace_member,
)
from origenlab_email_pipeline.chilecompra_anexo_evidence import (  # noqa: E402
    EvidenceBuildConfig,
    LocalAttachmentSource,
    build_tender_bundle,
)
from origenlab_email_pipeline.chilecompra_anexo_evidence.archive import (  # noqa: E402
    ArchiveLimits,
    preflight_archive,
    verify_actual_expansion,
)
from origenlab_email_pipeline.chilecompra_anexo_evidence.constants import (  # noqa: E402
    OUTCOME_EXTRACTION_SUCCESS,
    OUTCOME_PARTIAL_DUE_TO_SAFETY_LIMIT,
    REASON_ARCHIVE_ACTUAL_MEMBER_BYTES_LIMIT,
    REASON_ARCHIVE_ACTUAL_RATIO_LIMIT,
    REASON_ARCHIVE_ACTUAL_SIZE_MISMATCH,
    REASON_ARCHIVE_ACTUAL_TOTAL_BYTES_LIMIT,
    REASON_ARCHIVE_SUSPICIOUS_MEMBER,
    REASON_OOXML_CONTAINER_UNSAFE,
)
from origenlab_email_pipeline.chilecompra_anexo_evidence.extract import (  # noqa: E402
    extract_payload,
)

_SPEC = "ESPECTROFOTOMETRO UV VIS"
_SHEET = "xl/worksheets/sheet1.xml"


def _plain_xlsx() -> bytes:
    return make_xlsx(["S1"], cells={"S1": [(1, 1, _SPEC)]})


def _plain_docx() -> bytes:
    return make_docx(
        paragraph="parrafo",
        table_rows=[["Equipo", "Cantidad"], [_SPEC, "2"]],
        header="ENCABEZADO",
        footer="PIE",
    )


def _no_openpyxl(monkeypatch) -> None:
    openpyxl = pytest.importorskip("openpyxl")

    def _boom(*args, **kwargs):
        raise AssertionError("openpyxl must not open an unverified container")

    monkeypatch.setattr(openpyxl, "load_workbook", _boom)


# --- A: understated member ----------------------------------------------------


def test_understated_xlsx_member_is_rejected_before_openpyxl(monkeypatch) -> None:
    payload = make_understated_ooxml(_plain_xlsx(), _SHEET, real_size=400_000)
    limits = ArchiveLimits(max_member_uncompressed_bytes=50_000)

    # The lie survives the declared preflight; only actual expansion exposes it.
    assert preflight_archive(payload, limits=limits).rejected is False

    _no_openpyxl(monkeypatch)
    output = extract_payload(payload, detected_format="xlsx", archive_limits=limits)
    assert output.outcome == OUTCOME_PARTIAL_DUE_TO_SAFETY_LIMIT
    assert REASON_OOXML_CONTAINER_UNSAFE in output.warnings
    assert REASON_ARCHIVE_ACTUAL_MEMBER_BYTES_LIMIT in output.warnings
    assert output.chunks == []


def test_understated_member_is_reported_as_a_size_mismatch() -> None:
    payload = make_understated_ooxml(_plain_xlsx(), _SHEET, real_size=120_000)
    report = verify_actual_expansion(payload, limits=ArchiveLimits())
    assert report.rejected is True
    assert REASON_ARCHIVE_ACTUAL_SIZE_MISMATCH in report.reason_codes
    assert report.offending_member == _SHEET
    assert report.largest_member_bytes > 100_000


# --- B: aggregate actual bytes ------------------------------------------------


def test_actual_aggregate_bytes_exceed_total_budget_before_openpyxl(monkeypatch) -> None:
    base = _plain_xlsx()
    with zipfile.ZipFile(io.BytesIO(base)) as archive:
        names = [n for n in archive.namelist() if n.endswith(".xml")][:4]
    payload = base
    for name in names:
        payload = replace_member(payload, name, b"<x>" + b"B" * 60_000 + b"</x>")

    # Each member fits the per-member cap; only the sum breaches the budget.
    limits = ArchiveLimits(
        max_member_uncompressed_bytes=100_000,
        max_total_uncompressed_bytes=120_000,
        max_compression_ratio=10_000.0,
    )
    report = verify_actual_expansion(payload, limits=limits)
    assert report.rejected is True
    assert REASON_ARCHIVE_ACTUAL_TOTAL_BYTES_LIMIT in report.reason_codes
    assert report.largest_member_bytes <= 100_000

    _no_openpyxl(monkeypatch)
    output = extract_payload(payload, detected_format="xlsx", archive_limits=limits)
    assert output.outcome == OUTCOME_PARTIAL_DUE_TO_SAFETY_LIMIT


# --- C: actual ratio ----------------------------------------------------------


def test_actual_ratio_violation_is_caught_when_declared_ratio_looks_fine(
    monkeypatch,
) -> None:
    payload = make_understated_ooxml(_plain_xlsx(), _SHEET, real_size=300_000)
    limits = ArchiveLimits(
        max_compression_ratio=20.0,
        max_member_uncompressed_bytes=10_000_000,
        max_total_uncompressed_bytes=10_000_000,
    )
    declared = preflight_archive(payload, limits=limits)
    assert declared.rejected is False, "declared ratio must look acceptable"
    assert declared.worst_member_ratio <= limits.max_compression_ratio

    report = verify_actual_expansion(payload, limits=limits)
    assert report.rejected is True
    assert REASON_ARCHIVE_ACTUAL_RATIO_LIMIT in report.reason_codes
    assert report.worst_actual_ratio > limits.max_compression_ratio

    _no_openpyxl(monkeypatch)
    assert (
        extract_payload(payload, detected_format="xlsx", archive_limits=limits).outcome
        == OUTCOME_PARTIAL_DUE_TO_SAFETY_LIMIT
    )


# --- D: zero compressed size --------------------------------------------------


def test_member_declaring_zero_compressed_size_but_producing_bytes_fails_closed() -> None:
    payload = forge_member_header(_plain_xlsx(), _SHEET, declared_compressed=0)
    report = verify_actual_expansion(payload, limits=ArchiveLimits())
    assert report.rejected is True
    assert {
        REASON_ARCHIVE_SUSPICIOUS_MEMBER,
        REASON_ARCHIVE_ACTUAL_SIZE_MISMATCH,
    } & set(report.reason_codes)
    assert (
        extract_payload(payload, detected_format="xlsx").outcome
        == OUTCOME_PARTIAL_DUE_TO_SAFETY_LIMIT
    )


# --- E/F: normal documents unaffected -----------------------------------------


def test_normal_xlsx_passes_actual_verification_and_still_extracts() -> None:
    payload = _plain_xlsx()
    report = verify_actual_expansion(payload, limits=ArchiveLimits())
    assert report.rejected is False
    assert report.members_verified > 0
    assert report.actual_total_bytes > 0
    output = extract_payload(payload, detected_format="xlsx")
    assert output.outcome == OUTCOME_EXTRACTION_SUCCESS
    assert any(_SPEC in c.text for c in output.chunks)


def test_realistic_workbook_with_deep_evidence_still_extracts() -> None:
    payload = make_xlsx(
        ["S1", "S2", "S3", "S4", "S5", "Oculta"],
        cells={
            "S5": [(150, 15, _SPEC)],
            "Oculta": [(2, 2, "BANO TERMOSTATICO")],
            "S1": [(1, 1, "resumen")],
        },
        hidden_sheets=("Oculta",),
    )
    assert verify_actual_expansion(payload, limits=ArchiveLimits()).rejected is False
    output = extract_payload(payload, detected_format="xlsx")
    assert output.outcome == OUTCOME_EXTRACTION_SUCCESS
    assert output.sheet_count == 6
    joined = "\n".join(c.text for c in output.chunks)
    assert _SPEC in joined, "evidence past row 100 and column 12 must survive"
    assert "BANO TERMOSTATICO" in joined, "hidden sheet must survive"


def test_normal_docx_passes_actual_verification_and_still_extracts() -> None:
    payload = _plain_docx()
    assert verify_actual_expansion(payload, limits=ArchiveLimits()).rejected is False
    output = extract_payload(payload, detected_format="docx")
    assert output.outcome == OUTCOME_EXTRACTION_SUCCESS
    joined = "\n".join(c.text for c in output.chunks)
    for expected in ("parrafo", _SPEC, "ENCABEZADO", "PIE"):
        assert expected in joined


# --- Phase 5: DOCX shares the same model --------------------------------------


@pytest.mark.parametrize(
    ("member", "kind"),
    [("word/document.xml", "docx"), ("word/header1.xml", "docx")],
)
def test_understated_docx_part_is_rejected_like_xlsx(member, kind) -> None:
    payload = make_understated_ooxml(_plain_docx(), member, real_size=2_000_000)
    # Sit the cap just above the largest honest part so the declared preflight
    # has no unrelated reason to reject; only real expansion can catch this.
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        honest_max = max(i.file_size for i in archive.infolist() if not i.is_dir())
    limits = ArchiveLimits(max_member_uncompressed_bytes=honest_max + 1_000)
    assert preflight_archive(payload, limits=limits).rejected is False

    output = extract_payload(payload, detected_format=kind, archive_limits=limits)
    assert output.outcome == OUTCOME_PARTIAL_DUE_TO_SAFETY_LIMIT
    assert REASON_ARCHIVE_ACTUAL_MEMBER_BYTES_LIMIT in output.warnings


# --- G: memory bound ----------------------------------------------------------


def _peak_bytes(call) -> int:
    gc.collect()
    tracemalloc.start()
    try:
        call()
    finally:
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
    return peak


def test_verifier_does_not_hold_the_expanded_member_in_memory() -> None:
    """Verifier memory must track the chunk size, not the expanded member."""
    expanded = 40_000_000
    payload = make_understated_ooxml(_plain_xlsx(), _SHEET, real_size=expanded)
    limits = ArchiveLimits(
        max_member_uncompressed_bytes=expanded * 2,
        max_total_uncompressed_bytes=expanded * 4,
        max_compression_ratio=10_000_000.0,
    )
    report = verify_actual_expansion(payload, limits=limits)
    assert report.largest_member_bytes >= expanded, "must really walk the whole stream"

    peak = _peak_bytes(lambda: verify_actual_expansion(payload, limits=limits))
    assert peak < 4_000_000, (
        f"verifier peaked at {peak} bytes while expanding {expanded} bytes"
    )


@pytest.mark.parametrize(
    ("member", "kind"),
    [(_SHEET, "xlsx"), ("word/document.xml", "docx")],
)
def test_understated_member_no_longer_spikes_parser_memory(member, kind) -> None:
    base = _plain_xlsx() if kind == "xlsx" else _plain_docx()
    payload = make_understated_ooxml(base, member, real_size=60_000_000)
    limits = ArchiveLimits(max_member_uncompressed_bytes=1_000_000)

    peak = _peak_bytes(
        lambda: extract_payload(payload, detected_format=kind, archive_limits=limits)
    )
    assert peak < 8_000_000, f"{kind} peaked at {peak} bytes on a forged container"
    assert (
        extract_payload(payload, detected_format=kind, archive_limits=limits).outcome
        == OUTCOME_PARTIAL_DUE_TO_SAFETY_LIMIT
    )


def test_understated_generic_zip_member_keeps_bundle_incomplete() -> None:
    member_name = "specs.txt"
    payload = forge_member_header(
        make_zip([(member_name, (_SPEC + "\n").encode() * 20_000)]),
        member_name,
        declared_size=32,
    )
    limits = ArchiveLimits(
        max_member_uncompressed_bytes=50_000,
        max_total_uncompressed_bytes=1_000_000,
        max_compression_ratio=10_000.0,
    )
    assert preflight_archive(payload, limits=limits).rejected is False

    bundle = build_tender_bundle(
        "T-ZIP-ACTUAL",
        LocalAttachmentSource(items=[("anexos.zip", "application/zip", payload)]),
        config=EvidenceBuildConfig(archive_limits=limits),
    )

    record = bundle.attachments[0]
    assert record.outcome == OUTCOME_PARTIAL_DUE_TO_SAFETY_LIMIT
    assert record.archive_members == ()
    assert REASON_ARCHIVE_ACTUAL_MEMBER_BYTES_LIMIT in record.extraction.warnings
    assert REASON_ARCHIVE_ACTUAL_MEMBER_BYTES_LIMIT in bundle.incomplete_reason_codes
    assert bundle.chunks == ()
    assert bundle.bundle_complete is False


def test_bounded_part_read_does_not_spike_on_an_accepted_container() -> None:
    """Chunked part reads keep peak near the cap, not a multiple of it."""
    body = b"<w:t>" + b"C" * 8_000_000 + b"</w:t>"
    payload = replace_member(_plain_docx(), "word/document.xml", body)
    limits = ArchiveLimits(
        max_member_uncompressed_bytes=16_000_000,
        max_total_uncompressed_bytes=64_000_000,
        max_compression_ratio=10_000_000.0,
    )
    assert verify_actual_expansion(payload, limits=limits).rejected is False
    peak = _peak_bytes(
        lambda: extract_payload(payload, detected_format="docx", archive_limits=limits)
    )
    assert peak < 60_000_000, f"accepted-path peak {peak} is disproportionate"
