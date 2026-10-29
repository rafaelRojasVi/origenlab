"""Quote numbers named in what the sender wrote (spec 2026-10-14, «Hoy» automations). Invented
values only; the repository is public."""
from __future__ import annotations

from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import logging

from unittest.mock import MagicMock

from origenlab_worker.quote_refs import read_quote_refs
from origenlab_worker.triage import _is_oc_pdf, _po_refs_from_raw, _po_refs_from_text, record_po_refs


def _make_eml(attachments: list[tuple[str, bytes]]) -> bytes:
    """Minimal EML with the given (filename, data) attachments."""
    msg = MIMEMultipart()
    msg["From"] = "proveedor@ejemplo.invalid"
    msg["To"] = "contacto@origenlab.cl"
    msg["Subject"] = "Orden de compra"
    msg.attach(MIMEText("Se adjunta la orden de compra.", "plain"))
    for name, data in attachments:
        part = MIMEApplication(data, Name=name)
        part["Content-Disposition"] = f'attachment; filename="{name}"'
        msg.attach(part)
    return msg.as_bytes()


def test_a_payment_notice_names_its_quote_in_the_body() -> None:
    assert read_quote_refs("Aviso de transferencia", "Pago por cotización N°09259-26, gracias.") == ["09259-26"]


def test_cn_tokens_and_several_numbers_keep_their_order_once_each() -> None:
    body = "Ref. CN09262 y también 09259-26; de nuevo CN09262."
    assert read_quote_refs(None, body) == ["CN09262", "09259-26"]


def test_orders_long_supplier_numbers_and_bare_numbers_are_not_quotes() -> None:
    assert read_quote_refs(None, "OC-09259-26, pedido 123553-26, cotización 9259 y teléfono 2299-1234") == []


def test_nothing_to_read_is_empty() -> None:
    assert read_quote_refs("Hola", "") == []


# ------------------------------------------------------------------ po_refs:v1 (goal 2, 2026-10-29)

def test_po_refs_from_raw_yields_empty_for_non_oc_attachment() -> None:
    eml = _make_eml([("cotizacion.pdf", b"%PDF fake")])
    tokens, po_numbers = _po_refs_from_raw(eml)
    assert tokens == [] and po_numbers == []


def test_po_refs_from_raw_yields_empty_for_oc_without_text_layer() -> None:
    # OC filename matches but PDF has no text layer (scanned); pdftotext returns ""
    eml = _make_eml([("OC 4500000123.pdf", b"%PDF fake-scanned")])
    tokens, po_numbers = _po_refs_from_raw(eml)
    assert tokens == [] and po_numbers == []


def test_po_refs_from_raw_yields_empty_when_no_attachments() -> None:
    eml = _make_eml([])
    tokens, po_numbers = _po_refs_from_raw(eml)
    assert tokens == [] and po_numbers == []


# -------- _is_oc_pdf: OC attachment detection (goal 3 fix, 2026-10-29) --------

def test_is_oc_pdf_matches_oc_prefix() -> None:
    assert _is_oc_pdf("OC 4500000123.pdf")
    assert _is_oc_pdf("oc_991.pdf")
    assert _is_oc_pdf("O.C. 77.pdf")


def test_is_oc_pdf_matches_po_prefix() -> None:
    assert _is_oc_pdf("PO_4500012345.pdf")
    assert _is_oc_pdf("po 123.pdf")


def test_is_oc_pdf_matches_orden_de_compra_in_name() -> None:
    # Real-world long-form names seen in practice
    assert _is_oc_pdf("4521-2026 Orden de compra Proveedor Ficticio.pdf")
    assert _is_oc_pdf("orden_de_compra_4500.pdf")


def test_is_oc_pdf_matches_purchase_order_in_name() -> None:
    assert _is_oc_pdf("Purchase Order 123.pdf")
    assert _is_oc_pdf("purchase_order_abc.pdf")


def test_is_oc_pdf_rejects_non_pdf() -> None:
    assert not _is_oc_pdf("OC 4500000123.docx")
    assert not _is_oc_pdf("purchase order.xlsx")


def test_is_oc_pdf_rejects_unrelated_pdf() -> None:
    assert not _is_oc_pdf("cotizacion.pdf")
    assert not _is_oc_pdf("factura_1234.pdf")
    assert not _is_oc_pdf("ocean_research.pdf")   # «oc» inside a word — should not match


# -------- _po_refs_from_text: pure text→tokens (goal 2 fix, 2026-10-29) ------

def test_po_refs_from_text_extracts_cn_and_oc_from_uppercase_text() -> None:
    # PDF text may be uppercase; fold() must be applied before the regexes
    tokens, po_numbers = _po_refs_from_text(
        "ORDEN DE COMPRA N° 4500012345\nN° COTIZACIÓN CN09876"
    )
    assert "CN09876" in tokens
    assert "4500012345" in po_numbers


def test_po_refs_from_text_empty_on_blank() -> None:
    tokens, po_numbers = _po_refs_from_text("")
    assert tokens == [] and po_numbers == []


# -------- record_po_refs: no DB write on ordinary mail (issue 9, 2026-10-29) -

def test_record_po_refs_does_not_write_for_non_oc_email() -> None:
    """An ordinary email with no OC PDF must not write any po_refs:v1 assertion."""
    db = MagicMock()
    eml = _make_eml([("invoice_1234.pdf", b"%PDF fake")])
    result = record_po_refs(db, "src-ficticio-001", eml)
    assert result is False
    db.record_po_refs.assert_not_called()


# -------- exception logging (goal 7 fix, 2026-10-29) -------------------------

def test_po_refs_from_raw_logs_and_returns_empty_on_invalid_eml(caplog) -> None:
    with caplog.at_level(logging.WARNING):
        tokens, po_numbers = _po_refs_from_raw(b"not-an-eml-at-all\xff\xfe")
    # The function must not raise; the warning must be logged
    assert tokens == [] and po_numbers == []
    # Warning with the exception class name (not the email content)
    assert any("po reference" in r.message.lower() for r in caplog.records)
