-- Slice 9 — «Finanzas»: the four finance tables live in the existing `crm` schema
-- (DOMAIN.md §8, #73–#76). V2 is fixed at seven private schemas; a new schema cannot be
-- added without granting CREATE ON DATABASE, which the release parser disallows.
--
--   * #73 crm.expense — one manually-entered operating expense for a calendar month (category,
--     amount_clp, paid_by, notes); source text default 'manual' and external_ref text null so a
--     future SII purchase-document importer can land here as a suggestion later. Never physically
--     deleted: a wrong entry is corrected by inserting a corrective row or updating the existing
--     one (soft-replace).
--   * #74 crm.supplier_order — one order placed to a supplier for a case/quote: supplier_name,
--     ordered_on, paid_on, amount_original, currency, amount_clp (CLP equivalent),
--     expected_arrival_on, received_on, notes. Tracks capital atado and Por pagar.
--   * #75 crm.monthly_target — the monthly sales target (meta neto, editable). One row per
--     calendar month (PRIMARY KEY month date). The app reads the latest row when no row exists
--     for the current month; a default of $6 M is applied in the API when the table is empty.
--   * #76 crm.partner_rule — the partner distribution rule (50/50, opción A–D): one append-only
--     row per rule change; the rule in force is the row with the greatest valid_from.

set role origenlab_owner;

-- ── #73 crm.expense ───────────────────────────────────────────────────────────────────────────
create table crm.expense (
  id                      uuid primary key default gen_random_uuid(),
  month                   date not null,  -- first day of the month (e.g. 2026-11-01)
  category                text not null,
  amount_clp              integer not null,
  paid_by                 text not null default 'empresa',
  notes                   text,
  -- SII purchase-document integration: 'manual' now; a future importer lands as 'sii_purchase'
  source                  text not null default 'manual',
  -- external reference: future SII document identifier (tipo DTE + folio); null for manual entries
  external_ref            text,
  supplier_rut            text,
  created_at              timestamptz not null default now(),
  updated_at              timestamptz not null default now(),
  created_by_operator_id  uuid references platform.operator(id),
  constraint expense_month_first_day check (extract(day from month) = 1),
  constraint expense_category_check check (category in (
    'arriendo', 'servidores', 'contador', 'telefono', 'software',
    'viajes', 'sueldos', 'honorarios', 'otros'
  )),
  constraint expense_amount_positive check (amount_clp > 0),
  constraint expense_paid_by_check check (paid_by in ('empresa', 'tatiana', 'sebastian')),
  constraint expense_source_check check (source in ('manual', 'sii_purchase'))
);

comment on table crm.expense is
  'DOMAIN.md §8 #73 — one operating expense for a calendar month: category, amount_clp, paid_by. '
  'source=''manual'' by default; source=''sii_purchase'' + external_ref when a future SII purchase '
  'importer lands here. Soft-replaced, never physically deleted.';
comment on column crm.expense.month is 'First day of the calendar month (extract(day)=1).';
comment on column crm.expense.source is
  'Entry origin: ''manual'' (operator typed it) or ''sii_purchase'' (future SII importer). '
  'external_ref carries the SII document identifier when source=''sii_purchase''.';

create index expense_month_idx on crm.expense (month);
create index expense_created_by_idx on crm.expense (created_by_operator_id) where created_by_operator_id is not null;

-- ── grants & RLS: expense ────────────────────────────────────────────────────────────────────
revoke all on crm.expense from public, anon, authenticated, service_role;
grant select, insert, update on crm.expense to origenlab_api;

alter table crm.expense enable row level security;

create policy origenlab_api_select on crm.expense for select to origenlab_api using (true);
create policy origenlab_api_insert on crm.expense for insert to origenlab_api with check (true);
create policy origenlab_api_update on crm.expense for update to origenlab_api using (true) with check (true);

-- ── #74 crm.supplier_order ────────────────────────────────────────────────────────────────────
create table crm.supplier_order (
  id                   uuid primary key default gen_random_uuid(),
  opportunity_id       uuid references crm.opportunity(id),
  quote_id             uuid references crm.quote(id),
  supplier_name        text not null,
  ordered_on           date not null,
  paid_on              date,
  amount_original      numeric(15,2) not null,
  currency             char(3) not null default 'CLP',
  -- CLP equivalent; computed by caller when currency != CLP; null until set
  amount_clp           integer,
  expected_arrival_on  date,
  received_on          date,
  notes                text,
  created_at           timestamptz not null default now(),
  updated_at           timestamptz not null default now(),
  created_by_operator_id uuid references platform.operator(id),
  constraint supplier_order_amount_positive check (amount_original > 0),
  constraint supplier_order_currency_len check (char_length(currency) = 3),
  -- Written NULL-proof: a CHECK that evaluates to NULL passes
  constraint supplier_order_clp_shape check (currency = 'CLP' or amount_clp is null or amount_clp > 0),
  constraint supplier_order_arrival_shape check (
    expected_arrival_on is null or expected_arrival_on >= ordered_on
  ),
  constraint supplier_order_received_shape check (
    received_on is null or received_on >= ordered_on
  )
);

