-- Slice 0 — docs/MIGRATION.md §5.2 check 9 and docs/ARCHITECTURE.md §6.2 point 9, with a
-- transactional fixture that is rolled back: no permanent diagnostic function is created.
-- Inside a SECURITY DEFINER call current_user is the function owner (origenlab_owner) while
-- session_user stays the direct login; EXECUTE is the primary authorization boundary; the
-- function writes past the caller's grants while the caller still cannot reach the table directly.
-- pgTAP runs as one login (the CLI's postgres), so session_user here is that login; the same proof
-- with the real runtime logins is supabase/scripts/verify_direct_logins.sh.
begin;
create extension if not exists pgtap with schema extensions;
select plan(14);

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
  return sqlstate || ': ' || sqlerrm;
end
$$;
create function pg_temp.query_as(p_role text, p_sql text) returns text
language plpgsql as $$
declare v text;
begin
  execute format('set role %I', p_role);
  execute p_sql into v;
  reset role;
  return v;
exception when others then
  return sqlstate || ': ' || sqlerrm;
end
$$;

select is(
  (select count(*)::int from pg_proc p join pg_namespace n on n.oid = p.pronamespace
    where n.nspname in ('crm', 'comms', 'outbound', 'evidence', 'catalog', 'procurement', 'platform') and p.prosecdef),
  0, 'before the fixture: no SECURITY DEFINER function exists');

-- Fixture (rolled back): two probes owned by origenlab_owner in a private schema, pinned
-- search_path, EXECUTE revoked from PUBLIC/anon/authenticated/service_role and granted to the
-- worker only — the shape every function on the closed list must have.
select is(left(pg_temp.run_as('origenlab_owner', $fx$
  create function outbound.__probe_definer_identity() returns text
  language sql security definer set search_path = pg_catalog
  as $f$ select current_user::text || '|' || session_user::text $f$;
  create function outbound.__probe_definer_write(p_address text) returns void
  language sql security definer set search_path = pg_catalog
  as $f$ insert into outbound.contact_control (scope, value_norm, kind, purpose, reason, source)
         values ('address', p_address, 'block', 'all', 'definer probe', 'operator_command') $f$;
  revoke all on function outbound.__probe_definer_identity() from public, anon, authenticated, service_role;
  revoke all on function outbound.__probe_definer_write(text) from public, anon, authenticated, service_role;
  grant execute on function outbound.__probe_definer_identity() to origenlab_worker;
  grant execute on function outbound.__probe_definer_write(text) to origenlab_worker;
$fx$), 2), 'ok', 'fixture: two SECURITY DEFINER probes owned by origenlab_owner, EXECUTE for origenlab_worker only');

select is((select proowner::regrole::text from pg_proc where proname = '__probe_definer_identity'), 'origenlab_owner', 'the probe is owned by origenlab_owner');
select is((select proconfig from pg_proc where proname = '__probe_definer_identity'), array['search_path=pg_catalog'], 'the probe pins search_path = pg_catalog');

-- Check 9: current_user is the owner inside the call; session_user is the direct login.
select is(pg_temp.query_as('origenlab_worker', 'select outbound.__probe_definer_identity()'),
  'origenlab_owner|' || session_user::text,
  'inside a definer call current_user is origenlab_owner while session_user is the direct login');
select is(pg_temp.query_as('origenlab_worker', 'select current_user::text'), 'origenlab_worker',
  'outside the call the worker is current_user — the login assertion must use session_user, never current_user');

-- Checks 6-8: EXECUTE is the boundary.
select is(left(pg_temp.run_as('origenlab_api', 'select outbound.__probe_definer_identity()'), 5), '42501', 'the wrong runtime role (api) is refused: no EXECUTE');
select is(left(pg_temp.run_as('service_role', 'select outbound.__probe_definer_identity()'), 5), '42501', 'service_role is refused EXECUTE');
select is(left(pg_temp.run_as('anon', 'select outbound.__probe_definer_identity()'), 5), '42501', 'anon is refused EXECUTE');
select is(left(pg_temp.run_as('authenticated', 'select outbound.__probe_definer_identity()'), 5), '42501', 'authenticated is refused EXECUTE');

-- RLS-bypass shape: the function writes a table the caller cannot write directly.
select is(left(pg_temp.run_as('origenlab_worker', $$insert into outbound.contact_control (scope, value_norm, kind, purpose, reason, source)
  values ('address', 'direct@example.test', 'block', 'all', 'direct', 'operator_command')$$), 5), '42501',
  'the worker cannot write outbound.contact_control directly');
select is(left(pg_temp.run_as('origenlab_worker', $$select outbound.__probe_definer_write('via-definer@example.test')$$), 2), 'ok',
  'the worker writes outbound.contact_control through the definer function (ownership, not BYPASSRLS)');
select is(pg_temp.query_as('origenlab_worker', $$select count(*)::text from outbound.contact_control where value_norm = 'via-definer@example.test'$$), '1',
  'the row written by the definer function exists (the worker may read the table)');
select is(left(pg_temp.run_as('origenlab_api', $$select outbound.__probe_definer_write('api@example.test')$$), 5), '42501',
  'the api role cannot reach the writing probe either: EXECUTE is granted per function per role');

select * from finish();
rollback;
