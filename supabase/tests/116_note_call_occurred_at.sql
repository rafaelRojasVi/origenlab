-- Slice 8 — crm.note.occurred_at: when a «Llamada» logged after the fact happened. Fictitious
-- fixtures only.
begin;
create extension if not exists pgtap with schema extensions;
select plan(14);

grant origenlab_api to session_user with set true, inherit false;

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
select has_column('crm', 'note', 'occurred_at', 'crm.note.occurred_at exists');
select col_type_is('crm', 'note', 'occurred_at', 'timestamp with time zone', 'occurred_at is timestamptz');
select col_is_null('crm', 'note', 'occurred_at', 'occurred_at is nullable: null is the logged time');
select col_hasnt_default('crm', 'note', 'occurred_at', 'occurred_at has no default: an earlier call keeps its time');

-- ── fixtures ─────────────────────────────────────────────────────────────────────────────────
set local role origenlab_owner;
insert into platform.operator (id, auth_user_id, email_norm, display_name, role, status)
  values ('99000000-0000-4000-9900-000000003001', '99000000-0000-4000-9900-0000000030f1',
          'operador@ficticio.invalid', 'Operador Ficticio', 'sales', 'active');
insert into crm.opportunity (id, title, stage, owner_operator_id)
  values ('99000000-0000-4000-9900-000000003000', 'Caso Ficticio', 'lead',
          '99000000-0000-4000-9900-000000003001');
reset role;

-- ── accepted ─────────────────────────────────────────────────────────────────────────────────
select is(pg_temp.run_as('origenlab_api', $$ insert into crm.note
    (id, subject_kind, subject_id, author_operator_id, body, note_kind, call_outcome, occurred_at)
  values ('99000000-0000-4000-9900-000000003010', 'opportunity', '99000000-0000-4000-9900-000000003000',
          '99000000-0000-4000-9900-000000003001', 'Hablamos ayer', 'llamada', 'contesto',
          now() - interval '1 day') $$),
  'ok', 'the API logs a call that happened yesterday');
select is(pg_temp.run_as('origenlab_api', $$ insert into crm.note
    (subject_kind, subject_id, author_operator_id, body, note_kind, call_outcome)
  values ('opportunity', '99000000-0000-4000-9900-000000003000',
          '99000000-0000-4000-9900-000000003001', 'Recién', 'llamada', 'buzon') $$),
  'ok', 'a call without occurred_at is still accepted');
select is(pg_temp.run_as('origenlab_api', $$ insert into crm.note
    (subject_kind, subject_id, author_operator_id, body, note_kind, call_outcome, occurred_at)
  values ('opportunity', '99000000-0000-4000-9900-000000003000',
          '99000000-0000-4000-9900-000000003001', 'Reloj adelantado', 'llamada', 'buzon',
          now() + interval '2 minutes') $$),
  'ok', 'two minutes of clock slack are accepted');

-- ── refused ──────────────────────────────────────────────────────────────────────────────────
select is(left(pg_temp.run_as('origenlab_api', $$ insert into crm.note
    (subject_kind, subject_id, author_operator_id, body, occurred_at)
  values ('opportunity', '99000000-0000-4000-9900-000000003000',
          '99000000-0000-4000-9900-000000003001', 'Nota', now() - interval '1 day') $$), 5),
  '23514', 'an ordinary note cannot carry occurred_at');
select is(left(pg_temp.run_as('origenlab_api', $$ insert into crm.note
    (subject_kind, subject_id, author_operator_id, body, note_kind, call_outcome, occurred_at)
  values ('opportunity', '99000000-0000-4000-9900-000000003000',
          '99000000-0000-4000-9900-000000003001', 'Mañana', 'llamada', 'contesto',
          now() + interval '1 hour') $$), 5),
  '23514', 'a call in the future is refused');

-- ── frozen after insert ──────────────────────────────────────────────────────────────────────
select is(left(pg_temp.run_as('origenlab_api', $$ update crm.note set occurred_at = now()
  where id = '99000000-0000-4000-9900-000000003010' $$), 5),
  '42501', 'the API cannot update occurred_at');
select is(left(pg_temp.run_as('origenlab_owner', $$ update crm.note set occurred_at = now()
  where id = '99000000-0000-4000-9900-000000003010' $$), 5),
  'P0001', 'the guard refuses a changed occurred_at, even for the owner');
select is(left(pg_temp.run_as('origenlab_owner', $$ update crm.note
  set subject_id = '99000000-0000-4000-9900-000000003099', occurred_at = now()
  where id = '99000000-0000-4000-9900-000000003010' $$), 5),
  'P0001', 'the identity-repoint exception does not let occurred_at change with it');
select is(pg_temp.run_as('origenlab_api', $$ update crm.note
  set status = 'archived', archived_at = now(), archived_by_operator_id = '99000000-0000-4000-9900-000000003001',
      archive_reason = 'Duplicada', version = 2
  where id = '99000000-0000-4000-9900-000000003010' $$),
  'ok', 'a back-dated call can still be archived');
select is(left(pg_temp.run_as('anon', $$ select occurred_at from crm.note $$), 5), '42501',
  'anon is denied occurred_at');

select * from finish();
rollback;
