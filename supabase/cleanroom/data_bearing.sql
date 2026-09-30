-- OrigenLab V2 — what any clean-room database must satisfy, whatever it holds.
--
-- The data-bearing contract. `verify.sql` describes a database *immediately after* `build`:
-- exact row counts, one operator, zero opportunities. Those numbers stop being true the moment
-- an operator takes a decision in it, and `origenlab_clean` has carried decisions since
-- 2026-09-24 (reviews, the historical quotation import, the campaign import). This file asserts
-- what stays true across all of that — the things a migration or a grant, not a decision, can
-- break:
--
--   * the ledger names exactly the files in supabase/migrations/ (the caller passes the list
--     on disk as the psql variable `chain_on_disk`; nothing here measures its own expectation);
--   * the seven schemas, the 41 tables, the 148 policies, the four roles and their attributes;
--   * the grant boundary — PUBLIC/anon/authenticated/service_role hold nothing; the closed
--     SECURITY DEFINER list and its shape (owner, search_path, EXECUTE for origenlab_api alone);
--   * the sign-in privilege boundary — the runtime role can never read `pin_hash`, never write
--     an operator row or a throttle counter directly, and touches sessions and audit rows only
--     as the migrations grant;
--   * the send flags are off; no pytest fixture residue exists.
--
-- It counts no business row. `crm.opportunity` may be 0 or 44; this file does not care, and a
-- probe that would care belongs in verify.sql, behind `build`.
--
-- Emits one `key|value` line per probe and nothing else, for supabase/cleanroom/compare.py
-- against supabase/cleanroom/expected_data_bearing.json — exact in both directions, like the
-- fresh-rebuild contract. Read-only by construction: everything runs inside `begin read only`.
\pset pager off
\pset tuples_only on
\pset format unaligned
\pset fieldsep '|'

begin read only;

with
  ol_schemas(nspname) as (
    values ('crm'), ('comms'), ('outbound'), ('evidence'), ('catalog'), ('procurement'), ('platform')
  ),
  disk(version) as (
    select unnest(string_to_array(:'chain_on_disk', ','))
  ),
  ledger(version) as (
    select version from supabase_migrations.schema_migrations
  ),
  public_roles(rolname) as (
    values ('public'), ('anon'), ('authenticated'), ('service_role')
  ),
  ol_tables as (
    select c.oid, n.nspname, c.relname, c.relrowsecurity, c.relowner
    from pg_class c
    join pg_namespace n on n.oid = c.relnamespace
    join ol_schemas s on s.nspname = n.nspname
    where c.relkind = 'r'
  ),
  ol_functions as (
    select p.oid, n.nspname, p.proname, p.prosecdef, p.proowner, p.proconfig,
           p.oid::regprocedure::text as signature
    from pg_proc p
    join pg_namespace n on n.oid = p.pronamespace
    join ol_schemas s on s.nspname = n.nspname
  )

-- ── migrations: the ledger is exactly the chain on disk ─────────────────────────────────────
select 'migrations.missing_from_ledger',
       coalesce((select string_agg(version, ',' order by version)
                 from (select version from disk except select version from ledger) m), '<none>')
union all select 'migrations.not_in_supabase_migrations',
       coalesce((select string_agg(version, ',' order by version)
                 from (select version from ledger except select version from disk) m), '<none>')
union all select 'migrations.ledger_has_duplicates',
       (select count(*) from (select version from ledger group by version having count(*) > 1) d)::text

-- ── inventory: schemas, tables, policies ────────────────────────────────────────────────────
union all select 'schemas.present',
       (select string_agg(n.nspname, ',' order by n.nspname)
        from pg_namespace n join ol_schemas s on s.nspname = n.nspname)
union all select 'tables.count', (select count(*) from ol_tables)::text
union all select 'tables.by_schema',
       (select string_agg(nspname || '=' || n, ',' order by nspname)
        from (select nspname, count(*) n from ol_tables group by 1) t)
