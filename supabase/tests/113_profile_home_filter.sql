-- Slice 8 — platform.operator_profile.home_filter: the «Hoy» filter a profile opens on
-- (DOMAIN.md §7 #39). Companion to `20261111100000_slice8_profile_home_filter.sql`.
--
-- One nullable column with a closed set (todo, llamadas, cotizar), readable by the API column by
-- column like every other non-secret column, written by nobody but the owner (the roster tool),
-- and outside the version trigger: changing it never ends a profile session. Every value is
-- fictitious.
begin;
create extension if not exists pgtap with schema extensions;
grant usage on schema extensions to origenlab_owner;
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

select plan(15);

-- ── shape ────────────────────────────────────────────────────────────────────────────────────
select has_column('platform', 'operator_profile', 'home_filter', 'platform.operator_profile.home_filter exists');
select col_type_is('platform', 'operator_profile', 'home_filter', 'text', 'home_filter is text');
select col_is_null('platform', 'operator_profile', 'home_filter', 'home_filter is nullable: null is the default view');
select col_hasnt_default('platform', 'operator_profile', 'home_filter', 'home_filter has no default: existing profiles stay null');
select is(
  (select count(*)::int from pg_constraint
    where conrelid = 'platform.operator_profile'::regclass and contype = 'c'
      and conname = 'operator_profile_home_filter_check'),
  1, 'the home_filter check exists');

-- ── fixtures: one principal, one shared profile ──────────────────────────────────────────────
set local role origenlab_owner;
insert into platform.auth_principal (id, email_norm) values
  ('99000000-0000-4000-9900-000000003001', 'compartida@ficticio.example.test');
insert into platform.operator (id, auth_user_id, display_name, role, status, sign_in_kind) values
  ('99000000-0000-4000-9900-000000003011', gen_random_uuid(), 'Perfil Ficticio', 'sales', 'active', 'shared_profile');
insert into platform.operator_profile (operator_id, principal_id, profile_key, pin_hash) values
  ('99000000-0000-4000-9900-000000003011', '99000000-0000-4000-9900-000000003001', 'perfil-uno',
   '$argon2id$v=19$m=65536,t=3,p=1$c2FsdHNhbHRzYWx0c2FsdA$aGFzaGhhc2hoYXNoaGFzaGhhc2hoYXNoaGFzaGhhc2g');
reset role;

select is((select home_filter from platform.operator_profile
            where operator_id = '99000000-0000-4000-9900-000000003011'), null,
  'a profile inserted without it has no home filter');

-- ── the check ────────────────────────────────────────────────────────────────────────────────
select is(
  (select count(*)::int from unnest(array['todo', 'llamadas', 'cotizar']) v
    where pg_temp.run_as('origenlab_owner', format($$ update platform.operator_profile set home_filter = %L
      where operator_id = '99000000-0000-4000-9900-000000003011' $$, v)) = 'ok'),
  3, 'each of the three filters is accepted');
select is(left(pg_temp.run_as('origenlab_owner', $$ update platform.operator_profile set home_filter = 'otra'
  where operator_id = '99000000-0000-4000-9900-000000003011' $$), 5),
  '23514', 'a filter outside the three is refused');
select is(left(pg_temp.run_as('origenlab_owner', $$ update platform.operator_profile set home_filter = ''
  where operator_id = '99000000-0000-4000-9900-000000003011' $$), 5),
  '23514', 'an empty filter is refused (null means none)');

-- ── outside the version trigger: a filter change never ends a session ────────────────────────
select is(pg_temp.run_as('origenlab_owner', $$ update platform.operator_profile set home_filter = null
  where operator_id = '99000000-0000-4000-9900-000000003011' $$), 'ok', 'the owner clears the filter');
create temp table v0 as
  select version from platform.operator_profile where operator_id = '99000000-0000-4000-9900-000000003011';
select is(pg_temp.run_as('origenlab_owner', $$ update platform.operator_profile set home_filter = 'llamadas'
  where operator_id = '99000000-0000-4000-9900-000000003011' $$), 'ok', 'the owner sets the filter');
select is(
  (select version from platform.operator_profile where operator_id = '99000000-0000-4000-9900-000000003011'),
  (select version from v0),
  'changing home_filter leaves version unchanged (the profile session survives)');

-- ── grants: the API reads it and cannot write it; the worker never sees it ───────────────────
select column_privs_are('platform', 'operator_profile', 'home_filter', 'origenlab_api', array['SELECT'],
  'origenlab_api may only select home_filter');
select is(left(pg_temp.run_as('origenlab_api', $$ update platform.operator_profile set home_filter = 'todo'
  where operator_id = '99000000-0000-4000-9900-000000003011' $$), 5),
  '42501', 'the API cannot write home_filter: only the roster tool does');
select is(left(pg_temp.run_as('origenlab_worker', $$ select home_filter from platform.operator_profile $$), 5),
  '42501', 'the worker cannot read home_filter');

select * from finish();
rollback;
