"""Catalog commands against PostgreSQL, as the real `origenlab_api` role. Every name and number is invented.

Each command runs through `V2CatalogRepository.execute` exactly as the route calls it: the body is
validated by its Pydantic model and handed over as `model_dump(mode="json")`, with the same digest.
"""
from __future__ import annotations

import datetime as dt
import threading
import time
import uuid
from decimal import Decimal

import psycopg
import pytest

from origenlab_api.v2.catalog.commands import V2CatalogRepository
from origenlab_api.v2.catalog.reads import V2CatalogReads
from origenlab_api.v2.catalog.routes import (
    ConfirmProductContentBody,
    CreateProductBody,
    RecordFxRateBody,
    RecordSupplierCostBody,
    ReviewDocumentLineBody,
    SetCostParameterBody,
    SetSupplierTermsBody,
    UpdateProductBody,
)
from origenlab_api.v2.commands import CommandRefused, request_digest
from origenlab_api.v2.crm_authoring import V2CrmAuthoringRepository
from origenlab_api.v2.crm_authoring_routes import AddNoteBody
from origenlab_api.v2.identity import OperatorIdentity
from v2_command_harness import build_disposable_database, needs_db, runtime_dsn

BODIES = {
    "create-product": CreateProductBody,
    "update-product": UpdateProductBody,
    "confirm-product-content": ConfirmProductContentBody,
    "record-supplier-cost": RecordSupplierCostBody,
    "set-supplier-terms": SetSupplierTermsBody,
    "set-cost-parameter": SetCostParameterBody,
    "record-fx-rate": RecordFxRateBody,
    "review-document-line": ReviewDocumentLineBody,
}


@pytest.fixture(scope="module")
def disposable_database():
    yield from build_disposable_database()


def _owner(dsn: str, sql: str, params: tuple = ()):
    with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("set role origenlab_owner")
        cur.execute(sql, params)
        return cur.fetchall() if cur.description else None


@pytest.fixture(scope="module")
def operator(disposable_database) -> OperatorIdentity:
    email = f"sales-{uuid.uuid4().hex[:8]}@example.test"
    op_id = _owner(
        disposable_database,
        "insert into platform.operator (auth_user_id, email_norm, display_name, role, status) "
        "values (gen_random_uuid(), %s, 'Vendedora Prueba', 'sales', 'active') returning id::text",
        (email,),
    )[0][0]
    return OperatorIdentity(operator_id=op_id, email_norm=email, display_name="Vendedora Prueba",
                            role="sales", status="active")


def _org(dsn: str, *, kind: str = "manufacturer", archived_by: str | None = None) -> str:
    name = f"Fabricante Ficticio {uuid.uuid4().hex[:6]}"
    if archived_by:
        return _owner(dsn, "insert into crm.organization (kind, name, confirmation, status, archived_at, "
                           "archived_by_operator_id, archive_reason) values (%s, %s, 'machine_proposed', "
                           "'archived', now(), %s, 'duplicado') returning id::text", (kind, name, archived_by))[0][0]
    return _owner(dsn, "insert into crm.organization (kind, name, confirmation) "
                       "values (%s, %s, 'machine_proposed') returning id::text", (kind, name))[0][0]


def _run(dsn: str, operator: OperatorIdentity, command: str, raw: dict, key: str | None = None) -> dict:
    body = BODIES[command](**raw)
    repo = V2CatalogRepository(psycopg.connect, runtime_dsn(dsn))
    return repo.execute(command_name=command, operator=operator, fields=body.model_dump(mode="json"),
                        idempotency_key=key or uuid.uuid4().hex, digest=request_digest(command, body))


def _refused(dsn: str, operator: OperatorIdentity, command: str, raw: dict) -> CommandRefused:
    with pytest.raises(CommandRefused) as exc:
        _run(dsn, operator, command, raw)
    return exc.value


def _events(dsn: str, aggregate_kind: str, aggregate_id: str) -> list[tuple]:
    return _owner(dsn, "select event_type, payload, actor_kind, actor_operator_id::text from crm.domain_event "
                       "where aggregate_kind = %s and aggregate_id = %s order by seq", (aggregate_kind, aggregate_id))


def _product(dsn: str, product_id: str) -> dict:
    with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("set role origenlab_owner")
        cur.execute("select * from catalog.product where id = %s", (product_id,))
        return dict(zip([d[0] for d in cur.description], cur.fetchone(), strict=True))


def _new_product(dsn: str, operator: OperatorIdentity, *, model: str | None = None, maker: str | None = None,
                 **extra) -> str:
    raw = {"manufacturer_organization_id": maker or _org(dsn), "model_number": model or f"MX-{uuid.uuid4().hex[:6]}",
           **extra}
    return _run(dsn, operator, "create-product", raw)["product_id"]


