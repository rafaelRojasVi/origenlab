"""Operator-supplied complete attachment bundle: bounded ZIP import + provenance.

Covers the smallest clean acquisition-source abstraction: the existing
partial legacy HTTP scraper, a bounded operator ZIP importer (new), and a
future-API Protocol seam with no implementation. None of this touches
Mercado Público's reCAPTCHA-gated ``ViewAttachment.aspx`` flow -- every test
here reads bytes that were already placed on local disk, with zero network
I/O and zero browser automation.
"""

from __future__ import annotations

import ast
import hashlib
import io
import json
import stat
import sys
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent / "fixtures"))

from chilecompra_anexo_evidence.synthetic_documents import (  # noqa: E402
    make_csv,
    make_encrypted_member_zip,
    make_pdf,
    make_traversal_zip,
    make_truncated_zip,
    make_zip,
    make_zip_bomb,
)
from origenlab_email_pipeline.chilecompra_anexo_evidence.acquire import (  # noqa: E402
    AcquiredAttachment,
    AttachmentStub,
    SourceInventory,
)
from origenlab_email_pipeline.chilecompra_anexo_evidence.acquisition_provenance import (  # noqa: E402
    build_acquisition_provenance,
    is_operator_supplied,
    resolve_completeness_state,
)
from origenlab_email_pipeline.chilecompra_anexo_evidence.archive import ArchiveLimits  # noqa: E402
from origenlab_email_pipeline.chilecompra_anexo_evidence.constants import (  # noqa: E402
    ACQUISITION_SOURCE_CHILECOMPRA_LEGACY_PARTIAL_HTTP,
    ACQUISITION_SOURCE_OPERATOR_COMPLETE_BUNDLE,
    COMPLETENESS_STATE_COMPLETE,
    COMPLETENESS_STATE_INCOMPLETE,
    COMPLETENESS_STATE_UNKNOWN,
    OUTCOME_EXTRACTION_SUCCESS,
    REASON_GATED_LISTING_UNREACHABLE,
)
from origenlab_email_pipeline.chilecompra_anexo_evidence.operator_import import (  # noqa: E402
    OperatorZipAttachmentSource,
    OperatorZipImportError,
    import_operator_zip,
)
from origenlab_email_pipeline.chilecompra_anexo_evidence.planner import build_tender_bundle  # noqa: E402
from origenlab_email_pipeline.chilecompra_anexo_evidence.acquire import LocalAttachmentSource  # noqa: E402
from origenlab_email_pipeline.commercial_procurement_anexo_tender_terms.extract import (  # noqa: E402
    extract_tender_terms,
)
from origenlab_email_pipeline.operator_cli.import_tender_annex_bundle import (  # noqa: E402
    ImportTenderAnnexBundleOptions,
    build_import_tender_annex_bundle_result,
    run_import_tender_annex_bundle,
)

_TENDER = "2410-66-LP26"


def _write_zip(tmp_path: Path, name: str, payload: bytes) -> Path:
    path = tmp_path / name
    path.write_bytes(payload)
    return path


def _symlink_zip() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        info = zipfile.ZipInfo("link.txt")
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        archive.writestr(info, "target.txt")
        archive.writestr("real.txt", b"contenido real")
    return buffer.getvalue()


# --- Happy path / safety bounds ----------------------------------------------


def test_safe_zip_happy_path(tmp_path: Path) -> None:
    pdf = make_pdf(pages=1)
    csv = make_csv(3)
    zip_path = _write_zip(
        tmp_path, "anexos.zip", make_zip([("tecnico.pdf", pdf), ("planilla.csv", csv)])
    )

    result = import_operator_zip(zip_path)

    assert result.rejected_entries == ()
    assert [e.original_filename for e in result.entries] == ["tecnico.pdf", "planilla.csv"]
    assert result.entries[0].sha256 == hashlib.sha256(pdf).hexdigest()
    assert result.entries[0].byte_count == len(pdf)
    assert result.entries[1].sha256 == hashlib.sha256(csv).hexdigest()


def test_zip_slip_entry_rejected_other_files_preserved(tmp_path: Path) -> None:
    zip_path = _write_zip(tmp_path, "traversal.zip", make_traversal_zip())

    result = import_operator_zip(zip_path)

    assert [e.original_filename for e in result.entries] == ["safe.txt"]
    assert len(result.rejected_entries) == 1
    assert "unsafe_member_path" in result.rejected_entries[0]
    assert "escape.txt" in result.rejected_entries[0]


