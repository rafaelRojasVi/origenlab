"""Read-only queries for the «Finanzas» module.

| Route | Does |
|---|---|
| `GET /v2/workspace/finance/mes` | 5 KPI cards for the Mes view (vendido, meta, por cobrar, pipeline, capital atado, gastos) |
| `GET /v2/workspace/finance/expenses` | expenses for a month (default: current Santiago month) |
| `GET /v2/workspace/finance/supplier-orders` | supplier orders, optionally filtered by opportunity |
| `GET /v2/workspace/finance/settings` | active monthly target + active partner rule |
| `GET /v2/workspace/finance/resumen` | Resumen v3: caja 30/60/90, meta 3-layer, indicadores, cobrar_hoy, pedidos_estimados |

«Vendido» = net_amount of crm.invoice doc_type 33/34 issued in the Santiago month, plus notas de
débito (56) minus notas de crédito (61). «Por cobrar» is the total balance of unpaid invoices (not
month-scoped). «Pipeline ponderado» is the sum of open-case amounts weighted by stage probability.
«Capital atado» is the sum of supplier orders in-flight (ordered but not yet received). «Gastos mes»
is the sum of crm.expense for the current month.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date, timedelta
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


_RESUMEN_MES_SQL = """
with
  month_bounds as (
    select
      date_trunc('month', now() at time zone 'America/Santiago')::date as month_start,
      (date_trunc('month', now() at time zone 'America/Santiago') + interval '1 month')::date as month_end,
      (now() at time zone 'America/Santiago')::date as today
  ),
  vendido as (
    select coalesce(sum(
      case i.doc_type
        when 33 then i.net_amount
        when 34 then i.net_amount
        when 56 then i.net_amount
        when 61 then -i.net_amount
        else 0
      end
    ), 0) as neto
    from crm.invoice i, month_bounds mb
    where i.issued_on >= mb.month_start and i.issued_on < mb.month_end
      and i.doc_type in (33, 34, 56, 61)
  ),
  target as (
    select sales_target_net
      from crm.monthly_target, month_bounds mb
     where month = mb.month_start
     limit 1
  ),
  -- all won cases (no month filter — intentional: shows the full uninvoiced backlog) with no factura
  ganado_sin_facturar as (
    select coalesce(sum(
      case when qr.quote_currency = 'CLP' then coalesce(qr.tax_base, 0) else 0 end
    ), 0) as net_total,
    count(*) as count
    from crm.opportunity o
    join crm.quote_revision qr on qr.quote_id = o.won_quote_id and qr.revision_no = o.won_revision_no
    cross join month_bounds mb
    where o.stage = 'won'
      and not exists (select 1 from crm.invoice inv where inv.opportunity_id = o.id and inv.doc_type in (33, 34))
  ),
  -- negotiating cases (quoting+negotiating stage): latest CLP revision
  en_negociacion as (
    select coalesce(sum(
      coalesce(
        (select case when qr.quote_currency = 'CLP' then coalesce(qr.tax_base, 0) else 0 end
           from crm.quote q
           join crm.quote_revision qr on qr.quote_id = q.id
          where q.opportunity_id = o.id and qr.status <> 'void' and qr.superseded_at is null
          order by qr.created_at desc limit 1),
        0
      )
    ), 0) as net_total,
    count(*) as count
    from crm.opportunity o
    where o.stage in ('negotiating')
  )
select
  mb.month_start::text as mes,
  mb.today::text as today,
  mb.month_end::text as month_end,
  v.neto as vendido_net,
  coalesce(t.sales_target_net, %(default_target)s) as meta_net,
  gsf.net_total as ganado_sf_net,
  gsf.count as ganado_sf_count,
  en.net_total as neg_net,
  en.count as neg_count
