"""The quote-history cross-check, pure: template parsing, line matching, the evidence payload.

Every text, brand, model, client and amount here is invented; none comes from a real quote.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from origenlab_api.v2.catalog.history_check import (
    TemplateLine,
    cross_check,
    normalise_quote_date,
    parse_template_lines,
    quote_document_payload,
)

SHA = "a" * 64

#: The docx/web template as pdftotext -layout prints it: the header split over three lines, a
#: row's REF and amount on different lines, rows separated by blank lines.
DOCX_TEXT = """\
                                                                   Valdivia, 1 de enero de 2026

COTIZACION N°00001-26
Sra. Cliente Sintética


                                                 DETALLE
           REF                                                                                TOTAL
  ITEM


   1     ACME-1     Equipo sintético de prueba, modelo uno                      $ 1.234.000


         SONIC-100
   2.               Homogeneizador sintético, 230 V, con
                    descripción que continúa en otra línea
                                                                                $ 3.400.000
                    y una línea final


   2.1      25      Accesorio sintético sin modelo propio                       $ 250.000


                    Línea sin ítem ni referencia                                $ 90.000


                                                              NETO              $ 4.974.000
                                                              IVA 19%           $ 945.060
                                                              TOTAL             $ 5.919.060
"""

#: The xlsx master: one printed row per line, `ITEM BRAND · MODEL … CANT … TOTAL`.
XLSX_TEXT = """\
OrigenLab                                                                 ventas@example.test

 ITEM              MARCA / REF.                    DETALLE / ESPECIFICACIONES            CANT.      PRECIO UNIT. TOTAL
                                            Descripción sintética de la fila uno,
        1 ACME · ACME-1                     sigue la descripción                          1              $1,234,000
        2 ACME · SONIC-100                  Otro equipo sintético                         2              $1.000.000
                                                                   SUBTOTAL NETO                         $2,234,000
