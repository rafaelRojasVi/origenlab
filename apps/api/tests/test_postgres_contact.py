"""DB-3E: Postgres contact detail repository."""

from __future__ import annotations

import json
import os
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator
from unittest.mock import patch

import pytest

from origenlab_api.backends.factory import get_repository_bundle
from origenlab_api.repositories.postgres.contact import (
    PostgresContactRepository,
    build_fallback_result,
    map_profile_row,
)
from origenlab_api.repositories.sqlite.contact import SqliteContactRepository
from origenlab_api.settings import Settings


def _profile_row(**overrides: Any) -> dict[str, Any]:
    base = {
        "email_norm": "known@cliente.cl",
        "email_display": "known@cliente.cl",
        "contact_name": "Known Client",
        "domain": "cliente.cl",
        "organization_name": "Cliente SA",
        "organization_domain": "cliente.cl",
        "first_seen_at": "2026-01-01T00:00:00+00:00",
        "last_seen_at": "2026-05-19T10:00:00+00:00",
        "message_count": 12,
        "outreach_state": "contacted",
        "last_contacted_at": "2026-05-18T10:00:00+00:00",
        "outreach_source": "test_source",
        "outreach_updated_by": "operator",
        "outreach_notes": "note",
        "suppressed_email": False,
        "suppressed_domain": False,
        "do_not_repeat": True,
        "sent_count": 1,
        "latest_sent_at": "2026-05-18T10:00:00+00:00",
        "latest_subject": "Cotización equipo",
        "mart_present": True,
    }
    base.update(overrides)
    return base


def test_map_profile_row_known_contact() -> None:
    result = map_profile_row(
        _profile_row(),
        email_raw="Known@cliente.cl",
        email_norm="known@cliente.cl",
        domain="cliente.cl",
    )
    assert result.data_source == "postgres_mirror"
    assert result.reduced_mode is False
    assert result.contact["name"] == "Known Client"
    assert result.contact["message_count"] == 12
    assert result.outreach["state"] == "contacted"
    assert result.outreach["do_not_repeat"] is True
    assert result.sent_history["sent_count"] == 1
    dumped = json.dumps(result.contact)
    assert "body" not in dumped


def test_build_fallback_unknown_email() -> None:
    result = build_fallback_result(
        email_raw="unknown@elsewhere.cl",
        email_norm="unknown@elsewhere.cl",
        domain="elsewhere.cl",
        outreach_row=None,
        suppressed_email=False,
        suppressed_domain=False,
    )
    assert result.contact["message_count"] == 0
    assert result.outreach["state"] is None
    assert result.reduced_mode is False
    assert any("contact_master" in w for w in result.warnings)


def test_build_fallback_outreach_only() -> None:
    result = build_fallback_result(
        email_raw="outreach@x.cl",
        email_norm="outreach@x.cl",
        domain="x.cl",
        outreach_row={
            "state": "replied",
            "last_contacted_at": datetime(2026, 5, 1, tzinfo=timezone.utc),
            "source": "manual",
            "updated_by": "op",
            "notes": "ok",
        },
        suppressed_email=False,
        suppressed_domain=False,
    )
    assert result.outreach["state"] == "replied"
    assert result.outreach["do_not_repeat"] is True


def test_build_fallback_suppression_flags() -> None:
    result = build_fallback_result(
        email_raw="blocked@x.cl",
        email_norm="blocked@x.cl",
        domain="x.cl",
        outreach_row=None,
        suppressed_email=True,
        suppressed_domain=False,
    )
    assert result.outreach["suppressed_email"] is True
    assert result.outreach["do_not_repeat"] is True


def test_repository_bundle_sqlite_contact(tmp_path: Path) -> None:
    settings = Settings(api_backend="sqlite", sqlite_path=tmp_path / "x.sqlite")
    bundle = get_repository_bundle(settings)
    assert isinstance(bundle.contact, SqliteContactRepository)


def test_repository_bundle_postgres_contact() -> None:
    settings = Settings(
        api_backend="postgres",
        postgres_url="postgresql://127.0.0.1:5432/scratch",
    )
    bundle = get_repository_bundle(settings)
    assert isinstance(bundle.contact, PostgresContactRepository)


def test_invalid_email_raises_value_error() -> None:
    repo = PostgresContactRepository(
        Settings(api_backend="postgres", postgres_url="postgresql://127.0.0.1:5432/t")
    )
    with pytest.raises(ValueError, match="Correo no válido"):
        repo.get_contact_detail("not-an-email")


