"""Quote routes in process, behind the production error envelope, with fake reads and repository. No database."""
from __future__ import annotations

import datetime as dt
import json
import uuid
from decimal import Decimal

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from origenlab_api.commercial_operator_identity import OPERATOR_EMAIL_HEADER
from origenlab_api.errors import register_exception_handlers
from origenlab_api.v2.commands import CommandRefused
from origenlab_api.v2.identity import LocalDevIdentity, OperatorIdentity, OperatorLookup
from origenlab_api.v2.quotes.routes import quote_command_router, quote_read_router

LOOPBACK = "postgresql://origenlab_api:pw@127.0.0.1:54332/origenlab_dev"
HEADERS = {OPERATOR_EMAIL_HEADER: "op@example.test"}
QID = "00000000-0000-4000-8000-0000000000aa"
RID = "00000000-0000-4000-8000-0000000000bb"
CASE = "00000000-0000-4000-8000-0000000000cc"
DRAFT = {"items": [{"description": "Línea libre", "quantity": "1", "unit_cost": "100", "cost_currency": "CLP"}]}

COMMAND_BODIES = {
    "create-quote": {"opportunity_id": CASE},
    "save-quote-draft": {"revision_id": RID, "expected_version": 1, "draft": DRAFT},
    "approve-quote-revision": {"revision_id": RID, "expected_version": 2},
    "void-quote-revision": {"revision_id": RID, "expected_version": 2, "reason": "duplicada"},
    "create-quote-revision": {"quote_id": QID},
}


class _Lookup(OperatorLookup):
    def __init__(self, operator):
        self._operator = operator

    def by_email(self, email_norm):
        return self._operator


class _FakeReads:
    def __init__(self, refusal: CommandRefused | None = None):
        self.calls: list[tuple] = []
        self._refusal = refusal

    def _maybe_refuse(self):
        if self._refusal is not None:
            raise self._refusal

    def workspace(self, quote_id):
        self.calls.append(("workspace", str(quote_id)))
        self._maybe_refuse()
        if str(quote_id) != QID:
            return None
        return {"quote": {"id": QID, "quote_number": "01300-26"}, "price_history": None}

    def numbering(self):
        self.calls.append(("numbering",))
        self._maybe_refuse()
        return {"configured": True, "next_number": "01300-26"}

    def preview(self, body):
        self.calls.append(("preview", body))
        self._maybe_refuse()
        return {"computed": {"totals": {"net": "140"}}}

    def quote_list(self, *, status, q, limit, cursor):
        self.calls.append(("quote_list", status, q, limit, cursor))
        self._maybe_refuse()
        return {"rows": [], "counts": {"draft": 0, "approved": 0, "sent": 0, "void": 0, "all": 0}, "next_cursor": None}

    def client_suggestions(self, q):
        self.calls.append(("client_suggestions", q))
        self._maybe_refuse()
        return {"organizations": [], "people": []}


class _FakeRepo:
    def __init__(self, refusal: CommandRefused | None = None):
        self.calls: list[dict] = []
        self._refusal = refusal

    def execute(self, **kwargs):
        self.calls.append(kwargs)
        if self._refusal is not None:
            raise self._refusal
        return {"ok": True, "replayed": False}


def _client(role="sales", reads=None, repo=None):
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(quote_read_router)
    app.include_router(quote_command_router)
    app.state.quote_reads = reads or _FakeReads()
    app.state.quote_repository = repo or _FakeRepo()
    operator = OperatorIdentity(operator_id="00000000-0000-4000-8000-000000000001", email_norm="op@example.test",
                                display_name="Op", role=role, status="active")
    app.state.v2_identity = LocalDevIdentity(LOOPBACK, _Lookup(operator))
    return app.state.quote_reads, app.state.quote_repository, TestClient(app)


def _ikey():
    return {**HEADERS, "Idempotency-Key": uuid.uuid4().hex}


READS = [("get", "/v2/quotes/numbering", None), ("get", f"/v2/quotes/{QID}", None),
         ("post", "/v2/quotes/preview", {"draft": DRAFT}), ("get", "/v2/quotes", None),
         ("get", "/v2/quotes/clients?q=ab", None)]


# ------------------------------------------------------------------ reads

@pytest.mark.parametrize(("method", "path", "body"), READS)
def test_viewer_gets_no_quote_read(method, path, body) -> None:
    reads, _, client = _client("viewer")
    r = client.request(method, path, json=body, headers=HEADERS)
    assert r.status_code == 403
    err = r.json()["error"]
    assert err["code"] == "forbidden" and err["details"]["code"] == "role_may_not_view_costs"
    assert reads.calls == []


