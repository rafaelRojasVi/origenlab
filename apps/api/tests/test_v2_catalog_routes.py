"""Catalog read routes, in process, behind the production error envelope. No database."""
from __future__ import annotations

import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from origenlab_api.commercial_operator_identity import OPERATOR_EMAIL_HEADER
from origenlab_api.errors import register_exception_handlers
from origenlab_api.v2.catalog.routes import catalog_read_router
from origenlab_api.v2.identity import LocalDevIdentity, OperatorIdentity, OperatorLookup

LOOPBACK = "postgresql://origenlab_api:pw@127.0.0.1:54332/origenlab_dev"
HEADERS = {OPERATOR_EMAIL_HEADER: "op@example.test"}


class _Lookup(OperatorLookup):
    def __init__(self, operator):
        self._operator = operator

    def by_email(self, email_norm):
        return self._operator


class _FakeReads:
    def __init__(self):
        self.calls = []

    def search_products(self, *args):
        self.calls.append(("search", args))
        return {"items": [{"id": "p1", "name_es": "Sonicador",
                           "current_cost": {"price": "100.000000", "currency": "EUR"}}],
                "total": 1, "limit": args[3], "offset": args[4]}

    def product_detail(self, product_id, include_hidden=False):
        if str(product_id) == "00000000-0000-4000-8000-0000000000aa":
            return {"id": str(product_id), "cost_history": [{"price": "90"}], "name_es": "X"}
        return None

    def supplier_terms(self, organization_id):
        return None

    def suppliers(self):
        return {"items": [{"id": "s1", "display_name": "Soviquim", "products": 2,
                           "by_kind": {"equipment": 1, "consumable": 1}}]}

    def price_history(self, model_key, limit=20):
        self.calls.append(("history", (model_key, limit)))
        return {"model_key": model_key, "items": [{"line_total": "1100.0000", "unit_price": "1100.0000"}],
                "median_last_5": "1100.0000"}

    def parameters(self):
        return {"current": {}, "history": []}

    def catalog_index(self, known_version=None):
        self.calls.append(("index", known_version))
        if known_version == "v1":
            return {"version": "v1", "unchanged": True}
        return {"version": "v1", "fields": ["id", "name"], "suppliers": [], "items": [["p1", "Sonicador"]]}


def _client(role="sales"):
    app = FastAPI()
    register_exception_handlers(app)  # the envelope production serves, not FastAPI's default
    app.include_router(catalog_read_router)
    app.state.catalog_reads = _FakeReads()
    operator = OperatorIdentity(operator_id="00000000-0000-4000-8000-000000000001", email_norm="op@example.test",
                                display_name="Op", role=role, status="active")
    app.state.v2_identity = LocalDevIdentity(LOOPBACK, _Lookup(operator))
    return TestClient(app)


def test_switch_off_mounts_nothing() -> None:
    from origenlab_api import main
    from origenlab_api.settings import Settings

    for kw in ({}, {"v2_quoting_enabled": True}, {"v2_database_url": LOOPBACK}):
        app = FastAPI()
        main._mount_catalog(app, Settings(_env_file=None, **kw), LOOPBACK, lambda *a, **k: 1 / 0)
        assert app.state.quoting_enabled is False
        assert "/v2/catalog/products" not in set(app.openapi()["paths"])
    app = FastAPI()
    main._mount_catalog(app, Settings(_env_file=None, v2_database_url=LOOPBACK, v2_quoting_enabled=True),
                        LOOPBACK, lambda *a, **k: 1 / 0)
    assert app.state.quoting_enabled is True
    assert {"/v2/catalog/products", "/v2/catalog/products/{product_id}", "/v2/catalog/parameters",
            "/v2/catalog/suppliers", "/v2/catalog/suppliers/{organization_id}/terms"} <= set(app.openapi()["paths"])


def test_unmounted_path_is_404() -> None:
    app = FastAPI()
    register_exception_handlers(app)
    assert TestClient(app).get("/v2/catalog/products", headers=HEADERS).status_code == 404


