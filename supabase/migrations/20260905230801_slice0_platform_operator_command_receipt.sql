-- Slice 0 / M02 — platform: the shared mutation guard, platform.operator (#29) and
-- platform.command_receipt (#30).
--
-- docs/DOMAIN.md §7; docs/ARCHITECTURE.md §4 (command boundary), §5 (auth), §6.2 (INVOKER default).

set role origenlab_owner;

-- Shared trigger function: refuses UPDATE or DELETE on append-only / never-deleted rows.
-- SECURITY INVOKER (the default), pinned search_path, static SQL only. It is a structural guard
-- that also binds the owner and the migrator, which grants alone cannot do.
create function platform.reject_mutation() returns trigger
language plpgsql
set search_path = pg_catalog
as $$
begin
  raise exception '%.%: % refused — rows are %', tg_table_schema, tg_table_name, tg_op, tg_argv[0]
    using errcode = 'P0001';
end
$$;

comment on function platform.reject_mutation() is
  'Trigger guard: rejects UPDATE/DELETE on append-only or never-deleted tables. SECURITY INVOKER.';

revoke all on function platform.reject_mutation() from public, anon, authenticated, service_role;

-- #29 platform.operator — auth user → role and status.
create table platform.operator (
  id uuid primary key default gen_random_uuid(),
  auth_user_id uuid not null,
  email_norm text not null,
  display_name text not null,
  role text not null,
  status text not null,
  invited_by_operator_id uuid references platform.operator (id),
  version integer not null default 1,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint operator_auth_user_id_key unique (auth_user_id),
  constraint operator_email_norm_key unique (email_norm),
  constraint operator_role_check check (role in ('admin', 'sales', 'viewer')),
  constraint operator_status_check check (status in ('active', 'disabled')),
  constraint operator_email_norm_shape check (
    email_norm = lower(email_norm) and email_norm ~ '^[^@\s]+@[^@\s]+\.[^@\s]+$'
  ),
  constraint operator_display_name_nonblank check (length(btrim(display_name)) > 0),
  constraint operator_version_positive check (version >= 1)
);

comment on table platform.operator is
  'DOMAIN.md §7 #29 — auth user → role and status. auth_user_id is the Supabase Auth uid (no FK into the managed auth schema).';

-- #30 platform.command_receipt — command idempotency.
create table platform.command_receipt (
  id uuid primary key default gen_random_uuid(),
  operator_id uuid not null references platform.operator (id),
  idempotency_key text not null,
  command_name text not null,
  request_digest text not null,
  status text not null,
  response_status smallint,
  response_body jsonb,
  created_at timestamptz not null default now(),
  completed_at timestamptz,
  constraint command_receipt_operator_key_key unique (operator_id, idempotency_key),
  constraint command_receipt_idempotency_key_length check (length(idempotency_key) between 1 and 200),
  constraint command_receipt_command_name_nonblank check (length(btrim(command_name)) > 0),
  constraint command_receipt_request_digest_sha256 check (request_digest ~ '^[0-9a-f]{64}$'),
  constraint command_receipt_status_check check (status in ('in_progress', 'completed', 'failed')),
  constraint command_receipt_completion_shape check ((status = 'in_progress') = (completed_at is null)),
  constraint command_receipt_response_body_object check (
    response_body is null or jsonb_typeof(response_body) in ('object', 'array')
  )
);

comment on table platform.command_receipt is
  'DOMAIN.md §7 #30 — command idempotency: (operator, key) unique; a digest mismatch is a 409 at the API.';

create index command_receipt_operator_created_idx
  on platform.command_receipt (operator_id, created_at desc);

-- Deny-by-default from birth: RLS enabled, no policy until the policies migration adds one.
alter table platform.operator enable row level security;
alter table platform.command_receipt enable row level security;

reset role;
