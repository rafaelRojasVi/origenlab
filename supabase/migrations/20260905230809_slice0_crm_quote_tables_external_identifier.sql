-- Slice 0 / M07 — crm quotation: quote (#12), quote_revision (#13), quote_line (#14) and
-- external_identifier (#4), plus the opportunity → won revision links.
--
-- docs/DOMAIN.md §2.6 (external identifiers), §3.5 (quotes), §7; docs/WORKFLOWS.md §1.2 (revision
-- status), §W3a (arithmetic), §W3b (party snapshot). Declarative here: numbering, one open revision
-- per quote, snapshot/totals/approval/sent-evidence presence shapes, supersession shape, principal
-- and allocation rules on lines, and typed-FK-only external identifiers. The frozen-revision
-- trigger, the totals recomputation trigger and the snapshot key validator are Slice 3
-- (docs/MIGRATION.md §5). sent_attempt_id and sent_message_id gain their foreign keys when the
-- comms and outbound tables exist.

set role origenlab_owner;

-- #12 crm.quote — numbered offer for one opportunity.
create table crm.quote (
  id uuid primary key default gen_random_uuid(),
  opportunity_id uuid not null references crm.opportunity (id),
  quote_number text not null,
  created_by_operator_id uuid references platform.operator (id),
  origin_source_record_id uuid references evidence.source_record (id),
  version integer not null default 1,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint quote_number_key unique (quote_number),
  -- Lets crm.opportunity assert that its won quote is one of its own quotes.
  constraint quote_id_opportunity_key unique (id, opportunity_id),
  constraint quote_number_shape check (quote_number ~ '^[A-Za-z0-9][A-Za-z0-9._/-]{0,31}$'),
  constraint quote_version_positive check (version >= 1)
);

comment on table crm.quote is
  'DOMAIN.md §7 #12 — numbered offer for one opportunity: number unique; the opportunity has an organization (Slice 3 command precondition).';

create index quote_opportunity_idx on crm.quote (opportunity_id);

-- #13 crm.quote_revision — immutable priced snapshot with its party snapshot.
create table crm.quote_revision (
  id uuid primary key default gen_random_uuid(),
  quote_id uuid not null references crm.quote (id),
  revision_no integer not null,
  status text not null default 'draft',
  -- Customer-facing currency and tax (W3a).
  quote_currency text not null,
  price_decimals smallint not null,
  tax_rate numeric(6, 4) not null default 0.19,
  rounding_rule text not null default 'half_up',
  -- Stored totals, computed and frozen at approval.
  subtotal numeric(18, 4),
  discount_total numeric(18, 4),
  tax_base numeric(18, 4),
  tax_total numeric(18, 4),
  grand_total numeric(18, 4),
  totals_computed_at timestamptz,
  -- Commercial terms, frozen with the party snapshot.
  payment_terms text,
  valid_until date,
  delivery_terms text,
  tax_note text,
  -- Party lineage: which rows were used, never what the document says (W3b).
  billing_address_id uuid references crm.address (id),
  delivery_address_id uuid references crm.address (id),
  recipient_participant_id uuid references crm.opportunity_participant (id),
  signatory_participant_id uuid references crm.opportunity_participant (id),
  party_snapshot jsonb,
  party_snapshot_version smallint,
  -- Immutable proof of what was sent.
  pdf_sha256 text,
  sent_attempt_id uuid,
  sent_message_id uuid,
  sent_at timestamptz,
  approved_at timestamptz,
  approved_by_operator_id uuid references platform.operator (id),
  -- Supersession is a fact, not a status.
  superseded_by_revision_no integer,
  superseded_at timestamptz,
  created_by_operator_id uuid references platform.operator (id),
  version integer not null default 1,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint quote_revision_quote_no_key unique (quote_id, revision_no),
  constraint quote_revision_no_positive check (revision_no >= 1),
  constraint quote_revision_status_check check (status in ('draft', 'in_review', 'approved', 'sent', 'void')),
  constraint quote_revision_currency_shape check (quote_currency ~ '^[A-Z]{3}$'),
  constraint quote_revision_price_decimals_check check (price_decimals in (0, 2)),
  constraint quote_revision_tax_rate_range check (tax_rate >= 0 and tax_rate < 1),
  constraint quote_revision_rounding_rule_check check (rounding_rule = 'half_up'),
  constraint quote_revision_totals_present_once_approved check (
    status not in ('approved', 'sent') or (
      subtotal is not null and discount_total is not null and tax_base is not null
      and tax_total is not null and grand_total is not null and totals_computed_at is not null
    )
  ),
  -- Party snapshot: NULL while draft/in_review, NOT NULL from approved (W3b).
  constraint quote_revision_snapshot_null_while_open check (
    status not in ('draft', 'in_review') or party_snapshot is null
  ),
  constraint quote_revision_snapshot_present_once_approved check (
    status not in ('approved', 'sent') or party_snapshot is not null
  ),
  constraint quote_revision_snapshot_version_shape check ((party_snapshot is null) = (party_snapshot_version is null)),
  constraint quote_revision_snapshot_object check (party_snapshot is null or jsonb_typeof(party_snapshot) = 'object'),
  constraint quote_revision_approval_shape check (
    status not in ('approved', 'sent') or (approved_at is not null and approved_by_operator_id is not null)
  ),
  constraint quote_revision_pdf_sha256_shape check (pdf_sha256 is null or pdf_sha256 ~ '^[0-9a-f]{64}$'),
  -- sent requires pdf_sha256 and exactly one of sent_attempt_id / sent_message_id (§1.2).
  constraint quote_revision_sent_evidence_shape check (
    case when status = 'sent'
      then pdf_sha256 is not null and sent_at is not null and num_nonnulls(sent_attempt_id, sent_message_id) = 1
      else sent_attempt_id is null and sent_message_id is null and sent_at is null
    end
  ),
  constraint quote_revision_sent_attempt_key unique (sent_attempt_id),
  constraint quote_revision_sent_message_key unique (sent_message_id),
  constraint quote_revision_supersession_shape check ((superseded_by_revision_no is null) = (superseded_at is null)),
  constraint quote_revision_supersession_forward check (
    superseded_by_revision_no is null or superseded_by_revision_no > revision_no
  ),
  constraint quote_revision_supersession_only_issued check (
    superseded_by_revision_no is null or status in ('approved', 'sent')
  ),
  constraint quote_revision_superseded_by_fkey foreign key (quote_id, superseded_by_revision_no)
    references crm.quote_revision (quote_id, revision_no),
  constraint quote_revision_version_positive check (version >= 1)
);

comment on table crm.quote_revision is
  'DOMAIN.md §7 #13 — immutable priced snapshot with its party snapshot: one open revision per quote; snapshot NULL while open and NOT NULL from approved; sent ⇒ pdf_sha256 + exactly one sending evidence id; frozen once approved (Slice 3 trigger).';

-- At most one revision per quote in {draft, in_review}.
create unique index quote_revision_one_open_per_quote
  on crm.quote_revision (quote_id) where status in ('draft', 'in_review');
create index quote_revision_quote_idx on crm.quote_revision (quote_id, revision_no desc);

-- #14 crm.quote_line — items, logistics, fees, discounts with their own currency and FX.
create table crm.quote_line (
  id uuid primary key default gen_random_uuid(),
  quote_revision_id uuid not null references crm.quote_revision (id),
  line_no integer not null,
  line_kind text not null,
  -- Equals line_no for item lines only; the allocation FK below can therefore only reach an item.
  item_line_no integer generated always as (case when line_kind = 'item' then line_no end) stored,
  description text not null,
  product_id uuid references catalog.product (id),
  quantity numeric(18, 6) not null,
  -- Supplier cost currency and FX snapshot (all five or none).
  cost_currency text,
  unit_cost numeric(18, 6),
  fx_rate numeric(18, 8),
  fx_as_of date,
  fx_source text,
  margin_mode text not null default 'none',
  margin_pct numeric(9, 6),
  unit_price_qc numeric(18, 4),
  line_total_qc numeric(18, 4),
  is_principal boolean not null default false,
  allocated_to_line_no integer,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint quote_line_revision_line_key unique (quote_revision_id, line_no),
  constraint quote_line_revision_item_line_key unique (quote_revision_id, item_line_no),
  constraint quote_line_no_positive check (line_no >= 1),
  constraint quote_line_kind_check check (line_kind in ('item', 'logistics', 'fee', 'discount')),
  constraint quote_line_description_nonblank check (length(btrim(description)) > 0),
  constraint quote_line_quantity_positive check (quantity > 0),
  constraint quote_line_cost_snapshot_all_or_none check (
    num_nonnulls(cost_currency, unit_cost, fx_rate, fx_as_of, fx_source) in (0, 5)
  ),
  constraint quote_line_cost_currency_shape check (cost_currency is null or cost_currency ~ '^[A-Z]{3}$'),
  constraint quote_line_unit_cost_nonnegative check (unit_cost is null or unit_cost >= 0),
  constraint quote_line_fx_rate_positive check (fx_rate is null or fx_rate > 0),
  constraint quote_line_fx_source_check check (fx_source is null or fx_source in ('bcentral', 'supplier_quote', 'manual')),
  constraint quote_line_margin_mode_check check (margin_mode in ('margin', 'markup', 'none')),
  constraint quote_line_margin_shape check (
    case margin_mode
      when 'none'   then margin_pct is null
      when 'margin' then margin_pct >= 0 and margin_pct < 1
      when 'markup' then margin_pct >= 0
    end
  ),
  -- A principal is always an item line.
  constraint quote_line_principal_is_item check (not is_principal or line_kind = 'item'),
  -- Only logistics lines allocate, never to themselves, and only to an item line of the same revision.
  constraint quote_line_allocation_only_logistics check (allocated_to_line_no is null or line_kind = 'logistics'),
  constraint quote_line_allocation_not_self check (allocated_to_line_no is distinct from line_no),
  constraint quote_line_allocation_target_is_item_fkey foreign key (quote_revision_id, allocated_to_line_no)
    references crm.quote_line (quote_revision_id, item_line_no)
);

comment on table crm.quote_line is
  'DOMAIN.md §7 #14 — items, logistics, fees, discounts with their own currency and FX: at most one principal item; a logistics allocation target is an item line of the same revision.';

-- A quote revision may have zero or one principal item.
create unique index quote_line_one_principal_per_revision
  on crm.quote_line (quote_revision_id) where is_principal;
create index quote_line_product_idx on crm.quote_line (product_id) where product_id is not null;

-- #4 crm.external_identifier — RUT, ChileCompra codes, Drive ids, V1 ids.
create table crm.external_identifier (
  id uuid primary key default gen_random_uuid(),
  scheme text not null,
  value_norm text not null,
  organization_id uuid references crm.organization (id),
  person_id uuid references crm.person (id),
  opportunity_id uuid references crm.opportunity (id),
  quote_id uuid references crm.quote (id),
  origin_source_record_id uuid references evidence.source_record (id),
  created_at timestamptz not null default now(),
  constraint external_identifier_scheme_value_key unique (scheme, value_norm),
  constraint external_identifier_scheme_check check (scheme in (
    'rut', 'chilecompra_buyer_code', 'chilecompra_supplier_code', 'drive_folder',
    'v1_organization', 'v1_contact', 'v1_opportunity', 'v1_quote', 'v1_supplier_master'
  )),
  constraint external_identifier_value_nonblank check (length(btrim(value_norm)) > 0),
  -- Four real typed foreign keys, exactly one set. No polymorphic subject column (DOMAIN.md §2.6).
  constraint external_identifier_exactly_one_subject check (
    num_nonnulls(organization_id, person_id, opportunity_id, quote_id) = 1
  )
);

comment on table crm.external_identifier is
  'DOMAIN.md §7 #4 — RUT, ChileCompra codes, Drive ids, V1 ids: (scheme, value_norm) unique; exactly one typed FK subject.';

create index external_identifier_organization_idx on crm.external_identifier (organization_id) where organization_id is not null;
create index external_identifier_person_idx on crm.external_identifier (person_id) where person_id is not null;
create index external_identifier_opportunity_idx on crm.external_identifier (opportunity_id) where opportunity_id is not null;
create index external_identifier_quote_idx on crm.external_identifier (quote_id) where quote_id is not null;

-- The won quote belongs to this opportunity, and the won revision exists on that quote.
alter table crm.opportunity
  add constraint opportunity_won_quote_belongs_fkey
    foreign key (won_quote_id, id) references crm.quote (id, opportunity_id),
  add constraint opportunity_won_revision_fkey
    foreign key (won_quote_id, won_revision_no) references crm.quote_revision (quote_id, revision_no);

alter table crm.quote enable row level security;
alter table crm.quote_revision enable row level security;
alter table crm.quote_line enable row level security;
alter table crm.external_identifier enable row level security;

reset role;
