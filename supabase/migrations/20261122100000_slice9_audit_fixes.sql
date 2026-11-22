-- Slice 9 — audit fixes (2026-11-22): Finanzas writes, optimistic concurrency, a voided revision
-- gives the client's quote back, the note repoint exception, generic partner names, model spend.
--
--   * crm.domain_event: aggregate kinds 'expense', 'supplier_order', 'monthly_target' and
--     'partner_rule' (also taught to crm.domain_event_is_valid) and their six event types
--     (expense.created / expense.updated, supplier_order.created / supplier_order.updated,
--     monthly_target.set, partner_rule.set), which the four Finanzas commands of
--     20261112100000_slice9_finance_schema.sql have written since it shipped — every one of them
--     failed the checks. Plus 'quote_revision.supersession_cleared' (below). Full lists as left by
--     20261030100000_slice7_invoice.sql (kinds) and 20261118100000_slice8_opportunity_follow_up.sql
--     (types), plus the new values.
--   * crm.monthly_target.aggregate_id, crm.partner_rule.aggregate_id: a uuid per row (default
--     gen_random_uuid(), unique), because crm.domain_event.aggregate_id is a uuid and neither table
--     has one (a month; a bigint identity). Existing rows get one each.
--   * crm.expense.version, crm.supplier_order.version (int, default 1, >= 1): the commands update
--     at the version the operator saw, as every other V2 command does.
--   * crm.supplier_order_clp_shape: a CLP order states its amount in pesos (amount_clp > 0); a
--     foreign-currency one may leave it empty until known. A CLP row without one is first given its
--     original amount (the value the API has always written).
--   * Partner names out of the schema: crm.expense.paid_by takes 'empresa', 'partner_a' or
--     'partner_b' (rows rewritten); crm.partner_rule.sueldo_* → partner_a_salary, tasa_* →
--     partner_b_rate; arriendo_clp loses its default (an amount is an operator's input, never a
--     schema constant). The constraint on the salary is renamed with it.
--   * crm.quote_revision_authored_frozen() and crm.quote_revision_historical_guard(): supersession
--     is still recorded once, but it may be cleared — both columns back to null — when the revision
--     that superseded it is void. Voiding an approved revision (apps/api v2/quotes/commands.py)
--     clears the supersession it recorded in the same transaction, with one
--     quote_revision.supersession_cleared event per revision, so the quote the client holds is
--     current again.
--   * crm.note_guard(): the identity-repoint exception now also requires the archive columns
--     unchanged, subject_kind 'person' on both sides, and the two subjects to be a merge pair (the
--     old subject merged into the new one, or — the undo — the new one merged into the old).
--   * platform.model_usage_feature_check: also 'triage', 'case_pick', 'subject_memory' and
--     'quote_read', the worker's model calls.
--   No table, policy, function or foreign key is added: the functions above are replaced.

set role origenlab_owner;

-- ── Finanzas: uuid aggregate ids ─────────────────────────────────────────────────────────────
alter table crm.monthly_target
  add column aggregate_id uuid not null default gen_random_uuid(),
  add constraint monthly_target_aggregate_id_key unique (aggregate_id);
comment on column crm.monthly_target.aggregate_id is
  'The uuid crm.domain_event names this month''s target by (aggregate_kind monthly_target).';

alter table crm.partner_rule
  add column aggregate_id uuid not null default gen_random_uuid(),
  add constraint partner_rule_aggregate_id_key unique (aggregate_id);
comment on column crm.partner_rule.aggregate_id is
  'The uuid crm.domain_event names this rule by (aggregate_kind partner_rule).';

-- ── Finanzas: optimistic concurrency ─────────────────────────────────────────────────────────
alter table crm.expense
  add column version integer not null default 1,
  add constraint expense_version_positive check (version >= 1);
alter table crm.supplier_order
  add column version integer not null default 1,
  add constraint supplier_order_version_positive check (version >= 1);

-- ── Finanzas: a CLP order states its pesos ───────────────────────────────────────────────────
update crm.supplier_order
   set amount_clp = round(amount_original)::integer
 where currency = 'CLP' and amount_clp is null;
alter table crm.supplier_order drop constraint supplier_order_clp_shape;
-- Written NULL-proof: a CHECK that evaluates to NULL passes.
alter table crm.supplier_order add constraint supplier_order_clp_shape check (
  (currency = 'CLP' and amount_clp is not null and amount_clp > 0)
  or (currency <> 'CLP' and (amount_clp is null or amount_clp > 0))
);

