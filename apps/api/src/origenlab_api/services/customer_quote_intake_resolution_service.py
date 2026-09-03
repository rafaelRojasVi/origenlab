"""Intake resolution for "Incorporar al CRM" (CRM-Q2B).

Bounded, deterministic, explainable evidence resolver -- never a numeric ML
confidence score, never OCR/broad document ingestion, never an auto-commit
of an ambiguous match. Three evidence tiers, in precedence order:

  A. Drive folder-name parsing (document identifier + an organization-name
     candidate) -- low/medium confidence, never a durable claim.
  B. Durable CRM (commercial.organization/contact/sales_opportunity) --
     outranks everything else.
  C. lead_intel.prospect Gmail interaction evidence.

This service never mutates anything -- it is a pure read/assembly step. The
operator confirms before any durable write happens (via the existing
adopt_drive_folder / manual sales-opportunity-create commands, unchanged).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from origenlab_api.repositories.postgres.customer_quote_intake_resolution import (
    ContactMatch,
    LeadIntelEvidence,
    OrganizationMatch,
    PostgresCustomerQuoteIntakeResolutionRepository,
    SalesOpportunityMatch,
)
from origenlab_api.services.drive_pending_quote_service import (
    parse_drive_pending_document_identifier,
    parse_drive_pending_organization_candidate,
)
from origenlab_api.settings import Settings


def _normalize_org_name(value: str) -> str:
    return value.strip().casefold()


@dataclass(frozen=True)
class EvidenceItem:
    source: str
    reason: str
    detail: str


@dataclass(frozen=True)
class OrganizationCandidate:
    organization_id: str | None
    display_name: str
    confidence: str
    evidence: list[EvidenceItem]
    # Populated only when confidence == "possible_match" with 2+ durable
    # candidates -- the operator picks, nothing is auto-selected.
    alternates: list[OrganizationMatch] = field(default_factory=list)


@dataclass(frozen=True)
class ContactCandidate:
    contact_id: str | None
    display_name: str | None
    email: str | None
    confidence: str
    evidence: list[EvidenceItem]


@dataclass(frozen=True)
class OpportunityCandidate:
    sales_opportunity_id: str | None
    title: str
    confidence: str
    # Populated only when confidence == "ambiguous_match" -- 2+ active
    # sales opportunities exist for the confirmed organization and the
    # operator must pick, never a silent latest-created auto-selection.
    alternates: list[SalesOpportunityMatch] = field(default_factory=list)


@dataclass(frozen=True)
class IntakeResolution:
    document_number_candidate: str | None
    document_number_conflict: bool
    organization: OrganizationCandidate | None
    contacts: list[ContactCandidate]
    opportunity: OpportunityCandidate | None
    # Always False in this slice: no safe read model exists yet that
    # extracts a quote number from email/attachment content. The operator
    # always confirms this field explicitly -- see CRM-Q2B's numbering
    # invariant (never derived from document_number).
    quote_number_resolved: bool


class CustomerQuoteIntakeResolutionRepositoryProtocol(Protocol):
    def find_organization_matches(
        self, *, name_candidate: str, limit: int = 5
    ) -> list[OrganizationMatch]: ...

    def find_contacts_for_organization(
        self, *, organization_id: str, limit: int = 5
    ) -> list[ContactMatch]: ...

    def find_lead_intel_evidence(
        self, *, name_candidate: str, limit: int = 5
    ) -> list[LeadIntelEvidence]: ...

    def find_active_sales_opportunities_for_organization(
        self, *, organization_id: str
    ) -> list[SalesOpportunityMatch]: ...

    def document_number_in_use(self, *, document_number: str) -> bool: ...


class CustomerQuoteIntakeResolutionService:
    def __init__(
        self,
        settings: Settings,
        *,
        repository: CustomerQuoteIntakeResolutionRepositoryProtocol | None = None,
    ) -> None:
        self._settings = settings
        self._repository: CustomerQuoteIntakeResolutionRepositoryProtocol = (
            repository or PostgresCustomerQuoteIntakeResolutionRepository(settings)
        )

    def resolve(self, *, folder_name: str) -> IntakeResolution:
        document_number = parse_drive_pending_document_identifier(folder_name)
        organization_candidate_name = parse_drive_pending_organization_candidate(
            folder_name
        )

        document_conflict = (
            self._repository.document_number_in_use(document_number=document_number)
            if document_number is not None
            else False
        )

        if organization_candidate_name is None:
            return IntakeResolution(
                document_number_candidate=document_number,
                document_number_conflict=document_conflict,
                organization=None,
                contacts=[],
                opportunity=None,
                quote_number_resolved=False,
            )

        org_matches = self._repository.find_organization_matches(
            name_candidate=organization_candidate_name
        )
        organization = self._build_organization_candidate(
            organization_candidate_name, org_matches
        )
        contacts = self._build_contacts(organization, organization_candidate_name)
        opportunity = self._build_opportunity(organization)

        return IntakeResolution(
            document_number_candidate=document_number,
            document_number_conflict=document_conflict,
            organization=organization,
            contacts=contacts,
            opportunity=opportunity,
            quote_number_resolved=False,
        )

    def _build_organization_candidate(
        self, candidate_name: str, matches: list[OrganizationMatch]
    ) -> OrganizationCandidate:
        normalized_candidate = _normalize_org_name(candidate_name)
        exact_matches = [
            m for m in matches if _normalize_org_name(m.display_name) == normalized_candidate
        ]

        if len(exact_matches) == 1:
            match = exact_matches[0]
            return OrganizationCandidate(
                organization_id=match.organization_id,
                display_name=match.display_name,
                confidence="confirmed_durable_match",
                evidence=[
                    EvidenceItem(
                        source="durable_crm",
                        reason="exact_normalized_name_match",
                        detail=f"Coincidencia exacta con organización CRM: {match.display_name}",
                    )
                ],
            )

        if len(exact_matches) > 1:
            # Never call a tie an "exact match" outright -- two durable
            # organizations sharing a normalized name is ambiguous, not
            # auto-selectable, no matter how it happened.
            return OrganizationCandidate(
                organization_id=None,
                display_name=candidate_name,
                confidence="possible_match",
                evidence=[
                    EvidenceItem(
                        source="durable_crm",
                        reason="multiple_exact_name_matches",
                        detail=(
                            f"{len(exact_matches)} organizaciones del CRM coinciden "
                            "exactamente con este nombre"
                        ),
                    )
                ],
                alternates=exact_matches,
            )

        if matches:
            # 1+ partial (substring) matches, zero exact ones -- always
            # possible_match, never confirmed, even when there is exactly
            # one partial hit (e.g. candidate "ICN" vs CRM "ICN Chile").
            return OrganizationCandidate(
                organization_id=None,
                display_name=candidate_name,
                confidence="possible_match",
                evidence=[
                    EvidenceItem(
                        source="durable_crm",
                        reason="multiple_name_matches" if len(matches) > 1 else "partial_name_match",
                        detail=(
                            f"{len(matches)} organizaciones del CRM coinciden "
                            "parcialmente con este nombre"
                            if len(matches) > 1
                            else f"Coincidencia parcial con organización CRM: {matches[0].display_name}"
                        ),
                    )
                ],
                alternates=matches,
            )

        lead_intel_matches = self._repository.find_lead_intel_evidence(
            name_candidate=candidate_name
        )
        if lead_intel_matches:
            top = lead_intel_matches[0]
            return OrganizationCandidate(
                organization_id=None,
                display_name=candidate_name,
                confidence="possible_match",
                evidence=[
                    EvidenceItem(
                        source="gmail_history",
                        reason="gmail_organization_name_match",
                        detail=(
                            "Encontrado en el historial de correos "
                            f"({top.gmail_sent_count or 0} enviados)"
                        ),
                    )
                ],
            )

        return OrganizationCandidate(
            organization_id=None,
            display_name=candidate_name,
            confidence="unresolved",
            evidence=[
                EvidenceItem(
                    source="drive_folder_name",
                    reason="no_crm_or_email_evidence",
                    detail=(
                        "Detectado desde el nombre de la carpeta de Drive; "
                        "sin coincidencia en el CRM ni en correos"
                    ),
                )
            ],
        )

    def _build_contacts(
        self, organization: OrganizationCandidate, candidate_name: str
    ) -> list[ContactCandidate]:
        if organization.organization_id is not None:
            durable_contacts = self._repository.find_contacts_for_organization(
                organization_id=organization.organization_id,
            )
            if durable_contacts:
                # Exactly one durable contact may be proposed/selected
                # automatically. 2+ is ambiguous -- never silently pick
                # alphabetically-first; the operator must choose explicitly.
                single = len(durable_contacts) == 1
                confidence = "confirmed_durable_match" if single else "possible_match"
                detail = (
                    "Contacto existente en el CRM para esta organización"
                    if single
                    else (
                        f"{len(durable_contacts)} contactos existen en el CRM para "
                        "esta organización -- elige el correcto"
                    )
                )
                return [
                    ContactCandidate(
                        contact_id=contact.contact_id,
                        display_name=contact.display_name,
                        email=contact.primary_email,
                        confidence=confidence,
                        evidence=[
                            EvidenceItem(
                                source="durable_crm",
                                reason="organization_contact_match" if single else "multiple_organization_contacts",
                                detail=detail,
                            )
                        ],
                    )
                    for contact in durable_contacts
                ]

        lead_intel_matches = self._repository.find_lead_intel_evidence(
            name_candidate=candidate_name
        )
        return [
            ContactCandidate(
                contact_id=None,
                display_name=evidence.contact_name,
                email=evidence.email,
                confidence="possible_match" if evidence.email else "unresolved",
                evidence=[
                    EvidenceItem(
                        source="gmail_history",
                        reason="gmail_contact_history",
                        detail=(
                            f"Encontrado en {evidence.gmail_sent_count or 0} "
                            "correos enviados"
                            + (
                                f"; último contacto {evidence.gmail_last_contacted_at}"
                                if evidence.gmail_last_contacted_at
                                else ""
                            )
                        ),
                    )
                ],
            )
            for evidence in lead_intel_matches
            if evidence.email or evidence.contact_name
        ]

    def _build_opportunity(
        self, organization: OrganizationCandidate
    ) -> OpportunityCandidate:
        if organization.organization_id is not None:
            active = self._repository.find_active_sales_opportunities_for_organization(
                organization_id=organization.organization_id,
            )
            if len(active) == 1:
                existing = active[0]
                return OpportunityCandidate(
                    sales_opportunity_id=existing.sales_opportunity_id,
                    title=existing.title,
                    confidence="confirmed_durable_match",
                )

            if len(active) > 1:
                # Never silently select the newest -- the operator must
                # choose among the active opportunities, or no duplicate
                # is auto-created while this stays unresolved.
                return OpportunityCandidate(
                    sales_opportunity_id=None,
                    title=f"{organization.display_name} — Cotización",
                    confidence="ambiguous_match",
                    alternates=active,
                )

        return OpportunityCandidate(
            sales_opportunity_id=None,
            title=f"{organization.display_name} — Cotización",
            confidence="unresolved",
        )
