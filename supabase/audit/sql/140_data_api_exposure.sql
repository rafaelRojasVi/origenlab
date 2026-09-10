-- a13 — MIGRATION.md §5.2, slice 0 gate: the Data API is off and the seven application schemas are
-- not exposed through PostgREST.
--
-- This check CORROBORATES; it never proves. PostgREST's exposed-schema list lives in the platform's
-- own configuration, and a database session cannot read the project's API settings. What SQL can
-- show is the boundary that would have to hold even if the toggle were wrong — anon, authenticated
-- and authenticator reach none of the seven schemas — plus whatever pgrst.* role settings are
-- visible in this catalogue.
--
-- The audit therefore pairs this with an explicit operator attestation of the Dashboard toggle
-- (docs/OPERATIONS.md §4.2). Neither half alone is allowed to settle the Data API status.
--
-- Read-only: one SELECT, no mutation, no session change.
with pgrst_settings as (
  select coalesce(r.rolname::text, '') as rolname, s.setconfig
    from pg_db_role_setting s
    left join pg_roles r on r.oid = s.setrole
), pgrst_entries as (
  select pgrst_settings.rolname, e.entry
    from pgrst_settings
    cross join lateral unnest(coalesce(pgrst_settings.setconfig, array[]::text[])) as e(entry)
   where e.entry like 'pgrst.%'
), reach as (
  select n.nspname, r.rolname,
         has_schema_privilege(r.rolname, n.nspname, 'USAGE') as usage_held
    from pg_namespace n
    cross join (select unnest(array['anon', 'authenticated', 'authenticator', 'service_role']) as rolname) r
   where n.nspname in ('crm', 'comms', 'outbound', 'evidence', 'catalog', 'procurement', 'platform')
)
select jsonb_build_object(
  'check', 'a13',
  'data', jsonb_build_object(
    'pgrst_role_settings', coalesce((select jsonb_agg(jsonb_build_object(
                                              'role',  pgrst_entries.rolname,
                                              'entry', pgrst_entries.entry)
                                            order by pgrst_entries.rolname, pgrst_entries.entry)
                                       from pgrst_entries), '[]'::jsonb),
    'authenticator_rolconfig', coalesce((select jsonb_agg(e.entry order by e.entry)
                                           from pg_roles r
                                           cross join lateral unnest(coalesce(r.rolconfig, array[]::text[])) as e(entry)
                                          where r.rolname = 'authenticator'), '[]'::jsonb),
    'application_schema_reachable_by', coalesce((select jsonb_agg(jsonb_build_object(
                                                          'schema', reach.nspname,
                                                          'role',   reach.rolname)
                                                        order by reach.nspname, reach.rolname)
                                                   from reach where reach.usage_held), '[]'::jsonb)
  )
)::text;
