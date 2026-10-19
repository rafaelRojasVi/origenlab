-- Slice 7 — Licitaciones fase 1: preparing a tender bid (DOMAIN.md §7.11, #60–#63).
--
-- Owner 2026-10-19 (spec «Licitaciones con IA» §6.10): tenders are «something else completely» and
-- live in their own space, «Licitaciones». Underneath, a tender is a crm.opportunity of kind
-- 'tender' so it reuses the institution, the Cotizador, case documents and Drive; every general
-- read (Oportunidades, the board, Hoy, follow-ups, the Cotizador list, Rentabilidad, mail rules)
-- keeps to kind = 'sale'. What this migration builds:
--
--   * crm.opportunity.kind ('sale' | 'tender', every existing row 'sale', never changed — trigger
--     opportunity_kind_fixed) and tender_status (preparando | postulada | adjudicada | perdida,
--     set exactly on a tender). The stage machine is left as it is.
--   * crm.opportunity_document also holds a tender's anexos (kind tender_annex), in Word and Excel
--     as well as PDF and images.
--   * #60 crm.bidder_profile — who bids (today a natural person, later a company): append-only, the
--     current profile is the newest row.
--   * #61 crm.bidder_document — the bidder's standard documents (cédula, certificado, declaraciones,
--     experiencia), private bytes under crm-documents/bidder/<sha256>.<ext>; retired once.
--   * #62 evidence.bid_preparation — the machine's reading (phase read) and filling (phase fill) of
--     a tender's anexos; a re-run is a new row; one queued or running row per case and phase.
--   * #63 platform.model_usage — every model call's tokens and cost, append-only; the monthly cap
--     is checked against it before each call.
--   * procurement.notice: the API inserts the notice of a tender opened by hand and promotes it.
--   * catalog.product_document: the worker reads it (the spec sheets that go into a bid).
--
-- Events: bidder.profile_saved, bidder.document_added, bidder.document_retired (aggregate bidder),
-- opportunity.bid_preparation_requested, opportunity.tender_status_set and notice.promoted
-- (aggregate notice). The type list below is the live CHECK (last rebuilt by 20261016200000) plus
-- those six; the aggregate list is the live one (20261005140628) plus bidder and notice, and the
-- CHECK helper maps each to its own family.

set role origenlab_owner;

-- ── crm.opportunity: kind and tender_status ─────────────────────────────────────────────────
alter table crm.opportunity
  add column kind text not null default 'sale',
  add column tender_status text,
  add constraint opportunity_kind_check check (kind in ('sale', 'tender')),
  add constraint opportunity_tender_status_check check (
    tender_status in ('preparando', 'postulada', 'adjudicada', 'perdida')),
  add constraint opportunity_tender_status_shape check ((kind = 'tender') = (tender_status is not null));

comment on column crm.opportunity.kind is
  'sale (every general read) or tender (the «Licitaciones» space only, DOMAIN.md §7.11). Fixed at creation: trigger opportunity_kind_fixed.';
comment on column crm.opportunity.tender_status is
  'A tender''s own status: preparando, postulada, adjudicada or perdida. Set exactly when kind = tender.';

create index opportunity_tender_idx on crm.opportunity (tender_status) where kind = 'tender';

create function crm.opportunity_kind_fixed() returns trigger
language plpgsql
set search_path = pg_catalog
as $$
begin
  if new.kind is distinct from old.kind then
    raise exception 'crm.opportunity %: a case''s kind (%) never changes', old.id, old.kind
      using errcode = 'P0001';
  end if;
  return new;
end
$$;

comment on function crm.opportunity_kind_fixed() is
  'DOMAIN.md §7.11 — a case is a sale or a tender from creation and never becomes the other. SECURITY INVOKER.';

revoke all on function crm.opportunity_kind_fixed()
  from public, anon, authenticated, service_role;

create trigger opportunity_kind_fixed
  before update on crm.opportunity
  for each row execute function crm.opportunity_kind_fixed();

-- ── crm.opportunity_document: a tender's anexos ─────────────────────────────────────────────
alter table crm.opportunity_document
  drop constraint opportunity_document_kind_check,
  drop constraint opportunity_document_path_shape,
  drop constraint opportunity_document_content_type_check;
