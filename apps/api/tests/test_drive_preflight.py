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
from origenlab_api.drive.preflight import DrivePreflightResult, run_drive_preflight
from origenlab_api.settings import Settings


def _settings() -> Settings:
    return Settings(_env_file=None)


class FakeProvider:
    def __init__(
        self,
        *,
        destination_error: DriveProvisioningError | None = None,
        template_error: DriveProvisioningError | None = None,
    ) -> None:
        self.destination_error = destination_error
        self.template_error = template_error
        self.calls: list[str] = []

    def verify_destination(self) -> None:
        self.calls.append("verify_destination")
        if self.destination_error is not None:
            raise self.destination_error

    def verify_template(self) -> None:
        self.calls.append("verify_template")
        if self.template_error is not None:
            raise self.template_error


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
    assert provider.calls == ["verify_destination"]


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
    assert provider.calls == ["verify_destination", "verify_template"]


def test_preflight_reports_ok_when_every_check_passes() -> None:
    provider = FakeProvider()

    result = run_drive_preflight(
        _settings(),
        provider_factory=lambda settings_arg: provider,
    )

    assert result == DrivePreflightResult(ok=True, step=None, category=None)
    assert provider.calls == ["verify_destination", "verify_template"]


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
