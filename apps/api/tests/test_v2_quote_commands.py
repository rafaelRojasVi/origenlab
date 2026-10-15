"""Quote commands (create-quote, save-quote-draft) against PostgreSQL, as the real `origenlab_api` role.

Every name, number, cost and rate is invented. Each command runs through `V2QuoteRepository.execute`
exactly as the route will call it: the body validated by its Pydantic model and handed over as
`model_dump(mode="json")`, with the same digest.
"""
from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

import psycopg
import pytest

from origenlab_api.quoting.engine import peso
from origenlab_api.v2.catalog.fx import FxUnavailable
from origenlab_api.v2.commands import CommandRefused, request_digest
from origenlab_api.v2.identity import OperatorIdentity
from origenlab_api.v2.quotes.commands import V2QuoteRepository
from origenlab_api.v2.quotes.draft import QUOTE_BODIES
from v2_command_harness import build_disposable_database, needs_db, runtime_dsn

NOW = datetime(2026, 10, 15, 15, 0, tzinfo=UTC)
TODAY = date(2026, 10, 15)
RATES = {"EUR": Decimal("1000"), "USD": Decimal("900")}
PROFILE = {"profile_freight_clp": 500000, "profile_agent_clp": 120000, "profile_customs_clp": 180000,
           "profile_transport_clp": 30000}
WARRANTY = "Garantía de prueba: doce meses"


def fake_fx(currency: str, on: date) -> dict:
    return {"currency": currency, "clp_per_unit": RATES[currency], "as_of": on, "source": "bcentral",
            "provider": "test"}


def no_fx(currency: str, on: date) -> dict:
    raise FxUnavailable(f"no {currency} rate")


# ------------------------------------------------------------------ fixtures

def _owner(dsn: str, sql: str, params: tuple = ()):
    with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("set role origenlab_owner")
        cur.execute(sql, params)
        return cur.fetchall() if cur.description else None


def _make_operator(dsn: str) -> OperatorIdentity:
    email = f"sales-{uuid.uuid4().hex[:8]}@example.test"
    op_id = _owner(dsn, "insert into platform.operator (auth_user_id, email_norm, display_name, role, status) "
                        "values (gen_random_uuid(), %s, 'Vendedora Prueba', 'sales', 'active') returning id::text",
                   (email,))[0][0]
    return OperatorIdentity(operator_id=op_id, email_norm=email, display_name="Vendedora Prueba",
                            role="sales", status="active")


def _org(dsn: str, kind: str, name: str) -> str:
    return _owner(dsn, "insert into crm.organization (kind, name, confirmation) values (%s, %s, 'machine_proposed') "
                       "returning id::text", (kind, f"{name} {uuid.uuid4().hex[:6]}"))[0][0]


def _case(dsn: str, operator: OperatorIdentity, institution: str, *, closed: bool = False) -> str:
    """A case (a lead) with its requesting institution, and one primary participant."""
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("set role origenlab_owner")
        cur.execute("insert into crm.opportunity (title, stage, owner_operator_id, organization_id) "
                    "values ('Caso ficticio', 'lead', %s, %s) returning id::text", (operator.operator_id, institution))
        case_id = cur.fetchone()[0]
        if closed:
            cur.execute("update crm.opportunity set stage = 'lost', closed_at = now(), close_reason = 'precio' "
                        "where id = %s", (case_id,))
        cur.execute("insert into crm.opportunity_organization (opportunity_id, organization_id, role, valid_from, "
                    "confirmation, confirmed_by_operator_id) values (%s, %s, 'requesting_institution', "
                    "current_date, 'confirmed', %s)", (case_id, institution, operator.operator_id))
        cur.execute("insert into crm.person (display_name, confirmation) values ('Persona Ficticia', "
                    "'machine_proposed') returning id")
        person = cur.fetchone()[0]
        cur.execute("insert into crm.opportunity_participant (opportunity_id, person_id, role, is_primary, "
                    "valid_from, confirmation) values (%s, %s, 'quote_recipient', true, current_date, "
                    "'machine_proposed')", (case_id, person))
        conn.commit()
    return case_id


