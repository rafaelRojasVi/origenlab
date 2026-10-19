"""Bidder data without a database: the rules, and who may call each route (spec §6.3).

Every value is invented (public repository): «Oferente Ejemplo», RUT 11.111.111-1 and 61.111.111-8.
`test_v2_bidder_db.py` runs the transactions.
"""

from __future__ import annotations

import io
import zipfile

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from origenlab_api.commercial_operator_identity import OPERATOR_EMAIL_HEADER
from origenlab_api.v2.case_documents import DocumentRefused
from origenlab_api.v2.catalog.storage import FakeStorage
from origenlab_api.v2.commands import CommandRefused
from origenlab_api.v2.identity import LocalDevIdentity, OperatorIdentity, OperatorLookup
from origenlab_api.v2.tenders.bidder import (
    DOCX,
    XLSX,
    BidderProfileBody,
    bidder_document_path,
    check_bidder_document,
    format_rut,
    prepare_save_bidder_profile,
)

LOOPBACK = "postgresql://origenlab_api:pw@127.0.0.1:54332/origenlab_dev"
DOC_ID = "00000000-0000-4000-8000-0000000000dd"


def office_zip(main_part: str) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr(main_part, "<x/>")
    return out.getvalue()


def profile(**over) -> BidderProfileBody:
    base = {"bidder_kind": "natural_person", "legal_name": "Oferente Ejemplo", "rut": "11.111.111-1",
            "reason": "Primera carga"}
    return BidderProfileBody(**{**base, **over})


def test_rut_is_normalised_and_checked() -> None:
    assert prepare_save_bidder_profile(profile())["rut"] == "11111111-1"
    with pytest.raises(CommandRefused) as bad:
        prepare_save_bidder_profile(profile(rut="11.111.111-2"))
    assert (bad.value.status_code, bad.value.code) == (422, "rut_invalid")
    with pytest.raises(CommandRefused) as rep:
        prepare_save_bidder_profile(profile(bidder_kind="company", legal_rep_name="Rep Ejemplo",
                                            legal_rep_rut="61.111.111-1"))
    assert rep.value.code == "rut_invalid"


def test_legal_rep_is_for_a_company_only() -> None:
    company = prepare_save_bidder_profile(profile(bidder_kind="company", legal_rep_name="Rep Ejemplo",
                                                  legal_rep_rut="61.111.111-8"))
    assert company["legal_rep_rut"] == "61111111-8"
    assert prepare_save_bidder_profile(profile(bidder_kind="company"))["legal_rep_name"] is None
    for extra in ({"legal_rep_name": "Rep Ejemplo"}, {"legal_rep_rut": "61.111.111-8"}):
        with pytest.raises(CommandRefused) as exc:
            prepare_save_bidder_profile(profile(**extra))
        assert (exc.value.status_code, exc.value.code) == (422, "legal_rep_only_for_company")


def test_blank_optionals_become_none_and_reason_needs_three_characters() -> None:
    assert profile(giro="  ", phone="").giro is None
    with pytest.raises(ValueError):
        profile(reason="ok")


def test_rut_prints_with_dots() -> None:
    assert format_rut("11111111-1") == "11.111.111-1"
    assert format_rut("61111111-8") == "61.111.111-8"
    assert format_rut("7654321-K") == "7.654.321-K"


def test_the_bytes_decide_the_document_type() -> None:
    assert check_bidder_document(b"%PDF-1.7 ejemplo") == "application/pdf"
    assert check_bidder_document(b"\xff\xd8\xff\xe0 ejemplo") == "image/jpeg"
    assert check_bidder_document(b"\x89PNG\r\n\x1a\n ejemplo") == "image/png"
    assert check_bidder_document(office_zip("word/document.xml")) == DOCX
    assert check_bidder_document(office_zip("xl/workbook.xml")) == XLSX
    for data in (b"hola", b"PK\x03\x04 not a zip", office_zip("otra/cosa.xml"), b""):
        with pytest.raises(DocumentRefused) as exc:
            check_bidder_document(data)
        assert exc.value.status_code == 415
    with pytest.raises(DocumentRefused) as big:
        check_bidder_document(b"%PDF-" + b"0" * (20 * 1024 * 1024))
    assert big.value.status_code == 413


def test_the_path_is_content_addressed_under_bidder() -> None:
    sha = "ab" * 32
    assert bidder_document_path(sha, DOCX) == f"bidder/{sha}.docx"
    assert bidder_document_path(sha, "image/jpeg") == f"bidder/{sha}.jpg"
    with pytest.raises(ValueError):
        bidder_document_path("nope", DOCX)


# ------------------------------------------------------------------ who may call


class _Lookup(OperatorLookup):
    def __init__(self, operator: OperatorIdentity) -> None:
        self._operator = operator

    def by_email(self, email_norm: str) -> OperatorIdentity | None:
        return self._operator


