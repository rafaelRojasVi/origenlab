-- Slice 1 — the PIN attempt functions, `platform.begin_pin_attempt` and
-- `platform.finish_pin_attempt`, proven in the database against every line of ARCHITECTURE.md
-- §6.2 that a catalogue can show, with the interfaces they replaced proven gone.
--
-- Companion to `20260929100000_slice1_pin_attempt_begin_finish.sql`. The runtime API role has no
-- UPDATE on `platform.auth_principal` or `platform.operator_profile`, no UPDATE policy, cannot
-- read `operator_profile.pin_hash`, and cannot insert a PIN outcome event. Both functions are
-- SECURITY DEFINER, owned by origenlab_owner, pin search_path = pg_catalog, qualify every
-- relation, have no dynamic SQL, take no verdict, counter or timestamp, are executable by
-- origenlab_api alone, and refuse any session_user but origenlab_api. The HMAC helper matches
-- RFC 4231.
--
-- The attempts themselves (begin/finish in one transaction, one use, the pairing, the proof, the
-- threshold under concurrency, failure injection) need the real `origenlab_api` login as
-- session_user, which pgTAP's single login cannot be: they are proven through direct runtime-login
-- connections in apps/api/tests/test_v2_pin_attempt_boundary.py and
-- supabase/scripts/verify_direct_logins.sh.
--
-- SQLSTATE 42501 privilege, 23514 check. Every value is fictitious.
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
select plan(50);

create temp table definer (fn regprocedure);
insert into definer values
  ('platform.begin_pin_attempt(uuid,uuid)'::regprocedure),
  ('platform.finish_pin_attempt(uuid,uuid,uuid,uuid,bytea)'::regprocedure);

insert into platform.auth_principal (id, email_norm) values
  ('75000000-0000-4000-8000-000000000001', 'limite@example.test');
insert into platform.operator (id, auth_user_id, display_name, role, status, sign_in_kind) values
  ('75000000-0000-4000-8000-000000000011', gen_random_uuid(), 'Perfil', 'sales', 'active', 'shared_profile');
insert into platform.operator_profile (operator_id, principal_id, profile_key, pin_hash) values
  ('75000000-0000-4000-8000-000000000011', '75000000-0000-4000-8000-000000000001', 'perfil',
   '$argon2id$v=19$m=19456,t=2,p=1$c2FsdHNhbHRzYWx0c2FsdA$aGFzaGhhc2hoYXNoaGFzaGhhc2hoYXNoaGFzaGhhc2g');

-- ── 1. the replaced interface is gone ──────────────────────────────────────────────────────────
select hasnt_function('platform', 'record_pin_attempt', array['text', 'uuid', 'uuid'],
  'platform.record_pin_attempt (the caller-declared success) no longer exists');
select is(
  (select count(*)::int from pg_proc p join pg_namespace n on n.oid = p.pronamespace
    where n.nspname = 'platform' and p.proname = 'record_pin_attempt'),
  0, 'no overload of record_pin_attempt survives');

-- ── 2. the two functions' catalogue entries ────────────────────────────────────────────────────
select is((select pg_get_function_arguments('platform.begin_pin_attempt(uuid,uuid)'::regprocedure)),
  'p_principal_id uuid, p_operator_id uuid, OUT attempt_id uuid, OUT refused boolean, OUT memory_kib integer, OUT iterations integer, OUT lanes integer, OUT salt text, OUT hash_length integer, OUT nonce bytea',
  'begin takes a principal and a profile, and returns the refusal, public Argon2id parameters, salt and a one-use attempt — never a verifier');
select is((select pg_get_function_arguments('platform.finish_pin_attempt(uuid,uuid,uuid,uuid,bytea)'::regprocedure)),
  'p_attempt_id uuid, p_principal_id uuid, p_operator_id uuid, p_previous_operator_id uuid, p_candidate_proof bytea, OUT selected boolean, OUT reason text',
  'finish takes the attempt, the pair, the previous profile and a proof — no verdict, counter, lock or timestamp');
select is((select count(*)::int from definer d join pg_proc p on p.oid = d.fn where p.prosecdef), 2,
  'both are SECURITY DEFINER (the closed list, ARCHITECTURE.md §6.2)');
