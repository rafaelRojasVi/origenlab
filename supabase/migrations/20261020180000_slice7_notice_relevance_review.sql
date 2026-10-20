-- Slice 7 — Licitaciones fase 2: relevance on notice and human verdict review (DOMAIN.md §7.12, #64).
--
-- Owner 2026-10-20 (spec «Licitaciones con IA»): the worker matches every open ChileCompra notice
-- against the catalog and writes a machine verdict — tier, matching product families, terms, items
-- and the version + timestamp of the check — into procurement.notice.relevance (nullable jsonb,
-- object). The API shows «En Mercado Público» from that column; the operator glances at the
-- machine's match and records a single human verdict on it. This migration:
--
--   * procurement.notice gains `relevance` (nullable jsonb, must be an object when non-null),
--     the worker's machine match written at scan time. The worker already holds table-level update
--     on procurement.notice (slice 0 runtime grants); the API's update grant is column-scoped and
--     does not include relevance — no grant change needed.
--   * #64 crm.notice_review — the human verdict on one Mercado Público notice: id, notice_id →
--     procurement.notice, verdict (interesa | no_es_nuestro), reason (null or 1–500 chars, trimmed),
--     reviewed_by_operator_id → platform.operator, reviewed_at. Append-only: trigger
--     notice_review_append_only reuses the existing platform.reject_mutation function (same pattern
--     as crm.bidder_profile, crm.bidder_document and platform.model_usage). Two unconditional
--     indexes cover both foreign keys.
--   * crm.domain_event: 'notice.reviewed' added to the event_type CHECK (the current full list as
--     left by 20261020100000 plus 'notice.reviewed'). The aggregate kind 'notice' and its mapping
--     in crm.domain_event_is_valid are unchanged — split_part('notice.reviewed', '.', 1) = 'notice'
--     already routes correctly.

set role origenlab_owner;

-- ── procurement.notice: relevance ───────────────────────────────────────────────────────────
alter table procurement.notice
  add column relevance jsonb,
  add constraint notice_relevance_object check (relevance is null or jsonb_typeof(relevance) = 'object');

comment on column procurement.notice.relevance is
  'The worker''s machine match of this notice for «En Mercado Público»: tier, families, terms, items, version and checked_at. Machine evidence, rebuildable, never a human decision.';

-- ── #64 crm.notice_review ────────────────────────────────────────────────────────────────────
create table crm.notice_review (
  id uuid primary key default gen_random_uuid(),
  notice_id uuid not null references procurement.notice (id),
  verdict text not null,
  reason text,
  reviewed_by_operator_id uuid not null references platform.operator (id),
  reviewed_at timestamptz not null default now(),
  constraint notice_review_verdict_check check (verdict in ('interesa', 'no_es_nuestro')),
  constraint notice_review_reason_shape check (reason is null or length(btrim(reason)) between 1 and 500)
);

comment on table crm.notice_review is
  'DOMAIN.md §7 #64 — a person''s verdict on one Mercado Público notice in «En Mercado Público»: interesa (worth pursuing) or no_es_nuestro. Append-only: a change is a new row; each verdict is a permanent record.';

create index notice_review_notice_idx on crm.notice_review (notice_id, reviewed_at desc);
create index notice_review_operator_idx on crm.notice_review (reviewed_by_operator_id);

create trigger notice_review_append_only
  before update or delete on crm.notice_review
  for each row execute function platform.reject_mutation('append-only: a verdict is permanent');

-- ── grants ───────────────────────────────────────────────────────────────────────────────────
-- The worker already holds table-level update on procurement.notice (slice 0 grants); no change.
-- The API's update on procurement.notice is column-scoped and excludes relevance; no change.
revoke all on crm.notice_review from public, anon, authenticated, service_role;
grant select, insert on crm.notice_review to origenlab_api;
grant select on crm.notice_review to origenlab_worker;

-- ── RLS ──────────────────────────────────────────────────────────────────────────────────────
alter table crm.notice_review enable row level security;

create policy origenlab_api_select on crm.notice_review for select to origenlab_api using (true);
create policy origenlab_api_insert on crm.notice_review for insert to origenlab_api with check (true);
create policy origenlab_worker_select on crm.notice_review for select to origenlab_worker using (true);

-- ── domain events ────────────────────────────────────────────────────────────────────────────
-- The aggregate kind 'notice' is already in domain_event_aggregate_kind_check (20261020100000);
-- crm.domain_event_is_valid already maps it. Only the event_type list gains 'notice.reviewed'.
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
  'notice.reviewed'
));

reset role;
