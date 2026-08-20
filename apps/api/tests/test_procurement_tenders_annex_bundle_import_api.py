"""Persistent operator annex import API contract.

The preview endpoint remains non-mutating. The explicit import endpoint may
persist only validated structured T1/provenance for an already-actionable W1
tender. Raw ZIP files are never stored, other tenders are untouched, and a
malformed saved overlay fails closed.
"""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import pytest

from origenlab_email_pipeline.chilecompra_anexo_evidence.operator_import import (
    OperatorZipAttachmentSource,
)
from origenlab_email_pipeline.commercial_procurement_anexo_tender_terms.operator_annex_bundle_preview import (
    build_operator_annex_bundle_preview,
)

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from origenlab_api.main import create_app  # noqa: E402
from origenlab_api.settings import Settings, get_settings  # noqa: E402

from test_procurement_institutions_api import (  # noqa: E402
    _current_opportunity_row,
    _write_bundle,
)

_TENDER_A = "1057890-1-LE26"
_TENDER_B = "745712-19-LP26"
_ZIP_CONTENT_TYPE = "application/zip"


def _make_zip(entries: list[tuple[str, bytes]]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, data in entries:
            archive.writestr(name, data)
    return buffer.getvalue()


def _queue_rows(*tender_codes: str) -> dict[str, list[dict]]:
    return {
        "current_opportunity_queue": [
            _current_opportunity_row(
                institution_id=f"inst-{index}",
                display_name=f"Buyer {index}",
                tender_code=tender_code,
                equipment_category="centrifuge",
                eligibility_reason_codes=[],
            )
            for index, tender_code in enumerate(tender_codes, start=1)
        ]
    }


def _client(
    tmp_path: Path,
    *,
    tender_codes: tuple[str, ...] = (_TENDER_A,),
    healthy_w1: bool = True,
) -> tuple[TestClient, Path]:
    w1_dir = tmp_path / "institution_prospects"
    t1_dir = tmp_path / "tender_terms"
    operator_dir = tmp_path / "operator_tender_imports"

    if healthy_w1:
        _write_bundle(w1_dir, queue_rows=_queue_rows(*tender_codes))

    app = create_app()
    app.dependency_overrides[get_settings] = lambda: Settings(
        institution_prospect_dir=w1_dir,
        tender_terms_dir=t1_dir,
        operator_tender_import_dir=operator_dir,
    )
    return TestClient(app), operator_dir


def _preview_url(tender_code: str = _TENDER_A) -> str:
    return f"/operator/procurement/tenders/{tender_code}/annex-bundle/preview"


def _import_url(tender_code: str = _TENDER_A) -> str:
    return f"/operator/procurement/tenders/{tender_code}/annex-bundle/import"


def _detail_url(tender_code: str = _TENDER_A) -> str:
    return f"/operator/procurement/tenders/{tender_code}"


def _valid_payload() -> bytes:
    return _make_zip(
        [
            (
                "bases.csv",
                (
                    b"campo,valor\n"
                    b"descripcion,centrifuga refrigerada\n"
                    b"presupuesto,28419400 CLP\n"
                ),
            )
        ]
    )


def test_preview_remains_non_persistent(tmp_path: Path) -> None:
    client, operator_dir = _client(tmp_path)

    response = client.post(
        f"{_preview_url()}?declare_complete=true",
        content=_valid_payload(),
        headers={"Content-Type": _ZIP_CONTENT_TYPE},
    )

    assert response.status_code == 200
    assert response.json()["persisted"] is False
    assert response.json()["published"] is False
    assert not operator_dir.exists()


def test_explicit_import_persists_and_survives_followup_get(
    tmp_path: Path,
) -> None:
    client, operator_dir = _client(tmp_path)

    before = client.get(_detail_url())
    assert before.status_code == 200
    assert before.json()["t1_published"] is False

    imported = client.post(
        f"{_import_url()}?declare_complete=true",
        content=_valid_payload(),
        headers={"Content-Type": _ZIP_CONTENT_TYPE},
    )

    assert imported.status_code == 200
    imported_data = imported.json()
    assert imported_data["result"] == "imported"
    assert imported_data["persisted"] is True
    assert imported_data["published"] is True
    assert imported_data["contact_authorization"] is False
    assert imported_data["outreach_authorization"] is False

    saved_files = list(operator_dir.iterdir())
    assert len(saved_files) == 1
    assert saved_files[0].name == f"{_TENDER_A.casefold()}.json"

    # This second GET is the HTTP equivalent of refreshing the browser:
    # the response must come from disk, not React/request memory.
    after = client.get(_detail_url())
    assert after.status_code == 200
    detail = after.json()

    assert detail["t1_published"] is True
    assert detail["t1_meta"]["reduced_mode"] is False
    assert detail["t1_meta"]["published"] is True
    assert detail["t1_meta"]["canonical_reason"] == "operator_tender_import"
    assert detail["t1_meta"]["source_kind"] == "operator_annex_import"
    assert detail["coverage"] is not None
    assert detail["coverage"]["attachments_discovered"] == 1
    assert detail["coverage"]["attachments_downloaded"] == 1


def test_import_persists_structured_json_but_never_raw_zip_file(
    tmp_path: Path,
) -> None:
    client, operator_dir = _client(tmp_path)
    payload = _valid_payload()

    imported = client.post(
        f"{_import_url()}?declare_complete=true",
        content=payload,
        headers={"Content-Type": _ZIP_CONTENT_TYPE},
    )
    assert imported.status_code == 200

    files = [p for p in operator_dir.rglob("*") if p.is_file()]
    assert len(files) == 1
    assert files[0].suffix == ".json"
    assert not list(operator_dir.rglob("*.zip"))

    envelope = json.loads(files[0].read_text(encoding="utf-8"))
    assert envelope["contract_version"] == "operator_tender_import_v1"
    assert "terms" in envelope
    assert "archive" in envelope

    serialized = files[0].read_text(encoding="utf-8")
    assert "payload_bytes" not in serialized
    assert "zip_bytes" not in serialized
    assert "raw_zip" not in serialized


def test_import_of_one_tender_does_not_publish_another(
    tmp_path: Path,
) -> None:
    client, _operator_dir = _client(
        tmp_path,
        tender_codes=(_TENDER_A, _TENDER_B),
    )

    imported = client.post(
        f"{_import_url(_TENDER_A)}?declare_complete=true",
        content=_valid_payload(),
        headers={"Content-Type": _ZIP_CONTENT_TYPE},
    )
    assert imported.status_code == 200

    tender_a = client.get(_detail_url(_TENDER_A))
    tender_b = client.get(_detail_url(_TENDER_B))

    assert tender_a.status_code == 200
    assert tender_b.status_code == 200
    assert tender_a.json()["t1_published"] is True
    assert tender_b.json()["t1_published"] is False


def test_malformed_existing_overlay_fails_closed_instead_of_falling_back(
    tmp_path: Path,
) -> None:
    client, operator_dir = _client(tmp_path)

    imported = client.post(
        f"{_import_url()}?declare_complete=true",
        content=_valid_payload(),
        headers={"Content-Type": _ZIP_CONTENT_TYPE},
    )
    assert imported.status_code == 200

    saved = operator_dir / f"{_TENDER_A.casefold()}.json"
    saved.write_text("{not-json\n", encoding="utf-8")

    detail = client.get(_detail_url())
    assert detail.status_code == 200

    data = detail.json()
    assert data["t1_published"] is False
    assert data["t1_meta"]["reduced_mode"] is True
    assert data["t1_meta"]["canonical_reason"] == "malformed_operator_tender_import"
    assert data["tender_facts"] == []
    assert data["items"] == []
    assert data["coverage"] is None


def test_import_still_obeys_w1_actionability_gate(tmp_path: Path) -> None:
    client, operator_dir = _client(tmp_path)

    response = client.post(
        f"{_import_url('000000-0-LE00')}?declare_complete=true",
        content=_valid_payload(),
        headers={"Content-Type": _ZIP_CONTENT_TYPE},
    )

    assert response.status_code == 404
    assert not operator_dir.exists()


def test_import_fails_closed_when_w1_is_degraded(tmp_path: Path) -> None:
    client, operator_dir = _client(tmp_path, healthy_w1=False)

    response = client.post(
        f"{_import_url()}?declare_complete=true",
        content=_valid_payload(),
        headers={"Content-Type": _ZIP_CONTENT_TYPE},
    )

    assert response.status_code == 503
    assert not operator_dir.exists()


def test_import_rejects_malformed_zip_without_persisting(
    tmp_path: Path,
) -> None:
    client, operator_dir = _client(tmp_path)

    response = client.post(
        _import_url(),
        content=b"not a zip",
        headers={"Content-Type": _ZIP_CONTENT_TYPE},
    )

    assert response.status_code == 422
    assert not operator_dir.exists()


def test_import_rejects_wrong_content_type(tmp_path: Path) -> None:
    client, operator_dir = _client(tmp_path)

    response = client.post(
        _import_url(),
        content=_valid_payload(),
        headers={"Content-Type": "application/octet-stream"},
    )

    assert response.status_code == 415
    assert not operator_dir.exists()


def test_operator_import_dir_defaults_to_t1_storage_sibling(
    tmp_path: Path,
) -> None:
    settings = Settings(
        tender_terms_dir=tmp_path / "persistent-volume" / "tender_terms",
    )

    assert (
        settings.resolved_operator_tender_import_dir()
        == (tmp_path / "persistent-volume" / "operator_tender_imports").resolve()
    )


def test_explicit_operator_import_dir_overrides_t1_sibling(
    tmp_path: Path,
) -> None:
    explicit = tmp_path / "explicit-operator-store"
    settings = Settings(
        tender_terms_dir=tmp_path / "persistent-volume" / "tender_terms",
        operator_tender_import_dir=explicit,
    )

    assert settings.resolved_operator_tender_import_dir() == explicit.resolve()


def _local_structured_payload(
    tender_code: str = _TENDER_A,
    *,
    declare_complete: bool = True,
) -> dict:
    """Build the exact payload a workstation worker will POST after local compute."""
    source = OperatorZipAttachmentSource.from_bytes(
        _valid_payload(),
        tender_code=tender_code,
        declare_complete=declare_complete,
    )
    raw = build_operator_annex_bundle_preview(
        source,
        tender_code=tender_code,
    )
    assert raw["result"] == "imported"

    return {
        "contract_version": "local_tender_annex_import_v1",
        "tender_code": tender_code,
        "operator_declared_complete": declare_complete,
        "raw": raw,
    }


def test_structured_local_import_persists_without_server_ocr(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, operator_dir = _client(tmp_path)

    # Build the workstation result first. Everything below this line is the
    # simulated Render/API side of the request.
    payload = _local_structured_payload()

    def forbidden_server_compute(*args, **kwargs):
        raise AssertionError(
            "structured local import must not run server OCR/ZIP compute"
        )

    import origenlab_api.routes.institutions as institutions_route
    from origenlab_email_pipeline.chilecompra_anexo_evidence import (
        extract as extract_module,
    )

    # If JSON accidentally falls through to the old ZIP processing path,
    # this makes the test fail immediately.
    monkeypatch.setattr(
        institutions_route,
        "build_tender_annex_bundle_import",
        forbidden_server_compute,
    )

    # Independently guard the actual OCR seam as well.
    monkeypatch.setattr(
        extract_module,
        "extract_image_text",
        forbidden_server_compute,
    )

    response = client.post(
        _import_url(),
        json=payload,
    )

    assert response.status_code == 200
    data = response.json()
    assert data["result"] == "imported"
    assert data["persisted"] is True
    assert data["published"] is True
    assert data["contact_authorization"] is False
    assert data["outreach_authorization"] is False

    saved_files = list(operator_dir.glob("*.json"))
    assert len(saved_files) == 1
    assert not list(operator_dir.rglob("*.zip"))

    # Browser refresh / follow-up GET sees the persisted local-worker result.
    after = client.get(_detail_url())
    assert after.status_code == 200
    detail = after.json()
    assert detail["t1_published"] is True
    assert detail["t1_meta"]["source_kind"] == "operator_annex_import"
    assert detail["t1_meta"]["canonical_reason"] == "operator_tender_import"


def test_structured_local_import_rejects_tender_identity_mismatch(
    tmp_path: Path,
) -> None:
    client, operator_dir = _client(tmp_path)
    payload = _local_structured_payload()

    payload["raw"]["tender_code"] = _TENDER_B

    response = client.post(
        _import_url(),
        json=payload,
    )

    assert response.status_code == 422
    assert not operator_dir.exists()


def test_structured_local_import_rejects_portal_token(
    tmp_path: Path,
) -> None:
    client, operator_dir = _client(tmp_path)
    payload = _local_structured_payload()

    # Synthetic value only: verifies that opaque Mercado Público parameters
    # can never cross the structured-result persistence boundary.
    payload["raw"]["provenance"]["completeness_reason"] = "enc=synthetic-test-value"

    response = client.post(
        _import_url(),
        json=payload,
    )

    assert response.status_code == 422
    assert not operator_dir.exists()


def test_structured_local_import_still_obeys_w1_actionability(
    tmp_path: Path,
) -> None:
    client, operator_dir = _client(tmp_path)
    payload = _local_structured_payload()

    response = client.post(
        _import_url("000000-0-LE00"),
        json=payload,
    )

    assert response.status_code == 404
    assert not operator_dir.exists()


def test_structured_local_import_rejects_malformed_contract(
    tmp_path: Path,
) -> None:
    client, operator_dir = _client(tmp_path)
    payload = _local_structured_payload()

    payload["contract_version"] = "unsupported-version"

    response = client.post(
        _import_url(),
        json=payload,
    )

    assert response.status_code == 422
    assert not operator_dir.exists()


@pytest.mark.parametrize(
    "tamper",
    [
        "source_semantic_digest",
        "attachments_discovered",
        "incomplete_reason_codes",
        "bundle_complete",
    ],
)
def test_structured_local_import_rejects_cross_envelope_inconsistency(
    tmp_path: Path,
    tamper: str,
) -> None:
    client, operator_dir = _client(tmp_path)
    payload = _local_structured_payload()

    if tamper == "source_semantic_digest":
        # Leave the T1 row fully valid. Only disconnect the outer provenance
        # from the source bundle digest independently validated inside T1.
        payload["raw"]["provenance"]["source_semantic_digest"] = "0" * 64

    elif tamper == "attachments_discovered":
        # Keep downloaded/provenance counts internally valid while making the
        # archive inventory disagree with T1 coverage.
        payload["raw"]["archive"]["attachments_discovered"] += 1

    elif tamper == "incomplete_reason_codes":
        payload["raw"]["incomplete_reason_codes"] = [
            "synthetic_cross_envelope_mismatch"
        ]

    elif tamper == "bundle_complete":
        # Keep the acquisition-level completeness state consistent with the
        # tampered outer value so rejection reaches the T1 cross-check rather
        # than the earlier provenance-state invariant.
        assert payload["raw"]["bundle_complete"] is True
        payload["raw"]["bundle_complete"] = False
        payload["raw"]["provenance"]["completeness_state"] = "incomplete"

    response = client.post(
        _import_url(),
        json=payload,
    )

    assert response.status_code == 422

    error = response.json()["error"]
    assert error["code"] == "validation_error"
    assert error["message"] == "Invalid structured local annex import"
    assert not operator_dir.exists()


def test_structured_local_import_accepts_untampered_cross_envelope_contract(
    tmp_path: Path,
) -> None:
    client, operator_dir = _client(tmp_path)
    payload = _local_structured_payload()

    response = client.post(
        _import_url(),
        json=payload,
    )

    assert response.status_code == 200
    assert response.json()["persisted"] is True

    saved = operator_dir / f"{_TENDER_A.casefold()}.json"
    assert saved.is_file()
