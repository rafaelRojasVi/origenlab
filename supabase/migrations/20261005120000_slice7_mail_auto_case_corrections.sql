-- Slice 7 — email → cases: the machine records, a person corrects (spec 2026-10-05 §2, §4).
--
-- docs/DOMAIN.md §3.6; docs/WORKFLOWS.md §1.1 (the stage machine).
--
-- The rules engine (`apps/api/src/origenlab_api/v2/mail_rules.py`) applies its actions through
-- the existing command handlers and records them as `actor_kind = 'worker'` events — a value the
-- audit stream has carried since Slice 0. Every one of those actions must be undoable by a person,
-- and two of the inverses had no representation at all:
--
-- 1. **Unlinking an evidence link.** `crm.opportunity_evidence` has carried `unlinked_at` /
--    `unlink_reason` (and `origenlab_api`'s UPDATE grant on them) since Slice 3, but no event type
--    could record the act. `case_evidence.unlinked` is added.
--
-- 2. **Taking back a terminal stage the machine set.** `crm.opportunity_stage_guard` refuses every
--    move out of `won`, `lost` and `abandoned` — "never revived; reopening is a new case". That rule
--    stays for every ordinary write. The one exception is a correction: a transaction that has set
--    `origenlab.case_stage_correction` to the case's id may move it from `won` or `lost` back to the
--    exact stage the latest `opportunity.staged` event says it came from, and to nothing else —
--    and only when that event is the machine's (`actor_kind = 'worker'`): a case a person closed
--    is never revived, declaration or not. The
--    command that does it (`correct_case_stage`, API) clears `closed_at`, `close_reason` and the won
--    pair in the same UPDATE, so `opportunity_closed_shape` and `opportunity_won_shape` hold, and
--    writes `opportunity.stage_corrected`. `abandoned` is not correctable here: it is the stage an
--    undo itself uses to discard a machine-created case.
--
--    The setting is a transaction-local custom GUC, settable by any role, so it is a declaration of
--    intent, not an authority: the authority is that the only target it unlocks is the stage the
--    audit stream already records for this very case. It cannot skip a stage, invent one, or touch
--    a case that is not terminal.
--
-- Nothing else changes: no table, no column, no function added (the guard is replaced in place),
-- no grant, no SECURITY DEFINER, no event written by this migration. Tables 45, policies 160,
-- functions 32, SECURITY DEFINER 3 — unchanged.

set role origenlab_owner;

-- ── 1. vocabulary ──────────────────────────────────────────────────────────────────────────────

alter table crm.domain_event drop constraint domain_event_type_check;
alter table crm.domain_event add constraint domain_event_type_check check (event_type in (
  'organization.created', 'organization.confirmed', 'organization.merged',
  'organization.updated', 'organization.archived', 'organization.restored',
  'organization.identifier_added', 'organization.identifier_removed',
  'organization.domain_added', 'organization.domain_removed', 'organization.domain_restored',
  'organization.product_line_linked', 'organization.product_line_unlinked',
  'person.created', 'person.confirmed', 'person.merged',
  'person.updated', 'person.archived', 'person.restored',
  'affiliation.opened', 'affiliation.closed',
  'address.added', 'address.superseded',
  'contact_point.created', 'contact_point.confirmed',
  'contact_point.updated', 'contact_point.deactivated',
  'relationship.changed',
  'opportunity.created', 'opportunity.staged', 'opportunity.organization_set', 'opportunity.closed',
  'opportunity.stage_corrected',
  'participant.added', 'participant.linked', 'participant.primary_changed', 'participant.ended',
  'case_organization.added', 'case_organization.role_confirmed', 'case_organization.ended',
  'case_interest.added',
  'case_evidence.linked', 'case_evidence.unlinked',
  'task.created', 'task.completed', 'task.cancelled',
  'activity.linked',
  'quote.created', 'quote_revision.transitioned', 'quote_revision.superseded',
  'quote_revision.historical_recorded',
  'campaign.transitioned', 'campaign.approved', 'campaign.override_granted', 'campaign.dry_run_recorded',
  'campaign.draft_created', 'campaign.draft_content_saved',
  'campaign.audience_frozen',
  'campaign.planning_set', 'campaign.planning_cleared',
  'campaign_block.placed', 'campaign_block.lifted',
  'send_attempt.submission_changed', 'send_attempt.delivery_changed',
  'contact_control.added', 'contact_control.revoked', 'contact_control.evidence_linked',
  'send_control.changed',
  'operator.changed',
  'assertion.promoted', 'assertion.unsubscribe_review_opened', 'assertion.unsubscribe_review_dismissed',
  'assertion.supplier_candidate_confirmed', 'assertion.supplier_candidate_rejected',
  'source_record.quarantined', 'source_record.migration_manifest_recorded',
  'source_record.review_noted',
  'product.created',
  'note.created', 'note.revised', 'note.archived'
));

-- ── 2. the stage machine, with one correction ──────────────────────────────────────────────────

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

  if new.stage <> old.stage and not (new.stage = any (case old.stage
        when 'lead'        then array['qualifying', 'abandoned', 'lost']
        when 'qualifying'  then array['qualified', 'lead', 'abandoned', 'lost']
        when 'qualified'   then array['quoting', 'qualifying', 'abandoned', 'lost']
        when 'quoting'     then array['negotiating', 'qualified', 'abandoned', 'lost']
        when 'negotiating' then array['won', 'lost', 'quoting', 'abandoned']
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
        'crm.opportunity %: stage ''%'' is terminal and is never revived — reopening is a new case that references this one',
        old.id, old.stage
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
  'WORKFLOWS.md §1.1 — a case opens at `lead`, moves only along the transition table, and is never revived once terminal; the single exception is a declared correction (origenlab.case_stage_correction = the case id) returning `won`/`lost` to the stage its latest opportunity.staged event came from, when that event is a worker (email rules) event. SECURITY INVOKER.';

reset role;