def test_sales_sees_costs_viewer_does_not() -> None:
    sales = _client("sales").get("/v2/catalog/products", headers=HEADERS).json()
    assert sales["items"][0]["current_cost"]["price"] == "100.000000"
    viewer = _client("viewer").get("/v2/catalog/products", headers=HEADERS).json()
    assert viewer["items"][0]["current_cost"] == {"currency": "EUR"}


def test_viewer_detail_loses_cost_history() -> None:
    pid = "00000000-0000-4000-8000-0000000000aa"
    assert "cost_history" in _client("admin").get(f"/v2/catalog/products/{pid}", headers=HEADERS).json()
    assert "cost_history" not in _client("viewer").get(f"/v2/catalog/products/{pid}", headers=HEADERS).json()


def test_unknown_kind_is_422_in_the_production_envelope() -> None:
    r = _client().get("/v2/catalog/products?kind=widget", headers=HEADERS)
    assert r.status_code == 422
    err = r.json()["error"]
    assert err["code"] == "validation_error" and err["details"]["code"] == "unknown_product_kind"


def test_bad_paging_is_422_envelope() -> None:
    r = _client().get("/v2/catalog/products?limit=0", headers=HEADERS)
    assert r.status_code == 422 and r.json()["error"]["code"] == "validation_error"
    assert r.json()["error"]["details"]["validation_errors"]


def test_suppliers_list() -> None:
    r = _client().get("/v2/catalog/suppliers", headers=HEADERS)
    assert r.status_code == 200
    assert r.json()["items"][0]["display_name"] == "Soviquim" and r.json()["items"][0]["products"] == 2


def test_missing_product_and_terms_are_404_in_the_production_envelope() -> None:
    client = _client()
    r = client.get(f"/v2/catalog/products/{uuid.uuid4()}", headers=HEADERS)
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "not_found" and r.json()["error"]["details"]["code"] == "product_not_found"
    r = client.get(f"/v2/catalog/suppliers/{uuid.uuid4()}/terms", headers=HEADERS)
    assert r.status_code == 404 and r.json()["error"]["details"]["code"] == "supplier_terms_not_found"


def test_unauthenticated_is_refused() -> None:
    assert _client().get("/v2/catalog/parameters").status_code == 401


def test_parameters_are_forbidden_to_viewer_in_the_production_envelope() -> None:
    r = _client("viewer").get("/v2/catalog/parameters", headers=HEADERS)
    assert r.status_code == 403
    err = r.json()["error"]
    assert err["code"] == "forbidden" and err["details"]["code"] == "role_may_not_view_costs"


@pytest.mark.parametrize("role", ["sales", "admin"])
def test_parameters_readable_by_sales_and_admin(role) -> None:
    r = _client(role).get("/v2/catalog/parameters", headers=HEADERS)
    assert r.status_code == 200 and r.json() == {"current": {}, "history": []}


# ──────────────────────────────────────────────────────── command routes ──

from origenlab_api.v2.catalog.routes import catalog_command_router  # noqa: E402
from origenlab_api.v2.commands import CommandRefused  # noqa: E402

PID = "00000000-0000-4000-8000-0000000000aa"
ORG = "00000000-0000-4000-8000-0000000000bb"
COMMAND_BODIES = {
    "create-product": {"manufacturer_organization_id": ORG, "model_number": "SONIC-100", "weight_kg": "1.5"},
    "update-product": {"product_id": PID, "expected_version": 1, "name_es": "Sonicador"},
    "confirm-product-content": {"product_id": PID, "expected_version": 1},
    "record-supplier-cost": {"product_id": PID, "supplier_organization_id": ORG, "price": "10.5", "currency": "EUR",
                             "price_kind": "dealer_net", "as_of": "2026-09-01T00:00:00+00:00"},
    "set-supplier-terms": {"supplier_organization_id": ORG, "currency": "EUR", "route": "import_courier"},
    "set-cost-parameter": {"key": "iva_rate", "value": "0.19", "reason": "sintético"},
    "record-fx-rate": {"currency": "USD", "rate_date": "2026-09-01", "clp_per_unit": "900", "reason": "sintético"},
    "review-document-line": {"document_line_id": PID, "review_note": "revisado"},
    "update-product-image": {"image_id": PID, "expected_version": 1, "status": "hidden"},
}


