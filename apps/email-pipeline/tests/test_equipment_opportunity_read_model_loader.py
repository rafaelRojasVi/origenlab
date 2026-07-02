"""Direct equipment read-model loader tests."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator
from unittest.mock import MagicMock

from origenlab_email_pipeline import equipment_opportunity_mirror as mirror
from origenlab_email_pipeline import equipment_opportunity_read_model_loader as direct
from origenlab_email_pipeline.equipment_opportunity_read_model_loader import (
    apply_direct_rows_load,
    preview_direct_rows_load,
)


_ROWS = [
    {
        "priority_rank": "1",
        "codigo_licitacion": "1051-1-LP26",
        "buyer": "Hospital Demo",
        "region": "RM",
        "close_date": "2026-07-10T16:00:00",
        "equipment_category": "centrifuge",
        "item_description": "Centrifuga refrigerada",
        "next_action": "quote_now",
        "safe_channel": "mercado_publico_only",
        "supplier_needed": "yes",
        "contact_status": "review_required",
        "operator_note": "ChileCompra API candidate",
        "source": "chilecompra_api",
    }
]


class _RecordingCursor:
    def __init__(self, *, existing_source_id: int | None = None) -> None:
        self.existing_source_id = existing_source_id
        self.statements: list[tuple[str, tuple[Any, ...] | None]] = []
        self._fetch: tuple[Any, ...] | None = None

    def execute(self, sql: str, params: tuple[Any, ...] | None = None) -> None:
        self.statements.append((sql, params))
        if "SELECT id FROM commercial.equipment_opportunity_source" in sql:
            self._fetch = (self.existing_source_id,) if self.existing_source_id is not None else None
        elif "RETURNING id" in sql:
            self._fetch = (77,)
        else:
            self._fetch = None

    def fetchone(self) -> tuple[Any, ...] | None:
        return self._fetch

    def __enter__(self) -> _RecordingCursor:
        return self

    def __exit__(self, *_args: object) -> None:
        return None


class _RecordingConn:
    def __init__(self, *, existing_source_id: int | None = None) -> None:
        self.cur = _RecordingCursor(existing_source_id=existing_source_id)
        self.committed = False

    def cursor(self) -> _RecordingCursor:
        return self.cur

    def commit(self) -> None:
        self.committed = True

    def __enter__(self) -> _RecordingConn:
        return self

    def __exit__(self, *_args: object) -> None:
        return None


def _install_fake_pg(monkeypatch: Any, conn: _RecordingConn) -> None:
    @contextmanager
    def _connect(*_args: object, **_kwargs: object) -> Iterator[_RecordingConn]:
        yield conn

    fake_pg = MagicMock(connect=_connect)
    monkeypatch.setattr(direct, "psycopg", fake_pg)
    monkeypatch.setattr(mirror, "psycopg", fake_pg)
    monkeypatch.setattr(mirror, "Json", lambda obj: obj)


def test_preview_direct_rows_reports_typed_source_without_csv_read() -> None:
    summary = preview_direct_rows_load(
        _ROWS,
        source_key="equipment_read_model:chilecompra_api:20260702",
        manifest_path="manifest.json",
        date_suffix="20260702",
        campaign_mode="equipment_first",
    )

    assert summary["source_input"] == "typed_rows"
    assert summary["source_kind"] == "typed_read_model"
    assert summary["canonical_reason"] == "chilecompra_api_direct_rows"
    assert summary["would_insert_source"] is True
    assert summary["sample_rows"] == ["1051-1-LP26"]


def test_apply_direct_rows_inserts_source_and_opportunities(monkeypatch: Any) -> None:
    conn = _RecordingConn()
    _install_fake_pg(monkeypatch, conn)

    summary = apply_direct_rows_load(
        "postgresql://u:p@127.0.0.1/db",
        _ROWS,
        source_key="equipment_read_model:chilecompra_api:20260702",
        manifest_path="manifest.json",
        date_suffix="20260702",
        campaign_mode="equipment_first",
        updated_by="tester",
        reason="direct rows test",
        sync_run_id=10,
    )

    assert summary["applied"] is True
    assert summary["source_input"] == "typed_rows"
    assert summary["source_id"] == 77
    assert summary["rows_inserted"] == 1
    assert summary["is_canonical"] is True
    assert conn.committed is True
    sqls = [sql for sql, _ in conn.cur.statements]
    assert any("INSERT INTO commercial.equipment_opportunity_source" in sql for sql in sqls)
    assert any("INSERT INTO commercial.equipment_opportunity" in sql for sql in sqls)
    source_params = [
        params
        for sql, params in conn.cur.statements
        if "INSERT INTO commercial.equipment_opportunity_source" in sql
    ][0]
    assert source_params is not None
    assert source_params[-4] == direct.DIRECT_LOADER_VERSION
    assert source_params[-3] == "typed_read_model"
    assert source_params[-1] == "chilecompra_api_direct_rows"


def test_apply_direct_rows_replaces_existing_source(monkeypatch: Any) -> None:
    conn = _RecordingConn(existing_source_id=5)
    _install_fake_pg(monkeypatch, conn)

    summary = apply_direct_rows_load(
        "postgresql://u:p@127.0.0.1/db",
        _ROWS,
        source_key="equipment_read_model:chilecompra_api:20260702",
        updated_by="tester",
        reason="replace direct rows",
        replace_source=True,
    )

    assert summary["applied"] is True
    assert summary["replaced_source"] is True
    assert summary["source_id"] == 5
    sqls = [sql for sql, _ in conn.cur.statements]
    assert any("UPDATE commercial.equipment_opportunity_source" in sql for sql in sqls)
    assert any("DELETE FROM commercial.equipment_opportunity" in sql for sql in sqls)


def test_apply_direct_rows_duplicate_codigo_fails_closed(monkeypatch: Any) -> None:
    conn = _RecordingConn()
    _install_fake_pg(monkeypatch, conn)
    rows = [*_ROWS, dict(_ROWS[0])]

    summary = apply_direct_rows_load(
        "postgresql://u:p@127.0.0.1/db",
        rows,
        source_key="equipment_read_model:chilecompra_api:20260702",
        updated_by="tester",
        reason="dupe direct rows",
    )

    assert summary["applied"] is False
    assert summary["error"] == "duplicate_codigo_licitacion_in_rows"
    assert conn.cur.statements == []
