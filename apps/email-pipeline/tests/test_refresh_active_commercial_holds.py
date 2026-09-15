from __future__ import annotations

import importlib.util
import json
import sqlite3
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "campaigns" / "refresh_active_commercial_holds.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("refresh_active_commercial_holds", SCRIPT)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_normalize_postgres_url() -> None:
    mod = _load_module()
    assert (
        mod._normalize_postgres_url("postgresql+psycopg://user:pass@host/db")
        == "postgresql://user:pass@host/db"
    )
    assert mod._normalize_postgres_url("postgresql://user@host/db") == "postgresql://user@host/db"


def test_aggregate_rows_dedupes_email_and_tracks_unresolved() -> None:
    mod = _load_module()
    by_email, unresolved = mod._aggregate_rows(
        [
            ("Buyer@Lab.CL", "sales_opportunity", "so_1", "quoting"),
            ("buyer@lab.cl", "customer_quote", "q_1", "sent"),
            (None, "sales_opportunity", "so_2", "qualified"),
        ]
    )
    assert list(by_email) == ["buyer@lab.cl"]
    assert len(by_email["buyer@lab.cl"]) == 2
    assert unresolved == [
        {"source_kind": "sales_opportunity", "source_id": "so_2", "state": "qualified"}
    ]


def test_replace_sqlite_projection_is_atomic_shape(tmp_path: Path) -> None:
    mod = _load_module()
    db = tmp_path / "db.sqlite"
    sqlite3.connect(str(db)).close()

    mod._replace_sqlite_projection(
        db,
        by_email={
            "buyer@lab.cl": [
                {"source_kind": "sales_opportunity", "source_id": "so_1", "state": "quoting"},
                {"source_kind": "customer_quote", "source_id": "q_1", "state": "sent"},
            ]
        },
        refreshed_at="2026-09-15T21:00:00Z",
    )

    conn = sqlite3.connect(str(db))
    try:
        row = conn.execute(
            "SELECT email_norm, reasons_json, source_count, refreshed_at "
            "FROM outbound_commercial_hold"
        ).fetchone()
    finally:
        conn.close()

    assert row is not None
    assert row[0] == "buyer@lab.cl"
    assert len(json.loads(row[1])) == 2
    assert row[2] == 2
    assert row[3] == "2026-09-15T21:00:00Z"
