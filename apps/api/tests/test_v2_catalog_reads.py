"""Catalog reads against PostgreSQL as the real `origenlab_api` role. Every name is invented."""
from __future__ import annotations

import datetime as dt
import json
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
def test_suppliers_lists_every_supplier_with_products_by_kind(disposable_database):
    dsn = disposable_database
    supplier, other = _org(dsn, "supplier"), _org(dsn, "supplier")
    _, eq = _seed_product(dsn, model="SUP-EQ", name="e", name_es="Equipo", kind="equipment")
    _, c1 = _seed_product(dsn, model="SUP-C1", name="c", name_es="Insumo uno", kind="consumable")
    _, c2 = _seed_product(dsn, model="SUP-C2", name="c", name_es="Insumo dos", kind=None)
    for prod in (eq, c1, c2):
        _obs(dsn, prod, supplier, as_of="2026-01-01T00:00Z", price="10")
    _obs(dsn, eq, supplier, as_of="2026-02-01T00:00Z", price="11")  # a second observation counts once
    _obs(dsn, c1, other, as_of="2026-01-01T00:00Z", price="9")
    items = {i["id"]: i for i in _reads(dsn).suppliers()["items"]}
    assert items[str(supplier)]["products"] == 3
    assert items[str(supplier)]["by_kind"] == {"consumable": 1, "equipment": 1, "unclassified": 1}
    assert items[str(other)]["by_kind"] == {"consumable": 1}


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
                "('default_markup_accessory', 0.3071, '2999-01-01 00:00+00', 'synthetic future')")
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
    empty = _reads(dsn).price_history("NO-SUCH-MODEL")
    assert {k: empty[k] for k in ("model_key", "items", "median_last_5")} == {
        "model_key": "NOSUCHMODEL", "items": [], "median_last_5": None}
    assert empty["summary"]["times_quoted"] == 0 and empty["summary"]["last"] is None


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


# -- model aliases (DOMAIN.md §7 #59): a line printed «XQ100» is about the product stored as 30000001

def _alias(dsn, prod, alias: str, operator=None, *, retired: bool = False) -> None:
    op = operator or _operator(dsn)
    _owner(dsn, "insert into catalog.product_model_alias (product_id, alias, source, created_by_operator_id) "
                "values (%s, %s, 'operator', %s)", (prod, alias, op))
    if retired:
        _owner(dsn, "update catalog.product_model_alias set retired_at = now(), retired_by_operator_id = %s "
                    "where product_id = %s and alias = %s", (op, prod, alias))


@needs_db
def test_an_aliased_line_joins_the_products_price_history_kits_and_costs(disposable_database):
    dsn = disposable_database
    supplier = _org(dsn, "supplier")
    _, prod = _seed_product(dsn, model="30000101", name="Balance", name_es="Balanza XQ101")
    _obs(dsn, prod, supplier, as_of="2026-01-01T00:00Z", price="500")
    _alias(dsn, prod, "XQ 101")
    _, other = _seed_product(dsn, model="50000101", name="o", name_es="Otra balanza")
    _alias(dsn, other, "ZQ-909")  # another product's alias names nothing here
    _history_fixture(dsn, "XQ-101", "XQPROBE-1")  # every line prints the model, none the item number
    _qline(dsn, _quote(dsn, "2026-04-10", "T-D"), 1, "30000101", "1300.0000")  # one prints the item number
    reads = _reads(dsn)
    detail = reads.product_detail(prod)
    assert [i["line_total"] for i in detail["price_history"]["items"]] == [
        "1300.0000", "1200.0000", "1100.0000", "1000.0000"]
    assert [c["price"] for c in detail["price_history"]["costs"]] == ["500.000000"]
    assert detail["kit_suggestions"][0]["model_key"] == "XQPROBE1"
    # By key: the alias and the item number read the same history.
    for key in ("XQ101", "30000101"):
        assert len(reads.price_history(key)["items"]) == 4, key
        assert reads.kit_suggestions(key)[0]["model_key"] == "XQPROBE1", key
    # Search finds the product by what is printed.
    assert [i["id"] for i in reads.search_products("XQ-101", None, None, 20, 0)["items"]] == [str(prod)]


