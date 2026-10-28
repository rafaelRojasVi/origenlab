-- Slice 7 — «Pagos»: the sales invoices of the SII register, and whether each was paid (DOMAIN.md §7, #70).
--
-- Owner 2026-10-28: Tatiana issues her invoices on the SII's own portal; there is no SII API for
-- the sales register, so the operators download the monthly «Registro de Ventas» CSV
-- (sii.cl → Registro de Compras y Ventas → VENTA → Descargar Detalles) and upload it on «Pagos».
-- SII never knows whether a client paid: an operator marks an invoice «Pagada» with its day.
--
--   * #70 crm.invoice — one SII sales document: doc_type (33 factura, 34 exenta, 56 nota de
--     débito, 61 nota de crédito) + folio, unique together; issued_on, the client as SII prints it
--     (client_rut, client_name), the four CLP amounts, the referenced document of a nota
--     (reference_doc_type, reference_folio), the client organization (by RUT, at import), the case it
--     belongs to (opportunity_id; case_link 'machine' when the import attached it by amount,
--     'operator' when a person did), paid_on, version, timestamps. Never physically deleted; who
--     imported, attached or marked it paid is in its invoice.* events. The API holds
--     select/insert/update; the worker nothing.
--   * crm.domain_event: aggregate kind 'invoice' (also taught to crm.domain_event_is_valid) and four
--     invoice.* event types. Full lists as left by 20261028100000.

set role origenlab_owner;

create table crm.invoice (
  id uuid primary key default gen_random_uuid(),
  doc_type smallint not null,
  folio bigint not null,
  issued_on date not null,
  client_rut text not null,
  client_name text not null,
  exempt_amount bigint not null default 0,
  net_amount bigint not null default 0,
  vat_amount bigint not null default 0,
  total_amount bigint not null,
  reference_doc_type smallint,
  reference_folio bigint,
  organization_id uuid references crm.organization (id),
  opportunity_id uuid references crm.opportunity (id),
  case_link text,
  paid_on date,
  version integer not null default 1,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint invoice_doc_type_folio_key unique (doc_type, folio),
  constraint invoice_doc_type_check check (doc_type in (33, 34, 56, 61)),
  constraint invoice_folio_positive check (folio > 0),
  constraint invoice_client_rut_nonblank check (length(btrim(client_rut)) > 0),
  constraint invoice_amounts_nonnegative check (
    exempt_amount >= 0 and net_amount >= 0 and vat_amount >= 0 and total_amount >= 0),
  -- A case is attached by the import ('machine') or by a person ('operator'), never without saying which.
  -- Written NULL-proof: a CHECK that evaluates to NULL passes.
  constraint invoice_case_link_check check (case_link in ('machine', 'operator')),
  constraint invoice_case_link_shape check ((opportunity_id is null) = (case_link is null)),
  -- Only a factura or factura exenta is collected; a nota adjusts the document it references.
  constraint invoice_paid_only_facturas check (paid_on is null or doc_type in (33, 34)),
  constraint invoice_version_positive check (version >= 1)
);

comment on table crm.invoice is
  'DOMAIN.md §7 #70 — one SII sales document (doc_type 33/34/56/61 + folio, unique) imported from the «Registro de Ventas» CSV on «Pagos»: client RUT and name as SII prints them, CLP amounts, the referenced document of a nota, the client organization (by RUT), the case it belongs to (case_link machine | operator) and paid_on, set by an operator («Pagada»). Never physically deleted.';

create index invoice_opportunity_idx on crm.invoice (opportunity_id) where opportunity_id is not null;
create index invoice_organization_idx on crm.invoice (organization_id) where organization_id is not null;
create index invoice_unpaid_idx on crm.invoice (issued_on) where paid_on is null;
create index invoice_reference_idx on crm.invoice (reference_doc_type, reference_folio) where reference_folio is not null;

create trigger invoice_never_deleted
  before delete on crm.invoice
  for each row execute function platform.reject_mutation('never deleted (an SII document stays on record)');

