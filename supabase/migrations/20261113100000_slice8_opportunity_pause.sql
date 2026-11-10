-- Slice 8 — crm.opportunity.paused_until / pause_reason: «En pausa hasta…» as a state of the case
-- (owner decision 2026-11-10, DOMAIN.md §7.18).
--
-- Until now a pause was nothing but an open task titled «Retomar: <motivo>», due on the day the
-- case should come back (CaseMove, W11). That made the pause invisible to every read that did not
-- parse task titles: the case kept counting as «Enviada» in the pipeline and the goal, the mail
-- rules reconstructed the pause from the task, and when the day came the case surfaced in Hoy as
-- an anonymous overdue task. The owner chose to make it a state of the case itself: a date and a
-- reason, both or neither.
--
--   * `paused_until` (date, Santiago calendar day) and `pause_reason` (text, 1–200 chars): null
--     together = not paused. A case whose `paused_until` is today or earlier is «vuelve de pausa»:
--     still paused until a person resumes it, but due.
--   * One writer: the `set-case-pause` command (apps/api, v2/case_commands.py), which also ends a
--     pause (both to null) and writes one `opportunity.pause_set` event (20261113110000).
--   * No new grant: origenlab_api already has table-level select/insert/update on crm.opportunity
--     and the worker select.
--   * Backfill, once and idempotent: every open case that today holds an open «Retomar: <motivo>»
--     task becomes paused until that task's day with that reason. The task itself stays open: the
--     first `set-case-pause` on the case cancels it with its own `task.cancelled` event, by an
--     operator, so the history stays honest. The versions of the cases are not bumped: nothing a
--     person is looking at changed meaning.

set role origenlab_owner;

alter table crm.opportunity
  add column paused_until date,
  add column pause_reason text;

alter table crm.opportunity
  add constraint opportunity_pause_check check (
    (paused_until is null) = (pause_reason is null)
    and (pause_reason is null or length(btrim(pause_reason)) between 1 and 200)
  );

comment on column crm.opportunity.paused_until is
  '«En pausa hasta…»: the Santiago day the case comes back to Hoy. Null = not paused. Set and ended only by set-case-pause, together with pause_reason. DOMAIN.md §7.18.';
comment on column crm.opportunity.pause_reason is
  'Why the case waits (1–200 chars), set together with paused_until. DOMAIN.md §7.18.';

create index opportunity_paused_until_idx on crm.opportunity (paused_until) where paused_until is not null;

-- Backfill from the legacy representation: the earliest open «Retomar: …» task of each open case.
with legacy as (
  select distinct on (t.opportunity_id)
         t.opportunity_id,
         (t.due_at at time zone 'America/Santiago')::date as paused_until,
         coalesce(nullif(btrim(substr(t.title, length('Retomar:') + 1)), ''), 'Sin motivo') as reason
    from crm.task t
    join crm.opportunity o on o.id = t.opportunity_id
   where t.status = 'open'
     and t.title like 'Retomar:%'
     and o.closed_at is null
   order by t.opportunity_id, t.due_at asc
)
update crm.opportunity o
   set paused_until = l.paused_until,
       pause_reason = left(l.reason, 200),
       updated_at = now()
  from legacy l
 where l.opportunity_id = o.id
   and o.paused_until is null;

reset role;