# ------------------------------------------------------------------ create-product

@needs_db
def test_create_product_persists_and_records_one_event(disposable_database, operator) -> None:
    dsn = disposable_database
    maker = _org(dsn)
    out = _run(dsn, operator, "create-product", {
        "manufacturer_organization_id": maker, "model_number": "SONIC-100", "name": "Ultrasonic processor",
        "name_es": "Sonicador ultrasónico", "product_kind": "equipment", "category_es": "Sonicadores",
        "specs": [{"label_es": "Potencia", "value": "100", "unit": "W"}],
        "weight_kg": "4.250", "length_cm": "40.5", "origin_country": "DE", "note": "alta de prueba"})
    assert out["ok"] is True and out["version"] == 1 and out["replayed"] is False
    row = _product(dsn, out["product_id"])
    assert (row["model_number"], row["model_key"], row["content_origin"]) == ("SONIC-100", "SONIC100", "operator")
    assert str(row["created_by_operator_id"]) == operator.operator_id
    assert row["weight_kg"] == Decimal("4.250") and row["specs"] == [
        {"label_es": "Potencia", "value": "100", "unit": "W", "source": "operator"}]
    [(event_type, payload, actor_kind, actor)] = _events(dsn, "product", out["product_id"])
    assert (event_type, actor_kind, actor) == ("product.created", "operator", operator.operator_id)
    assert payload == {"model_number": "SONIC-100", "content_origin": "operator", "note": "alta de prueba"}


@needs_db
def test_same_model_two_manufacturers_allowed(disposable_database, operator) -> None:
    dsn = disposable_database
    a = _new_product(dsn, operator, model="TWIN-7")
    b = _new_product(dsn, operator, model="twin 7")
    assert a != b


@needs_db
def test_duplicate_model_key_same_manufacturer_refused(disposable_database, operator) -> None:
    dsn = disposable_database
    maker = _org(dsn)
    _new_product(dsn, operator, model="SONIC-100", maker=maker)
    refused = _refused(dsn, operator, "create-product", {"manufacturer_organization_id": maker,
                                                         "model_number": "sonic 100"})
    assert (refused.status_code, refused.code) == (409, "duplicate_model")
    count = _owner(dsn, "select count(*) from catalog.product where manufacturer_organization_id = %s", (maker,))
    assert count == [(1,)]


@needs_db
def test_create_product_refuses_an_unknown_merged_or_archived_manufacturer(disposable_database, operator) -> None:
    dsn = disposable_database
    refused = _refused(dsn, operator, "create-product", {"manufacturer_organization_id": str(uuid.uuid4()),
                                                         "model_number": "X-1"})
    assert (refused.status_code, refused.code) == (404, "manufacturer_not_found")
    survivor, loser = _org(dsn), _org(dsn)
    _owner(dsn, "update crm.organization set merged_into_organization_id = %s where id = %s", (survivor, loser))
    refused = _refused(dsn, operator, "create-product", {"manufacturer_organization_id": loser, "model_number": "X-1"})
    assert (refused.status_code, refused.code) == (409, "organization_merged")
    archived = _org(dsn, archived_by=operator.operator_id)
    refused = _refused(dsn, operator, "create-product", {"manufacturer_organization_id": archived,
                                                         "model_number": "X-1"})
    assert (refused.status_code, refused.code) == (409, "archived_subject")


@needs_db
def test_a_number_the_column_cannot_hold_is_422_not_500(disposable_database, operator) -> None:
    refused = _refused(disposable_database, operator, "create-product", {
        "manufacturer_organization_id": _org(disposable_database), "model_number": "BIG-1", "weight_kg": "1e30"})
    assert (refused.status_code, refused.code) == (422, "value_out_of_range")


# ------------------------------------------------------------------ update-product

@needs_db
def test_update_product_bumps_version_and_names_what_changed(disposable_database, operator) -> None:
    dsn = disposable_database
    pid = _new_product(dsn, operator, weight_kg="1.000", name_es="Balanza")
    out = _run(dsn, operator, "update-product", {"product_id": pid, "expected_version": 1, "weight_kg": "2.5",
                                                  "name_es": "Balanza", "active": False})
    assert out["version"] == 2
    row = _product(dsn, pid)
    assert (row["weight_kg"], row["active"], row["version"]) == (Decimal("2.500"), False, 2)
    updated = [e for e in _events(dsn, "product", pid) if e[0] == "product.updated"]
    assert len(updated) == 1 and sorted(updated[0][1]["changed"]) == ["active", "weight_kg"]
    assert (updated[0][2], updated[0][3]) == ("operator", operator.operator_id)


