-- Slice 0 / M11 — procurement.notice (#28): ChileCompra notice head and history.
--
-- docs/DOMAIN.md §7; docs/WORKFLOWS.md §W7. Notices are machine evidence that is re-fetched,
-- never deleted; withdrawal is recorded with disappeared_at. The promotion link points from the
-- rebuildable notice to the durable opportunity, never the other way round (docs/DATA.md §3).

set role origenlab_owner;

create table procurement.notice (
  id uuid primary key default gen_random_uuid(),
  codigo_externo text not null,
  buyer_name text,
  buyer_code text,
  title text,
  source_status text,
  published_at timestamptz,
  closes_at timestamptz,
  first_seen_at timestamptz not null default now(),
  last_seen_at timestamptz not null default now(),
  disappeared_at timestamptz,
  head jsonb not null,
  head_history jsonb not null default '[]'::jsonb,
  promoted_opportunity_id uuid references crm.opportunity (id),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint notice_codigo_externo_key unique (codigo_externo),
  constraint notice_codigo_externo_nonblank check (length(btrim(codigo_externo)) > 0),
  constraint notice_head_object check (jsonb_typeof(head) = 'object'),
  constraint notice_head_history_array check (jsonb_typeof(head_history) = 'array'),
  constraint notice_seen_order check (last_seen_at >= first_seen_at),
  constraint notice_disappeared_after_last_seen check (disappeared_at is null or disappeared_at >= last_seen_at)
);

comment on table procurement.notice is
  'DOMAIN.md §7 #28 — ChileCompra notice head and history: codigo_externo unique; disappeared_at for withdrawal; never deleted.';

create index notice_open_idx on procurement.notice (closes_at) where disappeared_at is null;
create index notice_promoted_idx on procurement.notice (promoted_opportunity_id) where promoted_opportunity_id is not null;

create trigger notice_never_deleted
  before delete on procurement.notice
  for each row execute function platform.reject_mutation('never deleted (withdrawal sets disappeared_at)');

alter table procurement.notice enable row level security;

reset role;
