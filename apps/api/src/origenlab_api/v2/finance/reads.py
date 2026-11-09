"""Read-only queries for the «Finanzas» module.

| Route | Does |
|---|---|
| `GET /v2/workspace/finance/mes` | 5 KPI cards for the Mes view (vendido, meta, por cobrar, pipeline, capital atado, gastos) |
| `GET /v2/workspace/finance/expenses` | expenses for a month (default: current Santiago month) |
| `GET /v2/workspace/finance/supplier-orders` | supplier orders, optionally filtered by opportunity |
| `GET /v2/workspace/finance/settings` | active monthly target + active partner rule |

«Vendido» = net_amount of crm.invoice doc_type 33/34 issued in the Santiago month, plus notas de
débito (56) minus notas de crédito (61). «Por cobrar» is the total balance of unpaid invoices (not
month-scoped). «Pipeline ponderado» is the sum of open-case amounts weighted by stage probability.
«Capital atado» is the sum of supplier orders in-flight (ordered but not yet received). «Gastos mes»
is the sum of crm.expense for the current month.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from decimal import Decimal
from typing import Any

# Stage-to-probability weights for the pipeline ponderado.
# Probabilities reflect historical OrigenLab close rates.
_STAGE_WEIGHTS: dict[str, float] = {
    "lead": 0.10,
    "qualifying": 0.20,
    "qualified": 0.35,
    "quoting": 0.50,
    "negotiating": 0.75,
}

_DEFAULT_TARGET_NET = 6_000_000  # pesos CLP

_POR_COBRAR_AGING_SQL = """
with month_bounds as (
  select (now() at time zone 'America/Santiago')::date as today
),
invoice_balance as (
  select
    i.id,
    i.issued_on,
    (i.total_amount
     + coalesce((
         select sum(case n.doc_type when 56 then n.total_amount else -n.total_amount end)
           from crm.invoice n
          where n.doc_type in (56, 61) and n.reference_doc_type = i.doc_type and n.reference_folio = i.folio
     ), 0)
     - coalesce((
         select sum(p.amount) from crm.invoice_payment p
          where p.invoice_id = i.id and p.removed_at is null
     ), 0)
    ) as balance_iva
  from crm.invoice i
  where i.doc_type in (33, 34) and i.paid_on is null
)
select
  case
    when (mb.today - ib.issued_on) <= 30 then '0-30'
    when (mb.today - ib.issued_on) <= 60 then '31-60'
    when (mb.today - ib.issued_on) <= 90 then '61-90'
    else '90+'
  end as bucket,
  coalesce(sum(ib.balance_iva), 0) as total_iva
from invoice_balance ib, month_bounds mb
where ib.balance_iva > 0
group by 1
"""

_MONTHLY_HISTORY_SQL = """
select
  date_trunc('month', i.issued_on at time zone 'America/Santiago')::date::text as month,
  coalesce(sum(
    case i.doc_type
      when 33 then i.net_amount
      when 34 then i.net_amount
      when 56 then i.net_amount
      when 61 then -i.net_amount
      else 0
    end
  ), 0) as neto
from crm.invoice i
where i.issued_on >= (date_trunc('month', now() at time zone 'America/Santiago') - interval '5 months')::date
  and i.doc_type in (33, 34, 56, 61)
