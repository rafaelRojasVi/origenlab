-- Slice 1 — revocable profile sessions (`platform.auth_session`, #41), proven in the database.
--
-- Companion to `20260928191000_slice1_revocable_profile_sessions.sql`: the row holds a 32-byte
-- keyed hash and never an identifier, a session lives at most seven days, a revocation carries a
-- closed reason and is final, a session is never extended and never changes hands, and the runtime
-- role may read, insert and revoke — nothing else, and never delete.
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
declare
  n bigint;
begin
  execute format('set role %I', p_role);
  execute p_sql;
  get diagnostics n = row_count;
  reset role;
  return 'ok ' || n;
exception when others then
  reset role;
  return sqlstate || ': ' || sqlerrm;
end
$$;

set role origenlab_owner;
select plan(23);

insert into platform.auth_principal (id, email_norm) values
  ('74000000-0000-4000-8000-000000000001', 'sesiones@example.test');
insert into platform.operator (id, auth_user_id, display_name, role, status, sign_in_kind) values
  ('74000000-0000-4000-8000-000000000011', gen_random_uuid(), 'Perfil Sesión', 'sales', 'active', 'shared_profile'),
  ('74000000-0000-4000-8000-000000000012', gen_random_uuid(), 'Sin perfil', 'sales', 'active', 'shared_profile');
insert into platform.operator_profile (operator_id, principal_id, profile_key, pin_hash) values
  ('74000000-0000-4000-8000-000000000011', '74000000-0000-4000-8000-000000000001', 'sesion',
   '$argon2id$v=19$m=65536,t=3,p=1$c2FsdHNhbHRzYWx0c2FsdA$aGFzaGhhc2hoYXNoaGFzaGhhc2hoYXNoaGFzaGhhc2g');
insert into platform.auth_session (id, token_hash, principal_id, operator_id, expires_at) values
  ('74000000-0000-4000-8000-0000000000a1', decode(repeat('a1', 32), 'hex'), '74000000-0000-4000-8000-000000000001',
   '74000000-0000-4000-8000-000000000011', now() + interval '8 hours'),
  ('74000000-0000-4000-8000-0000000000a2', decode(repeat('a2', 32), 'hex'), '74000000-0000-4000-8000-000000000001',
   null, now() + interval '8 hours');

-- ── 1. shape ──────────────────────────────────────────────────────────────────────────────────
select has_table('platform', 'auth_session', 'platform.auth_session exists');
select col_type_is('platform', 'auth_session', 'token_hash', 'bytea', 'the row holds a hash, as bytes');
select is(
  (select count(*)::int from information_schema.columns
    where table_schema = 'platform' and table_name = 'auth_session'
      and column_name in ('sid', 'session_id', 'token', 'cookie')),
  0, 'no column could hold the cookie''s identifier or the cookie');
select throws_ok($$ insert into platform.auth_session (token_hash, principal_id, expires_at)
  values (convert_to('a-raw-session-identifier', 'UTF8'), '74000000-0000-4000-8000-000000000001', now() + interval '1 hour') $$,
  '23514', null, 'anything but a 32-byte hash is refused, so a raw identifier cannot be stored');
select throws_ok($$ insert into platform.auth_session (token_hash, principal_id, expires_at)
  values (decode(repeat('a1', 32), 'hex'), '74000000-0000-4000-8000-000000000001', now() + interval '1 hour') $$,
  '23505', null, 'one hash is one session');
select throws_ok($$ insert into platform.auth_session (token_hash, principal_id, expires_at)
  values (decode(repeat('b1', 32), 'hex'), '74000000-0000-4000-8000-000000000001', now() + interval '8 days') $$,
  '23514', null, 'a session lives at most seven days');
select throws_ok($$ insert into platform.auth_session (token_hash, principal_id, expires_at)
  values (decode(repeat('b2', 32), 'hex'), '74000000-0000-4000-8000-000000000001', now() - interval '1 second') $$,
  '23514', null, 'a session expires after it is issued');
select throws_ok($$ insert into platform.auth_session (token_hash, principal_id, operator_id, expires_at)
  values (decode(repeat('b3', 32), 'hex'), '74000000-0000-4000-8000-000000000001', '74000000-0000-4000-8000-000000000012', now() + interval '1 hour') $$,
  '23503', null, 'a session selects only an operator that has a profile');
select throws_ok($$ update platform.auth_session set revoked_at = now() where id = '74000000-0000-4000-8000-0000000000a2' $$,
  '23514', null, 'a revocation carries its reason');
select throws_ok($$ update platform.auth_session set revoked_at = now(), revoked_reason = 'porque sí' where id = '74000000-0000-4000-8000-0000000000a2' $$,
  '23514', null, 'the revocation reason is a closed vocabulary');

-- ── 2. guards ─────────────────────────────────────────────────────────────────────────────────
select throws_ok($$ update platform.auth_session set expires_at = expires_at + interval '1 minute' where id = '74000000-0000-4000-8000-0000000000a1' $$,
  'P0001', null, 'a session is never extended, whoever writes');
select lives_ok($$ update platform.auth_session set expires_at = expires_at - interval '1 minute' where id = '74000000-0000-4000-8000-0000000000a1' $$,
  'a session may be shortened');
select throws_ok($$ update platform.auth_session set operator_id = null where id = '74000000-0000-4000-8000-0000000000a1' $$,
  'P0001', null, 'a session never changes its selected operator: selecting is a new row');
select throws_ok($$ update platform.auth_session set token_hash = decode(repeat('c1', 32), 'hex') where id = '74000000-0000-4000-8000-0000000000a1' $$,
  'P0001', null, 'a session never changes its identifier');
update platform.auth_session set revoked_at = now(), revoked_reason = 'logout' where id = '74000000-0000-4000-8000-0000000000a1';
select throws_ok($$ update platform.auth_session set revoked_at = null, revoked_reason = null where id = '74000000-0000-4000-8000-0000000000a1' $$,
  'P0001', null, 'a revocation is final: a logged-out session never comes back');
select throws_ok($$ update platform.auth_session set revoked_reason = 'profile_cleared' where id = '74000000-0000-4000-8000-0000000000a1' $$,
  'P0001', null, 'a revocation is never rewritten');

-- ── 3. runtime roles ──────────────────────────────────────────────────────────────────────────
select is(pg_temp.run_as('origenlab_api', 'select 1 from platform.auth_session'), 'ok 2', 'api reads sessions');
select is(pg_temp.run_as('origenlab_api', $$insert into platform.auth_session (token_hash, principal_id, expires_at)
  values (decode(repeat('d1', 32), 'hex'), '74000000-0000-4000-8000-000000000001', now() + interval '8 hours')$$), 'ok 1',
  'api records a live session');
select is(pg_temp.run_as('origenlab_api', $$update platform.auth_session set revoked_at = now(), revoked_reason = 'logout' where id = '74000000-0000-4000-8000-0000000000a2'$$), 'ok 1',
  'api revokes a live session');
select is(left(pg_temp.run_as('origenlab_api', $$update platform.auth_session set revoked_at = null, revoked_reason = null where id = '74000000-0000-4000-8000-0000000000a1'$$), 5), 'P0001',
  'api cannot bring a revoked session back (the guard binds every writer)');
select is(left(pg_temp.run_as('origenlab_api', $$update platform.auth_session set expires_at = expires_at$$), 5), '42501',
  'api may not change an expiry');
select is(left(pg_temp.run_as('origenlab_api', 'delete from platform.auth_session'), 5), '42501',
  'api deletes no session: expired rows are pruned by the migrator');
select is(left(pg_temp.run_as('origenlab_worker', 'select 1 from platform.auth_session'), 5), '42501',
  'the worker cannot read sessions');

select * from finish();
rollback;
