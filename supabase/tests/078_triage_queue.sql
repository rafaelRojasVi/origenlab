-- Slice 4 — the worker's Procrastinate queue and the triage assertion kinds, proven in the database.
--
-- Companions: 20261006180000_slice4_procrastinate_triage_queue.sql and
-- 20261006180100_slice4_triage_assertion_kinds.sql. The `procrastinate` schema is infrastructure
-- outside the seven application schemas, so the inventory, grant-boundary, RLS and definer suites
-- (010, 030, 040, 050, 070) do not see it; this file holds it to the same rules by itself:
-- owned by origenlab_owner, reachable by origenlab_worker only, RLS on every table, no SECURITY
-- DEFINER, every function's search_path pinned. Everything below is rolled back.
begin;
create extension if not exists pgtap with schema extensions;
select plan(24);

grant origenlab_api    to session_user with set true, inherit false;
grant origenlab_worker to session_user with set true, inherit false;

create function pg_temp.query_as(p_role text, p_sql text) returns text
language plpgsql as $$
declare v text;
begin
  execute format('set role %I', p_role);
  execute p_sql into v;
  reset role;
  return v;
exception when others then
  reset role;
  return sqlstate || ': ' || sqlerrm;
end
$$;

-- ── the schema and its owner ────────────────────────────────────────────────────────────────
select has_schema('procrastinate');
select is((select nspowner::regrole::text from pg_namespace where nspname = 'procrastinate'),
  'origenlab_owner', 'the queue schema is owned by origenlab_owner');
select set_eq(
  $$ select c.relname::text from pg_class c join pg_namespace n on n.oid = c.relnamespace
      where n.nspname = 'procrastinate' and c.relkind = 'r' $$,
  array['procrastinate_jobs', 'procrastinate_events', 'procrastinate_periodic_defers', 'procrastinate_workers'],
  'exactly the four Procrastinate 3.10 tables');
select is(
  (select count(*)::int from pg_class c join pg_namespace n on n.oid = c.relnamespace
    where n.nspname = 'procrastinate' and c.relowner <> 'origenlab_owner'::regrole),
  0, 'every relation in the queue schema is owned by origenlab_owner');
select is(
  (select count(*)::int from pg_proc p join pg_namespace n on n.oid = p.pronamespace
    where n.nspname = 'procrastinate' and p.proowner <> 'origenlab_owner'::regrole),
  0, 'every queue function is owned by origenlab_owner');

-- ── functions: invoker, pinned ──────────────────────────────────────────────────────────────
select is(
  (select count(*)::int from pg_proc p join pg_namespace n on n.oid = p.pronamespace
    where n.nspname = 'procrastinate' and p.prosecdef),
  0, 'no queue function is SECURITY DEFINER');
select is(
  (select count(*)::int from pg_proc p join pg_namespace n on n.oid = p.pronamespace
    where n.nspname = 'procrastinate'
      and not coalesce(p.proconfig @> array['search_path=procrastinate, pg_catalog'], false)),
  0, 'every queue function pins search_path = procrastinate, pg_catalog');
select ok(
  (select count(*) from pg_proc p join pg_namespace n on n.oid = p.pronamespace where n.nspname = 'procrastinate') >= 20,
  'the vendored functions are present');

-- ── RLS ─────────────────────────────────────────────────────────────────────────────────────
select is(
  (select count(*)::int from pg_class c join pg_namespace n on n.oid = c.relnamespace
    where n.nspname = 'procrastinate' and c.relkind = 'r' and c.relrowsecurity and not c.relforcerowsecurity),
  4, 'RLS is enabled, not forced, on all four queue tables');
select set_eq(
  $$ select tablename || ':' || policyname || ':' || cmd from pg_policies where schemaname = 'procrastinate' $$,
  array(select t || ':origenlab_worker_' || lower(c) || ':' || c
          from unnest(array['procrastinate_jobs', 'procrastinate_events',
                            'procrastinate_periodic_defers', 'procrastinate_workers']) t,
               unnest(array['SELECT', 'INSERT', 'UPDATE', 'DELETE']) c),
  'one named worker policy per command on each queue table, and no other');
select is(
  (select count(*)::int from pg_policies where schemaname = 'procrastinate' and roles <> array['origenlab_worker']::name[]),
  0, 'every queue policy is for origenlab_worker alone');

