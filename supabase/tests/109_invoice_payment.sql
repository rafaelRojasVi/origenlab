-- Slice 7 — «Pagos» abonos: crm.invoice_payment (DOMAIN.md §7, #71).
-- Fictitious fixtures only.
begin;
create extension if not exists pgtap with schema extensions;
select plan(11);

grant origenlab_api    to session_user with set true, inherit false;
grant origenlab_worker to session_user with set true, inherit false;

create function pg_temp.run_as(p_role text, p_sql text) returns text
language plpgsql as $f$
begin
  execute format('set role %I', p_role);
  execute p_sql;
  reset role;
  return 'ok';
exception when others then
  reset role;
  return sqlstate || ': ' || sqlerrm;
end
$f$;

-- ── shape ────────────────────────────────────────────────────────────────────────────────────
select has_table('crm', 'invoice_payment', 'crm.invoice_payment exists (#71)');
select is(
  (select count(*)::int from pg_class c join pg_namespace n on n.oid = c.relnamespace
    where c.relkind = 'r' and n.nspname = 'crm' and c.relname = 'invoice_payment'
      and c.relrowsecurity and not c.relforcerowsecurity),
  1, 'RLS is enabled and not forced on crm.invoice_payment');

-- ── fixtures: an operator and a factura ──────────────────────────────────────────────────────
set local role origenlab_owner;
insert into platform.operator (id, auth_user_id, email_norm, display_name, role, status) values
  ('99000000-0000-4000-9900-000000000801', '99000000-0000-4000-9900-0000000008f1',
   'abonos.operator@ficticio.invalid', 'Operador Ficticio', 'sales', 'active');
insert into crm.invoice (id, doc_type, folio, issued_on, client_rut, client_name, net_amount, vat_amount, total_amount)
  values ('99000000-0000-4000-9900-000000000810', 33, 9101, current_date, '12345678-5',
          'Instituto Ficticio', 100000, 19000, 119000);
reset role;

-- ── api ──────────────────────────────────────────────────────────────────────────────────────
select is(pg_temp.run_as('origenlab_api', $$ insert into crm.invoice_payment
    (id, invoice_id, paid_on, amount, recorded_by_operator_id)
  values ('99000000-0000-4000-9900-000000000820', '99000000-0000-4000-9900-000000000810', current_date, 59500,
          '99000000-0000-4000-9900-000000000801') $$), 'ok',
  'the API records half of a factura as paid');
select is(left(pg_temp.run_as('origenlab_api', $$ insert into crm.invoice_payment
    (invoice_id, paid_on, amount, recorded_by_operator_id)
  values ('99000000-0000-4000-9900-000000000810', current_date, 0, '99000000-0000-4000-9900-000000000801') $$), 5),
  '23514', 'a payment of zero is refused');
select is(left(pg_temp.run_as('origenlab_api', $$ update crm.invoice_payment set amount = 1
  where id = '99000000-0000-4000-9900-000000000820' $$), 5),
  '42501', 'the API cannot rewrite a payment''s amount');
select is(left(pg_temp.run_as('origenlab_api', $$ update crm.invoice_payment set removed_at = now()
  where id = '99000000-0000-4000-9900-000000000820' $$), 5),
  '23514', 'a removal must say who removed it');
select is(pg_temp.run_as('origenlab_api', $$ update crm.invoice_payment
  set removed_at = now(), removed_by_operator_id = '99000000-0000-4000-9900-000000000801'
  where id = '99000000-0000-4000-9900-000000000820' $$), 'ok',
  'the API removes a wrong payment, saying who');
select is(left(pg_temp.run_as('origenlab_api', $$ delete from crm.invoice_payment
  where id = '99000000-0000-4000-9900-000000000820' $$), 5),
  '42501', 'the API holds no DELETE');
select is(left(pg_temp.run_as('origenlab_owner', $$ delete from crm.invoice_payment
  where id = '99000000-0000-4000-9900-000000000820' $$), 5), 'P0001',
  'a payment is never deleted, not even by the owner (trigger)');

-- ── worker ───────────────────────────────────────────────────────────────────────────────────
select is(left(pg_temp.run_as('origenlab_worker', $$ select 1 from crm.invoice_payment $$), 5),
  '42501', 'the worker cannot read payments');
select is(
  (select count(*)::int from crm.invoice_payment where invoice_id = '99000000-0000-4000-9900-000000000810'),
  1, 'the removed payment stays on record');

select * from finish();
rollback;
