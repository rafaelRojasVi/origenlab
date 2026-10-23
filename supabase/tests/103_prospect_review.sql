-- Slice 7 — Prospectos (DOMAIN.md §7, #65): the worker's prospect_finding evidence and the
-- person's append-only verdict on it. Synthetic fixtures only.
begin;
create extension if not exists pgtap with schema extensions;
select plan(21);

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
select has_table('crm', 'prospect_review', 'crm.prospect_review exists (#65)');
select is(
  (select count(*)::int from pg_class c join pg_namespace n on n.oid = c.relnamespace
    where c.relkind = 'r' and n.nspname = 'crm' and c.relname = 'prospect_review'
      and c.relrowsecurity and not c.relforcerowsecurity),
  1, 'RLS is enabled and not forced on crm.prospect_review');

-- ── fixtures ────────────────────────────────────────────────────────────────────────────────
set local role origenlab_owner;
insert into platform.operator (id, auth_user_id, email_norm, display_name, role, status) values
  ('99000000-0000-4000-9900-000000000101', '99000000-0000-4000-9900-0000000001f1',
   'prospect.operator@example.test', 'Prospect Operator', 'sales', 'active');
reset role;

-- ── the worker writes a prospect finding ────────────────────────────────────────────────────
select is(pg_temp.run_as('origenlab_worker', $$ insert into evidence.source_record
    (id, kind, dedupe_key, payload, payload_sha256, source_uri, acquired_at, review_status)
  values ('99000000-0000-4000-9900-000000000110', 'prospect_finding',
          'prospect:manual:demo-1:instituto ficticio', '{"why_now": "Ganó un fondo"}'::jsonb,
          repeat('a', 64), 'https://example.test/fondo', now(), 'pending') $$), 'ok',
  'the worker records a prospect_finding source record');
select is(left(pg_temp.run_as('origenlab_api', $$ insert into evidence.source_record
    (kind, dedupe_key, payload, payload_sha256, source_uri, acquired_at, review_status)
  values ('prospect_finding', 'prospect:manual:demo-2:x', '{}'::jsonb, repeat('b', 64),
          'https://example.test/x', now(), 'pending') $$), 5), '42501',
  'the API never writes a finding itself');

-- ── crm.prospect_review: verdicts ───────────────────────────────────────────────────────────
select is(pg_temp.run_as('origenlab_api', $$ insert into crm.prospect_review
    (id, source_record_id, verdict, reason, reviewed_by_operator_id)
  values ('99000000-0000-4000-9900-000000000120', '99000000-0000-4000-9900-000000000110',
          'contactar', 'fuente: https://example.test/fondo', '99000000-0000-4000-9900-000000000101') $$), 'ok',
  'the API records contactar');
select is(pg_temp.run_as('origenlab_api', $$ insert into crm.prospect_review
    (source_record_id, verdict, reason, reviewed_by_operator_id)
  values ('99000000-0000-4000-9900-000000000110', 'descartar', 'No es nuestro rubro',
          '99000000-0000-4000-9900-000000000101') $$), 'ok',
  'the API records descartar with a reason');
select is(pg_temp.run_as('origenlab_api', $$ insert into crm.prospect_review
    (source_record_id, verdict, remind_on, reviewed_by_operator_id)
  values ('99000000-0000-4000-9900-000000000110', 'mas_tarde', date '2026-11-01',
          '99000000-0000-4000-9900-000000000101') $$), 'ok',
  'the API records mas_tarde with a date');
select is(pg_temp.run_as('origenlab_api', $$ insert into crm.prospect_review
    (source_record_id, verdict, reviewed_by_operator_id)
  values ('99000000-0000-4000-9900-000000000110', 'reabrir',
          '99000000-0000-4000-9900-000000000101') $$), 'ok',
  'the API records reabrir');
select is(left(pg_temp.run_as('origenlab_api', $$ insert into crm.prospect_review
    (source_record_id, verdict, reviewed_by_operator_id)
  values ('99000000-0000-4000-9900-000000000110', 'interesa',
          '99000000-0000-4000-9900-000000000101') $$), 5), '23514',
  'verdict is contactar, mas_tarde, descartar or reabrir');
select is(left(pg_temp.run_as('origenlab_api', $$ insert into crm.prospect_review
    (source_record_id, verdict, reviewed_by_operator_id)
  values ('99000000-0000-4000-9900-000000000110', 'mas_tarde',
          '99000000-0000-4000-9900-000000000101') $$), 5), '23514',
  'mas_tarde requires remind_on');
select is(left(pg_temp.run_as('origenlab_api', $$ insert into crm.prospect_review
    (source_record_id, verdict, remind_on, reviewed_by_operator_id)
  values ('99000000-0000-4000-9900-000000000110', 'contactar', date '2026-11-01',
          '99000000-0000-4000-9900-000000000101') $$), 5), '23514',
  'remind_on is only for mas_tarde');
select is(left(pg_temp.run_as('origenlab_api', $$ insert into crm.prospect_review
    (source_record_id, verdict, reason, reviewed_by_operator_id)
  values ('99000000-0000-4000-9900-000000000110', 'descartar', '   ',
          '99000000-0000-4000-9900-000000000101') $$), 5), '23514',
  'reason may not be blank');
select is(left(pg_temp.run_as('origenlab_api', $$ insert into crm.prospect_review
    (source_record_id, verdict, reason, reviewed_by_operator_id)
  values ('99000000-0000-4000-9900-000000000110', 'descartar', repeat('x', 501),
          '99000000-0000-4000-9900-000000000101') $$), 5), '23514',
  'reason is at most 500 characters');

-- ── append-only ─────────────────────────────────────────────────────────────────────────────
select is(left(pg_temp.run_as('origenlab_owner', $$ update crm.prospect_review set verdict = 'descartar'
  where id = '99000000-0000-4000-9900-000000000120' $$), 5), 'P0001',
  'a verdict is append-only even for its owner');
select is(left(pg_temp.run_as('origenlab_owner', $$ delete from crm.prospect_review
  where id = '99000000-0000-4000-9900-000000000120' $$), 5), 'P0001',
  'a verdict is never deleted');

-- ── access control ──────────────────────────────────────────────────────────────────────────
select is(pg_temp.run_as('origenlab_worker', $$ select 1 from crm.prospect_review $$), 'ok',
  'the worker reads verdicts');
select is(left(pg_temp.run_as('origenlab_worker', $$ insert into crm.prospect_review
    (source_record_id, verdict, reviewed_by_operator_id)
  values ('99000000-0000-4000-9900-000000000110', 'reabrir',
          '99000000-0000-4000-9900-000000000101') $$), 5), '42501',
  'the worker never writes a verdict');
select is(
  (select count(*)::int from information_schema.role_table_grants
    where table_schema = 'crm' and table_name = 'prospect_review'
      and grantee in ('anon', 'authenticated', 'service_role', 'PUBLIC')),
  0, 'no grant to anon, authenticated, service_role or public');

-- ── domain events ────────────────────────────────────────────────────────────────────────────
select ok(
  (select pg_get_constraintdef((select oid from pg_constraint where conname = 'domain_event_type_check'))
     like '%source_record.prospect_reviewed%'),
  'source_record.prospect_reviewed is in the event_type vocabulary');

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
select ok(pg_temp.valid_as_api('source_record', 'source_record.prospect_reviewed'),
  'aggregate kind source_record carries source_record.prospect_reviewed');
select is(pg_temp.run_as('origenlab_api', $$ insert into crm.domain_event
    (aggregate_kind, aggregate_id, seq, event_type, payload_version, payload, actor_kind, actor_operator_id)
  values ('source_record', '99000000-0000-4000-9900-000000000110', 1, 'source_record.prospect_reviewed', 1,
          '{"verdict": "contactar"}'::jsonb, 'operator',
          '99000000-0000-4000-9900-000000000101') $$), 'ok',
  'a source_record.prospect_reviewed domain event is accepted');

select * from finish();
rollback;
