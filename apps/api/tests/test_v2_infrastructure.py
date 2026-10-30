"""«Costos e infraestructura» — pure unit tests and a DB test.

All values below are invented; the repository is public.
"""

from __future__ import annotations

import json
import threading
import time
from typing import Any
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from origenlab_api.v2.crm_workspace_routes import workspace_router
from origenlab_api.v2.identity import IdentityPort, IdentityRefused, OperatorIdentity
from origenlab_api.v2.infrastructure import (
    PLANS,
    RenderCache,
    RenderClient,
    _sum_metric_values,
    compute_monthly_cost,
    fetch_render,
    read_supabase,
)

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

VIEWER = OperatorIdentity(
    operator_id="00000000-0000-4000-8000-000000000001",
    email_norm="op@ejemplo.invalid",
    display_name="Operadora",
    role="viewer",
    status="active",
)


class _Port(IdentityPort):
    def __init__(self, operator: OperatorIdentity | None) -> None:
        self._operator = operator

    def resolve(self, headers: dict[str, str]) -> OperatorIdentity:
        if self._operator is None:
            raise IdentityRefused("no identity")
        return self._operator


class _Repo:
    def __init__(self, body: dict[str, Any]) -> None:
        self._body = body

    def infrastructure(self, render_api_key: str | None = None) -> dict[str, Any]:
        return self._body


_SAMPLE_BODY: dict[str, Any] = {
    "supabase": {
        "database_bytes": 1_200_000_000,
        "largest_tables": [{"table": "crm.opportunity", "bytes": 400_000_000}],
        "mail_bytes": 500_000_000,
        "mail_count": 5000,
        "mail_bytes_this_month": 20_000_000,
        "plan": "Pro",
        "plan_monthly_usd": 25.0,
        "database_gb_included": 8.0,
        "file_storage_gb_included": 100.0,
    },
    "render": {
        "available": True,
        "services": [
            {"id": "svc-1", "name": "origenlab-api", "type": "web",
             "plan": "starter", "suspended": False, "price_usd": 7.0,
             "bandwidth_bytes_this_month": 4_200_000_000},
        ],
        "workspace_plan": "Hobby",
        "workspace_monthly_usd": 0.0,
        "bandwidth_bytes_this_month": 4_200_000_000,
        "bandwidth_gb_included": 5.0,
        "bandwidth_overage_usd_per_100_gb": 15.0,
    },
    "monthly_cost_usd": 39.0,
    "plans_as_of": "2026-10-30",
}


def _client(operator: OperatorIdentity | None, body: dict[str, Any] = _SAMPLE_BODY) -> TestClient:
    app = FastAPI()
    app.state.v2_identity = _Port(operator)
    app.state.crm_workspace = _Repo(body)
    app.state.render_api_key = None  # no real key in tests
    app.include_router(workspace_router)
    return TestClient(app)


# ---------------------------------------------------------------------------
# Plan table
# ---------------------------------------------------------------------------

def test_plan_table_has_known_fields() -> None:
    assert PLANS["render"]["workspace"]["outbound_bandwidth_gb_included"] == 5.0
    assert PLANS["render"]["workspace"]["outbound_overage_usd_per_100_gb"] == 15.0
    assert PLANS["supabase"]["monthly_usd"] == 25.0
    assert PLANS["supabase"]["database_gb_included"] == 8.0
    assert PLANS["_as_of"] == "2026-10-30"


def test_plan_render_starter_prices() -> None:
    from origenlab_api.v2.infrastructure import _plan_price_usd

    assert _plan_price_usd("web_service", "starter") == 7.0
    assert _plan_price_usd("background_worker", "starter") == 7.0
    assert _plan_price_usd("static_site", "") == 0.0
    assert _plan_price_usd("cron_job", "") is None
    assert _plan_price_usd("web_service", "pro") is None  # unknown plan → None


# ---------------------------------------------------------------------------
# Render metric value parser
# ---------------------------------------------------------------------------

def test_sum_metric_values_flat_list() -> None:
    data = [{"timestamp": "2026-10-01T00:00:00Z", "value": 1_000_000},
            {"timestamp": "2026-10-02T00:00:00Z", "value": 2_000_000}]
    assert _sum_metric_values(data) == 3_000_000


def test_sum_metric_values_nested_data_key() -> None:
    data = {"data": [{"value": 1_000}, {"value": 2_000}]}
    assert _sum_metric_values(data) == 3_000


def test_sum_metric_values_empty() -> None:
    assert _sum_metric_values({}) == 0
    assert _sum_metric_values([]) == 0
    assert _sum_metric_values(None) == 0