def test_symlink_entry_rejected_other_files_preserved(tmp_path: Path) -> None:
    zip_path = _write_zip(tmp_path, "symlink.zip", _symlink_zip())

    result = import_operator_zip(zip_path)

    assert [e.original_filename for e in result.entries] == ["real.txt"]
    assert len(result.rejected_entries) == 1
    assert "symlink_entry_rejected" in result.rejected_entries[0]
    assert "link.txt" in result.rejected_entries[0]


def test_encrypted_entry_rejected_other_files_preserved(tmp_path: Path) -> None:
    zip_path = _write_zip(tmp_path, "encrypted.zip", make_encrypted_member_zip())

    result = import_operator_zip(zip_path)

    assert [e.original_filename for e in result.entries] == ["abierto.txt"]
    assert len(result.rejected_entries) == 1
    assert "encrypted_entry_rejected" in result.rejected_entries[0]
    assert "secreto.txt" in result.rejected_entries[0]


def test_oversized_individual_file_rejected(tmp_path: Path) -> None:
    zip_path = _write_zip(tmp_path, "big.zip", make_zip([("grande.bin", b"A" * 10_000)]))
    tight_limits = ArchiveLimits(max_member_uncompressed_bytes=1_000)

    with pytest.raises(OperatorZipImportError):
        import_operator_zip(zip_path, limits=tight_limits)


def test_total_uncompressed_size_bound_rejected(tmp_path: Path) -> None:
    entries = [(f"f{i}.bin", b"B" * 2_000) for i in range(5)]
    zip_path = _write_zip(tmp_path, "total.zip", make_zip(entries))
    tight_limits = ArchiveLimits(max_total_uncompressed_bytes=5_000)

    with pytest.raises(OperatorZipImportError):
        import_operator_zip(zip_path, limits=tight_limits)


def test_excessive_member_count_rejected(tmp_path: Path) -> None:
    entries = [(f"f{i}.txt", b"x") for i in range(10)]
    zip_path = _write_zip(tmp_path, "many_members.zip", make_zip(entries))
    tight_limits = ArchiveLimits(max_members=5)

    with pytest.raises(OperatorZipImportError):
        import_operator_zip(zip_path, limits=tight_limits)


def test_nested_directory_entries_handled(tmp_path: Path) -> None:
    pdf = make_pdf(pages=1)
    zip_path = _write_zip(
        tmp_path,
        "nested.zip",
        make_zip(
            [
                ("anexos/", b""),
                ("anexos/tecnico/tecnico.pdf", pdf),
                ("anexos/planilla.csv", make_csv(2)),
            ]
        ),
    )

    result = import_operator_zip(zip_path)

    # Directory entries themselves are not files and are skipped; the
    # nested-path files are imported with their full relative path intact
    # (not flattened or rejected as unsafe).
    assert [e.original_filename for e in result.entries] == [
        "anexos/tecnico/tecnico.pdf",
        "anexos/planilla.csv",
    ]
    assert result.rejected_entries == ()
    assert result.entries[0].sha256 == hashlib.sha256(pdf).hexdigest()


def test_compression_ratio_zip_bomb_rejected(tmp_path: Path) -> None:
    zip_path = _write_zip(tmp_path, "bomb.zip", make_zip_bomb(member_bytes=8 * 1024 * 1024))

    with pytest.raises(OperatorZipImportError):
        import_operator_zip(zip_path)


def test_malformed_zip_rejected_cleanly(tmp_path: Path) -> None:
    zip_path = _write_zip(tmp_path, "garbage.zip", b"not a zip file at all")

    with pytest.raises(OperatorZipImportError):
        import_operator_zip(zip_path)


def test_truncated_zip_rejected_cleanly(tmp_path: Path) -> None:
    zip_path = _write_zip(tmp_path, "truncated.zip", make_truncated_zip("docx"))

    with pytest.raises(OperatorZipImportError):
        import_operator_zip(zip_path)


def test_empty_zip_handled_without_crash(tmp_path: Path) -> None:
    zip_path = _write_zip(tmp_path, "empty.zip", make_zip([]))

    result = import_operator_zip(zip_path)

    assert result.entries == ()
    assert result.rejected_entries == ()


def test_unicode_filenames_preserved(tmp_path: Path) -> None:
    name = "informe_técnico_ñandú_日本語.pdf"
    zip_path = _write_zip(tmp_path, "unicode.zip", make_zip([(name, make_pdf(pages=1))]))

    result = import_operator_zip(zip_path)

    assert result.entries[0].original_filename == name
    assert result.entries[0].safe_filename == name


