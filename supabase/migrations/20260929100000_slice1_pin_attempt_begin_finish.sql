-- Slice 1 — a PIN attempt is decided inside the database: begin, then finish with a bound proof.
--
-- docs/ARCHITECTURE.md §5.1, §6.2; docs/DOMAIN.md §7.3 (#38–#40); apps/api/docs/PRODUCTION_AUTH.md.
--
-- `20260928194000` moved the throttle arithmetic into `platform.record_pin_attempt`, but left the
-- *verdict* with the caller: `record_success` took the caller's word that the PIN was right. The
-- real `origenlab_api` login could therefore clear both failure counters with no PIN at all
-- (200 failures in 50 × (4 wrong + 1 claimed success), never locked, and no audit row, because the
-- audit was a separate INSERT the caller could simply leave out). The runtime role could also
-- SELECT `operator_profile.pin_hash`, the stored verifier itself.
--
-- This migration removes that interface and replaces it with two functions whose caller supplies
-- only *what was typed, transformed*, never *what it came to*:
--
--   1. `platform.begin_pin_attempt(principal_id, operator_id)` locks the principal row, then the
--      profile row (the one lock order), and returns whether the attempt is refused (a lock is
--      running), the public Argon2id parameters and salt to derive with, and an opaque attempt:
--      a random id and a random 32-byte nonce, recorded on the principal row together with the
--      current transaction id. For an unknown profile, another principal's profile or an unusable
--      (disabled, or role outside the dashboard) one it returns a **decoy**: a salt derived from
--      the two ids, at the profile's own parameters when it exists and at those of the
--      principal's first profile otherwise — so the API spends the same Argon2id cost and the
--      HTTP answer cannot tell the cases apart. It never returns the stored verifier.
--   2. `platform.finish_pin_attempt(attempt_id, principal_id, operator_id, previous_operator_id,
--      candidate_proof)` must run in the **same transaction** as its begin (the transaction id is
--      compared) and consumes the attempt, so it runs once. The caller derives
--      `K = Argon2id(PIN, salt, pepper)` and sends `HMAC-SHA256(K, "origenlab.pin-proof.v1" ‖
--      attempt_id ‖ principal_id ‖ operator_id ‖ nonce)`; the function recomputes it from the
--      stored verifier and compares SHA-256 digests of the two, never the raw values. It then
--      computes the verdict, the counters, the lock and its duration from the locked rows and
--      the clock, writes the throttle transition and **exactly one** `platform.auth_event` —
--      `profile.selected`, `profile.selection_refused`, `profile.locked` or `principal.locked` —
--      in the one statement, so both land or neither does. A missing or wrong-length proof is
--      `malformed_pin`: a failed attempt, counted like any other. Nothing stores or logs the proof.
--
-- Why a nonce. `K` is fixed for a given PIN and salt: sent raw, a copy of it (a server log with
-- bind parameters, a statement capture, a network tap inside the TLS terminator) would succeed
-- for ever, without the PIN or the pepper. Bound to a database-generated one-use nonce, attempt
-- and pair, a captured proof is worth exactly one attempt that has already ended. Argon2id with
-- the pepper stays the only way to produce `K`: nothing here weakens or replaces it.
--
-- What this does not protect against. A caller holding the pepper — that is, a compromised API
-- host — can derive `K` for any guess and roll a failed attempt back instead of committing it:
-- the throttle bounds only callers who commit. The pepper and the session secret are inside the
-- API's trust boundary (apps/api/docs/PRODUCTION_AUTH.md); this migration closes the SQL-only
-- attacker — an injection, a leaked runtime password, a bug — who holds neither.
--
-- Audit. The four PIN outcome events are recorded **only** by `finish_pin_attempt`:
-- `auth_event_actor_guard` now refuses them, like `lockout.cleared`, from any role that is not a
-- member of `origenlab_owner`, so a direct INSERT by the runtime role cannot forge a selection, a
-- refusal or a lockout. The runtime role keeps its INSERT for `profile.cleared` and
-- `session.logout`, the two events the API still records itself. A lock-starting failure is one
-- event now (`profile.locked` or `principal.locked`, carrying the refusal reason) instead of a
-- refusal plus a lock; when an attempt locks the profile and the principal at once the event is
-- `principal.locked` with the profile's operator (the principal lock covers every profile).
--
-- Privileges. `operator_profile` is readable by the runtime role column by column, every column
-- but `pin_hash`: a column REVOKE does not override a table-level SELECT, so the table-level
-- grant goes and the safe columns are granted one by one. The runtime role still has no UPDATE
-- on either throttle table. Both functions follow ARCHITECTURE.md §6.2 line by line (private
-- schema, owner `origenlab_owner`, `search_path = pg_catalog`, every object schema-qualified, no
-- dynamic SQL, EXECUTE revoked from PUBLIC and granted to `origenlab_api` alone, `session_user`
-- asserted). `platform.hmac_sha256` is a plain INVOKER helper with no EXECUTE for anyone but
-- its owner.

set role origenlab_owner;

-- ── 1. the vulnerable interface goes ──────────────────────────────────────────────────────────
drop function platform.record_pin_attempt(text, uuid, uuid);

-- Asserted again, although 20260928194000 already revoked them: no direct throttle write.
revoke update on platform.auth_principal from origenlab_api;
revoke update on platform.operator_profile from origenlab_api;

-- The stored verifier: a table-level SELECT would still cover it, so it goes, and every other
-- column is granted by name.
revoke select on platform.operator_profile from origenlab_api;
grant select (operator_id, operator_sign_in_kind, principal_id, profile_key, pin_set_at, status,
              sort_order, version, failed_attempts, lockout_count, locked_until, last_failed_at,
              created_at, updated_at)
  on platform.operator_profile to origenlab_api;

-- ── 2. the attempt in flight, on the principal row it serializes on ────────────────────────────
-- At most one per principal: begin holds the principal row `for update` until the transaction
-- ends. The id and nonce are not secrets (the caller receives them), only one-use: a finish must
-- come from the transaction recorded here, and clears all four.
alter table platform.auth_principal
  add column pin_attempt_id uuid,
  add column pin_attempt_xact xid8,
  add column pin_attempt_operator_id uuid,
  add column pin_attempt_nonce bytea;

alter table platform.auth_principal
  add constraint auth_principal_pin_attempt_shape check (
    (pin_attempt_id is null) = (pin_attempt_xact is null)
    and (pin_attempt_id is null) = (pin_attempt_nonce is null)
    and (pin_attempt_id is not null or pin_attempt_operator_id is null)
    and (pin_attempt_nonce is null or octet_length(pin_attempt_nonce) = 32)
  );

comment on column platform.auth_principal.pin_attempt_id is
  'The PIN attempt begun by platform.begin_pin_attempt and not yet finished; valid only inside the transaction pin_attempt_xact. Written only by the two attempt functions.';
comment on column platform.auth_principal.pin_attempt_nonce is
  'One-use random nonce the attempt''s proof is bound to. Not a secret; never reusable.';

-- ── 3. the audit vocabulary: one event per attempt ─────────────────────────────────────────────
alter table platform.auth_event
  drop constraint auth_event_type_check,
  drop constraint auth_event_refusal_shape,
  drop constraint auth_event_subject_shape;

alter table platform.auth_event
  add constraint auth_event_type_check check (event_type in (
    'profile.selected', 'profile.selection_refused', 'profile.locked', 'principal.locked',
    'profile.cleared', 'session.logout', 'lockout.cleared'
  )),
  -- A refused attempt, and a failure that started a lock, carry why it failed.
  add constraint auth_event_refusal_shape check (
    (event_type in ('profile.selection_refused', 'profile.locked', 'principal.locked'))
      = (refusal_reason is not null)
  ),
  add constraint auth_event_subject_shape check (
    case event_type
      when 'profile.selected' then principal_id is not null and operator_id is not null
      when 'profile.selection_refused' then principal_id is not null
      when 'profile.locked' then principal_id is not null and operator_id is not null
      when 'principal.locked' then principal_id is not null
      when 'profile.cleared' then principal_id is not null and operator_id is null
      when 'session.logout' then principal_id is not null or operator_id is not null
      when 'lockout.cleared' then principal_id is not null and previous_operator_id is null
    end
  );

comment on table platform.auth_event is
  'DOMAIN.md §7 #40 — append-only authentication audit. PIN outcomes (profile.selected, profile.selection_refused, profile.locked, principal.locked) are written only by platform.finish_pin_attempt, one per attempt; lockout.cleared only by the migrator; the API writes profile.cleared and session.logout. No column can hold a PIN or a proof.';

create or replace function platform.auth_event_actor_guard() returns trigger
language plpgsql
set search_path = pg_catalog
as $$
begin
  if new.event_type in ('profile.selected', 'profile.selection_refused', 'profile.locked',
                        'principal.locked', 'lockout.cleared')
     and not pg_catalog.pg_has_role(current_user, 'origenlab_owner', 'MEMBER') then
    raise exception 'platform.auth_event: % is recorded only by its own function or tool', new.event_type
      using errcode = '42501';
  end if;
  return new;
end
$$;

comment on function platform.auth_event_actor_guard() is
  'Trigger: the PIN outcome events are accepted only from platform.finish_pin_attempt (running as origenlab_owner) and lockout.cleared only from the migrator tool; the runtime API role may record profile.cleared and session.logout only. SECURITY INVOKER.';

revoke all on function platform.auth_event_actor_guard() from public, anon, authenticated, service_role;

-- ── 4. HMAC-SHA256 (RFC 2104) over the built-in sha256 ────────────────────────────────────────
create function platform.hmac_sha256(p_key bytea, p_message bytea) returns bytea
language plpgsql
immutable
strict
set search_path = pg_catalog
as $$
declare
  v_key bytea := p_key;
  v_inner bytea;
  v_outer bytea;
begin
  if pg_catalog.octet_length(v_key) > 64 then
    v_key := pg_catalog.sha256(v_key);
  end if;
  v_key := v_key || pg_catalog.decode(pg_catalog.repeat('00', 64 - pg_catalog.octet_length(v_key)), 'hex');
  v_inner := v_key;
  v_outer := v_key;
  for i in 0..63 loop
    v_inner := pg_catalog.set_byte(v_inner, i, pg_catalog.get_byte(v_key, i) # 54);
    v_outer := pg_catalog.set_byte(v_outer, i, pg_catalog.get_byte(v_key, i) # 92);
  end loop;
  return pg_catalog.sha256(v_outer || pg_catalog.sha256(v_inner || p_message));
end
$$;

comment on function platform.hmac_sha256(bytea, bytea) is
  'HMAC-SHA256 (RFC 2104) on pg_catalog.sha256, for the PIN proof in platform.finish_pin_attempt. SECURITY INVOKER; no runtime role may EXECUTE it.';

revoke all on function platform.hmac_sha256(bytea, bytea)
  from public, anon, authenticated, service_role, origenlab_api, origenlab_worker, origenlab_migrator;

-- ── 5. begin ───────────────────────────────────────────────────────────────────────────────────
create function platform.begin_pin_attempt(
  p_principal_id uuid,
  p_operator_id uuid,
  out attempt_id uuid,
  out refused boolean,
  out memory_kib integer,
  out iterations integer,
  out lanes integer,
  out salt text,
  out hash_length integer,
  out nonce bytea
)
language plpgsql
security definer
set search_path = pg_catalog
as $$
declare
  v_now timestamptz := pg_catalog.clock_timestamp();
  v_p_locked_until timestamptz;
  v_has_profile boolean := false;
  v_hash text;
  v_status text;
  v_r_locked_until timestamptz;
  v_operator_status text;
  v_role text;
  v_usable boolean := false;
  v_phc text[];
begin
  if session_user <> 'origenlab_api' then
    raise exception 'platform.begin_pin_attempt: refused for login %', session_user using errcode = '42501';
  end if;
  if p_principal_id is null then
    raise exception 'platform.begin_pin_attempt: a principal is required' using errcode = '22004';
  end if;

  -- Principal first, then the profile: the one lock order, so two attempts never deadlock.
  select a.locked_until
    into v_p_locked_until
    from platform.auth_principal a
   where a.id = p_principal_id
     for update;
  if not found then
    raise exception 'platform.begin_pin_attempt: no such principal' using errcode = 'P0002';
  end if;

  if p_operator_id is not null then
    select p.pin_hash, p.status, p.locked_until
      into v_hash, v_status, v_r_locked_until
      from platform.operator_profile p
     where p.operator_id = p_operator_id and p.principal_id = p_principal_id
       for update;
    v_has_profile := found;
  end if;
  if v_has_profile then
    select o.status, o.role
      into v_operator_status, v_role
      from platform.operator o
     where o.id = p_operator_id;
    v_usable := v_status = 'active' and v_operator_status = 'active'
                and v_role in ('viewer', 'sales', 'admin');
  else
    -- The cost reference for a decoy: the principal's first profile, whatever its state.
    select p.pin_hash
      into v_hash
      from platform.operator_profile p
     where p.principal_id = p_principal_id
     order by p.sort_order, p.operator_id
     limit 1;
  end if;

  refused := (v_p_locked_until is not null and v_p_locked_until > v_now)
             or (v_has_profile and v_r_locked_until is not null and v_r_locked_until > v_now);

  v_phc := pg_catalog.regexp_match(v_hash,
    '^\$argon2id\$v=19\$m=([0-9]{1,8}),t=([0-9]{1,3}),p=([0-9]{1,3})\$([A-Za-z0-9+/]+)\$([A-Za-z0-9+/]+)$');
  if v_phc is null then
    -- No profile at all under this principal: the API's production parameters (profile_pin.py).
    v_phc := array['65536', '3', '4', null, 'AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA'];
  end if;
  memory_kib := v_phc[1]::integer;
  iterations := v_phc[2]::integer;
  lanes := v_phc[3]::integer;
  hash_length := (pg_catalog.length(v_phc[5]) * 3) / 4;
  if v_usable and not refused then
    salt := v_phc[4];
  else
    salt := pg_catalog.rtrim(pg_catalog.encode(pg_catalog.substr(pg_catalog.sha256(
              pg_catalog.convert_to('origenlab.pin-decoy.v1', 'UTF8')
              || pg_catalog.uuid_send(p_principal_id)
              || coalesce(pg_catalog.uuid_send(p_operator_id), '\x00000000000000000000000000000000'::bytea)
            ), 1, 16), 'base64'), '=');
  end if;

  attempt_id := pg_catalog.gen_random_uuid();
  nonce := pg_catalog.sha256(pg_catalog.uuid_send(pg_catalog.gen_random_uuid())
                             || pg_catalog.uuid_send(pg_catalog.gen_random_uuid()));
  update platform.auth_principal
     set pin_attempt_id = attempt_id,
         pin_attempt_xact = pg_catalog.pg_current_xact_id(),
         pin_attempt_operator_id = p_operator_id,
         pin_attempt_nonce = nonce
   where id = p_principal_id;
end
$$;

comment on function platform.begin_pin_attempt(uuid, uuid) is
  'ARCHITECTURE.md §6.2 closed list — begins one PIN attempt: locks the principal then the profile row, records a one-use attempt (id, nonce, transaction) on the principal, and returns whether a lock refuses it, the public Argon2id parameters and salt (a decoy for an unknown, foreign or unusable profile), and the attempt. Never returns the stored verifier. session_user must be origenlab_api. SECURITY DEFINER, search_path = pg_catalog, no dynamic SQL.';

revoke all on function platform.begin_pin_attempt(uuid, uuid)
  from public, anon, authenticated, service_role, origenlab_worker, origenlab_migrator;
grant execute on function platform.begin_pin_attempt(uuid, uuid) to origenlab_api;

-- ── 6. finish ──────────────────────────────────────────────────────────────────────────────────
create function platform.finish_pin_attempt(
  p_attempt_id uuid,
  p_principal_id uuid,
  p_operator_id uuid,
  p_previous_operator_id uuid,
  p_candidate_proof bytea,
  out selected boolean,
  out reason text
)
language plpgsql
security definer
set search_path = pg_catalog
as $$
declare
  v_now timestamptz := pg_catalog.clock_timestamp();
  v_email text;
  v_p_failed integer;
  v_p_lockouts integer;
  v_p_locked_until timestamptz;
  v_p_last timestamptz;
  v_attempt_id uuid;
  v_attempt_xact xid8;
  v_attempt_operator uuid;
  v_nonce bytea;
  v_has_profile boolean := false;
  v_hash text;
  v_status text;
  v_r_failed integer;
  v_r_lockouts integer;
  v_r_locked_until timestamptz;
  v_r_last timestamptz;
  v_operator_status text;
  v_role text;
  v_usable boolean := false;
  v_locked boolean;
  v_stored bytea;
  v_encoded text;
  v_failed integer;
  v_lockouts integer;
  v_principal_lock_started boolean := false;
  v_profile_lock_started boolean := false;
  v_event text;
begin
  if session_user <> 'origenlab_api' then
    raise exception 'platform.finish_pin_attempt: refused for login %', session_user using errcode = '42501';
  end if;
  if p_attempt_id is null or p_principal_id is null then
    raise exception 'platform.finish_pin_attempt: an attempt and a principal are required' using errcode = '22004';
  end if;

  select a.email_norm, a.failed_attempts, a.lockout_count, a.locked_until, a.last_failed_at,
         a.pin_attempt_id, a.pin_attempt_xact, a.pin_attempt_operator_id, a.pin_attempt_nonce
    into v_email, v_p_failed, v_p_lockouts, v_p_locked_until, v_p_last,
         v_attempt_id, v_attempt_xact, v_attempt_operator, v_nonce
    from platform.auth_principal a
   where a.id = p_principal_id
     for update;
  -- Begun in this very transaction, for this principal and this profile, and not yet finished.
  if not found
     or v_attempt_id is distinct from p_attempt_id
     or v_attempt_xact is distinct from pg_catalog.pg_current_xact_id()
     or v_attempt_operator is distinct from p_operator_id then
    raise exception 'platform.finish_pin_attempt: no such attempt in this transaction' using errcode = 'P0002';
  end if;
  if p_previous_operator_id is not null and not exists (
       select 1 from platform.operator_profile q
        where q.operator_id = p_previous_operator_id and q.principal_id = p_principal_id) then
    raise exception 'platform.finish_pin_attempt: the previous operator is not a profile of this principal'
      using errcode = '22023';
  end if;

  -- Consumed: whatever follows, this attempt cannot finish twice.
  update platform.auth_principal
     set pin_attempt_id = null, pin_attempt_xact = null, pin_attempt_operator_id = null,
         pin_attempt_nonce = null
   where id = p_principal_id;

  if p_operator_id is not null then
    select p.pin_hash, p.status, p.failed_attempts, p.lockout_count, p.locked_until, p.last_failed_at
      into v_hash, v_status, v_r_failed, v_r_lockouts, v_r_locked_until, v_r_last
      from platform.operator_profile p
     where p.operator_id = p_operator_id and p.principal_id = p_principal_id
       for update;
    v_has_profile := found;
  end if;
  if v_has_profile then
    select o.status, o.role
      into v_operator_status, v_role
      from platform.operator o
     where o.id = p_operator_id;
    v_usable := v_status = 'active' and v_operator_status = 'active'
                and v_role in ('viewer', 'sales', 'admin');
  end if;

  v_locked := (v_p_locked_until is not null and v_p_locked_until > v_now)
              or (v_has_profile and v_r_locked_until is not null and v_r_locked_until > v_now);

  selected := false;
  if v_locked then
    reason := 'locked';
  elsif not v_has_profile then
    reason := 'unknown_profile';
  elsif p_candidate_proof is null or pg_catalog.octet_length(p_candidate_proof) <> 32 then
    reason := 'malformed_pin';
  elsif not v_usable then
    reason := 'profile_inactive';
  else
    v_encoded := pg_catalog.split_part(v_hash, '$', 6);
    begin
      v_stored := pg_catalog.decode(
        v_encoded || pg_catalog.repeat('=', (4 - pg_catalog.length(v_encoded) % 4) % 4), 'base64');
    exception when others then
      v_stored := null;  -- an unusable stored hash verifies nothing; its text is never reported
    end;
    if v_stored is not null then
      -- Digests of the two proofs are compared, never the proofs themselves.
      selected := pg_catalog.sha256(platform.hmac_sha256(v_stored,
                    pg_catalog.convert_to('origenlab.pin-proof.v1', 'UTF8')
                    || pg_catalog.uuid_send(p_attempt_id)
                    || pg_catalog.uuid_send(p_principal_id)
                    || pg_catalog.uuid_send(p_operator_id)
                    || v_nonce))
                  = pg_catalog.sha256(p_candidate_proof);
    end if;
    if not selected then
      reason := 'pin_mismatch';
    end if;
  end if;

  if selected then
    -- The principal's failure count restarts; its lockout history is kept (it still decays).
    -- Another profile's throttle is never touched: one known PIN helps guess no other.
    update platform.auth_principal
       set failed_attempts = 0, locked_until = null
     where id = p_principal_id;
    update platform.operator_profile
       set failed_attempts = 0, lockout_count = 0, locked_until = null, last_failed_at = null
     where operator_id = p_operator_id;
    insert into platform.auth_event
      (occurred_at, event_type, principal_id, principal_email_norm, operator_id, previous_operator_id)
    values (v_now, 'profile.selected', p_principal_id, v_email, p_operator_id, p_previous_operator_id);
    return;
  end if;

  -- A refusal. While locked it counts nothing (guessing never extends a lock).
  if not v_locked then
    v_failed := case when v_p_last is not null and v_now - v_p_last < interval '1 hour'
                     then v_p_failed else 0 end + 1;
    v_lockouts := case when v_p_last is not null and v_now - v_p_last < interval '24 hours'
                       then v_p_lockouts else 0 end;
    if v_failed >= 10 then
      update platform.auth_principal
         set failed_attempts = 0, lockout_count = v_lockouts + 1, last_failed_at = v_now,
             locked_until = v_now + least(
               interval '15 minutes' * pg_catalog.power(2, least(v_lockouts, 16)),
               interval '24 hours')
       where id = p_principal_id;
      v_principal_lock_started := true;
    else
      update platform.auth_principal
         set failed_attempts = v_failed, lockout_count = v_lockouts, locked_until = null,
             last_failed_at = v_now
       where id = p_principal_id;
    end if;

    if v_has_profile then
      v_failed := case when v_r_last is not null and v_now - v_r_last < interval '1 hour'
                       then v_r_failed else 0 end + 1;
      v_lockouts := case when v_r_last is not null and v_now - v_r_last < interval '24 hours'
                         then v_r_lockouts else 0 end;
      if v_failed >= 5 then
        update platform.operator_profile
           set failed_attempts = 0, lockout_count = v_lockouts + 1, last_failed_at = v_now,
               locked_until = v_now + least(
                 interval '15 minutes' * pg_catalog.power(2, least(v_lockouts, 16)),
                 interval '24 hours')
         where operator_id = p_operator_id;
        v_profile_lock_started := true;
      else
        update platform.operator_profile
           set failed_attempts = v_failed, lockout_count = v_lockouts, locked_until = null,
               last_failed_at = v_now
         where operator_id = p_operator_id;
      end if;
    end if;
  end if;

  v_event := case when v_principal_lock_started then 'principal.locked'
                  when v_profile_lock_started then 'profile.locked'
                  else 'profile.selection_refused' end;
  insert into platform.auth_event
    (occurred_at, event_type, principal_id, principal_email_norm, operator_id, refusal_reason)
  values (v_now, v_event, p_principal_id, v_email,
          case when v_has_profile then p_operator_id end, reason);
end
$$;

comment on function platform.finish_pin_attempt(uuid, uuid, uuid, uuid, bytea) is
  'ARCHITECTURE.md §6.2 closed list — finishes the PIN attempt begun in this transaction: consumes it, verifies the caller''s proof (HMAC-SHA256 of the Argon2id output over the attempt, the pair and the nonce) against the stored verifier by SHA-256 digest, computes the verdict, counters, lock and timestamps itself, and writes the throttle transition and exactly one platform.auth_event atomically. A missing or malformed proof is a counted failure. session_user must be origenlab_api. SECURITY DEFINER, search_path = pg_catalog, no dynamic SQL.';

revoke all on function platform.finish_pin_attempt(uuid, uuid, uuid, uuid, bytea)
  from public, anon, authenticated, service_role, origenlab_worker, origenlab_migrator;
grant execute on function platform.finish_pin_attempt(uuid, uuid, uuid, uuid, bytea) to origenlab_api;

reset role;
