"""Quote commands (create, save, approve, void, revise) against PostgreSQL, as the real `origenlab_api` role.

Every name, number, cost and rate is invented. Each command runs through `V2QuoteRepository.execute`
exactly as the route will call it: the body validated by its Pydantic model and handed over as
`model_dump(mode="json")`, with the same digest.
"""
from __future__ import annotations

import json
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

import psycopg
import pytest

from origenlab_api.quoting.engine import compute, peso
from origenlab_api.v2.catalog.fx import FxUnavailable
from origenlab_api.v2.commands import CommandRefused, request_digest
from origenlab_api.v2.identity import OperatorIdentity
from origenlab_api.v2.quotes.commands import (
    APPROVAL_REFUSALS,
    ApprovalFacts,
    V2QuoteRepository,
    check_approvable,
)
from origenlab_api.v2.quotes.draft import QUOTE_BODIES, DraftFxOverride, QuoteDraft
from origenlab_api.v2.quotes.inputs import (
    draft_from_rows,
    fx_overrides_from_row,
    load_parameters,
    resolve_draft,
)
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
    assert _revision(dsn, created["revision_id"])["fx_override_reasons"] == {
        "EUR": {"reason": "cotización proveedor", "clp_per_unit": "1100"}}
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


@needs_db
def test_an_override_for_a_currency_no_line_uses_keeps_its_rate(world) -> None:
    """A USD rate that only the DHL calculation would read is not in any line snapshot; the revision keeps it."""
    dsn = world["dsn"]
    created = _new_quote(world)
    override = {"currency": "USD", "clp_per_unit": "912.5", "reason": "tipo de cambio del agente"}
    _save(world, created, {"items": [_catalog_item(world)], "fx_overrides": [override]})
    stored = _revision(dsn, created["revision_id"])["fx_override_reasons"]
    assert stored == {"USD": {"reason": "tipo de cambio del agente", "clp_per_unit": "912.5"}}
    assert {line["cost_currency"] for line in _lines(dsn, created["revision_id"])} == {"EUR", "CLP"}
    assert fx_overrides_from_row(stored) == [DraftFxOverride(**override)]
    assert fx_overrides_from_row({}) == [] and fx_overrides_from_row(None) == []


# ------------------------------------------------------------------ which cost a line gets

class CountingCursor:
    """A cursor that counts the statements it runs."""

    def __init__(self, cur) -> None:
        self._cur, self.statements = cur, 0

    def execute(self, *args, **kwargs):
        self.statements += 1
        return self._cur.execute(*args, **kwargs)

    def __getattr__(self, name):
        return getattr(self._cur, name)


def _resolve(world: dict, items: list[dict], *, counter: list | None = None):
    draft = QuoteDraft(items=items)
    with psycopg.connect(runtime_dsn(world["dsn"])) as conn, conn.cursor() as raw:
        params = load_parameters(raw)
        cur = CountingCursor(raw)
        resolved = resolve_draft(cur, draft, params, fake_fx, TODAY)
        if counter is not None:
            counter.append(cur.statements)
        return resolved


def _observe(dsn: str, supplier: str, product: str, as_of: str, price: str, kind: str, *, stale: bool = False) -> str:
    return _owner(dsn, "insert into catalog.supplier_product (supplier_organization_id, product_id, as_of, price, "
                       "currency, price_kind, is_stale, provenance_note) values (%s, %s, %s::timestamptz, %s, 'EUR', "
                       "%s, %s, 'prueba') returning id::text", (supplier, product, as_of, price, kind, stale))[0][0]


@needs_db
def test_the_cost_a_catalog_line_gets(world) -> None:
    dsn = world["dsn"]
    maker = _org(dsn, "manufacturer", "Fabricante Ficticio")
    other_supplier = _org(dsn, "supplier", "Otro Proveedor Ficticio")
    survivor = _owner(dsn, "insert into catalog.product (manufacturer_organization_id, model_number, product_kind) "
                           "values (%s, 'OBS-1', 'equipment') returning id::text", (maker,))[0][0]
    twin = _owner(dsn, "insert into catalog.product (manufacturer_organization_id, model_number, product_kind, active, "
                       "merged_into_product_id) values (%s, 'OBS-1-DUP', 'equipment', false, %s) returning id::text",
                  (maker, survivor))[0][0]
    supplier = world["supplier"]

    def cost(**line):
        [item] = _resolve(world, [{"description": "Equipo", "quantity": "1", **line}]).items
        return item

    # The twin's observations are the survivor's; naming the twin resolves to the survivor.
    _observe(dsn, supplier, twin, "2026-10-01 13:00+00", "500", "dealer_net")
    assert (cost(product_id=survivor).unit_cost, cost(product_id=twin).product_id) == (Decimal("500"), survivor)
    # Same Santiago day: negotiated beats dealer_net, though recorded earlier; list and map come last.
    listed = _observe(dsn, supplier, survivor, "2026-10-01 16:00+00", "300", "list")
    _observe(dsn, supplier, survivor, "2026-10-01 17:00+00", "310", "map")
    _observe(dsn, supplier, survivor, "2026-10-01 14:00+00", "200", "dealer_net")
    negotiated = _observe(dsn, supplier, survivor, "2026-10-01 12:30+00", "150", "negotiated")
    item = cost(product_id=survivor)
    assert (item.unit_cost, item.cost_observation_id, item.supplier_organization_id) == (
        Decimal("150"), negotiated, supplier)
    # A newer stale row loses to an older live one.
    _observe(dsn, supplier, survivor, "2026-10-05 13:00+00", "1", "negotiated", stale=True)
    assert cost(product_id=survivor).unit_cost == Decimal("150")
    # A newer day beats a better kind.
    _observe(dsn, supplier, survivor, "2026-10-02 13:00+00", "180", "dealer_net")
    assert cost(product_id=survivor).unit_cost == Decimal("180")
    # A named supplier gets its own newest observation, even an older one; a named observation is that one.
    theirs = _observe(dsn, other_supplier, survivor, "2026-09-01 13:00+00", "999", "dealer_net")
    item = cost(product_id=survivor, supplier_organization_id=other_supplier)
    assert (item.unit_cost, item.cost_observation_id, item.supplier_organization_id) == (
        Decimal("999"), theirs, other_supplier)
    assert cost(product_id=survivor, cost_observation_id=listed).unit_cost == Decimal("300")
    # A named supplier with no observation leaves the line without a cost rather than borrowing another's.
    nobody = _org(dsn, "supplier", "Proveedor Sin Costos")
    item = cost(product_id=survivor, supplier_organization_id=nobody)
    assert (item.unit_cost, item.cost_currency, item.supplier_organization_id) == (None, None, nobody)