alter table crm.opportunity_document
  add constraint opportunity_document_kind_check check (kind in ('purchase_order', 'tender_annex')),
  add constraint opportunity_document_path_shape check (
    storage_path ~ '^opportunities/[0-9a-f-]{36}/[0-9a-f]{64}[.](pdf|jpg|png|docx|xlsx|doc|xls)$'
  ),
  add constraint opportunity_document_content_type_check check (
    content_type in ('application/pdf', 'image/jpeg', 'image/png',
                     'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
                     'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                     'application/msword', 'application/vnd.ms-excel')
  );

comment on table crm.opportunity_document is
  'DOMAIN.md §7 #56 — a document a person attached to a commercial case: the purchase order (kind purchase_order) that closes it, with the quote revision it closes when known, or a tender''s anexo (kind tender_annex, PDF, image, Word or Excel). Bytes in the private Storage bucket crm-documents under opportunities/<case>/<sha256>.<ext>; source upload (from a computer) or email (an attachment of source_record_id, copied after its SHA-256 matched). Never deleted, never rewritten: removal sets removed_at, its reason and who, once.';

-- ── #60 crm.bidder_profile ──────────────────────────────────────────────────────────────────
create table crm.bidder_profile (
  id uuid primary key default gen_random_uuid(),
  bidder_kind text not null,
  legal_name text not null,
  rut text not null,
  giro text,
  address text,
  comuna text,
  region text,
  phone text,
  email text,
  legal_rep_name text,
  legal_rep_rut text,
  bank_name text,
  bank_account_type text,
  bank_account_number text,
  chileproveedores_status text,
  chileproveedores_checked_on date,
  is_emt boolean not null default false,
  is_local_supplier boolean not null default false,
  set_by_operator_id uuid not null references platform.operator (id),
  reason text not null,
  created_at timestamptz not null default now(),
  constraint bidder_profile_kind_check check (bidder_kind in ('natural_person', 'company')),
  constraint bidder_profile_legal_name_shape check (length(btrim(legal_name)) between 1 and 200),
  constraint bidder_profile_rut_shape check (rut ~ '^[0-9]{7,8}-[0-9K]$'),
  constraint bidder_profile_legal_rep_rut_shape check (
    legal_rep_rut is null or legal_rep_rut ~ '^[0-9]{7,8}-[0-9K]$'),
  constraint bidder_profile_legal_rep_only_for_company check (
    bidder_kind = 'company' or (legal_rep_name is null and legal_rep_rut is null)),
  constraint bidder_profile_bank_account_type_check check (
    bank_account_type in ('corriente', 'vista', 'ahorro', 'rut')),
  constraint bidder_profile_chileproveedores_status_check check (
    chileproveedores_status in ('habil', 'no_habil', 'no_inscrito')),
  constraint bidder_profile_reason_nonblank check (length(btrim(reason)) > 0)
);

comment on table crm.bidder_profile is
  'DOMAIN.md §7 #60 — who bids in Mercado Público (a natural person today, a company later): identity, RUT (normalised, no dots), address, contact, legal representative (company only), bank and ChileProveedores data, EMT/local flags. Append-only: a save is a new row with who and why; the current profile is the newest row.';

create index bidder_profile_set_by_idx on crm.bidder_profile (set_by_operator_id);

create trigger bidder_profile_append_only
  before update or delete on crm.bidder_profile
  for each row execute function platform.reject_mutation('append-only: a save inserts a new profile');

-- ── #61 crm.bidder_document ─────────────────────────────────────────────────────────────────
create table crm.bidder_document (
  id uuid primary key default gen_random_uuid(),
  kind text not null,
  title text not null,
  storage_bucket text not null default 'crm-documents',
  storage_path text not null,
  sha256 text not null,
  content_type text not null,
  byte_size integer not null,
  original_filename text not null,
  added_by_operator_id uuid not null references platform.operator (id),
  added_at timestamptz not null default now(),
  retired_at timestamptz,
  retired_by_operator_id uuid references platform.operator (id),
  retire_reason text,
  constraint bidder_document_kind_check check (
    kind in ('cedula', 'certificado_inscripcion', 'declaracion_jurada', 'experiencia', 'otro')),
  constraint bidder_document_title_shape check (length(btrim(title)) between 1 and 200),
  constraint bidder_document_bucket_check check (storage_bucket = 'crm-documents'),
  constraint bidder_document_path_shape check (
    storage_path ~ '^bidder/[0-9a-f]{64}[.](pdf|jpg|png|docx|xlsx)$'),
  constraint bidder_document_sha256_shape check (sha256 ~ '^[0-9a-f]{64}$'),
  constraint bidder_document_content_type_check check (
    content_type in ('application/pdf', 'image/jpeg', 'image/png',
                     'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
                     'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')),
  constraint bidder_document_size_check check (byte_size between 1 and 20971520),
  constraint bidder_document_filename_shape check (length(btrim(original_filename)) between 1 and 255),
  constraint bidder_document_retirement_shape check (
    (retired_at is null) = (retired_by_operator_id is null)
    and (retired_at is null) = (retire_reason is null)),
  constraint bidder_document_retire_reason_nonblank check (
    retire_reason is null or length(btrim(retire_reason)) > 0)
);

comment on table crm.bidder_document is
  'DOMAIN.md §7 #61 — a standard document of the bidder (cédula, certificado de inscripción, declaración jurada, experiencia, otro), bytes in the private Storage bucket crm-documents under bidder/<sha256>.<ext>. Never deleted, never rewritten: retirement sets retired_at, its reason and who, once.';

create index bidder_document_added_by_idx on crm.bidder_document (added_by_operator_id);
create index bidder_document_retired_by_idx on crm.bidder_document (retired_by_operator_id);

create function crm.bidder_document_retire_once() returns trigger
language plpgsql
set search_path = pg_catalog
as $$
begin
  if new.id is distinct from old.id
     or new.kind is distinct from old.kind
     or new.title is distinct from old.title
     or new.storage_bucket is distinct from old.storage_bucket
     or new.storage_path is distinct from old.storage_path
     or new.sha256 is distinct from old.sha256
     or new.content_type is distinct from old.content_type
     or new.byte_size is distinct from old.byte_size
     or new.original_filename is distinct from old.original_filename
     or new.added_by_operator_id is distinct from old.added_by_operator_id
     or new.added_at is distinct from old.added_at
  then
    raise exception 'crm.bidder_document: a document is never rewritten — retire it and add the right one'
      using errcode = 'P0001';
  end if;
  if old.retired_at is not null then
    raise exception 'crm.bidder_document: a retired document stays retired — add it again instead'
      using errcode = 'P0001';
  end if;
  return new;
end
$$;

comment on function crm.bidder_document_retire_once() is
  'DOMAIN.md §7 #61 — a bidder document changes only by being retired (retired_at, retired_by_operator_id, retire_reason), once. SECURITY INVOKER.';

revoke all on function crm.bidder_document_retire_once()
  from public, anon, authenticated, service_role;

create trigger bidder_document_retire_once
  before update on crm.bidder_document
  for each row execute function crm.bidder_document_retire_once();

create trigger bidder_document_never_deleted
  before delete on crm.bidder_document
  for each row execute function platform.reject_mutation('never deleted (retirement sets retired_at and its reason)');

-- ── #62 evidence.bid_preparation ────────────────────────────────────────────────────────────
create table evidence.bid_preparation (
  id uuid primary key default gen_random_uuid(),
  opportunity_id uuid not null references crm.opportunity (id),
  phase text not null,
  status text not null default 'queued',
  annex_document_ids uuid[] not null,
  based_on_preparation_id uuid references evidence.bid_preparation (id),
  quote_revision_id uuid references crm.quote_revision (id),
  requested_by_operator_id uuid not null references platform.operator (id),
  requested_at timestamptz not null default now(),
  started_at timestamptz,
  finished_at timestamptz,
  failure_code text,
  result jsonb not null default '{}'::jsonb,
  outputs jsonb not null default '[]'::jsonb,
  models jsonb not null default '{}'::jsonb,
  updated_at timestamptz not null default now(),
  constraint bid_preparation_phase_check check (phase in ('read', 'fill')),
  constraint bid_preparation_status_check check (
    status in ('queued', 'running', 'ready', 'failed', 'budget_stopped')),
  constraint bid_preparation_annexes_nonempty check (cardinality(annex_document_ids) >= 1),
  constraint bid_preparation_fill_based_on_read check (
    phase <> 'fill' or based_on_preparation_id is not null),
  constraint bid_preparation_quote_revision_only_on_fill check (
    phase = 'fill' or quote_revision_id is null),
  constraint bid_preparation_failure_code_shape check (
    failure_code is null
    or (status in ('failed', 'budget_stopped') and length(btrim(failure_code)) > 0)),
  constraint bid_preparation_result_object check (jsonb_typeof(result) = 'object'),
  constraint bid_preparation_outputs_array check (jsonb_typeof(outputs) = 'array'),
  constraint bid_preparation_models_object check (jsonb_typeof(models) = 'object')
);

comment on table evidence.bid_preparation is
  'DOMAIN.md §7 #62 — the machine''s preparation of a tender bid: phase read (summary, checklist, items from the anexos) or fill (forms, offer, spec sheets, package; based on a read, with the offer''s quote revision). Requested by the API (queued), run by the worker (status, result, outputs, models). A re-run is a new row; at most one queued or running row per case and phase.';

create unique index bid_preparation_one_active
  on evidence.bid_preparation (opportunity_id, phase) where status in ('queued', 'running');
create index bid_preparation_opportunity_idx on evidence.bid_preparation (opportunity_id);
create index bid_preparation_based_on_idx on evidence.bid_preparation (based_on_preparation_id);
create index bid_preparation_quote_revision_idx on evidence.bid_preparation (quote_revision_id);
create index bid_preparation_requested_by_idx on evidence.bid_preparation (requested_by_operator_id);

create trigger bid_preparation_never_deleted
  before delete on evidence.bid_preparation
  for each row execute function platform.reject_mutation('never deleted (a re-run is a new row)');

-- ── #63 platform.model_usage ────────────────────────────────────────────────────────────────
create table platform.model_usage (
  id uuid primary key default gen_random_uuid(),
  feature text not null,
  model text not null,
  step text not null,
  bid_preparation_id uuid references evidence.bid_preparation (id),
  input_tokens integer not null default 0,
  output_tokens integer not null default 0,
  cache_read_tokens integer not null default 0,
  cache_write_tokens integer not null default 0,
  usd_cost numeric(12, 6) not null,
  created_at timestamptz not null default now(),
  constraint model_usage_feature_check check (feature in ('licitaciones')),
  constraint model_usage_model_nonblank check (length(btrim(model)) > 0),
  constraint model_usage_step_check check (step in ('read', 'match', 'fill')),
  constraint model_usage_tokens_nonnegative check (
    input_tokens >= 0 and output_tokens >= 0 and cache_read_tokens >= 0 and cache_write_tokens >= 0),
  constraint model_usage_cost_nonnegative check (usd_cost >= 0)
);

comment on table platform.model_usage is
  'DOMAIN.md §7 #63 — one model call: feature, model, step, the preparation it served, tokens (input, output, cache read, cache write) and its cost in USD. Append-only; the feature''s monthly cap is checked against these rows before each call.';

create index model_usage_feature_created_idx on platform.model_usage (feature, created_at);
create index model_usage_bid_preparation_idx on platform.model_usage (bid_preparation_id);

create trigger model_usage_append_only
  before update or delete on platform.model_usage
  for each row execute function platform.reject_mutation('append-only');

-- ── grants and policies ─────────────────────────────────────────────────────────────────────
-- crm.opportunity: origenlab_api already holds table-level UPDATE (Slice 0), so it sets
-- tender_status without a column grant; kind is held by the trigger above.

-- The API saves profiles and adds/retires documents; the worker reads both (it fills the forms).
grant select, insert on crm.bidder_profile to origenlab_api;
grant select on crm.bidder_profile to origenlab_worker;
grant select, insert on crm.bidder_document to origenlab_api;
grant update (retired_at, retired_by_operator_id, retire_reason) on crm.bidder_document to origenlab_api;
grant select on crm.bidder_document to origenlab_worker;
-- The API requests a preparation; the worker runs it and writes its result back.
grant select, insert on evidence.bid_preparation to origenlab_api;
grant select on evidence.bid_preparation to origenlab_worker;
grant update (status, started_at, finished_at, failure_code, result, outputs, models, updated_at)
  on evidence.bid_preparation to origenlab_worker;
-- The worker records spend; the API reads it (Operaciones, the cap before a request).
grant select, insert on platform.model_usage to origenlab_worker;
grant select on platform.model_usage to origenlab_api;
-- The API records the notice of a tender opened by hand and what it read from it.
grant insert on procurement.notice to origenlab_api;
grant update (promoted_opportunity_id, updated_at, last_seen_at, head, head_history, buyer_name,
              buyer_code, title, closes_at, source_status, published_at) on procurement.notice to origenlab_api;
-- The worker reads product data sheets (the spec sheets of a bid).
grant select on catalog.product_document to origenlab_worker;

alter table crm.bidder_profile enable row level security;
alter table crm.bidder_document enable row level security;
alter table evidence.bid_preparation enable row level security;
alter table platform.model_usage enable row level security;

create policy origenlab_api_select on crm.bidder_profile for select to origenlab_api using (true);
create policy origenlab_api_insert on crm.bidder_profile for insert to origenlab_api with check (true);
create policy origenlab_worker_select on crm.bidder_profile for select to origenlab_worker using (true);

create policy origenlab_api_select on crm.bidder_document for select to origenlab_api using (true);
create policy origenlab_api_insert on crm.bidder_document for insert to origenlab_api with check (true);
create policy origenlab_api_update on crm.bidder_document for update to origenlab_api using (true) with check (true);
create policy origenlab_worker_select on crm.bidder_document for select to origenlab_worker using (true);

create policy origenlab_api_select on evidence.bid_preparation for select to origenlab_api using (true);
create policy origenlab_api_insert on evidence.bid_preparation for insert to origenlab_api with check (true);
create policy origenlab_worker_select on evidence.bid_preparation for select to origenlab_worker using (true);
create policy origenlab_worker_update on evidence.bid_preparation for update to origenlab_worker using (true) with check (true);

create policy origenlab_worker_select on platform.model_usage for select to origenlab_worker using (true);
create policy origenlab_worker_insert on platform.model_usage for insert to origenlab_worker with check (true);
create policy origenlab_api_select on platform.model_usage for select to origenlab_api using (true);

create policy origenlab_api_insert on procurement.notice for insert to origenlab_api with check (true);

create policy origenlab_worker_select on catalog.product_document for select to origenlab_worker using (true);

-- ── domain events ───────────────────────────────────────────────────────────────────────────
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
         end;
$$;
comment on function crm.domain_event_is_valid(text, text, smallint, jsonb) is
  'CHECK helper for crm.domain_event: payload is an object, payload_version is defined, event_type family matches aggregate_kind — including the three commercial-case families (DOMAIN.md §3.6), campaign_block (§7 #37), note (§7 #44), fx_rate (§7 #52), cost_parameter (§7 #53), bidder (§7 #60–#61) and notice (§7 #28). SECURITY INVOKER.';

alter table crm.domain_event drop constraint domain_event_aggregate_kind_check;
alter table crm.domain_event add constraint domain_event_aggregate_kind_check check (aggregate_kind in (
  'organization', 'person', 'affiliation', 'address', 'contact_point', 'organization_relationship',
  'opportunity', 'opportunity_participant', 'opportunity_organization', 'opportunity_interest',
  'opportunity_evidence', 'task', 'activity', 'quote', 'quote_revision',
  'campaign', 'campaign_block', 'send_attempt', 'contact_control', 'send_control', 'operator',
  'assertion', 'source_record', 'product', 'note',
  'fx_rate', 'cost_parameter',
  'bidder', 'notice'
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
  'opportunity.bid_preparation_requested', 'opportunity.tender_status_set', 'notice.promoted'
));

reset role;
