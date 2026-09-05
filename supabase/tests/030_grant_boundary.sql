-- Slice 0 — the grant and exposure boundary (docs/MIGRATION.md §5.2 checks 3, 4, 5;
-- docs/ARCHITECTURE.md §6.4). PUBLIC, anon, authenticated and service_role hold no schema USAGE, no
-- object privilege and no EXECUTE in the seven schemas; origenlab_owner's default privileges keep
-- it that way for future objects; and service_role — which bypasses RLS — is still refused,
-- because bypassing RLS is not bypassing a grant.
begin;
create extension if not exists pgtap with schema extensions;
select plan(25);

-- Fixture (rolled back): let this session SET ROLE to the OrigenLab roles. The test login is the
-- CLI's `postgres`, which holds ADMIN OPTION on every role roles.sql created; it can already SET
-- ROLE to anon, authenticated and service_role.
grant origenlab_api      to session_user with set true, inherit false;
grant origenlab_worker   to session_user with set true, inherit false;
grant origenlab_migrator to session_user with set true, inherit false;

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

-- Check 3: no schema USAGE or CREATE.
select is(
  (select count(*)::int
     from (values ('anon'), ('authenticated'), ('service_role')) r(n),
          (values ('crm'), ('comms'), ('outbound'), ('evidence'), ('catalog'), ('procurement'), ('platform')) s(n)
    where has_schema_privilege(r.n, s.n, 'USAGE') or has_schema_privilege(r.n, s.n, 'CREATE')),
  0, 'anon, authenticated and service_role hold no USAGE or CREATE on any of the seven schemas');
select is(
  (select count(*)::int from pg_namespace n, aclexplode(n.nspacl) a
    where n.nspname in ('crm', 'comms', 'outbound', 'evidence', 'catalog', 'procurement', 'platform')
      and a.grantee = 0),
  0, 'PUBLIC holds no privilege on any of the seven schemas');
select is(
  (select count(*)::int
     from (values ('origenlab_api'), ('origenlab_worker')) r(n),
          (values ('crm'), ('comms'), ('outbound'), ('evidence'), ('catalog'), ('procurement'), ('platform')) s(n)
    where not has_schema_privilege(r.n, s.n, 'USAGE') or has_schema_privilege(r.n, s.n, 'CREATE')),
  0, 'the runtime roles hold USAGE and never CREATE on the seven schemas');

-- Check 3: no object privilege on tables or sequences (membership in PUBLIC is covered: if PUBLIC
-- held a privilege, anon would report it).
select is(
  (select count(*)::int
     from pg_class c join pg_namespace n on n.oid = c.relnamespace
    cross join (values ('anon'), ('authenticated'), ('service_role')) r(n)
    cross join (values ('SELECT'), ('INSERT'), ('UPDATE'), ('DELETE'), ('TRUNCATE'), ('REFERENCES'), ('TRIGGER')) p(v)
    where c.relkind in ('r', 'v', 'm', 'p', 'f')
      and n.nspname in ('crm', 'comms', 'outbound', 'evidence', 'catalog', 'procurement', 'platform')
      and has_table_privilege(r.n, c.oid, p.v)),
  0, 'anon, authenticated and service_role hold no table privilege on any application table');
select is(
  (select count(*)::int
     from pg_class c join pg_namespace n on n.oid = c.relnamespace, aclexplode(c.relacl) a
    where c.relkind in ('r', 'v', 'm', 'p', 'f', 'S')
      and n.nspname in ('crm', 'comms', 'outbound', 'evidence', 'catalog', 'procurement', 'platform')
      and a.grantee = 0),
  0, 'PUBLIC holds no privilege on any application table or sequence');
select is(
  (select count(*)::int
     from pg_class c join pg_namespace n on n.oid = c.relnamespace
    cross join (values ('anon'), ('authenticated'), ('service_role')) r(n)
    cross join (values ('USAGE'), ('SELECT'), ('UPDATE')) p(v)
    where c.relkind = 'S'
      and n.nspname in ('crm', 'comms', 'outbound', 'evidence', 'catalog', 'procurement', 'platform')
      and has_sequence_privilege(r.n, c.oid, p.v)),
  0, 'anon, authenticated and service_role hold no sequence privilege');

-- Check 4: no EXECUTE on any application function.
select is(
  (select count(*)::int
     from pg_proc p join pg_namespace n on n.oid = p.pronamespace
    cross join (values ('anon'), ('authenticated'), ('service_role')) r(n)
    where n.nspname in ('crm', 'comms', 'outbound', 'evidence', 'catalog', 'procurement', 'platform')
      and has_function_privilege(r.n, p.oid, 'EXECUTE')),
  0, 'anon, authenticated and service_role hold no EXECUTE on any application function');
