"""CRM-Q1 tests for the read-only Drive configuration/preflight boundary.

The preflight boundary lets an operator verify a Drive configuration
(credentials load, root folder is writable, template is copyable, storage
model matches the auth mode) before activating quote workspace creation --
without ever making a live Google API call in this test suite. Every path
returns a result object; nothing here raises, and no provider payload,
token, or credential path ever reaches the result.
"""

from __future__ import annotations

from typing import Any

import pytest

from origenlab_api.drive.errors import DriveProvisioningError
from origenlab_api.drive.google_drive import (
    DriveCredentialsError,
    GoogleDriveQuoteWorkspaceProvider,
)
from origenlab_api.drive.preflight import DrivePreflightResult, run_drive_preflight
from origenlab_api.settings import Settings


def _settings(**overrides: object) -> Settings:
    return Settings(_env_file=None, **overrides)


class FakeProvider:
    def __init__(
        self,
        *,
        principal_error: DriveProvisioningError | None = None,
        principal_email: str = "contacto@origenlab.cl",
        destination_error: DriveProvisioningError | None = None,
        template_error: DriveProvisioningError | None = None,
        template_owned_by_expected_principal: bool | None = None,
    ) -> None:
        self.principal_error = principal_error
        self.principal_email = principal_email
        self.destination_error = destination_error
        self.template_error = template_error
        self.template_owned_by_expected_principal = (
            template_owned_by_expected_principal
        )
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def verify_principal(self, expected_email: str) -> str:
        self.calls.append(("verify_principal", {"expected_email": expected_email}))
        if self.principal_error is not None:
            raise self.principal_error
        return self.principal_email

    def verify_destination(self, *, expected_owner_email: str | None = None) -> None:
        self.calls.append(
            ("verify_destination", {"expected_owner_email": expected_owner_email})
        )
        if self.destination_error is not None:
            raise self.destination_error

    def verify_template(
        self, *, expected_owner_email: str | None = None
    ) -> bool | None:
        self.calls.append(
            ("verify_template", {"expected_owner_email": expected_owner_email})
        )
        if self.template_error is not None:
            raise self.template_error
        return self.template_owned_by_expected_principal


def test_preflight_reports_factory_failure_without_calling_provider() -> None:
    def factory(settings_arg: Settings) -> Any:
        del settings_arg
        raise DriveProvisioningError("drive_credentials_not_configured")

    result = run_drive_preflight(_settings(), provider_factory=factory)

    assert result == DrivePreflightResult(
        ok=False,
        step="credentials",
        category="drive_credentials_not_configured",
    )


def test_preflight_redacts_credential_refresh_failure_end_to_end() -> None:
    # End-to-end through the real transport boundary (not a fake provider):
    # a credential/token failure at the token_supplier boundary must reach
    # this result as a redacted category, never a raw traceback or the
    # underlying provider/library message.
    secret = "invalid_grant: refresh_token revoked. client_secret=super-secret"

    class RaisingTransport:
        def request(self, *args: object, **kwargs: object) -> object:
            raise DriveCredentialsError(secret)

    provider = GoogleDriveQuoteWorkspaceProvider(
        transport=RaisingTransport(),
        root_folder_id="root-1",
        template_file_id="template-1",
    )

    result = run_drive_preflight(
        _settings(drive_expected_principal_email="contacto@origenlab.cl"),
        provider_factory=lambda settings_arg: provider,
    )

    assert result.ok is False
    assert result.step == "principal"
    assert result.category == "drive_credentials_invalid"
    assert "super-secret" not in repr(result)
    assert "client_secret" not in repr(result)


def test_preflight_reports_destination_failure_before_template_check() -> None:
    provider = FakeProvider(
        destination_error=DriveProvisioningError("drive_auth_mode_incompatible")
    )

    result = run_drive_preflight(
        _settings(),
        provider_factory=lambda settings_arg: provider,
    )

    assert result == DrivePreflightResult(
        ok=False,
        step="destination",
        category="drive_auth_mode_incompatible",
    )
    assert [name for name, _ in provider.calls] == ["verify_destination"]


