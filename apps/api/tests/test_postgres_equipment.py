"""DB-3B: Postgres equipment opportunities repository."""

from __future__ import annotations

import inspect
import json
import os
from contextlib import contextmanager
from typing import Any, Iterator
from unittest.mock import patch

from pathlib import Path

import pytest

from origenlab_api.backends.factory import get_repository_bundle, validate_api_settings
from origenlab_api.repositories.postgres.equipment import (
    PostgresEquipmentOpportunityRepository,
    build_equipment_meta,
    map_equipment_row,
)
from origenlab_api.repositories.sqlite.equipment import SqliteEquipmentOpportunityRepository
from origenlab_api.settings import Settings


def _fixture_row(**overrides: Any) -> dict[str, Any]:
    base = {
        "priority_rank": 1,
        "codigo_licitacion": "LP-001",
        "buyer": "Buyer One",
        "region": "RM",
        "close_date": "2026-06-01",
        "equipment_category": "centrifuge",
        "item_description": "Centrifuge unit",
        "next_action": "quote_now",
        "safe_channel": "mercado_publico_bid",
        "supplier_needed": "yes",
        "contact_status": "no_verified_buyer_email",
        "operator_note": "fit=90",
        "source_path": "/data/equipment_first_operator_queue_20260518.csv",
        "source_kind": "csv_artifact",
        "artifact_basename": "equipment_first_operator_queue_20260518.csv",
        "canonical_reason": "manifest_canonical",
        "campaign_mode": "equipment_first",
        "opportunity_key": "equipment:equipment_queue:lp-001",
    }
    base.update(overrides)
    return base


def test_map_equipment_row_fixture() -> None:
    mapped = map_equipment_row(_fixture_row())
    assert mapped["priority_rank"] == 1
    assert mapped["codigo_licitacion"] == "LP-001"
    assert mapped["close_date"] == "2026-06-01"
    assert mapped["opportunity_key"] == "equipment:equipment_queue:lp-001"


def test_map_equipment_row_merges_extra_json_detail_fields() -> None:
    mapped = map_equipment_row(
        _fixture_row(
            close_at="2026-06-17T19:00:00-04:00",
            extra_json={
                "fecha_publicacion": "10/06/2026",
                "validity_status": "open",
                "chilecompra_status": "Publicada",
                "mercado_publico_url": "https://www.mercadopublico.cl/BuscarLicitacion?codigoLicitacion=LP-001",
                "unspsc_code": "41100000",
                "cantidad": "3",
                "producto": "Centrifuga",
            },
        )
    )
    assert mapped["close_at"].startswith("2026-06-17")
    assert mapped["fecha_publicacion"] == "10/06/2026"
    assert mapped["validity_status"] == "open"
    assert mapped["chilecompra_status"] == "Publicada"
    assert "mercadopublico.cl" in mapped["mercado_publico_url"]
    assert mapped["unspsc_code"] == "41100000"
    assert mapped["cantidad"] == "3"
    assert mapped["producto"] == "Centrifuga"
    assert "ticket" not in mapped["mercado_publico_url"].lower()


def test_map_equipment_row_parses_anexos_from_extra_json() -> None:
    mapped = map_equipment_row(
        _fixture_row(
            extra_json={
                "anexos_json": json.dumps(
                    [
                        {
                            "nombre": "Bases administrativas.pdf",
                            "tipo": "Bases",
                            "url": "https://www.mercadopublico.cl/archivos/bases.pdf",
                        },
                        {
                            "nombre": "Ticket doc",
                            "url": "https://api.mercadopublico.cl/v1/doc?ticket=SECRET",
                        },
                    ]
                )
            },
        )
    )
    assert len(mapped["anexos"]) == 2
    assert mapped["anexos"][0]["nombre"] == "Bases administrativas.pdf"
    assert "mercadopublico.cl" in mapped["anexos"][0]["url"]
    assert mapped["anexos"][1]["url"] == ""


def test_build_equipment_meta_empty_has_note() -> None:
    meta = build_equipment_meta(items=[], source_path=None, campaign_mode=None)
    assert meta.data_source == "postgres_mirror"
    assert meta.read_only is True
    assert meta.reduced_mode is True
    assert "include-equipment-opportunities" in meta.note


def test_build_equipment_meta_with_rows_not_reduced() -> None:
    meta = build_equipment_meta(
        items=[{"priority_rank": 1}],
        source_path="/queue.csv",
        source_kind="csv_artifact",
        artifact_basename="queue.csv",
        canonical_reason="manifest_canonical",
        campaign_mode="equipment_first",
    )
    assert meta.reduced_mode is False
    assert meta.count == 1
    assert meta.source_path == "/queue.csv"
    assert meta.source_kind == "csv_artifact"
    assert meta.artifact_basename == "queue.csv"
    assert meta.canonical_reason == "manifest_canonical"
    assert meta.campaign_mode == "equipment_first"


