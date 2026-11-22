"""The two partners, as the schema names them and as the dashboard still does.

Migration 20261122100000 took the partners' names out of the schema: `crm.expense.paid_by` holds
`empresa`, `partner_a` or `partner_b`, and `crm.partner_rule` carries `partner_a_salary` and
`partner_b_rate`. The dashboard (`apps/dashboard/src/crm/finanzas/`) still sends and reads the
names it had before, so the API keeps those JSON names stable here — one place, deleted once the
dashboard moves to the generic ones.

Partner A is the one paid a salary before the split (opciones B/C) and who covers the rent
(opción A); partner B is the one who may lend capital at a rate (opción D).
"""

from __future__ import annotations

#: What `crm.expense.paid_by` holds.
PAID_BY_VALUES: frozenset[str] = frozenset({"empresa", "partner_a", "partner_b"})

#: The dashboard's JSON value for each partner (legacy, kept stable), and back.
_JSON_PAID_BY: dict[str, str] = {"partner_a": "tatiana", "partner_b": "sebastian"}
_COLUMN_PAID_BY: dict[str, str] = {v: k for k, v in _JSON_PAID_BY.items()}

#: The dashboard's JSON keys for the two partner-rule columns (legacy, kept stable).
LEGACY_PARTNER_A_SALARY = "sueldo_tatiana"
LEGACY_PARTNER_B_RATE = "tasa_sebastian"


def paid_by_from_json(value: str) -> str | None:
    """The column value for a `paid_by` the API was sent (generic or the dashboard's); None if unknown."""
    value = _COLUMN_PAID_BY.get(value, value)
    return value if value in PAID_BY_VALUES else None


def paid_by_to_json(value: str) -> str:
    """The `paid_by` a read answers with: the dashboard's name for a partner, else the value."""
    return _JSON_PAID_BY.get(value, value)
