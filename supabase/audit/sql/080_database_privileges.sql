-- a07 — ARCHITECTURE.md §6.4 point 6: origenlab_owner holds no CREATE on the database between
-- migrations, and no Data-API-facing role acquires it either.
--
-- Read-only: one SELECT, no mutation, no session change.
with probe as (
  select r.rolname, p.priv, has_database_privilege(r.rolname, current_database(), p.priv) as held
    from (select unnest(array['origenlab_owner', 'origenlab_migrator', 'origenlab_api', 'origenlab_worker',
                              'anon', 'authenticated', 'service_role', 'authenticator']) as rolname) r
    cross join (select unnest(array['CREATE', 'CONNECT', 'TEMPORARY']) as priv) p
)
select jsonb_build_object(
  'check', 'a07',
  'data', jsonb_build_object(
    'database_privilege', coalesce((select jsonb_agg(jsonb_build_object(
                                             'role',      probe.rolname,
                                             'privilege', probe.priv,
                                             'held',      probe.held)
                                           order by probe.rolname, probe.priv)
                                      from probe), '[]'::jsonb)
  )
)::text;
