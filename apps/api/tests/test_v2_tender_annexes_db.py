"""«Subir anexos» against a disposable database: rows, events, re-uploads, replay, and the non-tender case.

Runs as `origenlab_api`. Every value is invented (public repository): buyers «Hospital Ejemplo …»,
codes 12345xx-12-LE26, a fake bucket.
"""

from __future__ import annotations

import io
import itertools
import uuid
import zipfile
from datetime import UTC, datetime

import psycopg
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from v2_command_harness import build_disposable_database, needs_db, runtime_dsn

from origenlab_api.commercial_operator_identity import OPERATOR_EMAIL_HEADER
from origenlab_api.v2.catalog.storage import FakeStorage
from origenlab_api.v2.commands import request_digest
from origenlab_api.v2.identity import LocalDevIdentity, OperatorIdentity, OperatorLookup
from origenlab_api.v2.tenders.mercado_publico import TenderDetail
from origenlab_api.v2.tenders.open_case import (
    OPEN_TENDER_CASE,
    OpenTenderCaseBody,
    V2TenderCommandRepository,
    prepare_open_tender_case,
)
from origenlab_api.v2.tenders.reads import TenderReadRepository
from origenlab_api.v2.tenders.routes import tender_command_router, tender_read_router

pytestmark = needs_db
LOOPBACK = "postgresql://origenlab_api:pw@127.0.0.1:54332/origenlab_dev"
_KEYS = itertools.count(1)
_SERIALS = itertools.count(1)
MiB = 1024 * 1024
URL = "/v2/commands/attach-tender-annexes"


def office(main_part: str) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr(main_part, "<x/>")
    return out.getvalue()


def zipped(members: dict[str, bytes]) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in members.items():
            archive.writestr(name, data)
    return out.getvalue()


def pdf(tag: str = "") -> bytes:
    return b"%PDF-1.7 anexo de ejemplo " + (tag or uuid.uuid4().hex).encode()


@pytest.fixture(scope="module")
def db():
    yield from build_disposable_database()


def _owner(db):
    conn = psycopg.connect(db, autocommit=True)
    conn.execute("set role origenlab_owner")
    return conn


@pytest.fixture(scope="module")
def operators(db):
    out = {}
    with _owner(db) as conn:
        for role in ("sales", "viewer"):
            out[role] = OperatorIdentity(
                operator_id=conn.execute(
                    "insert into platform.operator (auth_user_id, email_norm, display_name, role, status) "
                    "values (gen_random_uuid(), %s, %s, %s, 'active') returning id::text",
                    (f"{role}-anexos@example.test", f"Operador {role}", role)).fetchone()[0],
                email_norm=f"{role}-anexos@example.test", display_name=f"Operador {role}", role=role,
                status="active")
    return out


class _Lookup(OperatorLookup):
    def __init__(self, operators) -> None:
        self._by_email = {o.email_norm: o for o in operators.values()}

    def by_email(self, email_norm: str):
        return self._by_email.get(email_norm)


@pytest.fixture
def storage() -> FakeStorage:
    return FakeStorage("crm-documents")


@pytest.fixture
def client(db, operators, storage) -> TestClient:
    app = FastAPI()
    app.include_router(tender_command_router)
    app.include_router(tender_read_router)
    app.state.v2_tender_command_repository = V2TenderCommandRepository(psycopg.connect, runtime_dsn(db))
    app.state.v2_tender_read_repository = TenderReadRepository(psycopg.connect, runtime_dsn(db))
    app.state.case_document_storage = storage
    app.state.v2_identity = LocalDevIdentity(LOOPBACK, _Lookup(operators))
    return TestClient(app)


def headers(role: str = "sales", key: str | None = None) -> dict[str, str]:
    return {OPERATOR_EMAIL_HEADER: f"{role}-anexos@example.test",
            "Idempotency-Key": key or f"pytest-anexos-{next(_KEYS)}"}


