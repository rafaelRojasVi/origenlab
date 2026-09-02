"""Read-only Drive Pendientes projection (CRM-Q1D follow-up).

Google Drive remains the human working-document store; this service exposes
a read-only, non-recursive listing of direct child folders under the
configured Pendientes container for operational visibility only. A
Drive-only folder is never turned into a durable ``customer_quote`` record
-- the durable CRM stays authoritative for CRM-created commercial records.
Folders whose ``folder_id`` is already referenced by a durable customer-quote
workspace are excluded here so the operator queue never shows the same
workspace twice.

``DriveProvisioningError`` from the provider factory or transport is
deliberately left uncaught here (unlike ``CustomerQuoteService``, which
persists it as a durable workspace failure): this is a pure read with no
durable row to record a failure against, so the route maps it directly to a
redacted HTTP response instead.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Protocol

from origenlab_api.drive.factory import build_drive_workspace_provider
from origenlab_api.drive.protocol import (
    DrivePendingFolder,
    QuoteDriveWorkspaceProvider,
)
from origenlab_api.repositories.postgres.customer_quotes_read import (
    PostgresCustomerQuoteReadRepository,
)
from origenlab_api.settings import Settings


# Conservative by design: only an unambiguous "CN" + digits prefix, not
# immediately followed by another letter/digit, is extracted for display.
# This is never a durable quote_number -- ambiguous folder names return
# None rather than guess.
_DOCUMENT_IDENTIFIER_RE = re.compile(r"^(CN\d+)(?![A-Za-z0-9])")


def parse_drive_pending_document_identifier(folder_name: str) -> str | None:
    match = _DOCUMENT_IDENTIFIER_RE.match(folder_name.strip())
    return match.group(1) if match else None


# Leading separator characters (hyphen, en/em dash, whitespace) stripped
# from what follows the document identifier.
_LEADING_SEPARATOR_RE = re.compile(r"^[\s\-–—]+")


def parse_drive_pending_organization_candidate(folder_name: str) -> str | None:
    """Everything after the document identifier and its separator, trimmed
    -- the raw remainder, no further heuristics (equipment-code suffixes
    like "UP400St" are NOT stripped). This is honestly low/medium-confidence
    evidence for the operator to edit, not a claim of accuracy. Returns None
    only when no identifier prefix is found, or nothing recognizable
    remains."""

    stripped = folder_name.strip()
    match = _DOCUMENT_IDENTIFIER_RE.match(stripped)

    if match is None:
        return None

    remainder = stripped[match.end() :]
    remainder = _LEADING_SEPARATOR_RE.sub("", remainder).strip()

    return remainder or None


@dataclass(frozen=True)
class DrivePendingQuoteWorkspace:
    folder_id: str
    folder_name: str
    folder_web_url: str
    document_identifier: str | None
    created_time: datetime | None
    modified_time: datetime | None


class DrivePendingQuoteFolderRepository(Protocol):
    def list_known_drive_folder_ids(self) -> set[str]: ...


class DrivePendingQuoteReadService:
    def __init__(
        self,
        settings: Settings,
        *,
        repository: DrivePendingQuoteFolderRepository | None = None,
        drive_provider_factory: (
            Callable[[Settings], QuoteDriveWorkspaceProvider] | None
        ) = None,
    ) -> None:
        self._settings = settings
        self._repository: DrivePendingQuoteFolderRepository = (
            repository or PostgresCustomerQuoteReadRepository(settings)
        )
        self._drive_provider_factory = (
            drive_provider_factory or build_drive_workspace_provider
        )

    def list_drive_pending_workspaces(self) -> list[DrivePendingQuoteWorkspace]:
        provider = self._drive_provider_factory(self._settings)
        folders: list[DrivePendingFolder] = provider.list_pending_children()

        known_folder_ids = self._repository.list_known_drive_folder_ids()

        return [
            DrivePendingQuoteWorkspace(
                folder_id=folder.folder_id,
                folder_name=folder.folder_name,
                folder_web_url=folder.folder_web_url,
                document_identifier=parse_drive_pending_document_identifier(
                    folder.folder_name
                ),
                created_time=folder.created_time,
                modified_time=folder.modified_time,
            )
            for folder in folders
            if folder.folder_id not in known_folder_ids
        ]
