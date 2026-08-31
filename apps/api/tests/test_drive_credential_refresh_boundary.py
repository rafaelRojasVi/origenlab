"""CRM-Q1C tests for the Drive credential/token-refresh boundary.

``token_supplier`` (built in ``origenlab_api.drive.factory``) calls
``credentials.refresh(...)`` outside the exception types the transport
previously mapped: a google-auth ``RefreshError`` (or any
``GoogleAuthError`` subclass) -- or an unbounded hang -- could otherwise
escape as a raw, unredacted exception. These tests inject fake
``google.auth``/``google.oauth2`` modules (never a real network call or
real google-auth install) to exercise the actual token_supplier closures.
"""

from __future__ import annotations

import sys
import time
import types
from pathlib import Path
from typing import Any, Callable

import pytest

import origenlab_api.drive.factory as factory_module
from origenlab_api.drive.google_drive import DriveCredentialsError


class _FakeGoogleAuthError(Exception):
    """Stand-in for google.auth.exceptions.GoogleAuthError."""


class _FakeRefreshError(_FakeGoogleAuthError):
    """Stand-in for google.auth.exceptions.RefreshError."""


@pytest.fixture
def fake_google_auth_modules(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Install fake google.auth / google.oauth2 submodules in sys.modules.

    Returns a dict the test can mutate to control Credentials.refresh()
    behavior (raise, sleep, or succeed).
    """

    state: dict[str, Any] = {"refresh": lambda: None}

    class FakeCredentials:
        def __init__(self) -> None:
            self.valid = False
            self.token = "fresh-token"

        @classmethod
        def from_authorized_user_file(cls, path: str, scopes: list[str]) -> "FakeCredentials":
            return cls()

        @classmethod
        def from_service_account_file(cls, path: str, scopes: list[str]) -> "FakeCredentials":
            return cls()

        def refresh(self, request: object) -> None:
            state["refresh"]()
            self.valid = True

    class FakeRequest:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

    service_account_module = types.SimpleNamespace(Credentials=FakeCredentials)
    oauth2_module = types.SimpleNamespace(service_account=service_account_module)

    fake_modules = {
        "google.auth.exceptions": types.SimpleNamespace(
            GoogleAuthError=_FakeGoogleAuthError,
            RefreshError=_FakeRefreshError,
        ),
        "google.auth.transport.requests": types.SimpleNamespace(Request=FakeRequest),
        "google.oauth2.credentials": types.SimpleNamespace(Credentials=FakeCredentials),
        "google.oauth2": oauth2_module,
        "google.oauth2.service_account": service_account_module,
        "google.auth.transport": types.SimpleNamespace(),
        "google.auth": types.SimpleNamespace(),
    }

    for name, module in fake_modules.items():
        monkeypatch.setitem(sys.modules, name, module)

    return state


def _write_credential_file(tmp_path: Path) -> Path:
    credential_file = tmp_path / "authorized_user.json"
    credential_file.write_text("{}", encoding="utf-8")
    return credential_file


def test_supply_token_maps_refresh_error_to_redacted_credentials_error(
    fake_google_auth_modules: dict[str, Any],
    tmp_path: Path,
) -> None:
    secret = "invalid_grant: refresh_token revoked. client_secret=super-secret-value"

    def _raise_refresh_error() -> None:
        raise _FakeRefreshError(secret)

    fake_google_auth_modules["refresh"] = _raise_refresh_error

    supplier = factory_module._build_authorized_user_token_supplier(
        _write_credential_file(tmp_path)
    )

    with pytest.raises(DriveCredentialsError) as excinfo:
        supplier()

    assert "super-secret-value" not in str(excinfo.value)
    assert "client_secret" not in str(excinfo.value)


def test_supply_token_maps_service_account_refresh_error_to_redacted_credentials_error(
    fake_google_auth_modules: dict[str, Any],
    tmp_path: Path,
) -> None:
    def _raise_refresh_error() -> None:
        raise _FakeRefreshError("secret token payload xyz")

    fake_google_auth_modules["refresh"] = _raise_refresh_error

    supplier = factory_module._build_service_account_token_supplier(
        _write_credential_file(tmp_path)
    )

    with pytest.raises(DriveCredentialsError) as excinfo:
        supplier()

    assert "secret token payload" not in str(excinfo.value)


def test_supply_token_bounds_a_hanging_refresh_with_a_finite_timeout(
    fake_google_auth_modules: dict[str, Any],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A refresh that never returns must not hang the caller forever -- the
    # credential boundary itself must enforce a finite timeout, since
    # google-auth's own Request(...) call inside credentials.refresh()
    # passes no timeout by default.
    monkeypatch.setattr(factory_module, "_TOKEN_REFRESH_TIMEOUT_SECONDS", 0.05)

    def _hang() -> None:
        time.sleep(2.0)

    fake_google_auth_modules["refresh"] = _hang

    supplier = factory_module._build_authorized_user_token_supplier(
        _write_credential_file(tmp_path)
    )

    started = time.monotonic()
    with pytest.raises(DriveCredentialsError):
        supplier()
    elapsed = time.monotonic() - started

    assert elapsed < 1.0


def test_supply_token_succeeds_and_returns_token_when_refresh_succeeds(
    fake_google_auth_modules: dict[str, Any],
    tmp_path: Path,
) -> None:
    supplier = factory_module._build_authorized_user_token_supplier(
        _write_credential_file(tmp_path)
    )

    assert supplier() == "fresh-token"


def test_token_refresh_timeout_matches_the_single_request_timeout_source() -> None:
    # One source of truth: the credential-refresh timeout must never drift
    # from the same bound used for every Drive HTTP request.
    assert (
        factory_module._TOKEN_REFRESH_TIMEOUT_SECONDS
        == factory_module._REQUEST_TIMEOUT_SECONDS
    )
