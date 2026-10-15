-- Cotizador PR A (spec 2026-10-15; 10-05 §4.9, §4.11): authored quote lines carry what the engine
-- needs, a revision carries its printed conditions and pricing choices, lines change only while
-- their revision is an authored draft, and a revision holds one exchange rate per currency.
--
--   * crm.quote_line gains item_type, expense_method, calc_inputs, supplier_organization_id,
--     cost_observation_id and line_note_es (all nullable: historical lines carry none of them).
--   * crm.quote_revision gains the printed conditions (warranty, delivery place, installation,
--     client notes, extra terms, validity) and the pricing choices (expense method, allocation
--     mode, principal rule, per-currency FX override reasons).
--   * crm.quote_line_authored_draft_only() guards every insert, update and delete on a line:
--     only while the revision is an authored draft, and one FX snapshot per cost currency.
--     Approved, sent, void and historical-import revisions stay untouched by construction.
--   * origenlab_api may delete a draft's lines (the editor replaces them); the guard above is
--     what keeps that from ever reaching an issued revision.
--   * catalog.cost_parameter accepts the quote numbering seed and the default printed terms.
--   * Events quote_revision.opened and quote_revision.draft_saved. The list below is the live
--     CHECK (last rebuilt by 20261014100000) plus those two.

set role origenlab_owner;

alter table crm.quote_line
  add column item_type text,
  add column expense_method text,
  add column calc_inputs jsonb,
  add column supplier_organization_id uuid references crm.organization (id),
  add column cost_observation_id uuid references catalog.supplier_product (id),
  add column line_note_es text,
  add constraint quote_line_item_type_check check (
    item_type is null or (line_kind = 'item' and item_type in
      ('equipment', 'accessory', 'consumable', 'spare_part', 'service', 'other'))),
  add constraint quote_line_expense_method_check check (
    expense_method is null or (line_kind in ('logistics', 'fee') and expense_method in ('dhl_calc', 'profile', 'manual'))),
  add constraint quote_line_calc_inputs_object check (calc_inputs is null or jsonb_typeof(calc_inputs) = 'object'),
  add constraint quote_line_note_nonblank check (line_note_es is null or length(btrim(line_note_es)) > 0);

create index quote_line_supplier_organization_idx on crm.quote_line (supplier_organization_id);
create index quote_line_cost_observation_idx on crm.quote_line (cost_observation_id);

comment on column crm.quote_line.item_type is 'Item lines: equipment, accessory, consumable, spare_part, service or other (snapshot of the product kind or chosen for a free line).';
comment on column crm.quote_line.expense_method is 'Logistics/fee lines: dhl_calc (computed from weights and the DHL card), profile (the Plantilla amounts) or manual.';
comment on column crm.quote_line.calc_inputs is 'dhl_calc lines: the inputs the amount was computed from, so it can be explained and re-run.';
comment on column crm.quote_line.supplier_organization_id is 'The supplier the unit cost was quoted by, when known.';
comment on column crm.quote_line.cost_observation_id is 'The catalog.supplier_product observation the unit cost came from, when one was used.';
comment on column crm.quote_line.line_note_es is 'Free Spanish note printed under the line, when the operator wrote one.';

alter table crm.quote_revision
  add column warranty_terms_es text,
  add column delivery_place_es text,
  add column installation_terms_es text,
  add column client_notes_es text,
  add column terms_extra jsonb not null default '[]'::jsonb,
  add column validity_days integer,
  add column expense_method text not null default 'profile',
  add column expense_allocation_mode text not null default 'principal',
  add column principal_rule text not null default 'highest_cost_imported_equipment',
  add column fx_override_reasons jsonb not null default '{}'::jsonb,
  add constraint quote_revision_terms_extra_array check (jsonb_typeof(terms_extra) = 'array'),
  add constraint quote_revision_validity_days_range check (validity_days is null or validity_days between 1 and 365),
  add constraint quote_revision_expense_method_check check (expense_method in ('dhl_calc', 'profile', 'manual')),
  add constraint quote_revision_allocation_mode_check check (expense_allocation_mode in ('principal', 'proportional_cost')),
  add constraint quote_revision_principal_rule_check check (principal_rule in
    ('highest_cost_imported_equipment', 'first_eur_equipment_over_threshold', 'manual')),
  add constraint quote_revision_fx_override_reasons_object check (jsonb_typeof(fx_override_reasons) = 'object');

