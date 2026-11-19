-- Slice 8 — crm.note.occurred_at: a «Llamada» logged after the fact says when the call happened.
--
-- The owner logs calls from earlier («ayer hablamos con ella»). Until now a call's time was the
-- row's created_at, so a call entered today counted today. One nullable column on crm.note:
--
--   occurred_at   when the call happened, or null = when it was logged (created_at). Only on a
--                 call record, never in the future (five minutes of clock slack). No default and
--                 no backfill: every call that exists today keeps its logged time.
--
-- Readers use the root call's coalesce(occurred_at, created_at), so a revision (a new row) never
-- moves the call's day. A revision copies the column anyway, like the other call columns.
--
--   * How far back a call may be dated is the API's rule (the request body), not the table's:
--     a revision written months later copies the root's time and must still pass the check.
--   * No new grant: origenlab_api already has table-level insert on crm.note; the column-level
--     UPDATE list is unchanged, so the API can never change it afterwards.
--   * crm.note_guard() is replaced so occurred_at is frozen after insert, like the call columns.

set role origenlab_owner;

alter table crm.note
  add column occurred_at timestamptz;

alter table crm.note
  add constraint note_occurred_at_shape check (occurred_at is null or (note_kind = 'llamada') is true),
  add constraint note_occurred_at_not_future check (occurred_at <= created_at + interval '5 minutes');

comment on column crm.note.occurred_at is
  'When a call happened, when it was logged later; null = created_at. Only on a llamada, never in the future. Written once at insert.';

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
       and new.call_next_step is not distinct from old.call_next_step
       and new.occurred_at is not distinct from old.occurred_at then
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
       or new.call_next_step is distinct from old.call_next_step
       or new.occurred_at is distinct from old.occurred_at then
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
  'Trigger guard for crm.note: never deleted; body, author, subject, chain, time and the call columns (note_kind, call_outcome, call_next_step, occurred_at) never change; only status/archive fields move, once, and version advances by exactly one. SECURITY INVOKER.';

reset role;
