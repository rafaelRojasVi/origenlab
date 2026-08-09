"""Hardening contracts for the anexo evidence lane (PR #450 audit).

Covers the OOXML container safety envelope, per-member compression ratio,
content-addressed cache trust rules, portal session binding, and structured
duplicate extraction provenance. All fixtures are synthetic and local.
"""

from __future__ import annotations

import os
import sys
import threading
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent / "fixtures"))

from chilecompra_anexo_evidence.synthetic_documents import (  # noqa: E402
    make_csv,
    make_diluted_ratio_bomb,
    make_docx,
    make_encrypted_member_zip,
    make_ooxml_bomb,
    make_truncated_zip,
    make_xlsx,
    make_zip,
)
from origenlab_email_pipeline.chilecompra_anexo_evidence import (  # noqa: E402
    ArchiveLimits,
    ContentAddressedCache,
    EvidenceBuildConfig,
    LocalAttachmentSource,
    build_tender_bundle,
    extract_payload,
)
from origenlab_email_pipeline.chilecompra_anexo_evidence import extract as extract_mod  # noqa: E402
from origenlab_email_pipeline.chilecompra_anexo_evidence import planner as planner_mod  # noqa: E402
from origenlab_email_pipeline.chilecompra_anexo_evidence.archive import (  # noqa: E402
    preflight_archive,
)
from origenlab_email_pipeline.chilecompra_anexo_evidence.constants import (  # noqa: E402
    OUTCOME_CORRUPT,
    OUTCOME_ENCRYPTED,
    OUTCOME_EXTRACTION_SUCCESS,
    OUTCOME_PARTIAL_DUE_TO_SAFETY_LIMIT,
    REASON_ARCHIVE_RATIO_LIMIT,
    REASON_ARCHIVE_SUSPICIOUS_MEMBER,
    REASON_OOXML_CONTAINER_UNSAFE,
)
from origenlab_email_pipeline.chilecompra_anexo_evidence.models import sha256_bytes  # noqa: E402

_SPEC = "ESPECTROFOTOMETRO UV VIS"

# Deliberately tiny so fixtures stay small.
_TIGHT_BYTES = ArchiveLimits(
    max_total_uncompressed_bytes=10_000, max_member_uncompressed_bytes=10_000
)
_TIGHT_RATIO = ArchiveLimits(max_compression_ratio=5.0)
_TIGHT_MEMBERS = ArchiveLimits(max_members=5)


# --- Phase 2: OOXML container preflight --------------------------------------


@pytest.mark.parametrize("kind", ["docx", "xlsx"])
@pytest.mark.parametrize(
    ("mode", "limits"),
    [("bytes", _TIGHT_BYTES), ("ratio", _TIGHT_RATIO), ("members", _TIGHT_MEMBERS)],
)
def test_ooxml_bomb_is_rejected_by_container_preflight(kind, mode, limits) -> None:
    payload = make_ooxml_bomb(kind, mode)
    output = extract_payload(
        payload, detected_format=kind, archive_limits=limits
    )
    assert output.outcome == OUTCOME_PARTIAL_DUE_TO_SAFETY_LIMIT
    assert REASON_OOXML_CONTAINER_UNSAFE in output.warnings
    assert output.chunks == []
    assert output.outcome != OUTCOME_CORRUPT, "must not be mislabeled as corrupt"


def test_docx_parser_is_not_entered_after_preflight_rejection(monkeypatch) -> None:
    def _boom(*args, **kwargs):
        raise AssertionError("member decompression must not start")

    monkeypatch.setattr(extract_mod, "_read_member_capped", _boom)
    output = extract_payload(
        make_ooxml_bomb("docx", "ratio"),
        detected_format="docx",
        archive_limits=_TIGHT_RATIO,
    )
    assert output.outcome == OUTCOME_PARTIAL_DUE_TO_SAFETY_LIMIT


