-- OrigenLab V2 — cluster role bootstrap (local).
--
-- The Supabase CLI executes this file before migrations on `supabase start` and on every
-- `supabase db reset` (the Postgres container is recreated each time). It is executed by the
-- CLI's `postgres` login, which in the Supabase image is NOT a superuser: it holds CREATEROLE and
-- CREATEDB and receives ADMIN OPTION on every role it creates. PostgreSQL therefore lets it create
-- roles with NOSUPERUSER / NOBYPASSRLS / NOREPLICATION, but forbids it from mentioning those
-- attributes in ALTER ROLE at all. The file is idempotent: roles are created only when absent,
-- the attributes a non-superuser may change are converged on every run, and the attributes it
-- may not change are asserted fail-closed.
--
-- Semantics: docs/ARCHITECTURE.md §6 (roles), §6.4 (platform-role boundary).
-- Proof obligations: docs/MIGRATION.md §5.2 checks 1-2 and 6-9, tested in supabase/tests.
--
-- No password, connection string or secret appears here. The three LOGIN roles are created
-- without a password; a local operator sets one out of band (see docs/OPERATIONS.md §4).
-- Supabase-managed roles (`anon`, `authenticated`, `service_role`, `supabase_admin`) are never
-- created, altered or granted anything by this file.

do $$
begin
  if not exists (select 1 from pg_catalog.pg_roles where rolname = 'origenlab_owner') then
    -- An ownership identity only. Nothing ever connects as it.
    create role origenlab_owner
      nologin nosuperuser nobypassrls nocreatedb nocreaterole noreplication inherit;
  end if;
  if not exists (select 1 from pg_catalog.pg_roles where rolname = 'origenlab_migrator') then
    -- DDL only, under an explicit `set role origenlab_owner`. Never serves a request.
    create role origenlab_migrator
      login noinherit nosuperuser nobypassrls nocreatedb nocreaterole noreplication;
  end if;
  if not exists (select 1 from pg_catalog.pg_roles where rolname = 'origenlab_api') then
    -- The FastAPI runtime. Not a member of the owner.
    create role origenlab_api
      login inherit nosuperuser nobypassrls nocreatedb nocreaterole noreplication;
  end if;
  if not exists (select 1 from pg_catalog.pg_roles where rolname = 'origenlab_worker') then
    -- The worker runtime. Not a member of the owner.
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

-- Membership in the owner.
--   origenlab_migrator: may assume the owner explicitly (SET), never inherits it.
--   postgres: the Supabase control-plane login the CLI uses to apply migrations. It already holds
--     ADMIN OPTION on origenlab_owner as its creator; this SET-only, non-inheriting membership is
--     what lets `set role origenlab_owner` inside a migration succeed. It is not an OrigenLab role,
--     is never a runtime identity, and inherits nothing from the owner.
grant origenlab_owner to origenlab_migrator with inherit false, set true, admin false;
grant origenlab_owner to postgres           with inherit false, set true;

-- Converge: the runtime roles hold no membership in the owner or in each other, and the migrator
-- holds none in the runtime roles.
revoke origenlab_owner  from origenlab_api;
revoke origenlab_owner  from origenlab_worker;
revoke origenlab_api    from origenlab_worker;
revoke origenlab_worker from origenlab_api;
revoke origenlab_api    from origenlab_migrator;
revoke origenlab_worker from origenlab_migrator;
