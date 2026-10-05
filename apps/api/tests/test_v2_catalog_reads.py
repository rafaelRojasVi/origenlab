"""Catalog reads against PostgreSQL as the real `origenlab_api` role. Every name is invented."""
from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal

import psycopg
import pytest

from origenlab_api.v2.catalog.reads import V2CatalogReads
from v2_command_harness import build_disposable_database, needs_db, runtime_dsn


@pytest.fixture(scope="module")
def disposable_database():
    yield from build_disposable_database()


def _owner(dsn: str, sql: str, params: tuple = ()):
    with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("set role origenlab_owner")
        cur.execute(sql, params)
        return cur.fetchall() if cur.description else None


def _org(dsn: str, kind: str = "manufacturer") -> uuid.UUID:
    return _owner(dsn, "insert into crm.organization (kind, name, confirmation) values (%s, %s, 'machine_proposed') "
                       "returning id", (kind, f"Fabricante Ficticio {uuid.uuid4().hex[:6]}"))[0][0]


def _operator(dsn: str) -> uuid.UUID:
    return _owner(dsn, "insert into platform.operator (auth_user_id, email_norm, display_name, role, status) "
                       "values (gen_random_uuid(), %s, 'Operadora Prueba', 'sales', 'active') returning id",
                  (f"op-{uuid.uuid4().hex[:8]}@example.test",))[0][0]


def _seed_product(dsn: str, *, model: str, name: str, name_es: str, manufacturer=None, kind="equipment"):
    org = manufacturer or _org(dsn)
    prod = _owner(dsn, "insert into catalog.product (manufacturer_organization_id, model_number, name, name_es, "
                       "product_kind, category_es) values (%s, %s, %s, %s, %s, 'Laboratorio') returning id",
                  (org, model, name, name_es, kind))[0][0]
    return org, prod


def _obs(dsn, prod, supplier, *, as_of, price, currency="EUR", kind="dealer_net", stale=False):
    _owner(dsn, "insert into catalog.supplier_product (supplier_organization_id, product_id, as_of, price, currency, "
                "price_kind, is_stale, provenance_note) values (%s, %s, %s, %s, %s, %s, %s, 'test')",
           (supplier, prod, as_of, price, currency, kind, stale))


def _reads(dsn: str) -> V2CatalogReads:
    return V2CatalogReads(psycopg.connect, runtime_dsn(dsn))


@needs_db
def test_search_by_code_and_spanish_text(disposable_database):
    dsn = disposable_database
    _, prod = _seed_product(dsn, model="SONIC-100", name="Ultrasonic processor", name_es="Sonicador ultrasónico")
    reads = _reads(dsn)
    assert [i["id"] for i in reads.search_products("sonic100", None, None, 20, 0)["items"]] == [str(prod)]
    assert [i["id"] for i in reads.search_products("sonicador", None, None, 20, 0)["items"]] == [str(prod)]
    assert reads.search_products("sonic100", None, "consumable", 20, 0)["items"] == []
    assert reads.search_products("zzz-no-such", None, None, 20, 0)["total"] == 0


@needs_db
def test_search_filters_by_supplier_and_pages(disposable_database):
    dsn = disposable_database
    supplier = _org(dsn, "supplier")
    mfr = _org(dsn)
    _, a = _seed_product(dsn, model="PGA-1", name="a", name_es="Pagina uno", manufacturer=mfr)
    _, b = _seed_product(dsn, model="PGB-2", name="b", name_es="Pagina dos", manufacturer=mfr)
    _obs(dsn, a, supplier, as_of="2026-01-01T00:00Z", price="10")
    reads = _reads(dsn)
    assert [i["id"] for i in reads.search_products(None, supplier, None, 20, 0)["items"]] == [str(a)]
    page = reads.search_products("pagina", None, None, 1, 1)
    assert page["total"] == 2 and [i["id"] for i in page["items"]] == [str(b)]


