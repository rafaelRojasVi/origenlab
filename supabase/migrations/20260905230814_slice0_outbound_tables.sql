-- Slice 0 / M10 — outbound: send_control (#19), campaign (#20), campaign_recipient (#21),
-- send_attempt (#22), contact_control (#23), plus the cross-schema links
-- comms.message.send_attempt_id and crm.quote_revision.sent_attempt_id.
--
-- docs/DOMAIN.md §7; docs/WORKFLOWS.md §1.3-1.6 (state vocabularies and the contact-control truth
-- table), §2 (the send predicate), §W12 (recontact override); docs/DATA.md §7 (Wave 1A shapes).
-- Declarative here: every closed vocabulary, the single send_control row with both flags false, the
-- purpose-scoped kind/purpose rules, the submission/delivery separation, one open attempt per
-- address, the frozen-audience keys and the all-or-none override triple. The privileged send
-- functions, the predicate, the state-transition triggers and the override-immutability trigger are
-- Slice 5 (docs/MIGRATION.md §5). No SECURITY DEFINER function exists yet.

set role origenlab_owner;

-- #19 outbound.send_control — global kill switches. Exactly one row, id = 1, both flags false.
create table outbound.send_control (
  id integer primary key,
  marketing_enabled boolean not null default false,
  transactional_enabled boolean not null default false,
  changed_at timestamptz not null default now(),
  changed_by_operator_id uuid references platform.operator (id),
  change_reason text not null,
  constraint send_control_single_row check (id = 1),
  constraint send_control_reason_nonblank check (length(btrim(change_reason)) > 0)
);

comment on table outbound.send_control is
  'DOMAIN.md §7 #19 — global kill switches: single row id = 1; both flags default false; every change carries a reason; changed only by an admin command.';

-- The row exists from the start so the predicate never finds an absent row. Both flags false.
insert into outbound.send_control (id, marketing_enabled, transactional_enabled, change_reason)
values (1, false, false, 'slice 0 bootstrap: both send flags false until an admin command changes them');

create trigger send_control_never_deleted
  before delete on outbound.send_control
  for each row execute function platform.reject_mutation('never deleted (the single control row is permanent)');

-- #20 outbound.campaign — lifecycle, budget, policy, approval.
create table outbound.campaign (
  id uuid primary key default gen_random_uuid(),
  name text not null,
  status text not null default 'draft',
  mailbox_id uuid not null references comms.mailbox (id),
  max_sends integer not null,
  recontact_interval_days integer not null,
  policy_include_suppliers boolean not null default false,
  approved_at timestamptz,
  approved_by_operator_id uuid references platform.operator (id),
  approved_override_count integer,
  created_by_operator_id uuid references platform.operator (id),
  origin_source_record_id uuid references evidence.source_record (id),
  version integer not null default 1,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint campaign_name_nonblank check (length(btrim(name)) > 0),
  constraint campaign_status_check check (
    status in ('draft', 'audience_frozen', 'approved', 'active', 'paused', 'completed', 'archived', 'cancelled')
  ),
  constraint campaign_max_sends_positive check (max_sends >= 1),
  constraint campaign_recontact_interval_positive check (recontact_interval_days >= 1),
  constraint campaign_override_count_nonnegative check (approved_override_count is null or approved_override_count >= 0),
  -- From approval onward the approval facts are present (the archived V1 campaign predates V2 approval).
  constraint campaign_approval_shape check (
    status not in ('approved', 'active', 'paused', 'completed')
    or (approved_at is not null and approved_by_operator_id is not null and approved_override_count is not null)
  ),
  constraint campaign_version_positive check (version >= 1)
);

comment on table outbound.campaign is
  'DOMAIN.md §7 #20 — lifecycle, budget, policy, approval: status machine (Slice 5 trigger); budget serialized by a row lock in reserve_attempts.';

create index campaign_status_idx on outbound.campaign (status);

-- #21 outbound.campaign_recipient — frozen audience, state, immutable recontact override.
create table outbound.campaign_recipient (
  id uuid primary key default gen_random_uuid(),
  campaign_id uuid not null references outbound.campaign (id),
  address_norm text not null,
  contact_point_id uuid references crm.contact_point (id),
  person_id uuid references crm.person (id),
  organization_id uuid references crm.organization (id),
  state text not null default 'snapshotted',
  exclusion_reason text,
  attempt_count integer not null default 0,
  recontact_override_by_operator_id uuid references platform.operator (id),
  recontact_override_reason text,
  recontact_override_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint campaign_recipient_campaign_address_key unique (campaign_id, address_norm),
  -- Lets send_attempt assert that its recipient belongs to its campaign.
  constraint campaign_recipient_id_campaign_key unique (id, campaign_id),
  constraint campaign_recipient_address_shape check (
    address_norm = lower(address_norm) and address_norm ~ '^[^@\s]+@[^@\s]+\.[^@\s]+$'
  ),
  constraint campaign_recipient_state_check check (state in (
    'snapshotted', 'excluded', 'reserved', 'sent', 'failed', 'needs_review', 'bounced', 'replied', 'unsubscribed'
  )),
  constraint campaign_recipient_exclusion_reason_check check (exclusion_reason is null or exclusion_reason in (
    'block', 'prior_contact', 'cooldown', 'policy_supplier', 'policy_no_channel', 'precheck_block', 'precheck_switch'
  )),
  constraint campaign_recipient_exclusion_shape check ((state = 'excluded') = (exclusion_reason is not null)),
  constraint campaign_recipient_attempt_count_nonnegative check (attempt_count >= 0),
  -- The override triple is set together or not at all (WORKFLOWS.md §W12); immutability is a Slice 5 trigger.
  constraint campaign_recipient_override_triple check (
    num_nonnulls(recontact_override_by_operator_id, recontact_override_reason, recontact_override_at) in (0, 3)
  )
);

comment on table outbound.campaign_recipient is
  'DOMAIN.md §7 #21 — frozen audience, state, immutable recontact override: (campaign_id, address_norm) unique; inserts only while draft (Slice 5 trigger).';

create index campaign_recipient_campaign_state_idx on outbound.campaign_recipient (campaign_id, state);

-- #22 outbound.send_attempt — the only send ledger, marketing and transactional.
create table outbound.send_attempt (
  id uuid primary key default gen_random_uuid(),
  purpose text not null,
  campaign_id uuid references outbound.campaign (id),
  campaign_recipient_id uuid,
  quote_revision_id uuid references crm.quote_revision (id),
  mailbox_id uuid not null references comms.mailbox (id),
  address_norm text not null,
  submission_state text not null default 'reserved',
  delivery_state text not null default 'n/a',
  bounce_class text,
  rfc822_message_id text,
  dispatch_started_at timestamptz,
  lease_expires_at timestamptz,
  accepted_at timestamptz,
  provider_message_id text,
  provider_thread_id text,
  error_class text,
  error_detail text,
  search_evidence jsonb,
  needs_human boolean not null default false,
  resolution_verdict text,
  resolution_reason text,
  resolved_at timestamptz,
  resolved_by_operator_id uuid references platform.operator (id),
  retry_of_attempt_id uuid references outbound.send_attempt (id),
  retry_reason text,
  origin_source_record_id uuid references evidence.source_record (id),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  -- The only RFC 822 uniqueness in the system: OrigenLab-minted outbound ids (DATA.md §6).
  constraint send_attempt_rfc822_message_id_key unique (rfc822_message_id),
  constraint send_attempt_recipient_in_campaign_fkey foreign key (campaign_recipient_id, campaign_id)
    references outbound.campaign_recipient (id, campaign_id),
  constraint send_attempt_address_shape check (
    address_norm = lower(address_norm) and address_norm ~ '^[^@\s]+@[^@\s]+\.[^@\s]+$'
  ),
  constraint send_attempt_purpose_check check (purpose in ('marketing', 'transactional')),
  -- Transactional is a workflow property with a mandatory typed reference (WORKFLOWS.md §1.6).
  constraint send_attempt_purpose_reference_shape check (
    case purpose
      when 'marketing'     then campaign_id is not null and campaign_recipient_id is not null and quote_revision_id is null
      when 'transactional' then quote_revision_id is not null and campaign_id is null and campaign_recipient_id is null
    end
  ),
  constraint send_attempt_submission_state_check check (submission_state in (
    'reserved', 'skipped', 'dispatching', 'accepted', 'rejected', 'ambiguous', 'not_dispatched'
  )),
  constraint send_attempt_delivery_state_check check (delivery_state in (
    'n/a', 'pending', 'sent_copy_confirmed', 'bounced', 'complained'
  )),
  -- delivery_state ≠ 'n/a' ⇔ submission_state = 'accepted' (WORKFLOWS.md §1.5).
  constraint send_attempt_delivery_requires_accepted check ((delivery_state <> 'n/a') = (submission_state = 'accepted')),
  constraint send_attempt_bounce_class_check check (bounce_class is null or bounce_class in ('hard', 'soft')),
  constraint send_attempt_bounce_class_shape check ((bounce_class is not null) = (delivery_state = 'bounced')),
  -- Minted at begin_dispatch: absent before, present while dispatching; migrated V1 rows may be NULL.
  constraint send_attempt_minted_id_shape check (
    case
      when submission_state in ('reserved', 'skipped') then rfc822_message_id is null
      when submission_state = 'dispatching' then rfc822_message_id is not null
      else true
    end
  ),
  constraint send_attempt_dispatching_shape check (
    submission_state <> 'dispatching' or (dispatch_started_at is not null and lease_expires_at is not null)
  ),
  constraint send_attempt_accepted_shape check (submission_state <> 'accepted' or accepted_at is not null),
  constraint send_attempt_error_class_check check (error_class is null or error_class in ('transient', 'permanent', 'invalid_address')),
  constraint send_attempt_rejected_has_error_class check (submission_state <> 'rejected' or error_class is not null),
  constraint send_attempt_search_evidence_object check (search_evidence is null or jsonb_typeof(search_evidence) = 'object'),
  constraint send_attempt_resolution_verdict_check check (resolution_verdict is null or resolution_verdict in ('accepted', 'not_dispatched')),
  constraint send_attempt_resolution_shape check (
    num_nonnulls(resolution_verdict, resolution_reason, resolved_at, resolved_by_operator_id) in (0, 4)
  ),
  constraint send_attempt_retry_shape check ((retry_of_attempt_id is null) = (retry_reason is null)),
  constraint send_attempt_no_self_retry check (retry_of_attempt_id is distinct from id)
);

comment on table outbound.send_attempt is
  'DOMAIN.md §7 #22 — the only send ledger, marketing and transactional: minted RFC 822 id unique; at most one open attempt per address; submission and delivery states never collapsed.';

-- At most one in-flight attempt per address (WORKFLOWS.md §2.1).
create unique index send_attempt_one_open_per_address
  on outbound.send_attempt (address_norm) where submission_state in ('reserved', 'dispatching', 'ambiguous');
create index send_attempt_campaign_idx on outbound.send_attempt (campaign_id) where campaign_id is not null;
create index send_attempt_recipient_idx on outbound.send_attempt (campaign_recipient_id) where campaign_recipient_id is not null;
create index send_attempt_quote_revision_idx on outbound.send_attempt (quote_revision_id) where quote_revision_id is not null;
create index send_attempt_open_lease_idx on outbound.send_attempt (lease_expires_at) where submission_state = 'dispatching';
create index send_attempt_ambiguous_idx on outbound.send_attempt (created_at) where submission_state = 'ambiguous';

-- #23 outbound.contact_control — purpose-scoped block / prior_contact / cooldown.
-- Keyed by normalized text, deliberately not by FK, so a safety fact survives every merge.
create table outbound.contact_control (
  id uuid primary key default gen_random_uuid(),
  scope text not null,
  value_norm text not null,
  kind text not null,
  purpose text not null,
  reason text not null,
  source text not null,
  until_at timestamptz,
  needs_review boolean not null default false,
  created_by_operator_id uuid references platform.operator (id),
  origin_source_record_id uuid references evidence.source_record (id),
  created_at timestamptz not null default now(),
  constraint contact_control_key unique (scope, value_norm, kind, purpose),
  constraint contact_control_scope_check check (scope in ('address', 'domain')),
  constraint contact_control_kind_check check (kind in ('block', 'prior_contact', 'cooldown')),
  constraint contact_control_purpose_check check (purpose in ('all', 'marketing')),
  -- prior_contact and cooldown are outreach facts: always marketing (WORKFLOWS.md §1.6).
  constraint contact_control_outreach_facts_are_marketing check (kind = 'block' or purpose = 'marketing'),
  -- Domain-scoped controls are blocks.
  constraint contact_control_domain_scope_is_block check (scope = 'address' or kind = 'block'),
  -- cooldown ⇔ until_at.
  constraint contact_control_cooldown_shape check ((kind = 'cooldown') = (until_at is not null)),
  constraint contact_control_reason_nonblank check (length(btrim(reason)) > 0),
  constraint contact_control_source_check check (source in (
    'wave1a_union', 'wave1a_rfc2047_addendum', 'wave1a_suppression', 'wave1a_investigation',
    'send_accepted', 'ndr_handler', 'complaint_handler', 'unsubscribe_handler', 'operator_command'
  )),
  constraint contact_control_value_shape check (
    case scope
      when 'address' then value_norm = lower(value_norm) and value_norm ~ '^[^@\s]+@[^@\s]+\.[^@\s]+$'
      when 'domain'  then value_norm = lower(value_norm) and value_norm ~ '^[a-z0-9-]+(\.[a-z0-9-]+)+$'
    end
  )
);

comment on table outbound.contact_control is
  'DOMAIN.md §7 #23 — purpose-scoped block / prior_contact / cooldown: (scope, value_norm, kind, purpose) unique; prior_contact and cooldown are marketing only; prior_contact never deleted, never expires.';

create index contact_control_lookup_idx on outbound.contact_control (value_norm, scope, purpose);
create index contact_control_review_idx on outbound.contact_control (created_at) where needs_review;

-- prior_contact is permanent: never deleted, never expires, never rewritten.
create trigger contact_control_prior_contact_permanent
  before update or delete on outbound.contact_control
  for each row when (old.kind = 'prior_contact')
  execute function platform.reject_mutation('permanent (prior_contact is never deleted and never expires)');

-- Cross-schema links that needed send_attempt to exist.
alter table comms.message
  add constraint message_send_attempt_fkey foreign key (send_attempt_id) references outbound.send_attempt (id);
alter table crm.quote_revision
  add constraint quote_revision_sent_attempt_fkey foreign key (sent_attempt_id) references outbound.send_attempt (id);

alter table outbound.send_control enable row level security;
alter table outbound.campaign enable row level security;
alter table outbound.campaign_recipient enable row level security;
alter table outbound.send_attempt enable row level security;
alter table outbound.contact_control enable row level security;

reset role;
