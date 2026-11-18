-- Slice 8 — crm.opportunity.follow_up_operator_id / follow_up_channel: who follows a sold-quote case
-- up and how («Ventas › Seguimiento»). Fictitious fixtures only.
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
select has_column('crm', 'opportunity', 'follow_up_operator_id', 'crm.opportunity.follow_up_operator_id exists');
select col_type_is('crm', 'opportunity', 'follow_up_operator_id', 'uuid', 'follow_up_operator_id is a uuid');
select col_is_null('crm', 'opportunity', 'follow_up_operator_id', 'follow_up_operator_id is nullable: null = Sin asignar');
select fk_ok('crm', 'opportunity', 'follow_up_operator_id', 'platform', 'operator', 'id',
  'follow_up_operator_id references platform.operator(id)');
select has_column('crm', 'opportunity', 'follow_up_channel', 'crm.opportunity.follow_up_channel exists');
select col_type_is('crm', 'opportunity', 'follow_up_channel', 'text', 'follow_up_channel is text');
select col_is_null('crm', 'opportunity', 'follow_up_channel', 'follow_up_channel is nullable');
select has_index('crm', 'opportunity', 'opportunity_follow_up_operator_idx', 'the encargado is found through an index');

-- ── fixtures: one operator, one case ────────────────────────────────────────────────────────
set local role origenlab_owner;
insert into platform.operator (id, auth_user_id, email_norm, display_name, role, status)
  values ('99000000-0000-4000-9900-000000001150', '99000000-0000-4000-9900-0000000015f1',
          'operador@ficticio.invalid', 'Operador Ficticio', 'sales', 'active');
insert into crm.opportunity (id, title, stage, owner_operator_id)
  values ('99000000-0000-4000-9900-000000001151', 'Caso Ficticio', 'lead',
          '99000000-0000-4000-9900-000000001150');
reset role;

select is((select follow_up_operator_id from crm.opportunity where id = '99000000-0000-4000-9900-000000001151'), null,
  'a new case has no encargado');

-- ── the channel check ────────────────────────────────────────────────────────────────────────
select is(pg_temp.run_as('origenlab_api', $$ update crm.opportunity
  set follow_up_operator_id = '99000000-0000-4000-9900-000000001150', follow_up_channel = 'whatsapp'
  where id = '99000000-0000-4000-9900-000000001151' $$), 'ok',
  'the API sets the encargado and whatsapp');
select is(pg_temp.run_as('origenlab_api', $$ update crm.opportunity set follow_up_channel = 'llamada'
  where id = '99000000-0000-4000-9900-000000001151' $$), 'ok', 'llamada is allowed');
select is(pg_temp.run_as('origenlab_api', $$ update crm.opportunity set follow_up_channel = 'correo'
  where id = '99000000-0000-4000-9900-000000001151' $$), 'ok', 'correo is allowed');
select is(pg_temp.run_as('origenlab_api', $$ update crm.opportunity set follow_up_channel = null
  where id = '99000000-0000-4000-9900-000000001151' $$), 'ok', 'a null channel is allowed');
select ok(pg_temp.run_as('origenlab_api', $$ update crm.opportunity set follow_up_channel = 'sms'
  where id = '99000000-0000-4000-9900-000000001151' $$) like '23514:%opportunity_follow_up_channel_check%',
  'sms is refused by opportunity_follow_up_channel_check');
select is(left(pg_temp.run_as('origenlab_api', $$ update crm.opportunity
  set follow_up_operator_id = '99000000-0000-4000-9900-0000000019ff'
  where id = '99000000-0000-4000-9900-000000001151' $$), 5),
  '23503', 'an encargado that is not an operator is refused');

-- ── privileges: same shape as the pause columns ──────────────────────────────────────────────
select is(
  (select array_agg(has_column_privilege('origenlab_api', 'crm.opportunity', c, 'update') order by c)
     from unnest(array['follow_up_operator_id', 'follow_up_channel', 'paused_until']) c),
  array[true, true, true], 'the API can update both follow-up columns wherever it can update paused_until');
select is(
  (select array_agg(has_column_privilege('origenlab_worker', 'crm.opportunity', c, 'select') order by c)
     from unnest(array['follow_up_operator_id', 'follow_up_channel', 'paused_until']) c),
  array[true, true, true], 'the worker reads the follow-up columns like the pause columns');
select is(left(pg_temp.run_as('origenlab_worker', $$ update crm.opportunity
  set follow_up_channel = 'correo' $$), 5),
  '42501', 'the worker cannot set the follow-up');
select is(left(pg_temp.run_as('anon', $$ select follow_up_channel from crm.opportunity $$), 5), '42501',
  'anon is denied the follow-up');

-- ── the event: opportunity.follow_up_set ─────────────────────────────────────────────────────
select ok(pg_get_constraintdef((select oid from pg_constraint where conname = 'domain_event_type_check'))
    like '%opportunity.follow_up_set%' and pg_get_constraintdef((select oid from pg_constraint where conname = 'domain_event_type_check'))
    like '%opportunity.pause_set%', 'event vocabulary: opportunity.follow_up_set, pause_set kept');
select is(pg_temp.run_as('origenlab_api', $$ insert into crm.domain_event
    (aggregate_kind, aggregate_id, seq, event_type, payload_version, payload, actor_kind, actor_operator_id)
  values ('opportunity', '99000000-0000-4000-9900-000000001151', 1, 'opportunity.follow_up_set', 1,
          '{"to_operator_id": "99000000-0000-4000-9900-000000001150", "to_channel": "llamada"}'::jsonb,
          'operator', '99000000-0000-4000-9900-000000001150') $$),
  'ok', 'the API appends an opportunity.follow_up_set event under an opportunity');

select * from finish();
rollback;
