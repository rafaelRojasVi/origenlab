-- Slice 8 — crm.note: a note can be a typed «Llamada» record (DOMAIN.md §7 #44, §7.17).
--
-- Calling a customer is a commercial fact the owner wants to count by outcome, so the outcome is a
-- column, not text folded into the body. Three nullable columns on crm.note:
--
--   note_kind       'llamada' or null. Null is an ordinary note: no default, no backfill, so every
--                   note that exists today stays ordinary.
--   call_outcome    contesto, no_contesto, buzon, numero_malo or reunion. Required on a call
--                   record, forbidden otherwise.
--   call_next_step  free text, at most 500 characters, optional, only on a call record.
--
-- The shape check ties them together: either a call record (kind 'llamada' with an outcome) or an
-- ordinary note (all three null). The first branch is `is true` because a null kind makes it null,
-- and a check passes on null: without it an outcome with no kind would slip through.
--
--   * No table, no crm.activity write path: a call is a note.
--   * No new grant: origenlab_api already has table-level select/insert on crm.note (so it may
--     write the three columns when it inserts a note) and the worker select. The column-level
--     UPDATE list (status, archive fields, version, subject_id) is unchanged, so the API can
--     never change them afterwards.
--   * crm.note_guard() is replaced so the three columns are frozen after insert for every role,
--     including the identity-repoint exception merge-people uses (which may move subject_id and
--     nothing else).

set role origenlab_owner;

alter table crm.note
  add column note_kind text,
  add column call_outcome text,
  add column call_next_step text;

alter table crm.note
  add constraint note_kind_check check (note_kind in ('llamada')),
  add constraint note_call_outcome_check check (
    call_outcome in ('contesto', 'no_contesto', 'buzon', 'numero_malo', 'reunion')
  ),
  add constraint note_call_next_step_check check (char_length(call_next_step) <= 500),
  add constraint note_call_shape check (
    (note_kind = 'llamada' and call_outcome is not null) is true
    or (note_kind is null and call_outcome is null and call_next_step is null)
  );

comment on column crm.note.note_kind is
  'llamada = a phone-call record; null = an ordinary note. Written once at insert. DOMAIN.md §7.17.';
comment on column crm.note.call_outcome is
  'How a call ended: contesto, no_contesto, buzon, numero_malo or reunion. Required when note_kind is llamada, null otherwise. Written once at insert.';
comment on column crm.note.call_next_step is
  'The agreed next step after a call (at most 500 characters). Only on a call record; optional. Written once at insert.';

create or replace function crm.note_guard() returns trigger
language plpgsql
set search_path = pg_catalog
as $$
begin
  if tg_op = 'DELETE' then
    raise exception 'crm.note is never deleted; archive it instead' using errcode = 'P0001';
  end if;
  if tg_op = 'UPDATE' then
    -- Identity-repoint exception: merge-people may change subject_id (and subject_kind)
    -- to repoint notes from a merged-away person to the winner. Every other column is
    -- frozen, and version does not advance (the repoint is not a content change).
    if new.subject_id is distinct from old.subject_id
       and new.body = old.body
       and new.author_operator_id = old.author_operator_id
       and new.root_note_id is not distinct from old.root_note_id
       and new.revision_of_note_id is not distinct from old.revision_of_note_id
       and new.revision_no = old.revision_no
       and new.status = old.status
       and new.version = old.version
       and new.note_kind is not distinct from old.note_kind
       and new.call_outcome is not distinct from old.call_outcome
       and new.call_next_step is not distinct from old.call_next_step then
      return new;  -- allow identity repoint; version stays the same
    end if;
    if new.body is distinct from old.body
       or new.author_operator_id is distinct from old.author_operator_id
       or new.subject_kind is distinct from old.subject_kind
       or new.subject_id is distinct from old.subject_id
       or new.root_note_id is distinct from old.root_note_id
       or new.revision_of_note_id is distinct from old.revision_of_note_id
       or new.revision_no is distinct from old.revision_no
       or new.created_at is distinct from old.created_at
       or new.note_kind is distinct from old.note_kind
       or new.call_outcome is distinct from old.call_outcome
       or new.call_next_step is distinct from old.call_next_step then
      raise exception 'crm.note % is written once: revise it with a new row' , old.id using errcode = 'P0001';
    end if;
    if old.status = 'archived' and new.status = 'archived'
       and (new.archived_at, new.archived_by_operator_id, new.archive_reason)
           is distinct from (old.archived_at, old.archived_by_operator_id, old.archive_reason) then
      raise exception 'crm.note % archive record is immutable', old.id using errcode = 'P0001';
    end if;
    if new.version <> old.version + 1 then
      raise exception 'crm.note % version must advance by one', old.id using errcode = 'P0001';
    end if;
  end if;
  return new;
end;
$$;

comment on function crm.note_guard() is
  'Trigger guard for crm.note: never deleted; body, author, subject, chain, time and the call columns (note_kind, call_outcome, call_next_step) never change; only status/archive fields move, once, and version advances by exactly one. SECURITY INVOKER.';

reset role;
