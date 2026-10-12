"""The quote reader, pure: a registered quote's PDF → the evidence the history import writes, or a
refusal with a reason. Every text, brand, model, client and amount here is invented."""

from __future__ import annotations

import base64
import json
from decimal import Decimal
from types import SimpleNamespace

import pytest

from origenlab_worker.quote_reader import (
    EXTRACTOR_AI,
    EXTRACTOR_TEMPLATE,
    ReadRefused,
    build_request,
    pdf_text,
    pdftotext_available,
    read_document,
)

SHA = "b" * 64
MODEL = "claude-opus-5-5"

#: The spreadsheet Plantilla as a PDF's text layer prints it (same shape as the history check's tests).
TEXT = """\
OrigenLab                                                                 ventas@example.test

 ITEM              MARCA / REF.                    DETALLE / ESPECIFICACIONES            CANT.      PRECIO UNIT. TOTAL
                                            Descripción sintética de la fila uno,
        1 ACME · ACME-1                     sigue la descripción                          1              $1,236,000
        2 ACME · SONIC-100                  Otro equipo sintético                         2              $1.000.000
                                                                   SUBTOTAL NETO                         $2,236,000
"""


def _extraction(**over):
    base = {
        "is_origenlab_issued_quote": True, "quote_number": "00999-26", "date": "2026-10-01",
        "client_type": "university", "currency": "CLP", "subtotal_net": "2236000", "template": "xlsx",
        "client_institution": "Universidad Ficticia del Sur", "client_contact": "Ana Ficticia",
        "lines": [
            {"item": "1", "parent_item": None, "kind": "equipment", "brand": "ACME", "model": "ACME-1",
             "description": "Equipo ficticio uno", "qty": "1", "unit_price": "1236000", "line_total": "1236000",
             "optional": False},
            {"item": "2", "parent_item": None, "kind": "equipment", "brand": "ACME", "model": "SONIC-100",
             "description": "Otro equipo ficticio", "qty": "2", "unit_price": "500000", "line_total": "1000000",
             "optional": False},
        ],
    }
    base.update(over)
    return base


def _message(payload=None, *, stop="end_turn", text=None):
    body = text if text is not None else json.dumps(payload)
    return SimpleNamespace(stop_reason=stop, content=[SimpleNamespace(type="text", text=body)],
                           usage=SimpleNamespace(input_tokens=1200, output_tokens=300))


class Reader:
    def __init__(self, message):
        self.message = message
        self.requests = []

    def __call__(self, request):
        self.requests.append(request)
        return self.message


def test_a_quote_is_read_into_document_lines_cross_checked_against_its_text() -> None:
    reader = Reader(_message(_extraction()))
    reading, log = read_document(b"%PDF-fake", SHA, reader=reader, model=MODEL, text=TEXT)
    assert reading.extractor == EXTRACTOR_AI
    assert reading.payload["net_total"] == "2236000" and reading.payload["currency"] == "CLP"
    assert reading.payload["printed_quote_number"] == "00999-26"
    assert [ln["check_status"] for ln in reading.lines] == ["verified", "verified"]
    assert reading.lines[1]["qty"] == Decimal("2") and reading.lines[1]["unit_price"] == Decimal("500000")
    assert log["outcome"] == "read" and log["lines"] == 2 and log["input_tokens"] == 1200
    stored = json.dumps([reading.payload, reading.lines], default=str)
    assert "Universidad Ficticia" not in stored and "Ana Ficticia" not in stored  # a category, never a name
    assert reading.payload["client_type"] == "university"


def test_the_client_never_reaches_a_written_line() -> None:
    lines = _extraction()["lines"]
    lines[0]["description"] = "Equipo para Universidad Ficticia del Sur"
    reading, log = read_document(b"x", SHA, reader=Reader(_message(_extraction(lines=lines))), model=MODEL, text=TEXT)
    assert reading.lines[0]["description"] is None and log["strings_withheld"] == 1


@pytest.mark.parametrize("extraction,text", [
    (_extraction(is_origenlab_issued_quote=False), TEXT),
    (_extraction(), TEXT.replace("OrigenLab", "Labdelivery")),
])
def test_what_is_not_an_origenlab_quote_is_refused(extraction, text) -> None:
    with pytest.raises(ReadRefused) as caught:
        read_document(b"x", SHA, reader=Reader(_message(extraction)), model=MODEL, text=text)
    assert caught.value.code == "not_origenlab_quote"


