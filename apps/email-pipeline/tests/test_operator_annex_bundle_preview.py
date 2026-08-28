"""Bytes-based operator ZIP seam + the shared full-detail preview builder.

Covers the domain foundation for the annex-upload-preview PR: an HTTP
adapter must be able to feed already-read-into-memory upload bytes through
the exact same validation/import/provenance/T1 pipeline #493's CLI uses for
a real on-disk ZIP, with identical output. None of this touches Mercado
Público's reCAPTCHA-gated ``ViewAttachment.aspx`` flow, performs any network
I/O, or writes anything to disk.
"""

from __future__ import annotations

import ast
import hashlib
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent / "fixtures"))

from chilecompra_anexo_evidence.synthetic_documents import (  # noqa: E402
    make_csv,
    make_pdf,
    make_traversal_zip,
    make_zip,
    make_zip_bomb,
)
from origenlab_email_pipeline.chilecompra_anexo_evidence.constants import (  # noqa: E402
    COMPLETENESS_STATE_COMPLETE,
    COMPLETENESS_STATE_UNKNOWN,
)
from origenlab_email_pipeline.chilecompra_anexo_evidence.operator_import import (  # noqa: E402
    OperatorZipAttachmentSource,
    OperatorZipImportError,
    import_operator_zip_bytes,
)
from origenlab_email_pipeline.commercial_procurement_anexo_tender_terms.operator_annex_bundle_preview import (  # noqa: E402
    build_operator_annex_bundle_preview,
)

_TENDER = "1057890-1-LE26"


def _write_zip(tmp_path: Path, name: str, payload: bytes) -> Path:
    path = tmp_path / name
    path.write_bytes(payload)
    return path


# --- Path vs bytes equivalence -------------------------------------------------


def test_from_path_and_from_bytes_produce_identical_import_result(
    tmp_path: Path,
) -> None:
    payload = make_zip(
        [("tecnico.pdf", make_pdf(pages=1)), ("planilla.csv", make_csv(3))]
    )
    zip_path = _write_zip(tmp_path, "anexos.zip", payload)

    path_source = OperatorZipAttachmentSource.from_path(zip_path, tender_code=_TENDER)
    bytes_source = OperatorZipAttachmentSource.from_bytes(payload, tender_code=_TENDER)

    path_result = path_source.loaded_import_result()
    bytes_result = bytes_source.loaded_import_result()

    assert (
        path_result.zip_sha256
        == bytes_result.zip_sha256
        == hashlib.sha256(payload).hexdigest()
    )
    assert [e.to_dict() for e in path_result.entries] == [
        e.to_dict() for e in bytes_result.entries
    ]
    assert path_result.rejected_entries == bytes_result.rejected_entries == ()
    # Only the descriptive label differs (real path vs. synthetic label) --
    # never surfaced as meaningful data by any caller.
    assert path_result.zip_path != bytes_result.zip_path


def test_direct_keyword_zip_path_construction_still_works(tmp_path: Path) -> None:
    """Backward compatibility: #493's direct `zip_path=` keyword construction is unchanged."""
    payload = make_zip([("a.pdf", make_pdf(pages=1))])
    zip_path = _write_zip(tmp_path, "a.zip", payload)

    source = OperatorZipAttachmentSource(zip_path=zip_path, tender_code=_TENDER)

    assert (
        source.loaded_import_result().zip_sha256 == hashlib.sha256(payload).hexdigest()
    )


