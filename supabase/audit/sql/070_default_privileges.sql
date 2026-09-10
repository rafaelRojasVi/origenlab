-- a06 — MIGRATION.md §5.2 check 5: the owner's default privileges preserve checks 3 and 4, so an
-- object created by a later migration does not silently regain access.
--
-- Every pg_default_acl row is expanded, whoever owns it, because a default privilege belonging to
-- some other role that targets an application schema would reopen the boundary just as effectively.
--
-- Read-only: one SELECT, no mutation, no session change.
-- Scope. Two disjoint sets matter and nothing else does:
--
--   every default privilege owned by origenlab_owner, in any schema — this is check 5 itself; and
--   every default privilege targeting one of the seven application schemas, whoever owns it, since
--   a provider-owned default inside crm would reopen the boundary just as effectively.
--
-- Supabase's own defaults in public, storage, graphql and supabase_functions are deliberately out
-- of scope: they are the provider's control plane (ARCHITECTURE.md §6.5), recorded there and not
-- something this repository may alter or fail on.
with d as (
  select da.oid, da.defaclrole, da.defaclnamespace, da.defaclobjtype, da.defaclacl
    from pg_default_acl da
   where da.defaclrole = 'origenlab_owner'::regrole
      or da.defaclnamespace in (select n.oid from pg_namespace n
                                 where n.nspname in ('crm', 'comms', 'outbound', 'evidence',
                                                     'catalog', 'procurement', 'platform'))
), expanded as (
  select d.defaclrole::regrole::text as owner_role,
         case when d.defaclnamespace = 0 then ''
              else coalesce((select n.nspname from pg_namespace n where n.oid = d.defaclnamespace), '') end as schema_name,
         d.defaclobjtype as objtype,
         case when a.grantee = 0 then 'PUBLIC' else a.grantee::regrole::text end as grantee,
         a.privilege_type
    from d, aclexplode(d.defaclacl) a
)
select jsonb_build_object(
  'check', 'a06',
  'data', jsonb_build_object(
    'default_acl', coalesce((select jsonb_agg(jsonb_build_object(
                                      'owner_role', expanded.owner_role,
                                      'schema',     expanded.schema_name,
                                      'objtype',    expanded.objtype,
                                      'grantee',    expanded.grantee,
                                      'privilege',  expanded.privilege_type)
                                    order by expanded.owner_role, expanded.schema_name,
                                             expanded.objtype, expanded.grantee, expanded.privilege_type)
                               from expanded), '[]'::jsonb),
    'forbidden_default_acl', coalesce((select jsonb_agg(jsonb_build_object(
                                                'owner_role', expanded.owner_role,
                                                'schema',     expanded.schema_name,
                                                'objtype',    expanded.objtype,
                                                'grantee',    expanded.grantee,
                                                'privilege',  expanded.privilege_type)
                                              order by expanded.owner_role, expanded.schema_name,
                                                       expanded.objtype, expanded.grantee, expanded.privilege_type)
                                         from expanded
                                        where expanded.grantee in ('PUBLIC', 'anon', 'authenticated',
                                                                   'service_role', 'authenticator')), '[]'::jsonb)
  )
)::text;
