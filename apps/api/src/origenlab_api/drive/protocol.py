"""Provider protocol for quote Drive workspaces.

The service layer orchestrates provisioning through this protocol only, so a
deterministic fake can stand in for Google in every test. Providers must be
idempotent-by-lookup: artifacts are stamped with the internal quote identity
(Drive ``appProperties``) so a retry can find prior artifacts instead of
creating duplicates.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


@dataclass(frozen=True)
class DriveFileRef:
    """A durable, safe reference to a Drive artifact."""

    file_id: str
    web_url: str


@dataclass(frozen=True)
class DrivePendingFolder:
    """Safe metadata for one direct child folder of the Pendientes
    container -- an operational-visibility read, never a durable CRM
    reference."""

    folder_id: str
    folder_name: str
    folder_web_url: str
    created_time: datetime | None
    modified_time: datetime | None


class QuoteDriveWorkspaceProvider(Protocol):
    def verify_principal(self, expected_email: str) -> str:
        """Read-only identity check; raises drive_principal_mismatch when the
        authenticated Drive identity does not match expected_email."""

    def verify_root(self, *, expected_owner_email: str | None = None) -> None:
        """Read-only, preflight-only check that the quotations root itself
        is a usable folder; raises before any mutation when unusable or
        incompatible with the auth mode."""

    def verify_destination(self, *, expected_owner_email: str | None = None) -> None:
        """Read-only check that the configured Pendientes container is a
        usable quote-workspace creation destination; raises before any
        mutation when it is unusable, not parented by the configured root,
        incompatible with the auth mode, or (when expected_owner_email is
        given and ownership metadata is present) not owned by the expected
        principal."""

    def verify_sent(self, *, expected_owner_email: str | None = None) -> None:
        """Read-only, preflight-only check that the configured Enviadas
        container is a usable, correctly-parented folder. Callers must only
        invoke this when Enviadas is configured."""

    def verify_template(
        self, *, expected_owner_email: str | None = None
    ) -> bool | None:
        """Read-only template check; raises when unusable. Ownership against
        expected_owner_email is informational only (never raises) and
        returns True/False/None."""

    def find_folder(self, quote_id: str) -> DriveFileRef | None:
        """Locate a previously created quote folder by internal identity."""

    def create_folder(self, quote_id: str, *, name: str) -> DriveFileRef:
        """Create the quote folder under the configured quotations root.

        Access is inherited from the root folder; providers must not apply
        any additional sharing/permission mutations (V1 rule).
        """

    def find_sheet(self, quote_id: str, *, folder_id: str) -> DriveFileRef | None:
        """Locate a previously copied working sheet inside the folder."""

    def copy_template_sheet(
        self,
        quote_id: str,
        *,
        folder_id: str,
        name: str,
    ) -> DriveFileRef:
        """Copy the master quotation template into the quote folder."""

    def list_pending_children(self) -> list[DrivePendingFolder]:
        """Read-only, non-recursive listing of direct child folders under
        the configured Pendientes container, for operational visibility.

        Never recurses, never inspects Enviadas or the template, and never
        mutates Drive. A Drive-only folder returned here is not a durable
        CRM quote -- callers must not fabricate one from this data."""
