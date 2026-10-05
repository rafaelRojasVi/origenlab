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


def _body(usd: Any = 950.5, eur: Any = 1049.75, uf: Any = 40000.0) -> dict[str, Any]:
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
        {"code": "EUR", "label": "Euro", "clp": 1049.75, "as_of": "2026-01-15"},
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


# ────────────────────────────────────────────────────────── second source ──


def _findic_body(usd: Any = 984.82) -> dict[str, Any]:
    # findic.cl answers in mindicador's shape, but dates each value with the plain Chile day.
    return {
        "autor": "findic.cl",
        "dolar": {"codigo": "dolar", "nombre": "Dólar observado", "fecha": "2026-10-05", "valor": usd},
        "euro": {"codigo": "euro", "nombre": "Euro", "fecha": "2026-10-05", "valor": 1108.41},
        "uf": {"codigo": "uf", "nombre": "Unidad de fomento (UF)", "fecha": "2026-10-05", "valor": 41098.15},
    }


def test_reads_a_plain_chile_day_as_the_date() -> None:
    out = parse_mindicador(_findic_body())
    assert [r["as_of"] for r in out] == ["2026-10-05", "2026-10-05", "2026-10-05"]


def test_refuses_a_plain_date_that_is_not_a_day() -> None:
    body = _findic_body()
    body["dolar"]["fecha"] = "2026-13-45"
    assert [r["code"] for r in parse_mindicador(body)] == ["EUR", "UF"]


def test_a_failed_first_source_falls_back_to_the_second() -> None:
    first, second = _Fetch(OSError("connection reset")), _Fetch(_findic_body())
    fx = FxRates(sources=(("mindicador.cl", first), ("findic.cl", second)), clock=_Clock())
    out = fx.current()
    assert out["stale"] is False
    assert out["source"] == "findic.cl"
    assert out["source_label"] == "Banco Central de Chile, vía findic.cl"
    assert out["source_url"] == "https://findic.cl"
    assert out["rates"][0]["clp"] == 984.82
    assert (first.calls, second.calls) == (1, 1)


def test_a_working_first_source_never_asks_the_second() -> None:
    first, second = _Fetch(_body()), _Fetch(_findic_body())
    fx = FxRates(sources=(("mindicador.cl", first), ("findic.cl", second)), clock=_Clock())
    assert fx.current()["source"] == "mindicador.cl"
    assert second.calls == 0


def test_an_unusable_answer_from_the_first_source_also_falls_back() -> None:
    first, second = _Fetch({"autor": "mindicador.cl"}), _Fetch(_findic_body())
    fx = FxRates(sources=(("mindicador.cl", first), ("findic.cl", second)), clock=_Clock())
    assert fx.current()["source"] == "findic.cl"


def test_every_source_failing_is_unavailable_and_each_failure_is_logged(caplog: pytest.LogCaptureFixture) -> None:
    first, second = _Fetch(OSError("connection reset")), _Fetch(TimeoutError("timed out"))
    fx = FxRates(sources=(("mindicador.cl", first), ("findic.cl", second)), clock=_Clock())
    with caplog.at_level("WARNING", logger="origenlab_api.v2.fx_rates"):
        with pytest.raises(FxUnavailable):
            fx.current()
    messages = [r.getMessage() for r in caplog.records]
    assert any("mindicador.cl" in m and "OSError" in m for m in messages)
    assert any("findic.cl" in m and "TimeoutError" in m for m in messages)


def test_the_default_sources_are_mindicador_then_findic() -> None:
    assert [name for name, _ in FxRates()._sources] == ["mindicador.cl", "findic.cl"]


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


# ───────────────────────────────────────── stored Banco Central figures (Task 16) ──
#
# A fresh API process (any deploy) has no figures in memory. When every source fails it answers
# the newest stored `catalog.fx_rate` observation instead of «no disponible»; every successful
# mindicador.cl fetch writes its USD and EUR through to that table. Readers and writers are fakes
# here; tests/test_v2_fx_rates_db.py proves the real SQL.

