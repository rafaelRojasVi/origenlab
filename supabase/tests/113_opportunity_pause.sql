-- Slice 8 — crm.opportunity.paused_until / pause_reason: «En pausa hasta…» as a state (DOMAIN.md §7.18).
-- Fictitious fixtures only.
begin;
create extension if not exists pgtap with schema extensions;
select plan(21);

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
select has_column('crm', 'opportunity', 'paused_until', 'crm.opportunity.paused_until exists');
select col_type_is('crm', 'opportunity', 'paused_until', 'date', 'paused_until is a calendar day');
select col_is_null('crm', 'opportunity', 'paused_until', 'paused_until is nullable: null = not paused');
select has_column('crm', 'opportunity', 'pause_reason', 'crm.opportunity.pause_reason exists');
select col_is_null('crm', 'opportunity', 'pause_reason', 'pause_reason is nullable with the date');
select is(
  (select count(*)::int from pg_constraint
    where conrelid = 'crm.opportunity'::regclass and conname = 'opportunity_pause_check' and contype = 'c'),
  1, 'opportunity_pause_check exists');
select has_index('crm', 'opportunity', 'opportunity_paused_until_idx', 'paused cases are found through an index');
select is(
  (select pg_get_indexdef('crm.opportunity_paused_until_idx'::regclass) like '%WHERE (paused_until IS NOT NULL)%'),
  true, 'the pause index is partial: only paused cases');

-- ── fixtures: one operator, one case ────────────────────────────────────────────────────────
set local role origenlab_owner;
insert into platform.operator (id, auth_user_id, email_norm, display_name, role, status)
  values ('99000000-0000-4000-9900-000000001120', '99000000-0000-4000-9900-0000000012f1',
          'operador@ficticio.invalid', 'Operador Ficticio', 'sales', 'active');
insert into crm.opportunity (id, title, stage, owner_operator_id)
  values ('99000000-0000-4000-9900-000000001121', 'Caso Ficticio', 'lead',
          '99000000-0000-4000-9900-000000001120');
reset role;

select is((select paused_until from crm.opportunity where id = '99000000-0000-4000-9900-000000001121'), null,
  'a new case is not paused');

-- ── the check: a date and a reason, both or neither ─────────────────────────────────────────
select is(pg_temp.run_as('origenlab_api', $$ update crm.opportunity
  set paused_until = date '2027-01-15', pause_reason = 'Esperando fondos o proyecto'
  where id = '99000000-0000-4000-9900-000000001121' $$), 'ok',
  'the API pauses a case until a day, for a reason');
select is(left(pg_temp.run_as('origenlab_api', $$ update crm.opportunity set pause_reason = null
  where id = '99000000-0000-4000-9900-000000001121' $$), 5),
  '23514', 'a date without a reason is refused');
select is(left(pg_temp.run_as('origenlab_api', $$ update crm.opportunity set paused_until = null
  where id = '99000000-0000-4000-9900-000000001121' $$), 5),
  '23514', 'a reason without a date is refused');
select is(left(pg_temp.run_as('origenlab_api', $$ update crm.opportunity set pause_reason = '   '
  where id = '99000000-0000-4000-9900-000000001121' $$), 5),
  '23514', 'a blank reason is refused');
select is(left(pg_temp.run_as('origenlab_api', $$ update crm.opportunity set pause_reason = repeat('x', 201)
  where id = '99000000-0000-4000-9900-000000001121' $$), 5),
  '23514', 'a reason over 200 characters is refused');
select is(pg_temp.run_as('origenlab_api', $$ update crm.opportunity
  set paused_until = null, pause_reason = null
  where id = '99000000-0000-4000-9900-000000001121' $$), 'ok',
  'the pause ends with both back to null');

-- ── other roles ──────────────────────────────────────────────────────────────────────────────
select is(pg_temp.run_as('origenlab_worker', $$ select paused_until, pause_reason from crm.opportunity $$), 'ok',
  'the worker reads the pause (the mail rules skip a paused case)');
select is(left(pg_temp.run_as('origenlab_worker', $$ update crm.opportunity
  set paused_until = current_date, pause_reason = 'x' $$), 5),
  '42501', 'the worker cannot pause a case');
select is(left(pg_temp.run_as('anon', $$ select paused_until from crm.opportunity $$), 5), '42501',
  'anon is denied the pause');

-- ── the event: opportunity.pause_set (20261113110000) ───────────────────────────────────────
select ok(pg_get_constraintdef((select oid from pg_constraint where conname = 'domain_event_type_check'))
    like '%opportunity.pause_set%', 'event vocabulary: opportunity.pause_set');
select is(pg_temp.run_as('origenlab_api', $$ insert into crm.domain_event
    (aggregate_kind, aggregate_id, seq, event_type, payload_version, payload, actor_kind, actor_operator_id)
  values ('opportunity', '99000000-0000-4000-9900-000000001121', 1, 'opportunity.pause_set', 1,
          '{"from_paused_until": null, "to_paused_until": "2027-01-15", "to_reason": "Esperando fondos o proyecto"}'::jsonb,
          'operator', '99000000-0000-4000-9900-000000001120') $$),
  'ok', 'the API appends an opportunity.pause_set event under an opportunity');
select is(left(pg_temp.run_as('origenlab_api', $$ insert into crm.domain_event
    (aggregate_kind, aggregate_id, seq, event_type, payload_version, payload, actor_kind, actor_operator_id)
  values ('task', '99000000-0000-4000-9900-000000001121', 1, 'opportunity.pause_set', 1,
          '{}'::jsonb, 'operator', '99000000-0000-4000-9900-000000001120') $$), 5),
  '23514', 'opportunity.pause_set may not be filed under another aggregate kind');

select * from finish();
rollback;
