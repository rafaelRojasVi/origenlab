"""CRM-Q1 tests for Drive provider factory + quote numbering settings.

The factory must fail closed with redacted categories until the operator has
explicitly configured the Drive workspace (root folder, template, credential
file). Numbering settings must resolve to a config only when the complete
business decision (prefix, pad width, seed) is present and valid.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import origenlab_api.drive.factory as factory_module
from origenlab_api.drive.errors import DriveProvisioningError
from origenlab_api.drive.factory import build_drive_workspace_provider
from origenlab_api.drive.google_drive import GoogleDriveQuoteWorkspaceProvider
from origenlab_api.settings import Settings


def _settings(**overrides: object) -> Settings:
    return Settings(_env_file=None, **overrides)


def test_factory_fails_closed_when_drive_wholly_unconfigured() -> None:
    with pytest.raises(DriveProvisioningError) as excinfo:
        build_drive_workspace_provider(_settings())

    assert excinfo.value.category == "drive_not_configured"


def test_factory_fails_closed_when_only_root_folder_configured() -> None:
    with pytest.raises(DriveProvisioningError) as excinfo:
        build_drive_workspace_provider(
            _settings(drive_quotes_root_folder_id="root-1")
        )

    assert excinfo.value.category == "drive_not_configured"


def test_factory_requires_credentials_once_workspace_configured(
    tmp_path: Path,
) -> None:
    with pytest.raises(DriveProvisioningError) as excinfo:
        build_drive_workspace_provider(
            _settings(
                drive_quotes_root_folder_id="root-1",
                drive_quote_template_file_id="template-1",
            )
        )

    assert excinfo.value.category == "drive_credentials_not_configured"


def test_factory_fails_closed_when_credential_file_missing(
    tmp_path: Path,
) -> None:
    with pytest.raises(DriveProvisioningError) as excinfo:
        build_drive_workspace_provider(
            _settings(
                drive_quotes_root_folder_id="root-1",
                drive_quote_template_file_id="template-1",
                drive_service_account_file=tmp_path / "missing.json",
            )
        )

    assert excinfo.value.category == "drive_credentials_not_configured"


def test_factory_builds_provider_with_injected_token_supplier(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    credential_file = tmp_path / "sa.json"
    credential_file.write_text("{}", encoding="utf-8")

    monkeypatch.setattr(
        factory_module,
        "_build_service_account_token_supplier",
        lambda path: (lambda: "fake-token"),
    )

    provider = build_drive_workspace_provider(
        _settings(
            drive_quotes_root_folder_id="root-1",
            drive_quote_template_file_id="template-1",
            drive_service_account_file=credential_file,
        )
    )

    assert isinstance(provider, GoogleDriveQuoteWorkspaceProvider)


def test_factory_maps_missing_google_auth_to_credentials_category(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    credential_file = tmp_path / "sa.json"
    credential_file.write_text("{}", encoding="utf-8")

    def raise_import_error(path: Path) -> object:
        raise ImportError("No module named 'google'")

    monkeypatch.setattr(
        factory_module,
        "_build_service_account_token_supplier",
        raise_import_error,
    )

    with pytest.raises(DriveProvisioningError) as excinfo:
        build_drive_workspace_provider(
            _settings(
                drive_quotes_root_folder_id="root-1",
                drive_quote_template_file_id="template-1",
                drive_service_account_file=credential_file,
            )
        )

    assert excinfo.value.category == "drive_credentials_not_configured"


def test_quote_numbering_config_absent_by_default() -> None:
    assert _settings().quote_numbering_config() is None


def test_quote_numbering_config_requires_complete_decision() -> None:
    # A partial decision is not a decision: fail closed (None) rather than
    # invent the missing parts.
    assert (
        _settings(quote_number_prefix="CN").quote_numbering_config() is None
    )
    assert (
        _settings(
            quote_number_prefix="CN",
            quote_number_pad_width=6,
        ).quote_numbering_config()
        is None
    )


def test_quote_numbering_config_complete_and_valid() -> None:
    config = _settings(
        quote_number_prefix="CN",
        quote_number_pad_width=6,
        quote_number_seed_next_serial=11729,
    ).quote_numbering_config()

    assert config is not None
    assert config.prefix == "CN"
    assert config.pad_width == 6
    assert config.seed_next_serial == 11729


@pytest.mark.parametrize(
    "overrides",
    [
        {"quote_number_prefix": "lowercase"},
        {"quote_number_prefix": "TOOLONGPREFIX"},
        {"quote_number_prefix": "CN 1"},
        {"quote_number_pad_width": 0},
        {"quote_number_pad_width": 11},
        {"quote_number_seed_next_serial": 0},
    ],
)
def test_quote_numbering_config_rejects_invalid_values(
    overrides: dict[str, object],
) -> None:
    values: dict[str, object] = {
        "quote_number_prefix": "CN",
        "quote_number_pad_width": 6,
        "quote_number_seed_next_serial": 11729,
    }
    values.update(overrides)

    assert _settings(**values).quote_numbering_config() is None
