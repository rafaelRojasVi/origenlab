-- Slice 1 — shared Google Workspace sign-in with operator profiles, proven in the database.
--
-- Companion to `20260928180000_slice1_shared_workspace_operator_profiles.sql`: the operator's
-- sign-in kind and address shape, the profile link that only a shared_profile operator can
-- take, a PIN column that refuses anything but an Argon2id PHC string, the version guards that
-- end sessions on every security-relevant change (and not on a throttle update), the principal ↔
-- operator address exclusion, the append-only audit, and each runtime role's exact reach.
--
-- Exercised as the owner unless stated. SQLSTATEs: 23514 check, 23505 unique, 23503 foreign key,
-- P0001 trigger guard, 42501 privilege. Every value is fictitious.
begin;
create extension if not exists pgtap with schema extensions;
grant usage on schema extensions to origenlab_owner;
grant origenlab_api    to session_user with set true, inherit false;
grant origenlab_worker to session_user with set true, inherit false;

create function pg_temp.run_as(p_role text, p_sql text) returns text
language plpgsql as $$
begin
  execute format('set role %I', p_role);
  execute p_sql;
  reset role;
  return 'ok';
exception when others then
  reset role;
  return sqlstate || ': ' || sqlerrm;
end
$$;

set role origenlab_owner;
select plan(71);

-- A valid Argon2id PHC string of an invented PIN (the value is irrelevant here; only its shape is).
create temp table phc (n int, v text);
insert into phc values
  (1, '$argon2id$v=19$m=65536,t=3,p=1$c2FsdHNhbHRzYWx0c2FsdA$aGFzaGhhc2hoYXNoaGFzaGhhc2hoYXNoaGFzaGhhc2g'),
  (2, '$argon2id$v=19$m=65536,t=3,p=1$b3RoZXJzYWx0b3RoZXJzYQ$b3RoZXJoYXNob3RoZXJoYXNob3RoZXJoYXNob3RoZXI');

insert into platform.auth_principal (id, email_norm) values
  ('73000000-0000-4000-8000-000000000001', 'compartida@example.test'),
  ('73000000-0000-4000-8000-000000000002', 'otra.compartida@example.test');
insert into platform.operator (id, auth_user_id, display_name, role, status, sign_in_kind) values
  ('73000000-0000-4000-8000-000000000011', gen_random_uuid(), 'Perfil Admin', 'admin', 'active', 'shared_profile'),
  ('73000000-0000-4000-8000-000000000012', gen_random_uuid(), 'Perfil Ventas', 'sales', 'active', 'shared_profile');
insert into platform.operator (id, auth_user_id, email_norm, display_name, role, status) values
  ('73000000-0000-4000-8000-000000000021', gen_random_uuid(), 'individual@example.test', 'Individual', 'admin', 'active');
insert into platform.operator_profile (operator_id, principal_id, profile_key, pin_hash)
  select '73000000-0000-4000-8000-000000000011', '73000000-0000-4000-8000-000000000001', 'admin-uno', v from phc where n = 1;
insert into platform.operator_profile (operator_id, principal_id, profile_key, pin_hash)
  select '73000000-0000-4000-8000-000000000012', '73000000-0000-4000-8000-000000000001', 'ventas-uno', v from phc where n = 1;

-- ── 1. shape ──────────────────────────────────────────────────────────────────────────────────
select has_table('platform', 'auth_principal', 'platform.auth_principal exists');
select has_table('platform', 'operator_profile', 'platform.operator_profile exists');
select has_table('platform', 'auth_event', 'platform.auth_event exists');
select col_default_is('platform', 'operator', 'sign_in_kind', 'google_account'::text,
  'an operator signs in with its own Google account unless stated otherwise');
select is(
  (select count(*)::int from information_schema.columns
    where table_schema = 'platform' and table_name in ('auth_principal', 'operator_profile', 'auth_event')
      and column_name ~ 'pin' and column_name not in ('pin_hash', 'pin_set_at')),
  0, 'no column but the hash and its timestamp is about a PIN');
select hasnt_column('platform', 'auth_event', 'pin_hash', 'the audit has no column that could hold a PIN or its hash');

