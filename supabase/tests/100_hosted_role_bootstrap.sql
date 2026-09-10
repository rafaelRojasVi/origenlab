-- Slice 0 — the hosted role model, proven at the catalogue level.
--
-- A new file. The ten existing pgTAP files are unchanged; this one adds the coverage the hosted
-- bootstrap needs and nothing else, so the hosted work cannot disturb the evidence that already
-- passes.
--
-- What it proves is *membership closure*, which the existing files touch only in part: not merely
-- that the documented memberships exist, but that no other membership exists in either direction,
-- and that the ones that do exist carry the options they are supposed to. That is the property
-- supabase/hosted_roles.sql must converge to on a hosted project (docs/ARCHITECTURE.md §6,
-- docs/MIGRATION.md §5.2 checks 1-2), and the property that would quietly rot if a later change
-- granted an OrigenLab role into a platform group, or handed a platform identity the owner.
--
-- ---------------------------------------------------------------------------------------------
-- Creator-admin rows are a platform fact, not a grant either bootstrap file issues.
--
-- PostgreSQL 16 and later record the role creator's implicit ADMIN OPTION as an ordinary
-- pg_auth_members row. Because the Supabase CLI creates these four roles, `postgres` therefore
-- holds a membership row in ALL FOUR of them, granted by `supabase_admin`, with
-- admin_option = true, set_option = false and inherit_option = false. It is administrative only:
-- it does not let `postgres` SET ROLE to any of them and it inherits nothing.
--
-- The same will be true on a hosted project, where the project's `postgres` login applies
-- supabase/hosted_roles.sql and creates the same four roles. Neither roles.sql nor hosted_roles.sql
-- grants it — PostgreSQL confers it — and neither file could suppress it. So this file asserts its
-- exact shape rather than asserting its absence, which would be a claim that is simply false.
--
-- The one thing supabase/roles.sql *does* grant a platform role is separate and visible here as a
-- second row with a different grantor: `postgres` gets set_option on origenlab_owner so the CLI can
-- `set role origenlab_owner` inside a local migration. THAT is the local/hosted divergence.
-- supabase/hosted_roles.sql grants nothing to any platform role, because hosted migrations connect
-- as origenlab_migrator, which holds that SET-only membership itself.
-- ---------------------------------------------------------------------------------------------
--
-- The credential obligation is proven here too, from pg_authid: neither bootstrap file assigns a
-- password, so no OrigenLab role may carry one at rest. Three independent boundaries back it up —
-- supabase/audit/olaudit/bootstrap.py refuses the token `password` anywhere in the hosted file's
-- code, scripts/security/check-public-repo-hygiene.sh refuses it in either file's tracked bytes,
-- and supabase/scripts/verify_direct_logins.sh owns the local throw-away credentials and clears
-- them fail-closed. This assertion is the catalogue's own answer, independent of all three.
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
-- Closure outward: what each OrigenLab role is itself a member of.
-- ------------------------------------------------------------------------------------------------
select is_empty(
  $$ select m.rolname::text from pg_auth_members am join pg_roles m on m.oid = am.roleid
      where am.member = 'origenlab_api'::regrole $$,
  'origenlab_api is a member of no role at all');

select is_empty(
  $$ select m.rolname::text from pg_auth_members am join pg_roles m on m.oid = am.roleid
      where am.member = 'origenlab_worker'::regrole $$,
  'origenlab_worker is a member of no role at all');

select is_empty(
  $$ select m.rolname::text from pg_auth_members am join pg_roles m on m.oid = am.roleid
      where am.member = 'origenlab_owner'::regrole $$,
  'origenlab_owner is a member of no role; it is an ownership identity, not a group member');

select results_eq(
  $$ select distinct m.rolname::text collate "default" from pg_auth_members am
       join pg_roles m on m.oid = am.roleid
      where am.member = 'origenlab_migrator'::regrole order by 1 $$,
  $$ values ('origenlab_owner') $$,
  'origenlab_migrator is a member of origenlab_owner and of nothing else');

select is_empty(
  $$ select r.rolname::text || ' in ' || m.rolname::text from pg_auth_members am
       join pg_roles m on m.oid = am.roleid join pg_roles r on r.oid = am.member
      where r.rolname like 'origenlab\_%' and m.rolname like 'pg\_%' $$,
  'no OrigenLab role is a member of any PostgreSQL predefined role (pg_read_all_data and the rest)');

