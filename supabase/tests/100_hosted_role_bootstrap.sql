-- Slice 0 — the hosted role model, proven at the catalogue level.
--
-- A new file. The ten existing pgTAP files are unchanged; this one adds the coverage the hosted
-- bootstrap needs and nothing else, so the hosted work cannot disturb the evidence that already
-- passes.
--
-- What it proves is *membership closure*, which the existing files touch only in part: not merely
-- that the documented memberships exist, but that no other membership exists in either direction.
-- That is the property supabase/hosted_roles.sql must converge to on a hosted project
-- (docs/ARCHITECTURE.md §6, docs/MIGRATION.md §5.2 checks 1-2), and it is the property that would
-- quietly rot if a later change granted an OrigenLab role into a platform group, or granted the
-- owner to a platform identity for convenience.
--
-- The one deliberate local/hosted divergence is asserted here rather than left implicit:
-- supabase/roles.sql grants the CLI's `postgres` login a SET-only, non-inheriting membership in
-- origenlab_owner so migrations can run in the local container; supabase/hosted_roles.sql grants
-- nothing to any platform role, because hosted migrations connect as origenlab_migrator. This file
-- pins that `postgres` membership to exactly those options, so the divergence stays one line wide
-- and cannot grow into an inherited or admin-bearing membership.
--
-- Not proven here: that no OrigenLab role carries a password. `pg_authid.rolpassword` is readable
-- only by a superuser and the CLI's login is not one, so the catalogue cannot answer it. The
-- obligation is discharged statically instead — supabase/audit/olaudit/bootstrap.py refuses the
-- token `password` anywhere in the bootstrap file's code, and supabase/scripts/verify_direct_logins.sh
-- owns the local throw-away credentials and clears them fail-closed.
begin;
create extension if not exists pgtap with schema extensions;
select plan(19);

-- ------------------------------------------------------------------------------------------------
-- The four roles, and only the four.
-- ------------------------------------------------------------------------------------------------
select is(
  (select count(*)::int from pg_roles where rolname like 'origenlab\_%'), 4,
  'exactly four OrigenLab roles exist; the hosted bootstrap creates no fifth');

select results_eq(
  $$ select rolname::text collate "default", rolsuper, rolbypassrls, rolreplication
       from pg_roles where rolname like 'origenlab\_%' order by 1 $$,
  $$ values ('origenlab_api',      false, false, false),
            ('origenlab_migrator', false, false, false),
            ('origenlab_owner',    false, false, false),
            ('origenlab_worker',   false, false, false) $$,
  'all four OrigenLab roles are NOSUPERUSER, NOBYPASSRLS and NOREPLICATION');

select results_eq(
  $$ select rolname::text collate "default", rolcanlogin, rolinherit
       from pg_roles where rolname like 'origenlab\_%' order by 1 $$,
  $$ values ('origenlab_api',      true,  true),
            ('origenlab_migrator', true,  false),
            ('origenlab_owner',    false, true),
            ('origenlab_worker',   true,  true) $$,
  'the owner is NOLOGIN; the migrator is a NOINHERIT login; the two runtimes are ordinary logins');

-- ------------------------------------------------------------------------------------------------
-- Membership closure, outward: what each OrigenLab role is a member of.
-- ------------------------------------------------------------------------------------------------
select is_empty(
  $$ select m.rolname::text from pg_auth_members am
       join pg_roles m on m.oid = am.roleid
      where am.member = 'origenlab_api'::regrole $$,
  'origenlab_api is a member of no role at all');

select is_empty(
  $$ select m.rolname::text from pg_auth_members am
       join pg_roles m on m.oid = am.roleid
      where am.member = 'origenlab_worker'::regrole $$,
  'origenlab_worker is a member of no role at all');

select is_empty(
  $$ select m.rolname::text from pg_auth_members am
       join pg_roles m on m.oid = am.roleid
      where am.member = 'origenlab_owner'::regrole $$,
  'origenlab_owner is a member of no role; it is an ownership identity, not a group member');

select results_eq(
  $$ select m.rolname::text collate "default" from pg_auth_members am
       join pg_roles m on m.oid = am.roleid
      where am.member = 'origenlab_migrator'::regrole order by 1 $$,
  $$ values ('origenlab_owner') $$,
  'origenlab_migrator is a member of origenlab_owner and of nothing else');

