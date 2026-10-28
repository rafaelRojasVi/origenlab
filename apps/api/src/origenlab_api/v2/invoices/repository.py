"""The transactional half of the «Pagos» commands (`commands.py`).

The same `CommandTransaction` as every other V2 command: one transaction, one receipt per
`(operator, Idempotency-Key)`, one `crm.domain_event` per change, and a refusal rolls back all
three. What it writes is `crm.invoice` and its `invoice.*` events — nothing else; a case is read
(it must exist) and never written.

**The import adds, never rewrites.** A document already on record (same type and folio) is left
exactly as it is — its case, its «Pagada» — so uploading the same month twice, or a month that
overlaps, is harmless. A new document gets its client organization from the RUT
(`crm.external_identifier`, scheme `rut`, followed through a merge), else from an earlier document
of the same RUT that has one, else from the name: «LABORATORIO FICTICIO S.A.» is the organization
`laboratorioficticio.cl` once the legal form and the domain ending are dropped. Its case, among that
client's cases: the one quote whose neto is the document's neto (within a peso) — the authored
amount, or the one the quote reader read off the PDF — else the client's only won case. Either is
`case_link = 'machine'`; anything less certain is left for a person.

A factura still without a case that no person has touched is tried again on every import, so a
case won (or a quote read) after the upload still finds it. One a person set — or cleared — is never
guessed over.
"""

from __future__ import annotations

import re
import unicodedata
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

# An earlier document of the same RUT already tied to a client (a person's «Asignar caso» sets it).
_ORGANIZATION_BY_EARLIER_INVOICE = """
select coalesce(o.merged_into_organization_id, o.id)::text as organization_id
  from crm.invoice i
  join crm.organization o on o.id = i.organization_id
 where i.client_rut = %s
 order by i.issued_on desc
 limit 1
"""

_ORGANIZATIONS = """
select id::text as organization_id, name from crm.organization where merged_into_organization_id is null
"""

# Cases of the organizations with a live CLP revision whose neto is the document's (±1 peso): the
# Cotizador's own amount, or the neto the quote reader read off the PDF.
_CASES_BY_NET = """
select distinct q.opportunity_id::text as opportunity_id
  from crm.quote q
  join crm.quote_revision qr on qr.quote_id = q.id
  join crm.opportunity op on op.id = q.opportunity_id
  left join evidence.source_record qd
         on qd.dedupe_key = 'quote_document:' || lower(qr.pdf_sha256) and qd.kind = 'quote_document'
 where op.organization_id = any(%s::uuid[])
   and qr.status <> 'void'
   and abs(coalesce(
         case when qr.quote_currency = 'CLP' then qr.tax_base end,
         case when qd.payload ->> 'currency' = 'CLP'
                   and qd.payload ->> 'net_total' ~ '^[0-9]+(\\.[0-9]+)?$'
              then (qd.payload ->> 'net_total')::numeric end) - %s) <= 1
"""

_WON_CASES = """
select id::text as opportunity_id from crm.opportunity where organization_id = any(%s::uuid[]) and stage = 'won'
"""

# A legal form or a filler word never tells two clients apart.
_NAME_NOISE = frozenset({"s", "a", "sa", "spa", "ltda", "limitada", "eirl", "cia", "y", "de", "del", "la", "el",
                         "los", "las"})


def name_key(name: str | None) -> str:
    """«LABORATORIO FICTICIO S.A.», «Laboratorio Ficticio SA» and `laboratorioficticio.cl` → `laboratorioficticio`; '' when too short to trust."""
    text = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode().lower().strip()
    if re.fullmatch(r"[a-z0-9-]+(\.[a-z0-9-]+)+", text):  # an organization known only by its domain
        text = text.split(".")[0]
    key = "".join(w for w in re.findall(r"[a-z0-9]+", text) if w not in _NAME_NOISE)
    return key if len(key) >= 4 else ""