@needs_db
def test_a_draft_resolves_in_a_constant_number_of_statements(world) -> None:
    dsn = world["dsn"]
    maker = _org(dsn, "manufacturer", "Fabricante Ficticio")
    products = []
    for n in range(10):
        pid = _owner(dsn, "insert into catalog.product (manufacturer_organization_id, model_number, product_kind, "
                          "weight_kg, origin_country) values (%s, %s, 'accessory', 1, 'DE') returning id::text",
                     (maker, f"CNT-{n}"))[0][0]
        _observe(dsn, world["supplier"], pid, "2026-10-01 13:00+00", str(100 + n), "dealer_net")
        products.append(pid)
    counts: list[int] = []
    one = _resolve(world, [{"product_id": products[0], "description": "Accesorio", "quantity": "1"}], counter=counts)
    ten = _resolve(world, [{"product_id": pid, "description": "Accesorio", "quantity": "2"} for pid in products]
                   + [{"description": "Línea libre", "quantity": "1", "unit_cost": "5", "cost_currency": "USD"}],
                   counter=counts)
    assert len(one.items) == 1 and len(ten.items) == 11
    assert [i.unit_cost for i in ten.items[:10]] == [Decimal(100 + n) for n in range(10)]
    assert counts[0] == counts[1] <= 6


# ------------------------------------------------------------------ robustness carried from Task 7

@needs_db
def test_save_refuses_an_unknown_cost_observation(world) -> None:
    created = _new_quote(world)
    refused = _refused(world, "save-quote-draft", {
        "revision_id": created["revision_id"], "expected_version": 1,
        "draft": {"items": [_catalog_item(world, cost_observation_id=str(uuid.uuid4()))]}})
    assert (refused.status_code, refused.code) == (422, "cost_observation_not_found")
    assert _lines(world["dsn"], created["revision_id"]) == []


@needs_db
def test_save_refuses_an_amount_too_large_for_its_column(world) -> None:
    created = _new_quote(world)
    refused = _refused(world, "save-quote-draft", {
        "revision_id": created["revision_id"], "expected_version": 1,
        "draft": {"items": [_catalog_item(world, unit_cost="999999999999", cost_currency="EUR")]}})
    assert (refused.status_code, refused.code, refused.message) == (422, "amount_too_large",
                                                                   "Un monto es demasiado grande.")
    assert _lines(world["dsn"], created["revision_id"]) == []
    assert _revision(world["dsn"], created["revision_id"])["version"] == 1


# ------------------------------------------------------------------ draft_from_rows

def _accessory(world: dict, model: str) -> str:
    maker = _org(world["dsn"], "manufacturer", "Fabricante Ficticio")
    return _owner(world["dsn"], "insert into catalog.product (manufacturer_organization_id, model_number, name_es, "
                                "product_kind, weight_kg, origin_country, content_origin) values (%s, %s, "
                                "'Accesorio ficticio', 'accessory', 1, 'DE', 'operator') returning id::text",
                  (maker, model))[0][0]


@needs_db
def test_draft_from_rows_reprices_exactly_what_was_saved(world) -> None:
    dsn = world["dsn"]
    created = _new_quote(world)
    accessory = _accessory(world, f"RT-{uuid.uuid4().hex[:6]}")
    draft = {
        "items": [
            _catalog_item(world, quantity="2", line_note_es="Incluye manual"),
            {"product_id": accessory, "description": "Accesorio por precio", "quantity": "3",
             "margin_mode": "none", "typed_unit_price": "45000.4", "unit_cost": "20",
             "cost_currency": "USD", "supplier_organization_id": world["supplier"]},
            {"description": "Servicio ficticio", "quantity": "1", "item_type": "service",
             "unit_cost": "100000", "cost_currency": "CLP", "margin_mode": "margin", "margin_pct": "0.3"},
        ],
        "manual_expenses": [{"label": "Seguro ficticio", "amount": "15", "currency": "USD"}],
        "fx_overrides": [{"currency": "USD", "clp_per_unit": "950", "reason": "tipo de cambio del agente"}],
        "terms": {"payment_terms": "30 días", "warranty_terms_es": WARRANTY, "validity_days": 20,
                  "terms_extra": [{"label_es": "Capacitación", "text_es": "Una sesión"}]},
    }
    _save(world, created, draft)
    revision = _revision(dsn, created["revision_id"])
    rows = _lines(dsn, created["revision_id"])

    rebuilt = draft_from_rows(revision, rows)
    assert [e.label for e in rebuilt.manual_expenses] == ["Seguro ficticio"]  # profile lines are regenerated
    assert rebuilt.fx_overrides == [DraftFxOverride(currency="USD", clp_per_unit=Decimal("950"),
                                                    reason="tipo de cambio del agente")]
    assert rebuilt.terms.validity_days == 20 and rebuilt.terms.terms_extra[0].label_es == "Capacitación"
    assert rebuilt.recipient_participant_id is not None
    assert [i.margin_mode for i in rebuilt.items] == ["markup", "none", "margin"]

    with psycopg.connect(runtime_dsn(dsn)) as conn, conn.cursor() as cur:
        params = {**load_parameters(cur), "iva_rate": Decimal(revision["tax_rate"])}
        resolved = resolve_draft(cur, rebuilt, params, fake_fx, TODAY)
    result = compute(resolved.engine_input)
    assert (result.net, result.iva, result.total) == (revision["subtotal"], revision["tax_total"],
                                                      revision["grand_total"])
    items = [r for r in rows if r["line_kind"] == "item"]
    assert [line.unit_price for line in result.lines] == [r["unit_price_qc"] for r in items]
    assert len(resolved.expenses) == len(rows) - len(items)


