-- Slice 8 — crm.opportunity.origin: the channel a case came from (DOMAIN.md §7.16).
-- Fictitious fixtures only.
begin;
create extension if not exists pgtap with schema extensions;
select plan(27);

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
select has_column('crm', 'opportunity', 'origin', 'crm.opportunity.origin exists');
select col_type_is('crm', 'opportunity', 'origin', 'text', 'origin is text');
select col_is_null('crm', 'opportunity', 'origin', 'origin is nullable: an earlier case has no known channel');
select col_hasnt_default('crm', 'opportunity', 'origin', 'origin has no default: null means not recorded');
select has_check('crm', 'opportunity', 'crm.opportunity carries check constraints');
select is(
  (select count(*)::int from pg_constraint
    where conrelid = 'crm.opportunity'::regclass and conname = 'opportunity_origin_check' and contype = 'c'),
  1, 'opportunity_origin_check exists');
select has_index('crm', 'opportunity', 'opportunity_origin_idx', 'cases are listed by origin through an index');
select is(
  (select pg_get_indexdef('crm.opportunity_origin_idx'::regclass) like '%WHERE (origin IS NOT NULL)%'),
  true, 'the origin index is partial: only cases with a recorded origin');

-- ── fixtures: one operator, one case ────────────────────────────────────────────────────────
set local role origenlab_owner;
insert into platform.operator (id, auth_user_id, email_norm, display_name, role, status)
  values ('99000000-0000-4000-9900-000000001110', '99000000-0000-4000-9900-0000000011f1',
          'operador@ficticio.invalid', 'Operador Ficticio', 'sales', 'active');
insert into crm.opportunity (id, title, stage, owner_operator_id)
  values ('99000000-0000-4000-9900-000000001111', 'Caso Ficticio', 'lead',
          '99000000-0000-4000-9900-000000001110');
reset role;

select is((select origin from crm.opportunity where id = '99000000-0000-4000-9900-000000001111'), null,
  'a case created without an origin has none');

-- ── the check: exactly the nine channels ─────────────────────────────────────────────────────
select is(
  (select count(*)::int from unnest(array['correo', 'web', 'whatsapp', 'campana', 'mercado_publico',
                                         'fabricante', 'recomendacion', 'llamada', 'ex_labdelivery']) o
    where pg_temp.run_as('origenlab_owner', format(
      $$ update crm.opportunity set origin = %L where id = '99000000-0000-4000-9900-000000001111' $$, o)) = 'ok'),
  9, 'each of the nine channels is accepted');
select is(left(pg_temp.run_as('origenlab_owner', $$ update crm.opportunity set origin = 'invalid_channel'
  where id = '99000000-0000-4000-9900-000000001111' $$), 5),
  '23514', 'a channel outside the nine is refused');
select is(left(pg_temp.run_as('origenlab_owner', $$ update crm.opportunity set origin = 'Web'
  where id = '99000000-0000-4000-9900-000000001111' $$), 5),
  '23514', 'a channel is case-sensitive: Web is refused');
select is(left(pg_temp.run_as('origenlab_owner', $$ update crm.opportunity set origin = ''
  where id = '99000000-0000-4000-9900-000000001111' $$), 5),
  '23514', 'an empty channel is refused');
select is(pg_temp.run_as('origenlab_owner', $$ update crm.opportunity set origin = null
  where id = '99000000-0000-4000-9900-000000001111' $$), 'ok',
  'the origin may be cleared back to null');
select is(
  (select array_length(regexp_split_to_array(
      (select pg_get_constraintdef(oid) from pg_constraint where conname = 'opportunity_origin_check'),
      '''::text'), 1) - 1),
  9, 'the check names exactly nine channels');

-- ── api: the one writer ─────────────────────────────────────────────────────────────────────
select is(pg_temp.run_as('origenlab_api', $$ update crm.opportunity set origin = 'whatsapp'
  where id = '99000000-0000-4000-9900-000000001111' $$), 'ok',
  'the API sets the origin of a case');
select is(left(pg_temp.run_as('origenlab_api', $$ update crm.opportunity set origin = 'invalid_channel'
  where id = '99000000-0000-4000-9900-000000001111' $$), 5),
  '23514', 'the API cannot set a channel outside the nine');
select is((select origin from crm.opportunity where id = '99000000-0000-4000-9900-000000001111'), 'whatsapp',
  'the API''s value stays on the case');

-- ── other roles ──────────────────────────────────────────────────────────────────────────────
select is(pg_temp.run_as('origenlab_worker', $$ select origin from crm.opportunity $$), 'ok',
  'the worker reads the origin');
select is(left(pg_temp.run_as('origenlab_worker', $$ update crm.opportunity set origin = 'web' $$), 5),
  '42501', 'the worker cannot write the origin');
select is(left(pg_temp.run_as('anon', $$ select origin from crm.opportunity $$), 5), '42501',
  'anon is denied crm.opportunity.origin');
select is(left(pg_temp.run_as('anon', $$ update crm.opportunity set origin = 'web' $$), 5), '42501',
  'anon cannot write the origin');

-- ── the event of a correction: opportunity.origin_set (20261109110000) ──────────────────────
select ok(pg_get_constraintdef((select oid from pg_constraint where conname = 'domain_event_type_check'))
    like '%opportunity.origin_set%', 'event vocabulary: opportunity.origin_set');
select ok(pg_get_constraintdef((select oid from pg_constraint where conname = 'domain_event_type_check'))
    like '%invoice.unpaid%', 'the rebuilt vocabulary keeps the newest earlier type');
select is(pg_temp.run_as('origenlab_api', $$ insert into crm.domain_event
    (aggregate_kind, aggregate_id, seq, event_type, payload_version, payload, actor_kind, actor_operator_id)
  values ('opportunity', '99000000-0000-4000-9900-000000001111', 1, 'opportunity.origin_set', 1,
          '{"from_origin": null, "to_origin": "web"}'::jsonb, 'operator', '99000000-0000-4000-9900-000000001110') $$),
  'ok', 'the API appends an opportunity.origin_set event under an opportunity');
select is(left(pg_temp.run_as('origenlab_api', $$ insert into crm.domain_event
    (aggregate_kind, aggregate_id, seq, event_type, payload_version, payload, actor_kind, actor_operator_id)
  values ('opportunity_organization', '99000000-0000-4000-9900-000000001111', 1, 'opportunity.origin_set', 1,
          '{}'::jsonb, 'operator', '99000000-0000-4000-9900-000000001110') $$), 5),
  '23514', 'opportunity.origin_set may not be filed under another aggregate kind');
select is(left(pg_temp.run_as('origenlab_api', $$ insert into crm.domain_event
    (aggregate_kind, aggregate_id, seq, event_type, payload_version, payload, actor_kind, actor_operator_id)
  values ('opportunity', '99000000-0000-4000-9900-000000001111', 2, 'opportunity.origin_invented', 1,
          '{}'::jsonb, 'operator', '99000000-0000-4000-9900-000000001110') $$), 5),
  '23514', 'the event vocabulary stays closed');

select * from finish();
rollback;