class _FakeRepo:
    def __init__(self, refusal: CommandRefused | None = None):
        self.calls: list[dict] = []
        self._refusal = refusal

    def execute(self, **kwargs):
        self.calls.append(kwargs)
        if self._refusal is not None:
            raise self._refusal
        return {"ok": True, "replayed": False}


def _command_client(role="sales", repo=None):
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(catalog_command_router)
    app.state.catalog_repository = repo or _FakeRepo()
    operator = OperatorIdentity(operator_id="00000000-0000-4000-8000-000000000001", email_norm="op@example.test",
                                display_name="Op", role=role, status="active")
    app.state.v2_identity = LocalDevIdentity(LOOPBACK, _Lookup(operator))
    return app.state.catalog_repository, TestClient(app)


def _ikey():
    return {**HEADERS, "Idempotency-Key": uuid.uuid4().hex}


def test_the_command_router_exposes_exactly_the_ten_catalog_commands() -> None:
    # add-product-image is multipart; its route tests live in test_v2_catalog_images.py.
    paths = {r.path for r in catalog_command_router.routes}
    assert paths == {f"/v2/commands/{name}" for name in [*COMMAND_BODIES, "add-product-image"]}


@pytest.mark.parametrize("command", sorted(COMMAND_BODIES))
def test_viewer_cannot_command(command) -> None:
    repo, client = _command_client("viewer")
    r = client.post(f"/v2/commands/{command}", json=COMMAND_BODIES[command], headers=_ikey())
    assert r.status_code == 403
    err = r.json()["error"]
    assert err["code"] == "forbidden" and err["details"]["code"] == "role_may_not_decide"
    assert repo.calls == []


@pytest.mark.parametrize("role", ["sales", "admin"])
@pytest.mark.parametrize("command", sorted(COMMAND_BODIES))
def test_sales_and_admin_reach_the_handler_with_a_json_ready_body(command, role) -> None:
    repo, client = _command_client(role)
    r = client.post(f"/v2/commands/{command}", json=COMMAND_BODIES[command], headers=_ikey())
    assert r.status_code == 200, r.text
    [call] = repo.calls
    assert call["command_name"] == command and call["operator"].role == role
    # Decimal, UUID and date travel as strings, exactly what the handlers bind.
    import json as _json

    _json.dumps(call["fields"])
    if command == "create-product":
        assert call["fields"]["weight_kg"] == "1.5" and call["fields"]["manufacturer_organization_id"] == ORG


def test_unauthenticated_command_is_401() -> None:
    _, client = _command_client()
    r = client.post("/v2/commands/create-product", json=COMMAND_BODIES["create-product"])
    assert r.status_code == 401 and r.json()["error"]["code"] == "unauthorized"


def test_missing_idempotency_key_is_400_in_the_production_envelope() -> None:
    repo, client = _command_client()
    r = client.post("/v2/commands/create-product", json=COMMAND_BODIES["create-product"], headers=HEADERS)
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "validation_error"
    assert repo.calls == []


@pytest.mark.parametrize(("status", "code", "envelope"), [
    (409, "duplicate_model", "conflict"), (409, "stale_version", "conflict"),
    (409, "duplicate_observation", "conflict"), (409, "parameter_changed", "conflict"),
    (404, "product_not_found", "not_found"), (422, "value_out_of_range", "validation_error"),
])
def test_refusals_surface_in_the_production_envelope(status, code, envelope) -> None:
    _, client = _command_client(repo=_FakeRepo(CommandRefused(status, code, "refused")))
    r = client.post("/v2/commands/create-product", json=COMMAND_BODIES["create-product"], headers=_ikey())
    assert r.status_code == status
    err = r.json()["error"]
    assert err["code"] == envelope and err["details"]["code"] == code