def test_repository_bundle_default_uses_sqlite_equipment(tmp_path: Path) -> None:
    settings = Settings(
        api_backend="sqlite",
        active_current=tmp_path / "current",
    )
    bundle = get_repository_bundle(settings)
    assert isinstance(bundle.equipment, SqliteEquipmentOpportunityRepository)


def test_repository_bundle_postgres_uses_postgres_equipment() -> None:
    settings = Settings(
        api_backend="postgres",
        postgres_url="postgresql://127.0.0.1:5432/scratch",
    )
    bundle = get_repository_bundle(settings)
    assert isinstance(bundle.equipment, PostgresEquipmentOpportunityRepository)


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
        "origenlab_api.repositories.postgres.equipment.postgres_connection",
        _connect,
    ):
        yield fake


def test_postgres_equipment_queries_canonical_view() -> None:
    settings = Settings(
        api_backend="postgres",
        postgres_url="postgresql://127.0.0.1:5432/test",
    )
    repo = PostgresEquipmentOpportunityRepository(settings)
    with _fake_postgres_connection([_fixture_row()]) as conn:
        items, meta = repo.list_equipment(limit=10, priority=2, safe_channel="mercado_publico_bid")
        cur = conn.last_cursor
    assert cur is not None
    sql_lower = cur.last_sql.lower()
    assert "api.v_equipment_opportunity_current" in sql_lower
    assert "opportunity_key" in sql_lower
    assert "source_kind" in sql_lower
    assert "artifact_basename" in sql_lower
    assert "canonical_reason" in sql_lower
    assert "is_canonical_source" not in sql_lower
    assert "active_current" not in sql_lower
    assert "equipment_first_operator_queue" not in sql_lower
    assert cur.last_params["priority"] == 2
    assert cur.last_params["safe_channel"] == "mercado_publico_bid"
    assert cur.last_params["limit"] == 10
    assert len(items) == 1
    assert meta.data_source == "postgres_mirror"
    assert meta.source_path.endswith(".csv")
    assert meta.source_kind == "csv_artifact"
    assert meta.artifact_basename.endswith(".csv")
    assert meta.canonical_reason == "manifest_canonical"


def test_postgres_equipment_repository_does_not_use_csv_fallback() -> None:
    import origenlab_api.repositories.postgres.equipment as pg_equipment

    source = inspect.getsource(pg_equipment)
    forbidden = (
        "fetch_equipment_opportunities",
        "resolved_active_current",
        "active_current",
    )
    for token in forbidden:
        assert token not in source


def test_production_env_rejects_sqlite_backend_before_csv_equipment_repo() -> None:
    settings = Settings(
        env="production",
        api_backend="sqlite",
        postgres_url="postgresql://127.0.0.1:5432/db",
        api_cors_origins="https://dashboard.origenlab.cl",
    )
    with pytest.raises(ValueError, match="ORIGENLAB_ENV=production requires ORIGENLAB_API_BACKEND=postgres"):
        validate_api_settings(settings)
    with pytest.raises(ValueError, match="ORIGENLAB_API_BACKEND=postgres"):
        get_repository_bundle(settings)


def test_postgres_equipment_no_rows_graceful() -> None:
    settings = Settings(
        api_backend="postgres",
        postgres_url="postgresql://127.0.0.1:5432/test",
    )
    repo = PostgresEquipmentOpportunityRepository(settings)
    with _fake_postgres_connection([]):
        items, meta = repo.list_equipment(limit=5)
    assert items == []
    assert meta.count == 0
    assert meta.reduced_mode is True
    assert meta.note


def test_postgres_equipment_exclude_account_intelligence_filter_param() -> None:
    settings = Settings(
        api_backend="postgres",
        postgres_url="postgresql://127.0.0.1:5432/test",
    )
    repo = PostgresEquipmentOpportunityRepository(settings)
    with _fake_postgres_connection([]) as conn:
        repo.list_equipment(limit=5, include_account_intelligence=False)
        cur = conn.last_cursor
    assert cur is not None
    assert cur.last_params["include_account_intel"] is False


@pytest.mark.skipif(
    not (os.environ.get("ORIGENLAB_TEST_POSTGRES_URL") or "").strip(),
    reason="Set ORIGENLAB_TEST_POSTGRES_URL for disposable Postgres integration.",
)
def test_postgres_equipment_integration_against_mirror() -> None:
    pytest.importorskip("psycopg")
    from psycopg import OperationalError

    url = os.environ["ORIGENLAB_TEST_POSTGRES_URL"].strip()
    settings = Settings(api_backend="postgres", postgres_url=url)
    repo = PostgresEquipmentOpportunityRepository(settings)
    try:
        items, meta = repo.list_equipment(limit=5)
    except OperationalError as exc:
        pytest.skip(f"Postgres not reachable at ORIGENLAB_TEST_POSTGRES_URL: {exc}")
    assert meta.data_source == "postgres_mirror"
    assert meta.read_only is True
    if items:
        assert meta.reduced_mode is False
        assert meta.source_path
        assert items[0]["priority_rank"] >= 0
    else:
        assert meta.reduced_mode is True
        assert "include-equipment-opportunities" in meta.note
