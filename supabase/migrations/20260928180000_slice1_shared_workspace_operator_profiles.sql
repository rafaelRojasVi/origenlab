-- Slice 1 — shared Google Workspace sign-in with per-person operator profiles.
--
-- docs/ARCHITECTURE.md §5 (authentication and authorization); docs/DOMAIN.md §7.3 (#38–#40);
-- apps/api/docs/PRODUCTION_AUTH.md, "Shared Workspace login with operator profiles".
--
-- One Google Workspace account is used by several people. Signing in with Google proves that
-- account and nothing else; which person is at the keyboard is a second, separate fact, chosen
-- on a profile screen and proven by a per-person PIN that the API verifies. The two facts live in
-- two relations so they can never be confused:
--
--   * `platform.auth_principal` (#38) — an external sign-in identity (today: one Google
--     account). It grants nothing by itself. A principal is never an operator: its address may
--     not also be an operator's address (both guards below), so one Google sign-in resolves to
--     one path only.
--   * `platform.operator` (#29) — unchanged in meaning: the person who acts, with the role every
--     command checks. It gains `sign_in_kind`. `google_account` is today's operator, reached by
--     signing in with their own address, which is still required. `shared_profile` is an
--     operator reached only through a profile of a principal; it has **no address at all**, so
--     no invented or duplicate mailbox is ever needed to represent a person. Individual Google
--     accounts remain possible side by side, and a person can move from one kind to the other
--     only as a new operator row (the kind never changes in place).
--   * `platform.operator_profile` (#39) — links a `shared_profile` operator to the principal it
--     may be selected from, and holds that person's PIN as an Argon2id PHC string computed by the
--     API with a server-side pepper. The table refuses anything that is not such a string, so a
--     plaintext PIN cannot be stored here even by mistake.
--   * `platform.auth_event` (#40) — the append-only authentication audit: profile selected,
--     refused, locked, cleared, and logout. It has no column that could hold a PIN.
--
-- Rate limiting is persistent and shared by every API worker: failure counters and a lockout
-- deadline on the principal row (every attempt through that Google account) and on the profile
-- row (attempts at that one person). The API takes both rows `for update` before verifying a PIN,
-- so concurrent attempts through different workers serialize and none is lost.
--
-- Session invalidation is a version compare. The API binds the operator's, the profile's and the
-- principal's `version` into the signed session and re-reads all three on every request. The
-- triggers below bump `version` whenever a security-relevant column changes — role, status or
-- address of an operator; PIN, link, key or status of a profile; address, subject or status of a
-- principal — **whoever writes the row**, a manual `update` included, so a session can never
-- survive the change it should not survive. Throttle columns do not bump it: a wrong PIN must not
-- sign out the person already using the profile.
--
-- Privileges. The runtime API role reads all three tables, may update only the throttle columns
-- of the principal and profile rows, and may insert (never change) audit events. It cannot write a
-- principal, a profile link or a PIN hash: provisioning runs as the owner through
-- `apps/api/scripts/profile_roster.py`. The worker has no privilege on any of the three.
--
-- Deliberately absent: no names, principals, profiles or PIN hashes are seeded here (they are
-- provisioned per environment, outside Git), no SECURITY DEFINER function, no session table
-- (sessions stay stateless signed cookies, revoked by the version compare above).

set role origenlab_owner;

-- ── 1. platform.operator: how the operator signs in ─────────────────────────────────────────────
alter table platform.operator
  add column sign_in_kind text not null default 'google_account';

alter table platform.operator
  alter column email_norm drop not null,
  add constraint operator_sign_in_kind_check check (sign_in_kind in ('google_account', 'shared_profile')),
  -- A google_account operator is its address; a shared_profile operator has none.
  add constraint operator_sign_in_shape check ((sign_in_kind = 'google_account') = (email_norm is not null)),
  -- Target of operator_profile's composite key, so only a shared_profile operator can be linked.
  add constraint operator_id_sign_in_kind_key unique (id, sign_in_kind);

comment on column platform.operator.sign_in_kind is
  'google_account: signs in with its own email_norm. shared_profile: no address; reached only through platform.operator_profile of a platform.auth_principal. Never changes in place.';

-- ── 2. platform.auth_principal (#38) ──────────────────────────────────────────────────────────
create table platform.auth_principal (
  id uuid primary key default gen_random_uuid(),
  provider text not null default 'google',
  email_norm text not null,
  provider_subject text,
  status text not null default 'active',
  version integer not null default 1,
  failed_attempts integer not null default 0,
  lockout_count integer not null default 0,
  locked_until timestamptz,
  last_failed_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint auth_principal_provider_check check (provider in ('google')),
  constraint auth_principal_provider_email_key unique (provider, email_norm),
  constraint auth_principal_provider_subject_key unique (provider, provider_subject),
  constraint auth_principal_email_norm_shape check (
    email_norm = lower(email_norm) and email_norm ~ '^[^@\s]+@[^@\s]+\.[^@\s]+$'
  ),
  constraint auth_principal_provider_subject_shape check (
    provider_subject is null or provider_subject ~ '^[A-Za-z0-9._-]{1,255}$'
  ),
  constraint auth_principal_status_check check (status in ('active', 'disabled')),
  constraint auth_principal_version_positive check (version >= 1),
  constraint auth_principal_throttle_shape check (failed_attempts >= 0 and lockout_count >= 0)
);

comment on table platform.auth_principal is
  'DOMAIN.md §7 #38 — an external sign-in identity (a shared Google Workspace account). Grants nothing by itself: the person acting is the platform.operator selected through platform.operator_profile. Carries the principal-wide PIN throttle.';
comment on column platform.auth_principal.provider_subject is
  'Optional pin to the provider''s stable subject (Google `sub`). When set, a sign-in whose subject differs is refused even for the same address.';

alter table platform.auth_principal enable row level security;

-- ── 3. platform.operator_profile (#39) ────────────────────────────────────────────────────────
create table platform.operator_profile (
  operator_id uuid primary key,
  operator_sign_in_kind text not null default 'shared_profile',
  principal_id uuid not null references platform.auth_principal (id),
  profile_key text not null,
  pin_hash text not null,
  pin_set_at timestamptz not null default now(),
  status text not null default 'active',
  sort_order smallint not null default 0,
  version integer not null default 1,
  failed_attempts integer not null default 0,
  lockout_count integer not null default 0,
  locked_until timestamptz,
  last_failed_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint operator_profile_operator_fkey foreign key (operator_id, operator_sign_in_kind)
    references platform.operator (id, sign_in_kind),
  constraint operator_profile_operator_sign_in_kind_check check (operator_sign_in_kind = 'shared_profile'),
  -- Covers the composite foreign key above (supabase/tests/090_foreign_key_indexes.sql).
  constraint operator_profile_operator_kind_key unique (operator_id, operator_sign_in_kind),
  -- Also covers principal_id.
  constraint operator_profile_principal_key_key unique (principal_id, profile_key),
  constraint operator_profile_key_shape check (profile_key ~ '^[a-z][a-z0-9_-]{1,39}$'),
  -- An Argon2id PHC string and nothing else: a plaintext PIN can never satisfy this.
  constraint operator_profile_pin_hash_argon2id check (
    pin_hash ~ '^\$argon2id\$v=19\$m=[0-9]{1,8},t=[0-9]{1,3},p=[0-9]{1,3}\$[A-Za-z0-9+/]{16,}\$[A-Za-z0-9+/]{32,}$'
  ),
  constraint operator_profile_status_check check (status in ('active', 'disabled')),
  constraint operator_profile_version_positive check (version >= 1),
  constraint operator_profile_throttle_shape check (failed_attempts >= 0 and lockout_count >= 0)
);

comment on table platform.operator_profile is
  'DOMAIN.md §7 #39 — a shared_profile operator selectable from one platform.auth_principal, with its PIN as an Argon2id PHC string (API-side pepper). Carries the per-profile PIN throttle. version bumps on any PIN, link, key or status change.';
comment on column platform.operator_profile.pin_hash is
  'Argon2id PHC string of the PIN, keyed with the API''s ORIGENLAB_PROFILE_PIN_PEPPER. Never a PIN.';

alter table platform.operator_profile enable row level security;

-- ── 4. platform.auth_event (#40) ──────────────────────────────────────────────────────────────
create table platform.auth_event (
  id uuid primary key default gen_random_uuid(),
  occurred_at timestamptz not null default now(),
  event_type text not null,
  principal_id uuid references platform.auth_principal (id),
  principal_email_norm text,
  operator_id uuid references platform.operator (id),
  previous_operator_id uuid references platform.operator (id),
  refusal_reason text,
  constraint auth_event_type_check check (event_type in (
    'profile.selected', 'profile.selection_refused', 'profile.locked', 'profile.cleared', 'session.logout'
  )),
  constraint auth_event_refusal_reason_check check (refusal_reason in (
    'pin_mismatch', 'locked', 'unknown_profile', 'profile_inactive', 'malformed_pin'
  )),
  constraint auth_event_refusal_shape check ((event_type = 'profile.selection_refused') = (refusal_reason is not null)),
  constraint auth_event_principal_email_shape check (
    principal_email_norm is null or (principal_email_norm = lower(principal_email_norm)
                                     and principal_email_norm ~ '^[^@\s]+@[^@\s]+\.[^@\s]+$')
  ),
  constraint auth_event_principal_pair check ((principal_id is null) = (principal_email_norm is null)),
  constraint auth_event_subject_shape check (
    case event_type
      when 'profile.selected' then principal_id is not null and operator_id is not null
      when 'profile.selection_refused' then principal_id is not null
      when 'profile.locked' then principal_id is not null
      when 'profile.cleared' then principal_id is not null and operator_id is null
      when 'session.logout' then principal_id is not null or operator_id is not null
    end
  )
);

comment on table platform.auth_event is
  'DOMAIN.md §7 #40 — append-only authentication audit: profile selection, refusal, lockout, clearing and logout, with the principal and the operator. No column can hold a PIN.';

create index auth_event_principal_idx on platform.auth_event (principal_id);
create index auth_event_operator_idx on platform.auth_event (operator_id);
create index auth_event_previous_operator_idx on platform.auth_event (previous_operator_id);

create trigger auth_event_append_only
  before update or delete on platform.auth_event
  for each row execute function platform.reject_mutation('append-only');

alter table platform.auth_event enable row level security;

-- ── 5. version guards: every security-relevant change ends the sessions it affects ───────────
create function platform.operator_security_version() returns trigger
language plpgsql
set search_path = pg_catalog
as $$
begin
  if new.email_norm is not null
     and exists (select 1 from platform.auth_principal p where p.email_norm = new.email_norm) then
    raise exception 'platform.operator: % is a shared sign-in principal, not an operator address', new.email_norm
      using errcode = '23505';
  end if;
  if tg_op = 'UPDATE' then
    if new.id <> old.id or new.sign_in_kind <> old.sign_in_kind then
      raise exception 'platform.operator: id and sign_in_kind never change'
        using errcode = 'P0001';
    end if;
    if new.version < old.version then
      raise exception 'platform.operator: version never decreases' using errcode = 'P0001';
    end if;
    if (new.role, new.status, new.email_norm) is distinct from (old.role, old.status, old.email_norm)
       and new.version = old.version then
      new.version := old.version + 1;
    end if;
  end if;
  return new;
end
$$;

comment on function platform.operator_security_version() is
  'Trigger: refuses an operator address that is a principal''s, keeps id and sign_in_kind fixed, and bumps version on any role, status or address change. SECURITY INVOKER.';

create trigger operator_security_version
  before insert or update on platform.operator
  for each row execute function platform.operator_security_version();

create function platform.auth_principal_security_version() returns trigger
language plpgsql
set search_path = pg_catalog
as $$
begin
  if exists (select 1 from platform.operator o where o.email_norm = new.email_norm) then
    raise exception 'platform.auth_principal: % is an operator address, not a shared sign-in principal', new.email_norm
      using errcode = '23505';
  end if;
  if tg_op = 'UPDATE' then
    if new.id <> old.id or new.provider <> old.provider then
      raise exception 'platform.auth_principal: id and provider never change' using errcode = 'P0001';
    end if;
    if new.version < old.version then
      raise exception 'platform.auth_principal: version never decreases' using errcode = 'P0001';
    end if;
    if (new.email_norm, new.provider_subject, new.status)
         is distinct from (old.email_norm, old.provider_subject, old.status)
       and new.version = old.version then
      new.version := old.version + 1;
    end if;
  end if;
  return new;
end
$$;

comment on function platform.auth_principal_security_version() is
  'Trigger: refuses a principal address that is an operator''s, keeps id and provider fixed, and bumps version on any address, subject or status change. SECURITY INVOKER.';

create trigger auth_principal_security_version
  before insert or update on platform.auth_principal
  for each row execute function platform.auth_principal_security_version();

create function platform.operator_profile_security_version() returns trigger
language plpgsql
set search_path = pg_catalog
as $$
begin
  if new.operator_id <> old.operator_id then
    raise exception 'platform.operator_profile: operator_id never changes' using errcode = 'P0001';
  end if;
  if new.version < old.version then
    raise exception 'platform.operator_profile: version never decreases' using errcode = 'P0001';
  end if;
  if new.pin_hash <> old.pin_hash then
    new.pin_set_at := now();
  end if;
  if (new.pin_hash, new.principal_id, new.profile_key, new.status)
       is distinct from (old.pin_hash, old.principal_id, old.profile_key, old.status)
     and new.version = old.version then
    new.version := old.version + 1;
  end if;
  return new;
end
$$;

comment on function platform.operator_profile_security_version() is
  'Trigger: keeps operator_id fixed, restamps pin_set_at on a new PIN, and bumps version on any PIN, link, key or status change. SECURITY INVOKER.';

create trigger operator_profile_security_version
  before update on platform.operator_profile
  for each row execute function platform.operator_profile_security_version();

revoke all on function platform.operator_security_version() from public, anon, authenticated, service_role;
revoke all on function platform.auth_principal_security_version() from public, anon, authenticated, service_role;
revoke all on function platform.operator_profile_security_version() from public, anon, authenticated, service_role;

-- ── 6. grants and policies ────────────────────────────────────────────────────────────────────
-- Reads for the sign-in path; the throttle columns only; audit append only.
grant select on platform.auth_principal to origenlab_api;
grant update (failed_attempts, lockout_count, locked_until, last_failed_at) on platform.auth_principal to origenlab_api;
grant select on platform.operator_profile to origenlab_api;
grant update (failed_attempts, lockout_count, locked_until, last_failed_at) on platform.operator_profile to origenlab_api;
grant select, insert on platform.auth_event to origenlab_api;

create policy origenlab_api_select on platform.auth_principal for select to origenlab_api using (true);
create policy origenlab_api_update on platform.auth_principal for update to origenlab_api using (true) with check (true);
create policy origenlab_api_select on platform.operator_profile for select to origenlab_api using (true);
create policy origenlab_api_update on platform.operator_profile for update to origenlab_api using (true) with check (true);
create policy origenlab_api_select on platform.auth_event for select to origenlab_api using (true);
create policy origenlab_api_insert on platform.auth_event for insert to origenlab_api with check (true);

reset role;
