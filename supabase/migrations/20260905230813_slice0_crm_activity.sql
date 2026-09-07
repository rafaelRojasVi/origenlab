-- Slice 0 / M09 — crm.activity (#10): an operator-relevant interaction deliberately linked to an
-- opportunity, optionally to a comms.message. Created after comms so the message link is a real FK.
--
-- docs/DOMAIN.md §7; docs/DATA.md §1.1 (the three audit-shaped tables are disjoint: a message
-- nobody linked is not an activity). Append-only: INSERT and SELECT only, plus a trigger guard.

set role origenlab_owner;

create table crm.activity (
  id uuid primary key default gen_random_uuid(),
  opportunity_id uuid not null references crm.opportunity (id),
  kind text not null,
  message_id uuid references comms.message (id),
  occurred_at timestamptz not null,
  summary text,
  recorded_by_operator_id uuid not null references platform.operator (id),
  created_at timestamptz not null default now(),
  constraint activity_kind_check check (kind in ('call', 'meeting', 'note', 'email')),
  -- A linked email is an activity of kind email, and only then.
  constraint activity_email_has_message check ((kind = 'email') = (message_id is not null)),
  constraint activity_summary_or_message check (summary is not null or message_id is not null),
  -- One link per (message, opportunity).
  constraint activity_message_opportunity_key unique (message_id, opportunity_id)
);

comment on table crm.activity is
  'DOMAIN.md §7 #10 — operator-relevant interaction, optional message link: append-only; (message_id, opportunity_id) unique.';

create index activity_opportunity_occurred_idx on crm.activity (opportunity_id, occurred_at desc);

create trigger activity_append_only
  before update or delete on crm.activity
  for each row execute function platform.reject_mutation('append-only');

alter table crm.activity enable row level security;

reset role;
