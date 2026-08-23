"""Optional SQLite PR3 ↔ ARCH-2A Postgres parity test for ARCH-2B."""

# ruff: noqa: E402

from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from urllib.parse import quote

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from origenlab_api.main import create_app
from origenlab_api.services.commercial_opportunity_service import (
    build_commercial_opportunities_response,
    build_commercial_opportunity_detail_response,
)
from origenlab_api.settings import Settings, get_settings


_POSTGRES_URL = (os.environ.get("ORIGENLAB_TEST_POSTGRES_URL") or "").strip()
_SQLITE_PATH_RAW = (os.environ.get("ORIGENLAB_TEST_SQLITE_PATH") or "").strip()


pytestmark = pytest.mark.skipif(
    not (_POSTGRES_URL and _SQLITE_PATH_RAW),
    reason=(
        "Set ORIGENLAB_TEST_POSTGRES_URL and ORIGENLAB_TEST_SQLITE_PATH "
        "for ARCH-2B real-backend parity."
    ),
)


def _settings() -> tuple[Settings, Settings, Path]:
    sqlite_path = Path(_SQLITE_PATH_RAW).expanduser().resolve()
    assert sqlite_path.is_file(), sqlite_path

    sqlite_settings = Settings(
        _env_file=None,
        env="development",
        api_backend="sqlite",
        sqlite_path=sqlite_path,
    )
    postgres_settings = Settings(
        _env_file=None,
        env="development",
        api_backend="postgres",
        postgres_url=_POSTGRES_URL,
    )
    return sqlite_settings, postgres_settings, sqlite_path


def _normalize(value):
    if isinstance(value, dict):
        return {
            key: _normalize(item)
            for key, item in value.items()
            if key not in {"synced_at", "data_source"}
        }
    if isinstance(value, list):
        return [_normalize(item) for item in value]
    return value


def _fetch_all(settings: Settings):
    rows = []
    offset = 0
    total = None

    while True:
        response = build_commercial_opportunities_response(
            settings,
            limit=200,
            offset=offset,
        )

        if total is None:
            total = response.meta.total_count
        else:
            assert response.meta.total_count == total

        rows.extend(response.items)

        if not response.items:
            break

        offset += len(response.items)
        if offset >= total:
            break

    return int(total or 0), rows


def _child_opportunity_ids(sqlite_path: Path) -> list[str]:
    encoded = quote(sqlite_path.as_posix(), safe="/")
    conn = sqlite3.connect(
        f"file:{encoded}?mode=ro",
        uri=True,
    )
    try:
        return [
            str(row[0])
            for row in conn.execute(
                """
                SELECT opportunity_id
                FROM (
                    SELECT opportunity_id
                    FROM commercial_opportunity_event

                    UNION

                    SELECT opportunity_id
                    FROM commercial_opportunity_evidence

                    UNION

                    SELECT opportunity_id
                    FROM commercial_opportunity_conflict
                    WHERE opportunity_id IS NOT NULL
                )
                ORDER BY opportunity_id
                """
            ).fetchall()
        ]
    finally:
        conn.close()


def test_commercial_opportunity_full_list_parity() -> None:
    sqlite_settings, postgres_settings, _ = _settings()

    sqlite_total, sqlite_rows = _fetch_all(sqlite_settings)
    postgres_total, postgres_rows = _fetch_all(postgres_settings)

    # A zero/zero comparison would be technically equal but operationally useless.
    assert sqlite_total > 0
    assert postgres_total == sqlite_total
    assert len(sqlite_rows) == sqlite_total
    assert len(postgres_rows) == postgres_total

    sqlite_payload = [_normalize(row.model_dump()) for row in sqlite_rows]
    postgres_payload = [_normalize(row.model_dump()) for row in postgres_rows]

    assert [row["opportunity_id"] for row in sqlite_payload] == [
        row["opportunity_id"] for row in postgres_payload
    ]

    assert postgres_payload == sqlite_payload


def test_commercial_opportunity_detail_graph_parity() -> None:
    sqlite_settings, postgres_settings, sqlite_path = _settings()

    opportunity_ids = _child_opportunity_ids(sqlite_path)
    assert opportunity_ids

    for opportunity_id in opportunity_ids:
        sqlite_detail = build_commercial_opportunity_detail_response(
            sqlite_settings,
            opportunity_id,
        )
        postgres_detail = build_commercial_opportunity_detail_response(
            postgres_settings,
            opportunity_id,
        )

        assert sqlite_detail is not None
        assert postgres_detail is not None

        assert _normalize(postgres_detail.model_dump()) == _normalize(
            sqlite_detail.model_dump()
        )


def test_commercial_opportunity_postgres_http_path() -> None:
    _, postgres_settings, _ = _settings()

    app = create_app()
    app.dependency_overrides[get_settings] = lambda: postgres_settings

    with TestClient(app) as client:
        response = client.get("/opportunities/commercial?limit=3")
        assert response.status_code == 200

        body = response.json()
        assert body["meta"]["data_source"] == "postgres_mirror"
        assert body["meta"]["read_only"] is True
        assert body["meta"]["total_count"] > 0
        assert body["meta"]["count"] == 3
        assert len(body["items"]) == 3

        opportunity_id = body["items"][0]["opportunity_id"]

        detail = client.get(f"/opportunities/commercial/{opportunity_id}")
        assert detail.status_code == 200
        assert detail.json()["opportunity"]["opportunity_id"] == opportunity_id

        missing = client.get("/opportunities/commercial/definitely-not-real")
        assert missing.status_code == 404
        assert missing.json()["error"]["code"] == "not_found"
