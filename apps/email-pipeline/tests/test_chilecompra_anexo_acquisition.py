"""Inventory-before-download and streaming acquisition contracts.

Also re-asserts the #438 safety behaviors these new entry points inherit, so the
evidence lane cannot quietly weaken them.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from origenlab_email_pipeline.chilecompra_anexo_evidence.acquire import (
    AttachmentBudgetError,
    PortalAttachmentSource,
)
from origenlab_email_pipeline.chilecompra_anexo_evidence.planner import (
    build_tender_bundle,
)
from origenlab_email_pipeline.chilecompra_api import (
    ChileCompraPortalError,
    DownloadedAttachment,
    fetch_licitacion_attachments,
    iter_licitacion_attachments,
    list_licitacion_attachments,
    save_licitacion_attachments,
)

_DETAIL_URL = (
    "https://www.mercadopublico.cl/Procurement/Modules/RFB/"
    "DetailsAcquisition.aspx?qs=6kaRy83zbKUYD9NMoSijTg=="
)
_LISTING_ONE = (
    "https://www.mercadopublico.cl/Procurement/Modules/Attachment/"
    "VerAntecedentes.aspx?enc=AAA%2fBBB"
)
_LISTING_TWO = (
    "https://www.mercadopublico.cl/Procurement/Modules/Attachment/"
    "VerAntecedentes.aspx?enc=CCC%2fDDD"
)

_DETAIL_HTML = """
<html><body>
  <a href="../Attachment/VerAntecedentes.aspx?enc=AAA%2fBBB">Ver anexos 1</a>
  <a href="../Attachment/VerAntecedentes.aspx?enc=CCC%2fDDD">Ver anexos 2</a>