-- ── Finanzas: generic partner names ──────────────────────────────────────────────────────────
alter table crm.expense drop constraint expense_paid_by_check;
update crm.expense
   set paid_by = case paid_by when 'tatiana' then 'partner_a' when 'sebastian' then 'partner_b' end
 where paid_by in ('tatiana', 'sebastian');
alter table crm.expense add constraint expense_paid_by_check
  check (paid_by in ('empresa', 'partner_a', 'partner_b'));
comment on column crm.expense.paid_by is
  'Who paid it: empresa, or a partner (partner_a, partner_b).';

alter table crm.partner_rule rename column sueldo_tatiana to partner_a_salary;
alter table crm.partner_rule rename column tasa_sebastian to partner_b_rate;
alter table crm.partner_rule rename constraint partner_rule_sueldo_nonneg to partner_rule_partner_a_salary_nonneg;
alter table crm.partner_rule alter column arriendo_clp drop default;
comment on column crm.partner_rule.partner_a_salary is
  'Monthly salary paid to partner A before the split (opción B/C); 0 = none.';
comment on column crm.partner_rule.partner_b_rate is
  'Monthly rate partner B charges on the capital lent (opción D); null = not lending.';
comment on column crm.partner_rule.arriendo_clp is
  'The monthly rent the rule splits, in CLP; set by the admin with each rule (no default).';

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
           when 'expense'                   then 'expense'
           when 'supplier_order'            then 'supplier_order'
           when 'monthly_target'            then 'monthly_target'
           when 'partner_rule'              then 'partner_rule'
         end;
$$;
comment on function crm.domain_event_is_valid(text, text, smallint, jsonb) is
  'CHECK helper for crm.domain_event: payload is an object, payload_version is defined, event_type family matches aggregate_kind — including the three commercial-case families (DOMAIN.md §3.6), campaign_block (§7 #37), note (§7 #44), fx_rate (§7 #52), cost_parameter (§7 #53), bidder (§7 #60–#61), notice (§7 #28), todo (§7 #68), invoice (§7 #70) and the four Finanzas aggregates expense, supplier_order, monthly_target, partner_rule (§8 #73–#76). SECURITY INVOKER.';

alter table crm.domain_event drop constraint domain_event_aggregate_kind_check;
alter table crm.domain_event add constraint domain_event_aggregate_kind_check check (aggregate_kind in (
  'organization', 'person', 'affiliation', 'address', 'contact_point', 'organization_relationship',
  'opportunity', 'opportunity_participant', 'opportunity_organization', 'opportunity_interest',
  'opportunity_evidence', 'task', 'activity', 'quote', 'quote_revision',
  'campaign', 'campaign_block', 'send_attempt', 'contact_control', 'send_control', 'operator',
  'assertion', 'source_record', 'product', 'note',
  'fx_rate', 'cost_parameter',
  'bidder', 'notice', 'todo', 'invoice',
  'expense', 'supplier_order', 'monthly_target', 'partner_rule'
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
  'invoice.imported', 'invoice.case_set', 'invoice.paid', 'invoice.unpaid',
  'opportunity.origin_set',
  'opportunity.pause_set',
  'opportunity.follow_up_set',
  'expense.created', 'expense.updated', 'supplier_order.created', 'supplier_order.updated',
  'monthly_target.set', 'partner_rule.set',
  'quote_revision.supersession_cleared'
));

-- ── a voided revision gives the client's quote back ──────────────────────────────────────────
-- 20261016100000_slice7_quote_authoring.sql's guard, with one change: an authored revision's
-- supersession is recorded once and cleared at most when its superseding revision is void.
create or replace function crm.quote_revision_authored_frozen() returns trigger
language plpgsql set search_path = pg_catalog
as $$
declare
  v_mutable constant text[] := array['status', 'pdf_sha256', 'sent_at', 'sent_attempt_id', 'sent_message_id',
                                     'superseded_by_revision_no', 'superseded_at', 'version', 'updated_at'];
