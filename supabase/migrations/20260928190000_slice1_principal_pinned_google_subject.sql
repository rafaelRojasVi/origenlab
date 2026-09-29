-- Slice 1 — a shared sign-in principal is the Google account (issuer + subject), not its address.
--
-- docs/ARCHITECTURE.md §5.1; docs/DOMAIN.md §7.3 (#38); apps/api/docs/PRODUCTION_AUTH.md.
--
-- An address is not an identity: a Google account that is deleted and created again under the
-- same address (the shared mailbox) is a different account with a different stable subject (`sub`).
-- `20260928180000` made the subject an optional pin. This migration makes it a pair — the
-- verified ID token's issuer and subject — and the API now requires it in production: a principal
-- with no pinned subject cannot sign in there at all, and one whose pinned subject differs from
-- the token's is refused even for the same address (`auth_routes._principal_signed_in`,
-- `identity.check_principal_session`).
--
--   * `provider_issuer` joins `provider_subject`. Google issues under two spellings of one issuer
--     (`accounts.google.com` and `https://accounts.google.com`); the API stores and compares the
--     canonical `https://…` form, and the check below admits nothing else.
--   * The two are set together or not at all.
--   * Changing either bumps `version` (the trigger is replaced below), so every session signed
--     under the old pin ends on its next request.
--
-- Who may set them: only the owner, through the migrator roster tool
-- (`apps/api/scripts/profile_roster.py`). The runtime API role's column grant on this table is
-- the throttle columns alone (`20260928180000`), so it cannot pin, re-pin or unpin a subject —
-- `supabase/tests/073_shared_workspace_profiles.sql` proves it.

set role origenlab_owner;

alter table platform.auth_principal
  add column provider_issuer text;

-- A subject pinned before this migration was necessarily a Google one.
update platform.auth_principal
   set provider_issuer = 'https://accounts.google.com'
 where provider_subject is not null and provider_issuer is null;

alter table platform.auth_principal
  add constraint auth_principal_provider_issuer_check
    check (provider_issuer is null or provider_issuer = 'https://accounts.google.com'),
  add constraint auth_principal_subject_issuer_pair
    check ((provider_issuer is null) = (provider_subject is null));

comment on column platform.auth_principal.provider_subject is
  'The Google account''s stable subject (ID token `sub`), with provider_issuer. Required for sign-in in production; a token whose issuer+subject differ is refused even for the same address. Set only by the migrator roster tool.';
comment on column platform.auth_principal.provider_issuer is
  'Canonical issuer of provider_subject (https://accounts.google.com). Set together with provider_subject or not at all.';

create or replace function platform.auth_principal_security_version() returns trigger
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
    if (new.email_norm, new.provider_issuer, new.provider_subject, new.status)
         is distinct from (old.email_norm, old.provider_issuer, old.provider_subject, old.status)
       and new.version = old.version then
      new.version := old.version + 1;
    end if;
  end if;
  return new;
end
$$;

comment on function platform.auth_principal_security_version() is
  'Trigger: refuses a principal address that is an operator''s, keeps id and provider fixed, and bumps version on any address, issuer, subject or status change. SECURITY INVOKER.';

revoke all on function platform.auth_principal_security_version() from public, anon, authenticated, service_role;

reset role;
