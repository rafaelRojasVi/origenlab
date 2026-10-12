-- Slice 7 — a case's documents: the purchase order (OC) that closes it, stored privately.
--
-- Owner request 2026-10-12: «en ganada attach the oc too, upload it to the database so it's there
-- with the cotización». Table #56 `crm.opportunity_document` records a file a person attached to a
-- case — today only `purchase_order` — with the quote revision it closes when known. The bytes
-- live in the private Storage bucket `crm-documents` (ARCHITECTURE §7), under a content-addressed
-- path; this row is what the file is, who attached it, from where and when.
--
-- Two sources, one shape: `upload` (a file from the operator's computer) and `email` (an attachment
-- of a captured Gmail message, `source_record_id`, copied by the API from the message's `.eml` after
-- its SHA-256 matched). Append-only in substance: nothing about a document is rewritten; a wrong
-- attachment is removed once (removed_at, its reason and who), never deleted.
--
-- Events: `opportunity.document_attached` and `opportunity.document_removed` on the case. The list
-- below is the live CHECK (91 types, last rebuilt by 20261006180200) plus those two.

set role origenlab_owner;

create table crm.opportunity_document (
  id uuid primary key default gen_random_uuid(),
  opportunity_id uuid not null references crm.opportunity (id),
  quote_revision_id uuid references crm.quote_revision (id),
  kind text not null,
  document_number text,
  storage_bucket text not null default 'crm-documents',
  storage_path text not null,
  sha256 text not null,
  content_type text not null,
  byte_size integer not null,
  original_filename text not null,
  source text not null,
  source_record_id uuid references evidence.source_record (id),
  attached_by_operator_id uuid not null references platform.operator (id),
  attached_at timestamptz not null default now(),
  removed_at timestamptz,
  removed_by_operator_id uuid references platform.operator (id),
  remove_reason text,
  created_at timestamptz not null default now(),
  constraint opportunity_document_kind_check check (kind in ('purchase_order')),
  constraint opportunity_document_number_shape check (
    document_number is null or (length(btrim(document_number)) between 1 and 64)
  ),
  constraint opportunity_document_bucket_check check (storage_bucket = 'crm-documents'),
  constraint opportunity_document_path_shape check (
    storage_path ~ '^opportunities/[0-9a-f-]{36}/[0-9a-f]{64}\.(pdf|jpg|png)$'
  ),
  constraint opportunity_document_sha256_shape check (sha256 ~ '^[0-9a-f]{64}$'),
  constraint opportunity_document_content_type_check check (
    content_type in ('application/pdf', 'image/jpeg', 'image/png')
  ),
  constraint opportunity_document_size_check check (byte_size between 1 and 20971520),
  constraint opportunity_document_filename_shape check (
    length(btrim(original_filename)) between 1 and 255
  ),
  constraint opportunity_document_source_check check (source in ('upload', 'email')),
  constraint opportunity_document_source_shape check (
    (source = 'email') = (source_record_id is not null)
  ),
  constraint opportunity_document_removal_shape check (
    (removed_at is null) = (removed_by_operator_id is null)
    and (removed_at is null) = (remove_reason is null)
  ),
  constraint opportunity_document_remove_reason_nonblank check (
    remove_reason is null or length(btrim(remove_reason)) > 0
  )
);

comment on table crm.opportunity_document is
  'DOMAIN.md §7 #56 — a document a person attached to a commercial case: today the purchase order (kind purchase_order) that closes it, with the quote revision it closes when known. Bytes in the private Storage bucket crm-documents under opportunities/<case>/<sha256>.<ext>; source upload (from a computer) or email (an attachment of source_record_id, copied after its SHA-256 matched). Never deleted, never rewritten: removal sets removed_at, its reason and who, once.';

-- One live copy of the same bytes per case and kind; a removed one may be attached again.
create unique index opportunity_document_one_live_copy
  on crm.opportunity_document (opportunity_id, kind, sha256) where removed_at is null;
create index opportunity_document_opportunity_idx on crm.opportunity_document (opportunity_id);
create index opportunity_document_quote_revision_idx on crm.opportunity_document (quote_revision_id);
create index opportunity_document_source_record_idx on crm.opportunity_document (source_record_id);
create index opportunity_document_attached_by_idx on crm.opportunity_document (attached_by_operator_id);
create index opportunity_document_removed_by_idx on crm.opportunity_document (removed_by_operator_id);

-- Append-only in substance: only the removal columns change, and only once.
create function crm.opportunity_document_immutable() returns trigger
language plpgsql
set search_path = pg_catalog
as $$
begin
  if new.id is distinct from old.id
     or new.opportunity_id is distinct from old.opportunity_id
     or new.quote_revision_id is distinct from old.quote_revision_id
     or new.kind is distinct from old.kind
     or new.document_number is distinct from old.document_number
     or new.storage_bucket is distinct from old.storage_bucket
     or new.storage_path is distinct from old.storage_path
     or new.sha256 is distinct from old.sha256
     or new.content_type is distinct from old.content_type
     or new.byte_size is distinct from old.byte_size
     or new.original_filename is distinct from old.original_filename
     or new.source is distinct from old.source
     or new.source_record_id is distinct from old.source_record_id
     or new.attached_by_operator_id is distinct from old.attached_by_operator_id
     or new.attached_at is distinct from old.attached_at
     or new.created_at is distinct from old.created_at
  then
    raise exception
      'crm.opportunity_document: a document is never rewritten — remove it and attach the right one'
      using errcode = 'P0001';
  end if;
  if old.removed_at is not null then
    raise exception
      'crm.opportunity_document: a removed document stays removed — attach it again instead'
      using errcode = 'P0001';
  end if;
  return new;
end
$$;

comment on function crm.opportunity_document_immutable() is
  'DOMAIN.md §7 #56 — a case document is append-only; only removed_at, removed_by_operator_id and remove_reason change, and only once. SECURITY INVOKER.';

revoke all on function crm.opportunity_document_immutable()
  from public, anon, authenticated, service_role;

create trigger opportunity_document_never_deleted
  before delete on crm.opportunity_document
  for each row execute function platform.reject_mutation('never deleted (removal sets removed_at and its reason)');

create trigger opportunity_document_immutable
  before update on crm.opportunity_document
  for each row execute function crm.opportunity_document_immutable();

-- The API attaches and removes; the worker only reads; no runtime role deletes.
grant select, insert on crm.opportunity_document to origenlab_api;
grant update (removed_at, removed_by_operator_id, remove_reason) on crm.opportunity_document to origenlab_api;
grant select on crm.opportunity_document to origenlab_worker;

alter table crm.opportunity_document enable row level security;
create policy origenlab_api_select on crm.opportunity_document for select to origenlab_api using (true);
create policy origenlab_api_insert on crm.opportunity_document for insert to origenlab_api with check (true);
create policy origenlab_api_update on crm.opportunity_document for update to origenlab_api using (true) with check (true);
create policy origenlab_worker_select on crm.opportunity_document for select to origenlab_worker using (true);

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
  'opportunity.document_attached', 'opportunity.document_removed'
));

reset role;
