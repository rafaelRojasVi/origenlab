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
# The real footer is pinned by its hash only; each test pins this invented one the same way.

ADDRESS = "Calle Inventada 123, Of. 45, Comuna Ficticia, Santiago, Chile. Teléfono (56-2) 21234567"
CELULAR = "Celular: (09) 81234567 www.labdelivery.cl"
FOOTER = f"      {ADDRESS}\n      {CELULAR}\n"
HEADER = ("                                                  Persona Ficticia - OrigenLab\n"
          "OrigenLab                                            contacto@origenlab.cl\n"
          "Equipos para laboratorio\n\n      COTIZACIÓN N°00000-26\n")
BODY = "   1     ACME-1     Equipo sintético de prueba            $ 1.000\n"


@pytest.fixture
def pinned(monkeypatch):
    monkeypatch.setattr(keys, "OLD_TEMPLATE_FOOTER_SHA256",
                        keys.OLD_TEMPLATE_FOOTER_SHA256 | {keys.footer_sha256(ADDRESS, CELULAR)})


def test_the_real_footer_is_pinned_by_one_hash_and_no_plaintext():
    assert len(keys.OLD_TEMPLATE_FOOTER_SHA256) == 1
    assert all(re.fullmatch(r"[0-9a-f]{64}", h) for h in keys.OLD_TEMPLATE_FOOTER_SHA256)
    assert keys.footer_sha256(ADDRESS, CELULAR) not in keys.OLD_TEMPLATE_FOOTER_SHA256


def test_a_page_whose_only_labdelivery_mention_is_the_pinned_footer_is_not_labdelivery_issued(pinned):
    page = HEADER + BODY + "\n\n" + FOOTER
    assert is_labdelivery(page)  # the string check still sees it
    assert not labdelivery_issued(page)
    assert not labdelivery_issued(page + "\f" + BODY + FOOTER)  # one footer per page
    assert not labdelivery_issued(HEADER + BODY + f"{ADDRESS} {CELULAR}\n")  # both on one line
    assert not labdelivery_issued(page.replace("\n", "\r\n"))
    assert not labdelivery_issued(page.replace("é", "e\u0301"))  # decomposed accents
    assert not labdelivery_issued(page.replace("Teléfono", "Telefono"))  # the same folded text
    assert not labdelivery_issued(None) and not labdelivery_issued("")


def test_the_footer_shape_with_another_address_is_not_exempt():
    """Without its pin the invented footer is Labdelivery's: only the one real footer is exempt."""
    assert labdelivery_issued(HEADER + BODY + FOOTER)


@pytest.mark.parametrize("page", [
    "LABDELIVERY – PERSONA FICTICIA\nGiro: Equipos para laboratorio\n" + BODY + FOOTER,  # letterhead + footer
    "L A B D E L I V E R Y\n" + HEADER + BODY + FOOTER,  # a letter-spaced letterhead
    "L.A.B.D.E.L.I.V.E.R.Y\n" + HEADER + BODY + FOOTER,
    "\uff2c\uff21\uff22\uff24\uff25\uff2c\uff29\uff36\uff25\uff32\uff39\n" + HEADER + BODY + FOOTER,  # fullwidth
    "Lab\u200bdelivery\n" + HEADER + BODY + FOOTER,  # zero-width space
    HEADER.replace("OrigenLab  ", "Labdelivery") + BODY + FOOTER,  # issuer
    HEADER + "COTIZACIÓN LAB DELIVERY\n" + BODY + FOOTER,  # title
    HEADER + BODY + FOOTER + "Saluda atentamente,\nPersona Ficticia\nLabdelivery\n",  # signature
    HEADER + BODY + FOOTER + "Tejeda Arellano\n",  # the person
    HEADER + "ventas@labdelivery.cl\n" + BODY + FOOTER,  # an address
    HEADER + BODY.replace("ACME-1", "Labdelivery") + FOOTER,  # a line
    BODY + FOOTER,  # the footer, and OrigenLab named nowhere else
    "LD\n" + BODY + FOOTER + FOOTER,
    HEADER + BODY + f"      {CELULAR}\n",  # the web address without the footer's address
    HEADER + BODY + FOOTER.replace("Calle Inventada", "Labdelivery Calle"),  # the address names it
    HEADER + BODY + FOOTER.replace("Celular", "Fono"),  # another wording
    HEADER + BODY + FOOTER.replace("www.labdelivery.cl", "www.labdelivery.cl LABDELIVERY"),  # more on the line
    HEADER + BODY + FOOTER.replace("www.labdelivery.cl", "visite www.labdelivery.cl"),
    HEADER + BODY + FOOTER.replace("www.labdelivery.cl", "https://www.labdelivery.cl"),
    HEADER + BODY + FOOTER.replace("labdelivery.cl", "lab-delivery.cl"),  # a variant is not the footer
    HEADER + BODY + FOOTER.replace("labdelivery.cl", "labdelivery.com"),
    HEADER + BODY + FOOTER.replace("\n      Celular", "\n\n      Celular"),  # not the next line
    HEADER + BODY + f"      {ADDRESS}\f{HEADER}      {CELULAR}\n",  # split across pages
    HEADER + BODY + FOOTER.replace("Calle Inventada", "Cotización de Persona Ficticia, Calle Inventada"),
], ids=["letterhead", "spaced", "dotted", "fullwidth", "zero_width", "issuer", "title", "signature", "person", "email",
        "line", "no_origenlab", "no_origenlab_twice", "url_alone", "address_names_it", "other_wording",
        "more_on_the_line", "words_before_url", "https", "variant", "other_domain", "not_the_next_line", "split",
        "text_before_address"])