@contextmanager
def _fake_postgres_connection(
    *,
    profile: dict[str, Any] | None,
    outreach: dict[str, Any] | None,
    email_suppressed: bool,
    domain_norms: list[str],
) -> Iterator[Any]:
    class FakeCursor:
        def __init__(self) -> None:
            self.calls: list[tuple[str, dict[str, Any]]] = []

        def __enter__(self) -> FakeCursor:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def execute(self, sql: str, params: dict[str, Any] | None = None) -> None:
            self.calls.append((sql, params or {}))

        def fetchone(self) -> dict[str, Any] | None:
            sql = self.calls[-1][0]
            if "api.v_contact_profile" in sql:
                return profile
            if "outreach_contact_state" in sql:
                return outreach
            if "contact_email_suppression" in sql:
                return {"?": 1} if email_suppressed else None
            return None

        def fetchall(self) -> list[dict[str, Any]]:
            sql = self.calls[-1][0]
            if "contact_domain_suppression" in sql:
                return [{"domain_norm": d} for d in domain_norms]
            return []

    class FakeConn:
        def __init__(self) -> None:
            self._cursor = FakeCursor()

        def cursor(self, *, row_factory: Any = None) -> FakeCursor:
            return self._cursor

        def __enter__(self) -> FakeConn:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

    fake = FakeConn()

    @contextmanager
    def _connect(_settings: Settings) -> Iterator[FakeConn]:
        yield fake

    with patch(
        "origenlab_api.repositories.postgres.contact.postgres_connection",
        _connect,
    ):
        yield fake


def test_postgres_queries_profile_view() -> None:
    repo = PostgresContactRepository(
        Settings(api_backend="postgres", postgres_url="postgresql://127.0.0.1:5432/t")
    )
    with _fake_postgres_connection(
        profile=_profile_row(),
        outreach=None,
        email_suppressed=False,
        domain_norms=[],
    ) as conn:
        result = repo.get_contact_detail("known@cliente.cl")
        sqls = [c[0] for c in conn._cursor.calls]
    assert any("api.v_contact_profile" in s for s in sqls)
    assert result.contact["normalized_email"] == "known@cliente.cl"
    assert result.data_source == "postgres_mirror"


def test_postgres_fallback_queries_outbound_tables() -> None:
    repo = PostgresContactRepository(
        Settings(api_backend="postgres", postgres_url="postgresql://127.0.0.1:5432/t")
    )
    with _fake_postgres_connection(
        profile=None,
        outreach={
            "state": "contacted",
            "last_contacted_at": datetime(2026, 5, 18, tzinfo=timezone.utc),
            "source": "test",
            "updated_by": "op",
            "notes": "",
        },
        email_suppressed=False,
        domain_norms=["blocked-domain.cl"],
    ) as conn:
        result = repo.get_contact_detail("user@blocked-domain.cl")
        sqls = [c[0] for c in conn._cursor.calls]
    assert any("api.v_contact_profile" in s for s in sqls)
    assert any("outreach_contact_state" in s for s in sqls)
    assert any("contact_email_suppression" in s for s in sqls)
    assert any("contact_domain_suppression" in s for s in sqls)
    assert result.outreach["state"] == "contacted"
    assert result.outreach["suppressed_domain"] is True
    assert result.contact["message_count"] == 0


@pytest.mark.skipif(
    not (os.environ.get("ORIGENLAB_TEST_POSTGRES_URL") or "").strip(),
    reason="Set ORIGENLAB_TEST_POSTGRES_URL for disposable Postgres integration.",
)
def test_postgres_contact_integration_against_mirror() -> None:
    pytest.importorskip("psycopg")
    from psycopg import OperationalError

    url = os.environ["ORIGENLAB_TEST_POSTGRES_URL"].strip()
    repo = PostgresContactRepository(
        Settings(api_backend="postgres", postgres_url=url)
    )
    try:
        result = repo.get_contact_detail("nobody-here@example.com")
    except OperationalError as exc:
        pytest.skip(f"Postgres not reachable: {exc}")
    assert result.data_source == "postgres_mirror"
    assert result.contact["normalized_email"] == "nobody-here@example.com"
    dumped = json.dumps(result.contact)
    assert "body_preview" not in dumped
    assert '"body"' not in dumped
