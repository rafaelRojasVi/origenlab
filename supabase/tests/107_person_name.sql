-- Slice 7 — «people first»: crm.person_name (DOMAIN.md §7, #69), a person's other names, and the
-- undo of an automatic merge (the API may clear crm.person.merged_into_person_id).
-- Fictitious fixtures only.
begin;
create extension if not exists pgtap with schema extensions;
select plan(13);

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
select has_table('crm', 'person_name', 'crm.person_name exists (#69)');
select is(
  (select count(*)::int from pg_class c join pg_namespace n on n.oid = c.relnamespace
    where c.relkind = 'r' and n.nspname = 'crm' and c.relname = 'person_name'
      and c.relrowsecurity and not c.relforcerowsecurity),
  1, 'RLS is enabled and not forced on crm.person_name');

-- ── fixtures: two people, the second merged into the first ───────────────────────────────────
set local role origenlab_owner;
insert into crm.person (id, display_name, confirmation) values
  ('99000000-0000-4000-9900-000000000701', 'Persona Ficticia Uno', 'machine_proposed'),
  ('99000000-0000-4000-9900-000000000702', 'Persona Ficticia Dos', 'machine_proposed');
update crm.person set merged_into_person_id = '99000000-0000-4000-9900-000000000701'
  where id = '99000000-0000-4000-9900-000000000702';
reset role;

-- ── api: insert and soft-remove ──────────────────────────────────────────────────────────────
select is(pg_temp.run_as('origenlab_api', $$ insert into crm.person_name
    (id, person_id, name_display, name_norm, source, confirmation)
  values ('99000000-0000-4000-9900-000000000710', '99000000-0000-4000-9900-000000000701',
          'P. Ficticia', 'p ficticia', 'display_name', 'machine_proposed') $$), 'ok',
  'the API inserts a machine-proposed name');
select is(left(pg_temp.run_as('origenlab_api', $$ insert into crm.person_name
    (person_id, name_display, name_norm, source, confirmation)
  values ('99000000-0000-4000-9900-000000000701', 'P. Ficticia', 'p ficticia',
          'signature', 'machine_proposed') $$), 5), '23505',
  'a second live row with the same (person, normalised name) is refused');
select is(left(pg_temp.run_as('origenlab_api', $$ insert into crm.person_name
    (person_id, name_display, name_norm, source, confirmation)
  values ('99000000-0000-4000-9900-000000000701', 'X', 'x', 'invented', 'machine_proposed') $$), 5),
  '23514', 'an unknown source is rejected');
select is(left(pg_temp.run_as('origenlab_api', $$ insert into crm.person_name
    (person_id, name_display, name_norm, source, confirmation)
  values ('99000000-0000-4000-9900-000000000701', 'X', 'x', 'operator', 'guessed') $$), 5),
  '23514', 'an unknown confirmation is rejected');
select is(pg_temp.run_as('origenlab_api', $$ update crm.person_name
    set removed_at = now(), removed_by_operator_id = null
  where id = '99000000-0000-4000-9900-000000000710' $$), 'ok',
  'the API soft-removes a name');
select is(pg_temp.run_as('origenlab_api', $$ insert into crm.person_name
    (person_id, name_display, name_norm, source, confirmation)
  values ('99000000-0000-4000-9900-000000000701', 'P. Ficticia', 'p ficticia',
          'operator', 'confirmed') $$), 'ok',
  'the same name is allowed again once the earlier row is removed');
select is(left(pg_temp.run_as('origenlab_api', $$ update crm.person_name
    set name_norm = 'otro' where person_id = '99000000-0000-4000-9900-000000000701' $$), 5), '42501',
  'the API cannot rewrite name_norm (column-restricted UPDATE)');

-- ── never deleted ────────────────────────────────────────────────────────────────────────────
select is(left(pg_temp.run_as('origenlab_api', $$ delete from crm.person_name $$), 5), '42501',
  'the API holds no DELETE privilege on crm.person_name');

-- ── worker: read only ────────────────────────────────────────────────────────────────────────
select is(pg_temp.run_as('origenlab_worker', $$ select count(*) from crm.person_name $$), 'ok',
  'the worker reads crm.person_name');
select is(left(pg_temp.run_as('origenlab_worker', $$ insert into crm.person_name
    (person_id, name_display, name_norm, source, confirmation)
  values ('99000000-0000-4000-9900-000000000701', 'Y', 'y', 'operator', 'confirmed') $$), 5), '42501',
  'the worker cannot insert into crm.person_name');

-- ── undo of an automatic merge ───────────────────────────────────────────────────────────────
select is(pg_temp.run_as('origenlab_api', $$ update crm.person set merged_into_person_id = null
  where id = '99000000-0000-4000-9900-000000000702' $$), 'ok',
  'the API clears crm.person.merged_into_person_id (undo of an automatic merge)');

select * from finish();
rollback;
