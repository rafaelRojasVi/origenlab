-- Slice 7 — crm.opportunity_document (#56): the documents a person attaches to a commercial case
-- (today its purchase order), append-only with one removal, and the two events that record them.
-- Synthetic fixtures only.
begin;
create extension if not exists pgtap with schema extensions;
select plan(20);

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

select has_table('crm', 'opportunity_document', 'opportunity_document exists');
select ok(exists (select 1 from pg_trigger where tgname = 'opportunity_document_never_deleted'), 'documents are never deleted');
select ok(exists (select 1 from pg_trigger where tgname = 'opportunity_document_immutable'), 'documents are never rewritten');
select ok(pg_get_constraintdef((select oid from pg_constraint where conname = 'domain_event_type_check')) like '%opportunity.document_attached%',
  'event vocabulary: opportunity.document_attached');
select ok(pg_get_constraintdef((select oid from pg_constraint where conname = 'domain_event_type_check')) like '%opportunity.document_removed%',
  'event vocabulary: opportunity.document_removed');
select ok(pg_get_constraintdef((select oid from pg_constraint where conname = 'domain_event_type_check')) like '%assertion.triage_reviewed%',
  'the rebuilt vocabulary keeps the newest earlier type');

set local role origenlab_owner;
insert into platform.operator (id, auth_user_id, email_norm, display_name, role, status) values
  ('92000000-0000-4000-8000-000000000001', '92000000-0000-4000-8000-0000000000f1', 'doc.admin@example.test', 'Doc Admin', 'admin', 'active');
insert into crm.opportunity (id, title, stage, owner_operator_id) values
  ('92000000-0000-4000-8000-000000000050', 'Synthetic case', 'lead', '92000000-0000-4000-8000-000000000001');
insert into evidence.source_record (id, kind, dedupe_key, payload, review_status)
  values ('92000000-0000-4000-8000-000000000060', 'gmail_message', 'gmail_message:pgtap-092', '{}'::jsonb, 'pending');
reset role;


select is(pg_temp.run_as('origenlab_api', $$ insert into crm.opportunity_document
    (id, opportunity_id, kind, document_number, storage_path, sha256, content_type, byte_size,
     original_filename, source, attached_by_operator_id)
  values ('92000000-0000-4000-8000-000000000070', '92000000-0000-4000-8000-000000000050', 'purchase_order', '4500',
          'opportunities/92000000-0000-4000-8000-000000000050/' || repeat('a', 64) || '.pdf', repeat('a', 64),
          'application/pdf', 1024, 'OC 4500.pdf', 'upload', '92000000-0000-4000-8000-000000000001') $$), 'ok',
  'the API attaches an uploaded purchase order');
select is(pg_temp.run_as('origenlab_api', $$ insert into crm.opportunity_document
    (opportunity_id, kind, storage_path, sha256, content_type, byte_size, original_filename, source,
     source_record_id, attached_by_operator_id)
  values ('92000000-0000-4000-8000-000000000050', 'purchase_order',
          'opportunities/92000000-0000-4000-8000-000000000050/' || repeat('b', 64) || '.pdf', repeat('b', 64),
          'application/pdf', 2048, 'orden.pdf', 'email', '92000000-0000-4000-8000-000000000060',
          '92000000-0000-4000-8000-000000000001') $$), 'ok',
  'the API attaches a purchase order copied from an email');
select is(left(pg_temp.run_as('origenlab_api', $$ insert into crm.opportunity_document
    (opportunity_id, kind, storage_path, sha256, content_type, byte_size, original_filename, source, attached_by_operator_id)
  values ('92000000-0000-4000-8000-000000000050', 'purchase_order',
          'opportunities/92000000-0000-4000-8000-000000000050/' || repeat('a', 64) || '.pdf', repeat('a', 64),
          'application/pdf', 1024, 'again.pdf', 'upload', '92000000-0000-4000-8000-000000000001') $$), 5), '23505',
  'the same bytes are attached to a case once while live');
select is(left(pg_temp.run_as('origenlab_api', $$ insert into crm.opportunity_document
    (opportunity_id, kind, storage_path, sha256, content_type, byte_size, original_filename, source, attached_by_operator_id)
  values ('92000000-0000-4000-8000-000000000050', 'purchase_order',
          'opportunities/92000000-0000-4000-8000-000000000050/' || repeat('c', 64) || '.pdf', repeat('c', 64),
          'application/pdf', 1024, 'x.pdf', 'email', '92000000-0000-4000-8000-000000000001') $$), 5), '23514',
  'an email document names its message');
select is(left(pg_temp.run_as('origenlab_api', $$ insert into crm.opportunity_document
    (opportunity_id, kind, storage_path, sha256, content_type, byte_size, original_filename, source, attached_by_operator_id)
  values ('92000000-0000-4000-8000-000000000050', 'purchase_order',
          'elsewhere/' || repeat('d', 64) || '.pdf', repeat('d', 64),
          'application/pdf', 1024, 'x.pdf', 'upload', '92000000-0000-4000-8000-000000000001') $$), 5), '23514',
  'the object lives under the case''s content-addressed path');
select is(left(pg_temp.run_as('origenlab_api', $$ insert into crm.opportunity_document
    (opportunity_id, kind, storage_path, sha256, content_type, byte_size, original_filename, source, attached_by_operator_id)
  values ('92000000-0000-4000-8000-000000000050', 'invoice',
          'opportunities/92000000-0000-4000-8000-000000000050/' || repeat('e', 64) || '.pdf', repeat('e', 64),
          'application/pdf', 1024, 'x.pdf', 'upload', '92000000-0000-4000-8000-000000000001') $$), 5), '23514',
  'only purchase orders for now');
select is(left(pg_temp.run_as('origenlab_api', $$ insert into crm.opportunity_document
    (opportunity_id, kind, storage_path, sha256, content_type, byte_size, original_filename, source, attached_by_operator_id)
  values ('92000000-0000-4000-8000-000000000050', 'purchase_order',
          'opportunities/92000000-0000-4000-8000-000000000050/' || repeat('f', 64) || '.pdf', repeat('f', 64),
          'application/zip', 1024, 'x.zip', 'upload', '92000000-0000-4000-8000-000000000001') $$), 5), '23514',
  'a PDF, a JPEG or a PNG');
select is(left(pg_temp.run_as('origenlab_api', $$ update crm.opportunity_document set document_number = '9999'
                     where id = '92000000-0000-4000-8000-000000000070' $$), 5), '42501',
  'the API cannot rewrite a document''s number (no column grant)');
select is(left(pg_temp.run_as('origenlab_api', $$ delete from crm.opportunity_document where id = '92000000-0000-4000-8000-000000000070' $$), 5), '42501',
  'the API cannot delete a document');
select is(left(pg_temp.run_as('origenlab_api', $$ update crm.opportunity_document set removed_at = now()
                     where id = '92000000-0000-4000-8000-000000000070' $$), 5), '23514',
  'a removal names who and why');
select is(pg_temp.run_as('origenlab_api', $$ update crm.opportunity_document
                     set removed_at = now(), removed_by_operator_id = '92000000-0000-4000-8000-000000000001',
                         remove_reason = 'wrong file'
                   where id = '92000000-0000-4000-8000-000000000070' $$), 'ok',
  'the API removes a document once, with its reason');
select is(left(pg_temp.run_as('origenlab_api', $$ update crm.opportunity_document set remove_reason = 'changed my mind'
                     where id = '92000000-0000-4000-8000-000000000070' $$), 5), 'P0001',
  'a removed document stays removed');
select is(pg_temp.run_as('origenlab_api', $$ insert into crm.opportunity_document
    (opportunity_id, kind, storage_path, sha256, content_type, byte_size, original_filename, source, attached_by_operator_id)
  values ('92000000-0000-4000-8000-000000000050', 'purchase_order',
          'opportunities/92000000-0000-4000-8000-000000000050/' || repeat('a', 64) || '.pdf', repeat('a', 64),
          'application/pdf', 1024, 'OC 4500.pdf', 'upload', '92000000-0000-4000-8000-000000000001') $$), 'ok',
  'after a removal the same bytes may be attached again');



select is(left(pg_temp.run_as('origenlab_worker', $$ insert into crm.opportunity_document
    (opportunity_id, kind, storage_path, sha256, content_type, byte_size, original_filename, source, attached_by_operator_id)
  values ('92000000-0000-4000-8000-000000000050', 'purchase_order',
          'opportunities/92000000-0000-4000-8000-000000000050/' || repeat('9', 64) || '.pdf', repeat('9', 64),
          'application/pdf', 1024, 'x.pdf', 'upload', '92000000-0000-4000-8000-000000000001') $$), 5), '42501',
  'the worker never attaches a document');


select * from finish();
rollback;