class V2InvoiceCommandRepository(CommandTransaction):
    """`import_invoices`, `set_invoice_case`, `mark_invoice_paid`, `mark_invoice_unpaid`."""

    # ------------------------------------------------------------------ helpers

    def _organizations_for(self, cur: Any, rut: str, name: str, by_name: dict[str, list[str]]) -> list[str]:
        """The client: by RUT, else an earlier document of that RUT, else every organization of that name."""
        for sql in (_ORGANIZATION_BY_RUT, _ORGANIZATION_BY_EARLIER_INVOICE):
            cur.execute(sql, (rut,))
            row = self._row(cur)
            if row is not None:
                return [row["organization_id"]]
        return by_name.get(name_key(name), [])

    def _organizations_by_name(self, cur: Any) -> dict[str, list[str]]:
        cur.execute(_ORGANIZATIONS)
        by_name: dict[str, list[str]] = {}
        for organization_id, name in cur.fetchall():
            if key := name_key(name):
                by_name.setdefault(key, []).append(organization_id)
        return by_name

    def _case_for(self, cur: Any, organization_ids: list[str], net: int) -> str | None:
        """The one case whose quote has the document's neto, else the client's only won case."""
        if not organization_ids:
            return None
        if net > 0:
            cur.execute(_CASES_BY_NET, (organization_ids, net))
            rows = cur.fetchall()
            if rows:
                return rows[0][0] if len(rows) == 1 else None
        cur.execute(_WON_CASES, (organization_ids,))
        rows = cur.fetchall()
        return rows[0][0] if len(rows) == 1 else None

    def _guess(self, cur: Any, doc: dict[str, Any], by_name: dict[str, list[str]]) -> tuple[str | None, str | None]:
        """(organization, case) for a document; a nota never gets a case of its own."""
        organization_ids = self._organizations_for(cur, doc["client_rut"], doc["client_name"], by_name)
        organization_id = organization_ids[0] if len(organization_ids) == 1 else None
        if doc["doc_type"] not in (33, 34):
            return organization_id, None
        opportunity_id = self._case_for(cur, organization_ids, doc["net_amount"] + doc["exempt_amount"])
        if opportunity_id is not None and organization_id is None:
            cur.execute("select organization_id::text from crm.opportunity where id = %s", (opportunity_id,))
            organization_id = cur.fetchone()[0]
        return organization_id, opportunity_id

    def _retry_guess(
        self, cur: Any, operator: OperatorIdentity, receipt_id: str, doc: dict[str, Any],
        by_name: dict[str, list[str]], note: str,
    ) -> str | None:
        """A factura on record with no case that no person touched: guess again; the event id when attached."""
        cur.execute(
            """
            select i.id::text as id, i.opportunity_id::text as opportunity_id, i.version
              from crm.invoice i
             where i.doc_type = %s and i.folio = %s and i.opportunity_id is null and i.doc_type in (33, 34)
               and not exists (select 1 from crm.domain_event e
                                where e.aggregate_kind = 'invoice' and e.aggregate_id = i.id
                                  and e.event_type = 'invoice.case_set')
               for update
            """,
            (doc["doc_type"], doc["folio"]),
        )
        invoice = self._row(cur)
        if invoice is None:
            return None
        organization_id, opportunity_id = self._guess(cur, doc, by_name)
        if opportunity_id is None:
            return None
        result = self._change(
            cur, operator, receipt_id, invoice,
            command=IMPORT_INVOICES, event_type="invoice.case_set",
            assignments={"organization_id": organization_id, "opportunity_id": opportunity_id, "case_link": "machine"},
            payload={"opportunity_id": opportunity_id, "previous_opportunity_id": None, "case_link": "machine",
                     "note": note},
        )
        return result["event_ids"][0]

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
                   organization_id::text as organization_id, paid_on, version
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
        by_name = self._organizations_by_name(cur)
        for doc in fields["documents"]:
            organization_id, opportunity_id = self._guess(cur, doc, by_name)
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
                retried = self._retry_guess(cur, operator, receipt_id, doc, by_name, fields["note"])
                if retried is not None:
                    attached += 1
                    event_ids.append(retried)
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
        assignments: dict[str, Any] = {"opportunity_id": opportunity_id,
                                       "case_link": "operator" if opportunity_id else None}
        if opportunity_id is not None and invoice["organization_id"] is None:
            # The case names the client: the next document of this RUT finds it.
            cur.execute("select organization_id::text from crm.opportunity where id = %s", (opportunity_id,))
            assignments["organization_id"] = cur.fetchone()[0]
        return self._change(
            cur, operator, receipt_id, invoice,
            command=SET_INVOICE_CASE, event_type="invoice.case_set",
            assignments=assignments,
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
