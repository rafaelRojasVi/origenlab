-- OrigenLab V2 — cluster role bootstrap (hosted).
--
-- The hosted counterpart of supabase/roles.sql. It is a separate file on purpose: the two differ
-- in exactly one substantive way, and that difference is the whole point of the separation.
--
--   supabase/roles.sql (local) grants `postgres` a SET-only membership in origenlab_owner, because
--   the Supabase CLI applies migrations as `postgres` against a disposable container.
--
--   THIS FILE GRANTS NOTHING TO ANY SUPABASE-MANAGED ROLE. On a hosted project, migrations connect
--   as origenlab_migrator, which holds the SET-only membership itself. The platform `postgres`
--   role is never altered, never granted a membership in an OrigenLab role, and never made an
--   owner of an application object. The bootstrap does not depend on altering it.
--
-- What this file may touch is closed to four names — origenlab_owner, origenlab_migrator,
-- origenlab_api, origenlab_worker — and supabase/audit/olaudit/bootstrap.py proves that statically,
-- before the file is ever shown to an operator or piped to psql. Every `create role`, `alter role`,
-- `grant` and `revoke` in this file is matched against a recognised statement shape and its role
-- names checked against that closed set; an unrecognised shape refuses the whole file.
--
-- NO PASSWORD APPEARS HERE, AND NONE MAY. The static analyser rejects the token `password`
-- outright. The three LOGIN roles are created without one. Assigning the origenlab_migrator
-- password is a separate operator action performed with a hidden secret input, out of band and
-- outside this repository (docs/OPERATIONS.md §4.3).
--
-- Applying role: the project's `postgres` login, which on Supabase is NOT a superuser — it holds
-- CREATEROLE and CREATEDB and receives ADMIN OPTION on every role it creates. PostgreSQL therefore
-- lets it create roles with NOSUPERUSER / NOBYPASSRLS / NOREPLICATION but forbids it from
-- mentioning those attributes in ALTER ROLE at all. So, exactly as in the local file: the
-- attributes it may change are converged on every run, and the attributes it may not change are
-- asserted fail-closed. That implicit ADMIN OPTION is conferred by PostgreSQL on the creator; it is
-- not a grant this file issues, and this file issues none to a platform role.
--
-- The file is idempotent and deterministic: applying it twice leaves the same catalogue state and
-- emits the same bytes.
--
-- Semantics: docs/ARCHITECTURE.md §6 (roles), §6.4 (platform-role boundary).
-- Proof obligations: docs/MIGRATION.md §5.2 checks 1-2 and 6-9.
-- Procedure: docs/OPERATIONS.md §4.3.

do $$
begin
  if not exists (select 1 from pg_catalog.pg_roles where rolname = 'origenlab_owner') then
    -- An ownership identity only. Nothing ever connects as it.
    create role origenlab_owner
      nologin nosuperuser nobypassrls nocreatedb nocreaterole noreplication inherit;
  end if;
  if not exists (select 1 from pg_catalog.pg_roles where rolname = 'origenlab_migrator') then
    -- DDL only, under an explicit `set role origenlab_owner`. Never serves a request. This is the
    -- role that applies hosted migrations; on a hosted project no platform login does it instead.
    create role origenlab_migrator
      login noinherit nosuperuser nobypassrls nocreatedb nocreaterole noreplication;
  end if;
  if not exists (select 1 from pg_catalog.pg_roles where rolname = 'origenlab_api') then
    -- The FastAPI runtime. Not a member of the owner. No direct-login password in this slice.
    create role origenlab_api
      login inherit nosuperuser nobypassrls nocreatedb nocreaterole noreplication;
  end if;
  if not exists (select 1 from pg_catalog.pg_roles where rolname = 'origenlab_worker') then
    -- The worker runtime. Not a member of the owner. No direct-login password in this slice.
    create role origenlab_worker
      login inherit nosuperuser nobypassrls nocreatedb nocreaterole noreplication;
  end if;
