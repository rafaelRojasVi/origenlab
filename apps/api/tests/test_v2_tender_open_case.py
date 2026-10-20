"""`POST /v2/commands/open-tender-case` at the route: who may call it, what reaches the repository.

A recording repository stands in for the database (`test_v2_tender_open_case_db.py` runs the
transaction). Every value is invented (public repository).
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from origenlab_api.commercial_operator_identity import OPERATOR_EMAIL_HEADER
from origenlab_api.v2.commands import CommandRefused
from origenlab_api.v2.identity import LocalDevIdentity, OperatorIdentity, OperatorLookup
from origenlab_api.v2.tenders.mercado_publico import TenderDetail
from origenlab_api.v2.tenders.open_case import OPEN_TENDER_CASE

LOOPBACK = "postgresql://origenlab_api:pw@127.0.0.1:54332/origenlab_dev"
PATH = "/v2/commands/open-tender-case"
CODE = "1234567-12-LE26"


class _Lookup(OperatorLookup):
    def __init__(self, operator: OperatorIdentity | None) -> None:
        self._operator = operator

    def by_email(self, email_norm: str) -> OperatorIdentity | None:
        return self._operator


def _operator(role: str = "sales") -> OperatorIdentity:
    return OperatorIdentity(operator_id="00000000-0000-4000-8000-000000000001", email_norm="operator@example.cl",
                            display_name="Operator", role=role, status="active")


class _Recorder:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def execute(self, **kwargs):
        self.calls.append(kwargs)
        return {"command": kwargs["command_name"], "opportunity_id": "x", "replayed": False}


class _Fetch:
    def __init__(self, detail: TenderDetail | None) -> None:
        self.detail = detail
        self.calls: list[tuple[str, str | None]] = []

    def __call__(self, code: str, *, ticket: str | None) -> TenderDetail | None:
        self.calls.append((code, ticket))
        return self.detail


def _client(repo: _Recorder, fetch: _Fetch, *, role: str = "sales", ticket: str | None = "ticket-ficticio") -> TestClient:
    from origenlab_api.v2.tenders.routes import tender_command_router

    app = FastAPI()
    app.include_router(tender_command_router)
    app.state.v2_tender_command_repository = repo
    app.state.tender_fetch = fetch
    app.state.chilecompra_ticket = ticket
    app.state.v2_identity = LocalDevIdentity(LOOPBACK, _Lookup(_operator(role)))
    return TestClient(app)


def _headers(key: str | None = "key-1") -> dict[str, str]:
    headers = {OPERATOR_EMAIL_HEADER: "operator@example.cl"}
    if key is not None:
        headers["Idempotency-Key"] = key
    return headers


DETAIL = TenderDetail(code=CODE, title="Balanzas analíticas", buyer_name="Hospital Ejemplo", buyer_code="7001",
                      buyer_rut="61111111-8", closes_at="2026-11-03T15:00:00", published_at=None,
                      source_status="Publicada")


def test_viewer_forbidden() -> None:
    repo, fetch = _Recorder(), _Fetch(DETAIL)
    response = _client(repo, fetch, role="viewer").post(PATH, json={"reference": CODE}, headers=_headers())
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "role_may_not_decide"
    assert repo.calls == [] and fetch.calls == []


def test_idempotency_key_required() -> None:
    repo, fetch = _Recorder(), _Fetch(DETAIL)
    response = _client(repo, fetch).post(PATH, json={"reference": CODE}, headers=_headers(key=None))
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "idempotency_key_required"
    assert repo.calls == []


@pytest.mark.parametrize(("reference", "code"), [
    ("no es un código", "tender_reference_invalid"),
    ("https://www.mercadopublico.cl/Procurement/Modules/RFB/DetailsAcquisition.aspx?qs=abc123", "tender_code_not_in_link"),
])
def test_unparseable_reference_is_422_before_any_lookup(reference: str, code: str) -> None:
    repo, fetch = _Recorder(), _Fetch(DETAIL)
    response = _client(repo, fetch).post(PATH, json={"reference": reference}, headers=_headers())
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == code
    assert repo.calls == [] and fetch.calls == []


def test_link_is_parsed_and_detail_read_with_the_ticket() -> None:
    repo, fetch = _Recorder(), _Fetch(DETAIL)
    link = f"https://www.mercadopublico.cl/Procurement/Modules/RFB/DetailsAcquisition.aspx?idlicitacion={CODE}"
    response = _client(repo, fetch).post(PATH, json={"reference": link}, headers=_headers())
    assert response.status_code == 200
    assert fetch.calls == [(CODE, "ticket-ficticio")]
    (call,) = repo.calls
    assert call["command_name"] == OPEN_TENDER_CASE and call["idempotency_key"] == "key-1"
    assert call["fields"]["code"] == CODE and call["fields"]["detail"] is DETAIL


def test_typed_buyer_reaches_the_repository_with_a_normalised_rut() -> None:
    repo, fetch = _Recorder(), _Fetch(None)
    body = {"reference": CODE, "buyer": {"name": "Hospital Ejemplo", "rut": "61.111.111-8"}}
    assert _client(repo, fetch, ticket=None).post(PATH, json=body, headers=_headers()).status_code == 200
    fields = repo.calls[0]["fields"]
    assert (fields["detail"], fields["typed_buyer_name"], fields["typed_buyer_rut"]) == (
        None, "Hospital Ejemplo", "61111111-8")


def test_an_invalid_typed_rut_is_refused() -> None:
    repo, fetch = _Recorder(), _Fetch(None)
    body = {"reference": CODE, "buyer": {"name": "Hospital Ejemplo", "rut": "61.111.111-1"}}
    response = _client(repo, fetch).post(PATH, json=body, headers=_headers())
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "buyer_rut_invalid"
    assert repo.calls == []


def test_unknown_field_refused() -> None:
    repo, fetch = _Recorder(), _Fetch(DETAIL)
    response = _client(repo, fetch).post(PATH, json={"reference": CODE, "operator_id": "x"}, headers=_headers())
    assert response.status_code == 422
    assert repo.calls == []


def test_a_refusal_keeps_its_code_and_details() -> None:
    class _Refusing(_Recorder):
        def execute(self, **kwargs):
            raise CommandRefused(409, "tender_case_exists", "ya existe", details={"opportunity_id": "abc"})

    response = _client(_Refusing(), _Fetch(DETAIL)).post(PATH, json={"reference": CODE}, headers=_headers())
    assert response.status_code == 409
    assert response.json()["detail"] == {"code": "tender_case_exists", "message": "ya existe", "opportunity_id": "abc"}


def test_router_exposes_only_post() -> None:
    from origenlab_api.v2.tenders.routes import tender_command_router

    assert [(r.path, r.methods) for r in tender_command_router.routes] == [
        (PATH, {"POST"}), ("/v2/commands/set-tender-status", {"POST"}), ("/v2/commands/review-notice", {"POST"}),
        ("/v2/commands/save-bidder-profile", {"POST"}), ("/v2/commands/retire-bidder-document", {"POST"}),
        ("/v2/commands/remove-tender-annex", {"POST"}), ("/v2/commands/add-bidder-document", {"POST"}), ("/v2/commands/attach-tender-annexes", {"POST"}),
        ("/v2/commands/prepare-tender-bid", {"POST"}), ("/v2/commands/create-tender-offer-quote", {"POST"})]


def test_bidder_read_router_exposes_only_get() -> None:
    from origenlab_api.v2.tenders.routes import bidder_read_router

    assert [(r.path, r.methods) for r in bidder_read_router.routes] == [
        ("/v2/bidder", {"GET"}), ("/v2/bidder/documents/{document_id}/url", {"GET"})]


def test_read_router_exposes_only_get() -> None:
    from origenlab_api.v2.tenders.routes import tender_read_router

    assert [(r.path, r.methods) for r in tender_read_router.routes] == [
        # «En Mercado Público» before the page, so `discovered` is never read as a tender id.
        ("/v2/tenders", {"GET"}), ("/v2/tenders/discovered", {"GET"}),
        ("/v2/tenders/discovered/{notice_id}", {"GET"}), ("/v2/tenders/{tender_id}", {"GET"}),
        ("/v2/tenders/{tender_id}/preparations/{preparation_id}/files/{file_key}/url", {"GET"})]