def test_xlsx_parser_is_not_entered_after_preflight_rejection(monkeypatch) -> None:
    openpyxl = pytest.importorskip("openpyxl")

    def _boom(*args, **kwargs):
        raise AssertionError("openpyxl must not open a rejected container")

    monkeypatch.setattr(openpyxl, "load_workbook", _boom)
    output = extract_payload(
        make_ooxml_bomb("xlsx", "bytes"),
        detected_format="xlsx",
        archive_limits=_TIGHT_BYTES,
    )
    assert output.outcome == OUTCOME_PARTIAL_DUE_TO_SAFETY_LIMIT


@pytest.mark.parametrize("kind", ["docx", "xlsx"])
def test_malformed_ooxml_central_directory_is_corrupt(kind) -> None:
    output = extract_payload(make_truncated_zip(kind), detected_format=kind)
    assert output.outcome == OUTCOME_CORRUPT
    assert output.chunks == []
    assert output.error_message


@pytest.mark.parametrize("kind", ["docx", "xlsx"])
def test_encrypted_ooxml_container_is_reported_as_encrypted(kind) -> None:
    output = extract_payload(make_encrypted_member_zip(), detected_format=kind)
    assert output.outcome == OUTCOME_ENCRYPTED
    assert REASON_OOXML_CONTAINER_UNSAFE in output.warnings


def test_ooxml_bomb_through_bundle_keeps_extraction_incomplete() -> None:
    config = EvidenceBuildConfig(archive_limits=_TIGHT_RATIO)
    bundle = build_tender_bundle(
        "T-OOXML",
        LocalAttachmentSource(
            items=[
                (
                    "bases.docx",
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    make_ooxml_bomb("docx", "ratio"),
                )
            ]
        ),
        config=config,
    )
    record = bundle.attachments[0]
    assert record.detected_format == "docx"
    assert record.outcome == OUTCOME_PARTIAL_DUE_TO_SAFETY_LIMIT
    assert bundle.extraction_complete is False
    assert bundle.bundle_complete is False
    assert OUTCOME_PARTIAL_DUE_TO_SAFETY_LIMIT in bundle.incomplete_reason_codes


# --- Phase 2 item 8-10: normal documents still extract fully ------------------


def test_normal_docx_still_extracts_paragraphs_tables_headers_footers() -> None:
    payload = make_docx(
        paragraph="parrafo introductorio",
        table_rows=[["Equipo", "Cantidad"], [_SPEC, "3"]],
        header="ENCABEZADO TECNICO",
        footer="PIE DE PAGINA",
    )
    output = extract_payload(payload, detected_format="docx")
    assert output.outcome == OUTCOME_EXTRACTION_SUCCESS
    joined = "\n".join(c.text for c in output.chunks)
    assert "parrafo introductorio" in joined
    assert _SPEC in joined
    assert "ENCABEZADO TECNICO" in joined
    assert "PIE DE PAGINA" in joined
    assert {"docx_paragraph", "docx_table"} <= {c.locator_type for c in output.chunks}


def test_normal_xlsx_still_extracts_all_sheets_including_hidden() -> None:
    payload = make_xlsx(
        ["S1", "S2", "S3", "S4", "S5", "Oculta"],
        cells={"S5": [(150, 15, _SPEC)], "Oculta": [(2, 2, "BANO TERMOSTATICO")]},
        hidden_sheets=("Oculta",),
    )
    output = extract_payload(payload, detected_format="xlsx")
    assert output.outcome == OUTCOME_EXTRACTION_SUCCESS
    assert output.sheet_count == 6
    joined = "\n".join(c.text for c in output.chunks)
    assert _SPEC in joined and "BANO TERMOSTATICO" in joined


def test_real_world_sized_ooxml_passes_default_limits() -> None:
    """Default per-member ratio must not reject ordinary Office output."""
    payload = make_docx(
        paragraph="texto " * 2000,
        table_rows=[["a", "b"], ["c", "d"]],
        header="h",
        footer="f",
    )
    preflight = preflight_archive(payload, limits=ArchiveLimits())
    assert preflight.rejected is False
    assert preflight.worst_member_ratio < ArchiveLimits().max_compression_ratio
    assert extract_payload(payload, detected_format="docx").outcome == (
        OUTCOME_EXTRACTION_SUCCESS
    )


