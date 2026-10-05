-- Slice 7 / catalog 1a — pricing FX, cost parameters, quote documents as durable evidence.
-- docs/DOMAIN.md §7 new #52 catalog.fx_rate, #53 catalog.cost_parameter, #54 evidence.document_line;
-- spec §4.5, §4.6, §4.8 (revised: durable evidence, DATA.md §4/§9 forbid projection tables).
set role origenlab_owner;

-- #52 catalog.fx_rate — pricing exchange rates (CLP per unit). v2/fx_rates.py stays display-only.
create table catalog.fx_rate (
  id uuid primary key default gen_random_uuid(),
  rate_date date not null,
  currency text not null,
  clp_per_unit numeric(18,6) not null,
  source text not null,
  provider text not null,
  reason text,
  fetched_at timestamptz not null default now(),
  recorded_by_operator_id uuid references platform.operator (id),
  created_at timestamptz not null default now(),
  constraint fx_rate_currency_check check (currency in ('USD', 'EUR')),
  constraint fx_rate_positive check (clp_per_unit > 0),
  constraint fx_rate_source_check check (source in ('bcentral', 'manual')),
  constraint fx_rate_provider_check check (provider in ('bde', 'mindicador', 'operator')),
  constraint fx_rate_manual_shape check (
    (source = 'manual') = (provider = 'operator')
    and (source <> 'manual' or (recorded_by_operator_id is not null and reason is not null))),
  constraint fx_rate_reason_nonblank check (reason is null or length(btrim(reason)) > 0)
);
comment on table catalog.fx_rate is
  'DOMAIN.md §7 #52 — Banco Central observed USD/EUR in CLP per day (via BDE or mindicador; one row per day, currency and provider) or manual with operator and reason; append-only. A mistaken manual rate is superseded by a newer manual row: the newest manual row (by created_at) wins for its day and currency, else the bcentral row.';
-- Automatic rates are unique per day, currency and provider. Manual rows are not, so a
-- correction is a newer row rather than an update.
create unique index fx_rate_bcentral_key on catalog.fx_rate (rate_date, currency, provider) where source = 'bcentral';
create index fx_rate_lookup_idx on catalog.fx_rate (currency, rate_date desc);
create index fx_rate_recorded_by_idx on catalog.fx_rate (recorded_by_operator_id);
create trigger fx_rate_append_only before update or delete on catalog.fx_rate
  for each row execute function platform.reject_mutation('append-only');

-- #53 catalog.cost_parameter — costing parameters with history.
create table catalog.cost_parameter (
  id uuid primary key default gen_random_uuid(),
  key text not null,
  value_numeric numeric(18,6),
  value_json jsonb,
  valid_from timestamptz not null default now(),
  set_by_operator_id uuid references platform.operator (id),
  reason text not null,
  created_at timestamptz not null default now(),
  constraint cost_parameter_key_check check (key in (
    'dhl_import_discount_pct', 'dhl_fuel_surcharge_pct', 'dhl_customs_clearance_fee_usd',
    'courier_threshold_usd_fob', 'customs_agent_fee_clp', 'duty_rate_general', 'duty_rate_fta',
    'insurance_pct_of_fob', 'domestic_transport_clp',
    'profile_freight_clp', 'profile_agent_clp', 'profile_customs_clp', 'profile_transport_clp',
    'principal_threshold_eur',
    'default_markup_equipment', 'default_markup_accessory', 'default_markup_consumable',
    'default_markup_spare_part', 'default_markup_service',
    'margin_target_min', 'margin_target_max', 'price_deviation_warn_pct', 'iva_rate')),
  constraint cost_parameter_value_shape check ((value_numeric is null) <> (value_json is null)),
  constraint cost_parameter_reason_nonblank check (length(btrim(reason)) > 0),
  constraint cost_parameter_key_valid_from unique (key, valid_from)
);
comment on table catalog.cost_parameter is
  'DOMAIN.md §7 #53 — costing parameter history; current value = newest valid_from <= now(); append-only; seed rows have no operator.';
create index cost_parameter_current_idx on catalog.cost_parameter (key, valid_from desc);
create index cost_parameter_set_by_idx on catalog.cost_parameter (set_by_operator_id);
create trigger cost_parameter_append_only before update or delete on catalog.cost_parameter
  for each row execute function platform.reject_mutation('append-only');