def test_construction_requires_exactly_one_of_path_or_bytes(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        OperatorZipAttachmentSource(tender_code=_TENDER)
    with pytest.raises(ValueError):
        OperatorZipAttachmentSource(
            tender_code=_TENDER,
            zip_path=_write_zip(tmp_path, "x.zip", make_zip([])),
            payload_bytes=b"PK\x05\x06" + b"\x00" * 18,
        )


def test_import_operator_zip_bytes_malicious_zip_behavior_unchanged() -> None:
    """The zip-slip defense applies identically to the bytes entrypoint."""
    result = import_operator_zip_bytes(make_traversal_zip())
    assert [e.original_filename for e in result.entries] == ["safe.txt"]
    assert len(result.rejected_entries) == 1
    assert "unsafe_member_path" in result.rejected_entries[0]


def test_import_operator_zip_bytes_zip_bomb_rejected() -> None:
    with pytest.raises(OperatorZipImportError):
        import_operator_zip_bytes(make_zip_bomb(member_bytes=8 * 1024 * 1024))


def test_bytes_seam_no_network_no_disk_extraction_at_module_scope() -> None:
    """AST import-scan: the bytes entrypoint module never gains a network/DB import."""
    import importlib

    mod = importlib.import_module(
        "origenlab_email_pipeline.chilecompra_anexo_evidence.operator_import"
    )
    banned = (
        "gmail",
        "outreach",
        "sqlite3",
        "psycopg",
        "smtplib",
        "requests",
        "urllib",
        "socket",
    )
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


def test_deterministic_output_across_repeated_bytes_imports() -> None:
    payload = make_zip([("z.txt", b"1"), ("a.txt", b"2"), ("m.txt", b"3")])
    first = import_operator_zip_bytes(payload)
    second = import_operator_zip_bytes(payload)
    assert [e.original_filename for e in first.entries] == [
        e.original_filename for e in second.entries
    ]
    assert [e.sha256 for e in first.entries] == [e.sha256 for e in second.entries]


# --- build_operator_annex_bundle_preview: full T1 detail, not just counts -----


def test_preview_from_bytes_contains_full_t1_facts_items_evidence() -> None:
    payload = make_zip(
        [("tecnico.pdf", make_pdf(pages=1, marker="ESPECTROFOTOMETRO", marker_page=1))]
    )
    source = OperatorZipAttachmentSource.from_bytes(
        payload, tender_code=_TENDER, declare_complete=True
    )

    result = build_operator_annex_bundle_preview(source, tender_code=_TENDER)

    assert result["result"] == "imported"
    assert result["published"] is False
    assert result["persisted"] is False
    assert result["contact_authorization"] is False
    assert result["outreach_authorization"] is False
    assert result["provenance"]["completeness_state"] == COMPLETENESS_STATE_COMPLETE
    assert result["archive"]["zip_sha256"] == hashlib.sha256(payload).hexdigest()
    # Full T1 structure, not a reduced counts-only summary: "terms" carries
    # the exact same tender_facts/items/coverage shape TenderTermsBundle.to_dict()
    # (and therefore a published tender's own detail response) uses.
    terms = result["terms"]
    assert "tender_facts" in terms
    assert "items" in terms
    assert "coverage" in terms
    assert isinstance(terms["tender_facts"], list)


def test_preview_completeness_unknown_without_declare_complete() -> None:
    payload = make_zip([("a.csv", make_csv(2))])
    source = OperatorZipAttachmentSource.from_bytes(
        payload, tender_code=_TENDER, declare_complete=False
    )

    result = build_operator_annex_bundle_preview(source, tender_code=_TENDER)

    assert result["result"] == "imported"
    assert result["provenance"]["completeness_state"] == COMPLETENESS_STATE_UNKNOWN


def test_preview_rejects_corrupt_zip_as_data_not_exception() -> None:
    source = OperatorZipAttachmentSource.from_bytes(
        b"not a zip at all", tender_code=_TENDER
    )

    result = build_operator_annex_bundle_preview(source, tender_code=_TENDER)

    assert result["result"] == "rejected"
    assert "error" in result
    assert result["published"] is False
    assert result["persisted"] is False


def test_preview_path_and_bytes_produce_identical_terms_and_provenance(
    tmp_path: Path,
) -> None:
    payload = make_zip([("tecnico.pdf", make_pdf(pages=1))])
    zip_path = _write_zip(tmp_path, "same.zip", payload)

    path_source = OperatorZipAttachmentSource.from_path(
        zip_path, tender_code=_TENDER, declare_complete=True
    )
    bytes_source = OperatorZipAttachmentSource.from_bytes(
        payload, tender_code=_TENDER, declare_complete=True
    )

    fixed_now = lambda: datetime(2026, 8, 18, tzinfo=timezone.utc)  # noqa: E731
    path_result = build_operator_annex_bundle_preview(
        path_source, tender_code=_TENDER, now_fn=fixed_now
    )
    bytes_result = build_operator_annex_bundle_preview(
        bytes_source, tender_code=_TENDER, now_fn=fixed_now
    )

    assert path_result["terms"] == bytes_result["terms"]
    assert path_result["archive"]["zip_sha256"] == bytes_result["archive"]["zip_sha256"]
    assert (
        path_result["provenance"]["completeness_state"]
        == bytes_result["provenance"]["completeness_state"]
    )


def test_preview_forwards_explicit_semantic_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = make_zip([("bases.txt", b"centrifuga refrigerada")])
    source = OperatorZipAttachmentSource.from_bytes(
        payload,
        tender_code=_TENDER,
        declare_complete=True,
    )
    semantic_client = object()
    captured = {}

    class FakeTermsBundle:
        def to_dict(self):
            return {
                "tender_facts": [],
                "items": [],
                "coverage": {},
            }

    def fake_extract_tender_terms(bundle, *, semantic_client=None):
        captured["semantic_client"] = semantic_client
        return FakeTermsBundle()

    monkeypatch.setattr(
        "origenlab_email_pipeline."
        "commercial_procurement_anexo_tender_terms."
        "operator_annex_bundle_preview.extract_tender_terms",
        fake_extract_tender_terms,
    )

    result = build_operator_annex_bundle_preview(
        source,
        tender_code=_TENDER,
        semantic_client=semantic_client,
    )

    assert result["result"] == "imported"
    assert captured["semantic_client"] is semantic_client