-- ── 2. operator sign-in kind ──────────────────────────────────────────────────────────────────
select throws_ok($$ insert into platform.operator (auth_user_id, display_name, role, status) values (gen_random_uuid(), 'Sin correo', 'sales', 'active') $$,
  '23514', null, 'a google_account operator must have its address');
select throws_ok($$ insert into platform.operator (auth_user_id, email_norm, display_name, role, status, sign_in_kind) values (gen_random_uuid(), 'perfil@example.test', 'Con correo', 'sales', 'active', 'shared_profile') $$,
  '23514', null, 'a shared_profile operator has no address: no invented mailbox is ever needed');
select throws_ok($$ insert into platform.operator (auth_user_id, display_name, role, status, sign_in_kind) values (gen_random_uuid(), 'X', 'sales', 'active', 'robot') $$,
  '23514', null, 'the sign-in kind is a closed vocabulary');
select throws_ok($$ update platform.operator set sign_in_kind = 'google_account', email_norm = 'x@example.test' where id = '73000000-0000-4000-8000-000000000012' $$,
  'P0001', null, 'the sign-in kind never changes in place');
select throws_ok($$ update platform.operator set version = 0 where id = '73000000-0000-4000-8000-000000000012' $$,
  'P0001', null, 'an operator version never decreases');

-- ── 3. the profile link ───────────────────────────────────────────────────────────────────────
select throws_ok($$ insert into platform.operator_profile (operator_id, principal_id, profile_key, pin_hash)
  select '73000000-0000-4000-8000-000000000021', '73000000-0000-4000-8000-000000000001', 'individual', v from phc where n = 1 $$,
  '23503', null, 'a google_account operator cannot be linked as a profile');
select throws_ok($$ insert into platform.operator_profile (operator_id, operator_sign_in_kind, principal_id, profile_key, pin_hash)
  select '73000000-0000-4000-8000-000000000021', 'google_account', '73000000-0000-4000-8000-000000000001', 'individual', v from phc where n = 1 $$,
  '23514', null, 'the link names shared_profile and nothing else');
select throws_ok($$ insert into platform.operator (id, auth_user_id, display_name, role, status, sign_in_kind) values ('73000000-0000-4000-8000-000000000013', gen_random_uuid(), 'Tercero', 'viewer', 'active', 'shared_profile');
  insert into platform.operator_profile (operator_id, principal_id, profile_key, pin_hash)
  select '73000000-0000-4000-8000-000000000013', '73000000-0000-4000-8000-000000000001', 'admin-uno', v from phc where n = 1 $$,
  '23505', null, 'a profile key is unique per principal');
select throws_ok($$ insert into platform.operator_profile (operator_id, principal_id, profile_key, pin_hash)
  values ('73000000-0000-4000-8000-000000000013', '73000000-0000-4000-8000-000000000001', 'tercero', '123456') $$,
  '23514', null, 'a plaintext PIN is refused: only an Argon2id PHC string fits');
select throws_ok($$ insert into platform.operator_profile (operator_id, principal_id, profile_key, pin_hash)
  values ('73000000-0000-4000-8000-000000000013', '73000000-0000-4000-8000-000000000001', 'tercero',
          '$argon2i$v=19$m=65536,t=3,p=1$c2FsdHNhbHRzYWx0c2FsdA$aGFzaGhhc2hoYXNoaGFzaGhhc2hoYXNoaGFzaGhhc2g') $$,
  '23514', null, 'another Argon2 variant is refused: Argon2id only');
select throws_ok($$ insert into platform.operator (id, auth_user_id, display_name, role, status, sign_in_kind) values ('73000000-0000-4000-8000-000000000014', gen_random_uuid(), 'Clave rara', 'viewer', 'active', 'shared_profile');
  insert into platform.operator_profile (operator_id, principal_id, profile_key, pin_hash)
  select '73000000-0000-4000-8000-000000000014', '73000000-0000-4000-8000-000000000001', 'Karla Admin', v from phc where n = 1 $$,
  '23514', null, 'a profile key is a lowercase slug');
select throws_ok($$ update platform.operator_profile set operator_id = '73000000-0000-4000-8000-000000000013' where operator_id = '73000000-0000-4000-8000-000000000012' $$,
  'P0001', null, 'a profile never moves to another operator');

