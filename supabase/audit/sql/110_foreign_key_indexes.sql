-- a10 — MIGRATION.md §5.2 (impl): every foreign key in the seven application schemas is covered by
-- an index whose leading columns are the referencing columns.
--
-- The rule is derived from pg_constraint, not from a list, so a foreign key added later is judged
-- by the same rule. A partial index counts as coverage only when its predicate is exactly
-- "<referencing column> is not null" — the predicate the referential-action lookup itself implies.
-- This is deliberately stricter than the Supabase performance advisor, which ignores
-- pg_index.indpred entirely. Mirrors supabase/tests/090_foreign_key_indexes.sql.
--
-- Read-only: one SELECT, no mutation, no session change.
with fk as (
  select c.oid, n.nspname, cl.relname, c.conname, c.conkey, c.conrelid
    from pg_constraint c
    join pg_class cl on cl.oid = c.conrelid
    join pg_namespace n on n.oid = cl.relnamespace
   where c.contype = 'f'
     and n.nspname in ('crm', 'comms', 'outbound', 'evidence', 'catalog', 'procurement', 'platform')
), covered as (
  select fk.oid,
         exists (
           select 1 from pg_index i
            where i.indrelid = fk.conrelid
              and i.indisvalid
              and (fk.conkey::int2[]) operator(pg_catalog.=)
                  ((i.indkey::int2[])[0 : array_length(fk.conkey, 1) - 1])
              and (i.indpred is null
                   or array_length(fk.conkey, 1) = 1
                      and pg_get_expr(i.indpred, i.indrelid) =
                          '(' || quote_ident((select a.attname from pg_attribute a
                                               where a.attrelid = fk.conrelid
                                                 and a.attnum = fk.conkey[1])) || ' IS NOT NULL)')
         ) as is_covered,
         exists (
           select 1 from pg_index i
            where i.indrelid = fk.conrelid
              and i.indisvalid
              and i.indpred is null
              and (fk.conkey::int2[]) operator(pg_catalog.=)
                  ((i.indkey::int2[])[0 : array_length(fk.conkey, 1) - 1])
         ) as is_covered_unconditionally
    from fk
)
select jsonb_build_object(
  'check', 'a10',
  'data', jsonb_build_object(
    'foreign_key_count', (select count(*) from fk),
    'covered_count', (select count(*) from covered where covered.is_covered),
    'covered_unconditionally', (select count(*) from covered where covered.is_covered_unconditionally),
    'uncovered', coalesce((select jsonb_agg(jsonb_build_object(
                                    'schema',     fk.nspname,
                                    'table',      fk.relname,
                                    'constraint', fk.conname)
                                  order by fk.nspname, fk.relname, fk.conname)
                             from fk join covered on covered.oid = fk.oid
                            where not covered.is_covered), '[]'::jsonb)
  )
)::text;
