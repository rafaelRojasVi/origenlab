"""Quote numbers named in what the sender wrote (spec 2026-10-14, «Hoy» automations). Invented
values only; the repository is public."""
from __future__ import annotations

from origenlab_worker.quote_refs import read_quote_refs


def test_a_payment_notice_names_its_quote_in_the_body() -> None:
    assert read_quote_refs("Aviso de transferencia", "Pago por cotización N°09259-26, gracias.") == ["09259-26"]


def test_cn_tokens_and_several_numbers_keep_their_order_once_each() -> None:
    body = "Ref. CN09262 y también 09259-26; de nuevo CN09262."
    assert read_quote_refs(None, body) == ["CN09262", "09259-26"]


def test_orders_long_supplier_numbers_and_bare_numbers_are_not_quotes() -> None:
    assert read_quote_refs(None, "OC-09259-26, pedido 123553-26, cotización 9259 y teléfono 2299-1234") == []


def test_nothing_to_read_is_empty() -> None:
    assert read_quote_refs("Hola", "") == []
