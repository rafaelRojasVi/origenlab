-- Slice 7 — evidence.subject_memory (#58): a running machine memory per case, per institution and
-- per person, updated event-driven each time an email is read by the triage worker.
--
-- This is a machine projection, not durable truth. Its job is to give the context matcher (layer 2,
-- case_match_model.py) and the triage model (triage_model.py) a compact summary of what has happened
-- in a case, institution or person's history so far — without reading the whole thread every time.
--
-- Design:
--   subject_kind + subject_id   identify whose memory this is (opportunity, organization or person).
--   subject_id is a logical UUID — NO foreign key, because the subjects live in crm.* and this is a
--   rebuildable machine projection: the repo rule «no durable FK into a rebuildable projection»
--   applies in reverse. The row survives even when the CRM row is removed or replaced.
--   version                     monotone counter: each new reading appends version+1.
--   memory                      the compact JSON summary (shape documented in DOMAIN.md §7 #58).
--   source_record_id            the gmail_message source record that produced this version (nullable:
--                               the backfill seed pass may not have one).
--   model                       the model id that wrote this version, for evaluation.
--
-- Append-only: no operator or role ever updates or deletes a row. The latest version per subject is
-- its current memory; an earlier version is its history.
--
-- The unique constraint (subject_kind, subject_id, version) prevents a race that writes the same
-- version twice; the writer reads max(version) inside the same transaction and inserts version+1.
--
-- Grants:
--   origenlab_worker  insert + select (writes new versions during triage; reads to seed prompts)
--   origenlab_api     select (the /v2/workspace/cases/{id}/memory route)
--   No other role reads or writes.
--
-- Domain events: no new event types — the memory is a rebuildable projection (never a durable
-- command outcome). ARCHITECTURE.md §8, near the case_match:v1 paragraph.

set role origenlab_owner;

create table evidence.subject_memory (
  id                uuid       primary key default gen_random_uuid(),
  subject_kind      text       not null,
  subject_id        uuid       not null,
  version           int        not null,
  memory            jsonb      not null,
  source_record_id  uuid       references evidence.source_record (id),
  model             text,
  created_at        timestamptz not null default now(),

  constraint subject_memory_kind_check check (
    subject_kind in ('opportunity', 'organization', 'person')
  ),
  constraint subject_memory_version_positive check (version >= 1),
  constraint subject_memory_kind_subject unique (subject_kind, subject_id, version),
  constraint subject_memory_memory_object check (jsonb_typeof(memory) = 'object')
);

comment on table evidence.subject_memory is
  'DOMAIN.md §7 #58 — a machine-written running memory per case, institution or person, append-only; each new email appends version+1. Rebuildable projection: subject_id carries no FK (the subjects live in crm.*). The latest version is the current memory; earlier versions are history.';

-- Covering index for the worker's reads: latest memory for a subject.
create index subject_memory_subject_idx
  on evidence.subject_memory (subject_kind, subject_id, version desc);

-- FK covering index (supabase/tests/090_foreign_key_indexes.sql).
create index subject_memory_source_record_idx
  on evidence.subject_memory (source_record_id);

-- Append-only: no UPDATE, no DELETE ever — reads the latest version, inserts the next.
create trigger subject_memory_never_updated
  before update on evidence.subject_memory
  for each row execute function platform.reject_mutation('append-only: insert version+1 instead of updating');

create trigger subject_memory_never_deleted
  before delete on evidence.subject_memory
  for each row execute function platform.reject_mutation('append-only: rows are never deleted');

-- The worker writes and reads; the API reads; no other role touches this table.
grant select, insert on evidence.subject_memory to origenlab_worker;
grant select            on evidence.subject_memory to origenlab_api;

alter table evidence.subject_memory enable row level security;
create policy origenlab_worker_select on evidence.subject_memory for select to origenlab_worker using (true);
create policy origenlab_worker_insert on evidence.subject_memory for insert to origenlab_worker with check (true);
create policy origenlab_api_select    on evidence.subject_memory for select to origenlab_api    using (true);

reset role;