@needs_db
def test_an_ambiguous_or_retired_alias_links_nothing(disposable_database):
    dsn = disposable_database
    _, a = _seed_product(dsn, model="30000202", name="a", name_es="Balanza A")
    _, b = _seed_product(dsn, model="40000202", name="b", name_es="Balanza B")  # another maker
    _alias(dsn, a, "XQ202")
    _alias(dsn, b, "XQ202")  # two makers print the same code
    _, c = _seed_product(dsn, model="30000303", name="c", name_es="Balanza C")
    _alias(dsn, c, "XQ303", retired=True)
    _qline(dsn, _quote(dsn, "2026-05-10", "T-E"), 1, "XQ202", "100.0000")
    _qline(dsn, _quote(dsn, "2026-05-11", "T-F"), 1, "XQ303", "200.0000")
    reads = _reads(dsn)
    assert reads.product_detail(a)["price_history"]["items"] == []
    assert reads.product_detail(b)["price_history"]["items"] == []
    assert reads.product_detail(c)["price_history"]["items"] == []
    # The key still reads its own lines, as any unknown model does — but no product's costs.
    ambiguous = reads.price_history("XQ202")
    assert len(ambiguous["items"]) == 1 and ambiguous["costs"] == []
    assert reads.price_history("30000202")["items"] == []


# -- Catálogo v2: the search index, documents in Drive, batch image paths

def _index_row(out: dict, product_id) -> dict:
    return next(dict(zip(out["fields"], r, strict=True)) for r in out["items"] if r[0] == str(product_id))


@needs_db
def test_index_lists_active_products_without_costs(disposable_database):
    dsn = disposable_database
    supplier = _org(dsn, "supplier")
    _, prod = _seed_product(dsn, model="IDX-200 St", name="Index probe", name_es="Sonda índice")
    _obs(dsn, prod, supplier, as_of=dt.date(2026, 1, 1), price=Decimal("10987.65"))
    out = _reads(dsn).catalog_index()
    row = _index_row(out, prod)
    assert row["model_key"] == "IDX200ST" and row["name"] == "Sonda índice"
    assert row["supplier_ids"] == [str(supplier)] and row["image_id"] is None and row["has_datasheet"] is False
    assert any(s["id"] == str(supplier) for s in out["suppliers"])
    flat = json.dumps(out)
    assert "10987" not in flat and "price" not in flat


@needs_db
def test_index_has_datasheet_only_for_an_active_datasheet(disposable_database):
    """«Con ficha» filters the index on this flag: a retired sheet or a price list never sets it."""
    dsn = disposable_database
    supplier = _org(dsn, "supplier")
    _, with_sheet = _seed_product(dsn, model="FIC-1", name="a", name_es="a")
    _, retired = _seed_product(dsn, model="FIC-2", name="b", name_es="b")
    _, bare = _seed_product(dsn, model="FIC-3", name="c", name_es="c")
    insert = ("insert into catalog.product_document (product_id, kind, title, drive_file_id, mime_type, source, status) "
              "values (%s, 'datasheet', 'x.pdf', %s, 'application/pdf', 'operator', %s)")
    _owner(dsn, insert, (with_sheet, "SyntheticSheet0101", "active"))
    _owner(dsn, insert, (retired, "SyntheticSheet0102", "retired"))
    _owner(dsn, "insert into catalog.product_document (supplier_organization_id, kind, title, drive_file_id, mime_type, "
                "source, as_of) values (%s, 'price_list', 'Lista.xlsx', 'SyntheticList00101', 'application/pdf', "
                "'operator', '2026-01-01')", (supplier,))
    out = _reads(dsn).catalog_index()
    assert [_index_row(out, p)["has_datasheet"] for p in (with_sheet, retired, bare)] == [True, False, False]


