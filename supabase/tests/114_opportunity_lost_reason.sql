-- Slice 8 — crm.opportunity.lost_reason: why a case was lost or abandoned, coded (DOMAIN.md §7.19).
-- Fictitious fixtures only.
begin;
create extension if not exists pgtap with schema extensions;
select plan(16);

grant origenlab_api    to session_user with set true, inherit false;
grant origenlab_worker to session_user with set true, inherit false;

create function pg_temp.run_as(p_role text, p_sql text) returns text
language plpgsql as $f$
begin
  execute format('set role %I', p_role);
  execute p_sql;
  reset role;
  return 'ok';
exception when others then
  reset role;
  return sqlstate || ': ' || sqlerrm;
end
$f$;

-- ── shape ────────────────────────────────────────────────────────────────────────────────────
select has_column('crm', 'opportunity', 'lost_reason', 'crm.opportunity.lost_reason exists');
select col_type_is('crm', 'opportunity', 'lost_reason', 'text', 'lost_reason is text');
select col_is_null('crm', 'opportunity', 'lost_reason', 'lost_reason is nullable: an open case has none');
select col_hasnt_default('crm', 'opportunity', 'lost_reason', 'lost_reason has no default');
select is(
  (select count(*)::int from pg_constraint
    where conrelid = 'crm.opportunity'::regclass and conname = 'opportunity_lost_reason_check' and contype = 'c'),
  1, 'opportunity_lost_reason_check exists');
select has_index('crm', 'opportunity', 'opportunity_lost_reason_idx', 'losses are counted by reason through an index');

-- ── fixtures: one operator, one case ────────────────────────────────────────────────────────
set local role origenlab_owner;
insert into platform.operator (id, auth_user_id, email_norm, display_name, role, status)
  values ('99000000-0000-4000-9900-000000001140', '99000000-0000-4000-9900-0000000014f1',
          'operador@ficticio.invalid', 'Operador Ficticio', 'sales', 'active');
insert into crm.opportunity (id, title, stage, owner_operator_id)
  values ('99000000-0000-4000-9900-000000001141', 'Caso Ficticio', 'lead',
          '99000000-0000-4000-9900-000000001140');
reset role;

-- ── the check: exactly the eight codes ──────────────────────────────────────────────────────
select is(
  (select count(*)::int from unnest(array['precio', 'otro_proveedor', 'sin_presupuesto', 'sin_respuesta',
                                         'no_era_real', 'plazo_entrega', 'cambio_necesidad', 'duplicado']) r
    where pg_temp.run_as('origenlab_api', format(
      $$ update crm.opportunity set lost_reason = %L where id = '99000000-0000-4000-9900-000000001141' $$, r)) = 'ok'),
  8, 'each of the eight codes is accepted');
select is(left(pg_temp.run_as('origenlab_api', $$ update crm.opportunity set lost_reason = 'Sin respuesta'
  where id = '99000000-0000-4000-9900-000000001141' $$), 5),
  '23514', 'the chip text is not a code: Sin respuesta is refused');
select is(left(pg_temp.run_as('origenlab_api', $$ update crm.opportunity set lost_reason = ''
  where id = '99000000-0000-4000-9900-000000001141' $$), 5),
  '23514', 'an empty code is refused');
select is(pg_temp.run_as('origenlab_api', $$ update crm.opportunity set lost_reason = null
  where id = '99000000-0000-4000-9900-000000001141' $$), 'ok',
  'the code may be cleared (a reopened case)');
select is(
  (select array_length(regexp_split_to_array(
      (select pg_get_constraintdef(oid) from pg_constraint where conname = 'opportunity_lost_reason_check'),
      '''::text'), 1) - 1),
  8, 'the check names exactly eight codes');

-- ── the backfill mapping, replayed on a fixture ─────────────────────────────────────────────
set local role origenlab_owner;
insert into crm.opportunity (id, title, stage, owner_operator_id, closed_at, close_reason)
  values ('99000000-0000-4000-9900-000000001142', 'Perdido por silencio', 'lead',
          '99000000-0000-4000-9900-000000001140', now(), 'Sin respuesta: tres correos sin contestar');
update crm.opportunity set stage = 'abandoned' where id = '99000000-0000-4000-9900-000000001142';
update crm.opportunity
   set lost_reason = case when close_reason ilike 'sin respuesta%' then 'sin_respuesta' end
 where id = '99000000-0000-4000-9900-000000001142' and lost_reason is null;
reset role;
select is((select lost_reason from crm.opportunity where id = '99000000-0000-4000-9900-000000001142'),
  'sin_respuesta', 'a chip with a detail after the colon maps to its code');

-- ── other roles ──────────────────────────────────────────────────────────────────────────────
select is(pg_temp.run_as('origenlab_worker', $$ select lost_reason from crm.opportunity $$), 'ok',
  'the worker reads the reason');
select is(left(pg_temp.run_as('origenlab_worker', $$ update crm.opportunity set lost_reason = 'precio' $$), 5),
  '42501', 'the worker cannot write the reason');
select is(left(pg_temp.run_as('anon', $$ select lost_reason from crm.opportunity $$), 5), '42501',
  'anon is denied crm.opportunity.lost_reason');
select is(left(pg_temp.run_as('anon', $$ update crm.opportunity set lost_reason = 'precio' $$), 5), '42501',
  'anon cannot write the reason');

select * from finish();
rollback;
