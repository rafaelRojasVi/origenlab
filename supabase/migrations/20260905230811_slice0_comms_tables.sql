-- Slice 0 / M08 — comms: mailbox (#15), message (#16), message_participant (#17),
-- attachment (#18), plus crm.quote_revision.sent_message_id → comms.message.
--
-- docs/DOMAIN.md §7; docs/DATA.md §1.1 (provider evidence, never edited), §5 (bytes live in
-- Storage), §6 (Gmail identity: provider id is canonical; RFC 822 ids are nullable and non-unique;
-- the only RFC 822 uniqueness is on minted outbound ids).

set role origenlab_owner;

-- #15 comms.mailbox — provider account, permissions, sync cursor.
create table comms.mailbox (
  id uuid primary key default gen_random_uuid(),
  address_norm text not null,
  display_name text,
  provider text not null default 'gmail',
  is_production_sender boolean not null default false,
  authorization_state text not null default 'unauthorized',
  granted_scopes text[] not null default '{}',
  history_id text,
  label_watermarks jsonb not null default '{}'::jsonb,
  last_synced_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint mailbox_address_norm_key unique (address_norm),
  constraint mailbox_address_norm_shape check (
    address_norm = lower(address_norm) and address_norm ~ '^[^@\s]+@[^@\s]+\.[^@\s]+$'
  ),
  constraint mailbox_provider_check check (provider in ('gmail')),
  constraint mailbox_authorization_state_check check (authorization_state in ('unauthorized', 'authorized', 'revoked')),
  constraint mailbox_label_watermarks_object check (jsonb_typeof(label_watermarks) = 'object')
);

comment on table comms.mailbox is
  'DOMAIN.md §7 #15 — provider account, permissions, sync cursor: address unique; at most one production sender.';

-- At most one production sender.
create unique index mailbox_single_production_sender
  on comms.mailbox ((is_production_sender)) where is_production_sender;

-- #16 comms.message — provider message evidence.
create table comms.message (
  id uuid primary key default gen_random_uuid(),
  mailbox_id uuid not null references comms.mailbox (id),
  provider_message_id text not null,
  provider_thread_id text,
  rfc822_message_id_norm text,
  send_attempt_id uuid,
  direction text not null,
  internal_date timestamptz not null,
  subject text,
  labels text[] not null default '{}',
  eml_storage_path text,
  eml_sha256 text,
  size_bytes bigint,
  parse_status text not null default 'parsed',
  parse_error text,
  created_at timestamptz not null default now(),
  -- The Gmail provider message id is the canonical identity (DATA.md §6).
  constraint message_mailbox_provider_key unique (mailbox_id, provider_message_id),
  -- The reconciler links at most one Sent copy per attempt.
  constraint message_send_attempt_key unique (send_attempt_id),
  constraint message_provider_message_id_nonblank check (length(btrim(provider_message_id)) > 0),
  constraint message_direction_check check (direction in ('inbound', 'outbound')),
  constraint message_parse_status_check check (parse_status in ('parsed', 'parse_failed')),
  constraint message_parse_error_shape check ((parse_status = 'parse_failed') = (parse_error is not null)),
  constraint message_eml_sha256_shape check (eml_sha256 is null or eml_sha256 ~ '^[0-9a-f]{64}$'),
  constraint message_eml_stored_has_hash check (eml_storage_path is null or eml_sha256 is not null),
  constraint message_size_nonnegative check (size_bytes is null or size_bytes >= 0)
);

comment on table comms.message is
  'DOMAIN.md §7 #16 — provider message evidence: (mailbox_id, provider_message_id) unique; send_attempt_id unique; rfc822_message_id_norm nullable and non-unique by design; bodies live in Storage.';

-- Non-unique by design: inbound RFC 822 ids may be absent, malformed or reused.
create index message_rfc822_idx on comms.message (rfc822_message_id_norm) where rfc822_message_id_norm is not null;
create index message_mailbox_date_idx on comms.message (mailbox_id, internal_date desc);
create index message_thread_idx on comms.message (mailbox_id, provider_thread_id) where provider_thread_id is not null;

-- #17 comms.message_participant — from/to/cc addresses with optional resolution.
create table comms.message_participant (
  id uuid primary key default gen_random_uuid(),
  message_id uuid not null references comms.message (id),
  role text not null,
  address_norm text not null,
  display_name text,
  resolved_contact_point_id uuid references crm.contact_point (id),
  created_at timestamptz not null default now(),
  constraint message_participant_key unique (message_id, role, address_norm),
  constraint message_participant_role_check check (role in ('from', 'sender', 'reply_to', 'to', 'cc', 'bcc')),
  constraint message_participant_address_shape check (address_norm = lower(address_norm) and length(btrim(address_norm)) > 0)
);

comment on table comms.message_participant is
  'DOMAIN.md §7 #17 — from/to/cc addresses with optional resolution: (message_id, role, address_norm) unique; unpromoted addresses live here as text, not as contact points.';

create index message_participant_address_idx on comms.message_participant (address_norm);
create index message_participant_resolved_idx on comms.message_participant (resolved_contact_point_id)
  where resolved_contact_point_id is not null;

-- #18 comms.attachment — MIME part metadata and Storage reference.
create table comms.attachment (
  id uuid primary key default gen_random_uuid(),
  message_id uuid not null references comms.message (id),
  part_index integer not null,
  filename text,
  mime_type text not null,
  size_bytes bigint,
  storage_path text,
  sha256 text,
  created_at timestamptz not null default now(),
  constraint attachment_message_part_key unique (message_id, part_index),
  constraint attachment_part_index_nonnegative check (part_index >= 0),
  constraint attachment_mime_type_nonblank check (length(btrim(mime_type)) > 0),
  constraint attachment_size_nonnegative check (size_bytes is null or size_bytes >= 0),
  constraint attachment_sha256_shape check (sha256 is null or sha256 ~ '^[0-9a-f]{64}$'),
  -- sha256 present when stored.
  constraint attachment_stored_has_hash check (storage_path is null or sha256 is not null)
);

comment on table comms.attachment is
  'DOMAIN.md §7 #18 — MIME part metadata and Storage reference: (message_id, part_index) unique; sha256 present when stored.';

create index attachment_sha256_idx on comms.attachment (sha256) where sha256 is not null;

-- An operator-linked Gmail message as sending evidence (WORKFLOWS.md §W3 step 7').
alter table crm.quote_revision
  add constraint quote_revision_sent_message_fkey foreign key (sent_message_id) references comms.message (id);

alter table comms.mailbox enable row level security;
alter table comms.message enable row level security;
alter table comms.message_participant enable row level security;
alter table comms.attachment enable row level security;

reset role;
