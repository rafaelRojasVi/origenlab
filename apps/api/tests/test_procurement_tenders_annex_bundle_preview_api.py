"""POST /operator/procurement/tenders/{tender_code}/annex-bundle/preview.

Preview-only operator ZIP import over HTTP: same W1 current_opportunity_queue
actionability gate as GET /tenders/{tender_code}, same #493 bounded ZIP
importer, zero persistence, zero publication, zero contact/outreach
authorization. See services/tender_annex_preview_service.py.
"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from origenlab_api.main import create_app
from origenlab_api.settings import Settings, get_settings

from test_procurement_institutions_api import (  # noqa: E402  (path fixture reuse)
    _current_opportunity_row,
    _write_bundle,
)

_TENDER_CODE = "1057890-1-LE26"
_ZIP_CONTENT_TYPE = "application/zip"


def _make_zip(entries: list[tuple[str, bytes]]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, data in entries:
            archive.writestr(name, data)
    return buffer.getvalue()


def _make_traversal_zip() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("safe.txt", b"contenido seguro")
        archive.writestr("../escape.txt", b"fuera del directorio")
    return buffer.getvalue()


def _sample_pdf() -> bytes:
    # Minimal but well-formed enough for detect_format/extraction to treat it
    # as a real (if content-empty) PDF, mirroring email-pipeline's synthetic
    # fixtures without importing across the app boundary.
    return b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"


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


def _client(tmp_path: Path, *, w1_with_bundle: bool = True, tender_code: str = _TENDER_CODE) -> TestClient:
    w1_dest = tmp_path / "institution_prospects"
    if w1_with_bundle:
        _write_bundle(w1_dest, queue_rows=_queue_rows(tender_code))
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: Settings(institution_prospect_dir=w1_dest)
    return TestClient(app)


def _preview_url(tender_code: str = _TENDER_CODE) -> str:
    return f"/operator/procurement/tenders/{tender_code}/annex-bundle/preview"


# --- happy path ----------------------------------------------------------------


def test_valid_zip_declare_complete_false_returns_unknown_completeness(tmp_path: Path) -> None:
    client = _client(tmp_path)
    payload = _make_zip([("planilla.csv", b"col1,col2\n1,2\n")])
    r = client.post(_preview_url(), content=payload, headers={"Content-Type": _ZIP_CONTENT_TYPE})
    assert r.status_code == 200
    data = r.json()
    assert data["result"] == "imported"
    assert data["acquisition"]["completeness_state"] == "unknown"
    assert data["acquisition"]["operator_declared_complete"] is False


def test_valid_zip_declare_complete_true_returns_complete_when_extraction_complete(tmp_path: Path) -> None:
    client = _client(tmp_path)
    payload = _make_zip([("planilla.csv", b"col1,col2\n1,2\n")])
    r = client.post(
        f"{_preview_url()}?declare_complete=true",
        content=payload,
        headers={"Content-Type": _ZIP_CONTENT_TYPE},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["acquisition"]["completeness_state"] == "complete"
    assert data["acquisition"]["operator_declared_complete"] is True


def test_response_contains_full_t1_shape_facts_items_evidence(tmp_path: Path) -> None:
    client = _client(tmp_path)
    payload = _make_zip([("tecnico.pdf", _sample_pdf())])
    r = client.post(_preview_url(), content=payload, headers={"Content-Type": _ZIP_CONTENT_TYPE})
    assert r.status_code == 200
    data = r.json()
    assert "tender_facts" in data
    assert "items" in data
    assert "coverage" in data
    assert isinstance(data["tender_facts"], list)


def test_safety_flags_always_false(tmp_path: Path) -> None:
    client = _client(tmp_path)
    payload = _make_zip([("a.csv", b"x")])
    r = client.post(
        f"{_preview_url()}?declare_complete=true",
        content=payload,
        headers={"Content-Type": _ZIP_CONTENT_TYPE},
    )
    data = r.json()
    assert data["published"] is False
    assert data["persisted"] is False
    assert data["contact_authorization"] is False
    assert data["outreach_authorization"] is False


def test_valid_zip_with_rejected_member_still_returns_200_preview(tmp_path: Path) -> None:
    client = _client(tmp_path)
    payload = _make_traversal_zip()
    r = client.post(_preview_url(), content=payload, headers={"Content-Type": _ZIP_CONTENT_TYPE})
    assert r.status_code == 200
    data = r.json()
    assert data["result"] == "imported"
    assert len(data["archive"]["rejected_entries"]) == 1
    assert "safe.txt" not in data["archive"]["rejected_entries"][0]


# --- error semantics -------------------------------------------------------------


def test_malformed_zip_returns_structured_422(tmp_path: Path) -> None:
    client = _client(tmp_path)
    r = client.post(_preview_url(), content=b"not a zip at all", headers={"Content-Type": _ZIP_CONTENT_TYPE})
    assert r.status_code == 422
    body = r.json()
    assert body["error"]["code"]
    assert "traceback" not in body["error"]["message"].lower()


def test_wrong_content_type_returns_415(tmp_path: Path) -> None:
    client = _client(tmp_path)
    payload = _make_zip([("a.csv", b"x")])
    r = client.post(_preview_url(), content=payload, headers={"Content-Type": "application/octet-stream"})
    assert r.status_code == 415


def test_oversized_upload_returns_413(tmp_path: Path, monkeypatch) -> None:
    import origenlab_api.routes.institutions as institutions_module

    monkeypatch.setattr(institutions_module, "_MAX_ANNEX_BUNDLE_UPLOAD_BYTES", 10)
    client = _client(tmp_path)
    payload = _make_zip([("a.csv", b"x" * 1000)])
    r = client.post(_preview_url(), content=payload, headers={"Content-Type": _ZIP_CONTENT_TYPE})
    assert r.status_code == 413


def test_oversized_content_length_rejected_before_body_read(tmp_path: Path, monkeypatch) -> None:
    import origenlab_api.routes.institutions as institutions_module

    monkeypatch.setattr(institutions_module, "_MAX_ANNEX_BUNDLE_UPLOAD_BYTES", 10)
    client = _client(tmp_path)
    payload = _make_zip([("a.csv", b"x")])
    r = client.post(
        _preview_url(),
        content=payload,
        headers={"Content-Type": _ZIP_CONTENT_TYPE, "Content-Length": str(10_000_000)},
    )
    assert r.status_code == 413


# --- W1 actionability gate --------------------------------------------------------


def test_exact_tender_code_only(tmp_path: Path) -> None:
    other_code = "999999-1-LE26"
    client = _client(tmp_path, tender_code=_TENDER_CODE)  # only _TENDER_CODE in queue
    payload = _make_zip([("a.csv", b"x")])
    r = client.post(_preview_url(other_code), content=payload, headers={"Content-Type": _ZIP_CONTENT_TYPE})
    assert r.status_code == 404


def test_tender_absent_from_healthy_w1_is_404(tmp_path: Path) -> None:
    client = _client(tmp_path)
    payload = _make_zip([("a.csv", b"x")])
    r = client.post(
        _preview_url("000000-0-LE00"), content=payload, headers={"Content-Type": _ZIP_CONTENT_TYPE}
    )
    assert r.status_code == 404


def test_degraded_w1_fails_closed_503(tmp_path: Path) -> None:
    client = _client(tmp_path, w1_with_bundle=False)
    payload = _make_zip([("a.csv", b"x")])
    r = client.post(_preview_url(), content=payload, headers={"Content-Type": _ZIP_CONTENT_TYPE})
    assert r.status_code == 503


# --- safety: no path leakage, no persistence --------------------------------------


def test_error_response_never_leaks_filesystem_path(tmp_path: Path) -> None:
    client = _client(tmp_path)
    r = client.post(_preview_url(), content=b"garbage", headers={"Content-Type": _ZIP_CONTENT_TYPE})
    assert r.status_code == 422
    body_text = r.text
    assert str(tmp_path) not in body_text
    assert "/home/" not in body_text
    assert "Traceback" not in body_text


def test_refresh_after_preview_returns_unchanged_published_state(tmp_path: Path) -> None:
    """The most important safety check for this feature: a preview upload
    must never leak into the published/read-only tender-detail view.
    Simulates "operator uploads a ZIP, then refreshes the browser tab" by
    calling GET /tenders/{tender_code} before and after the preview POST and
    asserting byte-identical JSON."""
    client = _client(tmp_path)
    before = client.get(f"/operator/procurement/tenders/{_TENDER_CODE}")
    assert before.status_code == 200

    payload = _make_zip([("tecnico.pdf", _sample_pdf())])
    preview = client.post(
        f"{_preview_url()}?declare_complete=true",
        content=payload,
        headers={"Content-Type": _ZIP_CONTENT_TYPE},
    )
    assert preview.status_code == 200
    assert preview.json()["result"] == "imported"

    after = client.get(f"/operator/procurement/tenders/{_TENDER_CODE}")
    assert after.status_code == 200
    assert after.json() == before.json()


def test_no_files_written_to_tmp_path_by_request(tmp_path: Path) -> None:
    client = _client(tmp_path)
    before = sorted(p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*") if p.is_file())
    payload = _make_zip([("tecnico.pdf", _sample_pdf())])
    r = client.post(
        f"{_preview_url()}?declare_complete=true",
        content=payload,
        headers={"Content-Type": _ZIP_CONTENT_TYPE},
    )
    assert r.status_code == 200
    after = sorted(p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*") if p.is_file())
    assert before == after
