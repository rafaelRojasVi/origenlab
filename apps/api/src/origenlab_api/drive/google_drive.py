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
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from origenlab_api.drive.errors import DriveProvisioningError
from origenlab_api.drive.protocol import DriveFileRef


DRIVE_API_BASE_URL = "https://www.googleapis.com"

DRIVE_QUOTE_ID_PROPERTY = "origenlab_quote_id"
DRIVE_ARTIFACT_PROPERTY = "origenlab_artifact"

_FOLDER_MIME_TYPE = "application/vnd.google-apps.folder"
_FIELDS_SINGLE = "id,webViewLink"
_FIELDS_LIST = "files(id,webViewLink)"


class DriveTransportTimeoutError(RuntimeError):
    """The transport timed out talking to Drive."""


class DriveTransportUnavailableError(RuntimeError):
    """The transport could not reach Drive."""


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


def _safe_ref(item: dict[str, Any]) -> DriveFileRef:
    file_id = str(item.get("id") or "").strip()
    web_url = str(item.get("webViewLink") or "").strip()

    if not file_id or not web_url.startswith("https://"):
        # Never let a non-https or empty reference reach durable state.
        raise DriveProvisioningError("drive_error")

    return DriveFileRef(file_id=file_id, web_url=web_url)


class GoogleDriveQuoteWorkspaceProvider:
    def __init__(
        self,
        *,
        transport: DriveTransport,
        root_folder_id: str,
        template_file_id: str,
    ) -> None:
        self._transport = transport
        self._root_folder_id = root_folder_id
        self._template_file_id = template_file_id

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

        body = self._request(
            "GET",
            f"{DRIVE_API_BASE_URL}/drive/v3/files",
            params={
                "q": " and ".join(clauses),
                "fields": _FIELDS_LIST,
                "pageSize": 2,
                "supportsAllDrives": True,
                "includeItemsFromAllDrives": True,
            },
        )

        files = body.get("files") or []

        if not files:
            return None

        return _safe_ref(files[0])

    def find_folder(self, quote_id: str) -> DriveFileRef | None:
        return self._find_artifact(
            quote_id,
            artifact="quote_folder",
            parent_id=self._root_folder_id,
            mime_clause=f"mimeType='{_FOLDER_MIME_TYPE}'",
        )

    def create_folder(self, quote_id: str, *, name: str) -> DriveFileRef:
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
                "parents": [self._root_folder_id],
                "appProperties": {
                    DRIVE_QUOTE_ID_PROPERTY: quote_id,
                    DRIVE_ARTIFACT_PROPERTY: "quote_folder",
                },
            },
        )

        return _safe_ref(body)

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
