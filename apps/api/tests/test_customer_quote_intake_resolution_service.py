"""Assembly-logic tests for the intake resolution service (CRM-Q2B). Pure
fake-repository unit tests -- no Postgres involved; the repository's own
query correctness is proven separately (test_customer_quote_intake_
resolution_repository_postgres.py)."""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from origenlab_api.repositories.postgres.customer_quote_intake_resolution import (
    ContactMatch,
    LeadIntelEvidence,
    OrganizationMatch,
    SalesOpportunityMatch,
)
from origenlab_api.services.customer_quote_intake_resolution_service import (
    CustomerQuoteIntakeResolutionService,
)
from origenlab_api.settings import Settings


@dataclass
class FakeRepository:
    organization_matches: list[OrganizationMatch] = field(default_factory=list)
    contacts_by_org: dict[str, list[ContactMatch]] = field(default_factory=dict)
    lead_intel_evidence: list[LeadIntelEvidence] = field(default_factory=list)
    active_opportunities_by_org: dict[str, list[SalesOpportunityMatch]] = field(default_factory=dict)
    document_numbers_in_use: set[str] = field(default_factory=set)

    def find_organization_matches(self, *, name_candidate: str, limit: int = 5) -> list[OrganizationMatch]:
        del name_candidate, limit
        return self.organization_matches

    def find_contacts_for_organization(self, *, organization_id: str, limit: int = 5) -> list[ContactMatch]:
        del limit
        return self.contacts_by_org.get(organization_id, [])

    def find_lead_intel_evidence(self, *, name_candidate: str, limit: int = 5) -> list[LeadIntelEvidence]:
        del name_candidate, limit
        return self.lead_intel_evidence

    def find_active_sales_opportunities_for_organization(self, *, organization_id: str) -> list[SalesOpportunityMatch]:
        return self.active_opportunities_by_org.get(organization_id, [])

    def document_number_in_use(self, *, document_number: str) -> bool:
        return document_number in self.document_numbers_in_use


def _service(fake: FakeRepository) -> CustomerQuoteIntakeResolutionService:
    return CustomerQuoteIntakeResolutionService(Settings(), repository=fake)  # type: ignore[arg-type]


def test_single_durable_organization_match_is_confirmed() -> None:
    fake = FakeRepository(organization_matches=[OrganizationMatch(organization_id="org_icn", display_name="ICN Chile")])
    resolution = _service(fake).resolve(folder_name="CN01191-ICN Chile")

    assert resolution.document_number_candidate == "CN01191"
    assert resolution.organization is not None
    assert resolution.organization.confidence == "confirmed_durable_match"
    assert resolution.organization.organization_id == "org_icn"
    assert resolution.organization.alternates == []


def test_multiple_durable_organization_matches_are_possible_not_auto_selected() -> None:
    fake = FakeRepository(
        organization_matches=[
            OrganizationMatch(organization_id="org_a", display_name="ICN Chile SPA"),
            OrganizationMatch(organization_id="org_b", display_name="ICN Chile Ltda"),
        ]
    )
    resolution = _service(fake).resolve(folder_name="CN01191-ICN Chile")

    assert resolution.organization is not None
    assert resolution.organization.confidence == "possible_match"
    assert resolution.organization.organization_id is None
    assert len(resolution.organization.alternates) == 2


def test_single_partial_organization_match_is_possible_not_confirmed() -> None:
    """candidate 'ICN' vs CRM 'ICN Chile' -- a substring hit is never an
    exact match, so this must stay possible_match, never auto-confirmed."""

    fake = FakeRepository(organization_matches=[OrganizationMatch(organization_id="org_icn", display_name="ICN Chile")])
    resolution = _service(fake).resolve(folder_name="CN01191-ICN")

    assert resolution.organization is not None
    assert resolution.organization.confidence == "possible_match"
    assert resolution.organization.organization_id is None


def test_exact_match_wins_over_unrelated_partial_matches() -> None:
    fake = FakeRepository(
        organization_matches=[
            OrganizationMatch(organization_id="org_exact", display_name="ICN Chile"),
            OrganizationMatch(organization_id="org_partial", display_name="ICN Chile Holdings"),
        ]
    )
    resolution = _service(fake).resolve(folder_name="CN01191-ICN Chile")

    assert resolution.organization is not None
    assert resolution.organization.confidence == "confirmed_durable_match"
    assert resolution.organization.organization_id == "org_exact"


