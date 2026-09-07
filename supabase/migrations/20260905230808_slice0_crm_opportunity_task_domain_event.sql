-- Slice 0 / M06 — crm lifecycle: opportunity (#8), opportunity_participant (#32), task (#9)
-- and domain_event (#11).
--
-- docs/DOMAIN.md §3 (relationships, leads, opportunities, participants), §7;
-- docs/DATA.md §1.1 (the audit stream); docs/WORKFLOWS.md §1.1 (stage vocabulary).
-- Declarative here: closed vocabularies, the organization-required-from-qualified rule, the
-- won/closed shapes, the participant subject/overlap/primary rules, and the append-only event
-- stream with a closed event_type list and a SECURITY INVOKER structural validator. The stage
-- transition table, the existence rule (organization or ≥1 current participant) and participant
-- consistency checks are Slice 2 command triggers (docs/MIGRATION.md §5).

set role origenlab_owner;

-- #8 crm.opportunity — lead → outcome lifecycle; the only owner of won/lost.
-- Deliberately no person_id and no contact_point_id: participants are the only authority.
create table crm.opportunity (
  id uuid primary key default gen_random_uuid(),
  organization_id uuid references crm.organization (id),
  title text not null,
  stage text not null,
  owner_operator_id uuid not null references platform.operator (id),
  won_quote_id uuid,
  won_revision_no integer,
  closed_at timestamptz,
  close_reason text,
  reopened_from_opportunity_id uuid references crm.opportunity (id),
  origin_source_record_id uuid references evidence.source_record (id),
  version integer not null default 1,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint opportunity_title_nonblank check (length(btrim(title)) > 0),
  constraint opportunity_stage_check check (
    stage in ('lead', 'qualifying', 'qualified', 'quoting', 'negotiating', 'won', 'lost', 'abandoned')
  ),
  -- Organization is mandatory from `qualified` onward (DOMAIN.md §3.3).
  constraint opportunity_organization_required_from_qualified check (
    stage in ('lead', 'qualifying') or organization_id is not null
  ),
  -- won ⇔ (won_quote_id, won_revision_no) set (WORKFLOWS.md §1.1).
  constraint opportunity_won_shape check (
    case when stage = 'won'
      then won_quote_id is not null and won_revision_no is not null
      else won_quote_id is null and won_revision_no is null
    end
  ),
  -- won/lost/abandoned ⇔ closed_at set.
  constraint opportunity_closed_shape check ((stage in ('won', 'lost', 'abandoned')) = (closed_at is not null)),
  constraint opportunity_no_self_reopen check (reopened_from_opportunity_id is distinct from id),
  constraint opportunity_version_positive check (version >= 1)
);

comment on table crm.opportunity is
  'DOMAIN.md §7 #8 — lead → outcome lifecycle, the only owner of won/lost: organization or ≥1 current participant (Slice 2 trigger); organization required from qualified; no person or contact-point column.';

create index opportunity_organization_idx on crm.opportunity (organization_id) where organization_id is not null;
create index opportunity_owner_idx on crm.opportunity (owner_operator_id);
create index opportunity_open_stage_idx on crm.opportunity (stage) where closed_at is null;

-- #32 crm.opportunity_participant — human roles on one opportunity.
create table crm.opportunity_participant (
  id uuid primary key default gen_random_uuid(),
  opportunity_id uuid not null references crm.opportunity (id),
  person_id uuid references crm.person (id),
  contact_point_id uuid references crm.contact_point (id),
  role text not null,
  is_primary boolean not null default false,
  valid_from date not null,
  valid_to date,
  confirmation text not null,
  confirmed_by_operator_id uuid references platform.operator (id),
  origin_source_record_id uuid references evidence.source_record (id),
  note text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint opportunity_participant_role_check check (
    role in ('end_user', 'technical', 'purchasing', 'finance', 'approver', 'quote_recipient', 'signatory', 'other')
  ),
  -- A participant may begin as only a contact point and gain person_id later.
  constraint opportunity_participant_subject_present check (num_nonnulls(person_id, contact_point_id) >= 1),
  constraint opportunity_participant_validity check (valid_to is null or valid_to > valid_from),
  constraint opportunity_participant_confirmation_check check (confirmation in ('machine_proposed', 'confirmed')),
  constraint opportunity_participant_confirmed_by_shape check (
    confirmation = 'confirmed' or confirmed_by_operator_id is null
  ),
  -- The same (opportunity, subject, role) may not overlap itself in time (DOMAIN.md §3.3).
  constraint opportunity_participant_no_overlap_same_subject_role exclude using gist (
    opportunity_id with =,
    role with =,
    (coalesce(person_id, contact_point_id)) with =,
    daterange(valid_from, valid_to, '[)') with &&
  )
);

comment on table crm.opportunity_participant is
  'DOMAIN.md §7 #32 — human roles on one opportunity: person and/or contact point; closed role; one current primary per role; no overlap for the same subject and role; rows are never deleted.';

-- At most one current primary participant per role.
create unique index opportunity_participant_one_current_primary_per_role
  on crm.opportunity_participant (opportunity_id, role) where is_primary and valid_to is null;
create index opportunity_participant_opportunity_idx on crm.opportunity_participant (opportunity_id);
create index opportunity_participant_person_idx on crm.opportunity_participant (person_id) where person_id is not null;
create index opportunity_participant_contact_point_idx on crm.opportunity_participant (contact_point_id)
  where contact_point_id is not null;

create trigger opportunity_participant_never_deleted
  before delete on crm.opportunity_participant
  for each row execute function platform.reject_mutation('never deleted (close valid_to instead)');

-- #9 crm.task — follow-up with a due date.
create table crm.task (
  id uuid primary key default gen_random_uuid(),
  opportunity_id uuid not null references crm.opportunity (id),
  owner_operator_id uuid not null references platform.operator (id),
  title text not null,
  due_at timestamptz not null,
  status text not null default 'open',
  completed_at timestamptz,
  cancel_reason text,
  created_by_operator_id uuid references platform.operator (id),
  version integer not null default 1,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint task_title_nonblank check (length(btrim(title)) > 0),
  constraint task_status_check check (status in ('open', 'done', 'cancelled')),
  -- done ⇔ completed_at IS NOT NULL (DOMAIN.md §7 #9).
  constraint task_done_shape check ((status = 'done') = (completed_at is not null)),
  constraint task_cancel_shape check ((status = 'cancelled') = (cancel_reason is not null)),
  constraint task_version_positive check (version >= 1)
);

comment on table crm.task is
  'DOMAIN.md §7 #9 — follow-up with a due date: done ⇔ completed_at IS NOT NULL; tasks never change an opportunity stage.';

create index task_opportunity_idx on crm.task (opportunity_id);
create index task_owner_open_due_idx on crm.task (owner_operator_id, due_at) where status = 'open';

-- #11 crm.domain_event — the single audit stream.
-- Structural validator (SECURITY INVOKER, IMMUTABLE): the payload is a JSON object, the payload
-- version is the one currently defined (1), and the event_type family matches the aggregate kind.
-- Per-event payload schemas arrive with the commands that emit them (Slice 2+), as new versions of
-- this function by migration.
create function crm.domain_event_is_valid(
  p_aggregate_kind text,
  p_event_type text,
  p_payload_version smallint,
  p_payload jsonb
) returns boolean
language sql
immutable
set search_path = pg_catalog
as $$
  select p_payload is not null
     and jsonb_typeof(p_payload) = 'object'
     and p_payload_version = 1
     and split_part(p_event_type, '.', 1) = case p_aggregate_kind
           when 'organization'              then 'organization'
           when 'person'                    then 'person'
           when 'affiliation'               then 'affiliation'
           when 'address'                   then 'address'
           when 'contact_point'             then 'contact_point'
           when 'organization_relationship' then 'relationship'
           when 'opportunity'               then 'opportunity'
           when 'opportunity_participant'   then 'participant'
           when 'task'                      then 'task'
           when 'activity'                  then 'activity'
           when 'quote'                     then 'quote'
           when 'quote_revision'            then 'quote_revision'
           when 'campaign'                  then 'campaign'
           when 'send_attempt'              then 'send_attempt'
           when 'contact_control'           then 'contact_control'
           when 'send_control'              then 'send_control'
           when 'operator'                  then 'operator'
           when 'assertion'                 then 'assertion'
           when 'source_record'             then 'source_record'
           when 'product'                   then 'product'
         end;
$$;

comment on function crm.domain_event_is_valid(text, text, smallint, jsonb) is
  'CHECK helper for crm.domain_event: payload is an object, payload_version is defined, event_type family matches aggregate_kind. SECURITY INVOKER.';

revoke all on function crm.domain_event_is_valid(text, text, smallint, jsonb)
  from public, anon, authenticated, service_role;

create table crm.domain_event (
  id uuid primary key default gen_random_uuid(),
  stream_position bigint generated always as identity,
  aggregate_kind text not null,
  aggregate_id uuid not null,
  seq integer not null,
  event_type text not null,
  payload_version smallint not null,
  payload jsonb not null,
  actor_kind text not null,
  actor_operator_id uuid references platform.operator (id),
  command_receipt_id uuid references platform.command_receipt (id),
  recorded_at timestamptz not null default now(),
  constraint domain_event_stream_position_key unique (stream_position),
  -- Monotonic per-aggregate sequence.
  constraint domain_event_aggregate_seq_key unique (aggregate_kind, aggregate_id, seq),
  constraint domain_event_seq_positive check (seq >= 1),
  constraint domain_event_aggregate_kind_check check (aggregate_kind in (
    'organization', 'person', 'affiliation', 'address', 'contact_point', 'organization_relationship',
    'opportunity', 'opportunity_participant', 'task', 'activity', 'quote', 'quote_revision',
    'campaign', 'send_attempt', 'contact_control', 'send_control', 'operator',
    'assertion', 'source_record', 'product'
  )),
  -- Closed event_type list, one entry per family in DATA.md §1.1. Extending it is a migration.
  constraint domain_event_type_check check (event_type in (
    'organization.created', 'organization.confirmed', 'organization.merged',
    'person.created', 'person.confirmed', 'person.merged',
    'affiliation.opened', 'affiliation.closed',
    'address.added', 'address.superseded',
    'contact_point.created', 'contact_point.confirmed',
    'relationship.changed',
    'opportunity.created', 'opportunity.staged', 'opportunity.organization_set', 'opportunity.closed',
    'participant.added', 'participant.linked', 'participant.primary_changed', 'participant.ended',
    'task.created', 'task.completed', 'task.cancelled',
    'activity.linked',
    'quote.created', 'quote_revision.transitioned', 'quote_revision.superseded',
    'campaign.transitioned', 'campaign.approved', 'campaign.override_granted', 'campaign.dry_run_recorded',
    'send_attempt.submission_changed', 'send_attempt.delivery_changed',
    'contact_control.added', 'contact_control.revoked',
    'send_control.changed',
    'operator.changed',
    'assertion.promoted', 'source_record.quarantined', 'source_record.migration_manifest_recorded',
    'product.created'
  )),
  constraint domain_event_payload_version_positive check (payload_version >= 1),
  constraint domain_event_actor_kind_check check (actor_kind in ('operator', 'worker', 'migrator')),
  constraint domain_event_actor_shape check ((actor_kind = 'operator') = (actor_operator_id is not null)),
  constraint domain_event_payload_valid check (
    crm.domain_event_is_valid(aggregate_kind, event_type, payload_version, payload)
  )
);

comment on table crm.domain_event is
  'DOMAIN.md §7 #11 — the single audit stream: closed event_type; payload_version; validated payload; INSERT only (no UPDATE, no DELETE — grants and trigger).';

create index domain_event_aggregate_idx on crm.domain_event (aggregate_kind, aggregate_id, seq);
create index domain_event_recorded_at_idx on crm.domain_event (recorded_at);
create index domain_event_command_receipt_idx on crm.domain_event (command_receipt_id) where command_receipt_id is not null;

create trigger domain_event_append_only
  before update or delete on crm.domain_event
  for each row execute function platform.reject_mutation('append-only');

alter table crm.opportunity enable row level security;
alter table crm.opportunity_participant enable row level security;
alter table crm.task enable row level security;
alter table crm.domain_event enable row level security;

reset role;
