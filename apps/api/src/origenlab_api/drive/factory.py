"""Drive workspace provider factory (CRM-Q1).

Fails closed with redacted categories until the operator has explicitly
configured the Drive quotations workspace:

* ``ORIGENLAB_DRIVE_QUOTES_ROOT_FOLDER_ID`` + ``ORIGENLAB_DRIVE_QUOTES_PENDING_FOLDER_ID``
  + ``ORIGENLAB_DRIVE_QUOTE_TEMPLATE_FILE_ID`` missing/incomplete ->
  ``drive_not_configured`` (``ORIGENLAB_DRIVE_QUOTES_SENT_FOLDER_ID`` is
  optional: verified read-only by preflight when set, never required for
  provisioning -- no ``sent`` lifecycle exists yet);
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

import concurrent.futures
from pathlib import Path
from typing import Any, Callable

from origenlab_api.drive.errors import DriveProvisioningError
from origenlab_api.drive.google_drive import (
    DriveCredentialsError,
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
# cannot see items merely shared to the account. This is the ONLY scope this
# backend requests -- never Gmail, Sheets, or profile scopes. Also used by
# scripts/authorize_drive_user.py so the one-time OAuth bootstrap and the
# runtime factory can never drift apart.
DRIVE_OAUTH_SCOPE = "https://www.googleapis.com/auth/drive"

_REQUEST_TIMEOUT_SECONDS = 20.0

# Single source of truth with _REQUEST_TIMEOUT_SECONDS: credentials.refresh()
# forwards no timeout of its own to google-auth's Request() (it calls the
# transport with timeout=None internally), so without this wrapper a stalled
# token endpoint could hang indefinitely. Bounding it to the same value as
# every other Drive HTTP call keeps one call-count arithmetic usable for the
# provisioning-attempt lease (see customer_quotes.PROVISION_ATTEMPT_LEASE_SECONDS).
_TOKEN_REFRESH_TIMEOUT_SECONDS = _REQUEST_TIMEOUT_SECONDS


def _refresh_with_timeout(credentials: Any) -> None:
    """Run ``credentials.refresh(Request())`` under a hard wall-clock bound.

    google-auth's own ``Request`` wrapper passes no timeout through to the
    underlying HTTP call unless the caller supplies one explicitly, and
    ``Credentials.refresh()`` has no timeout parameter of its own -- so a
    stalled token endpoint can otherwise hang the calling thread forever.
    Running the refresh in a worker thread with a bounded ``future.result()``
    enforces a finite timeout regardless of google-auth/requests internals.

    Raises ``DriveCredentialsError`` (never the raw google-auth exception)
    on any credential failure or timeout -- the caller must never see a raw
    provider/library message.
    """

    from google.auth.exceptions import GoogleAuthError  # type: ignore[import-not-found]
    from google.auth.transport.requests import Request  # type: ignore[import-not-found]

    def _do_refresh() -> None:
        credentials.refresh(Request())

    # Deliberately not `with ThreadPoolExecutor() as executor:` -- that
    # context manager's __exit__ calls shutdown(wait=True), which would
    # block the caller until the worker thread finishes even after we have
    # already given up on it via future.result(timeout=...). A genuinely
    # hung refresh leaks one worker thread (Python threads cannot be force-
    # killed); shutdown(wait=False) accepts that tradeoff in exchange for
    # never blocking the caller past the bound below.
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    future = executor.submit(_do_refresh)
    try:
        future.result(timeout=_TOKEN_REFRESH_TIMEOUT_SECONDS)
    except concurrent.futures.TimeoutError as exc:
        raise DriveCredentialsError("token refresh timed out") from exc
    except GoogleAuthError as exc:
        raise DriveCredentialsError("token refresh failed") from exc
    finally:
        executor.shutdown(wait=False)


def _build_authorized_user_token_supplier(
    credential_file: Path,
) -> Callable[[], str]:
    """Bearer-token supplier from an authorized-user credentials JSON.

    The file must carry an offline refresh token (client id/secret +
    refresh_token); this is the supported mode for a personal My Drive
    destination. Imported lazily: ``google-auth`` is an optional dependency
    installed only when Drive provisioning is activated.
    """

    from google.oauth2.credentials import Credentials  # type: ignore[import-not-found]

    credentials = Credentials.from_authorized_user_file(
        str(credential_file),
        scopes=[DRIVE_OAUTH_SCOPE],
    )

    def supply_token() -> str:
        if not credentials.valid:
            _refresh_with_timeout(credentials)
        return str(credentials.token)

    return supply_token


def _build_service_account_token_supplier(
    credential_file: Path,
) -> Callable[[], str]:
    """Bearer-token supplier from a service-account JSON file.

    Imported lazily: ``google-auth`` is an optional dependency installed only
    when Drive provisioning is activated (``uv sync --extra drive``).
    """

    from google.oauth2 import service_account  # type: ignore[import-not-found]

    credentials = service_account.Credentials.from_service_account_file(
        str(credential_file),
        scopes=[DRIVE_OAUTH_SCOPE],
    )

    def supply_token() -> str:
        if not credentials.valid:
            _refresh_with_timeout(credentials)
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
    pending_folder_id = (settings.drive_quotes_pending_folder_id or "").strip()

    if not root_folder_id or not pending_folder_id:
        raise DriveProvisioningError("drive_not_configured")

    template_provisioning_enabled = (
        settings.drive_quote_template_provisioning_enabled
    )
    configured_template_file_id = (
        settings.drive_quote_template_file_id or ""
    ).strip() or None

    if template_provisioning_enabled and not configured_template_file_id:
        raise DriveProvisioningError("drive_not_configured")

    # Template-document provisioning is an explicit, separately-activated
    # step: a leftover/misconfigured template ID must never be used while
    # the gate is off (ORIGENLAB_DRIVE_QUOTE_TEMPLATE_PROVISIONING_ENABLED).
    template_file_id = (
        configured_template_file_id if template_provisioning_enabled else None
    )

    sent_folder_id = (settings.drive_quotes_sent_folder_id or "").strip() or None

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
        pending_folder_id=pending_folder_id,
        sent_folder_id=sent_folder_id,
        template_file_id=template_file_id,
        shared_drive_id=shared_drive_id,
    )