def test_sum_metric_values_non_numeric_value_skipped() -> None:
    data = [{"value": "n/a"}, {"value": 500}]
    assert _sum_metric_values(data) == 500


# ---------------------------------------------------------------------------
# Render client — fixture-based
# ---------------------------------------------------------------------------

def _make_client(list_resp: Any, bw_resp: Any = None, raises: Exception | None = None) -> RenderClient:
    """Build a RenderClient whose HTTP calls return fixed payloads."""

    class _FakeClient(RenderClient):
        def _get(self, path: str) -> Any:
            if raises is not None:
                raise raises
            if "/metrics/bandwidth" in path:
                return bw_resp or {"data": []}
            return list_resp

    return _FakeClient(api_key="test-key-ficticio")


def test_render_client_lists_services() -> None:
    raw = [
        {"service": {"id": "svc-1", "name": "origenlab-api", "type": "web_service",
                      "suspended": "not_suspended", "serviceDetails": {"plan": "starter"}}},
    ]
    client = _make_client(raw, bw_resp={"data": [{"value": 1_000_000}]})
    cache = RenderCache()
    result = fetch_render(client, cache)
    assert result["available"] is True
    assert len(result["services"]) == 1
    svc = result["services"][0]
    assert svc["name"] == "origenlab-api"
    assert svc["type"] == "web"
    assert svc["price_usd"] == 7.0
    assert svc["bandwidth_bytes_this_month"] == 1_000_000


def test_render_client_suspended_service_has_zero_bandwidth() -> None:
    raw = [
        {"service": {"id": "svc-2", "name": "old-worker", "type": "background_worker",
                      "suspended": "suspended", "serviceDetails": {"plan": "starter"}}},
    ]
    client = _make_client(raw)
    cache = RenderCache()
    result = fetch_render(client, cache)
    svc = result["services"][0]
    assert svc["suspended"] is True
    assert svc["bandwidth_bytes_this_month"] == 0


def test_render_client_network_error_returns_unavailable() -> None:
    client = _make_client(None, raises=OSError("conexión rechazada"))
    cache = RenderCache()
    result = fetch_render(client, cache)
    assert result["available"] is False
    assert "reason" in result


def test_render_bandwidth_error_returns_zero_not_five_hundred() -> None:
    """A bandwidth sub-call failure yields 0 bytes, not a 500 response."""

    class _PartialClient(RenderClient):
        def _get(self, path: str) -> Any:
            if "/metrics/bandwidth" in path:
                raise OSError("timeout")
            return [{"service": {"id": "svc-1", "name": "origenlab-api",
                                  "type": "web_service", "suspended": "not_suspended",
                                  "serviceDetails": {"plan": "starter"}}}]

    cache = RenderCache()
    result = fetch_render(_PartialClient(api_key="test-key-ficticio"), cache)
    assert result["available"] is True
    assert result["services"][0]["bandwidth_bytes_this_month"] == 0


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------

def test_render_cache_returns_same_value_within_ttl() -> None:
    calls = 0

    def loader() -> str:
        nonlocal calls
        calls += 1
        return f"call-{calls}"

    cache = RenderCache(ttl=60)
    v1 = cache.get(loader)
    v2 = cache.get(loader)
    assert v1 == v2 == "call-1"
    assert calls == 1


def test_render_cache_refreshes_after_ttl(monkeypatch: pytest.MonkeyPatch) -> None:
    _t = [0.0]
    monkeypatch.setattr("origenlab_api.v2.infrastructure.time.monotonic", lambda: _t[0])

    calls = 0

    def loader() -> str:
        nonlocal calls
        calls += 1
        return f"call-{calls}"

    cache = RenderCache(ttl=10, clock=lambda: _t[0])
    cache.get(loader)
    _t[0] = 11.0
    cache.get(loader)
    assert calls == 2


def test_render_cache_is_thread_safe() -> None:
    results: list[str] = []
    lock = threading.Lock()
    calls = 0

    def loader() -> str:
        nonlocal calls
        calls += 1
        time.sleep(0.01)
        return f"call-{calls}"

    cache = RenderCache(ttl=60)

    def worker() -> None:
        v = cache.get(loader)
        with lock:
            results.append(v)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    # All should have gotten the same value (cache shared).
    assert len(set(results)) == 1
    assert calls == 1


# ---------------------------------------------------------------------------
# Monthly cost computation
# ---------------------------------------------------------------------------

