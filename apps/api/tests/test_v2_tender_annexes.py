"""«Subir anexos» without a database: the file rules, hostile zips, and who may call the route.

Every value is invented (public repository). `test_v2_tender_annexes_db.py` runs the transaction.
"""

from __future__ import annotations

import io
import zipfile

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from origenlab_api.commercial_operator_identity import OPERATOR_EMAIL_HEADER
from origenlab_api.v2.case_documents import DOC, DOCX, XLS, XLSX, DocumentRefused, check_document, document_path
from origenlab_api.v2.catalog.storage import FakeStorage
from origenlab_api.v2.identity import LocalDevIdentity, OperatorIdentity, OperatorLookup
from origenlab_api.v2.tenders.annexes import expand_uploads

LOOPBACK = "postgresql://origenlab_api:pw@127.0.0.1:54332/origenlab_dev"
CASE = "00000000-0000-4000-8000-0000000000c1"
PDF = b"%PDF-1.7 bases de ejemplo "
MiB = 1024 * 1024
OLE = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 64


def office(main_part: str, extra: dict[str, bytes] | None = None) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr(main_part, "<x/>")
        for name, data in (extra or {}).items():
            archive.writestr(name, data)
    return out.getvalue()


def zipped(members: dict[str, bytes], method: int = zipfile.ZIP_DEFLATED) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", method) as archive:
        for name, data in members.items():
            archive.writestr(name, data)
    return out.getvalue()


DOCX_BYTES = office("word/document.xml")
XLSX_BYTES = office("xl/workbook.xml")


def reasons(skipped) -> dict[str, str]:
    return {s["filename"]: s["reason"] for s in skipped}


def test_the_bytes_decide_including_legacy_word_and_excel() -> None:
    assert check_document(DOCX_BYTES, annex=True) == DOCX
    assert check_document(XLSX_BYTES, annex=True) == XLSX
    assert check_document(OLE + "WordDocument".encode("utf-16-le"), annex=True) == DOC
    assert check_document(OLE + "Workbook".encode("utf-16-le"), annex=True) == XLS
    assert check_document(PDF, annex=True) == "application/pdf"
    for data in (OLE, b"Rar!\x1a\x07\x00", b"", office("otra/cosa.xml"),
                 office("word/document.xml", {"word/vbaProject.bin": b"macro"})):
        with pytest.raises(DocumentRefused) as exc:
            check_document(data, annex=True)
        assert exc.value.status_code == 415
    # A purchase order still takes only PDF, JPEG or PNG.
    with pytest.raises(DocumentRefused):
        check_document(DOCX_BYTES)
    assert document_path(CASE, "ab" * 32, DOCX) == f"opportunities/{CASE}/{'ab' * 32}.docx"
    assert document_path(CASE, "ab" * 32, XLS).endswith(".xls")


def test_loose_files_are_kept_and_the_rest_listed_with_a_reason() -> None:
    kept, skipped = expand_uploads([
        ("bases.pdf", PDF), ("anexo1.docx", DOCX_BYTES), ("x.rar", b"Rar!\x1a\x07\x00"),
        ("grande.pdf", b"%PDF-" + b"0" * (21 * MiB)), ("vacio.pdf", b""),
    ])
    assert [(n, t) for n, t, _ in kept] == [("bases.pdf", "application/pdf"), ("anexo1.docx", DOCX)]
    assert reasons(skipped) == {"x.rar": "unsupported_type", "grande.pdf": "too_large",
                                "vacio.pdf": "unsupported_type"}


def test_a_zip_is_unpacked_and_each_member_judged() -> None:
    package = zipped({"bases.pdf": PDF, "carpeta/anexo1.docx": DOCX_BYTES, "../evil.pdf": PDF,
                      "planilla.xlsx": XLSX_BYTES, "x.rar": b"Rar!\x1a\x07\x00", "/abs.pdf": PDF,
                      "C:/win.pdf": PDF, "dentro.zip": zipped({"a.pdf": PDF})})
    kept, skipped = expand_uploads([("paquete.zip", package)])
    assert sorted(n for n, _, _ in kept) == ["anexo1.docx", "bases.pdf", "planilla.xlsx"]
    assert reasons(skipped) == {"../evil.pdf": "unsafe_path", "/abs.pdf": "unsafe_path",
                                "C:/win.pdf": "unsafe_path", "x.rar": "unsupported_type",
                                "dentro.zip": "unsupported_type"}


