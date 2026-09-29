-- Slice 1 — profile sessions are revocable: a database row behind every shared-sign-in cookie.
--
-- docs/ARCHITECTURE.md §5.1; docs/DOMAIN.md §7.3 (#41); apps/api/docs/PRODUCTION_AUTH.md.
--
-- `20260928180000` kept sessions as stateless signed cookies, revoked only by a version compare.
-- That leaves one hole: logout clears the browser's cookie but not a copy taken before it, which
-- stays valid until it expires. For a shared account used by several people that is not
-- acceptable, so a principal session (the shared Google sign-in and the profile selected through
-- it) is now also a row here, and the API requires the row on every request:
--
--   * The cookie stays HMAC-signed and names the session by a random identifier. **The table
--     never holds that identifier**: it holds `token_hash`, an HMAC-SHA256 of it keyed with the
--     API's session secret — 32 bytes a database reader cannot turn back into a usable cookie,
--     and cannot even forge a matching cookie from without the key.
--   * A request is served only while its row exists, is not revoked and has not expired
--     (`expires_at`, the database clock) — in addition to the cookie's own signature and expiry
--     and to the version compare, which stays: a role, PIN, status, link or principal change
--     still ends the session on its next request without touching this table.
--   * Logout revokes the row (`logout`) before the cookie is cleared, so a copied cookie fails on
--     its very next request, on any API instance. Selecting a profile and clearing one
--     ("Cambiar perfil") revoke the current row (`profile_selected`, `profile_cleared`) and
--     insert its successor **with the same `expires_at`**: a session is rotated, never extended.
--
-- Guards (trigger `auth_session_guard`, whoever writes): the identifier hash, the principal, the
-- operator and `issued_at` never change; `expires_at` may only move earlier; a revocation is
-- final. Checks: the hash is 32 bytes, a session lives at most seven days, and a revocation
-- carries its reason from a closed vocabulary.
--
-- Privileges. The runtime API role reads and inserts rows and may set only `revoked_at` /
-- `revoked_reason` (column grant); the trigger makes that a one-way step. Its policies are pure
-- role gates, like every other (supabase/tests/050_rls.sql). It deletes nothing — no runtime role holds
-- DELETE on any table (supabase/tests/040_grant_matrix.sql) — so expired and revoked rows are
-- removed by the migrator (`apps/api/scripts/profile_auth_admin.py prune-sessions`). The worker
-- has no privilege here. Operator sessions (an individual's own Google account) are unchanged:
-- they stay stateless and are not recorded here.

set role origenlab_owner;

create table platform.auth_session (
  id uuid primary key default gen_random_uuid(),
  token_hash bytea not null,
  principal_id uuid not null references platform.auth_principal (id),
  operator_id uuid references platform.operator_profile (operator_id),
  issued_at timestamptz not null default now(),
  expires_at timestamptz not null,
  revoked_at timestamptz,
  revoked_reason text,
  constraint auth_session_token_hash_key unique (token_hash),
  constraint auth_session_token_hash_shape check (octet_length(token_hash) = 32),
  constraint auth_session_lifetime check (expires_at > issued_at and expires_at <= issued_at + interval '7 days'),
  constraint auth_session_revocation_shape check ((revoked_at is null) = (revoked_reason is null)),
  constraint auth_session_revoked_reason_check check (
    revoked_reason in ('logout', 'profile_selected', 'profile_cleared')
  )
);

comment on table platform.auth_session is
  'DOMAIN.md §7 #41 — one shared-sign-in session (a principal, and the profile selected through it). The cookie names it by a random identifier; this row holds only its keyed HMAC. Served only while not revoked and not expired; rotated (never extended) on profile select and clear; revoked on logout.';
comment on column platform.auth_session.token_hash is
  'HMAC-SHA256 of the cookie''s session identifier, keyed with the API''s session secret. Never the identifier itself.';

create index auth_session_principal_idx on platform.auth_session (principal_id);
create index auth_session_operator_idx on platform.auth_session (operator_id);
create index auth_session_expires_idx on platform.auth_session (expires_at);

alter table platform.auth_session enable row level security;

create function platform.auth_session_guard() returns trigger
language plpgsql
set search_path = pg_catalog
as $$
begin
  if (new.id, new.token_hash, new.principal_id, new.operator_id, new.issued_at)
       is distinct from (old.id, old.token_hash, old.principal_id, old.operator_id, old.issued_at) then
    raise exception 'platform.auth_session: a session''s identity, principal, operator and issue time never change'
      using errcode = 'P0001';
  end if;
  if new.expires_at > old.expires_at then
    raise exception 'platform.auth_session: a session is never extended' using errcode = 'P0001';
  end if;
  if old.revoked_at is not null
     and (new.revoked_at, new.revoked_reason) is distinct from (old.revoked_at, old.revoked_reason) then
    raise exception 'platform.auth_session: a revocation is final' using errcode = 'P0001';
  end if;
  return new;
end
$$;

comment on function platform.auth_session_guard() is
  'Trigger: keeps a session''s identifier hash, principal, operator and issue time fixed, never lets expires_at move later, and makes a revocation final. SECURITY INVOKER.';

create trigger auth_session_guard
  before update on platform.auth_session
  for each row execute function platform.auth_session_guard();

revoke all on function platform.auth_session_guard() from public, anon, authenticated, service_role;

grant select, insert on platform.auth_session to origenlab_api;
grant update (revoked_at, revoked_reason) on platform.auth_session to origenlab_api;

create policy origenlab_api_select on platform.auth_session for select to origenlab_api using (true);
create policy origenlab_api_insert on platform.auth_session for insert to origenlab_api with check (true);
create policy origenlab_api_update on platform.auth_session for update to origenlab_api using (true) with check (true);

reset role;