def _param(dsn: str, key: str, value) -> None:
    if isinstance(value, str):
        _owner(dsn, "insert into catalog.cost_parameter (key, value_json, valid_from, reason) "
                    "values (%s, to_jsonb(%s::text), now() - interval '1 day', 'prueba')", (key, value))
    else:
        _owner(dsn, "insert into catalog.cost_parameter (key, value_numeric, valid_from, reason) "
                    "values (%s, %s, now() - interval '1 day', 'prueba')", (key, value))


@pytest.fixture(scope="module")
def world():
    """One disposable database with the catalog, a case and the parameters every test reads."""
    for dsn in build_disposable_database():
        operator = _make_operator(dsn)
        institution = _org(dsn, "institution", "Universidad Ficticia")
        supplier = _org(dsn, "supplier", "Proveedor Ficticio")
        maker = _org(dsn, "manufacturer", "Fabricante Ficticio")
        _owner(dsn, "insert into catalog.supplier_terms (supplier_organization_id, currency, origin_country, route) "
                    "values (%s, 'EUR', 'DE', 'import_courier')", (supplier,))
        product = _owner(dsn, "insert into catalog.product (manufacturer_organization_id, model_number, name_es, "
                              "product_kind, weight_kg, origin_country, content_origin) values (%s, 'FX-100', "
                              "'Equipo ficticio', 'equipment', 10, 'DE', 'operator') returning id::text",
                         (maker,))[0][0]
        observation = _owner(dsn, "insert into catalog.supplier_product (supplier_organization_id, product_id, as_of, "
                                  "price, currency, price_kind, provenance_note) values (%s, %s, now() - "
                                  "interval '3 days', 1000, 'EUR', 'dealer_net', 'prueba') returning id::text",
                             (supplier, product))[0][0]
        _param(dsn, "quote_number_seed", 1300)
        for key, value in PROFILE.items():
            _param(dsn, key, value)
        _param(dsn, "quote_default_warranty", WARRANTY)
        yield {"dsn": dsn, "operator": operator, "institution": institution, "supplier": supplier,
               "product": product, "observation": observation}


def _repo(dsn: str, fx=fake_fx) -> V2QuoteRepository:
    return V2QuoteRepository(psycopg.connect, runtime_dsn(dsn), fx_resolver=fx, clock=lambda: NOW)


def _run(world: dict, command: str, raw: dict, *, key: str | None = None, fx=fake_fx) -> dict:
    body = QUOTE_BODIES[command](**raw)
    return _repo(world["dsn"], fx).execute(command_name=command, operator=world["operator"],
                                           fields=body.model_dump(mode="json"),
                                           idempotency_key=key or uuid.uuid4().hex,
                                           digest=request_digest(command, body))


def _refused(world: dict, command: str, raw: dict, **kw) -> CommandRefused:
    with pytest.raises(CommandRefused) as exc:
        _run(world, command, raw, **kw)
    return exc.value


def _events(dsn: str, kind: str, aggregate_id: str) -> list[tuple]:
    return _owner(dsn, "select event_type, payload from crm.domain_event where aggregate_kind = %s "
                       "and aggregate_id = %s order by seq", (kind, aggregate_id))


def _rows(dsn: str, sql: str, params: tuple = ()) -> list[dict]:
    with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("set role origenlab_owner")
        cur.execute(sql, params)
        return [dict(zip([d[0] for d in cur.description], r, strict=True)) for r in cur.fetchall()]


def _lines(dsn: str, revision_id: str) -> list[dict]:
    return _rows(dsn, "select * from crm.quote_line where quote_revision_id = %s order by line_no", (revision_id,))


def _revision(dsn: str, revision_id: str) -> dict:
    return _rows(dsn, "select * from crm.quote_revision where id = %s", (revision_id,))[0]


def _new_quote(world: dict) -> dict:
    case = _case(world["dsn"], world["operator"], world["institution"])
    return _run(world, "create-quote", {"opportunity_id": case})


def _catalog_item(world: dict, **extra) -> dict:
    return {"product_id": world["product"], "description": "Equipo ficticio FX-100", "quantity": "1", **extra}


