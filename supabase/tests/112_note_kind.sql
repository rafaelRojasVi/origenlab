-- Slice 8 — crm.note.note_kind / call_outcome / call_next_step: a note can be a typed «Llamada»
-- record (DOMAIN.md §7 #44, §7.17). Fictitious fixtures only.
begin;
create extension if not exists pgtap with schema extensions;
select plan(35);

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
select has_column('crm', 'note', 'note_kind', 'crm.note.note_kind exists');
select has_column('crm', 'note', 'call_outcome', 'crm.note.call_outcome exists');
select has_column('crm', 'note', 'call_next_step', 'crm.note.call_next_step exists');
select col_type_is('crm', 'note', 'note_kind', 'text', 'note_kind is text');
select col_type_is('crm', 'note', 'call_outcome', 'text', 'call_outcome is text');
select col_type_is('crm', 'note', 'call_next_step', 'text', 'call_next_step is text');
select col_is_null('crm', 'note', 'note_kind', 'note_kind is nullable: null is an ordinary note');
select col_is_null('crm', 'note', 'call_outcome', 'call_outcome is nullable');
select col_is_null('crm', 'note', 'call_next_step', 'call_next_step is nullable');
select col_hasnt_default('crm', 'note', 'note_kind', 'note_kind has no default: an earlier note stays ordinary');
select col_hasnt_default('crm', 'note', 'call_outcome', 'call_outcome has no default');
select col_hasnt_default('crm', 'note', 'call_next_step', 'call_next_step has no default');
select is(
  (select count(*)::int from pg_constraint
    where conrelid = 'crm.note'::regclass and contype = 'c'
      and conname in ('note_kind_check', 'note_call_outcome_check', 'note_call_next_step_check', 'note_call_shape')),
  4, 'the four call-record checks exist');

-- ── fixtures: one operator, one case, one ordinary note ──────────────────────────────────────
set local role origenlab_owner;
insert into platform.operator (id, auth_user_id, email_norm, display_name, role, status)
  values ('99000000-0000-4000-9900-000000002001', '99000000-0000-4000-9900-0000000020f1',
          'operador@ficticio.invalid', 'Operador Ficticio', 'sales', 'active');
insert into crm.opportunity (id, title, stage, owner_operator_id)
  values ('99000000-0000-4000-9900-000000002000', 'Caso Ficticio', 'lead',
          '99000000-0000-4000-9900-000000002001');
reset role;

select is(pg_temp.run_as('origenlab_api', $$ insert into crm.note
    (id, subject_kind, subject_id, author_operator_id, body)
  values ('99000000-0000-4000-9900-000000002010', 'opportunity', '99000000-0000-4000-9900-000000002000',
          '99000000-0000-4000-9900-000000002001', 'Nota normal') $$),
  'ok', 'the API still inserts an ordinary note with no kind');
select is((select note_kind from crm.note where id = '99000000-0000-4000-9900-000000002010'), null,
  'an ordinary note has no kind');

-- ── valid call records ──────────────────────────────────────────────────────────────────────
select is(pg_temp.run_as('origenlab_api', $$ insert into crm.note
    (id, subject_kind, subject_id, author_operator_id, body, note_kind, call_outcome, call_next_step)
  values ('99000000-0000-4000-9900-000000002011', 'opportunity', '99000000-0000-4000-9900-000000002000',
          '99000000-0000-4000-9900-000000002001', 'Habló con la compradora', 'llamada', 'contesto',
          'Llamar la semana que viene') $$),
  'ok', 'the API inserts a call record with outcome and next step');
select is(
  (select count(*)::int from unnest(array['contesto', 'no_contesto', 'buzon', 'numero_malo', 'reunion']) o
    where pg_temp.run_as('origenlab_api', format($$ insert into crm.note
        (subject_kind, subject_id, author_operator_id, body, note_kind, call_outcome)
      values ('opportunity', '99000000-0000-4000-9900-000000002000',
              '99000000-0000-4000-9900-000000002001', 'Llamada', 'llamada', %L) $$, o)) = 'ok'),
  5, 'each of the five outcomes is accepted');
select is(pg_temp.run_as('origenlab_api', $$ insert into crm.note
    (subject_kind, subject_id, author_operator_id, body, note_kind, call_outcome)
  values ('opportunity', '99000000-0000-4000-9900-000000002000',
          '99000000-0000-4000-9900-000000002001', 'Sin próximo paso', 'llamada', 'buzon') $$),
  'ok', 'the next step is optional on a call record');

-- ── the checks ──────────────────────────────────────────────────────────────────────────────
select is(left(pg_temp.run_as('origenlab_api', $$ insert into crm.note
    (subject_kind, subject_id, author_operator_id, body, call_outcome)
  values ('opportunity', '99000000-0000-4000-9900-000000002000',
          '99000000-0000-4000-9900-000000002001', 'x', 'contesto') $$), 5),
  '23514', 'an outcome without the kind is refused');
select is(left(pg_temp.run_as('origenlab_api', $$ insert into crm.note
    (subject_kind, subject_id, author_operator_id, body, call_next_step)
  values ('opportunity', '99000000-0000-4000-9900-000000002000',
          '99000000-0000-4000-9900-000000002001', 'x', 'Llamar') $$), 5),
  '23514', 'a next step without the kind is refused');
select is(left(pg_temp.run_as('origenlab_api', $$ insert into crm.note
    (subject_kind, subject_id, author_operator_id, body, note_kind, call_outcome)
  values ('opportunity', '99000000-0000-4000-9900-000000002000',
          '99000000-0000-4000-9900-000000002001', 'x', 'llamada', 'invalid_outcome') $$), 5),
  '23514', 'an outcome outside the five is refused');
select is(left(pg_temp.run_as('origenlab_api', $$ insert into crm.note
    (subject_kind, subject_id, author_operator_id, body, note_kind)
  values ('opportunity', '99000000-0000-4000-9900-000000002000',
          '99000000-0000-4000-9900-000000002001', 'x', 'llamada') $$), 5),
  '23514', 'a call record without an outcome is refused');
select is(left(pg_temp.run_as('origenlab_api', $$ insert into crm.note
    (subject_kind, subject_id, author_operator_id, body, note_kind, call_outcome)
  values ('opportunity', '99000000-0000-4000-9900-000000002000',
          '99000000-0000-4000-9900-000000002001', 'x', 'invalid_kind', 'contesto') $$), 5),
  '23514', 'a kind other than llamada is refused');
select is(left(pg_temp.run_as('origenlab_api', format($$ insert into crm.note
    (subject_kind, subject_id, author_operator_id, body, note_kind, call_outcome, call_next_step)
  values ('opportunity', '99000000-0000-4000-9900-000000002000',
          '99000000-0000-4000-9900-000000002001', 'x', 'llamada', 'contesto', %L) $$, repeat('a', 501))), 5),
  '23514', 'a next step over 500 characters is refused');
select is(pg_temp.run_as('origenlab_api', format($$ insert into crm.note
    (subject_kind, subject_id, author_operator_id, body, note_kind, call_outcome, call_next_step)
  values ('opportunity', '99000000-0000-4000-9900-000000002000',
          '99000000-0000-4000-9900-000000002001', 'x', 'llamada', 'contesto', %L) $$, repeat('a', 500))),
  'ok', 'a next step of exactly 500 characters is accepted');

-- ── grants: insert and select, never update ─────────────────────────────────────────────────
select is(left(pg_temp.run_as('origenlab_api', $$ update crm.note set note_kind = null
  where id = '99000000-0000-4000-9900-000000002011' $$), 5),
  '42501', 'the API cannot update note_kind');
select is(left(pg_temp.run_as('origenlab_api', $$ update crm.note set call_outcome = 'buzon'
  where id = '99000000-0000-4000-9900-000000002011' $$), 5),
  '42501', 'the API cannot update call_outcome');
select is(left(pg_temp.run_as('origenlab_api', $$ update crm.note set call_next_step = 'otro'
  where id = '99000000-0000-4000-9900-000000002011' $$), 5),
  '42501', 'the API cannot update call_next_step');

-- ── the guard: the three columns are frozen after insert, for every role ─────────────────────
select is(left(pg_temp.run_as('origenlab_owner', $$ update crm.note set call_outcome = 'buzon'
  where id = '99000000-0000-4000-9900-000000002011' $$), 5),
  'P0001', 'the guard refuses a changed outcome, even for the owner');
select is(left(pg_temp.run_as('origenlab_owner', $$ update crm.note set call_next_step = 'otro'
  where id = '99000000-0000-4000-9900-000000002011' $$), 5),
  'P0001', 'the guard refuses a changed next step');
select is(left(pg_temp.run_as('origenlab_owner', $$ update crm.note
  set note_kind = null, call_outcome = null, call_next_step = null
  where id = '99000000-0000-4000-9900-000000002011' $$), 5),
  'P0001', 'the guard refuses turning a call record into an ordinary note');
select is(left(pg_temp.run_as('origenlab_owner', $$ update crm.note
  set subject_id = '99000000-0000-4000-9900-000000002099', call_outcome = 'buzon'
  where id = '99000000-0000-4000-9900-000000002011' $$), 5),
  'P0001', 'the identity-repoint exception does not let a call column change with it');
select is(pg_temp.run_as('origenlab_api', $$ update crm.note
  set status = 'archived', archived_at = now(), archived_by_operator_id = '99000000-0000-4000-9900-000000002001',
      archive_reason = 'Duplicada', version = 2
  where id = '99000000-0000-4000-9900-000000002011' $$),
  'ok', 'a call record can still be archived');

-- ── other roles ──────────────────────────────────────────────────────────────────────────────
select is(pg_temp.run_as('origenlab_worker', $$ select note_kind, call_outcome, call_next_step from crm.note $$), 'ok',
  'the worker reads the call columns');
select is(left(pg_temp.run_as('anon', $$ select call_outcome from crm.note $$), 5), '42501',
  'anon is denied the call columns');

select * from finish();
rollback;