def test_labdelivery_anywhere_but_the_pinned_footer_under_origenlab_is_labdelivery_issued(pinned, page):
    assert labdelivery_issued(page)


@pytest.mark.parametrize("text", ["L A B D E L I V E R Y", "l-a-b-d-e-l-i-v-e-r-y", "L. A. B. Delivery", "LAB DELI VERY",
                                  "T E J E D A  A R E L L A N O"])
def test_letter_spaced_spellings_are_labdelivery(text):
    assert is_labdelivery(text)


@pytest.mark.parametrize("text", ["la b de liveries", "Collab delivery", "a lab delivers it"])
def test_letter_spacing_adds_no_false_positive(text):
    assert not is_labdelivery(text)


def test_the_footer_identities_are_its_address_street_phone_label_and_long_numbers(pinned):
    identities = keys.old_footer_identities(HEADER + BODY + FOOTER)
    assert {"21234567", "81234567"} <= identities
    assert "calle inventada 123 of 45 comuna ficticia" in identities
    assert not any(i in {"56", "2", "09", "123", "45"} for i in identities)  # short numbers never count
    assert keys.old_footer_identities(HEADER + BODY) == frozenset()
    assert keys.old_footer_identities(HEADER + BODY + FOOTER.replace("Calle", "Avenida")) == frozenset()  # unpinned


@pytest.mark.parametrize("value", [
    ADDRESS, "Calle Inventada 123, Of. 45, Comuna Ficticia", "(09) 81234567", "Celular: (09) 81234567",
    "(56-2) 21234567", "+56 2 2123 4567", "2123-4567", "Equipo — llamar al 8123 4567",
])
def test_a_string_that_copies_the_footer_is_caught(pinned, value):
    assert keys.copies_old_footer(value, keys.old_footer_identities(HEADER + BODY + FOOTER))


@pytest.mark.parametrize("value", ["Equipo sintético de prueba", "ACME-1", "00000-26", "Comuna", "1234567", None, ""])
def test_an_ordinary_string_is_not_a_footer_copy(pinned, value):
    assert not keys.copies_old_footer(value, keys.old_footer_identities(HEADER + BODY + FOOTER))
    assert not keys.copies_old_footer(ADDRESS, frozenset())


def test_the_footer_search_stays_linear_on_hostile_text(pinned):
    import time

    for hostile in ("x" * 100_000, "a," * 50_000, ", Santiago, Chile. Teléfono (56-2) 1234567 " * 2_000):
        started = time.perf_counter()
        labdelivery_issued(hostile + "\n" + CELULAR)
        keys.old_footer_identities(hostile)
        assert time.perf_counter() - started < 2
