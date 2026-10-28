"""The transactional half of the «Pagos» commands (`commands.py`).

The same `CommandTransaction` as every other V2 command: one transaction, one receipt per
`(operator, Idempotency-Key)`, one `crm.domain_event` per change, and a refusal rolls back all
three. What it writes is `crm.invoice` and its `invoice.*` events — nothing else; a case is read
(it must exist) and never written.

**The import adds, never rewrites.** A document already on record (same type and folio) is left
exactly as it is — its case, its «Pagada» — so uploading the same month twice, or a month that
overlaps, is harmless. A new document gets its client organization from the RUT
(`crm.external_identifier`, scheme `rut`, followed through a merge), and its case when exactly one
case of that organization has a live CLP quote revision whose total is the document's total
(within a peso): `case_link = 'machine'`. Anything less certain is left for a person.
"""

from __future__ import annotations

from typing import Any

from origenlab_api.v2.command_core import CommandTransaction
from origenlab_api.v2.commands import CommandRefused
from origenlab_api.v2.identity import OperatorIdentity
from origenlab_api.v2.invoices.commands import (
    IMPORT_INVOICES,
    MARK_INVOICE_PAID,
    MARK_INVOICE_UNPAID,
    SET_INVOICE_CASE,
)

_ORGANIZATION_BY_RUT = """
select coalesce(o.merged_into_organization_id, o.id)::text as organization_id
  from crm.external_identifier ei
  join crm.organization o on o.id = ei.organization_id
 where ei.scheme = 'rut' and ei.value_norm = %s
"""

# Cases of the organization with a live CLP revision of the document's total (±1 peso).
_CASES_BY_TOTAL = """
select distinct q.opportunity_id::text as opportunity_id
  from crm.quote q
  join crm.quote_revision qr on qr.quote_id = q.id
  join crm.opportunity op on op.id = q.opportunity_id
 where op.organization_id = %s
   and qr.status <> 'void'
   and qr.quote_currency = 'CLP'
   and qr.grand_total is not null
   and abs(qr.grand_total - %s) <= 1
"""