</body></html>
"""


def _listing_html(rows: int, *, prefix: str) -> str:
    parts = [
        "<html><body><form>",
        '<input type="hidden" name="__VIEWSTATE" value="/wEPDwUxMjM=" />',
        '<input type="hidden" name="__EVENTVALIDATION" value="/wEdAAQ=" />',
        '<table id="grdAttachment">',
        '<tr class="cssFwkHeaderTableRow"><th>Anexo</th><th>Tipo</th>'
        "<th>Desc</th><th>Fecha</th><th>Acciones</th></tr>",
    ]
    for index in range(rows):
        control = f"ctl{index + 2:02d}"
        parts.append(
            '<tr class="cssFwkItemStyle ajustar">'
            f'<td><span id="grdAttachment_{control}_grdLblSourceFileName">'
            f"{prefix}_{index}.pdf</span></td>"
            "<td>Anexos Tecnicos</td>"
            f'<td><span id="grdAttachment_{control}_grdLblFileDescription">'
            "Anexo</span></td>"
            f'<td><span id="grdAttachment_{control}_grdLblFileDate">'
            "02-06-2026 15:36:39</span></td>"
            f'<td><input type="image" name="grdAttachment${control}$grdIbtnView" '
            f'id="grdAttachment_{control}_grdIbtnView" /></td></tr>'
        )
    parts.append("</table></form></body></html>")
    return "".join(parts)


def _response(body: bytes, *, headers=None, url=None):
    response = MagicMock()
    response.__enter__.return_value = response
    response.__exit__.return_value = False
    response.status = 200
    response.getcode.return_value = 200
    response.read.side_effect = lambda *args: body
    response.headers = headers or {}
    response.url = url
    return response


def _opener(rows_first: int = 2, rows_second: int = 3):
    calls: list[tuple[str, str]] = []

    def opener(request, timeout=None):
        url = request.full_url
        calls.append((request.get_method(), url))
        if request.data is not None:
            return _response(
                b"%PDF-1.7\ncuerpo del anexo\n%%EOF",
                headers={"Content-Type": "application/pdf"},
                url=url,
            )
        if "DetailsAcquisition" in url:
            return _response(_DETAIL_HTML.encode("utf-8"), url=url)
        prefix = "uno" if "AAA" in url else "dos"
        rows = rows_first if prefix == "uno" else rows_second
        return _response(_listing_html(rows, prefix=prefix).encode("utf-8"), url=url)

    opener.calls = calls
    return opener


# --- Inventory before download ------------------------------------------------


def test_inventory_enumerates_all_rows_without_any_download() -> None:
    opener = _opener(rows_first=2, rows_second=3)
    inventory = list_licitacion_attachments(_DETAIL_URL, opener=opener)

    assert inventory.attachments_discovered == 5
    assert inventory.listing_urls == (_LISTING_ONE, _LISTING_TWO)
    assert [method for method, _ in opener.calls] == ["GET", "GET", "GET"]
    assert not any(method == "POST" for method, _ in opener.calls)
    assert set(inventory.listing_form_fields) == {_LISTING_ONE, _LISTING_TWO}
    assert inventory.listing_form_fields[_LISTING_ONE]["__VIEWSTATE"] == "/wEPDwUxMjM="


def test_inventory_keeps_rows_with_identical_names() -> None:
    opener = _opener(rows_first=2, rows_second=2)
    inventory = list_licitacion_attachments(_DETAIL_URL, opener=opener)
    names = [a.nombre for a in inventory.attachments]
    assert names == ["uno_0.pdf", "uno_1.pdf", "dos_0.pdf", "dos_1.pdf"]
    assert len(names) == len(inventory.attachments)


def test_inventory_ordinal_maps_row_to_listing_page() -> None:
    opener = _opener(rows_first=1, rows_second=2)
    inventory = list_licitacion_attachments(_DETAIL_URL, opener=opener)
    ordinals = [inventory.listing_ordinal_of(a) for a in inventory.attachments]
    assert ordinals == [0, 1, 1]


# --- Streaming download -------------------------------------------------------


def test_iter_yields_one_attachment_at_a_time() -> None:
    opener = _opener(rows_first=2, rows_second=1)
    seen: list[str] = []
    for download in iter_licitacion_attachments(_DETAIL_URL, opener=opener):
        assert isinstance(download, DownloadedAttachment)
        seen.append(download.filename)
    assert seen == ["uno_0.pdf", "uno_1.pdf", "dos_0.pdf"]
    posts = [url for method, url in opener.calls if method == "POST"]
    assert len(posts) == 3


def test_iter_reuses_a_single_cookie_session() -> None:
    opener = _opener(rows_first=1, rows_second=1)
    list(iter_licitacion_attachments(_DETAIL_URL, opener=opener))
    # One detail GET + two listing GETs + two postbacks, all on the same opener.
    assert len(opener.calls) == 5


def test_iter_fails_before_downloading_when_count_budget_exceeded() -> None:
    opener = _opener(rows_first=30, rows_second=30)
    stream = iter_licitacion_attachments(
        _DETAIL_URL, opener=opener, max_attachment_count=50
    )
    with pytest.raises(ChileCompraPortalError) as exc:
        next(stream)
    assert "attachment_count_budget_exceeded" in str(exc.value)
    assert "discovered 60" in str(exc.value)
    assert not any(method == "POST" for method, _ in opener.calls)


def test_iter_supports_more_than_fifty_attachments_when_budget_allows() -> None:
    opener = _opener(rows_first=40, rows_second=35)
    downloads = list(
        iter_licitacion_attachments(
            _DETAIL_URL,
            opener=opener,
            max_attachment_count=300,
            max_total_bytes=64 * 1024 * 1024,
        )
    )
    assert len(downloads) == 75


def test_iter_enforces_total_byte_budget() -> None:
    opener = _opener(rows_first=3, rows_second=0)
    stream = iter_licitacion_attachments(_DETAIL_URL, opener=opener, max_total_bytes=40)
    collected = []
    with pytest.raises(ChileCompraPortalError, match="total_bytes_budget_exceeded"):
        for download in stream:
            collected.append(download)
    assert len(collected) < 3


def test_iter_accepts_a_prebuilt_inventory_without_refetching() -> None:
    opener = _opener(rows_first=1, rows_second=1)
    inventory = list_licitacion_attachments(_DETAIL_URL, opener=opener)
    before = len(opener.calls)
    downloads = list(
        iter_licitacion_attachments(_DETAIL_URL, opener=opener, inventory=inventory)
    )
    assert len(downloads) == 2
    # Only the two postbacks were added; listings were not re-fetched.
    assert len(opener.calls) - before == 2


# --- Portal source wiring -----------------------------------------------------


def test_portal_source_builds_a_reconciled_bundle() -> None:
    opener = _opener(rows_first=2, rows_second=1)
    source = PortalAttachmentSource(detail_url=_DETAIL_URL, opener=opener)
    bundle = build_tender_bundle("1702-20-L126", source)

    assert bundle.source_kind == "mercadopublico_portal"
    assert bundle.listing_page_count == 2
    assert bundle.attachments_discovered == 3
    assert bundle.attachments_downloaded == 3
    assert len(bundle.attachments) == bundle.attachments_discovered
    assert [r.listing_ordinal for r in bundle.attachments] == [0, 0, 1]
    assert all(r.tipo == "Anexos Tecnicos" for r in bundle.attachments)
    assert all(r.sha256 for r in bundle.attachments)


def test_portal_source_count_budget_records_discovered_count() -> None:
    opener = _opener(rows_first=5, rows_second=5)
    source = PortalAttachmentSource(
        detail_url=_DETAIL_URL, opener=opener, max_attachment_count=4
    )
    bundle = build_tender_bundle("T-BUDGET", source)
    assert bundle.attachments_discovered == 10
    assert bundle.attachments_downloaded == 0
    assert len(bundle.attachments) == 10
    assert "attachment_count_budget_exceeded" in bundle.incomplete_reason_codes
    assert bundle.bundle_complete is False


def test_portal_source_records_per_attachment_download_failure() -> None:
    failing_calls = {"count": 0}

    def opener(request, timeout=None):
        url = request.full_url
        if request.data is not None:
            failing_calls["count"] += 1
            if failing_calls["count"] == 1:
                return _response(
                    b"<html><body>error</body></html>",
                    headers={"Content-Type": "text/html"},
                    url=url,
                )
            return _response(
                b"%PDF-1.7\nok\n%%EOF",
                headers={"Content-Type": "application/pdf"},
                url=url,
            )
        if "DetailsAcquisition" in url:
            return _response(_DETAIL_HTML.encode("utf-8"), url=url)
        prefix = "uno" if "AAA" in url else "dos"
        return _response(_listing_html(1, prefix=prefix).encode("utf-8"), url=url)

    source = PortalAttachmentSource(detail_url=_DETAIL_URL, opener=opener)
    bundle = build_tender_bundle("T-FAIL", source)
    outcomes = [r.outcome for r in bundle.attachments]
    assert outcomes.count("download_failed") == 1
    assert bundle.attachments_downloaded == 1
    assert len(bundle.attachments) == 2
    assert bundle.bundle_complete is False


# --- #438 regression guards ---------------------------------------------------


def test_legacy_fetch_api_is_unchanged() -> None:
    opener = _opener(rows_first=2, rows_second=0)
    downloads = fetch_licitacion_attachments(_DETAIL_URL, opener=opener)
    assert [d.filename for d in downloads] == ["uno_0.pdf", "uno_1.pdf"]
    assert all(d.is_pdf for d in downloads)
    assert isinstance(downloads, list)


def test_legacy_save_api_is_unchanged(tmp_path) -> None:
    opener = _opener(rows_first=2, rows_second=0)
    written = save_licitacion_attachments(_DETAIL_URL, tmp_path / "anexos", opener=opener)
    assert [p.name for p in written] == ["uno_0.pdf", "uno_1.pdf"]
    assert all(p.read_bytes().startswith(b"%PDF") for p in written)


def test_new_entry_points_reject_non_portal_detail_urls() -> None:
    with pytest.raises(ChileCompraPortalError, match="Refusing non-detail portal URL"):
        list_licitacion_attachments("https://evil.example.com/x.aspx?qs=abc")
    with pytest.raises(ChileCompraPortalError, match="Refusing non-detail portal URL"):
        next(iter_licitacion_attachments("https://evil.example.com/x.aspx?qs=abc"))


def test_new_entry_points_do_not_leak_qs_tokens_in_errors() -> None:
    def failing_opener(request, timeout=None):
        raise AssertionError("should not be called")

    bad_detail = (
        "https://www.mercadopublico.cl/Procurement/Modules/Other/Page.aspx"
        "?qs=SUPERSECRETTOKEN"
    )
    with pytest.raises(ChileCompraPortalError) as exc:
        list_licitacion_attachments(bad_detail, opener=failing_opener)
    assert "SUPERSECRETTOKEN" not in str(exc.value)


def test_inventory_enforces_html_size_cap() -> None:
    def big_opener(request, timeout=None):
        return _response(b"<html>" + b"x" * 5000 + b"</html>", url=request.full_url)

    with pytest.raises(ChileCompraPortalError, match="exceeds max_bytes"):
        list_licitacion_attachments(_DETAIL_URL, opener=big_opener, html_max_bytes=1000)


def test_codigo_addressed_detail_url_is_accepted_but_stays_on_endpoint() -> None:
    from origenlab_email_pipeline.chilecompra_api import (
        build_licitacion_detail_url_for_codigo,
        is_portal_detail_url,
    )

    url = build_licitacion_detail_url_for_codigo("1003473-64-LE26")
    assert url.startswith(
        "https://www.mercadopublico.cl/Procurement/Modules/RFB/DetailsAcquisition.aspx?"
    )
    assert "idlicitacion=1003473-64-LE26" in url
    assert is_portal_detail_url(url)
    # Widening the key must not widen host, scheme, or path.
    assert not is_portal_detail_url(
        "https://evil.example.com/Procurement/Modules/RFB/"
        "DetailsAcquisition.aspx?idlicitacion=1"
    )
    assert not is_portal_detail_url(
        "https://www.mercadopublico.cl/Procurement/Modules/Other/Page.aspx?idlicitacion=1"
    )
    assert not is_portal_detail_url(
        "http://www.mercadopublico.cl/Procurement/Modules/RFB/"
        "DetailsAcquisition.aspx?idlicitacion=1"
    )
    with pytest.raises(ValueError, match="codigo is required"):
        build_licitacion_detail_url_for_codigo("  ")


def test_codigo_addressed_detail_url_works_end_to_end() -> None:
    codigo_url = (
        "https://www.mercadopublico.cl/Procurement/Modules/RFB/"
        "DetailsAcquisition.aspx?idlicitacion=1702-20-L126"
    )
    opener = _opener(rows_first=1, rows_second=1)
    inventory = list_licitacion_attachments(codigo_url, opener=opener)
    assert inventory.attachments_discovered == 2


def test_portal_budget_error_carries_reason_code() -> None:
    error = AttachmentBudgetError("total_bytes_budget_exceeded", "too big", discovered=7)
    assert error.reason_code == "total_bytes_budget_exceeded"
    assert error.discovered == 7