@pytest.mark.parametrize(("command", "patch"), [
    ("create-product", {"surprise": 1}),
    ("create-product", {"model_number": "   "}),
    ("create-product", {"origin_country": "chile"}),
    ("create-product", {"weight_kg": "-1"}),
    ("create-product", {"product_kind": "widget"}),
    ("create-product", {"specs": [{"label_es": "Potencia", "value": "1", "extra": "x"}]}),
    ("create-product", {"model_number": "-./ _"}),
    ("update-product", {"model_number": "--"}),
    ("record-supplier-cost", {"as_of": "2026-09-01T00:00:00"}),
    ("record-supplier-cost", {"discount_pct": "1"}),
    ("record-supplier-cost", {"currency": "GBP"}),
    ("record-supplier-cost", {"price": "NaN"}),
    ("set-supplier-terms", {"packing_pct": "0.6"}),
    ("set-cost-parameter", {"key": "secret_markup"}),
    ("set-cost-parameter", {"reason": "  "}),
    ("set-cost-parameter", {"value": "-0.1"}),
    ("set-cost-parameter", {"value_text": "texto"}),                       # both value and value_text
    ("set-cost-parameter", {"key": "quote_default_warranty"}),             # a text key with a number
    ("record-fx-rate", {"reason": "   "}),
    ("record-fx-rate", {"currency": "CLP"}),
    ("record-fx-rate", {"clp_per_unit": "0"}),
    ("review-document-line", {"review_note": "ok"}),
    ("review-document-line", {"qty": "0"}),
])
def test_bad_bodies_are_422_in_the_production_envelope(command, patch) -> None:
    repo, client = _command_client()
    r = client.post(f"/v2/commands/{command}", json={**COMMAND_BODIES[command], **patch}, headers=_ikey())
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "validation_error"
    assert repo.calls == []


def test_set_cost_parameter_takes_text_for_the_quote_defaults() -> None:
    repo, client = _command_client()
    body = {"key": "quote_default_warranty", "value_text": "[GARANTÍA]", "reason": "sintético"}
    r = client.post("/v2/commands/set-cost-parameter", json=body, headers=_ikey())
    assert r.status_code == 200, r.text
    assert repo.calls[0]["fields"]["value_text"] == "[GARANTÍA]" and repo.calls[0]["fields"]["value"] is None


def test_update_product_leaves_out_what_was_not_sent() -> None:
    repo, client = _command_client()
    client.post("/v2/commands/update-product", json=COMMAND_BODIES["update-product"], headers=_ikey())
    fields = repo.calls[0]["fields"]
    # Defaults that would overwrite stored data are absent, not False or [].
    assert fields["specs"] is None and fields["is_dangerous_goods"] is None and fields["active"] is None


def test_switch_on_mounts_the_command_routes() -> None:
    from origenlab_api import main
    from origenlab_api.settings import Settings

    app = FastAPI()
    main._mount_catalog(app, Settings(_env_file=None, v2_database_url=LOOPBACK, v2_quoting_enabled=True),
                        LOOPBACK, lambda *a, **k: 1 / 0)
    assert {f"/v2/commands/{name}" for name in COMMAND_BODIES} <= set(app.openapi()["paths"])
    assert app.state.catalog_repository is not None
    app = FastAPI()
    main._mount_catalog(app, Settings(_env_file=None, v2_database_url=LOOPBACK), LOOPBACK, lambda *a, **k: 1 / 0)
    assert "/v2/commands/create-product" not in set(app.openapi()["paths"])


def test_add_note_accepts_a_product_subject_and_keeps_the_others() -> None:
    from pydantic import ValidationError

    from origenlab_api.v2.crm_authoring_routes import AddNoteBody

    for kind in ("person", "organization", "opportunity", "product"):
        assert AddNoteBody(subject_kind=kind, subject_id=PID, body="nota").subject_kind == kind
    with pytest.raises(ValidationError):
        AddNoteBody(subject_kind="quote", subject_id=PID, body="nota")


