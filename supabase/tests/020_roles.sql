-- Slice 0 — role proofs (docs/MIGRATION.md §5.2 checks 1 and 2; docs/ARCHITECTURE.md §6).
-- Catalogue-level: attributes and memberships. The behavioural proof with real LOGIN connections
-- (a runtime login cannot SET ROLE to the owner) is supabase/scripts/verify_direct_logins.sh,
-- because SET ROLE is authorised against the session user and pgTAP runs as one login.
begin;
create extension if not exists pgtap with schema extensions;
select plan(21);

-- Check 1: every OrigenLab-created role is NOSUPERUSER, NOBYPASSRLS, NOREPLICATION, with the
-- documented LOGIN / INHERIT / CREATEDB / CREATEROLE attributes.
select results_eq(
  $$ select rolname::text collate "default", rolsuper, rolbypassrls, rolreplication, rolcanlogin, rolinherit, rolcreatedb, rolcreaterole
       from pg_roles where rolname like 'origenlab\_%' order by 1 $$,
  $$ values ('origenlab_api',      false, false, false, true,  true,  false, false),
            ('origenlab_migrator', false, false, false, true,  false, false, false),
            ('origenlab_owner',    false, false, false, false, true,  false, false),
            ('origenlab_worker',   false, false, false, true,  true,  false, false) $$,
  'the four OrigenLab roles carry exactly the documented attributes (all NOSUPERUSER, NOBYPASSRLS)');
select is((select count(*)::int from pg_roles where rolname like 'origenlab\_%'), 4, 'exactly four OrigenLab roles exist');
select is((select rolbypassrls from pg_roles where rolname = 'origenlab_owner'), false, 'origenlab_owner is NOBYPASSRLS');
select is((select rolbypassrls from pg_roles where rolname = 'origenlab_migrator'), false, 'origenlab_migrator is NOBYPASSRLS');
select is((select rolbypassrls from pg_roles where rolname = 'origenlab_api'), false, 'origenlab_api is NOBYPASSRLS');
select is((select rolbypassrls from pg_roles where rolname = 'origenlab_worker'), false, 'origenlab_worker is NOBYPASSRLS');

-- Check 2: service_role is a Supabase-managed BYPASSRLS role, recorded — never failed on, never altered.
select is(
  (select rolbypassrls from pg_roles where rolname = 'service_role'), true,
  'service_role carries its platform-defined BYPASSRLS (recorded; Supabase-managed, outside the OrigenLab trust boundary)');
select ok(
  not exists (select 1 from pg_auth_members am join pg_roles m on m.oid = am.roleid
               where am.member = 'service_role'::regrole and m.rolname like 'origenlab\_%'),
  'service_role is not a member of any OrigenLab role');

-- Ownership identity: nothing can log in as the owner.
select is((select rolcanlogin from pg_roles where rolname = 'origenlab_owner'), false, 'nothing can log in as origenlab_owner');

-- Memberships: only the migrator (SET, no INHERIT) and the CLI control-plane login `postgres`
-- (SET, no INHERIT; it already holds ADMIN OPTION as the creator) may assume the owner.
select is(pg_has_role('origenlab_migrator', 'origenlab_owner', 'SET'),   true,  'origenlab_migrator may SET ROLE origenlab_owner');
select is(pg_has_role('origenlab_migrator', 'origenlab_owner', 'USAGE'), false, 'origenlab_migrator does not inherit the owner (NOINHERIT)');
select is(pg_has_role('origenlab_api',    'origenlab_owner', 'MEMBER'), false, 'origenlab_api holds no membership in the owner');
select is(pg_has_role('origenlab_worker', 'origenlab_owner', 'MEMBER'), false, 'origenlab_worker holds no membership in the owner');
select is(pg_has_role('origenlab_api',    'origenlab_worker', 'MEMBER'), false, 'origenlab_api cannot assume or inherit origenlab_worker');
select is(pg_has_role('origenlab_worker', 'origenlab_api',    'MEMBER'), false, 'origenlab_worker cannot assume or inherit origenlab_api');
select is(pg_has_role('origenlab_migrator', 'origenlab_api',    'MEMBER'), false, 'origenlab_migrator holds no membership in origenlab_api');
select is(pg_has_role('origenlab_migrator', 'origenlab_worker', 'MEMBER'), false, 'origenlab_migrator holds no membership in origenlab_worker');
select set_eq(
  $$ select r.rolname::text collate "default" from pg_auth_members am join pg_roles r on r.oid = am.member
      where am.roleid = 'origenlab_owner'::regrole $$,
  array['origenlab_migrator', 'postgres'],
  'the only members of origenlab_owner are origenlab_migrator and the CLI control-plane login postgres');
select is(
  (select coalesce(bool_or(inherit_option), false) from pg_auth_members where roleid = 'origenlab_owner'::regrole),
  false, 'no member inherits origenlab_owner: assuming it is always an explicit SET ROLE');
select is(
  (select count(*)::int from pg_auth_members am
    where am.roleid in ('origenlab_api'::regrole, 'origenlab_worker'::regrole)
      and (am.set_option or am.inherit_option)),
  0, 'no role can SET ROLE to or inherit a runtime role');
select is(
  (select count(*)::int from pg_auth_members am where am.member in ('origenlab_api'::regrole, 'origenlab_worker'::regrole)),
  0, 'the runtime roles are members of nothing');

select * from finish();
rollback;
