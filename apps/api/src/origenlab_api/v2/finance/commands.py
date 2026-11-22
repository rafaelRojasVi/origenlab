"""The four «Finanzas» commands.

| Command | What durable fact it records |
|---|---|
| `upsert_expense` | one operating expense for a calendar month (insert or update) |
| `upsert_supplier_order` | one supplier order placed for a case/quote (insert or update) |
| `set_monthly_target` | the net-sales target for a calendar month |
| `set_partner_rule` | a new partner distribution rule (append-only; most-recent row wins) |

All four require `Deciding` (a `viewer` is 403) and an `Idempotency-Key`. The bodies carry a
non-blank `note` via `DecisionBody`. `set_partner_rule` is additionally gated to admin-only at the
route level. An update of an expense or a supplier order names the `expected_version` the operator
saw; a row changed since is a 409 `stale_version`, as in every other V2 command.

**Partners.** The schema names the two partners generically (`partner_a`, `partner_b`; migration
20261122100000). The dashboard still sends and reads its own names for them, so the bodies accept
both and the reads answer in the dashboard's (`partners.py`).
"""

from __future__ import annotations

from datetime import date
from typing import Annotated, Any

from pydantic import AliasChoices, Field

from origenlab_api.v2.commands import CommandRefused, DecisionBody
from origenlab_api.v2.finance.partners import (
    LEGACY_PARTNER_A_SALARY,
    LEGACY_PARTNER_B_RATE,
    PAID_BY_VALUES,
    paid_by_from_json,
)

UPSERT_EXPENSE = "upsert_expense"
UPSERT_SUPPLIER_ORDER = "upsert_supplier_order"
SET_MONTHLY_TARGET = "set_monthly_target"
SET_PARTNER_RULE = "set_partner_rule"

FINANCE_COMMAND_NAMES: tuple[str, ...] = (
    UPSERT_EXPENSE,
    UPSERT_SUPPLIER_ORDER,
    SET_MONTHLY_TARGET,
    SET_PARTNER_RULE,
)

EXPENSE_CATEGORIES = frozenset({
    "arriendo", "servidores", "contador", "telefono", "software",
    "viajes", "sueldos", "honorarios", "otros",
})

EXPENSE_PAID_BY = PAID_BY_VALUES  # what the column holds: empresa, partner_a, partner_b

PARTNER_RULE_NAMES = frozenset({
    "actual_50_50", "opcion_a", "opcion_b", "opcion_c", "opcion_d",
})


class UpsertExpenseBody(DecisionBody):
    """Add or update an operating expense for a calendar month.

    If `expense_id` is supplied the existing row is updated (must be a manual entry); otherwise a
    new row is inserted. `month` must be the first day of the month.
    """

    expense_id: str | None = None
    #: The version the operator saw; required with `expense_id`.
    expected_version: Annotated[int, Field(ge=1)] | None = None
    month: date  # e.g. 2026-11-01
    category: str
    amount_clp: Annotated[int, Field(gt=0)]
    paid_by: str = "empresa"
    expense_notes: str | None = None


class UpsertSupplierOrderBody(DecisionBody):
    """Add or update a supplier order placed for a case/quote.

    If `order_id` is supplied the existing row is updated; otherwise a new row is inserted.
    `amount_clp` is the CLP equivalent when the order is in a foreign currency.
    `received_on` marks delivery.
    """

    order_id: str | None = None
    #: The version the operator saw; required with `order_id`.
    expected_version: Annotated[int, Field(ge=1)] | None = None
    opportunity_id: str | None = None
    quote_id: str | None = None
    supplier_name: str
    ordered_on: date
    paid_on: date | None = None
    amount_original: Annotated[float, Field(gt=0)]
    currency: Annotated[str, Field(min_length=3, max_length=3)] = "CLP"
    amount_clp: Annotated[int, Field(gt=0)] | None = None
    expected_arrival_on: date | None = None
    received_on: date | None = None
    order_notes: str | None = None


class SetMonthlyTargetBody(DecisionBody):
    """Set the net-sales target for a calendar month. `month` must be the first day of the month."""

    month: date  # e.g. 2026-11-01
    sales_target_net: Annotated[int, Field(gt=0)]
    target_notes: str | None = None


