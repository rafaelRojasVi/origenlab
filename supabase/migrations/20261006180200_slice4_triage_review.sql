-- Slice 4 — a person approves, corrects or rejects each mail-triage reading.
--
-- docs/DOMAIN.md §7 #55; docs/ARCHITECTURE.md §8 (D2); docs/OPERATIONS.md §8.11. The triage worker
-- proposes (`evidence.assertion` kind `message_triage`, 20261006180100); an operator decides in
-- Revisión → «Correos (sugerencias)», and every decision is kept so the rules and the prompt can be
-- measured and improved against what people actually said.
--
-- #55 evidence.triage_review — append-only: one row per decision, never updated or deleted. The
-- latest row per assertion is its current verdict; a later decision supersedes, it never rewrites.
--   verdict    approved | corrected | rejected
--   corrected  for `corrected` only: the fields the person changed — any of class, stage, intent,
--              products (an array), with the same vocabularies as the reading
--   note       optional free text, never blank when present
-- The command (`review-triage`, apps/api) writes the row and one `assertion.triage_reviewed`
-- event in one transaction with its receipt. Moving the case is not done here: «Aprobar» on a stage
-- suggestion runs the existing `advance-case-stage` command afterwards, which writes its own event.

set role origenlab_owner;

create table evidence.triage_review (
  id uuid primary key default gen_random_uuid(),
  assertion_id uuid not null references evidence.assertion (id),
  verdict text not null,
  corrected jsonb not null default '{}'::jsonb,
  note text,
  reviewed_by_operator_id uuid not null references platform.operator (id),
  reviewed_at timestamptz not null default now(),
  constraint triage_review_verdict_check check (verdict in ('approved', 'corrected', 'rejected')),
  constraint triage_review_corrected_object check (jsonb_typeof(corrected) = 'object'),
  constraint triage_review_corrected_only_when_corrected check ((verdict = 'corrected') = (corrected <> '{}'::jsonb)),
  constraint triage_review_corrected_keys check (
    corrected - array['class', 'stage', 'intent', 'products'] = '{}'::jsonb),
  constraint triage_review_note_nonblank check (note is null or length(btrim(note)) > 0)
);

comment on table evidence.triage_review is
  'DOMAIN.md §7 #55 — a person''s verdict on one mail-triage reading (message_triage assertion): append-only; approved, corrected (with the corrected fields) or rejected, with an optional note.';

-- Foreign-key covering indexes (supabase/tests/090_foreign_key_indexes.sql). assertion_id leads the
-- read path too: an assertion's verdicts, newest first.
create index triage_review_assertion_idx on evidence.triage_review (assertion_id, reviewed_at desc);
create index triage_review_operator_idx on evidence.triage_review (reviewed_by_operator_id);

-- The API records decisions; the worker reads them (the evaluation of its own readings). Nobody
-- updates or deletes one.
grant select, insert on evidence.triage_review to origenlab_api;
grant select on evidence.triage_review to origenlab_worker;

alter table evidence.triage_review enable row level security;
create policy origenlab_api_select    on evidence.triage_review for select to origenlab_api using (true);
create policy origenlab_api_insert    on evidence.triage_review for insert to origenlab_api with check (true);
create policy origenlab_worker_select on evidence.triage_review for select to origenlab_worker using (true);

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
  'note.created', 'note.revised', 'note.archived',
  'product.created', 'product.updated', 'product.content_confirmed', 'product.image_added', 'product.image_updated',
  'product.cost_recorded',
  'organization.supplier_terms_set',
  'fx_rate.recorded', 'cost_parameter.set', 'source_record.document_line_reviewed',
  'assertion.triage_reviewed'
));

reset role;