class _Recorder:
    """Stands in for both repositories; any call means a route got past the role check."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def execute(self, **kwargs):
        self.calls.append(kwargs["command_name"])
        return {"command": kwargs["command_name"], "replayed": False}

    def _read(self):
        self.calls.append("read")
        raise AssertionError("a viewer must not reach the database")


def client(role: str) -> tuple[TestClient, _Recorder, FakeStorage]:
    from origenlab_api.v2.tenders.routes import bidder_read_router, tender_command_router, tender_read_router

    app = FastAPI()
    for router in (tender_command_router, tender_read_router, bidder_read_router):
        app.include_router(router)
    recorder, storage = _Recorder(), FakeStorage("crm-documents")
    app.state.v2_tender_command_repository = recorder
    app.state.v2_tender_read_repository = recorder
    app.state.case_document_storage = storage
    operator = OperatorIdentity(operator_id="00000000-0000-4000-8000-000000000001",
                                email_norm="operator@example.test", display_name="Operador", role=role,
                                status="active")
    app.state.v2_identity = LocalDevIdentity(LOOPBACK, _Lookup(operator))
    return TestClient(app), recorder, storage


HEADERS = {OPERATOR_EMAIL_HEADER: "operator@example.test", "Idempotency-Key": "pytest-bidder-1"}
PROFILE_JSON = {"bidder_kind": "natural_person", "legal_name": "Oferente Ejemplo", "rut": "11.111.111-1",
                "reason": "Primera carga"}


def test_a_viewer_is_refused_every_bidder_route_before_anything_is_read_or_stored() -> None:
    http, recorder, storage = client("viewer")
    responses = [
        http.get("/v2/bidder", headers=HEADERS),
        http.get(f"/v2/bidder/documents/{DOC_ID}/url", headers=HEADERS),
        http.post("/v2/commands/save-bidder-profile", json=PROFILE_JSON, headers=HEADERS),
        http.post("/v2/commands/retire-bidder-document", json={"document_id": DOC_ID, "reason": "mal cargado"},
                  headers=HEADERS),
        http.post("/v2/commands/add-bidder-document", data={"kind": "cedula", "title": "Cédula"},
                  files={"file": ("c.pdf", b"%PDF-1.7 ejemplo", "application/pdf")}, headers=HEADERS),
        http.get("/v2/tenders/" + DOC_ID, headers=HEADERS),
    ]
    for response in responses:
        assert response.status_code == 403, response.text
        assert response.json()["detail"]["code"] == "role_may_not_view_bidder"
    assert recorder.calls == [] and storage.puts == 0 and storage.objects == {}


@pytest.mark.parametrize("role", ["sales", "admin"])
def test_sales_and_admin_reach_the_repository(role: str) -> None:
    http, recorder, storage = client(role)
    saved = http.post("/v2/commands/save-bidder-profile", json=PROFILE_JSON, headers=HEADERS)
    assert saved.status_code == 200 and recorder.calls == ["save-bidder-profile"]
    added = http.post("/v2/commands/add-bidder-document", data={"kind": "cedula", "title": "Cédula"},
                      files={"file": ("cedula.pdf", b"%PDF-1.7 ejemplo", "application/pdf")}, headers=HEADERS)
    assert added.status_code == 200 and recorder.calls[-1] == "add-bidder-document"
    assert len(storage.objects) == 1 and next(iter(storage.objects)).startswith("bidder/")


def test_the_routes_need_a_key_a_valid_rut_and_a_supported_file() -> None:
    http, recorder, storage = client("sales")
    no_key = {OPERATOR_EMAIL_HEADER: HEADERS[OPERATOR_EMAIL_HEADER]}
    assert http.post("/v2/commands/save-bidder-profile", json=PROFILE_JSON, headers=no_key).status_code == 400
    bad = http.post("/v2/commands/save-bidder-profile", json={**PROFILE_JSON, "rut": "11.111.111-2"}, headers=HEADERS)
    assert (bad.status_code, bad.json()["detail"]["code"]) == (422, "rut_invalid")
    junk = http.post("/v2/commands/add-bidder-document", data={"kind": "cedula", "title": "Cédula"},
                     files={"file": ("x.txt", b"hola", "text/plain")}, headers=HEADERS)
    assert (junk.status_code, junk.json()["detail"]["code"]) == (415, "unsupported_document")
    unknown_kind = http.post("/v2/commands/add-bidder-document", data={"kind": "otra", "title": "T"},
                             files={"file": ("c.pdf", b"%PDF-1.7 ejemplo", "application/pdf")}, headers=HEADERS)
    assert unknown_kind.status_code == 422
    assert recorder.calls == [] and storage.objects == {}
