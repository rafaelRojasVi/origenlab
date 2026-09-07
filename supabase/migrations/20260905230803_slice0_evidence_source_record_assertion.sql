-- Slice 0 / M03 — evidence: evidence.source_record (#24) and evidence.assertion (#25).
--
-- docs/DOMAIN.md §5 (evidence and promotion), §7; docs/DATA.md §2, §3, §8.
-- Evidence→truth links are logical (resolved_kind, resolved_id): no foreign key from durable rows
-- into machine output, and none from assertions into crm.* either.

set role origenlab_owner;

-- #24 evidence.source_record — acquired external record and migration manifests.
create table evidence.source_record (
  id uuid primary key default gen_random_uuid(),
  kind text not null,
  dedupe_key text not null,
  payload jsonb not null,
  payload_sha256 text,
  source_uri text,
  acquired_at timestamptz not null default now(),
  review_status text not null default 'pending',
  is_quarantined boolean not null default false,
  quarantine_reason text,
  quarantined_at timestamptz,
  superseded_by_source_record_id uuid references evidence.source_record (id),
  superseded_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint source_record_dedupe_key_key unique (dedupe_key),
  constraint source_record_dedupe_key_nonblank check (length(btrim(dedupe_key)) > 0),
  constraint source_record_kind_check check (kind in (
    'workbook_import', 'chilecompra_notice', 'migration_manifest',
    'v1_parse_failure', 'v1_evidence_edge', 'v1_supplier_candidate', 'v1_historical_quote_candidate'
  )),
  constraint source_record_payload_object check (jsonb_typeof(payload) = 'object'),
  constraint source_record_payload_sha256_shape check (
    payload_sha256 is null or payload_sha256 ~ '^[0-9a-f]{64}$'
  ),
  constraint source_record_review_status_check check (
    review_status in ('pending', 'reviewed', 'promoted', 'rejected')
  ),
  constraint source_record_quarantine_shape check (
    (is_quarantined = (quarantine_reason is not null)) and (is_quarantined = (quarantined_at is not null))
  ),
  constraint source_record_supersession_shape check (
    (superseded_by_source_record_id is null) = (superseded_at is null)
  ),
  constraint source_record_no_self_supersession check (superseded_by_source_record_id is distinct from id)
);

comment on table evidence.source_record is
  'DOMAIN.md §7 #24 — acquired external record and migration manifests: dedupe_key unique; supersession chain; quarantine flag.';

create index source_record_kind_acquired_idx on evidence.source_record (kind, acquired_at desc);
create index source_record_quarantined_idx on evidence.source_record (quarantined_at) where is_quarantined;

-- #25 evidence.assertion — typed observation with resolution.
create table evidence.assertion (
  id uuid primary key default gen_random_uuid(),
  source_record_id uuid not null references evidence.source_record (id),
  kind text not null,
  value_norm text not null,
  value jsonb,
  resolution text not null default 'unresolved',
  resolved_kind text,
  resolved_id uuid,
  resolved_at timestamptz,
  resolved_by_operator_id uuid references platform.operator (id),
  ambiguity_note text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint assertion_source_kind_value_key unique (source_record_id, kind, value_norm),
  constraint assertion_kind_check check (kind in (
    'organization_name', 'contact_address', 'postal_address', 'affiliation',
    'contacted_address', 'supplier_candidate', 'historical_quote_candidate'
  )),
  constraint assertion_value_norm_nonblank check (length(btrim(value_norm)) > 0),
  constraint assertion_value_object check (value is null or jsonb_typeof(value) = 'object'),
  constraint assertion_resolution_check check (
    resolution in ('unresolved', 'promoted', 'linked', 'rejected', 'ambiguous')
  ),
  constraint assertion_resolved_kind_check check (resolved_kind is null or resolved_kind in (
    'organization', 'person', 'contact_point', 'affiliation', 'address',
    'organization_relationship', 'opportunity', 'opportunity_participant', 'contact_control'
  )),
  constraint assertion_resolution_target_shape check (
    case
      when resolution in ('promoted', 'linked')
        then resolved_kind is not null and resolved_id is not null and resolved_at is not null
      else resolved_kind is null and resolved_id is null
    end
  ),
  constraint assertion_ambiguity_shape check (resolution <> 'ambiguous' or ambiguity_note is not null)
);

comment on table evidence.assertion is
  'DOMAIN.md §7 #25 — typed observation with resolution: (source_record_id, kind, value_norm) unique; closed kind; logical resolved_kind/resolved_id.';

create index assertion_source_record_idx on evidence.assertion (source_record_id);
create index assertion_unresolved_idx on evidence.assertion (kind, value_norm) where resolution = 'unresolved';

alter table evidence.source_record enable row level security;
alter table evidence.assertion enable row level security;

reset role;
