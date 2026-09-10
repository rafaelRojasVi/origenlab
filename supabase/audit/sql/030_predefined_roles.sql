-- a02 — ARCHITECTURE.md §6.5: the predefined-role boundary, which leaves no relacl entry.
--
-- pg_read_all_data and pg_write_all_data confer effective access that a grant audit cannot see,
-- so membership is asserted directly. Two halves, deliberately separated:
--
--   `forbidden_members` is the PROOF. No OrigenLab-created role and no Data-API-facing role is
--   inside either predefined role, directly or transitively. It must be empty in every era.
--
--   `observed_members` and `bypassrls_roles` are RECORDED, never failed on. The hosted project has
--   its own role catalogue: do not assume it is the local one (ARCHITECTURE.md §6.5).
--
-- Read-only: one SELECT, no mutation, no session change.
select jsonb_build_object(
  'check', 'a02',
  'data', jsonb_build_object(
    'forbidden_members', coalesce((
      select jsonb_agg(jsonb_build_object(
               'role',       r.rolname::text,
               'predefined', p.rolname::text)
             order by r.rolname, p.rolname)
        from pg_roles r
        cross join (select unnest(array['pg_read_all_data', 'pg_write_all_data']) as rolname) p
       where r.rolname in ('anon', 'authenticated', 'service_role', 'authenticator',
                           'origenlab_owner', 'origenlab_migrator', 'origenlab_api', 'origenlab_worker')
         and pg_has_role(r.rolname, p.rolname, 'MEMBER')), '[]'::jsonb),
    'observed_members', coalesce((
      select jsonb_agg(jsonb_build_object(
               'predefined', p.rolname::text,
               'member',     m.rolname::text)
             order by p.rolname, m.rolname)
        from pg_auth_members am
        join pg_roles p on p.oid = am.roleid
        join pg_roles m on m.oid = am.member
       where p.rolname in ('pg_read_all_data', 'pg_write_all_data')), '[]'::jsonb),
    'bypassrls_roles', coalesce((
      select jsonb_agg(r.rolname::text order by r.rolname)
        from pg_roles r where r.rolbypassrls), '[]'::jsonb),
    'superuser_roles', coalesce((
      select jsonb_agg(r.rolname::text order by r.rolname)
        from pg_roles r where r.rolsuper), '[]'::jsonb)
  )
)::text;
