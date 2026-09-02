"""Schemas for the read-only Drive Pendientes projection (CRM-Q1D follow-up).

A Drive-only pending workspace is explicitly not a durable customer quote:
no quote_id, opportunity, lifecycle status, revision, or provisioning-retry
field is ever exposed here -- those only exist once a durable
``customer_quote`` record is created through the ``/operations/*`` command
path.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

from origenlab_api.services.drive_pending_quote_service import (
    DrivePendingQuoteWorkspace,
)


class CustomerQuoteDrivePendingItem(BaseModel):
    folder_id: str
    folder_name: str
    folder_web_url: str
    document_identifier: str | None = None
    created_time: datetime | None = None
    modified_time: datetime | None = None

    @classmethod
    def from_workspace(
        cls, workspace: DrivePendingQuoteWorkspace
    ) -> "CustomerQuoteDrivePendingItem":
        return cls(
            folder_id=workspace.folder_id,
            folder_name=workspace.folder_name,
            folder_web_url=workspace.folder_web_url,
            document_identifier=workspace.document_identifier,
            created_time=workspace.created_time,
            modified_time=workspace.modified_time,
        )


class CustomerQuoteDrivePendingListMeta(BaseModel):
    count: int


class CustomerQuoteDrivePendingListResponse(BaseModel):
    meta: CustomerQuoteDrivePendingListMeta
    items: list[CustomerQuoteDrivePendingItem]
