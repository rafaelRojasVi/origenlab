-- Slice 1 — the PIN throttle's only runtime writer, `platform.record_pin_attempt`, proven in the
-- database against every line of ARCHITECTURE.md §6.2 that a catalogue can show.
--
-- Companion to `20260928194000_slice1_pin_throttle_definer.sql`. The runtime API role has no
-- UPDATE — table or column — on `platform.auth_principal` or `platform.operator_profile`, and no
-- UPDATE policy: it can neither write a throttle column nor take a row lock itself. The function
-- is SECURITY DEFINER, owned by origenlab_owner, pins search_path = pg_catalog, qualifies every
-- relation, has no dynamic SQL, takes no counter or timestamp, and only origenlab_api may EXECUTE
-- it — and its body refuses any session_user but origenlab_api.
--
-- The state transitions (the policy, the lock order, concurrency, a locked success refused) need
-- the real `origenlab_api` login as session_user, which pgTAP's single login cannot be; they are
-- proven through direct runtime-login connections in apps/api/tests/test_v2_profile_login.py and
-- supabase/scripts/verify_direct_logins.sh.
--
-- SQLSTATE 42501 privilege. Every value is fictitious.
begin;
create extension if not exists pgtap with schema extensions;
grant usage on schema extensions to origenlab_owner;
grant origenlab_api    to session_user with set true, inherit false;
grant origenlab_worker to session_user with set true, inherit false;

create function pg_temp.run_as(p_role text, p_sql text) returns text
language plpgsql as $$
begin
  execute format('set role %I', p_role);
  execute p_sql;
  reset role;
  return 'ok';
exception when others then
  reset role;
  return sqlstate || ': ' || sqlerrm;
end
$$;

set role origenlab_owner;
select plan(24);

insert into platform.auth_principal (id, email_norm) values
  ('75000000-0000-4000-8000-000000000001', 'limite@example.test');

-- ── 1. the function's catalogue entry ─────────────────────────────────────────────────────────
select has_function('platform', 'record_pin_attempt', array['text', 'uuid', 'uuid'],
  'platform.record_pin_attempt(operation, principal_id, operator_id) exists');
select is((select pg_get_function_arguments('platform.record_pin_attempt(text,uuid,uuid)'::regprocedure)),
  'p_operation text, p_principal_id uuid, p_operator_id uuid, OUT principal_locked boolean, OUT profile_locked boolean, OUT principal_lock_started boolean, OUT profile_lock_started boolean',
  'it takes an operation and two ids — no counter, lock deadline or timestamp — and returns four flags');
select is((select prosecdef from pg_proc where oid = 'platform.record_pin_attempt(text,uuid,uuid)'::regprocedure),
  true, 'record_pin_attempt is SECURITY DEFINER (the closed list, ARCHITECTURE.md §6.2)');
select is((select proowner::regrole::text from pg_proc where oid = 'platform.record_pin_attempt(text,uuid,uuid)'::regprocedure),
  'origenlab_owner', 'record_pin_attempt is owned by the NOLOGIN owner');
select is((select proconfig from pg_proc where oid = 'platform.record_pin_attempt(text,uuid,uuid)'::regprocedure),
  array['search_path=pg_catalog'], 'record_pin_attempt pins search_path = pg_catalog and nothing else');
select ok(has_function_privilege('origenlab_api', 'platform.record_pin_attempt(text,uuid,uuid)', 'EXECUTE'),
  'origenlab_api may EXECUTE record_pin_attempt');
select is(
  (select count(*)::int from (values ('origenlab_worker'), ('origenlab_migrator'), ('anon'), ('authenticated'), ('service_role')) r(n)
    where has_function_privilege(r.n, 'platform.record_pin_attempt(text,uuid,uuid)', 'EXECUTE')),
  0, 'the worker, the migrator, anon, authenticated and service_role may not EXECUTE record_pin_attempt');
select is(
  (select count(*)::int from pg_proc p, aclexplode(p.proacl) a
    where p.oid = 'platform.record_pin_attempt(text,uuid,uuid)'::regprocedure and a.grantee = 0),
  0, 'PUBLIC holds no EXECUTE on record_pin_attempt');