# --- Phase 3: per-member compression ratio ------------------------------------


def test_per_member_ratio_catches_bomb_diluted_by_ordinary_members() -> None:
    payload = make_diluted_ratio_bomb()
    limits = ArchiveLimits(max_compression_ratio=50.0)

    with zipfile.ZipFile(__import__("io").BytesIO(payload)) as archive:
        infos = [i for i in archive.infolist() if not i.is_dir()]
    declared = sum(i.file_size for i in infos)
    compressed = sum(i.compress_size for i in infos)
    aggregate_ratio = declared / compressed
    assert aggregate_ratio <= limits.max_compression_ratio, (
        "fixture must hide the bomb from the aggregate check"
    )

    preflight = preflight_archive(payload, limits=limits)
    assert preflight.rejected is True
    assert REASON_ARCHIVE_RATIO_LIMIT in preflight.reason_codes
    assert preflight.worst_member_ratio > limits.max_compression_ratio


def test_rejected_archive_is_never_decompressed(monkeypatch) -> None:
    def _boom(*args, **kwargs):
        raise AssertionError("rejected archive must not be expanded")

    monkeypatch.setattr(planner_mod, "iter_archive_members", _boom)
    bundle = build_tender_bundle(
        "T-NODECOMP",
        LocalAttachmentSource(
            items=[("bomba.zip", "application/zip", make_diluted_ratio_bomb())]
        ),
        config=EvidenceBuildConfig(archive_limits=ArchiveLimits(max_compression_ratio=50.0)),
    )
    record = bundle.attachments[0]
    assert record.outcome == OUTCOME_PARTIAL_DUE_TO_SAFETY_LIMIT
    assert record.archive_members == ()


def test_member_declaring_size_without_compressed_bytes_fails_closed() -> None:
    payload = make_zip([("normal.txt", b"contenido")])
    data = bytearray(payload)
    # Zero the central-directory compressed size while keeping file_size > 0.
    index = data.find(b"PK\x01\x02")
    assert index != -1
    data[index + 20 : index + 24] = (0).to_bytes(4, "little")
    preflight = preflight_archive(bytes(data), limits=ArchiveLimits())
    assert preflight.rejected is True
    assert REASON_ARCHIVE_SUSPICIOUS_MEMBER in preflight.reason_codes


def test_whole_archive_ratio_check_is_retained() -> None:
    payload = make_zip([("a.txt", b"A" * 100_000), ("b.txt", b"B" * 100_000)])
    preflight = preflight_archive(
        payload, limits=ArchiveLimits(max_compression_ratio=5.0)
    )
    assert preflight.rejected is True
    assert REASON_ARCHIVE_RATIO_LIMIT in preflight.reason_codes


# --- Phase 5: content-addressed cache -----------------------------------------


def test_cache_hit_returns_verified_bytes(tmp_path) -> None:
    cache = ContentAddressedCache(root=tmp_path / "cache")
    payload = make_csv(5)
    digest, path = cache.put(payload)
    assert cache.has(digest) is True
    assert cache.read(digest) == payload
    again = cache.put(payload)
    assert again == (digest, path)


def test_cache_with_corrupted_target_is_not_trusted_and_is_repaired(tmp_path) -> None:
    cache = ContentAddressedCache(root=tmp_path / "cache")
    payload = make_csv(5)
    digest, path = cache.put(payload)

    path.write_bytes(b"contenido manipulado")
    assert cache.read(digest) is None, "hash mismatch must fail closed"
    assert cache.has(digest) is False

    # Documented policy: an object that does not hash to its own name is
    # untrusted and gets atomically replaced by the verified bytes.
    cache.put(payload)
    assert cache.read(digest) == payload


