-- Slice 9 — crm.note.call_outcome gains 'whatsapp': a WhatsApp sent to the client is a contact record.
--
-- The follow-up rhythm's step 3 is a WhatsApp, but only an email or a call that reached the client
-- counted as a touch, so a case whose WhatsApp went out stayed due every day (audit 2026-11-23).
-- A WhatsApp is logged like a call (note_kind 'llamada', outcome 'whatsapp') and counts as a touch.
--
--   * Only the check's value list changes: no column, no grant, no trigger. Every existing row
--     already satisfies the wider check.

set role origenlab_owner;

alter table crm.note
  drop constraint note_call_outcome_check,
  add constraint note_call_outcome_check check (
    call_outcome in ('contesto', 'no_contesto', 'buzon', 'numero_malo', 'reunion', 'whatsapp')
  );

comment on column crm.note.call_outcome is
  'How a contact record ended: contesto, no_contesto, buzon, numero_malo, reunion, or whatsapp (a WhatsApp sent). Required when note_kind is llamada, null otherwise. Written once at insert.';

reset role;