select is_empty(
  $$ select r.rolname::text || ' in ' || m.rolname::text
       from pg_auth_members am
       join pg_roles m on m.oid = am.roleid
       join pg_roles r on r.oid = am.member
      where r.rolname like 'origenlab\_%' and m.rolname like 'pg\_%' $$,
  'no OrigenLab role is a member of any PostgreSQL predefined role (pg_read_all_data and the rest)');

select is_empty(
  $$ select r.rolname::text || ' in ' || m.rolname::text
       from pg_auth_members am
       join pg_roles m on m.oid = am.roleid
       join pg_roles r on r.oid = am.member
      where r.rolname like 'origenlab\_%'
        and m.rolname in ('postgres', 'anon', 'authenticated', 'service_role', 'authenticator',
                          'supabase_admin', 'supabase_auth_admin', 'supabase_storage_admin',
                          'supabase_read_only_user', 'dashboard_user', 'pgbouncer') $$,
  'no OrigenLab role is a member of any Supabase-managed role');

-- ------------------------------------------------------------------------------------------------
-- Membership closure, inward: who is a member of each OrigenLab role.
-- ------------------------------------------------------------------------------------------------
select is_empty(
  $$ select r.rolname::text from pg_auth_members am
       join pg_roles r on r.oid = am.member
      where am.roleid = 'origenlab_api'::regrole $$,
  'nothing holds a membership in origenlab_api');

select is_empty(
  $$ select r.rolname::text from pg_auth_members am
       join pg_roles r on r.oid = am.member
      where am.roleid = 'origenlab_worker'::regrole $$,
  'nothing holds a membership in origenlab_worker');

select is_empty(
  $$ select r.rolname::text from pg_auth_members am
       join pg_roles r on r.oid = am.member
      where am.roleid = 'origenlab_migrator'::regrole $$,
  'nothing holds a membership in origenlab_migrator');

select results_eq(
  $$ select r.rolname::text collate "default" from pg_auth_members am
       join pg_roles r on r.oid = am.member
      where am.roleid = 'origenlab_owner'::regrole order by 1 $$,
  $$ values ('origenlab_migrator'), ('postgres') $$,
  'exactly two identities may assume origenlab_owner locally: the migrator, and the CLI login');

select results_eq(
  $$ select r.rolname::text collate "default" from pg_auth_members am
       join pg_roles r on r.oid = am.member
      where am.roleid = 'origenlab_owner'::regrole
        and r.rolname not like 'origenlab\_%' order by 1 $$,
  $$ values ('postgres') $$,
  'the only non-OrigenLab member of the owner is the CLI control-plane login, and supabase/hosted_roles.sql grants it nothing');

-- The local divergence, pinned to exactly the options it is allowed to have. A membership that
-- gained INHERIT would make every migration silently run as the owner; one that gained ADMIN would
-- let the platform login hand the owner out.
select results_eq(
  $$ select am.inherit_option, am.set_option, am.admin_option
       from pg_auth_members am
       join pg_roles r on r.oid = am.member
      where am.roleid = 'origenlab_owner'::regrole and r.rolname = 'postgres' $$,
  $$ values (false, true, false) $$,
  'the CLI login holds the owner SET-only: no INHERIT, no ADMIN OPTION');

select results_eq(
  $$ select am.inherit_option, am.set_option, am.admin_option
       from pg_auth_members am
       join pg_roles r on r.oid = am.member
      where am.roleid = 'origenlab_owner'::regrole and r.rolname = 'origenlab_migrator' $$,
  $$ values (false, true, false) $$,
  'origenlab_migrator holds the owner SET-only: no INHERIT, no ADMIN OPTION — the one membership the hosted bootstrap grants');

-- ------------------------------------------------------------------------------------------------
-- The behavioural consequence, at the catalogue level.
-- ------------------------------------------------------------------------------------------------
select is(pg_has_role('origenlab_migrator', 'origenlab_owner', 'SET'),   true,
  'origenlab_migrator may SET ROLE origenlab_owner — the hosted migration path needs no platform login');
select is(pg_has_role('origenlab_migrator', 'origenlab_owner', 'USAGE'), false,
  'origenlab_migrator never inherits the owner');
select is(
  pg_has_role('origenlab_api', 'origenlab_owner', 'MEMBER')
    or pg_has_role('origenlab_worker', 'origenlab_owner', 'MEMBER'),
  false,
  'neither runtime role can assume or inherit origenlab_owner');

select * from finish();
rollback;