# ------------------------------------------------------------------ check_approvable (pure)

def _facts(**over) -> ApprovalFacts:
    base = {"item_line_nos": (1,), "unpriced_line_nos": (), "expense_count": 0, "principal_line_no": None,
            "missing_fx": (), "has_institution": True, "has_recipient": True,
            "printed": (("Garantía", "Doce meses"),)}
    return ApprovalFacts(**{**base, **over})


def test_check_approvable_passes_a_complete_quote() -> None:
    assert check_approvable(_facts()) == []
    assert check_approvable(_facts(expense_count=2, principal_line_no=1)) == []


@pytest.mark.parametrize(("over", "expected"), [
    ({"item_line_nos": ()}, [("no_items", "Agrega al menos un producto.")]),
    ({"item_line_nos": (1, 2, 3), "unpriced_line_nos": (2, 3)},
     [("unpriced_line", "La línea 2 no tiene precio (falta costo o precio)."),
      ("unpriced_line", "La línea 3 no tiene precio (falta costo o precio).")]),
    ({"expense_count": 1}, [("principal_missing", "Hay gastos de importación pero ningún equipo principal; elige uno.")]),
    ({"missing_fx": ("EUR", "USD")}, [("fx_missing", "Falta el tipo de cambio de EUR."),
                                      ("fx_missing", "Falta el tipo de cambio de USD.")]),
    ({"has_institution": False}, [("no_institution", "El caso no tiene institución.")]),
    ({"has_recipient": False}, [("no_recipient", "Elige a quién va dirigida.")]),
    ({"printed": (("Garantía", "[GARANTÍA]"), ("Forma de pago", None), ("Capacitación", "Ver [detalle] aquí"))},
     [("placeholder_left", "Completa «Garantía»: aún tiene un texto entre corchetes."),
      ("placeholder_left", "Completa «Capacitación»: aún tiene un texto entre corchetes.")]),
])
def test_check_approvable_refusals(over, expected) -> None:
    assert check_approvable(_facts(**over)) == expected


def test_check_approvable_ignores_empty_brackets_and_lists_every_refusal_in_order() -> None:
    assert check_approvable(_facts(printed=(("Garantía", "sin [] nada"),))) == []
    codes = [code for code, _ in check_approvable(_facts(item_line_nos=(), expense_count=1, has_recipient=False))]
    assert codes == ["no_items", "principal_missing", "no_recipient"]
    assert set(APPROVAL_REFUSALS) == {"no_items", "unpriced_line", "principal_missing", "fx_missing",
                                      "no_institution", "no_recipient", "placeholder_left"}


# ------------------------------------------------------------------ approve-quote-revision

def _approve(world: dict, revision_id: str, version: int, **kw) -> dict:
    return _run(world, "approve-quote-revision", {"revision_id": revision_id, "expected_version": version}, **kw)


def _approve_refused(world: dict, revision_id: str, version: int, **kw) -> CommandRefused:
    return _refused(world, "approve-quote-revision", {"revision_id": revision_id, "expected_version": version}, **kw)


def _approvable(world: dict, **extra) -> tuple[dict, dict]:
    """A quote whose saved draft is approvable: the catalog equipment and a warranty."""
    created = _new_quote(world)
    draft = {"items": [_catalog_item(world)], "terms": {"warranty_terms_es": WARRANTY}, **extra}
    return created, _save(world, created, draft)


