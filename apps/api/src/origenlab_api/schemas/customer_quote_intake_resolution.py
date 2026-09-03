"""Response schema for the read-only intake resolution endpoint (CRM-Q2B).

Mirrors origenlab_api.services.customer_quote_intake_resolution_service's
dataclasses 1:1 -- kept as a separate Pydantic boundary rather than reusing
the dataclasses directly, matching this codebase's existing convention
(e.g. CustomerQuoteResponse vs. CustomerQuoteBundle).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from origenlab_api.repositories.postgres.customer_quote_intake_resolution import (
    OrganizationMatch,
    SalesOpportunityMatch,
)
from origenlab_api.services.customer_quote_intake_resolution_service import (
    IntakeResolution,
)


class EvidenceItemResponse(BaseModel):
    source: str
    reason: str
    detail: str


class OrganizationAlternateResponse(BaseModel):
    organization_id: str
    display_name: str

    @classmethod
    def from_match(cls, match: OrganizationMatch) -> "OrganizationAlternateResponse":
        return cls(organization_id=match.organization_id, display_name=match.display_name)


class OrganizationCandidateResponse(BaseModel):
    organization_id: str | None
    display_name: str
    confidence: str
    evidence: list[EvidenceItemResponse]
    alternates: list[OrganizationAlternateResponse]


class ContactCandidateResponse(BaseModel):
    contact_id: str | None
    display_name: str | None
    email: str | None
    confidence: str
    evidence: list[EvidenceItemResponse]


class OpportunityAlternateResponse(BaseModel):
    sales_opportunity_id: str
    title: str

    @classmethod
    def from_match(cls, match: SalesOpportunityMatch) -> "OpportunityAlternateResponse":
        return cls(sales_opportunity_id=match.sales_opportunity_id, title=match.title)


class OpportunityCandidateResponse(BaseModel):
    sales_opportunity_id: str | None
    title: str
    confidence: str
    alternates: list[OpportunityAlternateResponse]


class CustomerQuoteIntakeResolutionResponse(BaseModel):
    document_number_candidate: str | None
    document_number_conflict: bool
    organization: OrganizationCandidateResponse | None
    contacts: list[ContactCandidateResponse]
    opportunity: OpportunityCandidateResponse | None
    quote_number_resolved: Literal[False] = False

    @classmethod
    def from_resolution(
        cls, resolution: IntakeResolution
    ) -> "CustomerQuoteIntakeResolutionResponse":
        organization = (
            OrganizationCandidateResponse(
                organization_id=resolution.organization.organization_id,
                display_name=resolution.organization.display_name,
                confidence=resolution.organization.confidence,
                evidence=[
                    EvidenceItemResponse(
                        source=item.source, reason=item.reason, detail=item.detail
                    )
                    for item in resolution.organization.evidence
                ],
                alternates=[
                    OrganizationAlternateResponse.from_match(match)
                    for match in resolution.organization.alternates
                ],
            )
            if resolution.organization is not None
            else None
        )

        opportunity = (
            OpportunityCandidateResponse(
                sales_opportunity_id=resolution.opportunity.sales_opportunity_id,
                title=resolution.opportunity.title,
                confidence=resolution.opportunity.confidence,
                alternates=[
                    OpportunityAlternateResponse.from_match(match)
                    for match in resolution.opportunity.alternates
                ],
            )
            if resolution.opportunity is not None
            else None
        )

        return cls(
            document_number_candidate=resolution.document_number_candidate,
            document_number_conflict=resolution.document_number_conflict,
            organization=organization,
            contacts=[
                ContactCandidateResponse(
                    contact_id=contact.contact_id,
                    display_name=contact.display_name,
                    email=contact.email,
                    confidence=contact.confidence,
                    evidence=[
                        EvidenceItemResponse(
                            source=item.source, reason=item.reason, detail=item.detail
                        )
                        for item in contact.evidence
                    ],
                )
                for contact in resolution.contacts
            ],
            opportunity=opportunity,
        )
