-- Slice 0 / M10c — outbound.campaign_reply (#33): reply attribution for a frozen audience.
--
-- docs/DOMAIN.md §7; docs/WORKFLOWS.md §1.4. The ingest classifier *proposes* a class; an operator
-- records the commercial verdict. Evidence-shaped, exactly like evidence.source_record: the worker
-- may insert, never decide. This table never writes campaign_recipient.state — moving a recipient
-- to 'replied' is a Slice 5 command, not a side effect of ingestion.
--
-- Nothing here grants a send capability.

set role origenlab_owner;

create table outbound.campaign_reply (
  id uuid primary key default gen_random_uuid(),
  campaign_id uuid not null,
  campaign_recipient_id uuid not null,
  message_id uuid not null references comms.message (id),
  send_attempt_id uuid references outbound.send_attempt (id),
  received_at timestamptz not null,
  proposed_class text not null,
  proposed_by text not null,
  operator_class text,
  classified_by_operator_id uuid references platform.operator (id),
  classified_at timestamptz,
  origin_source_record_id uuid references evidence.source_record (id),
  created_at timestamptz not null default now(),
  -- One reply row per provider message: re-ingesting the same message is idempotent.
  constraint campaign_reply_message_key unique (message_id),
  -- Reuses campaign_recipient's (id, campaign_id) key, so a reply can never cross campaigns.
  constraint campaign_reply_recipient_fkey foreign key (campaign_recipient_id, campaign_id)
    references outbound.campaign_recipient (id, campaign_id),
  constraint campaign_reply_proposed_class_check check (proposed_class in (
    'human_reply', 'auto_reply', 'ndr', 'unsubscribe_request', 'complaint', 'unclassified')),
  constraint campaign_reply_proposed_by_check check (proposed_by in (
    'ingest_classifier', 'operator_command')),
  -- The operator may also overturn the proposal entirely ('not_a_reply').
  constraint campaign_reply_operator_class_check check (operator_class is null or operator_class in (
    'human_reply', 'auto_reply', 'ndr', 'unsubscribe_request', 'complaint', 'not_a_reply')),
  constraint campaign_reply_operator_triple check (
    num_nonnulls(operator_class, classified_by_operator_id, classified_at) in (0, 3))
);

comment on table outbound.campaign_reply is
  'DOMAIN.md §7 #33 — campaign reply attribution: one row per comms.message; the classifier proposes, the operator records. Never the writer of campaign_recipient.state.';

-- Foreign-key covering indexes (supabase/tests/090_foreign_key_indexes.sql enumerates pg_constraint
-- itself, so a new FK is judged by the same rule). message_id is covered by its unique constraint.
create index campaign_reply_recipient_idx
  on outbound.campaign_reply (campaign_recipient_id, campaign_id);
create index campaign_reply_attempt_idx
  on outbound.campaign_reply (send_attempt_id) where send_attempt_id is not null;
create index campaign_reply_operator_idx
  on outbound.campaign_reply (classified_by_operator_id) where classified_by_operator_id is not null;
create index campaign_reply_source_record_idx
  on outbound.campaign_reply (origin_source_record_id) where origin_source_record_id is not null;
-- Read path: a campaign's replies, newest first.
create index campaign_reply_campaign_received_idx
  on outbound.campaign_reply (campaign_id, received_at desc);

-- The worker proposes (insert); the operator records the verdict through the API, which may also
-- log a reply by hand but may never rewrite the machine's proposal.
grant select, insert on outbound.campaign_reply to origenlab_worker;
grant select, insert on outbound.campaign_reply to origenlab_api;
grant update (operator_class, classified_by_operator_id, classified_at)
  on outbound.campaign_reply to origenlab_api;

alter table outbound.campaign_reply enable row level security;
create policy origenlab_api_select    on outbound.campaign_reply for select to origenlab_api using (true);
create policy origenlab_api_insert    on outbound.campaign_reply for insert to origenlab_api with check (true);
create policy origenlab_api_update    on outbound.campaign_reply for update to origenlab_api using (true) with check (true);
create policy origenlab_worker_select on outbound.campaign_reply for select to origenlab_worker using (true);
create policy origenlab_worker_insert on outbound.campaign_reply for insert to origenlab_worker with check (true);

reset role;
