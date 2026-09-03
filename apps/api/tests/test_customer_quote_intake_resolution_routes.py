"""HTTP tests for the read-only intake resolution endpoint (CRM-Q2B)."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from origenlab_api.errors import register_exception_handlers
from origenlab_api.repositories.postgres.customer_quote_intake_resolution import (
    SalesOpportunityMatch,
)
from origenlab_api.routes import operations
from origenlab_api.services.customer_quote_intake_resolution_service import (
    ContactCandidate,
    EvidenceItem,
    IntakeResolution,
    OpportunityCandidate,
    OrganizationCandidate,
)


class FakeIntakeResolutionService:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.result = IntakeResolution(
            document_number_candidate="CN01191",
            document_number_conflict=False,
            organization=OrganizationCandidate(
                organization_id="org_icn",
                display_name="ICN Chile",
                confidence="confirmed_durable_match",
                evidence=[
                    EvidenceItem(
                        source="durable_crm",
                        reason="normalized_name_match",
                        detail="Coincidencia exacta con organización CRM: ICN Chile",
                    )
                ],
            ),
            contacts=[
                ContactCandidate(
                    contact_id=None,
                    display_name="Ana Example",
                    email="ana.example@icn.example",
                    confidence="possible_match",
                    evidence=[
                        EvidenceItem(
                            source="gmail_history",
                            reason="gmail_contact_history",
                            detail="Encontrado en 8 correos enviados",
                        )
                    ],
                )
            ],
            opportunity=OpportunityCandidate(
                sales_opportunity_id=None,
                title="ICN Chile — Cotización",
                confidence="unresolved",
            ),
            quote_number_resolved=False,
        )

    def resolve(self, *, folder_name: str) -> IntakeResolution:
        self.calls.append(folder_name)
        return self.result


def _client(service: FakeIntakeResolutionService | None = None) -> tuple[TestClient, FakeIntakeResolutionService]:
    service = service or FakeIntakeResolutionService()

    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(operations.router)

    app.dependency_overrides[
        operations.get_customer_quote_intake_resolution_service
    ] = lambda: service

    client = TestClient(app, raise_server_exceptions=False)

    return client, service


def test_resolve_route_requires_folder_name_query_param() -> None:
    client, _service = _client()

    response = client.get("/operations/customer-quotes/drive-pending/resolve")

    assert response.status_code == 422


def test_resolve_route_is_read_only_no_operator_identity_required() -> None:
    client, _service = _client()

    response = client.get(
        "/operations/customer-quotes/drive-pending/resolve",
        params={"folder_name": "CN01191-ICN Chile"},
    )

    assert response.status_code == 200


def test_resolve_route_forwards_the_folder_name() -> None:
    client, service = _client()

    client.get(
        "/operations/customer-quotes/drive-pending/resolve",
        params={"folder_name": "CN01191-ICN Chile"},
    )

    assert service.calls == ["CN01191-ICN Chile"]


def test_resolve_route_returns_evidence_shaped_response() -> None:
    client, _service = _client()

    response = client.get(
        "/operations/customer-quotes/drive-pending/resolve",
        params={"folder_name": "CN01191-ICN Chile"},
    )

    body: dict[str, Any] = response.json()
    assert body["document_number_candidate"] == "CN01191"
    assert body["quote_number_resolved"] is False
    assert body["organization"]["organization_id"] == "org_icn"
    assert body["organization"]["confidence"] == "confirmed_durable_match"
    assert body["contacts"][0]["email"] == "ana.example@icn.example"
    assert body["opportunity"]["title"] == "ICN Chile — Cotización"


def test_resolve_route_serializes_ambiguous_opportunity_alternates() -> None:
    service = FakeIntakeResolutionService()
    service.result = IntakeResolution(
        document_number_candidate="CN01191",
        document_number_conflict=False,
        organization=OrganizationCandidate(
            organization_id="org_icn",
            display_name="ICN Chile",
            confidence="confirmed_durable_match",
            evidence=[],
        ),
        contacts=[],
        opportunity=OpportunityCandidate(
            sales_opportunity_id=None,
            title="ICN Chile — Cotización",
            confidence="ambiguous_match",
            alternates=[
                SalesOpportunityMatch(sales_opportunity_id="sales_a", title="ICN Chile — Balanza", stage="new"),
                SalesOpportunityMatch(sales_opportunity_id="sales_b", title="ICN Chile — Centrífuga", stage="quoting"),
            ],
        ),
        quote_number_resolved=False,
    )
    client, _service = _client(service)

    response = client.get(
        "/operations/customer-quotes/drive-pending/resolve",
        params={"folder_name": "CN01191-ICN Chile"},
    )

    body: dict[str, Any] = response.json()
    assert body["opportunity"]["confidence"] == "ambiguous_match"
    assert body["opportunity"]["sales_opportunity_id"] is None
    assert {a["sales_opportunity_id"] for a in body["opportunity"]["alternates"]} == {"sales_a", "sales_b"}


def test_resolve_route_never_mutates_anything_no_write_dependencies_used() -> None:
    """The route only depends on the read-only intake resolution service --
    it never touches CustomerQuoteService (the write service)."""

    client, service = _client()

    client.get(
        "/operations/customer-quotes/drive-pending/resolve",
        params={"folder_name": "CN01191-ICN Chile"},
    )

    assert service.calls == ["CN01191-ICN Chile"]