-- The repository is public, so only public/regulatory values are seeded. Every other key
-- (negotiated carrier rates and fees, cost profiles, markups, margin targets, warning thresholds)
-- has no row until an operator sets it per environment with a reason; readers treat a key with
-- no current row as unset.
insert into catalog.cost_parameter (key, value_numeric, valid_from, reason) values
  ('iva_rate', 0.19, '2026-01-01 00:00+00', 'seed: Chilean VAT'),
  ('duty_rate_general', 0.06, '2026-01-01 00:00+00', 'seed: Aduanas general ad valorem tariff on CIF'),
  ('duty_rate_fta', 0.00, '2026-01-01 00:00+00', 'seed: EU/US free-trade preference with origin declaration'),
  ('courier_threshold_usd_fob', 3000, '2026-01-01 00:00+00', 'seed: Aduanas courier regime threshold'),
  ('insurance_pct_of_fob', 0.00, '2026-01-01 00:00+00', 'seed: no insurance component by default');

-- evidence: quote documents.
alter table evidence.source_record drop constraint source_record_kind_check;
alter table evidence.source_record add constraint source_record_kind_check check (kind in (
  'workbook_import', 'chilecompra_notice', 'migration_manifest',
  'v1_parse_failure', 'v1_evidence_edge', 'v1_supplier_candidate', 'v1_historical_quote_candidate',
  'gmail_message', 'drive_file', 'quote_document'
));
comment on column evidence.source_record.kind is
  'Closed acquisition vocabulary: where this record came from. gmail_message, drive_file and quote_document are staged, never-trusted observations — they arrive review_status = ''pending'' and reach crm.* only through operator-reviewed promotion (docs/DOMAIN.md §5). A quote_document carries its lines in evidence.document_line (#54).';

-- #54 evidence.document_line — what a quote document says, line by line (durable evidence).
create table evidence.document_line (
  id uuid primary key default gen_random_uuid(),
  source_record_id uuid not null references evidence.source_record (id),
  line_no integer not null,
  parent_item text,
  item_label text,
  kind text,
  brand text,
  model text,
  model_key text,
  description text,
  qty numeric(18,6),
  unit_price numeric(18,4),
  line_total numeric(18,4),
  currency text not null,
  optional boolean not null default false,
  extractor text not null,
  check_status text not null,
  reviewed_by_operator_id uuid references platform.operator (id),
  reviewed_at timestamptz,
  review_note text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint document_line_key unique (source_record_id, line_no),
  constraint document_line_currency_check check (currency in ('CLP', 'USD', 'EUR')),
  constraint document_line_kind_check check (kind is null or kind in
    ('equipment', 'accessory', 'consumable', 'spare_part', 'service', 'freight', 'other')),
  constraint document_line_check_status_check check (check_status in ('verified', 'disputed', 'single_source', 'reviewed')),
  constraint document_line_review_shape check (
    (check_status = 'reviewed') = (reviewed_by_operator_id is not null)
    and (reviewed_by_operator_id is null) = (reviewed_at is null)),
  constraint document_line_money_nonnegative check (coalesce(unit_price, 0) >= 0 and coalesce(line_total, 0) >= 0)
);
comment on table evidence.document_line is
  'DOMAIN.md §7 #54 — line of a quote document (source_record kind quote_document) as extracted and cross-checked; durable evidence; only a disputed line may be reviewed.';
create index document_line_model_key_idx on evidence.document_line (model_key);
create index document_line_reviewed_by_idx on evidence.document_line (reviewed_by_operator_id);
create index document_line_disputed_idx on evidence.document_line (source_record_id) where check_status = 'disputed';

create function evidence.document_line_review_guard() returns trigger
language plpgsql
set search_path = pg_catalog
as $$
begin
  if tg_op = 'INSERT' then
    -- A review is an operator act on a disputed line, never something a line is born with.
    if new.check_status = 'reviewed' or new.reviewed_by_operator_id is not null or new.reviewed_at is not null then
      raise exception 'document_line_review_guard: a line is never inserted as reviewed'
        using errcode = 'check_violation';
    end if;
    return new;
  end if;
  if old.check_status <> 'disputed' or new.check_status <> 'reviewed' then
    raise exception 'document_line_review_guard: only a disputed line may become reviewed'
      using errcode = 'check_violation';
  end if;
  -- Every column outside the API's column-level UPDATE grant is what the document says and who
  -- extracted it. The whole row minus those columns is compared, so the owner and the migrator
  -- are bounded too and a column added later is frozen unless it is named here.
  if (to_jsonb(new) - array['qty', 'unit_price', 'line_total', 'optional', 'check_status',
                            'reviewed_by_operator_id', 'reviewed_at', 'review_note', 'updated_at'])
     is distinct from
     (to_jsonb(old) - array['qty', 'unit_price', 'line_total', 'optional', 'check_status',
                            'reviewed_by_operator_id', 'reviewed_at', 'review_note', 'updated_at']) then
    raise exception 'document_line_review_guard: only qty, unit_price, line_total, optional and the review fields may change'
      using errcode = 'check_violation';
  end if;
  return new;