def test_duplicate_byte_content_identified(tmp_path: Path) -> None:
    payload = b"contenido identico"
    zip_path = _write_zip(
        tmp_path,
        "dupes.zip",
        make_zip([("a.txt", payload), ("b.txt", payload), ("c.txt", b"distinto")]),
    )

    result = import_operator_zip(zip_path)

    assert result.entries[0].duplicate_of_zip_entry_index is None
    assert result.entries[1].duplicate_of_zip_entry_index == 0
    assert result.entries[1].sha256 == result.entries[0].sha256
    assert result.entries[2].duplicate_of_zip_entry_index is None


def test_deterministic_inventory_ordering(tmp_path: Path) -> None:
    entries = [("z.txt", b"1"), ("a.txt", b"2"), ("m.txt", b"3")]
    zip_path = _write_zip(tmp_path, "order.zip", make_zip(entries))

    first = import_operator_zip(zip_path)
    second = import_operator_zip(zip_path)

    names = [e.original_filename for e in first.entries]
    # Original zip central-directory order is preserved, not re-sorted by name.
    assert names == ["z.txt", "a.txt", "m.txt"]
    assert [e.original_filename for e in second.entries] == names
    assert [e.sha256 for e in first.entries] == [e.sha256 for e in second.entries]


def test_sha256_provenance_recorded_correctly(tmp_path: Path) -> None:
    payload = b"contenido a verificar"
    zip_path = _write_zip(tmp_path, "hash.zip", make_zip([("x.bin", payload)]))

    result = import_operator_zip(zip_path)

    assert result.entries[0].sha256 == hashlib.sha256(payload).hexdigest()
    assert result.zip_sha256 == hashlib.sha256(zip_path.read_bytes()).hexdigest()


# --- No network / no browser automation / no DB -------------------------------


def test_operator_import_never_touches_urllib(tmp_path: Path, monkeypatch) -> None:
    """Mock-assertion companion to the AST import-scan below."""
    import urllib.request

    def _boom(*args, **kwargs):
        raise AssertionError("operator import must never open a network connection")

    monkeypatch.setattr(urllib.request, "urlopen", _boom)

    zip_path = _write_zip(tmp_path, "ok.zip", make_zip([("a.pdf", make_pdf(pages=1))]))
    source = OperatorZipAttachmentSource(zip_path=zip_path, tender_code=_TENDER)
    bundle = build_tender_bundle(_TENDER, source)
    assert bundle.attachments_downloaded == 1


@pytest.mark.parametrize(
    "module_path",
    [
        "origenlab_email_pipeline.chilecompra_anexo_evidence.operator_import",
        "origenlab_email_pipeline.chilecompra_anexo_evidence.acquisition_provenance",
        "origenlab_email_pipeline.operator_cli.import_tender_annex_bundle",
    ],
)
def test_no_db_gmail_network_import_at_module_scope(module_path: str) -> None:
    import importlib

    mod = importlib.import_module(module_path)
    banned = ("gmail", "outreach", "sqlite3", "psycopg", "smtplib", "requests", "urllib", "socket")
    tree = ast.parse(Path(mod.__file__).read_text(encoding="utf-8"))
    imported_modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_modules.append(node.module)
    joined = " ".join(imported_modules).lower()
    for name in banned:
        assert name not in joined, f"unexpected {name!r} import in {mod.__file__}"


def test_import_tender_annex_bundle_options_require_explicit_code_and_path() -> None:
    options = ImportTenderAnnexBundleOptions(tender_code=_TENDER, zip_path=Path("/nonexistent/x.zip"))
    assert options.tender_code == _TENDER
    assert options.declare_complete is False


def test_cli_reports_rejection_cleanly_for_malformed_zip(tmp_path: Path, capsys) -> None:
    zip_path = _write_zip(tmp_path, "bad.zip", b"definitely not a zip")
    options = ImportTenderAnnexBundleOptions(tender_code=_TENDER, zip_path=zip_path, json_output=True)

    rc = run_import_tender_annex_bundle(options)

    assert rc == 2
    captured = capsys.readouterr()
    assert '"result": "rejected"' in captured.out


def test_domain_result_is_pure_and_reusable_by_a_future_http_adapter(tmp_path: Path) -> None:
    """build_import_tender_annex_bundle_result is the seam a future FastAPI
    route reuses directly: it must return the full payload as data, with no
    printing and no CLI-specific exit code, and its JSON-serialized shape
    must carry everything the text report shows (no CLI-only fields)."""
    zip_path = _write_zip(tmp_path, "full.zip", make_zip([("a.pdf", make_pdf(pages=1))]))
    options = ImportTenderAnnexBundleOptions(
        tender_code=_TENDER, zip_path=zip_path, declare_complete=True
    )

    result = build_import_tender_annex_bundle_result(options)

    assert result["result"] == "imported"
    assert result["tender_code"] == _TENDER
    assert result["zip_path"] == str(zip_path)
    assert result["zip_sha256"] == hashlib.sha256(zip_path.read_bytes()).hexdigest()
    assert result["declare_complete"] is True
    assert result["published"] is False
    assert result["persisted"] is False
    # Round-trips cleanly through JSON, matching what the CLI prints.
    json.dumps(result)


