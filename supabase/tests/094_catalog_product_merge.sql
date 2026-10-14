-- Slice 7 — catalog.product.merged_into_product_id: a duplicate product (the same model loaded
-- twice under two names of one maker) points at the product it was merged into and is inactive.
-- Synthetic fixtures only.
begin;
create extension if not exists pgtap with schema extensions;
select plan(7);

grant origenlab_api to session_user with set true, inherit false;

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

select has_column('catalog', 'product', 'merged_into_product_id', 'a product can name the product it was merged into');
select ok(pg_get_constraintdef((select oid from pg_constraint where conname = 'domain_event_type_check')) like '%product.merged%',
  'event vocabulary: product.merged');
select ok(exists (select 1 from pg_indexes where schemaname = 'catalog' and indexname = 'product_merged_into_idx'),
  'the merge foreign key is indexed');

set local role origenlab_owner;
insert into crm.organization (id, kind, name, confirmation) values
  ('94000000-0000-4000-8000-000000000010', 'manufacturer', 'Fabricante Sintético', 'machine_proposed');
insert into catalog.product (id, manufacturer_organization_id, model_number, name) values
  ('94000000-0000-4000-8000-000000000020', '94000000-0000-4000-8000-000000000010', 'SYN-094-A', 'A'),
  ('94000000-0000-4000-8000-000000000021', '94000000-0000-4000-8000-000000000010', 'SYN-094-B', 'B');
reset role;

select is(left(pg_temp.run_as('origenlab_api', $$ update catalog.product set merged_into_product_id = id, active = false
                     where id = '94000000-0000-4000-8000-000000000020' $$), 5), '23514',
  'a product is never merged into itself');
select is(left(pg_temp.run_as('origenlab_api', $$ update catalog.product set merged_into_product_id = '94000000-0000-4000-8000-000000000021'
                     where id = '94000000-0000-4000-8000-000000000020' $$), 5), '23514',
  'a merged product is inactive');
select is(left(pg_temp.run_as('origenlab_api', $$ update catalog.product set merged_into_product_id = '94000000-0000-4000-8000-0000000000ff', active = false
                     where id = '94000000-0000-4000-8000-000000000020' $$), 5), '23503',
  'the merge target exists');
select is(pg_temp.run_as('origenlab_api', $$ update catalog.product set merged_into_product_id = '94000000-0000-4000-8000-000000000021',
                     active = false, version = version + 1, updated_at = now()
                     where id = '94000000-0000-4000-8000-000000000020' $$), 'ok',
  'the API merges a duplicate into its twin');

select * from finish();
rollback;