def test_cache_does_not_trust_or_follow_a_symlink_target(tmp_path) -> None:
    cache = ContentAddressedCache(root=tmp_path / "cache")
    payload = make_csv(5)
    digest = sha256_bytes(payload)
    target = cache.path_for(digest)
    target.parent.mkdir(parents=True, exist_ok=True)

    outside = tmp_path / "outside.bin"
    outside.write_bytes(payload)
    target.symlink_to(outside)

    assert cache.read(digest) is None, "symlinked target must never be trusted"
    assert cache.has(digest) is False

    cache.put(payload)
    assert target.is_symlink() is False, "publication must replace the symlink"
    assert outside.read_bytes() == payload, "must not write through the symlink"
    assert cache.read(digest) == payload


def test_cache_does_not_delete_a_concurrent_writers_temp_file(tmp_path) -> None:
    cache = ContentAddressedCache(root=tmp_path / "cache")
    payload = make_csv(7)
    digest = sha256_bytes(payload)
    target = cache.path_for(digest)
    target.parent.mkdir(parents=True, exist_ok=True)

    # The previous implementation derived the temp name from the PID alone and
    # unlinked it on collision, which would destroy a peer writer's staging file.
    decoy = target.with_name(f".{target.name}.tmp.{os.getpid()}")
    decoy.write_bytes(b"otro escritor en curso")

    cache.put(payload)
    assert decoy.exists(), "a peer writer's temp file must survive"
    assert decoy.read_bytes() == b"otro escritor en curso"
    assert cache.read(digest) == payload