@needs_db
def test_approve_freezes_the_revision(world) -> None:
    dsn, operator = world["dsn"], world["operator"]
    institution = _org(dsn, "institution", "Instituto Ficticio")
    _owner(dsn, "insert into crm.external_identifier (scheme, value_norm, organization_id) values ('rut', %s, %s)",
           (f"7{uuid.uuid4().int % 10**7:07d}-K", institution))
    [(rut,)] = _owner(dsn, "select value_norm from crm.external_identifier where organization_id = %s", (institution,))
    case = _case(dsn, operator, institution)
    [participant] = _rows(dsn, "select id::text as id, person_id from crm.opportunity_participant "
                               "where opportunity_id = %s", (case,))
    _owner(dsn, "insert into crm.contact_point (kind, value_norm, value_display, person_id, usage, confirmation) "
                "values ('email', %s, %s, %s, 'personal', 'machine_proposed')",
           (f"persona-{uuid.uuid4().hex[:6]}@example.test", "persona@example.test", participant["person_id"]))
    [(email,)] = _owner(dsn, "select value_norm from crm.contact_point where person_id = %s", (participant["person_id"],))
    created = _run(world, "create-quote", {"opportunity_id": case})
    accessory = _accessory(world, f"NEG-{uuid.uuid4().hex[:6]}")
    negotiated = {"product_id": accessory, "description": "Accesorio negociado", "quantity": "2",
                  "unit_cost": "80", "cost_currency": "EUR", "supplier_organization_id": world["supplier"]}
    terms = {"warranty_terms_es": WARRANTY, "payment_terms": "30 días", "delivery_terms": "60 días",
             "terms_extra": [{"label_es": "Capacitación", "text_es": "Una sesión"}]}
    saved = _save(world, created, {"items": [_catalog_item(world), negotiated], "terms": terms})
    before = _revision(dsn, created["revision_id"])

    out = _approve(world, created["revision_id"], saved["version"])
    number = created["quote_number"]
    assert {k: out[k] for k in ("ok", "revision_id", "version", "quote_number", "revision_label")} == {
        "ok": True, "revision_id": created["revision_id"], "version": saved["version"] + 1,
        "quote_number": number, "revision_label": number}

    rev = _revision(dsn, created["revision_id"])
    assert (rev["status"], rev["version"], str(rev["approved_by_operator_id"])) == (
        "approved", saved["version"] + 1, operator.operator_id)
    assert rev["approved_at"] is not None and rev["totals_computed_at"] is not None
    assert rev["valid_until"] == date(2026, 11, 14)  # TODAY + the default 30 days
    assert (rev["subtotal"], rev["tax_total"], rev["grand_total"]) == (
        before["subtotal"], before["tax_total"], before["grand_total"])
    assert rev["party_snapshot_version"] == 1
    snapshot = rev["party_snapshot"]
    assert snapshot["institution"] == {"id": institution, "name": _rows(dsn, "select name from crm.organization "
                                                                              "where id = %s", (institution,))[0]["name"],
                                       "rut": rut}
    assert snapshot["recipient"] == {"participant_id": participant["id"], "name": "Persona Ficticia", "email": email}
    assert snapshot["terms"] == {"delivery_terms": "60 días", "payment_terms": "30 días", "warranty_terms_es": WARRANTY,
                                 "delivery_place_es": None, "installation_terms_es": None, "client_notes_es": None,
                                 "validity_days": 30,
                                 "terms_extra": [{"label_es": "Capacitación", "text_es": "Una sesión"}]}
    assert (snapshot["quote_number"], snapshot["revision_label"], snapshot["approved_by"]) == (
        number, number, "Vendedora Prueba")
    assert (snapshot["approved_at"], snapshot["valid_until"]) == ("2026-10-15", "2026-11-14")
    printed = json.dumps(snapshot)
    assert all(word not in printed for word in ("cost", "margin", "markup", "price", "unit"))

    assert _events(dsn, "quote_revision", created["revision_id"])[-1] == (
        "quote_revision.transitioned", {"from": "draft", "to": "approved"})
    [observation] = _rows(dsn, "select id::text as id, supplier_organization_id::text as supplier, price, currency, "
                               "price_kind, provenance_note, recorded_by_operator_id::text as operator "
                               "from catalog.supplier_product where product_id = %s", (accessory,))
    assert (observation["supplier"], observation["price"], observation["currency"], observation["price_kind"],
            observation["provenance_note"], observation["operator"]) == (
        world["supplier"], Decimal("80"), "EUR", "negotiated", f"Cotización {number}", operator.operator_id)
    assert _events(dsn, "product", accessory) == [("product.cost_recorded", {
        "supplier_product_id": observation["id"], "supplier_organization_id": world["supplier"],
        "price_kind": "negotiated", "currency": "EUR", "quote_revision_id": created["revision_id"]})]
    assert [str(r["cost_observation_id"]) for r in _lines(dsn, created["revision_id"])[:2]] == [
        world["observation"], observation["id"]]
    # The catalog line's cost came from an observation: nothing new is recorded for it.
    assert _owner(dsn, "select count(*) from catalog.supplier_product where product_id = %s and price_kind = "
                       "'negotiated'", (world["product"],)) == [(0,)]

    refused = _approve_refused(world, created["revision_id"], out["version"])
    assert (refused.status_code, refused.code) == (409, "not_a_draft")


@needs_db
def test_approve_with_a_stale_version_is_refused(world) -> None:
    created, saved = _approvable(world)
    refused = _approve_refused(world, created["revision_id"], saved["version"] - 1)
    assert (refused.status_code, refused.code) == (409, "stale_version")
    assert _revision(world["dsn"], created["revision_id"])["status"] == "draft"


@needs_db
def test_approve_refuses_a_quote_without_items(world) -> None:
    created = _new_quote(world)
    refused = _approve_refused(world, created["revision_id"], 1)
    assert (refused.status_code, refused.code, refused.message) == (409, "no_items", "Agrega al menos un producto.")
    rev = _revision(world["dsn"], created["revision_id"])
    assert (rev["status"], rev["version"], rev["party_snapshot"]) == ("draft", 1, None)


@needs_db
def test_approve_refuses_import_expenses_without_a_principal(world) -> None:
    created = _new_quote(world)
    accessory = {"description": "Accesorio importado", "quantity": "1", "item_type": "accessory",
                 "unit_cost": "100", "cost_currency": "EUR", "supplier_organization_id": world["supplier"]}
    saved = _save(world, created, {"items": [accessory], "terms": {"warranty_terms_es": WARRANTY}})
    assert len(_lines(world["dsn"], created["revision_id"])) == 5  # the profile's four expense lines
    refused = _approve_refused(world, created["revision_id"], saved["version"])
    assert (refused.status_code, refused.code, refused.message) == (
        409, "principal_missing", "Hay gastos de importación pero ningún equipo principal; elige uno.")


@needs_db
def test_approve_refuses_a_manual_expense_without_a_principal(world) -> None:
    created = _new_quote(world)
    local = {"description": "Insumo local", "quantity": "1", "item_type": "consumable",
             "unit_cost": "10000", "cost_currency": "CLP"}
    saved = _save(world, created, {"items": [local], "manual_expenses": [{"label": "Despacho", "amount": "5000"}],
                                   "terms": {"warranty_terms_es": WARRANTY}})
    refused = _approve_refused(world, created["revision_id"], saved["version"])
    assert refused.code == "principal_missing"


@needs_db
def test_approve_refuses_a_placeholder_left_in_a_printed_condition(world) -> None:
    created, saved = _approvable(world, terms={"warranty_terms_es": "[GARANTÍA]"})
    refused = _approve_refused(world, created["revision_id"], saved["version"])
    assert (refused.status_code, refused.code, refused.message) == (
        409, "placeholder_left", "Completa «Garantía»: aún tiene un texto entre corchetes.")


