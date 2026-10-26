-- Slice 7 — Prospectos fase 2: evidence.prospect_search_run (#67) and the
-- platform.model_usage FK column. Fictitious fixtures only; no real ANID data.
begin;
create extension if not exists pgtap with schema extensions;
select plan(22);

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

-- ── shape: evidence.prospect_search_run ─────────────────────────────────────────────────────
select has_table('evidence', 'prospect_search_run', 'evidence.prospect_search_run exists (#67)');
select is(
  (select count(*)::int from pg_class c join pg_namespace n on n.oid = c.relnamespace
    where c.relkind = 'r' and n.nspname = 'evidence' and c.relname = 'prospect_search_run'
      and c.relrowsecurity and not c.relforcerowsecurity),
  1, 'RLS is enabled and not forced on evidence.prospect_search_run');

-- ── shape: platform.model_usage FK column ───────────────────────────────────────────────────
select has_column('platform', 'model_usage', 'prospect_search_run_id',
  'platform.model_usage has prospect_search_run_id column');

-- ── fixtures: operator for manual runs ──────────────────────────────────────────────────────
set local role origenlab_owner;
insert into platform.operator (id, auth_user_id, email_norm, display_name, role, status) values
  ('99000000-0000-4000-9900-000000000301', '99000000-0000-4000-9900-0000000003f1',
   'search.operator@ficticio.invalid', 'Operador Ficticio', 'sales', 'active');
reset role;

-- ── worker: insert a daily run ───────────────────────────────────────────────────────────────
select is(pg_temp.run_as('origenlab_worker', $$ insert into evidence.prospect_search_run
    (id, trigger, status)
  values ('99000000-0000-4000-9900-000000000310', 'daily', 'queued') $$), 'ok',
  'the worker inserts a daily queued run (no operator)');

-- ── partial unique index: only one queued-or-running at a time ───────────────────────────────
select is(left(pg_temp.run_as('origenlab_worker', $$ insert into evidence.prospect_search_run
    (trigger, status)
  values ('daily', 'queued') $$), 5), '23505',
  'a second queued run is rejected by the partial unique index');

-- ── worker: claim run (queued → running) ─────────────────────────────────────────────────────
select is(pg_temp.run_as('origenlab_worker', $$ update evidence.prospect_search_run
    set status = 'running', started_at = now(), updated_at = now()
  where id = '99000000-0000-4000-9900-000000000310' and status = 'queued' $$), 'ok',
  'the worker claims a queued run (running)');

-- ── worker: finish run (running → done) ──────────────────────────────────────────────────────
select is(pg_temp.run_as('origenlab_worker', $$ update evidence.prospect_search_run
    set status = 'done', finished_at = now(), counts = '{"added":3}'::jsonb, updated_at = now()
  where id = '99000000-0000-4000-9900-000000000310' $$), 'ok',
  'the worker marks a run done with counts');

-- ── partial index released after done ───────────────────────────────────────────────────────
select is(pg_temp.run_as('origenlab_worker', $$ insert into evidence.prospect_search_run
    (id, trigger, status)
  values ('99000000-0000-4000-9900-000000000311', 'daily', 'queued') $$), 'ok',
  'a second queued run is allowed once the previous run is done');

-- ── api: insert a manual run ─────────────────────────────────────────────────────────────────
-- First mark the worker-inserted run as done so the unique index is clear.
set local role origenlab_owner;
update evidence.prospect_search_run set status = 'done', finished_at = now(), updated_at = now()
  where id = '99000000-0000-4000-9900-000000000311';
reset role;

select is(pg_temp.run_as('origenlab_api', $$ insert into evidence.prospect_search_run
    (id, trigger, requested_by_operator_id, status)
  values ('99000000-0000-4000-9900-000000000320', 'manual',
          '99000000-0000-4000-9900-000000000301', 'queued') $$), 'ok',
  'the API inserts a manual queued run (with operator)');

-- ── constraints ──────────────────────────────────────────────────────────────────────────────
select is(left(pg_temp.run_as('origenlab_api', $$ insert into evidence.prospect_search_run
    (trigger, status)
  values ('manual', 'queued') $$), 5), '23514',
  'a manual run without operator_id is rejected (check constraint)');

select is(left(pg_temp.run_as('origenlab_api', $$ insert into evidence.prospect_search_run
    (trigger, requested_by_operator_id, status)
  values ('daily', '99000000-0000-4000-9900-000000000301', 'done') $$), 5), '23514',
  'a daily run with operator_id is rejected (check constraint)');

select is(left(pg_temp.run_as('origenlab_api', $$ insert into evidence.prospect_search_run
    (trigger, status)
  values ('anid_cache', 'queued') $$), 5), '23514',
  'an invalid trigger is rejected');

select is(left(pg_temp.run_as('origenlab_api', $$ insert into evidence.prospect_search_run
    (trigger, status)
  values ('daily', 'pending') $$), 5), '23514',
  'an invalid status is rejected');

-- ── api: cannot update status (only select + insert) ────────────────────────────────────────
select is(left(pg_temp.run_as('origenlab_api', $$ update evidence.prospect_search_run
    set status = 'running' where id = '99000000-0000-4000-9900-000000000320' $$), 5), '42501',
  'the API cannot update a run row');

-- ── worker: cannot update non-mutable fields ─────────────────────────────────────────────────
select is(left(pg_temp.run_as('origenlab_worker', $$ update evidence.prospect_search_run
    set trigger = 'manual' where id = '99000000-0000-4000-9900-000000000320' $$), 5), '42501',
  'the worker cannot update the trigger column');

-- ── append-only: delete is refused ───────────────────────────────────────────────────────────
select is(left(pg_temp.run_as('origenlab_owner', $$ delete from evidence.prospect_search_run
    where id = '99000000-0000-4000-9900-000000000310' $$), 5), 'P0001',
  'a search run row is never deleted');

-- ── model_usage: feature 'prospectos' is accepted ────────────────────────────────────────────
-- Mark the manual run as running so we can link model usage to it.
set local role origenlab_owner;
update evidence.prospect_search_run set status = 'running', started_at = now(), updated_at = now()
  where id = '99000000-0000-4000-9900-000000000320';
reset role;

select is(pg_temp.run_as('origenlab_worker', $$ insert into platform.model_usage
    (feature, step, model, input_tokens, output_tokens, usd_cost, prospect_search_run_id)
  values ('prospectos', 'prospect', 'claude-haiku-4-5', 100, 50, 0.00001,
          '99000000-0000-4000-9900-000000000320') $$), 'ok',
  'model_usage accepts feature=prospectos with prospect_search_run_id FK');

select is(left(pg_temp.run_as('origenlab_worker', $$ insert into platform.model_usage
    (feature, step, model, input_tokens, output_tokens, usd_cost)
  values ('unknown_feature', 'prospect', 'claude-haiku-4-5', 100, 50, 0.00001) $$), 5), '23514',
  'unknown feature is rejected in model_usage');

-- ── access: anonymous and service_role cannot read ───────────────────────────────────────────
select is(left(pg_temp.run_as('anon', $$ select count(*) from evidence.prospect_search_run $$), 5),
  '42501', 'anon is denied evidence.prospect_search_run');
select is(left(pg_temp.run_as('authenticated', $$ select count(*) from evidence.prospect_search_run $$), 5),
  '42501', 'authenticated (Supabase) is denied evidence.prospect_search_run');

-- ── access: api and worker can read ──────────────────────────────────────────────────────────
select is(pg_temp.run_as('origenlab_api', $$ select count(*)::text from evidence.prospect_search_run $$),
  'ok', 'the API can read evidence.prospect_search_run');
select is(pg_temp.run_as('origenlab_worker', $$ select count(*)::text from evidence.prospect_search_run $$),
  'ok', 'the worker can read evidence.prospect_search_run');

select * from finish();
rollback;
