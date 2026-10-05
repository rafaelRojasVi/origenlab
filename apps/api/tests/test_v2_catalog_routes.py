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

    def product_detail(self, product_id):
        if str(product_id) == "00000000-0000-4000-8000-0000000000aa":
            return {"id": str(product_id), "cost_history": [{"price": "90"}], "name_es": "X"}
        return None

    def supplier_terms(self, organization_id):
        return None

    def parameters(self):
        return {"current": {}, "history": []}


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
            "/v2/catalog/suppliers/{organization_id}/terms"} <= set(app.openapi()["paths"])


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
