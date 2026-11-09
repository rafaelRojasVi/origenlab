-- Slice 8 — platform.operator_profile.home_filter: the «Hoy» filter a profile opens on
-- (DOMAIN.md §7 #39).
--
-- The dashboard's Hoy page opens on one of three views (todo, llamadas, cotizar). Which one a
-- person starts on is a per-profile setting the owner decides, so it lives on the profile row:
--
--   home_filter   'todo', 'llamadas', 'cotizar' or null. Null is the dashboard's default view;
--                 no default and no backfill, so every existing profile stays null.
--
--   * One writer: the roster tool (`apps/api/src/origenlab_api/v2/profile_roster.py`), which
--     writes as origenlab_owner like every other profile column. No route writes it.
--   * Read by `/auth/session` for profile sessions. origenlab_api reads operator_profile column
--     by column (20260929100000: never pin_hash), so the new column is granted by name;
--     supabase/tests/040_grant_matrix.sql refuses any non-pin_hash column the API cannot read.
--     No UPDATE grant, no worker access.
--   * No policy change: the existing `origenlab_api_select` policy (using true) covers the row.
--   * Outside the version trigger: platform.operator_profile_security_version() bumps version
--     only on pin_hash, principal_id, profile_key or status, so changing the filter never ends
--     a profile session. The trigger is not touched.

set role origenlab_owner;

alter table platform.operator_profile
  add column home_filter text;

alter table platform.operator_profile
  add constraint operator_profile_home_filter_check check (home_filter in ('todo', 'llamadas', 'cotizar'));

comment on column platform.operator_profile.home_filter is
  'The Hoy view this profile opens on: todo, llamadas or cotizar; null = the dashboard default. Written only by the roster tool; not a security field (no version bump). DOMAIN.md §7 #39.';

grant select (home_filter) on platform.operator_profile to origenlab_api;

reset role;
