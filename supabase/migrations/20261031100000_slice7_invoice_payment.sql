-- Slice 7 — «Pagos»: a factura paid in parts (DOMAIN.md §7, #71).
--
-- Owner 2026-10-28: «already paid half — show how much was paid, and add up in parts». One factura
-- may be paid in several payments (abonos), each with its own day and amount; it is «Pagada» when
-- the live payments reach its saldo.
--
--   * #71 crm.invoice_payment — one payment an operator recorded against a factura: paid_on, amount
--     (CLP, > 0), who recorded it and when. Never deleted, never rewritten: a payment recorded by
--     mistake is removed once (removed_at, removed_by_operator_id; the API's only UPDATE). The API
--     sets crm.invoice.paid_on to the day the payments reach the saldo, and clears it when a removal
--     takes them below. Events: the existing invoice.paid / invoice.unpaid on the invoice aggregate,
--     with the payment in the payload — no new aggregate kind or event type.
--   * removed_by_operator_id carries no foreign key, as crm.person_name's does not: an index for a
--     column almost always null buys nothing, and the event names the operator.

set role origenlab_owner;

create table crm.invoice_payment (
  id uuid primary key default gen_random_uuid(),
  invoice_id uuid not null references crm.invoice (id),
  paid_on date not null,
  amount bigint not null,
  recorded_by_operator_id uuid not null references platform.operator (id),
  created_at timestamptz not null default now(),
  removed_at timestamptz,
  removed_by_operator_id uuid,
  constraint invoice_payment_amount_positive check (amount > 0),
  -- Written NULL-proof: a CHECK that evaluates to NULL passes.
  constraint invoice_payment_removal_shape check ((removed_at is null) = (removed_by_operator_id is null))
);

comment on table crm.invoice_payment is
  'DOMAIN.md §7 #71 — one payment (abono) an operator recorded against a crm.invoice factura: paid_on, amount (CLP > 0), who recorded it. The factura is «Pagada» when its live payments reach its saldo. Never deleted; a wrong payment is removed once (removed_at, removed_by_operator_id).';

create index invoice_payment_invoice_idx on crm.invoice_payment (invoice_id);
create index invoice_payment_recorded_by_idx on crm.invoice_payment (recorded_by_operator_id);

create trigger invoice_payment_never_deleted
  before delete on crm.invoice_payment
  for each row execute function platform.reject_mutation('never deleted (remove a payment via removed_at)');

-- ── grants ───────────────────────────────────────────────────────────────────────────────────
revoke all on crm.invoice_payment from public, anon, authenticated, service_role;
grant select, insert on crm.invoice_payment to origenlab_api;
grant update (removed_at, removed_by_operator_id) on crm.invoice_payment to origenlab_api;

-- ── RLS ──────────────────────────────────────────────────────────────────────────────────────
alter table crm.invoice_payment enable row level security;

create policy origenlab_api_select on crm.invoice_payment for select to origenlab_api using (true);
create policy origenlab_api_insert on crm.invoice_payment for insert to origenlab_api with check (true);
create policy origenlab_api_update on crm.invoice_payment for update to origenlab_api using (true) with check (true);

reset role;
