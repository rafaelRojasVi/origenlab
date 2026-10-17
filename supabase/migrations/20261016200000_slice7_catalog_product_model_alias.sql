-- Slice 7 — catalog.product_model_alias (DOMAIN.md §7 #59): the other names a product's model is
-- printed under.
--
-- A quote line or an email names a model as it is printed («XQ100», «Serie XQ 100»), while the
-- catalog may store the maker's item number as model_number («30001234») or another spelling. Reads
-- that link a line or a text to a product by model_key also follow an active alias. The key is the
-- same normalisation as catalog.product.model_key (a generated column with the same expression).
--
-- Rules the database keeps:
--   * one maker never maps one alias to two products (and never twice to one): at most one active
--     alias per (maker, alias_key) — two makers may share a printed code;
--   * an alias equal to the model_key of a product of the same maker is refused (it would name a
--     product other than its own, or repeat its own model);
--   * never deleted, never rewritten: an alias is retired (retired_at, retired_by_operator_id), once.
-- The maker is the product's (catalog.product.manufacturer_organization_id), so the per-maker key
-- cannot be a plain unique index: the guard below checks it under a transaction-scoped advisory lock
-- on (maker, alias_key). A BEFORE trigger does not see generated columns yet, so the guard computes
-- the key with the same expression.
--
-- Events: product.model_alias_added / product.model_alias_retired (aggregate product). The list
-- below is the live CHECK (last rebuilt by 20261016100000) plus those two.

set role origenlab_owner;

create table catalog.product_model_alias (
  id uuid primary key default gen_random_uuid(),
  product_id uuid not null references catalog.product (id),
  alias text not null,
  alias_key text generated always as (upper(regexp_replace(alias, '[[:space:]_./-]', '', 'g'))) stored,
  source text not null,
  origin_source_record_id uuid references evidence.source_record (id),
  created_by_operator_id uuid not null references platform.operator (id),
  created_at timestamptz not null default now(),
  retired_at timestamptz,
  retired_by_operator_id uuid references platform.operator (id),
  constraint product_model_alias_alias_shape check (length(btrim(alias)) between 1 and 120),
  constraint product_model_alias_key_nonempty check (alias_key <> ''),
  constraint product_model_alias_source_check check (source in ('import', 'operator')),
  constraint product_model_alias_import_has_manifest check (
    (source = 'import') = (origin_source_record_id is not null)),
  constraint product_model_alias_retired_shape check (
    (retired_at is null) = (retired_by_operator_id is null))
);

comment on table catalog.product_model_alias is
  'DOMAIN.md §7 #59 — another name a product''s model is printed under (alias, alias_key = the model_key normalisation). At most one active alias per maker and key; never equal to a model_key of the same maker; never deleted — retired once (retired_at).';

-- The read sites look an alias up by key among the active ones.
create index product_model_alias_key_active_idx
  on catalog.product_model_alias (alias_key) where retired_at is null;
create index product_model_alias_product_idx on catalog.product_model_alias (product_id);
create index product_model_alias_origin_idx on catalog.product_model_alias (origin_source_record_id);
create index product_model_alias_created_by_idx on catalog.product_model_alias (created_by_operator_id);
create index product_model_alias_retired_by_idx on catalog.product_model_alias (retired_by_operator_id);

create function catalog.product_model_alias_guard() returns trigger
language plpgsql
set search_path = pg_catalog
as $$
declare
  v_maker uuid;
  v_key text;
begin
  if tg_op = 'UPDATE' then
    if new.id is distinct from old.id
       or new.product_id is distinct from old.product_id
       or new.alias is distinct from old.alias
       or new.source is distinct from old.source
       or new.origin_source_record_id is distinct from old.origin_source_record_id
       or new.created_by_operator_id is distinct from old.created_by_operator_id
       or new.created_at is distinct from old.created_at
    then
      raise exception 'catalog.product_model_alias: an alias is never rewritten — retire it and add the right one'
        using errcode = 'P0001';
    end if;
    if old.retired_at is not null or new.retired_at is null then
      raise exception 'catalog.product_model_alias: an alias changes only by being retired, once'
        using errcode = 'P0001';
    end if;
    return new;
  end if;

  if new.retired_at is not null or new.retired_by_operator_id is not null then
    raise exception 'catalog.product_model_alias: an alias is added active'
      using errcode = 'P0001';
  end if;
  select p.manufacturer_organization_id into v_maker from catalog.product p where p.id = new.product_id;
  if v_maker is null then
    return new;  -- the foreign key refuses it
  end if;
  v_key := upper(regexp_replace(new.alias, '[[:space:]_./-]', '', 'g'));
  perform pg_advisory_xact_lock(hashtextextended('catalog.product_model_alias:' || v_maker::text || ':' || v_key, 0));
  if exists (select 1 from catalog.product q
              where q.manufacturer_organization_id = v_maker and q.model_key = v_key) then
    raise exception 'catalog.product_model_alias: % is the model of a product of the same maker', v_key
      using errcode = 'P0001';
  end if;
  if exists (select 1 from catalog.product_model_alias b join catalog.product q on q.id = b.product_id
              where b.alias_key = v_key and b.retired_at is null and q.manufacturer_organization_id = v_maker) then
    raise exception 'catalog.product_model_alias: the maker already has an active alias %', v_key
      using errcode = 'P0001';
  end if;
  return new;
end
$$;

comment on function catalog.product_model_alias_guard() is
  'DOMAIN.md §7 #59 — an alias is added active, at most once per maker and key, never equal to a model_key of the same maker, and changes only by being retired once. SECURITY INVOKER.';

revoke all on function catalog.product_model_alias_guard()
  from public, anon, authenticated, service_role;

create trigger product_model_alias_guard
  before insert or update on catalog.product_model_alias
  for each row execute function catalog.product_model_alias_guard();

create trigger product_model_alias_never_deleted
  before delete on catalog.product_model_alias
  for each row execute function platform.reject_mutation('never deleted (retire it via retired_at)');

-- The API adds and retires; the worker reads (the mail triage matches models by alias); no runtime
-- role deletes.
grant select, insert on catalog.product_model_alias to origenlab_api;
grant update (retired_at, retired_by_operator_id) on catalog.product_model_alias to origenlab_api;
grant select on catalog.product_model_alias to origenlab_worker;

alter table catalog.product_model_alias enable row level security;
create policy origenlab_api_select on catalog.product_model_alias for select to origenlab_api using (true);
create policy origenlab_api_insert on catalog.product_model_alias for insert to origenlab_api with check (true);
create policy origenlab_api_update on catalog.product_model_alias for update to origenlab_api using (true) with check (true);
create policy origenlab_worker_select on catalog.product_model_alias for select to origenlab_worker using (true);

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
  'product.model_alias_added', 'product.model_alias_retired'
));

reset role;