@pytest.mark.parametrize("role", ["sales", "admin"])
@pytest.mark.parametrize(("method", "path", "body"), READS)
def test_sales_and_admin_read(method, path, body, role) -> None:
    _, _, client = _client(role)
    r = client.request(method, path, json=body, headers=HEADERS)
    assert r.status_code == 200, r.text


@pytest.mark.parametrize(("method", "path", "body"), READS)
def test_unauthenticated_read_is_401(method, path, body) -> None:
    _, _, client = _client()
    assert client.request(method, path, json=body).status_code == 401


def test_unknown_quote_is_404_in_the_production_envelope() -> None:
    _, _, client = _client()
    r = client.get(f"/v2/quotes/{uuid.uuid4()}", headers=HEADERS)
    assert r.status_code == 404
    err = r.json()["error"]
    assert err["code"] == "not_found" and err["details"]["code"] == "quote_not_found"


def test_the_quote_list_passes_its_query_through() -> None:
    reads, _, client = _client()
    r = client.get("/v2/quotes", headers=HEADERS)
    assert r.status_code == 200, r.text
    assert reads.calls == [("quote_list", "all", None, 50, None)]
    r = client.get("/v2/quotes?status=draft&q=balanza&limit=10&cursor=abc", headers=HEADERS)
    assert r.status_code == 200, r.text
    assert reads.calls[-1] == ("quote_list", "draft", "balanza", 10, "abc")


@pytest.mark.parametrize("query", ["status=borrador", "limit=0", "limit=101", "q=" + "x" * 201])
def test_the_quote_list_refuses_a_bad_query(query) -> None:
    reads, _, client = _client()
    r = client.get(f"/v2/quotes?{query}", headers=HEADERS)
    assert r.status_code == 422
    assert reads.calls == []


def test_a_bad_cursor_is_422_in_the_production_envelope() -> None:
    _, _, client = _client(reads=_FakeReads(CommandRefused(422, "bad_cursor", "that cursor is not one this list gave")))
    r = client.get("/v2/quotes?cursor=x", headers=HEADERS)
    assert r.status_code == 422
    assert r.json()["error"]["details"]["code"] == "bad_cursor"


def test_client_suggestions_need_two_characters() -> None:
    reads, _, client = _client()
    for query in ("", "?q=", "?q=a", "?q=%20a%20", "?q=" + "x" * 121):
        assert client.get(f"/v2/quotes/clients{query}", headers=HEADERS).status_code == 422, query
    assert reads.calls == []
    assert client.get("/v2/quotes/clients?q=%20ab%20", headers=HEADERS).status_code == 200
    assert reads.calls == [("client_suggestions", "ab")]


def test_a_quote_id_that_is_not_a_uuid_is_422() -> None:
    _, _, client = _client()
    assert client.get("/v2/quotes/not-a-uuid", headers=HEADERS).status_code == 422


def test_numbering_is_not_taken_for_a_quote_id() -> None:
    reads, _, client = _client()
    assert client.get("/v2/quotes/numbering", headers=HEADERS).json()["next_number"] == "01300-26"
    assert reads.calls == [("numbering",)]


def test_a_bad_seed_is_409_not_500() -> None:
    reads = _FakeReads(CommandRefused(409, "quote_numbering_not_configured", "inicio inválido"))
    _, _, client = _client(reads=reads)
    r = client.get("/v2/quotes/numbering", headers=HEADERS)
    assert r.status_code == 409
    assert r.json()["error"]["details"]["code"] == "quote_numbering_not_configured"


def test_preview_hands_the_validated_body_over() -> None:
    from origenlab_api.v2.quotes.draft import PreviewQuoteBody

    reads, _, client = _client()
    r = client.post("/v2/quotes/preview", json={"revision_id": RID, "draft": DRAFT}, headers=HEADERS)
    assert r.status_code == 200 and r.json()["computed"]["totals"]["net"] == "140"
    [(_, body)] = reads.calls
    assert isinstance(body, PreviewQuoteBody) and str(body.revision_id) == RID
    assert body.draft.items[0].unit_cost == Decimal("100")


@pytest.mark.parametrize("patch", [
    {"surprise": 1},
    {"draft": {**DRAFT, "surprise": 1}},
    {"draft": {"items": [{**DRAFT["items"][0], "quantity": "0"}]}},
    {"draft": {"items": [{**DRAFT["items"][0], "cost_currency": "GBP"}]}},
])
def test_a_bad_preview_body_is_422(patch) -> None:
    reads, _, client = _client()
    r = client.post("/v2/quotes/preview", json={"draft": DRAFT, **patch}, headers=HEADERS)
    assert r.status_code == 422 and r.json()["error"]["code"] == "validation_error"
    assert reads.calls == []


