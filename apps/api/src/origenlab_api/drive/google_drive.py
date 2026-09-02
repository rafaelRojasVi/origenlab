"""Google Drive v3 REST adapter for quote workspaces (CRM-Q1).

The adapter speaks to Drive through an injected ``DriveTransport`` so its
logic (identity stamping, idempotent lookup, redacted error mapping) is fully
unit-testable without network or credentials. The production transport
(``HttpxDriveTransport``) is constructed only by the factory when Drive is
explicitly configured; no code path here initiates a network call on its own.

Artifacts are stamped with the internal quote identity via Drive
``appProperties`` so retries locate prior artifacts instead of creating
duplicates. Child artifacts inherit access from the configured root folder;
this adapter performs no sharing/permission mutations (V1 rule).

Hierarchy (CRM-Q1D): quote workspace folders are created/found under the
configured Pendientes container, never directly under the generic
quotations root. The root and (optional) Enviadas container exist for
preflight verification only -- no runtime path writes to them.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

from origenlab_api.drive.errors import DriveProvisioningError
from origenlab_api.drive.protocol import DriveFileRef, DrivePendingFolder


DRIVE_API_BASE_URL = "https://www.googleapis.com"

DRIVE_QUOTE_ID_PROPERTY = "origenlab_quote_id"
DRIVE_ARTIFACT_PROPERTY = "origenlab_artifact"

_FOLDER_MIME_TYPE = "application/vnd.google-apps.folder"
_FIELDS_SINGLE = "id,webViewLink"
_FIELDS_LIST = "files(id,webViewLink)"
_FIELDS_PENDING_LIST = (
    "nextPageToken,files(id,name,webViewLink,createdTime,modifiedTime)"
)


class DriveTransportTimeoutError(RuntimeError):
    """The transport timed out talking to Drive."""


class DriveTransportUnavailableError(RuntimeError):
    """The transport could not reach Drive."""


class DriveCredentialsError(RuntimeError):
    """Obtaining/refreshing the bearer token failed at the credential
    boundary (e.g. a google-auth ``RefreshError`` or a bounded refresh
    timeout). Raised by the token_supplier closure built in
    ``origenlab_api.drive.factory``; mapped here to a redacted
    ``DriveProvisioningError`` category the same as every other transport
    failure -- the underlying provider/library message must never reach
    durable state, API responses, or the UI."""


@dataclass(frozen=True)
class DriveTransportResponse:
    status_code: int
    body: dict[str, Any]


class DriveTransport(Protocol):
    def request(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> DriveTransportResponse:
        """Perform one HTTPS request against the Drive API."""


def _category_for_status(status_code: int) -> str:
    if status_code in (401, 403):
        return "drive_permission_denied"
    if status_code == 404:
        return "drive_not_found"
    if status_code == 429 or status_code >= 500:
        return "drive_unavailable"
    return "drive_error"


def _escape_query_value(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'")


def _owner_emails_contain(owners: list[dict[str, Any]], expected_email: str) -> bool:
    expected = expected_email.strip().lower()
    emails = {str(owner.get("emailAddress") or "").strip().lower() for owner in owners}
    return expected in emails


def _safe_ref(item: dict[str, Any]) -> DriveFileRef:
    file_id = str(item.get("id") or "").strip()
    web_url = str(item.get("webViewLink") or "").strip()

    if not file_id or not web_url.startswith("https://"):
        # Never let a non-https or empty reference reach durable state.
        raise DriveProvisioningError("drive_error")

    return DriveFileRef(file_id=file_id, web_url=web_url)


def _parse_drive_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        # Drive RFC3339 timestamps end in "Z"; fromisoformat needs "+00:00".
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _safe_pending_folder(item: dict[str, Any]) -> DrivePendingFolder | None:
    folder_id = str(item.get("id") or "").strip()
    folder_name = str(item.get("name") or "").strip()
    web_url = str(item.get("webViewLink") or "").strip()

    if not folder_id or not folder_name or not web_url.startswith("https://"):
        # This is a read-only visibility projection, not a mutation path:
        # skip a malformed row instead of failing the whole listing.
        return None

    return DrivePendingFolder(
        folder_id=folder_id,
        folder_name=folder_name,
        folder_web_url=web_url,
        created_time=_parse_drive_timestamp(item.get("createdTime")),
        modified_time=_parse_drive_timestamp(item.get("modifiedTime")),
    )


class GoogleDriveQuoteWorkspaceProvider:
    def __init__(
        self,
        *,
        transport: DriveTransport,
        root_folder_id: str,
        pending_folder_id: str,
        template_file_id: str,
        sent_folder_id: str | None = None,
        shared_drive_id: str | None = None,
    ) -> None:
        self._transport = transport
        self._root_folder_id = root_folder_id
        self._pending_folder_id = pending_folder_id
        self._sent_folder_id = sent_folder_id
        self._template_file_id = template_file_id
        self._shared_drive_id = shared_drive_id

    @property
    def shared_drive_id(self) -> str | None:
        return self._shared_drive_id

    @property
    def root_folder_id(self) -> str:
        return self._root_folder_id

    @property
    def pending_folder_id(self) -> str:
        return self._pending_folder_id

    @property
    def sent_folder_id(self) -> str | None:
        return self._sent_folder_id

    def _request(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        try:
            response = self._transport.request(
                method,
                url,
                params=params,
                json_body=json_body,
            )
        except DriveTransportTimeoutError as exc:
            raise DriveProvisioningError("drive_timeout") from exc
        except DriveTransportUnavailableError as exc:
            raise DriveProvisioningError("drive_unavailable") from exc
        except DriveCredentialsError as exc:
            raise DriveProvisioningError("drive_credentials_invalid") from exc

        if response.status_code != 200:
            raise DriveProvisioningError(
                _category_for_status(response.status_code)
            )

        return response.body

    def _find_artifact(
        self,
        quote_id: str,
        *,
        artifact: str,
        parent_id: str,
        mime_clause: str | None,
    ) -> DriveFileRef | None:
        clauses = [
            "appProperties has { key='%s' and value='%s' }"
            % (DRIVE_QUOTE_ID_PROPERTY, _escape_query_value(quote_id)),
            "appProperties has { key='%s' and value='%s' }"
            % (DRIVE_ARTIFACT_PROPERTY, artifact),
            "'%s' in parents" % _escape_query_value(parent_id),
            "trashed=false",
        ]

        if mime_clause is not None:
            clauses.insert(2, mime_clause)

        params: dict[str, Any] = {
            "q": " and ".join(clauses),
            "fields": _FIELDS_LIST,
            "pageSize": 2,
            # Oldest first: concurrent racers converge on the same artifact.
            "orderBy": "createdTime",
            "supportsAllDrives": True,
            "includeItemsFromAllDrives": True,
        }

        if self._shared_drive_id is not None:
            params["corpora"] = "drive"
            params["driveId"] = self._shared_drive_id

        body = self._request(
            "GET",
            f"{DRIVE_API_BASE_URL}/drive/v3/files",
            params=params,
        )

        files = body.get("files") or []

        if not files:
            return None

        return _safe_ref(files[0])

    def verify_principal(self, expected_email: str) -> str:
        """Read-only check that the authenticated identity matches.

        Calls the Drive ``about.get`` boundary and compares the
        authenticated user's email against ``expected_email``,
        case-insensitively. Returns the actual authenticated email (safe
        metadata, never a token) on success; raises ``drive_principal_mismatch``
        otherwise -- before any mutation.
        """

        body = self._request(
            "GET",
            f"{DRIVE_API_BASE_URL}/drive/v3/about",
            params={"fields": "user(emailAddress)"},
        )

        actual = str((body.get("user") or {}).get("emailAddress") or "").strip()

        if not actual or actual.lower() != expected_email.strip().lower():
            raise DriveProvisioningError("drive_principal_mismatch")

        return actual

    def _verify_container(
        self,
        folder_id: str,
        *,
        expected_owner_email: str | None,
        invalid_category: str,
        check_parent: bool,
        mismatch_category: str | None = None,
    ) -> None:
        """Shared read-only usability check for a configured container
        folder (root / Pendientes / Enviadas).

        Verifies the folder is writable and non-trashed and that its storage
        model matches the configuration: when a Shared Drive is configured
        the folder must actually live in that Shared Drive (a service
        account pointed at a personal My Drive folder fails closed here,
        before any mutation). When ``check_parent`` is set, the folder must
        also be a direct child of the configured quotations root -- a
        Pendientes/Enviadas container pointed at the wrong folder is a real
        misconfiguration risk, not a hypothetical.

        When ``expected_owner_email`` is given and the folder actually
        carries owner metadata (My Drive items only -- Shared Drive items
        have no personal owner and are silently skipped), the folder must be
        owned by that email or this fails closed as
        ``drive_principal_mismatch``.
        """

        fields = (
            "id,mimeType,trashed,driveId,owners(emailAddress),"
            "capabilities/canAddChildren"
        )
        if check_parent:
            fields += ",parents"

        body = self._request(
            "GET",
            f"{DRIVE_API_BASE_URL}/drive/v3/files/{folder_id}",
            params={
                "fields": fields,
                "supportsAllDrives": True,
            },
        )

        capabilities = body.get("capabilities") or {}

        if (
            body.get("mimeType") != _FOLDER_MIME_TYPE
            or body.get("trashed") is not False
            or capabilities.get("canAddChildren") is not True
        ):
            raise DriveProvisioningError(invalid_category)

        if self._shared_drive_id is not None:
            if body.get("driveId") != self._shared_drive_id:
                raise DriveProvisioningError("drive_auth_mode_incompatible")

        if check_parent:
            parents = body.get("parents") or []
            if self._root_folder_id not in parents:
                assert mismatch_category is not None
                raise DriveProvisioningError(mismatch_category)

        if expected_owner_email is not None:
            owners = body.get("owners") or []
            if owners and not _owner_emails_contain(owners, expected_owner_email):
                raise DriveProvisioningError("drive_principal_mismatch")

    def verify_root(self, *, expected_owner_email: str | None = None) -> None:
        """Read-only, preflight-only check that the quotations root itself
        is a usable folder. The root has no expected parent."""

        self._verify_container(
            self._root_folder_id,
            expected_owner_email=expected_owner_email,
            invalid_category="drive_root_invalid",
            check_parent=False,
        )

    def verify_destination(self, *, expected_owner_email: str | None = None) -> None:
        """Read-only check that the configured Pendientes container is a
        usable creation destination for new quote workspaces.

        This is the runtime, per-provisioning-attempt check (unlike
        ``verify_root``/``verify_sent``, which are preflight-only): it must
        stay a single Drive call so it doesn't change the provisioning
        attempt's worst-case call count.
        """

        self._verify_container(
            self._pending_folder_id,
            expected_owner_email=expected_owner_email,
            invalid_category="drive_pending_invalid",
            check_parent=True,
            mismatch_category="drive_pending_container_mismatch",
        )

    def verify_sent(self, *, expected_owner_email: str | None = None) -> None:
        """Read-only, preflight-only check that the configured Enviadas
        container (when configured) is a usable, correctly-parented folder.

        Callers must not invoke this when Enviadas is not configured; it
        fails closed as ``drive_not_configured`` rather than silently
        no-op-ing, since a preflight step that never actually checked
        anything must never be reported as passing.
        """

        if self._sent_folder_id is None:
            raise DriveProvisioningError("drive_not_configured")

        self._verify_container(
            self._sent_folder_id,
            expected_owner_email=expected_owner_email,
            invalid_category="drive_sent_invalid",
            check_parent=True,
            mismatch_category="drive_sent_container_mismatch",
        )

    def verify_template(
        self, *, expected_owner_email: str | None = None
    ) -> bool | None:
        """Read-only check that the template is readable and copyable.

        Ownership is informational only and never blocks activation (the
        template may legitimately be shared from another account): returns
        ``True``/``False`` when ``expected_owner_email`` is given and owner
        metadata is present, else ``None``.
        """

        body = self._request(
            "GET",
            f"{DRIVE_API_BASE_URL}/drive/v3/files/{self._template_file_id}",
            params={
                "fields": "id,trashed,owners(emailAddress),capabilities/canCopy",
                "supportsAllDrives": True,
            },
        )

        capabilities = body.get("capabilities") or {}

        if (
            body.get("trashed") is not False
            or capabilities.get("canCopy") is not True
        ):
            raise DriveProvisioningError("drive_template_invalid")

        if expected_owner_email is None:
            return None

        owners = body.get("owners") or []

        if not owners:
            return None

        return _owner_emails_contain(owners, expected_owner_email)

    def find_folder(self, quote_id: str) -> DriveFileRef | None:
        return self._find_artifact(
            quote_id,
            artifact="quote_folder",
            parent_id=self._pending_folder_id,
            mime_clause=f"mimeType='{_FOLDER_MIME_TYPE}'",
        )

    def create_folder(self, quote_id: str, *, name: str) -> DriveFileRef:
        # Every new quote workspace folder is created under the configured
        # Pendientes container, never directly under the generic quotations
        # root (CRM-Q1D).
        body = self._request(
            "POST",
            f"{DRIVE_API_BASE_URL}/drive/v3/files",
            params={
                "fields": _FIELDS_SINGLE,
                "supportsAllDrives": True,
            },
            json_body={
                "name": name,
                "mimeType": _FOLDER_MIME_TYPE,
                "parents": [self._pending_folder_id],
                "appProperties": {
                    DRIVE_QUOTE_ID_PROPERTY: quote_id,
                    DRIVE_ARTIFACT_PROPERTY: "quote_folder",
                },
            },
        )

        return _safe_ref(body)

    def list_pending_children(self) -> list[DrivePendingFolder]:
        # Read-only, non-recursive listing of direct children of Pendientes
        # only -- never Enviadas, never the template, never a mutation call.
        clauses = [
            "'%s' in parents" % _escape_query_value(self._pending_folder_id),
            f"mimeType='{_FOLDER_MIME_TYPE}'",
            "trashed=false",
        ]

        params: dict[str, Any] = {
            "q": " and ".join(clauses),
            "fields": _FIELDS_PENDING_LIST,
            "pageSize": 100,
            "orderBy": "createdTime",
            "supportsAllDrives": True,
            "includeItemsFromAllDrives": True,
        }

        if self._shared_drive_id is not None:
            params["corpora"] = "drive"
            params["driveId"] = self._shared_drive_id

        folders: list[DrivePendingFolder] = []
        page_token: str | None = None

        while True:
            request_params = dict(params)
            if page_token:
                request_params["pageToken"] = page_token

            body = self._request(
                "GET",
                f"{DRIVE_API_BASE_URL}/drive/v3/files",
                params=request_params,
            )

            for item in body.get("files") or []:
                folder = _safe_pending_folder(item)
                if folder is not None:
                    folders.append(folder)

            page_token = body.get("nextPageToken")
            if not page_token:
                break

        return folders

    def find_sheet(self, quote_id: str, *, folder_id: str) -> DriveFileRef | None:
        return self._find_artifact(
            quote_id,
            artifact="quote_sheet",
            parent_id=folder_id,
            mime_clause=None,
        )

    def copy_template_sheet(
        self,
        quote_id: str,
        *,
        folder_id: str,
        name: str,
    ) -> DriveFileRef:
        body = self._request(
            "POST",
            (
                f"{DRIVE_API_BASE_URL}/drive/v3/files/"
                f"{self._template_file_id}/copy"
            ),
            params={
                "fields": _FIELDS_SINGLE,
                "supportsAllDrives": True,
            },
            json_body={
                "name": name,
                "parents": [folder_id],
                "appProperties": {
                    DRIVE_QUOTE_ID_PROPERTY: quote_id,
                    DRIVE_ARTIFACT_PROPERTY: "quote_sheet",
                },
            },
        )

        return _safe_ref(body)
