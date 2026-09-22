-- Slice 3 — the commercial case: crm.opportunity_organization (#34), crm.opportunity_interest
-- (#35) and crm.opportunity_evidence (#36).
--
-- docs/DOMAIN.md §3.6 (the commercial case) and §7.1 (the inventory extension, 33 → 36);
-- docs/ARCHITECTURE.md §3, §3.1; docs/WORKFLOWS.md §1.1, §W2.
--
-- ── What this migration is, and what it deliberately is not ─────────────────────────────────
--
-- The commercial case **is** `crm.opportunity`. This migration creates no case table, no case
-- identifier, no second lifecycle and no new stage: it adds the three facts the opportunity row
-- cannot hold — every institution named on the case and what each one is *to this case*, what
-- the case is seeking, and which evidence is the reason to believe any of it.
--
-- It is schema only. No command is written, no row is inserted, and nothing reads these tables
-- yet. The commands (`set_requesting_institution`, the interest and evidence-link commands) and
-- their `crm.domain_event` types arrive with their own migration, together with the boundary
-- that emits them — an event type nothing can emit would be a promise, not a contract.
--
-- ── Where the machine stops ─────────────────────────────────────────────────────────────────
--
-- A machine may only ever propose `mentioned`. Every other role on a case — including
-- `manufacturer`, which a brand lookup can strongly suggest — is an operator's reading of what
-- an institution is to this deal, and the database refuses to hold it as anything else. This is
-- one CHECK (`confirmation <> 'machine_proposed' OR role = 'mentioned'`), and it makes a whole
-- class of quiet mistake unrepresentable rather than merely discouraged: nothing automated can
-- decide who is asking, who is supplying, or who is paying.
--
-- ── Why the agreement trigger is DEFERRED ───────────────────────────────────────────────────
--
-- `crm.opportunity.organization_id` keeps its shipped meaning — the customer organization — and
-- gains an exact definition: it is the confirmed requesting institution. The two must agree in
-- both directions, and `set_requesting_institution` writes both inside one transaction. An
-- immediate trigger would refuse whichever statement came first, so the check is a DEFERRABLE
-- INITIALLY DEFERRED constraint trigger on both tables: it judges the state at commit, which is
-- the only moment the pair is meaningful, and it is indifferent to the order of the writes.

set role origenlab_owner;

-- ── #34 crm.opportunity_organization ────────────────────────────────────────────────────────
-- What one institution is to one case. Distinct from crm.organization_relationship, which
-- records what an organization is to OrigenLab over all time.

create table crm.opportunity_organization (
  id uuid primary key default gen_random_uuid(),
  opportunity_id uuid not null references crm.opportunity (id),
  organization_id uuid not null references crm.organization (id),
  role text not null,
  valid_from date not null,
  valid_to date,
  confirmation text not null,
  confirmed_by_operator_id uuid references platform.operator (id),
  origin_source_record_id uuid references evidence.source_record (id),
  note text,
  -- The supplier exception, all-or-none and never rewritten (DOMAIN.md §3.6.1). There is no
  -- override table: these three columns are the record.
  supplier_exception_by_operator_id uuid references platform.operator (id),
  supplier_exception_reason text,
  supplier_exception_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint opportunity_organization_role_check check (
    role in ('requesting_institution', 'end_user_institution', 'purchasing_agent',
             'funder', 'supplier', 'manufacturer', 'mentioned')
  ),
  constraint opportunity_organization_confirmation_check check (
    confirmation in ('machine_proposed', 'confirmed')
  ),
  constraint opportunity_organization_confirmed_by_shape check (
    confirmation = 'confirmed' or confirmed_by_operator_id is null
  ),
  -- The machine proposes `mentioned` and nothing else: honest silence is representable, a guess
  -- with a role attached is not.
  constraint opportunity_organization_machine_proposes_mentioned_only check (
    confirmation <> 'machine_proposed' or role = 'mentioned'
  ),
  -- A requesting institution is always a human decision, with the human named.
  constraint opportunity_organization_requesting_is_human check (
    role <> 'requesting_institution'
    or (confirmation = 'confirmed' and confirmed_by_operator_id is not null)
  ),
  constraint opportunity_organization_validity check (valid_to is null or valid_to > valid_from),
  constraint opportunity_organization_exception_all_or_none check (
    num_nonnulls(supplier_exception_by_operator_id, supplier_exception_reason, supplier_exception_at) in (0, 3)
  ),
  constraint opportunity_organization_exception_reason_nonblank check (
    supplier_exception_reason is null or length(btrim(supplier_exception_reason)) > 0
  ),
  constraint opportunity_organization_exception_requesting_only check (
    supplier_exception_by_operator_id is null or role = 'requesting_institution'
  ),
  -- The same (case, institution, role) may not overlap itself in time; distinct roles coexist.
  constraint opportunity_organization_no_overlap_same_role exclude using gist (
    opportunity_id with =,
    organization_id with =,
    role with =,
    daterange(valid_from, valid_to, '[)') with &&
  )
);

comment on table crm.opportunity_organization is
  'DOMAIN.md §7 #34 — what one institution is to one case: closed role; at most one current requesting institution, always operator-confirmed; the machine may only propose `mentioned`; the supplier exception is an all-or-none triple that is never rewritten; rows are never deleted.';

comment on column crm.opportunity_organization.supplier_exception_reason is
  'DOMAIN.md §3.6.1 — why an organization OrigenLab buys from is the requesting institution on this one case. Non-blank, written with the operator and the timestamp, never rewritten, and scoped to this case alone: it opens no prospect or customer relationship and confers no marketing permission.';

-- At most one current requesting institution per case.
create unique index opportunity_organization_one_current_requesting
  on crm.opportunity_organization (opportunity_id)
  where role = 'requesting_institution' and valid_to is null;

-- The exclusion constraint's GiST index does not cover a foreign key (tests/090), so the two
-- referencing columns carry their own B-tree indexes.
create index opportunity_organization_opportunity_idx on crm.opportunity_organization (opportunity_id);
create index opportunity_organization_organization_idx on crm.opportunity_organization (organization_id);
create index opportunity_organization_confirmed_by_idx on crm.opportunity_organization (confirmed_by_operator_id)
  where confirmed_by_operator_id is not null;
create index opportunity_organization_origin_idx on crm.opportunity_organization (origin_source_record_id)
  where origin_source_record_id is not null;
create index opportunity_organization_exception_by_idx on crm.opportunity_organization (supplier_exception_by_operator_id)
  where supplier_exception_by_operator_id is not null;

-- ── #35 crm.opportunity_interest ────────────────────────────────────────────────────────────
-- What the case is seeking. Never a commitment and never a price: money exists only on
-- crm.quote_revision and crm.quote_line.

create table crm.opportunity_interest (
  id uuid primary key default gen_random_uuid(),
  opportunity_id uuid not null references crm.opportunity (id),
  product_id uuid references catalog.product (id),
  manufacturer_organization_id uuid references crm.organization (id),
  model_text text,
  description text,
  quantity numeric(18, 3),
  quantity_unit text,
  confirmation text not null,
  confirmed_by_operator_id uuid references platform.operator (id),
  origin_source_record_id uuid references evidence.source_record (id),
  withdrawn_at timestamptz,
  withdraw_reason text,
  note text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  -- An interest may begin as a model string read off a message and gain a product_id later, on
  -- the same row — exactly as a participant gains a person_id.
  constraint opportunity_interest_subject_present check (
    num_nonnulls(product_id, manufacturer_organization_id, model_text) >= 1
  ),
  constraint opportunity_interest_model_text_nonblank check (
    model_text is null or length(btrim(model_text)) > 0
  ),
  constraint opportunity_interest_quantity_positive check (quantity is null or quantity > 0),
  constraint opportunity_interest_confirmation_check check (
    confirmation in ('machine_proposed', 'confirmed')
  ),
  constraint opportunity_interest_confirmed_by_shape check (
    confirmation = 'confirmed' or confirmed_by_operator_id is null
  ),
  constraint opportunity_interest_withdrawal_shape check (
    (withdrawn_at is null) = (withdraw_reason is null)
  ),
  constraint opportunity_interest_withdraw_reason_nonblank check (
    withdraw_reason is null or length(btrim(withdraw_reason)) > 0
  )
);

comment on table crm.opportunity_interest is
  'DOMAIN.md §7 #35 — what the case is seeking: at least one of product, manufacturer or model text; product and manufacturer must agree; quantity > 0; withdrawal is a reason-bearing pair; no money column — an interest is never a commitment and never a price.';

create index opportunity_interest_opportunity_idx on crm.opportunity_interest (opportunity_id);
create index opportunity_interest_product_idx on crm.opportunity_interest (product_id)
  where product_id is not null;
create index opportunity_interest_manufacturer_idx on crm.opportunity_interest (manufacturer_organization_id)
  where manufacturer_organization_id is not null;
create index opportunity_interest_confirmed_by_idx on crm.opportunity_interest (confirmed_by_operator_id)
  where confirmed_by_operator_id is not null;
create index opportunity_interest_origin_idx on crm.opportunity_interest (origin_source_record_id)
  where origin_source_record_id is not null;

-- ── #36 crm.opportunity_evidence ────────────────────────────────────────────────────────────
-- Why the case believes what it believes. Typed foreign keys only — no subject_type/subject_id
-- pair. `contradicts` is first-class: the reading against a case is how one is later closed
-- honestly, and this is the only durable place it can live.

create table crm.opportunity_evidence (
  id uuid primary key default gen_random_uuid(),
  opportunity_id uuid not null references crm.opportunity (id),
  source_record_id uuid references evidence.source_record (id),
  assertion_id uuid references evidence.assertion (id),
  message_id uuid references comms.message (id),
  notice_id uuid references procurement.notice (id),
  relation text not null,
  linked_by_operator_id uuid not null references platform.operator (id),
  linked_at timestamptz not null default now(),
  unlinked_at timestamptz,
  unlink_reason text,
  note text,
  created_at timestamptz not null default now(),
  constraint opportunity_evidence_one_subject check (
    num_nonnulls(source_record_id, assertion_id, message_id, notice_id) = 1
  ),
  constraint opportunity_evidence_relation_check check (
    relation in ('origin', 'supports_requesting_institution', 'supports_interest',
                 'supports_participant', 'mentions', 'contradicts')
  ),
  constraint opportunity_evidence_unlink_shape check (
    (unlinked_at is null) = (unlink_reason is null)
  ),
  constraint opportunity_evidence_unlink_reason_nonblank check (
    unlink_reason is null or length(btrim(unlink_reason)) > 0
  )
);

comment on table crm.opportunity_evidence is
  'DOMAIN.md §7 #36 — why the case believes what it believes: exactly one typed subject among source record, assertion, message and notice; closed relation including contradicts; one link per (case, subject, relation) while linked; append-only, unlinked with a reason and never deleted.';

-- One link per (case, subject, relation) while linked — one partial unique index per subject
-- column, because the subject is typed rather than polymorphic.
create unique index opportunity_evidence_one_source_record_link
  on crm.opportunity_evidence (opportunity_id, source_record_id, relation)
  where unlinked_at is null and source_record_id is not null;
create unique index opportunity_evidence_one_assertion_link
  on crm.opportunity_evidence (opportunity_id, assertion_id, relation)
  where unlinked_at is null and assertion_id is not null;
create unique index opportunity_evidence_one_message_link
  on crm.opportunity_evidence (opportunity_id, message_id, relation)
  where unlinked_at is null and message_id is not null;
create unique index opportunity_evidence_one_notice_link
  on crm.opportunity_evidence (opportunity_id, notice_id, relation)
  where unlinked_at is null and notice_id is not null;

create index opportunity_evidence_opportunity_idx on crm.opportunity_evidence (opportunity_id);
create index opportunity_evidence_source_record_idx on crm.opportunity_evidence (source_record_id)
  where source_record_id is not null;
create index opportunity_evidence_assertion_idx on crm.opportunity_evidence (assertion_id)
  where assertion_id is not null;
create index opportunity_evidence_message_idx on crm.opportunity_evidence (message_id)
  where message_id is not null;
create index opportunity_evidence_notice_idx on crm.opportunity_evidence (notice_id)
  where notice_id is not null;
create index opportunity_evidence_linked_by_idx on crm.opportunity_evidence (linked_by_operator_id);

-- ── Structural guards ───────────────────────────────────────────────────────────────────────
-- All SECURITY INVOKER with a pinned search_path, like every other function in these schemas.
-- A trigger function needs no EXECUTE grant for the invoking role: PostgreSQL checks EXECUTE at
-- CREATE TRIGGER time, not when the trigger fires.

-- A registered supplier or manufacturer is never made a customer by appearing in a message. The
-- command refuses it; this repeats the refusal in the database, where no code path can miss it.
create function crm.opportunity_organization_supplier_exception_required() returns trigger
language plpgsql
set search_path = pg_catalog
as $$
begin
  if new.role = 'requesting_institution'
     and new.supplier_exception_by_operator_id is null
     and exists (
       select 1 from crm.organization_relationship r
        where r.organization_id = new.organization_id
          and r.role in ('supplier', 'manufacturer')
          and (r.valid_to is null or r.valid_to > current_date)
     )
  then
    raise exception
      'crm.opportunity_organization: % holds a current supplier or manufacturer relationship; making it the requesting institution requires the justified exception (operator, reason, timestamp)',
      new.organization_id
      using errcode = 'P0001';
  end if;
  return new;
end
$$;

comment on function crm.opportunity_organization_supplier_exception_required() is
  'DOMAIN.md §3.6.1 — a registered supplier or manufacturer may only be a requesting institution through the justified exception. SECURITY INVOKER.';

revoke all on function crm.opportunity_organization_supplier_exception_required()
  from public, anon, authenticated, service_role;

-- The exception is written once, in the open, and is never rewritten — as on the recontact
-- override. Correcting a wrong exception means closing the row and opening the right one, so
-- both readings stay visible.
create function crm.opportunity_organization_exception_immutable() returns trigger
language plpgsql
set search_path = pg_catalog
as $$
begin
  if new.supplier_exception_by_operator_id is distinct from old.supplier_exception_by_operator_id
     or new.supplier_exception_reason is distinct from old.supplier_exception_reason
     or new.supplier_exception_at is distinct from old.supplier_exception_at
  then
    raise exception
      'crm.opportunity_organization: the supplier exception is never rewritten — close this row and open a new one'
      using errcode = 'P0001';
  end if;
  return new;
end
$$;

comment on function crm.opportunity_organization_exception_immutable() is
  'DOMAIN.md §3.6.1 — the supplier-exception triple is written once and never changed. SECURITY INVOKER.';

revoke all on function crm.opportunity_organization_exception_immutable()
  from public, anon, authenticated, service_role;

-- crm.opportunity.organization_id IS the confirmed requesting institution. Deferred, because
-- one command writes both sides and neither order may be refused.
create function crm.opportunity_requesting_institution_agrees() returns trigger
language plpgsql
set search_path = pg_catalog
as $$
declare
  v_opportunity_id uuid;
  v_declared uuid;
  v_current uuid;
begin
  -- An IF statement rather than a CASE expression: plpgsql prepares a whole expression before
  -- evaluating it, so `new.opportunity_id` would have to exist on a crm.opportunity row too.
  if tg_table_name = 'opportunity' then
    v_opportunity_id := new.id;
  else
    v_opportunity_id := new.opportunity_id;
  end if;

  select o.organization_id into v_declared
    from crm.opportunity o where o.id = v_opportunity_id;
  select oo.organization_id into v_current
    from crm.opportunity_organization oo
   where oo.opportunity_id = v_opportunity_id
     and oo.role = 'requesting_institution'
     and oo.valid_to is null;

  if v_declared is distinct from v_current then
    raise exception
      'crm.opportunity %: organization_id (%) and the current confirmed requesting institution (%) must be the same institution',
      v_opportunity_id, coalesce(v_declared::text, '<none>'), coalesce(v_current::text, '<none>')
      using errcode = 'P0001';
  end if;
  return null;
end
$$;

comment on function crm.opportunity_requesting_institution_agrees() is
  'DOMAIN.md §3.6.1 — crm.opportunity.organization_id is the confirmed requesting institution, checked in both directions at commit. SECURITY INVOKER.';

revoke all on function crm.opportunity_requesting_institution_agrees()
  from public, anon, authenticated, service_role;

-- A product belongs to exactly one manufacturer; naming a different one on the same interest is
-- a contradiction, not a refinement. A CHECK cannot cross tables, so this is a trigger.
create function crm.opportunity_interest_manufacturer_agrees() returns trigger
language plpgsql
set search_path = pg_catalog
as $$
declare
  v_manufacturer uuid;
begin
  if new.product_id is not null and new.manufacturer_organization_id is not null then
    select p.manufacturer_organization_id into v_manufacturer
      from catalog.product p where p.id = new.product_id;
    if v_manufacturer is distinct from new.manufacturer_organization_id then
      raise exception
        'crm.opportunity_interest: product % is made by %, not by %',
        new.product_id, coalesce(v_manufacturer::text, '<unknown>'), new.manufacturer_organization_id
        using errcode = 'P0001';
    end if;
  end if;
  return new;
end
$$;

comment on function crm.opportunity_interest_manufacturer_agrees() is
  'DOMAIN.md §3.6.2 — when both are named, the interest''s manufacturer is the catalogue product''s manufacturer. SECURITY INVOKER.';

revoke all on function crm.opportunity_interest_manufacturer_agrees()
  from public, anon, authenticated, service_role;

-- Append-only in substance, not only in verb: a link's subject, relation, author and moment are
-- frozen, and unlinking happens once. Only unlinked_at, unlink_reason and note may change.
create function crm.opportunity_evidence_link_immutable() returns trigger
language plpgsql
set search_path = pg_catalog
as $$
begin
  if new.id is distinct from old.id
     or new.opportunity_id is distinct from old.opportunity_id
     or new.source_record_id is distinct from old.source_record_id
     or new.assertion_id is distinct from old.assertion_id
     or new.message_id is distinct from old.message_id
     or new.notice_id is distinct from old.notice_id
     or new.relation is distinct from old.relation
     or new.linked_by_operator_id is distinct from old.linked_by_operator_id
     or new.linked_at is distinct from old.linked_at
  then
    raise exception
      'crm.opportunity_evidence: a link is append-only — its subject, relation, author and moment are never rewritten'
      using errcode = 'P0001';
  end if;
  if old.unlinked_at is not null and new.unlinked_at is distinct from old.unlinked_at then
    raise exception
      'crm.opportunity_evidence: a closed link is never reopened — link the subject again instead'
      using errcode = 'P0001';
  end if;
  return new;
end
$$;

comment on function crm.opportunity_evidence_link_immutable() is
  'DOMAIN.md §3.6.3 — an evidence link is append-only; only unlinked_at, unlink_reason and note may change, and only once. SECURITY INVOKER.';

revoke all on function crm.opportunity_evidence_link_immutable()
  from public, anon, authenticated, service_role;

-- Triggers.
create trigger opportunity_organization_never_deleted
  before delete on crm.opportunity_organization
  for each row execute function platform.reject_mutation('never deleted (close valid_to instead)');

create trigger opportunity_organization_exception_immutable
  before update on crm.opportunity_organization
  for each row execute function crm.opportunity_organization_exception_immutable();

create trigger opportunity_organization_supplier_exception_required
  before insert or update on crm.opportunity_organization
  for each row execute function crm.opportunity_organization_supplier_exception_required();

create constraint trigger opportunity_organization_requesting_agrees
  after insert or update on crm.opportunity_organization
  deferrable initially deferred
  for each row execute function crm.opportunity_requesting_institution_agrees();

create constraint trigger opportunity_requesting_agrees
  after insert or update on crm.opportunity
  deferrable initially deferred
  for each row execute function crm.opportunity_requesting_institution_agrees();

create trigger opportunity_interest_never_deleted
  before delete on crm.opportunity_interest
  for each row execute function platform.reject_mutation('never deleted (withdrawal sets withdrawn_at and its reason)');

create trigger opportunity_interest_manufacturer_agrees
  before insert or update on crm.opportunity_interest
  for each row execute function crm.opportunity_interest_manufacturer_agrees();

create trigger opportunity_evidence_never_deleted
  before delete on crm.opportunity_evidence
  for each row execute function platform.reject_mutation('never deleted (unlinking sets unlinked_at and its reason)');

create trigger opportunity_evidence_link_immutable
  before update on crm.opportunity_evidence
  for each row execute function crm.opportunity_evidence_link_immutable();

-- ── Grants and policies ─────────────────────────────────────────────────────────────────────
-- Same matrix as every other never-deleted table: the API inserts and closes rows column by
-- column, the worker only reads, and no runtime role holds DELETE. Nothing here is writable by
-- the worker: a case decision is never a machine write.

grant select, insert on crm.opportunity_organization to origenlab_api;
grant update (valid_to, confirmation, confirmed_by_operator_id, note, updated_at)
  on crm.opportunity_organization to origenlab_api;
grant select on crm.opportunity_organization to origenlab_worker;

grant select, insert on crm.opportunity_interest to origenlab_api;
grant update (product_id, confirmation, confirmed_by_operator_id, withdrawn_at, withdraw_reason, note, updated_at)
  on crm.opportunity_interest to origenlab_api;
grant select on crm.opportunity_interest to origenlab_worker;

grant select, insert on crm.opportunity_evidence to origenlab_api;
grant update (unlinked_at, unlink_reason, note) on crm.opportunity_evidence to origenlab_api;
grant select on crm.opportunity_evidence to origenlab_worker;

alter table crm.opportunity_organization enable row level security;
alter table crm.opportunity_interest enable row level security;
alter table crm.opportunity_evidence enable row level security;

create policy origenlab_api_select on crm.opportunity_organization for select to origenlab_api using (true);
create policy origenlab_api_insert on crm.opportunity_organization for insert to origenlab_api with check (true);
create policy origenlab_api_update on crm.opportunity_organization for update to origenlab_api using (true) with check (true);
create policy origenlab_worker_select on crm.opportunity_organization for select to origenlab_worker using (true);

create policy origenlab_api_select on crm.opportunity_interest for select to origenlab_api using (true);
create policy origenlab_api_insert on crm.opportunity_interest for insert to origenlab_api with check (true);
create policy origenlab_api_update on crm.opportunity_interest for update to origenlab_api using (true) with check (true);
create policy origenlab_worker_select on crm.opportunity_interest for select to origenlab_worker using (true);

create policy origenlab_api_select on crm.opportunity_evidence for select to origenlab_api using (true);
create policy origenlab_api_insert on crm.opportunity_evidence for insert to origenlab_api with check (true);
create policy origenlab_api_update on crm.opportunity_evidence for update to origenlab_api using (true) with check (true);
create policy origenlab_worker_select on crm.opportunity_evidence for select to origenlab_worker using (true);

reset role;
