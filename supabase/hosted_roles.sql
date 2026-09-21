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
-- What this file may CREATE, ALTER or GRANT is closed to four names — origenlab_owner,
-- origenlab_migrator, origenlab_api, origenlab_worker — and supabase/audit/olaudit/bootstrap.py
-- proves that statically, before the file is ever shown to an operator or piped to psql. Every
-- `create role`, `alter role`, `grant` and `revoke` in this file is matched against a recognised
-- statement shape and its role names checked against that closed set; an unrecognised shape
-- refuses the whole file.
--
-- There is exactly one exception, and it is one-way. This file may REVOKE the SET and INHERIT
-- options on origenlab_owner from `postgres`, and nothing else about a platform identity. That
-- exception is itself a closed allowlist in bootstrap.py
-- (`PERMITTED_OPTION_REVOCATIONS`): it can only remove privilege from a platform role, it can
-- never confer any, it may not touch ADMIN, and it may not name a second platform role or a second
-- OrigenLab role. See the convergence block below for why it exists.
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

-- Converge the one relationship this bootstrap may have with a platform identity, and the only
-- place this file names one. Both statements are a REVOKE of a grant OPTION: they can remove
-- privilege from `postgres` and there is no shape here that could confer any.
--
-- Why this is needed rather than theoretical. The hosted `origenlab-v2` catalogue was measured on
-- 2026-09-20 and already carries `postgres -> origenlab_owner` with SET — the shape the *local*
-- supabase/roles.sql creates and this file deliberately does not. Without this convergence,
-- Direction 1 below refuses this file on the very project it exists to bootstrap. Provenance is
-- recorded in docs/STATUS.md §2.5.
--
-- Why SET and INHERIT, and not the whole membership. The boundary of docs/ARCHITECTURE.md §6.4 is
-- stated in terms of the two options that confer privilege. ADMIN is administrative only —
-- PostgreSQL 16 and later confer it on the creator of a role, and Direction 1 tolerates it by
-- design. `revoke origenlab_owner from postgres` would remove a whole grant, including a
-- relationship the policy permits, and would state an intent this policy does not hold. Revoking
-- ADMIN is equally out of bounds: converging past the policy is as much a deviation as falling
-- short of it.
--
-- Idempotent. Revoking an option that is not held raises a WARNING, never an error, so these are
-- no-ops on a fresh project and on every application after the first. Requires PostgreSQL 16 or
-- later for the `REVOKE ... OPTION FOR` grammar; the hosted project and the local container are
-- both PostgreSQL 17 (`major_version = 17` in the Supabase CLI config).
revoke set option for     origenlab_owner from postgres;
revoke inherit option for origenlab_owner from postgres;

-- Fail closed on the platform-role boundary (docs/ARCHITECTURE.md §6.4, §6.5). Two directions.
--
-- Direction 1: no identity outside the closed OrigenLab set may INHERIT or SET ROLE to an
-- OrigenLab role. That is the boundary that matters, and it is stated in terms of the two options
-- that confer privilege rather than as "holds no membership row" — because the latter is false on
-- any real project and would make this file fail the first time it is applied.
--
-- PostgreSQL 16 and later record the role creator's implicit ADMIN OPTION as an ordinary
-- pg_auth_members row. The project's `postgres` login applies this file and creates these four
-- roles, so it necessarily holds one such row per role, with admin_option true and both
-- set_option and inherit_option false. It is administrative only: it confers no SET ROLE and
-- inherits nothing. This file does not grant it, could not suppress it, and does not pretend it is
-- absent — it asserts that no such row ever carries INHERIT or SET instead.
--
-- Direction 2: no OrigenLab role holds a membership in any other role, except the migrator in the
-- owner. That is what would happen if a role were quietly folded into a platform group such as
-- pg_read_all_data.
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
     and r.rolname not in ('origenlab_owner', 'origenlab_migrator', 'origenlab_api', 'origenlab_worker')
     and (am.inherit_option or am.set_option);
  if v_bad is not null then
    raise exception 'no identity outside the OrigenLab set may inherit or assume an OrigenLab role; offending: %', v_bad;
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