def open_tender(db, operators) -> str:
    n = next(_SERIALS)
    tag = uuid.uuid4().hex[:6]
    code = f"{1234600 + n}-12-LE26"
    detail = TenderDetail(
        code=code, title=f"Adquisición de balanzas {tag}", buyer_name=f"Hospital Ejemplo {tag}",
        buyer_code=f"8{n:03d}{tag[:2]}", buyer_rut=None, closes_at=None, published_at=None,
        source_status="Publicada", head={"CodigoExterno": code})
    body = OpenTenderCaseBody(reference=code)
    fields = prepare_open_tender_case(body, ticket="ticket-ficticio", fetch=lambda c, ticket: detail)
    result = V2TenderCommandRepository(psycopg.connect, runtime_dsn(db)).execute(
        command_name=OPEN_TENDER_CASE, operator=operators["sales"], fields=fields,
        idempotency_key=f"pytest-open-{next(_KEYS)}", digest=request_digest(OPEN_TENDER_CASE, body))
    return result["opportunity_id"]


def upload(client, case: str, *pairs: tuple[str, bytes], role: str = "sales", key: str | None = None):
    return client.post(URL, data={"opportunity_id": case},
                       files=[("files", (n, d, "application/octet-stream")) for n, d in pairs],
                       headers=headers(role, key))


def rows(db, case: str):
    with _owner(db) as conn:
        return conn.execute(
            "select kind, original_filename, content_type, source, removed_at from crm.opportunity_document "
            "where opportunity_id = %s order by original_filename", (case,)).fetchall()


def test_two_pdfs_and_a_docx_attach_three_with_an_event_each(db, operators, client, storage) -> None:
    case = open_tender(db, operators)
    docx = office("word/document.xml")
    response = upload(client, case, ("bases.pdf", pdf()), ("tecnicas.pdf", pdf()), ("anexo1.docx", docx))
    assert response.status_code == 200, response.text
    body = response.json()
    assert [a["filename"] for a in body["attached"]] == ["bases.pdf", "tecnicas.pdf", "anexo1.docx"]
    assert body["skipped"] == [] and body["replayed"] is False
    assert [r[:4] for r in rows(db, case)] == [
        ("tender_annex", "anexo1.docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
         "upload"),
        ("tender_annex", "bases.pdf", "application/pdf", "upload"),
        ("tender_annex", "tecnicas.pdf", "application/pdf", "upload")]
    with _owner(db) as conn:
        events = conn.execute(
            "select event_type, payload from crm.domain_event where aggregate_id = %s "
            "and event_type = 'opportunity.document_attached'", (case,)).fetchall()
    assert len(events) == 3 and {e[1]["kind"] for e in events} == {"tender_annex"}
    assert {e[1]["document_id"] for e in events} == {a["document_id"] for a in body["attached"]}
    assert len(storage.objects) == 3


def test_a_hostile_zip_attaches_the_safe_members_and_lists_the_rest(db, operators, client) -> None:
    case = open_tender(db, operators)
    package = zipped({"bases.pdf": pdf(), "anexo1.docx": office("word/document.xml"), "../evil.pdf": pdf(),
                      "planilla.xlsx": office("xl/workbook.xml"), "x.rar": b"Rar!\x1a\x07\x00"})
    response = upload(client, case, ("paquete.zip", package))
    assert response.status_code == 200, response.text
    body = response.json()
    assert sorted(a["filename"] for a in body["attached"]) == ["anexo1.docx", "bases.pdf", "planilla.xlsx"]
    assert {s["filename"]: s["reason"] for s in body["skipped"]} == {
        "../evil.pdf": "unsafe_path", "x.rar": "unsupported_type"}
    assert len(rows(db, case)) == 3


def test_a_zip_bomb_attaches_nothing_and_names_every_member(db, operators, client) -> None:
    case = open_tender(db, operators)
    bomb = zipped({"a.pdf": b"%PDF-" + b"\0" * (10 * MiB), "b.docx": b"\0" * MiB})
    response = upload(client, case, ("bomba.zip", bomb))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["attached"] == []
    assert {s["filename"]: s["reason"] for s in body["skipped"]} == {
        "a.pdf": "archive_rejected", "b.docx": "archive_rejected"}
    assert rows(db, case) == []


