-- Slice 7 — Prospectos: a person's corrections to a prospect finding (DOMAIN.md §7, #66).
--
-- Owner 2026-10-25: «let me edit everything — I found the person's email». A finding is machine
-- evidence and is never rewritten; what a person corrects is recorded beside it and read over it.
--
--   * #66 crm.prospect_edit — one correction to one prospect finding: id, source_record_id →
--     evidence.source_record, changes (a non-empty JSON object whose keys are the editable finding
--     fields; a null value clears the field), edited_by_operator_id → platform.operator, edited_at.
--     Append-only: the reads apply every correction in order over the finding's payload, the later
--     one winning per field. Trigger prospect_edit_append_only reuses platform.reject_mutation. Two
--     unconditional indexes cover both foreign keys.
--   * crm.domain_event: 'source_record.prospect_edited' added to the event_type CHECK (the full
--     list as left by 20261023100000 plus the new type). Aggregate kind 'source_record', unchanged.

set role origenlab_owner;

-- ── #66 crm.prospect_edit ───────────────────────────────────────────────────────────────────
create table crm.prospect_edit (
  id uuid primary key default gen_random_uuid(),
  source_record_id uuid not null references evidence.source_record (id),
  changes jsonb not null,
  edited_by_operator_id uuid not null references platform.operator (id),
  edited_at timestamptz not null default now(),
  constraint prospect_edit_changes_object check (jsonb_typeof(changes) = 'object' and changes <> '{}'::jsonb),
  constraint prospect_edit_changes_keys check (changes - array[
    'person_name', 'role', 'organization_name', 'unit', 'email', 'email_source_url', 'email_source_note',
    'phone', 'website', 'linkedin_person', 'linkedin_org', 'orcid', 'scholar', 'why_now', 'equipment',
    'fund_label', 'amount_label'
  ]::text[] = '{}'::jsonb)
);

comment on table crm.prospect_edit is
  'DOMAIN.md §7 #66 — a person''s correction to one prospect finding (evidence.source_record of kind prospect_finding) in «Prospectos»: the fields they changed, null clearing one. The finding is never rewritten; the reads apply the corrections in order over its payload. Append-only.';

create index prospect_edit_record_idx on crm.prospect_edit (source_record_id, edited_at);
create index prospect_edit_operator_idx on crm.prospect_edit (edited_by_operator_id);

create trigger prospect_edit_append_only
  before update or delete on crm.prospect_edit
  for each row execute function platform.reject_mutation('append-only: a correction is permanent');

-- ── grants ───────────────────────────────────────────────────────────────────────────────────
revoke all on crm.prospect_edit from public, anon, authenticated, service_role;
grant select, insert on crm.prospect_edit to origenlab_api;
grant select on crm.prospect_edit to origenlab_worker;

-- ── RLS ──────────────────────────────────────────────────────────────────────────────────────
alter table crm.prospect_edit enable row level security;

create policy origenlab_api_select on crm.prospect_edit for select to origenlab_api using (true);
create policy origenlab_api_insert on crm.prospect_edit for insert to origenlab_api with check (true);
create policy origenlab_worker_select on crm.prospect_edit for select to origenlab_worker using (true);

-- ── domain events ────────────────────────────────────────────────────────────────────────────
alter table crm.domain_event drop constraint domain_event_type_check;
alter table crm.domain_event add constraint domain_event_type_check check (event_type in (
  'organization.created', 'organization.confirmed', 'organization.merged', 'organization.updated',
  'organization.archived', 'organization.restored', 'organization.identifier_added',
  'organization.identifier_removed', 'organization.domain_added', 'organization.domain_removed',
  'organization.domain_restored', 'organization.product_line_linked',
  'organization.product_line_unlinked', 'person.created', 'person.confirmed', 'person.merged',
  'person.updated', 'person.archived', 'person.restored', 'affiliation.opened',
  'affiliation.closed', 'address.added', 'address.superseded', 'contact_point.created',
  'contact_point.confirmed', 'contact_point.updated', 'contact_point.deactivated',
  'relationship.changed', 'opportunity.created', 'opportunity.staged',
  'opportunity.organization_set', 'opportunity.closed', 'opportunity.stage_corrected',
  'participant.added', 'participant.linked', 'participant.primary_changed', 'participant.ended',
  'case_organization.added', 'case_organization.role_confirmed', 'case_organization.ended',
  'case_interest.added', 'case_evidence.linked', 'case_evidence.unlinked', 'task.created',
  'task.completed', 'task.cancelled', 'activity.linked', 'quote.created',
  'quote_revision.transitioned', 'quote_revision.superseded', 'quote_revision.historical_recorded',
  'campaign.transitioned', 'campaign.approved', 'campaign.override_granted',
  'campaign.dry_run_recorded', 'campaign.draft_created', 'campaign.draft_content_saved',
  'campaign.audience_frozen', 'campaign.planning_set', 'campaign.planning_cleared',
  'campaign_block.placed', 'campaign_block.lifted', 'send_attempt.submission_changed',
  'send_attempt.delivery_changed', 'contact_control.added', 'contact_control.revoked',
  'contact_control.evidence_linked', 'send_control.changed', 'operator.changed',
  'assertion.promoted', 'assertion.unsubscribe_review_opened',
  'assertion.unsubscribe_review_dismissed', 'assertion.supplier_candidate_confirmed',
  'assertion.supplier_candidate_rejected', 'source_record.quarantined',
  'source_record.migration_manifest_recorded', 'source_record.review_noted', 'note.created',
  'note.revised', 'note.archived', 'product.created', 'product.updated',
  'product.content_confirmed', 'product.image_added', 'product.image_updated',
  'product.cost_recorded', 'organization.supplier_terms_set', 'fx_rate.recorded',
  'cost_parameter.set', 'source_record.document_line_reviewed', 'assertion.triage_reviewed',
  'opportunity.document_attached', 'opportunity.document_removed', 'product.document_recorded',
  'product.document_retired', 'organization.price_list_recorded', 'organization.price_list_retired',
  'product.merged',
  'quote_revision.opened', 'quote_revision.draft_saved',
  'product.model_alias_added', 'product.model_alias_retired',
  'bidder.profile_saved', 'bidder.document_added', 'bidder.document_retired',
  'opportunity.bid_preparation_requested', 'opportunity.tender_status_set', 'notice.promoted',
  'notice.reviewed',
  'source_record.prospect_reviewed', 'source_record.prospect_edited'
));

reset role;