def test_preflight_reports_template_failure() -> None:
    provider = FakeProvider(
        template_error=DriveProvisioningError("drive_template_invalid")
    )

    result = run_drive_preflight(
        _settings(),
        provider_factory=lambda settings_arg: provider,
    )

    assert result == DrivePreflightResult(
        ok=False,
        step="template",
        category="drive_template_invalid",
    )
    assert [name for name, _ in provider.calls] == [
        "verify_destination",
        "verify_template",
    ]


def test_preflight_reports_ok_when_every_check_passes() -> None:
    provider = FakeProvider()

    result = run_drive_preflight(
        _settings(),
        provider_factory=lambda settings_arg: provider,
    )

    assert result == DrivePreflightResult(ok=True, step=None, category=None)
    assert [name for name, _ in provider.calls] == [
        "verify_destination",
        "verify_template",
    ]


def test_preflight_skips_principal_check_when_not_configured() -> None:
    # No ORIGENLAB_DRIVE_EXPECTED_PRINCIPAL_EMAIL configured: backward
    # compatible, no about.get call.
    provider = FakeProvider()

    run_drive_preflight(_settings(), provider_factory=lambda settings_arg: provider)

    assert "verify_principal" not in [name for name, _ in provider.calls]


def test_preflight_verifies_principal_first_when_configured() -> None:
    provider = FakeProvider()

    run_drive_preflight(
        _settings(drive_expected_principal_email="contacto@origenlab.cl"),
        provider_factory=lambda settings_arg: provider,
    )

    call_names = [name for name, _ in provider.calls]

    assert call_names[0] == "verify_principal"
    assert provider.calls[0][1]["expected_email"] == "contacto@origenlab.cl"

    # Destination/template checks receive the same expected owner so
    # ownership can be verified/reported against the same principal.
    destination_call = next(
        kwargs for name, kwargs in provider.calls if name == "verify_destination"
    )
    template_call = next(
        kwargs for name, kwargs in provider.calls if name == "verify_template"
    )
    assert destination_call["expected_owner_email"] == "contacto@origenlab.cl"
    assert template_call["expected_owner_email"] == "contacto@origenlab.cl"


def test_preflight_reports_principal_mismatch_before_any_other_check() -> None:
    provider = FakeProvider(
        principal_error=DriveProvisioningError("drive_principal_mismatch")
    )

    result = run_drive_preflight(
        _settings(drive_expected_principal_email="contacto@origenlab.cl"),
        provider_factory=lambda settings_arg: provider,
    )

    assert result == DrivePreflightResult(
        ok=False,
        step="principal",
        category="drive_principal_mismatch",
    )
    assert [name for name, _ in provider.calls] == ["verify_principal"]


def test_preflight_ok_result_carries_authenticated_principal_email() -> None:
    provider = FakeProvider(principal_email="Contacto@OrigenLab.cl")

    result = run_drive_preflight(
        _settings(drive_expected_principal_email="contacto@origenlab.cl"),
        provider_factory=lambda settings_arg: provider,
    )

    assert result.ok is True
    assert result.principal_email == "Contacto@OrigenLab.cl"


def test_preflight_ok_result_carries_template_ownership_status() -> None:
    provider = FakeProvider(template_owned_by_expected_principal=False)

    result = run_drive_preflight(
        _settings(drive_expected_principal_email="contacto@origenlab.cl"),
        provider_factory=lambda settings_arg: provider,
    )

    # Ownership mismatch on the template is informational, never blocking.
    assert result.ok is True
    assert result.template_owned_by_expected_principal is False


def test_preflight_result_defaults_omit_principal_fields_when_not_configured() -> None:
    provider = FakeProvider()

    result = run_drive_preflight(
        _settings(), provider_factory=lambda settings_arg: provider
    )

    assert result.principal_email is None
    assert result.template_owned_by_expected_principal is None


def test_preflight_never_raises_on_any_redacted_category() -> None:
    def factory(settings_arg: Settings) -> Any:
        del settings_arg
        raise DriveProvisioningError("drive_error")

    # Must not raise -- always returns a result object.
    result = run_drive_preflight(_settings(), provider_factory=factory)

    assert result.ok is False


def test_preflight_result_repr_never_leaks_sensitive_text() -> None:
    result = DrivePreflightResult(
        ok=False,
        step="destination",
        category="drive_auth_mode_incompatible",
    )

    text = repr(result)

    for forbidden in ("token", "credential", "Bearer", "/secure/"):
        assert forbidden not in text
