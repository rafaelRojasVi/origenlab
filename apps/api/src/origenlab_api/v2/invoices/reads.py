"""Read-only query behind `GET /v2/workspace/invoices` («Pagos»). Same `(connect, dsn)` wiring as the other readers.

A factura's **billed** amount is its total, less the notas de crédito and plus the notas de débito
that reference it; its **balance** is what is still owed: billed less its live payments (abonos,
`crm.invoice_payment`). It is **por cobrar** while it is not «Pagada» and something is owed; the age
is counted from its issue day in Santiago. Notas are listed under the factura they adjust. A
factura marked «Pagada» before payments were recorded one by one owes nothing.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date
from typing import Any

_ITEMS = """
select i.id::text as invoice_id, i.version, i.doc_type, i.folio, i.issued_on, i.client_rut, i.client_name,
       i.net_amount, i.vat_amount, i.exempt_amount, i.total_amount,
       i.reference_doc_type, i.reference_folio, i.case_link, i.paid_on,
       (select coalesce(json_agg(json_build_object(
                 'payment_id', p.id, 'paid_on', p.paid_on, 'amount', p.amount, 'recorded_by', op.display_name)
                 order by p.paid_on, p.created_at), '[]'::json)
          from crm.invoice_payment p
          left join platform.operator op on op.id = p.recorded_by_operator_id
         where p.invoice_id = i.id and p.removed_at is null) as payments,
       org.id::text as org_id, org.name as org_name,
       o.id::text as o_id, o.title as o_title, o.stage as o_stage,
       (select q.quote_number from crm.quote q where q.opportunity_id = o.id
         order by q.quote_number desc limit 1) as o_quote
  from crm.invoice i
  left join crm.organization org on org.id = i.organization_id
  left join crm.opportunity o on o.id = i.opportunity_id
 where (%(opportunity_id)s::uuid is null or i.opportunity_id = %(opportunity_id)s::uuid
        or (i.doc_type in (56, 61) and exists (
              select 1 from crm.invoice f
               where f.opportunity_id = %(opportunity_id)s::uuid
                 and f.doc_type = i.reference_doc_type and f.folio = i.reference_folio)))
 order by i.issued_on desc, i.doc_type, i.folio desc
"""

BUCKETS = (("0-30", 0, 30), ("31-60", 31, 60), ("61-90", 61, 90), ("90+", 91, None))


def _iso(value: Any) -> str | None:
    return value.isoformat() if value is not None else None


def _bucket(days: int) -> str:
    for name, low, high in BUCKETS:
        if days >= low and (high is None or days <= high):
            return name
    return BUCKETS[0][0]


def shape(rows: list[dict[str, Any]], today: date) -> dict[str, Any]:
    """Facturas with their notas and balance, and the por-cobrar totals by age."""
    notes: dict[tuple[int, int], list[dict[str, Any]]] = {}
    for r in rows:
        if r["doc_type"] in (56, 61) and r["reference_folio"] is not None:
            notes.setdefault((r["reference_doc_type"], r["reference_folio"]), []).append(r)
    facturas = {(r["doc_type"], r["folio"]) for r in rows if r["doc_type"] in (33, 34)}
    items: list[dict[str, Any]] = []
    for r in rows:
        if r["doc_type"] in (56, 61) and (r["reference_doc_type"], r["reference_folio"]) in facturas:
            continue  # listed under the factura it adjusts
        own = notes.get((r["doc_type"], r["folio"]), []) if r["doc_type"] in (33, 34) else []
        billed = r["total_amount"] + sum(n["total_amount"] * (1 if n["doc_type"] == 56 else -1) for n in own)
        payments = r.get("payments") or []
        paid_amount = sum(int(p["amount"]) for p in payments)
        if r["paid_on"] is not None and paid_amount == 0:
            paid_amount = max(billed, 0)  # «Pagada» before payments were recorded one by one
        balance = billed - paid_amount
        collectable = r["doc_type"] in (33, 34)
        if not collectable:
            status = "nota"
        elif r["paid_on"] is not None:
            status = "paid"
        elif billed <= 0:
            status = "credited"
        else:
            status = "unpaid"
        days = (today - r["issued_on"]).days
        case = None
        if r["o_id"] is not None:
            case = {"id": r["o_id"], "title": r["o_title"], "stage": r["o_stage"], "quote_number": r["o_quote"]}
        items.append({
            "invoice_id": r["invoice_id"], "version": r["version"], "doc_type": r["doc_type"], "folio": r["folio"],
            "issued_on": _iso(r["issued_on"]), "days_open": days, "age_bucket": _bucket(days) if status == "unpaid" else None,
            "client_rut": r["client_rut"], "client_name": r["client_name"],
            "organization": {"id": r["org_id"], "name": r["org_name"]} if r["org_id"] else None,
            "net_amount": r["net_amount"], "vat_amount": r["vat_amount"], "exempt_amount": r["exempt_amount"],
            "total_amount": r["total_amount"], "billed": billed if collectable else None,
            "paid_amount": paid_amount if collectable else None, "balance": balance if collectable else None,
            "payments": [{"payment_id": p["payment_id"], "paid_on": p["paid_on"], "amount": int(p["amount"]),
                          "recorded_by": p["recorded_by"]} for p in payments],
            "reference": ({"doc_type": r["reference_doc_type"], "folio": r["reference_folio"]}
                          if r["reference_folio"] is not None else None),
            "notes": [{"invoice_id": n["invoice_id"], "doc_type": n["doc_type"], "folio": n["folio"],
                       "issued_on": _iso(n["issued_on"]), "total_amount": n["total_amount"]} for n in own],
            "status": status, "paid_on": _iso(r["paid_on"]), "case_link": r["case_link"], "opportunity": case,
        })
    unpaid = [i for i in items if i["status"] == "unpaid"]
    by_bucket = {name: sum(i["balance"] for i in unpaid if i["age_bucket"] == name) for name, _, _ in BUCKETS}
    return {
        "items": items,
        "summary": {
            "por_cobrar": sum(i["balance"] for i in unpaid),
            "unpaid_count": len(unpaid),
            "by_age": by_bucket,
            "without_case": sum(1 for i in items if i["status"] in ("unpaid", "paid") and i["opportunity"] is None),
            "last_issued_on": max((i["issued_on"] for i in items), default=None),
        },
        "today": today.isoformat(),
    }


class InvoiceReadRepository:
    def __init__(self, connect: Any, dsn: str, statement_timeout_ms: int = 30_000) -> None:
        self._connect = connect
        self._dsn = dsn
        self._statement_timeout_ms = statement_timeout_ms

    @contextmanager
    def _read(self) -> Iterator[Any]:
        with self._connect(self._dsn, autocommit=False) as conn:
            with conn.cursor() as cur:
                cur.execute("set transaction read only")
                cur.execute(f"set local statement_timeout = {int(self._statement_timeout_ms)}")
                try:
                    yield cur
                finally:
                    conn.rollback()

    def list(self, opportunity_id: str | None = None) -> dict[str, Any]:
        with self._read() as cur:
            cur.execute("select (now() at time zone 'America/Santiago')::date")
            today = cur.fetchone()[0]
            cur.execute(_ITEMS, {"opportunity_id": opportunity_id})
            names = [d.name for d in cur.description]
            rows = [dict(zip(names, r, strict=True)) for r in cur.fetchall()]
        return shape(rows, today)

    def today(self) -> date:
        with self._read() as cur:
            cur.execute("select (now() at time zone 'America/Santiago')::date")
            return cur.fetchone()[0]