union all select 'tables.without_rls', (select count(*) from ol_tables where not relrowsecurity)::text
union all select 'tables.not_owned_by_origenlab_owner',
       (select count(*) from ol_tables where pg_get_userbyid(relowner) <> 'origenlab_owner')::text
union all select 'policies.count',
       (select count(*) from pg_policies p join ol_schemas s on s.nspname = p.schemaname)::text
union all select 'policies.platform',
       (select string_agg(tablename || ':' || policyname || ':' || cmd, ',' order by tablename, policyname)
        from pg_policies where schemaname = 'platform')
union all select 'foreign_keys.without_covering_index',
       (select count(*) from pg_constraint fk
        join ol_tables t on t.oid = fk.conrelid
        where fk.contype = 'f'
          and not exists (
            select 1 from pg_index i
            where i.indrelid = fk.conrelid
              and (i.indkey::smallint[])[0:array_length(fk.conkey, 1) - 1] = fk.conkey))::text

-- ── roles ───────────────────────────────────────────────────────────────────────────────────
union all select 'roles.origenlab',
       (select string_agg(rolname, ',' order by rolname) from pg_roles where rolname like 'origenlab\_%')
union all select 'roles.with_forbidden_attribute',
       (select count(*) from pg_roles
        where rolname like 'origenlab\_%'
          and (rolbypassrls or rolsuper or rolcreaterole or rolcreatedb or rolreplication))::text
union all select 'roles.owner_can_login',
       (select rolcanlogin::text from pg_roles where rolname = 'origenlab_owner')
union all select 'roles.owner_holds_database_create',
       has_database_privilege('origenlab_owner', current_database(), 'CREATE')::text

-- ── the grant boundary (MIGRATION.md §5.2) ──────────────────────────────────────────────────
union all select 'grants.schema_usage_held_by_public_roles',
       (select count(*) from ol_schemas s, public_roles r
        where has_schema_privilege(r.rolname, s.nspname, 'USAGE'))::text
union all select 'grants.table_privilege_held_by_public_roles',
       (select count(*) from ol_tables t, public_roles r
        where has_table_privilege(r.rolname, t.oid, 'SELECT, INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER')
           or has_any_column_privilege(r.rolname, t.oid, 'SELECT, INSERT, UPDATE, REFERENCES'))::text
union all select 'grants.function_execute_held_by_public_roles',
       (select count(*) from ol_functions f, public_roles r
        where has_function_privilege(r.rolname, f.oid, 'EXECUTE'))::text
union all select 'grants.default_privileges_for_public_roles',
       (select count(*) from pg_default_acl d
        join pg_namespace n on n.oid = d.defaclnamespace
        join ol_schemas s on s.nspname = n.nspname,
        unnest(coalesce(d.defaclacl, '{}'::aclitem[])) a
        where a::text like 'anon=%' or a::text like 'authenticated=%'
           or a::text like 'service_role=%' or a::text like '=%')::text

-- ── functions: the closed SECURITY DEFINER list and its shape ───────────────────────────────
union all select 'functions.security_definer',
       (select coalesce(string_agg(signature, ',' order by signature), '<none>')
        from ol_functions where prosecdef)
union all select 'functions.security_definer_misconfigured',
       (select count(*) from ol_functions
        where prosecdef
          and (pg_get_userbyid(proowner) <> 'origenlab_owner'
               or coalesce(proconfig, '{}'::text[]) <> array['search_path=pg_catalog']
               or not has_function_privilege('origenlab_api', oid, 'EXECUTE')
               or has_function_privilege('origenlab_worker', oid, 'EXECUTE')
               or has_function_privilege('origenlab_migrator', oid, 'EXECUTE')))::text
union all select 'functions.not_owned_by_origenlab_owner',
       (select count(*) from ol_functions where pg_get_userbyid(proowner) <> 'origenlab_owner')::text
union all select 'functions.record_pin_attempt_present',
       (select count(*) from ol_functions where nspname = 'platform' and proname = 'record_pin_attempt')::text