@needs_db
def test_approve_lists_every_refusal(world) -> None:
    created = _new_quote(world)
    saved = _save(world, created, {"items": [{"description": "Sin costo", "quantity": "1"}],
                                   "terms": {"payment_terms": "[FORMA DE PAGO]"}})
    refused = _approve_refused(world, created["revision_id"], saved["version"])
    assert refused.code == "unpriced_line"
    assert refused.message == ("La línea 1 no tiene precio (falta costo o precio). · "
                               "Completa «Forma de pago»: aún tiene un texto entre corchetes.")


@needs_db
def test_approve_prices_with_the_saved_rates_not_the_day_s(world) -> None:
    """Saved at EUR 1000; by approval the rate is 1100 (or gone): the frozen totals are the save's."""
    dsn = world["dsn"]
    created, saved = _approvable(world)
    before = _revision(dsn, created["revision_id"])
    lines = _lines(dsn, created["revision_id"])

    def dearer(currency: str, on: date) -> dict:
        return {**fake_fx(currency, on), "clp_per_unit": RATES[currency] + 100}

    out = _approve(world, created["revision_id"], saved["version"], fx=dearer)
    rev = _revision(dsn, created["revision_id"])
    assert (rev["subtotal"], rev["tax_total"], rev["grand_total"]) == (
        before["subtotal"], before["tax_total"], before["grand_total"])
    assert [(r["id"], r["fx_rate"], r["unit_price_qc"]) for r in _lines(dsn, created["revision_id"])] == [
        (r["id"], r["fx_rate"], r["unit_price_qc"]) for r in lines]  # the lines are kept, not rewritten
    created, saved = _approvable(world)
    assert _approve(world, created["revision_id"], saved["version"], fx=no_fx)["ok"] is True
    assert out["ok"] is True


@needs_db
def test_approve_does_not_look_a_missing_cost_up_again(world) -> None:
    """Saved with a supplier that had no cost; one recorded since does not price the line at approval."""
    dsn = world["dsn"]
    latecomer = _org(dsn, "supplier", "Proveedor Tardío")
    created = _new_quote(world)
    saved = _save(world, created, {"items": [_catalog_item(world, supplier_organization_id=latecomer)],
                                   "terms": {"warranty_terms_es": WARRANTY}})
    _observe(dsn, latecomer, world["product"], "2026-10-14 13:00+00", "900", "dealer_net")
    refused = _approve_refused(world, created["revision_id"], saved["version"])
    assert (refused.code, refused.message) == ("unpriced_line", "La línea 1 no tiene precio (falta costo o precio).")


@needs_db
def test_a_zero_expense_needs_no_principal(world) -> None:
    created = _new_quote(world)
    local = {"description": "Insumo local", "quantity": "1", "item_type": "consumable",
             "unit_cost": "10000", "cost_currency": "CLP"}
    saved = _save(world, created, {"items": [local], "manual_expenses": [{"label": "Despacho", "amount": "0"}],
                                   "terms": {"warranty_terms_es": WARRANTY}})
    assert _approve(world, created["revision_id"], saved["version"])["ok"] is True


@needs_db
def test_approve_refuses_a_placeholder_in_a_line_note(world) -> None:
    created, saved = _approvable(world, items=[_catalog_item(world, line_note_es="Incluye [ACCESORIOS]")])
    refused = _approve_refused(world, created["revision_id"], saved["version"])
    assert (refused.code, refused.message) == (
        "placeholder_left", "Completa «Nota de la línea 1»: aún tiene un texto entre corchetes.")
    created, saved = _approvable(world, items=[_catalog_item(world, description="Pipeta [10-100 µL]")])
    assert _approve(world, created["revision_id"], saved["version"])["ok"] is True


@needs_db
def test_approve_refuses_a_closed_case(world) -> None:
    created, saved = _approvable(world)
    _owner(world["dsn"], "update crm.opportunity set stage = 'lost', closed_at = now(), close_reason = 'precio' "
                         "where id = (select opportunity_id from crm.quote where id = %s)", (created["quote_id"],))
    refused = _approve_refused(world, created["revision_id"], saved["version"])
    assert (refused.status_code, refused.code) == (409, "case_closed")


@needs_db
def test_an_approved_revision_keeps_its_lines(world) -> None:
    created, saved = _approvable(world)
    _approve(world, created["revision_id"], saved["version"])
    with psycopg.connect(runtime_dsn(world["dsn"])) as conn, conn.cursor() as cur:
        with pytest.raises(psycopg.errors.CheckViolation):
            cur.execute("insert into crm.quote_line (quote_revision_id, line_no, line_kind, description, quantity) "
                        "values (%s, 9, 'item', 'Línea agregada', 1)", (created["revision_id"],))
    with psycopg.connect(runtime_dsn(world["dsn"])) as conn, conn.cursor() as cur:
        with pytest.raises(psycopg.errors.CheckViolation):
            cur.execute("update crm.quote_line set quantity = 5 where quote_revision_id = %s", (created["revision_id"],))


# ------------------------------------------------------------------ void-quote-revision

def _void(world: dict, revision_id: str, version: int, reason: str = "el cliente desistió") -> dict:
    return _run(world, "void-quote-revision", {"revision_id": revision_id, "expected_version": version,
                                               "reason": reason})


def _void_refused(world: dict, revision_id: str, version: int) -> CommandRefused:
    return _refused(world, "void-quote-revision", {"revision_id": revision_id, "expected_version": version,
                                                   "reason": "el cliente desistió"})


@needs_db
def test_void_a_draft(world) -> None:
    created = _new_quote(world)
    out = _void(world, created["revision_id"], 1)
    assert (out["ok"], out["version"]) == (True, 2)
    rev = _revision(world["dsn"], created["revision_id"])
    assert (rev["status"], rev["version"]) == ("void", 2)
    assert _events(world["dsn"], "quote_revision", created["revision_id"])[-1] == (
        "quote_revision.transitioned", {"from": "draft", "to": "void", "reason": "el cliente desistió"})
    assert _void_refused(world, created["revision_id"], 2).code == "already_void"


