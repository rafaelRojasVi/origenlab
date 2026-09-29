-- Slice 1 — clearing a PIN lockout early is a migrator operation, and it is audited.
--
-- docs/ARCHITECTURE.md §5.1; docs/DOMAIN.md §7.3 (#40); apps/api/docs/PRODUCTION_AUTH.md.
--
-- A lock on a profile or on a whole principal (`20260928180000`) expires by itself. Clearing one
-- early is an operator decision taken outside the dashboard, through
-- `apps/api/scripts/profile_auth_admin.py clear-lockout` as the migrator: plan, then apply with
-- the exact change count and database name. It writes the throttle columns back to zero and one
-- `platform.auth_event` per cleared target:
--
--   * `event_type = 'lockout.cleared'`, with the principal, and the profile's operator when a
--     profile (not the principal-wide throttle) was cleared.
--
-- The runtime API role may append audit events, but never this one: a runtime path that could
-- write "lockout cleared" could also forge the record of an administrator's decision. Policies
-- stay pure role gates (supabase/tests/050_rls.sql), so the refusal is a trigger,
-- `auth_event_actor_guard`: a `lockout.cleared` event is accepted only from a role that is a
-- member of `origenlab_owner` — the migrator under `set role origenlab_owner`, or the owner — and
-- refused (42501) from the runtime role. It never appears as a route.

set role origenlab_owner;

alter table platform.auth_event
  drop constraint auth_event_type_check,
  drop constraint auth_event_subject_shape;

alter table platform.auth_event
  add constraint auth_event_type_check check (event_type in (
    'profile.selected', 'profile.selection_refused', 'profile.locked', 'profile.cleared', 'session.logout',
    'lockout.cleared'
  )),
  add constraint auth_event_subject_shape check (
    case event_type
      when 'profile.selected' then principal_id is not null and operator_id is not null
      when 'profile.selection_refused' then principal_id is not null
      when 'profile.locked' then principal_id is not null
      when 'profile.cleared' then principal_id is not null and operator_id is null
      when 'session.logout' then principal_id is not null or operator_id is not null
      when 'lockout.cleared' then principal_id is not null and previous_operator_id is null
    end
  );

comment on table platform.auth_event is
  'DOMAIN.md §7 #40 — append-only authentication audit: profile selection, refusal, lockout, clearing, logout, and a migrator''s early lockout clear, with the principal and the operator. No column can hold a PIN.';

create function platform.auth_event_actor_guard() returns trigger
language plpgsql
set search_path = pg_catalog
as $$
begin
  if new.event_type = 'lockout.cleared' and not pg_has_role(current_user, 'origenlab_owner', 'MEMBER') then
    raise exception 'platform.auth_event: only the migrator records a cleared lockout'
      using errcode = '42501';
  end if;
  return new;
end
$$;

comment on function platform.auth_event_actor_guard() is
  'Trigger: a lockout.cleared audit event is accepted only from a member of origenlab_owner (the migrator tool), never from the runtime API role. SECURITY INVOKER.';

create trigger auth_event_actor_guard
  before insert on platform.auth_event
  for each row execute function platform.auth_event_actor_guard();

revoke all on function platform.auth_event_actor_guard() from public, anon, authenticated, service_role;

reset role;