def test_compute_monthly_cost_supabase_only() -> None:
    render = {"available": False}
    supabase = {"plan_monthly_usd": 25.0}
    assert compute_monthly_cost(render, supabase) == 25.0


def test_compute_monthly_cost_adds_non_suspended_services() -> None:
    render = {
        "available": True,
        "services": [
            {"suspended": False, "price_usd": 7.0},
            {"suspended": True, "price_usd": 7.0},   # suspended → not counted
            {"suspended": False, "price_usd": None},  # unknown → not counted
        ],
    }
    supabase = {"plan_monthly_usd": 25.0}
    assert compute_monthly_cost(render, supabase) == 32.0


# ---------------------------------------------------------------------------
# Route: any signed-in operator reads it, nobody else
# ---------------------------------------------------------------------------

def test_any_signed_in_operator_reads_infrastructure() -> None:
    assert _client(None).get("/v2/workspace/operations/infrastructure").status_code == 401
    res = _client(VIEWER).get("/v2/workspace/operations/infrastructure")
    assert res.status_code == 200
    body = res.json()
    assert "supabase" in body
    assert "render" in body
    assert "monthly_cost_usd" in body


def test_infrastructure_post_is_not_allowed() -> None:
    assert _client(VIEWER).post("/v2/workspace/operations/infrastructure").status_code == 405


def test_infrastructure_render_unavailable_note_when_no_key() -> None:
    body = {
        "supabase": {"database_bytes": 100, "largest_tables": [], "mail_bytes": 0,
                     "mail_count": 0, "mail_bytes_this_month": 0, "plan": "Pro",
                     "plan_monthly_usd": 25.0, "database_gb_included": 8.0,
                     "file_storage_gb_included": 100.0},
        "render": {"available": False, "reason": "Falta la clave de la API de Render en el servicio"},
        "monthly_cost_usd": 25.0,
        "plans_as_of": "2026-10-30",
    }
    res = _client(VIEWER, body).get("/v2/workspace/operations/infrastructure")
    assert res.status_code == 200
    rendered = res.json()["render"]
    assert rendered["available"] is False
    assert "Render" in rendered["reason"] or "Falta" in rendered["reason"]


# ---------------------------------------------------------------------------
# DB test: read_supabase works as the runtime role
# ---------------------------------------------------------------------------

try:
    from v2_command_harness import build_disposable_database, needs_db, runtime_dsn
except ImportError:
    needs_db = pytest.mark.skip(reason="v2_command_harness not available")
    build_disposable_database = None  # type: ignore[assignment]
    runtime_dsn = None  # type: ignore[assignment]


@pytest.fixture(scope="module")
def disposable_database():  # type: ignore[return]
    if build_disposable_database is None:
        pytest.skip("v2_command_harness not available")
    yield from build_disposable_database()


@needs_db
def test_supabase_sql_queries_work_as_runtime_role(disposable_database: str) -> None:
    """Verify read_supabase can execute against the real schema as origenlab_api."""
    import psycopg

    from origenlab_api.v2.crm_workspace import CrmWorkspaceRepository

    repo = CrmWorkspaceRepository(psycopg.connect, runtime_dsn(disposable_database))
    with repo._read() as cur:
        result = read_supabase(cur)

    # database_bytes: must be a positive integer
    assert isinstance(result["database_bytes"], int)
    assert result["database_bytes"] > 0
    # largest_tables: must be a list (empty is fine in a fresh disposable database)
    assert isinstance(result["largest_tables"], list)
    for row in result["largest_tables"]:
        assert "table" in row
        assert isinstance(row["bytes"], int)
    # mail_bytes: 0 in a fresh database, never None
    assert isinstance(result["mail_bytes"], int)
    assert isinstance(result["mail_count"], int)
    assert isinstance(result["mail_bytes_this_month"], int)
    # plan info
    assert result["plan"] == "Pro"
    assert result["database_gb_included"] == 8.0


def test_errors_shown_to_operators_carry_the_kind_never_the_raw_text() -> None:
    """Any signed-in operator reads this page: an error's own text (a URL, a host, a database
    detail) is never passed on, only its kind."""
    secret = "texto-interno-que-no-debe-salir"
    result = fetch_render(_make_client(None, raises=OSError(secret)), RenderCache())
    assert secret not in result["reason"] and "OSError" in result["reason"]

    class _BrokenCursor:
        def execute(self, *a, **k):
            raise RuntimeError(secret)

    sup = read_supabase(_BrokenCursor())
    assert secret not in repr(sup)
    assert sup["database_bytes"] is None and sup["database_bytes_error"] == "RuntimeError"
