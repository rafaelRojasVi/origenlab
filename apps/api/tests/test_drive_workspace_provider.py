"""CRM-Q1 tests for the Google Drive quote-workspace adapter boundary.

No test here (or anywhere in this suite) talks to Google: the adapter is
exercised through a deterministic fake transport.

CRM-Q1D hierarchy: quote folders are created/found under the configured
Pendientes container (``pending_folder_id``), never directly under the
generic quotations root. ``verify_root``/``verify_sent`` are preflight-only
checks; ``verify_destination`` is the runtime, per-provisioning-attempt
check and targets Pendientes.
"""

from __future__ import annotations

from typing import Any

import pytest

from origenlab_api.drive.errors import DriveProvisioningError
from origenlab_api.drive.google_drive import (
    DRIVE_ARTIFACT_PROPERTY,
    DRIVE_QUOTE_ID_PROPERTY,
    DriveCredentialsError,
    DriveTransportResponse,
    DriveTransportTimeoutError,
    DriveTransportUnavailableError,
    GoogleDriveQuoteWorkspaceProvider,
)


QUOTE_ID = "quote_" + "a" * 32

ROOT_FOLDER_ID = "root-folder-1"
PENDING_FOLDER_ID = "pending-folder-1"
SENT_FOLDER_ID = "sent-folder-1"


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


def _provider(
    transport: FakeTransport,
    *,
    shared_drive_id: str | None = None,
    sent_folder_id: str | None = None,
) -> GoogleDriveQuoteWorkspaceProvider:
    return GoogleDriveQuoteWorkspaceProvider(
        transport=transport,
        root_folder_id=ROOT_FOLDER_ID,
        pending_folder_id=PENDING_FOLDER_ID,
        template_file_id="template-file-1",
        sent_folder_id=sent_folder_id,
        shared_drive_id=shared_drive_id,
    )


def _container_info_body(folder_id: str, **overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "id": folder_id,
        "mimeType": "application/vnd.google-apps.folder",
        "trashed": False,
        "capabilities": {"canAddChildren": True},
    }
    body.update(overrides)
    return body


def _template_info_body(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "id": "template-file-1",
        "trashed": False,
        "capabilities": {"canCopy": True},
    }
    body.update(overrides)
    return body


def _owners(*emails: str) -> list[dict[str, str]]:
    return [{"emailAddress": email} for email in emails]


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
    assert f"'{PENDING_FOLDER_ID}' in parents" in query


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
    assert body["parents"] == [PENDING_FOLDER_ID]
    assert body["appProperties"][DRIVE_QUOTE_ID_PROPERTY] == QUOTE_ID
    assert body["appProperties"][DRIVE_ARTIFACT_PROPERTY] == "quote_folder"


def test_create_folder_never_uses_root_as_parent_when_pending_configured() -> None:
    # A quote workspace must never accidentally land directly under the
    # generic quotations root when Pendientes is explicitly configured.
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

    _provider(transport).create_folder(QUOTE_ID, name="CN011729")

    body = transport.calls[0]["json_body"]

    assert ROOT_FOLDER_ID not in body["parents"]


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


def test_credentials_error_maps_to_redacted_credentials_invalid_category() -> None:
    # A google-auth RefreshError (or any credential/token failure) surfaced
    # by the token_supplier boundary must never escape as a raw exception --
    # it must be caught here and redacted to a stable category, the same as
    # every other transport-level failure.
    transport = FakeTransport()
    transport.queue(
        DriveCredentialsError(
            "invalid_grant: token expired. client_secret=super-secret-value"
        )
    )

    with pytest.raises(DriveProvisioningError) as excinfo:
        _provider(transport).find_folder(QUOTE_ID)

    assert excinfo.value.category == "drive_credentials_invalid"
    assert "super-secret-value" not in str(excinfo.value)
    assert "client_secret" not in str(excinfo.value)


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