end
$$;

-- Converge the attributes a CREATEROLE non-superuser may change.
alter role origenlab_owner    nologin inherit   nocreatedb nocreaterole;
alter role origenlab_migrator login   noinherit nocreatedb nocreaterole;
alter role origenlab_api      login   inherit   nocreatedb nocreaterole;
alter role origenlab_worker   login   inherit   nocreatedb nocreaterole;

-- Fail closed on the attributes this file cannot change: no OrigenLab-created role may ever be
-- SUPERUSER, BYPASSRLS or REPLICATION (docs/MIGRATION.md §5.2 check 1).
do $$
declare
  v_bad text;
begin
  select string_agg(rolname, ', ' order by rolname)
    into v_bad
    from pg_catalog.pg_roles
   where rolname in ('origenlab_owner', 'origenlab_migrator', 'origenlab_api', 'origenlab_worker')
     and (rolsuper or rolbypassrls or rolreplication);
  if v_bad is not null then
    raise exception 'OrigenLab roles must be NOSUPERUSER NOBYPASSRLS NOREPLICATION; offending: %', v_bad;
  end if;
end
$$;

-- Membership in the owner. Exactly one, and it is not a platform role: origenlab_migrator may
-- assume the owner explicitly (SET), never inherits it, and cannot pass the membership on (no
-- ADMIN). This is the hosted difference from supabase/roles.sql, which additionally grants the
-- CLI's `postgres` login the same SET-only membership. Here nothing is granted to `postgres`.
grant origenlab_owner to origenlab_migrator with inherit false, set true, admin false;

-- Converge: the runtime roles hold no membership in the owner or in each other, and the migrator
-- holds none in the runtime roles. Revoking is idempotent and is a no-op on a fresh project.
revoke origenlab_owner  from origenlab_api;
revoke origenlab_owner  from origenlab_worker;
revoke origenlab_api    from origenlab_worker;
revoke origenlab_worker from origenlab_api;
revoke origenlab_api    from origenlab_migrator;
revoke origenlab_worker from origenlab_migrator;

-- Fail closed on the platform-role boundary (docs/ARCHITECTURE.md §6.4, §6.5). Two directions,
-- both of which this file must leave empty on a hosted project:
--   1. no role outside the closed OrigenLab set holds a membership in an OrigenLab role;
--   2. no OrigenLab role holds a membership in any other role at all.
-- Direction 1 is what would happen if this file — or an operator repairing it by hand — granted
-- the owner to `postgres`, `service_role` or a dashboard identity. Direction 2 is what would
-- happen if an OrigenLab role were quietly folded into a platform group such as pg_read_all_data.
do $$
declare
  v_bad text;
begin
  select string_agg(format('%s in %s', r.rolname, m.rolname), ', ' order by r.rolname, m.rolname)
    into v_bad
    from pg_catalog.pg_auth_members am
    join pg_catalog.pg_roles m on m.oid = am.roleid
    join pg_catalog.pg_roles r on r.oid = am.member
   where m.rolname in ('origenlab_owner', 'origenlab_migrator', 'origenlab_api', 'origenlab_worker')
     and r.rolname <> 'origenlab_migrator';
  if v_bad is not null then
    raise exception 'no role outside the OrigenLab set may hold membership in an OrigenLab role; offending: %', v_bad;
  end if;

  select string_agg(format('%s in %s', r.rolname, m.rolname), ', ' order by r.rolname, m.rolname)
    into v_bad
    from pg_catalog.pg_auth_members am
    join pg_catalog.pg_roles m on m.oid = am.roleid
    join pg_catalog.pg_roles r on r.oid = am.member
   where r.rolname in ('origenlab_owner', 'origenlab_api', 'origenlab_worker')
      or (r.rolname = 'origenlab_migrator' and m.rolname <> 'origenlab_owner');
  if v_bad is not null then
    raise exception 'an OrigenLab role holds an unexpected membership; offending: %', v_bad;
  end if;
end
$$;
