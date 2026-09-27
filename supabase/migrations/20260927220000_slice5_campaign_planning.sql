-- Slice 5 — campaign planning: an internal date (and optional time) an operator intends to
-- send a campaign on. Planning metadata only.
--
-- docs/DOMAIN.md §7 #20; docs/WORKFLOWS.md §W4 (the campaign lifecycle).
--
-- outbound.campaign has no field that says "we intend to send this on the 14th". The calendar in
-- the CRM Marketing section needs one, and it must be impossible to mistake for a schedule:
-- nothing reads it to reserve, enqueue or send, and no send path exists to read it anyway. This
-- migration adds exactly that and nothing more:
--
--   1. outbound.campaign.planned_for_date — the calendar day in America/Santiago (a date, not an
--      instant: "the 14th" is a day in Chile, whatever the server's zone).
--      outbound.campaign.planned_for_at — the instant, in UTC, when the operator also chose a time.
--      outbound.campaign.planning_version — the compare-and-set token of the planning command,
--      deliberately separate from `version`: a planning change is not a content version, and
--      a frozen campaign's `version` is what its recipient snapshot names.
--   2. A trigger: planning moves only while the campaign is `draft` or `audience_frozen`, never in
--      the same statement as a status change, and a time must fall on the planned day in
--      America/Santiago. Planning cannot approve, freeze or send, because the statement that
--      changes it cannot change anything that would.
--   3. Two audit events, 'campaign.planning_set' and 'campaign.planning_cleared'.
--
-- What it deliberately does not do: no send flag moves, no grant widens (the runtime role's
-- table-level UPDATE on outbound.campaign already covers the new columns), no RLS policy relaxes,
-- and no function reads the planned date.

set role origenlab_owner;

-- ── 1. planning columns ───────────────────────────────────────────────────────────────────────
alter table outbound.campaign
  add column planned_for_date date,
  add column planned_for_at timestamptz,
  add column planning_version integer not null default 0;

alter table outbound.campaign
  add constraint campaign_planned_time_needs_date
    check (planned_for_at is null or planned_for_date is not null),
  add constraint campaign_planning_version_nonnegative
    check (planning_version >= 0);

comment on column outbound.campaign.planned_for_date is
  'Internal planning only: the day (America/Santiago) an operator intends to send. Never read by any send path; it schedules nothing.';
comment on column outbound.campaign.planned_for_at is
  'Internal planning only: the planned instant (UTC) when a time was chosen; falls on planned_for_date in America/Santiago. Schedules nothing.';
comment on column outbound.campaign.planning_version is
  'Compare-and-set token of set-campaign-planning; separate from the content version.';

-- ── 2. planning moves only on an unsent campaign, and alone ───────────────────────────────────
create function outbound.campaign_planning_guard() returns trigger
language plpgsql
set search_path = pg_catalog
as $$
begin
  if new.planned_for_date   is distinct from old.planned_for_date
     or new.planned_for_at  is distinct from old.planned_for_at
     or new.planning_version is distinct from old.planning_version then
    if old.status not in ('draft', 'audience_frozen') then
      raise exception 'a campaign is planned only while draft or audience_frozen (status is %)', old.status
        using errcode = 'P0001';
    end if;
    if new.status is distinct from old.status then
      raise exception 'planning never changes a campaign''s status in the same statement'
        using errcode = 'P0001';
    end if;
    if new.planned_for_at is not null
       and (new.planned_for_at at time zone 'America/Santiago')::date <> new.planned_for_date then
      raise exception 'the planned time must fall on the planned day in America/Santiago'
        using errcode = 'P0001';
    end if;
  end if;
  return new;
end;
$$;

create trigger campaign_planning_guard
  before update on outbound.campaign
  for each row execute function outbound.campaign_planning_guard();

-- A campaign is born unplanned: planning is recorded by the command, with its event.
create function outbound.campaign_planning_absent_at_insert() returns trigger
language plpgsql
set search_path = pg_catalog
as $$
begin
  if new.planned_for_date is not null or new.planned_for_at is not null or new.planning_version <> 0 then
    raise exception 'a new campaign carries no planning; set-campaign-planning records it'
      using errcode = 'P0001';
  end if;
  return new;
end;
$$;

create trigger campaign_planning_absent_at_insert
  before insert on outbound.campaign
  for each row execute function outbound.campaign_planning_absent_at_insert();

-- ── 3. audit vocabulary ───────────────────────────────────────────────────────────────────────
alter table crm.domain_event drop constraint domain_event_type_check;

alter table crm.domain_event add constraint domain_event_type_check check (event_type in (
  'organization.created', 'organization.confirmed', 'organization.merged',
  'person.created', 'person.confirmed', 'person.merged',
  'affiliation.opened', 'affiliation.closed',
  'address.added', 'address.superseded',
  'contact_point.created', 'contact_point.confirmed',
  'relationship.changed',
  'opportunity.created', 'opportunity.staged', 'opportunity.organization_set', 'opportunity.closed',
  'participant.added', 'participant.linked', 'participant.primary_changed', 'participant.ended',
  'case_organization.added', 'case_organization.role_confirmed', 'case_organization.ended',
  'case_interest.added',
  'case_evidence.linked',
  'task.created', 'task.completed', 'task.cancelled',
  'activity.linked',
  'quote.created', 'quote_revision.transitioned', 'quote_revision.superseded',
  'quote_revision.historical_recorded',
  'campaign.transitioned', 'campaign.approved', 'campaign.override_granted', 'campaign.dry_run_recorded',
  'campaign.draft_created', 'campaign.draft_content_saved',
  'campaign.audience_frozen',
  'campaign.planning_set', 'campaign.planning_cleared',
  'send_attempt.submission_changed', 'send_attempt.delivery_changed',
  'contact_control.added', 'contact_control.revoked',
  'send_control.changed',
  'operator.changed',
  'assertion.promoted', 'source_record.quarantined', 'source_record.migration_manifest_recorded',
  'source_record.review_noted',
  'product.created'
));

reset role;
