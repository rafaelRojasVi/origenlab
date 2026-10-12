"""Case documents (the purchase order that closes a case): the pure rules — what a document is,
where its object lives, and how an email attachment is found in its `.eml`. Synthetic data only."""
from __future__ import annotations

import hashlib
from email.message import EmailMessage

import pytest

from origenlab_api.v2.case_documents import (
    MAX_DOCUMENT_BYTES,
    DocumentRefused,
    attachment_from_eml,
    check_document,
    clean_filename,
    document_number_from,
    document_path,
    mail_storage_key,
)

PDF = b"%PDF-1.7\n1 0 obj\n<<>>\nendobj\n%%EOF\n"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 16
CASE = "11111111-1111-4111-8111-111111111111"


def test_a_document_is_what_its_bytes_say_not_what_the_browser_declared() -> None:
    assert check_document(PDF) == "application/pdf"
    assert check_document(PNG) == "image/png"
    assert check_document(JPEG) == "image/jpeg"
    with pytest.raises(DocumentRefused) as refused:
        check_document(b"PK\x03\x04 a zip")
    assert (refused.value.status_code, refused.value.code) == (415, "unsupported_document")
    with pytest.raises(DocumentRefused) as empty:
        check_document(b"")
    assert empty.value.code == "unsupported_document"


def test_a_document_over_twenty_mebibytes_is_refused() -> None:
    with pytest.raises(DocumentRefused) as refused:
        check_document(PDF + b"0" * MAX_DOCUMENT_BYTES)
    assert (refused.value.status_code, refused.value.code) == (413, "document_too_large")


def test_the_object_path_is_the_case_and_the_content() -> None:
    sha = hashlib.sha256(PDF).hexdigest()
    assert document_path(CASE, sha, "application/pdf") == f"opportunities/{CASE}/{sha}.pdf"
    assert document_path(CASE, sha, "image/jpeg").endswith(".jpg")
    with pytest.raises(ValueError):
        document_path(CASE, "not-a-hash", "application/pdf")
    with pytest.raises(ValueError):
        document_path("../etc", sha, "application/pdf")


def test_a_file_name_keeps_its_base_name_only() -> None:
    assert clean_filename("C:\\Users\\x\\OC 4500.pdf") == "OC 4500.pdf"
    assert clean_filename("../../secret/orden.pdf") == "orden.pdf"
    assert clean_filename("   ") == "documento"
    assert clean_filename(None) == "documento"
    assert len(clean_filename("a" * 400 + ".pdf")) <= 255


def test_the_purchase_order_number_is_read_from_the_name_when_it_says_so() -> None:
    assert document_number_from("OC 5500012345.pdf") == "5500012345"
    assert document_number_from("Orden de compra 4321 (1).pdf") == "4321"
    assert document_number_from("OC N º 12345.pdf") == "12345"
    assert document_number_from("ORD-CO-17.pdf") == "17"
    assert document_number_from("12345678.pdf") is None
    assert document_number_from(None) is None


def _eml(*attachments: tuple[str, bytes, str]) -> bytes:
    msg = EmailMessage()
    msg["From"] = "compras@cliente.example"
    msg["To"] = "contacto@origenlab.example"
    msg["Subject"] = "OC"
    msg.set_content("Adjunto la orden de compra.")
    for name, data, ctype in attachments:
        main, sub = ctype.split("/")
        msg.add_attachment(data, maintype=main, subtype=sub, filename=name)
    return msg.as_bytes()


def test_an_attachment_is_found_in_its_eml_by_its_hash_only() -> None:
    raw = _eml(("cotizacion.pdf", PDF + b"q", "application/pdf"), ("OC 77.pdf", PDF, "application/pdf"))
    found = attachment_from_eml(raw, hashlib.sha256(PDF).hexdigest())
    assert found is not None and found.data == PDF and found.filename == "OC 77.pdf"
    assert attachment_from_eml(raw, "0" * 64) is None


def test_the_eml_object_key_drops_the_bucket_prefix() -> None:
    assert mail_storage_key("mail/contacto@x.example/2026/10/abc.eml") == "contacto@x.example/2026/10/abc.eml"
    with pytest.raises(ValueError):
        mail_storage_key("mail/../catalog/x")
    with pytest.raises(ValueError):
        mail_storage_key("other/x.eml")
