-- Slice 7 — Prospectos (DOMAIN.md §7, #66): a person's append-only corrections to a prospect
-- finding. Synthetic fixtures only.
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

-- ── shape ───────────────────────────────────────────────────────────────────────────────────
select has_table('crm', 'prospect_edit', 'crm.prospect_edit exists (#66)');
select is(
  (select count(*)::int from pg_class c join pg_namespace n on n.oid = c.relnamespace
    where c.relkind = 'r' and n.nspname = 'crm' and c.relname = 'prospect_edit'
      and c.relrowsecurity and not c.relforcerowsecurity),
  1, 'RLS is enabled and not forced on crm.prospect_edit');

-- ── fixtures ────────────────────────────────────────────────────────────────────────────────
set local role origenlab_owner;
insert into platform.operator (id, auth_user_id, email_norm, display_name, role, status) values
  ('99000000-0000-4000-9900-000000000201', '99000000-0000-4000-9900-0000000002f1',
   'prospect.editor@example.test', 'Prospect Editor', 'sales', 'active');
insert into evidence.source_record
    (id, kind, dedupe_key, payload, payload_sha256, source_uri, acquired_at, review_status)
  values ('99000000-0000-4000-9900-000000000210', 'prospect_finding',
          'prospect:manual:demo-edit:instituto ficticio', '{"person_name": "Persona Ficticia"}'::jsonb,
          repeat('c', 64), 'https://example.test/fondo', now(), 'pending');
reset role;

-- ── crm.prospect_edit: corrections ──────────────────────────────────────────────────────────
select is(pg_temp.run_as('origenlab_api', $$ insert into crm.prospect_edit
    (id, source_record_id, changes, edited_by_operator_id)
  values ('99000000-0000-4000-9900-000000000220', '99000000-0000-4000-9900-000000000210',
          '{"email": "persona@ficticia.example", "email_source_note": "sitio del instituto"}'::jsonb,
          '99000000-0000-4000-9900-000000000201') $$), 'ok',
  'the API records a correction');
select is(pg_temp.run_as('origenlab_api', $$ insert into crm.prospect_edit
    (source_record_id, changes, edited_by_operator_id)
  values ('99000000-0000-4000-9900-000000000210', '{"phone": null, "equipment": ["Balanzas"]}'::jsonb,
          '99000000-0000-4000-9900-000000000201') $$), 'ok',
  'a null clears a field; a list is a value');
select is(left(pg_temp.run_as('origenlab_api', $$ insert into crm.prospect_edit
    (source_record_id, changes, edited_by_operator_id)
  values ('99000000-0000-4000-9900-000000000210', '{}'::jsonb,
          '99000000-0000-4000-9900-000000000201') $$), 5), '23514',
  'a correction changes something');
select is(left(pg_temp.run_as('origenlab_api', $$ insert into crm.prospect_edit
    (source_record_id, changes, edited_by_operator_id)
  values ('99000000-0000-4000-9900-000000000210', '["email"]'::jsonb,
          '99000000-0000-4000-9900-000000000201') $$), 5), '23514',
  'changes is an object');
select is(left(pg_temp.run_as('origenlab_api', $$ insert into crm.prospect_edit
    (source_record_id, changes, edited_by_operator_id)
  values ('99000000-0000-4000-9900-000000000210', '{"score": 99}'::jsonb,
          '99000000-0000-4000-9900-000000000201') $$), 5), '23514',
  'only the editable fields can be corrected');

-- ── append-only ─────────────────────────────────────────────────────────────────────────────
select is(left(pg_temp.run_as('origenlab_owner', $$ update crm.prospect_edit set changes = '{"role": "x"}'
  where id = '99000000-0000-4000-9900-000000000220' $$), 5), 'P0001',
  'a correction is append-only even for its owner');
select is(left(pg_temp.run_as('origenlab_owner', $$ delete from crm.prospect_edit
  where id = '99000000-0000-4000-9900-000000000220' $$), 5), 'P0001',
  'a correction is never deleted');

-- ── access control ──────────────────────────────────────────────────────────────────────────
select is(pg_temp.run_as('origenlab_worker', $$ select 1 from crm.prospect_edit $$), 'ok',
  'the worker reads corrections');
select is(left(pg_temp.run_as('origenlab_worker', $$ insert into crm.prospect_edit
    (source_record_id, changes, edited_by_operator_id)
  values ('99000000-0000-4000-9900-000000000210', '{"role": "x"}'::jsonb,
          '99000000-0000-4000-9900-000000000201') $$), 5), '42501',
  'the worker never writes a correction');
select is(left(pg_temp.run_as('origenlab_api', $$ update evidence.source_record set payload = '{}'::jsonb
  where id = '99000000-0000-4000-9900-000000000210' $$), 5), '42501',
  'the API still never rewrites the finding itself');
select is(
  (select count(*)::int from information_schema.role_table_grants
    where table_schema = 'crm' and table_name = 'prospect_edit'
      and grantee in ('anon', 'authenticated', 'service_role', 'PUBLIC')),
  0, 'no grant to anon, authenticated, service_role or public');

-- ── domain events ────────────────────────────────────────────────────────────────────────────
select ok(
  (select pg_get_constraintdef((select oid from pg_constraint where conname = 'domain_event_type_check'))
     like '%source_record.prospect_edited%'),
  'source_record.prospect_edited is in the event_type vocabulary');

create function pg_temp.valid_as_api(p_kind text, p_type text) returns boolean
language plpgsql as $f$
declare r boolean;
begin
  set local role origenlab_api;
  r := crm.domain_event_is_valid(p_kind, p_type, 1::smallint, '{}'::jsonb);
  reset role;
  return r;
end
$f$;
select ok(pg_temp.valid_as_api('source_record', 'source_record.prospect_edited'),
  'aggregate kind source_record carries source_record.prospect_edited');
select is(pg_temp.run_as('origenlab_api', $$ insert into crm.domain_event
    (aggregate_kind, aggregate_id, seq, event_type, payload_version, payload, actor_kind, actor_operator_id)
  values ('source_record', '99000000-0000-4000-9900-000000000210', 1, 'source_record.prospect_edited', 1,
          '{"fields": ["email"]}'::jsonb, 'operator',
          '99000000-0000-4000-9900-000000000201') $$), 'ok',
  'a source_record.prospect_edited domain event is accepted');

select * from finish();
rollback;
