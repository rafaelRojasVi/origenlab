"""Drive workspace provider factory (CRM-Q1).

Fails closed with redacted categories until the operator has explicitly
configured the Drive quotations workspace:

* ``ORIGENLAB_DRIVE_QUOTES_ROOT_FOLDER_ID`` + ``ORIGENLAB_DRIVE_QUOTE_TEMPLATE_FILE_ID``
  missing/incomplete -> ``drive_not_configured``;
* ``ORIGENLAB_DRIVE_AUTH_MODE`` missing or unknown ->
  ``drive_auth_mode_not_configured``;
* ``service_account_shared_drive`` mode without
  ``ORIGENLAB_DRIVE_SHARED_DRIVE_ID`` -> ``drive_auth_mode_incompatible``
  (service accounts have no My Drive storage quota and cannot own files:
  they are only valid against a Shared Drive destination);
* ``ORIGENLAB_DRIVE_CREDENTIALS_FILE`` missing or unreadable ->
  ``drive_credentials_not_configured``;
* the optional ``google-auth`` dependency not installed while Drive is
  otherwise configured -> ``drive_dependency_missing`` (a deployment
  misconfiguration, deliberately distinct from "not configured").

The credentials JSON file is read only by google-auth at token time; its
contents are never logged, stored, or attached to errors. Installing the
credential dependency is an explicit activation step:
``uv sync --extra drive`` (see pyproject ``[project.optional-dependencies]``).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from origenlab_api.drive.errors import DriveProvisioningError
from origenlab_api.drive.google_drive import (
    DriveTransportResponse,
    DriveTransportTimeoutError,
    DriveTransportUnavailableError,
    GoogleDriveQuoteWorkspaceProvider,
)
from origenlab_api.settings import Settings


AUTH_MODE_AUTHORIZED_USER = "authorized_user_my_drive"
AUTH_MODE_SERVICE_ACCOUNT = "service_account_shared_drive"

_VALID_AUTH_MODES = frozenset(
    {AUTH_MODE_AUTHORIZED_USER, AUTH_MODE_SERVICE_ACCOUNT}
)

# Full Drive scope: the operator shares the quotations root folder and the
# master template with the configured identity; the narrower drive.file scope
# cannot see items merely shared to the account.
_DRIVE_SCOPE = "https://www.googleapis.com/auth/drive"

_REQUEST_TIMEOUT_SECONDS = 20.0


def _build_authorized_user_token_supplier(
    credential_file: Path,
) -> Callable[[], str]:
    """Bearer-token supplier from an authorized-user credentials JSON.

    The file must carry an offline refresh token (client id/secret +
    refresh_token); this is the supported mode for a personal My Drive
    destination. Imported lazily: ``google-auth`` is an optional dependency
    installed only when Drive provisioning is activated.
    """

    from google.auth.transport.requests import Request  # type: ignore[import-not-found]
    from google.oauth2.credentials import Credentials  # type: ignore[import-not-found]

    credentials = Credentials.from_authorized_user_file(
        str(credential_file),
        scopes=[_DRIVE_SCOPE],
    )

    def supply_token() -> str:
        if not credentials.valid:
            credentials.refresh(Request())
        return str(credentials.token)

    return supply_token


def _build_service_account_token_supplier(
    credential_file: Path,
) -> Callable[[], str]:
    """Bearer-token supplier from a service-account JSON file.

    Imported lazily: ``google-auth`` is an optional dependency installed only
    when Drive provisioning is activated (``uv sync --extra drive``).
    """

    from google.auth.transport.requests import Request  # type: ignore[import-not-found]
    from google.oauth2 import service_account  # type: ignore[import-not-found]

    credentials = service_account.Credentials.from_service_account_file(
        str(credential_file),
        scopes=[_DRIVE_SCOPE],
    )

    def supply_token() -> str:
        if not credentials.valid:
            credentials.refresh(Request())
        return str(credentials.token)

    return supply_token


class HttpxDriveTransport:
    """Minimal HTTPS transport for the Drive REST API."""

    def __init__(self, token_supplier: Callable[[], str]) -> None:
        self._token_supplier = token_supplier

    def request(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> DriveTransportResponse:
        import httpx

        try:
            response = httpx.request(
                method,
                url,
                params=params,
                json=json_body,
                headers={
                    "Authorization": f"Bearer {self._token_supplier()}",
                },
                timeout=_REQUEST_TIMEOUT_SECONDS,
            )
        except httpx.TimeoutException as exc:
            raise DriveTransportTimeoutError("drive request timed out") from exc
        except httpx.HTTPError as exc:
            raise DriveTransportUnavailableError(
                "drive request failed"
            ) from exc

        try:
            body = response.json()
        except ValueError:
            body = {}

        if not isinstance(body, dict):
            body = {}

        return DriveTransportResponse(
            status_code=response.status_code,
            body=body,
        )


def build_drive_workspace_provider(
    settings: Settings,
) -> GoogleDriveQuoteWorkspaceProvider:
    root_folder_id = (settings.drive_quotes_root_folder_id or "").strip()
    template_file_id = (settings.drive_quote_template_file_id or "").strip()

    if not root_folder_id or not template_file_id:
        raise DriveProvisioningError("drive_not_configured")

    auth_mode = (settings.drive_auth_mode or "").strip()

    if auth_mode not in _VALID_AUTH_MODES:
        raise DriveProvisioningError("drive_auth_mode_not_configured")

    shared_drive_id = (settings.drive_shared_drive_id or "").strip() or None

    if auth_mode == AUTH_MODE_SERVICE_ACCOUNT and shared_drive_id is None:
        raise DriveProvisioningError("drive_auth_mode_incompatible")

    credential_file = settings.drive_credentials_file

    if credential_file is None:
        raise DriveProvisioningError("drive_credentials_not_configured")

    credential_path = credential_file.expanduser()

    if not credential_path.is_file():
        raise DriveProvisioningError("drive_credentials_not_configured")

    supplier_builder = (
        _build_authorized_user_token_supplier
        if auth_mode == AUTH_MODE_AUTHORIZED_USER
        else _build_service_account_token_supplier
    )

    try:
        token_supplier = supplier_builder(credential_path)
    except ImportError as exc:
        raise DriveProvisioningError("drive_dependency_missing") from exc
    except Exception as exc:
        # Malformed credential file etc. -- never leak details.
        raise DriveProvisioningError(
            "drive_credentials_not_configured"
        ) from exc

    return GoogleDriveQuoteWorkspaceProvider(
        transport=HttpxDriveTransport(token_supplier),
        root_folder_id=root_folder_id,
        template_file_id=template_file_id,
        shared_drive_id=shared_drive_id,
    )