select is((select count(*)::int from definer d join pg_proc p on p.oid = d.fn where p.proowner::regrole::text = 'origenlab_owner'), 2,
  'both are owned by the NOLOGIN owner');
select is((select count(*)::int from definer d join pg_proc p on p.oid = d.fn where p.proconfig = array['search_path=pg_catalog']), 2,
  'both pin search_path = pg_catalog and nothing else');
select is((select count(*)::int from definer d where has_function_privilege('origenlab_api', d.fn, 'EXECUTE')), 2,
  'origenlab_api may EXECUTE both');
select is(
  (select count(*)::int from definer d cross join (values ('origenlab_worker'), ('origenlab_migrator'), ('anon'), ('authenticated'), ('service_role')) r(n)
    where has_function_privilege(r.n, d.fn, 'EXECUTE')),
  0, 'the worker, the migrator, anon, authenticated and service_role may EXECUTE neither');
select is(
  (select count(*)::int from definer d join pg_proc p on p.oid = d.fn, aclexplode(p.proacl) a where a.grantee = 0),
  0, 'PUBLIC holds no EXECUTE on either');
select is(
  (select array_agg(distinct a.grantee::regrole::text) from definer d join pg_proc p on p.oid = d.fn, aclexplode(p.proacl) a
    where a.privilege_type = 'EXECUTE' and a.grantee <> p.proowner),
  array['origenlab_api'], 'EXECUTE is granted to origenlab_api and to no one else');
select is(
  (select count(*)::int from definer d join pg_proc p on p.oid = d.fn
    where p.prosrc ~* '\mexecute\M' or p.prosrc ~* '\mformat\s*\(' or p.prosrc ~* 'quote_(ident|literal|nullable)'),
  0, 'neither contains dynamic SQL');
select is(
  (select array_agg(distinct m[2] order by m[2]) from definer d join pg_proc p on p.oid = d.fn,
          regexp_matches(p.prosrc, '\m(from|join|into|update)\s+([a-z_]+)(\s|\.|$)', 'gi') m
    where m[3] <> '.' and m[2] !~ '^[vp]_'),
  null, 'every relation either reads or writes is schema-qualified');
select is((select count(*)::int from definer d join pg_proc p on p.oid = d.fn where p.prosrc ~ 'session_user <> ''origenlab_api'''), 2,
  'both assert session_user, never current_user');
select is((select count(*)::int from definer d join pg_proc p on p.oid = d.fn where p.prosrc ~* '\mpin_hash\M' and p.proname = 'finish_pin_attempt'
           and p.prosrc ~ 'pg_catalog\.sha256\(platform\.hmac_sha256\(' and p.prosrc ~ '= pg_catalog\.sha256\(p_candidate_proof\)'), 1,
  'finish compares SHA-256 digests of the recomputed and the candidate proof, never the raw values');

-- ── 3. the HMAC helper (RFC 2104) ──────────────────────────────────────────────────────────────
select is(encode(platform.hmac_sha256(decode(repeat('0b', 20), 'hex'), convert_to('Hi There', 'UTF8')), 'hex'),
  'b0344c61d8db38535ca8afceaf0bf12b881dc200c9833da726e9376c2e32cff7', 'RFC 4231 test case 1');
select is(encode(platform.hmac_sha256(convert_to('Jefe', 'UTF8'), convert_to('what do ya want for nothing?', 'UTF8')), 'hex'),
  '5bdcc146bf60754e6a042426089575c75a003f089d2739839dec58b964ec3843', 'RFC 4231 test case 2');
select is(encode(platform.hmac_sha256(decode(repeat('aa', 131), 'hex'),
    convert_to('Test Using Larger Than Block-Size Key - Hash Key First', 'UTF8')), 'hex'),
  '60e431591ee0b67f0d8a26aacbf5b77f8e0bc6213728c5140546040f0ee37f54', 'RFC 4231 test case 6 (a key longer than a block)');
select is((select prosecdef or not proisstrict or provolatile <> 'i' from pg_proc where oid = 'platform.hmac_sha256(bytea,bytea)'::regprocedure),
  false, 'the helper is SECURITY INVOKER, STRICT and IMMUTABLE');
