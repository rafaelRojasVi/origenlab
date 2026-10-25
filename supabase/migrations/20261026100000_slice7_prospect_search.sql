-- Slice 7 — Prospectos fase 2: the daily ANID search run (DOMAIN.md §7, #67).
--
-- Owner 2026-10-25: ANID open-data drip, keep 25 unreviewed prospects in the queue, daily ~06:00
-- Santiago, «Buscar ahora» YES, Claude spend cap US$ 10/month under feature key 'prospectos'.
--
--   * platform.model_usage: feature check widens to include 'prospectos'; step check widens to
--     add 'prospect' (one call per candidate, writing «Por qué ahora» + «Equipos»). A nullable
--     column `prospect_search_run_id` links each spend row to the search run that caused it —
--     same pattern as `bid_preparation_id`. The FK index follows the bid_preparation pattern.
--   * #67 evidence.prospect_search_run — one row per daily or manual search run: trigger
--     ('daily' | 'manual'), operator (null for daily, required for manual — CHECK),
--     status ('queued' | 'running' | 'done' | 'failed'), times, counts jsonb, error text (short
--     code only). A partial unique index prevents a second queued-or-running run. Mutable status:
--     the worker updates it as the run progresses; never deleted. Same grant/RLS pattern as
--     evidence.bid_preparation, with the worker also holding insert (it creates daily runs).
--   * crm.domain_event type check: adds 'source_record.prospect_search_requested' (the manual
--     «Buscar ahora» command records it) and 'source_record.prospect_email_drafted' (reserved for
--     part 2 — the intro-email Gmail draft). Full list as left by 20261025100000.

set role origenlab_owner;

-- ── platform.model_usage: widen feature + step + add FK column ──────────────────────────────
alter table platform.model_usage
  add column prospect_search_run_id uuid;  -- FK added after the run table is created

alter table platform.model_usage drop constraint model_usage_feature_check;
alter table platform.model_usage add constraint model_usage_feature_check
  check (feature in ('licitaciones', 'prospectos'));

alter table platform.model_usage drop constraint model_usage_step_check;
alter table platform.model_usage add constraint model_usage_step_check
  check (step in ('read', 'match', 'fill', 'prospect'));

comment on table platform.model_usage is
  'DOMAIN.md §7 #63 — one model call: feature, model, step, the preparation it served (bid_preparation_id) or search run (prospect_search_run_id), tokens (input, output, cache read, cache write) and its cost in USD. Append-only; the feature''s monthly cap is checked against it before each call.';

-- ── #67 evidence.prospect_search_run ────────────────────────────────────────────────────────
create table evidence.prospect_search_run (
  id uuid primary key default gen_random_uuid(),
  trigger text not null,
  requested_by_operator_id uuid references platform.operator (id),
  requested_at timestamptz not null default now(),
  status text not null default 'queued',
  started_at timestamptz,
  finished_at timestamptz,
  counts jsonb,
  error text,
  updated_at timestamptz not null default now(),
  constraint prospect_search_run_trigger_check check (trigger in ('daily', 'manual')),
  constraint prospect_search_run_status_check check (
    status in ('queued', 'running', 'done', 'failed')),
  constraint prospect_search_run_manual_operator_check check (
    (trigger = 'manual') = (requested_by_operator_id is not null)),
  constraint prospect_search_run_counts_object check (
    counts is null or jsonb_typeof(counts) = 'object'),
  constraint prospect_search_run_error_shape check (
    error is null or (status in ('failed') and length(btrim(error)) > 0))
);

comment on table evidence.prospect_search_run is
  'DOMAIN.md §7 #67 — one prospect search run: daily (trigger = ''daily'', no operator) or manual (trigger = ''manual'', operator required). Status: queued → running → done | failed. The worker updates status, started_at, finished_at, counts and error; never deleted (a re-run is a new row). Counts (jsonb object): added, skipped, no_email, low_fit, pool_left. Error is a short code only, never a message body.';

create unique index prospect_search_run_one_active
  on evidence.prospect_search_run (status) where status in ('queued', 'running');

create index prospect_search_run_requested_by_idx
  on evidence.prospect_search_run (requested_by_operator_id)
  where requested_by_operator_id is not null;

create index prospect_search_run_status_requested_idx
  on evidence.prospect_search_run (status, requested_at);

create trigger prospect_search_run_never_deleted
  before delete on evidence.prospect_search_run
  for each row execute function platform.reject_mutation('never deleted (a re-run is a new row)');

-- ── FK from model_usage → prospect_search_run (table now exists) ────────────────────────────
alter table platform.model_usage
  add constraint model_usage_prospect_search_run_id_fkey
    foreign key (prospect_search_run_id) references evidence.prospect_search_run (id);

create index model_usage_prospect_search_run_idx
  on platform.model_usage (prospect_search_run_id)
  where prospect_search_run_id is not null;

-- ── grants ───────────────────────────────────────────────────────────────────────────────────
revoke all on evidence.prospect_search_run from public, anon, authenticated, service_role;

-- The API inserts manual runs (request-prospect-search) and reads them for the list header.
grant select, insert on evidence.prospect_search_run to origenlab_api;
-- The worker inserts daily runs and updates the status as the run progresses.
grant select, insert, update on evidence.prospect_search_run to origenlab_worker;

-- ── RLS ──────────────────────────────────────────────────────────────────────────────────────
alter table evidence.prospect_search_run enable row level security;

create policy origenlab_api_select    on evidence.prospect_search_run for select to origenlab_api    using (true);
create policy origenlab_api_insert    on evidence.prospect_search_run for insert to origenlab_api    with check (true);
create policy origenlab_worker_select on evidence.prospect_search_run for select to origenlab_worker using (true);
create policy origenlab_worker_insert on evidence.prospect_search_run for insert to origenlab_worker with check (true);
create policy origenlab_worker_update on evidence.prospect_search_run for update to origenlab_worker using (true) with check (true);

-- ── domain events ────────────────────────────────────────────────────────────────────────────
-- Full list as left by 20261025100000 (prospect_edit) plus the two new prospect-search types.
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
  'source_record.prospect_search_requested', 'source_record.prospect_email_drafted'
));

reset role;
