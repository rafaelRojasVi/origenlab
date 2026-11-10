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
    _EXPENSES_SQL,
    _INDICADORES_SQL,
    _MES_SQL,
    _MONTHLY_HISTORY_SQL,
    _OPEN_INVOICES_SQL,
    _PEDIDOS_ESTIMADOS_SQL,
    _PIPELINE_BY_STAGE_SQL,
    _POR_COBRAR_AGING_SQL,
    _RESUMEN_MES_SQL,
    _SUPPLIER_ORDERS_SQL,
    FinanceReadRepository,
)

MES_ROW = {
    "mes": "2026-11-01", "today": "2026-11-09",
    "vendido_net": Decimal("1050000"), "meta_net": Decimal("6000000"),
    "por_cobrar_total": Decimal("2600000"), "por_cobrar_count": 3,
    "capital_atado_total": Decimal("0"), "capital_atado_count": 0,
    "gastos_total": Decimal("0"), "gastos_count": 0,
}

RESUMEN_MES_ROW = {
    "mes": "2026-11-01",
    "today": "2026-11-09",
    "month_end": "2026-12-01",
    "vendido_net": Decimal("1050000"),
    "meta_net": Decimal("6000000"),
    "ganado_sf_net": Decimal("800000"),
    "ganado_sf_count": 1,
    "neg_net": Decimal("2200000"),
    "neg_count": 3,
}

INDICADORES_ROW = {
    "dpc_avg": Decimal("28.5"),
    "dpc_count": 4,
    "margen_costo": Decimal("600000"),
    "margen_subtotal": Decimal("1050000"),
    "clientes_lentos": [{"org": "Inst. Ficticio", "days": 45, "n": 2}],
}

OPEN_INVOICE_ROW = {
    "invoice_id": "aaa00000-0000-0000-0000-000000000001",
    "folio": 1001,
    "issued_on": "2026-10-10",
    "client_rut": "12.345.678-5",
    "client_name": "Inst. Ficticio",
    "total_amount": Decimal("1190000"),
    "balance_iva": Decimal("1190000"),
    "avg_days_client": 30,
    "expected_pay_date": "2026-11-09",
    "opportunity_id": None,
    "opportunity_title": None,
    "today": "2026-11-09",
}


FAKE_EXPENSE_ROW = {
    "expense_id": "bbb00000-0000-0000-0000-000000000001",
    "month": "2026-11-01",
    "category": "operaciones",
    "amount_clp": 150000,
    "paid_by": "sebastian",
    "notes": None,
    "source": "manual",
    "external_ref": None,
    "created_at": "2026-11-05T10:00:00",
    "updated_at": "2026-11-05T10:00:00",
    "created_by": "Test",
}

