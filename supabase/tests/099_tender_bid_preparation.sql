-- Slice 7 — tender bid preparation (DOMAIN.md §7.11, #60–#63): a tender is a crm.opportunity of
-- kind 'tender' with its own status; the bidder's data and standard documents; the anexos on the
-- case; the machine's preparation rows and the model spend. Synthetic fixtures only.
begin;
create extension if not exists pgtap with schema extensions;
select plan(69);

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

-- ── shape ───────────────────────────────────────────────────────────────────────────────────
select has_table('crm', 'bidder_profile', 'crm.bidder_profile exists (#60)');
select has_table('crm', 'bidder_document', 'crm.bidder_document exists (#61)');
select has_table('evidence', 'bid_preparation', 'evidence.bid_preparation exists (#62)');
select has_table('platform', 'model_usage', 'platform.model_usage exists (#63)');
select is(
  (select count(*)::int from pg_class
    where oid in ('crm.bidder_profile'::regclass, 'crm.bidder_document'::regclass,
                  'evidence.bid_preparation'::regclass, 'platform.model_usage'::regclass)
      and relrowsecurity and not relforcerowsecurity),
  4, 'RLS is enabled, not forced, on the four new tables');
select has_index('crm', 'opportunity', 'opportunity_tender_idx', 'tenders are listed by status through a partial index');
select ok(
  (select bool_and(pg_get_constraintdef((select oid from pg_constraint where conname = 'domain_event_type_check')) like '%' || t || '%')
     from unnest(array['bidder.profile_saved', 'bidder.document_added', 'bidder.document_retired',
                       'opportunity.bid_preparation_requested', 'opportunity.tender_status_set',
                       'notice.promoted', 'product.model_alias_retired']) t),
  'event vocabulary: the six tender events, keeping the newest earlier type');
-- The CHECK helper runs as the role that inserts events.
set local role origenlab_api;
select ok(crm.domain_event_is_valid('bidder', 'bidder.profile_saved', 1::smallint, '{}'::jsonb),
  'aggregate kind bidder carries the bidder.* family');
select ok(crm.domain_event_is_valid('notice', 'notice.promoted', 1::smallint, '{}'::jsonb),
  'aggregate kind notice carries the notice.* family');
select ok(not crm.domain_event_is_valid('bidder', 'notice.promoted', 1::smallint, '{}'::jsonb),
  'a family never crosses aggregate kinds');
reset role;

-- ── fixtures ────────────────────────────────────────────────────────────────────────────────
set local role origenlab_owner;
insert into platform.operator (id, auth_user_id, email_norm, display_name, role, status) values
  ('99000000-0000-4000-8000-000000000001', '99000000-0000-4000-8000-0000000000f1', 'tender.sales@example.test', 'Tender Sales', 'sales', 'active');
insert into crm.opportunity (id, title, stage, owner_operator_id) values
  ('99000000-0000-4000-8000-000000000050', 'Caso de venta sintético', 'lead', '99000000-0000-4000-8000-000000000001');
reset role;

-- ── crm.opportunity: kind and tender_status ─────────────────────────────────────────────────
select is((select kind from crm.opportunity where id = '99000000-0000-4000-8000-000000000050'), 'sale',
  'a case opened without a kind is a sale');
select is((select count(*) from crm.opportunity where kind not in ('sale', 'tender')), 0::bigint,
  'every case is a sale or a tender (existing rows were backfilled to sale)');
select is(pg_temp.run_as('origenlab_api', $$ insert into crm.opportunity (id, title, stage, owner_operator_id, kind, tender_status)
  values ('99000000-0000-4000-8000-000000000051', 'Licitación 1234-5-LE26', 'lead', '99000000-0000-4000-8000-000000000001',
          'tender', 'preparando') $$), 'ok',
  'the API opens a tender case in preparation');
select is(left(pg_temp.run_as('origenlab_api', $$ insert into crm.opportunity (title, stage, owner_operator_id, kind)
  values ('Licitación sin estado', 'lead', '99000000-0000-4000-8000-000000000001', 'tender') $$), 5), '23514',
  'a tender carries a status');
select is(left(pg_temp.run_as('origenlab_api', $$ insert into crm.opportunity (title, stage, owner_operator_id, tender_status)
  values ('Venta con estado', 'lead', '99000000-0000-4000-8000-000000000001', 'preparando') $$), 5), '23514',
  'a sale never carries a tender status');
select is(left(pg_temp.run_as('origenlab_api', $$ insert into crm.opportunity (title, stage, owner_operator_id, kind, tender_status)
  values ('Licitación', 'lead', '99000000-0000-4000-8000-000000000001', 'tender', 'ganada') $$), 5), '23514',
  'the tender status is preparando, postulada, adjudicada or perdida');
select is(left(pg_temp.run_as('origenlab_api', $$ insert into crm.opportunity (title, stage, owner_operator_id, kind)
  values ('Otro', 'lead', '99000000-0000-4000-8000-000000000001', 'rental') $$), 5), '23514',
  'kind is sale or tender');
select is(pg_temp.run_as('origenlab_api', $$ update crm.opportunity set tender_status = 'postulada'
                     where id = '99000000-0000-4000-8000-000000000051' $$), 'ok',
  'the API sets a tender''s status');
select is(left(pg_temp.run_as('origenlab_api', $$ update crm.opportunity set kind = 'tender', tender_status = 'preparando'
                     where id = '99000000-0000-4000-8000-000000000050' $$), 5), 'P0001',
  'a sale never becomes a tender');
select is(left(pg_temp.run_as('origenlab_api', $$ update crm.opportunity set kind = 'sale', tender_status = null
                     where id = '99000000-0000-4000-8000-000000000051' $$), 5), 'P0001',
  'nor a tender a sale');

-- ── crm.opportunity_document: anexos ────────────────────────────────────────────────────────
select is(pg_temp.run_as('origenlab_api', $$ insert into crm.opportunity_document
    (id, opportunity_id, kind, storage_path, sha256, content_type, byte_size, original_filename, source, attached_by_operator_id)
  values ('99000000-0000-4000-8000-000000000071', '99000000-0000-4000-8000-000000000051', 'tender_annex',
          'opportunities/99000000-0000-4000-8000-000000000051/' || repeat('a', 64) || '.docx', repeat('a', 64),
          'application/vnd.openxmlformats-officedocument.wordprocessingml.document', 4096, 'Anexo 1.docx', 'upload',
          '99000000-0000-4000-8000-000000000001') $$), 'ok',
  'the API attaches a Word anexo');
select is(pg_temp.run_as('origenlab_api', $$ insert into crm.opportunity_document
    (opportunity_id, kind, storage_path, sha256, content_type, byte_size, original_filename, source, attached_by_operator_id)
  values ('99000000-0000-4000-8000-000000000051', 'tender_annex',
          'opportunities/99000000-0000-4000-8000-000000000051/' || repeat('b', 64) || '.xlsx', repeat('b', 64),
          'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', 4096, 'Anexo 2.xlsx', 'upload',
          '99000000-0000-4000-8000-000000000001') $$), 'ok',
  'and an Excel anexo');
select is(pg_temp.run_as('origenlab_api', $$ insert into crm.opportunity_document
    (opportunity_id, kind, storage_path, sha256, content_type, byte_size, original_filename, source, attached_by_operator_id)
  values ('99000000-0000-4000-8000-000000000051', 'tender_annex',
          'opportunities/99000000-0000-4000-8000-000000000051/' || repeat('c', 64) || '.doc', repeat('c', 64),
          'application/msword', 4096, 'Anexo 3.doc', 'upload', '99000000-0000-4000-8000-000000000001') $$), 'ok',
  'and a legacy Word anexo');
select is(pg_temp.run_as('origenlab_api', $$ insert into crm.opportunity_document
    (opportunity_id, kind, storage_path, sha256, content_type, byte_size, original_filename, source, attached_by_operator_id)
  values ('99000000-0000-4000-8000-000000000051', 'tender_annex',
          'opportunities/99000000-0000-4000-8000-000000000051/' || repeat('d', 64) || '.xls', repeat('d', 64),
          'application/vnd.ms-excel', 4096, 'Anexo 4.xls', 'upload', '99000000-0000-4000-8000-000000000001') $$), 'ok',
  'and a legacy Excel anexo');
select is(left(pg_temp.run_as('origenlab_api', $$ insert into crm.opportunity_document
    (opportunity_id, kind, storage_path, sha256, content_type, byte_size, original_filename, source, attached_by_operator_id)
  values ('99000000-0000-4000-8000-000000000051', 'tender_annex',
          'opportunities/99000000-0000-4000-8000-000000000051/' || repeat('e', 64) || '.zip', repeat('e', 64),
          'application/zip', 4096, 'anexos.zip', 'upload', '99000000-0000-4000-8000-000000000001') $$), 5), '23514',
  'a zip is unpacked, never stored as a case document');
select is(left(pg_temp.run_as('origenlab_api', $$ insert into crm.opportunity_document
    (opportunity_id, kind, storage_path, sha256, content_type, byte_size, original_filename, source, attached_by_operator_id)
  values ('99000000-0000-4000-8000-000000000051', 'bid_form',
          'opportunities/99000000-0000-4000-8000-000000000051/' || repeat('f', 64) || '.pdf', repeat('f', 64),
          'application/pdf', 4096, 'x.pdf', 'upload', '99000000-0000-4000-8000-000000000001') $$), 5), '23514',
  'a case document is a purchase order or a tender anexo');

-- ── crm.bidder_profile (#60) ────────────────────────────────────────────────────────────────
select is(left(pg_temp.run_as('origenlab_api', $$ insert into crm.bidder_profile
    (bidder_kind, legal_name, rut, legal_rep_name, set_by_operator_id, reason)
  values ('natural_person', 'Oferente Ejemplo', '11111111-1', 'Otro', '99000000-0000-4000-8000-000000000001', 'alta') $$), 5), '23514',
  'a natural person has no legal representative');
select is(pg_temp.run_as('origenlab_api', $$ insert into crm.bidder_profile
    (id, bidder_kind, legal_name, rut, giro, comuna, region, bank_account_type, chileproveedores_status,
     chileproveedores_checked_on, is_emt, set_by_operator_id, reason)
  values ('99000000-0000-4000-8000-000000000060', 'natural_person', 'Oferente Ejemplo', '11111111-1', 'Venta de equipos',
          'Santiago', 'Metropolitana', 'vista', 'habil', '2026-10-01', true,
          '99000000-0000-4000-8000-000000000001', 'alta') $$), 'ok',
  'the API saves a natural-person bidder profile');
select is(pg_temp.run_as('origenlab_api', $$ insert into crm.bidder_profile
    (bidder_kind, legal_name, rut, legal_rep_name, legal_rep_rut, set_by_operator_id, reason)
  values ('company', 'Empresa Ejemplo SpA', '76543210-K', 'Representante Ejemplo', '12345678-5',
          '99000000-0000-4000-8000-000000000001', 'cambio a empresa') $$), 'ok',
  'a company names its legal representative');
select is(left(pg_temp.run_as('origenlab_api', $$ insert into crm.bidder_profile
    (bidder_kind, legal_name, rut, set_by_operator_id, reason)
  values ('natural_person', 'Oferente Ejemplo', '11.111.111-1', '99000000-0000-4000-8000-000000000001', 'alta') $$), 5), '23514',
  'the RUT is stored normalised (no dots)');
select is(left(pg_temp.run_as('origenlab_api', $$ insert into crm.bidder_profile
    (bidder_kind, legal_name, rut, set_by_operator_id, reason)
  values ('natural_person', 'Oferente Ejemplo', '7654321-k', '99000000-0000-4000-8000-000000000001', 'alta') $$), 5), '23514',
  'the RUT check digit K is upper case');
select is(left(pg_temp.run_as('origenlab_api', $$ insert into crm.bidder_profile
    (bidder_kind, legal_name, rut, set_by_operator_id, reason)
  values ('natural_person', 'Oferente Ejemplo', '11111111-1', '99000000-0000-4000-8000-000000000001', '  ') $$), 5), '23514',
  'a save states its reason');
select is(left(pg_temp.run_as('origenlab_api', $$ insert into crm.bidder_profile
    (bidder_kind, legal_name, rut, bank_account_type, set_by_operator_id, reason)
  values ('natural_person', 'Oferente Ejemplo', '11111111-1', 'plazo', '99000000-0000-4000-8000-000000000001', 'alta') $$), 5), '23514',
  'the bank account type is corriente, vista, ahorro or rut');
select is(left(pg_temp.run_as('origenlab_api', $$ update crm.bidder_profile set legal_name = 'x' $$), 5), '42501',
  'the API never rewrites a profile (no UPDATE grant)');
select is(left(pg_temp.run_as('origenlab_owner', $$ update crm.bidder_profile set legal_name = 'x' $$), 5), 'P0001',
  'a profile is append-only even for its owner: a change is a new row');
select is(left(pg_temp.run_as('origenlab_owner', $$ delete from crm.bidder_profile $$), 5), 'P0001',
  'a profile is never deleted');
select is(pg_temp.run_as('origenlab_worker', $$ select 1 from crm.bidder_profile $$), 'ok',
  'the worker reads the bidder profile (it fills the forms)');

-- ── crm.bidder_document (#61) ───────────────────────────────────────────────────────────────
select is(pg_temp.run_as('origenlab_api', $$ insert into crm.bidder_document
    (id, kind, title, storage_path, sha256, content_type, byte_size, original_filename, added_by_operator_id)
  values ('99000000-0000-4000-8000-000000000070', 'cedula', 'Cédula de identidad',
          'bidder/' || repeat('1', 64) || '.pdf', repeat('1', 64), 'application/pdf', 2048, 'cedula.pdf',
          '99000000-0000-4000-8000-000000000001') $$), 'ok',
  'the API adds a standard bidder document');
select is(left(pg_temp.run_as('origenlab_api', $$ insert into crm.bidder_document
    (kind, title, storage_path, sha256, content_type, byte_size, original_filename, added_by_operator_id)
  values ('cedula', 'Cédula', 'opportunities/' || repeat('2', 64) || '.pdf', repeat('2', 64), 'application/pdf', 2048,
          'cedula.pdf', '99000000-0000-4000-8000-000000000001') $$), 5), '23514',
  'a bidder document lives under bidder/<sha256>');
select is(left(pg_temp.run_as('origenlab_api', $$ insert into crm.bidder_document
    (kind, title, storage_path, sha256, content_type, byte_size, original_filename, added_by_operator_id)
  values ('poder', 'Poder', 'bidder/' || repeat('3', 64) || '.pdf', repeat('3', 64), 'application/pdf', 2048,
          'poder.pdf', '99000000-0000-4000-8000-000000000001') $$), 5), '23514',
  'the document kind is from the closed list');
select is(left(pg_temp.run_as('origenlab_api', $$ insert into crm.bidder_document
    (kind, title, storage_path, sha256, content_type, byte_size, original_filename, added_by_operator_id)
  values ('otro', 'Grande', 'bidder/' || repeat('4', 64) || '.pdf', repeat('4', 64), 'application/pdf', 20971521,
          'grande.pdf', '99000000-0000-4000-8000-000000000001') $$), 5), '23514',
  'a bidder document is at most 20 MiB');
select is(left(pg_temp.run_as('origenlab_api', $$ update crm.bidder_document set title = 'Otro título'
                     where id = '99000000-0000-4000-8000-000000000070' $$), 5), '42501',
  'the API cannot rewrite a document (no column grant)');
select is(left(pg_temp.run_as('origenlab_api', $$ update crm.bidder_document set retired_at = now()
                     where id = '99000000-0000-4000-8000-000000000070' $$), 5), '23514',
  'a retirement names who and why');
select is(pg_temp.run_as('origenlab_api', $$ update crm.bidder_document
                     set retired_at = now(), retired_by_operator_id = '99000000-0000-4000-8000-000000000001',
                         retire_reason = 'vencida'
                   where id = '99000000-0000-4000-8000-000000000070' $$), 'ok',
  'the API retires a document once, with its reason');
select is(left(pg_temp.run_as('origenlab_api', $$ update crm.bidder_document set retire_reason = 'otra razón'
                     where id = '99000000-0000-4000-8000-000000000070' $$), 5), 'P0001',
  'a retired document stays retired as it was');
select is(left(pg_temp.run_as('origenlab_api', $$ delete from crm.bidder_document
                     where id = '99000000-0000-4000-8000-000000000070' $$), 5), '42501',
  'the API cannot delete a document');
select is(left(pg_temp.run_as('origenlab_worker', $$ insert into crm.bidder_document
    (kind, title, storage_path, sha256, content_type, byte_size, original_filename, added_by_operator_id)
  values ('otro', 'x', 'bidder/' || repeat('5', 64) || '.pdf', repeat('5', 64), 'application/pdf', 1,
          'x.pdf', '99000000-0000-4000-8000-000000000001') $$), 5), '42501',
  'the worker reads bidder documents, never adds one');

-- ── evidence.bid_preparation (#62) ──────────────────────────────────────────────────────────
select is(pg_temp.run_as('origenlab_api', $$ insert into evidence.bid_preparation
    (id, opportunity_id, phase, status, annex_document_ids, requested_by_operator_id)
  values ('99000000-0000-4000-8000-000000000080', '99000000-0000-4000-8000-000000000051', 'read', 'queued',
          array['99000000-0000-4000-8000-000000000071']::uuid[], '99000000-0000-4000-8000-000000000001') $$), 'ok',
  'the API queues a read');
select is(left(pg_temp.run_as('origenlab_api', $$ insert into evidence.bid_preparation
    (opportunity_id, phase, status, annex_document_ids, requested_by_operator_id)
  values ('99000000-0000-4000-8000-000000000051', 'read', 'queued',
          array['99000000-0000-4000-8000-000000000071']::uuid[], '99000000-0000-4000-8000-000000000001') $$), 5), '23505',
  'one queued or running preparation per case and phase');
select is(left(pg_temp.run_as('origenlab_api', $$ insert into evidence.bid_preparation
    (opportunity_id, phase, status, annex_document_ids, requested_by_operator_id)
  values ('99000000-0000-4000-8000-000000000051', 'fill', 'queued',
          array['99000000-0000-4000-8000-000000000071']::uuid[], '99000000-0000-4000-8000-000000000001') $$), 5), '23514',
  'a fill is based on a read');
select is(left(pg_temp.run_as('origenlab_api', $$ insert into evidence.bid_preparation
    (opportunity_id, phase, status, annex_document_ids, quote_revision_id, requested_by_operator_id)
  values ('99000000-0000-4000-8000-000000000050', 'read', 'queued',
          array['99000000-0000-4000-8000-000000000071']::uuid[], gen_random_uuid(),
          '99000000-0000-4000-8000-000000000001') $$), 5), '23514',
  'only a fill names the offer''s quote revision');
select is(left(pg_temp.run_as('origenlab_api', $$ insert into evidence.bid_preparation
    (opportunity_id, phase, status, annex_document_ids, requested_by_operator_id)
  values ('99000000-0000-4000-8000-000000000050', 'read', 'queued', '{}'::uuid[],
          '99000000-0000-4000-8000-000000000001') $$), 5), '23514',
  'a preparation reads at least one anexo');
select is(left(pg_temp.run_as('origenlab_api', $$ insert into evidence.bid_preparation
    (opportunity_id, phase, status, annex_document_ids, failure_code, requested_by_operator_id)
  values ('99000000-0000-4000-8000-000000000050', 'read', 'queued',
          array['99000000-0000-4000-8000-000000000071']::uuid[], 'boom', '99000000-0000-4000-8000-000000000001') $$), 5), '23514',
  'a failure code only on a failed or budget-stopped preparation');
select is(pg_temp.run_as('origenlab_worker', $$ update evidence.bid_preparation
                     set status = 'ready', started_at = now(), finished_at = now(),
                         result = '{"resumen": "x"}'::jsonb, outputs = '[]'::jsonb,
                         models = '{"read": "modelo-ejemplo"}'::jsonb, updated_at = now()
                   where id = '99000000-0000-4000-8000-000000000080' $$), 'ok',
  'the worker writes the result back onto the row');
select is(left(pg_temp.run_as('origenlab_worker', $$ update evidence.bid_preparation set opportunity_id = '99000000-0000-4000-8000-000000000050'
                     where id = '99000000-0000-4000-8000-000000000080' $$), 5), '42501',
  'the worker never moves a preparation to another case');
select is(left(pg_temp.run_as('origenlab_api', $$ update evidence.bid_preparation set status = 'ready'
                     where id = '99000000-0000-4000-8000-000000000080' $$), 5), '42501',
  'the API requests, the worker runs: the API never updates a preparation');
select is(pg_temp.run_as('origenlab_api', $$ insert into evidence.bid_preparation
    (id, opportunity_id, phase, status, annex_document_ids, based_on_preparation_id, requested_by_operator_id)
  values ('99000000-0000-4000-8000-000000000081', '99000000-0000-4000-8000-000000000051', 'fill', 'queued',
          array['99000000-0000-4000-8000-000000000071']::uuid[], '99000000-0000-4000-8000-000000000080',
          '99000000-0000-4000-8000-000000000001') $$), 'ok',
  'a fill based on the ready read is queued');
select is(pg_temp.run_as('origenlab_api', $$ insert into evidence.bid_preparation
    (opportunity_id, phase, status, annex_document_ids, requested_by_operator_id)
  values ('99000000-0000-4000-8000-000000000051', 'read', 'queued',
          array['99000000-0000-4000-8000-000000000071']::uuid[], '99000000-0000-4000-8000-000000000001') $$), 'ok',
  'once the read is done, a re-run is a new row');

-- ── platform.model_usage (#63) ──────────────────────────────────────────────────────────────
select ok(not has_table_privilege('origenlab_api', 'platform.model_usage', 'INSERT'),
  'the API never records model spend');
select is(pg_temp.run_as('origenlab_worker', $$ insert into platform.model_usage
    (feature, model, step, bid_preparation_id, input_tokens, output_tokens, cache_read_tokens, cache_write_tokens, usd_cost)
  values ('licitaciones', 'modelo-ejemplo', 'read', '99000000-0000-4000-8000-000000000080', 1000, 200, 0, 0, 0.012345) $$), 'ok',
  'the worker records a model call');
select is(left(pg_temp.run_as('origenlab_worker', $$ insert into platform.model_usage
    (feature, model, step, input_tokens, output_tokens, cache_read_tokens, cache_write_tokens, usd_cost)
  values ('licitaciones', 'modelo-ejemplo', 'read', -1, 0, 0, 0, 0) $$), 5), '23514',
  'token counts are never negative');
select is(left(pg_temp.run_as('origenlab_worker', $$ insert into platform.model_usage
    (feature, model, step, input_tokens, output_tokens, cache_read_tokens, cache_write_tokens, usd_cost)
  values ('triage', 'modelo-ejemplo', 'read', 0, 0, 0, 0, 0) $$), 5), '23514',
  'spend is recorded for a known feature');
select is(left(pg_temp.run_as('origenlab_worker', $$ update platform.model_usage set usd_cost = 0 $$), 5), '42501',
  'the worker never rewrites spend');
select is(left(pg_temp.run_as('origenlab_owner', $$ delete from platform.model_usage $$), 5), 'P0001',
  'spend is never deleted, even by its owner');

-- ── procurement.notice and catalog.product_document ─────────────────────────────────────────
select ok(has_table_privilege('origenlab_api', 'procurement.notice', 'INSERT'),
  'the API records the notice of a tender opened by hand');
select is(pg_temp.run_as('origenlab_api', $$ insert into procurement.notice (id, codigo_externo, head, title, buyer_name)
  values ('99000000-0000-4000-8000-000000000090', '1234-5-LE26', '{}'::jsonb, 'Compra de balanzas', 'Institución Ejemplo') $$), 'ok',
  'the API inserts a notice');
select is(pg_temp.run_as('origenlab_api', $$ update procurement.notice
                     set promoted_opportunity_id = '99000000-0000-4000-8000-000000000051', closes_at = now(),
                         buyer_code = '12345', updated_at = now()
                   where id = '99000000-0000-4000-8000-000000000090' $$), 'ok',
  'and promotes it to the tender case with what it read');
select is(left(pg_temp.run_as('origenlab_api', $$ update procurement.notice set source_status = 'x'
                     where id = '99000000-0000-4000-8000-000000000090' $$), 5), '42501',
  'the API never writes the notice''s source status');
select ok(has_table_privilege('origenlab_worker', 'catalog.product_document', 'SELECT'),
  'the worker reads product data sheets (the spec sheets of a bid)');

select * from finish();
rollback;
