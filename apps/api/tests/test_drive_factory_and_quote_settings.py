"""CRM-Q1 tests for Drive provider factory + quote numbering settings.

The factory must fail closed with redacted categories until the operator has
explicitly configured the Drive workspace (root folder, template, an explicit
authentication mode, and a credentials file). Two authentication modes exist:

* ``authorized_user_my_drive`` — an authorized-user credentials JSON with an
  offline refresh token, operating in the operator's personal My Drive;
* ``service_account_shared_drive`` — a service-account JSON, valid only when
  paired with an explicit Shared Drive ID (service accounts have no My Drive
  storage quota and cannot own files there).

Numbering settings must resolve to a config only when the complete business
decision (prefix, pad width, seed) is present and valid.
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


def test_factory_fails_closed_when_pending_folder_not_configured() -> None:
    # Root + template alone are not a complete decision: quote workspaces
    # are created under Pendientes, so its folder ID is mandatory too.
    with pytest.raises(DriveProvisioningError) as excinfo:
        build_drive_workspace_provider(
            _settings(
                drive_quotes_root_folder_id="root-1",
                drive_quote_template_file_id="template-1",
            )
        )

    assert excinfo.value.category == "drive_not_configured"


def test_factory_requires_explicit_auth_mode() -> None:
    with pytest.raises(DriveProvisioningError) as excinfo:
        build_drive_workspace_provider(
            _settings(
                drive_quotes_root_folder_id="root-1",
                drive_quotes_pending_folder_id="pending-1",
                drive_quote_template_file_id="template-1",
            )
        )

    assert excinfo.value.category == "drive_auth_mode_not_configured"


@pytest.mark.parametrize(
    "bad_mode",
    ["service_account", "authorized_user", "my_drive", "SERVICE", " "],
)
def test_factory_rejects_unknown_auth_mode(bad_mode: str) -> None:
    with pytest.raises(DriveProvisioningError) as excinfo:
        build_drive_workspace_provider(
            _settings(
                drive_quotes_root_folder_id="root-1",
                drive_quotes_pending_folder_id="pending-1",
                drive_quote_template_file_id="template-1",
                drive_auth_mode=bad_mode,
            )
        )

    assert excinfo.value.category == "drive_auth_mode_not_configured"


def test_service_account_mode_without_shared_drive_id_is_incompatible() -> None:
    # A bare service account has no My Drive storage quota and cannot own
    # files: without an explicit Shared Drive destination the pairing is
    # invalid, regardless of credentials.
    with pytest.raises(DriveProvisioningError) as excinfo:
        build_drive_workspace_provider(
            _settings(
                drive_quotes_root_folder_id="root-1",
                drive_quotes_pending_folder_id="pending-1",
                drive_quote_template_file_id="template-1",
                drive_auth_mode="service_account_shared_drive",
            )
        )

    assert excinfo.value.category == "drive_auth_mode_incompatible"


def test_factory_requires_credentials_once_mode_configured() -> None:
    with pytest.raises(DriveProvisioningError) as excinfo:
        build_drive_workspace_provider(
            _settings(
                drive_quotes_root_folder_id="root-1",
                drive_quotes_pending_folder_id="pending-1",
                drive_quote_template_file_id="template-1",
                drive_auth_mode="authorized_user_my_drive",
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
                drive_quotes_pending_folder_id="pending-1",
                drive_quote_template_file_id="template-1",
                drive_auth_mode="authorized_user_my_drive",
                drive_credentials_file=tmp_path / "missing.json",
            )
        )

    assert excinfo.value.category == "drive_credentials_not_configured"


def test_factory_builds_authorized_user_provider(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    credential_file = tmp_path / "authorized_user.json"
    credential_file.write_text("{}", encoding="utf-8")

    calls: list[Path] = []

    def fake_supplier_builder(path: Path) -> object:
        calls.append(path)
        return lambda: "fake-token"

    monkeypatch.setattr(
        factory_module,
        "_build_authorized_user_token_supplier",
        fake_supplier_builder,
    )

    provider = build_drive_workspace_provider(
        _settings(
            drive_quotes_root_folder_id="root-1",
            drive_quotes_pending_folder_id="pending-1",
            drive_quote_template_file_id="template-1",
            drive_auth_mode="authorized_user_my_drive",
            drive_credentials_file=credential_file,
        )
    )

    assert isinstance(provider, GoogleDriveQuoteWorkspaceProvider)
    assert calls == [credential_file]
    assert provider.shared_drive_id is None
    assert provider.root_folder_id == "root-1"
    assert provider.pending_folder_id == "pending-1"
    assert provider.sent_folder_id is None


def test_factory_builds_service_account_provider_scoped_to_shared_drive(
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
            drive_quotes_pending_folder_id="pending-1",
            drive_quotes_sent_folder_id="sent-1",
            drive_quote_template_file_id="template-1",
            drive_auth_mode="service_account_shared_drive",
            drive_shared_drive_id="shared-drive-1",
            drive_credentials_file=credential_file,
        )
    )

    assert isinstance(provider, GoogleDriveQuoteWorkspaceProvider)
    assert provider.shared_drive_id == "shared-drive-1"
    assert provider.sent_folder_id == "sent-1"


def test_authorized_user_mode_may_scope_to_a_shared_drive(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    # A human OAuth identity can also operate inside a Shared Drive; the
    # optional ID scopes discovery, it is not forbidden in this mode.
    credential_file = tmp_path / "authorized_user.json"
    credential_file.write_text("{}", encoding="utf-8")

    monkeypatch.setattr(
        factory_module,
        "_build_authorized_user_token_supplier",
        lambda path: (lambda: "fake-token"),
    )

    provider = build_drive_workspace_provider(
        _settings(
            drive_quotes_root_folder_id="root-1",
            drive_quotes_pending_folder_id="pending-1",
            drive_quote_template_file_id="template-1",
            drive_auth_mode="authorized_user_my_drive",
            drive_shared_drive_id="shared-drive-1",
            drive_credentials_file=credential_file,
        )
    )

    assert provider.shared_drive_id == "shared-drive-1"


@pytest.mark.parametrize(
    ("auth_mode", "supplier_name", "extra_settings"),
    [
        (
            "authorized_user_my_drive",
            "_build_authorized_user_token_supplier",
            {},
        ),
        (
            "service_account_shared_drive",
            "_build_service_account_token_supplier",
            {"drive_shared_drive_id": "shared-drive-1"},
        ),
    ],
)
def test_factory_maps_missing_dependency_to_dependency_category(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    auth_mode: str,
    supplier_name: str,
    extra_settings: dict[str, object],
) -> None:
    # Drive configuration present but the optional runtime dependency absent
    # is a deployment misconfiguration; it must be distinguishable from both
    # "not configured" and "credentials not configured".
    credential_file = tmp_path / "credentials.json"
    credential_file.write_text("{}", encoding="utf-8")

    def raise_import_error(path: Path) -> object:
        raise ImportError("No module named 'google'")

    monkeypatch.setattr(factory_module, supplier_name, raise_import_error)

    with pytest.raises(DriveProvisioningError) as excinfo:
        build_drive_workspace_provider(
            _settings(
                drive_quotes_root_folder_id="root-1",
                drive_quotes_pending_folder_id="pending-1",
                drive_quote_template_file_id="template-1",
                drive_auth_mode=auth_mode,
                drive_credentials_file=credential_file,
                **extra_settings,
            )
        )

    assert excinfo.value.category == "drive_dependency_missing"


def test_factory_maps_malformed_credentials_to_credentials_category(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    credential_file = tmp_path / "authorized_user.json"
    credential_file.write_text("not-json", encoding="utf-8")

    def raise_value_error(path: Path) -> object:
        raise ValueError("malformed credential payload")

    monkeypatch.setattr(
        factory_module,
        "_build_authorized_user_token_supplier",
        raise_value_error,
    )

    with pytest.raises(DriveProvisioningError) as excinfo:
        build_drive_workspace_provider(
            _settings(
                drive_quotes_root_folder_id="root-1",
                drive_quotes_pending_folder_id="pending-1",
                drive_quote_template_file_id="template-1",
                drive_auth_mode="authorized_user_my_drive",
                drive_credentials_file=credential_file,
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