-- ── 4. version guards ─────────────────────────────────────────────────────────────────────────
update platform.operator set role = 'admin' where id = '73000000-0000-4000-8000-000000000012';
select is((select version from platform.operator where id = '73000000-0000-4000-8000-000000000012'), 2,
  'a role change bumps the operator version, whoever writes it');
update platform.operator set role = 'sales', version = version + 1 where id = '73000000-0000-4000-8000-000000000012';
select is((select version from platform.operator where id = '73000000-0000-4000-8000-000000000012'), 3,
  'a writer that bumps the version itself is not bumped twice');
update platform.operator set status = 'disabled' where id = '73000000-0000-4000-8000-000000000012';
select is((select version from platform.operator where id = '73000000-0000-4000-8000-000000000012'), 4,
  'a status change bumps the operator version');
update platform.operator set display_name = 'Perfil Ventas Uno' where id = '73000000-0000-4000-8000-000000000012';
select is((select version from platform.operator where id = '73000000-0000-4000-8000-000000000012'), 4,
  'a display-name change is not security-relevant and bumps nothing');

update platform.operator_profile set failed_attempts = 3, last_failed_at = now() where operator_id = '73000000-0000-4000-8000-000000000011';
select is((select version from platform.operator_profile where operator_id = '73000000-0000-4000-8000-000000000011'), 1,
  'a throttle update does not bump the profile version: a wrong PIN signs nobody out');
update platform.operator_profile set pin_set_at = '2020-01-01', pin_hash = (select v from phc where n = 2)
  where operator_id = '73000000-0000-4000-8000-000000000011';
select is((select version from platform.operator_profile where operator_id = '73000000-0000-4000-8000-000000000011'), 2,
  'a new PIN bumps the profile version');
select ok((select pin_set_at > '2020-01-02' from platform.operator_profile where operator_id = '73000000-0000-4000-8000-000000000011'),
  'a new PIN restamps pin_set_at from the database clock');
update platform.operator_profile set status = 'disabled' where operator_id = '73000000-0000-4000-8000-000000000011';
select is((select version from platform.operator_profile where operator_id = '73000000-0000-4000-8000-000000000011'), 3,
  'a profile status change bumps the profile version');
update platform.operator_profile set principal_id = '73000000-0000-4000-8000-000000000002' where operator_id = '73000000-0000-4000-8000-000000000011';
select is((select version from platform.operator_profile where operator_id = '73000000-0000-4000-8000-000000000011'), 4,
  'relinking a profile to another principal bumps the profile version');
update platform.operator_profile set sort_order = 5 where operator_id = '73000000-0000-4000-8000-000000000011';
select is((select version from platform.operator_profile where operator_id = '73000000-0000-4000-8000-000000000011'), 4,
  'reordering the cards is not security-relevant');
select throws_ok($$ update platform.operator_profile set version = 1 where operator_id = '73000000-0000-4000-8000-000000000011' $$,
  'P0001', null, 'a profile version never decreases');

update platform.auth_principal set status = 'disabled' where id = '73000000-0000-4000-8000-000000000002';
select is((select version from platform.auth_principal where id = '73000000-0000-4000-8000-000000000002'), 2,
  'disabling a principal bumps its version');
update platform.auth_principal set provider_subject = '1098765', provider_issuer = 'https://accounts.google.com'
  where id = '73000000-0000-4000-8000-000000000002';
select is((select version from platform.auth_principal where id = '73000000-0000-4000-8000-000000000002'), 3,
  'pinning the Google account (issuer + subject) bumps the principal version');
update platform.auth_principal set failed_attempts = 9, locked_until = now() + interval '1 minute' where id = '73000000-0000-4000-8000-000000000002';
select is((select version from platform.auth_principal where id = '73000000-0000-4000-8000-000000000002'), 3,
  'a principal throttle update bumps nothing');
select throws_ok($$ update platform.auth_principal set provider = 'microsoft' where id = '73000000-0000-4000-8000-000000000002' $$,
  'P0001', null, 'the provider of a principal never changes');
update platform.auth_principal set provider_subject = '1098766' where id = '73000000-0000-4000-8000-000000000002';
select is((select version from platform.auth_principal where id = '73000000-0000-4000-8000-000000000002'), 4,
  're-pinning another subject (a recreated account) bumps the version: every session of the old one ends');
