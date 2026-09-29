-- Slice 1 — every dashboard session is a revocable row: individual Google accounts too.
--
-- docs/ARCHITECTURE.md §5, §5.1; docs/DOMAIN.md §7.3 (#41); apps/api/docs/PRODUCTION_AUTH.md.
--
-- `20260928191000` made a shared-sign-in session a `platform.auth_session` row, but left the
-- session of an operator signing in with their *own* Google account a stateless signed cookie:
-- logout cleared it in one browser and a copy taken before kept working until it expired. One
-- sign-in mode that can be revoked beside one that cannot is one boundary at its weakest, so the
-- individual session is now a row in the same table, required on every request and revoked by
-- logout exactly like the shared one.
--
--   * `sign_in_kind` says which session a row is, in the vocabulary `platform.operator` already
--     uses: `shared_profile` (a principal, and the profile selected through it — every row
--     before this migration, hence the default) or `google_account` (one operator's own Google
--     account).
--   * `account_operator_id` names the operator of a `google_account` session. The composite key
--     `(account_operator_id, sign_in_kind)` → `platform.operator (id, sign_in_kind)` admits only
--     a `google_account` operator there, and is unchecked on a `shared_profile` row, where the
--     column is null.
--   * `principal_id` becomes nullable: a `google_account` session has no principal. The shape
--     check makes each kind exact — a shared session has a principal, may have a selected
--     profile (`operator_id`) and no account operator; an individual one has an account
--     operator, no principal and no profile.
--   * The guard (replaced below) keeps the kind and the account operator fixed too: a session
--     never changes whose it is.
--
-- Everything else is unchanged: the table still holds only a keyed hash of the cookie's session
-- identifier, a session lives at most seven days and is never extended, a revocation is final
-- and carries a closed reason, and the runtime role reads, inserts and revokes (column grant) —
-- never deletes. The migrator's `prune-sessions` removes ended rows of either kind. Disabling an
-- operator or changing its role still ends its sessions on the next request: the API re-reads
-- the operator and compares its `version` (bumped by trigger, `20260928180000`) with the one
-- signed into the cookie.

set role origenlab_owner;

alter table platform.auth_session
  add column sign_in_kind text not null default 'shared_profile',
  add column account_operator_id uuid,
  alter column principal_id drop not null;

alter table platform.auth_session
  add constraint auth_session_sign_in_kind_check
    check (sign_in_kind in ('shared_profile', 'google_account')),
  add constraint auth_session_kind_shape check (
    case sign_in_kind
      when 'shared_profile' then principal_id is not null and account_operator_id is null
      when 'google_account' then principal_id is null and operator_id is null and account_operator_id is not null
      else false
    end
  ),
  add constraint auth_session_account_operator_fkey foreign key (account_operator_id, sign_in_kind)
    references platform.operator (id, sign_in_kind);

-- Covers the composite foreign key above (supabase/tests/090_foreign_key_indexes.sql).
create index auth_session_account_operator_idx on platform.auth_session (account_operator_id, sign_in_kind);

comment on table platform.auth_session is
  'DOMAIN.md §7 #41 — one dashboard session: a shared sign-in (a principal, and the profile selected through it) or an operator''s own Google account. The cookie names it by a random identifier; this row holds only its keyed HMAC. Served only while not revoked and not expired; rotated (never extended) on profile select and clear; revoked on logout.';
comment on column platform.auth_session.sign_in_kind is
  'shared_profile: principal_id set, operator_id the selected profile (or none yet). google_account: account_operator_id set, no principal, no profile. Never changes.';
comment on column platform.auth_session.account_operator_id is
  'The google_account operator whose own Google sign-in this session is; null on a shared_profile session.';

create or replace function platform.auth_session_guard() returns trigger
language plpgsql
set search_path = pg_catalog
as $$
begin
  if (new.id, new.token_hash, new.sign_in_kind, new.principal_id, new.operator_id,
      new.account_operator_id, new.issued_at)
       is distinct from (old.id, old.token_hash, old.sign_in_kind, old.principal_id, old.operator_id,
                         old.account_operator_id, old.issued_at) then
    raise exception 'platform.auth_session: a session''s identity, kind, principal, operators and issue time never change'
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
  'Trigger: keeps a session''s identifier hash, kind, principal, operators and issue time fixed, never lets expires_at move later, and makes a revocation final. SECURITY INVOKER.';

revoke all on function platform.auth_session_guard() from public, anon, authenticated, service_role;

reset role;
