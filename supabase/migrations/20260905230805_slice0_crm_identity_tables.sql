-- Slice 0 / M04 — crm identity: organization (#1), organization_domain (#2),
-- organization_relationship (#3), person (#5), affiliation (#6), contact_point (#7), address (#31).
--
-- docs/DOMAIN.md §2 (identity), §7 (inventory). Every closed vocabulary is a CHECK constraint;
-- extending one is a migration. Validity periods use daterange(valid_from, valid_to, '[)') with a
-- NULL upper bound meaning "current". Structural triggers here: the organization parent-cycle
-- rejection named in DOMAIN.md §2.1 and the never-deleted guards on affiliation and address.
-- Command-level rules (merge target confirmed, supersession as the only edit path, promotion
-- provenance) belong to the Slice 2 commands and their triggers (docs/MIGRATION.md §5).

set role origenlab_owner;

-- #1 crm.organization — canonical actor or unit.
create table crm.organization (
  id uuid primary key default gen_random_uuid(),
  kind text not null,
  name text not null,
  legal_name text,
  parent_organization_id uuid references crm.organization (id),
  merged_into_organization_id uuid references crm.organization (id),
  confirmation text not null,
  confirmed_by_operator_id uuid references platform.operator (id),
  origin_source_record_id uuid references evidence.source_record (id),
  note text,
  version integer not null default 1,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  -- `kind` is a lower_snake_case token. DOMAIN.md names the column but does not enumerate its
  -- values; the closed list is an open domain decision and will arrive as a CHECK by migration.
  constraint organization_kind_shape check (kind ~ '^[a-z][a-z_]{0,39}$'),
  constraint organization_name_nonblank check (length(btrim(name)) > 0),
  constraint organization_no_self_parent check (parent_organization_id is distinct from id),
  constraint organization_no_self_merge check (merged_into_organization_id is distinct from id),
  constraint organization_confirmation_check check (confirmation in ('machine_proposed', 'confirmed')),
  constraint organization_confirmed_by_shape check (confirmation = 'confirmed' or confirmed_by_operator_id is null),
  constraint organization_version_positive check (version >= 1)
);

comment on table crm.organization is
  'DOMAIN.md §7 #1 — canonical actor or unit: a unit is a row with parent_organization_id; no cycles (trigger); merge target confirmed (Slice 2 command).';

create index organization_parent_idx on crm.organization (parent_organization_id)
  where parent_organization_id is not null;
create index organization_merged_into_idx on crm.organization (merged_into_organization_id)
  where merged_into_organization_id is not null;
create index organization_name_idx on crm.organization (lower(name));

-- Hierarchy depth is unconstrained; a cycle is rejected (DOMAIN.md §2.1). SECURITY INVOKER.
create function crm.organization_reject_parent_cycle() returns trigger
language plpgsql
set search_path = pg_catalog
as $$
declare
  v_cycle boolean;
begin
  if new.parent_organization_id is null then
    return new;
  end if;
  with recursive ancestors as (
    select o.id, o.parent_organization_id
      from crm.organization o
     where o.id = new.parent_organization_id
    union all
    select o.id, o.parent_organization_id
      from crm.organization o
      join ancestors a on o.id = a.parent_organization_id
  ) cycle id set is_cycle using path
  select exists (select 1 from ancestors a where a.id = new.id) into v_cycle;
  if v_cycle then
    raise exception 'crm.organization %: parent_organization_id % would create a cycle',
      new.id, new.parent_organization_id using errcode = 'P0001';
  end if;
  return new;
end
$$;

comment on function crm.organization_reject_parent_cycle() is
  'Trigger: rejects a parent_organization_id that would make the organization its own ancestor. SECURITY INVOKER.';

revoke all on function crm.organization_reject_parent_cycle() from public, anon, authenticated, service_role;

create trigger organization_reject_parent_cycle
  before insert or update of parent_organization_id on crm.organization
  for each row execute function crm.organization_reject_parent_cycle();

-- #2 crm.organization_domain — domain ↔ organization with scope.
create table crm.organization_domain (
  id uuid primary key default gen_random_uuid(),
  organization_id uuid not null references crm.organization (id),
  domain_norm text not null,
  scope text not null,
  origin_source_record_id uuid references evidence.source_record (id),
  created_at timestamptz not null default now(),
  constraint organization_domain_org_domain_key unique (organization_id, domain_norm),
  constraint organization_domain_scope_check check (scope in ('exclusive', 'shared')),
  constraint organization_domain_norm_shape check (
    domain_norm = lower(domain_norm) and domain_norm ~ '^[a-z0-9-]+(\.[a-z0-9-]+)+$'
  )
);

comment on table crm.organization_domain is
  'DOMAIN.md §7 #2 — domain ↔ organization with scope: at most one exclusive owner per domain; a domain is a routing hint, never an identity.';

create unique index organization_domain_exclusive_owner_key
  on crm.organization_domain (domain_norm) where scope = 'exclusive';
create index organization_domain_lookup_idx on crm.organization_domain (domain_norm);

-- #3 crm.organization_relationship — time-bound roles.
create table crm.organization_relationship (
  id uuid primary key default gen_random_uuid(),
  organization_id uuid not null references crm.organization (id),
  role text not null,
  valid_from date not null,
  valid_to date,
  note text,
  origin_source_record_id uuid references evidence.source_record (id),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint organization_relationship_role_check check (
    role in ('customer', 'supplier', 'manufacturer', 'prospect', 'partner', 'competitor')
  ),
  constraint organization_relationship_validity check (valid_to is null or valid_to > valid_from),
  constraint organization_relationship_no_overlap_same_role exclude using gist (
    organization_id with =,
    role with =,
    daterange(valid_from, valid_to, '[)') with &&
  )
);

comment on table crm.organization_relationship is
  'DOMAIN.md §7 #3 — time-bound roles an organization plays for OrigenLab; the same role may not overlap itself in time; roles coexist.';

create index organization_relationship_organization_idx on crm.organization_relationship (organization_id);
create index organization_relationship_current_role_idx on crm.organization_relationship (role, organization_id)
  where valid_to is null;

-- #5 crm.person — canonical natural person.
create table crm.person (
  id uuid primary key default gen_random_uuid(),
  display_name text not null,
  given_name text,
  family_name text,
  merged_into_person_id uuid references crm.person (id),
  confirmation text not null,
  confirmed_by_operator_id uuid references platform.operator (id),
  origin_source_record_id uuid references evidence.source_record (id),
  note text,
  version integer not null default 1,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint person_display_name_nonblank check (length(btrim(display_name)) > 0),
  constraint person_no_self_merge check (merged_into_person_id is distinct from id),
  constraint person_confirmation_check check (confirmation in ('machine_proposed', 'confirmed')),
  constraint person_confirmed_by_shape check (confirmation = 'confirmed' or confirmed_by_operator_id is null),
  constraint person_version_positive check (version >= 1)
);

comment on table crm.person is
  'DOMAIN.md §7 #5 — canonical natural person; may have zero organizations and zero channels; merge target confirmed (Slice 2 command).';

create index person_merged_into_idx on crm.person (merged_into_person_id) where merged_into_person_id is not null;
create index person_display_name_idx on crm.person (lower(display_name));

-- #6 crm.affiliation — person ↔ organization over time.
create table crm.affiliation (
  id uuid primary key default gen_random_uuid(),
  person_id uuid not null references crm.person (id),
  organization_id uuid not null references crm.organization (id),
  role_title text,
  unit_label text,
  -- Normalized role identity: (role_title, unit_label) lower-cased, trimmed, whitespace-collapsed.
  role_key text generated always as (
    lower(regexp_replace(btrim(coalesce(role_title, '')), '\s+', ' ', 'g'))
    || '|' ||
    lower(regexp_replace(btrim(coalesce(unit_label, '')), '\s+', ' ', 'g'))
  ) stored,
  valid_from date not null,
  valid_to date,
  confirmation text not null,
  confirmed_by_operator_id uuid references platform.operator (id),
  origin_source_record_id uuid references evidence.source_record (id),
  note text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint affiliation_validity check (valid_to is null or valid_to > valid_from),
  constraint affiliation_confirmation_check check (confirmation in ('machine_proposed', 'confirmed')),
  constraint affiliation_confirmed_by_shape check (confirmation = 'confirmed' or confirmed_by_operator_id is null),
  -- Exact duplicates forbidden; concurrent distinct roles allowed (DOMAIN.md §2.4).
  constraint affiliation_no_overlap_same_role exclude using gist (
    person_id with =,
    organization_id with =,
    role_key with =,
    daterange(valid_from, valid_to, '[)') with &&
  )
);

comment on table crm.affiliation is
  'DOMAIN.md §7 #6 — person ↔ organization over time: no overlapping validity for the same (person, organization, role_key); rows are never deleted.';

create index affiliation_person_idx on crm.affiliation (person_id);
create index affiliation_organization_idx on crm.affiliation (organization_id);

create trigger affiliation_never_deleted
  before delete on crm.affiliation
  for each row execute function platform.reject_mutation('never deleted (close valid_to instead)');

-- #7 crm.contact_point — one normalized channel.
create table crm.contact_point (
  id uuid primary key default gen_random_uuid(),
  kind text not null,
  value_norm text not null,
  value_display text not null,
  person_id uuid references crm.person (id),
  organization_id uuid references crm.organization (id),
  usage text not null,
  confirmation text not null,
  origin_source_record_id uuid references evidence.source_record (id),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  -- One row per reachable channel, globally.
  constraint contact_point_kind_value_key unique (kind, value_norm),
  constraint contact_point_kind_check check (kind in ('email', 'phone')),
  constraint contact_point_usage_check check (usage in ('personal', 'work', 'shared_mailbox', 'unattributed')),
  constraint contact_point_confirmation_check check (confirmation in ('machine_proposed', 'confirmed')),
  -- Shape checks (DOMAIN.md §2.5).
  constraint contact_point_usage_shape check (
    case usage
      when 'personal'       then person_id is not null and organization_id is null
      when 'work'           then person_id is not null and organization_id is not null
      when 'shared_mailbox' then person_id is null
      when 'unattributed'   then person_id is null and organization_id is null
    end
  ),
  -- email: lower-cased (domain in punycode), no tag stripping; phone: E.164.
  constraint contact_point_value_norm_shape check (
    case kind
      when 'email' then value_norm = lower(value_norm) and value_norm ~ '^[^@\s]+@[^@\s]+\.[^@\s]+$'
      when 'phone' then value_norm ~ '^\+[1-9][0-9]{6,14}$'
    end
  ),
  constraint contact_point_value_display_nonblank check (length(btrim(value_display)) > 0)
);

comment on table crm.contact_point is
  'DOMAIN.md §7 #7 — one normalized channel: (kind, value_norm) globally unique; usage shape checks; deliberately no consent column.';

create index contact_point_person_idx on crm.contact_point (person_id) where person_id is not null;
create index contact_point_organization_idx on crm.contact_point (organization_id) where organization_id is not null;

-- #31 crm.address — one structured postal location of one organization.
create table crm.address (
  id uuid primary key default gen_random_uuid(),
  organization_id uuid not null references crm.organization (id),
  site_label text,
  street_line_1 text not null,
  street_line_2 text,
  locality text not null,
  administrative_area text,
  postal_code text,
  country_code text not null,
  -- Normalized structured key; site_label is a label, not part of the place.
  address_key text generated always as (
    lower(regexp_replace(btrim(street_line_1), '\s+', ' ', 'g'))
    || '|' || lower(regexp_replace(btrim(coalesce(street_line_2, '')), '\s+', ' ', 'g'))
    || '|' || lower(regexp_replace(btrim(locality), '\s+', ' ', 'g'))
    || '|' || lower(regexp_replace(btrim(coalesce(administrative_area, '')), '\s+', ' ', 'g'))
    || '|' || lower(regexp_replace(coalesce(postal_code, ''), '\s+', '', 'g'))
    || '|' || upper(country_code)
  ) stored,
  valid_from date not null,
  valid_to date,
  superseded_by_address_id uuid references crm.address (id),
  confirmation text not null,
  confirmed_by_operator_id uuid references platform.operator (id),
  origin_source_record_id uuid references evidence.source_record (id),
  note text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint address_required_fields_nonblank check (
    length(btrim(street_line_1)) > 0 and length(btrim(locality)) > 0
  ),
  constraint address_country_code_shape check (country_code ~ '^[A-Z]{2}$'),
  constraint address_validity check (valid_to is null or valid_to > valid_from),
  constraint address_no_self_supersession check (superseded_by_address_id is distinct from id),
  -- Supersession closes the predecessor: a superseded row is never current.
  constraint address_supersession_closes_validity check (superseded_by_address_id is null or valid_to is not null),
  constraint address_confirmation_check check (confirmation in ('machine_proposed', 'confirmed')),
  constraint address_confirmed_by_shape check (confirmation = 'confirmed' or confirmed_by_operator_id is null)
);

comment on table crm.address is
  'DOMAIN.md §7 #31 — one structured postal location of one organization: typed organization_id; structured fields canonical; supersession chain; never edited or deleted; no billing/delivery/default flag.';

-- Exact duplicates among an organization's current rows are refused.
create unique index address_no_duplicate_current_key
  on crm.address (organization_id, address_key) where valid_to is null;
create index address_organization_idx on crm.address (organization_id);

create trigger address_never_deleted
  before delete on crm.address
  for each row execute function platform.reject_mutation('never deleted (supersede instead)');

alter table crm.organization enable row level security;
alter table crm.organization_domain enable row level security;
alter table crm.organization_relationship enable row level security;
alter table crm.person enable row level security;
alter table crm.affiliation enable row level security;
alter table crm.contact_point enable row level security;
alter table crm.address enable row level security;

reset role;
