-- Slice 7 — a closed case reopens as the same case (owner decision 2026-10-10, STATUS §2.7.90).
--
-- Until now `lost` and `abandoned` were terminal and «reopening» meant a new case referencing the
-- old one (WORKFLOWS §1.1). In practice that lost the quotes and made a wrong close expensive to
-- undo: a client who wrote to pay a deposit on a case closed a day early needed a new case, the
-- quote registered again, then the win. The owner chose the simpler rule: a `lost` or `abandoned`
-- case may go back to any open stage as the same case, keeping its quotes, links and history;
-- the move is an ordinary `opportunity.staged` event. Unchanged: a case opens at `lead`; `won`
-- stays terminal (only the declared correction of 20261005120000 takes back a machine's win);
-- the institution rule from `qualified` on still holds (a CHECK, not this trigger); and a case an
-- undo discarded (`close_reason = 'discarded_by_correction'`) is never reopened.

set role origenlab_owner;

create or replace function crm.opportunity_stage_guard() returns trigger
language plpgsql
set search_path = pg_catalog
as $$
declare
  v_came_from text;
  v_actor text;
begin
  if tg_op = 'INSERT' then
    if new.stage <> 'lead' then
      raise exception
        'crm.opportunity: a case is opened at stage ''lead'' and moves from there; % is not an opening stage',
        new.stage
        using errcode = 'P0001';
    end if;
    return new;
  end if;

  -- A case an undo discarded (`discarded_by_correction`) was never a case: it is not reopened.
  -- Its email opens a new one instead.
  if new.stage <> old.stage and old.stage = 'abandoned' and old.close_reason = 'discarded_by_correction' then
    raise exception
      'crm.opportunity %: this case was discarded by a correction and is not reopened; open a new case from the email',
      old.id
      using errcode = 'P0001';
  end if;

  if new.stage <> old.stage and not (new.stage = any (case old.stage
        when 'lead'        then array['qualifying', 'abandoned', 'lost']
        when 'qualifying'  then array['qualified', 'lead', 'abandoned', 'lost']
        when 'qualified'   then array['quoting', 'qualifying', 'abandoned', 'lost']
        when 'quoting'     then array['negotiating', 'qualified', 'abandoned', 'lost']
        when 'negotiating' then array['won', 'lost', 'quoting', 'abandoned']
        -- Reopening (owner decision 2026-10-10): a lost or abandoned case goes back to any open
        -- stage as the SAME case, keeping its quotes and history. `won` stays terminal: taking
        -- back a win is the correction below, never a stage move.
        when 'lost'        then array['lead', 'qualifying', 'qualified', 'quoting', 'negotiating']
        when 'abandoned'   then array['lead', 'qualifying', 'qualified', 'quoting', 'negotiating']
        else array[]::text[]
      end))
  then
    -- The one way out of `won` or `lost`: a declared correction of this very case, back to the
    -- stage the audit stream says it came from (spec 2026-10-05 §4).
    if old.stage in ('won', 'lost')
       and coalesce(current_setting('origenlab.case_stage_correction', true), '') = old.id::text
       and new.stage not in ('won', 'lost', 'abandoned')
    then
      select e.payload ->> 'from_stage', e.actor_kind into v_came_from, v_actor
        from crm.domain_event e
       where e.aggregate_kind = 'opportunity'
         and e.aggregate_id = old.id
         and e.event_type = 'opportunity.staged'
         and e.payload ->> 'to_stage' = old.stage
       order by e.seq desc
       limit 1;
      if v_actor is distinct from 'worker' then
        raise exception
          'crm.opportunity %: only a stage the email rules set (a worker event) is corrected; a person closed this one',
          old.id
          using errcode = 'P0001';
      end if;
      if v_came_from is not null and v_came_from = new.stage then
        return new;
      end if;
      raise exception
        'crm.opportunity %: a correction returns ''%'' only to the stage it came from (%), not ''%''',
        old.id, old.stage, coalesce(v_came_from, 'unknown'), new.stage
        using errcode = 'P0001';
    end if;
    if old.stage in ('won', 'lost', 'abandoned') then
      raise exception
        'crm.opportunity %: stage ''%'' does not move to ''%'': a closed case reopens only to an open stage, and a won case only by a correction',
        old.id, old.stage, new.stage
        using errcode = 'P0001';
    end if;
    raise exception
      'crm.opportunity %: ''%'' → ''%'' is not an allowed transition (WORKFLOWS.md §1.1)',
      old.id, old.stage, new.stage
      using errcode = 'P0001';
  end if;
  return new;
end
$$;

comment on function crm.opportunity_stage_guard() is
  'WORKFLOWS.md §1.1 — a case opens at `lead` and moves only along the transition table. `lost` and `abandoned` reopen to any open stage as the same case (2026-10-10), except a case an undo discarded (discarded_by_correction). `won` is terminal; the single exception is a declared correction (origenlab.case_stage_correction = the case id) returning `won`/`lost` to the stage its latest opportunity.staged event came from, when that event is a worker (email rules) event. SECURITY INVOKER.';

reset role;
