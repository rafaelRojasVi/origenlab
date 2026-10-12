"""Case documents over HTTP, without a database: the upload stores the object and then runs the
command over its hash; an email attachment is found in its stored `.eml` and copied; removal and
the signed link. Storage is `FakeStorage`; the repository is a recorder. Every name, number and
byte here is invented.
"""
from __future__ import annotations

import hashlib
import uuid
from contextlib import contextmanager
from email.message import EmailMessage

from fastapi import FastAPI
from fastapi.testclient import TestClient

from origenlab_api.commercial_operator_identity import OPERATOR_EMAIL_HEADER
from origenlab_api.errors import register_exception_handlers
from origenlab_api.v2.case_documents import MAX_DOCUMENT_BYTES
from origenlab_api.v2.catalog.storage import FakeStorage
from origenlab_api.v2.commands import CommandRefused
from origenlab_api.v2.identity import LocalDevIdentity, OperatorIdentity, OperatorLookup

LOOPBACK = "postgresql://origenlab_api:pw@127.0.0.1:54332/origenlab_dev"
HEADERS = {OPERATOR_EMAIL_HEADER: "op@example.test"}
CASE = "00000000-0000-4000-8000-0000000000aa"
REV = "00000000-0000-4000-8000-0000000000bb"
SRC = "00000000-0000-4000-8000-0000000000cc"
DOC = "00000000-0000-4000-8000-0000000000dd"
PDF = b"%PDF-1.7\n% ficticio\n%%EOF\n"


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class _Lookup(OperatorLookup):
    def __init__(self, operator):
        self._operator = operator

    def by_email(self, email_norm):
        return self._operator


def _operator(role: str) -> OperatorIdentity:
    return OperatorIdentity(operator_id="00000000-0000-4000-8000-000000000001", email_norm="op@example.test",
                            display_name="Op", role=role, status="active")


class _Cursor:
    def __init__(self, rows):
        self._rows = rows
        self.sql: list[tuple[str, tuple]] = []

    def execute(self, sql, params=()):
        self.sql.append((sql, params))


class _FakeRepo:
    """Records each command and what Storage held then; answers the two reads the routes make."""

    def __init__(self, storage, *, message=None, document=None, refusal=None):
        self.calls: list[dict] = []
        self.stored_at_call: list[set] = []
        self._storage = storage
        self._message = message
        self._document = document
        self._refusal = refusal
        self._last: _Cursor | None = None

    def execute(self, **kwargs):
        self.calls.append(kwargs)
        self.stored_at_call.append(set(self._storage.objects))
        if self._refusal is not None:
            raise self._refusal
        return {"document": {"document_id": DOC}, "replayed": False}

    @contextmanager
    def _write(self):
        self._last = _Cursor([])
        yield self._last

    def _row(self, cur):
        sql = cur.sql[-1][0]
        if "from evidence.source_record" in sql:
            return self._message
        if "from crm.opportunity_document" in sql:
            return self._document
        return None


def _client(role="sales", *, storage=True, message=None, document=None, refusal=None, mail=None):
    from origenlab_api.v2.case_document_routes import case_document_command_router, case_document_read_router

    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(case_document_command_router)
    app.include_router(case_document_read_router)
    app.state.case_document_storage = FakeStorage("crm-documents") if storage else None
    app.state.mail_storage = mail if mail is not None else (FakeStorage("mail") if storage else None)
    app.state.v2_case_command_repository = _FakeRepo(
        app.state.case_document_storage or FakeStorage(), message=message, document=document, refusal=refusal)
    app.state.v2_identity = LocalDevIdentity(LOOPBACK, _Lookup(_operator(role)))
    return app, TestClient(app)


def _ikey():
    return {**HEADERS, "Idempotency-Key": uuid.uuid4().hex}