@needs_db
def test_update_content_clears_confirmation(disposable_database, operator) -> None:
    dsn = disposable_database
    pid = _new_product(dsn, operator, name_es="Centrífuga")
    _run(dsn, operator, "confirm-product-content", {"product_id": pid, "expected_version": 1})
    # A physical-data change keeps the confirmation; a content change clears it.
    _run(dsn, operator, "update-product", {"product_id": pid, "expected_version": 2, "height_cm": "30.0"})
    assert _product(dsn, pid)["content_confirmed_at"] is not None
    _run(dsn, operator, "update-product", {"product_id": pid, "expected_version": 3,
                                           "specs": [{"label_es": "Velocidad", "value": "4000", "unit": "rpm"}]})
    row = _product(dsn, pid)
    assert (row["content_confirmed_at"], row["content_confirmed_by_operator_id"]) == (None, None)
    assert (row["content_origin"], row["version"]) == ("operator", 4)


@needs_db
def test_update_to_a_duplicate_model_is_refused(disposable_database, operator) -> None:
    dsn = disposable_database
    maker = _org(dsn)
    _new_product(dsn, operator, model="ABC-1", maker=maker)
    other = _new_product(dsn, operator, model="ABC-2", maker=maker)
    refused = _refused(dsn, operator, "update-product", {"product_id": other, "expected_version": 1,
                                                         "model_number": "abc.1"})
    assert (refused.status_code, refused.code) == (409, "duplicate_model")
    assert _product(dsn, other)["model_number"] == "ABC-2"


@needs_db
def test_stale_version_refused(disposable_database, operator) -> None:
    dsn = disposable_database
    pid = _new_product(dsn, operator)
    refused = _refused(dsn, operator, "update-product", {"product_id": pid, "expected_version": 9, "name": "Otro"})
    assert (refused.status_code, refused.code) == (409, "stale_version")
    assert _product(dsn, pid)["version"] == 1
    assert [e[0] for e in _events(dsn, "product", pid)] == ["product.created"]


@needs_db
def test_update_with_nothing_different_is_refused(disposable_database, operator) -> None:
    dsn = disposable_database
    pid = _new_product(dsn, operator, name="Igual")
    refused = _refused(dsn, operator, "update-product", {"product_id": pid, "expected_version": 1, "name": "Igual"})
    assert (refused.status_code, refused.code) == (422, "no_changes")
    refused = _refused(dsn, operator, "update-product", {"product_id": str(uuid.uuid4()), "expected_version": 1,
                                                         "name": "X"})
    assert (refused.status_code, refused.code) == (404, "product_not_found")


# ------------------------------------------------------------------ confirm-product-content

@needs_db
def test_confirm_product_content_once(disposable_database, operator) -> None:
    dsn = disposable_database
    pid = _new_product(dsn, operator, name_es="Agitador")
    out = _run(dsn, operator, "confirm-product-content", {"product_id": pid, "expected_version": 1})
    assert (out["version"], out["already_confirmed"]) == (2, False)
    row = _product(dsn, pid)
    assert str(row["content_confirmed_by_operator_id"]) == operator.operator_id and row["content_confirmed_at"]
    again = _run(dsn, operator, "confirm-product-content", {"product_id": pid, "expected_version": 1})
    assert (again["version"], again["already_confirmed"]) == (2, True)
    confirmed = [e for e in _events(dsn, "product", pid) if e[0] == "product.content_confirmed"]
    assert len(confirmed) == 1 and confirmed[0][2] == "operator"
    refused = _refused(dsn, operator, "confirm-product-content", {"product_id": str(uuid.uuid4()),
                                                                  "expected_version": 1})
    assert (refused.status_code, refused.code) == (404, "product_not_found")


@needs_db
def test_confirm_product_content_stale_version_refused(disposable_database, operator) -> None:
    dsn = disposable_database
    pid = _new_product(dsn, operator)
    refused = _refused(dsn, operator, "confirm-product-content", {"product_id": pid, "expected_version": 5})
    assert (refused.status_code, refused.code) == (409, "stale_version")
    assert _product(dsn, pid)["content_confirmed_at"] is None


# ------------------------------------------------------------------ record-supplier-cost

def _cost(pid: str, supplier: str, **extra) -> dict:
    return {"product_id": pid, "supplier_organization_id": supplier, "price": "1234.5", "currency": "EUR",
            "price_kind": "dealer_net", "as_of": "2026-09-01T00:00:00+00:00", **extra}


