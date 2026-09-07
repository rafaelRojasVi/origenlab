-- Slice 0 — every foreign key in the seven application schemas is index-covered.
--
-- One catalogue assertion, not one assertion per constraint: the gate enumerates `pg_constraint`
-- itself, so a foreign key added later is judged by the same rule and cannot slip past by being
-- absent from a hand-written list.
--
-- A foreign key counts as covered when a **valid, ready B-tree** index on the referencing table
-- has the constraint's referencing columns, in the constraint's own column order, as its leading
-- key columns, and one of:
--
--   'full'             the index has no predicate;
--   'implied-partial'  the index predicate is a conjunction of `<column> IS NOT NULL` over the
--                      constraint's own referencing columns, and nothing else. Referential-action
--                      enforcement runs `SELECT 1 FROM ONLY child x WHERE x.fkcol = $1
--                      FOR KEY SHARE OF x`, and joins along the relationship are equally strict;
--                      `=` is strict, so `x.fkcol IS NOT NULL` is implied and the planner uses the
--                      index. Any other predicate — `WHERE status = 'open'`, a predicate over a
--                      column outside the constraint — is *not* implied and is rejected here.
--
-- That second class is why this gate is stricter than `supabase db advisors`, which ignores
-- `pg_index.indpred` entirely: the advisor accepted `task_owner_open_due_idx ... WHERE status =
-- 'open'` as covering `task_owner_operator_id_fkey`, which it does not. See the regression
-- assertion below and supabase/migrations/20260906200836_slice0_foreign_key_covering_indexes.sql.
--
-- The exception allowlist is closed and currently empty. `set_eq` compares it to the uncovered set
-- in both directions, so an uncovered foreign key that is not listed fails, and a listed exception
-- that has become covered also fails and must be deleted from the list.
begin;
create extension if not exists pgtap with schema extensions;
select plan(9);

-- Every foreign key in the seven application schemas, with its referencing columns in constraint
-- order and its coverage class.
create temporary view ol_fk as
select
  c.oid                                                                as conoid,
  n.nspname                                                            as fk_schema,
  rel.relname                                                          as fk_table,
  c.conname                                                            as fk_name,
  c.conkey                                                             as fk_attnums,
  rel.oid                                                              as fk_reloid,
  (select array_agg(a.attname order by k.ord)
     from unnest(c.conkey) with ordinality k(attnum, ord)
     join pg_attribute a on a.attrelid = rel.oid and a.attnum = k.attnum) as fk_columns
from pg_constraint c
join pg_class rel     on rel.oid = c.conrelid
join pg_namespace n   on n.oid   = rel.relnamespace
where c.contype = 'f'
  and n.nspname in ('crm', 'comms', 'outbound', 'evidence', 'catalog', 'procurement', 'platform');

-- B-tree indexes whose leading key columns are exactly the constraint's referencing columns, in
-- order, classified by whether their predicate is absent or implied by the foreign-key lookup.
-- The access method is part of the rule: the three GiST exclusion-constraint indexes in `crm`
-- lead with a referencing column but are not what a referential-action lookup or an equality join
-- should be made to depend on, so they never count as coverage here.
create temporary view ol_fk_index as
select
  f.conoid,
  i.indexrelid,
  i.indpred is null as unconditional,
  i.indpred is null
    or not exists (
      select 1
        from unnest(regexp_split_to_array(pg_get_expr(i.indpred, i.indrelid), ' AND ')) as conjunct
       where btrim(conjunct, '()') !~ '^[a-z_][a-z0-9_$]* IS NOT NULL$'
          or split_part(btrim(conjunct, '()'), ' ', 1) <> all (f.fk_columns)
    ) as implied
from ol_fk f
join pg_index i on i.indrelid = f.fk_reloid
join pg_class ic on ic.oid = i.indexrelid
where i.indisvalid
  and i.indisready
  and ic.relam = (select oid from pg_am where amname = 'btree')
  and i.indnkeyatts >= array_length(f.fk_attnums, 1)
  and (i.indkey::int2[])[0:array_length(f.fk_attnums, 1) - 1] = f.fk_attnums::int2[];

