"""The transactional half of the «Finanzas» commands (`commands.py`).

The same `CommandTransaction` as every other V2 command: one transaction, one receipt per
`(operator, Idempotency-Key)`, one `crm.domain_event` per change, and a refusal rolls back all
three.

What it writes:
  * `crm.expense` — `upsert_expense` inserts a new row or updates an existing manual one
  * `crm.supplier_order` — `upsert_supplier_order` inserts or updates
  * `crm.monthly_target` — `set_monthly_target` upserts (one row per calendar month)
  * `crm.partner_rule` — `set_partner_rule` inserts a new row (append-only by trigger)

Nothing here touches durable CRM tables directly. `crm.domain_event` and
`platform.command_receipt` are written by `CommandTransaction`.
"""

from __future__ import annotations

from typing import Any

from origenlab_api.v2.command_core import CommandTransaction
from origenlab_api.v2.commands import CommandRefused
from origenlab_api.v2.finance.commands import (
    SET_MONTHLY_TARGET,
    SET_PARTNER_RULE,
    UPSERT_EXPENSE,
    UPSERT_SUPPLIER_ORDER,
)
from origenlab_api.v2.identity import OperatorIdentity


class FinanceCommandRepository(CommandTransaction):
    """`upsert_expense`, `upsert_supplier_order`, `set_monthly_target`, `set_partner_rule`."""

    def _upsert_expense(
        self, cur: Any, operator: OperatorIdentity, fields: dict[str, Any], receipt_id: str
    ) -> dict[str, Any]:
        """Insert a new expense or update an existing manual one."""
        expense_id = fields.get("expense_id")
        if expense_id:
            # Update path: load the existing row and check it is a manual entry
            cur.execute(
                "select id::text as id, source, month, category, amount_clp from crm.expense "
                "where id = %s for update",
                (expense_id,),
            )
            existing = self._row(cur)
            if existing is None:
                raise CommandRefused(404, "expense_not_found", "no such expense")
            if existing["source"] != "manual":
                raise CommandRefused(
                    409, "expense_not_manual",
                    "only manually-entered expenses may be updated; SII-imported ones are immutable",
                )
            cur.execute(
                """
                update crm.expense
                   set month = %s, category = %s, amount_clp = %s, paid_by = %s,
                       notes = %s, updated_at = now()
                 where id = %s
                returning id::text
                """,
                (fields["month"], fields["category"], fields["amount_clp"], fields["paid_by"],
                 fields["expense_notes"], expense_id),
            )
            row = cur.fetchone()
            event_type = "expense.updated"
        else:
            # Insert path
            cur.execute(
                """
                insert into crm.expense
                    (month, category, amount_clp, paid_by, notes, source, created_by_operator_id)
                values (%s, %s, %s, %s, %s, 'manual', %s)
                returning id::text
                """,
                (fields["month"], fields["category"], fields["amount_clp"], fields["paid_by"],
                 fields["expense_notes"], operator.operator_id),
            )
            row = cur.fetchone()
            expense_id = row[0]
            event_type = "expense.created"

        self._append_event(
            cur,
            aggregate_kind="expense",
            aggregate_id=expense_id,
            event_type=event_type,
            payload={
                "month": fields["month"].isoformat(),
                "category": fields["category"],
                "amount_clp": fields["amount_clp"],
                "paid_by": fields["paid_by"],
                "note": fields["note"],
            },
            operator=operator,
            receipt_id=receipt_id,
        )
        return {"command": UPSERT_EXPENSE, "expense_id": expense_id, "event_type": event_type}

    def _upsert_supplier_order(
        self, cur: Any, operator: OperatorIdentity, fields: dict[str, Any], receipt_id: str
    ) -> dict[str, Any]:
        """Insert a new supplier order or update an existing one."""
        order_id = fields.get("order_id")
        if order_id:
            cur.execute(
                "select id::text as id from crm.supplier_order where id = %s for update",
                (order_id,),
            )
            existing = self._row(cur)
            if existing is None:
                raise CommandRefused(404, "supplier_order_not_found", "no such supplier order")
            cur.execute(
                """
                update crm.supplier_order
                   set opportunity_id = %s, quote_id = %s, supplier_name = %s,
                       ordered_on = %s, paid_on = %s, amount_original = %s, currency = %s,
                       amount_clp = %s, expected_arrival_on = %s, received_on = %s,
                       notes = %s, updated_at = now()
                 where id = %s
                """,
                (fields["opportunity_id"], fields["quote_id"], fields["supplier_name"],
                 fields["ordered_on"], fields["paid_on"], fields["amount_original"],
                 fields["currency"], fields["amount_clp"], fields["expected_arrival_on"],
                 fields["received_on"], fields["order_notes"], order_id),
            )
            event_type = "supplier_order.updated"
        else:
            cur.execute(
                """
                insert into crm.supplier_order
                    (opportunity_id, quote_id, supplier_name, ordered_on, paid_on,
                     amount_original, currency, amount_clp, expected_arrival_on, received_on,
                     notes, created_by_operator_id)
                values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                returning id::text
                """,
                (fields["opportunity_id"], fields["quote_id"], fields["supplier_name"],
                 fields["ordered_on"], fields["paid_on"], fields["amount_original"],
                 fields["currency"], fields["amount_clp"], fields["expected_arrival_on"],
                 fields["received_on"], fields["order_notes"], operator.operator_id),
            )
            order_id = cur.fetchone()[0]
            event_type = "supplier_order.created"

        self._append_event(
            cur,
            aggregate_kind="supplier_order",
            aggregate_id=order_id,
            event_type=event_type,
            payload={
                "supplier_name": fields["supplier_name"],
                "ordered_on": fields["ordered_on"].isoformat(),
                "amount_clp": fields["amount_clp"],
                "currency": fields["currency"],
                "note": fields["note"],
            },
            operator=operator,
            receipt_id=receipt_id,
        )
        return {"command": UPSERT_SUPPLIER_ORDER, "order_id": order_id, "event_type": event_type}

    def _set_monthly_target(
        self, cur: Any, operator: OperatorIdentity, fields: dict[str, Any], receipt_id: str
    ) -> dict[str, Any]:
        """Upsert the sales target for a calendar month."""
        cur.execute(
            """
            insert into crm.monthly_target (month, sales_target_net, notes, set_by_operator_id)
            values (%s, %s, %s, %s)
            on conflict (month) do update
               set sales_target_net = excluded.sales_target_net,
                   notes = excluded.notes,
                   set_by_operator_id = excluded.set_by_operator_id
            returning month::text
            """,
            (fields["month"], fields["sales_target_net"],
             fields["target_notes"], operator.operator_id),
        )
        month = cur.fetchone()[0]
        self._append_event(
            cur,
            aggregate_kind="monthly_target",
            aggregate_id=month,
            event_type="monthly_target.set",
            payload={
                "month": fields["month"].isoformat(),
                "sales_target_net": fields["sales_target_net"],
                "note": fields["note"],
            },
            operator=operator,
            receipt_id=receipt_id,
        )
        return {"command": SET_MONTHLY_TARGET, "month": month,
                "sales_target_net": fields["sales_target_net"]}

    def _set_partner_rule(
        self, cur: Any, operator: OperatorIdentity, fields: dict[str, Any], receipt_id: str
    ) -> dict[str, Any]:
        """Append a new partner distribution rule."""
        cur.execute(
            """
            insert into crm.partner_rule
                (valid_from, rule_name, arriendo_clp, tasa_sebastian, sueldo_tatiana,
                 arriendo_pct_empresa, split_pct, notes, set_by_operator_id)
            values (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            returning id
            """,
            (fields["valid_from"], fields["rule_name"], fields["arriendo_clp"],
             fields["tasa_sebastian"], fields["sueldo_tatiana"],
             fields["arriendo_pct_empresa"], fields["split_pct"],
             fields["rule_notes"], operator.operator_id),
        )
        rule_id = cur.fetchone()[0]
        self._append_event(
            cur,
            aggregate_kind="partner_rule",
            aggregate_id=str(rule_id),
            event_type="partner_rule.set",
            payload={
                "valid_from": fields["valid_from"].isoformat(),
                "rule_name": fields["rule_name"],
                "split_pct": fields["split_pct"],
                "note": fields["note"],
            },
            operator=operator,
            receipt_id=receipt_id,
        )
        return {"command": SET_PARTNER_RULE, "rule_id": rule_id, "rule_name": fields["rule_name"]}

    _HANDLERS = {
        UPSERT_EXPENSE: _upsert_expense,
        UPSERT_SUPPLIER_ORDER: _upsert_supplier_order,
        SET_MONTHLY_TARGET: _set_monthly_target,
        SET_PARTNER_RULE: _set_partner_rule,
    }