def test_multiple_exact_organization_matches_are_ambiguous_not_auto_selected() -> None:
    fake = FakeRepository(
        organization_matches=[
            OrganizationMatch(organization_id="org_a", display_name="ICN Chile"),
            OrganizationMatch(organization_id="org_b", display_name="ICN Chile"),
        ]
    )
    resolution = _service(fake).resolve(folder_name="CN01191-ICN Chile")

    assert resolution.organization is not None
    assert resolution.organization.confidence == "possible_match"
    assert resolution.organization.organization_id is None
    assert len(resolution.organization.alternates) == 2


def test_exact_organization_match_normalizes_case_and_whitespace() -> None:
    fake = FakeRepository(organization_matches=[OrganizationMatch(organization_id="org_icn", display_name="  icn chile  ")])
    resolution = _service(fake).resolve(folder_name="CN01191-ICN Chile")

    assert resolution.organization is not None
    assert resolution.organization.confidence == "confirmed_durable_match"
    assert resolution.organization.organization_id == "org_icn"


def test_no_organization_match_anywhere_proposes_create_new() -> None:
    fake = FakeRepository()
    resolution = _service(fake).resolve(folder_name="CN01191-ICN Chile")

    assert resolution.organization is not None
    assert resolution.organization.organization_id is None
    assert resolution.organization.display_name == "ICN Chile"
    assert resolution.organization.confidence == "unresolved"


def test_lead_intel_evidence_populates_organization_as_possible_match_when_no_durable_match() -> None:
    fake = FakeRepository(
        lead_intel_evidence=[
            LeadIntelEvidence(
                organization_name="ICN Chile",
                contact_name="Ana Example",
                email="ana.example@icn.example",
                domain="icn.example",
                gmail_sent_count=8,
                gmail_received_count=3,
                gmail_last_contacted_at="2026-08-28",
            )
        ]
    )
    resolution = _service(fake).resolve(folder_name="CN01191-ICN Chile")

    assert resolution.organization is not None
    assert resolution.organization.confidence == "possible_match"
    assert any(e.source == "gmail_history" for e in resolution.organization.evidence)


def test_lead_intel_evidence_populates_contact_and_email_when_no_durable_contact() -> None:
    fake = FakeRepository(
        organization_matches=[OrganizationMatch(organization_id="org_icn", display_name="ICN Chile")],
        lead_intel_evidence=[
            LeadIntelEvidence(
                organization_name="ICN Chile",
                contact_name="Ana Example",
                email="ana.example@icn.example",
                domain="icn.example",
                gmail_sent_count=8,
                gmail_received_count=3,
                gmail_last_contacted_at="2026-08-28",
            )
        ],
    )
    resolution = _service(fake).resolve(folder_name="CN01191-ICN Chile")

    assert len(resolution.contacts) == 1
    assert resolution.contacts[0].email == "ana.example@icn.example"
    assert any(e.source == "gmail_history" for e in resolution.contacts[0].evidence)


def test_durable_contact_outranks_lead_intel_when_organization_is_confirmed() -> None:
    fake = FakeRepository(
        organization_matches=[OrganizationMatch(organization_id="org_icn", display_name="ICN Chile")],
        contacts_by_org={
            "org_icn": [ContactMatch(contact_id="contact_1", organization_id="org_icn", display_name="Ana CRM", primary_email="ana@icn.cl")]
        },
        lead_intel_evidence=[
            LeadIntelEvidence(
                organization_name="ICN Chile",
                contact_name="Someone Else",
                email="someone@icn.example",
                domain="icn.example",
                gmail_sent_count=1,
                gmail_received_count=0,
                gmail_last_contacted_at=None,
            )
        ],
    )
    resolution = _service(fake).resolve(folder_name="CN01191-ICN Chile")

    assert len(resolution.contacts) == 1
    assert resolution.contacts[0].contact_id == "contact_1"
    assert resolution.contacts[0].confidence == "confirmed_durable_match"


def test_two_durable_contacts_are_ambiguous_neither_auto_selected() -> None:
    """Never pick alphabetically-first: 2+ durable contacts must not carry
    confirmed_durable_match -- the operator must pick explicitly."""

    fake = FakeRepository(
        organization_matches=[OrganizationMatch(organization_id="org_icn", display_name="ICN Chile")],
        contacts_by_org={
            "org_icn": [
                ContactMatch(contact_id="contact_ana", organization_id="org_icn", display_name="Ana", primary_email="ana@icn.cl"),
                ContactMatch(contact_id="contact_bruno", organization_id="org_icn", display_name="Bruno", primary_email="bruno@icn.cl"),
            ]
        },
    )
    resolution = _service(fake).resolve(folder_name="CN01191-ICN Chile")

    assert len(resolution.contacts) == 2
    assert all(c.confidence != "confirmed_durable_match" for c in resolution.contacts)
    assert {c.contact_id for c in resolution.contacts} == {"contact_ana", "contact_bruno"}