@needs_db
def test_record_supplier_cost_appends_an_observation(disposable_database, operator) -> None:
    dsn = disposable_database
    pid, supplier = _new_product(dsn, operator), _org(dsn, kind="supplier")
    out = _run(dsn, operator, "record-supplier-cost", _cost(pid, supplier, list_price="2000", discount_pct="0.3",
                                                             source_document="OF-0001", note="oferta sintética"))
    assert out["ok"] is True and out["product_id"] == pid
    [row] = _owner(dsn, "select price, currency, price_kind, list_price, discount_pct, source_document, "
                        "provenance_note, recorded_by_operator_id::text from catalog.supplier_product "
                        "where id = %s", (out["supplier_product_id"],))
    assert row == (Decimal("1234.500000"), "EUR", "dealer_net", Decimal("2000.000000"), Decimal("0.300000"),
                   "OF-0001", "oferta sintética", operator.operator_id)
    [(event_type, payload, actor_kind, _)] = [e for e in _events(dsn, "product", pid) if e[0] == "product.cost_recorded"]
    assert (event_type, actor_kind) == ("product.cost_recorded", "operator")
    assert payload["supplier_organization_id"] == supplier and payload["price_kind"] == "dealer_net"
    assert _product(dsn, pid)["version"] == 1  # an observation is not a product edit


@needs_db
def test_cost_event_payload_has_no_price(disposable_database, operator) -> None:
    dsn = disposable_database
    pid, supplier = _new_product(dsn, operator), _org(dsn, kind="supplier")
    _run(dsn, operator, "record-supplier-cost", _cost(pid, supplier, price="777.25", list_price="999",
                                                      discount_pct="0.2222", note="precio 777.25"))
    [payload] = [e[1] for e in _events(dsn, "product", pid) if e[0] == "product.cost_recorded"]
    assert not {"price", "list_price", "discount_pct", "note"} & set(payload)
    assert "777.25" not in str(payload) and "999" not in str(payload)


@needs_db
def test_duplicate_observation_refused(disposable_database, operator) -> None:
    dsn = disposable_database
    pid, supplier = _new_product(dsn, operator), _org(dsn, kind="supplier")
    _run(dsn, operator, "record-supplier-cost", _cost(pid, supplier))
    refused = _refused(dsn, operator, "record-supplier-cost", _cost(pid, supplier, price="1"))
    assert (refused.status_code, refused.code) == (409, "duplicate_observation")
    # Another price kind (or quantity tier) on the same day is a different observation.
    _run(dsn, operator, "record-supplier-cost", _cost(pid, supplier, price_kind="list"))
    _run(dsn, operator, "record-supplier-cost", _cost(pid, supplier, min_qty="10"))
    count = _owner(dsn, "select count(*) from catalog.supplier_product where product_id = %s", (pid,))
    assert count == [(3,)]


@needs_db
def test_record_supplier_cost_unknown_product_or_supplier(disposable_database, operator) -> None:
    dsn = disposable_database
    pid, supplier = _new_product(dsn, operator), _org(dsn, kind="supplier")
    refused = _refused(dsn, operator, "record-supplier-cost", _cost(str(uuid.uuid4()), supplier))
    assert (refused.status_code, refused.code) == (404, "product_not_found")
    refused = _refused(dsn, operator, "record-supplier-cost", _cost(pid, str(uuid.uuid4())))
    assert (refused.status_code, refused.code) == (404, "supplier_not_found")


# ------------------------------------------------------------------ set-supplier-terms

def _terms(supplier: str, **extra) -> dict:
    return {"supplier_organization_id": supplier, "currency": "EUR", "route": "import_courier", **extra}


@needs_db
def test_set_supplier_terms_creates_then_updates(disposable_database, operator) -> None:
    dsn = disposable_database
    supplier = _org(dsn, kind="supplier")
    created = _run(dsn, operator, "set-supplier-terms", _terms(supplier, origin_country="DE", incoterm="EXW",
                                                               default_discount_pct="0.25", map_enforced=True))
    assert created["version"] == 1
    refused = _refused(dsn, operator, "set-supplier-terms", _terms(supplier))
    assert (refused.status_code, refused.code) == (409, "terms_exist")
    updated = _run(dsn, operator, "set-supplier-terms", _terms(supplier, expected_version=1, route="import_freight",
                                                               default_lead_time_es="4 semanas"))
    assert updated["version"] == 2
    [row] = _owner(dsn, "select route, incoterm, default_discount_pct, map_enforced, default_lead_time_es, version, "
                        "updated_by_operator_id::text from catalog.supplier_terms where supplier_organization_id = %s",
                   (supplier,))
    # The set replaces the whole record: what the second call left out is cleared.
    assert row == ("import_freight", None, None, False, "4 semanas", 2, operator.operator_id)
    refused = _refused(dsn, operator, "set-supplier-terms", _terms(supplier, expected_version=1))
    assert (refused.status_code, refused.code) == (409, "stale_version")
    events = _events(dsn, "organization", supplier)
    assert [e[0] for e in events] == ["organization.supplier_terms_set"] * 2
    assert {(e[2], e[3]) for e in events} == {("operator", operator.operator_id)}
    assert {k: events[1][1][k] for k in ("route", "currency")} == {"route": "import_freight", "currency": "EUR"}
    assert "default_discount_pct" not in events[0][1]