end;
$$;
comment on function evidence.document_line_review_guard() is
  'Trigger guard for evidence.document_line (BEFORE INSERT OR UPDATE): a line is never inserted reviewed (status reviewed, reviewer or review time); the only update is the one review of a disputed line (disputed → reviewed), which may change only qty, unit_price, line_total, optional, check_status, reviewed_by_operator_id, reviewed_at, review_note and updated_at — every other column, for every role, never changes. SECURITY INVOKER.';
create trigger document_line_review_guard before insert or update on evidence.document_line
  for each row execute function evidence.document_line_review_guard();
create trigger document_line_never_deleted before delete on evidence.document_line
  for each row execute function platform.reject_mutation('never deleted');

-- Grants, RLS, policies.
grant select, insert on catalog.fx_rate to origenlab_api;
grant select, insert on catalog.cost_parameter to origenlab_api;
grant select, insert on evidence.document_line to origenlab_api;
grant update (qty, unit_price, line_total, optional, check_status, reviewed_by_operator_id, reviewed_at, review_note, updated_at)
  on evidence.document_line to origenlab_api;
alter table catalog.fx_rate enable row level security;
alter table catalog.cost_parameter enable row level security;
alter table evidence.document_line enable row level security;
create policy origenlab_api_select on catalog.fx_rate for select to origenlab_api using (true);
create policy origenlab_api_insert on catalog.fx_rate for insert to origenlab_api with check (true);
create policy origenlab_api_select on catalog.cost_parameter for select to origenlab_api using (true);
create policy origenlab_api_insert on catalog.cost_parameter for insert to origenlab_api with check (true);
create policy origenlab_api_select on evidence.document_line for select to origenlab_api using (true);
create policy origenlab_api_insert on evidence.document_line for insert to origenlab_api with check (true);
create policy origenlab_api_update on evidence.document_line for update to origenlab_api using (true) with check (true);

-- Two table comments 20261005134832 left stale: the observation key it widened, and notes on products.
comment on table catalog.supplier_product is 'DOMAIN.md §7 #27 — supplier cost observation, append-only; unique (supplier_organization_id, product_id, as_of, price_kind, min_qty) nulls not distinct; current cost = newest non-stale row by as_of, preferring per-deal kinds (supplier_offer, order_confirmation, purchase_order, costing_sheet, negotiated) on the same day.';
comment on table crm.note is
  'DOMAIN.md §7 #44 — an operator note on a person, institution (including a supplier), commercial case or product (subject_kind product since 20261005134832). Body, author, subject and time never change: an edit is a new row (revision_no + 1, revision_of_note_id → the previous one, root_note_id → the first); an archived note stays. Never deleted.';

-- Domain events: new aggregate kinds and types.
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
         end;
$$;
comment on function crm.domain_event_is_valid(text, text, smallint, jsonb) is
  'CHECK helper for crm.domain_event: payload is an object, payload_version is defined, event_type family matches aggregate_kind — including the three commercial-case families (DOMAIN.md §3.6), campaign_block (§7 #37), note (§7 #44), fx_rate (§7 #52) and cost_parameter (§7 #53). SECURITY INVOKER.';
alter table crm.domain_event drop constraint domain_event_aggregate_kind_check;
alter table crm.domain_event add constraint domain_event_aggregate_kind_check check (aggregate_kind in (
  'organization', 'person', 'affiliation', 'address', 'contact_point', 'organization_relationship',
  'opportunity', 'opportunity_participant', 'opportunity_organization', 'opportunity_interest',
  'opportunity_evidence', 'task', 'activity', 'quote', 'quote_revision',
  'campaign', 'campaign_block', 'send_attempt', 'contact_control', 'send_control', 'operator',
  'assertion', 'source_record', 'product', 'note',
  'fx_rate', 'cost_parameter'
));
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
  'participant.added', 'participant.linked', 'participant.primary_changed', 'participant.ended',
  'case_organization.added', 'case_organization.role_confirmed', 'case_organization.ended',
  'case_interest.added',
  'case_evidence.linked',
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
  'fx_rate.recorded', 'cost_parameter.set', 'source_record.document_line_reviewed'
));

reset role;
