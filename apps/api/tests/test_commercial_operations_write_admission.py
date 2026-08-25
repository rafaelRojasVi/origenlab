"""ARCH-3B1 fail-closed admission tests for commercial operation writes."""

from __future__ import annotations

import pytest

from origenlab_api.backends.factory import validate_api_settings
from origenlab_api.settings import Settings


def _settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "api_backend": "postgres",
        "postgres_url": "postgresql://readonly/example",
        "api_cors_origins": "https://dashboard.example.com",
        "api_allowed_hosts": "api.example.com",
        "api_auth_token": "test-token",
        "env": None,
    }
    values.update(overrides)
    return Settings(**values)


def test_write_feature_defaults_disabled() -> None:
    settings = _settings()

    assert settings.commercial_operations_writes_enabled is False


def test_write_url_is_distinct_from_read_url() -> None:
    settings = _settings(
        postgres_write_url="postgresql://writer/example",
    )

    assert settings.require_postgres_url() == "postgresql://readonly/example"
    assert settings.require_postgres_write_url() == "postgresql://writer/example"


def test_write_url_never_falls_back_to_read_url() -> None:
    settings = _settings(
        postgres_write_url=None,
    )

    with pytest.raises(
        ValueError,
        match="ORIGENLAB_POSTGRES_WRITE_URL",
    ):
        settings.require_postgres_write_url()


def test_enabling_writes_requires_write_url() -> None:
    settings = _settings(
        commercial_operations_writes_enabled=True,
        postgres_write_url=None,
    )

    with pytest.raises(
        ValueError,
        match="ORIGENLAB_POSTGRES_WRITE_URL",
    ):
        validate_api_settings(settings)


def test_enabling_writes_requires_postgres_backend() -> None:
    settings = _settings(
        api_backend="sqlite",
        commercial_operations_writes_enabled=True,
        postgres_write_url="postgresql://writer/example",
    )

    with pytest.raises(
        ValueError,
        match="commercial operations writes require",
    ):
        validate_api_settings(settings)


def test_disabled_writes_do_not_require_write_url() -> None:
    settings = _settings(
        commercial_operations_writes_enabled=False,
        postgres_write_url=None,
    )

    validate_api_settings(settings)


def test_enabled_writes_admit_separate_writer() -> None:
    settings = _settings(
        commercial_operations_writes_enabled=True,
        postgres_write_url="postgresql://writer/example",
    )

    validate_api_settings(settings)


def test_write_connection_uses_write_dsn_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from origenlab_api.repositories.postgres import write_common

    observed: dict[str, object] = {}

    class FakeConnection:
        def __enter__(self) -> "FakeConnection":
            return self

        def __exit__(self, *args: object) -> None:
            return None

    class FakePg:
        class Error(Exception):
            pass

        def connect(
            self,
            url: str,
            *,
            connect_timeout: int,
            options: str,
        ) -> FakeConnection:
            observed["url"] = url
            observed["connect_timeout"] = connect_timeout
            observed["options"] = options
            return FakeConnection()

    monkeypatch.setattr(
        write_common,
        "require_psycopg",
        lambda: FakePg(),
    )

    settings = _settings(
        postgres_url="postgresql://readonly/SHOULD_NOT_BE_USED",
        postgres_write_url="postgresql://writer/MUST_BE_USED",
    )

    with write_common.postgres_write_connection(settings):
        pass

    assert observed["url"] == "postgresql://writer/MUST_BE_USED"
    assert "readonly" not in str(observed["url"])
