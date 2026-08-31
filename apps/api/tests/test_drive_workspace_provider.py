"""CRM-Q1 tests for the Google Drive quote-workspace adapter boundary.

No test here (or anywhere in this suite) talks to Google: the adapter is
exercised through a deterministic fake transport.
"""

from __future__ import annotations

from typing import Any

import pytest

from origenlab_api.drive.errors import DriveProvisioningError
from origenlab_api.drive.google_drive import (
    DRIVE_ARTIFACT_PROPERTY,
    DRIVE_QUOTE_ID_PROPERTY,
    DriveTransportResponse,
    DriveTransportTimeoutError,
    DriveTransportUnavailableError,
    GoogleDriveQuoteWorkspaceProvider,
)


QUOTE_ID = "quote_" + "a" * 32


class FakeTransport:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.responses: list[DriveTransportResponse | Exception] = []

    def queue(self, *responses: DriveTransportResponse | Exception) -> None:
        self.responses.extend(responses)

    def request(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> DriveTransportResponse:
        self.calls.append(
            {
                "method": method,
                "url": url,
                "params": dict(params or {}),
                "json_body": json_body,
            }
        )

        if not self.responses:
            raise AssertionError("Unexpected transport request")

        result = self.responses.pop(0)

        if isinstance(result, Exception):
            raise result

        return result


def _provider(transport: FakeTransport) -> GoogleDriveQuoteWorkspaceProvider:
    return GoogleDriveQuoteWorkspaceProvider(
        transport=transport,
        root_folder_id="root-folder-1",
        template_file_id="template-file-1",
    )


def test_find_folder_queries_by_app_properties_and_returns_none_when_absent() -> None:
    transport = FakeTransport()
    transport.queue(DriveTransportResponse(status_code=200, body={"files": []}))

    assert _provider(transport).find_folder(QUOTE_ID) is None

    call = transport.calls[0]

    assert call["method"] == "GET"
    assert call["url"].endswith("/drive/v3/files")

    query = call["params"]["q"]

    assert f"key='{DRIVE_QUOTE_ID_PROPERTY}' and value='{QUOTE_ID}'" in query
    assert f"key='{DRIVE_ARTIFACT_PROPERTY}' and value='quote_folder'" in query
    assert "mimeType='application/vnd.google-apps.folder'" in query
    assert "trashed=false" in query
    assert "'root-folder-1' in parents" in query


def test_find_folder_returns_existing_reference() -> None:
    transport = FakeTransport()
    transport.queue(
        DriveTransportResponse(
            status_code=200,
            body={
                "files": [
                    {
                        "id": "folder-1",
                        "webViewLink": "https://drive.google.com/drive/folders/folder-1",
                    }
                ]
            },
        )
    )

    folder = _provider(transport).find_folder(QUOTE_ID)

    assert folder is not None
    assert folder.file_id == "folder-1"
    assert folder.web_url == "https://drive.google.com/drive/folders/folder-1"


def test_create_folder_stamps_quote_identity_app_properties() -> None:
    transport = FakeTransport()
    transport.queue(
        DriveTransportResponse(
            status_code=200,
            body={
                "id": "folder-1",
                "webViewLink": "https://drive.google.com/drive/folders/folder-1",
            },
        )
    )

    folder = _provider(transport).create_folder(
        QUOTE_ID,
        name="CN011729 — Centrífuga CEAF",
    )

    assert folder.file_id == "folder-1"

    call = transport.calls[0]

    assert call["method"] == "POST"
    assert call["url"].endswith("/drive/v3/files")

    body = call["json_body"]

    assert body["name"] == "CN011729 — Centrífuga CEAF"
    assert body["mimeType"] == "application/vnd.google-apps.folder"
    assert body["parents"] == ["root-folder-1"]
    assert body["appProperties"][DRIVE_QUOTE_ID_PROPERTY] == QUOTE_ID
    assert body["appProperties"][DRIVE_ARTIFACT_PROPERTY] == "quote_folder"


def test_find_sheet_scopes_query_to_folder() -> None:
    transport = FakeTransport()
    transport.queue(DriveTransportResponse(status_code=200, body={"files": []}))

    assert _provider(transport).find_sheet(QUOTE_ID, folder_id="folder-1") is None

    query = transport.calls[0]["params"]["q"]

    assert f"key='{DRIVE_ARTIFACT_PROPERTY}' and value='quote_sheet'" in query
    assert "'folder-1' in parents" in query


def test_copy_template_sheet_copies_into_folder_with_identity() -> None:
    transport = FakeTransport()
    transport.queue(
        DriveTransportResponse(
            status_code=200,
            body={
                "id": "sheet-1",
                "webViewLink": "https://docs.google.com/spreadsheets/d/sheet-1",
            },
        )
    )

    sheet = _provider(transport).copy_template_sheet(
        QUOTE_ID,
        folder_id="folder-1",
        name="CN011729 — Centrífuga CEAF",
    )

    assert sheet.file_id == "sheet-1"

    call = transport.calls[0]

    assert call["method"] == "POST"
    assert call["url"].endswith("/drive/v3/files/template-file-1/copy")

    body = call["json_body"]

    assert body["parents"] == ["folder-1"]
    assert body["appProperties"][DRIVE_QUOTE_ID_PROPERTY] == QUOTE_ID
    assert body["appProperties"][DRIVE_ARTIFACT_PROPERTY] == "quote_sheet"


def test_timeout_maps_to_redacted_drive_timeout_category() -> None:
    transport = FakeTransport()
    transport.queue(DriveTransportTimeoutError("socket timeout to 10.0.0.1"))

    with pytest.raises(DriveProvisioningError) as excinfo:
        _provider(transport).find_folder(QUOTE_ID)

    assert excinfo.value.category == "drive_timeout"
    assert "10.0.0.1" not in str(excinfo.value)


def test_connection_failure_maps_to_drive_unavailable() -> None:
    transport = FakeTransport()
    transport.queue(DriveTransportUnavailableError("conn refused"))

    with pytest.raises(DriveProvisioningError) as excinfo:
        _provider(transport).find_folder(QUOTE_ID)

    assert excinfo.value.category == "drive_unavailable"


@pytest.mark.parametrize(
    ("status_code", "category"),
    [
        (401, "drive_permission_denied"),
        (403, "drive_permission_denied"),
        (404, "drive_not_found"),
        (429, "drive_unavailable"),
        (500, "drive_unavailable"),
        (503, "drive_unavailable"),
        (418, "drive_error"),
    ],
)
def test_http_errors_map_to_redacted_categories(
    status_code: int,
    category: str,
) -> None:
    transport = FakeTransport()
    transport.queue(
        DriveTransportResponse(
            status_code=status_code,
            body={"error": {"message": "secret-internal-detail token=abc123"}},
        )
    )

    with pytest.raises(DriveProvisioningError) as excinfo:
        _provider(transport).find_folder(QUOTE_ID)

    assert excinfo.value.category == category
    assert "secret-internal-detail" not in str(excinfo.value)
    assert "abc123" not in str(excinfo.value)


def test_non_https_web_link_from_provider_is_rejected() -> None:
    transport = FakeTransport()
    transport.queue(
        DriveTransportResponse(
            status_code=200,
            body={
                "files": [
                    {
                        "id": "folder-1",
                        "webViewLink": "http://drive.google.com/insecure",
                    }
                ]
            },
        )
    )

    with pytest.raises(DriveProvisioningError) as excinfo:
        _provider(transport).find_folder(QUOTE_ID)

    assert excinfo.value.category == "drive_error"