select throws_ok($$ update platform.auth_principal set provider_issuer = null where id = '73000000-0000-4000-8000-000000000002' $$,
  '23514', null, 'a subject is never pinned without its issuer');
select throws_ok($$ insert into platform.auth_principal (email_norm, provider_issuer) values ('solo.emisor@example.test', 'https://accounts.google.com') $$,
  '23514', null, 'an issuer is never recorded without a subject');
select throws_ok($$ update platform.auth_principal set provider_issuer = 'accounts.google.com' where id = '73000000-0000-4000-8000-000000000002' $$,
  '23514', null, 'the issuer is stored in its one canonical spelling');
select throws_ok($$ insert into platform.auth_principal (email_norm, provider_issuer, provider_subject) values ('mismo.sujeto@example.test', 'https://accounts.google.com', '1098766') $$,
  '23505', null, 'one Google account is at most one principal');

-- ── 5. a principal is never an operator ───────────────────────────────────────────────────────
select throws_ok($$ insert into platform.operator (auth_user_id, email_norm, display_name, role, status) values (gen_random_uuid(), 'compartida@example.test', 'Suplantación', 'admin', 'active') $$,
  '23505', null, 'an operator may not take a shared principal''s address');
select throws_ok($$ insert into platform.auth_principal (email_norm) values ('individual@example.test') $$,
  '23505', null, 'a principal may not take an individual operator''s address');
select throws_ok($$ insert into platform.auth_principal (email_norm) values ('Mayus@Example.test') $$,
  '23514', null, 'a principal address is normalized');

-- ── 6. the audit ──────────────────────────────────────────────────────────────────────────────
insert into platform.auth_event (event_type, principal_id, principal_email_norm, operator_id)
  values ('profile.selected', '73000000-0000-4000-8000-000000000001', 'compartida@example.test', '73000000-0000-4000-8000-000000000012');
select throws_ok($$ update platform.auth_event set operator_id = null $$, 'P0001', null, 'an audit event is never rewritten');
select throws_ok($$ delete from platform.auth_event $$, 'P0001', null, 'an audit event is never deleted');
select throws_ok($$ insert into platform.auth_event (event_type, principal_id, principal_email_norm) values ('profile.selection_refused', '73000000-0000-4000-8000-000000000001', 'compartida@example.test') $$,
  '23514', null, 'a refusal carries its reason');
select throws_ok($$ insert into platform.auth_event (event_type, principal_id, principal_email_norm, refusal_reason) values ('profile.selection_refused', '73000000-0000-4000-8000-000000000001', 'compartida@example.test', 'pin was 123456') $$,
  '23514', null, 'a refusal reason is a closed vocabulary, so free text (a PIN) cannot be recorded');
select throws_ok($$ insert into platform.auth_event (event_type, principal_id, principal_email_norm) values ('profile.selected', '73000000-0000-4000-8000-000000000001', 'compartida@example.test') $$,
  '23514', null, 'a selection names the operator selected');
select throws_ok($$ insert into platform.auth_event (event_type, principal_id) values ('profile.cleared', '73000000-0000-4000-8000-000000000001') $$,
  '23514', null, 'the principal is recorded with its address');

-- ── 7. runtime roles ──────────────────────────────────────────────────────────────────────────
select is(pg_temp.run_as('origenlab_api', 'select 1 from platform.operator_profile limit 1'), 'ok', 'api reads profiles');
-- 20260928194000: the throttle is written only by platform.record_pin_attempt (pgTAP 075).
select is(left(pg_temp.run_as('origenlab_api', 'select 1 from platform.operator_profile for update'), 5), '42501',
  'api may not lock a profile row itself: record_pin_attempt takes the lock');
select is(left(pg_temp.run_as('origenlab_api', 'update platform.operator_profile set failed_attempts = 0, locked_until = null'), 5), '42501',
  'api may not write a profile throttle column');
select is(left(pg_temp.run_as('origenlab_api', $$update platform.operator_profile set pin_hash = pin_hash$$), 5), '42501',
  'api may not write a PIN hash');
select is(left(pg_temp.run_as('origenlab_api', $$update platform.operator_profile set status = 'active'$$), 5), '42501',
  'api may not enable or disable a profile');
select is(left(pg_temp.run_as('origenlab_api', $$update platform.operator_profile set principal_id = principal_id$$), 5), '42501',
  'api may not relink a profile');