begin
  if tg_op = 'DELETE' then
    if old.origin = 'authored' and old.status in ('approved', 'sent', 'void') then
      raise exception 'crm.quote_revision %: an issued revision is never deleted', old.id
        using errcode = 'check_violation';
    end if;
    return old;
  end if;
  if old.origin <> 'authored' or old.status not in ('approved', 'sent', 'void') then
    return new;
  end if;

  if (to_jsonb(new) - v_mutable) is distinct from (to_jsonb(old) - v_mutable) then
    raise exception 'crm.quote_revision %: an issued revision is frozen', old.id using errcode = 'check_violation';
  end if;
  if new.status is distinct from old.status
     and not ((old.status = 'approved' and new.status in ('sent', 'void')) or (old.status = 'sent' and new.status = 'void')) then
    raise exception 'crm.quote_revision %: an issued revision moves only approved → sent and approved | sent → void, not % → %',
      old.id, old.status, new.status using errcode = 'check_violation';
  end if;
  if (new.sent_at, new.sent_attempt_id, new.sent_message_id)
       is distinct from (old.sent_at, old.sent_attempt_id, old.sent_message_id)
     and not (old.status = 'approved' and new.status = 'sent'
              and num_nonnulls(old.sent_at, old.sent_attempt_id, old.sent_message_id) = 0) then
    raise exception 'crm.quote_revision %: the sending evidence is recorded once, on approved → sent', old.id
      using errcode = 'check_violation';
  end if;
  if new.pdf_sha256 is distinct from old.pdf_sha256
     and not (old.pdf_sha256 is null and old.status = 'approved' and new.status in ('approved', 'sent')) then
    raise exception 'crm.quote_revision %: the document hash is recorded once, while approved', old.id
      using errcode = 'check_violation';
  end if;
  if old.superseded_by_revision_no is not null
     and (new.superseded_by_revision_no, new.superseded_at) is distinct from (old.superseded_by_revision_no, old.superseded_at)
     and not (new.superseded_by_revision_no is null and new.superseded_at is null
              and exists (select 1 from crm.quote_revision s
                           where s.quote_id = old.quote_id and s.revision_no = old.superseded_by_revision_no
                             and s.status = 'void')) then
    raise exception 'crm.quote_revision %: supersession is recorded once, and cleared only when its superseding revision is void',
      old.id using errcode = 'check_violation';
  end if;
  return new;
end;
$$;
comment on function crm.quote_revision_authored_frozen() is
  'Guard on crm.quote_revision: an authored revision once approved, sent or void changes only by approved → sent (sending evidence once), approved | sent → void, pdf_sha256 once, supersession once (cleared only when the superseding revision is void), version/updated_at; never deleted. SECURITY INVOKER.';

-- 20261006120000_slice7_historical_current_revision_choice.sql's guard, with the same change.
create or replace function crm.quote_revision_historical_guard() returns trigger
language plpgsql
set search_path = pg_catalog
as $$
declare
  superseding record;
begin
  if tg_op = 'DELETE' then
    if old.origin = 'historical_import' then
      raise exception 'crm.quote_revision %: a historical revision is never deleted — void it instead', old.id
        using errcode = 'P0001';
    end if;
    return old;
  end if;

  if old.origin is distinct from new.origin then
    raise exception 'crm.quote_revision %: origin is fixed at insert', old.id using errcode = 'P0001';
  end if;
  if old.origin <> 'historical_import' then
    return new;
  end if;

  if (new.quote_id, new.revision_no, new.pdf_sha256, new.sent_at, new.origin_source_record_id,
      new.quote_currency, new.price_decimals, new.subtotal, new.discount_total, new.tax_base,
      new.tax_total, new.grand_total, new.party_snapshot, new.party_snapshot_version,
      new.created_by_operator_id, new.created_at)
     is distinct from
     (old.quote_id, old.revision_no, old.pdf_sha256, old.sent_at, old.origin_source_record_id,
      old.quote_currency, old.price_decimals, old.subtotal, old.discount_total, old.tax_base,
      old.tax_total, old.grand_total, old.party_snapshot, old.party_snapshot_version,
      old.created_by_operator_id, old.created_at)
  then
    raise exception 'crm.quote_revision %: a historical revision records a document and is immutable', old.id
      using errcode = 'P0001';
  end if;
  if old.superseded_by_revision_no is not null
     and new.superseded_by_revision_no is distinct from old.superseded_by_revision_no
     and not (new.superseded_by_revision_no is null
              and exists (select 1 from crm.quote_revision s
                           where s.quote_id = old.quote_id and s.revision_no = old.superseded_by_revision_no
                             and s.status = 'void')) then
    raise exception 'crm.quote_revision %: supersession is recorded once, and cleared only when its superseding revision is void',
      old.id using errcode = 'P0001';
  end if;
  if old.superseded_by_revision_no is null and new.superseded_by_revision_no is not null then
    select r.status, r.superseded_by_revision_no
      into superseding
      from crm.quote_revision r
     where r.quote_id = new.quote_id and r.revision_no = new.superseded_by_revision_no
       for share;
    if found and (superseding.status not in ('approved', 'sent')
                  or superseding.superseded_by_revision_no is not null) then
      raise exception 'crm.quote_revision %: superseded only by a current revision; revision % is %',
        old.id, new.superseded_by_revision_no,
        case when superseding.superseded_by_revision_no is not null then 'superseded'
             else superseding.status end
        using errcode = 'P0001';
    end if;
  end if;
  if new.status <> old.status and not (old.status = 'sent' and new.status = 'void') then
    raise exception 'crm.quote_revision %: a historical revision moves only sent → void, not % → %',
      old.id, old.status, new.status
      using errcode = 'P0001';
  end if;
  return new;
