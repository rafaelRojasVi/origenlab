-- Slice 7 — notice relevance and review (DOMAIN.md §7.12, #64): a worker-written machine match
-- on procurement.notice and the human verdict that follows it. Synthetic fixtures only.
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

-- ── procurement.notice.relevance ────────────────────────────────────────────────────────────
select has_column('procurement', 'notice', 'relevance', 'procurement.notice.relevance exists');
select col_is_nullable('procurement', 'notice', 'relevance', 'relevance is nullable');
select is(left(pg_temp.run_as('origenlab_owner', $$ update procurement.notice
  set relevance = '["not","an","object"]'::jsonb where false $$), 5), '23514',
  'relevance rejects a JSON array');

-- ── shape ───────────────────────────────────────────────────────────────────────────────────
select has_table('crm', 'notice_review', 'crm.notice_review exists (#64)');
select is(
  (select count(*)::int from pg_class c join pg_namespace n on n.oid = c.relnamespace
    where c.relkind = 'r' and n.nspname = 'crm' and c.relname = 'notice_review'
      and c.relrowsecurity and not c.relforcerowsecurity),
  1, 'RLS is enabled and not forced on crm.notice_review');

-- ── fixtures ────────────────────────────────────────────────────────────────────────────────
set local role origenlab_owner;
insert into platform.operator (id, auth_user_id, email_norm, display_name, role, status) values
  ('99000000-0000-4000-9900-000000000001', '99000000-0000-4000-9900-0000000000f1',
   'review.operator@example.test', 'Review Operator', 'sales', 'active');
insert into procurement.notice
  (id, codigo_externo, head, title, buyer_name)
  values ('99000000-0000-4000-9900-000000000010', '9999-1-LE26', '{}'::jsonb,
          'Adquisición de Equipos de Laboratorio', 'Hospital Ejemplo');
reset role;

-- ── notice.relevance written by the worker ──────────────────────────────────────────────────
select is(pg_temp.run_as('origenlab_worker', $$ update procurement.notice
  set relevance = '{"tier": 1, "families": ["equipos"], "checked_at": "2026-10-20T18:00:00Z"}'::jsonb
  where id = '99000000-0000-4000-9900-000000000010' $$), 'ok',
  'the worker writes a relevance object on a notice');
select is(left(pg_temp.run_as('origenlab_api', $$ update procurement.notice
  set relevance = '{}'::jsonb where id = '99000000-0000-4000-9900-000000000010' $$), 5), '42501',
  'the API cannot write relevance (column-scoped update excludes it)');

-- ── crm.notice_review: verdict and reason constraints ───────────────────────────────────────
select is(pg_temp.run_as('origenlab_api', $$ insert into crm.notice_review
    (id, notice_id, verdict, reviewed_by_operator_id)
  values ('99000000-0000-4000-9900-000000000020', '99000000-0000-4000-9900-000000000010',
          'interesa', '99000000-0000-4000-9900-000000000001') $$), 'ok',
  'the API records a verdict of interesa');
select is(pg_temp.run_as('origenlab_api', $$ insert into crm.notice_review
    (id, notice_id, verdict, reason, reviewed_by_operator_id)
  values ('99000000-0000-4000-9900-000000000021', '99000000-0000-4000-9900-000000000010',
          'no_es_nuestro', 'Fuera del rango de productos que ofrecemos',
          '99000000-0000-4000-9900-000000000001') $$), 'ok',
  'the API records a verdict of no_es_nuestro with a reason');
select is(left(pg_temp.run_as('origenlab_api', $$ insert into crm.notice_review
    (notice_id, verdict, reviewed_by_operator_id)
  values ('99000000-0000-4000-9900-000000000010', 'no_aplica',
          '99000000-0000-4000-9900-000000000001') $$), 5), '23514',
  'verdict is interesa or no_es_nuestro');
select is(left(pg_temp.run_as('origenlab_api', $$ insert into crm.notice_review
    (notice_id, verdict, reason, reviewed_by_operator_id)
  values ('99000000-0000-4000-9900-000000000010', 'interesa', '   ',
          '99000000-0000-4000-9900-000000000001') $$), 5), '23514',
  'reason may not be blank (all whitespace)');
select is(left(pg_temp.run_as('origenlab_api', $$ insert into crm.notice_review
    (notice_id, verdict, reason, reviewed_by_operator_id)
  values ('99000000-0000-4000-9900-000000000010', 'interesa', repeat('x', 501),
          '99000000-0000-4000-9900-000000000001') $$), 5), '23514',
  'reason is at most 500 characters');

-- ── append-only ─────────────────────────────────────────────────────────────────────────────
select is(left(pg_temp.run_as('origenlab_owner', $$ update crm.notice_review set verdict = 'no_es_nuestro'
  where id = '99000000-0000-4000-9900-000000000020' $$), 5), 'P0001',
  'a verdict is append-only even for its owner');
select is(left(pg_temp.run_as('origenlab_owner', $$ delete from crm.notice_review
  where id = '99000000-0000-4000-9900-000000000020' $$), 5), 'P0001',
  'a verdict is never deleted');

-- ── access control ──────────────────────────────────────────────────────────────────────────
select is(pg_temp.run_as('origenlab_api', $$ select 1 from crm.notice_review $$), 'ok',
  'the API reads notice reviews');
select is(pg_temp.run_as('origenlab_worker', $$ select 1 from crm.notice_review $$), 'ok',
  'the worker reads notice reviews');
select is(left(pg_temp.run_as('origenlab_worker', $$ insert into crm.notice_review
    (notice_id, verdict, reviewed_by_operator_id)
  values ('99000000-0000-4000-9900-000000000010', 'interesa',
          '99000000-0000-4000-9900-000000000001') $$), 5), '42501',
  'the worker reads verdicts, never writes one');

-- ── domain events ────────────────────────────────────────────────────────────────────────────
select ok(
  (select pg_get_constraintdef((select oid from pg_constraint where conname = 'domain_event_type_check'))
     like '%notice.reviewed%'),
  'notice.reviewed is in the event_type vocabulary');

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
select ok(pg_temp.valid_as_api('notice', 'notice.reviewed'),
  'aggregate kind notice carries notice.reviewed');
select is(pg_temp.run_as('origenlab_api', $$ insert into crm.domain_event
    (id, aggregate_kind, aggregate_id, event_type, payload_version, payload)
  values ('99000000-0000-4000-9900-000000000030', 'notice',
          '99000000-0000-4000-9900-000000000010', 'notice.reviewed', 1, '{}'::jsonb) $$), 'ok',
  'a notice.reviewed domain event is accepted');

select * from finish();
rollback;