import datetime as dt  # noqa: E402
from decimal import Decimal  # noqa: E402

#: 2026-10-05 15:00 UTC — noon in Santiago.
_WALL = dt.datetime(2026, 10, 5, 15, 0, tzinfo=dt.timezone.utc).timestamp()


def _row(currency: str, day: str, clp: str, created: str = "2026-10-03T12:30:00+00:00") -> dict[str, Any]:
    return {"currency": currency, "rate_date": dt.date.fromisoformat(day), "clp_per_unit": Decimal(clp),
            "source": "bcentral", "provider": "mindicador", "created_at": dt.datetime.fromisoformat(created)}


class _Store:
    """A stored-rate reader: `(currency, on) -> row | None`, recording what it was asked."""

    def __init__(self, *rows: dict[str, Any], error: Exception | None = None) -> None:
        self.rows = {r["currency"]: r for r in rows}
        self.error = error
        self.asked: list[tuple[str, dt.date]] = []

    def __call__(self, currency: str, on: dt.date) -> dict[str, Any] | None:
        self.asked.append((currency, on))
        if self.error is not None:
            raise self.error
        return self.rows.get(currency)


class _Writer:
    def __init__(self, error: Exception | None = None) -> None:
        self.calls: list[list[dict[str, Any]]] = []
        self.error = error

    def __call__(self, rows: list[dict[str, Any]]) -> None:
        self.calls.append(rows)
        if self.error is not None:
            raise self.error


def _down() -> _Fetch:
    return _Fetch(OSError("connection refused"))


def test_a_fresh_process_with_every_source_down_answers_the_stored_figures() -> None:
    store = _Store(_row("USD", "2026-10-02", "952.500000", "2026-10-02T13:00:00+00:00"),
                   _row("EUR", "2026-10-03", "1101.250000", "2026-10-03T13:30:00+00:00"))
    fx = FxRates(sources=(("mindicador.cl", _down()), ("findic.cl", _down())), clock=_Clock(),
                 wall_clock=lambda: _WALL, store_reader=store)
    assert fx.current() == {
        "source": "catalog.fx_rate",
        "source_label": "Banco Central de Chile (último valor guardado)",
        "source_url": "https://si3.bcentral.cl",
        "rates": [
            {"code": "USD", "label": "Dólar observado", "clp": 952.5, "as_of": "2026-10-02"},
            {"code": "EUR", "label": "Euro", "clp": 1101.25, "as_of": "2026-10-03"},
        ],
        "fetched_at": "2026-10-03T13:30:00+00:00",
        "stale": True,
    }
    # Asked for each stored currency as of today in Chile, never a later day.
    assert store.asked == [("USD", dt.date(2026, 10, 5)), ("EUR", dt.date(2026, 10, 5))]


def test_one_stored_currency_is_enough() -> None:
    fx = FxRates(fetch=_down(), clock=_Clock(), wall_clock=lambda: _WALL,
                 store_reader=_Store(_row("EUR", "2026-10-03", "1101.25")))
    out = fx.current()
    assert [r["code"] for r in out["rates"]] == ["EUR"]
    assert out["stale"] is True


def test_a_fresh_process_with_an_empty_store_is_still_unavailable() -> None:
    fx = FxRates(fetch=_down(), clock=_Clock(), wall_clock=lambda: _WALL, store_reader=_Store())
    with pytest.raises(FxUnavailable):
        fx.current()


def test_a_stored_row_dated_after_today_is_never_shown() -> None:
    store = _Store(_row("USD", "2026-10-06", "999"), _row("EUR", "2026-10-03", "1101.25"))
    fx = FxRates(fetch=_down(), clock=_Clock(), wall_clock=lambda: _WALL, store_reader=store)
    assert [r["code"] for r in fx.current()["rates"]] == ["EUR"]