comment on column crm.quote_revision.terms_extra is 'Extra printed condition lines (JSON array of strings) added by the operator for this revision.';
comment on column crm.quote_revision.validity_days is 'Offer validity in days; NULL means the quote_default_validity_days parameter applies.';
comment on column crm.quote_revision.expense_method is 'How import expenses were obtained for this revision: dhl_calc, profile or manual.';
comment on column crm.quote_revision.expense_allocation_mode is 'principal (all expenses on the principal item) or proportional_cost (spread by cost).';
comment on column crm.quote_revision.principal_rule is 'How the principal item was chosen.';
comment on column crm.quote_revision.fx_override_reasons is 'Per currency, why the operator replaced the Banco Central rate (JSON object keyed by currency).';

create function crm.quote_line_authored_draft_only() returns trigger
language plpgsql set search_path = pg_catalog
as $$
declare
  v_revision uuid := case when tg_op = 'DELETE' then old.quote_revision_id else new.quote_revision_id end;
  v_status text;
  v_origin text;
begin
  select r.status, r.origin into v_status, v_origin from crm.quote_revision r where r.id = v_revision;
  if v_origin is distinct from 'authored' or v_status is distinct from 'draft' then
    raise exception 'quote lines change only while their revision is an authored draft'
      using errcode = 'check_violation';
  end if;
  if tg_op = 'UPDATE' and new.quote_revision_id <> old.quote_revision_id then
    raise exception 'a quote line never moves to another revision' using errcode = 'check_violation';
  end if;
  if tg_op <> 'DELETE' and new.cost_currency is not null and exists (
       select 1 from crm.quote_line l
        where l.quote_revision_id = new.quote_revision_id and l.id <> new.id
          and l.cost_currency = new.cost_currency
          and (l.fx_rate, l.fx_as_of, l.fx_source) is distinct from (new.fx_rate, new.fx_as_of, new.fx_source)) then
    raise exception 'one exchange rate per currency in a quote revision' using errcode = 'check_violation';
  end if;
  return case when tg_op = 'DELETE' then old else new end;
end;
$$;
comment on function crm.quote_line_authored_draft_only() is
  'Guard on crm.quote_line: rows are inserted, changed or deleted only while their revision is an authored draft; one exchange rate per cost currency per revision. SECURITY INVOKER.';

create trigger quote_line_authored_draft_only
  before insert or update or delete on crm.quote_line
  for each row execute function crm.quote_line_authored_draft_only();

grant delete on crm.quote_line to origenlab_api;
create policy origenlab_api_delete on crm.quote_line for delete to origenlab_api using (true);

alter table catalog.cost_parameter drop constraint cost_parameter_key_check;
alter table catalog.cost_parameter add constraint cost_parameter_key_check check (key in (
    'dhl_import_discount_pct', 'dhl_fuel_surcharge_pct', 'dhl_customs_clearance_fee_usd',
    'courier_threshold_usd_fob', 'customs_agent_fee_clp', 'duty_rate_general', 'duty_rate_fta',
    'insurance_pct_of_fob', 'domestic_transport_clp',
    'profile_freight_clp', 'profile_agent_clp', 'profile_customs_clp', 'profile_transport_clp',
    'principal_threshold_eur',
    'default_markup_equipment', 'default_markup_accessory', 'default_markup_consumable',
    'default_markup_spare_part', 'default_markup_service',
    'margin_target_min', 'margin_target_max', 'price_deviation_warn_pct', 'iva_rate',
    'quote_number_seed',
    'quote_default_delivery_terms', 'quote_default_validity_days', 'quote_default_payment_terms',
    'quote_default_warranty', 'quote_default_delivery_place', 'quote_default_installation'));

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
  'quote_revision.opened', 'quote_revision.draft_saved'
));

reset role;