"""


# ------------------------------------------------------------------ parse_template_lines

def test_docx_layout_rows_and_totals() -> None:
    lines = parse_template_lines(DOCX_TEXT)
    assert lines == [
        TemplateLine(item_label="1", model="ACME-1", line_total=Decimal("1234000")),
        TemplateLine(item_label="2", model="SONIC-100", line_total=Decimal("3400000")),
        TemplateLine(item_label="2.1", model="25", line_total=Decimal("250000")),
        TemplateLine(item_label=None, model=None, line_total=Decimal("90000")),
    ]
    assert all(isinstance(line.line_total, Decimal) for line in lines)


def test_xlsx_layout_rows_and_totals() -> None:
    assert parse_template_lines(XLSX_TEXT) == [
        TemplateLine(item_label="1", model="ACME-1", line_total=Decimal("1234000")),
        TemplateLine(item_label="2", model="SONIC-100", line_total=Decimal("1000000")),
    ]


def test_single_line_row_with_the_briefs_amount() -> None:
    text = "  ITEM   REF    DETALLE      TOTAL\n\n   1     ACME-1    Equipo     $ 1.234.000\n"
    assert parse_template_lines(text) == [TemplateLine("1", "ACME-1", Decimal("1234000"))]


def test_text_without_a_known_header_gives_model_less_lines() -> None:
    text = "Equipo sintético (230 V): 1 a 10 ml          $ 1.500.000\nTotal neto:        $ 1.500.000\n"
    assert parse_template_lines(text) == [TemplateLine(None, None, Decimal("1500000"))]


def test_lines_not_ending_with_a_clp_amount_are_ignored() -> None:
    text = "  ITEM   REF    DETALLE      TOTAL\n\n   1     ACME-1    Equipo  $ 1.234.000 aprox\n" \
           "\n   2     ACME-2    Equipo     USD 1,234.50\n\n   3     ACME-3    Equipo     $ 500\n"
    assert parse_template_lines(text) == []


# ------------------------------------------------------------------ cross_check

def _ai(item: str | None, model: str | None, total: str | None) -> dict:
    return {"item": item, "model": model, "line_total": None if total is None else Decimal(total)}


def test_cross_check_verified_by_model_and_total() -> None:
    [line] = cross_check([_ai("1", "acme 1", "1234000")], [TemplateLine("1", "ACME-1", Decimal("1234000"))])
    assert line.check_status == "verified" and line.ai_index == 0
    assert line.ai_total == line.template_total == Decimal("1234000")


def test_cross_check_verified_when_the_template_line_has_no_model() -> None:
    checked = cross_check([_ai("2.1", "ACME-9", "250000"), _ai(None, "ACME-8", "90000")],
                          [TemplateLine("2.1", None, Decimal("250000")), TemplateLine(None, None, Decimal("90000"))])
    assert [c.check_status for c in checked] == ["verified", "verified"]


def test_cross_check_disputed_when_totals_differ_for_the_same_model() -> None:
    [line] = cross_check([_ai("1", "ACME-1", "1234000")], [TemplateLine("1", "ACME-1", Decimal("1243000"))])
    assert line.check_status == "disputed"
    assert (line.ai_total, line.template_total) == (Decimal("1234000"), Decimal("1243000"))


def test_cross_check_disputed_when_totals_differ_for_the_same_item_label() -> None:
    [line] = cross_check([_ai("2.", "ACME-2", "100000")], [TemplateLine("2", None, Decimal("110000"))])
    assert line.check_status == "disputed"


def test_cross_check_single_source_on_either_side() -> None:
    checked = cross_check([_ai("1", "ACME-1", "1234000"), _ai("3", "ACME-3", None)],
                          [TemplateLine("2", "ACME-2", Decimal("500000"))])
    by_side = {(c.ai_index is not None, c.check_status) for c in checked}
    assert by_side == {(True, "single_source"), (False, "single_source")}
    assert len(checked) == 3
    template_only = [c for c in checked if c.ai_index is None]
    assert template_only[0].template_total == Decimal("500000") and template_only[0].ai_total is None


def test_cross_check_a_different_model_and_item_is_not_the_same_line() -> None:
    checked = cross_check([_ai("1", "ACME-1", "1234000")], [TemplateLine("2", "ACME-2", Decimal("1234000"))])
    assert sorted(c.check_status for c in checked) == ["single_source", "single_source"]


def test_cross_check_verified_by_model_and_total_records_its_reason() -> None:
    [line] = cross_check([_ai("4", "ACME-1", "1234000")], [TemplateLine("9", "ACME-1", Decimal("1234000"))])
    assert (line.check_status, line.reason) == ("verified", "model_total_match")


def test_cross_check_same_item_and_total_is_verified_even_when_the_models_differ() -> None:
    [line] = cross_check([_ai("1", "ACME-1", "1234000")], [TemplateLine("1", "1234567", Decimal("1234000"))])
    assert (line.check_status, line.reason) == ("verified", "item_total_match")


def test_cross_check_same_item_other_total_and_other_model_stays_apart() -> None:
    checked = cross_check([_ai("1", "ACME-1", "1234000")], [TemplateLine("1", "ACME-2", Decimal("1000"))])
    assert sorted(c.check_status for c in checked) == ["single_source", "single_source"]
    assert all(c.reason is None for c in checked)


def test_cross_check_prefers_an_equal_total_and_uses_each_template_line_once() -> None:
    template = [TemplateLine("1", "ACME-1", Decimal("100")), TemplateLine("1", "ACME-1", Decimal("200"))]
    checked = cross_check([_ai("1", "ACME-1", "200"), _ai("1", "ACME-1", "100")], template)
    assert [c.check_status for c in checked] == ["verified", "verified"]
    assert [c.template_total for c in checked] == [Decimal("200"), Decimal("100")]


# ------------------------------------------------------------------ payload and dates

def _extraction(**overrides) -> dict:
    base = {
        "sha256": SHA, "is_origenlab_issued_quote": True, "quote_number": "00001-26", "date": "2026-01-02",
        "city": "Ciudad Sintética", "client_contact": "Persona Inventada", "client_institution": "Instituto Ficticio",
        "client_unit": "Unidad Imaginaria", "client_type": "university", "currency": "CLP",
        "prices_include_iva": False, "lines": [], "subtotal_net": 1234000, "iva_amount": 234460, "total": 1468460,
        "conditions": {"delivery": "pronto"}, "template": "docx_web2026",
        "sum_check": {"lines_sum": 1234000, "stated_net": 1234000, "ok": True}, "extraction_issues": [],
    }
    return {**base, **overrides}


def test_payload_has_exactly_the_contract_keys_and_no_client_identity() -> None:
    payload = quote_document_payload(_extraction(), SHA)
    assert payload == {"printed_quote_number": "00001-26", "quote_date": "2026-01-02", "client_type": "university",
                       "currency": "CLP", "net_total": "1234000", "template": "docx_web2026", "file_sha256": SHA}
    assert "client_institution" not in payload and "client_contact" not in payload
    text = repr(payload)
    for secret in ("Persona Inventada", "Instituto Ficticio", "Unidad Imaginaria", "Ciudad Sintética", "pronto"):
        assert secret not in text


@pytest.mark.parametrize("raw, iso", [("2026-01-02", "2026-01-02"), ("02-01-2026", "2026-01-02"),
                                      ("02/01/2026", "2026-01-02"), ("2/1/2026", "2026-01-02"), (None, None)])
def test_quote_date_is_normalised_to_iso(raw, iso) -> None:
    assert normalise_quote_date(raw) == iso
    assert quote_document_payload(_extraction(date=raw), SHA)["quote_date"] == iso


@pytest.mark.parametrize("raw", ["2 de enero de 2026", "2026/01/02", "31-02-2026", "01-2026", "20260102", 20260102])
def test_other_date_formats_are_refused(raw) -> None:
    with pytest.raises(ValueError, match="date"):
        normalise_quote_date(raw)
    with pytest.raises(ValueError, match="date"):
        quote_document_payload(_extraction(date=raw), SHA)


def test_payload_refuses_an_unknown_currency_or_a_foreign_sha() -> None:
    with pytest.raises(ValueError, match="currency"):
        quote_document_payload(_extraction(currency="GBP"), SHA)
    with pytest.raises(ValueError, match="sha256"):
        quote_document_payload(_extraction(), "b" * 64)


def test_description_text_starting_in_the_ref_column_is_not_a_model() -> None:
    text = ("  ITEM   REF    DETALLE      TOTAL\n\n"
            "   1      Equipo sintético con una descripción larga, que empieza en REF     $ 100.000\n\n"
            "   2      SONIC-200 Equipo sintético con una descripción larga y su código     $ 200.000\n")
    assert parse_template_lines(text) == [TemplateLine("1", None, Decimal("100000")),
                                          TemplateLine("2", "SONIC-200", Decimal("200000"))]
