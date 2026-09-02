"""CRM-Q1 service tests: quote creation + Drive workspace orchestration.

All Drive behavior is exercised through a deterministic fake provider; no
test touches the network or requires credentials.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from typing import Any

import pytest

from origenlab_api.drive.errors import DriveProvisioningError
from origenlab_api.drive.protocol import DriveFileRef
from origenlab_api.repositories.postgres.commercial_operations import (
    CommercialOperationConflictError,
    CommercialOperationNotFoundError,
)
from origenlab_api.repositories.postgres.customer_quotes import (
    CustomerQuote,
    CustomerQuoteBundle,
    CustomerQuoteDriveWorkspace,
    CustomerQuoteRevision,
    QuoteNumberingNotConfiguredError,
)
from origenlab_api.services.customer_quote_service import (
    CustomerQuoteService,
    build_quote_workspace_name,
)
from origenlab_api.settings import Settings


NOW = datetime(2026, 8, 30, 14, 0, tzinfo=timezone.utc)

QUOTE_ID = "quote_" + "a" * 32
SALES_ID = "sales_" + "b" * 32
OPERATOR = "tatiana@origenlab.cl"

FOLDER = DriveFileRef(
    file_id="folder-1",
    web_url="https://drive.google.com/drive/folders/folder-1",
)
SHEET = DriveFileRef(
    file_id="sheet-1",
    web_url="https://docs.google.com/spreadsheets/d/sheet-1",
)


def _settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "quote_document_prefix": "CN",
        "quote_serial_pad_width": 5,
        "quote_seed_next_serial": 1183,
        "drive_quote_template_file_id": "template-file-1",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def _quote(**overrides: Any) -> CustomerQuote:
    values: dict[str, Any] = {
        "quote_id": QUOTE_ID,
        "sales_opportunity_id": SALES_ID,
        "quote_number": "01183-26",
        "serial": 1183,
        "issue_year": 2026,
        "document_number": "CN01183",
        "quote_origin": "generated",
        "status": "draft",
        "version": 1,
        "created_by": OPERATOR,
        "updated_by": OPERATOR,
        "created_at": NOW,
        "updated_at": NOW,
    }
    values.update(overrides)
    return CustomerQuote(**values)


def _revision() -> CustomerQuoteRevision:
    return CustomerQuoteRevision(
        quote_id=QUOTE_ID,
        revision_number=1,
        template_reference="template-file-1",
        status="draft",
        created_by=OPERATOR,
        created_at=NOW,
        updated_by=OPERATOR,
        updated_at=NOW,
    )


def _workspace(**overrides: Any) -> CustomerQuoteDriveWorkspace:
    values: dict[str, Any] = {
        "quote_id": QUOTE_ID,
        "provider": "google_drive",
        "provisioning_status": "pending",
        "folder_id": None,
        "folder_web_url": None,
        "sheet_file_id": None,
        "sheet_web_url": None,
        "failure_category": None,
        "attempt_count": 0,
        "version": 1,
        "lease_expires_at": None,
        "requested_at": None,
        "completed_at": None,
        "created_by": OPERATOR,
        "updated_by": OPERATOR,
        "created_at": NOW,
        "updated_at": NOW,
    }
    values.update(overrides)
    return CustomerQuoteDriveWorkspace(**values)


def _bundle(workspace: CustomerQuoteDriveWorkspace) -> CustomerQuoteBundle:
    return CustomerQuoteBundle(
        quote=_quote(),
        revision=_revision(),
        workspace=workspace,
        sales_opportunity_title="Centrífuga CEAF",
    )


class FakeRepository:
    def __init__(self, create_result: CustomerQuoteBundle) -> None:
        self.create_result = create_result
        self.bundles: dict[str, CustomerQuoteBundle] = {}
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.begin_error: Exception | None = None

    def create_quote(self, **kwargs: Any) -> CustomerQuoteBundle:
        self.calls.append(("create_quote", kwargs))
        if isinstance(self.create_result, Exception):
            raise self.create_result
        self.bundles[self.create_result.quote.quote_id] = self.create_result
        return self.create_result

    def get_quote_bundle(self, *, quote_id: str) -> CustomerQuoteBundle | None:
        self.calls.append(("get_quote_bundle", {"quote_id": quote_id}))
        return self.bundles.get(quote_id)

    def begin_drive_provision_attempt(
        self, **kwargs: Any
    ) -> CustomerQuoteDriveWorkspace:
        self.calls.append(("begin_drive_provision_attempt", kwargs))
        if self.begin_error is not None:
            raise self.begin_error
        bundle = self.bundles[kwargs["quote_id"]]
        workspace = replace(
            bundle.workspace,
            attempt_count=bundle.workspace.attempt_count + 1,
            version=bundle.workspace.version + 1,
            provisioning_status="pending",
            failure_category=None,
            requested_at=NOW,
        )
        self.bundles[kwargs["quote_id"]] = replace(bundle, workspace=workspace)
        return workspace

    def complete_drive_provision(
        self, **kwargs: Any
    ) -> CustomerQuoteDriveWorkspace:
        self.calls.append(("complete_drive_provision", kwargs))
        bundle = self.bundles[kwargs["quote_id"]]
        workspace = replace(
            bundle.workspace,
            provisioning_status="ready",
            folder_id=kwargs["folder_id"],
            folder_web_url=kwargs["folder_web_url"],
            sheet_file_id=kwargs["sheet_file_id"],
            sheet_web_url=kwargs["sheet_web_url"],
            failure_category=None,
            completed_at=NOW,
            version=bundle.workspace.version + 1,
        )
        self.bundles[kwargs["quote_id"]] = replace(bundle, workspace=workspace)
        return workspace

    def fail_drive_provision(
        self, **kwargs: Any
    ) -> CustomerQuoteDriveWorkspace:
        self.calls.append(("fail_drive_provision", kwargs))
        bundle = self.bundles[kwargs["quote_id"]]
        workspace = replace(
            bundle.workspace,
            provisioning_status="failed",
            failure_category=kwargs["failure_category"],
            folder_id=kwargs.get("folder_id") or bundle.workspace.folder_id,
            folder_web_url=(
                kwargs.get("folder_web_url") or bundle.workspace.folder_web_url
            ),
            version=bundle.workspace.version + 1,
        )
        self.bundles[kwargs["quote_id"]] = replace(bundle, workspace=workspace)
        return workspace


class FakeDriveProvider:
    def __init__(self) -> None:
        self.folders: dict[str, DriveFileRef] = {}
        self.sheets: dict[str, DriveFileRef] = {}
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.verify_destination_error: DriveProvisioningError | None = None
        self.create_folder_error: DriveProvisioningError | None = None
        self.copy_sheet_error: DriveProvisioningError | None = None

    def verify_destination(self) -> None:
        self.calls.append(("verify_destination", {}))
        if self.verify_destination_error is not None:
            raise self.verify_destination_error

    def find_folder(self, quote_id: str) -> DriveFileRef | None:
        self.calls.append(("find_folder", {"quote_id": quote_id}))
        return self.folders.get(quote_id)

    def create_folder(self, quote_id: str, *, name: str) -> DriveFileRef:
        self.calls.append(("create_folder", {"quote_id": quote_id, "name": name}))
        if self.create_folder_error is not None:
            raise self.create_folder_error
        self.folders[quote_id] = FOLDER
        return FOLDER

    def find_sheet(self, quote_id: str, *, folder_id: str) -> DriveFileRef | None:
        self.calls.append(
            ("find_sheet", {"quote_id": quote_id, "folder_id": folder_id})
        )
        return self.sheets.get(quote_id)

    def copy_template_sheet(
        self, quote_id: str, *, folder_id: str, name: str
    ) -> DriveFileRef:
        self.calls.append(
            (
                "copy_template_sheet",
                {"quote_id": quote_id, "folder_id": folder_id, "name": name},
            )
        )
        if self.copy_sheet_error is not None:
            raise self.copy_sheet_error
        self.sheets[quote_id] = SHEET
        return SHEET


def _service(
    repository: FakeRepository,
    provider: FakeDriveProvider | None = None,
    *,
    settings: Settings | None = None,
    factory_error: DriveProvisioningError | None = None,
) -> CustomerQuoteService:
    def factory(settings_arg: Settings) -> FakeDriveProvider:
        del settings_arg
        if factory_error is not None:
            raise factory_error
        assert provider is not None
        return provider

    return CustomerQuoteService(
        settings or _settings(),
        repository=repository,  # type: ignore[arg-type]
        drive_provider_factory=factory,
    )


def test_create_quote_provisions_drive_workspace_end_to_end() -> None:
    repository = FakeRepository(_bundle(_workspace()))
    provider = FakeDriveProvider()

    result = _service(repository, provider).create_quote(
        sales_opportunity_id=SALES_ID,
        operator=OPERATOR,
        idempotency_key="quote-create-1",
    )

    assert result.workspace.provisioning_status == "ready"
    assert result.workspace.folder_web_url == FOLDER.web_url
    assert result.workspace.sheet_web_url == SHEET.web_url

    create_call = repository.calls[0]

    assert create_call[0] == "create_quote"
    assert create_call[1]["sales_opportunity_id"] == SALES_ID
    assert create_call[1]["operator"] == OPERATOR
    assert create_call[1]["idempotency_key"] == "quote-create-1"
    assert create_call[1]["quote_id"].startswith("quote_")
    assert len(create_call[1]["quote_id"]) == len("quote_") + 32
    assert create_call[1]["numbering"] is not None
    assert create_call[1]["numbering"].document_prefix == "CN"
    assert create_call[1]["template_reference"] == "template-file-1"

    provider_methods = [name for name, _ in provider.calls]

    assert provider_methods == [
        "verify_destination",
        "find_folder",
        "create_folder",
        "find_sheet",
        "copy_template_sheet",
    ]

    # Two distinct identifiers name two distinct artifacts: the folder
    # carries the human quote_number, the copied template carries the
    # separate document_number.
    assert provider.calls[2][1]["name"] == "01183-26 — Centrífuga CEAF"
    assert provider.calls[4][1]["name"] == "CN01183 — Centrífuga CEAF"


def test_create_quote_without_numbering_config_fails_closed() -> None:
    repository = FakeRepository(_bundle(_workspace()))
    repository.create_result = QuoteNumberingNotConfiguredError(  # type: ignore[assignment]
        "quote_numbering_not_configured"
    )

    with pytest.raises(QuoteNumberingNotConfiguredError):
        _service(repository, FakeDriveProvider(), settings=_settings(
            quote_document_prefix=None,
        )).create_quote(
            sales_opportunity_id=SALES_ID,
            operator=OPERATOR,
            idempotency_key="quote-create-1",
        )

    assert repository.calls[0][1]["numbering"] is None


def test_create_quote_replay_with_ready_workspace_skips_drive() -> None:
    repository = FakeRepository(
        _bundle(
            _workspace(
                provisioning_status="ready",
                folder_id="folder-1",
                folder_web_url=FOLDER.web_url,
                sheet_file_id="sheet-1",
                sheet_web_url=SHEET.web_url,
            )
        )
    )
    provider = FakeDriveProvider()

    result = _service(repository, provider).create_quote(
        sales_opportunity_id=SALES_ID,
        operator=OPERATOR,
        idempotency_key="quote-create-1",
    )

    assert result.workspace.provisioning_status == "ready"
    assert provider.calls == []
    assert not any(
        name == "begin_drive_provision_attempt" for name, _ in repository.calls
    )


def test_drive_unconfigured_failure_keeps_quote_and_records_category() -> None:
    repository = FakeRepository(_bundle(_workspace()))

    result = _service(
        repository,
        factory_error=DriveProvisioningError("drive_not_configured"),
    ).create_quote(
        sales_opportunity_id=SALES_ID,
        operator=OPERATOR,
        idempotency_key="quote-create-1",
    )

    assert result.quote.quote_number == "01183-26"
    assert result.workspace.provisioning_status == "failed"
    assert result.workspace.failure_category == "drive_not_configured"

    fail_call = next(
        kwargs
        for name, kwargs in repository.calls
        if name == "fail_drive_provision"
    )

    assert fail_call["failure_category"] == "drive_not_configured"
    assert fail_call.get("folder_id") is None


def test_provision_verifies_destination_before_any_drive_mutation() -> None:
    repository = FakeRepository(_bundle(_workspace()))
    provider = FakeDriveProvider()

    _service(repository, provider).create_quote(
        sales_opportunity_id=SALES_ID,
        operator=OPERATOR,
        idempotency_key="quote-create-1",
    )

    call_names = [name for name, _ in provider.calls]

    assert call_names[0] == "verify_destination"
    assert "create_folder" in call_names


def test_destination_verification_failure_blocks_all_drive_writes() -> None:
    # A service account paired with a My Drive destination (or any unusable
    # root) must fail closed before creating anything.
    repository = FakeRepository(_bundle(_workspace()))
    provider = FakeDriveProvider()
    provider.verify_destination_error = DriveProvisioningError(
        "drive_auth_mode_incompatible"
    )

    result = _service(repository, provider).create_quote(
        sales_opportunity_id=SALES_ID,
        operator=OPERATOR,
        idempotency_key="quote-create-1",
    )

    assert result.workspace.provisioning_status == "failed"
    assert result.workspace.failure_category == "drive_auth_mode_incompatible"

    call_names = [name for name, _ in provider.calls]

    assert "find_folder" not in call_names
    assert "create_folder" not in call_names
    assert "copy_template_sheet" not in call_names


def test_folder_creation_failure_records_category_without_partial_refs() -> None:
    repository = FakeRepository(_bundle(_workspace()))
    provider = FakeDriveProvider()
    provider.create_folder_error = DriveProvisioningError("drive_unavailable")

    result = _service(repository, provider).create_quote(
        sales_opportunity_id=SALES_ID,
        operator=OPERATOR,
        idempotency_key="quote-create-1",
    )

    assert result.workspace.provisioning_status == "failed"
    assert result.workspace.failure_category == "drive_unavailable"
    assert result.workspace.folder_id is None


def test_sheet_copy_failure_preserves_partial_folder_reference() -> None:
    repository = FakeRepository(_bundle(_workspace()))
    provider = FakeDriveProvider()
    provider.copy_sheet_error = DriveProvisioningError("drive_timeout")

    result = _service(repository, provider).create_quote(
        sales_opportunity_id=SALES_ID,
        operator=OPERATOR,
        idempotency_key="quote-create-1",
    )

    assert result.workspace.provisioning_status == "failed"
    assert result.workspace.failure_category == "drive_timeout"
    # The partial folder stays discoverable for retry/reconciliation.
    assert result.workspace.folder_id == "folder-1"
    assert result.workspace.folder_web_url == FOLDER.web_url


def test_retry_after_partial_creation_reuses_existing_artifacts() -> None:
    workspace = _workspace(
        provisioning_status="failed",
        failure_category="drive_timeout",
        folder_id="folder-1",
        folder_web_url=FOLDER.web_url,
        attempt_count=1,
        version=3,
    )
    repository = FakeRepository(_bundle(workspace))
    repository.bundles[QUOTE_ID] = _bundle(workspace)
    provider = FakeDriveProvider()
    provider.folders[QUOTE_ID] = FOLDER
    provider.sheets[QUOTE_ID] = SHEET

    result = _service(repository, provider).retry_drive_provisioning(
        quote_id=QUOTE_ID,
        operator=OPERATOR,
        expected_version=3,
    )

    assert result.workspace.provisioning_status == "ready"

    provider_methods = [name for name, _ in provider.calls]

    # The retry must never create duplicate folders or template copies.
    assert "create_folder" not in provider_methods
    assert "copy_template_sheet" not in provider_methods


def test_retry_conflict_surfaces_conflict_error() -> None:
    workspace = _workspace(provisioning_status="failed", version=3)
    repository = FakeRepository(_bundle(workspace))
    repository.bundles[QUOTE_ID] = _bundle(workspace)
    repository.begin_error = CommercialOperationConflictError("version conflict")

    with pytest.raises(CommercialOperationConflictError):
        _service(repository, FakeDriveProvider()).retry_drive_provisioning(
            quote_id=QUOTE_ID,
            operator=OPERATOR,
            expected_version=1,
        )


def test_create_swallows_concurrent_provisioning_conflict() -> None:
    repository = FakeRepository(_bundle(_workspace()))
    repository.begin_error = CommercialOperationConflictError("concurrent")

    result = _service(repository, FakeDriveProvider()).create_quote(
        sales_opportunity_id=SALES_ID,
        operator=OPERATOR,
        idempotency_key="quote-create-1",
    )

    # The quote itself was created; a concurrent provisioning attempt is not
    # an error for the creating request.
    assert result.quote.quote_id == QUOTE_ID


def test_retry_missing_quote_raises_not_found() -> None:
    repository = FakeRepository(_bundle(_workspace()))

    with pytest.raises(CommercialOperationNotFoundError):
        _service(repository, FakeDriveProvider()).retry_drive_provisioning(
            quote_id="quote_" + "f" * 32,
            operator=OPERATOR,
            expected_version=1,
        )


def test_retry_validates_expected_version() -> None:
    repository = FakeRepository(_bundle(_workspace()))

    with pytest.raises(ValueError):
        _service(repository, FakeDriveProvider()).retry_drive_provisioning(
            quote_id=QUOTE_ID,
            operator=OPERATOR,
            expected_version=0,
        )


def test_workspace_name_sanitizes_title() -> None:
    assert (
        build_quote_workspace_name("01183-26", "  Centrífuga  CEAF  ")
        == "01183-26 — Centrífuga CEAF"
    )
    assert (
        build_quote_workspace_name("CN01183", "a/b\\c\ncontrol\x00chars")
        == "CN01183 — a-b-c control chars"
    )
    assert build_quote_workspace_name("01183-26", "   ") == "01183-26"

    long_title = "x" * 500
    name = build_quote_workspace_name("01183-26", long_title)

    assert len(name) <= 120
    assert name.startswith("01183-26 — ")
