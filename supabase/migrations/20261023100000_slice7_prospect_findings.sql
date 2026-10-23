-- Slice 7 — Prospectos fase 1: prospect findings and the human verdict on them (DOMAIN.md §7, #65).
--
-- Owner 2026-10-23 (spec «Prospectos»): a worker loader — later the daily search — records every
-- prospect it finds as machine evidence; Tatiana reviews them in «Prospectos» and records a verdict.
-- New sources enter through evidence and promotion (DOMAIN.md §5): no prospect identity table.
--
--   * evidence.source_record.kind gains 'prospect_finding' (the full list as left by
--     20261014140000 plus the new kind). The worker already inserts source records and
--     assertions (slice 0 runtime grants); the API's update stays column-scoped. No grant change.
--   * #65 crm.prospect_review — the human verdict on one prospect finding: id, source_record_id →
--     evidence.source_record, verdict (contactar | mas_tarde | descartar | reabrir), reason (null or
--     1–500 chars, trimmed), remind_on (required for mas_tarde, null otherwise),
--     reviewed_by_operator_id → platform.operator, reviewed_at. Append-only: the latest verdict per
--     finding is the current one; «Deshacer» after descartar is a new reabrir row. Trigger
--     prospect_review_append_only reuses platform.reject_mutation. Two unconditional indexes cover
--     both foreign keys.
--   * crm.domain_event: 'source_record.prospect_reviewed' added to the event_type CHECK (the full
--     list as left by 20261020180000 plus the new type). The aggregate kind 'source_record' and its
--     mapping in crm.domain_event_is_valid are unchanged — split_part routes it.

set role origenlab_owner;

-- ── evidence.source_record.kind ─────────────────────────────────────────────────────────────
alter table evidence.source_record drop constraint source_record_kind_check;
alter table evidence.source_record add constraint source_record_kind_check check (kind in (
  'workbook_import', 'chilecompra_notice', 'migration_manifest',
  'v1_parse_failure', 'v1_evidence_edge', 'v1_supplier_candidate', 'v1_historical_quote_candidate',
  'gmail_message', 'drive_file', 'quote_document', 'forwarded_request', 'prospect_finding'
));

comment on column evidence.source_record.kind is
  'Closed acquisition vocabulary: where this record came from. gmail_message, drive_file, quote_document, forwarded_request and prospect_finding are staged, never-trusted observations — they arrive review_status = ''pending'' and reach crm.* only through operator-reviewed promotion (docs/DOMAIN.md §5). A quote_document carries its lines in evidence.document_line (#54); a forwarded_request carries only the customer fields the worker read out of a forward we sent to contacto@, never its text.';

-- ── #65 crm.prospect_review ──────────────────────────────────────────────────────────────────
create table crm.prospect_review (
  id uuid primary key default gen_random_uuid(),
  source_record_id uuid not null references evidence.source_record (id),
  verdict text not null,
  reason text,
  remind_on date,
  reviewed_by_operator_id uuid not null references platform.operator (id),
  reviewed_at timestamptz not null default now(),
  constraint prospect_review_verdict_check check (verdict in ('contactar', 'mas_tarde', 'descartar', 'reabrir')),
  constraint prospect_review_reason_shape check (reason is null or length(btrim(reason)) between 1 and 500),
  constraint prospect_review_remind_on_shape check ((verdict = 'mas_tarde') = (remind_on is not null))
);

comment on table crm.prospect_review is
  'DOMAIN.md §7 #65 — a person''s verdict on one prospect finding (evidence.source_record of kind prospect_finding) in «Prospectos»: contactar (promoted into the CRM), mas_tarde (until remind_on), descartar (never shown again) or reabrir (back to review). Append-only: the latest row per finding is the current verdict.';

create index prospect_review_record_idx on crm.prospect_review (source_record_id, reviewed_at desc);
create index prospect_review_operator_idx on crm.prospect_review (reviewed_by_operator_id);

create trigger prospect_review_append_only
  before update or delete on crm.prospect_review
  for each row execute function platform.reject_mutation('append-only: a verdict is permanent');

-- ── grants ───────────────────────────────────────────────────────────────────────────────────
revoke all on crm.prospect_review from public, anon, authenticated, service_role;
grant select, insert on crm.prospect_review to origenlab_api;
grant select on crm.prospect_review to origenlab_worker;

-- ── RLS ──────────────────────────────────────────────────────────────────────────────────────
alter table crm.prospect_review enable row level security;

create policy origenlab_api_select on crm.prospect_review for select to origenlab_api using (true);
create policy origenlab_api_insert on crm.prospect_review for insert to origenlab_api with check (true);
create policy origenlab_worker_select on crm.prospect_review for select to origenlab_worker using (true);

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
  'source_record.prospect_reviewed'
));

reset role;