@needs_db
def test_current_cost_prefers_newest_non_stale(disposable_database):
    dsn = disposable_database
    supplier = _org(dsn, "supplier")
    _, prod = _seed_product(dsn, model="COST-1", name="c", name_es="Costo uno")
    _obs(dsn, prod, supplier, as_of="2026-01-01T00:00Z", price="100", kind="dealer_net")
    _obs(dsn, prod, supplier, as_of="2026-03-01T00:00Z", price="80", kind="list", stale=True)
    [item] = _reads(dsn).search_products("COST-1", None, None, 20, 0)["items"]
    assert item["current_cost"]["price"] == "100.000000"
    assert item["current_cost"]["is_stale"] is False and item["current_cost"]["supplier"]["id"] == str(supplier)


@needs_db
def test_detail_includes_history_terms_images_notes(disposable_database):
    dsn = disposable_database
    supplier = _org(dsn, "supplier")
    op = _operator(dsn)
    _, prod = _seed_product(dsn, model="DET-1", name="d", name_es="Detalle uno")
    _obs(dsn, prod, supplier, as_of="2026-01-01T00:00Z", price="100")
    _obs(dsn, prod, supplier, as_of="2026-02-01T00:00Z", price="110")
    _owner(dsn, "insert into catalog.supplier_terms (supplier_organization_id, currency, route, default_discount_pct) "
                "values (%s, 'EUR', 'domestic', 0.25)", (supplier,))
    for n, status in enumerate(("confirmed", "hidden")):
        _owner(dsn, "insert into catalog.product_image (product_id, storage_path, sha256, content_type, source, status) "
                    "values (%s, %s, %s, 'image/png', 'upload', %s)",
               (prod, f"img/{uuid.uuid4().hex}.png", f"{n:064x}", status))
    _owner(dsn, "insert into crm.note (subject_kind, subject_id, body, author_operator_id) "
                "values ('product', %s, 'nota de prueba', %s)", (prod, op))
    detail = _reads(dsn).product_detail(prod)
    assert (len(detail["cost_history"]), len(detail["supplier_terms"]), len(detail["images"]), len(detail["notes"])) == (2, 1, 1, 1)
    assert detail["cost_history"][0]["price"] == "110.000000"  # newest first
    assert detail["supplier_terms"][0]["default_discount_pct"] == "0.250000"
    assert detail["version"] == 1 and "search_tsv" not in detail
    assert _reads(dsn).product_detail(uuid.uuid4()) is None


@needs_db
def test_supplier_terms_read(disposable_database):
    dsn = disposable_database
    supplier = _org(dsn, "supplier")
    _owner(dsn, "insert into catalog.supplier_terms (supplier_organization_id, currency, route) "
                "values (%s, 'USD', 'import_courier')", (supplier,))
    assert _reads(dsn).supplier_terms(supplier)["route"] == "import_courier"
    assert _reads(dsn).supplier_terms(uuid.uuid4()) is None


@needs_db
def test_parameters_current_and_history(disposable_database):
    dsn = disposable_database
    reads = _reads(dsn)
    seeded = reads.parameters()["current"]
    assert seeded["iva_rate"]["value"] == "0.190000" and seeded["iva_rate"]["set_by"] is None
    # Synthetic rows for other keys; never real commercial values.
    _owner(dsn, "insert into catalog.cost_parameter (key, value_numeric, valid_from, reason) values "
                "('default_markup_service', 0.111, '2026-02-01 00:00+00', 'synthetic old'), "
                "('default_markup_service', 0.222, '2026-03-01 00:00+00', 'synthetic new'), "
                "('default_markup_accessory', 0.333, '2999-01-01 00:00+00', 'synthetic future')")
    out = reads.parameters()
    assert out["current"]["default_markup_service"]["value"] == "0.222000"
    assert out["current"]["default_markup_service"]["reason"] == "synthetic new"
    assert "default_markup_accessory" not in out["current"]  # not yet valid
    hist = [h["value"] for h in out["history"] if h["key"] == "default_markup_service"]
    assert hist == ["0.222000", "0.111000"]
    assert out["history"][0]["key"] == "default_markup_accessory"  # newest valid_from first


