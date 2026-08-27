"""CRM-4A runtime compatibility: durable CRM links on sales opportunities.

The migration appends nullable ``organization_id`` / ``primary_crm_contact_id``
to ``commercial.sales_opportunity`` and its read view. The dataclass and the
API response schema must accept those columns without disturbing existing
callers or promotion semantics.
"""

from __future__ import annotations

from datetime import datetime, timezone

from origenlab_api.repositories.postgres.commercial_operations import (
    SalesOpportunity,
)
from origenlab_api.schemas.commercial_operations import (
    SalesOpportunityResponse,
)


NOW = datetime(2026, 8, 27, 12, 0, tzinfo=timezone.utc)


def _row(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "sales_opportunity_id": "sales_1",
        "source_kind": "pr3",
        "source_opportunity_id": "o_1",
        "account_id": "a_1",
        "primary_contact_id": "c_1",
        "title": "Centrífuga",
        "stage": "new",
        "owner_key": "tatiana@origenlab.cl",
        "version": 1,
        "created_by": "tatiana@origenlab.cl",
        "updated_by": "tatiana@origenlab.cl",
        "created_at": NOW,
        "updated_at": NOW,
    }
    row.update(overrides)
    return row


def test_dataclass_defaults_durable_links_to_none() -> None:
    result = SalesOpportunity(**_row())

    assert result.organization_id is None
    assert result.primary_crm_contact_id is None


def test_dataclass_accepts_durable_links_from_select_star() -> None:
    result = SalesOpportunity(
        **_row(
            organization_id="org_" + "b" * 32,
            primary_crm_contact_id="contact_1",
        )
    )

    assert result.organization_id == "org_" + "b" * 32
    assert result.primary_crm_contact_id == "contact_1"


def test_response_schema_appends_optional_durable_links() -> None:
    populated = SalesOpportunity(
        **_row(
            organization_id="org_" + "c" * 32,
            primary_crm_contact_id="contact_9",
        )
    )

    body = SalesOpportunityResponse.model_validate(
        populated,
        from_attributes=True,
    )

    assert body.organization_id == "org_" + "c" * 32
    assert body.primary_crm_contact_id == "contact_9"

    unlinked = SalesOpportunityResponse.model_validate(
        SalesOpportunity(**_row()),
        from_attributes=True,
    )

    assert unlinked.organization_id is None
    assert unlinked.primary_crm_contact_id is None

    dumped = unlinked.model_dump()
    assert dumped["organization_id"] is None
    assert dumped["primary_crm_contact_id"] is None
