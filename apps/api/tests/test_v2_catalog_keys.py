import re
from pathlib import Path

import pytest

from origenlab_api.v2.catalog import keys
from origenlab_api.v2.catalog.keys import (
    LabdeliveryRefused,
    is_labdelivery,
    labdelivery_issued,
    model_key,
    refuse_labdelivery,
)

MIGRATIONS = Path(__file__).resolve().parents[3] / "supabase" / "migrations"


def _sql(pattern: str) -> str:
    found = sorted(MIGRATIONS.glob(pattern))
    assert len(found) == 1, found
    return found[0].read_text()


def _check_list(sql: str, constraint: str, column: str) -> tuple[str, ...]:
    m = re.search(
        rf"{constraint}\s+check\s*\(\s*(?:{column}\s+is\s+null\s+or\s+)?{column}\s+in\s*\((.*?)\)\s*\)",
        sql,
        re.S,
    )
    assert m, constraint
    return tuple(re.findall(r"'([^']+)'", m.group(1)))


PRODUCTS = "*slice7_catalog_products_suppliers*.sql"
# The parameter key CHECK was last rebuilt by the Cotizador migration (quote numbering and text defaults).
PRICING = "*slice7_quote_authoring*.sql"


@pytest.mark.parametrize(
    ("attr", "pattern", "constraint", "column"),
    [
        ("PRODUCT_KINDS", PRODUCTS, "product_kind_check", "product_kind"),
        ("CONTENT_ORIGINS", PRODUCTS, "product_content_origin_check", "content_origin"),
        ("IMAGE_SOURCES", PRODUCTS, "product_image_source_check", "source"),
        ("IMAGE_STATUSES", PRODUCTS, "product_image_status_check", "status"),
        ("PRICE_KINDS", PRODUCTS, "supplier_product_price_kind_check", "price_kind"),
        ("ROUTES", PRODUCTS, "supplier_terms_route_check", "route"),
        ("PARAMETER_KEYS", PRICING, "cost_parameter_key_check", "key"),
    ],
)
def test_vocabulary_matches_sql(attr, pattern, constraint, column):
    sql_values = _check_list(_sql(pattern), constraint, column)
    assert sql_values, attr
    assert getattr(keys, attr) == sql_values


@pytest.mark.parametrize("raw", ["UP-200 St", "up200st", "UP200ST", "Up.200/St", "up_200 st", "up\t200-st"])
def test_model_key_normalises(raw):
    assert model_key(raw) == "UP200ST"


def test_model_key_none_and_blank():
    assert model_key(None) is None and model_key("  ") is None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("a b\tc-d_e.f/g", "ABCDEFG"),
        ("Ømega-µ 1", "ØMEGA" + "µ".upper() + "1"),
        ("mIxEd 12a", "MIXED12A"),
        ("ß-1", "ß1"),
    ],
)
def test_model_key_tricky(raw, expected):
    assert model_key(raw) == expected


def test_model_key_sql_normalisation_is_the_documented_one():
    sql = _sql(PRODUCTS)
    assert r"upper(regexp_replace(model_number, '[\s\-_./]', '', 'g'))" in sql


@pytest.mark.parametrize("text", ["LABDELIVERY", "Lab-Delivery", "lab delivery", "/x/Price_list_LabDelivery.xlsx",
                                  "www.labdelivery.cl", "Labdelivery – Juan Andrés Tejeda Arellano"])
def test_labdelivery_variants_refused(text):
    assert is_labdelivery(text)
    with pytest.raises(LabdeliveryRefused):
        refuse_labdelivery("ok", text)


def test_ordinary_text_passes():
    assert not is_labdelivery("ACME synthetic price list 2026")
    assert not is_labdelivery(None)
    refuse_labdelivery("ACME", None, "Valdivia")


@pytest.mark.parametrize("text", [
    "Lab/Delivery", "lab\u2013delivery", "LAB\u2014DELIVERY", "Lab_Delivery", "LAB.DELIVERY",
    "lab+delivery", "lab\u00addelivery", "lab\u200bdelivery", "Lab\nDelivery", "labdelivery.cl",
    "https://www.labdelivery.cl/x", "Juan Andre\u0301s Tejeda", "Tejeda Arellano",
])
def test_labdelivery_more_variants(text):
    assert is_labdelivery(text)


@pytest.mark.parametrize("text", [
    "Collab Delivery", "Elab delivery", "laboratory delivery", "ACME lab deliveries schedule", "Valdivia",
])
def test_labdelivery_no_false_positive_in_words(text):
    assert not is_labdelivery(text)


# ── labdelivery_issued: the old OrigenLab template's footer is not a Labdelivery document ─────
# Invented address and numbers, in the footer's real shape (two lines, as `pdftotext -layout` prints).

FOOTER = ("      Calle Inventada 123, Of. 45, Comuna Ficticia, Santiago, Chile. Teléfono (56-2) 21234567\n"
          "      Celular: (09) 81234567 www.labdelivery.cl\n")
HEADER = ("                                                  Persona Ficticia - OrigenLab\n"
          "OrigenLab                                            contacto@origenlab.cl\n"
          "Equipos para laboratorio\n\n      COTIZACIÓN N°00000-26\n")
BODY = "   1     ACME-1     Equipo sintético de prueba            $ 1.000\n"


def test_a_page_whose_only_labdelivery_mention_is_the_old_footer_is_not_labdelivery_issued():
    page = HEADER + BODY + "\n\n" + FOOTER
    assert is_labdelivery(page)  # the string check still sees it
    assert not labdelivery_issued(page)
    assert not labdelivery_issued(page + "\f" + BODY + FOOTER)  # one footer per page
    assert not labdelivery_issued(FOOTER.replace("\n      Celular", " Celular"))  # both on one line
    assert not labdelivery_issued(FOOTER.replace("\n", "\r\n"))
    assert not labdelivery_issued(None) and not labdelivery_issued("")


@pytest.mark.parametrize("page", [
    "LABDELIVERY – PERSONA FICTICIA\nGiro: Equipos para laboratorio\n" + BODY + FOOTER,  # letterhead + footer
    HEADER.replace("OrigenLab  ", "Labdelivery") + BODY + FOOTER,  # issuer
    HEADER + "COTIZACIÓN LAB DELIVERY\n" + BODY + FOOTER,  # title
    HEADER + BODY + FOOTER + "Saluda atentamente,\nPersona Ficticia\nLabdelivery\n",  # signature
    HEADER + BODY.replace("ACME-1", "Labdelivery") + FOOTER,  # a line
    HEADER + BODY + "Celular: (09) 81234567 www.labdelivery.cl\n",  # the web address without the address line
    HEADER + BODY + FOOTER.replace("Calle Inventada", "Labdelivery Calle"),  # the address names it
    HEADER + BODY + FOOTER.replace("Celular", "Fono"),  # another wording
    HEADER + BODY + FOOTER.replace("www.labdelivery.cl", "www.labdelivery.cl LABDELIVERY"),  # more on the line
    HEADER + BODY + FOOTER.replace("labdelivery.cl", "lab-delivery.cl"),  # a variant is not the footer
    HEADER + BODY + FOOTER.replace("\n      Celular", "\n\n      Celular"),  # not the next line
], ids=["letterhead", "issuer", "title", "signature", "line", "url_alone", "address_names_it", "other_wording",
        "more_on_the_line", "variant", "not_the_next_line"])
def test_labdelivery_anywhere_but_the_exact_footer_is_labdelivery_issued(page):
    assert labdelivery_issued(page)
