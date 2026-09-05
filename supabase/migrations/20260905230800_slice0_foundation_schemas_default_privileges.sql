-- Slice 0 / M01 — foundation: extension, database privilege, the seven private schemas,
-- the schema boundary and the owner's default privileges.
--
-- docs/ARCHITECTURE.md §3 (schemas), §6 (roles), §6.4 (platform-role boundary);
-- docs/MIGRATION.md §5.2 checks 3-5. Roles come from supabase/roles.sql, which the CLI runs first.
--
-- Convention for every Slice 0 migration: the connection role (locally `postgres`) performs only
-- what the owner cannot, then `set role origenlab_owner` so that every application object is
-- created by, and owned by, origenlab_owner. Each file ends with `reset role`.

-- 1. btree_gist: exclusion constraints over uuid/text equality plus daterange overlap
--    (crm.affiliation, crm.organization_relationship, crm.opportunity_participant). A trusted
--    extension, installed into the Supabase-managed `extensions` schema by the connection role.
create extension if not exists btree_gist with schema extensions;

-- 2. The owner creates the seven schemas, so it needs CREATE on this database.
do $$
begin
  execute format('grant create on database %I to origenlab_owner', current_database());
end
$$;

set role origenlab_owner;

-- 3. Seven private schemas, owned by origenlab_owner. `public` holds nothing and is untouched.
create schema crm;
create schema comms;
create schema outbound;
create schema evidence;
create schema catalog;
create schema procurement;
create schema platform;

comment on schema crm is 'OrigenLab V2 — durable human commercial truth (DOMAIN.md §7 #1-14, #31-32).';
comment on schema comms is 'OrigenLab V2 — provider message evidence (DOMAIN.md §7 #15-18).';
comment on schema outbound is 'OrigenLab V2 — the only send ledger and send safety facts (DOMAIN.md §7 #19-23).';
comment on schema evidence is 'OrigenLab V2 — acquired external records and typed assertions (DOMAIN.md §7 #24-25).';
comment on schema catalog is 'OrigenLab V2 — manufacturer models and supplier price observations (DOMAIN.md §7 #26-27).';
comment on schema procurement is 'OrigenLab V2 — ChileCompra notices (DOMAIN.md §7 #28).';
comment on schema platform is 'OrigenLab V2 — operators and command idempotency (DOMAIN.md §7 #29-30).';

-- 4. Schema boundary (ARCHITECTURE.md §6.4 point 4). An owner-created schema grants nothing to
--    PUBLIC by default; the revocation is explicit so intent is visible and auditable.
revoke all on schema crm, comms, outbound, evidence, catalog, procurement, platform
  from public, anon, authenticated, service_role;

-- The runtime roles may resolve names in the seven schemas. USAGE implies no object privilege;
-- those are granted per table and per verb in the grants migration.
grant usage on schema crm, comms, outbound, evidence, catalog, procurement, platform
  to origenlab_api, origenlab_worker;

-- 5. Default privileges for objects origenlab_owner creates later (ARCHITECTURE.md §6.4 point 5).
--    PostgreSQL's hard-wired defaults grant EXECUTE on functions and USAGE on types to PUBLIC.
--    Per-schema default-privilege entries are purely additive over those hard-wired defaults and
--    cannot revoke them, so the revocation uses the GLOBAL form: it replaces the hard-wired default
--    for every function and type origenlab_owner creates, in any schema (it only ever creates
--    objects in the seven private schemas). Tables and sequences carry no hard-wired PUBLIC
--    privilege, so no entry is needed for them; the tests assert that no default-privilege entry of
--    origenlab_owner grants anything to PUBLIC, anon, authenticated or service_role.
alter default privileges for role origenlab_owner
  revoke all on functions from public, anon, authenticated, service_role;

alter default privileges for role origenlab_owner
  revoke all on types from public, anon, authenticated, service_role;

reset role;
