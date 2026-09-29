-- Slice 1 — the runtime API role can no longer create or change an operator.
--
-- docs/ARCHITECTURE.md §5, §5.1, §6; docs/DOMAIN.md §7 (#29); apps/api/docs/PRODUCTION_AUTH.md.
--
-- Slice 0 granted `origenlab_api` SELECT, INSERT and UPDATE on `platform.operator`, for an admin
-- command that was never built. Nothing in the running application uses the two write verbs:
-- no route, command, repository or SECURITY DEFINER function inserts or updates an operator
-- (searched 2026-09-28 across apps/, supabase/migrations and scripts/). They are pure risk: any
-- SQL reachable as the runtime role — an injection, a bug, a compromised API host — could raise
-- an operator's `role`, re-enable a disabled one, change a `sign_in_kind`, or create an admin
-- whose address it controls and then sign in as it.
--
-- So both verbs, and the two policies that only served them, go. The roster tools that change
-- operators and profiles — `apps/api/scripts/operator_roster.py`, `profile_roster.py` — run as
-- the migrator under `set local role origenlab_owner`, plan first and apply an exact confirmed
-- count; the runtime role cannot assume the owner. Profile linkage and PIN state were already out
-- of reach: on `platform.operator_profile` and `platform.auth_principal` the runtime role holds
-- UPDATE on the four throttle columns only (`20260928180000`).
--
-- Everything the running API does with operators is a read: resolving the signed-in operator,
-- re-checking its role and status on every request, and attributing commands to it.

set role origenlab_owner;

revoke insert, update on platform.operator from origenlab_api;

drop policy origenlab_api_insert on platform.operator;
drop policy origenlab_api_update on platform.operator;

reset role;