def test_a_reupload_is_already_attached_and_a_removed_one_may_come_back(db, operators, client) -> None:
    case = open_tender(db, operators)
    data = pdf()
    first = upload(client, case, ("bases.pdf", data)).json()
    again = upload(client, case, ("copia.pdf", data), ("otra.pdf", pdf())).json()
    assert [a["filename"] for a in again["attached"]] == ["otra.pdf"]
    assert again["skipped"] == [{"filename": "copia.pdf", "reason": "already_attached"}]
    same_twice = upload(client, case, ("uno.pdf", pdf("x")), ("dos.pdf", pdf("x"))).json()
    assert [a["filename"] for a in same_twice["attached"]] == ["uno.pdf"]
    assert same_twice["skipped"] == [{"filename": "dos.pdf", "reason": "already_attached"}]
    with _owner(db) as conn:
        conn.execute("update crm.opportunity_document set removed_at = now(), remove_reason = 'prueba', "
                     "removed_by_operator_id = %s where id = %s", (operators["sales"].operator_id,
                                                                   first["attached"][0]["document_id"]))
    back = upload(client, case, ("bases.pdf", data)).json()
    assert [a["filename"] for a in back["attached"]] == ["bases.pdf"]


def test_a_21_mib_file_is_too_large_and_the_others_still_attach(db, operators, client) -> None:
    case = open_tender(db, operators)
    response = upload(client, case, ("grande.pdf", b"%PDF-" + b"0" * (21 * MiB)), ("bases.pdf", pdf()))
    assert response.status_code == 200, response.text
    body = response.json()
    assert [a["filename"] for a in body["attached"]] == ["bases.pdf"]
    assert body["skipped"] == [{"filename": "grande.pdf", "reason": "too_large"}]


def test_a_legacy_doc_is_accepted_and_recorded(db, operators, client) -> None:
    case = open_tender(db, operators)
    doc = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + "WordDocument".encode("utf-16-le") + uuid.uuid4().bytes
    body = upload(client, case, ("formulario.doc", doc)).json()
    assert [a["filename"] for a in body["attached"]] == ["formulario.doc"]
    assert rows(db, case)[0][2] == "application/msword"


def test_a_sales_case_is_422_not_a_tender_and_nothing_is_recorded(db, operators, client, storage) -> None:
    with _owner(db) as conn:
        sale = conn.execute(
            "insert into crm.opportunity (title, stage, owner_operator_id) values (%s, 'lead', %s) returning id::text",
            ("Venta de ejemplo", operators["sales"].operator_id)).fetchone()[0]
    response = upload(client, sale, ("bases.pdf", pdf()))
    assert (response.status_code, response.json()["detail"]["code"]) == (422, "not_a_tender_case")
    assert rows(db, sale) == []
    missing = upload(client, str(uuid.uuid4()), ("bases.pdf", pdf()))
    assert (missing.status_code, missing.json()["detail"]["code"]) == (404, "case_not_found")
    assert storage.puts == 0  # the case is checked before any object is stored


def test_a_replayed_key_does_not_attach_twice_and_a_viewer_is_refused(db, operators, client) -> None:
    case = open_tender(db, operators)
    data = pdf()
    one = upload(client, case, ("bases.pdf", data), key="pytest-anexos-replay").json()
    two = upload(client, case, ("bases.pdf", data), key="pytest-anexos-replay").json()
    assert two["replayed"] is True and two["attached"] == one["attached"]
    assert len(rows(db, case)) == 1
    assert upload(client, case, ("otro.pdf", pdf()), role="viewer").status_code == 403
    assert len(rows(db, case)) == 1


def test_the_page_lists_the_annexes(db, operators, client) -> None:
    case = open_tender(db, operators)
    sent = upload(client, case, ("bases.pdf", pdf()))
    page = client.get(f"/v2/tenders/{case}", headers=headers()).json()
    assert [a["filename"] for a in page["annexes"]] == ["bases.pdf"]
    assert page["annexes"][0]["document_id"] == sent.json()["attached"][0]["document_id"]


def test_a_zip_member_that_repeats_a_loose_file_is_attached_once(db, operators, client) -> None:
    case = open_tender(db, operators)
    data = pdf("repetido")
    body = upload(client, case, ("bases.pdf", data), ("paquete.zip", zipped({"copia.pdf": data}))).json()
    assert [a["filename"] for a in body["attached"]] == ["bases.pdf"]
    assert body["skipped"] == [{"filename": "copia.pdf", "reason": "already_attached"}]
    assert len(rows(db, case)) == 1
