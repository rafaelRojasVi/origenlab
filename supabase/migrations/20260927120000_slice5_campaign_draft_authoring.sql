-- Slice 5 (first step) — campaign drafts: an operator writes the content of a campaign that
-- has not been frozen yet.
--
-- docs/DOMAIN.md §7 #20; docs/WORKFLOWS.md §W10 (campaign lifecycle); docs/DATA.md §1.1.
--
-- outbound.campaign already carries the draft state and the content columns (subject,
-- body_text, body_html). What it lacks is the one content field every email client shows next
-- to the subject, the rule that stops content moving after the draft stage, and the names of
-- the two durable decisions a draft editor records. This migration adds exactly those:
--
--   1. outbound.campaign.preheader — the inbox preview line. Part of the content: it freezes
--      with subject and bodies, and the freeze fingerprint (content_sha256, still unbuilt)
--      must include it.
--   2. A trigger: subject, preheader, body_text and body_html change only while the row is a
--      draft. From audience_frozen onward the content is what was approved, and the
--      application's own check is not the only thing standing between it and an edit.
--   3. Two audit events, 'campaign.draft_created' and 'campaign.draft_content_saved'.
--
-- What it deliberately does not do: audience criteria stay frozen-or-absent
-- (campaign_audience_criteria_together is unchanged — a draft still records no audience), no
-- recipient row can be written by a draft editor, no send flag moves, no grant widens (the
-- runtime role's table-level UPDATE on outbound.campaign already covers the new column), and
-- no RLS policy relaxes.

set role origenlab_owner;

-- ── 1. preheader ───────────────────────────────────────────────────────────────────────────────
alter table outbound.campaign
  add column preheader text;

alter table outbound.campaign
  add constraint campaign_preheader_nonblank
    check (preheader is null or length(btrim(preheader)) > 0),
  add constraint campaign_preheader_length
    check (preheader is null or length(preheader) <= 255);

comment on column outbound.campaign.preheader is
  'Inbox preview line shown after the subject. Content: editable only while draft, frozen with subject and bodies; the content_sha256 serialization must include it once the freeze command exists.';

-- ── 2. content is editable only while draft ───────────────────────────────────────────────────
create function outbound.campaign_content_draft_only() returns trigger
language plpgsql
set search_path = pg_catalog
as $$
begin
  if old.status <> 'draft'
     and (new.subject    is distinct from old.subject
       or new.preheader  is distinct from old.preheader
       or new.body_text  is distinct from old.body_text
       or new.body_html  is distinct from old.body_html) then
    raise exception 'campaign content is editable only while the campaign is a draft (status is %)', old.status
      using errcode = 'P0001';
  end if;
  return new;
end;
$$;

create trigger campaign_content_draft_only
  before update on outbound.campaign
  for each row execute function outbound.campaign_content_draft_only();

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
  'send_attempt.submission_changed', 'send_attempt.delivery_changed',
  'contact_control.added', 'contact_control.revoked',
  'send_control.changed',
  'operator.changed',
  'assertion.promoted', 'source_record.quarantined', 'source_record.migration_manifest_recorded',
  'source_record.review_noted',
  'product.created'
));

reset role;