group by 1
order by 1
"""

_MES_SQL = """
with
  -- current Santiago month boundaries
  month_bounds as (
    select
      date_trunc('month', now() at time zone 'America/Santiago')::date as month_start,
      (date_trunc('month', now() at time zone 'America/Santiago') + interval '1 month')::date as month_end,
      (now() at time zone 'America/Santiago')::date as today
  ),
  -- vendido: net_amount of invoices issued in this Santiago month (facturas + notas de débito + notas de crédito)
  vendido as (
    select coalesce(sum(
      case i.doc_type
        when 33 then i.net_amount
        when 34 then i.net_amount
        when 56 then i.net_amount   -- nota de débito: adds to vendido
        when 61 then -i.net_amount  -- nota de crédito: subtracts from vendido
        else 0
      end
    ), 0) as neto
    from crm.invoice i, month_bounds mb
    where i.issued_on >= mb.month_start and i.issued_on < mb.month_end
      and i.doc_type in (33, 34, 56, 61)
  ),
  -- por cobrar: total balance of unpaid facturas (not month-scoped)
  por_cobrar as (
    select coalesce(sum(
      -- billed = total + notas adjustments
      i.total_amount
      + coalesce((
        select sum(case n.doc_type when 56 then n.total_amount else -n.total_amount end)
          from crm.invoice n
         where n.doc_type in (56, 61) and n.reference_doc_type = i.doc_type and n.reference_folio = i.folio
      ), 0)
      -- subtract payments
      - coalesce((
        select sum(p.amount) from crm.invoice_payment p
         where p.invoice_id = i.id and p.removed_at is null
      ), 0)
    ), 0) as total,
    count(*) filter (where i.doc_type in (33, 34) and i.paid_on is null) as count
    from crm.invoice i
    where i.doc_type in (33, 34)
      and i.paid_on is null
      and (
        i.total_amount
        + coalesce((
          select sum(case n.doc_type when 56 then n.total_amount else -n.total_amount end)
            from crm.invoice n
           where n.doc_type in (56, 61) and n.reference_doc_type = i.doc_type and n.reference_folio = i.folio
        ), 0)
        - coalesce((
          select sum(p.amount) from crm.invoice_payment p
           where p.invoice_id = i.id and p.removed_at is null
        ), 0)
      ) > 0
  ),
  -- pipeline ponderado: open cases with their amounts × stage weight
  -- We pass stage weights from Python since SQL doesn't have the mapping
  pipeline_raw as (
    select o.stage,
           coalesce(
             -- latest live quote revision (CLP tax base), else null
             (select qr.tax_base
                from crm.quote q
                join crm.quote_revision qr on qr.quote_id = q.id
               where q.opportunity_id = o.id and qr.status = 'active'
                 and qr.quote_currency = 'CLP' and qr.tax_base is not null
               order by qr.created_at desc limit 1),
             0
           ) as amount_clp
      from crm.opportunity o
     where o.stage not in ('won', 'lost', 'abandoned')
  ),
  -- capital atado: supplier orders placed but not fully received
  capital as (
    select coalesce(sum(so.amount_clp), 0) as total,
           count(*) as count
      from crm.supplier_order so
     where so.received_on is null
  ),
  -- gastos mes: manual operating expenses for the current month
  gastos as (
    select coalesce(sum(e.amount_clp), 0) as total,
           count(*) as count
      from crm.expense e, month_bounds mb
     where e.month = mb.month_start
  ),
  -- monthly target
  target as (
    select sales_target_net
      from crm.monthly_target, month_bounds mb
     where month = mb.month_start
     limit 1
  )
select
  mb.month_start::text as mes,
  mb.today::text as today,
  v.neto as vendido_net,
  coalesce(t.sales_target_net, %(default_target)s) as meta_net,
  pc.total as por_cobrar_total,
  pc.count as por_cobrar_count,
  cap.total as capital_atado_total,
  cap.count as capital_atado_count,
  g.total as gastos_total,
  g.count as gastos_count
from month_bounds mb, vendido v, por_cobrar pc, capital cap, gastos g
left join target t on true
"""

_PIPELINE_BY_STAGE_SQL = """
-- Each open case's neto in CLP, from its current quote revision (not void, not superseded): the
-- Cotizador's own `tax_base`, else what the quote reader read from the PDF (`quote_document`) —
-- the same two sources the case panel and the board use (`quote_documents.quote_contents`).
select o.stage,
       coalesce(
         (select coalesce(
                   case when qr.quote_currency = 'CLP' then qr.tax_base end,
                   case when qd.payload ->> 'currency' = 'CLP'
                         and qd.payload ->> 'net_total' ~ '^[0-9]+([.][0-9]+)?$'
                        then (qd.payload ->> 'net_total')::numeric end)
            from crm.quote q
            join crm.quote_revision qr on qr.quote_id = q.id
            left join evidence.source_record qd
                   on qd.dedupe_key = 'quote_document:' || lower(qr.pdf_sha256) and qd.kind = 'quote_document'
           where q.opportunity_id = o.id and qr.status <> 'void' and qr.superseded_at is null
           order by qr.created_at desc limit 1),
         0
       ) as amount_clp,
       o.id::text as opportunity_id,
       o.title
  from crm.opportunity o
 where o.stage not in ('won', 'lost', 'abandoned')
 order by o.stage, o.created_at desc
