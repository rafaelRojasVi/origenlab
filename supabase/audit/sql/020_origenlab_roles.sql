-- a01 — MIGRATION.md §5.2 check 1: every OrigenLab-created role is NOBYPASSRLS.
--
-- Catalogue attributes of the four roles this repository creates, plus the membership graph that
-- decides who may assume the owner. Compared against supabase/audit/baselines/slice0.json; the
-- hosted catalogue must reproduce it exactly. Mirrors supabase/tests/020_roles.sql, which proves
-- the same facts locally under pgTAP.
--
-- Read-only: one SELECT, no mutation, no session change.
select jsonb_build_object(
  'check', 'a01',
  'data', jsonb_build_object(
    'roles', coalesce((
      select jsonb_agg(jsonb_build_object(
               'rolname',        r.rolname::text,
               'rolsuper',       r.rolsuper,
               'rolbypassrls',   r.rolbypassrls,
               'rolreplication', r.rolreplication,
               'rolcanlogin',    r.rolcanlogin,
               'rolinherit',     r.rolinherit,
               'rolcreatedb',    r.rolcreatedb,
               'rolcreaterole',  r.rolcreaterole)
             order by r.rolname)
        from pg_roles r where r.rolname like 'origenlab\_%'), '[]'::jsonb),
    -- PostgreSQL 16 records one pg_auth_members row per grantor, so the same membership granted
    -- twice appears twice. The question the boundary asks is per member, not per grant: the options
    -- are therefore folded with bool_or, which preserves "no member inherits the owner" exactly.
    'owner_members', coalesce((
      select jsonb_agg(jsonb_build_object(
               'member',         g.member,
               'inherit_option', g.inherit_option,
               'set_option',     g.set_option)
             order by g.member)
        from (select m.rolname::text as member,
                     bool_or(am.inherit_option) as inherit_option,
                     bool_or(am.set_option) as set_option
                from pg_auth_members am
                join pg_roles m on m.oid = am.member
               where am.roleid = 'origenlab_owner'::regrole
               group by m.rolname::text) g), '[]'::jsonb),
    'runtime_role_memberships', coalesce((
      select jsonb_agg(jsonb_build_object(
               'member', m.rolname::text,
               'roleid', g.rolname::text)
             order by m.rolname, g.rolname)
        from pg_auth_members am
        join pg_roles m on m.oid = am.member
        join pg_roles g on g.oid = am.roleid
       where m.rolname in ('origenlab_api', 'origenlab_worker')
          or g.rolname in ('origenlab_api', 'origenlab_worker')), '[]'::jsonb),
    'service_role_in_origenlab_role', exists (
      select 1 from pg_auth_members am
        join pg_roles g on g.oid = am.roleid
       where am.member = 'service_role'::regrole
         and g.rolname like 'origenlab\_%')
  )
)::text;