select is(
  (select count(*)::int from (values ('origenlab_api'), ('origenlab_worker'), ('origenlab_migrator'), ('anon'), ('authenticated'), ('service_role')) r(n)
    where has_function_privilege(r.n, 'platform.hmac_sha256(bytea,bytea)', 'EXECUTE')),
  0, 'no role but its owner may EXECUTE the helper');

-- ── 4. the calling login is asserted ───────────────────────────────────────────────────────────
-- This login holds EXECUTE only through `set role origenlab_api`; session_user stays itself.
select is(left(pg_temp.run_as('origenlab_api',
  $$select * from platform.begin_pin_attempt('75000000-0000-4000-8000-000000000001', null)$$), 5), '42501',
  'under set role origenlab_api begin refuses: session_user is not the runtime login');
select is(left(pg_temp.run_as('origenlab_api',
  $$select * from platform.finish_pin_attempt(gen_random_uuid(), '75000000-0000-4000-8000-000000000001', null, null, null)$$), 5), '42501',
  'under set role origenlab_api finish refuses too');
select is(left(pg_temp.run_as('origenlab_worker',
  $$select * from platform.begin_pin_attempt('75000000-0000-4000-8000-000000000001', null)$$), 5), '42501',
  'the worker is refused EXECUTE on begin');
select is(left(pg_temp.run_as('origenlab_worker',
  $$select * from platform.finish_pin_attempt(gen_random_uuid(), '75000000-0000-4000-8000-000000000001', null, null, null)$$), 5), '42501',
  'the worker is refused EXECUTE on finish');

-- ── 5. the direct write surface is gone ────────────────────────────────────────────────────────
select is(
  (select count(*)::int from information_schema.column_privileges
    where table_schema = 'platform' and table_name in ('auth_principal', 'operator_profile')
      and grantee = 'origenlab_api' and privilege_type = 'UPDATE'),
  0, 'origenlab_api holds UPDATE on no column of auth_principal or operator_profile');
select is(
  (select count(*)::int from pg_policies
    where schemaname = 'platform' and tablename in ('auth_principal', 'operator_profile') and cmd = 'UPDATE'),
  0, 'no UPDATE policy remains on auth_principal or operator_profile');
select is(left(pg_temp.run_as('origenlab_api', 'update platform.auth_principal set failed_attempts = 0'), 5), '42501',
  'api may not reset a principal''s failure count');
select is(left(pg_temp.run_as('origenlab_api', 'update platform.auth_principal set lockout_count = 0'), 5), '42501',
  'api may not reset a principal''s lockout history');
select is(left(pg_temp.run_as('origenlab_api', 'update platform.auth_principal set locked_until = null'), 5), '42501',
  'api may not lift a principal-wide lock');
select is(left(pg_temp.run_as('origenlab_api', 'update platform.auth_principal set last_failed_at = null'), 5), '42501',
  'api may not rewrite a principal''s failure time');
select is(left(pg_temp.run_as('origenlab_api', 'update platform.auth_principal set pin_attempt_id = null, pin_attempt_xact = null, pin_attempt_nonce = null'), 5), '42501',
  'api may not forge, clear or replay an attempt record');
select is(left(pg_temp.run_as('origenlab_api', 'update platform.operator_profile set lockout_count = 0'), 5), '42501',
  'api may not reset a profile''s lockout history');
select is(left(pg_temp.run_as('origenlab_api', 'update platform.operator_profile set last_failed_at = null'), 5), '42501',
  'api may not rewrite a profile''s failure time');
select is(left(pg_temp.run_as('origenlab_api', 'select 1 from platform.auth_principal for update'), 5), '42501',
  'api may not lock a principal row itself: begin_pin_attempt takes the lock');
select is(pg_temp.run_as('origenlab_api', 'select 1 from platform.auth_principal limit 1'), 'ok',
  'api still reads principals');

-- ── 6. the stored verifier is unreadable ───────────────────────────────────────────────────────
select is(left(pg_temp.run_as('origenlab_api', 'select * from platform.operator_profile'), 5), '42501',
  'api may not select * from operator_profile: it would include pin_hash');
