"""Exchange rates for the dashboard — ``GET /v2/workspace/fx``.

No network: every fetch below is a stub, and every figure is invented.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from origenlab_api.v2.crm_workspace import CrmWorkspaceRepository
from origenlab_api.v2.crm_workspace_routes import workspace_router
from origenlab_api.v2.fx_rates import FxRates, FxUnavailable, parse_mindicador
from origenlab_api.v2.identity import IdentityPort, IdentityRefused, OperatorIdentity


def _body(usd: Any = 950.5, eur: Any = 1050.25, uf: Any = 40000.0) -> dict[str, Any]:
    # mindicador dates each value at midnight Chile time, written in UTC.
    return {
        "autor": "mindicador.cl",
        "dolar": {"codigo": "dolar", "nombre": "Dólar observado", "fecha": "2026-01-15T03:00:00.000Z", "valor": usd},
        "euro": {"codigo": "euro", "nombre": "Euro", "fecha": "2026-01-15T03:00:00.000Z", "valor": eur},
        "uf": {"codigo": "uf", "nombre": "Unidad de fomento (UF)", "fecha": "2026-01-16T03:00:00.000Z", "valor": uf},
    }


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class _Fetch:
    def __init__(self, *answers: Any) -> None:
        self.answers = list(answers)
        self.calls = 0

    def __call__(self) -> Any:
        self.calls += 1
        answer = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        if isinstance(answer, Exception):
            raise answer
        return answer


# ─────────────────────────────────────────────────────────────── parsing ──


def test_reads_dollar_euro_and_uf_in_pesos_dated_in_chile() -> None:
    out = parse_mindicador(_body())
    assert out == [
        {"code": "USD", "label": "Dólar observado", "clp": 950.5, "as_of": "2026-01-15"},
        {"code": "EUR", "label": "Euro", "clp": 1050.25, "as_of": "2026-01-15"},
        {"code": "UF", "label": "UF", "clp": 40000.0, "as_of": "2026-01-16"},
    ]


def test_skips_a_value_that_is_not_a_positive_number() -> None:
    out = parse_mindicador(_body(eur="1050", uf=0))
    assert [r["code"] for r in out] == ["USD"]


def test_refuses_an_answer_with_no_usable_value() -> None:
    with pytest.raises(FxUnavailable):
        parse_mindicador(_body(usd=None, eur=-1, uf=True))
    with pytest.raises(FxUnavailable):
        parse_mindicador(["not", "an", "object"])


# ──────────────────────────────────────────────────────────────── cache ──


def test_fetches_once_within_the_hour() -> None:
    fetch, clock = _Fetch(_body()), _Clock()
    fx = FxRates(fetch=fetch, clock=clock)
    first = fx.current()
    clock.now += 3599
    assert fx.current() == first
    assert fetch.calls == 1
    assert first["stale"] is False
    assert first["source_label"] == "Banco Central de Chile, vía mindicador.cl"


def test_fetches_again_after_the_hour() -> None:
    fetch, clock = _Fetch(_body(usd=950.5), _body(usd=960.0)), _Clock()
    fx = FxRates(fetch=fetch, clock=clock)
    fx.current()
    clock.now += 3601
    assert fx.current()["rates"][0]["clp"] == 960.0
    assert fetch.calls == 2


def test_a_failed_refresh_keeps_the_last_figures_marked_stale() -> None:
    fetch, clock = _Fetch(_body(usd=950.5), OSError("down")), _Clock()
    fx = FxRates(fetch=fetch, clock=clock)
    fx.current()
    clock.now += 3601
    out = fx.current()
    assert out["rates"][0]["clp"] == 950.5
    assert out["stale"] is True


def test_without_any_figure_a_failure_is_unavailable_and_not_retried_at_once() -> None:
    fetch, clock = _Fetch(OSError("down")), _Clock()
    fx = FxRates(fetch=fetch, clock=clock)
    with pytest.raises(FxUnavailable):
        fx.current()
    clock.now += 30
    with pytest.raises(FxUnavailable):
        fx.current()
    assert fetch.calls == 1
    clock.now += 31
    with pytest.raises(FxUnavailable):
        fx.current()
    assert fetch.calls == 2


# ──────────────────────────────────────────────────────────────── route ──


class _Port(IdentityPort):
    def __init__(self, operator: OperatorIdentity | None) -> None:
        self._operator = operator

    def resolve(self, headers: dict[str, str]) -> OperatorIdentity:
        if self._operator is None:
            raise IdentityRefused("no identity")
        return self._operator


def _app(operator: OperatorIdentity | None, fx: FxRates) -> TestClient:
    app = FastAPI()
    app.state.v2_identity = _Port(operator)
    app.state.crm_workspace = CrmWorkspaceRepository(connect=None, dsn="unused")
    app.state.fx_rates = fx
    app.include_router(workspace_router)
    return TestClient(app)


VIEWER = OperatorIdentity(
    operator_id="00000000-0000-4000-8000-000000000001",
    email_norm="operator@example.invalid",
    display_name="Operator",
    role="viewer",
    status="active",
)


def test_route_needs_a_signed_in_operator() -> None:
    fetch = _Fetch(_body())
    client = _app(None, FxRates(fetch=fetch, clock=_Clock()))
    assert client.get("/v2/workspace/fx").status_code == 401
    assert fetch.calls == 0


def test_route_answers_the_rates_to_any_operator() -> None:
    client = _app(VIEWER, FxRates(fetch=_Fetch(_body()), clock=_Clock()))
    res = client.get("/v2/workspace/fx")
    assert res.status_code == 200
    assert [r["code"] for r in res.json()["rates"]] == ["USD", "EUR", "UF"]


def test_route_says_503_when_no_figure_is_available() -> None:
    client = _app(VIEWER, FxRates(fetch=_Fetch(OSError("down")), clock=_Clock()))
    res = client.get("/v2/workspace/fx")
    assert res.status_code == 503
