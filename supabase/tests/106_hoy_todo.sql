-- Slice 7 — «Pendientes» de Hoy: crm.todo (DOMAIN.md §7, #68) and the todo.* audit events.
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
select has_table('crm', 'todo', 'crm.todo exists (#68)');
select is(
  (select count(*)::int from pg_class c join pg_namespace n on n.oid = c.relnamespace
    where c.relkind = 'r' and n.nspname = 'crm' and c.relname = 'todo'
      and c.relrowsecurity and not c.relforcerowsecurity),
  1, 'RLS is enabled and not forced on crm.todo');

-- ── fixtures: an operator and a closed case ──────────────────────────────────────────────────
set local role origenlab_owner;
insert into platform.operator (id, auth_user_id, email_norm, display_name, role, status) values
  ('99000000-0000-4000-9900-000000000601', '99000000-0000-4000-9900-0000000006f1',
   'pendientes.operator@ficticio.invalid', 'Operador Ficticio', 'sales', 'active');
insert into crm.opportunity (id, title, stage, owner_operator_id) values
  ('99000000-0000-4000-9900-000000000650', 'Caso cerrado sintético', 'lead',
   '99000000-0000-4000-9900-000000000601');
update crm.opportunity set stage = 'lost', closed_at = now()
  where id = '99000000-0000-4000-9900-000000000650';
reset role;

-- ── api: insert ──────────────────────────────────────────────────────────────────────────────
select is(pg_temp.run_as('origenlab_api', $$ insert into crm.todo
    (id, body, position, created_by_operator_id)
  values ('99000000-0000-4000-9900-000000000610', 'Llamar al proveedor ficticio', 1000,
          '99000000-0000-4000-9900-000000000601') $$), 'ok',
  'the API inserts an open line with no case');
select is(pg_temp.run_as('origenlab_api', $$ insert into crm.todo
    (id, body, is_urgent, position, due_on, opportunity_id, created_by_operator_id)
  values ('99000000-0000-4000-9900-000000000611', 'Revisar el caso cerrado', true, 2000, current_date,
          '99000000-0000-4000-9900-000000000650', '99000000-0000-4000-9900-000000000601') $$), 'ok',
  'the API inserts a line tied to a closed case (a to-do never touches the stage)');
select is((select status || '/' || version from crm.todo
            where id = '99000000-0000-4000-9900-000000000610'), 'open/1',
  'a new line is open at version 1');

-- ── constraints ──────────────────────────────────────────────────────────────────────────────
select is(left(pg_temp.run_as('origenlab_api', $$ insert into crm.todo
    (body, position, created_by_operator_id)
  values ('   ', 1, '99000000-0000-4000-9900-000000000601') $$), 5), '23514',
  'a blank body is rejected');
select is(left(pg_temp.run_as('origenlab_api', $$ insert into crm.todo
    (body, position, created_by_operator_id)
  values (repeat('x', 501), 1, '99000000-0000-4000-9900-000000000601') $$), 5), '23514',
  'a body over 500 characters is rejected');
select is(left(pg_temp.run_as('origenlab_api', $$ insert into crm.todo
    (body, position, status, created_by_operator_id)
  values ('Sin hora de cierre', 1, 'done', '99000000-0000-4000-9900-000000000601') $$), 5), '23514',
  'status done without completed_at is rejected');
select is(left(pg_temp.run_as('origenlab_api', $$ insert into crm.todo
    (body, position, status, created_by_operator_id)
  values ('Estado inventado', 1, 'archived', '99000000-0000-4000-9900-000000000601') $$), 5), '23514',
  'an unknown status is rejected');

-- ── api: complete and soft-delete ────────────────────────────────────────────────────────────
select is(pg_temp.run_as('origenlab_api', $$ update crm.todo
    set status = 'done', completed_at = now(), version = version + 1, updated_at = now()
  where id = '99000000-0000-4000-9900-000000000610' $$), 'ok',
  'the API completes a line (done with completed_at)');
select is(pg_temp.run_as('origenlab_api', $$ update crm.todo
    set status = 'deleted', version = version + 1, updated_at = now()
  where id = '99000000-0000-4000-9900-000000000611' $$), 'ok',
  'the API soft-deletes a line (status deleted)');

-- ── never physically deleted ─────────────────────────────────────────────────────────────────
select is(left(pg_temp.run_as('origenlab_api', $$ delete from crm.todo
  where id = '99000000-0000-4000-9900-000000000611' $$), 5), '42501',
  'the API holds no DELETE privilege on crm.todo');
select is(left(pg_temp.run_as('origenlab_owner', $$ delete from crm.todo
  where id = '99000000-0000-4000-9900-000000000611' $$), 5), 'P0001',
  'even the owner cannot delete a line (trigger)');

-- ── access ───────────────────────────────────────────────────────────────────────────────────
select is(left(pg_temp.run_as('origenlab_worker', $$ select count(*) from crm.todo $$), 5), '42501',
  'the worker has no access to crm.todo');
select is(left(pg_temp.run_as('anon', $$ select count(*) from crm.todo $$), 5), '42501',
  'anon is denied crm.todo');

-- ── events ───────────────────────────────────────────────────────────────────────────────────
select is(pg_temp.run_as('origenlab_api', $$ insert into crm.domain_event
    (aggregate_kind, aggregate_id, seq, event_type, payload_version, payload, actor_kind, actor_operator_id)
  values ('todo', '99000000-0000-4000-9900-000000000610', 1, 'todo.created', 1, '{}',
          'operator', '99000000-0000-4000-9900-000000000601') $$), 'ok',
  'a todo.created event on aggregate kind todo is accepted');

select * from finish();
rollback;
