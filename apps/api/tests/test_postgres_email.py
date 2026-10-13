"""DB-3D: Postgres recent emails repository."""

from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator
from unittest.mock import patch

import pytest

from origenlab_api.backends.factory import get_repository_bundle
from origenlab_api.repositories.postgres.email import (
    PostgresEmailRecentRepository,
    build_scope_note,
    date_cutoff_iso,
    map_recent_email_row,
)
from origenlab_api.repositories.sqlite.email import SqliteEmailRecentRepository
from origenlab_api.settings import Settings


def _fixture_row(**overrides: Any) -> dict[str, Any]:
    base = {
        "email_id": 42,
        "date_iso": "2026-05-19T10:00:00-04:00",
        "subject_preview": "Cotización equipo",
        "sender_preview": "client@example.cl",
        "source_file": None,
        "folder_hint": "[Gmail]/Enviados",
        "has_positive_signal": True,
        "has_suppression_signal": False,
        "predicted_label": "customer",
    }
    base.update(overrides)
    return base


def test_map_recent_email_row_null_source_file_uses_folder_hint() -> None:
    mapped = map_recent_email_row(_fixture_row())
    assert mapped["source_file"] is None
    assert mapped["folder_hint"] == "[Gmail]/Enviados"
    assert mapped["email_id"] == 42
    assert "body" not in mapped


def test_map_recent_email_row_with_source_file() -> None:
    mapped = map_recent_email_row(
        _fixture_row(source_file="gmail:contacto@origenlab.cl/[Gmail]/Enviados")
    )
    assert mapped["source_file"] is not None
    assert mapped["folder_hint"] == "[Gmail]"


def test_build_scope_note_empty() -> None:
    assert "dashboard sync" in build_scope_note(items=[])


def test_build_scope_note_null_source_file() -> None:
    note = build_scope_note(items=[{"source_file": None}])
    assert "source_file" in note


def test_date_cutoff_iso() -> None:
    assert len(date_cutoff_iso(7)) == 10


def test_repository_bundle_default_uses_sqlite_email(tmp_path: Path) -> None:
    settings = Settings(api_backend="sqlite", sqlite_path=tmp_path / "x.sqlite")
    bundle = get_repository_bundle(settings)
    assert isinstance(bundle.email_recent, SqliteEmailRecentRepository)


def test_repository_bundle_postgres_uses_postgres_email() -> None:
    settings = Settings(
        api_backend="postgres",
        postgres_url="postgresql://127.0.0.1:5432/scratch",
    )
    bundle = get_repository_bundle(settings)
    assert isinstance(bundle.email_recent, PostgresEmailRecentRepository)


@contextmanager
def _fake_postgres_connection(rows: list[dict[str, Any]]) -> Iterator[Any]:
    class FakeCursor:
        def __init__(self) -> None:
            self.last_sql = ""
            self.last_params: dict[str, Any] = {}

        def __enter__(self) -> FakeCursor:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def execute(self, sql: str, params: dict[str, Any]) -> None:
            self.last_sql = sql
            self.last_params = params

        def fetchall(self) -> list[dict[str, Any]]:
            return rows

    class FakeConn:
        def __init__(self) -> None:
            self.last_cursor: FakeCursor | None = None

        def cursor(self, *, row_factory: Any = None) -> FakeCursor:
            self.last_cursor = FakeCursor()
            return self.last_cursor

        def __enter__(self) -> FakeConn:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

    fake = FakeConn()

    @contextmanager
    def _connect(_settings: Settings) -> Iterator[FakeConn]:
        yield fake

    with patch(
        "origenlab_api.repositories.postgres.email.postgres_connection",
        _connect,
    ):
        yield fake


def test_postgres_email_queries_view() -> None:
    settings = Settings(
        api_backend="postgres",
        postgres_url="postgresql://127.0.0.1:5432/test",
    )
    repo = PostgresEmailRecentRepository(settings)
    with _fake_postgres_connection([_fixture_row()]) as conn:
        result = repo.list_recent(days=14, limit=10, exclude_noise=True, folder="enviados")
        cur = conn.last_cursor
    assert cur is not None
    assert "api.v_recent_email" in cur.last_sql
    assert "date_iso DESC NULLS LAST" in cur.last_sql
    assert cur.last_params["limit"] == 10
    assert cur.last_params["exclude_noise"] is True
    assert cur.last_params["folder"] == "enviados"
    assert len(result.items) == 1
    assert result.meta.data_source == "postgres_mirror"
    assert result.enrichment_available is True
    assert result.reduced_mode is False
    assert "source_file" in result.scope_note


def test_postgres_email_exclude_noise_params() -> None:
    settings = Settings(
        api_backend="postgres",
        postgres_url="postgresql://127.0.0.1:5432/test",
    )
    repo = PostgresEmailRecentRepository(settings)
    with _fake_postgres_connection([]) as conn:
        repo.list_recent(exclude_noise=True)
        cur = conn.last_cursor
    assert cur is not None
    assert "has_suppression_signal" in cur.last_sql
    assert "mailer-daemon" in cur.last_sql
    assert cur.last_params["noise_labels"]


def test_postgres_email_no_rows_graceful() -> None:
    settings = Settings(
        api_backend="postgres",
        postgres_url="postgresql://127.0.0.1:5432/test",
    )
    repo = PostgresEmailRecentRepository(settings)
    with _fake_postgres_connection([]):
        result = repo.list_recent(days=7, limit=5)
    assert result.items == []
    assert result.reduced_mode is True
    assert "dashboard sync" in result.scope_note


@pytest.mark.skipif(
    not (os.environ.get("ORIGENLAB_TEST_POSTGRES_URL") or "").strip(),
    reason="Set ORIGENLAB_TEST_POSTGRES_URL for disposable Postgres integration.",
)
def test_postgres_email_integration_against_mirror() -> None:
    pytest.importorskip("psycopg")
    from psycopg import OperationalError

    url = os.environ["ORIGENLAB_TEST_POSTGRES_URL"].strip()
    settings = Settings(api_backend="postgres", postgres_url=url)
    repo = PostgresEmailRecentRepository(settings)
    try:
        result = repo.list_recent(days=30, limit=5, exclude_noise=True)
    except OperationalError as exc:
        pytest.skip(f"Postgres not reachable at ORIGENLAB_TEST_POSTGRES_URL: {exc}")
    assert result.meta.data_source == "postgres_mirror"
    if result.items:
        assert result.reduced_mode is False
        dumped = str(result.items[0])
        assert "body_preview" not in dumped
    else:
        assert result.reduced_mode is True