@needs_db
def test_set_supplier_terms_refusals(disposable_database, operator) -> None:
    dsn = disposable_database
    refused = _refused(dsn, operator, "set-supplier-terms", _terms(str(uuid.uuid4())))
    assert (refused.status_code, refused.code) == (404, "supplier_not_found")
    refused = _refused(dsn, operator, "set-supplier-terms", _terms(_org(dsn, kind="supplier"), expected_version=1))
    assert (refused.status_code, refused.code) == (409, "stale_version")


# ------------------------------------------------------------------ set-cost-parameter

def _reads(dsn: str) -> V2CatalogReads:
    return V2CatalogReads(psycopg.connect, runtime_dsn(dsn))


@needs_db
def test_set_cost_parameter_appends_a_current_row(disposable_database, operator) -> None:
    dsn = disposable_database
    before = _reads(dsn).parameters()["current"]["iva_rate"]
    out = _run(dsn, operator, "set-cost-parameter", {"key": "iva_rate", "value": "0.2", "reason": "valor sintético",
                                                      "expected_current_id": before["id"]})
    current = _reads(dsn).parameters()["current"]["iva_rate"]
    assert (current["id"], current["value"], current["reason"]) == (out["cost_parameter_id"], "0.200000",
                                                                    "valor sintético")
    [(set_by,)] = _owner(dsn, "select set_by_operator_id::text from catalog.cost_parameter where id = %s",
                         (out["cost_parameter_id"],))
    assert set_by == operator.operator_id
    [(event_type, payload, actor_kind, _)] = _events(dsn, "cost_parameter", out["cost_parameter_id"])
    assert (event_type, actor_kind, payload["key"]) == ("cost_parameter.set", "operator", "iva_rate")
    assert "value" not in payload
    # Twice in a row for the same key: each gets its own valid_from, no collision.
    again = _run(dsn, operator, "set-cost-parameter", {"key": "iva_rate", "value": "0.19", "reason": "vuelta atrás"})
    assert _reads(dsn).parameters()["current"]["iva_rate"]["id"] == again["cost_parameter_id"]


@needs_db
def test_set_cost_parameter_refuses_a_changed_current_row(disposable_database, operator) -> None:
    dsn = disposable_database
    first = _run(dsn, operator, "set-cost-parameter", {"key": "default_markup_service", "value": "0.11",
                                                        "reason": "sintético uno"})
    _run(dsn, operator, "set-cost-parameter", {"key": "default_markup_service", "value": "0.22",
                                               "reason": "sintético dos", "expected_current_id": first["cost_parameter_id"]})
    # A second admin still holding the first row's id loses, rather than overwriting silently.
    refused = _refused(dsn, operator, "set-cost-parameter", {"key": "default_markup_service", "value": "0.33",
                                                             "reason": "sintético tres",
                                                             "expected_current_id": first["cost_parameter_id"]})
    assert (refused.status_code, refused.code) == (409, "parameter_changed")
    # A key with no row yet refuses any expected id.
    refused = _refused(dsn, operator, "set-cost-parameter", {"key": "margin_target_max", "value": "0.5",
                                                             "reason": "sintético", "expected_current_id": str(uuid.uuid4())})
    assert (refused.status_code, refused.code) == (409, "parameter_changed")
    assert _reads(dsn).parameters()["current"]["default_markup_service"]["value"] == "0.220000"


# ------------------------------------------------------------------ record-fx-rate

@needs_db
def test_record_fx_rate_manual_rows_newest_wins(disposable_database, operator) -> None:
    dsn = disposable_database
    first = _run(dsn, operator, "record-fx-rate", {"currency": "USD", "rate_date": "2026-09-15",
                                                    "clp_per_unit": "900.5", "reason": "tasa sintética"})
    second = _run(dsn, operator, "record-fx-rate", {"currency": "USD", "rate_date": "2026-09-15",
                                                     "clp_per_unit": "901.5", "reason": "corrección sintética"})
    assert first["fx_rate_id"] != second["fx_rate_id"]
    rows = _owner(dsn, "select source, provider, recorded_by_operator_id::text, reason from catalog.fx_rate "
                       "where id = %s", (second["fx_rate_id"],))
    assert rows == [("manual", "operator", operator.operator_id, "corrección sintética")]
    assert _reads(dsn).stored_fx("USD", dt.date(2026, 9, 15))["clp_per_unit"] == "901.500000"
    [(event_type, payload, actor_kind, _)] = _events(dsn, "fx_rate", second["fx_rate_id"])
    assert (event_type, actor_kind) == ("fx_rate.recorded", "operator")
    assert {k: payload[k] for k in ("currency", "rate_date", "source")} == {
        "currency": "USD", "rate_date": "2026-09-15", "source": "manual"}