select is_empty(
  $$ select r.rolname::text || ' in ' || m.rolname::text from pg_auth_members am
       join pg_roles m on m.oid = am.roleid join pg_roles r on r.oid = am.member
      where r.rolname like 'origenlab\_%'
        and m.rolname in ('postgres', 'anon', 'authenticated', 'service_role', 'authenticator',
                          'supabase_admin', 'supabase_auth_admin', 'supabase_storage_admin',
                          'supabase_read_only_user', 'dashboard_user', 'pgbouncer') $$,
  'no OrigenLab role is a member of any Supabase-managed role');

-- ------------------------------------------------------------------------------------------------
-- Closure inward: who holds a membership in an OrigenLab role, and with which options.
-- ------------------------------------------------------------------------------------------------
select results_eq(
  $$ select distinct r.rolname::text collate "default" from pg_auth_members am
       join pg_roles m on m.oid = am.roleid join pg_roles r on r.oid = am.member
      where m.rolname like 'origenlab\_%' and r.rolname not like 'origenlab\_%' order by 1 $$,
  $$ values ('postgres') $$,
  'the only non-OrigenLab identity holding any membership in an OrigenLab role is the CLI control-plane login');

-- The property that actually matters: nothing outside the OrigenLab set inherits anything. An
-- inheriting membership would make every platform session silently carry OrigenLab privileges.
select is_empty(
  $$ select r.rolname::text || ' in ' || m.rolname::text from pg_auth_members am
       join pg_roles m on m.oid = am.roleid join pg_roles r on r.oid = am.member
      where m.rolname like 'origenlab\_%' and r.rolname not like 'origenlab\_%'
        and am.inherit_option $$,
  'no identity outside the OrigenLab set inherits an OrigenLab role');

-- SET ROLE is the other half. Outside the OrigenLab set it is permitted on the owner only, and
-- only locally — that is the single row supabase/roles.sql adds and supabase/hosted_roles.sql omits.
select is_empty(
  $$ select r.rolname::text || ' in ' || m.rolname::text from pg_auth_members am
       join pg_roles m on m.oid = am.roleid join pg_roles r on r.oid = am.member
      where m.rolname in ('origenlab_migrator', 'origenlab_api', 'origenlab_worker')
        and r.rolname not like 'origenlab\_%' and am.set_option $$,
  'no identity outside the OrigenLab set may SET ROLE to the migrator or to either runtime role');

-- The creator-admin rows: recorded with their exact shape, because PostgreSQL confers them on the
-- creator and neither bootstrap file grants or could suppress them.
select results_eq(
  $$ select m.rolname::text collate "default", am.admin_option, am.set_option, am.inherit_option
       from pg_auth_members am
       join pg_roles m on m.oid = am.roleid join pg_roles r on r.oid = am.member
       join pg_roles g on g.oid = am.grantor
      where m.rolname like 'origenlab\_%' and r.rolname = 'postgres' and am.admin_option
      order by 1 $$,
  $$ values ('origenlab_api',      true, false, false),
            ('origenlab_migrator', true, false, false),
            ('origenlab_owner',    true, false, false),
            ('origenlab_worker',   true, false, false) $$,
  'the CLI login holds exactly one administrative (ADMIN, no SET, no INHERIT) creator row per OrigenLab role');

-- The local divergence, isolated to one row and pinned to the options it is allowed to have.
select results_eq(
  $$ select am.inherit_option, am.set_option, am.admin_option from pg_auth_members am
       join pg_roles r on r.oid = am.member
      where am.roleid = 'origenlab_owner'::regrole and r.rolname = 'postgres' and not am.admin_option $$,
  $$ values (false, true, false) $$,
  'supabase/roles.sql gives the CLI login exactly one SET-only, non-inheriting, non-admin row on the owner — the row supabase/hosted_roles.sql does not create');

select results_eq(
  $$ select am.inherit_option, am.set_option, am.admin_option from pg_auth_members am
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

-- No OrigenLab role carries a password. Neither bootstrap file can assign one; assigning the
-- migrator's credential on a hosted project is a separate operator action with a hidden secret
-- input, and the two runtime roles get none at all in this slice (docs/OPERATIONS.md §4.3).
select is(
  (select count(*)::int from pg_authid where rolname like 'origenlab\_%' and rolpassword is not null),
  0,
  'no OrigenLab role carries a password: neither supabase/roles.sql nor supabase/hosted_roles.sql assigns one');

select * from finish();
rollback;