@needs_db
def test_stored_fx_latest_on_or_before_prefers_newest_manual(disposable_database):
    dsn = disposable_database
    op = _operator(dsn)
    ins = ("insert into catalog.fx_rate (rate_date, currency, clp_per_unit, source, provider, reason, "
           "recorded_by_operator_id, created_at) values (%s, 'EUR', %s, %s, %s, %s, %s, %s)")
    _owner(dsn, ins, ("2026-04-01", "1000", "bcentral", "mindicador", None, None, "2026-04-01T08:00Z"))
    _owner(dsn, ins, ("2026-04-01", "1001", "bcentral", "bde", None, None, "2026-04-01T09:00Z"))
    reads = _reads(dsn)
    assert reads.stored_fx("EUR", dt.date(2026, 4, 5))["clp_per_unit"] == "1001.000000"  # bde over mindicador
    _owner(dsn, ins, ("2026-04-01", "1100", "manual", "operator", "typo", op, "2026-04-01T10:00Z"))
    _owner(dsn, ins, ("2026-04-01", "1010", "manual", "operator", "corregido", op, "2026-04-01T11:00Z"))
    got = reads.stored_fx("EUR", dt.date(2026, 4, 5))
    assert got["clp_per_unit"] == "1010.000000" and got["source"] == "manual" and got["rate_date"] == "2026-04-01"
    assert reads.stored_fx("EUR", dt.date(2026, 3, 31)) is None
    assert reads.stored_fx("USD", dt.date(2026, 4, 5)) is None


@needs_db
def test_total_is_correct_when_offset_is_past_the_end(disposable_database):
    dsn = disposable_database
    mfr = _org(dsn)
    for n in range(3):
        _seed_product(dsn, model=f"OFF-{n}", name="o", name_es="Fuera de rango", manufacturer=mfr)
    out = _reads(dsn).search_products("fuera de rango", None, None, 2, 5)
    assert out["items"] == [] and out["total"] == 3


# ------------------------------------------------------------------ price history and kits

def _quote(dsn: str, quote_date: str, number: str, client_type: str = "universidad") -> uuid.UUID:
    return _owner(dsn, "insert into evidence.source_record (kind, dedupe_key, payload) values ('quote_document', %s, "
                       "jsonb_build_object('quote_date', %s::text, 'printed_quote_number', %s::text, "
                       "'client_type', %s::text)) returning id",
                  (f"quote_document:{uuid.uuid4().hex}", quote_date, number, client_type))[0][0]


def _qline(dsn, source, line_no, model, total, *, qty="1", status="verified", optional=False, brand="ACME",
           currency="CLP"):
    _owner(dsn, "insert into evidence.document_line (source_record_id, line_no, brand, model, model_key, qty, "
                "unit_price, line_total, currency, optional, extractor, check_status) values "
                "(%s, %s, %s, %s, upper(regexp_replace(%s::text, '[\\s\\-_./]', '', 'g')), %s, %s, %s, %s, %s, 'test', %s)",
           (source, line_no, brand, model, model, qty, total, total, currency, optional, status))


def _history_fixture(dsn: str, sonic: str = "SONIC-100", probe: str = "PROBE-2"):
    """`sonic` quoted at 1000/1100/1200 on three dates; `probe` beside it in two of the three."""
    q1, q2, q3 = (_quote(dsn, d, n) for d, n in (("2026-01-10", "T-A"), ("2026-02-10", "T-B"), ("2026-03-10", "T-C")))
    _qline(dsn, q1, 1, sonic, "1000.0000")
    _qline(dsn, q1, 2, probe, "50.0000", brand="PROBA")
    _qline(dsn, q2, 1, sonic, "1100.0000")
    _qline(dsn, q2, 2, probe, "50.0000", brand="PROBA")
    _qline(dsn, q2, 3, probe, "50.0000", brand="PROBA")  # twice in one document: counts once
    _qline(dsn, q3, 1, sonic, "1200.0000")
    _qline(dsn, q3, 2, None, "10.0000")  # no model: never a suggestion
    return q1, q2, q3