def test_figures_in_memory_win_over_the_store() -> None:
    store, clock = _Store(_row("USD", "2026-10-02", "1")), _Clock()
    fx = FxRates(fetch=_Fetch(_body(usd=950.5), OSError("down")), clock=clock, store_reader=store)
    fx.current()
    clock.now += 3601
    out = fx.current()
    assert out["stale"] is True and out["rates"][0]["clp"] == 950.5
    assert store.asked == []


def test_a_failing_store_read_is_logged_and_unavailable(caplog: pytest.LogCaptureFixture) -> None:
    store = _Store(error=RuntimeError("could not connect to server"))
    fx = FxRates(fetch=_down(), clock=_Clock(), wall_clock=lambda: _WALL, store_reader=store)
    with caplog.at_level("WARNING", logger="origenlab_api.v2.fx_rates"):
        with pytest.raises(FxUnavailable):
            fx.current()
    assert any("stored" in r.getMessage() and "RuntimeError" in r.getMessage() for r in caplog.records)


def test_a_mindicador_fetch_writes_usd_and_eur_through_once() -> None:
    writer, clock = _Writer(), _Clock()
    fx = FxRates(fetch=_Fetch(_body()), clock=clock, store_writer=writer)
    fx.current()
    clock.now += 3599
    fx.wait_for_write_through(5)
    fx.current()  # served from memory: no second write
    fx.wait_for_write_through(5)
    assert writer.calls == [[
        {"rate_date": dt.date(2026, 1, 15), "currency": "USD", "clp_per_unit": Decimal("950.5"),
         "provider": "mindicador"},
        {"rate_date": dt.date(2026, 1, 15), "currency": "EUR", "clp_per_unit": Decimal("1049.75"),
         "provider": "mindicador"},
    ]]


def test_a_findic_fetch_is_never_written_through() -> None:
    # catalog.fx_rate names only reviewed providers (bde, mindicador, operator); findic.cl's
    # figures are shown, never stored under another provider's name.
    writer = _Writer()
    fx = FxRates(sources=(("mindicador.cl", _down()), ("findic.cl", _Fetch(_findic_body()))),
                 clock=_Clock(), store_writer=writer)
    assert fx.current()["source"] == "findic.cl"
    assert writer.calls == []


def test_a_failing_write_through_changes_nothing_and_is_logged(caplog: pytest.LogCaptureFixture) -> None:
    plain = FxRates(fetch=_Fetch(_body()), clock=_Clock(), wall_clock=lambda: _WALL).current()
    fx = FxRates(fetch=_Fetch(_body()), clock=_Clock(), wall_clock=lambda: _WALL,
                 store_writer=_Writer(error=RuntimeError("deadlock detected")))
    with caplog.at_level("WARNING", logger="origenlab_api.v2.fx_rates"):
        assert fx.current() == plain
        assert fx.wait_for_write_through(5)
    warnings = [r for r in caplog.records if r.levelname == "WARNING"]
    assert len(warnings) == 1 and "RuntimeError" in warnings[0].getMessage()


def test_a_failed_source_is_logged_once_per_retry_window(caplog: pytest.LogCaptureFixture) -> None:
    clock = _Clock()
    fx = FxRates(fetch=_Fetch(ConnectionResetError("reset by peer")), clock=clock, wall_clock=lambda: _WALL,
                 store_reader=_Store(_row("USD", "2026-10-02", "952.5")))
    with caplog.at_level("WARNING", logger="origenlab_api.v2.fx_rates"):
        fx.current()
        clock.now += 30
        fx.current()
    source_logs = [r.getMessage() for r in caplog.records if "fx source" in r.getMessage()]
    assert len(source_logs) == 1 and "ConnectionResetError" in source_logs[0]