def test_find_folder_supports_both_storage_models_by_default() -> None:
    # My Drive and Shared Drive items must both be discoverable; ordering by
    # creation time makes concurrent racers converge on the oldest artifact.
    transport = FakeTransport()
    transport.queue(DriveTransportResponse(status_code=200, body={"files": []}))

    _provider(transport).find_folder(QUOTE_ID)

    params = transport.calls[0]["params"]

    assert params["supportsAllDrives"] is True
    assert params["includeItemsFromAllDrives"] is True
    assert params["orderBy"] == "createdTime"
    assert "corpora" not in params
    assert "driveId" not in params


def test_find_folder_scopes_to_configured_shared_drive() -> None:
    transport = FakeTransport()
    transport.queue(DriveTransportResponse(status_code=200, body={"files": []}))

    _provider(transport, shared_drive_id="shared-drive-1").find_folder(QUOTE_ID)

    params = transport.calls[0]["params"]

    assert params["corpora"] == "drive"
    assert params["driveId"] == "shared-drive-1"
    assert params["supportsAllDrives"] is True
    assert params["includeItemsFromAllDrives"] is True


def test_find_sheet_scopes_to_configured_shared_drive() -> None:
    transport = FakeTransport()
    transport.queue(DriveTransportResponse(status_code=200, body={"files": []}))

    _provider(transport, shared_drive_id="shared-drive-1").find_sheet(
        QUOTE_ID,
        folder_id="folder-1",
    )

    params = transport.calls[0]["params"]

    assert params["corpora"] == "drive"
    assert params["driveId"] == "shared-drive-1"


def test_verify_destination_accepts_writable_pending_folder() -> None:
    transport = FakeTransport()
    transport.queue(
        DriveTransportResponse(
            status_code=200,
            body=_container_info_body(PENDING_FOLDER_ID, parents=[ROOT_FOLDER_ID]),
        )
    )

    _provider(transport).verify_destination()

    call = transport.calls[0]

    assert call["method"] == "GET"
    assert call["url"].endswith(f"/drive/v3/files/{PENDING_FOLDER_ID}")
    assert call["params"]["supportsAllDrives"] is True
    assert "driveId" in call["params"]["fields"]
    assert "canAddChildren" in call["params"]["fields"]
    assert "parents" in call["params"]["fields"]


def test_verify_destination_accepts_matching_shared_drive_pending() -> None:
    transport = FakeTransport()
    transport.queue(
        DriveTransportResponse(
            status_code=200,
            body=_container_info_body(
                PENDING_FOLDER_ID,
                parents=[ROOT_FOLDER_ID],
                driveId="shared-drive-1",
            ),
        )
    )

    _provider(
        transport, shared_drive_id="shared-drive-1"
    ).verify_destination()


@pytest.mark.parametrize(
    "overrides",
    [
        {"mimeType": "application/vnd.google-apps.document"},
        {"trashed": True},
        {"capabilities": {"canAddChildren": False}},
        {"capabilities": {}},
    ],
)
def test_verify_destination_rejects_unusable_pending_folder(
    overrides: dict[str, Any],
) -> None:
    transport = FakeTransport()
    transport.queue(
        DriveTransportResponse(
            status_code=200,
            body=_container_info_body(
                PENDING_FOLDER_ID, parents=[ROOT_FOLDER_ID], **overrides
            ),
        )
    )

    with pytest.raises(DriveProvisioningError) as excinfo:
        _provider(transport).verify_destination()

    assert excinfo.value.category == "drive_pending_invalid"


def test_verify_destination_rejects_pending_not_parented_by_root() -> None:
    # A quote workspace must never be created under a Pendientes folder that
    # isn't actually inside the configured quotations root -- a real
    # misconfiguration risk, not a hypothetical.
    transport = FakeTransport()
    transport.queue(
        DriveTransportResponse(
            status_code=200,
            body=_container_info_body(
                PENDING_FOLDER_ID, parents=["some-other-folder"]
            ),
        )
    )

    with pytest.raises(DriveProvisioningError) as excinfo:
        _provider(transport).verify_destination()

    assert excinfo.value.category == "drive_pending_container_mismatch"