FAKE_PEDIDO_ROW = {
    "opportunity_id": "ccc00000-0000-0000-0000-000000000001",
    "title": "Instituto Ficticio — Equipo",
    "supplier_name": "Proveedor Ejemplo",
    "supplier_org_id": None,
    "estimated_cost_clp": Decimal("500000"),
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
        elif sql == _RESUMEN_MES_SQL:
            self.description = [SimpleNamespace(name=k) for k in RESUMEN_MES_ROW]
            self._rows = [tuple(RESUMEN_MES_ROW.values())]
        elif sql == _INDICADORES_SQL:
            self.description = [SimpleNamespace(name=k) for k in INDICADORES_ROW]
            self._rows = [tuple(INDICADORES_ROW.values())]
        elif sql == _OPEN_INVOICES_SQL:
            self.description = [SimpleNamespace(name=k) for k in OPEN_INVOICE_ROW]
            self._rows = [tuple(OPEN_INVOICE_ROW.values())]
        elif sql == _SUPPLIER_ORDERS_SQL:
            self._rows = []  # no in-flight orders
        elif sql == _PEDIDOS_ESTIMADOS_SQL:
            self.description = [SimpleNamespace(name=k) for k in FAKE_PEDIDO_ROW]
            self._rows = [tuple(FAKE_PEDIDO_ROW.values())]
        elif sql == _EXPENSES_SQL:
            self.description = [SimpleNamespace(name=k) for k in FAKE_EXPENSE_ROW]
            self._rows = [tuple(FAKE_EXPENSE_ROW.values())]

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


def test_resumen_decimal_amounts_are_int() -> None:
    """resumen() must return int amounts, not Decimal strings — same bug vector as mes()."""
    out = FinanceReadRepository(lambda dsn, autocommit=False: FakeConn(), "dsn").resumen()

    # caja horizons
    assert len(out["caja"]) == 3
    for h in out["caja"]:
        assert isinstance(h["entra"], int), f"caja entra at {h['horizon']}d is {type(h['entra'])}"
        assert isinstance(h["sale"], int), f"caja sale at {h['horizon']}d is {type(h['sale'])}"
        assert isinstance(h["sale_confirmado"], int), f"caja sale_confirmado at {h['horizon']}d is {type(h['sale_confirmado'])}"
        assert isinstance(h["sale_estimado"], int), f"caja sale_estimado at {h['horizon']}d is {type(h['sale_estimado'])}"
        assert isinstance(h["neto"], int), f"caja neto at {h['horizon']}d is {type(h['neto'])}"

    # meta section
    meta = out["meta"]
    for key in ("facturado_net", "ganado_sin_facturar_net", "en_negociacion_net", "meta_net", "ritmo_necesario"):
        assert isinstance(meta[key], int), f"meta.{key} is {type(meta[key])}"

    # indicadores
    ind = out["indicadores"]
    assert isinstance(ind["margen_mes_costo"], int)
    assert isinstance(ind["margen_mes_subtotal"], int)
    assert 0.0 <= ind["margen_mes_pct"] <= 100.0

    # cobrar_hoy: invoice balance must be int
    for inv in out["cobrar_hoy"]:
        assert isinstance(inv["balance_iva"], int), f"cobrar_hoy balance_iva is {type(inv['balance_iva'])}"


def test_resumen_amounts_leave_as_json_numbers() -> None:
    """Pydantic serialises Decimal as a string — resumen amounts must remain numbers at JSON output."""
    from pydantic import TypeAdapter

    out = FinanceReadRepository(lambda dsn, autocommit=False: FakeConn(), "dsn").resumen()
    serialised = TypeAdapter(dict[str, Any]).dump_python(out, mode="json")

    # caja
    for h in serialised["caja"]:
        for k in ("entra", "sale", "neto"):
            assert isinstance(h[k], (int, float)) and not isinstance(h[k], bool), (k, h[k])

    # meta
    for k in ("facturado_net", "ganado_sin_facturar_net", "en_negociacion_net", "meta_net"):
        v = serialised["meta"][k]
        assert isinstance(v, (int, float)) and not isinstance(v, bool), (k, v)

    # indicadores
    for k in ("margen_mes_costo", "margen_mes_subtotal", "margen_mes_pct", "dias_promedio_cobro"):
        v = serialised["indicadores"][k]
        assert isinstance(v, (int, float)) and not isinstance(v, bool), (k, v)

    # caja sale_confirmado and sale_estimado are JSON numbers
    for h in serialised["caja"]:
        for k in ("sale_confirmado", "sale_estimado"):
            assert isinstance(h[k], (int, float)) and not isinstance(h[k], bool), (k, h[k])


def test_resumen_caja_sale_includes_pedidos_and_expenses() -> None:
    """Bug 2 fix: caja sale must equal sale_confirmado + sale_estimado.

    With FakeCursor returning one pedido (500,000 CLP) and one expense (150,000 CLP),
    and no in-flight supplier orders, sale_confirmado = 0, sale_estimado = 650,000,
    sale = 650,000 for all three horizons.
    """
    out = FinanceReadRepository(lambda dsn, autocommit=False: FakeConn(), "dsn").resumen()

    for h in out["caja"]:
        assert h["sale_confirmado"] == 0, f"sale_confirmado should be 0 at {h['horizon']}d"
        assert h["sale_estimado"] == 650_000, f"sale_estimado should be 650,000 at {h['horizon']}d (500k pedido + 150k gastos)"
        assert h["sale"] == 650_000, f"sale should equal sale_confirmado + sale_estimado at {h['horizon']}d"
        assert h["neto"] == h["entra"] - 650_000, f"neto = entra - sale at {h['horizon']}d"


def test_indicadores_sql_uses_lateral_for_cost() -> None:
    """Bug 1 fix: margen_this_month CTE must use LATERAL to prevent tax_base inflation.

    If the query has a direct LEFT JOIN to crm.quote_line in the margen_this_month CTE,
    tax_base gets summed once per LINE rather than once per revision — inflating the
    margin subtotal 3× for a 3-line quote. The fix aggregates lines inside a LATERAL so
    tax_base joins exactly once per won case.
    """
    sql_lower = _INDICADORES_SQL.lower()
    assert "left join lateral" in sql_lower, (
        "margen_this_month CTE must use LEFT JOIN LATERAL to aggregate item lines once per revision"
    )
    # The join to quote_line must be inside the lateral subquery, not at the main FROM level
    assert "rev_cost" in sql_lower, (
        "LATERAL result alias 'rev_cost' expected in margen_this_month CTE"
    )


def test_pedidos_estimados_sql_excludes_invoiced_cases() -> None:
    """Bug 3 fix: _PEDIDOS_ESTIMADOS_SQL must exclude cases that already have a factura.

    Without the NOT EXISTS check on crm.invoice, won cases that have already been invoiced
    (and whose supplier should have been paid) appear in the estimated list unnecessarily.
    """
    sql_lower = _PEDIDOS_ESTIMADOS_SQL.lower()
    assert "crm.invoice" in sql_lower, (
        "_PEDIDOS_ESTIMADOS_SQL must exclude already-invoiced cases via NOT EXISTS on crm.invoice"
    )
    assert "doc_type in (33, 34)" in sql_lower, (
        "_PEDIDOS_ESTIMADOS_SQL must filter doc_type in (33, 34) for the invoice check"
    )
