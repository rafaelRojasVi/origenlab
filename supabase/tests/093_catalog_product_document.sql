-- Slice 7 — catalog.product_document (#57): where a product's own data sheet, or a supplier's
-- price list, lives in Google Drive (only the file id). Never deleted; retired by status.
-- Synthetic fixtures only.
begin;
create extension if not exists pgtap with schema extensions;
select plan(18);

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

select has_table('catalog', 'product_document', 'product_document exists');
select ok(pg_get_constraintdef((select oid from pg_constraint where conname = 'domain_event_type_check')) like '%product.document_recorded%',
  'event vocabulary: product.document_recorded');
select ok(pg_get_constraintdef((select oid from pg_constraint where conname = 'domain_event_type_check')) like '%product.document_retired%',
  'event vocabulary: product.document_retired');
select ok(pg_get_constraintdef((select oid from pg_constraint where conname = 'domain_event_type_check')) like '%organization.price_list_recorded%',
  'event vocabulary: organization.price_list_recorded');
select ok(pg_get_constraintdef((select oid from pg_constraint where conname = 'domain_event_type_check')) like '%opportunity.document_removed%',
  'the rebuilt vocabulary keeps the newest earlier type');

set local role origenlab_owner;
insert into crm.organization (id, kind, name, confirmation) values
  ('93000000-0000-4000-8000-000000000010', 'manufacturer', 'Fabricante Sintético', 'machine_proposed'),
  ('93000000-0000-4000-8000-000000000011', 'supplier', 'Proveedor Sintético', 'machine_proposed');
insert into catalog.product (id, manufacturer_organization_id, model_number, name) values
  ('93000000-0000-4000-8000-000000000020', '93000000-0000-4000-8000-000000000010', 'SYN-093', 'Synthetic');
reset role;

select is(left(pg_temp.run_as('origenlab_api', $$ insert into catalog.product_document
    (kind, title, drive_file_id, mime_type, source)
  values ('datasheet', 'x.pdf', 'SyntheticFile0001', 'application/pdf', 'operator') $$), 5), '23514',
  'a data sheet names its product');
select is(left(pg_temp.run_as('origenlab_api', $$ insert into catalog.product_document
    (product_id, kind, title, drive_file_id, mime_type, source)
  values ('93000000-0000-4000-8000-000000000020', 'price_list', 'x.pdf', 'SyntheticFile0002', 'application/pdf', 'operator') $$), 5), '23514',
  'a price list names its supplier, not a product');
select is(left(pg_temp.run_as('origenlab_api', $$ insert into catalog.product_document
    (product_id, kind, title, drive_file_id, mime_type, source)
  values ('93000000-0000-4000-8000-000000000020', 'datasheet', 'x.pdf', 'bad id!', 'application/pdf', 'operator') $$), 5), '23514',
  'a Drive file id has the Drive shape');
select is(left(pg_temp.run_as('origenlab_api', $$ insert into catalog.product_document
    (product_id, kind, title, drive_file_id, mime_type, source)
  values ('93000000-0000-4000-8000-000000000020', 'brochure', 'x.pdf', 'SyntheticFile0003', 'application/pdf', 'operator') $$), 5), '23514',
  'only data sheets and price lists');
select is(pg_temp.run_as('origenlab_api', $$ insert into catalog.product_document
    (id, product_id, kind, title, drive_file_id, mime_type, source)
  values ('93000000-0000-4000-8000-000000000030', '93000000-0000-4000-8000-000000000020', 'datasheet', 'SYN-093.pdf',
          'SyntheticFile0004', 'application/pdf', 'operator') $$), 'ok',
  'the API records a product''s data sheet');
select is(left(pg_temp.run_as('origenlab_api', $$ insert into catalog.product_document
    (product_id, kind, title, drive_file_id, mime_type, source)
  values ('93000000-0000-4000-8000-000000000020', 'datasheet', 'again.pdf', 'SyntheticFile0004', 'application/pdf', 'operator') $$), 5), '23505',
  'the same file is recorded once per product');
select is(pg_temp.run_as('origenlab_api', $$ insert into catalog.product_document
    (supplier_organization_id, kind, title, drive_file_id, mime_type, source, as_of)
  values ('93000000-0000-4000-8000-000000000011', 'price_list', 'Lista.xlsx', 'SyntheticFile0005',
          'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', 'operator', '2026-01-01') $$), 'ok',
  'the API records a supplier''s price list');
select is(pg_temp.run_as('origenlab_api', $$ update catalog.product_document set status = 'retired', version = version + 1,
                     updated_at = now() where id = '93000000-0000-4000-8000-000000000030' $$), 'ok',
  'the API retires a document');
select is(left(pg_temp.run_as('origenlab_api', $$ update catalog.product_document set title = 'other.pdf'
                     where id = '93000000-0000-4000-8000-000000000030' $$), 5), '42501',
  'the API cannot rewrite a document (no column grant)');
select is(left(pg_temp.run_as('origenlab_api', $$ delete from catalog.product_document
                     where id = '93000000-0000-4000-8000-000000000030' $$), 5), '42501',
  'the API cannot delete a document');
select is(left(pg_temp.run_as('origenlab_worker', $$ select 1 from catalog.product_document $$), 5), '42501',
  'the worker has no access');
select is(left(pg_temp.run_as('anon', $$ select 1 from catalog.product_document $$), 5), '42501',
  'anon has no access');
select ok((select relrowsecurity and not relforcerowsecurity from pg_class
            where oid = 'catalog.product_document'::regclass), 'RLS enabled, not forced');

select * from finish();
rollback;
