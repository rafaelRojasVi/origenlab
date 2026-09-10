-- a14 — the installed extension inventory. RECORDED, never failed on.
--
-- pg_cron, pgmq and the independent bucket-backup job are slice 0 hosted gate items
-- (MIGRATION.md §5); locally they are absent by design, and the hosted project's set is its own.
-- The audit therefore reports what is installed and where, and leaves the judgement to the gate
-- rather than encoding a hosted expectation it cannot verify from this era.
--
-- Read-only: one SELECT, no mutation, no session change.
select jsonb_build_object(
  'check', 'a14',
  'data', jsonb_build_object(
    'extensions', coalesce((select jsonb_agg(jsonb_build_object(
                                     'name',    e.extname::text,
                                     'schema',  coalesce(n.nspname::text, ''),
                                     'version', e.extversion)
                                   order by e.extname)
                              from pg_extension e
                              left join pg_namespace n on n.oid = e.extnamespace), '[]'::jsonb)
  )
)::text;