@needs_db
def test_index_leaves_out_inactive_products(disposable_database):
    dsn = disposable_database
    _, prod = _seed_product(dsn, model="IDX-OFF", name="off", name_es="off")
    _owner(dsn, "update catalog.product set active = false where id = %s", (prod,))
    assert all(r[0] != str(prod) for r in _reads(dsn).catalog_index()["items"])


@needs_db
def test_index_version_changes_after_a_write_and_short_circuits(disposable_database):
    dsn = disposable_database
    reads = _reads(dsn)
    v1 = reads.catalog_version()
    assert reads.catalog_index(known_version=v1) == {"version": v1, "unchanged": True}
    _seed_product(dsn, model="IDX-NEW", name="n", name_es="n")
    assert reads.catalog_version() != v1
    assert "items" in reads.catalog_index(known_version=v1)


@needs_db
def test_detail_documents_datasheet_and_supplier_price_list(disposable_database):
    dsn = disposable_database
    supplier = _org(dsn, "supplier")
    _, prod = _seed_product(dsn, model="DOC-1", name="d", name_es="d")
    _obs(dsn, prod, supplier, as_of=dt.date(2026, 1, 1), price=Decimal("5"))
    _owner(dsn, "insert into catalog.product_document (product_id, kind, title, drive_file_id, mime_type, source) "
                "values (%s, 'datasheet', 'DOC-1.pdf', 'SyntheticSheet0001', 'application/pdf', 'operator')", (prod,))
    _owner(dsn, "insert into catalog.product_document (supplier_organization_id, kind, title, drive_file_id, mime_type, "
                "source, as_of) values (%s, 'price_list', 'Lista.xlsx', 'SyntheticList00001', 'application/pdf', "
                "'operator', '2026-01-01')", (supplier,))
    _owner(dsn, "insert into catalog.product_document (supplier_organization_id, kind, title, drive_file_id, mime_type, "
                "source, status) values (%s, 'price_list', 'Vieja.xlsx', 'SyntheticList00002', 'application/pdf', "
                "'operator', 'retired')", (supplier,))
    docs = _reads(dsn).product_detail(prod)["documents"]
    assert [d["url"] for d in docs["datasheets"]] == ["https://drive.google.com/file/d/SyntheticSheet0001/view"]
    assert [(d["title"], d["supplier"]["id"], d["as_of"]) for d in docs["price_lists"]] == [
        ("Lista.xlsx", str(supplier), "2026-01-01")]
    assert _index_row(_reads(dsn).catalog_index(), prod)["has_datasheet"] is True


@needs_db
def test_images_returns_paths_for_visible_ids_only(disposable_database):
    dsn = disposable_database
    _, prod = _seed_product(dsn, model="IMG-1", name="i", name_es="i")
    ids = []
    for ch, status in (("a", "proposed"), ("b", "hidden")):
        ids.append(_owner(dsn, "insert into catalog.product_image (product_id, storage_path, sha256, content_type, "
                               "source, status) values (%s, %s, %s, 'image/png', 'upload', %s) returning id",
                          (prod, f"products/{prod}/{ch * 64}.png", ch * 64, status))[0][0])
    reads = _reads(dsn)
    unknown = uuid.uuid4()
    assert reads.images([*ids, unknown], include_hidden=False) == {str(ids[0]): f"products/{prod}/{'a' * 64}.png"}
    assert set(reads.images([*ids, unknown], include_hidden=True)) == {str(ids[0]), str(ids[1])}
    assert _index_row(reads.catalog_index(), prod)["image_id"] == str(ids[0])