def test_rejected_domain_result_also_carries_safety_flags(tmp_path: Path) -> None:
    zip_path = _write_zip(tmp_path, "bad.zip", b"not a zip")
    options = ImportTenderAnnexBundleOptions(tender_code=_TENDER, zip_path=zip_path)

    result = build_import_tender_annex_bundle_result(options)

    assert result["result"] == "rejected"
    assert result["published"] is False
    assert result["persisted"] is False
    json.dumps(result)


# --- Completeness declaration rule --------------------------------------------


def test_explicit_declaration_flows_through_to_complete(tmp_path: Path) -> None:
    zip_path = _write_zip(tmp_path, "complete.zip", make_zip([("planilla.csv", make_csv(3))]))
    source = OperatorZipAttachmentSource(zip_path=zip_path, tender_code=_TENDER, declare_complete=True)

    bundle = build_tender_bundle(_TENDER, source)
    provenance = build_acquisition_provenance(
        bundle,
        acquisition_source=ACQUISITION_SOURCE_OPERATOR_COMPLETE_BUNDLE,
        acquired_at="2026-08-17T00:00:00+00:00",
        operator_declared_complete=True,
    )

    assert bundle.bundle_complete is True
    assert provenance.completeness_state == COMPLETENESS_STATE_COMPLETE
    assert provenance.operator_supplied is True


def test_no_declaration_never_reaches_complete_regardless_of_file_count(tmp_path: Path) -> None:
    # Many legitimate, fully-extractable files -- still never "complete"
    # without the explicit operator declaration.
    entries = [(f"doc_{i}.csv", make_csv(2)) for i in range(10)]
    zip_path = _write_zip(tmp_path, "many.zip", make_zip(entries))
    source = OperatorZipAttachmentSource(zip_path=zip_path, tender_code=_TENDER, declare_complete=False)

    bundle = build_tender_bundle(_TENDER, source)
    provenance = build_acquisition_provenance(
        bundle,
        acquisition_source=ACQUISITION_SOURCE_OPERATOR_COMPLETE_BUNDLE,
        acquired_at="2026-08-17T00:00:00+00:00",
        operator_declared_complete=False,
    )

    assert bundle.bundle_complete is False
    assert provenance.completeness_state == COMPLETENESS_STATE_UNKNOWN
    assert provenance.completeness_state != COMPLETENESS_STATE_COMPLETE


def test_declared_complete_but_extraction_failure_is_incomplete_not_complete(tmp_path: Path) -> None:
    # Operator declares the inventory complete, but one file is a corrupt/
    # unsupported format -- the declaration cannot manufacture a real read.
    zip_path = _write_zip(
        tmp_path, "partial_extract.zip", make_zip([("roto.pdf", b"%PDF-1.4\ncorrupted, not a real pdf body")])
    )
    source = OperatorZipAttachmentSource(zip_path=zip_path, tender_code=_TENDER, declare_complete=True)

    bundle = build_tender_bundle(_TENDER, source)
    state = resolve_completeness_state(
        acquisition_source=ACQUISITION_SOURCE_OPERATOR_COMPLETE_BUNDLE,
        bundle=bundle,
        operator_declared_complete=True,
    )

    assert bundle.bundle_complete is False
    assert state == COMPLETENESS_STATE_INCOMPLETE


def test_legacy_gated_listing_still_stays_incomplete_regression(tmp_path: Path) -> None:
    """Regression: the existing gated_listing_unreachable fail-closed behavior
    must be unchanged by this new abstraction."""
    source = LocalAttachmentSource(
        items=[("anexo.pdf", "application/pdf", make_pdf(pages=1))],
        incomplete_reason_codes=(REASON_GATED_LISTING_UNREACHABLE,),
    )

    bundle = build_tender_bundle(_TENDER, source)
    state = resolve_completeness_state(
        acquisition_source=ACQUISITION_SOURCE_CHILECOMPRA_LEGACY_PARTIAL_HTTP,
        bundle=bundle,
    )

    assert bundle.bundle_complete is False
    assert REASON_GATED_LISTING_UNREACHABLE in bundle.incomplete_reason_codes
    assert state == COMPLETENESS_STATE_INCOMPLETE
    assert state != COMPLETENESS_STATE_COMPLETE


