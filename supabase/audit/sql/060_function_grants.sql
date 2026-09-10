-- a05 — MIGRATION.md §5.2 check 4 and the SECURITY DEFINER half of check 9.
--
-- No EXECUTE for PUBLIC, anon, authenticated or service_role on any function in the seven
-- application schemas, and the SECURITY DEFINER inventory read straight from pg_proc.
--
-- acldefault('f', ...) is what makes this check real: a function whose proacl is NULL grants
-- EXECUTE to PUBLIC. Reading proacl alone would report such a function as ungranted.
--
-- The closed definer list (ARCHITECTURE.md §6.2) is empty in slice 0 and the baseline says so;
-- any definer function appearing hosted is a finding, not a detail.
--
-- Read-only: one SELECT, no mutation, no session change.
with app_proc as (
  select p.oid, n.nspname, p.proname, p.prokind, p.prosecdef, p.proowner,
         pg_get_function_identity_arguments(p.oid) as args,
         p.proconfig,
         coalesce(p.proacl, acldefault('f', p.proowner)) as acl
    from pg_proc p
    join pg_namespace n on n.oid = p.pronamespace
   where n.nspname in ('crm', 'comms', 'outbound', 'evidence', 'catalog', 'procurement', 'platform')
), proc_acl as (
  select app_proc.nspname, app_proc.proname, app_proc.args,
         case when a.grantee = 0 then 'PUBLIC' else a.grantee::regrole::text end as grantee,
         a.privilege_type
    from app_proc, aclexplode(app_proc.acl) a
), probe as (
  select app_proc.nspname, app_proc.proname, app_proc.args, r.rolname,
         has_function_privilege(r.rolname, app_proc.oid, 'EXECUTE') as held
    from app_proc
    cross join (select unnest(array['anon', 'authenticated', 'service_role', 'authenticator']) as rolname) r
)
select jsonb_build_object(
  'check', 'a05',
  'data', jsonb_build_object(
    'function_count', (select count(*) from app_proc),
    'security_definer_functions', coalesce((select jsonb_agg(jsonb_build_object(
                                                     'schema',      app_proc.nspname,
                                                     'name',        app_proc.proname,
                                                     'arguments',   app_proc.args,
                                                     'owner',       app_proc.proowner::regrole::text,
                                                     'proconfig',   coalesce(array_to_string(app_proc.proconfig, ','), ''))
                                                   order by app_proc.nspname, app_proc.proname, app_proc.args)
                                              from app_proc where app_proc.prosecdef), '[]'::jsonb),
    'forbidden_execute_grants', coalesce((select jsonb_agg(jsonb_build_object(
                                                   'schema',    proc_acl.nspname,
                                                   'function',  proc_acl.proname,
                                                   'arguments', proc_acl.args,
                                                   'grantee',   proc_acl.grantee,
                                                   'privilege', proc_acl.privilege_type)
                                                 order by proc_acl.nspname, proc_acl.proname, proc_acl.args,
                                                          proc_acl.grantee, proc_acl.privilege_type)
                                            from proc_acl
                                           where proc_acl.grantee in ('PUBLIC', 'anon', 'authenticated',
                                                                      'service_role', 'authenticator')), '[]'::jsonb),
    'effective_execute', coalesce((select jsonb_agg(jsonb_build_object(
                                            'schema',    probe.nspname,
                                            'function',  probe.proname,
                                            'arguments', probe.args,
                                            'role',      probe.rolname)
                                          order by probe.nspname, probe.proname, probe.args, probe.rolname)
                                     from probe where probe.held), '[]'::jsonb)
  )
)::text;
