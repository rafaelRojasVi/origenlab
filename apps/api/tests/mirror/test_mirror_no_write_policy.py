"""Mirror routes are GET-only read-only Postgres mirror reads."""

from __future__ import annotations

from pathlib import Path

import pytest

from origenlab_api.main import create_app

_MIRROR_SRC = Path(__file__).resolve().parents[2] / "src" / "origenlab_api" / "mirror"


def test_mirror_routes_are_get_only() -> None:
    app = create_app()
    unsafe: list[str] = []
    for route in app.routes:
        path = getattr(route, "path", "") or ""
        if not path.startswith("/mirror"):
            continue
        methods = getattr(route, "methods", None)
        if not methods:
            continue
        mutating = methods - {"GET", "HEAD", "OPTIONS"}
        if mutating:
            unsafe.append(f"{path} {sorted(mutating)}")
    assert unsafe == [], f"mutating mirror routes: {unsafe}"


def test_mirror_source_has_no_mutation_script_references() -> None:
    forbidden = (
        "sync_dashboard_postgres_mirror",
        "gmail_send",
        "alembic upgrade",
        "subprocess.run",
    )
    hits: list[str] = []
    for path in _MIRROR_SRC.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for needle in forbidden:
            if needle in text:
                hits.append(f"{path.relative_to(_MIRROR_SRC)}: {needle}")
    assert hits == []


def test_operator_health_route_unchanged_contract() -> None:
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    client = TestClient(create_app())
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert set(body.keys()) == {
        "ok",
        "service",
        "mode",
        "backend",
        "postgres_configured",
        "sqlite_immutable",
        "sqlite_query_only",
        "recovery_mode",
    }
    assert body["mode"] == "operator-sqlite-readonly"
    assert body["sqlite_immutable"] is False
    assert body["recovery_mode"] is False
