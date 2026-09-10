-- a08 — the slice 0 object inventory: seven schemas, thirty-three tables, every one of them owned
-- by origenlab_owner with RLS enabled (ARCHITECTURE.md §6.1, DOMAIN.md §7).
--
-- Compared name by name against supabase/audit/baselines/slice0.json rather than by count, so a
-- table that is missing hosted and a table that exists only hosted are both findings, and neither
-- can cancel the other out in a total.
--
-- Read-only: one SELECT, no mutation, no session change.
with app as (
  select n.nspname, c.relname, c.relkind, c.relowner::regrole::text as owner,
         c.relrowsecurity, c.relforcerowsecurity
    from pg_class c
    join pg_namespace n on n.oid = c.relnamespace
   where n.nspname in ('crm', 'comms', 'outbound', 'evidence', 'catalog', 'procurement', 'platform')
     and c.relkind in ('r', 'p')
)
select jsonb_build_object(
  'check', 'a08',
  'data', jsonb_build_object(
    'schemas', coalesce((select jsonb_agg(jsonb_build_object(
                                  'schema', n.nspname,
                                  'owner',  n.nspowner::regrole::text)
                                order by n.nspname)
                           from pg_namespace n
                          where n.nspname in ('crm', 'comms', 'outbound', 'evidence',
                                              'catalog', 'procurement', 'platform')), '[]'::jsonb),
    'tables', coalesce((select jsonb_agg(jsonb_build_object(
                                 'schema',      app.nspname,
                                 'table',       app.relname,
                                 'owner',       app.owner,
                                 'rls_enabled', app.relrowsecurity,
                                 'rls_forced',  app.relforcerowsecurity)
                               order by app.nspname, app.relname)
                          from app), '[]'::jsonb),
    'table_count', (select count(*) from app),
    'tables_without_rls', coalesce((select jsonb_agg(app.nspname || '.' || app.relname
                                                     order by app.nspname, app.relname)
                                      from app where not app.relrowsecurity), '[]'::jsonb),
    'tables_not_owned_by_owner', coalesce((select jsonb_agg(app.nspname || '.' || app.relname
                                                            order by app.nspname, app.relname)
                                             from app where app.owner <> 'origenlab_owner'), '[]'::jsonb)
  )
)::text;