from month_bounds mb, vendido v, ganado_sin_facturar gsf, en_negociacion en
left join target t on true
"""

_INDICADORES_SQL = """
with
  month_bounds as (
    select
      date_trunc('month', now() at time zone 'America/Santiago')::date as month_start,
      (date_trunc('month', now() at time zone 'America/Santiago') + interval '1 month')::date as month_end
  ),
  -- avg payment days per client RUT (from paid facturas)
  avg_per_client as (
    select i.client_rut,
           avg(i.paid_on - i.issued_on)::numeric as avg_days,
           count(*) as n
      from crm.invoice i
     where i.doc_type in (33, 34) and i.paid_on is not null
     group by i.client_rut
  ),
  -- overall avg payment days (all paid facturas)
  overall_avg as (
    select coalesce(avg(i.paid_on - i.issued_on), 0)::numeric as avg_days,
           count(*) as n
      from crm.invoice i
     where i.doc_type in (33, 34) and i.paid_on is not null
  ),
  -- this month's won cases: cost and subtotal for margin.
  -- cost is aggregated once per revision via LATERAL; without it, a direct JOIN to quote_line would
  -- multiply tax_base by the line count (3 lines → subtotal 3×), inflating the margin percentage.
  margen_this_month as (
    select
      coalesce(sum(rev_cost.costo), 0) as costo,
      coalesce(sum(qr.tax_base), 0) as subtotal
    from crm.opportunity o
    join crm.quote_revision qr on qr.quote_id = o.won_quote_id and qr.revision_no = o.won_revision_no
    cross join month_bounds mb
    -- LATERAL aggregates item lines once per revision; tax_base then joins exactly once per won case
    left join lateral (
      select coalesce(sum(
        -- CLP lines have fx_rate = null; coalesce(null, 1) = 1, so no currency conversion is applied.
        -- Foreign-currency lines store the CLP-per-unit rate (same as engine.py _rate()).
        l.quantity * l.unit_cost * coalesce(l.fx_rate, 1)
      ), 0) as costo
        from crm.quote_line l
       where l.quote_revision_id = qr.id and l.line_kind = 'item'
    ) rev_cost on true
    where o.stage = 'won'
      and date(o.closed_at at time zone 'America/Santiago') >= mb.month_start
      and date(o.closed_at at time zone 'America/Santiago') < mb.month_end
      and qr.quote_currency = 'CLP'
  ),
  -- top 3 slow payers (by avg days on paid invoices)
  slow_payers as (
    select i.client_name,
           avg(i.paid_on - i.issued_on)::numeric as avg_days,
           count(*) as n
      from crm.invoice i
     where i.doc_type in (33, 34) and i.paid_on is not null
     group by i.client_name
     having count(*) >= 1
     order by avg(i.paid_on - i.issued_on) desc nulls last
     limit 3
  )
select
  oa.avg_days as dpc_avg,
  oa.n as dpc_count,
  mm.costo as margen_costo,
  mm.subtotal as margen_subtotal,
  (select json_agg(json_build_object('org', sp.client_name, 'days', round(sp.avg_days)::int, 'n', sp.n)
                   order by sp.avg_days desc)
     from slow_payers sp) as clientes_lentos
from overall_avg oa, margen_this_month mm
"""

_OPEN_INVOICES_SQL = """
with avg_days as (
  select i.client_rut,
         avg(i.paid_on - i.issued_on)::numeric as avg_d
    from crm.invoice i
   where i.doc_type in (33, 34) and i.paid_on is not null
   group by i.client_rut
),
today_cte as (
  select (now() at time zone 'America/Santiago')::date as today
)
select
  i.id::text as invoice_id, i.folio, i.issued_on::text, i.client_rut, i.client_name,
  i.total_amount,
  -- balance = total + nota adjustments - payments
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
  ) as balance_iva,
  round(coalesce(a.avg_d, 30))::int as avg_days_client,
  (i.issued_on + (coalesce(a.avg_d, 30))::int)::text as expected_pay_date,
  o.id::text as opportunity_id,
  o.title as opportunity_title,
  t.today::text as today
from crm.invoice i
cross join today_cte t
left join avg_days a on a.client_rut = i.client_rut
left join crm.opportunity o on o.id = i.opportunity_id
where i.doc_type in (33, 34) and i.paid_on is null
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
order by i.issued_on asc
"""

_PEDIDOS_ESTIMADOS_SQL = """
-- Won cases with no supplier order at all; estimated cost from principal item line
select
  o.id::text as opportunity_id,
  o.title,
  coalesce(sup.name, 'Proveedor desconocido') as supplier_name,
  coalesce(sup.id::text, null) as supplier_org_id,
  coalesce(
    (select sum(l.quantity * l.unit_cost * coalesce(l.fx_rate, 1))
       from crm.quote_line l
      where l.quote_revision_id = qr.id and l.line_kind = 'item'),
    0
  )::numeric as estimated_cost_clp
from crm.opportunity o
join crm.quote_revision qr on qr.quote_id = o.won_quote_id and qr.revision_no = o.won_revision_no
-- supplier from the first item line's supplier org
left join lateral (
  select l2.supplier_organization_id
    from crm.quote_line l2
   where l2.quote_revision_id = qr.id and l2.line_kind = 'item' and l2.supplier_organization_id is not null
   order by l2.line_no limit 1
) first_line on true
left join crm.organization sup on sup.id = first_line.supplier_organization_id
where o.stage = 'won'
  and not exists (select 1 from crm.supplier_order so where so.opportunity_id = o.id)
  -- exclude cases that already have a factura (doc_type 33/34): those have been invoiced
  -- and the supplier should have been paid already; they don't need an estimate here
  and not exists (select 1 from crm.invoice inv where inv.opportunity_id = o.id and inv.doc_type in (33, 34))
