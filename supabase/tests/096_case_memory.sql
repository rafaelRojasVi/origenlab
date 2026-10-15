-- Slice 7 — evidence.subject_memory (#58): a running machine memory per case, org or person.
-- Append-only, no FK on subject_id, worker inserts + selects, api selects.
-- Synthetic fixtures only.
begin;
create extension if not exists pgtap with schema extensions;
select plan(17);

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

-- Table exists.
select has_table('evidence', 'subject_memory', 'subject_memory exists');

-- subject_kind is closed.
select is(left(pg_temp.run_as('origenlab_worker', $$
  insert into evidence.subject_memory (subject_kind, subject_id, version, memory)
  values ('project', '96000000-0000-4000-8000-000000000001', 1, '{"summary":"x"}')
$$), 5), '23514', 'subject_kind must be opportunity, organization or person');

-- version must be positive.
select is(left(pg_temp.run_as('origenlab_worker', $$
  insert into evidence.subject_memory (subject_kind, subject_id, version, memory)
  values ('opportunity', '96000000-0000-4000-8000-000000000001', 0, '{"summary":"x"}')
$$), 5), '23514', 'version >= 1');

-- memory must be an object.
select is(left(pg_temp.run_as('origenlab_worker', $$
  insert into evidence.subject_memory (subject_kind, subject_id, version, memory)
  values ('opportunity', '96000000-0000-4000-8000-000000000001', 1, '"not an object"')
$$), 5), '23514', 'memory must be a JSON object');

-- Worker inserts a valid row.
select is(pg_temp.run_as('origenlab_worker', $$
  insert into evidence.subject_memory (id, subject_kind, subject_id, version, memory, model)
  values ('96000000-0000-4000-8000-000000000010', 'opportunity',
          '96000000-0000-4000-8000-000000000001', 1,
          '{"summary":"Cotización de microscopio","facts":[],"open_questions":[],"next_step":null}',
          'claude-haiku-5-5')
$$), 'ok', 'worker inserts a valid opportunity memory');

-- API cannot insert.
select is(left(pg_temp.run_as('origenlab_api', $$
  insert into evidence.subject_memory (subject_kind, subject_id, version, memory)
  values ('opportunity', '96000000-0000-4000-8000-000000000001', 2, '{"summary":"x"}')
$$), 5), '42501', 'api may not insert subject_memory');

-- API can select.
select is(pg_temp.run_as('origenlab_api', $$
  select 1 from evidence.subject_memory where subject_kind = 'opportunity' limit 1
$$), 'ok', 'api may select subject_memory');

-- Worker can select.
select is(pg_temp.run_as('origenlab_worker', $$
  select 1 from evidence.subject_memory where id = '96000000-0000-4000-8000-000000000010'
$$), 'ok', 'worker may select subject_memory');

-- anon has no access.
select is(left(pg_temp.run_as('anon', $$
  select 1 from evidence.subject_memory limit 1
$$), 5), '42501', 'anon has no access');

-- Append-only: worker has no UPDATE grant (privilege denied before the trigger).
select is(left(pg_temp.run_as('origenlab_worker', $$
  update evidence.subject_memory set model = 'other' where id = '96000000-0000-4000-8000-000000000010'
$$), 5), '42501', 'worker cannot update subject_memory (no UPDATE grant)');

-- Append-only: worker has no DELETE grant (privilege denied before the trigger).
select is(left(pg_temp.run_as('origenlab_worker', $$
  delete from evidence.subject_memory where id = '96000000-0000-4000-8000-000000000010'
$$), 5), '42501', 'worker cannot delete subject_memory (no DELETE grant)');

-- Unique constraint on (subject_kind, subject_id, version).
select is(left(pg_temp.run_as('origenlab_worker', $$
  insert into evidence.subject_memory (subject_kind, subject_id, version, memory)
  values ('opportunity', '96000000-0000-4000-8000-000000000001', 1, '{"summary":"dup"}')
$$), 5), '23505', 'duplicate (subject_kind, subject_id, version) is refused');

-- Worker may insert version 2 for same subject.
select is(pg_temp.run_as('origenlab_worker', $$
  insert into evidence.subject_memory (subject_kind, subject_id, version, memory)
  values ('opportunity', '96000000-0000-4000-8000-000000000001', 2, '{"summary":"updated"}')
$$), 'ok', 'worker may insert a later version of the same subject');

-- subject_id carries no FK — a random UUID is accepted.
select is(pg_temp.run_as('origenlab_worker', $$
  insert into evidence.subject_memory (subject_kind, subject_id, version, memory)
  values ('organization', '96000000-0000-4000-8000-000000000099', 1, '{"summary":"org"}')
$$), 'ok', 'subject_id is not a FK — any UUID is accepted');

-- Organization and person subjects also work.
select is(pg_temp.run_as('origenlab_worker', $$
  insert into evidence.subject_memory (subject_kind, subject_id, version, memory)
  values ('person', '96000000-0000-4000-8000-000000000098', 1, '{"summary":"person"}')
$$), 'ok', 'person subject_kind is accepted');

-- RLS enabled, not forced.
select ok((select relrowsecurity and not relforcerowsecurity from pg_class
            where oid = 'evidence.subject_memory'::regclass), 'RLS enabled, not forced');

-- Table comment starts with DOMAIN.md §7 #58.
select ok(obj_description('evidence.subject_memory'::regclass, 'pg_class') like 'DOMAIN.md §7 #58%',
  'table comment references DOMAIN.md §7 #58');

select * from finish();
rollback;
