-- Slice 7 — «Pendientes» de Hoy: the shared to-do list (DOMAIN.md §7, #68).
--
-- Owner 2026-10-27: the operators keep one shared list of loose to-dos on «Hoy» — a line of text,
-- optionally urgent, optionally dated, optionally tied to a case; ordered by hand inside two
-- groups (urgent first). Not a Gmail/Tasks mirror and not crm.task (a case follow-up with a due
-- instant): this is a lightweight list that never touches a case's stage.
--
--   * #68 crm.todo — body (1–500 chars), is_urgent, position (fractional order inside its group,
--     smaller = higher), due_on (a Santiago day), optional opportunity_id, status
--     ('open' | 'done' | 'deleted'; done <=> completed_at), created_by_operator_id, version
--     (optimistic concurrency), timestamps. Never physically deleted: a removed line is
--     status 'deleted'. The API holds select/insert/update (like crm.task); the worker nothing.
--   * crm.domain_event: new aggregate kind 'todo' (also taught to crm.domain_event_is_valid, whose
--     family check would otherwise reject it) and the six todo.* event types. Full lists as left
--     by 20261020100000 (aggregate kinds) and 20261026100000 (event types).

set role origenlab_owner;

create table crm.todo (
  id uuid primary key default gen_random_uuid(),
  body text not null,
  is_urgent boolean not null default false,
  position numeric not null,
  due_on date,
  opportunity_id uuid references crm.opportunity (id),
  status text not null default 'open',
  completed_at timestamptz,
  created_by_operator_id uuid not null references platform.operator (id),
  version integer not null default 1,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint todo_body_length check (length(btrim(body)) between 1 and 500),
  constraint todo_status_check check (status in ('open', 'done', 'deleted')),
  -- done <=> completed_at IS NOT NULL (as crm.task).
  constraint todo_done_shape check ((status = 'done') = (completed_at is not null)),
  constraint todo_version_positive check (version >= 1)
);

comment on table crm.todo is
  'DOMAIN.md §7 #68 — a line of the shared «Pendientes» list on Hoy: body (1–500 chars), is_urgent, position (fractional order inside its group, smaller = higher), optional due_on day (Santiago) and optional case (opportunity_id); status open | done | deleted, done <=> completed_at IS NOT NULL. Never physically deleted (a removed line is status deleted); never changes a case stage.';

create index todo_opportunity_idx on crm.todo (opportunity_id) where opportunity_id is not null;
create index todo_created_by_operator_idx on crm.todo (created_by_operator_id);
create index todo_open_order_idx on crm.todo (is_urgent desc, position) where status = 'open';

create trigger todo_never_deleted
  before delete on crm.todo
  for each row execute function platform.reject_mutation('never deleted (a removed line is status deleted)');

-- ── grants ───────────────────────────────────────────────────────────────────────────────────
revoke all on crm.todo from public, anon, authenticated, service_role;
grant select, insert, update on crm.todo to origenlab_api;

-- ── RLS ──────────────────────────────────────────────────────────────────────────────────────
alter table crm.todo enable row level security;

create policy origenlab_api_select on crm.todo for select to origenlab_api using (true);
create policy origenlab_api_insert on crm.todo for insert to origenlab_api with check (true);
create policy origenlab_api_update on crm.todo for update to origenlab_api using (true) with check (true);

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
         end;
$$;
comment on function crm.domain_event_is_valid(text, text, smallint, jsonb) is
  'CHECK helper for crm.domain_event: payload is an object, payload_version is defined, event_type family matches aggregate_kind — including the three commercial-case families (DOMAIN.md §3.6), campaign_block (§7 #37), note (§7 #44), fx_rate (§7 #52), cost_parameter (§7 #53), bidder (§7 #60–#61), notice (§7 #28) and todo (§7 #68). SECURITY INVOKER.';

alter table crm.domain_event drop constraint domain_event_aggregate_kind_check;
alter table crm.domain_event add constraint domain_event_aggregate_kind_check check (aggregate_kind in (
  'organization', 'person', 'affiliation', 'address', 'contact_point', 'organization_relationship',
  'opportunity', 'opportunity_participant', 'opportunity_organization', 'opportunity_interest',
  'opportunity_evidence', 'task', 'activity', 'quote', 'quote_revision',
  'campaign', 'campaign_block', 'send_attempt', 'contact_control', 'send_control', 'operator',
  'assertion', 'source_record', 'product', 'note',
  'fx_rate', 'cost_parameter',
  'bidder', 'notice', 'todo'
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
  'todo.created', 'todo.updated', 'todo.moved', 'todo.completed', 'todo.reopened', 'todo.deleted'
));

reset role;
