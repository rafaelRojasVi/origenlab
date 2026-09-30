-- Slice 6 — the archived campaign HTML recovered from the Gmail Sent history, and the first
-- freeform CRM authoring surface (people, institutions, contact points, classifications, product
-- lines, supplier candidates, notes) with archive-instead-of-delete semantics.
--
-- docs/DOMAIN.md §7 #42–#45; docs/WORKFLOWS.md §W4 (campaign history), §W14 (CRM authoring).
--
-- Part A — campaign content archive (#42, #43). The four historical campaigns in `outbound.campaign`
-- are archived and immutable (`outbound.archived_campaign_immutable`): their rows carry no subject,
-- preheader or HTML and never will. What was actually sent survives only as Sent copies in the V1
-- Gmail archive, attributable to a send attempt by recipient lineage corroborated by sender, subject
-- and a timestamp window (the Gmail resource id V1 stored is not the RFC Message-ID the archive
-- keeps). The recovered content is stored next to the campaign, one row per distinct normalized HTML
-- variant, immutable, hashed, with per-message provenance in #43 — never by rewriting the campaign.
--
-- Part B — CRM authoring (#44, #45 and lifecycle columns). Until now every CRM write was bound to
-- evidence. Operators need to create and correct people and institutions directly, attach contact
-- points, open and close affiliations and commercial classifications, link a real supplier to the
-- six curated product lines, decide machine-proposed supplier candidates, and annotate. Nothing is
-- ever physically deleted: a person, institution or contact point is `archived`/`inactive`, a
-- domain or identifier is soft-removed, a note is revised by a new row and archived, never edited.
--
-- What it deliberately does not do: no campaign, recipient or attempt row changes; no SECURITY
-- DEFINER; no grant widens beyond the new tables and the lifecycle columns; no event is written by
-- this migration; no send flag moves.

set role origenlab_owner;

-- ═══════════════════════════════════════════════════════════════════════════════════════════════
-- Part A — campaign content archive
-- ═══════════════════════════════════════════════════════════════════════════════════════════════

-- #42 outbound.campaign_content — one immutable row per campaign × content kind × HTML variant.
create table outbound.campaign_content (
  id uuid primary key default gen_random_uuid(),
  campaign_id uuid not null references outbound.campaign (id),
  content_kind text not null,
  variant_no integer not null,
  subject text,
  preheader text,
  body_html text not null,
  body_html_sha256 text not null,
  body_html_normalized_sha256 text not null,
  message_count integer not null default 0,
  first_sent_at timestamptz,
  last_sent_at timestamptz,
  attribution_method text not null,
  attribution_confidence text not null,
  attribution_policy_version text not null,
  unmatched_attempt_count integer not null default 0,
  origin_source_record_id uuid not null references evidence.source_record (id),
  created_at timestamptz not null default now(),
  constraint campaign_content_kind_check check (content_kind in ('sent_html', 'historical_draft')),
  constraint campaign_content_variant_positive check (variant_no >= 1),
  constraint campaign_content_variant_key unique (campaign_id, content_kind, variant_no),
  constraint campaign_content_hash_key unique (campaign_id, content_kind, body_html_normalized_sha256),
  constraint campaign_content_sha256_shape check (
    body_html_sha256 ~ '^[0-9a-f]{64}$' and body_html_normalized_sha256 ~ '^[0-9a-f]{64}$'
  ),
  constraint campaign_content_body_nonblank check (length(btrim(body_html)) > 0),
  constraint campaign_content_subject_shape check (subject is null or length(btrim(subject)) > 0),
  constraint campaign_content_preheader_shape check (preheader is null or length(preheader) <= 255),
  constraint campaign_content_method_check check (
    attribution_method in ('gmail_message_id', 'recipient_lineage_timestamp', 'unsent_draft')
  ),
  constraint campaign_content_confidence_check check (attribution_confidence in ('exact', 'corroborated')),
  constraint campaign_content_policy_shape check (attribution_policy_version ~ '^[a-z0-9-]+/[0-9]{4}-[0-9]{2}-[0-9]{2}\.v[0-9]+$'),
  constraint campaign_content_counts_shape check (message_count >= 0 and unmatched_attempt_count >= 0),
  -- A sent variant was sent at least once and has a send window; a historical draft was never sent.
  constraint campaign_content_kind_shape check (
    case content_kind
      when 'sent_html' then message_count >= 1 and first_sent_at is not null and last_sent_at is not null
                            and last_sent_at >= first_sent_at
                            and attribution_method <> 'unsent_draft'
      when 'historical_draft' then message_count = 0 and first_sent_at is null and last_sent_at is null
                            and attribution_method = 'unsent_draft'
    end
  )
);

comment on table outbound.campaign_content is
  'DOMAIN.md §7 #42 — immutable historical campaign content: one row per campaign, content kind (sent_html / historical_draft) and normalized-HTML variant, with raw and normalized SHA-256, the send window, how it was attributed and under which policy. Inserted only by the historical importer (origenlab_owner); never updated or deleted. Never a rewrite of outbound.campaign.';
comment on column outbound.campaign_content.body_html_normalized_sha256 is
  'SHA-256 of the HTML with runs of whitespace collapsed to one space and trimmed — the variant identity.';
comment on column outbound.campaign_content.attribution_method is
  'gmail_message_id: the attempt''s provider id matched the archive (precedence 1); recipient_lineage_timestamp: the attempt''s recipient, sender, subject and a timestamp window matched exactly one Sent copy (precedence 2+3); unsent_draft: a draft never sent.';

create index campaign_content_campaign_idx on outbound.campaign_content (campaign_id);
create index campaign_content_origin_idx on outbound.campaign_content (origin_source_record_id);

-- #43 outbound.campaign_content_message — per-message provenance of a sent variant. No address.
create table outbound.campaign_content_message (
  id uuid primary key default gen_random_uuid(),
  campaign_content_id uuid not null references outbound.campaign_content (id),
  send_attempt_id uuid references outbound.send_attempt (id),
  rfc822_message_id text not null,
  v1_gmail_message_id text,
  v1_attempt_id integer,
  sent_at timestamptz not null,
  matched_delta_seconds integer not null,
  archive_folder text not null,
  created_at timestamptz not null default now(),
  constraint campaign_content_message_rfc_key unique (campaign_content_id, rfc822_message_id),
  constraint campaign_content_message_attempt_key unique (send_attempt_id),
  constraint campaign_content_message_rfc_shape check (
    length(btrim(rfc822_message_id)) > 0 and rfc822_message_id !~ '\s'
  ),
  constraint campaign_content_message_gmail_shape check (
    v1_gmail_message_id is null or v1_gmail_message_id ~ '^[0-9a-f]{8,32}$'
  ),
  constraint campaign_content_message_delta_shape check (matched_delta_seconds between -3600 and 3600),
  constraint campaign_content_message_folder_nonblank check (length(btrim(archive_folder)) > 0)
);

comment on table outbound.campaign_content_message is
  'DOMAIN.md §7 #43 — which archived Sent copy (RFC Message-ID, folder, time) carried which campaign_content variant, linked to the V2 send attempt when one matches by address and acceptance time. Carries no address. Inserted only by the historical importer; never updated or deleted.';

create index campaign_content_message_content_idx on outbound.campaign_content_message (campaign_content_id);
create index campaign_content_message_attempt_idx on outbound.campaign_content_message (send_attempt_id);

create function outbound.campaign_content_immutable() returns trigger
language plpgsql
set search_path = pg_catalog
as $$
begin
  if tg_op <> 'INSERT' then
    raise exception '%.% is immutable history: % refused', tg_table_schema, tg_table_name, tg_op
      using errcode = 'P0001';
  end if;
  if current_user <> 'origenlab_owner' then
    raise exception '%.% is written only by the historical importer', tg_table_schema, tg_table_name
      using errcode = 'P0001';
  end if;
  return new;
end;
$$;

comment on function outbound.campaign_content_immutable() is
  'Trigger guard for outbound.campaign_content and outbound.campaign_content_message: INSERT only, and only as origenlab_owner (the historical importer); UPDATE and DELETE always refused. SECURITY INVOKER.';

create trigger campaign_content_immutable
  before insert or update or delete on outbound.campaign_content
  for each row execute function outbound.campaign_content_immutable();

create trigger campaign_content_message_immutable
  before insert or update or delete on outbound.campaign_content_message
  for each row execute function outbound.campaign_content_immutable();

alter table outbound.campaign_content enable row level security;
alter table outbound.campaign_content_message enable row level security;

grant select on outbound.campaign_content to origenlab_api;
grant select on outbound.campaign_content to origenlab_worker;
grant select on outbound.campaign_content_message to origenlab_api;
grant select on outbound.campaign_content_message to origenlab_worker;

create policy origenlab_api_select on outbound.campaign_content for select to origenlab_api using (true);
create policy origenlab_worker_select on outbound.campaign_content for select to origenlab_worker using (true);
create policy origenlab_api_select on outbound.campaign_content_message for select to origenlab_api using (true);
create policy origenlab_worker_select on outbound.campaign_content_message for select to origenlab_worker using (true);

-- ═══════════════════════════════════════════════════════════════════════════════════════════════
-- Part B — CRM authoring
-- ═══════════════════════════════════════════════════════════════════════════════════════════════

-- ── B1. lifecycle columns: archived people and institutions, inactive contact points ──────────
alter table crm.person
  add column title text,
  add column status text not null default 'active',
  add column archived_at timestamptz,
  add column archived_by_operator_id uuid references platform.operator (id),
  add column archive_reason text,
  add constraint person_title_shape check (title is null or (length(btrim(title)) > 0 and length(title) <= 200)),
  add constraint person_status_check check (status in ('active', 'archived')),
  add constraint person_archived_shape check (
    (status = 'archived') = (archived_at is not null)
    and (archived_at is null) = (archived_by_operator_id is null)
    and (archived_at is null) = (archive_reason is null)
  ),
  add constraint person_archive_reason_shape check (
    archive_reason is null or (length(btrim(archive_reason)) > 0 and length(archive_reason) <= 2000)
  );

comment on column crm.person.status is
  'active | archived. Archiving is the only removal: the row, its contact points, affiliations, notes and every reference (campaign recipients, participants, quotes, evidence) stay. Set only through archive-person / restore-person with a reason.';

create index person_archived_by_operator_idx on crm.person (archived_by_operator_id);

alter table crm.organization
  add column status text not null default 'active',
  add column archived_at timestamptz,
  add column archived_by_operator_id uuid references platform.operator (id),
  add column archive_reason text,
  add constraint organization_status_check check (status in ('active', 'archived')),
  add constraint organization_archived_shape check (
    (status = 'archived') = (archived_at is not null)
    and (archived_at is null) = (archived_by_operator_id is null)
    and (archived_at is null) = (archive_reason is null)
  ),
  add constraint organization_archive_reason_shape check (
    archive_reason is null or (length(btrim(archive_reason)) > 0 and length(archive_reason) <= 2000)
  );

comment on column crm.organization.status is
  'active | archived. Archiving is the only removal (see crm.person.status).';

create index organization_archived_by_operator_idx on crm.organization (archived_by_operator_id);

alter table crm.contact_point
  add column status text not null default 'active',
  add column version integer not null default 1,
  add column note text,
  add column deactivated_at timestamptz,
  add column deactivated_by_operator_id uuid references platform.operator (id),
  add constraint contact_point_status_check check (status in ('active', 'inactive')),
  add constraint contact_point_version_positive check (version >= 1),
  add constraint contact_point_note_shape check (note is null or length(note) <= 2000),
  add constraint contact_point_deactivated_shape check (
    (status = 'inactive') = (deactivated_at is not null)
    and (deactivated_at is null) = (deactivated_by_operator_id is null)
  );

comment on column crm.contact_point.status is
  'active | inactive. An inactive contact point is never deleted (evidence, campaign recipients and messages reference it); it stops being offered as a destination.';
comment on column crm.contact_point.version is
  'Compare-and-set token of update-contact-point / deactivate-contact-point (application-checked, like crm.person.version).';

create index contact_point_deactivated_by_operator_idx on crm.contact_point (deactivated_by_operator_id);

-- Soft removal of domains and identifiers: the row stays, the removal is dated, authored and explained.
alter table crm.organization_domain
  add column removed_at timestamptz,
  add column removed_by_operator_id uuid references platform.operator (id),
  add column remove_reason text,
  add constraint organization_domain_removed_shape check (
    num_nonnulls(removed_at, removed_by_operator_id, remove_reason) in (0, 3)
  ),
  add constraint organization_domain_remove_reason_shape check (
    remove_reason is null or (length(btrim(remove_reason)) > 0 and length(remove_reason) <= 2000)
  );

create index organization_domain_removed_by_operator_idx on crm.organization_domain (removed_by_operator_id);

-- An exclusive domain owner is unique among the domains still in force.
drop index if exists crm.organization_domain_exclusive_owner_key;
create unique index organization_domain_exclusive_owner_key
  on crm.organization_domain (domain_norm) where scope = 'exclusive' and removed_at is null;

alter table crm.external_identifier
  add column removed_at timestamptz,
  add column removed_by_operator_id uuid references platform.operator (id),
  add column remove_reason text,
  add constraint external_identifier_removed_shape check (
    num_nonnulls(removed_at, removed_by_operator_id, remove_reason) in (0, 3)
  ),
  add constraint external_identifier_remove_reason_shape check (
    remove_reason is null or (length(btrim(remove_reason)) > 0 and length(remove_reason) <= 2000)
  );

create index external_identifier_removed_by_operator_idx on crm.external_identifier (removed_by_operator_id);

-- Never deleted — by anyone, the owner included. Archive is the only removal.
create trigger person_never_deleted
  before delete on crm.person
  for each row execute function platform.reject_mutation('never deleted; archive the person instead');
create trigger organization_never_deleted
  before delete on crm.organization
  for each row execute function platform.reject_mutation('never deleted; archive the institution instead');
create trigger contact_point_never_deleted
  before delete on crm.contact_point
  for each row execute function platform.reject_mutation('never deleted; deactivate the contact point instead');
create trigger organization_domain_never_deleted
  before delete on crm.organization_domain
  for each row execute function platform.reject_mutation('never deleted; soft-remove the domain instead');
create trigger external_identifier_never_deleted
  before delete on crm.external_identifier
  for each row execute function platform.reject_mutation('never deleted; soft-remove the identifier instead');

-- ── B2. #44 crm.note — timestamped operator notes, revised by a new row, archived never edited ──
create table crm.note (
  id uuid primary key default gen_random_uuid(),
  subject_kind text not null,
  subject_id uuid not null,
  body text not null,
  author_operator_id uuid not null references platform.operator (id),
  root_note_id uuid references crm.note (id),
  revision_of_note_id uuid references crm.note (id),
  revision_no integer not null default 1,
  status text not null default 'active',
  archived_at timestamptz,
  archived_by_operator_id uuid references platform.operator (id),
  archive_reason text,
  version integer not null default 1,
  created_at timestamptz not null default now(),
  constraint note_subject_kind_check check (subject_kind in ('person', 'organization', 'opportunity')),
  constraint note_body_shape check (length(btrim(body)) > 0 and length(body) <= 8000),
  constraint note_status_check check (status in ('active', 'archived')),
  constraint note_revision_positive check (revision_no >= 1),
  constraint note_version_positive check (version >= 1),
  constraint note_revision_shape check (
    (revision_no = 1) = (revision_of_note_id is null)
    and (revision_no = 1) = (root_note_id is null)
  ),
  constraint note_no_self_revision check (revision_of_note_id is null or revision_of_note_id <> id),
  constraint note_archived_shape check (
    (status = 'archived') = (archived_at is not null)
    and (archived_at is null) = (archived_by_operator_id is null)
    and (archived_at is null) = (archive_reason is null)
  ),
  constraint note_archive_reason_shape check (
    archive_reason is null or (length(btrim(archive_reason)) > 0 and length(archive_reason) <= 2000)
  )
);

comment on table crm.note is
  'DOMAIN.md §7 #44 — an operator note on a person, institution (including a supplier) or commercial case. Body, author, subject and time never change: an edit is a new row (revision_no + 1, revision_of_note_id → the previous one, root_note_id → the first); an archived note stays. Never deleted.';

create index note_subject_idx on crm.note (subject_kind, subject_id, created_at desc);
create index note_author_operator_idx on crm.note (author_operator_id);
create index note_root_idx on crm.note (root_note_id);
create index note_revision_of_idx on crm.note (revision_of_note_id);
create index note_archived_by_operator_idx on crm.note (archived_by_operator_id);

create function crm.note_guard() returns trigger
language plpgsql
set search_path = pg_catalog
as $$
begin
  if tg_op = 'DELETE' then
    raise exception 'crm.note is never deleted; archive it instead' using errcode = 'P0001';
  end if;
  if tg_op = 'UPDATE' then
    if new.body is distinct from old.body
       or new.author_operator_id is distinct from old.author_operator_id
       or new.subject_kind is distinct from old.subject_kind
       or new.subject_id is distinct from old.subject_id
       or new.root_note_id is distinct from old.root_note_id
       or new.revision_of_note_id is distinct from old.revision_of_note_id
       or new.revision_no is distinct from old.revision_no
       or new.created_at is distinct from old.created_at then
      raise exception 'crm.note % is written once: revise it with a new row' , old.id using errcode = 'P0001';
    end if;
    if old.status = 'archived' and new.status = 'archived'
       and (new.archived_at, new.archived_by_operator_id, new.archive_reason)
           is distinct from (old.archived_at, old.archived_by_operator_id, old.archive_reason) then
      raise exception 'crm.note % archive record is immutable', old.id using errcode = 'P0001';
    end if;
    if new.version <> old.version + 1 then
      raise exception 'crm.note % version must advance by one', old.id using errcode = 'P0001';
    end if;
  end if;
  return new;
end;
$$;

comment on function crm.note_guard() is
  'Trigger guard for crm.note: never deleted; body, author, subject, chain and time never change; only status/archive fields move, once, and version advances by exactly one. SECURITY INVOKER.';

create trigger note_guard
  before update or delete on crm.note
  for each row execute function crm.note_guard();

alter table crm.note enable row level security;

grant select, insert on crm.note to origenlab_api;
grant update (status, archived_at, archived_by_operator_id, archive_reason, version) on crm.note to origenlab_api;
grant select on crm.note to origenlab_worker;

create policy origenlab_api_select on crm.note for select to origenlab_api using (true);
create policy origenlab_api_insert on crm.note for insert to origenlab_api with check (true);
create policy origenlab_api_update on crm.note for update to origenlab_api using (true) with check (true);
create policy origenlab_worker_select on crm.note for select to origenlab_worker using (true);

-- ── B3. #45 crm.organization_product_line — a real supplier/manufacturer ↔ a curated line ─────
-- The six lines are the website's catalogue brands (apps/api equipment_taxonomy.json), pinned here
-- so a link can never name a line the catalogue does not carry. The curated directory is not a CRM
-- organization; this table is the only bridge between them.
create table crm.organization_product_line (
  id uuid primary key default gen_random_uuid(),
  organization_id uuid not null references crm.organization (id),
  line_id text not null,
  linked_by_operator_id uuid not null references platform.operator (id),
  note text,
  valid_from date not null default current_date,
  valid_to date,
  unlinked_by_operator_id uuid references platform.operator (id),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint organization_product_line_id_check check (
    line_id in ('hielscher', 'ortoalresa', 'ika', 'adam-equipment', 'loeser', 'serva')
  ),
  constraint organization_product_line_validity check (valid_to is null or valid_to >= valid_from),
  constraint organization_product_line_unlink_shape check ((valid_to is null) = (unlinked_by_operator_id is null)),
  constraint organization_product_line_note_shape check (note is null or length(note) <= 2000)
);

comment on table crm.organization_product_line is
  'DOMAIN.md §7 #45 — a real CRM organization linked to one of the six curated product lines (Hielscher, Ortoalresa, IKA, Adam Equipment, Löser, SERVA). Closed by valid_to, never deleted. The curated directory itself is not a row here.';

create unique index organization_product_line_active_key
  on crm.organization_product_line (organization_id, line_id) where valid_to is null;
create index organization_product_line_organization_idx on crm.organization_product_line (organization_id);
create index organization_product_line_linked_by_idx on crm.organization_product_line (linked_by_operator_id);
create index organization_product_line_unlinked_by_idx on crm.organization_product_line (unlinked_by_operator_id);

create trigger organization_product_line_never_deleted
  before delete on crm.organization_product_line
  for each row execute function platform.reject_mutation('never deleted; close the link with valid_to instead');

alter table crm.organization_product_line enable row level security;

grant select, insert on crm.organization_product_line to origenlab_api;
grant update (valid_to, unlinked_by_operator_id, note, updated_at) on crm.organization_product_line to origenlab_api;
grant select on crm.organization_product_line to origenlab_worker;

create policy origenlab_api_select on crm.organization_product_line for select to origenlab_api using (true);
create policy origenlab_api_insert on crm.organization_product_line for insert to origenlab_api with check (true);
create policy origenlab_api_update on crm.organization_product_line for update to origenlab_api using (true) with check (true);
create policy origenlab_worker_select on crm.organization_product_line for select to origenlab_worker using (true);

-- ── B4. vocabulary ────────────────────────────────────────────────────────────────────────────
create or replace function crm.domain_event_is_valid(
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
           when 'opportunity_organization'  then 'case_organization'
           when 'opportunity_interest'      then 'case_interest'
           when 'opportunity_evidence'      then 'case_evidence'
           when 'task'                      then 'task'
           when 'activity'                  then 'activity'
           when 'quote'                     then 'quote'
           when 'quote_revision'            then 'quote_revision'
           when 'campaign'                  then 'campaign'
           when 'campaign_block'            then 'campaign_block'
           when 'send_attempt'              then 'send_attempt'
           when 'contact_control'           then 'contact_control'
           when 'send_control'              then 'send_control'
           when 'operator'                  then 'operator'
           when 'assertion'                 then 'assertion'
           when 'source_record'             then 'source_record'
           when 'product'                   then 'product'
           when 'note'                      then 'note'
         end;
$$;

comment on function crm.domain_event_is_valid(text, text, smallint, jsonb) is
  'CHECK helper for crm.domain_event: payload is an object, payload_version is defined, event_type family matches aggregate_kind — including the three commercial-case families (DOMAIN.md §3.6), campaign_block (§7 #37) and note (§7 #44). SECURITY INVOKER.';

alter table crm.domain_event drop constraint domain_event_aggregate_kind_check;
alter table crm.domain_event add constraint domain_event_aggregate_kind_check check (aggregate_kind in (
  'organization', 'person', 'affiliation', 'address', 'contact_point', 'organization_relationship',
  'opportunity', 'opportunity_participant', 'opportunity_organization', 'opportunity_interest',
  'opportunity_evidence', 'task', 'activity', 'quote', 'quote_revision',
  'campaign', 'campaign_block', 'send_attempt', 'contact_control', 'send_control', 'operator',
  'assertion', 'source_record', 'product', 'note'
));

alter table crm.domain_event drop constraint domain_event_type_check;
alter table crm.domain_event add constraint domain_event_type_check check (event_type in (
  'organization.created', 'organization.confirmed', 'organization.merged',
  'organization.updated', 'organization.archived', 'organization.restored',
  'organization.identifier_added', 'organization.identifier_removed',
  'organization.domain_added', 'organization.domain_removed',
  'organization.product_line_linked', 'organization.product_line_unlinked',
  'person.created', 'person.confirmed', 'person.merged',
  'person.updated', 'person.archived', 'person.restored',
  'affiliation.opened', 'affiliation.closed',
  'address.added', 'address.superseded',
  'contact_point.created', 'contact_point.confirmed',
  'contact_point.updated', 'contact_point.deactivated',
  'relationship.changed',
  'opportunity.created', 'opportunity.staged', 'opportunity.organization_set', 'opportunity.closed',
  'participant.added', 'participant.linked', 'participant.primary_changed', 'participant.ended',
  'case_organization.added', 'case_organization.role_confirmed', 'case_organization.ended',
  'case_interest.added',
  'case_evidence.linked',
  'task.created', 'task.completed', 'task.cancelled',
  'activity.linked',
  'quote.created', 'quote_revision.transitioned', 'quote_revision.superseded',
  'quote_revision.historical_recorded',
  'campaign.transitioned', 'campaign.approved', 'campaign.override_granted', 'campaign.dry_run_recorded',
  'campaign.draft_created', 'campaign.draft_content_saved',
  'campaign.audience_frozen',
  'campaign.planning_set', 'campaign.planning_cleared',
  'campaign_block.placed', 'campaign_block.lifted',
  'send_attempt.submission_changed', 'send_attempt.delivery_changed',
  'contact_control.added', 'contact_control.revoked', 'contact_control.evidence_linked',
  'send_control.changed',
  'operator.changed',
  'assertion.promoted', 'assertion.unsubscribe_review_opened', 'assertion.unsubscribe_review_dismissed',
  'assertion.supplier_candidate_confirmed', 'assertion.supplier_candidate_rejected',
  'source_record.quarantined', 'source_record.migration_manifest_recorded',
  'source_record.review_noted',
  'product.created',
  'note.created', 'note.revised', 'note.archived'
));

reset role;
