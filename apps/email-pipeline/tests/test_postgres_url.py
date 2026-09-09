"""The neutral Postgres URL helpers, moved out of postgres_outbound_audit."""

from __future__ import annotations

import pytest

from origenlab_email_pipeline import postgres_outbound_audit as audit
from origenlab_email_pipeline.postgres_url import (
    PostgresUrlError,
    normalize_postgres_url,
    redact_postgres_url,
    resolve_postgres_url,
)


def test_normalize_strips_the_sqlalchemy_driver() -> None:
    assert normalize_postgres_url("postgresql+psycopg://u@h/db") == "postgresql://u@h/db"
    assert normalize_postgres_url("postgresql+psycopg2://u@h/db") == "postgresql://u@h/db"
    assert normalize_postgres_url("  postgresql://u@h/db  ") == "postgresql://u@h/db"


def test_redact_hides_the_password_and_keeps_the_host() -> None:
    red = redact_postgres_url("postgresql://user:secret@db.example:5432/name")
    assert "secret" not in red
    assert "***" in red
    assert "db.example:5432" in red


@pytest.mark.parametrize("value", [None, "", "not a url"])
def test_redact_never_raises(value) -> None:
    assert isinstance(redact_postgres_url(value), str)


def test_explicit_url_wins_over_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ORIGENLAB_POSTGRES_URL", "postgresql://u@h/from_env")
    assert resolve_postgres_url("postgresql+psycopg://u@h/explicit") == "postgresql://u@h/explicit"


def test_environment_keys_are_consulted_in_order(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ORIGENLAB_POSTGRES_URL", raising=False)
    monkeypatch.setenv("ALEMBIC_DATABASE_URL", "postgresql://u@h/alembic")
    assert resolve_postgres_url(None) == "postgresql://u@h/alembic"
    monkeypatch.setenv("ORIGENLAB_POSTGRES_URL", "postgresql://u@h/origenlab")
    assert resolve_postgres_url(None) == "postgresql://u@h/origenlab"


def test_unresolved_is_none_unless_required(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ORIGENLAB_POSTGRES_URL", raising=False)
    monkeypatch.delenv("ALEMBIC_DATABASE_URL", raising=False)
    assert resolve_postgres_url(None) is None
    with pytest.raises(PostgresUrlError):
        resolve_postgres_url(None, required=True)


def test_the_audit_wrapper_still_raises_its_own_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """The export lanes catch OutboundAuditError; the move must not change that."""
    monkeypatch.delenv("ORIGENLAB_POSTGRES_URL", raising=False)
    monkeypatch.delenv("ALEMBIC_DATABASE_URL", raising=False)
    with pytest.raises(audit.OutboundAuditError):
        audit.resolve_postgres_url(None, require_when_requested=True, audit_requested=True)
    assert issubclass(audit.OutboundAuditError, PostgresUrlError)