def test_is_operator_supplied() -> None:
    assert is_operator_supplied(ACQUISITION_SOURCE_OPERATOR_COMPLETE_BUNDLE) is True
    assert is_operator_supplied(ACQUISITION_SOURCE_CHILECOMPRA_LEGACY_PARTIAL_HTTP) is False


# --- Downstream extraction / T1 agnosticism -----------------------------------


def test_downstream_extraction_receives_real_imported_bytes(tmp_path: Path) -> None:
    pdf = make_pdf(pages=1)
    zip_path = _write_zip(tmp_path, "real.zip", make_zip([("tecnico.pdf", pdf)]))
    source = OperatorZipAttachmentSource(zip_path=zip_path, tender_code=_TENDER, declare_complete=True)

    bundle = build_tender_bundle(_TENDER, source)

    assert len(bundle.attachments) == 1
    record = bundle.attachments[0]
    assert record.outcome == OUTCOME_EXTRACTION_SUCCESS
    assert record.sha256 == hashlib.sha256(pdf).hexdigest()
    assert bundle.chunks
    assert any("pagina 1" in c.text for c in bundle.chunks)


def test_no_contact_or_outreach_authorization(tmp_path: Path) -> None:
    zip_path = _write_zip(tmp_path, "auth.zip", make_zip([("a.csv", make_csv(2))]))
    source = OperatorZipAttachmentSource(zip_path=zip_path, tender_code=_TENDER, declare_complete=True)
    bundle = build_tender_bundle(_TENDER, source)

    terms = extract_tender_terms(bundle)

    assert terms.contact_authorization is False
    assert terms.outreach_authorization is False


# --- Future-API seam: identical downstream semantics --------------------------


@dataclass
class _FutureApiTestDouble:
    """Test-only stand-in proving the Protocol seam is source-agnostic.

    Not a real adapter: no network call, no endpoint, nothing that could be
    mistaken for a working implementation. It exists purely so this test can
    show that build_tender_bundle()/extract_tender_terms() do not care which
    concrete AttachmentSource produced the bytes.
    """

    items: list[tuple[str, bytes]]
    source_kind: str = "future_chilecompra_document_api"

    def inventory(self) -> SourceInventory:
        from origenlab_email_pipeline.chilecompra_api import safe_attachment_filename

        stubs = tuple(
            AttachmentStub(
                listing_ordinal=0,
                row_ordinal=index,
                portal_filename=name,
                safe_filename=safe_attachment_filename(name),
            )
            for index, (name, _payload) in enumerate(self.items)
        )
        return SourceInventory(listing_page_count=1, stubs=stubs, incomplete_reason_codes=())

    def iter_payloads(self, inventory: SourceInventory) -> Iterator[AcquiredAttachment]:
        for stub in inventory.stubs:
            _name, payload = self.items[stub.row_ordinal]
            yield AcquiredAttachment(stub=stub, payload=payload, content_type="")


def test_future_api_and_operator_source_produce_identical_downstream_semantics(
    tmp_path: Path,
) -> None:
    payload = make_pdf(pages=2, marker="ESPECTROFOTOMETRO", marker_page=1)
    filename = "tecnico.pdf"

    zip_path = _write_zip(tmp_path, "compare.zip", make_zip([(filename, payload)]))
    operator_source = OperatorZipAttachmentSource(
        zip_path=zip_path, tender_code=_TENDER, declare_complete=True
    )
    future_source = _FutureApiTestDouble(items=[(filename, payload)])

    operator_bundle = build_tender_bundle(_TENDER, operator_source)
    future_bundle = build_tender_bundle(_TENDER, future_source)

    # Only the source-provenance field may differ.
    assert operator_bundle.source_kind != future_bundle.source_kind
    assert operator_bundle.attachments == future_bundle.attachments
    assert operator_bundle.chunks == future_bundle.chunks
    assert operator_bundle.semantic_digest == future_bundle.semantic_digest
    assert operator_bundle.bundle_complete == future_bundle.bundle_complete is True

    operator_terms = extract_tender_terms(operator_bundle)
    future_terms = extract_tender_terms(future_bundle)

    assert operator_terms.tender_facts == future_terms.tender_facts
    assert operator_terms.items == future_terms.items
    assert operator_terms.semantic_digest == future_terms.semantic_digest

    operator_provenance = build_acquisition_provenance(
        operator_bundle,
        acquisition_source=ACQUISITION_SOURCE_OPERATOR_COMPLETE_BUNDLE,
        acquired_at="2026-08-17T00:00:00+00:00",
        operator_declared_complete=True,
    )
    assert operator_provenance.operator_supplied is True