-- ── who can reach it ────────────────────────────────────────────────────────────────────────
select is(
  (select count(*)::int from unnest(array['anon', 'authenticated', 'service_role', 'origenlab_api']) r(n)
    where has_schema_privilege(r.n, 'procrastinate', 'USAGE') or has_schema_privilege(r.n, 'procrastinate', 'CREATE')),
  0, 'anon, authenticated, service_role and origenlab_api hold no USAGE or CREATE on the queue schema');
select is(
  (select count(*)::int from pg_namespace n, aclexplode(coalesce(n.nspacl, acldefault('n', n.nspowner))) a
    where n.nspname = 'procrastinate' and a.grantee = 0),
  0, 'PUBLIC holds no privilege on the queue schema');
select is(
  (select count(*)::int from pg_proc p join pg_namespace n on n.oid = p.pronamespace,
          aclexplode(coalesce(p.proacl, acldefault('f', p.proowner))) a
    where n.nspname = 'procrastinate' and a.grantee = 0),
  0, 'PUBLIC holds no EXECUTE on any queue function');
select ok(has_schema_privilege('origenlab_worker', 'procrastinate', 'USAGE')
          and not has_schema_privilege('origenlab_worker', 'procrastinate', 'CREATE'),
  'origenlab_worker holds USAGE and never CREATE on the queue schema');
select is(
  (select count(*)::int from pg_class c join pg_namespace n on n.oid = c.relnamespace,
          unnest(array['SELECT', 'INSERT', 'UPDATE', 'DELETE', 'TRUNCATE', 'TRIGGER']) p(priv)
    where n.nspname = 'procrastinate' and c.relkind = 'r' and has_table_privilege('origenlab_api', c.oid, p.priv)),
  0, 'origenlab_api holds no privilege on any queue table');
select is(
  (select count(*)::int from pg_class c join pg_namespace n on n.oid = c.relnamespace,
          unnest(array['TRUNCATE', 'TRIGGER', 'REFERENCES']) p(priv)
    where n.nspname = 'procrastinate' and c.relkind = 'r' and has_table_privilege('origenlab_worker', c.oid, p.priv)),
  0, 'origenlab_worker holds no TRUNCATE, TRIGGER or REFERENCES on the queue tables');
select ok(not has_database_privilege('origenlab_owner', current_database(), 'CREATE'),
  'the migration gave CREATE on the database back: origenlab_owner holds none');

-- ── the worker can run the queue ────────────────────────────────────────────────────────────
select is(
  pg_temp.query_as('origenlab_worker', $$
    select array_length(procrastinate.procrastinate_defer_jobs_v1(array[
      row('triage', 'triage_message', 0, null, 'triage:pgtap', '{"message_id": "x"}'::jsonb, null)
        ::procrastinate.procrastinate_job_to_defer_v1]), 1)::text $$),
  '1', 'origenlab_worker defers a job through the queue function');
select is(
  pg_temp.query_as('origenlab_worker', $$
    select count(*)::text from procrastinate.procrastinate_events e
      join procrastinate.procrastinate_jobs j on j.id = e.job_id where j.queueing_lock = 'triage:pgtap' $$),
  '1', 'the deferral is recorded as one event, written by the invoker trigger under RLS');
select is(
  pg_temp.query_as('origenlab_api', $$ select count(*)::text from procrastinate.procrastinate_jobs $$),
  '42501: permission denied for schema procrastinate', 'origenlab_api cannot read the queue');

-- ── the triage assertion kinds ──────────────────────────────────────────────────────────────
set role origenlab_owner;
insert into evidence.source_record (id, kind, dedupe_key, payload, review_status)
values ('78000000-0000-4000-8000-000000000001', 'gmail_message', 'gmail_message:pgtap-078', '{}'::jsonb, 'pending');
select lives_ok($$ insert into evidence.assertion (source_record_id, kind, value_norm, value)
  values ('78000000-0000-4000-8000-000000000001', 'message_triage', 'triage:v1', '{"class": "quote_request"}') $$,
  'message_triage is an assertion kind');
select lives_ok($$ insert into evidence.assertion (source_record_id, kind, value_norm, value)
  values ('78000000-0000-4000-8000-000000000001', 'product_mention', 'UP200HT', '{"text": "UP200Ht"}') $$,
  'product_mention is an assertion kind');
select throws_ok($$ insert into evidence.assertion (source_record_id, kind, value_norm)
  values ('78000000-0000-4000-8000-000000000001', 'lead_status', 'warm') $$,
  '23514', null, 'the vocabulary stays closed');
reset role;

select * from finish();
rollback;