create temporary view ol_fk_coverage as
select
  f.fk_schema, f.fk_table, f.fk_name, f.fk_columns,
  case
    when exists (select 1 from ol_fk_index x where x.conoid = f.conoid and x.unconditional)
      then 'full'
    when exists (select 1 from ol_fk_index x where x.conoid = f.conoid and x.implied)
      then 'implied-partial'
    else 'uncovered'
  end as coverage
from ol_fk f;

-- The closed exception allowlist: schema, table, constraint and the reason the relation neither
-- joins nor pays a referential-action cost. Deliberately empty — every foreign key is covered.
create temporary view ol_fk_exception (fk_schema, fk_table, fk_name, reason) as
select * from (values (null::text, null::text, null::text, null::text)) v where false;

-- (a) The invariant. Both directions: nothing uncovered that is not an accepted exception, and no
--     accepted exception that is in fact covered and should be struck from the list.
select set_eq(
  $$ select fk_schema || '.' || fk_table || '.' || fk_name from ol_fk_coverage where coverage = 'uncovered' $$,
  $$ select fk_schema || '.' || fk_table || '.' || fk_name from ol_fk_exception $$,
  'every foreign key is index-covered, except exactly the accepted exceptions');

-- (b) An exception may not outlive the constraint it excuses.
select is(
  (select count(*)::int from ol_fk_exception e
    where not exists (select 1 from ol_fk f
                       where f.fk_schema = e.fk_schema and f.fk_table = e.fk_table and f.fk_name = e.fk_name)),
  0, 'no allowlisted exception names a foreign key that no longer exists');

-- (c) Every exception carries a reason.
select is(
  (select count(*)::int from ol_fk_exception where reason is null or length(btrim(reason)) = 0),
  0, 'every allowlisted exception states why');

-- (d) The census. A foreign key added without updating this count fails here even when it is
--     covered, so new relations are reviewed rather than absorbed silently.
select is(
  (select count(*)::int from ol_fk), 97,
  'the seven application schemas declare 97 foreign keys');
select is(
  (select count(*)::int from ol_fk_coverage where coverage = 'full'), 79,
  '79 foreign keys are covered by an unconditional index');
select is(
  (select count(*)::int from ol_fk_coverage where coverage = 'implied-partial'), 18,
  '18 foreign keys are covered by a partial index whose predicate the lookup implies');

-- (e) Regression against the advisor false negative this gate exists to catch: an index partial on
--     a column outside the constraint must not count, so crm.task.owner_operator_id needs — and now
--     has — an unconditional index of its own.
select is(
  (select coverage from ol_fk_coverage where fk_schema = 'crm' and fk_name = 'task_owner_operator_id_fkey'),
  'full', 'task_owner_operator_id_fkey is covered unconditionally, not by task_owner_open_due_idx');

-- (f) Every index that provides foreign-key coverage is owned by origenlab_owner.
select is(
  (select count(*)::int
     from (select distinct indexrelid from ol_fk_index where unconditional or implied) x
     join pg_class ic on ic.oid = x.indexrelid
    where ic.relowner <> 'origenlab_owner'::regrole),
  0, 'every covering index is owned by origenlab_owner');

-- (g) No redundant index: two indexes on one table may not have identical key columns, predicate
--     and uniqueness. This is what stops a later pass from "fixing" a finding that is already met.
select is(
  (select count(*)::int from (
     select i.indrelid, i.indkey::text, coalesce(pg_get_expr(i.indpred, i.indrelid), ''), i.indisunique
       from pg_index i
       join pg_class c on c.oid = i.indrelid
       join pg_namespace n on n.oid = c.relnamespace
      where n.nspname in ('crm', 'comms', 'outbound', 'evidence', 'catalog', 'procurement', 'platform')
      group by 1, 2, 3, 4 having count(*) > 1) d),
  0, 'no two indexes on one table share key columns, predicate and uniqueness');

select * from finish();
rollback;