def _save(world: dict, created: dict, draft: dict, *, version: int | None = None, **kw) -> dict:
    return _run(world, "save-quote-draft", {"revision_id": created["revision_id"],
                                            "expected_version": version or created["version"], "draft": draft}, **kw)


# ------------------------------------------------------------------ create-quote

@needs_db
def test_create_quote_mints_a_number_and_opens_a_draft(world) -> None:
    dsn = world["dsn"]
    case = _case(dsn, world["operator"], world["institution"])
    out = _run(world, "create-quote", {"opportunity_id": case})
    assert out["ok"] is True and out["quote_number"] == "01300-26" and out["version"] == 1
    [quote] = _rows(dsn, "select * from crm.quote where id = %s", (out["quote_id"],))
    assert (str(quote["opportunity_id"]), quote["number_origin"]) == (case, "minted")
    assert str(quote["created_by_operator_id"]) == world["operator"].operator_id
    rev = _revision(dsn, out["revision_id"])
    assert (rev["revision_no"], rev["status"], rev["origin"], rev["quote_currency"], rev["price_decimals"]) == (
        1, "draft", "authored", "CLP", 0)
    assert rev["warranty_terms_es"] == WARRANTY and rev["validity_days"] == 30
    assert rev["tax_rate"] == Decimal("0.19") and rev["payment_terms"] is None
    [participant] = _rows(dsn, "select id from crm.opportunity_participant where opportunity_id = %s", (case,))
    assert rev["recipient_participant_id"] == participant["id"]
    assert _events(dsn, "quote", out["quote_id"]) == [
        ("quote.created", {"quote_number": "01300-26", "opportunity_id": case})]
    assert _events(dsn, "quote_revision", out["revision_id"]) == [
        ("quote_revision.opened", {"quote_id": out["quote_id"], "revision_no": 1})]


@needs_db
def test_create_quote_refused_without_seed() -> None:
    """A database of its own: the seed is global state the module database already has."""
    for dsn in build_disposable_database():
        operator = _make_operator(dsn)
        case = _case(dsn, operator, _org(dsn, "institution", "Universidad Ficticia"))
        body = QUOTE_BODIES["create-quote"](opportunity_id=case)
        with pytest.raises(CommandRefused) as exc:
            _repo(dsn).execute(command_name="create-quote", operator=operator, fields=body.model_dump(mode="json"),
                               idempotency_key=uuid.uuid4().hex, digest=request_digest("create-quote", body))
        assert (exc.value.status_code, exc.value.code) == (409, "quote_numbering_not_configured")
        assert _owner(dsn, "select count(*) from crm.quote") == [(0,)]


@needs_db
def test_create_quote_refuses_an_unknown_or_closed_case(world) -> None:
    refused = _refused(world, "create-quote", {"opportunity_id": str(uuid.uuid4())})
    assert (refused.status_code, refused.code) == (404, "case_not_found")
    closed = _case(world["dsn"], world["operator"], world["institution"], closed=True)
    refused = _refused(world, "create-quote", {"opportunity_id": closed})
    assert (refused.status_code, refused.code) == (409, "case_closed")


# ------------------------------------------------------------------ save-quote-draft