# ------------------------------------------------------------------ review-document-line

def _document(dsn: str) -> str:
    return _owner(dsn, "insert into evidence.source_record (kind, dedupe_key, payload) values ('quote_document', %s, "
                       "'{\"printed_quote_number\":\"T-1\"}') returning id::text",
                  (f"quote_document:{uuid.uuid4().hex}",))[0][0]


def _line(dsn: str, source: str, line_no: int, status: str) -> str:
    return _owner(dsn, "insert into evidence.document_line (source_record_id, line_no, model, qty, unit_price, "
                       "line_total, currency, extractor, check_status) values (%s, %s, 'MX-1', 1, 100, 100, 'CLP', "
                       "'test', %s) returning id::text", (source, line_no, status))[0][0]


@needs_db
def test_review_only_disputed_line(disposable_database, operator) -> None:
    dsn = disposable_database
    source = _document(dsn)
    verified, disputed = _line(dsn, source, 1, "verified"), _line(dsn, source, 2, "disputed")
    refused = _refused(dsn, operator, "review-document-line", {"document_line_id": verified,
                                                               "review_note": "revisión sintética"})
    assert (refused.status_code, refused.code) == (409, "line_not_disputed")
    out = _run(dsn, operator, "review-document-line", {"document_line_id": disputed, "qty": "2", "line_total": "200",
                                                        "review_note": "cantidad corregida"})
    assert out["ok"] is True and out["source_record_id"] == source
    [row] = _owner(dsn, "select check_status, qty, unit_price, line_total, review_note, reviewed_by_operator_id::text "
                        "from evidence.document_line where id = %s", (disputed,))
    assert row == ("reviewed", Decimal("2.000000"), Decimal("100.0000"), Decimal("200.0000"), "cantidad corregida",
                   operator.operator_id)
    [(event_type, payload, actor_kind, _)] = _events(dsn, "source_record", source)
    assert (event_type, actor_kind, payload["document_line_id"]) == (
        "source_record.document_line_reviewed", "operator", disputed)
    refused = _refused(dsn, operator, "review-document-line", {"document_line_id": disputed,
                                                               "review_note": "otra vez"})
    assert (refused.status_code, refused.code) == (409, "line_not_disputed")
    refused = _refused(dsn, operator, "review-document-line", {"document_line_id": str(uuid.uuid4()),
                                                               "review_note": "no existe"})
    assert (refused.status_code, refused.code) == (404, "document_line_not_found")


# ------------------------------------------------------------------ idempotency

@needs_db
def test_replay_returns_same_body(disposable_database, operator) -> None:
    dsn = disposable_database
    key = uuid.uuid4().hex
    raw = {"manufacturer_organization_id": _org(dsn), "model_number": "REPLAY-1"}
    first = _run(dsn, operator, "create-product", raw, key)
    second = _run(dsn, operator, "create-product", raw, key)
    assert first["replayed"] is False and second["replayed"] is True
    assert {k: v for k, v in second.items() if k != "replayed"} == {k: v for k, v in first.items() if k != "replayed"}
    assert len(_events(dsn, "product", first["product_id"])) == 1


# ------------------------------------------------------------------ notes on products

def _note(dsn: str, operator: OperatorIdentity, raw: dict) -> dict:
    body = AddNoteBody(**raw)
    repo = V2CrmAuthoringRepository(psycopg.connect, runtime_dsn(dsn))
    return repo.execute(command_name="add-note", operator=operator, fields=body.model_dump(),
                        idempotency_key=uuid.uuid4().hex, digest=request_digest("add-note", body))


@needs_db
def test_a_note_on_an_existing_product(disposable_database, operator) -> None:
    dsn = disposable_database
    pid = _new_product(dsn, operator)
    out = _note(dsn, operator, {"subject_kind": "product", "subject_id": pid, "body": "Pedir ficha técnica."})
    assert _owner(dsn, "select subject_kind, subject_id::text from crm.note where id = %s", (out["note_id"],)) == [
        ("product", pid)]
    assert [n["body"] for n in _reads(dsn).product_detail(uuid.UUID(pid))["notes"]] == ["Pedir ficha técnica."]