def test_route_answers_the_stored_figures_with_the_usual_shape() -> None:
    fx = FxRates(fetch=_down(), clock=_Clock(), wall_clock=lambda: _WALL,
                 store_reader=_Store(_row("USD", "2026-10-02", "952.5")))
    res = _app(VIEWER, fx).get("/v2/workspace/fx")
    assert res.status_code == 200
    body = res.json()
    assert set(body) == {"source", "source_label", "source_url", "rates", "fetched_at", "stale"}
    assert body["stale"] is True and body["rates"][0] == {
        "code": "USD", "label": "Dólar observado", "clp": 952.5, "as_of": "2026-10-02"}


def test_route_says_503_not_500_when_the_store_read_fails() -> None:
    fx = FxRates(fetch=_down(), clock=_Clock(), wall_clock=lambda: _WALL,
                 store_reader=_Store(error=RuntimeError("pool timeout")))
    assert _app(VIEWER, fx).get("/v2/workspace/fx").status_code == 503


# ──────────────────────────────────────────────────── fix round 1 (review) ──

import threading  # noqa: E402


def test_a_stored_read_is_made_once_per_retry_window(caplog: pytest.LogCaptureFixture) -> None:
    clock = _Clock()
    store = _Store(error=RuntimeError('relation "catalog.fx_rate" does not exist'))
    fx = FxRates(fetch=_down(), clock=clock, wall_clock=lambda: _WALL, store_reader=store)
    with caplog.at_level("WARNING", logger="origenlab_api.v2.fx_rates"):
        for _ in range(3):
            with pytest.raises(FxUnavailable):
                fx.current()
            clock.now += 10
    assert len(store.asked) == 1  # one currency asked, then the failure was remembered
    stored_logs = [r for r in caplog.records if "stored-rate read" in r.getMessage()]
    assert len(stored_logs) == 1


def test_a_stored_answer_is_reused_inside_the_window_and_read_again_after_it() -> None:
    clock = _Clock()
    store = _Store(_row("USD", "2026-10-02", "952.5"))
    fx = FxRates(fetch=_down(), clock=clock, wall_clock=lambda: _WALL, store_reader=store)
    first = fx.current()
    clock.now += 10
    assert fx.current() == first
    clock.now += 10
    assert fx.current() == first
    assert len(store.asked) == 2  # USD and EUR, once
    clock.now += 61
    fx.current()
    assert len(store.asked) == 4


class _BlockingWriter:
    def __init__(self) -> None:
        self.release = threading.Event()
        self.entered = threading.Event()
        self.calls = 0

    def __call__(self, rows: list[dict[str, Any]]) -> None:
        self.calls += 1
        self.entered.set()
        self.release.wait(10)


def test_the_answer_never_waits_on_the_write_through() -> None:
    writer = _BlockingWriter()
    fx = FxRates(fetch=_Fetch(_body()), clock=_Clock(), store_writer=writer)
    out = fx.current()  # returns while the writer is still blocked
    assert out["stale"] is False and out["source"] == "mindicador.cl"
    assert writer.entered.wait(5)
    assert not writer.release.is_set()
    writer.release.set()
    assert fx.wait_for_write_through(5)
    assert writer.calls == 1


def test_rows_that_cannot_be_stored_never_fail_the_answer(caplog: pytest.LogCaptureFixture) -> None:
    import origenlab_api.v2.fx_rates as module

    writer = _Writer()

    def boom(source: str, rates: list[dict[str, Any]]) -> Any:
        raise ValueError("bad figure")

    fx = FxRates(fetch=_Fetch(_body()), clock=_Clock(), store_writer=writer)
    original, module._rows_to_store = module._rows_to_store, boom
    try:
        with caplog.at_level("WARNING", logger="origenlab_api.v2.fx_rates"):
            assert fx.current()["stale"] is False
            assert fx.wait_for_write_through(5)
    finally:
        module._rows_to_store = original
    assert writer.calls == []
    assert any("write-through" in r.getMessage() and "ValueError" in r.getMessage() for r in caplog.records)