def test_preview_refuses_labdelivery_text() -> None:
    reads, _, client = _client()
    draft = {"items": [{**DRAFT["items"][0], "description": "Equipo de Labdelivery"}]}
    r = client.post("/v2/quotes/preview", json={"draft": draft}, headers=HEADERS)
    assert r.status_code == 422 and r.json()["error"]["details"]["code"] == "labdelivery_refused"
    assert reads.calls == []


@pytest.mark.parametrize(("status", "code", "envelope"), [
    (404, "revision_not_found", "not_found"), (404, "product_not_found", "not_found"),
    (409, "fx_unavailable", "conflict"), (422, "cost_observation_not_found", "validation_error"),
])
def test_preview_refusals_surface_in_the_envelope(status, code, envelope) -> None:
    _, _, client = _client(reads=_FakeReads(CommandRefused(status, code, "refused")))
    r = client.post("/v2/quotes/preview", json={"draft": DRAFT}, headers=HEADERS)
    assert r.status_code == status
    assert r.json()["error"]["code"] == envelope and r.json()["error"]["details"]["code"] == code


def test_workspace_price_history_gets_margins_from_stored_rates_only() -> None:
    class _Fx:
        def __init__(self):
            self.calls = []

        def rates_for(self, currency, days, *, fetch=True):
            self.calls.append((currency, fetch))
            return {d: {"clp_per_unit": Decimal("1000"), "as_of": d} for d in days}

    class _Reads(_FakeReads):
        def workspace(self, quote_id):
            return {"quote": {"id": QID}, "price_history": {
                "items": [{"date": "2026-09-01", "line_total": "2000000", "currency": "CLP"}],
                "costs": [{"as_of": "2026-08-01T00:00:00+00:00", "price": "1000", "currency": "EUR",
                           "price_kind": "dealer_net", "is_stale": False}]}}

    _, _, client = _client(reads=_Reads())
    fx = _Fx()
    client.app.state.catalog_fx = fx
    r = client.get(f"/v2/quotes/{QID}", headers=HEADERS)
    assert r.status_code == 200
    assert fx.calls == [("EUR", False)]
    assert "margin" in json.dumps(r.json()["price_history"]["items"][0])


# ------------------------------------------------------------------ commands

def test_the_command_router_exposes_exactly_the_five_quote_commands() -> None:
    assert {r.path for r in quote_command_router.routes} == {f"/v2/commands/{n}" for n in COMMAND_BODIES}


@pytest.mark.parametrize("command", sorted(COMMAND_BODIES))
def test_viewer_cannot_command(command) -> None:
    _, repo, client = _client("viewer")
    r = client.post(f"/v2/commands/{command}", json=COMMAND_BODIES[command], headers=_ikey())
    assert r.status_code == 403
    err = r.json()["error"]
    assert err["code"] == "forbidden" and err["details"]["code"] == "role_may_not_decide"
    assert repo.calls == []


@pytest.mark.parametrize("role", ["sales", "admin"])
@pytest.mark.parametrize("command", sorted(COMMAND_BODIES))
def test_sales_and_admin_reach_the_handler_with_a_json_ready_body(command, role) -> None:
    _, repo, client = _client(role)
    r = client.post(f"/v2/commands/{command}", json=COMMAND_BODIES[command], headers=_ikey())
    assert r.status_code == 200, r.text
    [call] = repo.calls
    assert call["command_name"] == command and call["operator"].role == role
    json.dumps(call["fields"])  # Decimal and UUID travel as strings
    if command == "save-quote-draft":
        assert call["fields"]["draft"]["items"][0]["unit_cost"] == "100"


@pytest.mark.parametrize("command", sorted(COMMAND_BODIES))
def test_missing_idempotency_key_is_400(command) -> None:
    _, repo, client = _client()
    r = client.post(f"/v2/commands/{command}", json=COMMAND_BODIES[command], headers=HEADERS)
    assert r.status_code == 400 and r.json()["error"]["code"] == "validation_error"
    assert repo.calls == []


@pytest.mark.parametrize("command", sorted(COMMAND_BODIES))
def test_an_extra_field_is_422(command) -> None:
    _, repo, client = _client()
    r = client.post(f"/v2/commands/{command}", json={**COMMAND_BODIES[command], "surprise": 1}, headers=_ikey())
    assert r.status_code == 422 and r.json()["error"]["code"] == "validation_error"
    assert repo.calls == []