@needs_db
def test_a_note_on_an_unknown_product_is_refused(disposable_database, operator) -> None:
    with pytest.raises(CommandRefused) as exc:
        _note(disposable_database, operator, {"subject_kind": "product", "subject_id": str(uuid.uuid4()),
                                              "body": "No existe."})
    assert (exc.value.status_code, exc.value.code) == (404, "product_not_found")


@needs_db
def test_notes_on_other_subjects_are_unchanged(disposable_database, operator) -> None:
    # Person/organization/opportunity notes keep their behaviour: no subject lookup is added.
    out = _note(disposable_database, operator, {"subject_kind": "organization",
                                                "subject_id": _org(disposable_database), "body": "Proveedor serio."})
    assert out["ok"] is True


# ------------------------------------------------------------------ fix round 1

@needs_db
def test_update_product_moves_it_to_another_live_manufacturer(disposable_database, operator) -> None:
    dsn = disposable_database
    pid = _new_product(dsn, operator, model="MOVE-1")
    other = _org(dsn)
    out = _run(dsn, operator, "update-product", {"product_id": pid, "expected_version": 1,
                                                  "manufacturer_organization_id": other})
    assert out["version"] == 2 and str(_product(dsn, pid)["manufacturer_organization_id"]) == other
    [changed] = [e[1]["changed"] for e in _events(dsn, "product", pid) if e[0] == "product.updated"]
    assert changed == ["manufacturer_organization_id"]
    archived = _org(dsn, archived_by=operator.operator_id)
    refused = _refused(dsn, operator, "update-product", {"product_id": pid, "expected_version": 2,
                                                         "manufacturer_organization_id": archived})
    assert (refused.status_code, refused.code) == (409, "archived_subject")
    refused = _refused(dsn, operator, "update-product", {"product_id": pid, "expected_version": 2,
                                                         "manufacturer_organization_id": str(uuid.uuid4())})
    assert (refused.status_code, refused.code) == (404, "manufacturer_not_found")
    assert _product(dsn, pid)["version"] == 2


@needs_db
@pytest.mark.parametrize("field", ["source_document", "note"])
def test_a_labdelivery_cost_is_refused(disposable_database, operator, field) -> None:
    dsn = disposable_database
    pid, supplier = _new_product(dsn, operator), _org(dsn, kind="supplier")
    refused = _refused(dsn, operator, "record-supplier-cost", _cost(pid, supplier, **{field: "Cotización Lab-Delivery 12"}))
    assert (refused.status_code, refused.code) == (422, "labdelivery_refused")
    assert _owner(dsn, "select count(*) from catalog.supplier_product where product_id = %s", (pid,)) == [(0,)]


def _ungranted_locks(dsn: str) -> int:
    with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
        # A row-lock wait is an ungranted transactionid (or tuple) lock; transactionid rows carry
        # no database, so this counts the whole disposable cluster, which only this test uses now.
        cur.execute("select count(*) from pg_locks where not granted")
        return cur.fetchone()[0]


@needs_db
def test_concurrent_costs_on_one_product_take_consecutive_event_positions(disposable_database, operator) -> None:
    """Two costs on one product while a third transaction holds the product: both wait, then append
    one after the other (seq 2 and 3) instead of colliding on the stream position."""
    dsn = disposable_database
    pid, supplier = _new_product(dsn, operator), _org(dsn, kind="supplier")
    results: dict[int, object] = {}

    def record(i: int) -> None:
        try:
            results[i] = _run(dsn, operator, "record-supplier-cost",
                              _cost(pid, supplier, as_of=f"2026-09-0{i + 1}T00:00:00+00:00"))
        except Exception as exc:  # noqa: BLE001 - the assertion below names it
            results[i] = exc

    with psycopg.connect(dsn) as holder, holder.cursor() as cur:
        cur.execute("set role origenlab_owner")
        cur.execute("select id from catalog.product where id = %s for update", (pid,))
        threads = [threading.Thread(target=record, args=(i,)) for i in (1, 2)]
        for t in threads:
            t.start()
        deadline = time.monotonic() + 10
        while _ungranted_locks(dsn) < 2 and time.monotonic() < deadline:
            time.sleep(0.05)
        assert _ungranted_locks(dsn) >= 2 and all(t.is_alive() for t in threads)
        holder.commit()  # release both at the same instant
    for t in threads:
        t.join(timeout=30)
    assert all(isinstance(r, dict) and r["ok"] for r in results.values()), results
    seqs = _owner(dsn, "select seq, event_type from crm.domain_event where aggregate_kind = 'product' "
                       "and aggregate_id = %s order by seq", (pid,))
    assert seqs == [(1, "product.created"), (2, "product.cost_recorded"), (3, "product.cost_recorded")]