select is(
  (select array_agg(distinct a.grantee::regrole::text order by a.grantee::regrole::text) from pg_proc p, aclexplode(p.proacl) a
    where p.oid = 'platform.record_pin_attempt(text,uuid,uuid)'::regprocedure
      and a.privilege_type = 'EXECUTE' and a.grantee <> p.proowner),
  array['origenlab_api'], 'EXECUTE on record_pin_attempt is granted to origenlab_api and to no one else');
select ok(
  (select prosrc !~* '\mexecute\M' and prosrc !~* '\mformat\s*\(' and prosrc !~* 'quote_(ident|literal|nullable)'
     from pg_proc where oid = 'platform.record_pin_attempt(text,uuid,uuid)'::regprocedure),
  'record_pin_attempt contains no dynamic SQL');
select is(
  (select array_agg(distinct m[2] order by m[2]) from pg_proc p,
          regexp_matches(p.prosrc, '\m(from|join|into|update)\s+([a-z_]+)(\s|\.|$)', 'gi') m
    where p.oid = 'platform.record_pin_attempt(text,uuid,uuid)'::regprocedure
      and m[3] <> '.' and m[2] !~ '^[vp]_'),
  null, 'every relation record_pin_attempt reads or writes is schema-qualified');
select ok(
  (select prosrc ~ 'session_user <> ''origenlab_api'''
     from pg_proc where oid = 'platform.record_pin_attempt(text,uuid,uuid)'::regprocedure),
  'record_pin_attempt asserts session_user, never current_user');

-- ── 2. the calling login is asserted ──────────────────────────────────────────────────────────
-- This login holds EXECUTE only through `set role origenlab_api`; session_user stays itself.
select is(left(pg_temp.run_as('origenlab_api',
  $$select * from platform.record_pin_attempt('begin_attempt', '75000000-0000-4000-8000-000000000001', null)$$), 5), '42501',
  'under set role origenlab_api the body refuses: session_user is not the runtime login');
select is(left(pg_temp.run_as('origenlab_worker',
  $$select * from platform.record_pin_attempt('begin_attempt', '75000000-0000-4000-8000-000000000001', null)$$), 5), '42501',
  'the worker is refused EXECUTE');

-- ── 3. the direct write surface is gone ───────────────────────────────────────────────────────
select is(
  (select count(*)::int from information_schema.column_privileges
    where table_schema = 'platform' and table_name in ('auth_principal', 'operator_profile')
      and grantee = 'origenlab_api' and privilege_type = 'UPDATE'),
  0, 'origenlab_api holds UPDATE on no column of auth_principal or operator_profile');
select is(
  (select count(*)::int from pg_policies
    where schemaname = 'platform' and tablename in ('auth_principal', 'operator_profile') and cmd = 'UPDATE'),
  0, 'no UPDATE policy remains on auth_principal or operator_profile');
select is(left(pg_temp.run_as('origenlab_api', 'update platform.auth_principal set failed_attempts = 0'), 5), '42501',
  'api may not reset a principal''s failure count');
select is(left(pg_temp.run_as('origenlab_api', 'update platform.auth_principal set lockout_count = 0'), 5), '42501',
  'api may not reset a principal''s lockout history');
select is(left(pg_temp.run_as('origenlab_api', 'update platform.auth_principal set locked_until = null'), 5), '42501',
  'api may not lift a principal-wide lock');
select is(left(pg_temp.run_as('origenlab_api', 'update platform.auth_principal set last_failed_at = null'), 5), '42501',
  'api may not rewrite a principal''s failure time');
select is(left(pg_temp.run_as('origenlab_api', 'update platform.operator_profile set lockout_count = 0'), 5), '42501',
  'api may not reset a profile''s lockout history');
select is(left(pg_temp.run_as('origenlab_api', 'update platform.operator_profile set last_failed_at = null'), 5), '42501',
  'api may not rewrite a profile''s failure time');
select is(left(pg_temp.run_as('origenlab_api', 'select 1 from platform.auth_principal for update'), 5), '42501',
  'api may not lock a principal row itself: record_pin_attempt takes the lock');
select is(pg_temp.run_as('origenlab_api', 'select 1 from platform.auth_principal limit 1'), 'ok',
  'api still reads principals');

select * from finish();
rollback;