@needs_db
def test_void_an_approved_revision_but_not_a_sent_one(world) -> None:
    dsn = world["dsn"]
    created, saved = _approvable(world)
    approved = _approve(world, created["revision_id"], saved["version"])
    assert _void_refused(world, created["revision_id"], approved["version"] - 1).code == "stale_version"
    out = _void(world, created["revision_id"], approved["version"])
    assert _revision(dsn, created["revision_id"])["status"] == "void" and out["version"] == approved["version"] + 1
    assert _events(dsn, "quote_revision", created["revision_id"])[-1][1]["from"] == "approved"

    created, saved = _approvable(world)
    approved = _approve(world, created["revision_id"], saved["version"])
    mailbox = _owner(dsn, "insert into comms.mailbox (address_norm) values (%s) returning id",
                     (f"buzon-{uuid.uuid4().hex[:6]}@example.invalid",))[0][0]
    message = _owner(dsn, "insert into comms.message (mailbox_id, provider_message_id, direction, internal_date) "
                          "values (%s, %s, 'outbound', now()) returning id", (mailbox, uuid.uuid4().hex))[0][0]
    _owner(dsn, "update crm.quote_revision set status = 'sent', pdf_sha256 = %s, sent_at = now(), sent_message_id = %s "
                "where id = %s", ("a" * 64, message, created["revision_id"]))
    refused = _void_refused(world, created["revision_id"], approved["version"])
    assert (refused.status_code, refused.code, refused.message) == (
        409, "already_sent", "Ya se envió: crea una nueva revisión.")


@needs_db
def test_void_refuses_a_historical_revision(world) -> None:
    dsn = world["dsn"]
    case = _case(dsn, world["operator"], world["institution"])
    record = _owner(dsn, "insert into evidence.source_record (kind, dedupe_key, payload) values ('quote_document', "
                         "%s, '{}'::jsonb) returning id", (f"hist-{uuid.uuid4().hex}",))[0][0]
    quote = _owner(dsn, "insert into crm.quote (opportunity_id, quote_number, number_origin) values (%s, %s, "
                        "'printed_historical') returning id", (case, f"H{uuid.uuid4().hex[:8]}"))[0][0]
    revision = _owner(dsn, "insert into crm.quote_revision (quote_id, revision_no, status, origin, pdf_sha256, sent_at, "
                           "origin_source_record_id) values (%s, 1, 'sent', 'historical_import', %s, now(), %s) "
                           "returning id::text", (quote, "b" * 64, record))[0][0]
    refused = _void_refused(world, revision, 1)
    assert (refused.status_code, refused.code) == (409, "not_authored")


# ------------------------------------------------------------------ create-quote-revision

@needs_db
def test_a_new_revision_copies_the_issued_one_and_supersedes_it_on_approval(world) -> None:
    dsn = world["dsn"]
    created, saved = _approvable(world, terms={"warranty_terms_es": WARRANTY, "payment_terms": "30 días"})
    quote = {"quote_id": created["quote_id"]}
    refused = _refused(world, "create-quote-revision", quote)
    assert (refused.status_code, refused.code) == (409, "draft_open")  # revision 1 is still the draft
    _approve(world, created["revision_id"], saved["version"])

    opened = _run(world, "create-quote-revision", quote)
    number = created["quote_number"]
    label = f"{number[:5]}A-{number[-2:]}"
    assert (opened["ok"], opened["revision_no"], opened["version"], opened["revision_label"]) == (True, 2, 1, label)
    rev2 = _revision(dsn, opened["revision_id"])
    rev1 = _revision(dsn, created["revision_id"])
    assert (rev2["status"], rev2["origin"], rev2["party_snapshot"], rev2["approved_at"]) == (
        "draft", "authored", None, None)
    copied = ("payment_terms", "warranty_terms_es", "validity_days", "expense_method", "expense_allocation_mode",
              "principal_rule", "fx_override_reasons", "recipient_participant_id", "tax_rate", "terms_extra",
              "subtotal", "grand_total")
    assert {c: rev2[c] for c in copied} == {c: rev1[c] for c in copied}

    def content(rows):
        skip = {"id", "quote_revision_id", "created_at", "updated_at"}
        return [{k: v for k, v in r.items() if k not in skip} for r in rows]

    assert content(_lines(dsn, opened["revision_id"])) == content(_lines(dsn, created["revision_id"]))
    assert _events(dsn, "quote_revision", opened["revision_id"]) == [
        ("quote_revision.opened", {"quote_id": created["quote_id"], "revision_no": 2, "copied_from": 1})]
    refused = _refused(world, "create-quote-revision", quote)
    assert (refused.status_code, refused.code) == (409, "draft_open")

    again = _save(world, {"revision_id": opened["revision_id"], "version": 1},
                  {"items": [_catalog_item(world, quantity="2")], "terms": {"warranty_terms_es": WARRANTY}})
    out = _approve(world, opened["revision_id"], again["version"])
    assert (out["revision_label"], out["quote_number"]) == (label, number)
    assert _revision(dsn, opened["revision_id"])["party_snapshot"]["revision_label"] == label
    rev1 = _revision(dsn, created["revision_id"])
    assert (rev1["status"], rev1["superseded_by_revision_no"]) == ("approved", 2) and rev1["superseded_at"]
    assert _events(dsn, "quote_revision", created["revision_id"])[-1] == ("quote_revision.superseded", {
        "quote_id": created["quote_id"], "revision_no": 1, "superseded_by_revision_no": 2})


