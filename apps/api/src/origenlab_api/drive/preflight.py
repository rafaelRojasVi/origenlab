"""Read-only Drive configuration/preflight boundary (CRM-Q1).

Lets an operator verify a Drive configuration before activating quote
workspace creation: credentials load, the root folder is a writable,
non-trashed destination whose storage model matches the configured auth
mode, and the template is readable, non-trashed, and copyable. Nothing here
mutates Drive state.

The result carries only a redacted step/category pair -- never a token,
credential path, file content, or provider response.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from origenlab_api.drive.errors import DriveProvisioningError
from origenlab_api.drive.factory import build_drive_workspace_provider
from origenlab_api.drive.protocol import QuoteDriveWorkspaceProvider
from origenlab_api.settings import Settings


@dataclass(frozen=True)
class DrivePreflightResult:
    ok: bool
    step: str | None
    category: str | None


def run_drive_preflight(
    settings: Settings,
    *,
    provider_factory: (
        Callable[[Settings], QuoteDriveWorkspaceProvider] | None
    ) = None,
) -> DrivePreflightResult:
    factory = provider_factory or build_drive_workspace_provider

    try:
        provider = factory(settings)
    except DriveProvisioningError as exc:
        return DrivePreflightResult(
            ok=False, step="credentials", category=exc.category
        )

    try:
        provider.verify_destination()
    except DriveProvisioningError as exc:
        return DrivePreflightResult(
            ok=False, step="destination", category=exc.category
        )

    try:
        provider.verify_template()
    except DriveProvisioningError as exc:
        return DrivePreflightResult(
            ok=False, step="template", category=exc.category
        )

    return DrivePreflightResult(ok=True, step=None, category=None)