@needs_db
def test_index_keeps_the_english_name_when_a_spanish_one_exists(disposable_database):
    dsn = disposable_database
    _, both = _seed_product(dsn, model="IDX-EN1", name="Magnetic stirrer", name_es="Agitador magnético")
    _, same = _seed_product(dsn, model="IDX-EN2", name="Agitador", name_es="Agitador")
    out = _reads(dsn).catalog_index()
    assert _index_row(out, both)["name_en"] == "Magnetic stirrer"
    assert _index_row(out, same)["name_en"] is None


# -- «Ventas»: each quoted price with its case and outcome, and the summary

def _case_with_quote(dsn: str, number: str, stage: str, institution: str) -> str:
    """A case at `stage` holding CRM quote `number`, its requesting institution named. Seeded with the
    triggers off (as the test DB's owner may): the stage walk and confirmations are not under test."""
    with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("set session_replication_role = replica")
        cur.execute("set role origenlab_owner")
        cur.execute("select id from platform.operator limit 1")
        row = cur.fetchone()
        op = row[0] if row else _operator(dsn)
        cur.execute("insert into crm.organization (kind, name, confirmation) values ('customer', %s, 'confirmed') "
                    "returning id", (institution,))
        org = cur.fetchone()[0]
        cur.execute("insert into crm.opportunity (title, stage, owner_operator_id, organization_id) "
                    "values (%s, 'quoting', %s, %s) returning id::text", (f"Caso {number}", op, org))
        case = cur.fetchone()[0]
        cur.execute("insert into crm.quote (opportunity_id, quote_number, number_origin) values (%s, %s, "
                    "'printed_historical') returning id", (case, number))
        quote = cur.fetchone()[0]
        cur.execute("insert into evidence.source_record (kind, dedupe_key, payload, review_status) values "
                    "('gmail_message', %s, '{}'::jsonb, 'pending') returning id", (f"gmail_message:{uuid.uuid4().hex}",))
        mail = cur.fetchone()[0]
        cur.execute("insert into crm.quote_revision (quote_id, revision_no, status, origin, pdf_sha256, sent_at, "
                    "origin_source_record_id) values (%s, 1, 'sent', 'historical_import', %s, now(), %s)",
                    (quote, "a" * 64, mail))
        if stage == "won":
            cur.execute("update crm.opportunity set stage = 'won', closed_at = now(), won_quote_id = %s, "
                        "won_revision_no = 1 where id = %s", (quote, case))
        elif stage == "lost":
            cur.execute("update crm.opportunity set stage = 'lost', closed_at = now() where id = %s", (case,))
    return case


@needs_db
def test_price_history_names_case_institution_and_outcome_and_summarises(disposable_database):
    dsn = disposable_database
    won_doc, open_doc, loose_doc = (_quote(dsn, d, n) for d, n in (
        ("2026-04-10", "09101-26"), ("2026-05-10", "09102-26"), ("2026-06-10", "09103-26")))
    _qline(dsn, won_doc, 1, "SALES-1", "1000.0000")
    _qline(dsn, open_doc, 1, "SALES-1", "1300.0000")
    _qline(dsn, loose_doc, 1, "SALES-1", "1200.0000")
    won_case = _case_with_quote(dsn, "09101-26", "won", "Universidad Ficticia")
    open_case = _case_with_quote(dsn, "09102-26", "quoting", "Laboratorio Ficticio")

    out = _reads(dsn).price_history("SALES1")
    by_number = {i["document_number"]: i for i in out["items"]}
    assert by_number["09101-26"]["case"] == {"id": won_case, "institution": "Universidad Ficticia", "outcome": "won"}
    assert by_number["09102-26"]["case"] == {"id": open_case, "institution": "Laboratorio Ficticio", "outcome": "open"}
    assert by_number["09103-26"]["case"] is None
    s = out["summary"]
    assert s["times_quoted"] == 3 and s["times_won"] == 1 and s["currency"] == "CLP"
    assert (s["min"], s["max"], s["last"]["price"], s["last"]["date"]) == ("1000.0000", "1300.0000", "1200.0000",
                                                                        "2026-06-10")
    assert s["won_prices"] == ["1000.0000"]
