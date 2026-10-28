-- Slice 7 — «Pagos»: crm.invoice (DOMAIN.md §7, #70) and the invoice.* audit events.
-- Fictitious fixtures only.
begin;
create extension if not exists pgtap with schema extensions;
select plan(16);

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
select has_table('crm', 'invoice', 'crm.invoice exists (#70)');
select is(
  (select count(*)::int from pg_class c join pg_namespace n on n.oid = c.relnamespace
    where c.relkind = 'r' and n.nspname = 'crm' and c.relname = 'invoice'
      and c.relrowsecurity and not c.relforcerowsecurity),
  1, 'RLS is enabled and not forced on crm.invoice');

-- ── fixtures: an operator and a case ─────────────────────────────────────────────────────────
set local role origenlab_owner;
insert into platform.operator (id, auth_user_id, email_norm, display_name, role, status) values
  ('99000000-0000-4000-9900-000000000701', '99000000-0000-4000-9900-0000000007f1',
   'pagos.operator@ficticio.invalid', 'Operador Ficticio', 'sales', 'active');
insert into crm.opportunity (id, title, stage, owner_operator_id) values
  ('99000000-0000-4000-9900-000000000750', 'Caso facturado sintético', 'lead',
   '99000000-0000-4000-9900-000000000701');
reset role;

-- ── api: insert ──────────────────────────────────────────────────────────────────────────────
select is(pg_temp.run_as('origenlab_api', $$ insert into crm.invoice
    (id, doc_type, folio, issued_on, client_rut, client_name, net_amount, vat_amount, total_amount)
  values ('99000000-0000-4000-9900-000000000710', 33, 9001, current_date, '12345678-5',
          'Instituto Ficticio', 100000, 19000, 119000) $$), 'ok',
  'the API inserts a factura with no case');
select is(pg_temp.run_as('origenlab_api', $$ insert into crm.invoice
    (id, doc_type, folio, issued_on, client_rut, client_name, net_amount, vat_amount, total_amount,
     reference_doc_type, reference_folio)
  values ('99000000-0000-4000-9900-000000000711', 61, 9002, current_date, '12345678-5',
          'Instituto Ficticio', 10000, 1900, 11900, 33, 9001) $$), 'ok',
  'the API inserts a nota de crédito that references the factura');
select is((select version from crm.invoice where id = '99000000-0000-4000-9900-000000000710'), 1,
  'a new invoice is at version 1');

-- ── constraints ──────────────────────────────────────────────────────────────────────────────
select is(left(pg_temp.run_as('origenlab_api', $$ insert into crm.invoice
    (doc_type, folio, issued_on, client_rut, client_name, total_amount)
  values (33, 9001, current_date, '12345678-5', 'Instituto Ficticio', 1) $$), 5), '23505',
  'the same document type and folio cannot be imported twice');
select is(left(pg_temp.run_as('origenlab_api', $$ insert into crm.invoice
    (doc_type, folio, issued_on, client_rut, client_name, total_amount)
  values (39, 9003, current_date, '12345678-5', 'Instituto Ficticio', 1) $$), 5), '23514',
  'an unknown document type (a boleta) is rejected');
select is(left(pg_temp.run_as('origenlab_api', $$ insert into crm.invoice
    (doc_type, folio, issued_on, client_rut, client_name, total_amount)
  values (33, 9004, current_date, '12345678-5', 'Instituto Ficticio', -1) $$), 5), '23514',
  'a negative amount is rejected');
select is(left(pg_temp.run_as('origenlab_api', $$ update crm.invoice
    set opportunity_id = '99000000-0000-4000-9900-000000000750'
  where id = '99000000-0000-4000-9900-000000000710' $$), 5), '23514',
  'a case without saying who attached it is rejected');
select is(left(pg_temp.run_as('origenlab_api', $$ update crm.invoice set paid_on = current_date
  where id = '99000000-0000-4000-9900-000000000711' $$), 5), '23514',
  'a nota de crédito cannot be marked paid');

-- ── api: attach a case and mark paid ─────────────────────────────────────────────────────────
select is(pg_temp.run_as('origenlab_api', $$ update crm.invoice
    set opportunity_id = '99000000-0000-4000-9900-000000000750', case_link = 'operator',
        paid_on = current_date, version = version + 1, updated_at = now()
  where id = '99000000-0000-4000-9900-000000000710' $$), 'ok',
  'the API attaches a case and marks the factura paid');

-- ── never physically deleted ─────────────────────────────────────────────────────────────────
select is(left(pg_temp.run_as('origenlab_api', $$ delete from crm.invoice
  where id = '99000000-0000-4000-9900-000000000711' $$), 5), '42501',
  'the API holds no DELETE privilege on crm.invoice');
select is(left(pg_temp.run_as('origenlab_owner', $$ delete from crm.invoice
  where id = '99000000-0000-4000-9900-000000000711' $$), 5), 'P0001',
  'even the owner cannot delete an invoice (trigger)');

-- ── access ───────────────────────────────────────────────────────────────────────────────────
select is(left(pg_temp.run_as('origenlab_worker', $$ select count(*) from crm.invoice $$), 5), '42501',
  'the worker has no access to crm.invoice');
select is(left(pg_temp.run_as('anon', $$ select count(*) from crm.invoice $$), 5), '42501',
  'anon is denied crm.invoice');

-- ── events ───────────────────────────────────────────────────────────────────────────────────
select is(pg_temp.run_as('origenlab_api', $$ insert into crm.domain_event
    (aggregate_kind, aggregate_id, seq, event_type, payload_version, payload, actor_kind, actor_operator_id)
  values ('invoice', '99000000-0000-4000-9900-000000000710', 1, 'invoice.imported', 1, '{}',
          'operator', '99000000-0000-4000-9900-000000000701') $$), 'ok',
  'an invoice.imported event on aggregate kind invoice is accepted');

select * from finish();
rollback;
