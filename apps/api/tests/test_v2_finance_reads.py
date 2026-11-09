"""Finanzas › Mes read: the numbers psycopg really returns (numeric → Decimal) must not break the view.

Production answered 500 on GET /v2/workspace/finance/mes because the weighted pipeline multiplied a
Decimal amount by a float weight. These tests feed `mes()` Decimal rows through a fake connection.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

from origenlab_api.v2.finance.reads import (
    _MES_SQL,
    _MONTHLY_HISTORY_SQL,
    _PIPELINE_BY_STAGE_SQL,
    _POR_COBRAR_AGING_SQL,
    FinanceReadRepository,
)

MES_ROW = {
    "mes": "2026-11-01", "today": "2026-11-09",
    "vendido_net": Decimal("1050000"), "meta_net": Decimal("6000000"),
    "por_cobrar_total": Decimal("2600000"), "por_cobrar_count": 3,
    "capital_atado_total": Decimal("0"), "capital_atado_count": 0,
    "gastos_total": Decimal("0"), "gastos_count": 0,
}


class FakeCursor:
    def __init__(self) -> None:
        self._rows: list[tuple[Any, ...]] = []
        self.description: list[Any] = []

    def execute(self, sql: str, params: Any = None) -> None:
        self.description, self._rows = [], []
        if sql == _MES_SQL:
            self.description = [SimpleNamespace(name=k) for k in MES_ROW]
            self._rows = [tuple(MES_ROW.values())]
        elif sql == _PIPELINE_BY_STAGE_SQL:
            self._rows = [("quoting", Decimal("4700000.00"), "a", "SB900"),
                          ("lead", Decimal("1000000"), "b", "Balanza")]
        elif sql == _POR_COBRAR_AGING_SQL:
            self._rows = [("0-30", Decimal("2200000")), ("31-60", Decimal("410000"))]
        elif sql == _MONTHLY_HISTORY_SQL:
            self._rows = [(date(2026, 10, 1).isoformat(), Decimal("5050000"))]

    def fetchone(self) -> tuple[Any, ...]:
        return self._rows[0]

    def fetchall(self) -> list[tuple[Any, ...]]:
        return self._rows

    def __enter__(self) -> FakeCursor:
        return self

    def __exit__(self, *exc: object) -> None:
        return None


class FakeConn:
    def cursor(self) -> FakeCursor:
        return FakeCursor()

    def rollback(self) -> None:
        return None

    def __enter__(self) -> FakeConn:
        return self

    def __exit__(self, *exc: object) -> None:
        return None


def test_mes_handles_decimal_amounts() -> None:
    out = FinanceReadRepository(lambda dsn, autocommit=False: FakeConn(), "dsn").mes()

    stages = out["pipeline"]["by_stage"]
    assert stages["quoting"]["total_clp"] == 4_700_000
    assert out["pipeline"]["ponderado_net"] == sum(s["weighted_clp"] for s in stages.values())
    assert all(isinstance(s["weighted_clp"], int) for s in stages.values())
    assert out["por_cobrar"]["by_age"] == {"0-30": 2_200_000, "31-60": 410_000, "61-90": 0, "90+": 0}
    assert out["vendido"]["pct_meta"] == 17.5
    assert out["monthly_history"] == [{"month": "2026-10-01", "neto": 5_050_000}]


def test_mes_amounts_leave_as_json_numbers() -> None:
    """The route returns `dict[str, Any]`, which Pydantic serializes with Decimal as a JSON string;
    «pct.toFixed is not a function» then blanked the whole dashboard."""
    from pydantic import TypeAdapter

    mes = FinanceReadRepository(lambda dsn, autocommit=False: FakeConn(), "dsn").mes()
    out = TypeAdapter(dict[str, Any]).dump_python(mes, mode="json")

    for path in (("vendido", "net"), ("vendido", "meta_net"), ("vendido", "pct_meta"), ("por_cobrar", "total"),
                 ("capital_atado", "total"), ("gastos", "total")):
        value = out[path[0]][path[1]]
        assert isinstance(value, (int, float)) and not isinstance(value, bool), (path, value)
