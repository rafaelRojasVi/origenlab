-- Slice 7 — catalog.product_model_alias (#59): another name a product's model is printed under.
-- One active alias per maker and key, never a model of the same maker, never deleted — retired
-- once. Synthetic fixtures only.
begin;
create extension if not exists pgtap with schema extensions;
select plan(24);

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

select has_table('catalog', 'product_model_alias', 'product_model_alias exists');
select ok(pg_get_constraintdef((select oid from pg_constraint where conname = 'domain_event_type_check')) like '%product.model_alias_added%',
  'event vocabulary: product.model_alias_added');
select ok(pg_get_constraintdef((select oid from pg_constraint where conname = 'domain_event_type_check')) like '%product.model_alias_retired%',
  'event vocabulary: product.model_alias_retired');
select ok(pg_get_constraintdef((select oid from pg_constraint where conname = 'domain_event_type_check')) like '%quote_revision.draft_saved%',
  'the rebuilt vocabulary keeps the newest earlier type');

set local role origenlab_owner;
insert into platform.operator (id, auth_user_id, email_norm, display_name, role, status) values
  ('98000000-0000-4000-8000-000000000001', '98000000-0000-4000-8000-0000000000f1', 'alias.sales@example.test', 'Alias Sales', 'sales', 'active');
insert into crm.organization (id, kind, name, confirmation) values
  ('98000000-0000-4000-8000-000000000010', 'manufacturer', 'Fabricante Ejemplo', 'machine_proposed'),
  ('98000000-0000-4000-8000-000000000011', 'manufacturer', 'Otro Fabricante Ejemplo', 'machine_proposed');
insert into catalog.product (id, manufacturer_organization_id, model_number, name) values
  ('98000000-0000-4000-8000-000000000020', '98000000-0000-4000-8000-000000000010', '30000001', 'Balanza XQ100'),
  ('98000000-0000-4000-8000-000000000021', '98000000-0000-4000-8000-000000000010', 'XQ-200', 'Balanza XQ200'),
  ('98000000-0000-4000-8000-000000000022', '98000000-0000-4000-8000-000000000011', 'ZR-1', 'Centrífuga ZR1');
insert into evidence.source_record (id, kind, dedupe_key, payload, review_status)
  values ('98000000-0000-4000-8000-000000000060', 'migration_manifest', 'catalog-import:pgtap-098', '{}'::jsonb, 'reviewed');
reset role;

select is(pg_temp.run_as('origenlab_api', $$ insert into catalog.product_model_alias
    (id, product_id, alias, source, origin_source_record_id, created_by_operator_id)
  values ('98000000-0000-4000-8000-000000000030', '98000000-0000-4000-8000-000000000020', 'XQ 100', 'import',
          '98000000-0000-4000-8000-000000000060', '98000000-0000-4000-8000-000000000001') $$), 'ok',
  'the API adds an alias');
select is((select alias_key from catalog.product_model_alias where id = '98000000-0000-4000-8000-000000000030'),
  'XQ100', 'alias_key is the model_key normalisation of what is printed');
select is(left(pg_temp.run_as('origenlab_api', $$ insert into catalog.product_model_alias
    (product_id, alias, source, origin_source_record_id, created_by_operator_id)
  values ('98000000-0000-4000-8000-000000000021', 'xq-100', 'import',
          '98000000-0000-4000-8000-000000000060', '98000000-0000-4000-8000-000000000001') $$), 5), 'P0001',
  'one maker never maps one alias to two products');
select is(left(pg_temp.run_as('origenlab_api', $$ insert into catalog.product_model_alias
    (product_id, alias, source, created_by_operator_id)
  values ('98000000-0000-4000-8000-000000000020', 'XQ.100', 'operator', '98000000-0000-4000-8000-000000000001') $$), 5), 'P0001',
  'nor the same alias twice to one product');
select is(pg_temp.run_as('origenlab_api', $$ insert into catalog.product_model_alias
    (product_id, alias, source, created_by_operator_id)
  values ('98000000-0000-4000-8000-000000000022', 'XQ100', 'operator', '98000000-0000-4000-8000-000000000001') $$), 'ok',
  'two makers may share a printed code');
select is(left(pg_temp.run_as('origenlab_api', $$ insert into catalog.product_model_alias
    (product_id, alias, source, created_by_operator_id)
  values ('98000000-0000-4000-8000-000000000020', 'XQ 200', 'operator', '98000000-0000-4000-8000-000000000001') $$), 5), 'P0001',
  'an alias equal to the model of another product of the same maker is refused');
select is(left(pg_temp.run_as('origenlab_api', $$ insert into catalog.product_model_alias
    (product_id, alias, source, created_by_operator_id)
  values ('98000000-0000-4000-8000-000000000020', '3000-0001', 'operator', '98000000-0000-4000-8000-000000000001') $$), 5), 'P0001',
  'and so is one equal to the product''s own model');
select is(left(pg_temp.run_as('origenlab_api', $$ insert into catalog.product_model_alias
    (product_id, alias, source, created_by_operator_id)
  values ('98000000-0000-4000-8000-000000000020', ' -./ ', 'operator', '98000000-0000-4000-8000-000000000001') $$), 5), '23514',
  'an alias has a letter or a digit');
select is(left(pg_temp.run_as('origenlab_api', $$ insert into catalog.product_model_alias
    (product_id, alias, source, created_by_operator_id)
  values ('98000000-0000-4000-8000-000000000020', 'XQ100S', 'import', '98000000-0000-4000-8000-000000000001') $$), 5), '23514',
  'an imported alias names its manifest');
select is(left(pg_temp.run_as('origenlab_api', $$ insert into catalog.product_model_alias
    (product_id, alias, source, created_by_operator_id)
  values ('98000000-0000-4000-8000-000000000020', 'XQ100S', 'machine', '98000000-0000-4000-8000-000000000001') $$), 5), '23514',
  'source is import or operator');
select is(left(pg_temp.run_as('origenlab_api', $$ insert into catalog.product_model_alias
    (product_id, alias, source, created_by_operator_id, retired_at, retired_by_operator_id)
  values ('98000000-0000-4000-8000-000000000020', 'XQ100T', 'operator', '98000000-0000-4000-8000-000000000001',
          now(), '98000000-0000-4000-8000-000000000001') $$), 5), 'P0001',
  'an alias is added active');
select is(left(pg_temp.run_as('origenlab_api', $$ update catalog.product_model_alias set alias = 'XQ 101'
                     where id = '98000000-0000-4000-8000-000000000030' $$), 5), '42501',
  'the API cannot rewrite an alias (no column grant)');
select is(left(pg_temp.run_as('origenlab_api', $$ update catalog.product_model_alias set retired_at = now()
                     where id = '98000000-0000-4000-8000-000000000030' $$), 5), '23514',
  'a retirement names who retired it');
select is(pg_temp.run_as('origenlab_api', $$ update catalog.product_model_alias
                     set retired_at = now(), retired_by_operator_id = '98000000-0000-4000-8000-000000000001'
                     where id = '98000000-0000-4000-8000-000000000030' $$), 'ok',
  'the API retires an alias');
select is(left(pg_temp.run_as('origenlab_api', $$ update catalog.product_model_alias
                     set retired_at = null, retired_by_operator_id = null
                     where id = '98000000-0000-4000-8000-000000000030' $$), 5), 'P0001',
  'a retired alias stays retired');
select is(pg_temp.run_as('origenlab_api', $$ insert into catalog.product_model_alias
    (product_id, alias, source, created_by_operator_id)
  values ('98000000-0000-4000-8000-000000000021', 'XQ100', 'operator', '98000000-0000-4000-8000-000000000001') $$), 'ok',
  'once retired, the maker may give the alias to another product');
select is(left(pg_temp.run_as('origenlab_api', $$ delete from catalog.product_model_alias
                     where id = '98000000-0000-4000-8000-000000000030' $$), 5), '42501',
  'the API cannot delete an alias');
select is(pg_temp.run_as('origenlab_worker', $$ select 1 from catalog.product_model_alias $$), 'ok',
  'the worker reads aliases (mail triage)');
select is(left(pg_temp.run_as('origenlab_worker', $$ insert into catalog.product_model_alias
    (product_id, alias, source, created_by_operator_id)
  values ('98000000-0000-4000-8000-000000000022', 'ZR 9', 'operator', '98000000-0000-4000-8000-000000000001') $$), 5), '42501',
  'the worker never writes one');
select ok((select relrowsecurity and not relforcerowsecurity from pg_class
            where oid = 'catalog.product_model_alias'::regclass), 'RLS enabled, not forced');

select * from finish();
rollback;