def test_catalog_commands_module_never_deletes() -> None:
    import pathlib

    import origenlab_api.v2.catalog.commands as module

    assert "delete from" not in pathlib.Path(module.__file__).read_text(encoding="utf-8").lower()


def test_a_blank_spec_unit_is_none() -> None:
    from origenlab_api.v2.catalog.routes import CreateProductBody

    body = CreateProductBody(manufacturer_organization_id=ORG, model_number="X1",
                             specs=[{"label_es": "Potencia", "value": "1", "unit": "   "},
                                    {"label_es": "Peso", "value": "2", "unit": " kg "}])
    assert [s.unit for s in body.specs] == [None, "kg"]


def test_the_review_guard_refusal_is_line_not_disputed_not_500() -> None:
    import psycopg

    from origenlab_api.v2.catalog.commands import _execute_mapped

    class _Cur:
        def execute(self, *_):
            raise psycopg.errors.CheckViolation("document_line_review_guard: only a disputed line may become reviewed")

    with pytest.raises(CommandRefused) as exc:
        _execute_mapped(_Cur(), "update …", (), unique={},
                        check=("line_not_disputed", "only a disputed line can be reviewed"))
    assert (exc.value.status_code, exc.value.code) == (409, "line_not_disputed")
    with pytest.raises(psycopg.errors.CheckViolation):  # unmapped: a defect, not an answer
        _execute_mapped(_Cur(), "update …", (), unique={})


def test_price_history_is_visible_to_every_role_unredacted() -> None:
    for role in ("viewer", "sales", "admin"):
        r = _client(role).get("/v2/catalog/price-history?model_key=SONIC100", headers=HEADERS)
        assert r.status_code == 200
        assert r.json()["items"] == [{"line_total": "1100.0000", "unit_price": "1100.0000"}]


def test_price_history_normalizes_the_key_and_defaults_the_limit() -> None:
    client = _client()
    for raw in ("sonic-100", "SONIC 100", "sonic_100"):
        assert client.get("/v2/catalog/price-history", params={"model_key": raw}, headers=HEADERS).json()["model_key"] == "SONIC100"
    assert client.app.state.catalog_reads.calls[-1] == ("history", ("SONIC100", 20))


@pytest.mark.parametrize("query", ["model_key=", "model_key=%20-_./%20", "", "model_key=X&limit=0",
                                   "model_key=X&limit=101"])
def test_price_history_bad_input_is_422_in_the_production_envelope(query) -> None:
    r = _client().get(f"/v2/catalog/price-history?{query}", headers=HEADERS)
    assert r.status_code == 422 and r.json()["error"]["code"] == "validation_error"


def test_price_history_blank_key_names_its_code() -> None:
    r = _client().get("/v2/catalog/price-history?model_key=%20-_./", headers=HEADERS)
    assert r.json()["error"]["details"]["code"] == "invalid_model_key"


def test_price_history_limit_bounds_are_accepted() -> None:
    client = _client()
    for limit in (1, 100):
        assert client.get(f"/v2/catalog/price-history?model_key=X&limit={limit}", headers=HEADERS).status_code == 200


def test_price_history_unauthenticated_is_refused() -> None:
    assert _client().get("/v2/catalog/price-history?model_key=X").status_code == 401


def test_index_passes_the_known_version_and_is_not_cached_blindly() -> None:
    client = _client("viewer")
    r = client.get("/v2/catalog/index", headers=HEADERS)
    assert r.status_code == 200 and r.json()["items"] == [["p1", "Sonicador"]]
    assert "no-cache" in r.headers["cache-control"] and "private" in r.headers["cache-control"]
    assert client.get("/v2/catalog/index", params={"version": "v1"}, headers=HEADERS).json() == {
        "version": "v1", "unchanged": True}