def _upload(client, data=PDF, name="OC 4500.pdf", ctype="application/octet-stream", *, headers=None, **form):
    fields = {"opportunity_id": CASE, "note": "OC adjuntada al marcar ganada", **form}
    return client.post("/v2/commands/attach-case-document", headers=headers or _ikey(),
                       data=fields, files={"file": (name, data, ctype)})


# ──────────────────────────────────────────────────────────── upload ──

def test_an_upload_is_stored_first_then_recorded_over_its_hash() -> None:
    app, client = _client()
    r = _upload(client, quote_revision_id=REV)
    assert r.status_code == 200, r.text
    path = f"opportunities/{CASE}/{_sha(PDF)}.pdf"
    assert app.state.case_document_storage.objects == {path: (PDF, "application/pdf")}
    [call] = app.state.v2_case_command_repository.calls
    assert app.state.v2_case_command_repository.stored_at_call == [{path}]
    assert call["command_name"] == "attach_case_document"
    assert call["fields"] == {
        "opportunity_id": CASE, "kind": "purchase_order", "quote_revision_id": REV, "document_number": "4500",
        "note": "OC adjuntada al marcar ganada", "storage_path": path, "sha256": _sha(PDF),
        "content_type": "application/pdf", "byte_size": len(PDF), "original_filename": "OC 4500.pdf",
        "source": "upload", "source_record_id": None,
    }


def test_a_typed_number_wins_over_the_file_name_and_a_blank_revision_is_absent() -> None:
    app, client = _client()
    assert _upload(client, document_number="77-A", quote_revision_id="").status_code == 200
    fields = app.state.v2_case_command_repository.calls[0]["fields"]
    assert fields["document_number"] == "77-A" and fields["quote_revision_id"] is None


def test_a_file_that_is_not_a_pdf_or_image_is_refused_before_anything_is_stored() -> None:
    app, client = _client()
    r = _upload(client, data=b"PK\x03\x04zip", name="oc.pdf", ctype="application/pdf")
    assert r.status_code == 415 and r.json()["error"]["details"]["code"] == "unsupported_document"
    assert app.state.case_document_storage.objects == {} and app.state.v2_case_command_repository.calls == []


def test_a_document_over_the_cap_is_refused() -> None:
    app, client = _client()
    r = _upload(client, data=PDF + b"0" * MAX_DOCUMENT_BYTES)
    assert r.status_code == 413
    assert app.state.case_document_storage.objects == {}


def test_a_viewer_cannot_attach_and_nothing_is_stored() -> None:
    app, client = _client("viewer")
    assert _upload(client).status_code == 403
    assert app.state.case_document_storage.objects == {}


def test_without_an_idempotency_key_nothing_is_read_or_stored() -> None:
    app, client = _client()
    assert _upload(client, headers=HEADERS).status_code in (400, 428)
    assert app.state.case_document_storage.objects == {}


def test_without_storage_the_upload_is_a_503() -> None:
    app, client = _client(storage=False)
    r = _upload(client)
    assert r.status_code == 503 and r.json()["error"]["details"]["code"] == "storage_unavailable"


def test_a_refused_command_is_answered_with_its_code() -> None:
    app, client = _client(refusal=CommandRefused(409, "document_already_attached", "ya está"))
    r = _upload(client)
    assert r.status_code == 409 and r.json()["error"]["details"]["code"] == "document_already_attached"


# ──────────────────────────────────────────────────────────── from an email ──

def _eml(*attachments: tuple[str, bytes]) -> bytes:
    msg = EmailMessage()
    msg["From"] = "compras@cliente.example"
    msg["To"] = "contacto@origenlab.example"
    msg["Subject"] = "Orden de compra"
    msg.set_content("Adjunto OC.")
    for name, data in attachments:
        msg.add_attachment(data, maintype="application", subtype="pdf", filename=name)
    return msg.as_bytes()


def _from_email(client, sha, **extra):
    body = {"opportunity_id": CASE, "source_record_id": SRC, "document_sha256": sha, "note": "OC del correo", **extra}
    return client.post("/v2/commands/attach-case-document-from-email", headers=_ikey(), json=body)