select is(left(pg_temp.run_as('origenlab_api', 'select pin_hash from platform.operator_profile'), 5), '42501',
  'api may not select pin_hash');
select is(left(pg_temp.run_as('origenlab_api', 'select p from platform.operator_profile p'), 5), '42501',
  'api may not select the whole row as a value');
select is(pg_temp.run_as('origenlab_api', 'select operator_id, principal_id, status, version, locked_until from platform.operator_profile'), 'ok',
  'api still reads every other profile column it needs');

-- ── 7. the PIN outcome events are the function's alone ─────────────────────────────────────────
select is(
  (select count(*)::int from (values ('profile.selected', null), ('profile.selection_refused', 'pin_mismatch'),
                                     ('profile.locked', 'pin_mismatch'), ('principal.locked', 'pin_mismatch')) e(t, r)
    where left(pg_temp.run_as('origenlab_api', format(
      $$insert into platform.auth_event (event_type, principal_id, principal_email_norm, operator_id, refusal_reason)
        values (%L, '75000000-0000-4000-8000-000000000001', 'limite@example.test', '75000000-0000-4000-8000-000000000011', %L)$$, e.t, e.r)), 5) = '42501'),
  4, 'api may insert none of the four PIN outcome events: success, refusal, profile lockout, account lockout');
select is(pg_temp.run_as('origenlab_api', $$insert into platform.auth_event (event_type, principal_id, principal_email_norm)
  values ('profile.cleared', '75000000-0000-4000-8000-000000000001', 'limite@example.test')$$), 'ok',
  'api still records a cleared profile');
select is(pg_temp.run_as('origenlab_api', $$insert into platform.auth_event (event_type, principal_id, principal_email_norm)
  values ('session.logout', '75000000-0000-4000-8000-000000000001', 'limite@example.test')$$), 'ok',
  'api still records a logout');
set role origenlab_owner;
select lives_ok($$ insert into platform.auth_event (event_type, principal_id, principal_email_norm, refusal_reason)
  values ('principal.locked', '75000000-0000-4000-8000-000000000001', 'limite@example.test', 'unknown_profile') $$,
  'an account lockout (as the function writes it) carries the principal and why the attempt failed');
select throws_ok($$ insert into platform.auth_event (event_type, principal_id, principal_email_norm)
  values ('principal.locked', '75000000-0000-4000-8000-000000000001', 'limite@example.test') $$,
  '23514', null, 'a lockout without the failure that caused it is refused');
select throws_ok($$ insert into platform.auth_event (event_type, principal_id, principal_email_norm, refusal_reason)
  values ('profile.locked', '75000000-0000-4000-8000-000000000001', 'limite@example.test', 'pin_mismatch') $$,
  '23514', null, 'a profile lockout names its profile');
select throws_ok($$ insert into platform.auth_event (event_type, principal_id, principal_email_norm, refusal_reason)
  values ('profile.selected', '75000000-0000-4000-8000-000000000001', 'limite@example.test', 'pin_mismatch') $$,
  '23514', null, 'a success carries no refusal reason');

-- ── 8. the attempt record's shape ──────────────────────────────────────────────────────────────
select throws_ok($$ update platform.auth_principal set pin_attempt_id = gen_random_uuid()
  where id = '75000000-0000-4000-8000-000000000001' $$,
  '23514', null, 'an attempt is recorded with its transaction and nonce, or not at all');
select throws_ok($$ update platform.auth_principal set pin_attempt_id = gen_random_uuid(), pin_attempt_xact = pg_current_xact_id(),
  pin_attempt_nonce = '\x00'::bytea where id = '75000000-0000-4000-8000-000000000001' $$,
  '23514', null, 'the nonce is 32 bytes');
select lives_ok($$ update platform.auth_principal set pin_attempt_id = gen_random_uuid(), pin_attempt_xact = pg_current_xact_id(),
  pin_attempt_nonce = sha256('\x00'::bytea) where id = '75000000-0000-4000-8000-000000000001' $$,
  'a complete attempt record is accepted');
select is((select version from platform.auth_principal where id = '75000000-0000-4000-8000-000000000001'), 1,
  'an attempt record never bumps the principal version: beginning an attempt signs no one out');

select * from finish();
rollback;