def test_verify_destination_rejects_pending_with_no_parents() -> None:
    transport = FakeTransport()
    transport.queue(
        DriveTransportResponse(
            status_code=200,
            body=_container_info_body(PENDING_FOLDER_ID),
        )
    )

    with pytest.raises(DriveProvisioningError) as excinfo:
        _provider(transport).verify_destination()

    assert excinfo.value.category == "drive_pending_container_mismatch"


@pytest.mark.parametrize(
    "pending_drive_id",
    [None, "another-shared-drive"],
)
def test_verify_destination_rejects_shared_drive_mismatch(
    pending_drive_id: str | None,
) -> None:
    body = _container_info_body(PENDING_FOLDER_ID, parents=[ROOT_FOLDER_ID])
    if pending_drive_id is not None:
        body["driveId"] = pending_drive_id

    transport = FakeTransport()
    transport.queue(DriveTransportResponse(status_code=200, body=body))

    with pytest.raises(DriveProvisioningError) as excinfo:
        _provider(
            transport,
            shared_drive_id="shared-drive-1",
        ).verify_destination()

    assert excinfo.value.category == "drive_auth_mode_incompatible"


def test_verify_destination_maps_missing_pending_to_drive_not_found() -> None:
    transport = FakeTransport()
    transport.queue(DriveTransportResponse(status_code=404, body={}))

    with pytest.raises(DriveProvisioningError) as excinfo:
        _provider(transport).verify_destination()

    assert excinfo.value.category == "drive_not_found"


def test_verify_destination_accepts_matching_owner() -> None:
    transport = FakeTransport()
    transport.queue(
        DriveTransportResponse(
            status_code=200,
            body=_container_info_body(
                PENDING_FOLDER_ID,
                parents=[ROOT_FOLDER_ID],
                owners=_owners("Contacto@OrigenLab.cl"),
            ),
        )
    )

    _provider(transport).verify_destination(
        expected_owner_email="contacto@origenlab.cl"
    )

    call = transport.calls[0]
    assert "owners(emailAddress)" in call["params"]["fields"]


def test_verify_destination_rejects_owner_mismatch() -> None:
    transport = FakeTransport()
    transport.queue(
        DriveTransportResponse(
            status_code=200,
            body=_container_info_body(
                PENDING_FOLDER_ID,
                parents=[ROOT_FOLDER_ID],
                owners=_owners("someone-else@gmail.com"),
            ),
        )
    )

    with pytest.raises(DriveProvisioningError) as excinfo:
        _provider(transport).verify_destination(
            expected_owner_email="contacto@origenlab.cl"
        )

    assert excinfo.value.category == "drive_principal_mismatch"


def test_verify_destination_skips_owner_check_when_owners_absent() -> None:
    # A Shared Drive item has no personal owner: skip, do not fail.
    transport = FakeTransport()
    transport.queue(
        DriveTransportResponse(
            status_code=200,
            body=_container_info_body(
                PENDING_FOLDER_ID,
                parents=[ROOT_FOLDER_ID],
                driveId="shared-drive-1",
                owners=[],
            ),
        )
    )

    _provider(
        transport, shared_drive_id="shared-drive-1"
    ).verify_destination(expected_owner_email="contacto@origenlab.cl")


def test_verify_root_accepts_writable_root_folder() -> None:
    transport = FakeTransport()
    transport.queue(
        DriveTransportResponse(
            status_code=200,
            body=_container_info_body(ROOT_FOLDER_ID),
        )
    )

    _provider(transport).verify_root()

    call = transport.calls[0]

    assert call["method"] == "GET"
    assert call["url"].endswith(f"/drive/v3/files/{ROOT_FOLDER_ID}")
    # The root has no expected parent -- only pending/sent containers do.
    assert "parents" not in call["params"]["fields"]