@needs_db
def test_create_quote_revision_needs_an_issued_revision(world) -> None:
    refused = _refused(world, "create-quote-revision", {"quote_id": str(uuid.uuid4())})
    assert (refused.status_code, refused.code) == (404, "quote_not_found")
    created = _new_quote(world)
    _void(world, created["revision_id"], 1)
    refused = _refused(world, "create-quote-revision", {"quote_id": created["quote_id"]})
    assert (refused.status_code, refused.code) == (409, "nothing_to_revise")


# ------------------------------------------------------------------ fix round 1

@needs_db
def test_negotiated_costs_reuse_an_identical_one_and_keep_two_different_ones(world) -> None:
    dsn = world["dsn"]
    accessory = _accessory(world, f"TWO-{uuid.uuid4().hex[:6]}")

    def typed(cost: str) -> dict:
        return {"product_id": accessory, "description": "Accesorio negociado", "quantity": "1",
                "unit_cost": cost, "cost_currency": "EUR", "supplier_organization_id": world["supplier"]}

    created, saved = _approvable(world, items=[_catalog_item(world), typed("70"), typed("75")])
    _approve(world, created["revision_id"], saved["version"])
    recorded = _rows(dsn, "select id::text as id, price from catalog.supplier_product where product_id = %s "
                          "and price_kind = 'negotiated' order by price", (accessory,))
    assert [r["price"] for r in recorded] == [Decimal("70"), Decimal("75")]
    assert [str(r["cost_observation_id"]) for r in _lines(dsn, created["revision_id"])[1:3]] == [
        r["id"] for r in recorded]

    # The same cost on another quote: the observation is reused, nothing new recorded.
    created, saved = _approvable(world, items=[_catalog_item(world), typed("70")])
    _approve(world, created["revision_id"], saved["version"])
    assert _owner(dsn, "select count(*) from catalog.supplier_product where product_id = %s", (accessory,)) == [(2,)]
    assert str(_lines(dsn, created["revision_id"])[1]["cost_observation_id"]) == recorded[0]["id"]
    assert len(_events(dsn, "product", accessory)) == 2


@needs_db
def test_negotiated_costs_skip_a_dead_supplier_and_follow_a_product_merge(world) -> None:
    """Between save and approval the supplier may be archived or merged, the product merged into a
    twin. Approval still succeeds: a dead supplier's cost is not recorded (the response says why),
    and a merged product's cost is recorded on the product it survives as."""
    dsn, operator = world["dsn"], world["operator"]
    archived, merged, survivor_supplier = (_org(dsn, "supplier", name) for name in (
        "Proveedor Archivado", "Proveedor Fusionado", "Proveedor Sobreviviente"))
    loser, survivor = (_accessory(world, f"MRG-{uuid.uuid4().hex[:6]}") for _ in range(2))

    def typed(product: str, supplier: str, cost: str) -> dict:
        return {"product_id": product, "description": "Accesorio negociado", "quantity": "1",
                "unit_cost": cost, "cost_currency": "EUR", "supplier_organization_id": supplier}

    created, saved = _approvable(world, items=[_catalog_item(world), typed(loser, world["supplier"], "61"),
                                               typed(survivor, archived, "62"), typed(survivor, merged, "63")])
    _owner(dsn, "update catalog.product set active = false, merged_into_product_id = %s where id = %s",
           (survivor, loser))
    _owner(dsn, "update crm.organization set status = 'archived', archived_at = now(), "
                "archived_by_operator_id = %s, archive_reason = 'duplicado' where id = %s",
           (operator.operator_id, archived))
    _owner(dsn, "update crm.organization set merged_into_organization_id = %s where id = %s",
           (survivor_supplier, merged))

    out = _approve(world, created["revision_id"], saved["version"])
    assert out["ok"] is True
    assert out["negotiated_costs_skipped"] == [
        {"line_no": 3, "reason": "supplier_archived"}, {"line_no": 4, "reason": "supplier_merged"}]
    [recorded] = _rows(dsn, "select id::text as id, product_id::text as product, price from catalog.supplier_product "
                            "where product_id in (%s, %s) and price_kind = 'negotiated'", (loser, survivor))
    assert (recorded["product"], recorded["price"]) == (survivor, Decimal("61"))
    assert _events(dsn, "product", loser) == []
    assert [e[0] for e in _events(dsn, "product", survivor)] == ["product.cost_recorded"]
    lines = _lines(dsn, created["revision_id"])
    assert [str(r["cost_observation_id"]) if r["cost_observation_id"] else None for r in lines[1:4]] == [
        recorded["id"], None, None]

    # Nothing to skip: the response says so with an empty list.
    created, saved = _approvable(world)
    assert _approve(world, created["revision_id"], saved["version"])["negotiated_costs_skipped"] == []


@needs_db
def test_approval_locks_the_quote_before_its_revisions(world) -> None:
    """The order every quote-wide command takes (create-quote-revision, «Elegir revisión vigente»):
    the quote row first, then its revisions — so approval and those never deadlock."""
    created, saved = _approvable(world)
    with psycopg.connect(world["dsn"]) as holder, holder.cursor() as cur:
        cur.execute("set role origenlab_owner")
        cur.execute("select 1 from crm.quote where id = %s for update", (created["quote_id"],))
        repo = V2QuoteRepository(psycopg.connect, runtime_dsn(world["dsn"]), fx_resolver=fake_fx, clock=lambda: NOW,
                                 statement_timeout_ms=300)
        body = QUOTE_BODIES["approve-quote-revision"](revision_id=created["revision_id"],
                                                      expected_version=saved["version"])
        with pytest.raises(psycopg.errors.QueryCanceled):
            repo.execute(command_name="approve-quote-revision", operator=world["operator"],
                         fields=body.model_dump(mode="json"), idempotency_key=uuid.uuid4().hex,
                         digest=request_digest("approve-quote-revision", body))
        holder.rollback()
    assert _approve(world, created["revision_id"], saved["version"])["ok"] is True


