-- Slice 7 / catalog 1a — products, images, supplier observations, supplier terms, notes on products.
-- docs/DOMAIN.md §7 (#26 catalog.product, #27 catalog.supplier_product, new #50 catalog.product_image,
-- #51 catalog.supplier_terms); spec ~/data/…/2026-10-05-catalogo-cotizador-design.md §4.1–4.4, §4.12.
set role origenlab_owner;

-- #26 catalog.product — Spanish content, specs, physical data, search keys.
alter table catalog.product
  add column product_kind text,
  add column category_es text,
  add column name_es text,
  add column description_es text,
  add column specs jsonb not null default '[]'::jsonb,
  add column weight_kg numeric(10,3),
  add column length_cm numeric(8,1),
  add column width_cm numeric(8,1),
  add column height_cm numeric(8,1),
  add column origin_country char(2),
  add column hs_code text,
  add column is_dangerous_goods boolean not null default false,
  add column active boolean not null default true,
  add column content_origin text not null default 'import',
  add column content_confirmed_by_operator_id uuid references platform.operator (id),
  add column content_confirmed_at timestamptz,
  add column version integer not null default 1,
  add column model_key text generated always as (upper(regexp_replace(model_number, '[\s\-_./]', '', 'g'))) stored,
  add column search_tsv tsvector generated always as (
    to_tsvector('spanish'::regconfig, coalesce(name_es, '') || ' ' || coalesce(name, '') || ' ' || coalesce(model_number, '')
                            || ' ' || coalesce(category_es, '') || ' ' || coalesce(description_es, ''))
  ) stored,
  add constraint product_kind_check check (product_kind is null or product_kind in
    ('equipment', 'accessory', 'consumable', 'spare_part', 'service')),
  add constraint product_content_origin_check check (content_origin in ('import', 'machine', 'operator')),
  add constraint product_specs_array check (jsonb_typeof(specs) = 'array'),
  add constraint product_confirmation_shape check (
    (content_confirmed_by_operator_id is null) = (content_confirmed_at is null)),
  add constraint product_origin_country_shape check (origin_country is null or origin_country ~ '^[A-Z]{2}$'),
  add constraint product_physical_nonnegative check (
    coalesce(weight_kg, 0) >= 0 and coalesce(length_cm, 0) >= 0 and coalesce(width_cm, 0) >= 0 and coalesce(height_cm, 0) >= 0),
  add constraint product_version_positive check (version >= 1);

create index product_model_key_idx on catalog.product (model_key);
create index product_search_tsv_idx on catalog.product using gin (search_tsv);
create index product_content_confirmed_by_idx on catalog.product (content_confirmed_by_operator_id);

-- #50 catalog.product_image — photos in the private `catalog` Storage bucket.
create table catalog.product_image (
  id uuid primary key default gen_random_uuid(),
  product_id uuid not null references catalog.product (id),
  storage_bucket text not null default 'catalog',
  storage_path text not null,
  sha256 text not null,
  content_type text not null,
  width_px integer,
  height_px integer,
  sort_order integer not null default 0,
  caption_es text,
  source text not null,
  source_url text,
  status text not null default 'proposed',
  created_by_operator_id uuid references platform.operator (id),
  version integer not null default 1,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint product_image_bucket_check check (storage_bucket = 'catalog'),
  constraint product_image_path_key unique (storage_bucket, storage_path),
  constraint product_image_sha256_shape check (sha256 ~ '^[0-9a-f]{64}$'),
  constraint product_image_content_type_check check (content_type in ('image/jpeg', 'image/png', 'image/webp')),
  constraint product_image_source_check check (source in ('upload', 'datasheet', 'manufacturer_site')),
  constraint product_image_status_check check (status in ('proposed', 'confirmed', 'hidden')),
  constraint product_image_version_positive check (version >= 1)
);
comment on table catalog.product_image is
  'DOMAIN.md §7 #50 — product photo stored in the private catalog bucket; never deleted (hide via status); machine-proposed rows have created_by_operator_id null.';
create index product_image_product_idx on catalog.product_image (product_id, sort_order);
create index product_image_created_by_idx on catalog.product_image (created_by_operator_id);
create trigger product_image_never_deleted before delete on catalog.product_image
  for each row execute function platform.reject_mutation('never deleted; hide the image instead');

-- #27 catalog.supplier_product — richer observation; still append-only.
alter table catalog.supplier_product
  add column price_kind text not null default 'dealer_net',
  add column list_price numeric(18,6),
  add column discount_pct numeric(9,6),
  add column incoterm text,
  add column valid_until date,
  add column min_qty numeric(18,6),
  add column is_stale boolean not null default false,
  add column source_document text,
  add column recorded_by_operator_id uuid references platform.operator (id),
  add constraint supplier_product_price_kind_check check (price_kind in
    ('dealer_net', 'list', 'map', 'supplier_offer', 'order_confirmation', 'purchase_order', 'costing_sheet', 'negotiated')),
  add constraint supplier_product_discount_range check (discount_pct is null or (discount_pct >= 0 and discount_pct < 1)),
  add constraint supplier_product_min_qty_positive check (min_qty is null or min_qty > 0);

-- Slice 0 (20260905230807) named the old (supplier, product, as_of) key explicitly; the wider key
-- replaces it under the same name.
alter table catalog.supplier_product drop constraint supplier_product_observation_key;
alter table catalog.supplier_product add constraint supplier_product_observation_key
  unique nulls not distinct (supplier_organization_id, product_id, as_of, price_kind, min_qty);
create index supplier_product_recorded_by_idx on catalog.supplier_product (recorded_by_operator_id);

-- #51 catalog.supplier_terms — commercial terms per supplier organization.
create table catalog.supplier_terms (
  supplier_organization_id uuid primary key references crm.organization (id),
  currency text not null,
  origin_country char(2),
  route text not null,
  incoterm text,
  default_discount_pct numeric(9,6),
  packing_pct numeric(9,6),
  map_enforced boolean not null default false,
  default_lead_time_es text,
  notes text,
  version integer not null default 1,
  updated_by_operator_id uuid references platform.operator (id),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint supplier_terms_currency_check check (currency in ('EUR', 'USD', 'CLP')),
  constraint supplier_terms_route_check check (route in ('import_courier', 'import_freight', 'domestic')),
  constraint supplier_terms_origin_country_shape check (origin_country is null or origin_country ~ '^[A-Z]{2}$'),
  constraint supplier_terms_pct_range check (
    coalesce(default_discount_pct, 0) between 0 and 0.99 and coalesce(packing_pct, 0) between 0 and 0.5),
  constraint supplier_terms_version_positive check (version >= 1)
);
comment on table catalog.supplier_terms is
  'DOMAIN.md §7 #51 — supplier commercial terms (currency, origin, import route, default discount, packing, MAP); never deleted.';
create index supplier_terms_updated_by_idx on catalog.supplier_terms (updated_by_operator_id);
create trigger supplier_terms_never_deleted before delete on catalog.supplier_terms
  for each row execute function platform.reject_mutation('never deleted');

-- #44 crm.note — notes on products.
alter table crm.note drop constraint note_subject_kind_check;
alter table crm.note add constraint note_subject_kind_check
  check (subject_kind in ('person', 'organization', 'opportunity', 'product'));

-- Grants, RLS, policies.
grant select, insert on catalog.product_image to origenlab_api;
grant update (sort_order, caption_es, status, version, updated_at) on catalog.product_image to origenlab_api;
grant select, insert on catalog.supplier_terms to origenlab_api;
grant update (currency, origin_country, route, incoterm, default_discount_pct, packing_pct, map_enforced,
              default_lead_time_es, notes, version, updated_by_operator_id, updated_at) on catalog.supplier_terms to origenlab_api;
alter table catalog.product_image enable row level security;
alter table catalog.supplier_terms enable row level security;
create policy origenlab_api_select on catalog.product_image for select to origenlab_api using (true);
create policy origenlab_api_insert on catalog.product_image for insert to origenlab_api with check (true);
create policy origenlab_api_update on catalog.product_image for update to origenlab_api using (true) with check (true);
create policy origenlab_api_select on catalog.supplier_terms for select to origenlab_api using (true);
create policy origenlab_api_insert on catalog.supplier_terms for insert to origenlab_api with check (true);
create policy origenlab_api_update on catalog.supplier_terms for update to origenlab_api using (true) with check (true);

-- Domain events: full list restated (from 20260930120000…:492-529) plus product.updated, product.content_confirmed,
-- product.image_added, product.image_updated, product.cost_recorded, organization.supplier_terms_set.
-- domain_event_is_valid needs no change: the product and organization families already exist.
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
  'organization.supplier_terms_set'
));

reset role;