"""

_EXPENSES_SQL = """
select e.id::text as expense_id, e.month::text, e.category, e.amount_clp,
       e.paid_by, e.notes, e.source, e.external_ref,
       e.created_at::text, e.updated_at::text,
       op.display_name as created_by
  from crm.expense e
  left join platform.operator op on op.id = e.created_by_operator_id
 where e.month = %(month)s::date
 order by e.created_at desc
"""

_SUPPLIER_ORDERS_SQL = """
select so.id::text as order_id, so.supplier_name, so.ordered_on::text,
       so.paid_on::text, so.amount_original, so.currency, so.amount_clp,
       so.expected_arrival_on::text, so.received_on::text, so.notes,
       so.created_at::text, so.updated_at::text,
       so.opportunity_id::text, so.quote_id::text,
       o.title as opportunity_title
  from crm.supplier_order so
  left join crm.opportunity o on o.id = so.opportunity_id
 where (%(opportunity_id)s::uuid is null or so.opportunity_id = %(opportunity_id)s::uuid)
 order by so.ordered_on desc, so.created_at desc
 limit 500
"""

_SETTINGS_SQL = """
with active_rule as (
  select rule_name, arriendo_clp, tasa_sebastian, sueldo_tatiana,
         arriendo_pct_empresa, split_pct, valid_from::text, notes
    from crm.partner_rule
   order by valid_from desc
   limit 1
),
current_target as (
  select sales_target_net, notes as target_notes, month::text
    from crm.monthly_target
   order by month desc
   limit 1
)
select
  coalesce(ct.sales_target_net, %(default_target)s) as latest_target_net,
  ct.target_notes,
  ct.month as target_month,
  ar.rule_name,
  ar.arriendo_clp,
  ar.tasa_sebastian,
  ar.sueldo_tatiana,
  ar.arriendo_pct_empresa,
  ar.split_pct,
  ar.valid_from,
  ar.notes as rule_notes