def test_viewer_detail_loses_price_lists_but_keeps_datasheets() -> None:
    class _Reads(_FakeReads):
        def product_detail(self, product_id, include_hidden=False):
            return {"id": str(product_id), "documents": {"datasheets": [{"url": "d"}], "price_lists": [{"url": "p"}]}}

    pid = "00000000-0000-4000-8000-0000000000aa"
    for role, expected in (("viewer", {"datasheets": [{"url": "d"}]}),
                           ("sales", {"datasheets": [{"url": "d"}], "price_lists": [{"url": "p"}]})):
        client = _client(role)
        client.app.state.catalog_reads = _Reads()
        assert client.get(f"/v2/catalog/products/{pid}", headers=HEADERS).json()["documents"] == expected


class _MarginReads(_FakeReads):
    def price_history(self, model_key, limit=20):
        return {"model_key": model_key, "median_last_5": None, "summary": {"times_quoted": 1},
                "items": [{"date": "2026-09-17", "line_total": "7000000.0000", "unit_price": "7000000.0000",
                           "qty": "1", "currency": "CLP", "document_number": "09201-26"}],
                "costs": [{"as_of": "2026-01-01T00:00:00+00:00", "price": "2500.000000", "currency": "EUR",
                           "price_kind": "dealer_net", "is_stale": False}]}


class _FakeFx:
    def __init__(self):
        self.asked = []

    def rates_for(self, currency, days, *, fetch=True):
        from datetime import date
        from decimal import Decimal

        assert fetch is False, "the panel never waits on a rate provider"
        self.asked.append((currency, sorted(days)))
        return {d: {"clp_per_unit": Decimal("1100"), "as_of": date(2026, 9, 16)} for d in days}


def _margin_client(role):
    client = _client(role)
    client.app.state.catalog_reads = _MarginReads()
    client.app.state.catalog_fx = _FakeFx()
    return client


def test_price_history_carries_cost_and_margin_for_sales() -> None:
    client = _margin_client("sales")
    body = client.get("/v2/catalog/price-history", params={"model_key": "SYN1"}, headers=HEADERS).json()
    [line] = body["items"]
    assert line["margin"] == "0.6071" and line["cost"]["clp"] == "2750000" and line["cost"]["fx_as_of"] == "2026-09-16"
    assert body["summary"]["margin"]["last"] == "0.6071" and "costs" not in body


def test_a_viewer_sees_prices_but_no_cost_and_no_margin_and_no_rate_is_fetched() -> None:
    client = _margin_client("viewer")
    body = client.get("/v2/catalog/price-history", params={"model_key": "SYN1"}, headers=HEADERS).json()
    [line] = body["items"]
    assert line["line_total"] == "7000000.0000"
    assert "cost" not in line and "margin" not in line and "costs" not in body and "margin" not in body["summary"]
    assert client.app.state.catalog_fx.asked == []


class _DetailMarginReads(_MarginReads):
    def product_detail(self, product_id, include_hidden=False):
        return {"id": str(product_id), "model_key": "SYN1", "price_history": self.price_history("SYN1")}


def test_the_product_carries_its_margins_so_the_panel_needs_one_request() -> None:
    pid = "00000000-0000-4000-8000-0000000000ab"
    client = _client("sales")
    client.app.state.catalog_reads = _DetailMarginReads()
    client.app.state.catalog_fx = _FakeFx()
    history = client.get(f"/v2/catalog/products/{pid}", headers=HEADERS).json()["price_history"]
    assert history["items"][0]["margin"] == "0.6071" and history["summary"]["margin"]["last"] == "0.6071"
    assert "costs" not in history

    viewer = _client("viewer")
    viewer.app.state.catalog_reads = _DetailMarginReads()
    viewer.app.state.catalog_fx = _FakeFx()
    history = viewer.get(f"/v2/catalog/products/{pid}", headers=HEADERS).json()["price_history"]
    assert "margin" not in history["items"][0] and "costs" not in history and "margin" not in history["summary"]
    assert viewer.app.state.catalog_fx.asked == []
