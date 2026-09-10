-- a03 — MIGRATION.md §5.2 check 3 (schema half): PUBLIC, anon, authenticated and service_role
-- hold no USAGE and no CREATE on any of the seven application schemas.
--
-- Two independent readings, because each is blind to something the other sees:
--
--   `forbidden_schema_grants` expands the ACL itself, so it names a direct grant precisely. A NULL
--   nspacl is expanded through acldefault(), never treated as "no privileges".
--
--   `effective_schema_privilege` asks has_schema_privilege(), which also answers true for a
--   privilege reached through a role membership the ACL never mentions.
--
-- Both must be empty / all-false. Bypassing RLS is not bypassing a grant (ARCHITECTURE.md §6.4).
--
-- Read-only: one SELECT, no mutation, no session change.
with app as (
  select n.oid, n.nspname, n.nspowner, coalesce(n.nspacl, acldefault('n', n.nspowner)) as acl
    from pg_namespace n
   where n.nspname in ('crm', 'comms', 'outbound', 'evidence', 'catalog', 'procurement', 'platform')
), exploded as (
  select app.nspname,
         case when a.grantee = 0 then 'PUBLIC' else a.grantee::regrole::text end as grantee,
         a.privilege_type, a.is_grantable
    from app, aclexplode(app.acl) a
), probe as (
  select app.nspname, r.rolname, p.priv,
         has_schema_privilege(r.rolname, app.nspname, p.priv) as held
    from app
    cross join (select unnest(array['anon', 'authenticated', 'service_role', 'authenticator']) as rolname) r
    cross join (select unnest(array['USAGE', 'CREATE']) as priv) p
)
select jsonb_build_object(
  'check', 'a03',
  'data', jsonb_build_object(
    'schemas', coalesce((select jsonb_agg(jsonb_build_object(
                                  'schema', app.nspname,
                                  'owner',  app.nspowner::regrole::text)
                                order by app.nspname) from app), '[]'::jsonb),
    'schema_acl', coalesce((select jsonb_agg(jsonb_build_object(
                                     'schema',    e.nspname,
                                     'grantee',   e.grantee,
                                     'privilege', e.privilege_type,
                                     'grantable', e.is_grantable)
                                   order by e.nspname, e.grantee, e.privilege_type)
                              from exploded e), '[]'::jsonb),
    'forbidden_schema_grants', coalesce((select jsonb_agg(jsonb_build_object(
                                                  'schema',    e.nspname,
                                                  'grantee',   e.grantee,
                                                  'privilege', e.privilege_type)
                                                order by e.nspname, e.grantee, e.privilege_type)
                                           from exploded e
                                          where e.grantee in ('PUBLIC', 'anon', 'authenticated',
                                                              'service_role', 'authenticator')), '[]'::jsonb),
    'effective_schema_privilege', coalesce((select jsonb_agg(jsonb_build_object(
                                                     'schema',    probe.nspname,
                                                     'role',      probe.rolname,
                                                     'privilege', probe.priv)
                                                   order by probe.nspname, probe.rolname, probe.priv)
                                              from probe where probe.held), '[]'::jsonb)
  )
)::text;