from (select 1 as _) x
left join active_rule ar on true
left join current_target ct on true
"""


def _iso(v: Any) -> str | None:
    return v.isoformat() if v is not None else None


class FinanceReadRepository:
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

    def mes(self) -> dict[str, Any]:
        """The 5 KPI cards for the Mes view plus pipeline ponderado by stage."""
        with self._read() as cur:
            cur.execute(_MES_SQL, {"default_target": _DEFAULT_TARGET_NET})
            names = [d.name for d in cur.description]
            row = dict(zip(names, cur.fetchone(), strict=True))

            # pipeline ponderado: fetch open cases and apply stage weights
            cur.execute(_PIPELINE_BY_STAGE_SQL)
            pipe_rows = cur.fetchall()

            # por_cobrar aging breakdown (IVA-inclusive)
            cur.execute(_POR_COBRAR_AGING_SQL)
            aging_rows = cur.fetchall()

            # monthly history (last 6 months)
            cur.execute(_MONTHLY_HISTORY_SQL)
            history_rows = cur.fetchall()

        pipeline_net = 0
        pipeline_by_stage: dict[str, dict[str, Any]] = {}
        for stage, amount_clp, opp_id, title in pipe_rows:
            w = _STAGE_WEIGHTS.get(stage, 0.0)
            # amount_clp is a numeric (Decimal) column and the weights are floats: Decimal * float raises.
            weighted = int(Decimal(amount_clp) * Decimal(str(w)))
            pipeline_net += weighted
            if stage not in pipeline_by_stage:
                pipeline_by_stage[stage] = {"weight": w, "count": 0, "total_clp": 0, "weighted_clp": 0}
            pipeline_by_stage[stage]["count"] += 1
            pipeline_by_stage[stage]["total_clp"] += int(amount_clp)
            pipeline_by_stage[stage]["weighted_clp"] += weighted

        by_age: dict[str, int] = {"0-30": 0, "31-60": 0, "61-90": 0, "90+": 0}
        for bucket, total_iva in aging_rows:
            if bucket in by_age:
                by_age[bucket] = int(total_iva)

        monthly_history = [{"month": r[0], "neto": int(r[1])} for r in history_rows]

        pct = (row["vendido_net"] / row["meta_net"] * 100) if row["meta_net"] > 0 else 0.0
        return {
            "mes": row["mes"],
            "today": row["today"],
            "vendido": {"net": row["vendido_net"], "meta_net": row["meta_net"], "pct_meta": round(pct, 1)},
            "por_cobrar": {
                "total": row["por_cobrar_total"],
                "count": row["por_cobrar_count"],
                "by_age": by_age,
            },
            "pipeline": {"ponderado_net": pipeline_net, "by_stage": pipeline_by_stage},
            "capital_atado": {"total": row["capital_atado_total"], "count": row["capital_atado_count"]},
            "gastos": {"total": row["gastos_total"], "count": row["gastos_count"]},
            "monthly_history": monthly_history,
        }

    def expenses(self, month: str | None = None) -> dict[str, Any]:
        """Expenses for a month (default: current Santiago month)."""
        with self._read() as cur:
            if month is None:
                cur.execute(
                    "select date_trunc('month', now() at time zone 'America/Santiago')::date::text"
                )
                month = cur.fetchone()[0]
            cur.execute(_EXPENSES_SQL, {"month": month})
            names = [d.name for d in cur.description]
            rows = [dict(zip(names, r, strict=True)) for r in cur.fetchall()]
        return {
            "month": month,
            "items": rows,
            "total_clp": sum(r["amount_clp"] for r in rows),
        }

    def supplier_orders(self, opportunity_id: str | None = None) -> dict[str, Any]:
        """Supplier orders, optionally filtered to one case."""
        with self._read() as cur:
            cur.execute(_SUPPLIER_ORDERS_SQL, {"opportunity_id": opportunity_id})
            names = [d.name for d in cur.description]
            rows = [dict(zip(names, r, strict=True)) for r in cur.fetchall()]
        in_flight = [r for r in rows if r["received_on"] is None]
        return {
            "items": rows,
            "in_flight_total": sum(r["amount_clp"] or 0 for r in in_flight),
            "in_flight_count": len(in_flight),
        }

    def settings(self) -> dict[str, Any]:
        """Active monthly target and partner rule."""
        with self._read() as cur:
            cur.execute(_SETTINGS_SQL, {"default_target": _DEFAULT_TARGET_NET})
            names = [d.name for d in cur.description]
            row = dict(zip(names, cur.fetchone(), strict=True))
        return {
            "monthly_target": {
                "net": row["latest_target_net"],
                "notes": row["target_notes"],
                "month": row["target_month"],
                "is_default": row["target_month"] is None,
            },
            "partner_rule": {
                "rule_name": row["rule_name"],
                "arriendo_clp": row["arriendo_clp"],
                "tasa_sebastian": float(row["tasa_sebastian"]) if row["tasa_sebastian"] is not None else None,
                "sueldo_tatiana": row["sueldo_tatiana"],
                "arriendo_pct_empresa": float(row["arriendo_pct_empresa"]) if row["arriendo_pct_empresa"] is not None else None,
                "split_pct": float(row["split_pct"]) if row["split_pct"] is not None else None,
                "valid_from": row["valid_from"],
                "notes": row["rule_notes"],
            } if row["rule_name"] is not None else None,
        }
