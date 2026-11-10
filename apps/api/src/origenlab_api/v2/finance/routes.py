"""«Finanzas» — the finance dashboard read and command boundary.

| Route | Does |
|---|---|
| `GET /v2/workspace/finance/mes` | 5 KPI cards for the Mes view (vendido, meta, por cobrar, pipeline, capital atado, gastos) |
| `GET /v2/workspace/finance/expenses` | expenses for a month (`?month=YYYY-MM-DD`; default: current Santiago month) |
| `GET /v2/workspace/finance/supplier-orders` | supplier orders (`?opportunity_id=` optional) |
| `GET /v2/workspace/finance/settings` | active monthly target + active partner rule |
| `POST /v2/commands/upsert-expense` | add or update an operating expense |
| `POST /v2/commands/upsert-supplier-order` | add or update a supplier order |
| `POST /v2/commands/set-monthly-target` | set the net-sales target for a calendar month |
| `POST /v2/commands/set-partner-rule` | add a new partner distribution rule (admin-only) |

Any signed-in operator reads; commands require `Deciding` (a `viewer` is 403) and an
`Idempotency-Key`. `set_partner_rule` additionally requires the `admin` role.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from origenlab_api.v2.command_routes import Deciding, IdempotencyKey, _detail
from origenlab_api.v2.commands import CommandRefused, as_uuid, request_digest, require_idempotency_key
from origenlab_api.v2.contact_redaction import ContactRedactingRoute
from origenlab_api.v2.finance.commands import (
    SET_MONTHLY_TARGET,
    SET_PARTNER_RULE,
    UPSERT_EXPENSE,
    UPSERT_SUPPLIER_ORDER,
    SetMonthlyTargetBody,
    SetPartnerRuleBody,
    UpsertExpenseBody,
    UpsertSupplierOrderBody,
    validated_finance,
)
from origenlab_api.v2.finance.reads import FinanceReadRepository
from origenlab_api.v2.finance.repository import FinanceCommandRepository
from origenlab_api.v2.cockpit_routes import Operator

finance_command_router = APIRouter(prefix="/v2/commands", tags=["v2-commands"])
finance_read_router = APIRouter(
    prefix="/v2/workspace/finance", tags=["v2-finance"], route_class=ContactRedactingRoute
)


def get_finance_command_repository(request: Request) -> FinanceCommandRepository:
    repo = getattr(request.app.state, "v2_finance_command_repository", None)
    if repo is None:  # pragma: no cover
        raise HTTPException(status_code=503, detail="the V2 finance command boundary is not configured")
    return repo


def get_finance_read_repository(request: Request) -> FinanceReadRepository:
    repo = getattr(request.app.state, "v2_finance_read_repository", None)
    if repo is None:  # pragma: no cover
        raise HTTPException(status_code=503, detail="the V2 finance read boundary is not configured")
    return repo


FinanceCommandRepo = Annotated[FinanceCommandRepository, Depends(get_finance_command_repository)]
FinanceReadRepo = Annotated[FinanceReadRepository, Depends(get_finance_read_repository)]


def _run(*, command_name: str, body: Any, repo: FinanceCommandRepository,
         operator: Any, idempotency_key: str | None) -> dict[str, Any]:
    try:
        key = require_idempotency_key(idempotency_key)
        fields = validated_finance(command_name, body)
        digest = request_digest(command_name, body)
        return repo.execute(command_name=command_name, operator=operator, fields=fields,
                            idempotency_key=key, digest=digest)
    except CommandRefused as exc:
        raise HTTPException(status_code=exc.status_code, detail=_detail(exc)) from exc


# ── reads ─────────────────────────────────────────────────────────────────────

@finance_read_router.get("/resumen")
def finance_resumen(_: Operator, repo: FinanceReadRepo) -> dict[str, Any]:
    """Resumen v3: caja 30/60/90, meta 3-layer, indicadores, cobrar_hoy, pedidos_estimados."""
    return repo.resumen()


@finance_read_router.get("/mes")
def finance_mes(_: Operator, repo: FinanceReadRepo) -> dict[str, Any]:
    """5 KPI cards for the Mes view: vendido vs meta, por cobrar, pipeline, capital atado, gastos."""
    return repo.mes()


@finance_read_router.get("/expenses")
def finance_expenses(
    _: Operator,
    repo: FinanceReadRepo,
    month: Annotated[str | None, Query()] = None,
) -> dict[str, Any]:
    """Expenses for a calendar month (`?month=YYYY-MM-DD`; default: current Santiago month)."""
    return repo.expenses(month=month)


@finance_read_router.get("/supplier-orders")
def finance_supplier_orders(
    _: Operator,
    repo: FinanceReadRepo,
    opportunity_id: Annotated[str | None, Query()] = None,
) -> dict[str, Any]:
    """Supplier orders; `?opportunity_id=` narrows to one case."""
    try:
        opp = as_uuid(opportunity_id, "opportunity_id") if opportunity_id is not None else None
    except CommandRefused as exc:
        raise HTTPException(status_code=exc.status_code, detail=_detail(exc)) from exc
    return repo.supplier_orders(opportunity_id=opp)


@finance_read_router.get("/settings")
def finance_settings(_: Operator, repo: FinanceReadRepo) -> dict[str, Any]:
    """Active monthly target and active partner rule."""
    return repo.settings()


# ── commands ──────────────────────────────────────────────────────────────────

@finance_command_router.post("/upsert-expense")
def upsert_expense(
    body: UpsertExpenseBody,
    operator: Deciding,
    repo: FinanceCommandRepo,
    idempotency_key: IdempotencyKey = None,
) -> dict[str, Any]:
    """Add or update an operating expense for a calendar month."""
    return _run(command_name=UPSERT_EXPENSE, body=body, repo=repo, operator=operator,
                idempotency_key=idempotency_key)


@finance_command_router.post("/upsert-supplier-order")
def upsert_supplier_order(
    body: UpsertSupplierOrderBody,
    operator: Deciding,
    repo: FinanceCommandRepo,
    idempotency_key: IdempotencyKey = None,
) -> dict[str, Any]:
    """Add or update a supplier order placed for a case/quote."""
    return _run(command_name=UPSERT_SUPPLIER_ORDER, body=body, repo=repo, operator=operator,
                idempotency_key=idempotency_key)


@finance_command_router.post("/set-monthly-target")
def set_monthly_target(
    body: SetMonthlyTargetBody,
    operator: Deciding,
    repo: FinanceCommandRepo,
    idempotency_key: IdempotencyKey = None,
) -> dict[str, Any]:
    """Set the net-sales target for a calendar month."""
    return _run(command_name=SET_MONTHLY_TARGET, body=body, repo=repo, operator=operator,
                idempotency_key=idempotency_key)


@finance_command_router.post("/set-partner-rule")
def set_partner_rule(
    body: SetPartnerRuleBody,
    operator: Deciding,
    repo: FinanceCommandRepo,
    idempotency_key: IdempotencyKey = None,
) -> dict[str, Any]:
    """Add a new partner distribution rule (admin-only, append-only)."""
    if operator.role != "admin":
        raise HTTPException(status_code=403, detail="set_partner_rule requires the admin role")
    return _run(command_name=SET_PARTNER_RULE, body=body, repo=repo, operator=operator,
                idempotency_key=idempotency_key)