def test_too_many_disputed_lines_refuse_the_document() -> None:
    lines = _extraction()["lines"]
    lines[0]["line_total"] = "999"
    lines[1]["line_total"] = "888"
    with pytest.raises(ReadRefused) as caught:
        read_document(b"x", SHA, reader=Reader(_message(_extraction(lines=lines, subtotal_net="1887"))),
                      model=MODEL, text=TEXT)
    assert caught.value.code == "too_many_disputed"


def test_a_neto_that_does_not_match_its_lines_is_refused_but_optional_lines_do_not_count() -> None:
    with pytest.raises(ReadRefused) as caught:
        read_document(b"x", SHA, reader=Reader(_message(_extraction(subtotal_net="3000000"))), model=MODEL, text=TEXT)
    assert caught.value.code == "net_mismatch"
    lines = _extraction()["lines"] + [
        {"item": "3", "parent_item": None, "kind": "accessory", "brand": None, "model": None,
         "description": "Opcional ficticio", "qty": "1", "unit_price": "500000", "line_total": "500000",
         "optional": True}]
    reading, _ = read_document(b"x", SHA, reader=Reader(_message(_extraction(lines=lines))), model=MODEL, text=TEXT)
    assert len(reading.lines) == 3 and reading.payload["net_total"] == "2236000"


@pytest.mark.parametrize("message,code", [
    (_message(stop="refusal", text=""), "model_refused"),
    (_message(stop="max_tokens", text="{"), "model_refused"),
    (_message(text="not json"), "unreadable"),
    (_message({"lines": "nope", "currency": "CLP", "is_origenlab_issued_quote": True}), "unreadable"),
])
def test_an_unusable_model_answer_is_refused_by_code(message, code) -> None:
    with pytest.raises(ReadRefused) as caught:
        read_document(b"x", SHA, reader=Reader(message), model=MODEL, text=TEXT)
    assert caught.value.code == code


def test_with_the_model_off_the_template_alone_gives_models_and_totals() -> None:
    reading, log = read_document(b"x", SHA, reader=None, model="off", text=TEXT)
    assert reading.extractor == EXTRACTOR_TEMPLATE and log["model"] == "off"
    assert [(ln["model"], ln["line_total"], ln["check_status"]) for ln in reading.lines] == [
        ("ACME-1", Decimal("1236000"), "single_source"), ("SONIC-100", Decimal("1000000"), "single_source")]
    assert reading.payload["net_total"] == "2236000" and reading.lines[0]["description"] is None
    with pytest.raises(ReadRefused) as caught:
        read_document(b"x", SHA, reader=None, model="off", text="")
    assert caught.value.code == "unreadable"


def test_the_request_sends_the_pdf_as_a_document_with_a_closed_schema() -> None:
    pdf = b"%PDF-1.4 ficticio"
    request = build_request(pdf, model=MODEL)
    blocks = request["messages"][0]["content"]
    document = next(b for b in blocks if b["type"] == "document")
    assert document["source"] == {"type": "base64", "media_type": "application/pdf",
                                  "data": base64.b64encode(pdf).decode()}
    assert request["output_config"]["format"]["type"] == "json_schema"
    schema = request["output_config"]["format"]["schema"]
    assert schema["additionalProperties"] is False and "lines" in schema["required"]
    assert "untrusted" in request["system"][0]["text"].lower()
    with pytest.raises(ValueError):
        build_request(pdf, model="not-a-claude-model")


def _minimal_pdf(text: str) -> bytes:
    """A one-page PDF whose text layer is `text` (Helvetica, one line)."""
    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for n, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{n} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)


@pytest.mark.skipif(not pdftotext_available(), reason="poppler's pdftotext is not installed here")
def test_pdf_text_reads_the_text_layer_with_pdftotext() -> None:
    assert "COTIZACION FICTICIA" in pdf_text(_minimal_pdf("COTIZACION FICTICIA"))


def test_pdf_text_never_raises_and_is_empty_without_a_text_layer_or_without_poppler(monkeypatch) -> None:
    assert pdf_text(b"not a pdf at all") == ""
    monkeypatch.setattr("origenlab_worker.quote_reader.shutil.which", lambda _name: None)
    assert pdf_text(_minimal_pdf("COTIZACION FICTICIA")) == ""