def test_existing_active_opportunity_is_proposed_when_exactly_one() -> None:
    fake = FakeRepository(
        organization_matches=[OrganizationMatch(organization_id="org_icn", display_name="ICN Chile")],
        active_opportunities_by_org={
            "org_icn": [SalesOpportunityMatch(sales_opportunity_id="sales_existing", title="ICN Chile deal", stage="quoting")]
        },
    )
    resolution = _service(fake).resolve(folder_name="CN01191-ICN Chile")

    assert resolution.opportunity is not None
    assert resolution.opportunity.sales_opportunity_id == "sales_existing"
    assert resolution.opportunity.confidence == "confirmed_durable_match"


def test_only_dormant_opportunity_proposes_no_active_opportunity() -> None:
    """The repository already excludes dormant -- this proves the service
    treats an empty active list the same as "no opportunity found", never
    surfacing a dormant deal as confirmed."""

    fake = FakeRepository(
        organization_matches=[OrganizationMatch(organization_id="org_icn", display_name="ICN Chile")],
        active_opportunities_by_org={"org_icn": []},
    )
    resolution = _service(fake).resolve(folder_name="CN01191-ICN Chile")

    assert resolution.opportunity is not None
    assert resolution.opportunity.sales_opportunity_id is None
    assert resolution.opportunity.confidence == "unresolved"


def test_two_active_opportunities_are_ambiguous_not_silently_latest() -> None:
    fake = FakeRepository(
        organization_matches=[OrganizationMatch(organization_id="org_icn", display_name="ICN Chile")],
        active_opportunities_by_org={
            "org_icn": [
                SalesOpportunityMatch(sales_opportunity_id="sales_older", title="ICN Chile — Balanza", stage="new"),
                SalesOpportunityMatch(sales_opportunity_id="sales_newer", title="ICN Chile — Centrífuga", stage="quoting"),
            ]
        },
    )
    resolution = _service(fake).resolve(folder_name="CN01191-ICN Chile")

    assert resolution.opportunity is not None
    assert resolution.opportunity.sales_opportunity_id is None
    assert resolution.opportunity.confidence == "ambiguous_match"
    assert {a.sales_opportunity_id for a in resolution.opportunity.alternates} == {"sales_older", "sales_newer"}


def test_no_opportunity_found_proposes_auto_create_title() -> None:
    fake = FakeRepository(organization_matches=[OrganizationMatch(organization_id="org_icn", display_name="ICN Chile")])
    resolution = _service(fake).resolve(folder_name="CN01191-ICN Chile")

    assert resolution.opportunity is not None
    assert resolution.opportunity.sales_opportunity_id is None
    assert resolution.opportunity.title == "ICN Chile — Cotización"
    assert resolution.opportunity.confidence == "unresolved"


def test_unresolved_organization_still_proposes_auto_create_opportunity() -> None:
    fake = FakeRepository()
    resolution = _service(fake).resolve(folder_name="CN01191-ICN Chile")

    assert resolution.opportunity is not None
    assert resolution.opportunity.sales_opportunity_id is None
    assert resolution.opportunity.title == "ICN Chile — Cotización"


def test_quote_number_is_never_resolved_in_this_slice() -> None:
    resolution = _service(FakeRepository()).resolve(folder_name="CN01191-ICN Chile")
    assert resolution.quote_number_resolved is False


def test_document_number_conflict_flagged_when_already_in_use() -> None:
    fake = FakeRepository(document_numbers_in_use={"CN01191"})
    resolution = _service(fake).resolve(folder_name="CN01191-ICN Chile")

    assert resolution.document_number_conflict is True


def test_document_number_conflict_false_when_not_in_use() -> None:
    fake = FakeRepository()
    resolution = _service(fake).resolve(folder_name="CN01191-ICN Chile")

    assert resolution.document_number_conflict is False


def test_ambiguous_folder_name_with_no_identifier_returns_no_document_candidate_or_organization() -> None:
    resolution = _service(FakeRepository()).resolve(folder_name="Miscellaneous folder")

    assert resolution.document_number_candidate is None
    assert resolution.organization is None
    assert resolution.contacts == []
    assert resolution.opportunity is None


def test_folder_name_with_only_identifier_and_no_remainder_has_no_organization() -> None:
    resolution = _service(FakeRepository()).resolve(folder_name="CN01191")

    assert resolution.document_number_candidate == "CN01191"
    assert resolution.organization is None