@needs_db
def test_save_prices_a_catalog_line_with_profile_expenses(world) -> None:
    dsn = world["dsn"]
    created = _new_quote(world)
    out = _save(world, created, {"items": [_catalog_item(world)]})
    assert out["ok"] is True and out["version"] == 2 and out["revision_id"] == created["revision_id"]

    expected_price = peso((Decimal(1_000_000) + sum(Decimal(v) for v in PROFILE.values())) * Decimal("1.40"))
    item, *expenses = _lines(dsn, created["revision_id"])
    assert (item["line_no"], item["line_kind"], item["item_type"], str(item["product_id"])) == (
        1, "item", "equipment", world["product"])
    assert (item["unit_cost"], item["cost_currency"], item["fx_rate"], item["fx_as_of"], item["fx_source"]) == (
        Decimal("1000"), "EUR", Decimal("1000"), TODAY, "bcentral")
    assert str(item["cost_observation_id"]) == world["observation"]
    assert str(item["supplier_organization_id"]) == world["supplier"]
    assert (item["margin_mode"], item["margin_pct"], item["is_principal"]) == ("markup", Decimal("0.4"), True)
    assert item["unit_price_qc"] == expected_price and item["line_total_qc"] == expected_price

    assert [e["description"] for e in expenses] == ["Flete", "Agente de aduana", "Derechos y aduana",
                                                    "Transporte local"]
    assert [e["line_no"] for e in expenses] == [2, 3, 4, 5]
    assert {(e["line_kind"], e["expense_method"], e["margin_mode"], e["allocated_to_line_no"], e["cost_currency"],
             e["unit_price_qc"], e["line_total_qc"]) for e in expenses} == {
        ("logistics", "profile", "none", 1, "CLP", Decimal(0), Decimal(0))}
    assert [e["unit_cost"] for e in expenses] == [Decimal(v) for v in PROFILE.values()]

    rev = _revision(dsn, created["revision_id"])
    iva = peso(expected_price * Decimal("0.19"))
    assert (rev["subtotal"], rev["discount_total"], rev["tax_base"], rev["tax_total"], rev["grand_total"]) == (
        expected_price, 0, expected_price, iva, expected_price + iva)
    assert rev["totals_computed_at"] is not None and rev["version"] == 2
    assert out["computed"]["totals"]["net"] == str(expected_price)
    assert out["computed"]["principal_line_no"] == 1
    assert _events(dsn, "quote_revision", created["revision_id"])[-1] == (
        "quote_revision.draft_saved", {"items": 1, "net": str(expected_price)})


@needs_db
def test_save_with_stale_version_writes_nothing(world) -> None:
    dsn = world["dsn"]
    created = _new_quote(world)
    _save(world, created, {"items": [_catalog_item(world)]})
    before = _lines(dsn, created["revision_id"])
    refused = _refused(world, "save-quote-draft", {
        "revision_id": created["revision_id"], "expected_version": 1,
        "draft": {"items": [_catalog_item(world, quantity="3")]}})
    assert (refused.status_code, refused.code) == (409, "stale_version")
    assert _lines(dsn, created["revision_id"]) == before
    assert _revision(dsn, created["revision_id"])["version"] == 2


@needs_db
def test_save_replaces_lines(world) -> None:
    dsn = world["dsn"]
    created = _new_quote(world)
    free = {"description": "Accesorio ficticio", "quantity": "2", "item_type": "accessory",
            "unit_cost": "50000", "cost_currency": "CLP"}
    first = _save(world, created, {"items": [_catalog_item(world), free], "expense_method": "manual"})
    assert [line["line_kind"] for line in _lines(dsn, created["revision_id"])] == ["item", "item"]
    _save(world, created, {"items": [free], "expense_method": "manual"}, version=first["version"])
    [only] = _lines(dsn, created["revision_id"])
    assert (only["description"], only["product_id"], only["item_type"]) == ("Accesorio ficticio", None, "accessory")
    assert (only["fx_rate"], only["fx_as_of"], only["fx_source"]) == (Decimal(1), TODAY, "manual")
    assert only["margin_pct"] == Decimal("0.45")
    assert only["unit_price_qc"] == peso(Decimal(50000) * Decimal("1.45"))


@needs_db
def test_save_refuses_labdelivery_text(world) -> None:
    created = _new_quote(world)
    refused = _refused(world, "save-quote-draft", {
        "revision_id": created["revision_id"], "expected_version": 1,
        "draft": {"items": [_catalog_item(world, description="Equipo de Lab Delivery")]}})
    assert (refused.status_code, refused.code) == (422, "labdelivery_refused")
    assert _lines(world["dsn"], created["revision_id"]) == []


@needs_db
def test_fx_override_is_used_and_its_reason_kept(world) -> None:
    dsn = world["dsn"]
    created = _new_quote(world)
    out = _save(world, created, {"items": [_catalog_item(world)], "fx_overrides": [
        {"currency": "EUR", "clp_per_unit": "1100", "reason": "cotización proveedor"}]})
    item = _lines(dsn, created["revision_id"])[0]
    assert (item["fx_rate"], item["fx_source"], item["fx_as_of"]) == (Decimal("1100"), "manual", TODAY)
    assert _revision(dsn, created["revision_id"])["fx_override_reasons"] == {"EUR": "cotización proveedor"}
    assert out["computed"]["fx"] == [{"currency": "EUR", "clp_per_unit": "1100", "as_of": "2026-10-15",
                                      "source": "manual"}]