@pytest.mark.parametrize(
    "overrides",
    [
        {"mimeType": "application/vnd.google-apps.document"},
        {"trashed": True},
        {"capabilities": {"canAddChildren": False}},
    ],
)
def test_verify_root_rejects_unusable_root(overrides: dict[str, Any]) -> None:
    transport = FakeTransport()
    transport.queue(
        DriveTransportResponse(
            status_code=200,
            body=_container_info_body(ROOT_FOLDER_ID, **overrides),
        )
    )

    with pytest.raises(DriveProvisioningError) as excinfo:
        _provider(transport).verify_root()

    assert excinfo.value.category == "drive_root_invalid"


def test_verify_root_rejects_shared_drive_mismatch() -> None:
    transport = FakeTransport()
    transport.queue(
        DriveTransportResponse(
            status_code=200,
            body=_container_info_body(ROOT_FOLDER_ID),
        )
    )

    with pytest.raises(DriveProvisioningError) as excinfo:
        _provider(
            transport, shared_drive_id="shared-drive-1"
        ).verify_root()

    assert excinfo.value.category == "drive_auth_mode_incompatible"


def test_verify_root_rejects_owner_mismatch() -> None:
    transport = FakeTransport()
    transport.queue(
        DriveTransportResponse(
            status_code=200,
            body=_container_info_body(
                ROOT_FOLDER_ID, owners=_owners("someone-else@gmail.com")
            ),
        )
    )

    with pytest.raises(DriveProvisioningError) as excinfo:
        _provider(transport).verify_root(
            expected_owner_email="contacto@origenlab.cl"
        )

    assert excinfo.value.category == "drive_principal_mismatch"


def test_verify_sent_not_configured_fails_closed() -> None:
    transport = FakeTransport()

    with pytest.raises(DriveProvisioningError) as excinfo:
        _provider(transport).verify_sent()

    assert excinfo.value.category == "drive_not_configured"
    assert transport.calls == []


def test_verify_sent_accepts_writable_sent_folder_parented_by_root() -> None:
    transport = FakeTransport()
    transport.queue(
        DriveTransportResponse(
            status_code=200,
            body=_container_info_body(SENT_FOLDER_ID, parents=[ROOT_FOLDER_ID]),
        )
    )

    _provider(transport, sent_folder_id=SENT_FOLDER_ID).verify_sent()

    call = transport.calls[0]
    assert call["url"].endswith(f"/drive/v3/files/{SENT_FOLDER_ID}")
    assert "parents" in call["params"]["fields"]


def test_verify_sent_rejects_unusable_sent_folder() -> None:
    transport = FakeTransport()
    transport.queue(
        DriveTransportResponse(
            status_code=200,
            body=_container_info_body(
                SENT_FOLDER_ID, parents=[ROOT_FOLDER_ID], trashed=True
            ),
        )
    )

    with pytest.raises(DriveProvisioningError) as excinfo:
        _provider(transport, sent_folder_id=SENT_FOLDER_ID).verify_sent()

    assert excinfo.value.category == "drive_sent_invalid"


def test_verify_sent_rejects_sent_not_parented_by_root() -> None:
    transport = FakeTransport()
    transport.queue(
        DriveTransportResponse(
            status_code=200,
            body=_container_info_body(SENT_FOLDER_ID, parents=["some-other-folder"]),
        )
    )

    with pytest.raises(DriveProvisioningError) as excinfo:
        _provider(transport, sent_folder_id=SENT_FOLDER_ID).verify_sent()

    assert excinfo.value.category == "drive_sent_container_mismatch"