comment on table crm.supplier_order is
  'DOMAIN.md §8 #74 — one order placed to a supplier for a case/quote: supplier_name, ordered_on, '
  'paid_on, amount in original currency and CLP equivalent, expected_arrival_on, received_on. '
  'Tracks capital atado (unpaid + undelivered orders) and «Por pagar y pedidos».';

create index supplier_order_opportunity_idx on crm.supplier_order (opportunity_id) where opportunity_id is not null;
create index supplier_order_quote_idx on crm.supplier_order (quote_id) where quote_id is not null;
create index supplier_order_ordered_idx on crm.supplier_order (ordered_on);

-- ── grants & RLS: supplier_order ─────────────────────────────────────────────────────────────
revoke all on crm.supplier_order from public, anon, authenticated, service_role;
grant select, insert, update on crm.supplier_order to origenlab_api;

alter table crm.supplier_order enable row level security;

create policy origenlab_api_select on crm.supplier_order for select to origenlab_api using (true);
create policy origenlab_api_insert on crm.supplier_order for insert to origenlab_api with check (true);
create policy origenlab_api_update on crm.supplier_order for update to origenlab_api using (true) with check (true);

-- ── #75 crm.monthly_target ───────────────────────────────────────────────────────────────────
create table crm.monthly_target (
  month            date primary key,  -- first day of the month
  sales_target_net integer not null,
  notes            text,
  set_by_operator_id uuid references platform.operator(id),
  created_at       timestamptz not null default now(),
  constraint monthly_target_month_first_day check (extract(day from month) = 1),
  constraint monthly_target_positive check (sales_target_net > 0)
);

comment on table crm.monthly_target is
  'DOMAIN.md §8 #75 — the monthly net-sales target (meta neto). One row per calendar month '
  '(PRIMARY KEY month). The API returns the latest row when the current month has no row; '
  'the application default is $6,000,000. Set by an admin from Ajustes › Meta mensual.';

-- ── grants & RLS: monthly_target ─────────────────────────────────────────────────────────────
revoke all on crm.monthly_target from public, anon, authenticated, service_role;
grant select, insert, update on crm.monthly_target to origenlab_api;

alter table crm.monthly_target enable row level security;

create policy origenlab_api_select on crm.monthly_target for select to origenlab_api using (true);
create policy origenlab_api_insert on crm.monthly_target for insert to origenlab_api with check (true);
create policy origenlab_api_update on crm.monthly_target for update to origenlab_api using (true) with check (true);

-- ── #76 crm.partner_rule ─────────────────────────────────────────────────────────────────────
-- Append-only: the rule in force is the row with the greatest valid_from.
-- Never physically deleted: use a new row to change the rule.
create table crm.partner_rule (
  id                      serial primary key,
  valid_from              date not null,
  rule_name               text not null,
  -- arriendo paid by Tatiana; relevant to split calculation
  arriendo_clp            integer not null default 875000,
  -- NULL when Sebastian is not acting as a lender (Opción D)
  tasa_sebastian          numeric(5,4),
  -- monthly salary paid to Tatiana before the split (Opción C)
  sueldo_tatiana          integer not null default 0,
  -- fraction of arriendo treated as a company cost before the 50/50 split (0.0 = none, 0.5 = half, 1.0 = all)
  arriendo_pct_empresa    numeric(3,2) not null default 0,
  -- each partner's fraction of the residual (default 0.50 = 50/50)
  split_pct               numeric(5,4) not null default 0.5000,
  notes                   text,
  set_by_operator_id      uuid references platform.operator(id),
  created_at              timestamptz not null default now(),
  constraint partner_rule_rule_name_check check (rule_name in (
    'actual_50_50', 'opcion_a', 'opcion_b', 'opcion_c', 'opcion_d'
  )),
  constraint partner_rule_arriendo_positive check (arriendo_clp > 0),
  constraint partner_rule_sueldo_nonneg check (sueldo_tatiana >= 0),
  constraint partner_rule_arriendo_pct_range check (arriendo_pct_empresa between 0 and 1),
  constraint partner_rule_split_pct_range check (split_pct between 0 and 1)
);

comment on table crm.partner_rule is
  'DOMAIN.md §8 #76 — partner distribution rule (append-only). The rule in force is the row with '
  'the greatest valid_from. rule_name: actual_50_50 (current) or opciones A–D from the strategy. '
  'Admin-only; set from Ajustes › Regla de socios.';

create trigger partner_rule_never_deleted
  before delete on crm.partner_rule
  for each row execute function platform.reject_mutation('append-only: insert a new row to change the rule');

-- No seed row: when crm.partner_rule is empty the API defaults to the fifty_fifty rule
-- (actual_50_50 / 50–50 split). An admin sets the first row from Ajustes › Regla de socios.

-- ── grants & RLS: partner_rule ───────────────────────────────────────────────────────────────
revoke all on crm.partner_rule from public, anon, authenticated, service_role;
grant select, insert on crm.partner_rule to origenlab_api;

alter table crm.partner_rule enable row level security;

create policy origenlab_api_select on crm.partner_rule for select to origenlab_api using (true);
create policy origenlab_api_insert on crm.partner_rule for insert to origenlab_api with check (true);

-- grant partner_rule sequences too (serial)
grant usage, select on sequence crm.partner_rule_id_seq to origenlab_api;
revoke all on sequence crm.partner_rule_id_seq from public, anon, authenticated, service_role;

reset role;