def test_concurrent_writers_publish_identical_verified_bytes(tmp_path) -> None:
    cache = ContentAddressedCache(root=tmp_path / "cache")
    payload = make_csv(50)
    digest = sha256_bytes(payload)
    errors: list[BaseException] = []
    start = threading.Barrier(4)

    def _writer() -> None:
        try:
            start.wait(timeout=5)
            cache.put(payload)
        except BaseException as exc:  # noqa: BLE001 - surfaced below
            errors.append(exc)

    threads = [threading.Thread(target=_writer) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert errors == []
    assert cache.read(digest) == payload
    leftovers = list(cache.path_for(digest).parent.glob(".*tmp*"))
    assert leftovers == [], "no staging files may be left behind"


# --- Phase 6: inventory / session binding -------------------------------------


def _session_opener(name: str):
    from unittest.mock import MagicMock

    detail_html = (
        "<html><body>"
        '<a href="../Attachment/VerAntecedentes.aspx?enc=AAA%2fBBB">anexos</a>'
        "</body></html>"
    )
    listing_html = (
        "<html><body><form>"
        '<input type="hidden" name="__VIEWSTATE" value="VS-' + name + '" />'
        '<table id="grdAttachment">'
        '<tr class="cssFwkHeaderTableRow"><th>a</th><th>b</th><th>c</th>'
        "<th>d</th><th>e</th></tr>"
        '<tr class="cssFwkItemStyle ajustar">'
        '<td><span id="grdAttachment_ctl02_grdLblSourceFileName">a.pdf</span></td>'
        "<td>Anexos</td>"
        '<td><span id="grdAttachment_ctl02_grdLblFileDescription">d</span></td>'
        '<td><span id="grdAttachment_ctl02_grdLblFileDate">02-06-2026</span></td>'
        '<td><input type="image" name="grdAttachment$ctl02$grdIbtnView" /></td>'
        "</tr></table></form></body></html>"
    )

    def _response(body: bytes, url: str, headers=None):
        response = MagicMock()
        response.__enter__.return_value = response
        response.__exit__.return_value = False
        response.status = 200
        response.getcode.return_value = 200
        response.read.side_effect = lambda *a: body
        response.headers = headers or {}
        response.url = url
        return response

    def opener(request, timeout=None):
        url = request.full_url
        opener.calls.append((request.get_method(), url))
        if request.data is not None:
            return _response(
                b"%PDF-1.7\nok\n%%EOF", url, {"Content-Type": "application/pdf"}
            )
        if "DetailsAcquisition" in url:
            return _response(detail_html.encode(), url)
        return _response(listing_html.encode(), url)

    opener.calls = []
    opener.session_name = name
    return opener


_DETAIL_URL = (
    "https://www.mercadopublico.cl/Procurement/Modules/RFB/"
    "DetailsAcquisition.aspx?qs=TOKEN"
)


def test_inventory_carries_the_session_that_built_it() -> None:
    from origenlab_email_pipeline.chilecompra_api import list_licitacion_attachments

    session = _session_opener("A")
    inventory = list_licitacion_attachments(_DETAIL_URL, opener=session)
    assert inventory.session is session
    assert inventory.attachments_discovered == 1


def test_inventory_cannot_be_consumed_through_a_different_session() -> None:
    from origenlab_email_pipeline.chilecompra_api import (
        ChileCompraPortalError,
        iter_licitacion_attachments,
        list_licitacion_attachments,
    )

    session_a = _session_opener("A")
    session_b = _session_opener("B")
    inventory = list_licitacion_attachments(_DETAIL_URL, opener=session_a)
    before = len(session_b.calls)

    with pytest.raises(ChileCompraPortalError, match="different portal session"):
        next(
            iter_licitacion_attachments(
                _DETAIL_URL, opener=session_b, inventory=inventory
            )
        )
    assert len(session_b.calls) == before, "no request may reach the wrong session"


def test_inventory_without_an_opener_reuses_its_own_session_not_a_new_one() -> None:
    from origenlab_email_pipeline.chilecompra_api import (
        iter_licitacion_attachments,
        list_licitacion_attachments,
    )

    session = _session_opener("A")
    inventory = list_licitacion_attachments(_DETAIL_URL, opener=session)
    downloads = list(
        iter_licitacion_attachments(_DETAIL_URL, inventory=inventory)
    )
    assert len(downloads) == 1
    # The postback was issued on the original session, not a freshly built one.
    assert [m for m, _ in session.calls].count("POST") == 1


def test_inventory_without_a_bound_session_is_refused() -> None:
    from origenlab_email_pipeline.chilecompra_api import (
        ChileCompraPortalError,
        PortalAttachmentInventory,
        iter_licitacion_attachments,
    )

    detached = PortalAttachmentInventory(
        detail_url=_DETAIL_URL,
        listing_urls=(),
        attachments=(),
        listing_form_fields={},
    )
    with pytest.raises(ChileCompraPortalError, match="no bound portal session"):
        next(iter_licitacion_attachments(_DETAIL_URL, inventory=detached))


def test_normal_single_call_iteration_still_works() -> None:
    from origenlab_email_pipeline.chilecompra_api import iter_licitacion_attachments

    session = _session_opener("A")
    downloads = list(iter_licitacion_attachments(_DETAIL_URL, opener=session))
    assert [d.filename for d in downloads] == ["a.pdf"]


def test_session_binding_is_runtime_only_and_never_leaves_the_process() -> None:
    """The cookie jar is held by reference; it is not repr'd, compared, or exported."""
    from origenlab_email_pipeline.chilecompra_api import list_licitacion_attachments

    session = _session_opener("A")
    inventory = list_licitacion_attachments(_DETAIL_URL, opener=session)

    rendered = repr(inventory)
    assert "session=" not in rendered
    assert "opener" not in rendered
    assert "_session_opener" not in rendered

    same_without_session = type(inventory)(
        detail_url=inventory.detail_url,
        listing_urls=inventory.listing_urls,
        attachments=inventory.attachments,
        listing_form_fields=inventory.listing_form_fields,
    )
    # Session identity is excluded from equality, so it cannot leak via compare.
    assert inventory == same_without_session


def test_published_evidence_artifacts_carry_no_session_or_cookie_state(tmp_path) -> None:
    from origenlab_email_pipeline.chilecompra_anexo_evidence import write_evidence_outputs

    root = tmp_path / "email-pipeline"
    (root / "reports" / "out").mkdir(parents=True)
    bundle = build_tender_bundle(
        "T-SESSION",
        LocalAttachmentSource(items=[("a.csv", "text/csv", make_csv(3))]),
    )
    out_dir = root / "reports" / "out" / "session_bundle"
    write_evidence_outputs(
        [bundle], out_dir, repo_email_pipeline_root=root, require_git_ignored=False
    )
    for path in sorted(out_dir.rglob("*")):
        if not path.is_file():
            continue
        blob = path.read_text(errors="replace").lower()
        for marker in ("__viewstate", "cookie", "set-cookie", "aspxauth", "opener"):
            assert marker not in blob, f"{marker} leaked into {path.name}"


# --- Phase 7: structured duplicate extraction provenance ----------------------


def _resolve_chunks(bundle, record):
    return [c for c in bundle.chunks if c.attachment_id == record.evidence_attachment_id]


def test_same_name_same_bytes_resolves_to_evidence_without_parsing_warnings() -> None:
    payload = make_csv(6, marker=_SPEC, marker_row=3)
    bundle = build_tender_bundle(
        "T-DUP1",
        LocalAttachmentSource(
            items=[("anexo.csv", "text/csv", payload), ("anexo.csv", "text/csv", payload)]
        ),
    )
    first, second = bundle.attachments
    assert second.duplicate_of_attachment_id == first.attachment_id
    assert second.duplicate_kind == "same_name_same_bytes"
    assert second.extraction_reused_from_attachment_id == first.attachment_id
    assert second.evidence_attachment_id == first.attachment_id
    assert first.evidence_attachment_id == first.attachment_id
    assert first.extraction_reused_from_attachment_id is None

    for record in (first, second):
        chunks = _resolve_chunks(bundle, record)
        assert chunks, "both rows must resolve to evidence chunks"
        assert any(_SPEC in c.text for c in chunks)
    # Text is stored once, not duplicated per row.
    assert second.extraction.chunk_count == 0


def test_different_name_same_bytes_resolves_to_shared_evidence() -> None:
    payload = make_csv(6, marker=_SPEC, marker_row=2)
    bundle = build_tender_bundle(
        "T-DUP2",
        LocalAttachmentSource(
            items=[("uno.csv", "text/csv", payload), ("dos.csv", "text/csv", payload)]
        ),
    )
    first, second = bundle.attachments
    assert second.duplicate_kind == "different_name_same_bytes"
    assert second.extraction_reused_from_attachment_id == first.attachment_id
    assert _resolve_chunks(bundle, second) == _resolve_chunks(bundle, first)
    assert any(_SPEC in c.text for c in _resolve_chunks(bundle, second))


def test_distinct_content_has_independent_evidence_pointers() -> None:
    bundle = build_tender_bundle(
        "T-DUP3",
        LocalAttachmentSource(
            items=[
                ("bases.csv", "text/csv", make_csv(3, marker="EQUIPO A", marker_row=1)),
                ("bases.csv", "text/csv", make_csv(3, marker="EQUIPO B", marker_row=1)),
            ]
        ),
    )
    first, second = bundle.attachments
    assert second.extraction_reused_from_attachment_id is None
    assert second.evidence_attachment_id == second.attachment_id
    assert first.evidence_attachment_id != second.evidence_attachment_id
    assert any("EQUIPO B" in c.text for c in _resolve_chunks(bundle, second))


def test_duplicate_provenance_survives_manifest_serialization(tmp_path) -> None:
    import json

    from origenlab_email_pipeline.chilecompra_anexo_evidence import write_evidence_outputs

    root = tmp_path / "email-pipeline"
    (root / "reports" / "out").mkdir(parents=True)
    payload = make_csv(4, marker=_SPEC, marker_row=2)
    bundle = build_tender_bundle(
        "T-DUP4",
        LocalAttachmentSource(
            items=[("a.csv", "text/csv", payload), ("b.csv", "text/csv", payload)]
        ),
    )
    out_dir = root / "reports" / "out" / "dup_bundle"
    write_evidence_outputs(
        [bundle], out_dir, repo_email_pipeline_root=root, require_git_ignored=False
    )
    rows = json.loads((out_dir / "attachment_manifest.json").read_text())["tenders"][0][
        "attachments"
    ]
    assert rows[1]["extraction_reused_from_attachment_id"] == rows[0]["attachment_id"]
    assert rows[1]["evidence_attachment_id"] == rows[0]["attachment_id"]

    chunk_ids = {
        json.loads(line)["attachment_id"]
        for line in (out_dir / "evidence_chunks.jsonl").read_text().splitlines()
    }
    assert rows[1]["evidence_attachment_id"] in chunk_ids


# --- Phase 8: idlicitacion security contract ----------------------------------


def test_codigo_url_is_encoded_and_cannot_inject_extra_parameters() -> None:
    from origenlab_email_pipeline.chilecompra_api import (
        build_licitacion_detail_url_for_codigo,
        is_portal_detail_url,
    )
    from urllib.parse import parse_qs, urlparse

    url = build_licitacion_detail_url_for_codigo("1702-20-L126&enc=INJECTED")
    query = parse_qs(urlparse(url).query)
    assert set(query) == {"idlicitacion"}, "injected parameters must be encoded away"
    assert query["idlicitacion"] == ["1702-20-L126&enc=INJECTED"]
    assert "&enc=INJECTED" not in url
    assert is_portal_detail_url(url)


def test_idlicitacion_does_not_widen_scheme_host_or_path() -> None:
    from origenlab_email_pipeline.chilecompra_api import is_portal_detail_url

    assert not is_portal_detail_url(
        "http://www.mercadopublico.cl/Procurement/Modules/RFB/"
        "DetailsAcquisition.aspx?idlicitacion=1"
    )
    assert not is_portal_detail_url(
        "https://evil.example.com/Procurement/Modules/RFB/"
        "DetailsAcquisition.aspx?idlicitacion=1"
    )
    assert not is_portal_detail_url(
        "https://www.mercadopublico.cl.evil.example.com/Procurement/Modules/RFB/"
        "DetailsAcquisition.aspx?idlicitacion=1"
    )
    assert not is_portal_detail_url(
        "https://user:pass@www.mercadopublico.cl/Procurement/Modules/RFB/"
        "DetailsAcquisition.aspx?idlicitacion=1"
    )
    assert not is_portal_detail_url(
        "https://www.mercadopublico.cl/Procurement/Modules/Attachment/"
        "VerAntecedentes.aspx?idlicitacion=1"
    )
    assert not is_portal_detail_url(
        "https://www.mercadopublico.cl/Procurement/Modules/RFB/"
        "DetailsAcquisition.aspx?idlicitacion="
    )


def test_codigo_redirect_target_is_validated_before_follow() -> None:
    from origenlab_email_pipeline.chilecompra_api import (
        ChileCompraPortalError,
        _ValidatedPortalRedirectHandler,
    )
    from urllib.request import Request

    handler = _ValidatedPortalRedirectHandler()
    request = Request(
        "https://www.mercadopublico.cl/Procurement/Modules/RFB/"
        "DetailsAcquisition.aspx?idlicitacion=1702-20-L126"
    )
    # The real portal answers idlicitacion with a same-endpoint qs redirect.
    allowed = handler.redirect_request(
        request, None, 302, "Found", {}, "DetailsAcquisition.aspx?qs=TOKEN"
    )
    assert allowed is not None
    for hostile in (
        "https://evil.example.com/steal",
        "http://www.mercadopublico.cl/Procurement/Modules/RFB/DetailsAcquisition.aspx?qs=T",
        "https://127.0.0.1/admin",
    ):
        with pytest.raises(ChileCompraPortalError):
            handler.redirect_request(request, None, 302, "Found", {}, hostile)


def test_codigo_is_redacted_from_shareable_artifacts_when_embedded_in_a_url() -> None:
    from origenlab_email_pipeline.chilecompra_anexo_evidence.redaction import (
        assert_no_portal_tokens,
        redact_portal_tokens,
    )

    text = (
        "https://www.mercadopublico.cl/Procurement/Modules/RFB/"
        "DetailsAcquisition.aspx?qs=SECRETO&enc=OTRO"
    )
    assert_no_portal_tokens(redact_portal_tokens(text), where="test")