def test_save_takes_a_recipient_picked_from_the_case_s_emails() -> None:
    _, repo, client = _client()
    body = {**COMMAND_BODIES["save-quote-draft"],
            "draft": {**DRAFT, "recipient": {"email": "Persona@Ejemplo.TEST", "name": "Persona Ficticia"}}}
    r = client.post("/v2/commands/save-quote-draft", json=body, headers=_ikey())
    assert r.status_code == 200, r.text
    assert repo.calls[0]["fields"]["draft"]["recipient"] == {
        "participant_id": None, "person_id": None, "contact_point_id": None, "email": "persona@ejemplo.test",
        "name": "Persona Ficticia"}
    two = {**body, "draft": {**DRAFT, "recipient": {"email": "a@ejemplo.test", "person_id": CASE}}}
    r = client.post("/v2/commands/save-quote-draft", json=two, headers=_ikey())
    assert r.status_code == 422 and r.json()["error"]["code"] == "validation_error" and len(repo.calls) == 1


@pytest.mark.parametrize(("status", "code", "envelope"), [
    (404, "case_not_found", "not_found"), (409, "stale_version", "conflict"),
    (409, "no_items", "conflict"), (422, "labdelivery_refused", "validation_error"),
])
def test_command_refusals_surface_in_the_envelope(status, code, envelope) -> None:
    _, _, client = _client(repo=_FakeRepo(CommandRefused(status, code, "refused")))
    r = client.post("/v2/commands/create-quote", json=COMMAND_BODIES["create-quote"], headers=_ikey())
    assert r.status_code == status
    assert r.json()["error"]["code"] == envelope and r.json()["error"]["details"]["code"] == code


# ------------------------------------------------------------------ mounting and FX wiring

QUOTE_PATHS = {"/v2/quotes/numbering", "/v2/quotes/{quote_id}", "/v2/quotes/preview",
               *(f"/v2/commands/{n}" for n in COMMAND_BODIES)}


def test_the_quote_routes_mount_only_behind_the_quoting_switch() -> None:
    from origenlab_api import main
    from origenlab_api.settings import Settings

    for kw in ({}, {"v2_quoting_enabled": True}, {"v2_database_url": LOOPBACK},
               {"v2_database_url": LOOPBACK, "v2_quoting_enabled": False}):
        app = FastAPI()
        main._mount_catalog(app, Settings(_env_file=None, **kw), LOOPBACK, lambda *a, **k: 1 / 0)
        assert not QUOTE_PATHS & set(app.openapi()["paths"])
        assert not hasattr(app.state, "quote_reads")
    app = FastAPI()
    main._mount_catalog(app, Settings(_env_file=None, v2_database_url=LOOPBACK, v2_quoting_enabled=True),
                        LOOPBACK, lambda *a, **k: 1 / 0)
    assert QUOTE_PATHS <= set(app.openapi()["paths"])
    assert app.state.quote_reads is not None and app.state.quote_repository is not None


def test_switch_off_answers_404_in_the_envelope() -> None:
    from origenlab_api import main
    from origenlab_api.settings import Settings

    app = FastAPI()
    register_exception_handlers(app)
    main._mount_catalog(app, Settings(_env_file=None, v2_database_url=LOOPBACK, v2_quoting_enabled=False),
                        LOOPBACK, lambda *a, **k: 1 / 0)
    client = TestClient(app)
    assert client.get(f"/v2/quotes/{QID}", headers=HEADERS).status_code == 404
    assert client.post("/v2/commands/create-quote", json=COMMAND_BODIES["create-quote"],
                       headers=_ikey()).status_code == 404


def test_the_command_fallback_never_asks_a_provider() -> None:
    from origenlab_api.v2.catalog.fx import FxUnavailable
    from origenlab_api.v2.quotes.inputs import no_provider

    with pytest.raises(FxUnavailable):
        no_provider("USD", dt.date(2026, 10, 15))


def test_mount_wires_the_full_fx_into_the_reads_and_no_provider_into_the_commands() -> None:
    from origenlab_api import main
    from origenlab_api.settings import Settings
    from origenlab_api.v2.quotes.inputs import no_provider

    app = FastAPI()
    main._mount_catalog(app, Settings(_env_file=None, v2_database_url=LOOPBACK, v2_quoting_enabled=True),
                        LOOPBACK, lambda *a, **k: 1 / 0)
    seen = []

    class _Fx:
        def rate(self, currency, on):
            seen.append((currency, on))
            return {"clp_per_unit": Decimal(1), "as_of": on, "source": "bcentral"}

    app.state.catalog_fx = _Fx()  # looked up at call time: always the app's current one
    app.state.quote_reads._fx_resolver("EUR", dt.date(2026, 10, 15))
    assert seen == [("EUR", dt.date(2026, 10, 15))]
    assert app.state.quote_repository._fx_resolver is no_provider