def test_a_zip_bomb_skips_every_member_as_archive_rejected() -> None:
    bomb = zipped({"a.pdf": b"%PDF-" + b"\0" * (10 * MiB), "b.docx": b"\0" * MiB, "c.pdf": PDF})
    assert len(bomb) * 200 < 11 * MiB + 100
    kept, skipped = expand_uploads([("bomba.zip", bomb)])
    assert kept == []
    assert reasons(skipped) == {"a.pdf": "archive_rejected", "b.docx": "archive_rejected",
                                "c.pdf": "archive_rejected"}


def test_a_lying_header_bomb_is_caught_by_the_actual_expansion() -> None:
    raw = bytearray(zipped({"a.pdf": b"%PDF-" + b"\0" * (4 * MiB)}))
    # Understate the uncompressed size in both headers (local at 22, central directory).
    for marker in (b"PK\x03\x04", b"PK\x01\x02"):
        at = raw.index(marker)
        off = at + (22 if marker == b"PK\x03\x04" else 24)
        raw[off:off + 4] = (100).to_bytes(4, "little")
    kept, skipped = expand_uploads([("miente.zip", bytes(raw))])
    assert kept == [] and reasons(skipped) == {"a.pdf": "archive_rejected"}


def test_too_many_members_and_a_broken_zip_are_rejected_not_raised() -> None:
    many = zipped({f"f{i}.pdf": PDF + str(i).encode() for i in range(513)})
    kept, skipped = expand_uploads([("muchos.zip", many)])
    assert kept == [] and all(s["reason"] == "archive_rejected" for s in skipped) and len(skipped) == 101
    assert skipped[-1]["filename"] == "muchos.zip (y 413 archivos más)"
    kept, skipped = expand_uploads([("roto.zip", b"PK\x03\x04" + b"basura" * 20)])
    assert kept == [] and skipped == [{"filename": "roto.zip", "reason": "archive_rejected"}]


def test_an_encrypted_member_and_an_encrypted_office_file_are_listed() -> None:
    raw = bytearray(zipped({"secreto.pdf": PDF, "libre.pdf": PDF + b"2"}))
    for marker, flag_at in ((b"PK\x03\x04", 6), (b"PK\x01\x02", 8)):
        at = raw.index(marker)  # the first entry, secreto.pdf (sorted order is by name)
        raw[at + flag_at] |= 1
    kept, skipped = expand_uploads([("cifrado.zip", bytes(raw))])
    assert reasons(skipped) == {"secreto.pdf": "encrypted"}
    assert [n for n, _, _ in kept] == ["libre.pdf"]
    # A password-protected Word file is an OLE wrapper around an EncryptedPackage stream.
    kept, skipped = expand_uploads([("clave.docx", OLE + "EncryptedPackage".encode("utf-16-le"))])
    assert kept == [] and reasons(skipped) == {"clave.docx": "encrypted"}


def test_an_office_file_that_expands_unsafely_is_archive_rejected() -> None:
    bomb = office("word/document.xml", {"word/media/big.bin": b"\0" * (8 * MiB)})
    assert len(bomb) * 200 < 8 * MiB
    kept, skipped = expand_uploads([("bomba.docx", bomb)])
    assert kept == [] and reasons(skipped) == {"bomba.docx": "archive_rejected"}


# ------------------------------------------------------------------ who may call


class _Lookup(OperatorLookup):
    def __init__(self, operator: OperatorIdentity) -> None:
        self._operator = operator

    def by_email(self, email_norm: str) -> OperatorIdentity | None:
        return self._operator


class _Recorder:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def execute(self, **kwargs):
        self.calls.append(kwargs)
        return {"command": kwargs["command_name"], "replayed": False, "attached": [], "skipped": []}