def test_an_email_attachment_is_copied_from_its_eml_and_recorded() -> None:
    mail = FakeStorage("mail")
    mail.objects["contacto@origenlab.example/2026/10/m1.eml"] = (_eml(("cot.pdf", PDF + b"x"), ("OC 4321.pdf", PDF)),
                                                                 "message/rfc822")
    message = {"eml_storage_path": "mail/contacto@origenlab.example/2026/10/m1.eml",
               "filename": "Orden de compra 4321.pdf", "subject": "RE: cotización"}
    app, client = _client(message=message, mail=mail)
    r = _from_email(client, _sha(PDF))
    assert r.status_code == 200, r.text
    path = f"opportunities/{CASE}/{_sha(PDF)}.pdf"
    assert app.state.case_document_storage.objects == {path: (PDF, "application/pdf")}
    fields = app.state.v2_case_command_repository.calls[0]["fields"]
    assert fields["source"] == "email" and fields["source_record_id"] == SRC
    assert fields["original_filename"] == "Orden de compra 4321.pdf" and fields["document_number"] == "4321"


def test_an_attachment_the_message_does_not_carry_is_a_404() -> None:
    app, client = _client(message=None)
    r = _from_email(client, "a" * 64)
    assert r.status_code == 404 and r.json()["error"]["details"]["code"] == "document_not_in_message"
    assert app.state.v2_case_command_repository.calls == []


def test_a_message_whose_eml_is_not_stored_asks_for_an_upload() -> None:
    message = {"eml_storage_path": "mail/contacto@origenlab.example/2026/05/old.eml", "filename": "OC.pdf", "subject": ""}
    app, client = _client(message=message)
    r = _from_email(client, _sha(PDF))
    assert r.status_code == 409 and r.json()["error"]["details"]["code"] == "message_not_stored"


def test_an_eml_without_that_attachment_asks_for_an_upload() -> None:
    mail = FakeStorage("mail")
    mail.objects["k.eml"] = (_eml(("otra.pdf", PDF + b"y")), "message/rfc822")
    app, client = _client(message={"eml_storage_path": "mail/k.eml", "filename": "OC.pdf", "subject": ""}, mail=mail)
    r = _from_email(client, _sha(PDF))
    assert r.status_code == 409 and r.json()["error"]["details"]["code"] == "attachment_not_in_eml"
    assert app.state.case_document_storage.objects == {}


def test_a_malformed_hash_is_refused() -> None:
    app, client = _client()
    assert _from_email(client, "Z" * 64).status_code == 422


# ──────────────────────────────────────────────────────────── removal and the link ──

def test_removal_names_the_document_and_its_reason() -> None:
    app, client = _client()
    r = client.post("/v2/commands/remove-case-document", headers=_ikey(),
                    json={"document_id": DOC, "note": "archivo equivocado"})
    assert r.status_code == 200, r.text
    call = app.state.v2_case_command_repository.calls[0]
    assert call["command_name"] == "remove_case_document"
    assert call["fields"] == {"document_id": DOC, "note": "archivo equivocado"}


def test_the_link_is_signed_for_ten_minutes_and_never_cached() -> None:
    path = f"opportunities/{CASE}/{'a' * 64}.pdf"
    app, client = _client("viewer", document={"storage_path": path, "original_filename": "OC 1.pdf"})
    r = client.get(f"/v2/workspace/opportunities/{CASE}/documents/{DOC}/url", headers=HEADERS)
    assert r.status_code == 200, r.text
    assert r.json()["expires_in"] == 600 and path in r.json()["url"]
    assert r.headers["cache-control"] == "no-store"


def test_a_removed_or_foreign_document_has_no_link() -> None:
    app, client = _client(document=None)
    r = client.get(f"/v2/workspace/opportunities/{CASE}/documents/{DOC}/url", headers=HEADERS)
    assert r.status_code == 404
