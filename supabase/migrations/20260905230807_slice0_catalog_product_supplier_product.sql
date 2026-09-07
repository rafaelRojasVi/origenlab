-- Slice 0 / M05 — catalog: catalog.product (#26) and catalog.supplier_product (#27).
--
-- docs/DOMAIN.md §4, §7. A manufacturer and a supplier are organizations holding the matching
-- relationship role; there is no separate identity table. supplier_product is an append-only
-- price observation: the current price is the newest row, nothing is ever overwritten.

set role origenlab_owner;

-- #26 catalog.product — manufacturer model.
create table catalog.product (
  id uuid primary key default gen_random_uuid(),
  manufacturer_organization_id uuid not null references crm.organization (id),
  model_number text not null,
  name text,
  description text,
  created_by_operator_id uuid references platform.operator (id),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint product_manufacturer_model_key unique (manufacturer_organization_id, model_number),
  constraint product_model_number_nonblank check (length(btrim(model_number)) > 0)
);

comment on table catalog.product is
  'DOMAIN.md §7 #26 — manufacturer model: (manufacturer_organization_id, model_number) unique; belongs to exactly one manufacturer.';

-- #27 catalog.supplier_product — supplier price observation (append-only).
create table catalog.supplier_product (
  id uuid primary key default gen_random_uuid(),
  supplier_organization_id uuid not null references crm.organization (id),
  product_id uuid not null references catalog.product (id),
  as_of timestamptz not null,
  price numeric(18, 6) not null,
  currency text not null,
  origin_source_record_id uuid references evidence.source_record (id),
  provenance_note text,
  created_at timestamptz not null default now(),
  constraint supplier_product_observation_key unique (supplier_organization_id, product_id, as_of),
  constraint supplier_product_price_nonnegative check (price >= 0),
  constraint supplier_product_currency_shape check (currency ~ '^[A-Z]{3}$'),
  -- Every observation carries a provenance: a source record or an explicit note.
  constraint supplier_product_provenance_present check (
    origin_source_record_id is not null or provenance_note is not null
  )
);

comment on table catalog.supplier_product is
  'DOMAIN.md §7 #27 — supplier price observation: append-only; (supplier_organization_id, product_id, as_of) unique; the current price is the newest row.';

create index supplier_product_product_as_of_idx on catalog.supplier_product (product_id, as_of desc);
create index supplier_product_supplier_idx on catalog.supplier_product (supplier_organization_id);

create trigger supplier_product_append_only
  before update or delete on catalog.supplier_product
  for each row execute function platform.reject_mutation('append-only');

alter table catalog.product enable row level security;
alter table catalog.supplier_product enable row level security;

reset role;