class SetPartnerRuleBody(DecisionBody):
    """Add a new partner distribution rule (append-only; most-recent by valid_from wins).

    Admin-only. `valid_from` is the first day the new rule applies.
    """

    valid_from: date
    rule_name: str
    #: The monthly rent the rule splits. An operator's input: there is no default amount.
    arriendo_clp: Annotated[int, Field(gt=0)]
    #: Partner B's monthly rate on the capital lent (opción D); the dashboard's name is accepted too.
    partner_b_rate: Annotated[float | None, Field(ge=0, le=1, validation_alias=AliasChoices(
        "partner_b_rate", LEGACY_PARTNER_B_RATE))] = None
    #: Partner A's monthly salary before the split (opción B/C); the dashboard's name is accepted too.
    partner_a_salary: Annotated[int, Field(ge=0, validation_alias=AliasChoices(
        "partner_a_salary", LEGACY_PARTNER_A_SALARY))] = 0
    arriendo_pct_empresa: Annotated[float, Field(ge=0, le=1)] = 0.0
    split_pct: Annotated[float, Field(ge=0, le=1)] = 0.5
    rule_notes: str | None = None


def _require_version(row_id: str | None, expected_version: int | None) -> None:
    """An update names the version it was read at; an insert names none."""
    if row_id and expected_version is None:
        raise CommandRefused(422, "expected_version_required",
                             "an update names the expected_version of the row it was read at")


def validated_finance(command_name: str, body: Any) -> dict[str, Any]:
    """Field-level validation that needs no database, in one place."""
    fields: dict[str, Any] = {"note": body.note.strip()}

    if isinstance(body, UpsertExpenseBody):
        if body.category not in EXPENSE_CATEGORIES:
            raise CommandRefused(422, "expense_category_invalid",
                                 f"category must be one of {sorted(EXPENSE_CATEGORIES)}")
        paid_by = paid_by_from_json(body.paid_by)
        if paid_by is None:
            raise CommandRefused(422, "expense_paid_by_invalid",
                                 f"paid_by must be one of {sorted(EXPENSE_PAID_BY)}")
        _require_version(body.expense_id, body.expected_version)
        if body.month.day != 1:
            raise CommandRefused(422, "expense_month_not_first_day",
                                 "month must be the first day of the month (e.g. 2026-11-01)")
        fields.update({
            "expense_id": str(body.expense_id) if body.expense_id else None,
            "month": body.month,
            "category": body.category,
            "expected_version": body.expected_version,
            "amount_clp": body.amount_clp,
            "paid_by": paid_by,
            "expense_notes": (body.expense_notes or "").strip() or None,
        })

    elif isinstance(body, UpsertSupplierOrderBody):
        if body.expected_arrival_on is not None and body.expected_arrival_on < body.ordered_on:
            raise CommandRefused(422, "arrival_before_ordered",
                                 "expected_arrival_on cannot be before ordered_on")
        if body.received_on is not None and body.received_on < body.ordered_on:
            raise CommandRefused(422, "received_before_ordered",
                                 "received_on cannot be before ordered_on")
        _require_version(body.order_id, body.expected_version)
        if body.currency.upper() == "CLP" and body.amount_clp is None and round(body.amount_original) < 1:
            raise CommandRefused(422, "amount_clp_required", "a CLP order is at least one peso")
        if body.currency != "CLP" and body.amount_clp is None:
            raise CommandRefused(422, "amount_clp_required",
                                 "amount_clp (CLP equivalent) is required when currency is not CLP")
        fields.update({
            "order_id": str(body.order_id) if body.order_id else None,
            "expected_version": body.expected_version,
            "opportunity_id": str(body.opportunity_id) if body.opportunity_id else None,
            "quote_id": str(body.quote_id) if body.quote_id else None,
            "supplier_name": body.supplier_name.strip(),
            "ordered_on": body.ordered_on,
            "paid_on": body.paid_on,
            "amount_original": body.amount_original,
            "currency": body.currency.upper(),
            "amount_clp": body.amount_clp if body.amount_clp else round(body.amount_original),
            "expected_arrival_on": body.expected_arrival_on,
            "received_on": body.received_on,
            "order_notes": (body.order_notes or "").strip() or None,
        })

    elif isinstance(body, SetMonthlyTargetBody):
        if body.month.day != 1:
            raise CommandRefused(422, "target_month_not_first_day",
                                 "month must be the first day of the month (e.g. 2026-11-01)")
        fields.update({
            "month": body.month,
            "sales_target_net": body.sales_target_net,
            "target_notes": (body.target_notes or "").strip() or None,
        })

    elif isinstance(body, SetPartnerRuleBody):
        if body.rule_name not in PARTNER_RULE_NAMES:
            raise CommandRefused(422, "partner_rule_name_invalid",
                                 f"rule_name must be one of {sorted(PARTNER_RULE_NAMES)}")
        fields.update({
            "valid_from": body.valid_from,
            "rule_name": body.rule_name,
            "arriendo_clp": body.arriendo_clp,
            "partner_b_rate": body.partner_b_rate,
            "partner_a_salary": body.partner_a_salary,
            "arriendo_pct_empresa": body.arriendo_pct_empresa,
            "split_pct": body.split_pct,
            "rule_notes": (body.rule_notes or "").strip() or None,
        })

    return fields