end
$$;
comment on function crm.quote_revision_historical_guard() is
  'A historical_import revision is immutable except supersession (once, only by a current revision, and cleared only when that revision is void) and sent → void; never deleted. SECURITY INVOKER.';

-- ── crm.note: the repoint exception is a merge's, and nothing else ───────────────────────────
-- 20261119100000_slice8_note_call_occurred_at.sql's guard. The exception lets merge-people move a
-- person's notes to the person it was merged into (crm.person.merged_into_person_id is set first),
-- and its undo move them back (before the merge is cleared). Every other column is frozen.
create or replace function crm.note_guard() returns trigger
language plpgsql
set search_path = pg_catalog
as $$
begin
  if tg_op = 'DELETE' then
    raise exception 'crm.note is never deleted; archive it instead' using errcode = 'P0001';
  end if;
  if tg_op = 'UPDATE' then
    if new.subject_id is distinct from old.subject_id
       and old.subject_kind = 'person' and new.subject_kind = 'person'
       and new.body = old.body
       and new.author_operator_id = old.author_operator_id
       and new.root_note_id is not distinct from old.root_note_id
       and new.revision_of_note_id is not distinct from old.revision_of_note_id
       and new.revision_no = old.revision_no
       and new.status = old.status
       and new.version = old.version
       and new.created_at = old.created_at
       and new.archived_at is not distinct from old.archived_at
       and new.archived_by_operator_id is not distinct from old.archived_by_operator_id
       and new.archive_reason is not distinct from old.archive_reason
       and new.note_kind is not distinct from old.note_kind
       and new.call_outcome is not distinct from old.call_outcome
       and new.call_next_step is not distinct from old.call_next_step
       and new.occurred_at is not distinct from old.occurred_at
       and exists (select 1 from crm.person p
                    where (p.id = old.subject_id and p.merged_into_person_id = new.subject_id)
                       or (p.id = new.subject_id and p.merged_into_person_id = old.subject_id)) then
      return new;  -- a merge (or its undo) repoints the note; version stays the same
    end if;
    if new.body is distinct from old.body
       or new.author_operator_id is distinct from old.author_operator_id
       or new.subject_kind is distinct from old.subject_kind
       or new.subject_id is distinct from old.subject_id
       or new.root_note_id is distinct from old.root_note_id
       or new.revision_of_note_id is distinct from old.revision_of_note_id
       or new.revision_no is distinct from old.revision_no
       or new.created_at is distinct from old.created_at
       or new.note_kind is distinct from old.note_kind
       or new.call_outcome is distinct from old.call_outcome
       or new.call_next_step is distinct from old.call_next_step
       or new.occurred_at is distinct from old.occurred_at then
      raise exception 'crm.note % is written once: revise it with a new row' , old.id using errcode = 'P0001';
    end if;
    if old.status = 'archived' and new.status = 'archived'
       and (new.archived_at, new.archived_by_operator_id, new.archive_reason)
           is distinct from (old.archived_at, old.archived_by_operator_id, old.archive_reason) then
      raise exception 'crm.note % archive record is immutable', old.id using errcode = 'P0001';
    end if;
    if new.version <> old.version + 1 then
      raise exception 'crm.note % version must advance by one', old.id using errcode = 'P0001';
    end if;
  end if;
  return new;
end;
$$;

comment on function crm.note_guard() is
  'Trigger guard for crm.note: never deleted; body, author, subject, chain, time and the call columns (note_kind, call_outcome, call_next_step, occurred_at) never change — except a person note repointed by a merge (old subject merged into the new, or the undo), with every other column unchanged; only status/archive fields move, once, and version advances by exactly one. SECURITY INVOKER.';

-- ── platform.model_usage: the worker's model calls ───────────────────────────────────────────
alter table platform.model_usage drop constraint model_usage_feature_check;
alter table platform.model_usage add constraint model_usage_feature_check
  check (feature in ('licitaciones', 'prospectos', 'triage', 'case_pick', 'subject_memory', 'quote_read'));

reset role;