select is(left(pg_temp.run_as('origenlab_api', $$insert into platform.operator_profile (operator_id, principal_id, profile_key, pin_hash) select operator_id, principal_id, 'x' || profile_key, pin_hash from platform.operator_profile where false$$), 5), '42501',
  'api may not create a profile');
select is(left(pg_temp.run_as('origenlab_api', $$insert into platform.auth_principal (email_norm) select 'p@example.test' where false$$), 5), '42501',
  'api may not create a principal');
select is(left(pg_temp.run_as('origenlab_api', $$update platform.auth_principal set status = 'active'$$), 5), '42501',
  'api may not enable or disable a principal');
select is(left(pg_temp.run_as('origenlab_api', $$update platform.auth_principal set provider_subject = 'sub-atacante', provider_issuer = 'https://accounts.google.com'$$), 5), '42501',
  'api may not pin or re-pin a Google account: only the migrator roster tool does');
select is(left(pg_temp.run_as('origenlab_api', $$update platform.auth_principal set provider_subject = null, provider_issuer = null$$), 5), '42501',
  'api may not unpin a Google account');
-- 20260928192000: the runtime role changes no operator — not its role, status or sign-in kind,
-- and it creates none (zero-row statements still exercise the privilege check).
select is(left(pg_temp.run_as('origenlab_api', $$update platform.operator set role = 'admin' where id = '73000000-0000-4000-8000-000000000012'$$), 5), '42501',
  'api may not raise an operator''s role');
select is(left(pg_temp.run_as('origenlab_api', $$update platform.operator set status = 'active' where false$$), 5), '42501',
  'api may not enable or disable an operator');
select is(left(pg_temp.run_as('origenlab_api', $$update platform.operator set sign_in_kind = 'google_account' where false$$), 5), '42501',
  'api may not change an operator''s sign-in kind');
select is(left(pg_temp.run_as('origenlab_api', $$update platform.operator set display_name = display_name where false$$), 5), '42501',
  'api may not update any operator column');
select is(left(pg_temp.run_as('origenlab_api', $$insert into platform.operator (auth_user_id, email_norm, display_name, role, status) select gen_random_uuid(), 'nuevo@example.test', 'Nuevo', 'admin', 'active' where false$$), 5), '42501',
  'api may not create an operator');
select is(pg_temp.run_as('origenlab_api', 'select 1 from platform.operator limit 1'), 'ok', 'api still reads operators');
select is(pg_temp.run_as('origenlab_api', $$insert into platform.auth_event (event_type, principal_id, principal_email_norm) values ('profile.cleared', '73000000-0000-4000-8000-000000000001', 'compartida@example.test')$$), 'ok',
  'api appends audit events');
-- 20260928193000: an early lockout clear is recorded only by the migrator (as the owner).
set role origenlab_owner;
select lives_ok($$ insert into platform.auth_event (event_type, principal_id, principal_email_norm, operator_id)
  values ('lockout.cleared', '73000000-0000-4000-8000-000000000001', 'compartida@example.test', '73000000-0000-4000-8000-000000000012') $$,
  'the owner records a cleared lockout');
select throws_ok($$ insert into platform.auth_event (event_type) values ('lockout.cleared') $$,
  '23514', null, 'a cleared lockout names its principal');
select is(left(pg_temp.run_as('origenlab_api', $$insert into platform.auth_event (event_type, principal_id, principal_email_norm) values ('lockout.cleared', '73000000-0000-4000-8000-000000000001', 'compartida@example.test')$$), 5), '42501',
  'api may not record a cleared lockout: it cannot forge an administrator''s decision');
select is(pg_temp.run_as('origenlab_api', $$insert into platform.auth_event (event_type, principal_id, principal_email_norm) values ('profile.locked', '73000000-0000-4000-8000-000000000001', 'compartida@example.test')$$), 'ok',
  'api still records the events of the sign-in path');
select is(left(pg_temp.run_as('origenlab_worker', 'select 1 from platform.operator_profile limit 1'), 5), '42501',
  'the worker cannot read a PIN hash');
select is(left(pg_temp.run_as('origenlab_worker', 'select 1 from platform.auth_event limit 1'), 5), '42501',
  'the worker cannot read the authentication audit');

select * from finish();
rollback;
