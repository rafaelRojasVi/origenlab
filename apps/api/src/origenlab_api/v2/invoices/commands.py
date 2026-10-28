"""The four «Pagos» commands.

| Command | What durable fact it records |
|---|---|
| `import_invoices` | the documents of one SII «Registro de Ventas» CSV are on record (new folios only) |
| `set_invoice_case` | the document belongs to this case — or to none |
| `mark_invoice_paid` | the client paid this factura, on this day |
| `mark_invoice_unpaid` | it was not paid after all |

A document is `crm.invoice` (`DOMAIN.md` §7 #70). The import is multipart (`routes.py`); the other
three are JSON with the version the operator was shown and a non-blank `note` (`DecisionBody`).
"""

from __future__ import annotations

from datetime import date
from typing import Annotated, Any

from pydantic import Field

from origenlab_api.v2.commands import CommandRefused, DecisionBody, as_uuid

IMPORT_INVOICES = "import_invoices"
SET_INVOICE_CASE = "set_invoice_case"
MARK_INVOICE_PAID = "mark_invoice_paid"
MARK_INVOICE_UNPAID = "mark_invoice_unpaid"

INVOICE_COMMAND_NAMES: tuple[str, ...] = (
    IMPORT_INVOICES,
    SET_INVOICE_CASE,
    MARK_INVOICE_PAID,
    MARK_INVOICE_UNPAID,
)


class _InvoiceBody(DecisionBody):
    """Which document, and the version of it the operator was shown."""

    invoice_id: str
    invoice_version: Annotated[int, Field(ge=1)]


class SetInvoiceCaseBody(_InvoiceBody):
    """The case the document belongs to; `null` takes it off its case."""

    opportunity_id: str | None


class MarkInvoicePaidBody(_InvoiceBody):
    """The day the client paid (not in the future)."""

    paid_on: date


class MarkInvoiceUnpaidBody(_InvoiceBody):
    """The «Pagada» was a mistake."""


def validated_invoice(command_name: str, body: _InvoiceBody, *, today: date) -> dict[str, Any]:
    """Everything that needs no database, checked in one place."""
    fields: dict[str, Any] = {
        "note": body.note.strip(),
        "invoice_id": as_uuid(body.invoice_id, "invoice_id"),
        "invoice_version": int(body.invoice_version),
    }
    if isinstance(body, SetInvoiceCaseBody):
        fields["opportunity_id"] = (
            as_uuid(body.opportunity_id, "opportunity_id") if body.opportunity_id is not None else None
        )
    if isinstance(body, MarkInvoicePaidBody):
        if body.paid_on > today:
            raise CommandRefused(422, "paid_on_in_future", "the payment day cannot be in the future")
        fields["paid_on"] = body.paid_on
    return fields