@needs_db
def test_fx_unavailable_is_a_409(world) -> None:
    created = _new_quote(world)
    refused = _refused(world, "save-quote-draft", {
        "revision_id": created["revision_id"], "expected_version": 1,
        "draft": {"items": [_catalog_item(world)]}}, fx=no_fx)
    assert (refused.status_code, refused.code) == (409, "fx_unavailable")
    assert "EUR" in refused.message


@needs_db
def test_mixed_currencies_get_one_rate_each(world) -> None:
    dsn = world["dsn"]
    created = _new_quote(world)
    usd = {"description": "Repuesto ficticio", "quantity": "1", "item_type": "spare_part",
           "unit_cost": "100", "cost_currency": "USD"}
    out = _save(world, created, {"items": [_catalog_item(world), usd]})
    lines = _lines(dsn, created["revision_id"])
    rates = {(line["cost_currency"], line["fx_rate"]) for line in lines if line["line_kind"] == "item"}
    assert rates == {("EUR", Decimal("1000")), ("USD", Decimal("900"))}
    assert lines[1]["unit_price_qc"] == peso(Decimal(90000) * Decimal("1.45"))
    assert out["version"] == 2


@needs_db
def test_idempotent_replay(world) -> None:
    dsn = world["dsn"]
    created = _new_quote(world)
    raw = {"revision_id": created["revision_id"], "expected_version": 1, "draft": {"items": [_catalog_item(world)]}}
    key = uuid.uuid4().hex
    first = _run(world, "save-quote-draft", raw, key=key)
    again = _run(world, "save-quote-draft", raw, key=key)
    assert first["replayed"] is False and again["replayed"] is True
    assert again["version"] == first["version"] == 2
    saved = [e for e in _events(dsn, "quote_revision", created["revision_id"]) if e[0] == "quote_revision.draft_saved"]
    assert len(saved) == 1


@needs_db
def test_save_refuses_an_unknown_revision(world) -> None:
    refused = _refused(world, "save-quote-draft", {"revision_id": str(uuid.uuid4()), "expected_version": 1,
                                                   "draft": {"items": []}})
    assert (refused.status_code, refused.code) == (404, "revision_not_found")


@needs_db
def test_terms_are_written_and_blank_text_is_null(world) -> None:
    dsn = world["dsn"]
    created = _new_quote(world)
    _save(world, created, {"items": [], "terms": {
        "payment_terms": "30 días", "warranty_terms_es": "", "validity_days": 15,
        "terms_extra": [{"label_es": "Capacitación", "text_es": "Una sesión"}]}})
    rev = _revision(dsn, created["revision_id"])
    assert (rev["payment_terms"], rev["warranty_terms_es"], rev["validity_days"]) == ("30 días", None, 15)
    assert rev["terms_extra"] == [{"label_es": "Capacitación", "text_es": "Una sesión"}]
    assert rev["subtotal"] == 0 and _lines(dsn, created["revision_id"]) == []


@needs_db
def test_a_draft_with_nothing_imported_gets_no_import_expenses(world) -> None:
    """A CLP cost with no supplier terms is domestic: nothing ships, so the profile adds nothing."""
    dsn = world["dsn"]
    created = _new_quote(world)
    out = _save(world, created, {"items": [{"description": "Insumo local", "quantity": "1", "item_type": "consumable",
                                            "unit_cost": "10000", "cost_currency": "CLP"}],
                                 "manual_expenses": [{"label": "Despacho local", "amount": "5000"}]})
    item, expense = _lines(dsn, created["revision_id"])
    assert (expense["line_no"], expense["expense_method"], expense["unit_cost"]) == (2, "manual", Decimal(5000))
    assert expense["allocated_to_line_no"] is None and item["is_principal"] is False
    assert "principal_missing" in out["computed"]["warnings"]
