"""What a factura still owes — the one formula «Pagos», «Finanzas» and the «Pagada» command share.

For each factura (`doc_type` 33 or 34) of `crm.invoice`:

* **billed** — its total, plus the notas de débito (56) and less the notas de crédito (61) that
  reference it (`reference_doc_type`, `reference_folio`);
* **paid** — the sum of its live payments (`crm.invoice_payment`, `removed_at is null`); a factura
  marked «Pagada» before payments were recorded one by one (`paid_on` set, no live payment) counts
  as paid in full;
* **balance** — billed less paid: what is still owed. It is **por cobrar** while the balance is
  above zero, whatever `paid_on` says — so a nota de débito issued after «Pagada» shows up again.

`INVOICE_BALANCE_SQL` is a plain select (one row per factura: `invoice_id`, `billed`, `live_paid`,
`paid`, `balance`), used as a CTE: `with invoice_balance as ({INVOICE_BALANCE_SQL}) …`. It takes no
parameters, so it can sit inside a statement that uses `%(name)s` or `%s` placeholders.
"""

from __future__ import annotations

INVOICE_BALANCE_SQL = """
select i.id as invoice_id, a.billed, a.live_paid, p.paid, a.billed - p.paid as balance
  from crm.invoice i
  cross join lateral (
    select i.total_amount
           + coalesce((select sum(case n.doc_type when 56 then n.total_amount else -n.total_amount end)
                         from crm.invoice n
                        where n.doc_type in (56, 61) and n.reference_doc_type = i.doc_type
                          and n.reference_folio = i.folio), 0) as billed,
           coalesce((select sum(pay.amount) from crm.invoice_payment pay
                      where pay.invoice_id = i.id and pay.removed_at is null), 0) as live_paid
  ) a
  cross join lateral (
    select case when i.paid_on is not null and a.live_paid = 0 then greatest(a.billed, 0)
                else a.live_paid end as paid
  ) p
 where i.doc_type in (33, 34)
"""