def _send(dsn: str, revision_id: str) -> None:
    """What the mail path will do: an approved revision becomes sent with its message (synthetic)."""
    mailbox = _owner(dsn, "insert into comms.mailbox (address_norm) values (%s) returning id",
                     (f"buzon-{uuid.uuid4().hex[:6]}@example.invalid",))[0][0]
    message = _owner(dsn, "insert into comms.message (mailbox_id, provider_message_id, direction, internal_date) "
                          "values (%s, %s, 'outbound', now()) returning id", (mailbox, uuid.uuid4().hex))[0][0]
    _owner(dsn, "update crm.quote_revision set status = 'sent', pdf_sha256 = %s, sent_at = now(), sent_message_id = %s "
                "where id = %s", (uuid.uuid4().hex * 2, message, revision_id))


@needs_db
def test_a_sent_revision_is_superseded_and_only_the_current_one_voided(world) -> None:
    dsn = world["dsn"]
    created, saved = _approvable(world)
    _approve(world, created["revision_id"], saved["version"])
    _send(dsn, created["revision_id"])
    opened = _run(world, "create-quote-revision", {"quote_id": created["quote_id"]})
    assert _events(dsn, "quote_revision", opened["revision_id"])[0][1]["copied_from"] == 1
    out = _approve(world, opened["revision_id"], 1)

    rev1 = _revision(dsn, created["revision_id"])
    assert (rev1["status"], rev1["superseded_by_revision_no"]) == ("sent", 2)
    refused = _void_refused(world, created["revision_id"], rev1["version"])
    assert (refused.status_code, refused.code) == (409, "already_sent")

    # Revision 3, approved: revision 2 is superseded and can no longer be voided; 3 can.
    third = _run(world, "create-quote-revision", {"quote_id": created["quote_id"]})
    assert third["revision_no"] == 3
    approved = _approve(world, third["revision_id"], 1)
    rev2 = _revision(dsn, opened["revision_id"])
    assert rev2["superseded_by_revision_no"] == 3 and _revision(dsn, created["revision_id"])["superseded_by_revision_no"] == 2
    refused = _void_refused(world, opened["revision_id"], rev2["version"])
    assert (refused.status_code, refused.code, refused.message) == (
        409, "superseded", "Ya fue reemplazada por la revisión 3.")
    assert out["version"] == 2
    voided = _void(world, third["revision_id"], approved["version"])
    assert _revision(dsn, third["revision_id"])["status"] == "void" and voided["ok"] is True


@needs_db
def test_an_issued_revision_is_frozen_for_the_api_role(world) -> None:
    created, saved = _approvable(world)
    _approve(world, created["revision_id"], saved["version"])
    for sql in ("update crm.quote_revision set grand_total = 1 where id = %s",
                "update crm.quote_revision set party_snapshot = '{}'::jsonb where id = %s",
                "update crm.quote_revision set payment_terms = 'otra' where id = %s"):
        with psycopg.connect(runtime_dsn(world["dsn"])) as conn, conn.cursor() as cur:
            with pytest.raises(psycopg.errors.CheckViolation, match="frozen"):
                cur.execute(sql, (created["revision_id"],))


def _round_trip(world: dict, draft: dict) -> tuple[QuoteDraft, dict, list[dict], object]:
    dsn = world["dsn"]
    created = _new_quote(world)
    _save(world, created, draft)
    revision = _revision(dsn, created["revision_id"])
    rows = _lines(dsn, created["revision_id"])
    rebuilt = draft_from_rows(revision, rows)
    with psycopg.connect(runtime_dsn(dsn)) as conn, conn.cursor() as cur:
        params = {**load_parameters(cur), "iva_rate": Decimal(revision["tax_rate"])}
        resolved = resolve_draft(cur, rebuilt, params, fake_fx, TODAY)
    result = compute(resolved.engine_input)
    assert (result.net, result.iva, result.total) == (revision["subtotal"], revision["tax_total"],
                                                      revision["grand_total"])
    return rebuilt, revision, rows, resolved


#: Invented DHL parameters (the calculation needs every one of them, else it falls back to the profile).
DHL = {"dhl_import_discount_pct": "0.5", "dhl_fuel_surcharge_pct": "0.2", "dhl_customs_clearance_fee_usd": "40",
       "courier_threshold_usd_fob": "3000", "customs_agent_fee_clp": "150000", "duty_rate_general": "0.06",
       "duty_rate_fta": "0", "insurance_pct_of_fob": "0.01", "domestic_transport_clp": "35000"}


@needs_db
def test_draft_from_rows_under_dhl_calc(world) -> None:
    for key, value in DHL.items():
        _param(world["dsn"], key, Decimal(value))
    rebuilt, _, rows, resolved = _round_trip(world, {"items": [_catalog_item(world)], "expense_method": "dhl_calc"})
    stored = [(r["description"], r["unit_cost"], r["cost_currency"], r["calc_inputs"])
              for r in rows if r["line_kind"] == "logistics"]
    assert stored and {r["expense_method"] for r in rows if r["line_kind"] == "logistics"} == {"dhl_calc"}
    assert rebuilt.manual_expenses == [] and rebuilt.expense_method == "dhl_calc"
    assert [(e.label, e.amount, e.currency, e.calc_inputs) for e in resolved.expenses] == stored


@needs_db
def test_draft_from_rows_under_the_manual_principal_rule(world) -> None:
    accessory = {"description": "Accesorio principal", "quantity": "1", "item_type": "accessory",
                 "unit_cost": "50", "cost_currency": "EUR", "supplier_organization_id": world["supplier"],
                 "is_principal": True}
    rebuilt, _, rows, _ = _round_trip(world, {"items": [_catalog_item(world), accessory],
                                              "principal_rule": "manual"})
    assert [i.is_principal for i in rebuilt.items] == [False, True]
    assert [r["is_principal"] for r in rows if r["line_kind"] == "item"] == [False, True]
    assert {r["allocated_to_line_no"] for r in rows if r["line_kind"] == "logistics"} == {2}