-- ── grants ───────────────────────────────────────────────────────────────────────────────────
revoke all on crm.invoice from public, anon, authenticated, service_role;
grant select, insert, update on crm.invoice to origenlab_api;

-- ── RLS ──────────────────────────────────────────────────────────────────────────────────────
alter table crm.invoice enable row level security;

create policy origenlab_api_select on crm.invoice for select to origenlab_api using (true);
create policy origenlab_api_insert on crm.invoice for insert to origenlab_api with check (true);
create policy origenlab_api_update on crm.invoice for update to origenlab_api using (true) with check (true);

-- ── domain events ────────────────────────────────────────────────────────────────────────────
create or replace function crm.domain_event_is_valid(
  p_aggregate_kind text, p_event_type text, p_payload_version smallint, p_payload jsonb
) returns boolean
language sql immutable set search_path = pg_catalog
as $$
  select p_payload is not null
     and jsonb_typeof(p_payload) = 'object'
     and p_payload_version = 1
     and split_part(p_event_type, '.', 1) = case p_aggregate_kind
           when 'organization'              then 'organization'
           when 'person'                    then 'person'
           when 'affiliation'               then 'affiliation'
           when 'address'                   then 'address'
           when 'contact_point'             then 'contact_point'
           when 'organization_relationship' then 'relationship'
           when 'opportunity'               then 'opportunity'
           when 'opportunity_participant'   then 'participant'
           when 'opportunity_organization'  then 'case_organization'
           when 'opportunity_interest'      then 'case_interest'
           when 'opportunity_evidence'      then 'case_evidence'
           when 'task'                      then 'task'
           when 'activity'                  then 'activity'
           when 'quote'                     then 'quote'
           when 'quote_revision'            then 'quote_revision'
           when 'campaign'                  then 'campaign'
           when 'campaign_block'            then 'campaign_block'
           when 'send_attempt'              then 'send_attempt'
           when 'contact_control'           then 'contact_control'
           when 'send_control'              then 'send_control'
           when 'operator'                  then 'operator'
           when 'assertion'                 then 'assertion'
           when 'source_record'             then 'source_record'
           when 'product'                   then 'product'
           when 'note'                      then 'note'
           when 'fx_rate'                   then 'fx_rate'
           when 'cost_parameter'            then 'cost_parameter'
           when 'bidder'                    then 'bidder'
           when 'notice'                    then 'notice'
           when 'todo'                      then 'todo'
           when 'invoice'                   then 'invoice'
         end;
$$;
comment on function crm.domain_event_is_valid(text, text, smallint, jsonb) is
  'CHECK helper for crm.domain_event: payload is an object, payload_version is defined, event_type family matches aggregate_kind — including the three commercial-case families (DOMAIN.md §3.6), campaign_block (§7 #37), note (§7 #44), fx_rate (§7 #52), cost_parameter (§7 #53), bidder (§7 #60–#61), notice (§7 #28), todo (§7 #68) and invoice (§7 #70). SECURITY INVOKER.';

alter table crm.domain_event drop constraint domain_event_aggregate_kind_check;
alter table crm.domain_event add constraint domain_event_aggregate_kind_check check (aggregate_kind in (
  'organization', 'person', 'affiliation', 'address', 'contact_point', 'organization_relationship',
  'opportunity', 'opportunity_participant', 'opportunity_organization', 'opportunity_interest',
  'opportunity_evidence', 'task', 'activity', 'quote', 'quote_revision',
  'campaign', 'campaign_block', 'send_attempt', 'contact_control', 'send_control', 'operator',
  'assertion', 'source_record', 'product', 'note',
  'fx_rate', 'cost_parameter',
  'bidder', 'notice', 'todo', 'invoice'
));

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
  'source_record.prospect_reviewed', 'source_record.prospect_edited',
  'source_record.prospect_search_requested', 'source_record.prospect_email_drafted',
  'todo.created', 'todo.updated', 'todo.moved', 'todo.completed', 'todo.reopened', 'todo.deleted',
  'invoice.imported', 'invoice.case_set', 'invoice.paid', 'invoice.unpaid'
));

reset role;
