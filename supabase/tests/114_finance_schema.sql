-- Slice 9 — «Finanzas»: crm.expense, crm.supplier_order, crm.monthly_target,
-- crm.partner_rule (DOMAIN.md §8, #73–#76). Tables live in `crm`, not a new schema.
-- Fictitious fixtures only — no real client names, RUTs, amounts or company data.
begin;
create extension if not exists pgtap with schema extensions;
select plan(39);

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

-- ── fixtures ─────────────────────────────────────────────────────────────────────────────────
set local role origenlab_owner;
insert into platform.operator (id, auth_user_id, email_norm, display_name, role, status) values
  ('99000000-0000-4000-9900-000000001001', '99000000-0000-4000-9900-00000001f001',
   'finanzas.operator@ficticio.invalid', 'Operador Finanzas', 'admin', 'active');
insert into crm.opportunity (id, title, stage, owner_operator_id) values
  ('99000000-0000-4000-9900-000000001050', 'Caso de prueba finanzas', 'lead',
   '99000000-0000-4000-9900-000000001001');
reset role;

-- ── expense: shape ───────────────────────────────────────────────────────────────────────────
select has_table('crm', 'expense', 'crm.expense exists (#73)');
select is(
  (select count(*)::int from pg_class c join pg_namespace n on n.oid = c.relnamespace
    where c.relkind = 'r' and n.nspname = 'crm' and c.relname = 'expense'
      and c.relrowsecurity and not c.relforcerowsecurity),
  1, 'RLS is enabled and not forced on crm.expense');

-- ── expense: api inserts ─────────────────────────────────────────────────────────────────────
select is(pg_temp.run_as('origenlab_api', $$ insert into crm.expense
    (id, month, category, amount_clp, paid_by, created_by_operator_id)
  values ('99000000-0000-4000-9900-000000001101', '2026-11-01', 'servidores', 38500, 'empresa',
          '99000000-0000-4000-9900-000000001001') $$), 'ok',
  'the API inserts a manual expense');
select is(pg_temp.run_as('origenlab_api', $$ insert into crm.expense
    (id, month, category, amount_clp, paid_by, source, external_ref, supplier_rut)
  values ('99000000-0000-4000-9900-000000001102', '2026-11-01', 'otros', 10000, 'empresa',
          'sii_purchase', '76543210-9', '12345678-5') $$), 'ok',
  'the API inserts an sii_purchase expense with external_ref');

-- ── expense: constraints ─────────────────────────────────────────────────────────────────────
select is(left(pg_temp.run_as('origenlab_api', $$ insert into crm.expense
    (month, category, amount_clp, paid_by) values ('2026-11-15', 'servidores', 100, 'empresa') $$), 5),
  '23514', 'month must be the first day of the month');
select is(left(pg_temp.run_as('origenlab_api', $$ insert into crm.expense
    (month, category, amount_clp, paid_by) values ('2026-11-01', 'banco', 100, 'empresa') $$), 5),
  '23514', 'unknown category is rejected');
select is(left(pg_temp.run_as('origenlab_api', $$ insert into crm.expense
    (month, category, amount_clp, paid_by) values ('2026-11-01', 'servidores', 0, 'empresa') $$), 5),
  '23514', 'amount_clp = 0 is rejected');
select is(left(pg_temp.run_as('origenlab_api', $$ insert into crm.expense
    (month, category, amount_clp, paid_by) values ('2026-11-01', 'servidores', 100, 'gerente') $$), 5),
  '23514', 'unknown paid_by is rejected');

-- ── expense: worker denied ───────────────────────────────────────────────────────────────────
select is(left(pg_temp.run_as('origenlab_worker', $$ select count(*) from crm.expense $$), 5),
  '42501', 'worker has no access to crm.expense');

-- ── supplier_order: shape ────────────────────────────────────────────────────────────────────
select has_table('crm', 'supplier_order', 'crm.supplier_order exists (#74)');
select is(
  (select count(*)::int from pg_class c join pg_namespace n on n.oid = c.relnamespace
    where c.relkind = 'r' and n.nspname = 'crm' and c.relname = 'supplier_order'
      and c.relrowsecurity and not c.relforcerowsecurity),
  1, 'RLS is enabled and not forced on crm.supplier_order');

-- ── supplier_order: api inserts ──────────────────────────────────────────────────────────────
select is(pg_temp.run_as('origenlab_api', $$ insert into crm.supplier_order
    (id, opportunity_id, supplier_name, ordered_on, amount_original, currency, amount_clp,
     expected_arrival_on, created_by_operator_id)
  values ('99000000-0000-4000-9900-000000001201', '99000000-0000-4000-9900-000000001050',
          'Proveedor Ficticio SA', '2026-11-01', 5000.00, 'USD', 4925000, '2026-11-30',
          '99000000-0000-4000-9900-000000001001') $$), 'ok',
  'the API inserts a supplier order in USD');
select is(pg_temp.run_as('origenlab_api', $$ insert into crm.supplier_order
    (id, supplier_name, ordered_on, amount_original, currency, created_by_operator_id)
  values ('99000000-0000-4000-9900-000000001202', 'Proveedor Local Ltda', '2026-11-01',
          1200000, 'CLP', '99000000-0000-4000-9900-000000001001') $$), 'ok',
  'the API inserts a CLP supplier order without a case');

-- ── supplier_order: constraints ──────────────────────────────────────────────────────────────
select is(left(pg_temp.run_as('origenlab_api', $$ insert into crm.supplier_order
    (supplier_name, ordered_on, amount_original, currency)
  values ('X', '2026-11-01', 0, 'CLP') $$), 5), '23514', 'amount_original = 0 is rejected');
select is(left(pg_temp.run_as('origenlab_api', $$ insert into crm.supplier_order
    (supplier_name, ordered_on, amount_original, currency, expected_arrival_on)
  values ('X', '2026-11-01', 100, 'CLP', '2026-10-31') $$), 5), '23514',
  'expected_arrival_on before ordered_on is rejected');

-- ── supplier_order: update ───────────────────────────────────────────────────────────────────
select is(pg_temp.run_as('origenlab_api', $$ update crm.supplier_order
  set received_on = '2026-11-28', updated_at = now()
  where id = '99000000-0000-4000-9900-000000001201' $$), 'ok',
  'the API marks an order as received');

-- ── supplier_order: worker denied ────────────────────────────────────────────────────────────
select is(left(pg_temp.run_as('origenlab_worker', $$ select 1 from crm.supplier_order $$), 5),
  '42501', 'worker has no access to crm.supplier_order');

-- ── monthly_target: shape ────────────────────────────────────────────────────────────────────
select has_table('crm', 'monthly_target', 'crm.monthly_target exists (#75)');
select is(
  (select count(*)::int from pg_class c join pg_namespace n on n.oid = c.relnamespace
    where c.relkind = 'r' and n.nspname = 'crm' and c.relname = 'monthly_target'
      and c.relrowsecurity and not c.relforcerowsecurity),
  1, 'RLS is enabled and not forced on crm.monthly_target');

-- ── monthly_target: api inserts ──────────────────────────────────────────────────────────────
select is(pg_temp.run_as('origenlab_api', $$ insert into crm.monthly_target
    (month, sales_target_net, notes, set_by_operator_id)
  values ('2026-11-01', 6000000, 'Meta acordada nov 2026',
          '99000000-0000-4000-9900-000000001001') $$), 'ok',
  'the API inserts the November target');
select is(pg_temp.run_as('origenlab_api', $$ insert into crm.monthly_target
    (month, sales_target_net) values ('2026-12-01', 7000000) $$), 'ok',
  'the API inserts a December target without notes');

-- ── monthly_target: constraints ──────────────────────────────────────────────────────────────
select is(left(pg_temp.run_as('origenlab_api', $$ insert into crm.monthly_target
    (month, sales_target_net) values ('2026-11-01', 6000000) $$), 5), '23505',
  'duplicate month is rejected');
select is(left(pg_temp.run_as('origenlab_api', $$ insert into crm.monthly_target
    (month, sales_target_net) values ('2026-10-15', 5000000) $$), 5), '23514',
  'month not on the first day is rejected');
select is(left(pg_temp.run_as('origenlab_api', $$ insert into crm.monthly_target
    (month, sales_target_net) values ('2027-01-01', 0) $$), 5), '23514',
  'sales_target_net = 0 is rejected');

-- ── monthly_target: update ───────────────────────────────────────────────────────────────────
select is(pg_temp.run_as('origenlab_api', $$ update crm.monthly_target
  set sales_target_net = 6500000
  where month = '2026-11-01' $$), 'ok',
  'the API updates the target for a month');

-- ── monthly_target: worker denied ────────────────────────────────────────────────────────────
select is(left(pg_temp.run_as('origenlab_worker', $$ select 1 from crm.monthly_target $$), 5),
  '42501', 'worker has no access to crm.monthly_target');

-- ── partner_rule: shape ──────────────────────────────────────────────────────────────────────
select has_table('crm', 'partner_rule', 'crm.partner_rule exists (#76)');
select is(
  (select count(*)::int from pg_class c join pg_namespace n on n.oid = c.relnamespace
    where c.relkind = 'r' and n.nspname = 'crm' and c.relname = 'partner_rule'
      and c.relrowsecurity and not c.relforcerowsecurity),
  1, 'RLS is enabled and not forced on crm.partner_rule');

-- ── partner_rule: api inserts ────────────────────────────────────────────────────────────────
select is(pg_temp.run_as('origenlab_api', $$ insert into crm.partner_rule
    (valid_from, rule_name, arriendo_clp, arriendo_pct_empresa, split_pct, notes,
     set_by_operator_id)
  values ('2027-01-01', 'opcion_b', 875000, 1.00, 0.5000, 'Arriendo completo como costo',
          '99000000-0000-4000-9900-000000001001') $$), 'ok',
  'the API appends a new partner rule');

-- ── partner_rule: append-only ────────────────────────────────────────────────────────────────
select is(left(pg_temp.run_as('origenlab_api', $$ delete from crm.partner_rule
  where id = 1 $$), 5), '42501',
  'the API holds no DELETE on crm.partner_rule');
select is(left(pg_temp.run_as('origenlab_owner', $$ delete from crm.partner_rule
  where id = 1 $$), 5), 'P0001',
  'even the owner cannot delete a partner rule (trigger)');

-- ── partner_rule: constraints ────────────────────────────────────────────────────────────────
select is(left(pg_temp.run_as('origenlab_api', $$ insert into crm.partner_rule
    (valid_from, rule_name, arriendo_clp, arriendo_pct_empresa, split_pct)
  values ('2027-02-01', 'opcion_z', 875000, 0, 0.5) $$), 5), '23514',
  'unknown rule_name is rejected');
select is(left(pg_temp.run_as('origenlab_api', $$ insert into crm.partner_rule
    (valid_from, rule_name, arriendo_clp, arriendo_pct_empresa, split_pct)
  values ('2027-02-01', 'opcion_a', 875000, 1.5, 0.5) $$), 5), '23514',
  'arriendo_pct_empresa > 1 is rejected');
select is(left(pg_temp.run_as('origenlab_api', $$ insert into crm.partner_rule
    (valid_from, rule_name, arriendo_clp, arriendo_pct_empresa, split_pct)
  values ('2027-02-01', 'opcion_a', 875000, 0, 1.1) $$), 5), '23514',
  'split_pct > 1 is rejected');

-- ── partner_rule: worker denied ──────────────────────────────────────────────────────────────
select is(left(pg_temp.run_as('origenlab_worker', $$ select 1 from crm.partner_rule $$), 5),
  '42501', 'worker has no access to crm.partner_rule');

-- ── anon denied ──────────────────────────────────────────────────────────────────────────────
select is(left(pg_temp.run_as('anon', $$ select 1 from crm.expense $$), 5), '42501',
  'anon is denied crm.expense');
select is(left(pg_temp.run_as('anon', $$ select 1 from crm.supplier_order $$), 5), '42501',
  'anon is denied crm.supplier_order');
select is(left(pg_temp.run_as('anon', $$ select 1 from crm.monthly_target $$), 5), '42501',
  'anon is denied crm.monthly_target');
select is(left(pg_temp.run_as('anon', $$ select 1 from crm.partner_rule $$), 5), '42501',
  'anon is denied crm.partner_rule');

select * from finish();
rollback;