# ── final-review fixes (2026-10-11) ───────────────────────────────────────────────────────────

def test_a_pdf_without_a_text_layer_is_refused_when_poppler_could_have_read_it(monkeypatch) -> None:
    monkeypatch.setattr("origenlab_worker.quote_reader.pdftotext_available", lambda: True)
    with pytest.raises(ReadRefused) as caught:
        read_document(b"x", SHA, reader=Reader(_message(_extraction())), model=MODEL, text="")
    assert caught.value.code == "no_text_layer"


def test_the_prompt_says_only_origenlab_issues_a_quote_never_labdelivery() -> None:
    system = build_request(b"x", model=MODEL)["system"][0]["text"]
    assert "Labdelivery" in system


def test_a_missing_neto_is_the_sum_of_the_lines_and_no_line_total_at_all_is_unreadable() -> None:
    reading, _ = read_document(b"x", SHA, reader=Reader(_message(_extraction(subtotal_net=None))), model=MODEL, text=TEXT)
    assert reading.payload["net_total"] == "2236000"
    lines = [{**ln, "line_total": None} for ln in _extraction()["lines"]]
    with pytest.raises(ReadRefused) as caught:
        read_document(b"x", SHA, reader=Reader(_message(_extraction(lines=lines, subtotal_net=None))), model=MODEL, text=TEXT)
    assert caught.value.code == "unreadable"


def test_the_neto_check_still_runs_when_one_line_has_no_total() -> None:
    lines = _extraction()["lines"]
    lines[1]["line_total"] = None  # an «incluido» row: the printed neto must still match the rest
    with pytest.raises(ReadRefused) as caught:
        read_document(b"x", SHA, reader=Reader(_message(_extraction(lines=lines, subtotal_net="9000000"))),
                      model=MODEL, text=TEXT)
    assert caught.value.code == "net_mismatch"


def test_an_absurd_amount_or_a_nul_byte_never_reaches_the_database() -> None:
    lines = _extraction()["lines"]
    lines[0]["qty"] = "10000000000000"  # numeric(18,6) holds less
    with pytest.raises(ReadRefused) as caught:
        read_document(b"x", SHA, reader=Reader(_message(_extraction(lines=lines))), model=MODEL, text=TEXT)
    assert caught.value.code == "unreadable"
    lines = _extraction()["lines"]
    lines[0]["description"] = "Equipo\x00ficticio"
    reading, _ = read_document(b"x", SHA, reader=Reader(_message(_extraction(lines=lines))), model=MODEL, text=TEXT)
    assert "\x00" not in reading.lines[0]["description"]


def _unions(schema) -> int:
    """Parameters whose type is a union (anyOf or a type array), counted the way the API does."""
    n = 0
    if isinstance(schema, dict):
        if "anyOf" in schema or isinstance(schema.get("type"), list):
            n += 1
        for value in schema.values():
            n += _unions(value)
    elif isinstance(schema, list):
        n += sum(_unions(v) for v in schema)
    return n


def test_the_answer_schema_stays_within_the_apis_union_limit() -> None:
    from origenlab_worker.quote_reader import EXTRACTION_SCHEMA

    assert _unions(EXTRACTION_SCHEMA) <= 16  # the API refuses more (HTTP 400, 2026-10-11 evaluation)


def test_empty_client_and_parent_strings_mean_none() -> None:
    lines = _extraction()["lines"]
    lines[0]["parent_item"] = ""
    reading, _ = read_document(b"x", SHA, reader=Reader(_message(_extraction(lines=lines, client_unit="", client_contact=""))),
                               model=MODEL, text=TEXT)
    assert reading.lines[0]["parent_item"] is None


def test_a_date_written_out_in_words_does_not_refuse_the_quote() -> None:
    reading, _ = read_document(b"x", SHA, reader=Reader(_message(_extraction(date="Valdivia, 07 de mayo del 2026"))),
                               model=MODEL, text=TEXT)
    assert reading.payload["quote_date"] is None and reading.payload["net_total"] == "2236000"


def test_the_prompt_asks_for_an_iso_date() -> None:
    assert "YYYY-MM-DD" in build_request(b"x", model=MODEL)["system"][0]["text"]
