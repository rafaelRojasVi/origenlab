"""Provider protocol for quote Drive workspaces.

The service layer orchestrates provisioning through this protocol only, so a
deterministic fake can stand in for Google in every test. Providers must be
idempotent-by-lookup: artifacts are stamped with the internal quote identity
(Drive ``appProperties``) so a retry can find prior artifacts instead of
creating duplicates.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class DriveFileRef:
    """A durable, safe reference to a Drive artifact."""

    file_id: str
    web_url: str


class QuoteDriveWorkspaceProvider(Protocol):
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
