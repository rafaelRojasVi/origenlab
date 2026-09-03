"""Tests for the read-only Drive Pendientes projection service.

Covers CRM/Drive-folder deduplication, conservative document-identifier
parsing, and that Drive misconfiguration/failure propagates uncaught (the
route maps it to a redacted HTTP response -- there is no durable row to
persist a failure into for a pure read)."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from origenlab_api.drive.errors import DriveProvisioningError
from origenlab_api.drive.protocol import DrivePendingFolder
from origenlab_api.services.drive_pending_quote_service import (
    DrivePendingQuoteReadService,
    parse_drive_pending_document_identifier,
    parse_drive_pending_organization_candidate,
)
from origenlab_api.settings import Settings


class FakeRepository:
    def __init__(self, known_folder_ids: set[str] | None = None) -> None:
        self.known_folder_ids = known_folder_ids or set()
        self.calls = 0

    def list_known_drive_folder_ids(self) -> set[str]:
        self.calls += 1
        return self.known_folder_ids


class FakeProvider:
    def __init__(
        self,
        folders: list[DrivePendingFolder] | None = None,
        *,
        error: DriveProvisioningError | None = None,
    ) -> None:
        self.folders = folders or []
        self.error = error
        self.calls: list[str] = []

    def list_pending_children(self) -> list[DrivePendingFolder]:
        self.calls.append("list_pending_children")
        if self.error is not None:
            raise self.error
        return self.folders


def _folder(
    folder_id: str,
    name: str,
    *,
    web_url: str = "https://drive.google.com/drive/folders/x",
) -> DrivePendingFolder:
    return DrivePendingFolder(
        folder_id=folder_id,
        folder_name=name,
        folder_web_url=web_url,
        created_time=datetime(2026, 8, 1, tzinfo=timezone.utc),
        modified_time=datetime(2026, 8, 15, tzinfo=timezone.utc),
    )


def _service(
    provider: FakeProvider,
    repository: FakeRepository,
    *,
    factory_error: DriveProvisioningError | None = None,
) -> DrivePendingQuoteReadService:
    def factory(settings: Settings) -> FakeProvider:
        del settings
        if factory_error is not None:
            raise factory_error
        return provider

    return DrivePendingQuoteReadService(
        Settings(_env_file=None),
        repository=repository,
        drive_provider_factory=factory,
    )


def test_lists_drive_only_folders_with_document_identifier() -> None:
    provider = FakeProvider([_folder("f1", "CN01191-ICN Chile")])
    repository = FakeRepository()

    result = _service(provider, repository).list_drive_pending_workspaces()

    assert len(result) == 1
    assert result[0].folder_id == "f1"
    assert result[0].folder_name == "CN01191-ICN Chile"
    assert result[0].document_identifier == "CN01191"


def test_excludes_folders_already_owned_by_a_durable_crm_quote() -> None:
    provider = FakeProvider(
        [_folder("f1", "CN01191-ICN Chile"), _folder("f2", "CN01190-Otro")]
    )
    repository = FakeRepository(known_folder_ids={"f1"})

    result = _service(provider, repository).list_drive_pending_workspaces()

    assert [item.folder_id for item in result] == ["f2"]
    assert repository.calls == 1


def test_drive_factory_failure_propagates_uncaught() -> None:
    provider = FakeProvider([])
    repository = FakeRepository()
    service = _service(
        provider,
        repository,
        factory_error=DriveProvisioningError("drive_not_configured"),
    )

    with pytest.raises(DriveProvisioningError) as excinfo:
        service.list_drive_pending_workspaces()

    assert excinfo.value.category == "drive_not_configured"


def test_provider_failure_propagates_uncaught() -> None:
    provider = FakeProvider(error=DriveProvisioningError("drive_unavailable"))
    repository = FakeRepository()

    with pytest.raises(DriveProvisioningError) as excinfo:
        _service(provider, repository).list_drive_pending_workspaces()

    assert excinfo.value.category == "drive_unavailable"


def test_never_calls_repository_or_provider_mutation_methods() -> None:
    """Zero quote-create/number-series side effects: only the two read
    methods this service depends on are ever invoked."""

    provider = FakeProvider([_folder("f1", "CN01191-ICN Chile")])
    repository = FakeRepository()

    _service(provider, repository).list_drive_pending_workspaces()

    assert provider.calls == ["list_pending_children"]
    assert not hasattr(repository, "create_quote")
    assert not hasattr(provider, "create_folder")


@pytest.mark.parametrize(
    ("folder_name", "expected"),
    [
        ("CN01191-ICN Chile", "CN01191"),
        (
            "CN01190-Prof. Dr. Juan Matos Lale – Universidad "
            "Autónoma- UP400St",
            "CN01190",
        ),
        ("CN1185 — Gustavo Zúñiga - UP200St", "CN1185"),
        ("Sin prefijo reconocible", None),
        ("CN", None),
        ("CN1185B", None),
    ],
)
def test_parse_drive_pending_document_identifier_is_conservative(
    folder_name: str, expected: str | None
) -> None:
    assert parse_drive_pending_document_identifier(folder_name) == expected


def test_organization_candidate_from_simple_folder_name() -> None:
    assert parse_drive_pending_organization_candidate("CN01191-ICN Chile") == "ICN Chile"


def test_organization_candidate_from_verbose_folder_name() -> None:
    result = parse_drive_pending_organization_candidate(
        "CN01190-Prof. Dr. Juan Matos Lale – Universidad Autónoma- UP400St"
    )
    assert result == "Prof. Dr. Juan Matos Lale – Universidad Autónoma- UP400St"


def test_organization_candidate_handles_em_dash_separator() -> None:
    assert (
        parse_drive_pending_organization_candidate("CN1185 — Gustavo Zúñiga - UP200St")
        == "Gustavo Zúñiga - UP200St"
    )


def test_organization_candidate_none_when_no_document_identifier() -> None:
    assert parse_drive_pending_organization_candidate("Sin prefijo reconocible") is None


def test_organization_candidate_none_when_nothing_remains_after_identifier() -> None:
    assert parse_drive_pending_organization_candidate("CN01191") is None
    assert parse_drive_pending_organization_candidate("CN01191-") is None
    assert parse_drive_pending_organization_candidate("CN01191   ") is None