order by o.closed_at desc
limit 50
"""


def _business_days_left(today: date, month_end: date) -> int:
    """Count business days from today (inclusive of today if it's a weekday) to month_end (exclusive)."""
    count = 0
    d = today + timedelta(days=1)
    while d < month_end:
        if d.weekday() < 5:  # Monday–Friday
            count += 1
        d += timedelta(days=1)
    return count


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

        # Sums over numeric/bigint come back as Decimal, which the JSON encoder writes as a string;
        # the dashboard does arithmetic on these (`pct.toFixed`), so every amount leaves as a number.
        vendido_net, meta_net = int(row["vendido_net"]), int(row["meta_net"])
        pct = (vendido_net / meta_net * 100) if meta_net > 0 else 0.0
        return {
            "mes": row["mes"],
            "today": row["today"],
            "vendido": {"net": vendido_net, "meta_net": meta_net, "pct_meta": round(pct, 1)},
            "por_cobrar": {
                "total": int(row["por_cobrar_total"]),
                "count": int(row["por_cobrar_count"]),
                "by_age": by_age,
            },
            "pipeline": {"ponderado_net": pipeline_net, "by_stage": pipeline_by_stage},
            "capital_atado": {"total": int(row["capital_atado_total"]), "count": int(row["capital_atado_count"])},
            "gastos": {"total": int(row["gastos_total"]), "count": int(row["gastos_count"])},
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

    def resumen(self) -> dict[str, Any]:
        """Resumen v3: caja 30/60/90, meta 3-layer, indicadores, cobrar_hoy, pedidos_estimados."""
        with self._read() as cur:
            # 1. Meta section: vendido, ganado_sin_facturar, en_negociacion, meta
            cur.execute(_RESUMEN_MES_SQL, {"default_target": _DEFAULT_TARGET_NET})
            names = [d.name for d in cur.description]
            mes_row = dict(zip(names, cur.fetchone(), strict=True))

            # 2. Indicadores: avg payment days, margin, slow payers
            cur.execute(_INDICADORES_SQL)
            names2 = [d.name for d in cur.description]
            ind_row = dict(zip(names2, cur.fetchone(), strict=True))

            # 3. Open invoices (for caja calculation + cobrar_hoy)
            cur.execute(_OPEN_INVOICES_SQL)
            inv_names = [d.name for d in cur.description]
            open_invs = [dict(zip(inv_names, r, strict=True)) for r in cur.fetchall()]

            # 4. Supplier orders in-flight (for caja sale side)
            cur.execute(_SUPPLIER_ORDERS_SQL, {"opportunity_id": None})
            so_names = [d.name for d in cur.description]
            so_rows = [dict(zip(so_names, r, strict=True)) for r in cur.fetchall()]

            # 5. Pedidos estimados (won cases without supplier order)
            cur.execute(_PEDIDOS_ESTIMADOS_SQL)
            ped_names = [d.name for d in cur.description]
            ped_rows = [dict(zip(ped_names, r, strict=True)) for r in cur.fetchall()]

            # 6. Current-month expenses (for the caja sale-side: committed operational spend)
            cur.execute(_EXPENSES_SQL, {"month": mes_row["mes"]})
            exp_names = [d.name for d in cur.description]
            exp_rows = [dict(zip(exp_names, r, strict=True)) for r in cur.fetchall()]

        today_str = mes_row["today"]
        today_d = date.fromisoformat(today_str)
        month_end_d = date.fromisoformat(mes_row["month_end"])

        # ── caja: 30 / 60 / 90 day horizons ─────────────────────────────────
        IVA_RATE = Decimal("1.19")
        # sale_confirmado: confirmed supplier orders still in transit (no arrival date → all horizons)
        in_flight_total = sum(int(so["amount_clp"] or 0) for so in so_rows if so["received_on"] is None)
        sale_confirmado = in_flight_total
        # sale_estimado: won cases whose supplier purchase hasn't been registered yet
        #   + current-month operational expenses already committed
        pedidos_est_total = sum(int(r["estimated_cost_clp"]) for r in ped_rows)
        gastos_mes_total = sum(int(r["amount_clp"]) for r in exp_rows)
        sale_estimado = pedidos_est_total + gastos_mes_total
        # ganado_sin_facturar as a future invoice (IVA-inclusive at ~60d)
        gsf_iva = int(Decimal(str(mes_row["ganado_sf_net"])) * IVA_RATE)

        caja = []
        for horizon in (30, 60, 90):
            cutoff = today_d + timedelta(days=horizon)
            until = cutoff.isoformat()
            # entra: open invoices with expected pay date within horizon
            entra_inv = sum(
                int(inv["balance_iva"])
                for inv in open_invs
                if date.fromisoformat(inv["expected_pay_date"]) <= cutoff
            )
            # At 60+ d, add won-not-invoiced expected revenue (IVA-inclusive)
            entra_gsf = gsf_iva if horizon >= 60 else 0
            entra = entra_inv + entra_gsf
            # sale = all known outflows: confirmed orders + estimated cost + monthly expenses
            sale = sale_confirmado + sale_estimado
            neto = entra - sale
            caja.append({
                "horizon": horizon,
                "until": until,
                "entra": entra,
                "sale": sale,
                "sale_confirmado": sale_confirmado,
                "sale_estimado": sale_estimado,
                "neto": neto,
            })

        # ── meta section ─────────────────────────────────────────────────────
        vendido_net = int(mes_row["vendido_net"])
        meta_net = int(mes_row["meta_net"])
        gsf_net = int(mes_row["ganado_sf_net"])
        neg_net = int(mes_row["neg_net"])
        dias_habiles = _business_days_left(today_d, month_end_d)
        faltan = meta_net - vendido_net
        ritmo = int(faltan / dias_habiles) if dias_habiles > 0 else 0
        meta = {
            "facturado_net": vendido_net,
            "ganado_sin_facturar_net": gsf_net,
            "ganado_sin_facturar_count": int(mes_row["ganado_sf_count"]),
            "en_negociacion_net": neg_net,
            "en_negociacion_count": int(mes_row["neg_count"]),
            "meta_net": meta_net,
            "dias_habiles_restantes": dias_habiles,
            "ritmo_necesario": ritmo,
            "mes": mes_row["mes"],
        }

        # ── indicadores ───────────────────────────────────────────────────────
        dpc_avg = float(ind_row["dpc_avg"]) if ind_row["dpc_avg"] else 0.0
        margen_costo = int(ind_row["margen_costo"])
        margen_subtotal = int(ind_row["margen_subtotal"])
        margen_pct = round((1 - margen_costo / margen_subtotal) * 100, 1) if margen_subtotal > 0 else 0.0
        clientes_lentos_raw = ind_row["clientes_lentos"] or []
        clientes_lentos = [
            {
                "org": r["org"],
                "days": int(r["days"]),
                "hint": "pedir 50 % de anticipo" if int(r["days"]) > 30 else None,
            }
            for r in clientes_lentos_raw
        ]
        indicadores = {
            "dias_promedio_cobro": round(dpc_avg, 1),
            "dias_promedio_cobro_count": int(ind_row["dpc_count"]),
            "margen_mes_pct": margen_pct,
            "margen_mes_costo": margen_costo,
            "margen_mes_subtotal": margen_subtotal,
            "clientes_lentos": clientes_lentos,
        }

        # ── ¿A quién cobrar hoy? ─────────────────────────────────────────────
        # Prioritise oldest invoices (issued_on asc already, so oldest first)
        cobrar_hoy = [
            {
                "invoice_id": inv["invoice_id"],
                "folio": inv["folio"],
                "issued_on": inv["issued_on"],
                "client_name": inv["client_name"],
                "balance_iva": int(inv["balance_iva"]),
                "avg_days_client": int(inv["avg_days_client"]),
                "expected_pay_date": inv["expected_pay_date"],
                "age_days": (today_d - date.fromisoformat(inv["issued_on"])).days,
                "opportunity_id": inv["opportunity_id"],
                "opportunity_title": inv["opportunity_title"],
            }
            for inv in open_invs[:3]  # top 3 oldest
        ]

        # ── pedidos_estimados ─────────────────────────────────────────────────
        pedidos_estimados = [
            {
                "opportunity_id": r["opportunity_id"],
                "title": r["title"],
                "supplier_name": r["supplier_name"],
                "supplier_org_id": r["supplier_org_id"],
                "estimated_cost_clp": int(r["estimated_cost_clp"]),
            }
            for r in ped_rows
        ]

        return {
            "caja": caja,
            "meta": meta,
            "indicadores": indicadores,
            "cobrar_hoy": cobrar_hoy,
            "pedidos_estimados": pedidos_estimados,
        }
