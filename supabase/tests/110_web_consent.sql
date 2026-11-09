-- Slice 8 — web quote form consent: crm.consent (DOMAIN.md §7, #72).
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
select has_table('crm', 'consent', 'crm.consent exists (#72)');
select is(
  (select count(*)::int from pg_class c join pg_namespace n on n.oid = c.relnamespace
    where c.relkind = 'r' and n.nspname = 'crm' and c.relname = 'consent'
      and c.relrowsecurity and not c.relforcerowsecurity),
  1, 'RLS is enabled and not forced on crm.consent');

-- ── fixtures: a person ───────────────────────────────────────────────────────────────────────
set local role origenlab_owner;
insert into crm.person (id, display_name, confirmation)
  values ('99000000-0000-4000-9900-000000001010', 'Persona Ficticia', 'machine_proposed');
reset role;

-- ── api ──────────────────────────────────────────────────────────────────────────────────────
select is(pg_temp.run_as('origenlab_api', $$ insert into crm.consent
    (email, purpose, text_version, accepted_at, source, source_record_id, person_id)
  values ('solicitante@ficticio.invalid', 'quote_request', 'consent-v1-2026-11', now(), 'web_quote_form',
          '99000000-0000-4000-9900-000000001020', '99000000-0000-4000-9900-000000001010') $$), 'ok',
  'the API records a consent row');
select is(left(pg_temp.run_as('origenlab_api', $$ insert into crm.consent
    (email, purpose, text_version, accepted_at, source, source_record_id)
  values ('solicitante@ficticio.invalid', 'quote_request', 'consent-v1-2026-11', now(), 'web_quote_form',
          '99000000-0000-4000-9900-000000001020') $$), 5),
  '23505', 'the same email, purpose, text version and message is recorded once');
select is(pg_temp.run_as('origenlab_api', $$ insert into crm.consent
    (email, purpose, text_version, accepted_at, source)
  values ('solicitante@ficticio.invalid', 'newsletter', 'consent-v1-2026-11', now(), 'newsletter_signup') $$), 'ok',
  'a row with no source record is not subject to the uniqueness rule');
select is(pg_temp.run_as('origenlab_api', $$ select count(*) from crm.consent $$), 'ok',
  'the API reads consent rows');
select is(left(pg_temp.run_as('origenlab_api', $$ update crm.consent set source = 'x' $$), 5),
  '42501', 'the API cannot rewrite a consent row');
select is(left(pg_temp.run_as('origenlab_api', $$ delete from crm.consent
  where email = 'solicitante@ficticio.invalid' $$), 5),
  '42501', 'the API cannot delete a consent row');

select is(left(pg_temp.run_as('origenlab_owner', $$ update crm.consent set source = 'x'
  where email = 'solicitante@ficticio.invalid' $$), 5), 'P0001',
  'a consent row is never updated, not even by the owner (trigger)');
select is(left(pg_temp.run_as('origenlab_owner', $$ delete from crm.consent
  where email = 'solicitante@ficticio.invalid' $$), 5), 'P0001',
  'a consent row is never deleted, not even by the owner (trigger)');

-- ── constraints ──────────────────────────────────────────────────────────────────────────────
select is(left(pg_temp.run_as('origenlab_owner', $$ insert into crm.consent
    (email, purpose, text_version, accepted_at, source)
  values ('x@ficticio.invalid', 'invalid_purpose', 'v1', now(), 'test') $$), 5),
  '23514', 'a purpose outside quote_request and newsletter is refused');
select is(left(pg_temp.run_as('origenlab_owner', $$ insert into crm.consent
    (email, purpose, text_version, accepted_at, source)
  values ('Mayuscula@Ficticio.invalid', 'quote_request', 'v1', now(), 'test') $$), 5),
  '23514', 'an email that is not lower-cased is refused');
select is(left(pg_temp.run_as('origenlab_owner', $$ insert into crm.consent
    (email, purpose, text_version, accepted_at, source)
  values ('x@ficticio.invalid', 'quote_request', '   ', now(), 'test') $$), 5),
  '23514', 'a blank text version is refused');

-- ── other roles ──────────────────────────────────────────────────────────────────────────────
select is(left(pg_temp.run_as('origenlab_worker', $$ select 1 from crm.consent $$), 5),
  '42501', 'the worker cannot read consent rows');
select is(left(pg_temp.run_as('anon', $$ select count(*) from crm.consent $$), 5), '42501',
  'anon is denied crm.consent');
select is(
  (select count(*)::int from crm.consent where email = 'solicitante@ficticio.invalid'),
  2, 'the two accepted rows stay on record');

select * from finish();
rollback;
