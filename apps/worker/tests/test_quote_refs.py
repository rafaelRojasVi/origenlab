"""Quote numbers named in what the sender wrote (spec 2026-10-14, «Hoy» automations). Invented
values only; the repository is public."""
from __future__ import annotations

from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from origenlab_worker.quote_refs import read_quote_refs
from origenlab_worker.triage import _po_refs_from_raw


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
