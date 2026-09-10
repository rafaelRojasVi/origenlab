-- a04 — MIGRATION.md §5.2 check 3 (object half): PUBLIC, anon, authenticated and service_role hold
-- no privilege on any table, view, sequence or column in the seven application schemas.
--
-- Column-level grants are read separately from pg_attribute.attacl. Slice 0 uses them (the API may
-- write outbound.campaign_reply.operator_class and may not write proposed_class), so a table-level
-- reading alone would miss a column granted to a Data-API-facing role.
--
-- A NULL relacl or attacl means "the default for this object kind", not "no privileges", so every
-- expansion goes through acldefault().
--
-- Read-only: one SELECT, no mutation, no session change.
with app_rel as (
  select c.oid, n.nspname, c.relname, c.relkind, c.relowner,
         coalesce(c.relacl, acldefault((case when c.relkind = 'S' then 's' else 'r' end)::"char", c.relowner)) as acl
    from pg_class c
    join pg_namespace n on n.oid = c.relnamespace
   where n.nspname in ('crm', 'comms', 'outbound', 'evidence', 'catalog', 'procurement', 'platform')
     and c.relkind in ('r', 'p', 'v', 'm', 'S', 'f')
), rel_acl as (
  select app_rel.nspname, app_rel.relname, app_rel.relkind,
         case when a.grantee = 0 then 'PUBLIC' else a.grantee::regrole::text end as grantee,
         a.privilege_type
    from app_rel, aclexplode(app_rel.acl) a
), col_acl as (
  select app_rel.nspname, app_rel.relname, att.attname,
         case when a.grantee = 0 then 'PUBLIC' else a.grantee::regrole::text end as grantee,
         a.privilege_type
    from app_rel
    join pg_attribute att on att.attrelid = app_rel.oid and att.attnum > 0 and not att.attisdropped
    cross join lateral aclexplode(att.attacl) a
   where att.attacl is not null
), probe as (
  select app_rel.nspname, app_rel.relname, r.rolname, p.priv,
         has_table_privilege(r.rolname, app_rel.oid, p.priv) as held
    from app_rel
    cross join (select unnest(array['anon', 'authenticated', 'service_role', 'authenticator']) as rolname) r
    cross join (select unnest(array['SELECT', 'INSERT', 'UPDATE', 'DELETE', 'TRUNCATE',
                                    'REFERENCES', 'TRIGGER']) as priv) p
   where app_rel.relkind <> 'S'
)
select jsonb_build_object(
  'check', 'a04',
  'data', jsonb_build_object(
    'relation_count', (select count(*) from app_rel),
    'forbidden_table_grants', coalesce((select jsonb_agg(jsonb_build_object(
                                                 'schema',    rel_acl.nspname,
                                                 'relation',  rel_acl.relname,
                                                 'kind',      rel_acl.relkind,
                                                 'grantee',   rel_acl.grantee,
                                                 'privilege', rel_acl.privilege_type)
                                               order by rel_acl.nspname, rel_acl.relname,
                                                        rel_acl.grantee, rel_acl.privilege_type)
                                          from rel_acl
                                         where rel_acl.grantee in ('PUBLIC', 'anon', 'authenticated',
                                                                   'service_role', 'authenticator')), '[]'::jsonb),
    'forbidden_column_grants', coalesce((select jsonb_agg(jsonb_build_object(
                                                  'schema',    col_acl.nspname,
                                                  'relation',  col_acl.relname,
                                                  'column',    col_acl.attname,
                                                  'grantee',   col_acl.grantee,
                                                  'privilege', col_acl.privilege_type)
                                                order by col_acl.nspname, col_acl.relname, col_acl.attname,
                                                         col_acl.grantee, col_acl.privilege_type)
                                           from col_acl
                                          where col_acl.grantee in ('PUBLIC', 'anon', 'authenticated',
                                                                    'service_role', 'authenticator')), '[]'::jsonb),
    'effective_table_privilege', coalesce((select jsonb_agg(jsonb_build_object(
                                                    'schema',    probe.nspname,
                                                    'relation',  probe.relname,
                                                    'role',      probe.rolname,
                                                    'privilege', probe.priv)
                                                  order by probe.nspname, probe.relname,
                                                           probe.rolname, probe.priv)
                                             from probe where probe.held), '[]'::jsonb)
  )
)::text;
