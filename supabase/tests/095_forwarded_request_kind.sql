-- Slice 4 — evidence.source_record kind forwarded_request: the worker writes it, the API never does.
-- Synthetic fixtures only.
begin;
create extension if not exists pgtap with schema extensions;
select plan(4);

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

select ok(pg_get_constraintdef((select oid from pg_constraint where conname = 'source_record_kind_check')) like '%forwarded_request%',
  'acquisition vocabulary: forwarded_request');
select is(pg_temp.run_as('origenlab_worker', $$ insert into evidence.source_record (kind, dedupe_key, payload)
  values ('forwarded_request', 'forwarded_request:pgtap-095', '{"original_email": "a@ejemplo.invalid"}') $$), 'ok',
  'the worker records who really asked');
select is(left(pg_temp.run_as('origenlab_api', $$ insert into evidence.source_record (kind, dedupe_key, payload)
  values ('forwarded_request', 'forwarded_request:pgtap-095-api', '{}') $$), 5), '42501',
  'the API never writes one');
select is(pg_temp.run_as('origenlab_api', $$ update evidence.source_record set review_status = 'rejected'
  where dedupe_key = 'forwarded_request:pgtap-095' $$), 'ok',
  'the API dismisses or promotes one by its review status');

select * from finish();
rollback;
