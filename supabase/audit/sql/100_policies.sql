-- a09 — the RLS policy inventory, compared policy by policy against the committed baseline.
--
-- Names, commands, roles and the predicate texts are all carried: a policy that survives with a
-- widened USING clause is drift the count alone would not show.
--
-- Read-only: one SELECT, no mutation, no session change.
select jsonb_build_object(
  'check', 'a09',
  'data', jsonb_build_object(
    'policy_count', (select count(*) from pg_policies p
                      where p.schemaname in ('crm', 'comms', 'outbound', 'evidence',
                                             'catalog', 'procurement', 'platform')),
    'policies', coalesce((select jsonb_agg(jsonb_build_object(
                                   'schema',     p.schemaname::text,
                                   'table',      p.tablename::text,
                                   'policy',     p.policyname::text,
                                   'permissive', p.permissive,
                                   'roles',      p.roles::text,
                                   'command',    p.cmd,
                                   'using',      coalesce(p.qual, ''),
                                   'with_check', coalesce(p.with_check, ''))
                                 order by p.schemaname, p.tablename, p.policyname)
                            from pg_policies p
                           where p.schemaname in ('crm', 'comms', 'outbound', 'evidence',
                                                  'catalog', 'procurement', 'platform')), '[]'::jsonb)
  )
)::text;
