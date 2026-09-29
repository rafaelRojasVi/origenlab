-- Slice 1 — the PIN throttle is written only by one narrow SECURITY DEFINER function.
--
-- docs/ARCHITECTURE.md §5.1, §6.2; docs/DOMAIN.md §7.3 (#38, #39); apps/api/docs/PRODUCTION_AUTH.md.
--
-- `20260928180000` let the runtime API role UPDATE the four throttle columns of
-- `platform.auth_principal` and `platform.operator_profile` directly. That grant is a free-form
-- write surface: any SQL reachable as `origenlab_api` — an injection, a bug, a compromised API
-- host — could zero a principal's failure count or lift a lock before every guess, and the
-- throttle would bound nothing. The counters, the lock deadline and the timestamps are the
-- throttle's own state; the caller should say *what happened*, never *what the state is*.
--
-- So the column grants and the two UPDATE policies that served them go, and the only runtime
-- write is `platform.record_pin_attempt(operation, principal_id, operator_id)`:
--
--   * `operation` is a closed vocabulary of three, and the caller supplies nothing else — no
--     counter, no deadline, no timestamp, no column name:
--       - `begin_attempt`  — lock the rows and report whether either is locked. Called first,
--                            before any PIN is checked, so that concurrent attempts through
--                            different API workers serialize on the principal row (the runtime
--                            role holds no UPDATE on either table any more, so it can no longer
--                            take `for update` itself);
--       - `record_failure` — one failed attempt: the counters, the lock and its duration are
--                            computed here from the rows and the database clock. An attempt
--                            while locked counts nothing (the lock is not extended by guessing);
--       - `record_success` — the PIN was right: the profile's throttle is cleared, the
--                            principal's failure count restarts but its lockout history is kept
--                            (it still decays). Refused while either row is locked, so a caller
--                            that skipped `begin_attempt` cannot lift a lock by claiming success.
--   * `least` is a SQL construct (like `coalesce`), not a catalog function, so it is not qualified.
--   * Every operation locks the principal row, then — only when `operator_id` names a profile
--     *of that principal* — the profile row, `for update`, always in that order. A profile of
--     another principal is treated exactly like an unknown one: only the principal is counted.
--   * The policy is the one `apps/api/src/origenlab_api/v2/profile_auth.py` documented and
--     computed until now, moved here unchanged: 5 failures lock a profile, 10 lock the whole
--     principal; a lock lasts 15 minutes × 2^(earlier lockouts), capped at 24 hours; a failure
--     older than an hour no longer counts, a lockout older than 24 hours no longer doubles.
--   * It returns four flags: whether each row was locked when the call found it, and whether
--     this call started a lock on each (so the caller can write the `profile.locked` audit).
--
-- The audit (`platform.auth_event`) stays the API's, in the same transaction; this function
-- writes no audit row and no `crm.domain_event` — authentication is not a commercial aggregate.
--
-- ARCHITECTURE.md §6.2, line by line: private schema; owned by `origenlab_owner`;
-- `search_path = pg_catalog`; every relation and function schema-qualified; no dynamic SQL;
-- EXECUTE revoked from PUBLIC, anon, authenticated, service_role (and never granted to the
-- worker or the migrator) and granted to `origenlab_api` only; `session_user` asserted to be
-- `origenlab_api`; one state transition on one aggregate (a principal's throttle).
--
-- The migrator's early `clear-lockout` (`apps/api/scripts/profile_auth_admin.py`) is unchanged:
-- it runs as `origenlab_owner`, which owns the tables.

set role origenlab_owner;

-- ── 1. the direct write surface goes ──────────────────────────────────────────────────────────
revoke update on platform.auth_principal from origenlab_api;
revoke update on platform.operator_profile from origenlab_api;
revoke update (failed_attempts, lockout_count, locked_until, last_failed_at) on platform.auth_principal from origenlab_api;
revoke update (failed_attempts, lockout_count, locked_until, last_failed_at) on platform.operator_profile from origenlab_api;

drop policy origenlab_api_update on platform.auth_principal;
drop policy origenlab_api_update on platform.operator_profile;

-- ── 2. the one transition function ────────────────────────────────────────────────────────────
create function platform.record_pin_attempt(
  p_operation text,
  p_principal_id uuid,
  p_operator_id uuid,
  out principal_locked boolean,
  out profile_locked boolean,
  out principal_lock_started boolean,
  out profile_lock_started boolean
)
language plpgsql
security definer
set search_path = pg_catalog
as $$
declare
  v_now timestamptz := pg_catalog.now();
  v_p_failed integer;
  v_p_lockouts integer;
  v_p_locked_until timestamptz;
  v_p_last timestamptz;
  v_has_profile boolean := false;
  v_r_failed integer;
  v_r_lockouts integer;
  v_r_locked_until timestamptz;
  v_r_last timestamptz;
  v_failed integer;
  v_lockouts integer;
begin
  if session_user <> 'origenlab_api' then
    raise exception 'platform.record_pin_attempt: refused for login %', session_user using errcode = '42501';
  end if;
  if p_operation is null or p_operation not in ('begin_attempt', 'record_failure', 'record_success') then
    raise exception 'platform.record_pin_attempt: unknown operation' using errcode = '22023';
  end if;
  if p_principal_id is null then
    raise exception 'platform.record_pin_attempt: a principal is required' using errcode = '22004';
  end if;

  -- Principal first, then the profile: the one lock order, so two attempts never deadlock.
  select a.failed_attempts, a.lockout_count, a.locked_until, a.last_failed_at
    into v_p_failed, v_p_lockouts, v_p_locked_until, v_p_last
    from platform.auth_principal a
   where a.id = p_principal_id
     for update;
  if not found then
    raise exception 'platform.record_pin_attempt: no such principal' using errcode = 'P0002';
  end if;

  if p_operator_id is not null then
    select p.failed_attempts, p.lockout_count, p.locked_until, p.last_failed_at
      into v_r_failed, v_r_lockouts, v_r_locked_until, v_r_last
      from platform.operator_profile p
     where p.operator_id = p_operator_id and p.principal_id = p_principal_id
       for update;
    v_has_profile := found;
  end if;

  principal_locked := v_p_locked_until is not null and v_p_locked_until > v_now;
  profile_locked := v_has_profile and v_r_locked_until is not null and v_r_locked_until > v_now;
  principal_lock_started := false;
  profile_lock_started := false;

  if p_operation = 'begin_attempt' then
    return;
  end if;

  if p_operation = 'record_success' then
    if not v_has_profile then
      raise exception 'platform.record_pin_attempt: success needs a profile of this principal' using errcode = 'P0002';
    end if;
    if principal_locked or profile_locked then
      raise exception 'platform.record_pin_attempt: a locked attempt cannot succeed' using errcode = '55P03';
    end if;
    update platform.auth_principal
       set failed_attempts = 0, locked_until = null
     where id = p_principal_id;
    update platform.operator_profile
       set failed_attempts = 0, lockout_count = 0, locked_until = null, last_failed_at = null
     where operator_id = p_operator_id;
    return;
  end if;

  -- record_failure. While locked the attempt is refused without counting.
  if principal_locked or profile_locked then
    return;
  end if;

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
    principal_lock_started := true;
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
      profile_lock_started := true;
    else
      update platform.operator_profile
         set failed_attempts = v_failed, lockout_count = v_lockouts, locked_until = null,
             last_failed_at = v_now
       where operator_id = p_operator_id;
    end if;
  end if;
end
$$;

comment on function platform.record_pin_attempt(text, uuid, uuid) is
  'ARCHITECTURE.md §6.2 closed list — the only runtime writer of the PIN throttle columns of platform.auth_principal and platform.operator_profile. Closed operations begin_attempt (lock and report), record_failure and record_success; counters, lock duration and timestamps are computed here from the locked rows and the database clock, never supplied by the caller. session_user must be origenlab_api. SECURITY DEFINER, search_path = pg_catalog, no dynamic SQL.';

revoke all on function platform.record_pin_attempt(text, uuid, uuid)
  from public, anon, authenticated, service_role, origenlab_worker, origenlab_migrator;
grant execute on function platform.record_pin_attempt(text, uuid, uuid) to origenlab_api;

reset role;
