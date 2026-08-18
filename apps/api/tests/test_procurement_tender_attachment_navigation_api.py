"""GET /operator/procurement/tenders/{tender_code}/attachment-navigation.

Ephemeral Mercado Público navigation only: W1-gated, no-store, never
persisted or published. The underlying portal resolver is mocked here so this
API test module performs zero external network I/O.
"""

from __future__ import annotations

from pathlib import Path

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from origenlab_email_pipeline.chilecompra_api import PortalAttachmentNavigation  # noqa: E402

from origenlab_api.main import create_app  # noqa: E402
from origenlab_api.settings import Settings, get_settings  # noqa: E402

from test_procurement_institutions_api import (  # noqa: E402
    _current_opportunity_row,
    _write_bundle,
)

_TENDER_CODE = "1057890-1-LE26"
_DIRECT_URL = (
    "https://www.mercadopublico.cl/Procurement/Modules/"
    "Attachment/ViewAttachmentLC.aspx?enc=EPHEMERAL123"
)


def _queue_rows(tender_code: str) -> dict[str, list[dict]]:
    return {
        "current_opportunity_queue": [
            _current_opportunity_row(
                institution_id="inst-sag",
                display_name="SAG",
                tender_code=tender_code,
                equipment_category="balance",
                eligibility_reason_codes=[],
            )
        ]
    }


def _client(
    tmp_path: Path,
    *,
    w1_with_bundle: bool = True,
    tender_code: str = _TENDER_CODE,
) -> TestClient:
    w1_dest = tmp_path / "institution_prospects"
    if w1_with_bundle:
        _write_bundle(w1_dest, queue_rows=_queue_rows(tender_code))

    app = create_app()
    app.dependency_overrides[get_settings] = lambda: Settings(
        institution_prospect_dir=w1_dest
    )
    return TestClient(app)


def _navigation_url(tender_code: str = _TENDER_CODE) -> str:
    return f"/operator/procurement/tenders/{tender_code}/attachment-navigation"


def test_direct_attachment_navigation_is_ephemeral_and_no_store(
    tmp_path: Path,
    monkeypatch,
) -> None:
    import origenlab_api.services.tender_attachment_navigation_service as service

    calls: list[str] = []

    def fake_resolver(tender_code: str) -> PortalAttachmentNavigation:
        calls.append(tender_code)
        return PortalAttachmentNavigation(
            destination_kind="attachments",
            url=_DIRECT_URL,
        )

    monkeypatch.setattr(
        service,
        "resolve_licitacion_attachment_navigation",
        fake_resolver,
    )

    client = _client(tmp_path)
    response = client.get(_navigation_url())

    assert response.status_code == 200
    assert response.json() == {
        "tender_code": _TENDER_CODE,
        "destination_kind": "attachments",
        "url": _DIRECT_URL,
        "ephemeral": True,
    }
    assert calls == [_TENDER_CODE]
    assert "no-store" in response.headers["cache-control"]
    assert "private" in response.headers["cache-control"]
    assert response.headers["pragma"] == "no-cache"


def test_tender_page_fallback_is_returned_without_fabricating_attachment_url(
    tmp_path: Path,
    monkeypatch,
) -> None:
    import origenlab_api.services.tender_attachment_navigation_service as service

    fallback_url = (
        "https://www.mercadopublico.cl/Procurement/Modules/"
        "RFB/DetailsAcquisition.aspx?idlicitacion=1057890-1-LE26"
    )

    monkeypatch.setattr(
        service,
        "resolve_licitacion_attachment_navigation",
        lambda tender_code: PortalAttachmentNavigation(
            destination_kind="tender",
            url=fallback_url,
        ),
    )

    client = _client(tmp_path)
    response = client.get(_navigation_url())

    assert response.status_code == 200
    data = response.json()
    assert data["destination_kind"] == "tender"
    assert data["url"] == fallback_url
    assert "enc=" not in data["url"]
    assert data["ephemeral"] is True


def test_exact_tender_code_only_is_404_before_portal_resolution(
    tmp_path: Path,
    monkeypatch,
) -> None:
    import origenlab_api.services.tender_attachment_navigation_service as service

    called = False

    def should_not_run(tender_code: str) -> PortalAttachmentNavigation:
        nonlocal called
        called = True
        raise AssertionError("portal resolver must not run")

    monkeypatch.setattr(
        service,
        "resolve_licitacion_attachment_navigation",
        should_not_run,
    )

    client = _client(tmp_path)
    response = client.get(_navigation_url("999999-1-LE26"))

    assert response.status_code == 404
    assert called is False


def test_degraded_w1_is_503_before_portal_resolution(
    tmp_path: Path,
    monkeypatch,
) -> None:
    import origenlab_api.services.tender_attachment_navigation_service as service

    called = False

    def should_not_run(tender_code: str) -> PortalAttachmentNavigation:
        nonlocal called
        called = True
        raise AssertionError("portal resolver must not run")

    monkeypatch.setattr(
        service,
        "resolve_licitacion_attachment_navigation",
        should_not_run,
    )

    client = _client(tmp_path, w1_with_bundle=False)
    response = client.get(_navigation_url())

    assert response.status_code == 503
    assert called is False


def test_navigation_request_does_not_change_published_tender_state(
    tmp_path: Path,
    monkeypatch,
) -> None:
    import origenlab_api.services.tender_attachment_navigation_service as service

    monkeypatch.setattr(
        service,
        "resolve_licitacion_attachment_navigation",
        lambda tender_code: PortalAttachmentNavigation(
            destination_kind="attachments",
            url=_DIRECT_URL,
        ),
    )

    client = _client(tmp_path)

    before = client.get(f"/operator/procurement/tenders/{_TENDER_CODE}")
    assert before.status_code == 200

    navigation = client.get(_navigation_url())
    assert navigation.status_code == 200
    assert "enc=EPHEMERAL123" in navigation.json()["url"]

    after = client.get(f"/operator/procurement/tenders/{_TENDER_CODE}")
    assert after.status_code == 200
    assert after.json() == before.json()

    # The opaque navigation token must never leak into the published tender
    # read model even though it was intentionally returned by the ephemeral
    # navigation endpoint.
    assert "EPHEMERAL123" not in after.text