class V2InvoiceCommandRepository(CommandTransaction):
    """`import_invoices`, `set_invoice_case`, `mark_invoice_paid`, `mark_invoice_unpaid`."""

    # ------------------------------------------------------------------ helpers

    def _organization_for(self, cur: Any, rut: str) -> str | None:
        cur.execute(_ORGANIZATION_BY_RUT, (rut,))
        row = self._row(cur)
        return None if row is None else row["organization_id"]

    def _case_for(self, cur: Any, organization_id: str | None, total: int) -> str | None:
        if organization_id is None or total <= 0:
            return None
        cur.execute(_CASES_BY_TOTAL, (organization_id, total))
        rows = cur.fetchall()
        return rows[0][0] if len(rows) == 1 else None

    def _require_case(self, cur: Any, opportunity_id: str | None) -> None:
        if opportunity_id is None:
            return
        cur.execute("select 1 from crm.opportunity where id = %s for key share", (opportunity_id,))
        if cur.fetchone() is None:
            raise CommandRefused(404, "case_not_found", "no such commercial case")

    def _lock_invoice(self, cur: Any, fields: dict[str, Any]) -> dict[str, Any]:
        """The document, locked, at the version the operator saw."""
        cur.execute(
            """
            select id::text as id, doc_type, folio, opportunity_id::text as opportunity_id, case_link,
                   paid_on, version
              from crm.invoice
             where id = %s
               for update
            """,
            (fields["invoice_id"],),
        )
        invoice = self._row(cur)
        if invoice is None:
            raise CommandRefused(404, "invoice_not_found", "no such invoice")
        if int(invoice["version"]) != fields["invoice_version"]:
            raise CommandRefused(
                409,
                "invoice_version_conflict",
                f"this invoice changed since it was shown to you (you saw version "
                f"{fields['invoice_version']}, it is at {invoice['version']}); re-read it",
            )
        return invoice

    def _change(
        self,
        cur: Any,
        operator: OperatorIdentity,
        receipt_id: str,
        invoice: dict[str, Any],
        *,
        command: str,
        event_type: str,
        assignments: dict[str, Any],
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """Apply `assignments` at the locked version, bump it, and write the event."""
        columns = ", ".join(f"{name} = %s" for name in assignments)
        cur.execute(
            f"update crm.invoice set {columns}, version = version + 1, updated_at = now() "  # noqa: S608 - names are literals
            "where id = %s and version = %s",
            (*assignments.values(), invoice["id"], int(invoice["version"])),
        )
        if cur.rowcount != 1:
            raise CommandRefused(409, "invoice_version_conflict", "this invoice changed while the command ran")
        event = self._append_event(
            cur,
            aggregate_kind="invoice",
            aggregate_id=invoice["id"],
            event_type=event_type,
            payload=payload,
            operator=operator,
            receipt_id=receipt_id,
        )
        return {
            "command": command,
            "invoice_id": invoice["id"],
            "invoice_version": int(invoice["version"]) + 1,
            "event_ids": [event],
        }

    # ------------------------------------------------------------------ commands

    def _import_invoices(
        self, cur: Any, operator: OperatorIdentity, fields: dict[str, Any], receipt_id: str
    ) -> dict[str, Any]:
        imported = already = attached = without_organization = 0
        event_ids: list[str] = []
        for doc in fields["documents"]:
            organization_id = self._organization_for(cur, doc["client_rut"])
            opportunity_id = self._case_for(cur, organization_id, doc["total_amount"]) if doc["doc_type"] in (33, 34) else None
            cur.execute(
                """
                insert into crm.invoice
                    (doc_type, folio, issued_on, client_rut, client_name, exempt_amount, net_amount,
                     vat_amount, total_amount, reference_doc_type, reference_folio, organization_id,
                     opportunity_id, case_link)
                values (%(doc_type)s, %(folio)s, %(issued_on)s, %(client_rut)s, %(client_name)s,
                        %(exempt_amount)s, %(net_amount)s, %(vat_amount)s, %(total_amount)s,
                        %(reference_doc_type)s, %(reference_folio)s, %(organization_id)s,
                        %(opportunity_id)s, %(case_link)s)
                on conflict (doc_type, folio) do nothing
                returning id::text as id
                """,
                {**doc, "organization_id": organization_id, "opportunity_id": opportunity_id,
                 "case_link": "machine" if opportunity_id else None},
            )
            row = self._row(cur)
            if row is None:
                already += 1
                continue
            imported += 1
            attached += opportunity_id is not None
            without_organization += organization_id is None
            event_ids.append(self._append_event(
                cur,
                aggregate_kind="invoice",
                aggregate_id=row["id"],
                event_type="invoice.imported",
                payload={
                    "doc_type": doc["doc_type"],
                    "folio": doc["folio"],
                    "total_amount": doc["total_amount"],
                    "organization_id": organization_id,
                    "opportunity_id": opportunity_id,
                    "file_sha256": fields["sha256"],
                    "note": fields["note"],
                },
                operator=operator,
                receipt_id=receipt_id,
            ))
        return {
            "command": IMPORT_INVOICES,
            "imported": imported,
            "already_on_record": already,
            "attached_to_case": attached,
            "without_organization": without_organization,
            "skipped_types": fields["skipped_types"],
            "event_ids": event_ids,
        }

    def _set_invoice_case(
        self, cur: Any, operator: OperatorIdentity, fields: dict[str, Any], receipt_id: str
    ) -> dict[str, Any]:
        invoice = self._lock_invoice(cur, fields)
        self._require_case(cur, fields["opportunity_id"])
        opportunity_id = fields["opportunity_id"]
        return self._change(
            cur, operator, receipt_id, invoice,
            command=SET_INVOICE_CASE, event_type="invoice.case_set",
            assignments={"opportunity_id": opportunity_id, "case_link": "operator" if opportunity_id else None},
            payload={"opportunity_id": opportunity_id, "previous_opportunity_id": invoice["opportunity_id"],
                     "note": fields["note"]},
        )

    def _mark_invoice_paid(
        self, cur: Any, operator: OperatorIdentity, fields: dict[str, Any], receipt_id: str
    ) -> dict[str, Any]:
        invoice = self._lock_invoice(cur, fields)
        if invoice["doc_type"] not in (33, 34):
            raise CommandRefused(409, "invoice_not_collectable", "only a factura is paid; a nota adjusts one")
        if invoice["paid_on"] is not None:
            raise CommandRefused(409, "invoice_already_paid", "this invoice is already marked paid")
        return self._change(
            cur, operator, receipt_id, invoice,
            command=MARK_INVOICE_PAID, event_type="invoice.paid",
            assignments={"paid_on": fields["paid_on"]},
            payload={"paid_on": fields["paid_on"].isoformat(), "note": fields["note"]},
        )

    def _mark_invoice_unpaid(
        self, cur: Any, operator: OperatorIdentity, fields: dict[str, Any], receipt_id: str
    ) -> dict[str, Any]:
        invoice = self._lock_invoice(cur, fields)
        if invoice["paid_on"] is None:
            raise CommandRefused(409, "invoice_not_paid", "this invoice is not marked paid")
        return self._change(
            cur, operator, receipt_id, invoice,
            command=MARK_INVOICE_UNPAID, event_type="invoice.unpaid",
            assignments={"paid_on": None},
            payload={"previous_paid_on": invoice["paid_on"].isoformat(), "note": fields["note"]},
        )

    _HANDLERS = {
        IMPORT_INVOICES: _import_invoices,
        SET_INVOICE_CASE: _set_invoice_case,
        MARK_INVOICE_PAID: _mark_invoice_paid,
        MARK_INVOICE_UNPAID: _mark_invoice_unpaid,
    }