def client(role: str) -> tuple[TestClient, _Recorder, FakeStorage]:
    from origenlab_api.v2.tenders.routes import tender_command_router

    app = FastAPI()
    app.include_router(tender_command_router)
    recorder, storage = _Recorder(), FakeStorage("crm-documents")
    app.state.v2_tender_command_repository = recorder
    app.state.case_document_storage = storage
    operator = OperatorIdentity(operator_id="00000000-0000-4000-8000-000000000001",
                                email_norm="operator@example.test", display_name="Operador", role=role,
                                status="active")
    app.state.v2_identity = LocalDevIdentity(LOOPBACK, _Lookup(operator))
    return TestClient(app), recorder, storage


HEADERS = {OPERATOR_EMAIL_HEADER: "operator@example.test", "Idempotency-Key": "pytest-annex-1"}
URL = "/v2/commands/attach-tender-annexes"


def files(*pairs: tuple[str, bytes]):
    return [("files", (name, data, "application/octet-stream")) for name, data in pairs]


def test_a_viewer_is_refused_before_anything_is_stored() -> None:
    http, recorder, storage = client("viewer")
    response = http.post(URL, data={"opportunity_id": CASE}, files=files(("a.pdf", PDF)), headers=HEADERS)
    assert response.status_code == 403
    assert recorder.calls == [] and storage.puts == 0


def test_it_needs_a_key_a_case_id_and_between_one_and_twenty_files() -> None:
    http, recorder, storage = client("sales")
    no_key = {OPERATOR_EMAIL_HEADER: HEADERS[OPERATOR_EMAIL_HEADER]}
    assert http.post(URL, data={"opportunity_id": CASE}, files=files(("a.pdf", PDF)), headers=no_key).status_code == 400
    assert http.post(URL, data={"opportunity_id": "no-es-uuid"}, files=files(("a.pdf", PDF)),
                     headers=HEADERS).status_code == 422
    assert http.post(URL, data={"opportunity_id": CASE}, files={"x": ("a.txt", b"hola")},
                     headers=HEADERS).status_code == 422
    twenty_one = files(*[(f"a{i}.pdf", PDF + str(i).encode()) for i in range(21)])
    assert http.post(URL, data={"opportunity_id": CASE}, files=twenty_one, headers=HEADERS).status_code == 422
    assert http.post(URL, data={"opportunity_id": CASE}, files=[("file", ("a.pdf", PDF))],
                     headers=HEADERS).status_code == 422
    assert recorder.calls == [] and storage.puts == 0


def test_files_and_a_zip_are_stored_by_content_and_the_command_gets_the_rest() -> None:
    http, recorder, storage = client("sales")
    package = zipped({"bases.pdf": PDF + b"zip", "../evil.pdf": PDF, "x.rar": b"Rar!\x1a\x07\x00"})
    response = http.post(
        URL, data={"opportunity_id": CASE},
        files=files(("uno.pdf", PDF), ("dos.docx", DOCX_BYTES), ("paquete.zip", package),
                    ("grande.pdf", b"%PDF-" + b"0" * (21 * MiB))),
        headers=HEADERS)
    assert response.status_code == 200, response.text
    (call,) = recorder.calls
    assert call["command_name"] == "attach-tender-annexes"
    fields = call["fields"]
    assert [f["filename"] for f in fields["files"]] == ["uno.pdf", "dos.docx", "bases.pdf"]
    assert reasons(fields["skipped"]) == {"grande.pdf": "too_large", "../evil.pdf": "unsafe_path",
                                          "x.rar": "unsupported_type"}
    assert len(storage.objects) == 3
    assert all(p.startswith(f"opportunities/{CASE}/") for p in storage.objects)
    assert {t for _, t in storage.objects.values()} == {"application/pdf", DOCX}


def test_the_whole_body_is_capped_at_60_mib() -> None:
    http, recorder, storage = client("sales")
    chunk = b"%PDF-" + b"0" * (19 * MiB)
    response = http.post(URL, data={"opportunity_id": CASE},
                         files=files(*[(f"{i}.pdf", chunk + bytes([i])) for i in range(4)]), headers=HEADERS)
    assert response.status_code == 413
    assert recorder.calls == [] and storage.puts == 0
