"""Which commercial cases a read or a rule may see: sales, never tenders (spec 2026-10-19 §6.10).

A tender is a `crm.opportunity` of `kind = 'tender'` (migration 20261020100000). It lives in its own
space, «Licitaciones» (`v2/tenders/`), and nowhere else: not in Oportunidades or the board, not in
Hoy's lists and follow-ups, not in the Cotizador's general list or Rentabilidad, not in price
history or Operaciones' counts, not in the email → cases rules, and no general case command acts on
one. Every general read that selects cases adds `SALE_CASES` (alias `o`) or `sale_cases(alias)`.

The worker (`apps/worker`: `triage.py`, the context matcher's case cards and the case memory) writes
the same predicate inline as `o.kind = 'sale'`, with a comment naming this module.
"""

from __future__ import annotations

from typing import Any

from origenlab_api.v2.commands import CommandRefused

TENDER_KIND = "tender"
SALE_CASES = "o.kind = 'sale'"
TENDER_CASES = "o.kind = 'tender'"

#: The refusal every general case command answers on a tender.
TENDER_NOT_A_SALE = "tender_not_a_sale"


def sale_cases(alias: str = "o") -> str:
    """`SALE_CASES` for a query that names `crm.opportunity` by another alias."""
    if not alias.isidentifier():
        raise ValueError(f"not an SQL alias: {alias!r}")
    return f"{alias}.kind = 'sale'"


def is_tender(kind: str | None) -> bool:
    """Whether a case's `kind` is a tender (a rule that differs for a tender's offer asks this)."""
    return kind == TENDER_KIND


def tender_not_a_sale() -> CommandRefused:
    return CommandRefused(409, TENDER_NOT_A_SALE,
                          "Esta es una licitación: se trabaja desde «Licitaciones», no como caso de venta.")


def refuse_tender(cur: Any, opportunity_id: Any) -> None:
    """Raise `tender_not_a_sale` when this case is a tender; an unknown id passes (the command's own
    lookup answers it)."""
    if opportunity_id is None:
        return
    cur.execute("select kind from crm.opportunity where id = %s::uuid", (str(opportunity_id),))
    row = cur.fetchone()
    if row is not None and is_tender(row[0]):
        raise tender_not_a_sale()
