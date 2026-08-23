"""Repository protocols for dual SQLite / Postgres backends (DB-3)."""

from __future__ import annotations

from typing import Any, Protocol

from origenlab_api.repositories.contact_types import ContactQueryResult
from origenlab_api.schemas.commercial_opportunities import (
    CommercialOpportunitiesMeta,
    CommercialOpportunityDetailResponse,
    CommercialOpportunityItem,
)
from origenlab_api.repositories.email_types import RecentEmailsQueryResult
from origenlab_api.schemas.cases import WarmCaseItem, WarmCasesMeta
from origenlab_api.schemas.opportunities import EquipmentOpportunitiesMeta


class OperatorStatusRepository(Protocol):
    def get_status(self, *, max_staleness_days: float = 14.0) -> dict[str, Any]:
        """Return a dict compatible with ``OperatorStatusResponse`` fields."""

    def get_automation_status(
        self, *, mirror_cooldown_seconds: int = 60
    ) -> dict[str, Any]:
        """Return a dict compatible with ``OperatorAutomationStatusResponse`` fields."""


class EquipmentOpportunityRepository(Protocol):
    def list_equipment(
        self,
        *,
        limit: int = 50,
        priority: int | None = None,
        next_action: str | None = None,
        safe_channel: str | None = None,
        include_account_intelligence: bool = True,
    ) -> tuple[list[dict[str, Any]], EquipmentOpportunitiesMeta]:
        """Return equipment queue rows and response meta."""


class CommercialOpportunityRepository(Protocol):
    def list_commercial(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
        canonical_stage: str | None = None,
        record_kind: str | None = None,
        review_status: str | None = None,
        account_id: str | None = None,
        primary_contact_id: str | None = None,
    ) -> tuple[list[CommercialOpportunityItem], CommercialOpportunitiesMeta]:
        """Return canonical PR3 commercial opportunity rows."""

    def get_commercial_detail(
        self,
        opportunity_id: str,
    ) -> CommercialOpportunityDetailResponse | None:
        """Return one opportunity with events/evidence/conflicts."""


class WarmCaseRepository(Protocol):
    def list_warm_cases(
        self,
        *,
        days: int = 14,
        limit: int = 50,
        category: str | None = None,
        positive_signal_only: bool = False,
        include_noise: bool = False,
    ) -> tuple[list[WarmCaseItem], WarmCasesMeta]:
        """Return warm case items and response meta."""


class EmailRecentRepository(Protocol):
    def list_recent(
        self,
        *,
        days: int = 7,
        limit: int = 50,
        exclude_noise: bool = True,
        folder: str | None = None,
    ) -> RecentEmailsQueryResult:
        """Return recent email preview rows and response metadata."""


class ContactRepository(Protocol):
    def get_contact_detail(self, email_raw: str) -> ContactQueryResult:
        """Return contact intelligence for one email (raises ValueError if invalid)."""