# ------------------------------------------------------------------ Labdelivery, slice-wide (spec S5)

_ORG = "00000000-0000-4000-8000-0000000000bb"
_PID = "00000000-0000-4000-8000-0000000000aa"
_LAB = "según la cotización de Labdelivery"
#: A valid body for every catalog command; each case below puts Labdelivery into one free-text field.
_VALID_BODIES = {
    "create-product": {"manufacturer_organization_id": _ORG, "model_number": "SONIC-100"},
    "update-product": {"product_id": _PID, "expected_version": 1, "name_es": "Sonicador"},
    "confirm-product-content": {"product_id": _PID, "expected_version": 1},
    "record-supplier-cost": {"product_id": _PID, "supplier_organization_id": _ORG, "price": "10.5",
                             "currency": "EUR", "price_kind": "dealer_net", "as_of": "2026-09-01T00:00:00+00:00"},
    "set-supplier-terms": {"supplier_organization_id": _ORG, "currency": "EUR", "route": "import_courier"},
    "set-cost-parameter": {"key": "iva_rate", "value": "0.19", "reason": "sintético"},
    "record-fx-rate": {"currency": "USD", "rate_date": "2026-09-01", "clp_per_unit": "900", "reason": "sintético"},
    "review-document-line": {"document_line_id": _PID, "review_note": "revisado"},
    "update-product-image": {"image_id": _PID, "expected_version": 1, "status": "hidden"},
    "add-product-image": {"product_id": _PID, "sha256": "a" * 64, "content_type": "image/png",
                          "storage_path": f"products/{_PID}/{'a' * 64}.png"},
}
_FREE_TEXT = [(command, "note", _LAB) for command in _VALID_BODIES if command != "add-product-image"] + [
    ("create-product", "model_number", "Labdelivery 100"),
    ("create-product", "name", _LAB),
    ("create-product", "name_es", _LAB),
    ("create-product", "description_es", _LAB),
    ("create-product", "category_es", _LAB),
    ("create-product", "specs", [{"label_es": "Origen", "value": _LAB}]),
    ("update-product", "description_es", _LAB),
    ("update-product", "specs", [{"label_es": _LAB, "value": "1"}]),
    ("record-supplier-cost", "source_document", _LAB),
    ("record-supplier-cost", "incoterm", "Labdelivery"),
    ("set-supplier-terms", "notes", _LAB),
    ("set-supplier-terms", "default_lead_time_es", _LAB),
    ("set-supplier-terms", "incoterm", "Labdelivery"),
    ("set-cost-parameter", "reason", _LAB),
    ("record-fx-rate", "reason", _LAB),
    ("review-document-line", "review_note", _LAB),
    ("update-product-image", "caption_es", _LAB),
    ("add-product-image", "caption_es", _LAB),
    ("add-product-image", "source_url", "https://lab-delivery.example/x.png"),
]


def _no_transaction(*_args, **_kwargs):
    raise AssertionError("a Labdelivery refusal must come before any transaction opens")


@pytest.mark.parametrize(("command", "field", "value"), _FREE_TEXT,
                         ids=[f"{c}-{f}" for c, f, _ in _FREE_TEXT])
def test_labdelivery_in_any_free_text_field_of_any_catalog_command_is_refused(command, field, value) -> None:
    from origenlab_api.v2.catalog.routes import AddProductImageFields, UpdateProductImageBody

    bodies = {**BODIES, "update-product-image": UpdateProductImageBody, "add-product-image": AddProductImageFields}
    body = bodies[command](**{**_VALID_BODIES[command], field: value})
    operator = OperatorIdentity(operator_id="00000000-0000-4000-8000-000000000001", email_norm="op@example.test",
                                display_name="Op", role="sales", status="active")
    repo = V2CatalogRepository(_no_transaction, "postgresql://unused@127.0.0.1/unused")
    with pytest.raises(CommandRefused) as exc:
        repo.execute(command_name=command, operator=operator, fields=body.model_dump(mode="json"),
                     idempotency_key=uuid.uuid4().hex, digest=request_digest(command, body))
    assert (exc.value.status_code, exc.value.code) == (422, "labdelivery_refused")


def test_every_catalog_command_is_covered_by_the_labdelivery_cases() -> None:
    assert set(_VALID_BODIES) == set(V2CatalogRepository._HANDLERS)
    assert {c for c, f, _ in _FREE_TEXT if f == "note"} == set(V2CatalogRepository._HANDLERS) - {"add-product-image"}