def test_verify_template_accepts_copyable_template() -> None:
    transport = FakeTransport()
    transport.queue(
        DriveTransportResponse(status_code=200, body=_template_info_body())
    )

    _provider(transport).verify_template()

    call = transport.calls[0]

    assert call["method"] == "GET"
    assert call["url"].endswith("/drive/v3/files/template-file-1")
    assert call["params"]["supportsAllDrives"] is True
    assert "canCopy" in call["params"]["fields"]


@pytest.mark.parametrize(
    "overrides",
    [
        {"trashed": True},
        {"capabilities": {"canCopy": False}},
        {"capabilities": {}},
    ],
)
def test_verify_template_rejects_unusable_template(
    overrides: dict[str, Any],
) -> None:
    transport = FakeTransport()
    transport.queue(
        DriveTransportResponse(
            status_code=200,
            body=_template_info_body(**overrides),
        )
    )

    with pytest.raises(DriveProvisioningError) as excinfo:
        _provider(transport).verify_template()

    assert excinfo.value.category == "drive_template_invalid"


def test_verify_principal_accepts_matching_identity_case_insensitively() -> None:
    transport = FakeTransport()
    transport.queue(
        DriveTransportResponse(
            status_code=200,
            body={"user": {"emailAddress": "Contacto@OrigenLab.cl"}},
        )
    )

    email = _provider(transport).verify_principal("contacto@origenlab.cl")

    assert email == "Contacto@OrigenLab.cl"

    call = transport.calls[0]
    assert call["method"] == "GET"
    assert call["url"].endswith("/drive/v3/about")
    assert "user(emailAddress)" in call["params"]["fields"]


def test_verify_principal_rejects_mismatched_identity() -> None:
    transport = FakeTransport()
    transport.queue(
        DriveTransportResponse(
            status_code=200,
            body={"user": {"emailAddress": "someone-else@gmail.com"}},
        )
    )

    with pytest.raises(DriveProvisioningError) as excinfo:
        _provider(transport).verify_principal("contacto@origenlab.cl")

    assert excinfo.value.category == "drive_principal_mismatch"


def test_verify_principal_rejects_missing_identity() -> None:
    transport = FakeTransport()
    transport.queue(DriveTransportResponse(status_code=200, body={}))

    with pytest.raises(DriveProvisioningError) as excinfo:
        _provider(transport).verify_principal("contacto@origenlab.cl")

    assert excinfo.value.category == "drive_principal_mismatch"


def test_verify_template_reports_none_when_expected_owner_not_provided() -> None:
    transport = FakeTransport()
    transport.queue(
        DriveTransportResponse(
            status_code=200,
            body=_template_info_body(owners=_owners("someone-else@gmail.com")),
        )
    )

    result = _provider(transport).verify_template()

    assert result is None


def test_verify_template_reports_true_when_owned_by_expected_principal() -> None:
    transport = FakeTransport()
    transport.queue(
        DriveTransportResponse(
            status_code=200,
            body=_template_info_body(owners=_owners("Contacto@OrigenLab.cl")),
        )
    )

    result = _provider(transport).verify_template(
        expected_owner_email="contacto@origenlab.cl"
    )

    assert result is True


def test_verify_template_reports_false_without_raising_when_owned_elsewhere() -> None:
    # Ownership mismatch on the template is informational only -- it must
    # never block activation (the template may legitimately be shared from
    # another account).
    transport = FakeTransport()
    transport.queue(
        DriveTransportResponse(
            status_code=200,
            body=_template_info_body(owners=_owners("rafarojasv6@gmail.com")),
        )
    )

    result = _provider(transport).verify_template(
        expected_owner_email="contacto@origenlab.cl"
    )

    assert result is False


def test_verify_template_reports_none_when_owners_absent() -> None:
    transport = FakeTransport()
    transport.queue(
        DriveTransportResponse(
            status_code=200,
            body=_template_info_body(owners=[]),
        )
    )

    result = _provider(transport).verify_template(
        expected_owner_email="contacto@origenlab.cl"
    )

    assert result is None


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