-- ── sign-in tables and the runtime role's privilege boundary (slice 1) ──────────────────────
union all select 'auth.tables_present',
       (select string_agg(relname, ',' order by relname) from ol_tables
        where nspname = 'platform'
          and relname in ('auth_principal', 'operator_profile', 'auth_event', 'auth_session'))
union all select 'auth.api_execute_begin_pin_attempt',
       has_function_privilege('origenlab_api', 'platform.begin_pin_attempt(uuid, uuid)', 'EXECUTE')::text
union all select 'auth.api_execute_finish_pin_attempt',
       has_function_privilege('origenlab_api', 'platform.finish_pin_attempt(uuid, uuid, uuid, bytea)', 'EXECUTE')::text
union all select 'auth.api_execute_hmac_sha256',
       has_function_privilege('origenlab_api', 'platform.hmac_sha256(bytea, bytea)', 'EXECUTE')::text
union all select 'auth.api_select_pin_hash',
       has_column_privilege('origenlab_api', 'platform.operator_profile', 'pin_hash', 'SELECT')::text
union all select 'auth.api_select_operator_profile_table_level',
       has_table_privilege('origenlab_api', 'platform.operator_profile', 'SELECT')::text
union all select 'auth.api_operator_profile_columns_not_readable',
       (select count(*) from information_schema.columns c
        where c.table_schema = 'platform' and c.table_name = 'operator_profile'
          and c.column_name <> 'pin_hash'
          and not has_column_privilege('origenlab_api', 'platform.operator_profile', c.column_name, 'SELECT'))::text
union all select 'auth.api_updates_auth_principal',
       has_any_column_privilege('origenlab_api', 'platform.auth_principal', 'UPDATE')::text
union all select 'auth.api_updates_operator_profile',
       has_any_column_privilege('origenlab_api', 'platform.operator_profile', 'UPDATE')::text
union all select 'auth.api_writes_operator',
       has_any_column_privilege('origenlab_api', 'platform.operator', 'INSERT, UPDATE')::text
union all select 'auth.api_deletes_in_platform',
       (select count(*) from ol_tables t
        where nspname = 'platform' and has_table_privilege('origenlab_api', t.oid, 'DELETE'))::text
union all select 'auth.api_auth_session_updatable_columns',
       (select coalesce(string_agg(c.column_name, ',' order by c.column_name), '<none>')
        from information_schema.columns c
        where c.table_schema = 'platform' and c.table_name = 'auth_session'
          and has_column_privilege('origenlab_api', 'platform.auth_session', c.column_name, 'UPDATE'))
union all select 'auth.api_auth_session_insert_select',
       (has_table_privilege('origenlab_api', 'platform.auth_session', 'INSERT')
        and has_table_privilege('origenlab_api', 'platform.auth_session', 'SELECT'))::text
union all select 'auth.api_auth_event_insert_select_only',
       (has_table_privilege('origenlab_api', 'platform.auth_event', 'INSERT')
        and has_table_privilege('origenlab_api', 'platform.auth_event', 'SELECT')
        and not has_any_column_privilege('origenlab_api', 'platform.auth_event', 'UPDATE'))::text
union all select 'auth.worker_reaches_sign_in_tables',
       (select count(*) from ol_tables t
        where nspname = 'platform'
          and relname in ('auth_principal', 'operator_profile', 'auth_event', 'auth_session')
          and (has_table_privilege('origenlab_worker', t.oid, 'SELECT, INSERT, UPDATE, DELETE')
               or has_any_column_privilege('origenlab_worker', t.oid, 'SELECT, INSERT, UPDATE')))::text

-- ── invariants that hold whatever the data ─────────────────────────────────────────────────
union all select 'outbound.send_control.any_flag_enabled',
       (select coalesce(bool_or(marketing_enabled or transactional_enabled), false)::text from outbound.send_control)
union all select 'residue.source_record_pytest',
       (select count(*) from evidence.source_record where dedupe_key like 'pytest-%')::text
union all select 'residue.operator_pytest',
       (select count(*) from platform.operator where email_norm like 'pytest-%')::text
;

commit;