select is(
  (select count(*)::int
     from pg_proc p join pg_namespace n on n.oid = p.pronamespace,
          aclexplode(coalesce(p.proacl, acldefault('f', p.proowner))) a
    where n.nspname in ('crm', 'comms', 'outbound', 'evidence', 'catalog', 'procurement', 'platform')
      and a.grantee = 0),
  0, 'PUBLIC holds no EXECUTE on any application function (the hard-wired default was revoked)');

-- Check 5: default privileges of origenlab_owner. Functions and types are the two object types with
-- a hard-wired PUBLIC grant; the owner carries a GLOBAL entry for each that names only itself.
select set_eq(
  $$ select defaclobjtype::text || ':' || defaclnamespace::text from pg_default_acl
      where defaclrole = 'origenlab_owner'::regrole $$,
  array['f:0', 'T:0'],
  'origenlab_owner has global default-privilege entries for functions (f) and types (T) and no per-schema entries');
select is(
  (select count(*)::int from pg_default_acl d, aclexplode(d.defaclacl) a
    where d.defaclrole = 'origenlab_owner'::regrole
      and (a.grantee = 0 or a.grantee in (select oid from pg_roles where rolname in ('anon', 'authenticated', 'service_role')))),
  0, 'no default privilege of origenlab_owner reaches PUBLIC, anon, authenticated or service_role');
select is(
  (select count(*)::int from pg_default_acl d, aclexplode(d.defaclacl) a
    where d.defaclrole = 'origenlab_owner'::regrole and a.grantee <> 'origenlab_owner'::regrole::oid),
  0, 'the owner''s default privileges name no grantee but the owner itself');

-- Check 5, behaviourally: objects the owner creates later grant nothing to those roles.
select is(
  left(pg_temp.run_as('origenlab_owner', $$
    create function crm.__future_probe() returns int language sql set search_path = pg_catalog as 'select 1';
    create table crm.__future_probe_t (id int primary key);
    create type crm.__future_probe_e as enum ('a');
  $$), 2), 'ok', 'fixture: origenlab_owner creates a future function, table and type (rolled back)');
select is(
  (select count(*)::int from pg_proc p, aclexplode(coalesce(p.proacl, acldefault('f', p.proowner))) a
    where p.proname = '__future_probe' and a.grantee <> 'origenlab_owner'::regrole::oid),
  0, 'a function created later by the owner is executable by the owner only, not by PUBLIC');
select is(has_function_privilege('service_role', 'crm.__future_probe()', 'EXECUTE'), false, 'service_role cannot execute a future function');
select is(has_function_privilege('anon', 'crm.__future_probe()', 'EXECUTE'), false, 'anon cannot execute a future function');
select is(has_table_privilege('service_role', 'crm.__future_probe_t', 'SELECT'), false, 'service_role cannot read a future table');
select is(has_table_privilege('authenticated', 'crm.__future_probe_t', 'SELECT'), false, 'authenticated cannot read a future table');
select is(has_type_privilege('anon', 'crm.__future_probe_e', 'USAGE'), false, 'anon cannot use a future type (hard-wired PUBLIC USAGE revoked by default)');
select is(has_type_privilege('service_role', 'crm.__future_probe_e', 'USAGE'), false, 'service_role cannot use a future type');

-- Functional: the platform roles are refused at the schema boundary, service_role included.
select is(left(pg_temp.run_as('service_role', 'select 1 from crm.organization'), 5), '42501',
  'service_role (BYPASSRLS) is refused on crm.organization: bypassing RLS is not bypassing a grant');
select is(left(pg_temp.run_as('service_role', 'select 1 from outbound.send_control'), 5), '42501',
  'service_role is refused on outbound.send_control');
select is(left(pg_temp.run_as('service_role', $$select crm.domain_event_is_valid('task', 'task.created', 1::smallint, '{}'::jsonb)$$), 5), '42501',
  'service_role is refused EXECUTE on an application function');
select is(left(pg_temp.run_as('anon', 'select 1 from platform.operator'), 5), '42501',
  'anon is refused on platform.operator');
select is(left(pg_temp.run_as('authenticated', 'select 1 from crm.quote_revision'), 5), '42501',
  'authenticated is refused on crm.quote_revision');
select is(left(pg_temp.run_as('service_role', 'insert into outbound.contact_control (scope, value_norm, kind, purpose, reason, source) values (''address'', ''x@example.test'', ''block'', ''all'', ''probe'', ''operator_command'')'), 5), '42501',
  'service_role cannot write outbound.contact_control');

select * from finish();
rollback;
