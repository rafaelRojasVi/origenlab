-- Slice 7 — catalog.product_document (DOMAIN.md §7 #57): where a product's own data sheet, or a
-- supplier's price list, lives in Google Drive. Spec 2026-10-13 (Catálogo v2).
--
-- Only the Drive file id is stored; the reader builds the view URL. No bytes are kept: the
-- dashboard opens the file in Drive as contacto@ (ARCHITECTURE §7 — no file bodies through the
-- proxy). A price list carries dealer costs, so the API redacts it for viewers like any cost.
-- Never deleted: a superseded list or sheet is retired (status). Rows written by the importer
-- carry the manifest that wrote them (origin_source_record_id), as catalog.supplier_product does.
--
-- Events: product.document_recorded / product.document_retired. The list below is the live CHECK
-- (last rebuilt by 20261012120000) plus those two.

set role origenlab_owner;

create table catalog.product_document (
  id uuid primary key default gen_random_uuid(),
  product_id uuid references catalog.product (id),
  supplier_organization_id uuid references crm.organization (id),
  kind text not null,
  title text not null,
  drive_file_id text not null,
  mime_type text not null,
  as_of date,
  status text not null default 'active',
  source text not null,
  origin_source_record_id uuid references evidence.source_record (id),
  created_by_operator_id uuid references platform.operator (id),
  version integer not null default 1,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint product_document_kind_check check (kind in ('datasheet', 'price_list')),
  constraint product_document_subject_shape check (
    (kind = 'datasheet' and product_id is not null and supplier_organization_id is null)
    or (kind = 'price_list' and supplier_organization_id is not null and product_id is null)
  ),
  constraint product_document_title_shape check (length(btrim(title)) between 1 and 255),
  constraint product_document_drive_file_id_shape check (drive_file_id ~ '^[A-Za-z0-9_-]{10,100}$'),
  constraint product_document_mime_type_shape check (length(btrim(mime_type)) between 3 and 255),
  constraint product_document_status_check check (status in ('active', 'retired')),
  constraint product_document_source_check check (source in ('drive_manifest', 'operator')),
  constraint product_document_version_positive check (version >= 1)
);

comment on table catalog.product_document is
  'DOMAIN.md §7 #57 — a Google Drive file that documents the catalog: a product''s own data sheet (kind datasheet, product_id) or a supplier''s price list (kind price_list, supplier_organization_id; carries dealer costs, redacted for viewers). Only the Drive file id is stored. Never deleted: retired via status.';

create unique index product_document_one_datasheet_file
  on catalog.product_document (product_id, drive_file_id) where kind = 'datasheet';
create unique index product_document_one_price_list_file
  on catalog.product_document (supplier_organization_id, drive_file_id) where kind = 'price_list';
create index product_document_product_idx on catalog.product_document (product_id);
create index product_document_supplier_idx on catalog.product_document (supplier_organization_id);
create index product_document_origin_idx on catalog.product_document (origin_source_record_id);
create index product_document_created_by_idx on catalog.product_document (created_by_operator_id);

create trigger product_document_never_deleted
  before delete on catalog.product_document
  for each row execute function platform.reject_mutation('never deleted (retire it via status)');

-- The API records and retires; the worker never touches it; no runtime role deletes.
grant select, insert on catalog.product_document to origenlab_api;
grant update (status, version, updated_at) on catalog.product_document to origenlab_api;

alter table catalog.product_document enable row level security;
create policy origenlab_api_select on catalog.product_document for select to origenlab_api using (true);
create policy origenlab_api_insert on catalog.product_document for insert to origenlab_api with check (true);
create policy origenlab_api_update on catalog.product_document for update to origenlab_api using (true) with check (true);

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
  'product.document_retired'
));

reset role;
