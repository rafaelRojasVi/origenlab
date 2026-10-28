-- Slice 7 — «people first» (DOMAIN.md §7, #69): crm.person_name, a person's other names.
--
-- The system learns the other names (and, via contact points, addresses) a person writes with from
-- email: a display name on a message or a signature line proposes a name; an operator confirms it.
-- Rows are never deleted: a wrong name is soft-removed (removed_at), and the same name may be
-- proposed again later. The worker reads (matching); the API adds and removes.
--
-- Undoing an automatic merge needs the API to clear crm.person.merged_into_person_id. Checked
-- 2026-10-27: crm.person has only the person_never_deleted trigger (no UPDATE trigger), the API
-- already holds table-level UPDATE and an update policy — so no change to crm.person is needed.

set role origenlab_owner;

create table crm.person_name (
  id uuid primary key default gen_random_uuid(),
  person_id uuid not null references crm.person (id),
  name_display text not null,
  name_norm text not null,
  source text not null,
  confirmation text not null,
  origin_source_record_id uuid references evidence.source_record (id),
  created_at timestamptz not null default now(),
  removed_at timestamptz,
  removed_by_operator_id uuid,
  constraint person_name_source_check check (source in ('display_name', 'signature', 'operator')),
  constraint person_name_confirmation_check check (confirmation in ('machine_proposed', 'confirmed'))
);

comment on table crm.person_name is
  'DOMAIN.md §7 #69 — another name a person is known by (learned from a message display name or signature, or typed by an operator); machine_proposed until confirmed; at most one live row per (person_id, name_norm); never deleted — soft-removed via removed_at.';

create unique index person_name_live_uniq on crm.person_name (person_id, name_norm) where removed_at is null;
create index person_name_person_idx on crm.person_name (person_id);
create index person_name_norm_idx on crm.person_name (name_norm) where removed_at is null;
create index person_name_origin_source_record_idx on crm.person_name (origin_source_record_id)
  where origin_source_record_id is not null;

create trigger person_name_never_deleted
  before delete on crm.person_name
  for each row execute function platform.reject_mutation('never deleted (soft-remove it via removed_at)');

revoke all on crm.person_name from public, anon, authenticated, service_role;
grant select, insert on crm.person_name to origenlab_api;
grant update (removed_at, removed_by_operator_id) on crm.person_name to origenlab_api;
grant select on crm.person_name to origenlab_worker;

alter table crm.person_name enable row level security;

create policy origenlab_api_select on crm.person_name for select to origenlab_api using (true);
create policy origenlab_api_insert on crm.person_name for insert to origenlab_api with check (true);
create policy origenlab_api_update on crm.person_name for update to origenlab_api using (true) with check (true);
create policy origenlab_worker_select on crm.person_name for select to origenlab_worker using (true);

reset role;