@needs_db
def test_price_history_order_median_and_exclusions(disposable_database):
    dsn = disposable_database
    q1, q2, q3 = _history_fixture(dsn, "ORDER-100", "ORDPROBE-2")
    _qline(dsn, q3, 5, "ORDER-100", "9999.0000", optional=True)  # optional: not listed
    _qline(dsn, q3, 6, "ORDER-100", "5000.0000", status="disputed")  # listed, never in the median
    _qline(dsn, q1, 7, "ORDER-100", "4000.0000", qty="4")  # not a single unit: listed, not in the median
    history = _reads(dsn).price_history("ORDER100")
    assert history["model_key"] == "ORDER100"
    assert history["median_last_5"] == "1100.0000"
    assert [(i["document_number"], i["line_total"], i["check_status"]) for i in history["items"]] == [
        ("T-C", "5000.0000", "disputed"), ("T-C", "1200.0000", "verified"),
        ("T-B", "1100.0000", "verified"), ("T-A", "4000.0000", "verified"), ("T-A", "1000.0000", "verified")]
    first = history["items"][0]
    assert first["date"] == "2026-03-10" and first["source"] == "documento" and first["currency"] == "CLP"
    assert first["client_type"] == "universidad" and first["qty"] == "1.000000" and first["unit_price"] == "5000.0000"
    assert history["items"][3]["qty"] == "4.000000"
    assert "9999.0000" not in {i["line_total"] for i in history["items"]}


@needs_db
def test_price_history_limit_and_median_use_the_last_five(disposable_database):
    dsn = disposable_database
    for n in range(1, 8):
        _qline(dsn, _quote(dsn, f"2026-04-{n:02d}", f"L-{n}"), 1, "LIMIT-1", f"{n * 100}.0000")
    history = _reads(dsn).price_history("limit 1", limit=2)
    assert [i["line_total"] for i in history["items"]] == ["700.0000", "600.0000"]
    assert history["median_last_5"] == "500.0000"  # 300..700, not all seven
    assert _reads(dsn).price_history("NO-SUCH-MODEL") == {"model_key": "NOSUCHMODEL", "items": [], "median_last_5": None}


@needs_db
def test_price_history_median_ignores_disputed_and_other_currencies(disposable_database):
    dsn = disposable_database
    _qline(dsn, _quote(dsn, "2026-05-01", "M-1"), 1, "MEDIAN-1", "10.0000", currency="USD")
    _qline(dsn, _quote(dsn, "2026-05-02", "M-2"), 1, "MEDIAN-1", "200.0000")
    _qline(dsn, _quote(dsn, "2026-05-03", "M-3"), 1, "MEDIAN-1", "400.0000", status="single_source")
    _qline(dsn, _quote(dsn, "2026-05-04", "M-4"), 1, "MEDIAN-1", "999999.0000", status="disputed")
    history = _reads(dsn).price_history("MEDIAN-1")
    assert len(history["items"]) == 4 and history["median_last_5"] == "300.0000"  # mean of 200 and 400, CLP only


@needs_db
def test_kit_suggestions_count_documents_not_lines(disposable_database):
    dsn = disposable_database
    _history_fixture(dsn, "KITSON-100", "KITPROBE-2")
    [kit] = _reads(dsn).kit_suggestions("kitson-100")
    assert kit == {"model_key": "KITPROBE2", "brand": "PROBA", "share": "0.6667"}
    assert _reads(dsn).kit_suggestions("KITSON-100", min_share=Decimal("0.70")) == []
    assert _reads(dsn).kit_suggestions("UNQUOTED-9") == []


@needs_db
def test_detail_carries_history_and_kits(disposable_database):
    dsn = disposable_database
    _history_fixture(dsn, "DETSON-100", "DETPROBE-2")
    _, prod = _seed_product(dsn, model="DETSON-100", name="Ultrasonic processor", name_es="Sonicador ultrasónico")
    detail = _reads(dsn).product_detail(prod)
    assert detail["price_history"]["median_last_5"] == "1100.0000"
    assert detail["kit_suggestions"][0]["model_key"] == "DETPROBE2"
